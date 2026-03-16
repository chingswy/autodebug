---
name: auto-run
description: >
  自动批量执行 TODO_LIST.md 中的待办任务。通过 SQLite 数据库管理任务队列，
  每次精确取一条待办任务，通过 Task 子代理进行最小化代码改动。
  完成后更新数据库状态，并导出完成记录到 FINISH_LIST.md。
  子代理天然隔离上下文，无需手动压缩即可继续下一条。
  复杂任务会先拆解为独立子任务再添加回队列。
  当用户说"自动执行 TODO"、"批量处理 TODO"、"auto run"、"跑一下 TODO"、
  "帮我把 TODO 做了"、"执行待办"、"run todos"时触发。
  也适用于用户说"继续做 TODO"、"接着上次的 TODO"、"把剩下的任务做完"等场景。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# auto-run Skill（SQLite 版）

通过 SQLite 数据库精确管理任务队列。每次取**一条**待办任务派发给独立子代理执行，
完成/失败后更新数据库，自动继续下一条直到所有任务完成。

## 使用方式

- `/auto-run` 或 `/auto-run --all` — 执行所有未完成任务，自动连续运行直到完成
- `/auto-run 3` — 最多执行 3 条任务后停止
- `/auto-run --dry-run` — 只列出所有未完成任务，不实际执行
- 自然语言："帮我跑一下 TODO"、"自动执行待办任务"

根据 `$ARGUMENTS` 决定行为：
- 空 或 `--all` → 执行所有未完成任务，自动连续运行，不中途询问
- 数字 N → 最多执行 N 条后停止
- `--dry-run` → 只列出所有未完成任务，不执行

---

## 任务管理工具

所有任务管理通过 `task_manager.py` CLI 完成：

```bash
TM=".claude/skills/autorun/task_manager.py"
```

| 命令 | 作用 |
|------|------|
| `python $TM init` | 建库建表（幂等） |
| `python $TM sync --file TODO_LIST.md` | 增量同步 TODO_LIST.md → DB |
| `python $TM next` | 取一条 pending 任务（JSON），标记 in_progress |
| `python $TM complete <id> --conclusion "..." --files '[...]' --notes "..."` | 标记完成 |
| `python $TM fail <id> --notes "..."` | 标记失败 |
| `python $TM retry <id>` | 重置为 pending |
| `python $TM decompose <id> --subtasks '<JSON array>'` | 拆解为子任务 |
| `python $TM list [--status pending]` | 列出任务 |
| `python $TM count [--status pending]` | 返回数量 |
| `python $TM export [--output FINISH_LIST.md]` | 导出完成记录 |

所有命令输出 JSON，便于精确解析。

---

## 文件约定

### TODO_LIST.md 格式

```markdown
- [ ] [模块标签] 任务描述
- [ ] [模块标签] 另一条任务
```

- `- [ ]` 表示未完成，待导入
- `- [>]` 表示已导入 DB，不再重复导入
- `- [x]` 表示 DB 中已完成
- 模块标签（如 `[前端]`、`[后端]`）是可选的，用于分类

### FINISH_LIST.md

由 `export` 命令自动生成，包含所有已完成任务的详细记录。

---

## 核心原则

### 一次一条，自动推进

`next` 命令保证只返回**单条**任务 JSON。任务完成后更新 DB，
然后再调 `next` 取下一条，自动推进。

### 最小改动原则

每条 TODO 只做完成该任务所必需的最少代码改动。不顺手重构，不添加额外功能，
不修改不相关的代码。

### 复杂任务拆解

判断一条任务是否"复杂"的标准：
- 需要修改 3 个以上文件
- 涉及多个独立的逻辑变更
- 改动之间没有强耦合关系

如果任务复杂，使用 `decompose` 命令拆解为 2-5 个子任务，然后从第一个子任务开始。

### 上下文管理

每条任务通过 **Task 工具派发给独立子代理（subagent）** 执行。
子代理拥有独立上下文窗口，任务间互不干扰，主会话只保留调度信息。

---

## 执行流程

### Phase 0 — 初始化与同步

```bash
python $TM init
python $TM sync --file TODO_LIST.md
```

每次启动都执行：
1. `init` 确保数据库和表存在（幂等）
2. `sync` 检测 TODO_LIST.md 中的新增 `- [ ]` 行，导入 DB 后标记为 `- [>]`

### Phase 1 — 取下一条任务

```bash
python $TM next
```

返回单条 JSON：
```json
{"id": 1, "description": "...", "tag": "前端", "progress": "1/5", "started_at": "..."}
```

如果 `id` 为 `null`，说明没有待办任务，跳到 Phase 6。

### Phase 2 — 分析复杂度

1. 展示当前任务：
   ```
   📋 [1/5] [前端] 删除掉video数据管线相关的界面和入口
   ```

2. 判断任务复杂度：
   - 先阅读相关的 CLAUDE.md 模块文档
   - 评估需要修改的文件数量和改动范围

3. 复杂任务 → Phase 2b（拆解）
4. 简单任务 → Phase 3（执行）

### Phase 2b — 拆解复杂任务

```bash
python $TM decompose <id> --subtasks '["子任务1", "子任务2", "子任务3"]'
```

父任务标记为 `decomposed`，子任务按 position 排在父任务后面。
输出拆解结果后回到 Phase 1 取第一个子任务。

### Phase 3 — 执行任务（通过子代理）

使用 **Task 工具** 派发 `general-purpose` 子代理执行当前任务。

子代理 prompt 需包含：
- 当前任务的完整描述和 ID
- 相关 CLAUDE.md 模块文档路径
- 最小改动原则的提醒
- 要求返回以下格式的完成报告：

  ```
  【完成报告】
  结论：<一句话总结>
  修改文件：
    - path/to/file（说明）
  备注：<异常或注意事项；无则填"无">
  ```

### Phase 4 — 更新数据库

**成功时**：
```bash
python $TM complete <id> --conclusion "成功删除 VideoView.vue 及相关路由" --files '["src/views/VideoView.vue", "src/router/index.ts"]' --notes "无"
```

**失败时**：
```bash
python $TM fail <id> --notes "编译报错，需要人工检查"
```

失败后**停止执行**，等待用户介入。用户可以用 `retry` 重置后重新运行。

输出完成摘要：
```
✅ 完成: [前端] 删除掉video数据管线相关的界面和入口
   耗时: 约 6 分钟 | 结论: 成功删除 VideoView.vue 及相关路由，共修改 3 个文件
   → 继续下一条...
```

### Phase 5 — 导出并继续

```bash
python $TM export --output FINISH_LIST.md
```

导出最新的完成记录后，回到 Phase 1 取下一条任务。

- 如果指定了数量 N 且已完成 N 条 → 停止并汇总
- 任务失败 → 停止运行，等待用户处理

### Phase 6 — 全部完成

```bash
python $TM export --output FINISH_LIST.md
python $TM count
```

输出总结：
```
🎉 所有 TODO 已完成！共执行 N 条任务，详细记录见 FINISH_LIST.md
```

---

## 边界情况处理

### 任务描述模糊

如果任务描述不够具体，用 AskUserQuestion 询问用户澄清，而不是猜测。

### 任务涉及删除功能

1. 先搜索所有引用点（import、路由、配置等）
2. 从叶子节点开始删除
3. 确认没有其他代码依赖被删除的部分

### 改动出错需要回退

发现改错时，用 `git checkout -- <file>` 恢复文件。
使用 `fail` 命令标记任务失败，停止运行等待用户处理。

---

## 重要约束

- **遵守目录安全规则**：不对数据目录递归搜索
- **中文优先**：任务描述、日志输出使用中文
- **不自动 commit**：改完代码后不自动提交，由用户决定何时 commit
- **优先读文档**：根据 CLAUDE.md 模块索引了解代码结构，减少直接读大量源码
- **失败即停止**：任务失败时标记 DB 为 failed，停止执行等用户介入
