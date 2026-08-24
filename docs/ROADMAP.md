# VibeTrace 项目规划文档：AI 编程深度追踪

> **文档版本**：v1.2 | **更新日期**：2026-08-23 | **状态**：已落地至 v2.8.2（2026-08-23）

## 一、项目定位与愿景

### 1.1 项目简介

**VibeTrace** 是一款 Windows 平台的开源电脑使用行为监控工具。与市面上其他同类工具不同，本项目将 **“AI 辅助编程”** 作为核心追踪维度，致力于帮助开发者量化、分析和优化自己在 AI 时代的编程效率与成本。

### 1.2 核心价值

- **量化 AI 编程投入**：准确记录你每天/每周在 AI 辅助编程上投入的时间、成本和产出。
- **洞察编程行为模式**：通过数据透视你的“Vibe Coding”习惯，发现效率瓶颈和优化空间。
- **本地优先，隐私至上**：所有数据本地存储，零联网上传，仪表盘仅监听本地回环地址。

### 1.3 与竞品的差异化

| 维度 | ActivityWatch / Tai | VibeTrace |
|------|---------------------|--------------|
| 核心定位 | 通用时间追踪 | **AI 编程专用场景追踪** |
| 会话粒度 | 应用/窗口级别 | **AI 对话轮次级** |
| 成本维度 | 不涉及 | **API 用量成本估算** |
| 分析深度 | 使用时长统计 | **质量分析 + 行为洞察** |

## 二、当前功能回顾（已完成）

- ✅ 前台应用与窗口标题实时监控
- ✅ 进程树解析与活动时长统计
- ✅ 浏览器历史记录解析（Chrome/Edge/Firefox）
- ✅ 社交联系人识别
- ✅ **AI 编程时段识别**（窗口标题 + 进程树双重识别）
- ✅ AI 会话深度统计（opencode/ChatGPT/Claude/Cursor/Windsurf/Trae/DeepSeek/Pi Agent/DSH）
- ✅ 日报/周报仪表盘（本地 Web 服务）
- ✅ 可选 SQLite 后端与一致性校验（JSONL 仍为原始事实源）
- ✅ 纯 Python + ctypes，零第三方运行时依赖
- ✅ 打包为独立 exe，支持安装/卸载、应用内更新
- ✅ 统一测试体系：pytest 483 项用例（原 test_all.py 336 项断言已全部并入 pytest 分层，test_all.py 退役）
- ✅ ROADMAP Phase 1：对话轮次 / Token 估算 / 按模型·项目拆分 / 会话详情面板与日报章节
- ✅ ROADMAP Phase 3：按模型费用估算 / 按项目成本分摊 / 成本面板与日报成本章节（+ 周/月汇总成本账本）
- ✅ ROADMAP Phase 4：死循环检测 + 专注度评分 + Vibe 编程人格分析（洞察页面板与日报今日建议）
- ✅ ROADMAP Phase 2：Git 代码变更分析（`git_insights.py` 只读本地提交，代码产出/修改率）
- ✅ AI 会话质量评分（`ai_sessions.quality_score`：提问含金量/返工/稳定性/上下文健康度四因子加权 0–100 分档）
- ✅ Vibe 时间轴回放（`timeline.py` + `/api/timeline`）
- ✅ 成本预算告警（`budget.py` + `/api/budget`）
- ✅ 多工具横向对比（`tool_compare.py` + `/api/ai-compare`）
- ✅ 能力成长曲线（`growth.py` + `/api/trend`）
- ✅ 受限模板查询（`query.py` + `/api/query`，正则白名单不嵌大模型）
- ✅ 告警闭环（`alerts.py`：预算 warn/exceed + 连续工作休息提醒，托盘气泡）
- ✅ 每日目标 streak（`goals.py`：总活跃/编码时长目标 + 连续达成天数）
- ✅ 个性化基线（`learn.py`：滑动窗口 + z-score 常态检测）
- ✅ 性能指纹缓存（ai_sessions / browser_history / sqlite_store 提速）
- ✅ 覆盖率门禁 70%（pytest 483 项，实测 79%）
- ✅ Git 侧采纳率代理指标（`adoption.py` + `/api/adoption`：retention/reworked_ratio 粗代理，免责+折叠展示，confidence 永不 high；AI 侧 per-file 归因按 spike 结论判砍）
- ✅ 受限查询模板扩充（q6 产出对比 / q7 专注度最佳日 / q8 成本趋势，双周期解析与周期别名）
- ✅ dashboard 纯函数外置 `dashboard_util.py` + frontend smoke / e2e 冒烟测试上线

## 三、AI 功能深化方向（路线图）

### Phase 1：会话级精细追踪（优先级：高 | 难度：中）

**目标**：从“用了多久 AI”升级到“AI 在做什么”。

| 功能点 | 描述 | 实现思路 |
|--------|------|----------|
| **会话数统计** | 记录每天启动了多少次 AI 编程会话 | 检测 IDE 窗口 + AI 工具窗口（如 Copilot Chat 面板）的激活次数 |
| **对话轮次追踪** | 统计每次会话中的提问/回答轮数 | 结合浏览器历史中的 ChatGPT/Claude/Cursor 页面访问序列，利用 DOM 或 URL 变化推断轮次 |
| **Token 消耗估算** | 根据模型和对话长度估算输入/输出 Token | 参考各 API 定价文档，按文本长度粗略估算 |
| **按模型拆分** | 区分不同的 AI 模型使用情况 | 从窗口标题或 URL 中提取模型名（如“Claude 3.5 Sonnet”“GPT-4o”） |
| **按项目拆分** | 追踪 AI 使用时间在哪个项目上 | 结合 IDE 当前打开的项目路径或 Git 仓库名 |

**技术参考**：
- [ActivityWatch 的窗口追踪机制](https://github.com/ActivityWatch/aw-watcher-window)
- [Vibe Coding 成本追踪相关讨论](https://x.com/lancemartin/status/1899845951274582118)

### Phase 2：质量与效率分析（优先级：中高 | 难度：高）

**目标**：衡量 AI 编程的**产出质量**，而非仅仅衡量投入时间。

| 功能点 | 描述 | 实现思路 |
|--------|------|----------|
| **采纳率 (Acceptance Rate)** | 统计 AI 建议被接受的比例（**待插件事件源**） | 结合 IDE 插件 API 或检测编辑器中的“接受建议”快捷键事件（需 IDE 插件提供采纳事件源） |
| **代码留存率 (Retention Rate)** | 统计 AI 生成的代码在最终提交中的保留比例（**待插件事件源**） | 分析 Git diff 历史，对比 commit 前后的代码变更（需 IDE 插件事件源）；参考 [GitHub Next 的 Copilot 研究](https://githubnext.com/projects/copilot-metrics) |
| **修改率 (Modification Rate)** | 统计 AI 生成代码被手动修改的比例 | 结合编辑器的撤销/重做记录或 Git 逐行分析 |
| **Git 代码变更分析** ✅ | 衡量代码产出与改写/返工 | 配置 `insights.git.projects` 后用 `git log --numstat` 统计当日提交/增删行/改动文件，“修改率”=删除/变更（Phase 2 已落地） |
| **任务完成率** | 统计开启 AI 会话后，任务被标记为“完成”的比例 | 结合 IDE 中的任务管理插件或自定义标记 |

**技术参考**：
- [GitHub Copilot 采纳率研究](https://github.blog/2023-06-20-research-quantifying-github-copilots-impact-on-developer-productivity-and-happiness/)
- [Codeium 的开发者效率报告方法论](https://codeium.com/blog/dev-analytics)

### Phase 3：成本与 ROI 追踪（优先级：中 | 难度：低）

**目标**：把 AI 编程变成一笔“算得清账”的投入。

| 功能点 | 描述 | 实现思路 |
|--------|------|----------|
| **按模型的费用估算** ✅ | 结合 Token 用量 × 单价计算成本 | 内置主流模型定价表 + config 自定义单价（Phase 3 已落地） |
| **按项目的成本分摊** ✅ | 将 AI 费用分摊到具体项目 | 基于 Phase 1 的 by_project 数据（Phase 3 已落地） |
| **“时间节省”估算** ✅ 已落地（粗估版） | 基于 AI 编程时长 × 效率因子离线粗估（仅参考） | `insights.time_saved`（默认 factor 2.0，1.0-5.0 可调，数据少时标注“仅作参考”；待接入 Git 行数做可信估算）（v2.4.0 已落地） |
| **自动化支出报表** ✅ | 自动生成“AI 编程账本” | 仪表盘概览成本面板 + 日报成本章节 + 月报/周报自动追加速「AI 成本账本」章节（Phase 3 已落地） |

### Phase 4：行为洞察与智能诊断（优先级：中低 | 难度：中高）

**目标**：从数据中“读懂”你的编程习惯，给出可行动的建议。

| 功能点 | 描述 | 实现思路 |
|--------|------|----------|
| **“死循环”检测 (Death Loop)** ✅ | 检测在多个应用间高频切换的低效模式 | 监测窗口切换频率和序列；时间窗内密集短会话高频反复切换判定（Phase 4 已落地） |
| **专注度评分** ✅ | 基于持续编码时长、切换频率等计算每日专注分数 | 最长专注段 + 编码占比 − 切换负担，0–100 分级（Phase 4 已落地） |
| **Vibe Coding 人格分析** ✅ | 根据使用模式生成有趣的“编程人格标签” | 基于当日活动分布的加权打分规则（Phase 4 已落地：10 种脸谱） |

## 四、分阶段实施路线图

### v1.0（当前）— 基础追踪
- ✅ 前台应用/窗口监控
- ✅ 基础 AI 时段识别
- ✅ 日报/周报仪表盘

### v1.5 — 会话级精细化 ✅（2026-08 · 已随 v2.3.0 规划落地）
- [x] 对话轮次追踪（本地会话 user→assistant 配对 + 浏览器历史深度解析 Web AI 会话）
- [x] Token 用量估算（`ai_sessions.token_estimation`，CJK 1 Token/字、其余 4 字符/Token）
- [x] 按模型/项目拆分（`by_model` / `by_project`，会话级归口）
- [x] 仪表盘新增“AI 会话详情”面板（+ 日报「AI 会话深度」章节）

### v2.0（预计 2027 Q1）— 质量与成本
- [ ] 采纳率/留存率分析（**待插件事件源**：需 IDE 插件提供采纳/留存事件源）
- [x] API 成本追踪与报表（AI 会话费用估算已落地，Phase 3）
- [x] Git 集成（代码变更分析）

### v2.5（预计 2027 Q2）— 智能洞察
- [x] 死循环检测与提醒
- [x] 专注度评分
- [x] Vibe Coding 人格分析（趣味功能）
- [x] 受限模板查询（`query.py` + `/api/query`，正则白名单不嵌大模型；v2.5.0 已落地）
- [ ] 自然语言查询（嵌入本地小模型，如 Qwen Coder，仍需外部模型/事件源）

## 五、技术架构扩展建议

### 5.1 数据存储升级

当前以 JSONL 原始日志为主，并可选维护 SQLite 索引（`usage.db`）。未来若面临高频写入和大数据量查询需求：
- 可考虑引入时间序列数据库（如 [QuestDB](https://questdb.io/)），或优化 SQLite 的分区表策略。

### 5.2 IDE 插件扩展

为实现“采纳率/留存率”分析，需要开发 IDE 插件（**待插件事件源**：采纳率/留存率需 IDE 插件提供事件源后方可落地）：
- **VS Code**：通过 [VS Code API](https://code.visualstudio.com/api) 监听 `vscode.workspace.onDidChangeTextDocument` 等事件。
- **JetBrains 系列**：通过 [IntelliJ Platform Plugin SDK](https://plugins.jetbrains.com/docs/intellij/welcome.html) 实现类似功能。

### 5.3 浏览器扩展

替代当前基于历史记录的解析方案，提供更精准的 AI 活动追踪：
- Chrome/Edge 扩展：通过 [chrome.tabs](https://developer.chrome.com/docs/extensions/reference/tabs/) 和 [chrome.webNavigation](https://developer.chrome.com/docs/extensions/reference/webNavigation/) API 实时捕获 AI 工具使用情况。

## 六、相关资源与参考项目

| 项目 | 简介 | 可借鉴点 |
|------|------|----------|
| [ActivityWatch](https://github.com/ActivityWatch/ActivityWatch) | 15k+ Star 的跨平台时间追踪 | 数据模型设计、插件架构 |
| [Tai](https://github.com/Planshit/Tai) | Windows 软件/网站时长统计 | 窗口追踪实现、UI 设计 |
| [Wakatime](https://wakatime.com/) | 开发者编程时间追踪（商业产品） | 项目拆分、语言/IDE 维度统计 |
| [GitHub Copilot 官方研究](https://github.blog/2023-06-20-research-quantifying-github-copilots-impact-on-developer-productivity-and-happiness/) | 采纳率与效率的学术研究 | 质量分析的学术方法论 |

## 七、FAQ

**Q：这些 AI 深化功能会不会让项目变得太复杂，偏离了“简单本地工具”的初衷？**
A：所有新功能都作为**可选模块**，用户可在配置中按需开启。核心的“零依赖、本地优先”原则不会改变。

**Q：如何保证新增的“浏览器深度解析”不侵犯用户隐私？**
A：所有解析在本地完成，数据永不离开用户设备。仪表盘访问仅限 `127.0.0.1`。未来若引入 IDE 插件，同样遵循本地优先原则。

**Q：这些功能我一个人能实现吗？**
A：分阶段推进，每个 Phase 都可以独立交付，不必一次性完成。欢迎社区贡献 —— 这也是开源项目的魅力所在。

## 八、加入我们

- 📦 项目地址：[github.com/Niangaol/VibeTrace](https://github.com/Niangaol/VibeTrace)
- 🐛 问题反馈：[Issues](https://github.com/Niangaol/VibeTrace/issues)
- 💡 功能建议：欢迎提交 Feature Request 或直接发起 PR

## 九、工程与代码优化路线图（Code & Structure Optimization）

> 基于对当前代码结构的审视，梳理出一批"低风险、行为不变"的工程优化点，按优先级排列，作为后续维护的路线图。

### 9.1 现状审视（优点）

- 零裸 `except`；无未使用 import（`from __future__ import annotations` 为有意设计）。
- 已有成熟缓存范式：`report._agg_cache` 按 mtime/size、`classifier.load_config` 按 mtime+TTL、`dashboard._token_cache` 短时 TTL——均为"改配置后数秒生效"的一致性做法。
- `.gitignore` 完善：运行数据（日期文件夹 / `usage.db`）、构建产物（`build/`、`dist/`）、隐私配置（`aliases.json`、`app_groups.json`、`ai_custom.json`）均忽略。
- ~~双测试体系~~ 已合并：`test_all.py` 的 47 个测试函数整体移植为 `tests/` 四个主题模块 + 共享支撑层，另有全链路 E2E 七阶段（造数→镜像→报表→API→写循环→备份恢复→安全）。

### 9.2 优化项（按优先级）

| # | 优化项 | 改动 | 优先级 | 风险 | 状态 |
|---|--------|------|--------|------|------|
| 1 | **前端模板外抽** | `dashboard.py` 内约 2400 行内联 `PAGE_TEMPLATE`（HTML/JS）抽到 `assets/dashboard.html`，运行时加载（兼容 `sys._MEIPASS` 与 `paths.script_dir()`），`VibeTrace.spec` 的 `datas` 增加该文件；`dashboard.py` 由约 3850 行降至约 1450 行 | 中 | 低（沿用既有打包范式） | 已规划 |
| 2 | **`_available_days` 加 mtime/TTL 缓存** | 仿 `_agg_cache` 范式，单次请求内多次调用（如 `/api/days`+`/api/dates`、`_collect_known_apps` 两次切片）与长历史安装（数百日期文件夹）省去重复 `os.listdir` | 高 | 极低 | 已规划 |
| 3 | **`applog.read_recent` 流式读尾部** | `collections.deque(maxlen=n)` 逐行迭代替代 `readlines()` 全量读入内存，长日志下显著降低内存占用，行为等价 | 高 | 极低 | 已规划 |

### 9.3 暂不做（记录原因）

- **命名统一 `usagemon`/`usagemonitor` → `vibetrace`**：代码里仍残留（`applog` logger 名、`USAGEMON_*` 环境变量、`usagemon_hist_*` / `usagemonitor-update` 临时目录、备份文件名）。但 `updater` 过渡期仍在用旧资产名 `UsageMonitor.exe`（git 已有"过渡期双名支持"），建议待过渡完成后单独 PR，避免打断更新链路。
- ~~合并双测试体系~~ **已完成**（原判「暂缓」）：采用机械整移而非逐条改写，零断言丢失、可静态对账（check/ok 调用数守恒），成本远低于当初评估。
- **`timeline.py`**：并非死代码，是生成 `assets/timeline_preview.html` 的独立预览工具，保留。
