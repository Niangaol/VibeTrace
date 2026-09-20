# 交接文档 / 待办清单

> 交接时间：2026-09-20 · 项目：VibeTrace（刻迹）（VibeTrace）
> 远程仓库：https://github.com/Niangaol/VibeTrace（master 分支）
> 当前版本：v2.9.12（本次发布；合并 v2.9.7 – v2.9.12 六个内部迭代，均未单独打 tag）
> 当前提交：见 git log（v2.9.12 release 提交，含 2.9.7 起全部未提交改动）

---

## 版本里程碑

| 版本 | 状态 | 关键内容 |
|---|---|---|
| v1.0.0 | ✅ 已发布 | 监控核心、日报/仪表盘、CI 构建 |
| v1.1.0 | ✅ 已发布 | 应用分组（P0）+ 九项增强（P1）+ Electron 壳 |
| v1.2.0 / 1.2.1 / 1.3.0 | ✅ 已发布 | 智能洞察、分组显示名/导入导出、修复 |
| v1.3.1 / v1.4.0 | ⚠️ 无 tag/Release | 仅在 CHANGELOG 有记录，从未发布 |
| v1.5.0 | ✅ 已发布 | AI 洞察扩充 + 客制化模块 + 图形安装向导 |
| v1.6.0 | ⚠️ 无 tag/Release？ | 新版本检测与应用内更新（代码已合入 2.0 演进） |
| v2.0.0 | ✅ 已发布 | AI 会话深度统计、SQLite 后端、GitHub Pages、Review 修复 |
| v2.1.0 | ✅ 已发布 | AI 统计支持更多工具（Cursor/Windsurf/Trae/DeepSeek/Pi Agent/DSH） |
| v2.1.1 | ✅ 已发布 | SQLite 一致性校验、周聚合快速路径、updater/更新 API 测试、SHA256 资产、覆盖率扩展 |
| v2.2.0 | ✅ 已发布 | UWP 识别、管理员模式、Firefox 停留时长、更新供应链安全、更多应用适配 |
| v2.3.0 | ✅ 已发布 | AI 会话深度（Phase 1）+ 成本与 ROI（Phase 3）+ 概览整合 / AI 洞察独立 |
| v2.4.0 | ✅ 已发布 | 测试流程金字塔（docs/TEST_WORKFLOW.md + CI fast/full + 覆盖率门禁）；Phase 3 时间节省估算（insights.time_saved）；前端 6 项细节修补；配置漂移修复；应用白名单补齐 |
| v2.5.0 | ✅ 已发布 | Vibe Coding 分析平台主线：AI 会话质量评分、时间轴回放、成本预算告警、多工具横向对比、能力成长曲线、受限模板查询；前端模板外抽 |
| v2.5.1 | ✅ 已发布 | 修复真实使用中的前端/数据层缺陷（导出 400、成长/对比按钮、模型识别与成本）+ AI 模型价格设置（/api/pricing） |
| v2.5.2 | ✅ 已发布 | 精炼 AI 会话模型识别：会话级模型仅统计 assistant 消息已知模型 |
| v2.5.3 | ⚠️ 无 tag/Release | 仅在 CHANGELOG 有记录，未打 tag（AI 价格设置可用 + 导出进度反馈） |
| v2.7.0 | ✅ 已发布 | 行动与目标：告警闭环（alerts.py）；每日目标与 streak（goals.py）；全局性能优化（AI/浏览器历史指纹缓存、SQLite 提速、Token 真实用量优先与加权估算、learn.py 基线） |
| v2.8.0 | ✅ 已发布 | 工程收尾：dashboard 纯函数外置 dashboard_util.py；frontend smoke + e2e 冒烟；覆盖率门禁 65→70；Git 侧采纳率代理指标（/api/adoption）；受限查询模板扩充（q6/q7/q8） |
| v2.8.1 | ✅ 已发布 | 小版本收口：多日 AI 成本查询 121s→0.95s；/api/budget 边界年 500→200 空态契约；测试体系合二为一（test_all.py 并入 pytest 并退役）；覆盖率实测 79% |
| v2.8.2 | ✅ 已发布 | 小版本修复：五处模块级缓存补线程锁；报表链透传 config_path；守护循环暂停分支补退出检查；三类回归钉扎 |
| v2.9.0 | ✅ 已发布 | 仪表盘视觉升级：设计令牌系统化、亮暗双主题、11 类克制动效；模型价格按厂商分组折叠；应用品牌图标；目录枚举确定性化；周/月报 config_path 贯通 |
| v2.9.1 | ✅ 已发布 | 成长/对比七类新指标；Git 深度分析（auto_discover + author_detail/commit_rhythm/language_dist）；模型正则 60+/agent 路径 29/定价 90 条；ActivityWatch 参考指标；DSH Desktop 适配 |
| v2.9.2 | ✅ 已发布 | 修复批次：Git 深度崩溃（UnboundLocalError）；学习曲线洞察死代码；模型多样熵误塞 app 切换熵；对比视图 colspan；增量合并死变量 |
| v2.9.3 | ✅ 已发布 | vibe coding 指标三连修复；ZCode/Codex CLI 会话深度适配；DSH zstd 支持（含 PyInstaller zstandard 打包） |
| v2.9.4 | ✅ 已发布 | 框架统一：tool_registry 工具注册表 + metrics_util 统计辅助单一来源；仪表盘响应缓存框架（TTL+SWR 单飞，六端点共用）；AI 编程热力图；前端错误横幅+重试；非法日历日 400 契约；全量 730 passed |
| v2.9.5 | ✅ 已发布 | Token 口径与成本修正（缓存读按供应商折扣计）；概览「建议」栏位（advice.py）；两处热力图统一趋势口径；托盘单击改开浏览器；config.json 取消版本跟踪；全量 744 passed |
| v2.9.6 | ✅ 已发布 | 修复 v2.9.5 托盘回归（单击恢复唤出 Electron 壳）；USAGEMON_USE_BROWSER=1 调试开关；spec hiddenimports 补全；新增 test_open_dashboard.py 三项钉扎；全量 747 passed |
| v2.9.7 – v2.9.12 | 🔀 合并为 v2.9.12 发布 | 六个内部迭代从未单独打 tag，改动一次性合并发布：derived.py 取数框架；浏览器历史免整库拷贝（12×）；git 区间批量 range_batch（44 天 9.2s→1.4s）+ 等价性修正；34 处降级可观测（applog.note）；metrics_util 桶字段收敛；.gitignore 封堵内嵌 Python；测试隔离修复 |

---

## 已完成（截至 v2.9.12）

### 基础与监控
- ✅ 前台窗口计时（5s 轮询、变化才写、静止零写入）；空闲/锁屏不计时
- ✅ 软件清单扫描（注册表/开始菜单/运行进程）+ 自动分类
- ✅ 社交联系人识别 + 别名表（aliases.json）
- ✅ 浏览器 URL 级历史（Chromium 系 + Firefox，免拷贝直读源库）
- ✅ AI 编程监控（进程树识别终端里的 AI CLI，tool_registry 单一事实源）
- ✅ UWP/商店应用识别；管理员模式（--admin 自动 UAC）

### 报表与仪表盘
- ✅ 日报/周报/月报（Markdown + CSV）+ CLI 查询 + verify/repair
- ✅ 本地网页仪表盘十三视图（概览/趋势/日报/周报/月报/会话/时间轴/成长/对比/日志/分组/洞察/设置）
- ✅ 数据导出（CSV/JSON）、备份/恢复、访问口令、Origin 校验
- ✅ 仪表盘响应缓存框架（TTL+SWR 单飞，六端点共用）

### 洞察与 AI
- ✅ 离线规则引擎 + 个性化基线（learn.py，Welford/z-score）
- ✅ 可选 AI 洞察（OpenAI 兼容端点，默认关闭，聚合统计隐私过滤）
- ✅ AI 会话深度统计（轮次/Token/成本/质量评分；ZCode/Codex/DSH zstd 等适配）
- ✅ 告警闭环（alerts.py）+ 每日目标 streak（goals.py）
- ✅ 采纳率代理（adoption.py，Git 侧，免责+折叠）
- ✅ 降级可观测（applog.note，34 处静默异常落盘 logs/app.log）

### 框架与性能（v2.9.7 – v2.9.12）
- ✅ derived.py 派生取数框架：13 处逐日循环（229 行）收敛为 day_bundle/series 单入口，六模块共用
- ✅ 浏览器历史免整库拷贝：_query_source_ro 直读源库，6.1ms → 0.49ms（约 12×）
- ✅ git 区间批量 range_batch：一次 git log 按 committer date 分桶，44 天约 9.2s → 1.4s
- ✅ metrics_util 统计辅助单一来源（熵/HHI/切换计数/维度合并/格式化），_new_bucket 消除双份定义
- ✅ tool_registry 工具注册表（21 本地 + 4 Web AI 单一事实源）

### 测试与工程
- ✅ pytest 分层测试（unit/integration/api/frontend/performance/security/e2e）
- ✅ 覆盖率门禁 70%（实测约 80%）；ruff check . 0 违规
- ✅ PyInstaller 单文件 exe + CI 打 tag 自动构建 Release（附 sha256）
- ✅ GitHub Pages 文档站

---

## 未做 / 待定

| # | 项 | 说明 |
|---|---|---|
| 1 | exe 代码签名 | 需要有效的代码签名证书，当前无证书 |
| 2 | AI 会话解析精度 | 第三方工具格式差异较大，目前 best-effort，可能统计缺失 |
| 3 | GitHub Pages 只做简单 landing | 如需完整文档站可继续扩展（当前够用） |
| 4 | 周报/月报多语言 / UI 多语言 | 可选，当前 UI 中文 |
| 5 | ROADMAP Phase 2 采纳率/留存率 | 需 IDE 插件提供事件源；当前只有 Git 侧粗代理（adoption.py） |
| 6 | 剩余约 19 处 except pass | 「日志自身失败」与 __main__ 入口兜底，按设计保持静默 |

---

## 已知限制

- 管理员权限窗口标题：普通权限读取不到，可用 `monitor.py --admin`
- UWP/商店应用：已能识别包显示名，部分应用仍可能按 exe 记录
- 后台标签页不计时（前台注意力口径）
- 打包 exe 未代码签名，可能有杀软误报
- Firefox 停留时长是估算值（相邻访问间隔，上限可配）
- WSL 内运行的 CLI 工具会话文件不被自动扫描（可在 `ai_sessions.paths` 显式配 UNC 路径）
- `shannon_entropy` 对负计数产出负熵（理论缺陷，全部调用点已核实恒非负，characterization 测试钉死）

---

## 交接备忘（环境/命令）

- **代理**：`127.0.0.1:7897`；git 已配代理
- **GitHub token**：`powershell -File setup_gh_token.ps1`（把 GH_TOKEN/GITHUB_TOKEN 写进 PowerShell profile）；验证 `gh auth status`（账号 Niangaol，scopes: repo/workflow/read:org/gist）
- **Python**：
  - 跑测试/ruff：`C:\Python314\python.exe`（仓库根的 `python` 是内嵌精简运行时，**没有 pytest/ruff**，别用它跑测试）
  - 打包 exe：带 PyInstaller 的 3.11 在 `C:\Users\niangao\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe`
- **测试**：`C:\Python314\python.exe -m pytest tests -q -p no:cacheprovider -o addopts=""`
  - 注意：`-q` 已在 pyproject addopts 里，显式传 `-o addopts=""` 才能看到 passed/failed 摘要行（否则只打印进度点）
  - Windows 临时目录权限异常时，先清理 `%TEMP%\usagemon_hist_*` / `dsh-*`
  - 测试隔离：api 用例必须传独立 `config_path`，否则会读到开发机仓库根 config.json（AI 开启→真实调用 LLM 挂到超时；CI 无此文件所以不暴露）
- **构建**：`python -m PyInstaller VibeTrace.spec --noconfirm`（先停守护任务，exe 会被占用）
- **发布**：提交全部改动 → `git tag vX.Y.Z` → `git push origin master --tags` → CI 自动测试→构建→冒烟→Release（build.yml 监听 `v*` tag）
- **守护**：计划任务 `VibeTrace`（exe）/ `VibeTraceReport`（每日 19:30 日报）
- **工作区卫生**：仓库根有真实 `config.json`（含 api_key，已 gitignore，**严禁提交**）；评审报告 `review-*.md` 与内嵌 `Python/` 运行时均已 gitignore
