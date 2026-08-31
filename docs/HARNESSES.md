# VibeTrace · AI Harness 监控支持矩阵

> 本文说明 VibeTrace 当前能监控哪些 AI 编程工具（harness）、每个工具被监控到什么程度、
> 以及如何扩展。所有能力均为**只读本地、绝不联网**。

VibeTrace 对一个 AI 工具的"监控"分三个独立维度，一个工具可能只被其中一两个维度覆盖：

| 维度 | 做什么 | 数据来源 | 精度 |
|---|---|---|---|
| **A · 计时**（进程树识别） | 记录你"正在用这个工具"的时长 | 前台窗口 + 进程树 BFS（终端里跑的 CLI 也能归属到工具）+ 窗口标题关键词 | 前台注意力口径，5 秒轮询 |
| **B · 会话深度统计**（本地文件解析） | 轮次 / Token / 成本 / 质量 / 按模型·项目拆分 | 解析工具写在磁盘上的本地会话文件（JSON/JSONL/SQLite） | 启发式估算，量级参考 |
| **C · Web AI 会话**（浏览器历史） | 网页版 AI 聊天的会话数与轮次推断 | Chromium/Firefox 浏览器历史中的 URL 分组 | URL 级推断，best-effort |

---

## 支持矩阵

图例：✅ 完整支持 · ⚠️ 部分/best-effort · ❌ 不支持

| 工具 | A 计时 | B 会话深度 | C Web | 备注 |
|---|---|---|---|---|
| opencode | ✅ | ✅ | — | B 含专用 SQLite 解析器（`~/.local/share/opencode`） |
| Claude / Claude Code | ✅ | ✅ (`~/.claude`, `%APPDATA%/Claude`) | ✅ claude.ai | |
| ChatGPT 桌面版 | ✅ | ✅ (`%APPDATA%/ChatGPT`) | ✅ chatgpt.com | |
| Cursor | ✅ | ✅ (`%APPDATA%/Cursor`) | ✅ chat.cursor.com | |
| Windsurf | ✅ | ✅ (`%APPDATA%/Windsurf`, `~/.codeium/windsurf`) | — | |
| Trae | ✅ | ✅ (`%APPDATA%/Trae`) | — | |
| DeepSeek | ✅ | ✅ (`%APPDATA%/DeepSeek`) | ✅ chat.deepseek.com | |
| Pi Agent（π） | ✅（含标题 "π" 特判，排除 python/pip 误伤） | ✅（专用解析器，`~/.pi/agent/sessions` 等） | — | |
| DSH | ✅ | ✅（`%DSH_DATA%`/`%DSH_HOME%`/`~/.dsh`） | — | |
| ZCode | ✅ | ✅（`~/.zcode/cli/db/db.sqlite` + `~/.zcode/v2/sessions`） | — | B：SQLite 与 opencode 同源 schema（活跃 WAL 只读）；v2 JSON 走通用解析，无 model 字段按"未识别"口径 |
| Gemini CLI / Gemini | ✅ | ❌（本地会话格式未适配） | ✅ gemini.google.com | |
| Codex CLI | ✅ | ✅（专用解析器，`~/.codex/sessions`） | — | token 取 token_count 单次增量求和（真实口径，归因近似见 ai_sessions.\_parse_codex_file） |
| Goose | ✅ | ❌ | — | |
| Amazon Q | ✅ | ❌ | — | |
| Aider | ✅ | ❌ | — | |
| GitHub Copilot | ✅ | ❌ | ✅ copilot.microsoft.com | |
| Cline | ✅ | ❌ | — | |
| Continue | ✅ | ❌ | — | |
| Augment | ✅ | ❌ | — | |
| Bamboo | ✅ | ❌ | — | |
| Warp | ✅ | ❌ | — | |
| Perplexity | — | — | ✅ perplexity.ai | 仅 Web 维度 |
| Kimi | — | — | ✅ kimi.moonshot.cn / kimi.com | 仅 Web 维度 |
| 通义千问 Qwen | — | — | ✅ chat.qwen.ai / tongyi.aliyun.com | 仅 Web 维度 |
| 豆包 | — | — | ✅ doubao.com | 仅 Web 维度 |
| 秘塔 Metaso | — | — | ✅ metaso.cn | 仅 Web 维度 |

> B 列"❌"不代表永远不支持：这些工具的本地会话目录结构各异且随版本变化，
> 我们按 best-effort 逐步适配（见文末"路线"）。A 列识别的关键词表见
> `config.default.json` 的 `ai_keywords` / `ai_tool_names`。

---

## 各维度工作原理

### A · 计时（classifier.detect_ai_tool）

1. 前台进程自身 exe 名匹配关键词；
2. 进程树 BFS 向下找子孙进程（如 `wt.exe → opencode.exe`），取**最深层**命中——
   这样在 Windows Terminal / VS Code 集成终端里跑的 CLI 工具能归属到具体工具而非终端；
3. 窗口标题关键词兜底（含 π 特判：标题含 "π" 但同时含 python/pip/pypi 时不误判）。

### B · 会话深度统计（ai_sessions.collect）

- 按 `_DEFAULT_PATHS`（ai_sessions.py:45）逐工具扫描候选目录，递归收集
  `.json/.jsonl/.ndjson`（单文件 ≤20MB、每工具 ≤500 个文件）；
- 通用字段启发式解析：时间戳/角色/内容/模型/项目/会话 ID 各有一组候选键名
  （`_TIME_KEYS`/`_ROLE_KEYS`/`_CONTENT_KEYS`/`_MODEL_KEYS`/`_PROJECT_KEYS`），
  兼容大多数 JSON 结构；opencode 与 pi 有专用解析器；
- 轮次 = user→assistant 配对数；Token = CJK 1 字/Token、其余 4 字符/Token（进一法）；
- 成本 = tokens_in×输入价 + tokens_out×输出价（USD/百万 Token，内置价目表可覆盖）；
- 质量评分 = 提问含金量 0.35 + 返工(负向) 0.25 + 稳定性 0.2 + 上下文健康度 0.2。

### C · Web AI 会话（browser_history + web_ai_sessions）

- 从浏览器历史按域名识别 11 组 Web AI 站点（`_WEB_AI_TOOLS`，ai_sessions.py:219）；
- URL 模式匹配会话 ID（`/c/<id>`、`/chat/<id>`、`/conversation/<id>` 等 8 种模式 +
  `?c=<id>` 查询参数），同一会话 ID 的多次访问归并为一次会话并推断轮次。

---

## 自定义与扩展

**添加/修改 B 维度的扫描路径**（工具装在非默认位置、或适配新工具）：

```json
// config.json → ai_sessions.paths（整体覆盖默认表；支持 ~ 与 %VAR% 展开）
"ai_sessions": {
  "paths": {
    "my-tool": ["D:/tools/my-tool/sessions", "%APPDATA%/MyTool"],
    "claude": ["E:/claude-data"]
  }
}
```

只要新工具的会话文件是 JSON/JSONL 且字段命名常见（role/content/model/timestamp…），
通用解析器即可直接工作，无需改代码。

**添加 A 维度的识别关键词**：

```json
// config.json（顶层，与 config.default.json 同名段深合并）
"ai_tool_names": { "mytool": "my tool" },
"ai_keywords": ["mytool"]
```

**自定义模型单价**：`<data_root>/ai_pricing.json`（优先级最高）或
`config.ai_sessions.costs.model_pricing`，格式 `{"model": [输入价, 输出价]}`。

---

## 已知限制

- **WSL**：在 WSL 发行版内运行的 CLI 工具（如 WSL 里的 Claude Code / opencode），
  其会话文件位于 Linux 文件系统（`\\wsl.localhost\<发行版>\home\...`），当前
  **不会被自动扫描**。临时方案：在 `ai_sessions.paths` 中显式配置 UNC 路径，
  例如 `"claude": ["\\\\wsl.localhost\\Ubuntu\\home\\me\\.claude"]`
  （Windows 侧可直接读取该路径，性能略慢于本机盘）。
- **B 维度是 best-effort**：第三方工具格式差异大、随版本变化，可能出现统计缺失；
  Token/成本为估算口径，非官方账单。
- **A 维度为前台注意力口径**：后台挂机的会话不计时。
- 工具更新频繁，若发现某工具路径变更导致统计缺失，欢迎提 issue 或 PR 补充
  `_DEFAULT_PATHS`。
