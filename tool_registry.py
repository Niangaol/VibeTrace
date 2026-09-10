# -*- coding: utf-8 -*-
"""tool_registry.py — 工具注册表（唯一事实源）。

同一个 AI 工具的知识曾经散落在 ai_sessions.py 的 5 个地方（默认目录、解析分发、
缓存豁免、指纹特判、Web 域名表），再加 config 的进程关键词映射——新增/修改一个
工具要改五六处，极易漏改。本模块把「一个工具长什么样」收敛为一条 ToolSpec 记录，
各消费方（ai_sessions / tool_compare / query / dashboard）都从这里取数。

本模块是**纯数据模块**：只依赖标准库 dataclasses，绝不 import ai_sessions 等
业务模块（避免循环依赖）。

## 新增一个工具要改哪里（适配清单）

1. 在下方 TOOLS 里加一个 ToolSpec：
   - `default_paths` 决定 B 维度扫描哪些目录（支持 ~ 与 %VAR%，运行时展开）；
   - 会话文件是常见 JSON/JSONL 时其余字段留默认，通用解析器即可工作；
   - 纯网页 AI 只填 `web_domains`（default_paths 留空）。
2. （可选）格式特殊需要专用解析时：在 ai_sessions.py 写 `_parse_<name>_file`
   并登记进模块级 PARSERS 表，ToolSpec.parser 填对应键名。
3. （可选）定价：往 ai_sessions._DEFAULT_PRICING 加模型键（USD/百万 Token）。
4. （可选）A 维度计时识别：config.default.json 的 ai_keywords / ai_tool_names
   加进程关键词 → 显示名映射（ToolSpec.display_name 填同一个显示名）。

## 命名打通（两套叫法）

- `key`：collect 侧的目录扫描键（如 "pi_agent"）；
- `display_name`：进程计时侧显示名（如 "pi agent"，与 config.ai_tool_names 的值一致）；
- `aliases`：历史/进程侧的其他叫法（如 "pi-agent"、"pi"、"claude code"）。
  `resolve_tool()` 能把任一叫法归一到同一条 ToolSpec，跨数据源的 join 由它收敛。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """一个 AI 工具的完整画像（全部字段纯描述，不含行为）。"""

    key: str                                  # collect 侧 canonical 键，如 "pi_agent"
    display_name: str                         # 进程计时侧显示名，如 "pi agent"
    aliases: tuple[str, ...] = ()             # 历史/进程侧别名，如 ("pi-agent", "pi")
    default_paths: tuple[str, ...] = ()       # 默认会话目录（~/%VAR% 运行时展开）
    sqlite_file: str | None = None            # 工具目录下的 SQLite 库相对路径（如 "opencode.db"）
    sqlite_immutable: bool = True             # True=库只追加不回写（immutable 连接）
    sqlite_exclusive: bool = False            # True=目录里有库就不再通用 walk（zcode）
    parser: str | None = None                 # 专用解析器键："codex"/"dsh"/"pi"/None=通用
    cache_tag: str | None = None              # 解析缓存的 tag（与 PARSERS 键一致）
    cache_size_exempt: bool = False           # True=超 2MB 仍进解析缓存（dsh/codex 大文件）
    # 指纹特判：额外纳入目录指纹的文件（相对路径或后缀）。zcode 专用库目录整树
    # walk 会被 log 噪声打穿 mtime，只指纹这两个文件；opencode.db 在 walk 中按名认领。
    fingerprint_suffixes: tuple[str, ...] = ()
    web_domains: tuple[str, ...] = ()         # 纯 Web AI 域名（浏览器历史识别用）


# ---------------------------------------------------------------------------
# 工具表：21 个本地工具（default_paths 非空，顺序=默认扫描顺序）+ 4 个纯 Web AI。
# 只加不改：新增工具按「适配清单」插入一条即可。
# ---------------------------------------------------------------------------
TOOLS: dict[str, ToolSpec] = {
    spec.key: spec for spec in (
        # —— 本地工具（B 维度会话扫描）——
        ToolSpec(
            key="opencode", display_name="opencode",
            default_paths=("~/.local/share/opencode", "~/.config/opencode"),
            sqlite_file="opencode.db", sqlite_immutable=True, sqlite_exclusive=False,
            # db 只追加（immutable 读更快）；目录仍继续通用 walk（json 双源向后兼容）
            fingerprint_suffixes=("opencode.db",),
        ),
        ToolSpec(
            key="chatgpt", display_name="chatgpt",
            default_paths=("%APPDATA%/ChatGPT", "%LOCALAPPDATA%/ChatGPT", "~/.chatgpt"),
            web_domains=("chatgpt.com", "chat.openai.com"),
        ),
        ToolSpec(
            # collect 侧 "claude" 扫 %APPDATA%/Claude（桌面版）+ ~/.claude（Claude Code CLI）；
            # 进程计时侧 "claude-code" 的显示名是 "claude code"——同一工具的两种入口，
            # 设为别名让 by_ai 分钟数与 collect 统计在 join 时收敛到同一行。
            key="claude", display_name="claude", aliases=("claude code",),
            default_paths=("%APPDATA%/Claude", "%LOCALAPPDATA%/Claude", "~/.claude"),
            web_domains=("claude.ai",),
        ),
        ToolSpec(
            key="cursor", display_name="cursor",
            default_paths=("%APPDATA%/Cursor", "%LOCALAPPDATA%/Cursor", "~/.cursor"),
            web_domains=("chat.cursor.com", "cursor.com"),
        ),
        ToolSpec(
            key="windsurf", display_name="windsurf",
            default_paths=("%APPDATA%/Windsurf", "%LOCALAPPDATA%/Windsurf",
                           "~/.codeium/windsurf", "~/.windsurf"),
        ),
        ToolSpec(
            key="trae", display_name="trae",
            default_paths=("%APPDATA%/Trae", "%LOCALAPPDATA%/Trae", "~/.trae"),
        ),
        ToolSpec(
            key="deepseek", display_name="deepseek",
            default_paths=("%APPDATA%/DeepSeek", "%LOCALAPPDATA%/DeepSeek", "~/.deepseek"),
            web_domains=("chat.deepseek.com",),
        ),
        ToolSpec(
            key="pi_agent", display_name="pi agent", aliases=("pi-agent", "pi"),
            default_paths=("~/.pi/agent/sessions", "~/.pi-agent",
                           "~/.local/share/pi-agent", "%APPDATA%/pi-agent",
                           "%LOCALAPPDATA%/pi-agent", "~/.config/pi-agent"),
            parser="pi", cache_tag="pi",
        ),
        ToolSpec(
            key="dsh", display_name="dsh",
            default_paths=("%DSH_DATA%", "%DSH_HOME%", "~/.dsh", "%LOCALAPPDATA%/dsh"),
            parser="dsh", cache_tag="dsh", cache_size_exempt=True,
            fingerprint_suffixes=(".zstd",),   # 会话为 .jsonl.zstd，mtime 变化须失效缓存
        ),
        ToolSpec(
            key="qwen", display_name="qwen",
            default_paths=("%APPDATA%/Qwen", "%LOCALAPPDATA%/Qwen", "~/.qwen"),
            web_domains=("chat.qwen.ai", "tongyi.aliyun.com"),
        ),
        ToolSpec(
            key="glm", display_name="glm",
            default_paths=("%APPDATA%/zhipu", "%LOCALAPPDATA%/GLM", "~/.glm"),
        ),
        ToolSpec(
            key="doubao", display_name="doubao",
            default_paths=("%APPDATA%/Doubao", "%LOCALAPPDATA%/Doubao", "~/.doubao"),
            web_domains=("doubao.com",),
        ),
        ToolSpec(
            key="kimi", display_name="kimi",
            default_paths=("%APPDATA%/Kimi", "%LOCALAPPDATA%/Kimi", "~/.kimi"),
            web_domains=("kimi.moonshot.cn", "kimi.com"),
        ),
        ToolSpec(
            key="marscode", display_name="marscode",
            default_paths=("%APPDATA%/Marscode", "%LOCALAPPDATA%/Marscode", "~/.marscode"),
        ),
        ToolSpec(
            key="codebuddy", display_name="codebuddy",
            default_paths=("%APPDATA%/CodeBuddy", "%LOCALAPPDATA%/CodeBuddy", "~/.codebuddy"),
        ),
        ToolSpec(
            key="minimax", display_name="minimax",
            default_paths=("%APPDATA%/MiniMax", "%LOCALAPPDATA%/MiniMax", "~/.minimax"),
        ),
        ToolSpec(
            key="stepfun", display_name="stepfun",
            default_paths=("%APPDATA%/StepFun", "%LOCALAPPDATA%/StepFun", "~/.step"),
        ),
        ToolSpec(
            key="yi", display_name="yi",
            default_paths=("%APPDATA%/Yi", "%LOCALAPPDATA%/Yi", "~/.yi"),
        ),
        ToolSpec(
            key="baichuan", display_name="baichuan",
            default_paths=("%APPDATA%/Baichuan", "%LOCALAPPDATA%/Baichuan", "~/.baichuan"),
        ),
        ToolSpec(
            key="zcode", display_name="zcode",
            # cli 目录只认 db/db.sqlite（活跃 WAL，mode=ro）；v2/sessions 走通用 JSON 解析
            default_paths=("~/.zcode/cli", "~/.zcode/v2/sessions"),
            sqlite_file="db/db.sqlite", sqlite_immutable=False, sqlite_exclusive=True,
            fingerprint_suffixes=("db/db.sqlite", "db/db.sqlite-wal"),
        ),
        ToolSpec(
            key="codex", display_name="codex",
            default_paths=("~/.codex/sessions",),
            parser="codex", cache_tag="codex", cache_size_exempt=True,
        ),
        # —— 纯 Web AI 工具（无本地会话目录，仅浏览器历史识别）——
        ToolSpec(key="gemini", display_name="gemini",
                 web_domains=("gemini.google.com",)),
        ToolSpec(key="perplexity", display_name="perplexity",
                 web_domains=("perplexity.ai",)),
        ToolSpec(key="copilot", display_name="copilot",
                 web_domains=("copilot.microsoft.com",)),
        ToolSpec(key="metaso", display_name="metaso",
           
                 web_domains=("metaso.cn",)),
    )
}


# ---------------------------------------------------------------------------
# 命名归一：把两套工具命名（collect 键 / 进程显示名 / 历史别名）收敛到 ToolSpec
# ---------------------------------------------------------------------------
def canonical_tool_key(name: str) -> str:
    """规范化工具名：小写并去掉空格/横杠/下划线/点。

    "pi agent" / "pi-agent" / "pi_agent" / "Pi.Agent" → "piagent"。
    """
    s = str(name or "").strip().lower()
    for ch in " -_.":
        s = s.replace(ch, "")
    return s


# 别名精确索引与 canonical 模糊索引（导入时构建一次，查询 O(1)）
_ALIAS_INDEX: dict[str, ToolSpec] = {}
_CANON_INDEX: dict[str, ToolSpec] = {}
for _spec in TOOLS.values():
    for _a in _spec.aliases:
        _ALIAS_INDEX.setdefault(_a, _spec)
    _CANON_INDEX.setdefault(canonical_tool_key(_spec.key), _spec)
    for _a in _spec.aliases:
        _CANON_INDEX.setdefault(canonical_tool_key(_a), _spec)


def resolve_tool(name: str) -> ToolSpec | None:
    """把任一叫法解析为 ToolSpec：先精确匹配 key/别名，再 canonical 模糊匹配。

    解析不了（用户自定义工具名等）返回 None，调用方保持原名即可。
    """
    n = str(name or "").strip().lower()
    if not n:
        return None
    spec = TOOLS.get(n) or _ALIAS_INDEX.get(n)
    if spec is not None:
        return spec
    return _CANON_INDEX.get(canonical_tool_key(n))


# ---------------------------------------------------------------------------
# 模型名 → 厂商（前端价格分组 PRICE_VENDORS 的后端镜像）。
# 注意：不含工具名 "dsh"（前端曾把它误当模型前缀归入"国内其他"，本次修正不沿用）。
# ---------------------------------------------------------------------------
MODEL_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "Anthropic"),
    ("gpt", "OpenAI"), ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI"),
    ("codex", "OpenAI"), ("chatgpt", "OpenAI"),
    ("gemini", "Google"), ("gemma", "Google"),
    ("deepseek", "DeepSeek"),
    ("qwen", "Qwen"),
    ("glm", "Zhipu"), ("chatglm", "Zhipu"), ("zhipu", "Zhipu"),
    ("kimi", "Moonshot"), ("moonshot", "Moonshot"),
    ("minimax", "MiniMax"),
    ("mistral", "Mistral"), ("codestral", "Mistral"), ("pixtral", "Mistral"),
    ("ministral", "Mistral"),
    ("llama", "Meta"),
    ("grok", "xAI"),
    ("ernie", "Baidu"), ("spark", "Baidu"),
    ("step", "StepFun"),
    ("yi", "01.AI"),
    ("baichuan", "Baichuan"),
    ("doubao", "ByteDance"),
    ("command", "Cohere"),
    ("hunyuan", "Tencent"),
    ("nova", "Amazon"),
    ("phi", "Microsoft"), ("wizardlm", "Microsoft"),
)

# 最长前缀优先（"codestral" 须先于普通词命中；与声明顺序解耦）
_VENDOR_PREFIXES_BY_LENGTH: tuple[tuple[str, str], ...] = tuple(
    sorted(MODEL_VENDOR_PREFIXES, key=lambda kv: -len(kv[0])))


def model_vendor(model: str) -> str | None:
    """模型名 → 厂商（最长前缀匹配，大小写不敏感）；识别不了返回 None。"""
    s = str(model or "").strip().lower()
    if not s:
        return None
    for prefix, vendor in _VENDOR_PREFIXES_BY_LENGTH:
        if s.startswith(prefix):
            return vendor
    return None
