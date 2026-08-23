# 交接文档 / 待办清单

> 交接时间：2026-08-20 · 项目：VibeTrace（刻迹）（VibeTrace）
> 远程仓库：https://github.com/Niangaol/VibeTrace（master 分支）
> 当前版本：v2.8.1（已发布，2026-08-23）
> 当前提交：7d60620

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
| v2.4.0 | ✅ 已发布 | 测试流程金字塔（docs/TEST_WORKFLOW.md + pytest 85项 + CI fast/full + 覆盖率 56%）；Phase 3 时间节省估算（insights.time_saved）落地；前端 6 项细节修补；配置漂移修复；应用白名单补齐 |
| v2.5.0 | ✅ 已发布 | Vibe Coding 分析平台主线：AI 会话质量评分、时间轴回放、成本预算告警、多工具横向对比、能力成长曲线、受限模板查询；前端模板外抽 |
| v2.5.1 | ✅ 已发布 | 修复真实使用中的前端/数据层缺陷（导出 400、成长/对比按钮、模型识别与成本）+ AI 模型价格设置（/api/pricing） |
| v2.5.2 | ✅ 已发布 | 精炼 AI 会话模型识别：会话级模型仅统计 assistant 消息已知模型，「未识别」大幅减少 |
| v2.5.3 | ⚠️ 无 tag/Release | 仅在 CHANGELOG 有记录，未打 tag（AI 价格设置可用 + 导出进度反馈） |
| v2.7.0 | ✅ 已发布 | 行动与目标：告警闭环（alerts.py · 预算 warn/exceed + 连续工作休息提醒，托盘气泡）；每日目标与 streak（goals.py · 可选默认关闭，/api/goals + 概览进度面板 + 设置开关组）；全局性能优化（AI/浏览器历史指纹缓存、SQLite 提速、Token 真实用量优先与加权估算、learn.py 基线） |
| v2.8.1 | ✅ 已发布 | 小版本收口（无新特性）：多日 AI 成本查询 121s→0.95s（指纹确定性/缓存容量/解析记忆化/查询级批作用域）；/api/budget 边界年 500 兑现 200 空态契约；测试体系合二为一（test_all.py 47 函数并入 pytest 并退役，全链路 E2E 七阶段，覆盖率实测 79%） |
| v2.8.0 | ✅ 已发布 | 工程收尾与测试补位：dashboard 纯函数外置 dashboard_util.py（1917→1714 行）、frontend smoke（4 项）+ e2e 冒烟（2 项）、覆盖率门禁 65→70；Git 侧采纳率代理指标（/api/adoption · 免责+折叠展示，confidence 永不 high）；受限查询模板扩充（q6 产出对比 / q7 专注度最佳日 / q8 成本趋势） |

---

## 已完成（截至 v2.2.0）

### P1
- ✅ 仪表盘周报/月报视图
- ✅ 仪表盘数据导出（CSV/JSON）
- ✅ AI 会话深度统计（`ai_sessions.py`，默认关闭）
- ✅ 数据备份/恢复
- ✅ 配置热重载
- ✅ 托盘通知
- ✅ 主题切换
- ✅ 仪表盘访问口令
- ✅ Firefox 历史支持
- ✅ SQLite 后端 `usage.db`（`sqlite_store.py`，JSONL 仍为原始事实源）
- ✅ 多语言 README

### P2
- ✅ CHANGELOG.md + CHANGELOG.en.md
- ✅ CONTRIBUTING.md + Issue/PR 模板
- ✅ README CI/Release 徽章
- ✅ version.py ↔ tag 同步校验
- ✅ 测试覆盖率（含 insights/updater/sqlite_store/ai_sessions）
- ✅ GitHub Pages 文档站
- ⏸️ exe 代码签名（无证书，未做）

### 长期目标
- ✅ UWP/商店应用识别（`win32core.get_uwp_app_name` + `config.uwp_app_names`）
- ✅ 管理员权限模式（`monitor.py --admin` 自动 UAC 提权）
- ✅ Firefox 停留时长估算（`config.firefox_dwell_max_s`，默认 600s）
- ✅ 更新供应链安全（资产下载地址白名单）

### AI 编程深度追踪（docs/ROADMAP.md · Phase 1 · v2.3.0 开发中）
- ✅ 对话轮次追踪（本地会话 user→assistant 配对 + 浏览器历史 Web AI 会话分组/轮次推断）
- ✅ Token 用量估算（`ai_sessions.token_estimation`：CJK 1 Token/字，其余 4 字符/Token）
- ✅ 按模型拆分（`by_model`，模型字段/内容正则识别）
- ✅ 按项目拆分（`by_project`，cwd/project/repo 字段，会话级归口）
- ✅ 仪表盘「AI 会话详情」面板 + 日报「AI 会话深度」章节 + `ai_sessions --web` CLI
- ✅ Phase 3 成本与 ROI（按模型计价 / 按项目分摊 / 成本面板与日报成本章节 + 周/月汇总成本账本）
- ✅ Phase 3 时间节省估算（`insights.time_saved`：AI 时长 × 因子 2.0 离线估算，洞察面板 · v2.4.0）
- ✅ Phase 4 行为洞察（死循环检测 + 专注度评分 + Vibe 编程人格分析；洞察页面板 + 日报今日建议）
- ✅ Phase 2 的 Git 集成·代码变更分析（`git_insights.py`：只读本地提交/增删行/改动文件/修改率，洞察面板 + 日报）
- ⏸️ Phase 2 采纳率/留存率/修改率（需 IDE 插件，尚未做）
### 更多应用适配
- ✅ 常用软件显示名/分类补充（Obsidian/Notion/Slack/Teams/企业微信/飞书/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools 等）
- ✅ 社交软件识别补充（企业微信/飞书/Slack/Teams/WhatsApp/LINE/Skype）
- ✅ 浏览器适配补充（Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/搜狗/傲游/Slimjet）
- ✅ AI 工具识别补充（Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp）
- ✅ 终端 TUI 工具补充（tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig）

---

## 未做 / 待定

| # | 项 | 说明 |
|---|---|---|
| 1 | exe 代码签名 | 需要有效的代码签名证书，当前无证书 |
| 2 | AI 会话解析精度 | 第三方工具格式差异较大，目前 best-effort，可能统计缺失 |
| 3 | GitHub Pages 只做简单 landing | 如需完整文档站可继续扩展（当前够用） |
| 4 | 周报/月报多语言 / UI 多语言 | 可选，当前 UI 中文 |
| 5 | ROADMAP Phase 1 已落地（v2.3.0 规划） | 对话轮次/Token估算/按模型·项目拆分/会话详情面板/报告章节 ☆ 见 [docs/ROADMAP.md](docs/ROADMAP.md)；Phase 3 成本与 ROI、Phase 4 行为洞察（死循环/专注度/人格）已落地；Phase 2 的 Git 代码变更分析已落地，采纳率/留存率仍需 IDE 插件；Phase 3 时间节省估算（time_saved）随 v2.4.0 落地 |

---

## 已知限制

- 管理员权限窗口标题：普通权限读取不到，可用 `monitor.py --admin` 以管理员运行
- UWP/商店应用：已能识别包显示名，但部分应用仍可能按 exe 记录
- 后台标签页不计时（前台注意力口径）
- 打包 exe 未代码签名，可能有杀软误报
- Firefox 停留时长是估算值（相邻访问间隔，上限可配）
- WSL 内运行的 CLI 工具会话文件不被自动扫描（可在 `ai_sessions.paths` 显式配 `\\wsl.localhost\...` UNC 路径）；各工具监控支持矩阵见 [docs/HARNESSES.md](docs/HARNESSES.md)

---

## 交接备忘（环境/命令）

- **代理**：`127.0.0.1:7897`；git 已配代理；gh 已登录（Niangaol）
- **Python**：默认 `python`=3.14；带 PyInstaller 的 3.11 在
  `C:\Users\niangao\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe`
- **构建**：`python -m PyInstaller VibeTrace.spec --noconfirm`（先停守护任务，exe 会被占用）
- **测试**：`python -m pytest tests/ -q`（483 项全过；test_all.py 已并入退役）；`ruff check .`（0 违规）；`coverage run -m pytest tests/unit tests/integration tests/api tests/security tests/performance tests/e2e -q && coverage report --fail-under=70`（实测 79%）；详见 `docs/TEST_WORKFLOW.md`
  - 若 Windows 临时目录权限导致测试失败，可先清理 `%TEMP%\usagemon_hist_*` / `dsh-*`
- **发布**：`git tag vX.Y.Z && git push origin vX.Y.Z` → CI 自动测试→构建→冒烟→Release
- **守护**：计划任务 `VibeTrace`（exe）/`VibeTraceReport`（每日 19:30 日报）
