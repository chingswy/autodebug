#!/usr/bin/env python3
"""autodebug 外部循环脚本

通过反复调用 claude CLI 的 /autodebug skill 实现迭代式自动调试。
每次调用完成一个完整的 "分析->改代码->跑->记录" 迭代。
通过 insights.md 中的 status 字段判断是否继续。

用法:
    python .claude/skills/autodebug/run_loop.py --workspace /path/to/workspace
    python .claude/skills/autodebug/run_loop.py --workspace /path/to/workspace --max-iterations 20
"""

import argparse
import os
import re
import subprocess
import signal
import sys
import time


def parse_args():
    parser = argparse.ArgumentParser(description="autodebug 迭代循环")
    parser.add_argument(
        "--workspace", required=True,
        help="workspace 目录路径，包含 brief.md 等文件"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=50,
        help="最大迭代次数安全上限 (默认 50)"
    )
    return parser.parse_args()


def check_insights_status(workspace):
    """检查 insights.md 中的 status 字段，返回 'running', 'done', 或 'not_found'"""
    insights_path = os.path.join(workspace, "insights.md")
    if not os.path.exists(insights_path):
        return "not_found"

    with open(insights_path, "r") as f:
        content = f.read()

    match = re.search(r"^>\s*status:\s*(\w+)", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "running"


def run_iteration(workspace, iteration_num):
    """执行一次 autodebug 迭代，返回 (success, elapsed_seconds)"""
    start = time.time()
    abs_workspace = os.path.abspath(workspace)

    cmd = ["claude", "-p", f"/autodebug {abs_workspace}"]
    print(f"\n{'='*60}")
    print(f"Iteration {iteration_num} — 开始")
    print(f"命令: {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)).rsplit(".claude", 1)[0],
        capture_output=False,
    )

    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print(f"\nIteration {iteration_num} — 完成 (耗时 {minutes}m{seconds}s, exit={result.returncode})")

    return result.returncode == 0, elapsed


def main():
    args = parse_args()
    workspace = args.workspace
    max_iter = args.max_iterations

    # 检查 workspace 和 brief.md
    if not os.path.isdir(workspace):
        print(f"错误: workspace 目录不存在: {workspace}")
        sys.exit(1)

    brief_path = os.path.join(workspace, "brief.md")
    if not os.path.exists(brief_path):
        print(f"错误: brief.md 不存在: {brief_path}")
        print(f"请参考模板创建: .claude/skills/autodebug/references/brief_template.md")
        sys.exit(1)

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

    print(f"autodebug 循环启动")
    print(f"  workspace: {os.path.abspath(workspace)}")
    print(f"  max_iterations: {max_iter}")

    total_time = 0.0
    for i in range(1, max_iter + 1):
        if stop_flag:
            print(f"\n用户中断，共完成 {i-1} 次迭代")
            break

        success, elapsed = run_iteration(workspace, i)
        total_time += elapsed

        # 检查退出条件
        status = check_insights_status(workspace)
        if status == "done":
            print(f"\ninsights.md status=done，调试完成！共 {i} 次迭代")
            break

        if not success:
            print(f"\n迭代 {i} 执行失败 (非零退出码)，停止循环")
            break
    else:
        print(f"\n达到最大迭代次数 {max_iter}，停止循环")

    total_min = int(total_time // 60)
    total_sec = int(total_time % 60)
    print(f"\n总耗时: {total_min}m{total_sec}s")


if __name__ == "__main__":
    main()
