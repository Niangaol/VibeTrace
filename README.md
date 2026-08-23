# VibeTrace · 电脑使用情况监控

> 纯 vibe coding 产物 · 本地优先 · Python 标准库 + ctypes · 零第三方运行时依赖

[VibeTrace](https://github.com/Niangaol/VibeTrace) 是一个 Windows 本地使用情况监控工具。它以常驻守护进程方式运行，采集前台窗口信息，记录软件、社交联系人、浏览器与 AI 编程使用时长，并由此生成日报、周报、月报和本地网页仪表盘。

No build step，无框架，无打包器 —— 纯 Python 标准库 + vanilla JS，`ctypes` 直调 Win32。数据默认只存在本机，不截屏、不录屏、不读键盘输入、不读聊天内容。

| 网页仪表盘 | 每日日报 | 桌面壳 |
|---|---|---|
| <img src="docs/screenshots/dashboard.png" width="480" alt="仪表盘"> | <img src="docs/screenshots/report.png" width="480" alt="日报"> | <img src="docs/screenshots/desktop_app.png" width="300" alt="桌面壳"> |

---

## Contents

- [Why VibeTrace](#why-usagemonitor) — 它是什么，和其他工具怎么比
- [Quick start](#quick-start) — clone + `python monitor.py`
- [Features](#features) — 监控 / 报表 / 洞察 / 适配 / 更新 / 安全
- [Configuration & access](#configuration--access) — 配置发现、环境变量、访问方式
- [Architecture](#architecture) — 后端模块布局
- [Roadmap](#roadmap) — AI 编程深度追踪规划
- [Running tests](#running-tests)
- [Docs](#docs)

---

## Why VibeTrace

多数使用统计工具把数据同步到云端，或依赖商业服务计费，且没有专门覆盖“AI 编程”和“浏览器 URL 级历史”两个维度。

在目前的开源项目中，追踪 AI 编程的工具大多只统计 Token 消耗或会话数，且通常是独立工具，不与整体电脑使用数据放在一起分析。VibeTrace 把 AI 编程时间作为与软件、社交联系人、浏览器并列的一个维度，整合进同一套监控和报表体系——既在前台窗口 / 进程树层面做 AI 工具计时，也支持读取本地 AI 工具会话文件做进一步的深度统计。

与其他工具的关键差异：

- **纯本地默认无上传** — 数据在 `data_root`，仪表盘只监听 127.0.0.1
- **零第三方运行时依赖** — 不依赖 psutil / pywin32 / 浏览器扩展 / 云服务
- **AI 编程监控** — 进程树识别终端里的 opencode / pi agent / claude 等，而非只看前台窗口
- **浏览器 URL 级历史** — Chromium + Firefox 访问明细，分类与停留时长
- **可选 AI 会话深度统计** — 读取本地 AI 工具会话文件，统计轮数与生成量
- **开源免费（MIT）**

**vs. 同类工具**：

| | VibeTrace | RescueTime | ManicTime | WakaTime | ActivityWatch |
|---|---|---|---|---|---|
| 本地优先、默认不上传 | Yes | 云同步 | 部分 | 云同步 | Yes |
| 开源免费 | Yes (MIT) | No | No | 部分 | Yes (MPL) |
| 零第三方运行时依赖 | Yes | No | No | No | 部分 |
| AI 编程监控（进程树） | Yes | No | 部分 | 部分 | 部分 |
| 浏览器 URL 级历史 | Yes | 部分 | 部分 | 编辑器为主 | 部分 |
| 本地网页仪表盘 | Yes | No | No | No | Dashboard |
| 应用内更新 | Yes | — | — | — | — |

> 说明：同类工具的能力边界会随版本变化，表内为常规功能口径。

---

## Quick start

```powershell
git clone https://github.com/Niangaol/VibeTrace.git
cd VibeTrace

# 测试运行 30 秒（观察是否正常写当日文件夹）
python monitor.py --test 30

# 前台运行
python monitor.py --foreground

# 托盘守护
python monitor.py --tray
```

图形安装 / 卸载（免命令行）：

```powershell
powershell -ExecutionPolicy Bypass -File installer.ps1
powershell -ExecutionPolicy Bypass -File uninstaller.ps1
```

如果你需要读取管理员权限窗口的标题：

```powershell
python monitor.py --admin   # 非管理员时自动弹 UAC 提权重启
```

> **停止守护**：托盘右键「退出」；`--foreground` 按 Ctrl-C；以计划任务运行时由任务管理器处理。

---

## Features

### Monitoring

- 前台窗口计时：默认 5 秒轮询，仅在状态变化时写一条（静止零写入）
- 空闲/锁屏不计时：默认 3 分钟无键鼠输入截断会话
- 软件清单扫描：注册表卸载项、开始菜单快捷方式、运行中进程
- 社交联系人识别：微信 / QQ / 钉钉 / 企业微信 / 飞书 / Slack / Teams 等
- 浏览器活动分类：标题关键词 → 视频 / 代码 / 学习 / 其他
- 浏览器 URL 级历史：Chromium（Chrome/Edge/Brave/Opera/Vivaldi 等）与 Firefox
- AI 编程监控：进程树识别终端/编辑器集成终端里的 AI CLI 工具

### Reports & dashboard

- 日报、周报、月报（Markdown + CSV）
- 本地网页仪表盘，十三个视图：概览 / 趋势 / 日报 / 周报 / 月报 / 会话 / 时间轴 / 成长 / 对比 / 日志 / 分组 / 洞察 / 设置
- 数据导出（CSV / JSON）、备份 / 恢复

### Insights & AI

- 离线规则引擎：学习 / 游戏 / 健康 / 效率 / 平衡 / 趋势建议
- 个性化基线（v2.7「简单学习」）：滑动窗口 + z-score 在线统计学习，"今日比你的常态偏离 N σ"类洞察，越用越准、零依赖零预设阈值
- 可选 AI 洞察：OpenAI 兼容端点，聚合统计隐私过滤，默认关闭
- AI 会话深度统计：读取 opencode / ChatGPT / Claude / Cursor / Windsurf / Trae / DeepSeek / Pi Agent / DSH 本地会话文件（各工具支持程度与扩展方法见 [docs/HARNESSES.md](docs/HARNESSES.md)）；Token 优先读会话内真实 usage 字段，缺失时按字符类别加权估算（`token_estimation_mode: weighted|simple`）
- 告警闭环（v2.7）：AI 成本预算接近/超支、连续工作休息提醒——托盘气泡主动通知，阈值/冷却可配
- 每日目标（v2.7 · 可选）：总活跃/编码时长目标 + 连续达成天数，概览页进度面板，默认关闭
- 采纳率代理（v2.8 · 仅参考）：Git 侧 retention / 返工率粗代理（`/api/adoption`），洞察页折叠 + 灰色降权 + 强制免责声明，confidence 永不 high；AI 侧 per-file 归因按 spike 结论判砍
- 受限查询扩充（v2.8）：新增「今日产出 vs 昨日」「本周专注度最佳日」「成本趋势」模板，支持双周期对比与周期别名

### Adaptation

- 应用分组自定义：覆盖层配置，实时生效
- 常用软件显示名 / 分类：Obsidian / Notion / Slack / Teams / Steam / Spotify / VLC / PowerToys 等
- 浏览器适配：Vivaldi / Yandex / Chromium / Opera GX / Arc / Cent / 搜狗 / 傲游 / Slimjet 等
- AI 工具识别：Codex / Goose / Amazon Q / DSH / Claude Code / Gemini CLI / Continue / Bamboo / Augment / Warp 等
- 终端 TUI 工具：tmux / btop / lazygit / k9s / lazydocker / kubectl / fzf / rg / ncdu / tig 等

### Updates & packaging

- 新版本检测：启动检查、托盘菜单、仪表盘设置页
- 应用内更新：SHA256 校验下载 → 优雅退出 → 替换 exe → 自动重启
- 更新供应链安全：下载地址白名单，仅接受 GitHub 官方域名或 `update.api_base` 指定域名
- PyInstaller 单文件 exe，CI 打 tag 自动构建 Release（附 `sha256`）

### Security & privacy

- 仪表盘只监听 `127.0.0.1`；所有 `/api/*` 校验 Origin / Referer
- 可选访问口令（`dashboard_token`，HMAC 常量时间比较）
- 标题隐私黑名单，命中记 `[已隐藏]`
- AI 洞察默认关闭，开启才发送聚合统计（不含标题 / URL / 联系人）
- UWP/商店应用识别（`uwp_app_names`）

### Optional backends

- SQLite 后端 `usage.db`：JSONL 之外的镜像/索引，支持回填 / 重建 / 一致性校验
- GitHub Pages 文档站：https://niangaol.github.io/VibeTrace/

---

## Configuration & access

### 配置发现

| 项 | 怎么找到 |
|---|---|
| 配置文件 | `config.json`（不存在时用 `config.default.json`） |
| 数据根目录 | `data_root`；空字符串 = 程序所在目录 |
| 配置热重载 | monitor 每轮重读 `config.json`（`data_root` 保持启动值） |
| 别名表 | `<data_root>/aliases.json`（不入库） |

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `USAGEMON_PROJECT_DIR` | 脚本目录 | 项目根覆盖 |
| `DATA_ROOT` | `config.json` 的 `data_root` | 数据根覆盖 |
| `PORT` | `8765` | 仪表盘端口 |
| `PYTHON` | 自动探测 | Electron 壳 / 启动器使用的 Python |
| `USAGEMON_USE_BROWSER` | 未设置 | `=1` 强制用浏览器打开仪表盘（默认优先 Electron 壳） |

### 访问

```powershell
python dashboard.py --open            # 打开 http://127.0.0.1:8765
python dashboard.py --port 9000       # 自定义端口
VibeTrace.exe --dashboard --open   # exe 方式
```

托盘右键：今日概览 / 打开仪表盘 / 检查更新 / 暂停·继续 / 退出。

---

## Architecture

No build step、无框架 —— Python 标准库 `http.server` + vanilla JS。核心模块：

```
monitor.py         守护进程（轮询前台窗口、托盘、跨天聚合、--admin）
win32core.py       Win32 API（ctypes）：前台窗口 / 进程 / 空闲 / UWP / 管理员检测
classifier.py      分类、联系人、AI 工具、终端工具、配置加载
report.py          日报/周报/月报聚合、重分类、校验修复（含 SQLite 快速路径）
dashboard.py       本地网页仪表盘 + 全部 /api/* 路由
browser_history.py Chromium + Firefox 历史解析（含 Firefox 停留时长估算）
insights.py        智能洞察（离线规则 + 可选 AI）
ai_sessions.py     AI 会话深度统计
sqlite_store.py    可选 SQLite 后端 + 一致性校验
updater.py         新版本检测、应用内更新、下载地址白名单
tray.py            托盘图标
paths.py / applog.py  路径解析 / 滚动日志
```

状态默认存在仓库外的运行目录（日期文件夹 + `usage.jsonl`）。

> 性能：AI 会话统计、浏览器历史、SQLite 镜像写入均带**指纹缓存/共享连接**
> （mtime+size 变化自动失效，行为不变），仪表盘多端点重复聚合只算一次——
> AI 会话与浏览器历史热路径各提速约 200× / 144×，SQLite 写入约 66×。

---

## Roadmap

AI 编程深度追踪规划：

- **Phase 1（高优先）**：会话级精细追踪 —— 对话轮次、Token 估算、按模型/项目拆分
- **Phase 2（中高）**：质量与效率 —— 采纳率 / 留存率（需 IDE 插件）
- **Phase 3（中）**：成本与 ROI —— 模型定价、按项目分摊、自动化支出报表
- **Phase 4（中低）**：行为洞察 —— 死循环检测、专注度评分、Vibe Coding 人格分析

各阶段均可独立交付，完整规划见 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

**AI 会话成本估算**：`ai_sessions` 按内置主流模型定价表（USD/百万 Token，已更新至最新一代）做费用估算。定价随厂商波动，可用以下任一路径自定义/覆盖单个模型单价：

- `config.json` → `ai_sessions.costs.model_pricing`：
  `{"gpt-5": [1.25, 10]}` 或 `{"claude-sonnet-5": {"input": 2, "output": 10}}`
- 数据目录下放置 `ai_pricing.json`（同格式，`{"model": [输入价, 输出价]}` 或 `{"model": {"input":..,"output":..}}`；**优先级最高**）

---

## Running tests


```powershell
python -m pytest tests -q   # 483 项用例（单测/集成/API/安全/性能/E2E 全链路）
coverage run -m pytest tests/unit tests/integration tests/api tests/security tests/performance tests/e2e -q
coverage report --fail-under=70
ruff check .                # 0 违规
```

CI：测试 → coverage（含 insights/updater/sqlite_store/ai_sessions）→ PyInstaller 构建 → exe 冒烟 → 打 tag 发布 Release。

> Windows 临时目录权限异常时，先清理 `%TEMP%\usagemon_hist_*` / `dsh-*` 再跑测试。

---

## Docs

- [CHANGELOG.md](CHANGELOG.md)（[English](CHANGELOG.en.md)）
- [README.en.md](README.en.md)（English）
- [HARNESSES.md](docs/HARNESSES.md)（AI 工具监控支持矩阵：哪些工具能计时/会话深度/Web 追踪，如何扩展）
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [TODO.md](TODO.md)（交接/待办清单）
- [ROADMAP.md](docs/ROADMAP.md)（AI 编程深度追踪规划）
- [项目需求与开发文档.md](项目需求与开发文档.md)
- GitHub Pages：https://niangaol.github.io/VibeTrace/
