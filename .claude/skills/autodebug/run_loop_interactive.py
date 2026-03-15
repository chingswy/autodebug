#!/usr/bin/env python3
"""autodebug 交互式循环脚本

针对交互式 CLI 工具设计（如 claude-internal、claude 等）。CLI 启动后进入交互式窗口，
运行完一轮迭代后不会自动退出。本脚本通过轮询 history.md 检测迭代完成，
然后 kill 进程并启动下一轮。

使用 PTY 运行子进程，输出同时显示到终端并写入日志文件。

判断迭代完成的依据：
1. 先监测 insights.md 的 mtime 发生变化（相对于启动前）
2. 等待 insights.md 的 mtime 收敛（连续 60 秒不再变化）
3. 最后检查 history.md 中是否新增了 `## Iteration` 标题

用法:
    python .claude/skills/autodebug/run_loop_interactive.py --workspace /path/to/workspace
    python .claude/skills/autodebug/run_loop_interactive.py --workspace /path/to/workspace --max-iterations 20
    python .claude/skills/autodebug/run_loop_interactive.py --workspace /path/to/workspace --cli-command claude-internal
"""

import argparse
import datetime
import errno
import os
import pty
import re
import select
import signal
import sys
import time


def parse_args():
    parser = argparse.ArgumentParser(description="autodebug 交互式循环")
    parser.add_argument(
        "--workspace", required=True,
        help="workspace 目录路径，包含 brief.md 等文件"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=50,
        help="最大迭代次数安全上限 (默认 50)"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=60,
        help="轮询文件变化的间隔秒数 (默认 60)"
    )
    parser.add_argument(
        "--timeout", type=int, default=1800,
        help="单次迭代超时秒数 (默认 1800 即 30 分钟)"
    )
    parser.add_argument(
        "--log-dir", default=None,
        help="日志输出目录 (默认: <workspace>/logs)"
    )
    parser.add_argument(
        "--cli-command", default="claude",
        help="交互式 CLI 命令名 (默认: claude，可设为 claude-internal 等)"
    )
    parser.add_argument(
        "--skill-args", default="",
        help="附加到 /autodebug 命令后的额外参数 (默认: 空)"
    )
    return parser.parse_args()


def count_iterations(workspace):
    """统计 history.md 中 ## Iteration 标题的数量"""
    history_path = os.path.join(workspace, "history.md")
    if not os.path.exists(history_path):
        return 0
    with open(history_path, "r") as f:
        content = f.read()
    return len(re.findall(r"^## Iteration\s+", content, re.MULTILINE))


def check_insights_status(workspace):
    """检查 insights.md 中的 status 字段"""
    insights_path = os.path.join(workspace, "insights.md")
    if not os.path.exists(insights_path):
        return "not_found"
    with open(insights_path, "r") as f:
        content = f.read()
    match = re.search(r"^>\s*status:\s*(\w+)", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "running"


def kill_process_tree(pid):
    """终止进程及其所有子进程"""
    # 先尝试 SIGTERM 整个进程组
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for target_fn in (lambda p: os.killpg(os.getpgid(p), sig), lambda p: os.kill(p, sig)):
            try:
                target_fn(pid)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if sig == signal.SIGTERM:
            time.sleep(2)


def strip_ansi(text):
    """去除 ANSI 转义序列，用于写入干净的日志"""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def run_iteration_interactive(workspace, iteration_num, poll_interval, timeout, log_file, cli_command="claude", skill_args=""):
    """启动交互式 CLI 会话（PTY），轮询 history.md 检测完成，然后 kill。

    子进程输出同时 tee 到终端和 log_file。

    返回 (completed, elapsed_seconds)
    """
    start = time.time()
    abs_workspace = os.path.abspath(workspace)
    project_root = os.path.dirname(os.path.abspath(__file__)).rsplit(".claude", 1)[0]

    iter_count_before = count_iterations(workspace)

    # 记录启动前 insights.md 的 mtime，用于后续判断文件是否被更新
    insights_path = os.path.join(workspace, "insights.md")
    insights_mtime_before = os.path.getmtime(insights_path) if os.path.exists(insights_path) else 0

    skill_cmd = f"/autodebug {abs_workspace}"
    if skill_args:
        skill_cmd += f" {skill_args}"
    cmd = [cli_command, skill_cmd]
    header = (
        f"\n{'='*60}\n"
        f"Iteration {iteration_num} — 启动交互式会话\n"
        f"命令: {' '.join(cmd)}\n"
        f"当前 history.md 迭代数: {iter_count_before}\n"
        f"日志: {log_file.name}\n"
        f"{'='*60}\n"
    )
    sys.stdout.write(header)
    sys.stdout.flush()
    log_file.write(header)
    log_file.flush()

    # 用 pty.openpty() 创建伪终端，让子进程以为自己在终端中运行
    master_fd, slave_fd = pty.openpty()

    pid = os.fork()
    if pid == 0:
        # ---- 子进程 ----
        os.close(master_fd)
        os.setsid()
        # 把 slave 端设为 stdin/stdout/stderr
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.chdir(project_root)
        os.execvp(cmd[0], cmd)
        # execvp 不返回，如果失败直接 _exit
        os._exit(127)

    # ---- 父进程 ----
    os.close(slave_fd)

    completed = False
    child_exited = False

    # 状态机：
    #   watching_insights  — 等待 insights.md mtime 比启动前更新（说明本轮开始写入）
    #   converging         — insights.md 已变化，等 mtime 收敛（连续 60s 不变 = 写入完毕）
    #   checking_history   — 收敛后检查 history.md 是否新增迭代，确认则完成
    state = "watching_insights"
    last_insights_mtime = insights_mtime_before
    converge_since = 0  # mtime 最后一次变化的时刻

    def _log(msg):
        """同时输出到终端和日志文件"""
        sys.stdout.write(msg)
        sys.stdout.flush()
        log_file.write(msg)
        log_file.flush()

    def _drain_pty():
        """从 PTY 读取所有可用数据并 tee 输出，返回 False 表示 PTY 已关闭"""
        while True:
            try:
                rlist, _, _ = select.select([master_fd], [], [], 0.1)
            except (select.error, ValueError):
                return False
            if not rlist:
                return True
            try:
                data = os.read(master_fd, 4096)
            except OSError as e:
                if e.errno == errno.EIO:
                    return False
                raise
            if not data:
                return False
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            log_file.write(strip_ansi(data.decode("utf-8", errors="replace")))
            log_file.flush()

    while True:
        # 持续读取 PTY 输出（非阻塞），保持子进程不被 buffer 阻塞
        if not _drain_pty():
            break

        # 检查子进程是否退出
        try:
            wpid, wstatus = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            child_exited = True
            break
        if wpid != 0:
            child_exited = True
            ret = os.WEXITSTATUS(wstatus) if os.WIFEXITED(wstatus) else -1
            _log(f"\n进程已自行退出 (exit={ret})\n")
            iter_count_now = count_iterations(workspace)
            completed = iter_count_now > iter_count_before
            break

        elapsed = time.time() - start
        now = time.time()

        # 超时检查
        if elapsed > timeout:
            _log(f"\n迭代超时 ({timeout}s)，终止进程...\n")
            kill_process_tree(pid)
            os.waitpid(pid, 0)
            break

        if state == "watching_insights":
            # 等待 insights.md 的 mtime 比启动前更新
            cur_mtime = os.path.getmtime(insights_path) if os.path.exists(insights_path) else 0
            if cur_mtime > insights_mtime_before:
                _log(f"\n  insights.md 开始变化 (mtime: {insights_mtime_before} -> {cur_mtime})\n")
                last_insights_mtime = cur_mtime
                converge_since = now
                state = "converging"

        elif state == "converging":
            # 等待 insights.md mtime 收敛：连续 60 秒不再变化
            cur_mtime = os.path.getmtime(insights_path) if os.path.exists(insights_path) else 0
            if cur_mtime != last_insights_mtime:
                # 还在变，重置计时
                last_insights_mtime = cur_mtime
                converge_since = now
            elif now - converge_since >= 60:
                _log(f"  insights.md mtime 已收敛 (稳定 60s, mtime={last_insights_mtime})\n")
                state = "checking_history"

        elif state == "checking_history":
            # 确认 history.md 新增了迭代
            iter_count_now = count_iterations(workspace)
            if iter_count_now > iter_count_before:
                _log(f"  history.md 确认新迭代 ({iter_count_before} -> {iter_count_now})，终止会话...\n")
                kill_process_tree(pid)
                os.waitpid(pid, 0)
                completed = True
                break
            else:
                # insights 变了但 history 没新增，可能是中间状态，回到 watching
                _log(f"  history.md 未新增迭代 (仍为 {iter_count_before})，继续监测...\n")
                insights_mtime_before = last_insights_mtime
                state = "watching_insights"

    os.close(master_fd)

    # 确保子进程已回收
    if not child_exited:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    status_str = "完成" if completed else "未完成"
    msg = f"Iteration {iteration_num} — {status_str} (耗时 {minutes}m{seconds}s)\n"
    sys.stdout.write(msg)
    log_file.write(msg)
    log_file.flush()

    return completed, elapsed


def main():
    args = parse_args()
    workspace = args.workspace
    max_iter = args.max_iterations
    poll_interval = args.poll_interval
    timeout = args.timeout
    log_dir = args.log_dir or os.path.join(workspace, "logs")
    cli_command = args.cli_command
    skill_args = args.skill_args

    # 检查 workspace 和 brief.md
    if not os.path.isdir(workspace):
        print(f"错误: workspace 目录不存在: {workspace}")
        sys.exit(1)

    brief_path = os.path.join(workspace, "brief.md")
    if not os.path.exists(brief_path):
        print(f"错误: brief.md 不存在: {brief_path}")
        print(f"请参考模板创建: .claude/skills/autodebug/references/brief_template.md")
        sys.exit(1)

    os.makedirs(log_dir, exist_ok=True)

    # 优雅退出
    stop_flag = False
    def signal_handler(sig, frame):
        nonlocal stop_flag
        if stop_flag:
            print("\n强制退出")
            sys.exit(1)
        print("\n收到中断信号，当前迭代完成后退出...")
        stop_flag = True
    signal.signal(signal.SIGINT, signal_handler)

    initial_iters = count_iterations(workspace)

    print(f"autodebug 交互式循环启动")
    print(f"  workspace:       {os.path.abspath(workspace)}")
    print(f"  max_iterations:  {max_iter}")
    print(f"  poll_interval:   {poll_interval}s")
    print(f"  timeout:         {timeout}s")
    print(f"  cli_command:     {cli_command}")
    print(f"  log_dir:         {os.path.abspath(log_dir)}")
    print(f"  已有迭代数:      {initial_iters}")

    total_time = 0.0
    completed_count = 0
    for i in range(1, max_iter + 1):
        if stop_flag:
            print(f"\n用户中断，共完成 {completed_count} 次迭代")
            break

        # 每轮迭代一个日志文件
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(log_dir, f"iter_{i}_{ts}.log")
        log_file = open(log_path, "w", encoding="utf-8")

        completed, elapsed = run_iteration_interactive(
            workspace, i, poll_interval, timeout, log_file, cli_command, skill_args
        )
        log_file.close()
        total_time += elapsed

        if completed:
            completed_count += 1

        # 检查 insights.md status
        status = check_insights_status(workspace)
        if status == "done":
            print(f"\ninsights.md status=done，调试完成！共 {completed_count} 次迭代")
            break

        if not completed:
            print(f"\n迭代 {i} 未正常完成，停止循环")
            break

        # 迭代间短暂暂停
        print(f"\n等待 3 秒后启动下一轮...")
        time.sleep(3)
    else:
        print(f"\n达到最大迭代次数 {max_iter}，停止循环")

    total_min = int(total_time // 60)
    total_sec = int(total_time % 60)
    final_iters = count_iterations(workspace)
    print(f"\n总耗时: {total_min}m{total_sec}s")
    print(f"本次新增迭代: {final_iters - initial_iters}")
    print(f"日志目录: {os.path.abspath(log_dir)}")


if __name__ == "__main__":
    main()
