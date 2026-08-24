# 更新日志

本项目所有值得记录的变更都归档在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
发布流程：`git tag vX.Y.Z` 后由 CI 自动构建并发布 Release。

> 🌐 English version: [CHANGELOG.en.md](CHANGELOG.en.md)

## [未发布]

### 修复

- **模块级缓存线程安全**：dashboard 为多线程 HTTP 服务，但 `ai_sessions` 的 `_COLLECT_CACHE`/`_PARSE_CACHE`、`report` 的 `_agg_cache` 与 `_aliases_cache`、`dashboard_util` 的 days-cache 此前全部无锁——并发请求下 LRU 的 `move_to_end`/驱逐可抛「OrderedDict mutated during iteration」或脏读。五处统一补 `threading.Lock`（锁内只动表、解析与聚合计算留在锁外；days-cache 重扫留锁内防惊群）；新增 8 线程并发锤回归测试（含正面锤驱逐循环）
- **报表链配置流断裂**：`finalize_day → generate_day_report → generate_consolidated_md` 链路此前不透传配置，report 自行取全局默认——`--data-root`/`--config` 用户写在数据根或显式文件里的 ai_sessions/browser 等设置对每日报表不生效，且与仪表盘口径不一致。现全链路接通 `config_path`，解析优先级统一为 **显式 config_path > `<root>/config.json` > 全局默认**（与 dashboard `_load_config_for_root` 同语义；既有调用方零改动）。周/月报同病已定位、留待下批
- **暂停态退出语义**：守护循环暂停分支的 `continue` 会跳过循环尾部全部退出检查，「先暂停再退出」时线程永远等不到停止信号。现暂停等待前即响应 stop_event 与 test_seconds 到时

### 测试

- 新增 `tests/integration/test_report_config_flow.py`（双配置反向钉扎：谁把显式优先级改回去谁就红）、`tests/unit/test_cache_concurrency.py`、`test_stop_while_paused`（旧代码上挂死被 timeout 击杀的反证钉扎）；conftest 清理一处历史死代码

## [2.8.1] - 2026-08-23

> 主题：小版本收口——多日 AI 成本查询性能修复（121s→0.95s）+ 预算端点边界修复 + 测试体系合二为一（`test_all.py` 退役、全链路 E2E）。无新特性。

### 测试（2.8.1）

- **双测试体系合并**：`test_all.py`（47 个测试函数、336 项断言）按域机械整移为 pytest 四个主题模块——`tests/integration/test_monitor_scenarios.py`（15，监控/分类/跨天场景）、`tests/integration/test_report_content.py`（9，报表/浏览器历史/分组）、`tests/api/test_dashboard_surface.py`（5，仪表盘 API 面）、`tests/integration/test_insights_ecosystem.py`（18，洞察/AI 会话/更新器/sqlite）+ 共享支撑层 `tests/support/scenario.py`；断言零丢失（check/ok 调用数静态守恒），`test_all.py` 物理删除，CI 删除 legacy 步骤，pyproject omit 清理
- **全链路 E2E**：新增 `tests/e2e/test_full_chain.py` 七阶段一条龙（同一模拟数据世界）：造数→SQLite 镜像 rebuild/verify→日报/月报/CSV 导出→仪表盘只读面 20+ 端点→写循环（分组增删 / 目标设置持久化）→备份 zip→恢复到全新根并复核聚合一致→安全抽查（口令 401/带 token 200/CSP 头/跨源 POST 拒绝）→洞察↔查询一致性对账
- **确定性修复**：移植时把 legacy 内同款的 `time.time()` 墙钟锚造数改为正午锚 `_day_noon_ft`（消除午夜抖动类 flaky）
- **覆盖率**：门禁维持 70%，实测 **79%**（合并前 73%）；pytest 总量 429 → **483**（59 个文件）

### 修复（2.8.1）

- **多日 AI 成本查询性能（实测 121s → 0.95s）**：①`_paths_fingerprint` 按 os.walk 返回序拼接导致指纹串不确定，v2.7 结果缓存在真实目录上从未命中——改为排序后拼接（语义不变）；②`_COLLECT_CACHE_MAX` 8 → 160（原值 < 查询 max_days=92，区间查询逐日结果互挤，缓存形同虚设）；③新增会话文件解析记忆化 `(解析器, 路径, mtime_ns, size)`，条目 ≤4096 且源字节预算 ≤256MB 双上限；④新增 `collect_fingerprint_batch()` 批作用域并由 `query.run_query` 整体包裹——一次查询的目录树遍历/枚举从「每日一次」降为「每次查询一次」
- **`/api/budget` 月度档边界年 500**：`9999-12` 月末算法（+4 天跨越 date 上限）抛 OverflowError 未被捕获且异常路径返回 500，违反端点自身「配置未开启/无效/异常 → 200 空态」契约——`_month_days` 补接 OverflowError 按无效月处理，handler 异常路径兑现 200 空态
- **测试确定性**：`test_pause_resume` 由真实 sleep 编排（负载下首轮轮询越窗即 flaky）改为伪时钟全确定版；顺带修场景测试隔离缺陷——`finalize_day` 报表链此前会扫描开发机真实 AI 会话目录，现 `run_scenario` 助手统一关闭 ai_sessions/浏览器扫描
- **CI flaky 修复**：conftest 的 `seed_day` 写入后主动失效 days-cache——目录 mtime 存在时钟粒度（~10ms 级），同一刻度内「播种→读取」会拿到过期日期列表（Windows CI 快速盘真实复现：goals streak 误判断签致构建失败）

### 文档（2.8.1）

- `README.md` / `README.en.md` 测试命令统一为 pytest+coverage 单一口径；`docs/ROADMAP.md` 翻转「合并暂缓」结论为已完成；`docs/TEST_WORKFLOW.md` v1.2 记录迁移完成方式（机械整移 vs 原计划差异）；`TODO.md` 交接命令同步

## [2.8.0] - 2026-08-23

> 主题：工程收尾与测试补位（dashboard 拆分 / frontend·e2e 测试 / 覆盖率门禁 70）+ Git 侧采纳率代理指标 + 受限查询模板扩充。

### 新增（v2.8.0）

- **Git 侧采纳率代理指标**（`adoption.py` 收敛 + `/api/adoption?date=`）：按 ADOPTION_SPIKE 结论弃用 AI 侧 per-file 归因（真实数据 join 命中率 0%），只保留只读 Git 代理——`retention` = 新增行/(新增+删除)、`reworked_ratio` = 删除行/(新增+删除)；单仓库失败仅跳过、单源失败契约空态 200 不 500；带强制免责声明，洞察页折叠 + 灰色降权展示，confidence 永不等于 high
- **受限查询模板扩充**（`query.py`）：新增 q6「产出对比（两周期）」/ q7「专注度最佳日」/ q8「成本趋势」三个模板，沿用正则白名单 + 周期词 + 空态 200 + notice 的受限契约，并补「今日/昨日」周期别名

### 工程（v2.8.0 · 可维护性与测试）

- **`dashboard.py` 拆分**：把与 HTTP 无关的纯函数/常量（`_agg_to_csv`、`_backup_zip`、`_safe_extract_zip`、`_available_days`、`_collect_known_apps` 与 days-cache 组等）外置到新模块 `dashboard_util.py`，行为不变、re-export 保持 `dashboard.<name>` 兼容；dashboard.py 1917→1714 行，新增 13 项单测
- **测试金字塔补位**：新增 `tests/frontend/test_frontend_smoke.py`（4 项：nav↔section↔loader↔TITLES 接线、前端 24 个 `/api/*` 全部落在后端路由、模板结构缺口）与 `tests/e2e/test_smoke.py`（2 项：根路径 HTML + 造数→`/api/day`→`/api/trend` 全链路冒烟）
- **覆盖率门禁 65 → 70**：coverage source 补 `learn`/`alerts`/`goals`；实测快集 73%，`pyproject.toml` / `ci-fast.yml` / `build.yml` 三处同步设为 70

### 文档（v2.8.0）

- `TODO.md` / `docs/ROADMAP.md` 同步到实际发布状态（v2.5.x / v2.7.0 已发布、v2.5.3 无 tag 注明、采纳率标「待插件事件源」）；`.gitignore` 忽略 `.agent-teams/`（AgentTeams 团队状态目录）

## [2.7.0] - 2026-08-21

> 主题：告警闭环 + 每日目标 + 全局性能优化（AI 会话 / 浏览器历史 / SQLite 指纹缓存）+ Token 真实用量优先与加权估算。

### 性能（v2.7.0 · 全局优化）

- **AI 会话解析指纹缓存**（`ai_sessions.collect`）：按各工具目录会话文件的 (path, mtime_ns, size) 指纹缓存 tools/total 结果——只 stat 不读内容，文件新增/追加自动失效。实测合成负载（45 文件 × 40 消息）冷 76.9ms → 暖 **0.37ms（208x）**；仪表盘多端点、报表逐日成本账本、成长/对比/预算等所有调用方自动受益。`tools/total` 为共享只读对象（同 `report.aggregate` 约定），web_ai 部分不缓存随入参现算
- **浏览器历史库指纹缓存**（`browser_history.collect`）：Chrome History 库可达数十至上百 MB，此前每次调用都整库拷贝+解析且无缓存；现按 (day, 各库 mtime_ns+size) 缓存，实测暖调用 **274x**，浏览器写入历史（mtime 变化）自动失效
- **SQLite 镜像写入提速 ~66x**：连接按 data_root 进程内复用 + init_db 每连接仅一次 + WAL/synchronous=NORMAL 放宽持久化（JSONL 才是事实源，镜像可 rebuild 自愈）。实测 9.9ms/条 → **0.15ms/条**；`rebuild()` 前自动释放共享句柄（防删到孤儿文件）；新增 `close_connection(s)()` 供测试与退出释放句柄
- **goals 日期列表 TTL 缓存**：streak 回推不再每次请求重复 os.listdir（mtime+5s TTL 范式，同 dashboard/classifier）

### 变更（v2.7.0 · 算法精进）

- **Token 估算双口径**（`ai_sessions.token_estimation_mode`）：新增 `weighted`（默认）按字符类别加权——CJK 1 Token/字、字母 4 字符/Token、数字 ~3 字符/Token、标点符号 2 字符/Token、空白 8 字符/Token，对代码/JSON 等符号密集文本显著修正 simple 口径的低估；`simple` 回退历史口径
- **真实用量优先**：解析消息内 API 返回的 `usage` 字段（`input_tokens/output_tokens`、`prompt_tokens/completion_tokens` 及平铺变体），命中时 token 与成本按**真实值**计算，不再估算；新增 `tokens_from_usage` 统计实际覆盖消息数。Claude Code / OpenAI 风格会话文件直接受益
- **「简单学习」个性化基线（`learn.py` + `insights.baseline_insights`）**
  - 纯标准库在线统计学习：滑动窗口样本环（180 天）+ z-score 异常检测，Welford 精神的无依赖实现；深度学习因零依赖约束与样本量现实不采用（模块 docstring 附决策理由）
  - 对 总活跃 / 编码 / 会话数 三个指标维护"你的常态"，当日偏离 ≥2σ 出 warn 卡片、≥3σ 出 alert 卡片（type=trend，复用现有前端渲染）
  - 语义：打分先于记录（当日不污染自身基线）；同日重复调用覆盖重写（日报 19:30 与仪表盘多次打开安全收敛到当日最终值）；坏档自愈；习惯漂移自动跟随窗口
  - 配置段 `insights.baseline`（`enabled` / `min_days`=7 / `z_warn`=2.0 / `z_alert`=3.0）；接入 `/api/insights` 与日报「今日建议」

### 性能（v2.7.0 · 全局瘦身）

- **AI 会话统计指纹缓存**（`ai_sessions.collect`）：按 (日期, 工具目录下会话文件 mtime+size 指纹) 缓存，命中时跳过全部文件读取与解析——实测 76.9ms → 0.37ms（**约 208×**）；文件新增/追加自动失效；web_ai 部分不缓存随入参现算；新增 `invalidate_collect_cache()`
- **浏览器历史指纹缓存**（`browser_history.collect`）：按 (日期, 各 History 库 mtime+size) 缓存，消除重复整库拷贝+解析（Chrome 库可达上百 MB）——实测 6.9ms → 0.05ms（**约 144×**）；浏览器写入历史自动失效；新增 `invalidate_visits_cache()`
- **SQLite 镜像写入提速**：进程内按 data_root 复用连接（`_CONN_CACHE`）+ `init_db` 每连接仅一次 + **WAL / synchronous=NORMAL**（JSONL 才是事实源，镜像可重建，放宽持久化合理）——实测 9.9ms → 0.15ms/条（**约 66×**）；`rebuild` 前自动释放共享句柄（`close_connection`），新增 `close_connections()` 供测试/退出清理
- **goals 日期列表 TTL 缓存**：与 dashboard/classifier 同范式（mtime+5s），消除 streak 回推的重复 listdir
- 以上均为**行为不变**的缓存加速：缓存对象共享但只读（沿用 report.aggregate 的"调用方不得修改"约定），指纹变化即自动失效，无需手动清理

### 测试

- 新增 `test_learn`：配置夹取 / 指标提取 / 预热期 / z-score 偏离检测 / 当日排除自身 / 同日覆盖幂等 / 坏档自愈 / 窗口裁剪
- 新增 `test_ai_sessions_refined`：加权估算器分桶 / usage 嵌套与平铺提取 / collect 真实用量优先（混合语义）/ simple 模式回退 / 符号密集文本两口径对比
- 新增 `test_baseline_api`：异常日经 `/api/insights` 透出 trend 卡片 / 预热期不打扰
- 新增 `test_perf_caches`：AI collect 对象复用与追加失效、浏览器历史对象复用与 touch 失效、SQLite 共享连接与 rebuild 句柄释放（缓存语义回归，防优化退化）

### 新增（v2.7.0 · 行动与目标）

- **告警闭环（`alerts.py`）**：预算接近/超支与连续工作休息提醒，经托盘气泡主动通知（Win10/11 自动转 Toast）
  - 预算告警复用 `budget_status` 三态判定，warn/exceed 各自「每日至多一次」，跨天自动重新武装
  - 连续工作提醒：持续活跃 ≥ `rest_after_min` 分钟且期间无足够空闲即提醒；空闲 ≥ `idle_reset_s` 视为已休息、累计清零；`cooldown_min` 冷却防打扰
  - 预算检查限频（默认 15 分钟，内部扫描 AI 会话文件较重）；暂停监控时不累计不评估
  - 配置段 `alerts`（`enabled` / `check_interval_s` / `budget_warn` / `budget_exceed` / `rest_reminder` / `rest_after_min` / `idle_reset_s` / `cooldown_min`），支持热重载
- **每日目标与连续达成（`goals.py`，可选功能 · 默认关闭）**
  - 两类目标：总活跃时长 + 编码时长（口径 = 开发工具 + AI编程 合计）
  - 概览页新增目标进度面板（进度条 + 连续达成天数）；设置页新增开关组（`/api/goals` GET + `/api/goals/settings` POST）
  - streak 纯派生即时回推（不落状态文件）：当日未达成不断签（从昨天起算）、缺数据自然日断签、回看上限 90 天；修改目标后按新目标重算
  - 配置段 `goals`（`enabled` 默认 false / `daily_active_min` / `daily_coding_min`）

### 测试

- 新增 `test_alerts`：配置归一化夹取 / 工作累计与空闲清零 / rest 阈值与冷却 / 预算 warn→exceed 升级与每日去重 / 开关与暂停短路
- 新增 `test_goals`：配置归一化 / 全 0 目标不构成 streak / 进度达标与未达标 / 连续达成与缺日断签 / 当日未达成保持昨日 streak
- 新增 `test_goals_api`：默认关闭空态 / 设置保存落盘与进度反映 / 越界输入夹取 / 非法 date 回退今天

### 文档

- **新增 [docs/HARNESSES.md](docs/HARNESSES.md)（AI 工具监控支持矩阵）**：按 计时（进程树识别）/ 会话深度统计（本地文件解析）/ Web AI 会话（浏览器历史）三个维度，逐一列明当前支持的 harness 及覆盖程度；说明各维度工作原理、扫描路径、自定义扩展方法（`ai_sessions.paths` / `ai_tool_names` / `ai_pricing.json`）与已知限制（含 WSL 场景的 UNC 路径临时方案）；README 双语版已链接

### 修复（v2.7.0）

- **备份恢复端点**：`/api/backup/restore` 在请求体超限直接拒绝时未读取 body，keep-alive 会把残留字节当新请求解析、客户端读到连接重置；现拒绝前有界排空请求体并关闭连接，返回干净的 400（新测试 `test_restore_reject_bad_bodies` 覆盖）

## [2.5.3] - 2026-08-21

> 主题：AI 价格设置可用 + 导出进度反馈。

### 新增

- **AI 模型价格设置页可直接改内置价目**：设置页「💲 AI 模型价格」现在把内置 60 个模型价目也渲染成可编辑行，改单价即写入覆盖文件（<数据根>/ai_pricing.json，纯 diff 层；未改的内置不写入），点「重置」恢复默认。无需再手填模型名。新增后端断言：内置价目完整返回（数量与代码内置一致）。

### 修复

- **月报导出点下变「导出中…」后卡住无下文**：根因为月报聚合在后端较慢（首次未缓存约十余秒）且前端 `fetch` 无超时，一旦被 SQLite 锁竞争阻塞就永久挂起。导出改为**流式读取**：生成期显示不确定滑动进度条（「正在生成报表…」），响应开始后用 `Content-Length` 定量进度（「正在下载…」）；并加 **120s 客户端超时**兜底——后端卡死时弹「导出超时」提示并复位按钮，不再永久卡在「导出中」。

## [2.5.2] - 2026-08-20

> 主题：精炼 AI 会话模型识别——时间轴/对比/深度面板不再被「未识别」淹没。

### 修复

- **AI 会话模型识别仍偏多「未识别」**：此前会话级模型取「全部消息中最常见的 model」，而 Claude 等工具的用户消息本身不带 model 字段（被记为「未识别」），当其数量多于助手消息时把真实模型顶成了「未识别」。改为**会话级模型仅统计 assistant 消息的已知模型**（真实模型均在 assistant 上），未识别会话由 14/20 降至 4/20；`by_model` 维度仍保留「未识别」键以兼容既有行为。成本估算与多工具发现不受影响（总成本仍 ~$0.70/日）。

## [2.5.1] - 2026-08-20

> 主题：修复真实使用中发现的一批前端/数据层缺陷，并补上「AI 模型价格」设置入口。全部离线派生、零第三方运行时依赖。

### 修复

- **导出按钮恒返回 400**：前端 `doExport` 参数顺序与后端契约相反（`scope`/`type` 颠倒），导致 `type` 非法被拒。已校正参数顺序并加加载态；后端同步严格校验 `type`/`scope`，契约不匹配返回 400（不再静默空文件）。
- **成长/对比界面 4/8/12/24 周按钮失效**：原 `$$('.controls [data-gw]')` 选择器在视图容器外绑不到按钮。改为事件委托（`#view-growth [data-gw]`），并补 `primary` 高亮反馈。
- **AI 会话只识别到 claude、模型全「未识别」、成本全 0**：`ai_sessions` 会话发现此前只覆盖 claude。现新增 **opencode（SQLite，真实 modelID/成本）** 与 **pi agent（`~/.pi/agent/sessions` 专用解析，model_change 上下文回填）** 解析器，`model` 多为会话级需回填，否则每条都「未识别」。修复后工具覆盖 `claude / opencode / pi_agent`，模型识别率与成本估算恢复正常。
- **快速提问输入框无法输入 + 应在接入 AI 后才显示**：面板改为默认隐藏，仅当 AI 洞察启用时显现（避免未接入时误导）；并确认无 overlay/`readonly`/`preventDefault` 阻断输入。
- **周/月报「生成不出来」**：月报聚合在真实数据上耗时 ~12s 且无反馈。前端加明确加载态（「正在聚合本月数据…请稍候」），避免误以为卡死。
- **左下角版本号硬编码 v1.0.0**：改为由后端 `version.VERSION` 注入模板，与发布版本一致。
- **联系人识别无数据**：经核查为 by-design——仅当微信/QQ/钉钉等前台窗口标题含联系人时记录，用户近期无此类窗口故为空，非 bug。AI 工具识别部分已随上述数据层修复恢复。

### 新增

- **AI 模型价格设置 UI**（设置页「💲 AI 模型价格」）：内置常见模型价目表（USD/百万 Token，量级参考），可在此覆盖未收录/价格变动的模型；保存至 `<数据根>/ai_pricing.json`，立即用于时间轴/对比/成本统计。新增 `/api/pricing` GET/POST 端点。

### 测试

- 新增 `tests/api/test_regression_bugs.py`：锁定导出参数契约（正确顺序 200、错误顺序 400）、`/api/pricing` 读写往返、多工具发现结构。

## [2.5.0] - 2026-08-20

> 主题：从「记录用了多久」进化为「看懂 AI 编程过程、成本与成长」。全部离线派生、零第三方运行时依赖、原始 `usage.jsonl` 永不被改写。

### 新增（Vibe Coding 分析平台 · v2.5/v2.6 主线）

- **AI 会话质量评分**（`ai_sessions`）：按 提问含金量 / 返工 / 稳定性 / 上下文健康度 四因子加权算 0–100 分并分档（优/良/中/待优化），逐会话给 `quality_score`、`quality_factors`、`quality_notice`；日报「AI 会话深度」章节增加质量摘要与「质量」列，仪表盘 AI 面板新增质量均分卡并按质量降序排列。纯派生不落盘，明确标注非采纳率
- **Vibe Coding 时间轴回放**（`timeline.py` + `/api/timeline`）：把 `usage.jsonl` 前台会话、AI 会话深度、Git 提交三源按时间合并成事件流（`session` / `ai_session` / `git_commit`），新增「时间轴」视图按时段回放当天编程叙事，附 summary（AI 分钟 / 提交数 / churn / 成本）
- **成本预算告警**（`budget.py` + `/api/budget`）：为 AI 成本设日/月预算，判定 正常 / 接近（≥80%）/ 超支 三态；概览新增预算 banner（超支变红），周报月报自动追加预算小结。默认关闭不打扰（`insights.budget`）
- **多工具横向对比**（`tool_compare.py` + `/api/ai-compare`）：同期间各 AI 工具的 会话 / 轮次 / 分钟 / Token / 成本 / 字符每美元 / 质量均分 / 成本占比对比，支持项目过滤与 1–90 天区间；新增「对比」视图
- **能力成长曲线**（`growth.py` + `/api/trend`）：按 ISO 周聚合 依赖度 / 效率 / 质量 / 专注度 等周均值，给出 上升 / 持平 / 下降 趋势；周快照 `tmp + os.replace` 原子写、幂等、坏档自愈；新增「成长」视图
- **受限模板查询**（`query.py` + `/api/query`）：5 类固定模板（AI 成本 / 成本排位 / 专注度趋势 / AI 产出 vs Git 产出 / AI 活跃概况），支持今天/昨天/本周/上周/本月/最近 N 天等周期词；概览页新增「快速提问」面板。**不嵌入任何大模型**，严格正则白名单匹配防注入

### 工程优化（ROADMAP §9）

- **前端模板外抽**：`dashboard.py` 内联 2405 行 `PAGE_TEMPLATE` 抽到 `assets/dashboard.html`，运行时加载并带 `mtime/size` 缓存；三级路径回退（`sys._MEIPASS` → 程序目录 → 源码目录）+ 模板缺失时内联兜底页保证不白屏；`dashboard.py` 由 3957 行降至 1616 行（-59%）
- **`_available_days` 缓存**：按数据根目录 mtime + 5s TTL 缓存日期列表，返回浅拷贝防污染，避免单次请求内多次 `os.listdir` 与长历史（数百日期文件夹）重复扫描
- **`applog.read_recent` 流式读尾部**：`deque(maxlen=n)` 逐行迭代替代 `readlines()`，内存占用与日志总行数无关（20 万行日志实测通过）

### 修复

- **日志视图 section 丢失**：前端模板外抽过程中 `<section id="view-log">` 开标签被吞，导致日志页 DOM 错位（已恢复并加接线守卫测试）
- **功能无入口**：`/api/ai-compare`、`/api/query` 后端已实现但主仪表盘无导航入口，现已补齐视图与调用
- **配置缺段**：`config.default.json` 补 `query` 段（`enabled` / `max_days`），旧 `config.json` 靠深合并自动获得
- **覆盖率统计遗漏**：`pyproject.toml` 的 coverage source 补 `query` 模块

### 测试

- **pytest 85 → 290 项**（unit / integration / api / security / performance 五层），`test_all.py` 334 项 LEGACY 兜底保持全过；覆盖率 56% → 59%（`timeline` 89% / `budget` 96% / `tool_compare` 96%）
- 新增 `test_ai_quality`、`test_timeline`、`test_budget`、`test_tool_compare`、`test_growth`、`test_query`、`test_adoption`、`test_applog`、`test_days_cache`、`test_dashboard_template`、`test_frontend_wiring` 等
- **接线守卫**：`test_frontend_wiring` 断言 nav ↔ section ↔ loader ↔ TITLES 一致、前端调用的 `/api/*` 后端必须存在，防止「后端做完前端没接」与本次 section 丢失类回归

### 已知限制（诚实声明）

- **采纳率 / 留存率归因不予采用**：`adoption.py` 与 `docs/ADOPTION_SPIKE.md` 记录了基于 Git numstat × AI 会话时间窗 × 文件 mtime 的启发式 spike，真实数据实测三源命中率为 0%（会话在凌晨、写盘在午间、提交在午后），远低于 30% 验收线，因此**不接入仪表盘**，仅留档说明为何不做
- Token / 成本 / 时间节省 / 质量分均为**离线估算**，非官方账单与真实采纳率，界面与报表均带声明

## [2.4.0] - 2026-08-20

### 测试

- **测试金字塔 85 项**：测试按 `unit / security / integration / api / frontend / performance / e2e` 分层组织，`pytest tests` 全量 85 项通过
- **覆盖率 56%**：全量行覆盖率 56%，覆盖 monitor / insights / report / dashboard 合约 / updater / 安全边界等核心路径，作为后续回归基线

### 新增

- **time_saved 离线估算**（`insights.time_saved`，Phase 3）：按当日 AI 编程活跃时长 × 效率因子粗估节省时间（节省 = AI 时长 × (因子-1)），离线计算、不入库不上传；仪表盘概览新增「时间节省估算」卡，因子 1.0–5.0 与最低 AI 活跃分钟数可配（`factor` / `min_ai_min`）

### 前端细节

- 控件统一 hover / focus / active 态与过渡动画（select / input / textarea / file / button，含 `:focus-visible` 焦点环）
- 热力图「少 → 多」图例提示
- 移动端侧边栏汉堡菜单与抽屉开合（同步 `aria-expanded`）
- 鉴权弹层 Enter 解锁 / Esc 关闭，进入自动聚焦
- 窗口 resize 防抖刷新当前视图
- 「最近 14 天活跃趋势」单日聚合失败逐日兜底 0、概览趋势 try/catch 降级提示，不再整体 500 / 留白

### 修复

- **配置漂移**：`insights.time_saved` 等新配置键经 `_merge_dict` 合并进默认值，旧 config.json 缺键自动补齐，不再因配置漂移导致行为不一致
- **更新白名单**：`updater._is_allowed_asset_url` 白名单校验——自定义 api_base 镜像放行、非白名单域名一律拒绝（`test_update_whitelist_rejects_evil` 覆盖）

### 补充归档（v2.7.0 整理）

> 以下能力实际随 v2.4.0 发布，但当时未写入发布说明，现补记。

- **专注度评分**（离线规则）：基于最长专注段、编码/开发占比、每小时切换频率综合打分 0–100 并按高中低分级；**死循环检测**识别时间窗内密集短会话高频反复切换并告警；洞察页「行为洞察」面板 + 日报「今日建议」；`/api/insights` 返回 `behavior`；阈值可配 `insights.behavior`
- **Vibe 编程人格分析**（趣味 · 离线）：按当日活动分布加权打分挑出人格脸谱；行为洞察面板顶部人格卡 + 日报提示；`/api/insights` 返回 `persona`；阈值可配 `insights.persona`
- **Git 代码变更分析**（Phase 2 · 只读本地提交）：`git log --numstat` 统计指定日期提交/增删行/改动文件，「修改率」作返工近似；洞察页「代码产出（Git）」面板 + 日报；`/api/insights` 返回 `git`；只读带超时、无 git/未配置/非仓库优雅降级；阈值可配 `insights.git`
- **AI 成本账本**（Phase 3 · 周/月汇总支出报表）：遍历期间每日 AI 会话深度，聚合消息/轮次/Token/成本并按 模型/项目/工具 汇总；周报月报自动追加「AI 成本账本」章节；只读本地不联网，无数据自动省略
- **修复**：剔除指向未知类别的「孤儿分组」（分类/列表/导入只信任已登记类别，内置 ∪ 自定义）；`/api/heatmap` 单日聚合失败以 0 兜底不再整体 500

## [2.3.0] - 2026-08-18

### 新增（ROADMAP Phase 1 · AI 编程深度追踪 v1.5）

- **对话轮次追踪**（`ai_sessions.rounds`）：本地会话文件内按 user→assistant 配对计 Q/A 轮次；并通过 `browser_history` 访问明细深度解析 Web AI 会话（ChatGPT/Claude/Gemini 等聊天页面的会话分组，同一会话页的返回/刷新次数 ≈ 轮次，尽力而为）
- **Token 用量估算**（`ai_sessions.token_estimation`，默认开）：CJK 按 1 Token/字、其余按 4 字符/Token 折算输入/输出 Token，逐工具/逐会话统计
- **按模型拆分**（`by_model`）：从消息 `model` 字段或内容中的模型名（Claude/GPT/DeepSeek/Qwen 等）提取，聚合到工具/合计/会话详情
- **按项目拆分**（`by_project`）：从 cwd/project/repo 等字段提取，按「会话级」归口，避免工具目录名污染，聚合到工具/合计/会话详情
- **AI 会话深度默认开启**：`ai_sessions.enabled` 默认置 `true`（不再需要单独开启；可在配置显式关闭）
- **仪表盘「AI 会话详情」面板**：固定于**概览**页底部（新增 `/api/ai-sessions` 接口，始终展示），汇总卡（消息/轮次/Token 进/出）+ 模型/项目分布 + 本地会话详情表 + Web AI 会话表
- **前端结构调整**：移除会话深度的单独面板/单独页；「AI 洞察」独立为自身功能，未开启（`insights.ai.enabled=false`）时侧边栏**不显示**「AI 洞察」项，规则洞察保留在该页内
- **日报「AI 会话深度」章节**：汇总 + 模型/项目分布 + 本地/Web 会话详表（默认开启，有数据时即出现）
- 新增 `ai_sessions --web` CLI：附带解析浏览器侧 Web AI 会话
- 配置：`ai_sessions.enabled` 默认 `true`；新增 `ai_sessions.token_estimation`（默认 `true`）、`ai_sessions.web_ai.enabled`（默认 `true`）

### 新增（ROADMAP Phase 3 · 成本与 ROI）

- **按模型费用估算**：内置主流模型定价表（USD/百万 Token）已更新到最新一代（GPT-5.x/4.1/o3/o4-mini、Claude Fable 5/Opus 5/Sonnet 5/Haiku 4.5、DeepSeek V4、Gemini 3.x/2.5、Qwen3/GLM-5/Kimi/Doubao/Grok-4 等）；按「模型 × Token」折算输入/输出费用
- **按项目成本分摊**：成本随 `by_project` 会话级归口到项目，查看每个项目花了多少钱
- **成本数据贯通**：`tools` / `total` / `by_model` / `by_project` / 会话详情均带 `cost_in` / `cost_out` / `cost_total`
- **仪表盘概览面板**：新增「成本估算」卡，模型/项目分布与会话详情表加成本列
- **日报「AI 会话深度」章节**：新增成本汇总与按模型/项目成本表现
- **CLI 展示费用**：`ai_sessions --json` 及文本输出含费用
- 配置新增：`ai_sessions.costs.enabled`（默认 `true`）、`ai_sessions.costs.model_pricing`（默认空）
- **自定义单价两途径**：① config `ai_sessions.costs.model_pricing`（`{"gpt-5": [1.25, 10]}` 或 `{"...": {"input":..,"output":..}}`）；② 数据目录下放 `ai_pricing.json`（同格式，优先级最高，便于不改 config 维护）——定价随厂商波动，建议用户自维护

### 测试

- 新增 `test_ai_sessions_costs`：按模型计价 / 按项目分摊 / 自定义单价 / `costs.enabled=false` 关闭路径

### 测试

- `test_ai_sessions` 扩展：轮次 / Token / by_model / by_project 断言
- 新增 `test_ai_sessions_phase1`：多轮会话、模型·项目拆分、会话详情、Web AI 会话（含开关关闭路径）
## [2.2.0] - 2026-08-17

### 新增
- **UWP/商店应用识别**：通过进程路径识别 WindowsApps 包并映射显示名（`config.uwp_app_names`，支持计算器/Store/照片/终端等）
- **管理员权限模式**：`python monitor.py --admin` 非管理员时自动请求 UAC 提权重启
- **Firefox 停留时长估算**：按相邻访问时间差估测停留时长（`config.firefox_dwell_max_s`，默认 600 秒）
- **更新供应链安全**：更新资产下载地址加入白名单校验（GitHub 官方域名 / `update.api_base` 域名），拒绝任意第三方地址
- **更多应用适配**：
  - 常用软件显示名/分类补充（Obsidian/Notion/Slack/Teams/企业微信/飞书/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools 等）
  - 社交软件识别补充（企业微信/飞书/Slack/Teams/WhatsApp/LINE/Skype）
  - 浏览器适配补充（Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/搜狗/傲游/Slimjet）
  - AI 工具识别补充（Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp）
  - 终端 TUI 工具补充（tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig 等）

## [2.1.1] - 2026-08-17

### 新增
- **SQLite 一致性校验**：`sqlite_store.py --verify` 对比 JSONL 与 usage.db 记录数，发现差异可 `--rebuild` 修复
- **周报 SQLite 快速路径**：`report.aggregate_days()` 支持多日范围一次查询，周报/仪表盘周视图不再逐日扫 JSONL
- **更新模块测试**：新增 updater 版本比较、检测、下载校验、脚本生成、信号文件测试
- **仪表盘更新 API 测试**：覆盖 `/api/update/status|check|download|apply` 错误态
- **Release 资产补充**：CI 构建后生成并上传 `UsageMonitor.exe.sha256`
- **覆盖率范围扩展**：CI 覆盖率纳入 `insights/updater/sqlite_store/ai_sessions`

### 修复
- 修复若干测试断言对 JSON 空白格式的依赖

## [2.1.0] - 2026-08-17

### 新增
- **AI 会话深度统计支持更多工具**：
  - 新增 Cursor / Windsurf / Trae / DeepSeek / Pi Agent（π）/ DSH 的默认本地会话目录探测
  - 解析器增强：支持嵌套 `conversations` / `sessions` / `threads` / `entries` 等常见格式，兼容性更好
  - DSH 等路径仍可通过 `ai_sessions.paths` 自定义；未配置时自动探测常见目录

## [2.0.0] - 2026-08-17

### 新增
- **AI 会话深度统计**（§6.4.3，默认关闭）：
  - 新增 `ai_sessions.py`：读取 opencode / ChatGPT / Claude 等本地会话文件（JSON / JSONL），
    统计某天 AI 交互轮数、用户/助手消息数、生成行数/字符数
  - 仪表盘「洞察」视图新增「AI 会话深度」面板；`python ai_sessions.py --day ...` 或
    `python insights.py --ai-sessions` 可 CLI 查看
  - `config.default.json` 新增 `ai_sessions` 段（`enabled` 默认 false，`paths` 可自定义，
    缺省自动探测常见目录）
- **SQLite 后端 usage.db**（§6.5，可选高效查询）：
  - 新增 `sqlite_store.py`：在 data_root 下维护 `usage.db`，作为 JSONL 原始日志之外的额外镜像/索引
  - monitor 写入 JSONL 后 best-effort 同步写 SQLite；
    `python sqlite_store.py --backfill / --rebuild / --query / --status` 可回填与查询
  - `config.default.json` 新增 `sqlite.enabled`（默认 true，失败静默降级，不影响 JSONL）
- **GitHub Pages 文档站**（P2 #7）：
  - 新增 `docs/index.md` 与 `.github/workflows/pages.yml`，推送 master 自动发布文档站
- **Review 修正**：
  - 移除 `updater.py` 未使用的 `datetime` 导入（ruff 0 违规）
  - 修正 README 中 Firefox 支持说明（自 v1.1.0 起已支持 Firefox places.sqlite）

### 变更
- `UsageMonitor.spec` hiddenimports 增加 `sqlite_store`、`ai_sessions`
- 版本号升至 2.0.0

## [1.6.0] - 2026-08-17

### 新增
- **新版本检测**：
  - 启动后自动检查 GitHub Releases 最新版本，有新版本时托盘气泡提示（可配置
    `update.check_on_startup` 关闭；`update.api_base` 可覆盖检测源，测试/镜像用）
  - 托盘菜单新增「检查更新」，直接打开仪表盘设置页并自动检查
  - 仪表盘「设置 → 软件更新」可手动检查，展示最新版本、发布时间、更新说明与体积
- **应用内更新**：
  - 一键下载最新版 exe（后台线程 + 进度条；校验 Content-Length 大小与 GitHub 提供的
    SHA256 digest，校验失败自动中止）
  - 应用更新：写更新信号让守护进程优雅退出 → PowerShell 脚本等待全部进程退出
    （60 秒超时强杀兜底）→ 替换 exe → 自动重启 → 自清理
  - 开发模式（源码运行）仅支持检测，应用内安装会明确提示不可用
  - 新增 `/api/update/check`、`/api/update/status`、`/api/update/download`、
    `/api/update/apply`（apply 支持 `dryrun` 预览，测试用）

## [1.5.0] - 2026-08-17

### 新增
- **AI 洞察内容大幅扩充**（发送给 AI 的聚合数据新增多个维度，均只含聚合数字、不含隐私）：
  - 星期/周末、首次与末次活跃时间、平均会话时长、上午/下午/晚上/深夜时段分布
  - 工作/学习占比（AI 编程 + 开发工具 + 办公学习 + 设计创作）
  - 子分类 Top 5、终端工具 Top 3、近 7 天日均活跃与会话数对比
- **AI 洞察客制化模块**（与应用分组同模式，持久化于数据目录 `ai_custom.json`）：
  - 自定义 Provider 预设：任意新增/删除 OpenAI 兼容端点，显示在「设置 → Provider 预设」下拉中并优先于内置预设
  - 提示词定制：逐段勾选发送给 AI 的数据内容、调整洞察数量范围（1-10 条）、填写自定义指令（最多 500 字，附加到提示词末尾）
  - 新增 `GET/POST /api/ai/module`、`GET /api/ai/module/export`、`POST /api/ai/module/import`，洞察页可直接导出/导入整份模块配置（迁移/备份）

## [1.4.0] - 2026-08-16

### 新增
- **图形安装向导**（`installer.ps1`，零依赖）：类似成熟软件的安装体验——选择安装目录、
  注册登录自启与每日日报计划任务、创建开始菜单/桌面快捷方式、登记到「添加或删除程序」；
  支持 `-Silent` 静默安装（自动化/CI 可用）。
- **图形卸载器**（`uninstaller.ps1`）：从「添加或删除程序」或命令行触发，停止运行中的
  实例并清理计划任务/快捷方式/注册表条目/程序文件，可选择是否连记录数据一起删除。
- AI 洞察支持 **Ollama 本地模型**：
  - 新增「Ollama 本地」provider 预设（默认 `http://127.0.0.1:11434/v1`，API Key 可留空）
  - 设置页选中 Ollama 后自动填入端点/模型，并可一键「刷新 Ollama 模型列表」
    （读取本地已安装模型，输入框可下拉选择；未安装/未启动 Ollama 时给出明确提示）
  - 新增 `GET /api/insights/ollama/models`（经仪表盘代理本地 Ollama `/api/tags`，免跨域问题）

## [1.3.1] - 2026-08-16

### 修复
- 修复仪表盘「分组」页导入配置按钮：点击「导入配置」现在会弹出文件选择框，原生文件选择框不再裸露在页面上；
  导入中显示进度提示，导入失败后自动清空选择，可再次选择同一文件重试。
- 「设置 → 数据恢复」的原生文件选择框同样改为隐藏，新增「选择备份文件」按钮并显示已选文件名。

## [1.3.0] - 2026-08-16

### 新增
- 应用分组更细粒度客制化：
  - `app_groups.json` 新增 `app_names`（每个 exe 的自定义显示名）与 `group_meta`（分组元数据）
  - 仪表盘「分组」视图新增「显示名」编辑列，改名后新会话/仪表盘即时生效
  - 新增 `/api/groups/rename`、`/api/groups/export`、`/api/groups/import`
  - 分组视图新增「导出配置 / 导入配置」按钮，可整份备份/迁移分组配置
- `classifier.resolve_app_name()` 支持用户自定义显示名优先于 `config.json` 的 `apps` 映射。

## [1.2.1] - 2026-08-16

### 修复
- 修复打包版 exe 点击托盘「打开仪表盘」仍回退浏览器的问题：`_find_electron_shell()`
  改用 `paths.script_dir()` 并探测父目录（exe 在 `dist/` 时项目根在父目录），
  同时移除会令 Electron 以 Node 模式运行的 `ELECTRON_RUN_AS_NODE` 环境变量。

## [1.2.0] - 2026-08-16

智能洞察大版本：离线规则建议 + 可选 AI 洞察（内置 Provider 预设 / 自定义端点 / 设置页开关）。

### 新增
- 智能洞察模块（v1.2.0 候选）：新增 `insights.py`（纯标准库），离线规则引擎基于
  `report.aggregate()` 生成学习 / 游戏 / 健康 / 效率 / 平衡 / 趋势六类结构化建议；
  可选 AI 建议（OpenAI 兼容 `chat/completions`，`urllib` 零依赖，默认关闭、聚合统计
  隐私过滤、成功写缓存 `<data_root>/YYYY-MM-DD/insights.json` + 线程单飞锁）。
- 仪表盘「洞察」视图（侧边栏新入口）：`GET /api/insights`（规则即时 + AI 读缓存）与
  `GET /api/insights/ai?date=…&refresh=1`（强制重生成）；规则卡片 severity 配色、
  AI 面板状态/错误态。
- 仪表盘「设置」页新增 AI 可选功能面板：**启用/关闭开关**、内置 Provider 预设
  （OpenCode Go / OpenAI / DeepSeek / Moonshot / OpenRouter / 智谱 GLM / 通义千问 / 自定义）、
  Base URL / API Key / Model / 超时 / 原始标题样本开关，保存后写入 `config.json`；
  新增 `GET /api/insights/settings` 与 `POST /api/insights/settings`（API Key 不回显、留空保留）。
- 日报 `report.md` 追加「📌 今日建议」段（仅离线规则洞察，`insights.enabled &&
  insights.in_report` 时启用，绝不发起网络请求）。
- `config.default.json` 新增 `insights` 配置段（规则阈值 + AI 端点 + 内置 provider 预设）；
  本地与仓库默认 `ai.enabled=false`（可选功能默认关闭，用户可在设置页一键开启）。
- CLI：`python insights.py --day YYYY-MM-DD [--ai] [--json] [--data-root …]`。
- 测试新增 8 组智能洞察测试（规则 / AI 提示词隐私 / AI 调用 / provider 预设 / 缓存 /
  仪表盘 API / AI 设置 API / 日报段落）。

### 变更
- `UsageMonitor.spec` `hiddenimports` 增加 `insights`。
- 新增 `ruff.toml` 锁定基础 lint 规则集（E4/E7/E9/F），并清理存量 E/F 违规。
- 文档同步：README（中英）新增「智能洞察」章节与隐私声明；TODO 记录执行状态。

## [1.1.0] - 2026-08-15

功能增强大版本：应用分组自定义（P0）+ 九项功能增强（P1）+ 工程/质量项（P2）。

### 新增
- 应用分组自定义（P0）：`classifier` 支持 `load/save_app_groups`（TTL 5s 缓存 + 原子写）、
  `all_categories`、`classify_category` 用户覆盖优先；仪表盘新增 GET `/api/groups` 与
  POST `/api/groups/set|add|delete`（含 Origin 校验）及「分组」视图（侧边栏第 6 项）。
- 功能增强 P1（九大项）：周报 / 月报视图（`/api/week`、`/api/month`）、数据导出
  （`/api/export`，CSV/JSON 防注入）、备份下载与恢复上传（`/api/backup[/restore]`，
  白名单 + 路径穿越防护）、浅色主题切换、可选访问口令（`dashboard_token`，hmac
  常量时间比较，默认关闭）、`classifier` 配置热重载（mtime + TTL 3s + 浅拷贝防污染）、
  `monitor` 循环内每轮重读配置（`data_root` 保持启动值）、托盘气泡通知（`show_balloon`，
  NIF_INFO + 点击事件打开日报视图）、Firefox 历史支持（自动发现 profile、PRTime 换算、
  统一输出结构）。
- Electron 桌面壳（独立应用窗口替代默认浏览器）：electron-app/（Electron 33 壳，约
  1280x820 窗口），自动探测 / 启动 Python 仪表盘服务，窗口关闭清理自启服务、托盘常驻
  服务则复用；`--smoke` 冒烟模式（启动 → 截图 → 退出，CI 自检用）；`monitor.open_dashboard`
  优先 Electron 壳（打包 exe > dev 模式），找不到回退默认浏览器，`USAGEMON_USE_BROWSER=1`
  强制回退；paths 环境变量 `USAGEMON_PROJECT_DIR`/`DATA_ROOT`/`PORT`/`PYTHON`。
- 英文版 README（`README.en.md`）及双语互链。
- 工程/质量（P2）：CHANGELOG.md（keep-a-changelog 格式）、CONTRIBUTING.md 贡献指南、
  Issue/PR 模板（bug_report / feature_request / pull request）、README CI/Release 徽章、
  杀软误报处理指引；CI 新增 version↔git tag 同步校验与 coverage 覆盖率报告（上传 artifact）。

### 修复
- 修复 `do_POST` 未知路径 405 回归；`/api/groups` 分类使用服务 `data_root`。
- 修复 report.py 缺失 `import re` 的历史遗留 bug（verify 路径 NameError）。

### 变更
- gitignore 覆盖沙箱测试 / 运行临时目录（`.tmp_*/`）。
- 新增交接文档（应用分组功能现场 + 完整功能 / 工程待办清单）。
- 测试：全量 152 项通过（新增托盘调度 9 项、`test_app_groups` 14 项断言）。

## [1.0.0] - 2026-08-13

首个正式版本：Windows 本地使用情况监控工具（Phase 1-3 + 监控维度细化）。
纯标准库零依赖，静态 CPU <0.1%，内存 <25MB。

### 新增
- 监控核心（win32core.py，5s 轮询、状态变化才写盘、跨天隔离、空闲截断、零写入静态）。
- 前台窗口会话计时；软件清单扫描（注册表 / 开始菜单 / 进程）+ 自动分类 + 每日自动刷新；
  社交联系人识别（微信 / QQ / 钉钉）+ 别名表；浏览器站点分类 + URL 级历史解析
  （锁安全、停留时长、跨天分摊）；vibe coding 监控（opencode / pi agent(π) / ChatGPT 等，
  进程树 + 标题双重识别）；终端 TUI 工具识别 / 二级子分类 / 窗口状态 / 会话 URL 关联。
- 每日汇总 MD 日报（总览 + 小时分布 + 分类 + 联系人 + AI + 浏览器明细 + 清单概要）；
  周报 / 月报 / JSON 导出 / 重分类 / 本地网页仪表盘 / 托盘 / 开机自启。
- 仪表盘前端重构：左侧固定侧边栏（概览 / 趋势 / 日报 / 会话 / 日志 5 视图）、暖灰暗色
  设计系统（#101318 + 琥珀单强调色 #e0a53c）、克制圆角 / 细边框 / 等宽数字、无 AI
  生成味（去紫色渐变 / 玻璃拟态 / emoji）、动画（视图切换 / 数字滚动 / 柱状 / 热力图入场 /
  悬停反馈 / 骨架屏 / prefers-reduced-motion）、趋势页热力图（24 小时 × 天数）、日报页
  Markdown 平滑进度条渲染、会话页筛选 / 搜索、紧凑时长格式、统一标签样式。
- 统一日志系统：applog.py 滚动日志（1MB × 5），monitor / report / dashboard 均接入；
  `/api/log` 端点 + 日志视图（运行日志 + 错误日志，15s 自动刷新）。
- 图标资产（assets/icon.png / icon.ico / tray.ico）+ 项目截图 + README 品牌化 + 自定义托盘
  图标（tray.py 优先加载，回退系统图标）；make_demo_data.py 虚构演示数据生成器。
- 配置文件单一事实源：config.default.json（DEFAULT_CONFIG 改从文件加载）、
  classifier.py `--sync-config` 校验差异、补全 `editor_exes`。
- 可移植性：新增 paths.py（frozen 感知），消除全部 13 处硬编码 `D:` 路径；
  UsageMonitor.spec（exe 使用 icon.ico + 内置图标资源）。
- CI 自动构建：.github/workflows/build.yml（Windows 构建 exe + 打 tag 自动发布 Release，
  Release 生成权限 / 幂等 allowUpdates / action-gh-release v2 参数修复）。
- 统一版本号：version.py = 1.0.0，monitor / report / dashboard 均支持 `--version`。

### 修复
- 安全：dashboard `/api/*` 校验 Origin / Referer 必须指向 `127.0.0.1:<port>`，恶意站点 403；
  页面加 `X-Frame-Options: DENY` + CSP。
- 可靠性：usage.jsonl 写入 flush + fsync；report.py `--verify`/`--repair`
  （剔除坏行自动备份 + 重建缺失日报）。
- 可移植性：修复 exe 打包后数据写进 `_MEIPASS` 临时目录的隐蔽 bug。
- 前端：修复 DATA_ROOT 双替换 / JSON.parse 预解码 / 双引号嵌套三个模板注入 bug；
  HTML 响应加 `Cache-Control: no-store`；修复热力图 opacity 过渡在虚拟时间下不可见。
- 测试：test_all 新增 11 项 dashboard API 测试（端点 / 403 / 安全头 / 错误码 / 路径穿越），
  构建后 `UsageMonitor.exe --version` 冒烟，全量 125 项门禁通过。

[2.8.1]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.1
[2.8.0]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.0
[2.7.0]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.7.0
[2.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.2.0
[2.1.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.1
[2.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.0
[2.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.0.0
[1.6.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.6.0
[1.5.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.5.0
[1.4.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.4.0
[1.3.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.1
[1.3.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.0
[1.2.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.1
[1.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.0
[1.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.1.0
[1.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.0.0
