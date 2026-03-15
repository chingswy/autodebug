# Autodebug

让 AI 像人类工程师一样调 bug：**先猜原因，再改代码，然后跑一下看对不对，不对就换个思路再来**——自动循环，直到修好为止。

```
┌─────────────────────────────────────────────────────────┐
│                     Autodebug 工作流程                    │
│                                                         │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│   │ 读记忆文件 │───→│ 猜原因    │───→│ 改代码    │         │
│   │ 了解现状   │    │ (提出假设) │    │ (最小改动) │         │
│   └──────────┘    └──────────┘    └──────────┘         │
│        ↑                                │               │
│        │                                ↓               │
│   ┌──────────┐                   ┌──────────┐          │
│   │ 写记忆文件 │←──────────────────│ 跑测试    │          │
│   │ 记录经验   │   猜对了？修好！   │ 验证结果   │          │
│   └──────────┘   猜错了？下一轮    └──────────┘          │
│                                                         │
│   记忆文件 = brief.md + history.md + insights.md         │
│   （让每一轮都知道之前试过什么、学到了什么）                   │
└─────────────────────────────────────────────────────────┘
```

基于 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) Skill 构建，每次调用完成一轮"分析→改代码→验证→记录"，外部脚本驱动循环直到问题解决。

## 快速开始

### 前置要求

- 已安装 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- 将本项目作为 Claude Code 的 skill 目录（`.claude/skills/autodebug/`）

### 方式一：交互式单次调用

在 Claude Code 中直接使用 skill 命令：

```
/autodebug /path/to/workspace
```

每次调用完成一个完整的迭代。首次运行会进入 Startup 阶段，向你询问问题背景并构建代码知识库。

### 方式二：自动循环（推荐）

使用外部循环脚本，自动反复调用直到调试完成或达到上限：

```bash
# 基础用法（使用 claude -p 非交互模式）
python .claude/skills/autodebug/run_loop.py --workspace /path/to/workspace

# 指定最大迭代次数
python .claude/skills/autodebug/run_loop.py --workspace /path/to/workspace --max-iterations 20
```

如果你的 CLI 工具是交互式的（如 `claude-internal`），使用交互式循环脚本：

```bash
# 交互式循环（通过 PTY + 文件轮询检测迭代完成）
python .claude/skills/autodebug/run_loop_interactive.py --workspace /path/to/workspace

# 指定 CLI 命令
python .claude/skills/autodebug/run_loop_interactive.py --workspace /path/to/workspace --cli-command claude-internal
```

### Workspace 准备

1. 创建一个 workspace 目录
2. 参照模板 `.claude/skills/autodebug/references/brief_template.md` 创建 `brief.md`，至少填写：
   - **问题描述**：你观察到的具体现象
   - **代码入口**：关键文件路径
   - **运行命令**：如何执行验证
   - **目标指标**：什么算"修好了"

其余内容（代码知识库、数据分析、观测映射等）由 Skill 在 Startup 和后续迭代中自动填充。

## 项目结构

```
.claude/skills/autodebug/
├── SKILL.md                        # Skill 主文件（执行流程定义）
├── run_loop.py                     # 外部循环脚本（非交互式 CLI）
├── run_loop_interactive.py         # 外部循环脚本（交互式 CLI）
└── references/
    ├── brief_template.md           # brief.md 模板
    ├── startup_protocol.md         # Startup 阶段协议
    ├── decision_engine.md          # ReAct 决策引擎详细流程
    └── memory_formats.md           # history.md / insights.md 格式定义
```

运行后 workspace 目录中会生成：

```
workspace/
├── brief.md          # 问题背景 + 代码知识库（人类可编辑）
├── history.md        # 完整迭代记录（追加写入）
├── insights.md       # 活跃假设 + 结论 + 方向（每轮安全重写）
├── test_quick.*      # 快速验证脚本（自动创建）
├── backups/          # 每次迭代的代码备份
│   ├── iter_1/
│   ├── iter_2/
│   └── ...
└── logs/             # 循环脚本日志（交互式模式）
```

## 设计理念

### 假设驱动，不靠碰运气

每次改代码之前，必须先说清楚"我认为问题出在哪里、为什么"，然后预测改完之后结果会怎样。跑完测试后，拿实际结果和预测对比——猜对了说明理解对了，猜错了就修正认知。这比盲目试错高效得多。

### 一切都是迭代

没有"准备阶段"和"正式调试"的区分。写测试脚本、分析数据、跑 baseline——这些和改代码一样，都是一轮迭代。工具用优先级自动决定每轮该干什么：

| 优先级 | 该干什么 | 举例 |
|--------|---------|------|
| P1 | 先让程序跑起来 | 修语法错误、缺少 import |
| P2 | 补齐观测手段 | 写验证脚本、建 baseline、加日志 |
| P3 | 验证最靠谱的猜测 | 观测充分了，改代码验证假设 |
| P4 | 找新方向 | 没头绪时，读代码/数据找线索 |

### 三个文件当记忆

AI 的上下文窗口有限，调多了就忘了前面试过什么。所以用三个文件做"外部记忆"：

- **`brief.md`**：问题是什么、代码怎么组织的（背景知识）
- **`history.md`**：每一轮做了什么、结果如何（操作日志）
- **`insights.md`**：当前还有哪些猜测、已经确认了什么（认知状态）

每轮开始先读这三个文件，结束时更新它们。这样即使 AI 上下文被清空，也不会丢失之前的进展。

### 不会在死胡同里打转

内置防循环机制：自动检测是否在重复尝试同样的东西。如果同一个方向连续失败 3 次，强制换方向。

## 调试哲学

这个工具内化了几条调试原则：

- **不理解就不动手** — 改代码之前，必须能说清目标模块的输入、输出和数据流
- **用事实说话** — 猜测只用于缩小范围，定位根因必须靠实验观察
- **每次只改一个东西** — 控制变量，确认因果
- **不断缩小范围** — 二分法逼近，用最小配置隔离问题
- **对自己的假设保持怀疑** — 尤其是调了很久还没找到原因的时候

## Acknowledgements

本项目的设计受到以下工作的启发：

- [autoresearch](https://github.com/karpathy/autoresearch) by Andrej Karpathy — AI agent 自主进行神经网络实验的迭代循环框架。Autodebug 的外部循环设计（agent 修改代码 → 运行实验 → 根据结果迭代）直接受其影响。
- [如何找到实验不 work 的原因](https://pengsida.notion.site/1aee6e718de6472f834d13da8f4ff097) by Sida Peng — 关于系统性定位实验失败根因的方法论。Autodebug 的假设驱动调试思想、控制变量原则、以及"不理解就不动手"的核心理念与该文一脉相承。
