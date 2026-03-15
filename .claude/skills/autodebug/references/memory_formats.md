# 记忆文件格式参考

SKILL.md 引用本文件获取 history.md 和 insights.md 的完整模板。

---

## history.md 格式

### 文件头（首次创建时写入）

```markdown
# Debug History
> workspace 创建于 YYYY-MM-DD HH:MM:SS
> 问题: <一句话概括>
> 重点数据: 首次迭代后填充

---
```

### 迭代条目模板

```markdown
## Iteration N — YYYY-MM-DD HH:MM:SS
### 假设与预注册
- 假设: <本次验证的因果假设>
- 预期结果: <如果假设成立，指标应如何变化>
- 检验指标: <具体看哪个数值>
### 方向
<本次尝试什么，为什么选这个方向>
### 修改
- `path/file.py:LXX`: <具体改了什么>
### 执行结果
- 测试范围: <单条 <case_name> / 重点 case 集 / 完整验证>
- 最终指标: <值>
- 中间过程观察:
  - <观测量>: <值或趋势>
### 预测 vs 实际
- 预测: <...> | 实际: <...> | 偏差原因: <分析>
### 结论
<改善/恶化/信息收集 + 推导出的认知>
### 回退
<是否回退，回退了哪些文件>
---
```

### CRASH FIX 条目（语法/导入错误，不计为失败方向）

```markdown
## Iteration N (CRASH FIX) — YYYY-MM-DD HH:MM:SS
### 问题
<错误类型: SyntaxError/ImportError/...>
### 修复
- `path/file.py:LXX`: <修复内容>
### 结果
<修复后是否可正常运行>
---
```

### 基础设施迭代条目（创建验证脚本/数据分析/baseline/添加指标）

```markdown
## Iteration N (INFRA) — YYYY-MM-DD HH:MM:SS
### 类型
<验证脚本创建 / 数据分析与分类 / Baseline 建立 / 观测指标添加>
### 目标
<本次基础设施工作要达成什么>
### 执行
- <具体做了什么>
### 产出
- <创建了什么文件 / 分析了什么数据 / 建立了什么基线>
### 结论
<基础设施就绪状态更新>
---
```

---

## insights.md 完整模板

```markdown
# Debug Insights
> status: running
> iteration: N
> best_result: <最佳指标值>
> last_updated: YYYY-MM-DD HH:MM:SS

## 基础设施状态
<!-- 跟踪基础设施就绪情况，ReAct 循环据此判断是否需要触发 P2 优先级搭建基础设施 -->
- 快速验证脚本: ❌ 未创建 / ✅ 已创建 (<脚本名>)
- 数据分类: ❌ 未分析 / ✅ 已分析 (🔴 N条 🟡 M条 🟢 K条) / N/A（单一问题无需分类）
- baseline: ❌ 未建立 / ✅ 已建立 (Iteration X)

## 活跃假设 (Active Hypotheses)
<!-- 当前排序后的因果假设列表，驱动下一步行动 -->
1. **H1: <描述>** — 置信度: 高/中/低
   - 来源: <观察/文献/代码分析> | 验证方法: <...> | 预期: <...> | 状态: 待验证/已证实/已否定
2. **H2: <描述>** — 置信度: 中
   - ...

## 当前观测手段 (Instrumentation)
<!-- 代码中当前添加的中间输出及其作用 -->
- `file.py:LXX`: print 关键变量值/中间状态
- `module.py:LYY`: 添加的断言或日志输出
<!-- 随迭代演进，不再有用的可移除 -->

## 过程观察结论
<!-- 从中间输出中提炼的认知 -->
- 观察1: 现象 → 推断
- 观察2: 现象 → 推断

## 有效方向 (Successful)
1. **方向名** — 做法 → 效果 (Iteration X)

## 失败尝试 (Failed)
1. **方向名** — 具体做法（含参数/位置）→ 结果和原因 (Iteration X)

## 策略转折点 (Pivots)
<!-- 记录重大方向转换，帮助理解调试轨迹 -->
- Iter N: 从 <A> 转向 <B>，原因：<观察>

## 数据分级验证状态
<!-- 各 case 的最新指标，避免重复跑已验证的数据 -->
- 🔴 <case_name>: <metric>=X (Iter N)
- 🟡 共性问题: 最后验证于 Iter M
- 🟢 正常 case: 最后验证于 Iter M

## 待探索方向
1. 方向 — 优先级: 高/中/低 — 理由
2. 方向 — 优先级: 中 — 理由
<!-- 空列表 = 无方向可探索 = 下次写 status: done -->
```

---

## status 字段有效值

| 值 | 含义 | 外部循环行为 |
|---|---|---|
| `running` | 正常迭代中（含首次迭代的基础设施搭建） | 继续 |
| `done` | 调试完成或无法继续 | 停止 |

> **兼容性**：任何非 `done` 的 status 值（包括旧版 `initializing`）都按 `running` 处理。
