# 贡献指南 · CONTRIBUTING

欢迎为 **VibeTrace（刻迹）（VibeTrace）** 贡献代码、文档或 Issue。本指南面向所有协作者，
约定项目的开发环境、代码风格、测试、提交、分支与 PR、发布以及隐私要求。

请确保已阅读并理解 [README](README.md) 与《[项目需求与开发文档](./项目需求与开发文档.md)》，
尤其是其中的**架构**、**数据说明**与**隐私约定**章节。

> 一句话原则：**纯本地、零第三方依赖、隐私第一**。任何改动都要尊重这三点。

---

## 目录

- [开发环境要求](#开发环境要求)
- [代码风格约定](#代码风格约定)
- [现有文件结构说明](#现有文件结构说明)
- [测试规范](#测试规范)
- [提交规范（commit message）](#提交规范commit-message)
- [分支与 PR 流程](#分支与-pr-流程)
- [发布流程](#发布流程)
- [隐私约定](#隐私约定)

---

## 开发环境要求

本项目为 **Windows 专属**工具，开发环境需满足：

| 项 | 要求 |
|---|---|
| 操作系统 | **Windows 10 / Windows 11（x64，64 位）** |
| Python | **Python 3.10 及以上**（推荐 3.11；CI 使用 3.11） |
| 架构 | **64 位**（`win32core.py` 直接 `ctypes` 调用 Win32 API，32 位解释器不可用） |
| Git | 任意可用版本 |
| 可选 | `pip install pyinstaller`（本地重建 exe 时用） |
| 开发工具 | `pip install pytest ruff`（跑测试 / 静态检查用；**运行时零依赖**，这两个只装开发机） |

- 项目**零第三方运行时依赖**，正常工作只需 Python 标准库 + `ctypes`，**不要**引入 psutil / pywin32 等包。
- 需要打包测试时，用 `python -m PyInstaller VibeTrace.spec --noconfirm` 重建 exe（见 README「打包为 exe」）。
- 只读检查、本地定时任务、Windows 基础操作一般无需管理员权限；但读取**提权窗口标题**需要以管理员身份运行监控进程。

---

## 代码风格约定

请严格遵循以下约定，保持与现有代码一致：

- **零第三方依赖**：项目刻意 **只使用 Python 标准库 + `ctypes` 直调 Win32**。
  提交的代码不得新增对 `pip install xxx` 的依赖（测试、文档除外，见下方说明）。
  如需读取系统信息，优先用 `CreateToolhelp32Snapshot` 等 Win32 API，而非额外轮询或第三方包。
- **中文注释**：源码注释、docstring、README、日志文案一律使用**简体中文**；标识符与关键字仍用英文。
- **类型注解**：函数与类尽量带类型注解（`from __future__ import annotations` 已普遍使用），方便静态检查与阅读。
- **编码与换行**：源码文件保持 **UTF-8**，建议配合 CRLF 行尾；`# -*- coding: utf-8 -*-` 用于需显式标识的脚本。
- **日志与输出**：面向用户的打印/日志统一走项目现有的日志机制（`errors.log`、标准输出格式化）；不要新增与现有一致的风格冲突的输出方式。
- **健壮性**：边界情况要兜底（如窗口标题为空、进程树不全、数据文件损坏等），失败时静默降级或记入 `errors.log`，不要向用户抛裸异常。
- **不写死路径**：路径解析统一走 `paths.py`（全项目零硬编码路径，请看 README「数据位置」），不要新增硬编码盘符。
- 保持代码**小步增量**：改动尽量局部、可读、可回滚，避免大范围重构夹带进功能提交。

### 现有文件结构说明

改动前先熟悉以下核心文件（详细职责见 README「目录结构」）：

| 文件 | 职责 | 涉及改动注意 |
|---|---|---|
| `monitor.py` | 守护进程：轮询前台窗口、写 `usage.jsonl`、跨天聚合 | ⚠️ 改动影响数据写入，务必全量回归测试 |
| `win32core.py` | Win32 API 封装（`ctypes`，零第三方） | 新 API 调用都放这里 |
| `classifier.py` | 分类引擎：类别/联系人/AI 工具/黑名单/子分类 | 分类规则改这里或 `config.json` |
| `inventory.py` | 软件清单扫描 | 读取注册表/快捷方式 |
| `report.py` | 日报/周报/月报生成与 CLI 查询 | — |
| `derived.py` | 派生指标共用取数框架（`day_bundle` / `series`，v2.9.12） | 新派生指标一律走这里，不要再写逐日循环 |
| `metrics_util.py` | 统计辅助单一来源（熵 / HHI / 切换计数 / 维度合并 / 格式化） | 统计计算不要各写一份 |
| `tool_registry.py` | 工具注册表（唯一事实源） | 新增 AI 工具 / Web AI 域名改这里 |
| `git_insights.py` | Git 代码变更分析（区间批量 `range_batch`） | 多日取数进 `range_batch` 上下文 |
| `growth.py` / `query.py` / `tool_compare.py` / `advice.py` | 成长曲线 / 受限查询 / 工具对比 / 建议栏位 | 取数均已走 `derived` |
| `alerts.py` / `budget.py` / `goals.py` / `learn.py` / `adoption.py` / `timeline.py` | 告警 / 预算 / 目标 / 基线 / 采纳率 / 时间轴 | 可选功能默认关闭 |
| `inventory.py` | 软件清单扫描 | 读取注册表/快捷方式 |
| `dashboard_util.py` | dashboard 纯函数外置 | 纯函数测试不需要起服务 |
| `applog.py` | 滚动日志 + 降级观测（`note()`） | 有意吞掉的异常统一走 `note()`，不要静默 `pass` |
| `version.py` | 统一版本号 | 发布时在此递增 |
| `browser_history.py` | 浏览器 URL 级历史解析（Chromium/Firefox） | ⚠️ 涉及时复制的只读打开，勿改浏览器数据 |
| `dashboard.py` | 本地网页仪表盘（仅 127.0.0.1） | ⚠️ `/api/*` 必须做 Origin/Token 校验 |
| `tray.py` | 托盘图标（可选） | — |
| `tests/` | pytest 分层测试（unit/integration/api/frontend/performance/security/e2e，全量 882 passed / 7 skipped） | 新增功能必须补测试，详见「测试规范」与 `docs/TEST_WORKFLOW.md` |
| `paths.py` | 统一路径解析 | 新增路径一律加到这里 |
| `install.ps1` / `uninstall.ps1` | 安装/卸载（计划任务） | — |
| `VibeTrace.spec` | PyInstaller 打包配置 | 新增资源/图标需同步 |

> `config.default.json` 是分类/关键词/黑名单规则的**单一事实源**；新增规则优先改它，用户覆盖放 `config.json`。

---

## 测试规范

- **必须全量跑通最简命令**：
  ```powershell
  python -m pytest tests/ -q
  ```
  pytest **全量 882 passed / 7 skipped**（七层金字塔：unit / integration / api / frontend / performance / security / e2e），约 3 分钟。
  **任何提交前都必须保证全量通过**，不能只跑自己新增的用例。两个实操注意：
  - `-q` 已写进 pyproject 的 `addopts`，重复传会被吞掉摘要行；想看 passed/failed 摘要加 `-o addopts=""`；
  - 测试必须传**独立的 `config_path`**：默认配置会读到开发机仓库根的 `config.json`（AI 开启 → 真实调用 LLM 挂到超时；CI 无此文件所以从不暴露）。
- **新增功能必须补测试**：新用例写入 `tests/` 下对应分层目录（纯函数→`tests/unit/`、跨模块管线→`tests/integration/`、
  Dashboard 路由→`tests/api/`、安全断言→`tests/security/` 等），复用 `tests/conftest.py` 的 fixtures
  （临时目录 / mock_win32 / 伪时钟），保持无头、确定性、可重复；命名沿 `test_xxx` 风格。分层与迁移详见 `docs/TEST_WORKFLOW.md`。
- 测试里**不要依赖真实前台/网络/浏览器数据**；用临时目录隔离测试数据，用例结束后清理。
- 涉及数据写入的改动（monitor / classifier / report 等），务必跑 `python report.py --verify`（及 `--repair`）验证数据完整性路径不被破坏。
- 打包类改动可用 `python -m PyInstaller VibeTrace.spec --noconfirm` 顺带验证 exe 冒烟（`--version`）。
- 覆盖率：`coverage run -m pytest tests/`（当前实测约 80%，CI 门禁 `fail-under=70%`）；`ruff check .` 必须 0 违规。

> CI（`.github/workflows/ci-fast.yml`）：push/PR 触发 ruff + unit/integration/api/security；tag push（`build.yml` 监听 `v*`）触发全量测试 + 覆盖率门禁 + PyInstaller 构建 + exe 冒烟 + 自动建 GitHub Release（见 `docs/TEST_WORKFLOW.md`）。

---

## 提交规范（commit message）

提交信息使用**中文描述**，并带如下**前缀**（参考现有 `git log` 风格），前缀后接冒号与一句话/短段中文摘要：

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat:` | 新功能 | `feat: 应用分组自定义（覆盖层分类 + 仪表盘分组视图 + API）` |
| `fix:` | 缺陷修复 | `fix: report.py 缺失 import re（verify 路径 NameError）` |
| `docs:` | 文档（README / 文档 / 注释 / TODO） | `docs: README 目录/架构图/FAQ + --version 统一版本号` |
| `ci:` | CI / 工作流 / 构建脚本 | `ci: Release 步骤幂等（allowUpdates）` |
| `test:` | 测试相关 | `test: 补托盘调度 9 项断言` |
| `chore:` | 杂项（依赖/配置/工具链） | `chore: gitignore 覆盖沙箱测试临时目录（.tmp_*/）` |

规范要点：

- 首行建议 ≤ 72 字；需要时用空行分隔，加一段说明正文（中文）。
- 一个 commit 只做一件事；不要把无关改动（如格式化 + 功能 + 文档）混进一个 commit。
- 涉及隐私/数据语义的改动，在正文里说明对现有 `usage.jsonl` 数据是否向后兼容。
- 不要提交运行数据或本地私有配置（见[隐私约定](#隐私约定)）；提交前 `git status` 确认干净。

---

## 分支与 PR 流程

采用 **GitHub Flow 的简化版**，主分支为 `master`：

1. 从最新的 `master` 拉出**功能分支**，命名风格 `feature/<简述>` 或 `<类型>/<简述>`
   （如 `feature/app-groups`、`fix/report-import`）。
2. 在功能分支上小步提交（遵循上面的提交规范），**中途与提交前都全量跑 `python -m pytest tests/ -q`**。
3. 推送到远程后，打开 **Pull Request**，使用仓库提供的 [PULL_REQUEST_TEMPLATE](./.github/PULL_REQUEST_TEMPLATE.md) 填写：
   变更摘要、关联 Issue、测试情况、截图/验证说明。
4. 至少 **1 位协作者 Review** 后合并；有修改意见就继续补 commit，直到通过。
5. **PR 需通过 CI**：`.github/workflows/ci-fast.yml` 在 push/PR 时跑 ruff + 分层测试；tag push 时走全量 + 覆盖率门禁 + 构建冒烟（见 `docs/TEST_WORKFLOW.md`）。CI 未通过前不要自行 merge 到 master。
6. 合并到 `master` 后删掉已合并的功能分支。

> 涉及数据/管线改动建议在合并前跑一次完整链路：`python -m pytest tests/ -q`，保证全绿。

---

## 发布流程

> 详见 README「打包为 exe」与 `.github/workflows/build.yml`。

1. **版本号递增**：在 `version.py` 递增 `__version__`（遵循 `vX.Y.Z` 语义化），并**同步更新《CHANGELOG.md》**（按 keep-a-changelog 格式记录 `Added / Fixed / Changed`）。
2. 确保本期所有改动已合并到 `master`，且 `python -m pytest tests/ -q`（全量 882 passed / 7 skipped）与 `ruff check .`（0 违规）均通过。
3. **打 tag 并推送**（触发 CI 自动「测试 → 构建 exe → 冒烟 → 创建 Release」）：
   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. 查看 GitHub Actions 中的 `test` 与 `build` job：`build` 依赖 `test` 通过，之后构建 `dist\VibeTrace.exe`，
   冒烟 `.\dist\VibeTrace.exe --version`，并（仅 tag push）自动创建 GitHub Release 附上 exe 产物。
5. Release 完成后，在 Release 页面核对产物与版本号一致；如需要可补充发布说明。

> CI 会用 `--version` 冒烟，请确保 tag 版本与 `version.py` 一致（P2 后续计划加入二者一致性断言）。

---

## 隐私约定

本项目高度敏感于**个人隐私**，贡献者务必遵守：

- **运行数据绝不入库**：每天的日期文件夹（`usage.jsonl` / `report.md` / 软件清单 / 浏览器访问明细）
  包含**真实窗口标题与 URL**，属于个人隐私。`.gitignore` 已排除 `20*-*/`、`usage.db` 等；
  **提交代码时确保 `git status` 不出现任何日期目录或数据文件**。
- **本地私有配置不入库**：`aliases.json`（真实联系人别名）被 `.gitignore` 忽略；
  仓库只提供 `aliases.example.json` 模板。不要在 Issue/PR/提交中粘贴真实的联系人名、真实窗口标题或真实 URL。
- 代码本身不得包含任何**个人/演示数据**；分类规则、关键词、黑名单放 `config.default.json`（可入库的通用规则）。
- 截图/演示一律使用 `python make_demo_data.py` 生成的**虚构数据**，且不截到真实使用记录。
- 默认**无任何联网上传**；仪表盘仅监听 `127.0.0.1`；不实现截屏/录屏/OCR/键盘钩子/剪贴板读取。
  新增的本地服务端点必须做安全校验（`/api/*` 校验 `Origin`/`Referer` 与可选的 Dashboard Token）。
- 汇报 bug 或隐私相关问题时，先在本地以 `[已隐藏]` 替换敏感字段（黑名单命中即为 `[已隐藏]`），再提交日志片段。

---

再次感谢你的贡献！有任何疑问可在 Issue 中提出，或先阅读《项目需求与开发文档》与 [TODO.md](TODO.md)。
