#!/usr/bin/env python3
"""
task_manager.py - SQLite-based task manager for autorun skill.

CLI interface for managing TODO tasks via SQLite database.
DB path: .claude/autorun-tasks.db (relative to project root)

Usage:
    python task_manager.py init
    python task_manager.py sync --file TODO_LIST.md
    python task_manager.py next
    python task_manager.py complete <id> --conclusion "..." [--files '[...]'] [--notes "..."]
    python task_manager.py fail <id> [--notes "..."]
    python task_manager.py retry <id>
    python task_manager.py decompose <id> --subtasks '<JSON array>'
    python task_manager.py list [--status pending]
    python task_manager.py count [--status pending]
    python task_manager.py export [--output FINISH_LIST.md]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# DB path relative to project root
DB_DIR = ".claude"
DB_NAME = "autorun-tasks.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    description     TEXT NOT NULL,
    tag             TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','in_progress','completed','failed','decomposed')),
    parent_id       INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    position        REAL NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M','now','localtime')),
    started_at      TEXT,
    completed_at    TEXT,
    duration_minutes REAL,
    conclusion      TEXT,
    modified_files  TEXT,
    notes           TEXT
);
"""


def find_project_root():
    """Find project root by walking up to find .claude directory or .git."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").is_dir() or (parent / ".git").is_dir():
            return parent
    return cwd


def get_db_path():
    root = find_project_root()
    return root / DB_DIR / DB_NAME


def get_connection():
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── Commands ──────────────────────────────────────────────────────────

def cmd_init(_args):
    """Create database and tables (idempotent)."""
    conn = get_connection()
    conn.executescript(CREATE_TABLE_SQL)
    conn.close()
    print(json.dumps({"ok": True, "db": str(get_db_path())}))


def cmd_sync(args):
    """Incremental sync from TODO_LIST.md to DB.

    - New `- [ ]` lines are imported and marked as `- [>]` in the file.
    - DB completed tasks are marked as `- [x]` in the file if still present.
    """
    root = find_project_root()
    todo_file = root / args.file
    if not todo_file.exists():
        print(json.dumps({"ok": True, "imported": 0, "message": "TODO file not found, nothing to sync"}))
        return

    conn = get_connection()
    conn.executescript(CREATE_TABLE_SQL)

    lines = todo_file.read_text(encoding="utf-8").splitlines()
    # Pattern for todo items: `- [ ] ...` or `- [>] ...` or `- [x] ...`
    todo_re = re.compile(r'^(\s*)- \[( |>|x)\]\s*(.*)')

    # Get max position in DB
    row = conn.execute("SELECT MAX(position) as mp FROM tasks").fetchone()
    max_pos = row["mp"] if row["mp"] is not None else 0.0

    # Get completed task descriptions for marking [x]
    completed_descs = set()
    for r in conn.execute("SELECT description FROM tasks WHERE status='completed'"):
        completed_descs.add(r["description"].strip())

    imported = 0
    new_lines = []
    tag_re = re.compile(r'^\[([^\]]+)\]\s*(.*)')

    for line in lines:
        m = todo_re.match(line)
        if not m:
            new_lines.append(line)
            continue

        indent, marker, text = m.group(1), m.group(2), m.group(3).strip()

        # Parse optional tag
        tag = None
        desc = text
        tm = tag_re.match(text)
        if tm:
            tag = tm.group(1)
            desc = tm.group(2).strip()

        # Skip already-imported lines
        if marker == '>':
            # Check if this task is now completed in DB -> mark [x]
            if desc in completed_descs or (tag and f"[{tag}] {desc}" in completed_descs):
                new_lines.append(f"{indent}- [x] {text}")
            else:
                new_lines.append(line)
            continue

        # Skip already-completed lines
        if marker == 'x':
            new_lines.append(line)
            continue

        # marker == ' ' -> new unchecked task, import it
        # Skip lines that end with （已拆解） - these are parent placeholders
        if desc.endswith("（已拆解）") or desc.endswith("(已拆解)"):
            new_lines.append(line)
            continue

        max_pos += 1.0
        conn.execute(
            "INSERT INTO tasks (description, tag, status, position) VALUES (?, ?, 'pending', ?)",
            (desc, tag, max_pos)
        )
        imported += 1
        # Mark as imported in the file
        new_lines.append(f"{indent}- [>] {text}")

    conn.commit()
    conn.close()

    # Write back modified file
    todo_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "imported": imported}))


def cmd_next(_args):
    """Get next pending task, mark it in_progress, return single JSON."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM tasks WHERE status='pending' ORDER BY position ASC LIMIT 1"
    ).fetchone()

    if row is None:
        print(json.dumps({"id": None, "message": "No pending tasks"}))
        conn.close()
        return

    task_id = row["id"]
    started = now_str()
    conn.execute(
        "UPDATE tasks SET status='in_progress', started_at=? WHERE id=?",
        (started, task_id)
    )
    conn.commit()

    # Count totals for progress display
    total = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status != 'decomposed'").fetchone()["c"]
    completed = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status='completed'").fetchone()["c"]

    result = {
        "id": task_id,
        "description": row["description"],
        "tag": row["tag"],
        "parent_id": row["parent_id"],
        "position": row["position"],
        "started_at": started,
        "progress": f"{completed + 1}/{total}"
    }
    conn.close()
    print(json.dumps(result, ensure_ascii=False))


def cmd_complete(args):
    """Mark task as completed with conclusion and metadata."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (args.id,)).fetchone()
    if row is None:
        print(json.dumps({"ok": False, "error": f"Task {args.id} not found"}))
        conn.close()
        return

    completed_at = now_str()
    duration = None
    if row["started_at"]:
        try:
            start = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M")
            end = datetime.strptime(completed_at, "%Y-%m-%d %H:%M")
            duration = round((end - start).total_seconds() / 60, 1)
        except ValueError:
            pass

    conn.execute(
        """UPDATE tasks SET status='completed', completed_at=?, duration_minutes=?,
           conclusion=?, modified_files=?, notes=? WHERE id=?""",
        (completed_at, duration, args.conclusion, args.files, args.notes, args.id)
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "ok": True,
        "id": args.id,
        "duration_minutes": duration,
        "completed_at": completed_at
    }))


def cmd_fail(args):
    """Mark task as failed."""
    conn = get_connection()
    conn.execute(
        "UPDATE tasks SET status='failed', notes=? WHERE id=?",
        (args.notes, args.id)
    )
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "id": args.id, "status": "failed"}))


def cmd_retry(args):
    """Reset a failed task back to pending."""
    conn = get_connection()
    conn.execute(
        "UPDATE tasks SET status='pending', started_at=NULL, notes=NULL WHERE id=?",
        (args.id,)
    )
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "id": args.id, "status": "pending"}))


def cmd_decompose(args):
    """Decompose a task into subtasks."""
    conn = get_connection()
    parent = conn.execute("SELECT * FROM tasks WHERE id=?", (args.id,)).fetchone()
    if parent is None:
        print(json.dumps({"ok": False, "error": f"Task {args.id} not found"}))
        conn.close()
        return

    subtasks = json.loads(args.subtasks)
    parent_pos = parent["position"]

    # Find next task's position
    next_row = conn.execute(
        "SELECT position FROM tasks WHERE position > ? ORDER BY position ASC LIMIT 1",
        (parent_pos,)
    ).fetchone()

    if next_row is not None:
        next_pos = next_row["position"]
        gap = next_pos - parent_pos
    else:
        next_pos = None
        gap = None

    # Calculate subtask positions
    n = len(subtasks)
    positions = []
    if next_pos is not None:
        step = gap / (n + 1)
        # Check if interval is too small
        if step < 0.001:
            _renumber_all(conn)
            # Re-fetch positions after renumber
            parent = conn.execute("SELECT * FROM tasks WHERE id=?", (args.id,)).fetchone()
            parent_pos = parent["position"]
            next_row = conn.execute(
                "SELECT position FROM tasks WHERE position > ? ORDER BY position ASC LIMIT 1",
                (parent_pos,)
            ).fetchone()
            if next_row:
                step = (next_row["position"] - parent_pos) / (n + 1)
            else:
                step = 0.25
        for i in range(n):
            positions.append(parent_pos + step * (i + 1))
    else:
        for i in range(n):
            positions.append(parent_pos + 0.25 * (i + 1))

    # Mark parent as decomposed
    conn.execute("UPDATE tasks SET status='decomposed' WHERE id=?", (args.id,))

    # Insert subtasks
    tag_re = re.compile(r'^\[([^\]]+)\]\s*(.*)')
    created_ids = []
    for i, st in enumerate(subtasks):
        tag = parent["tag"]
        desc = st
        tm = tag_re.match(st)
        if tm:
            tag = tm.group(1)
            desc = tm.group(2).strip()
        cur = conn.execute(
            "INSERT INTO tasks (description, tag, status, parent_id, position) VALUES (?, ?, 'pending', ?, ?)",
            (desc, tag, args.id, positions[i])
        )
        created_ids.append(cur.lastrowid)

    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "parent_id": args.id, "subtask_ids": created_ids}))


def _renumber_all(conn):
    """Renumber all tasks with integer positions to restore spacing."""
    rows = conn.execute("SELECT id FROM tasks ORDER BY position ASC").fetchall()
    for i, row in enumerate(rows):
        conn.execute("UPDATE tasks SET position=? WHERE id=?", (float(i + 1), row["id"]))
    conn.commit()


def cmd_list(args):
    """List tasks, optionally filtered by status."""
    conn = get_connection()
    if args.status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status=? ORDER BY position ASC",
            (args.status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY position ASC"
        ).fetchall()
    conn.close()

    tasks = []
    for r in rows:
        tasks.append({
            "id": r["id"],
            "description": r["description"],
            "tag": r["tag"],
            "status": r["status"],
            "parent_id": r["parent_id"],
            "position": r["position"],
            "created_at": r["created_at"],
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "duration_minutes": r["duration_minutes"],
            "conclusion": r["conclusion"],
        })
    print(json.dumps(tasks, ensure_ascii=False))


def cmd_count(args):
    """Count tasks, optionally filtered by status."""
    conn = get_connection()
    if args.status:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE status=?", (args.status,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) as c FROM tasks").fetchone()
    conn.close()
    print(json.dumps({"count": row["c"]}))


def cmd_export(args):
    """Export completed tasks to markdown file."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status='completed' ORDER BY completed_at ASC"
    ).fetchall()
    conn.close()

    root = find_project_root()
    output_path = root / args.output

    lines = [
        "# FINISH_LIST.md — 已完成任务记录",
        "",
        "> 本文件由 auto-run skill 自动维护，记录每条已完成 TODO 的处理过程和结论。",
        "",
        "---",
        "",
    ]

    for r in rows:
        tag_prefix = f"[{r['tag']}] " if r['tag'] else ""
        desc = f"{tag_prefix}{r['description']}"
        duration = r["duration_minutes"]
        duration_str = f"约 {duration} 分钟" if duration is not None else "未知"
        conclusion = r["conclusion"] or "无"
        notes = r["notes"] or "无"

        lines.append(f"## [{r['completed_at']}] {desc}")
        lines.append("")
        lines.append(f"- **完成时间**：{r['completed_at']}")
        lines.append(f"- **耗时**：{duration_str}")
        lines.append(f"- **结论**：{conclusion}")

        # Parse modified files
        files_str = r["modified_files"]
        if files_str:
            try:
                files = json.loads(files_str)
                lines.append("- **修改文件**：")
                for f in files:
                    if isinstance(f, dict):
                        lines.append(f"  - `{f.get('path', f)}`（{f.get('desc', '')}）")
                    else:
                        lines.append(f"  - `{f}`")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"- **修改文件**：{files_str}")
        else:
            lines.append("- **修改文件**：无")

        lines.append(f"- **备注**：{notes}")
        lines.append("")
        lines.append("---")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "exported": len(rows), "path": str(output_path)}))


# ── Argument Parser ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SQLite task manager for autorun skill")
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Create database and tables")

    # sync
    p_sync = sub.add_parser("sync", help="Sync TODO_LIST.md to DB")
    p_sync.add_argument("--file", default="TODO_LIST.md", help="Path to TODO file (relative to project root)")

    # next
    sub.add_parser("next", help="Get next pending task")

    # complete
    p_complete = sub.add_parser("complete", help="Mark task as completed")
    p_complete.add_argument("id", type=int)
    p_complete.add_argument("--conclusion", default="")
    p_complete.add_argument("--files", default=None, help="JSON array of modified files")
    p_complete.add_argument("--notes", default=None)

    # fail
    p_fail = sub.add_parser("fail", help="Mark task as failed")
    p_fail.add_argument("id", type=int)
    p_fail.add_argument("--notes", default=None)

    # retry
    p_retry = sub.add_parser("retry", help="Reset failed task to pending")
    p_retry.add_argument("id", type=int)

    # decompose
    p_decompose = sub.add_parser("decompose", help="Decompose task into subtasks")
    p_decompose.add_argument("id", type=int)
    p_decompose.add_argument("--subtasks", required=True, help="JSON array of subtask descriptions")

    # list
    p_list = sub.add_parser("list", help="List tasks")
    p_list.add_argument("--status", default=None)

    # count
    p_count = sub.add_parser("count", help="Count tasks")
    p_count.add_argument("--status", default=None)

    # export
    p_export = sub.add_parser("export", help="Export completed tasks to markdown")
    p_export.add_argument("--output", default="FINISH_LIST.md")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "sync": cmd_sync,
        "next": cmd_next,
        "complete": cmd_complete,
        "fail": cmd_fail,
        "retry": cmd_retry,
        "decompose": cmd_decompose,
        "list": cmd_list,
        "count": cmd_count,
        "export": cmd_export,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
