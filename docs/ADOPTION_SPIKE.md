# ADOPTION_SPIKE.md — 无插件采纳率近似归因 spike 记录（v3.0 · P9）

> 对应 `docs/VIBECODING_IMPLEMENTATION_GUIDE.md` §6.1「功能 A：无插件采纳率/留存率近似归因（先 spike，失败就砍）」。
> 本文件是 **spike 过程与结论的完整记录**，不是功能设计文档；若结论为「砍」，本文件即作为
> 「不可行及其原因，留待插件方案」的存档（guide §6.1 验收条第 2 点）。
>
> **结论（摘要）**：**可做「仅 Git 侧」的近似（acceptance / reworked_ratio），
> 但「AI 侧」的 per-file 归因（ai_generated_ratio / retention）不可行** ——
> 实测真实数据 join 命中率 = 0.0%（远低于 30% 验收线），时间窗重叠信号在真实数据上为 0。
> **建议：砍掉 per-file AI 归因，仅保留带强制免责声明的 Git 侧代理指标，或整体纳入 v3.0 观察列表等插件方案。**
> 若保留任何近似值，必须显著标注「非精确/仅参考」并折叠展示（guide §12 风险 R1）。
>
> **落地状态（2026-08-23 · v2.8.0）**：本 spike 的「Git 侧代理指标」分支**已落地**——
> `adoption.py` 已收敛为纯 Git 侧实现（不再 import ai_sessions / 不读 mtime），
> 经 `/api/adoption?date=` 暴露，洞察页折叠 + 灰色降权展示；AI 侧 per-file 归因
> 维持「判砍」，待 IDE 插件事件源再评估。本文其余内容保留为过程存档。

---

## 1. 背景与目标

VibeTrace 无插件事件源（无 IDE 插件上报「哪行代码由哪条 AI 消息生成」）。
guide §6.1 要求先验证一个命题：

> 「AI 会话产出」与「Git 提交产出」之间是否有可用相关性？

若能验证 → 提供 `approximate_retention`（AI 产出进提交的比例）与 `approximate_acceptance`（提交未被删除的比例）；
若验证失败 → 记录不可行原因，留待插件方案。

**本次 spike 任务**：基于 `git_insights`（git numstat：per-repo/per-file 真实变更）+ `ai_sessions`
（会话时间窗 + 项目归口 + generated_lines 估算）+ **文件 mtime** 三重启发式，实现
`adoption.py::adoption_stats(date, data_root, config)` 雏形并**量化误差边界**。

---

## 2. 启发式方案（adoption.py 已实现）

### 2.1 三信号输入

| 信号 | 来源 | 内容 | 属性 |
|---|---|---|---|
| Git 真实变更 | `git_insights.analyze_repo`（直接调用，`top_files` 拉满） | 当日每仓库每文件 `added/deleted/churn`、`modify_ratio` | **真实**（git 纪实） |
| AI 会话 | `ai_sessions.collect` | 当日会话 `{project, first, last, turns}` + `total.generated_lines` | **估算**（消息级启发式） |
| 文件 mtime | `os.path.getmtime`（仓库工作区文件） | 文件最后写入时刻（本地 epoch） | **近似**（写盘时间≠生成时刻） |

### 2.2 join 规则（时间窗重叠）

- **项目归口**：仓库名（`insights.git.projects` 的 name，缺省取 basename）与 AI 会话 `project`
  做**双向子串匹配**（大小写不敏感，`_fuzzy_match`）。命中仓库拿到自己的会话窗集合。
- **时间窗**：每会话窗 = `[first - slack, last + slack]`，`slack` 默认 600 秒（`adoption.window_slack_s`）。
- **文件命中**：仓库内某文件 `mtime` 落在任一命中项目会话窗内 → `in_ai_window=True`（疑似 AI 触碰）。

### 2.3 派生指标（全部带「仅参考」强制声明 `_NOTICE`）

| 指标 | 公式 | 口径 | 除零兜底 |
|---|---|---|---|
| `per-file ai_generated_ratio` | 项目 `generated_lines` 按会话 turns 占比分摊 → 窗口内文件按 added 占比再分摊 → `min(1, 分摊行/added)` | AI 生成行 / 文件新增行 | added=0 → None；无窗口/AI→0.0 |
| `per-file reworked_ratio` | `deleted / (added + deleted)`（逐文件 modify_ratio） | 「返工/改写」近似代理 | churn=0 → 0.0 |
| `per-project approximate_retention` | `lines_added / max(proj_ai_lines, 1)` | AI 产出落进提交的比例 | 分母 0 → None |
| `per-project approximate_acceptance` | `1 - modify_ratio` | 提交中未被删除的比例 | churn=0 → None |
| `confidence` | 无 AI 数据或 join_rate < 0.3 → `low`；否则 `medium` | 置信度 | 永远不给 `high`（缺 IDE 事件，物理天花板） |

**契约**：`{date, enabled, found, notice, summary, projects[]}`；任何单源失败 → 契约空态
（found=False，200 可展示，绝不 500）；单仓库失败仅跳过该仓库（best-effort）。

---

## 3. 误差边界分析（本次 spike 核心交付）

### 3.1 误差源清单

| # | 误差源 | 影响方向 | 量级估计 | 缓解/证据 |
|---|---|---|---|---|
| E1 | **`generated_lines` 本身是估算**（`ai_sessions._generated_lines` 按换行计数；assistant 消息可能只含工具调用/空内容） | 双向（低估/高估） | 实测多日为 **0** | 误差直接传导至 ratio/retention；无法缓解，只能标注 |
| E2 | **AI 会话窗 = 消息时间戳的最小-最大**，不是真实编辑时长；实测多为**秒级窗口**（如 03:22:59→03:23:07） | 系统性低估窗口 | ±数分钟~数小时 | slack 只缓解秒级抖动，救不了「会话在上午、提交在中午」的脱节 |
| E3 | **mtime ≠ 生成时刻**：写盘时间晚于 AI 生成、早于 git 提交；`git checkout/clone/stash` 会批量改 mtime；构建/格式化工具也会 touch | 双向，偏假阴性 | 实测 12:17 写盘 vs 12:18 提交（差 ~1 分钟）；跨天更常见 | 无法可靠识别；spike 实测见 §4 |
| E4 | **项目名 join 靠子串**：AI 会话 `project` 常是目录 basename（如 `niangao`/`AgentPrograms`），与仓库名（`VibeTrace`）不匹配 | 假阴性为主 | 实测 3 天全部 0 命中 | 无解（除配置别名白名单，属过度工程） |
| E5 | **`by_project` 无 generated_lines 维**：只能按 turns 占比从 total 分摊 | 双向 | 同一会话多项目时失真 | 已实现 turns 兜底；标注即可 |
| E6 | **同一日人工/多工具混编同一文件**：无法区分 AI 与人工行 | 双向 | 无界 | 物理不可分（guide 明示「做不到」），只做聚合归口 |
| E7 | **删行 ≠ 返工**：重构、格式化、移动会放大 deleted | 单向高估 reworked | 实测 acceptance 0.50~0.95 波动 | 标注「粗代理」 |
| E8 | **未提交工作区不可见**：写完未 commit 的 AI 产出不计入 git | 单向低估 retention | 视用户习惯，可达 100% | 无法缓解 |
| E9 | **跨午夜会话**：会话 first 在 D-1、last 在 D，按消息日期归口可能整会话落到别的天 | 双向 | 少见，小时级 | 标注 |

### 3.2 判定阈值（guide §6.1 验收标准）

- **join 命中率**（窗口内文件数 / 当日 git 文件数）< **30%** → 判「proxy 无信息量」→ **砍**；
- **逐日方差**：`approximate_retention` / `approximate_acceptance` 的变异系数（CV=std/mean）过大（>50%）→ 判「不稳定」→ **砍**；
- 二者同时通过 → 才允许进入正式实现（且仍标 low/medium confidence + 免责声明）。

---

## 4. 实测验证（真实仓库 VibeTrace @ D:\VibeTrace，2026-08-17/18/20）

> 环境：Git for Windows + 本机真实 `~/.claude` 会话 + 真实 worktree mtime，零 mock。
> `adoption.py` 直接跑真实 config（`insights.git.projects=[D:/VibeTrace]`，slack=600s）。

| 日期 | git 文件数 | AI 窗口数 | join 命中率 | retention | acceptance | confidence |
|---|---|---|---|---|---|---|
| 2026-08-17 | 21 | 1 | **0.000** | None | 0.4955 | low |
| 2026-08-18 | 15 | 1 | **0.000** | None | 0.9500 | low |
| 2026-08-20 | 57 | 15 | **0.000** | None | 0.9366 | low |

**逐日方差（acceptance）**：mean≈0.79，std≈0.21 → **CV≈27%**（勉强达标，但样本仅 3 天且语义为
git 全局修改率，与 AI 无关）。

**致命事实**：
1. `generated_lines` 三日全为 **0**（assistant 消息多为工具调用/空内容）→ retention/per-file ratio 分母无意义；
2. **AI 会话窗全部在 03:00 前后、git 提交在 11:34–12:18、mtime 在 12:17–16:22** —— 三者**完全没重叠**，
   join 命中率 0%；
3. 会话 `project` 归口到 `niangao`/`AgentPrograms`（home/一级目录 basename），与仓库名 `VibeTrace` 子串不匹配；
4. demo_data 无 git 仓库（`insights.git` 未配置），无法构造同源对照。

**结论（验收）**：join 命中率 = 0% < 30% 且 retention 全部 None —— **踩线判砍**。

---

## 5. 结论与建议

### 5.1 判定：砍（per-file AI 归因）

| 指标 | 判定 | 理由 |
|---|---|---|
| per-file `ai_generated_ratio` / `approximate_retention` | **砍** | 需要 mtime×会话窗 overlap，实测 0% 命中；`generated_lines` 常为 0，无信号可归因 |
| per-file `reworked_ratio` / `approximate_acceptance` | **可保留（仅 Git 侧）** | 纯 git 派生、无 AI 依赖、真实稳定；但语义是「churn 中被保留的比例」，**不是采纳率** |

### 5.2 若保留的约束（不可让步）

1. **所有近似值强制附 `notice`/免责声明**（`_NOTICE` 已内置：「无插件近似归因，非真实采纳率，仅供参考」）；
2. UI 必须**折叠 + 灰色降权**展示，不可与真实指标并列（guide §7 数据矩阵：低置信、易误导 → 藏起来）；
3. 永远不给 `confidence=high`；只允许 `low` / `medium`；
4. 不落库、不参与报表汇总、不进入任何对比/成长曲线（与 tool_compare/growth 解耦）。

### 5.3 正式方案建议（若 v3.0 仍想做归因）

- **插件事件源才是正解**：v3.0 规划已明确（guide §6.1 原文「物理做不到」），
  优先规划插件版（IDE 侧记录 diff 归属）；本 spike 的 per-file 结构（paths + ratios）可作为插件版返回契约的雏形；
- 若一定要做「近似采纳率」：只做 **Git 侧漏斗**=「当日新增行中最终被保留的比例」
  （`lines_added / (lines_added + lines_deleted)` 全局口径），不掺 AI 时间窗 —— 诚实、稳定、无假阳性。

### 5.4 回滚范围

砍掉本功能仅影响：`adoption.py`（新文件，可删或留作插件版契约参照）、
`tests/unit/test_adoption.py`（测试保留，守护「诚实标注 + 降级不崩」的契约）、
`pyproject.toml` coverage 列表加了一项 `adoption`。**不影响任何既有模块/端点/数据**。

---

## 6. 交付物清单

| 交付物 | 状态 | 说明 |
|---|---|---|
| `docs/ADOPTION_SPIKE.md` | ✅ 本文件 | spike 推理链 + 实测证据 + 判定 |
| `adoption.py` | ✅ 骨架实现 | `adoption_stats(date, data_root, config)` + 契约空态 + CLI |
| `tests/unit/test_adoption.py` | ✅ 20 用例 | 空数据/降级/时间窗匹配/文件匹配/返工近似/除零，全部确定性（monkeypatch 两源） |
| 全量回归 | ✅ | `pytest tests/` 229 passed；`ruff check` 通过 |