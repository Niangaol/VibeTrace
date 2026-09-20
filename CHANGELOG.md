# 更新日志

本项目所有值得记录的变更都归档在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
发布流程：`git tag vX.Y.Z` 后由 CI 自动构建并发布 Release。

> 🌐 English version: [CHANGELOG.en.md](CHANGELOG.en.md)

## [2.9.12] - 2026-09-20

> 本版合并 v2.9.7 – v2.9.12 六个内部迭代补丁的全部改动（这些迭代号从未单独打 tag
> 发布，本次以单一补丁版本统一发布）。主题：**取数框架统一、两处 P0 性能修复、
> 全项目降级可观测**。上一个已发布版本为 v2.9.6（2026-09-14）。

### 重构 · derived.py 派生取数框架（v2.9.8）

- 新增 `derived.py`：`day_bundle()` / `series()` / `cached_endpoint()` 三件套，把
  growth / tool_compare / query / budget / insights / advice 六个模块里 13 处、229 行
  逐日 `report.aggregate` + `ai_sessions.collect` 同构循环，收敛为「按天取齐多源数据」
  的单一实现。`need` 位掩码（NEED_AGG / NEED_AI / NEED_GIT / NEED_WEB）按需取源，
  未请求的源不 import 不调用，零额外开销。
- 语义铁律（测试逐条钉住）：每源每天恰调用一次；必须用运行时属性访问
  `report.aggregate`（100+ 处测试 monkeypatch 打的是模块属性）；AGG/AI 失败即该日
  作废、GIT/WEB 失败仅降级；不在 `day_bundle` 内嵌套 `collect_fingerprint_batch()`。
- `growth._aggregate_week` 与 `_merge_incremental` 的单日取数收敛为共用
  `_day_metrics()`——历史上 focus_hhi / ai_sessions / prompt_efficiency 的口径 bug
  正是因为两处各写一份、改一处漏一处。
- 落地过程修正三处框架自身缺陷（v2.9.9）：`series` 增加显式 `days=` 参数（growth 周
  日期有空洞、query/budget 区间非回溯形状，不再有静默漏日风险）；`day_bundle` 的 AI 源
  兼容两参实现、WEB 源补传 `config`；git 调用签名修正为 `git_insights(config, day_str)`
  （此前异常被吞导致 Git 指标静默归零）。

### 性能 · P0-1 浏览器历史免整库拷贝（v2.9.9 / 2.9.10）

- `find_url_for_session` 原先每次调用都 `shutil.copy2` 整个 History 到临时目录再查
  （Chrome 常态 50–300MB，会话切换即秒级 I/O，且卡在 5 秒轮询线程里）。
- 现改为 `_query_source_ro()` 直读源库：有 `-wal` 时先试 `mode=ro`（能重放未
  checkpoint 的访问记录，比原先 immutable 读拷贝还多读到数据），失败（locked /
  无 `-shm`）再退 `immutable=1`；无 `-wal` 时直接 immutable。
  - 关键细节：`sqlite3.connect()` 本身不报错，"database is locked" 是第一次
    `execute` 才抛，所以降级必须包住 `execute`。
  - `_open_ro` 补 `timeout=0.5`，防万一持锁时挂住轮询线程。
  - 任何 sqlite 错误返回 `[]`：查询失败不拖垮会话落盘（best-effort 语义不变）。
- 实测：6.1ms/次 → 0.49ms/次（约 12×），且零磁盘临时文件。
- 顺带消除一个隐藏副作用：monitor 场景测试此前真的在整库拷贝开发机浏览器库
  （`_close_session` 不看 `browser_history_enabled` 开关）。

### 性能 · P0-2 git 子进程 N+1 → 区间批量（v2.9.11）

- 新增 `git_insights.range_batch(days)` 上下文（照 `collect_fingerprint_batch` 风格）：
  进入后一次 `git log --since/--until` 取整个区间，按 **committer date（%cd）** 分桶
  回填；块内 `git_insights()` 直接查表，块外行为与旧版逐字一致。
  - 按 `%cd` 而非 `%ad` 分桶是关键：`--since/--until` 本身按 committer date 过滤，
    author date 会在跨日 amend/rebase 下算错天（已用专门用例钉住）。
- `git_insights()` 主体拆为 `_resolve_projects` / `_assemble_result` / `_empty_result`，
  `analyze_repo` 拆为「跑 git」+ `_stats_from_commits`，区间与单日两条路共用同一套
  聚合代码——避免两处实现各自漂移。
- `_parse_numstat` 多留 `cd` 字段（旧格式自动回退为 `date`，向后兼容）。
- `growth._aggregate_week` 与 `_merge_incremental` 的逐日循环外包 `range_batch`。
- 收益（本地实测）：一周 1.16s → 0.20s，44 天约 9.2s → 1.4s；deep 模式约 18s → 1.4s。

### 修复 · range_batch 等价性（v2.9.12）

- **deep 模式批内丢失整个深度分析**：`_build_day_table()` 原先无条件走浅层
  `_stats_from_commits()`，`insights.git.deep=true` 时批内结果缺 deep_work_summary 与
  authors_detail / commit_rhythm / adoption_proxy / language_dist / deep_work_blocks
  （每个有提交的天 15 个字段）。现按 `gc[deep]` 分支跟 `analyze_repo_deep`，统一复用
  `_assemble_result` 汇总（deep 指标本按单日计算，无法从区间结果切分，故逐仓库重跑）；
  `ai_project_files` 此前在批内被完全忽略，现已透传。
- **降级 notice 文案不一致**：enabled=false / 空 projects 时批内统一返回「批次未启用
  或无仓库」，与逐日路径的两条提示逐字对齐。
- **批表未按配置指纹隔离**：`_batch_key()` 写好了却全文 0 处调用（死代码），同一
  range_batch 块内换 config 会拿到上一份缓存表；现已接入。
- 汇总逻辑统一跟 `_assemble_result`，不再手写累加公式。

### 可观测 · 34 处「有意降级」不再静默（v2.9.7）

- 新增 `applog.note(exc, ctx)`：全项目 34 处按设计必须继续执行的
  `except Exception: ... pass`（定价文件损坏、分类规则加载失败、Win32 调用被拒、
  SQLite 快路回退 JSONL、托盘气泡失败等）统一改走 `note()`——行为完全不变（仍不抛、
  仍降级），但从「完全无痕迹」变为落盘 `<data_root>/logs/app.log`，仪表盘「日志」视图
  可直接看到，直指「统计数字对不上却查不到原因」这类雪隐故障。
- 观测出口本身零风险：挂 NullHandler，未 `configure()` 前零输出（CLI / 测试环境保持
  安静）；`note()` 自身永不抛异常。
- 剩余约 19 处 `except Exception: ... pass` 为「日志自身失败」或 `__main__` 入口兜底，
  按设计保持静默（记日志的故障不能再记日志）。

### 修复与清理

- `metrics_util.merge_dim` 对自建缺字段桶抛 KeyError：float 键累加原为 `t[fk] +=`，
  调用方传入的桶缺 `cost_in` / `cost_out` / `cost_total` 任一键即崩，与 docstring
  「字段缺失时按 0 处理」矛盾。已补 `setdefault(fk, 0.0)`，并新增 `_new_bucket()` 由
  `_MERGE_*_KEYS` 元组生成初始桶，消除「字面量与 KEYS 两处定义」的漂移隐患。
  对正常输入行为完全不变。
- `.gitignore` 封堵 170MB 内嵌 Python 运行时误入库：补 `/Python/`、
  `pythoncore-*-*/`、`review-*.md` 等条目（此前一次 `git add -A` 即可能把整个
  CPython 交进仓库）。
- `VibeTrace.spec` hiddenimports 补 `derived`。
- 修复 v2.9.8 引入的 17 处 `NameError: applog`（alerts/dashboard/monitor/report 四个
  文件只有函数内惰性 import，模块级调用成未定义名；生产环境一旦走到那些降级分支就会崩，
  由 ruff F821 报出）。已补模块级 `import applog`。
- `tool_compare.compare_tools` 已知语义变化（有意）：`report.aggregate` 单日失败时
  原先保留当天 AI 侧数据，现因走 derived 而整日作废；两源正常时结果逐字段相同。

### 测试隔离修复（发布前）

- `test_api_insights_includes_time_saved` 传入关闭 AI 的独立 `config.json`：该用例
  触发 `/api/insights` 的 AI 路径，若沿用默认配置会读到开发机仓库根的 `config.json`
  （`insights.ai.enabled=true`）从而**真实调用 LLM 接口**并挂到超时（CI 无此文件所以
  从不暴露，属测试隔离缺陷）。修复后断言只关心离线 time_saved 字段。

### 测试

- 新增：`test_derived.py`（33 例）、`test_git_range_batch.py`（17 例）、
  `test_find_url_no_copy.py`（8 例）、`test_applog_note.py`（6 例）、
  `test_metrics_util.py`（44 例）、`test_tray.py`（41 例 + 1 skip）。
- `test_git_range_batch` 的 repo fixture 加 `requires_git` 守卫：Windows 下 pytest 的
  fd 级捕获会使进程继承的 stdin 句柄失效，`subprocess.run` 抛
  `OSError [WinError 6]`（环境固有、非代码问题），守卫后该场景降级为 skip 而非 error。

### 全量状态（发布前实测 · 2026-09-20）

- **全量 `pytest tests`：882 passed / 7 skipped / 0 failed**（unit / integration / api / frontend / security / performance / e2e 全链路，192s）
  - 其中 tests/unit：696 passed / 7 skipped / 0 failed
  - tests/security + tests/e2e + tests/performance + tests/api：108 passed / 0 failed
- ruff check .：**0 违规**

## [2.9.6] - 2026-09-14

> 主题：修复 v2.9.5 的托盘行为回归——单击托盘图标错误地打开浏览器，恢复为唤出 Electron 桌面应用窗口。

### 修复
- **托盘单击错误地打开浏览器（v2.9.5 回归）**：v2.9.5 把 `open_dashboard` 的默认改为「浏览器优先」，单击托盘图标不再唤出 Electron 桌面应用窗口。现恢复 **Electron 壳优先**（`USAGEMON_USE_BROWSER=1` 仍为强制浏览器的调试开关）；壳缺失或启动失败时自动回退浏览器。README 中英环境变量表同步回退
- **打包漏模块**：`VibeTrace.spec` 的 `hiddenimports` 补全本地惰性导入模块（`advice` / `dashboard` / `dashboard_util` / `goals` / `metrics_util` / `tool_registry` / `git_insights` / `alerts` / `learn` / `applog` / `browser_history` / `classifier` / `report` / `tray` / `paths` / `version` / `win32core` / `inventory`），避免打包后运行期 import 失败（v2.9.5 新增的 `advice` 正属此类）

### 测试（2.9.6）
- 新增 `tests/unit/test_open_dashboard.py`（3 项）：有壳必启 Electron 且不开浏览器、无壳回退浏览器、`USAGEMON_USE_BROWSER=1` 强制浏览器——钉扎本次回归
- 全量回归 **747 passed, 0 failed**

### 说明
- 本机 `electron-app/node_modules/electron/dist/electron.exe` 曾缺失（DLL 齐全、主程序不在，疑为杀软误删），导致无论优先级如何都只能开浏览器；已用镜像重装 electron 33.4.11 二进制修复。属本地环境问题，非代码缺陷

## [2.9.5] - 2026-09-12

> 主题：Token 口径与成本修正（新鲜输入 + 缓存分列、缓存按官方折扣价计）+ 概览「建议」栏位（可选）+ 两处热力图统一（以趋势为准）+ config.json 取消版本跟踪（防密钥泄漏）。

### 新特性
- **概览「建议」栏位（可选功能，默认关闭）**：新增 `advice.py`（纯标准库、离线、只读）从当日数据提炼**可执行的短建议**，每条都带数字依据；复用 `insights.activitywatch_metrics` / `goals.today_progress` 等既有指标，不另起判断逻辑。首批规则：连续深度工作过久→休息、深夜活跃→作息、今日成本异常（对比近 7 日均值）、缓存占比高→正向说明、未识别模型占比高→补模型信息、编码久但无 AI 会话→建议尝试、项目集中度偏低→专注建议、目标差距。配置 `advice.enabled` / `advice.max_items`（1-20，默认 6）；`GET /api/advice`（走共享响应缓存框架）+ `POST /api/advice/settings`；概览面板非阻塞加载 + 设置页开关

### 变更
- **两处热力图统一口径（以趋势为准）**：概览原走 `?tokens=1` 的 Token 热力图（28 天），与趋势的活跃热力图（84 天）在**指标与窗口上都不同**；现统一为「总活跃时长 × 最近 84 天」——同一请求、同一字段（`hourly_ms`）、同一渲染实现。顺带移除 tokens 变体的专用缓存、服务启动预热线程与 `tokens=1` 参数（该参数唯一消费方即概览热力图，已被替换）
- **Token 展示改为「新鲜输入 + 缓存分列」**：概览主卡由 `tokens_in`（含缓存读/写）改为 `tokens_input_fresh`，缓存读/写单列展示；AI 会话面板与日报/周报同口径改写。API 字段保持不变（additive）
- **托盘单击打开浏览器**（收口上一批未发布改动）：`open_dashboard` 默认用系统浏览器，Electron 桌面壳降为 `USAGEMON_USE_ELECTRON=1` 可选；旧 `USAGEMON_USE_BROWSER=1` 保留兼容

### 修复
- **成本按缓存单价计（未标缓存价的模型不再按全价输入）**：内置定价 157 条中 119 条是 2 元组，缓存读/写此前一律按输入价计（源码注释自称「保守高估」）——而缓存密集型工作流的缓存读可达输入的 **98%**，成本虚高数倍（实测 09-10 glm-5.3 报 $12.65，按官方缓存价应约 $1.2~$2.9）。现对已查证供应商按**官方命中折扣**计：智谱 GLM-5.3 = 25%（缓存命中 2 元 / 输入 8 元）、GLM-5.3-Flash = 20%（cached $0.03 / input $0.15）、阿里云百炼 qwen 系隐式缓存 = 20%；未列入的模型维持按输入价，并在定价页/文案标注为**成本上限估算**（不臆造折扣）
- **Token 口径造成的观感失真**：提供方 `input_tokens` 本身已含缓存读，概览把 `tokens_in` 当主数字会显示「单日 1 亿 Token」（实测 2026-09-10：104,162,189 中 102,084,160 是缓存读，新鲜输入仅 2,070,682）。改为新鲜输入主显 + 缓存单列后与直觉一致
- **config.json 取消版本跟踪**：该文件是设置页写入的运行期配置，含 `data_root` / dashboard 口令 / AI insights 的 `api_key` 等敏感项，被跟踪时存在把明文密钥提交到公开仓库的风险。仓库只保留 `config.default.json` 模板（安装器已在缺失时自动生成），并新增 `.gitignore` 条目

### 测试（2.9.5）
- 新增 `tests/unit/test_advice.py`（9 项：各规则触发/不触发、开关空态、上限夹取、排序）与 `tests/api/test_advice_api.py`（3 项：默认关闭空态、非法日期 400、设置保存往返并生效）；前端接线新增建议面板断言
- 定价：4 元组缓存价 + 官方折扣比断言，并新增「折扣真正进入成本」端到端钉扎（test_token_cache_fixes.py）
- 热力图：统一口径契约测试（只返回 `hourly_ms`；`tokens=1` 不再影响结果）
- 报表/前端措辞断言同步；全量回归 **744 passed, 0 failed**

## [2.9.4] - 2026-09-10

> 主题：内部框架统一（tool_registry 工具注册表 + metrics_util 统计辅助单一来源）+ 仪表盘响应缓存框架（TTL + SWR 单飞）+ 前端健壮性（错误横幅 / 换日竞态保护 / 非阻塞加载）+ AI 编程热力图（概览）。

### 新特性
- **AI 编程热力图（概览）**：概览新增 28 天 × 24 小时热力图，展示全部已知 agent 工具 Token 用量（in+out 合计）；服务启动后台预热，TTL 响应缓存（120s + stale-while-revalidate）秒开
- **tool_registry.py 工具注册表（唯一事实源）**：21 个本地工具 + 4 个纯 Web AI 的「默认目录 / 专用解析器 / 缓存豁免 / 指纹特判 / Web 域名 / 别名」收敛为一条 ToolSpec 记录；ai_sessions / query / tool_compare / dashboard 统一消费；新增工具只改一处（docs/HARNESSES.md 附适配清单）。模型→厂商映射（MODEL_VENDOR_PREFIXES）随 /api/pricing 下发前端，前端本地兜底表仅降级用
- **metrics_util.py 统计辅助单一来源**：fmt_usd / shannon_entropy / hhi / count_switches / tool_switch_series / merge_dim 收拢，budget / growth / insights / tool_compare / ai_sessions 原先各写一份的实现统一（熵/HHI 的精度口径差异由调用方各自舍入）
- **仪表盘响应缓存框架**：单日/区间重端点统一接入 TTL（60s）+ stale-while-revalidate + 单飞（CAS）缓存：urls / ai-sessions / timeline / ai-compare / insights / heatmap；冷态并发请求绝不重复重算（8 路并发只 compute 1 次），错误响应不入缓存
- **报表措辞**：有真实 usage（含缓存）的 Token 不再称「估算」

### 修复
- **非法日历日穿透 400 契约**：_valid_date 此前只校验 YYYY-MM-DD 正则，"2026-13-99" / "2026-02-30" 这类格式合法但日历不存在的日期以空数据 200 通过（初版遗留；_valid_month 与 ai-compare 早有语义校验）。补 fromisoformat 语义校验，14 个单日端点（ai-sessions / insights / timeline / budget 等）全部受益
- **前端健壮性**：视图加载失败不再清空 DOM（错误横幅 + 重试按钮，state.loaded 置回 false）；换日后旧响应作废（日期标记防慢响应覆盖新面板）；概览浏览器停留 / ai-sessions / budget 改为非阻塞加载；本地日期函数 localDateStr 替代 toISOString（东八区本地零点不再倒退成前一天）
- **/api/days?n=abc 回退 14**：非法 n 不再 500（B1 回归钉扎）
- **config.json 行尾噪音**：工作区误转 CRLF 已恢复 LF（内容零差异，diff 归零）

### 测试（2.9.4）
- 新增响应缓存契约（SWR 过期回旧值 / 后台刷新 / 错误不入缓存 / 8 路并发单飞）、语义日期 400、前端 wiring 扩充、token cache 修复、tool_registry 解析
- 修复 CI 无 zstandard 环境的 dsh 集成测试：v2.9.3 起 dsh 走专用 zstd 解析器，明文兜底文件无法再被通用解析，测试按「缺失时降级为空」设计断言（仅 cursor）；CI 测试依赖补装 zstandard 以覆盖真实 zstd 路径
- 全量回归 730 passed, 0 failed

## [2.9.3] - 2026-08-31

> 主题：vibe coding 指标修正（growth 三指标复活 + insights 两处假数据）+ 新增 ZCode / Codex 会话深度适配 + DSH zstd 会话支持（补录此前未提交的工作区改动）。

### 新特性
- **ZCode 适配（双数据源）**：`ai_keywords`/`ai_tool_names` 新增 zcode（进程/标题计时识别）；会话深度读取 `~/.zcode/cli/db/db.sqlite`（opencode 同源 schema：session/message/part，活跃 WAL 库以 `mode=ro` 只读，modelID 真实模型名）与 `~/.zcode/v2/sessions/*.json`（meta+messages 走通用解析，ms epoch）；`_paths_fingerprint` 对 cli 目录只 stat db.sqlite(-wal)，防 log/*.jsonl 追加打穿 collect 缓存
- **Codex CLI 适配**：新增 `_parse_codex_file` 专用解析器（`~/.codex/sessions/**/*.jsonl` rollout 格式）：session_meta→cwd/会话 id、turn_context→模型上下文回填、response_item(message)→user/assistant 消息（developer 跳过）、event_msg(token_count) 的 `last_token_usage` 单次增量累加归因到最近一条 assistant（真实用量口径；`total_token_usage` 为会话累计不可求和）；UTC 时间戳统一转本地时区再匹配日期；大文件豁免 2MB 解析缓存上限（同 dsh 先例）
- **DSH zstd 会话支持**：`_parse_dsh_file` 解析 `session.jsonl.zstd`（request/header 提供模型上下文，assistant 真实 inputTokens/outputTokens 归一化，user→assistant 配对计轮）；`_walk_dsh_files` 只认会话文件；`_dsh_may_contain` 按 createdAt 预检免全量解压；`_SKIP_SCAN_DIRS` 剪枝 node_modules 等重型目录；zstandard 为可选依赖（缺库优雅降级为空）；PyInstaller spec `collect_all('zstandard')` 打包 C 扩展；`_message_usage` 识别 inputTokens/outputTokens 与 totalTokens 兜底

### 修复
- **成长指标三连修复（v2.9.2 回归收尾）**：`model_diversity_entropy` 数据源从不存在的 `agg.get("by_model")`（report.aggregate 不产出该键）改为 `ai_sessions.collect` 的 `total.by_model`——v2.9.2 修复"张冠李戴"时误改数据源，此后该指标恒 None；`prompt_efficiency` 分母从不存在的 `total.get("sessions")` 改为 conversations 条数——此前恒 0；增量合并写入的幽灵键 `project_focus_hhi` 修正为前端契约键 `focus_hhi`（此前增量更新后该指标永远冻结）；周快照新增 `ai_sessions` 会话计数字段并按"总生成行/总会话数"重写 prompt_efficiency 增量合并公式（旧公式引用不存在的 `ai_sessions` 死键）
- **学习曲线假洞察**：`rule_insights` 学习规则删除"curr_ai==0 时用总活跃时长顶替"的兜底——此前"昨日有用 AI、今日没用"会反报"AI 使用时长较昨日增长 N%"假卡
- **Vibe 人格 ai_ratio 死键**：persona_insights 的 by_ai 回退从不存在的"总计"键改为 values() 求和（v2.9.2 在 rule_insights 修过同类，此处补齐）
- **deep_work 跨大间隙合并**：activitywatch_metrics 的编码连续块新增间隔上限 `insights.behavior.deep_work_max_gap_s`（默认 300s）——此前只按序列相邻累加，上午/傍晚两段编码会因中间无会话记录被串成同一个"连续深度块"；顺带移除只写不读的 block_start 死变量
- **web_ai 配置容错**：`ai_sessions.web_ai` 配成布尔/标量不再 AttributeError（dict 走 enabled 键，其他形态直接当开关）

### 测试（2.9.3）
- 新增 tests/unit/test_zcode_support.py（6 项：SQLite 解析/collect 全链路/v2 JSON 通用解析/默认路径注册/指纹 WAL 失效/log 噪声剪枝）与 tests/unit/test_codex_parser.py（5 项：解析与 developer 跳过/usage 增量归因/collect 真实 token/UTC→本地日期边界/坏行跳过）
- growth 三指标断言钉扎（周聚合 + 增量合并双路径）、insights 学习曲线/persona/deep_work/web_ai 四项行为钉扎
- 全量回归 670 passed, 0 failed

## [2.9.2] - 2026-08-26

### 修复
- **Git 深度分析崩溃**：analyze_repo_deep 使用 `_parse_numstat` 返回的 `date` 字段（ISO 字符串），但代码误用 `c.get("ts")` 始终取到 None，导致 `ts_sorted` 未赋值即引用 → UnboundLocalError。开启 `insights.git.deep: true` 时任何有提交的仓库直接崩溃。修复：统一解析 `date` → `fromisoformat().timestamp()`，`ts_sorted` 前置初始化为 `[]`，补 `import datetime`
- **学习曲线洞察死代码**：`rule_insights` 学习规则读取 `(prev_agg.get("by_ai") or {}).get("total_active_ms")`，但 `by_ai` 是 `{tool: ms}` 扁平映射，该键永远不存在 → prev_ai/curr_ai 恒为 0 → 规则永不触发。修复：改为 `sum(...values())`
- **成长指标张冠李戴**：`_aggregate_week` 中 `model_diversity_entropy` 实际塞入的是 app 切换熵（activitywatch_metrics 的 `switch_entropy`），不是模型多样熵。`_merge_incremental` 增量路径也存在同样问题。修复：两处统一改为从 `by_model` 的 `turns` 字段计算 Shannon 熵
- **对比视图加载态/空态 colspan 不一致**：加载态 `colspan="11"` 而表头 12 列、空态已改 12。修复：加载态也改为 `colspan="12"`
- **增量合并死变量**：`_merge_incremental` 中 `aggs` 循环赋值但从未读取。修复：移除

### 测试（2.9.2）
- 全量回归 643 passed, 0 failed

## [2.9.1] - 2026-08-26

### 新特性
- **成长/对比指标扩展**：新增 model_diversity_entropy、tool_switch_freq、focus_hhi、learning_curve、efficiency_stability、adoption_proxy、prompt_efficiency 七类指标，后端 growth/tool_compare/insights 全链路打通，前端趋势卡与对比表自动适配
- **Git 深度分析**：git_insights.py 新增 auto_discover_repos（递归≤3 自动发现子仓库）与 analyze_repo_deep（author_detail/commit_rhythm/language_dist/deep_work_blocks/adoption_proxy），配置开关 `insights.git.auto_discover` / `insights.git.deep`
- **模型与 agent 覆盖扩展**：ai_sessions.py 正则扩至 60+ 模型、29 个 agent 路径、90 条定价；classifier/config 新增 DSH Desktop 等关键词与路径映射
- **ActivityWatch 参考指标**：insights.py 新增 activitywatch_metrics()（focus_time/switch_entropy/deep_work/project_focus_hhi），规则引擎新增 6 类洞察（project_focus/tool_switch/model_diversity/learning/efficiency_stability/adoption）

### 修复
- **AI 时间轴/成长/对比加载态**：timeline、growth、compare 三个视图新增骨架屏/加载提示，避免空白等待
- **DSH 前端厂商归类**：dashboard.html priceVendorOf 新增 dsh 前缀映射，定价页正确归类为国内模型

### 测试（2.9.1）
- 新增 4 个单元测试文件共 137 个函数（model_regex/pricing_table/agent_paths/git_auto_discover）
- 全量回归 643 passed, 0 failed

## [2.9.0] - 2026-08-25

> 主题：仪表盘全面视觉与交互升级（前端重做 + 精修）+ 批次二修复。首个含新特性的 minor 版本。

### 新特性

- **前端整体重做**：设计令牌系统化（radius/space/shadow/ease/transition）、亮暗双主题精修（保留 `dash_theme` 行为，设置页新增第二主题入口）、11 类克制动效（视图交错入场/数字滚动/骨架屏/卡片悬停浮起/表格行高亮侧边条/导航滑动指示器/按钮涟漪/进度条生长/图表扫光与提示淡入/滚动显现/告警脉冲），全部受 `prefers-reduced-motion` 降级；零第三方依赖
- **模型价格按供应商分组折叠**：内置价目表按 6 厂商组（Anthropic/OpenAI/DeepSeek/Google/国内模型/其他）前缀自动归类，手风琴默认全折叠、点击展开、支持全部展开/收起；添加新模型自动展开所在组并滚动定位；编辑/覆盖/重置/删除行为原样保留
- **应用图标（任务管理器风格）**：概览应用 Top10/AI 工具/联系人、会话表、分组表的应用名前显示彩色圆角图标——30+ 常见应用品牌映射，其余按名称哈希稳定取色 + 首字母；零依赖、纯本地生成
- **表格与配色美化**：不透明渐变吸顶表头、斑马纹、数值列右对齐（时间列保持左对齐）；新增多色调色板令牌（--cat-1..6/状态浅色/金色渐变），应用于厂商色点/统计条渐变/主按钮/分类标签等

### 修复

- **目录枚举截断的非确定性**：`_walk_files` 按 os.walk 返回序在 500 文件处静默截断——会话文件较多的机器上每天统计的是随机子集且逐日漂移，更糟的是指纹不变导致结果缓存把 A 子集的结果当 B 子集的合法缓存返回（静默错数）。现枚举每层排序保证字节级确定、上限提至 4096（与解析缓存对齐）、截断留模块级计数信号
- **周/月报配置流断裂 + CLI 死参数**：`_ai_cost_ledger_md`/`generate_month_report_md` 及月/周报 budget 块此前自行取全局默认配置，`--config` 在 CLI main 中定义却从未被任何报表消费。现全部接通 `config_path`（优先级与日报链一致：显式 > 根配置 > 默认），dashboard 月报入口同步透传；CLI 单日报路径的同类残留留待下批收口
- **仪表盘启动即空白的幽灵调用（关键）**：`startApp()` 自 v2.5.0 起调用 `wireExportButtons()` 但函数定义在模板外抽时丢失（v2.8.2 安装版同样带病）——启动即 ReferenceError 导致后续初始化全断：打开无数据、点侧边栏无反应。已恢复定义（顺带修复「导出 CSV/JSON」按钮从未绑定的问题），并为 `initReveal` 增加视口扫描保险丝防止面板卡在透明态
- **趋势热力图塌陷成裸数字**：`#trHeatmap` 容器自 v2.5.0 起缺失 `.hm` 类，全部热力图布局规则从未命中；旧版靠巧合可用，新动画系统的 MutationObserver 给动态注入的列结构加上 `reveal-init`（透明）后彻底塌掉——只剩小时标签裸奔。现渲染输出补上 `<div class="hm">` 容器并让热力图内部结构不参与 reveal 动画

### 测试（2.9.0）

- 新增 `tests/frontend/test_startapp_integrity.py`：静态解析 `startApp()` 函数体，逐一校验被调用标识符均有定义——永久拦截「幽灵调用」类运行时崩溃（反证用例验证可抓出删定义/缺接线/移除启动三种破坏）
- CDP 真浏览器验收流程建立（headless Edge 驱动：零 console 错误/价格手风琴交互/图标渲染/双主题切换全覆盖）

## [2.8.2] - 2026-08-23

> 主题：小版本修复——缓存并发安全、报表链配置流、暂停态退出语义。无新特性。

### 修复

- **模块级缓存线程安全**：dashboard 为多线程 HTTP 服务，但 `ai_sessions` 的 `_COLLECT_CACHE`/`_PARSE_CACHE`、`report` 的 `_agg_cache` 与 `_aliases_cache`、`dashboard_util` 的 days-cache 此前全部无锁——并发请求下 LRU 的 `move_to_end`/驱逐可抛「OrderedDict mutated during iteration」或脏读。五处统一补 `threading.Lock`（锁内只动表、解析与聚合计算留在锁外；days-cache 重扫留锁内防惊群）；新增 8 线程并发锤回归测试（含正面锤驱逐循环）
- **报表链配置流断裂**：`finalize_day → generate_day_report → generate_consolidated_md` 链路此前不透传配置，report 自行取全局默认——`--data-root`/`--config` 用户写在数据根或显式文件里的 ai_sessions/browser 等设置对每日报表不生效，且与仪表盘口径不一致。现全链路接通 `config_path`，解析优先级统一为 **显式 config_path > `<root>/config.json` > 全局默认**（与 dashboard `_load_config_for_root` 同语义；既有调用方零改动）。周/月报同病已定位、留待下批
- **暂停态退出语义**：守护循环暂停分支的 `continue` 会跳过循环尾部全部退出检查，「先暂停再退出」时线程永远等不到停止信号。现暂停等待前即响应 stop_event 与 test_seconds 到时

### 测试（2.8.2）

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

[2.8.2]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.2
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
