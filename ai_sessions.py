# -*- coding: utf-8 -*-
"""ai_sessions.py — AI 会话深度统计（默认开启，§6.4.3）。

读取 opencode / ChatGPT / Claude / Cursor / Windsurf / Trae / DeepSeek /
Pi Agent / DSH 等工具的本地会话文件（JSON / JSONL），统计某天
“AI 交互轮数、对话轮次、生成行数/字符数、Token 用量估算、按模型/项目拆分”
等指标；并可从浏览器访问明细（browser_history 输出）深度解析 Web AI 会话
（ChatGPT/Claude/Gemini 等聊天页面的会话轮次推断）。默认开启；可在 config.json
显式设 `ai_sessions.enabled=false` 关闭（仪表盘概览始终展示该维度）。
路径可用 `ai_sessions.paths` 自定义，未配置时自动探测常见目录。

实现要点（docs/ROADMAP.md Phase 1）：
- **对话轮次追踪**：本地会话文件内按 user→assistant 配对数计 `rounds`；
  浏览器历史里同一聊天会话页面的多次访问视为页面刷新轮次（best-effort）。
- **Token 用量估算**：`token_estimation`（默认开）。CJK 字符按 1 Token/字，
  其余按 4 字符/Token 折算输入/输出 Token。
- **按模型拆分**：从消息 `model` 字段或内容中的模型名正则提取。
- **按项目拆分**：从消息 `cwd/project/repo/...` 字段或会话文件目录推断。

设计原则：
- 纯标准库、零第三方依赖；
- 只读取用户配置/常见 AI 工具本地会话目录，**不会上传任何数据**；
- 解析失败/格式未知时静默跳过，不影响监控主流程；
- JSONL 仍是原始事实源，本模块只是附加统计。

CLI：
  python ai_sessions.py --day 2026-08-10 [--web] [--json] [--data-root ...] [--config ...]
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
import urllib.parse
import contextlib
import threading

from collections import OrderedDict

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_FILE_SIZE = 20 * 1024 * 1024  # 单文件最大 20MB，避免误扫大文件卡顿

# 常见本地会话目录（ai_sessions.paths 未配置时使用）
_DEFAULT_PATHS: dict[str, list[str]] = {
    "opencode": [
        "~/.local/share/opencode",
        "~/.config/opencode",
    ],
    "chatgpt": [
        "%APPDATA%/ChatGPT",
        "%LOCALAPPDATA%/ChatGPT",
        "~/.chatgpt",
    ],
    "claude": [
        "%APPDATA%/Claude",
        "%LOCALAPPDATA%/Claude",
        "~/.claude",
    ],
    "cursor": [
        "%APPDATA%/Cursor",
        "%LOCALAPPDATA%/Cursor",
        "~/.cursor",
    ],
    "windsurf": [
        "%APPDATA%/Windsurf",
        "%LOCALAPPDATA%/Windsurf",
        "~/.codeium/windsurf",
        "~/.windsurf",
    ],
    "trae": [
        "%APPDATA%/Trae",
        "%LOCALAPPDATA%/Trae",
        "~/.trae",
    ],
    "deepseek": [
        "%APPDATA%/DeepSeek",
        "%LOCALAPPDATA%/DeepSeek",
        "~/.deepseek",
    ],
    "pi_agent": [
        "~/.pi/agent/sessions",
        "~/.pi-agent",
        "~/.local/share/pi-agent",
        "%APPDATA%/pi-agent",
        "%LOCALAPPDATA%/pi-agent",
        "~/.config/pi-agent",
    ],
    "dsh": [
        "%DSH_DATA%",
        "%DSH_HOME%",
        "~/.dsh",
        "%LOCALAPPDATA%/dsh",
    ],
    "qwen": [
        "%APPDATA%/Qwen",
        "%LOCALAPPDATA%/Qwen",
        "~/.qwen",
    ],
    "glm": [
        "%APPDATA%/zhipu",
        "%LOCALAPPDATA%/GLM",
        "~/.glm",
    ],
    "doubao": [
        "%APPDATA%/Doubao",
        "%LOCALAPPDATA%/Doubao",
        "~/.doubao",
    ],
    "kimi": [
        "%APPDATA%/Kimi",
        "%LOCALAPPDATA%/Kimi",
        "~/.kimi",
    ],
    "marscode": [
        "%APPDATA%/Marscode",
        "%LOCALAPPDATA%/Marscode",
        "~/.marscode",
    ],
    "codebuddy": [
        "%APPDATA%/CodeBuddy",
        "%LOCALAPPDATA%/CodeBuddy",
        "~/.codebuddy",
    ],
    "minimax": [
        "%APPDATA%/MiniMax",
        "%LOCALAPPDATA%/MiniMax",
        "~/.minimax",
    ],
    "stepfun": [
        "%APPDATA%/StepFun",
        "%LOCALAPPDATA%/StepFun",
        "~/.step",
    ],
    "yi": [
        "%APPDATA%/Yi",
        "%LOCALAPPDATA%/Yi",
        "~/.yi",
    ],
    "baichuan": [
        "%APPDATA%/Baichuan",
        "%LOCALAPPDATA%/Baichuan",
        "~/.baichuan",
    ],
}


# 时间字段候选
_TIME_KEYS = ("timestamp", "created_at", "time", "date", "ts", "created")
# 角色字段候选
_ROLE_KEYS = ("role", "type", "author")
# 内容字段候选
_CONTENT_KEYS = ("content", "text", "message", "value")

# 用户 / 助手 角色白话集合
_USER_ROLES = ("user", "human", "prompt", "client", "user_msg")
_ASSISTANT_ROLES = ("assistant", "ai", "bot", "model", "agent", "assistant_msg", "response")

# 模型字段候选（含嵌套 response/model；不含通用 name，避免误把用户名/函数名当模型）
_MODEL_KEYS = ("model", "model_name", "model_id", "modelId", "model_id_str")
# 已知模型名正则（内容里的模型名识别按贪婪先后顺序；大小写不敏感）
# 防误伤规则：通用词加长度/边界限制，确保匹配到真实模型名而非普通文本。
_KNOWN_MODEL_RE = re.compile(
    r"(?:gpt[-_ ]?5(?:\.\d+)?[\w.-]*"      # gpt-5.x 全家族（允许裸 gpt-5）
    r"|gpt[-_ ]?(?:4o|4\.5|4\.1|4|3\.5|3\.1|3)[\w.-]*"
    r"|o[134](?:-mini|-pro)?[\w.-]*"
    r"|claude[- ](?:5(?:\.[0-9]+)?|4(?:\.[0-9]+)?|3(?:\.[0-9]+)?)(?:-[\w.-]+)?"
    r"|claude-(?:opus|sonnet|haiku|mythos)[- 0-9.\w-]*"
    r"|gemini[- ](?:3\.[6-9]|3\.[0-5]|3|2\.[5-9]|2\.[0-4]|1\.5|1)[\w.-]*"
    r"|gemma(?:[- ][0-9][\w.-]*)?"
    r"|deepseek(?:[-_ ]?(?:v[34]|chat|reasoner|r1|coder|math))?[\w.-]*"
    r"|qwen(?:3|2\.5|-max|-plus|-turbo|-long)?[\w.-]*"
    r"|glm[-_ ]?(?:5\.[0-9]|4\.[0-9]|4)?[\w.-]*"
    r"|kimi[- ]?(?:k3|v3|moonshot)?[\w.-]*"
    r"|moonshot[- ]?(?:v3|ai)?[\w.-]*"
    r"|minimax[- ]?(?:m[23](?:\.\d+)?|ai|text)?[\w.-]*"
    r"|mistral(?:[- ]?(?:3|large[- ]?3|medium[- ]?3\.5|2\.5|small|nemo|codestral))?[\w.-]*"
    r"|codestral[\w.-]*"
    r"|llama[- ]?[0-9]?[\w.-]*"
    r"|grok[- ]?(?:4\.1|4[- ]heavy|4|3|2|1)?[\w.-]*"
    r"|ernie[- ]?(?:4\.5|5|4|3|speed|turbo|lite)?[\w.-]*"
    r"|step[- ]?(?:[12]|fun|turbo|mini|flash)?[\w.-]*"
    r"|yi[- ]?(?:large|medium|spark|light|vision|coder)?[\w.-]*"
    r"|baichuan[- ]?(?:[234]|turbo|wide|agent|m1)?[\w.-]*"
    r"|doubao[\w.-]*"
    r"|spark[\w.-]*"
    r"|command(?:-r|-a|-plus)?[\w.-]*"
    r"|codex(?:[- ][\w.-]+)?"
    r"|hunyuan[\w.-]*"
    r")",
    re.IGNORECASE,
)

# 项目字段候选（cwd 等取最后一段作为项目名，避免路径噪声）
_PROJECT_KEYS = ("project", "cwd", "repo", "repository", "directory", "folder",
                 "workspace", "project_name", "git_repo", "worktree")
# 会话标识字段候选（同一文件内多会话时按此分组；缺失则整个文件视为一个会话）
_CONV_KEYS = ("conversation_id", "session_id", "thread_id", "chat_id", "conversationId",
              "sessionId", "threadId", "conversation", "session", "thread")

# CJK 字符（中文/日文/韩文）按 1 Token/字 折算
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")

# 内置主流模型定价表（USD / 百万 Token：(输入价, 输出价)）。
# 仅作“量级参考”；实际费用以各厂商实时报价为准，可用
# config 的 ai_sessions.costs.model_pricing 覆盖/补充（键为模型名子串，小写）。
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # —— Anthropic（USD/百万 Token：输入, 输出）——
    "claude-opus-5": (5.0, 25.0),
    "claude-opus": (5.0, 25.0),          # Opus 4.x
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),      # 4.5/4.6
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku": (0.25, 1.25),
    "claude-mythos": (6.0, 30.0),        # Mythos 5 系列
    # —— OpenAI ——
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5": (0.5, 1.5),
    "o4-mini": (1.1, 4.4),
    "o3-mini": (1.1, 4.4),
    "o3-pro": (20.0, 80.0),
    "o3": (2.0, 8.0),
    "o1": (15.0, 60.0),
    "codex": (1.75, 14.0),               # gpt-5.x-codex
    # —— DeepSeek（2026 V4）——
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-r1": (0.55, 2.19),
    # —— Google Gemini ——
    "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.1-pro": (2.0, 12.0),
    "gemini-3-flash-lite": (0.30, 2.50),
    "gemini-3-flash": (0.50, 3.0),
    "gemini-3-pro": (2.0, 12.0),        # 3.x 标准版
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2-flash": (0.30, 2.50),
    "gemini-2-pro": (1.0, 8.0),
    "gemma-3": (0.20, 0.60),            # Gemma 3 系列
    "gemma-2": (0.20, 0.60),
    "gemma": (0.20, 0.60),              # 通用
    # —— 国内模型（约合 USD）——
    "qwen3-max": (0.50, 5.0),
    "qwen3": (0.30, 0.60),              # Qwen3 通义千问
    "qwen2.5": (0.20, 0.40),            # Qwen 2.5 系列
    "qwen-max": (1.6, 6.4),
    "qwen-plus": (0.8, 2.0),
    "qwen-turbo": (0.3, 0.6),
    "qwen-long": (0.20, 0.60),
    "glm-5.2": (0.85, 3.4),
    "glm-5": (0.85, 3.4),
    "glm-4.7": (0.50, 1.40),
    "glm-4.6": (0.50, 1.40),
    "glm-4": (0.50, 1.40),
    "kimi-k3": (1.0, 3.0),             # Kimi K3
    "kimi": (1.0, 3.0),
    "moonshot-v3": (1.0, 3.0),          # Moonshot V3
    "moonshot": (1.0, 3.0),
    "doubao": (0.30, 0.60),
    "ernie-5": (0.57, 2.57),
    "ernie-4.5": (0.57, 2.57),
    "ernie": (0.57, 2.57),
    "hunyuan": (0.20, 0.90),
    # —— 其他 ——
    "grok-4.1": (1.25, 2.5),
    "grok-4-heavy": (1.25, 2.5),
    "grok-4": (1.25, 2.5),
    "grok-3": (3.0, 15.0),
    "grok-2": (2.0, 10.0),
    "mistral-large-3": (2.0, 6.0),
    "mistral-large": (2.0, 6.0),
    "mistral-medium-3.5": (1.0, 3.0),
    "mistral-3": (1.0, 3.0),           # Mistral 3 系列
    "mistral-small": (0.20, 0.60),
    "codestral": (0.30, 0.90),
    "llama-4": (0.20, 0.40),
    "llama-3": (0.50, 0.75),
    "minimax-m3": (0.20, 0.60),
    "minimax-m2.7": (0.15, 0.50),
    "minimax": (0.20, 0.60),
    "command-r": (0.15, 0.60),
    "command-a": (0.20, 0.80),
    "step-1": (0.50, 2.0),             # 阶跃星辰 Step
    "yi-large": (1.0, 3.0),            # 零一万物 Yi
    "yi": (1.0, 3.0),
    "baichuan-4": (0.50, 1.50),        # 百川 Baichuan
    "baichuan": (0.50, 1.50),
    # 定价随时变动，以上为“量级参考”。请用 config 的
    # ai_sessions.costs.model_pricing 或数据目录 ai_pricing.json 覆盖。
}
_WEB_AI_TOOLS: dict[str, tuple[str, ...]] = {
    "chatgpt": ("chatgpt.com", "chat.openai.com"),
    "claude": ("claude.ai",),
    "gemini": ("gemini.google.com",),
    "perplexity": ("perplexity.ai",),
    "deepseek": ("chat.deepseek.com",),
    "kimi": ("kimi.moonshot.cn", "kimi.com"),
    "copilot": ("copilot.microsoft.com",),
    "cursor": ("chat.cursor.com", "cursor.com"),
    "qwen": ("chat.qwen.ai", "tongyi.aliyun.com"),
    "metaso": ("metaso.cn",),
    "doubao": ("doubao.com",),
}
_WEB_CONV_PATTERNS = (
    re.compile(r"/c/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/chat/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/app/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/conversations?/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/share/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/session/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/thread/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/inbox/([A-Za-z0-9_~-]{8,64})"),
)
_WEB_CONV_QUERY_RE = re.compile(r"[?&]c=([A-Za-z0-9_~-]{8,64})")

def _expand(path: str) -> str:
    """展开 ~ 与 %VAR% 环境变量。"""
    path = os.path.expanduser(str(path or "").strip())
    path = os.path.expandvars(path)
    return path


def _default_tool_paths() -> dict[str, list[str]]:
    out = {}
    for tool, dirs in _DEFAULT_PATHS.items():
        expanded = [p for p in (_expand(d) for d in dirs) if p]
        if expanded:
            out[tool] = expanded
    return out


def _config_paths(config: dict) -> dict[str, list[str]]:
    """从 config 读取 ai_sessions.paths；未配置时返回默认探测路径。"""
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    raw_paths = section.get("paths")
    if isinstance(raw_paths, dict) and raw_paths:
        out = {}
        for tool, dirs in raw_paths.items():
            if isinstance(dirs, list):
                expanded = [p for p in (_expand(d) for d in dirs) if p]
                if expanded:
                    out[str(tool)] = expanded
        if out:
            return out
    return _default_tool_paths()


# 目录枚举截断上限（B3）：与解析记忆化上限 _PARSE_CACHE_MAX_ENTRIES 同值（4096）。
# 旧值 500 是 v2.7 的保守估计：重度用户单日会话文件实测 900+，每天统计的都是随
# os.walk 返回序漂移的「随机子集」；提到与解析缓存一致的容量后，「能枚举的」与
# 「能缓存的」对齐，正常开发机不再触发截断。仍保留上限防失控目录拖垮热路径。
_WALK_MAX_FILES = 4096
# 截断信号（轻量方案）：发生截断时 +1 的模块级诊断计数器（观测/测试断言点，
# 不抛异常、不 print；多线程下允许极小概率漏计——仅用于发现「统计为截断子集」，
# 非精确审计）。
_WALK_TRUNCATED_COUNT = 0


def _walk_files(dirs: list[str], max_files: int | None = None) -> list[str]:
    """递归收集目录下 JSON/JSONL 文件（限制数量与单文件大小）。

    确定性（B3）：os.walk 返回序由文件系统决定且不保证有序，同一棵树两次枚举
    可能得到不同序列；在数量截断下这会让统计口径变成逐日漂移的随机子集，也破坏
    指纹缓存「同树同结果」的前提。因此每层先排序再遍历：顶层目录列表排序 +
    sorted(dirs)/sorted(files)，同一棵树任意两次枚举结果字节级一致。

    公平性：调用方按工具各传各的目录列表（_collect_local → _iter_tool_messages
    每工具一次），单个巨目录只占本工具的配额，不会饿死其他工具。

    截断可见性：达到 max_files 提前返回时结果为截断子集（统计数字偏小），通过
    模块级计数器 _WALK_TRUNCATED_COUNT 记一次信号供观测，保持热路径零 I/O 开销。
    """
    global _WALK_TRUNCATED_COUNT
    if max_files is None:
        max_files = _WALK_MAX_FILES  # 调用时取值：改常量即全局生效（便于测试）
    out: list[str] = []
    seen: set[str] = set()
    for base in sorted(dirs):
        if not os.path.isdir(base):
            continue
        for root, sub_dirs, files in os.walk(base):
            sub_dirs[:] = sorted(sub_dirs)  # 每层目录排序：固定递归顺序（topdown 原地生效）
            for name in sorted(files):      # 每层文件排序：消除文件系统返回序差异
                if len(out) >= max_files:
                    # 截断信号：本次统计为截断子集（数字偏小），记一次供观测
                    _WALK_TRUNCATED_COUNT += 1
                    return out
                if not name.lower().endswith((".json", ".jsonl", ".ndjson")):
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getsize(path) > _MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                real = os.path.normcase(os.path.abspath(path))
                if real not in seen:
                    seen.add(real)
                    out.append(path)
    return out


def _extract_timestamp(obj: dict) -> str | None:
    """从对象中提取可解析的本地时间字符串/时间戳。"""
    for key in _TIME_KEYS:
        val = obj.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            # 秒级/毫秒级时间戳
            ts = float(val)
            if ts > 10_000_000_000:
                ts /= 1000.0
            try:
                return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
            except (OSError, ValueError, OverflowError):
                continue
        text = str(val)
        if not text:
            continue
        # 去掉常见后缀 Z / 时区偏移，取前 19 位
        text = text.strip().replace("Z", "+00:00")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(text[:19], fmt).isoformat(timespec="seconds")
            except ValueError:
                continue
    return None


_CONTAINER_KEYS = ("messages", "conversation", "history", "thread", "items", "turns",
                   "chat_messages", "entries", "conversations")
_DICT_CONTAINER_KEYS = ("conversations", "sessions", "threads")


def _extract_messages(obj, _depth: int = 0) -> list[dict]:
    """从 JSON/JSONL 对象中递归尽力提取消息列表（兼容多工具嵌套结构）。"""
    if _depth > 6:
        return []
    if isinstance(obj, dict):
        # 单条消息对象
        if any(k in obj for k in _ROLE_KEYS) and any(k in obj for k in _CONTENT_KEYS):
            return [obj]
        # 常见嵌套 message / data
        for key in ("message", "data"):
            inner = obj.get(key)
            if isinstance(inner, dict):
                sub = _extract_messages(inner, _depth + 1)
                if sub:
                    return sub
        # 列表容器
        for key in _CONTAINER_KEYS:
            val = obj.get(key)
            if isinstance(val, list):
                out: list[dict] = []
                for item in val:
                    if isinstance(item, dict):
                        out.extend(_extract_messages(item, _depth + 1))
                if out:
                    return out
        # 字典容器（conversations/sessions/threads）
        for key in _DICT_CONTAINER_KEYS:
            val = obj.get(key)
            if isinstance(val, dict):
                out = []
                for item in val.values():
                    if isinstance(item, dict):
                        out.extend(_extract_messages(item, _depth + 1))
                if out:
                    return out
    elif isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict):
                out.extend(_extract_messages(item, _depth + 1))
        return out
    return []


def _message_role(msg: dict) -> str | None:
    for key in _ROLE_KEYS:
        val = msg.get(key)
        if isinstance(val, str):
            return val.lower()
        if isinstance(val, dict):
            inner = val.get("role")
            if isinstance(inner, str):
                return inner.lower()
    return None


def _message_content(msg: dict) -> str:
    for key in _CONTENT_KEYS:
        val = msg.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts = []
            for part in val:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        parts.append(part["text"])
                    elif isinstance(part.get("content"), str):
                        parts.append(part["content"])
                elif isinstance(part, str):
                    parts.append(part)
            if parts:
                return "\n".join(parts)
        if isinstance(val, dict):
            text = val.get("text") or val.get("content")
            if isinstance(text, str):
                return text
    return ""


def _message_time(msg: dict) -> str | None:
    # 嵌套 message 对象优先
    for key in ("message", "data"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            ts = _extract_timestamp(inner)
            if ts:
                return ts
    return _extract_timestamp(msg)



def estimate_tokens(text: str) -> int:
    """Token 量粗略估算（零依赖启发式，simple 口径）。

    CJK 字符按 1 Token/字，其余字符按 4 字符/Token（进一法）；空文本返回 0。
    仅用于“量级”参考，非精确计费。此函数为历史口径，测试钉值；
    新代码默认走 estimate_tokens_weighted（见 token_estimation_mode 配置）。
    """
    text = text or ""
    if not text.strip():
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


# 加权估算：按字符类别近似 BPE 分词器行为（cl100k/o200k 量级校准）。
# 代码/JSON 文本里符号密度高，simple 口径会明显低估——加权版对此修正。
_TOKEN_WEIGHTS = {
    "cjk": 1.0,        # 中日韩字：约 1 Token/字
    "latin": 0.25,     # 字母：4 字符 ≈ 1 Token（英文单词级）
    "digit": 0.35,     # 数字：约 3 字符/Token
    "space": 0.125,    # 空白：8 字符 ≈ 1 Token（连续空格常被合并）
    "punct": 0.5,      # 标点/符号：2 字符 ≈ 1 Token（代码主场景）
}
_RE_DIGIT = re.compile(r"[0-9]")
_RE_SPACE = re.compile(r"\s")
_RE_LATIN = re.compile(r"[A-Za-z]")


def estimate_tokens_weighted(text: str) -> int:
    """Token 估算（weighted 口径）：按字符类别加权求和后进一。

    比 simple 口径更贴近真实分词器：对符号密集的代码/JSON 上修、
    对空白密集文本下修。仍为零依赖启发式，非精确计费。
    """
    text = text or ""
    if not text.strip():
        return 0
    total = 0.0
    cjk = len(_CJK_RE.findall(text))
    if cjk:
        total += cjk * _TOKEN_WEIGHTS["cjk"]
    rest = _CJK_RE.sub("", text)
    for ch in rest:
        if _RE_SPACE.match(ch):
            total += _TOKEN_WEIGHTS["space"]
        elif _RE_DIGIT.match(ch):
            total += _TOKEN_WEIGHTS["digit"]
        elif _RE_LATIN.match(ch):
            total += _TOKEN_WEIGHTS["latin"]
        else:
            total += _TOKEN_WEIGHTS["punct"]
    return int(total + 0.999)  # 进一


def _message_usage(msg: dict) -> tuple[int, int] | None:
    """提取消息里的**真实** token 用量（许多 harness 会记录 API 返回的 usage）。

    候选位置：
    - msg["usage"] = {input_tokens/output_tokens | prompt_tokens/completion_tokens}
    - msg 顶层平铺：input_tokens / output_tokens / prompt_tokens /
      completion_tokens / tokens_in / tokens_out
    任一输入或输出侧有效（非负 int）即返回 (in, out)；缺失侧记 0；
    完全不存在返回 None（调用方回退内容估算）。
    """
    def _num(v) -> int | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return int(v) if v >= 0 else None

    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
    in_vals = [usage.get(k) for k in ("input_tokens", "prompt_tokens")] + \
              [msg.get(k) for k in ("input_tokens", "prompt_tokens", "tokens_in")]
    out_vals = [usage.get(k) for k in ("output_tokens", "completion_tokens")] + \
               [msg.get(k) for k in ("output_tokens", "completion_tokens", "tokens_out")]
    n_in = next((_num(v) for v in in_vals if _num(v) is not None), None)
    n_out = next((_num(v) for v in out_vals if _num(v) is not None), None)
    if n_in is None and n_out is None:
        return None
    return (n_in or 0, n_out or 0)


def _cost_section(config: dict) -> dict:
    """读取 ai_sessions.costs 配置段（空则返回空 dict）。"""
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    costs = section.get("costs") if isinstance(section.get("costs"), dict) else {}
    return costs


def _merge_pricing(table: dict[str, tuple[float, float]], raw: object) -> None:
    """把一段定价覆盖（{model: {"input":..,"output":..} 或 [in,out]}）并入表。"""
    if not isinstance(raw, dict):
        return
    for key, val in raw.items():
        if isinstance(val, dict):
            try:
                i = float(val.get("input", 0) or 0)
                o = float(val.get("output", 0) or 0)
            except (TypeError, ValueError):
                continue
        elif isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                i, o = float(val[0]), float(val[1])
            except (TypeError, ValueError):
                continue
        else:
            continue
        table[str(key).lower()] = (i, o)


def _pricing_file(config: dict) -> str | None:
    """用户自定义定价文件路径：<data_root>/ai_pricing.json（存在才返回）。"""
    root = config.get("data_root") or ""
    if not root:
        return None
    candidate = os.path.join(str(root), "ai_pricing.json")
    return candidate if os.path.isfile(candidate) else None


def _pricing_table(config: dict) -> dict[str, tuple[float, float]]:
    """合并内置定价表 + config 的 model_pricing + 数据目录 ai_pricing.json。

    优先级（后者覆盖前者）：内置默认 < config.ai_sessions.costs.model_pricing
    < <data_root>/ai_pricing.json。键为小写模型名子串。
    """
    table: dict[str, tuple[float, float]] = dict(_DEFAULT_PRICING)
    overrides = _cost_section(config).get("model_pricing")
    if isinstance(overrides, dict):
        _merge_pricing(table, overrides)
    fpath = _pricing_file(config)
    if fpath:
        try:
            with open(fpath, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            _merge_pricing(table, raw)
        except Exception:  # noqa: BLE001 —— 定价文件损坏时忽略，不影响主流程
            pass
    return table


def _model_price(table: dict[str, tuple[float, float]], model: str) -> tuple[float, float]:
    """按模型名匹配（进/出 USD 每百万 Token）。未匹配返回 (0.0, 0.0)。"""
    m = (model or "").lower()
    if not m:
        return (0.0, 0.0)
    if m in table:
        return table[m]
    for key in sorted(table, key=len, reverse=True):
        if key in m:
            return table[key]
    return (0.0, 0.0)


def _fmt_cost(usd: float) -> str:
    """费用格式化：美元，小于 1 分显示 4 位小数，其余按需。"""
    usd = float(usd or 0)
    if usd == 0:
        return "$0"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def _message_model(msg: dict) -> str:
    """尽力提取模型名。优先级：直接字段 > 嵌套 response/model/delta > 内容正则。"""
    for key in _MODEL_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return _clean_model(val.strip())
        if isinstance(val, dict):  # 可能 {model: "..."} / {id: "..."}
            for inner_key in ("model", "name", "id"):
                inner = val.get(inner_key)
                if isinstance(inner, str) and inner.strip():
                    return _clean_model(inner.strip())
    for key in ("message", "data", "response", "result", "delta"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            for ik in _MODEL_KEYS:
                iv = inner.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return _clean_model(iv.strip())
    content = _message_content(msg)
    m = _KNOWN_MODEL_RE.search(content)
    if m:
        return _clean_model(m.group(0))
    return "未识别"


def _clean_model(raw: str) -> str:
    """归一化模型名（小写、去引号/换行、去日期/包装后缀、截断过长值）。

    保留模型语义后缀（-pro/-flash/-lite/-plus/-long/-chat/-coder/-math/-reasoner/-r1/-v3/-v4 等），
    只去掉包装/日期类无意义后缀，避免把模型身份信息也抹掉。
    """
    name = re.sub(r"[\"'`\r\n]+", " ", raw).strip().lower()
    # 日期后缀
    name = re.sub(r"[-_]\d{4}[-_]\d{2}[-_]\d{2}$", "", name)
    name = re.sub(r"[-_]\d{6,8}$", "", name)
    # 包装后缀（只去这些，其余保留）
    for suffix in ("-preview", "-latest", "-custom"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name[:48] or "未识别"


def _message_project(msg: dict) -> str | None:
    """从消息字段提取项目名（取路径最后一段）；没有返回 None。"""
    for key in _PROJECT_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            p = os.path.basename(val.rstrip("/\\"))
            if p and p not in ("", ".", ".."):
                return p[:64]
    for key in ("message", "data"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            sub = _message_project(inner)
            if sub:
                return sub
    return None


def _message_conv_id(msg: dict, file_path: str) -> str:
    """会话标识：优先消息里的会话/线程字段；缺失则退回「文件名」整体为一会话。"""
    for key in _CONV_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:48]
        if isinstance(val, dict):
            for ik in _CONV_KEYS:
                iv = val.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return iv.strip()[:48]
    basename = os.path.basename(file_path)
    return f"file:{basename[:40]}" if basename else "file:<unknown>"


def _count_rounds(msgs: list[dict]) -> int:
    """对话轮次：一次 user 提问后（在下一个 user 提问前）收到至少一条 assistant
    回复，记为一轮（Q/A 配对完成）。消息按文件顺序判定，尽力而为。
    """
    rounds = 0
    expecting = False
    for msg in msgs:
        role = _message_role(msg)
        if role in _USER_ROLES:
            expecting = True
        elif role in _ASSISTANT_ROLES and expecting:
            rounds += 1
            expecting = False
    return rounds


def _ts_seconds(ts: str) -> float | None:
    """把时间戳字符串（YYYY-MM-DD[THH:MM:SS...]）转成 epoch 秒；解析失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


_QUALITY_NOTICE = "仅基于本地会话消息长度/轮次/配比启发式估算，非真实采纳率，仅供参考"


# 分档阈值（阈值本身归入高档）：≥80 优 / ≥65 良 / ≥45 中 / 其余待优化
_GRADE_BOUNDS = (80, 65, 45)
_GRADE_NAMES = ("优", "良", "中", "待优化")


def quality_grade(score: int) -> str:
    """0-100 分 → 分档：≥80 优 / ≥65 良 / ≥45 中 / 其余待优化。

    纯函数、确定性，供会话/报告/仪表盘复用。
    """
    score = int(score or 0)
    if score >= _GRADE_BOUNDS[0]:
        return _GRADE_NAMES[0]
    if score >= _GRADE_BOUNDS[1]:
        return _GRADE_NAMES[1]
    if score >= _GRADE_BOUNDS[2]:
        return _GRADE_NAMES[2]
    return _GRADE_NAMES[3]


def _clamp01(v: float) -> float:
    """夹到 [0, 1]。"""
    if v <= 0:
        return 0.0
    if v >= 1:
        return 1.0
    return v


def _conversation_quality(*, user_n: int, assistant_n: int, rounds: int,
                          tokens_in: int, tokens_out: int, tokens_total: int,
                          model_count: int, span_s: float,
                          user_tokens: list[int], generated_chars: int) -> dict:
    """会话质量派生评分（纯函数、零依赖启发式、best-effort、不伪装精确）。

    四个 0-1 子因子（仅用于解释"为什么是这个分"，非精确测量）：
    - question_value  提问含金量：用户提问平均长度适中为佳（15-200 token），
      过短（单字指令）或过长（整段粘贴日志/需求）都降分；无用户消息取中性 0.5。
    - rework          返工程度（**负向**，越高越差）：用户侧 token 占比高（反复
      粘贴/重发）、或用户消息多于完成轮次（连发未获答复）视为返工。
    - stability       稳定性：模型单一、时间密度高（少碎片化挂机）为佳。
    - context_health  上下文健康度：轮次完成度（user→assistant 配对）+ 输出实质
      （每条助手回复的 token/字符密度）+ 上下文长度适中（过长稀释记忆、抬高成本）。

    总分 = 0.35*qv + 0.25*(1-rework) + 0.2*stability + 0.2*context_health，
    再 ×100 取整并夹到 [0, 100]。
    """
    # 提问含金量：平均用户消息长度（token 估算，与 token_estimation 开关无关）
    if user_tokens:
        avg_ut = sum(user_tokens) / len(user_tokens)
        if avg_ut < 15:
            question_value = 0.3 + 0.7 * (avg_ut / 15.0)
        elif avg_ut <= 200:
            question_value = 1.0
        else:
            question_value = 1.0 - 0.5 * min(1.0, (avg_ut - 200) / 200.0)
    else:
        question_value = 0.5
    question_value = _clamp01(question_value)

    # 返工：用户 token 占比 + 连发未获答复占比
    user_ratio = tokens_in / max(tokens_total, 1)
    rework = (user_ratio - 0.3) / 0.5 if user_ratio > 0.3 else 0.0
    if user_n > 0:
        rework = max(rework, (user_n - rounds) / user_n)
    rework = _clamp01(rework)

    # 稳定性：模型统一度 + 时间密度
    model_stability = 1.0 / math.sqrt(max(model_count, 1))
    span_h = max(float(span_s) / 3600.0, 0.25)
    density = (user_n + assistant_n) / span_h / 24.0  # ≥24 条消息/小时视为密集稳定
    stability = 0.6 * model_stability + 0.4 * _clamp01(density)

    # 上下文健康度：轮次完成度 + 输出实质 + 长度健康
    round_completion = rounds / max(user_n, 1) if user_n > 0 else 0.5
    avg_out_tokens = tokens_out / max(assistant_n, 1)
    substance = _clamp01(avg_out_tokens / 80.0)   # 每条助手回复 ≥80 token 视为有实质
    char_per_tok = generated_chars / max(tokens_out, 1)
    efficiency = _clamp01((char_per_tok - 0.5) / 3.5)  # >4 字符/token（代码为主）视为高效
    output_health = 0.5 * (substance + efficiency)
    if tokens_total > 60000:
        length_health = _clamp01(1.0 - (tokens_total - 60000) / 240000.0)
    else:
        length_health = 1.0
    context_health = _clamp01(0.35 * round_completion + 0.35 * output_health + 0.3 * length_health)

    raw = (0.35 * question_value + 0.25 * (1.0 - rework)
           + 0.2 * stability + 0.2 * context_health)
    score = int(round(100 * _clamp01(raw)))
    return {
        "question_value": round(question_value, 3),
        "rework": round(rework, 3),
        "stability": round(stability, 3),
        "context_health": round(context_health, 3),
        "score": score,
        "grade": quality_grade(score),
    }


def _quality_summary(convs: list[dict]) -> dict:
    """聚合会话质量：平均分 / 最佳 / 最差 / 分档分布。

    空列表返回可展示空态（sessions_scored=0），不抛异常。
    """
    scored = [c for c in convs if isinstance(c, dict) and isinstance(c.get("quality_score"), int)]
    empty = {
        "sessions_scored": 0, "avg": 0, "best": None, "best_score": 0,
        "worst": None, "worst_score": 0,
        "grade_dist": {"优": 0, "良": 0, "中": 0, "待优化": 0},
        "notice": _QUALITY_NOTICE,
    }
    if not scored:
        return empty
    avg = int(round(sum(c["quality_score"] for c in scored) / len(scored)))
    best = max(scored, key=lambda c: c["quality_score"])
    worst = min(scored, key=lambda c: c["quality_score"])
    dist = {"优": 0, "良": 0, "中": 0, "待优化": 0}
    for c in scored:
        g = c.get("quality_grade") or quality_grade(c["quality_score"])
        dist[g] = dist.get(g, 0) + 1
    return {
        "sessions_scored": len(scored),
        "avg": avg,
        "best": best.get("id"), "best_score": best["quality_score"],
        "worst": worst.get("id"), "worst_score": worst["quality_score"],
        "grade_dist": dist,
        "notice": _QUALITY_NOTICE,
    }


def _attach_quality(total: dict) -> dict:
    """给 total 聚合结果追加 quality_summary（只加不解构，向后兼容）。"""
    total["quality_summary"] = _quality_summary(total.get("conversations") or [])
    return total


def _web_tool(domain: str) -> str | None:
    """从域名识别 Web AI 工具名；不是 AI 聊天域名返回 None。"""
    domain = (domain or "").lower()
    for tool, subs in _WEB_AI_TOOLS.items():
        for sub in subs:
            if sub in domain:
                return tool
    return None


def _web_conv_id(url: str) -> str | None:
    """从聊天页面 URL 提取会话 ID；非会话页（首页/新建）返回 None。"""
    url = str(url or "")
    parsed = urllib.parse.urlparse(url)
    for rx in _WEB_CONV_PATTERNS:
        m = rx.search(parsed.path)
        if m:
            return m.group(1)[:48]
    m = _WEB_CONV_QUERY_RE.search(url)
    if m:
        return m.group(1)[:48]
    return None


def web_ai_sessions(visits: list[dict]) -> dict:
    """从浏览器访问明细深度解析 Web AI 会话（对话轮次追踪的浏览器侧）。

    输入为 browser_history.collect() 的 visits 条目（含 domain/url/time/title）。
    同一聊天会话 URL 的每次访问视为一次页面刷新 ≈ 一轮；按 (工具, 会话ID) 分组。
    返回结构：
    {
      "found": bool, "turns": int, "conversations": int, "browsing_visits": int,
      "by_tool": {tool: {"conversations": int, "turns": int}},
      "sessions": [ {tool, id, title, visits, first, last} ... ]（按访问次数倒序，上限 20）
    }
    """
    per: dict[tuple[str, str], dict] = {}
    browsing = 0
    for v in visits or []:
        tool = _web_tool(v.get("domain", ""))
        if not tool:
            continue
        conv = _web_conv_id(v.get("url", ""))
        if not conv:
            browsing += 1
            continue
        key = (tool, conv)
        entry = per.get(key)
        if entry is None:
            title = v.get("title") or ""
            if title in ("[已隐藏]", ""):
                title = ""
            entry = {"tool": tool, "id": conv, "title": title, "visits": 0,
                     "first": v.get("time") or "", "last": v.get("time") or ""}
            per[key] = entry
        entry["visits"] += 1
        if not entry.get("first") or (v.get("time") or "") < entry["first"]:
            entry["first"] = v.get("time") or ""
        if not entry.get("last") or (v.get("time") or "") > entry["last"]:
            entry["last"] = v.get("time") or ""
        if not entry["title"] and v.get("title"):
            entry["title"] = v["title"]

    by_tool: dict[str, dict] = {}
    turns = 0
    for (_tool, _conv), entry in per.items():
        turns += entry["visits"]
        agg = by_tool.setdefault(entry["tool"], {"conversations": 0, "turns": 0})
        agg["conversations"] += 1
        agg["turns"] += entry["visits"]
    sessions = sorted(per.values(), key=lambda e: e["visits"], reverse=True)[:20]
    return {
        "found": bool(per),
        "turns": turns,
        "conversations": len(per),
        "browsing_visits": browsing,
        "by_tool": by_tool,
        "sessions": sessions,
    }


def parse_file(path: str) -> list[dict]:
    """解析单个会话文件，返回消息对象列表（含 timestamp/role/content 近似字段）。"""
    out: list[dict] = []
    try:
        if path.lower().endswith((".jsonl", ".ndjson")):
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for msg in _extract_messages(obj):
                        out.append(msg)
        else:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                data = json.load(fh)
            for msg in _extract_messages(data):
                out.append(msg)
    except Exception:  # noqa: BLE001 —— 格式未知/文件损坏时跳过
        return []
    return out


# ---------------------------------------------------------------------------
# 工具专用解析器（model 多为会话级，需上下文回填，否则每条消息 model 均“未识别”）
# 返回与 parse_file 同构的 [{role, content, timestamp, model, conv_id?, project?}]。
# 均 best-effort：任何异常/缺失 → 空列表，绝不抛。
# ---------------------------------------------------------------------------
def _norm_pi_model(provider: str, model_id: str) -> str:
    """pi/opencode 的 provider+modelId → 统一模型名（去 provider 前缀，保留型号）。"""
    m = (model_id or "").strip()
    return _clean_model(m) if m else "未识别"


def _parse_pi_file(path: str) -> list[dict]:
    """解析 pi agent 会话 jsonl（~/.pi/agent/sessions/<proj>/*.jsonl）。

    pi 格式：逐行 JSON，type ∈ {session, model_change, message, ...}。
    - model_change 行携 modelId/provider → 作为当前会话 model 上下文回填
    - message 行：message.role / message.content[].text / timestamp
    - cwd（session 行）→ project
    每条消息回填当时生效的 model。
    """
    out: list[dict] = []
    cur_model = "未识别"
    project = None
    conv_id = os.path.splitext(os.path.basename(path))[0][:48]
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = obj.get("type")
                if typ == "session":
                    cwd = obj.get("cwd") or obj.get("directory")
                    if cwd:
                        project = os.path.basename(str(cwd).rstrip("/\\")) or None
                elif typ == "model_change":
                    cur_model = _norm_pi_model(obj.get("provider", ""), obj.get("modelId", ""))
                elif typ == "message":
                    inner = obj.get("message")
                    if not isinstance(inner, dict):
                        continue
                    role = inner.get("role")
                    if role not in _USER_ROLES and role not in _ASSISTANT_ROLES:
                        continue
                    content = inner.get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        parts = []
                        for p in content:
                            if isinstance(p, dict) and p.get("type") in ("text", None) and p.get("text"):
                                parts.append(str(p["text"]))
                            elif isinstance(p, str):
                                parts.append(p)
                        text = "\n".join(parts)
                    out.append({
                        "role": role, "content": text,
                        "timestamp": obj.get("timestamp") or inner.get("timestamp"),
                        "model": cur_model, "project": project, "conv_id": conv_id,
                    })
    except Exception:  # noqa: BLE001
        return out
    return out


def _parse_opencode_db(db_path: str, date_str: str) -> list[dict]:
    """读 opencode.db（SQLite）当日消息：message 表含 role/modelID/time，part 表含 text。

    只读、immutable 打开（不与守护竞争锁）；modelID 为会话级真实模型名。
    时间戳 time_created 为毫秒 epoch。任何异常 → 空列表。
    """
    import sqlite3  # noqa: PLC0415
    out: list[dict] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2.0)
    except Exception:  # noqa: BLE001
        return out
    try:
        conn.row_factory = sqlite3.Row
        # part text 按 message_id 聚合
        texts: dict[str, list[str]] = {}
        try:
            for r in conn.execute("SELECT message_id, data FROM part"):
                try:
                    pd = json.loads(r["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(pd, dict) and pd.get("type") == "text" and pd.get("text"):
                    texts.setdefault(r["message_id"], []).append(str(pd["text"]))
        except sqlite3.Error:
            pass
        # session -> project（directory 末段）
        proj: dict[str, str] = {}
        try:
            for r in conn.execute("SELECT id, directory FROM session"):
                d = r["directory"]
                if d:
                    proj[r["id"]] = os.path.basename(str(d).rstrip("/\\")) or ""
        except sqlite3.Error:
            pass
        for r in conn.execute("SELECT id, session_id, time_created, data FROM message"):
            try:
                md = json.loads(r["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(md, dict):
                continue
            role = md.get("role")
            if role not in _USER_ROLES and role not in _ASSISTANT_ROLES:
                continue
            try:
                ts = datetime.datetime.fromtimestamp(int(r["time_created"]) / 1000.0).isoformat()
            except (OSError, ValueError, OverflowError, TypeError):
                continue
            if not ts.startswith(date_str):
                continue
            out.append({
                "role": role,
                "content": "\n".join(texts.get(r["id"], [])),
                "timestamp": ts,
                "model": _clean_model(str(md.get("modelID") or "")) or "未识别",
                "project": proj.get(r["session_id"]) or None,
                "conv_id": r["session_id"],
            })
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def _iter_tool_messages(tool: str, dirs: list[str], date_str: str,
                        parsed_paths: set) -> list[tuple[str, list[dict]]]:
    """按工具选择解析器，产出 (标识, messages) 列表。

    - opencode：先读 opencode.db（SQLite，真实 model/成本），再傅 json/jsonl 文件（向后兼容）
    - pi_agent：jsonl 用 pi 专用解析（model_change 上下文回填）
    - 其他：parse_file
    """
    out: list[tuple[str, list[dict]]] = []
    if tool == "opencode":
        for d in dirs:
            db = os.path.join(d, "opencode.db")
            key = os.path.normcase(os.path.abspath(db))
            if os.path.isfile(db) and key not in parsed_paths:
                parsed_paths.add(key)
                out.append((db, _parse_opencode_db(db, date_str)))
    is_pi = tool == "pi_agent" or tool.startswith("pi")
    for path in _walk_files_batched(dirs):
        real = os.path.normcase(os.path.abspath(path))
        if real in parsed_paths:
            continue
        parsed_paths.add(real)
        msgs = (_parse_file_cached(path, tag="pi") if is_pi
                else _parse_file_cached(path))
        if is_pi and not msgs:  # pi 解析空时回退通用解析（同样记忆化：跨日只解析一次）
            msgs = _parse_file_cached(path)
        out.append((path, msgs))
    return out



def _generated_lines(text: str) -> int:
    """生成行数：按换行符拆分，空内容返回 0。"""
    text = text or ""
    if not text.strip():
        return 0
    return len(text.splitlines())


# ---------------------------------------------------------------------------
# collect 结果缓存（v2.7 性能）：指纹 = 各工具目录下会话文件的 (path, mtime_ns,
# size) 串接。只 stat 不读内容，比解析便宜一个数量级以上；文件新增/追加/变化
# 自动失效。调用方**不得修改**返回的 tools/total（跨请求共享，同 report.aggregate
# 约定）。web_ai 部分不缓存（随 web_visits 入参变化），每次现算。
# ---------------------------------------------------------------------------
_COLLECT_CACHE: "OrderedDict[tuple, tuple[str, dict]]" = OrderedDict()
_COLLECT_CACHE_MAX = 160  # ≥ 查询模板 max_days(92)：区间查询的逐日结果不再互挤（曾致缓存形同虚设）
# 并发安全（dashboard 为 ThreadingHTTPServer）：LRU 的 move_to_end / popitem 驱逐
# 在多线程并发下会互相踩踏（OrderedDict mutated during iteration / 脏读）。
# 锁内只做查表/改表；指纹计算、解析与收集等重活一律在锁外，不放大持锁时间。
_COLLECT_LOCK = threading.Lock()

# 批内指纹复用（v2.9 性能修复）：_paths_fingerprint 本身要 os.walk 整棵会话目录
# 树并 stat 全部文件——多日查询逐日调用 collect 时同一棵树被重复遍历 N 次
# （实测真实大目录单次 ~2s，90 天成本趋势 ≈ 3 分钟）。collect_fingerprint_batch()
# 提供**显式**批作用域：批内首个 collect 算一次指纹，后续同配置的 collect 直接复用；
# 批外行为与 v2.7 完全一致（每次现算、文件变化立即可见），无全局状态、无 TTL 语义变更。
_FP_BATCH = threading.local()


@contextlib.contextmanager
def collect_fingerprint_batch():
    """批作用域：with 块内的多次 collect 复用首次计算的目录指纹。

    适用：同一份 config 跨多天的批量收集（query 模板 / 报表成本章节）。
    批内若出现**不同**工具路径组合，会为该组合单独计算并各自记忆，
    互不污染；退出时恢复默认逐次现算语义。
    """
    ctx = _FP_BATCH
    prev_on = getattr(ctx, "on", False)
    prev_fp = getattr(ctx, "fp_map", None)
    prev_walk = getattr(ctx, "walk_map", None)
    ctx.on = True
    ctx.fp_map = {}
    ctx.walk_map = {}
    try:
        yield
    finally:
        ctx.on = prev_on
        ctx.fp_map = prev_fp
        ctx.walk_map = prev_walk


def _walk_files_batched(dirs: list[str]) -> list[str]:
    """批作用域内复用目录枚举结果（os.walk 是逐日收集的第二大开销）；
    批外直接现算。key=规范化后的目录元组。"""
    if not getattr(_FP_BATCH, "on", False):
        return _walk_files(dirs)
    key = tuple(os.path.normcase(os.path.abspath(d)) for d in dirs)
    walk_map = _FP_BATCH.walk_map
    if key not in walk_map:
        walk_map[key] = _walk_files(dirs)
    return walk_map[key]


def _fingerprint_for_batch(tool_paths: dict[str, list[str]]) -> str:
    """在批作用域内取指纹：同路径组合只算一次；批外退化为直接计算。"""
    if not getattr(_FP_BATCH, "on", False):
        return _paths_fingerprint(tool_paths)
    key = tuple((t, tuple(ps)) for t, ps in sorted(tool_paths.items()))
    fp_map = _FP_BATCH.fp_map
    if key not in fp_map:
        fp_map[key] = _paths_fingerprint(tool_paths)
    return fp_map[key]


def invalidate_collect_cache() -> None:
    """清空 collect 结果缓存与解析缓存（测试用；正常运行靠指纹/TTL 自动失效）。"""
    with _COLLECT_LOCK:
        _COLLECT_CACHE.clear()
    with _PARSE_LOCK:
        _PARSE_CACHE.clear()


# 文件解析记忆化（v2.9 性能修复）：多日查询逐日调用 collect 时，同一份会话文件
# 被反复 读盘+JSON 解析（真实大目录 90 天 ≈ 上万次重复解析，实测单查询 >100s）。
# 以 (绝对路径, mtime_ns, size) 为键缓存解析结果：内容未变即命中，追加/修改自动
# 失效。纯函数语义零变化；条目数与单文件大小双重上限防内存膨胀。
_PARSE_CACHE: "OrderedDict[tuple, tuple[int, list[dict]]]" = OrderedDict()
# 双上限防内存膨胀：条目数 + 缓存内容的「源文件字节数」预算。条目上限单独不够——
# 真实机器每日工作集约 900 个会话文件，512 条的旧上限让 LRU 每天整体换血，
# 缓存形同虚设（实测 90 天查询每次都全量重解析，>80s）。
_PARSE_CACHE_MAX_ENTRIES = 4096
_PARSE_CACHE_MAX_FILE_BYTES = 2 * 1024 * 1024
_PARSE_CACHE_BUDGET_BYTES = 256 * 1024 * 1024
_PARSE_LOCK = threading.Lock()  # 同 _COLLECT_LOCK：保护解析表的查/改与驱逐循环（含 sum 遍历）


def _parse_file_cached(path: str, *, tag: str = "gen") -> list[dict]:
    try:
        st = os.stat(path)
    except OSError:
        return []
    if st.st_size > _PARSE_CACHE_MAX_FILE_BYTES:
        return _parse_pi_file(path) if tag == "pi" else parse_file(path)
    key = (tag, os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    with _PARSE_LOCK:
        hit = _PARSE_CACHE.get(key)
        if hit is not None:
            _PARSE_CACHE.move_to_end(key)
            return hit[1]
    msgs = _parse_pi_file(path) if tag == "pi" else parse_file(path)  # 解析在锁外
    with _PARSE_LOCK:
        _PARSE_CACHE[key] = (st.st_size, msgs)
        # 驱逐：任一上限越界即从最旧端淘汰（同键新 mtime 视为新条目，自然置顶）
        while (len(_PARSE_CACHE) > _PARSE_CACHE_MAX_ENTRIES
               or sum(v[0] for v in _PARSE_CACHE.values()) > _PARSE_CACHE_BUDGET_BYTES):
            if not _PARSE_CACHE:
                break
            _PARSE_CACHE.popitem(last=False)
    return msgs


def _paths_fingerprint(tool_paths: dict[str, list[str]]) -> str:
    """轻量指纹：遍历各工具目录 stat 会话文件（含 opencode.db），不读内容。"""
    parts: list[str] = []
    for tool in sorted(tool_paths):
        parts.append(tool)
        for d in tool_paths[tool]:
            if not os.path.isdir(d):
                continue
            parts.append(d)
            for root, _dirs, files in os.walk(d):
                for name in files:
                    low = name.lower()
                    is_db = (tool == "opencode" and root == os.path.dirname(
                        os.path.join(d, "opencode.db")) and low == "opencode.db")
                    if not low.endswith((".json", ".jsonl", ".ndjson")) and not is_db:
                        continue
                    p = os.path.join(root, name)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_size > _MAX_FILE_SIZE:
                        continue
                    parts.append(f"{p}|{st.st_mtime_ns}|{st.st_size}")
    # 排序后拼接：os.scandir 顺序在 NTFS 上是任意的，不排序则同一目录内容
    # 每次产生不同指纹串，_COLLECT_CACHE 的比对永远失配（缓存形同虚设）。
    return "\n".join(sorted(parts))


def _collect_cached(date_str: str, config: dict, section: dict,
                    token_est: bool, mode: str,
                    costs_enabled: bool, pricing: dict) -> dict:
    """本地工具部分（tools/total）的带缓存收集；返回共享对象（只读约定）。"""
    tool_paths = _config_paths(config)
    cfg_sig = json.dumps(
        {"e": bool(section.get("enabled", True)), "te": token_est, "m": mode,
         "c": costs_enabled, "p": {k: list(v) for k, v in sorted(pricing.items())}},
        sort_keys=True, ensure_ascii=False)
    key = (date_str, cfg_sig)
    fp = _fingerprint_for_batch(tool_paths)
    with _COLLECT_LOCK:
        hit = _COLLECT_CACHE.get(key)
        if hit is not None and hit[0] == fp:
            _COLLECT_CACHE.move_to_end(key)
            return hit[1]
    result = _collect_local(date_str, config, section, token_est, mode,
                            costs_enabled, pricing, tool_paths)  # 收集计算在锁外
    with _COLLECT_LOCK:
        _COLLECT_CACHE[key] = (fp, result)
        while len(_COLLECT_CACHE) > _COLLECT_CACHE_MAX:
            _COLLECT_CACHE.popitem(last=False)
    return result


def _collect_local(date_str: str, config: dict, section: dict,
                   token_est: bool, mode: str,
                   costs_enabled: bool, pricing: dict,
                   tool_paths: dict[str, list[str]]) -> dict:
    """原 collect() 的本地工具扫描主体（无缓存版，供 _collect_cached 调用）。"""
    estimator = estimate_tokens if mode == "simple" else estimate_tokens_weighted
    tools: dict[str, dict] = {}
    parsed_paths: set[str] = set()
    for tool, dirs in tool_paths.items():
        stats = _empty_tool_stats()
        conv_buckets: dict[str, list[dict]] = {}
        for path, messages in _iter_tool_messages(tool, dirs, date_str, parsed_paths):
            hit_file = False
            for msg in messages:
                ts = _message_time(msg)
                if not ts or not ts.startswith(date_str):
                    continue
                role = _message_role(msg)
                content = _message_content(msg)
                hit_file = True
                stats["turns"] += 1
                is_user = role in _USER_ROLES
                is_assistant = role in _ASSISTANT_ROLES
                if is_user:
                    stats["user_messages"] += 1
                elif is_assistant:
                    stats["assistant_messages"] += 1
                    stats["generated_lines"] += _generated_lines(content)
                    stats["generated_chars"] += len(content or "")
                model = _message_model(msg)
                # Token 口径：优先消息内真实 usage 字段（许多 harness 记录 API 返回值），
                # 缺失时按配置估算（weighted/simple），估算按角色拆分 in/out
                usage = _message_usage(msg)
                if usage is not None:
                    eff_in, eff_out = usage
                    stats["tokens_from_usage"] += 1
                elif token_est:
                    est = estimator(content)
                    eff_in, eff_out = (0, est) if is_assistant else (est, 0)
                else:
                    eff_in = eff_out = 0
                tokens = eff_in + eff_out
                stats["tokens_in"] += eff_in
                stats["tokens_out"] += eff_out
                stats["tokens_total"] += tokens
                # 成本估算（真实/估算 token × 模型单价；未识模型/未开启时为 0）
                c_in = c_out = 0.0
                if costs_enabled:
                    p_in, p_out = _model_price(pricing, model)
                    c_in = eff_in * p_in / 1e6
                    c_out = eff_out * p_out / 1e6
                    stats["cost_in"] += c_in
                    stats["cost_out"] += c_out
                stats["cost_total"] += c_in + c_out
                # 模型维度（按消息归属）
                _add_dim(stats["by_model"], model, eff_in, eff_out, c_in, c_out)
                # 会话分组（轮次 / 详情 / 项目以此为准）
                conv_id = _message_conv_id(msg, path)
                conv_buckets.setdefault(conv_id, []).append({
                    "msg": msg, "role": role, "tokens": tokens,
                    "t_in": eff_in, "t_out": eff_out,
                    "model": model, "project": _message_project(msg),
                    "cost_in": c_in, "cost_out": c_out,
                })
            if hit_file:
                stats["files"] += 1
        # 会话轮次统计 + 项目维度 + 详情（项目按会话归口，避免工具目录名污染）
        for conv_id, items in conv_buckets.items():
            detail = _conversation_summary(conv_id, tool, items, token_est)
            if detail is None:
                continue
            stats["rounds"] += detail["rounds"]
            pe = stats["by_project"].setdefault(
                detail["project"],
                {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                 "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0},
            )
            pe["turns"] += detail["turns"]
            pe["tokens_in"] += detail["tokens_in"]
            pe["tokens_out"] += detail["tokens_out"]
            pe["tokens_total"] += detail["tokens_total"]
            pe["cost_in"] += detail["cost_in"]
            pe["cost_out"] += detail["cost_out"]
            pe["cost_total"] += detail["cost_total"]
            stats["conversations"].append(detail)
        stats["conversations"].sort(key=lambda c: c["turns"], reverse=True)
        stats["conversations"] = stats["conversations"][:20]
        if stats["files"] or stats["turns"]:
            tools[tool] = stats

    total = _empty_total()
    for stats in tools.values():
        for key in ("files", "turns", "rounds", "user_messages", "assistant_messages",
                    "generated_lines", "generated_chars", "tokens_in", "tokens_out",
                    "tokens_total", "tokens_from_usage", "cost_in", "cost_out", "cost_total"):
            total[key] += stats[key]
        _merge_dim(total["by_model"], stats["by_model"])
        _merge_dim(total["by_project"], stats["by_project"])
        total["conversations"].extend(stats["conversations"])
    total["conversations"].sort(key=lambda c: c["turns"], reverse=True)
    total["conversations"] = total["conversations"][:20]
    total = _attach_quality(total)
    return {"tools": tools, "total": total}


def collect(date_str: str, config: dict, web_visits: list[dict] | None = None) -> dict:
    """统计某天 AI 会话深度指标（ROADMAP Phase 1）。

    返回结构（向后兼容旧的 tools/total 标量字段，新增维度）：
    {
      "date": "YYYY-MM-DD",
      "enabled": bool,
      "found": bool,
      "tools": {tool: {...}},   # 共享缓存对象，调用方不得修改
      "total": {...},           # 同上
      "web_ai": web_ai_sessions(web_visits)（web_visits 为空或 web_ai.enabled=false 时 found=false）
    }
    性能：本地工具部分带指纹缓存（文件 mtime/size 变化自动失效），
    重复调用（仪表盘多端点 / 报表逐日成本账本）只做一次解析。
    """
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    enabled = bool(section.get("enabled", True))
    token_est = bool(section.get("token_estimation", True))
    # 估算口径：weighted（默认，按字符类别加权）| simple（历史口径）
    mode = str(section.get("token_estimation_mode") or "weighted").strip().lower()
    costs_enabled = bool(_cost_section(config).get("enabled", True))
    pricing = _pricing_table(config) if costs_enabled else {}
    empty_web = {"found": False, "turns": 0, "conversations": 0, "browsing_visits": 0,
                 "by_tool": {}, "sessions": []}
    if not enabled:
        return {"date": date_str, "enabled": False, "found": False,
                "tools": {}, "total": _attach_quality(_empty_total()), "web_ai": empty_web}

    core = _collect_cached(date_str, config, section, token_est, mode,
                           costs_enabled, pricing)
    tools, total = core["tools"], core["total"]

    # Web AI 会话（浏览器历史深度解析）
    web_ai = empty_web
    if bool(section.get("web_ai", {}).get("enabled", True)) and web_visits:
        web_ai = web_ai_sessions(web_visits)
    return {
        "date": date_str,
        "enabled": True,
        "found": bool(tools) or bool(web_ai["found"]),
        "tools": tools,
        "total": total,
        "web_ai": web_ai,
    }


def _empty_tool_stats() -> dict:
    return {"files": 0, "turns": 0, "rounds": 0, "user_messages": 0,
            "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "tokens_from_usage": 0,
            "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0,
            "by_model": {}, "by_project": {}, "conversations": []}


def _add_dim(dim: dict, key: str, t_in: int, t_out: int,
             c_in: float = 0.0, c_out: float = 0.0) -> None:
    """累计 by_model 维度（t_in/t_out 为该消息的输入/输出 token，调用方算好）。"""
    e = dim.setdefault(key, {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                             "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
    e["turns"] += 1
    e["tokens_in"] += t_in
    e["tokens_out"] += t_out
    e["tokens_total"] += t_in + t_out
    e["cost_in"] += c_in
    e["cost_out"] += c_out
    e["cost_total"] += c_in + c_out


def _merge_dim(target: dict, src: dict) -> None:
    """把 src 维度聚合并入 target。"""
    for key, e in src.items():
        t = target.setdefault(key, {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                                    "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
        t["turns"] += e["turns"]
        t["tokens_in"] += e["tokens_in"]
        t["tokens_out"] += e["tokens_out"]
        t["tokens_total"] += e["tokens_total"]
        t["cost_in"] += e["cost_in"]
        t["cost_out"] += e["cost_out"]
        t["cost_total"] += e["cost_total"]


def _conversation_summary(conv_id: str, tool: str, items: list[dict],
                          token_est: bool) -> dict | None:
    """把一个会话的消息桶汇总成会话详情（轮次/Token/主导模型/项目/质量）。

    items 为 [{msg, role, tokens, model, project}]（其中 project 为显式字段值或 None）：
    - rounds 由原始消息序列按 user→assistant 配对计算；
    - project 取该会话里显式字段（cwd/project/repo...）的众数，缺失则「未识别」；
    - model 取消息序列的众数；
    - quality_score/quality_factors 为派生评分（_conversation_quality，纯启发式）。
    """
    if not items:
        return None
    model_counter: dict[str, int] = {}
    # 会话级模型：仅统计 assistant 消息的已知模型（用户消息无 model 字段，
    # 若混入会把「未识别」顶成众数；claude/opencode 等真实模型在 assistant 上）。
    model_counter_assistant: dict[str, int] = {}
    project_counter: dict[str, int] = {}
    user_n = assistant_n = tokens_out = tokens_total = 0
    cost_in_sum = cost_out_sum = 0.0
    first = last = ""
    first_sec = last_sec = None
    user_tokens: list[int] = []
    generated_chars = 0
    for it in items:
        role = it.get("role")
        ts = _message_time(it["msg"]) or ""
        content = _message_content(it["msg"])
        tokens = it.get("tokens") or 0
        t_in = it.get("t_in")
        t_out = it.get("t_out")
        if t_in is None or t_out is None:
            # 兼容旧调用方（items 无 t_in/t_out 键）：按角色拆分
            if role in _ASSISTANT_ROLES:
                t_in, t_out = 0, tokens
            else:
                t_in, t_out = tokens, 0
        c_in = it.get("cost_in") or 0.0
        c_out = it.get("cost_out") or 0.0
        if role in _USER_ROLES:
            user_n += 1
            # 质量评分独立估算用户消息长度（不受 token_estimation 开关影响）
            user_tokens.append(estimate_tokens(content))
        elif role in _ASSISTANT_ROLES:
            assistant_n += 1
            generated_chars += len(content)
        tokens_out += t_out
        tokens_total += t_in + t_out
        cost_in_sum += c_in
        cost_out_sum += c_out
        model_counter[it.get("model") or "未识别"] = model_counter.get(it.get("model") or "未识别", 0) + 1
        if role in _ASSISTANT_ROLES:
            _m = it.get("model")
            if _m and _m != "未识别":
                model_counter_assistant[_m] = model_counter_assistant.get(_m, 0) + 1
        proj = it.get("project")
        if proj:
            project_counter[proj] = project_counter.get(proj, 0) + 1
        sec = _ts_seconds(ts)
        if not first or (ts and ts < first):
            first = ts
            first_sec = sec
        if not last or (ts and ts > last):
            last = ts
            last_sec = sec
    project = max(project_counter, key=project_counter.get) if project_counter else "未识别"
    model = (max(model_counter_assistant, key=model_counter_assistant.get)
             if model_counter_assistant else max(model_counter, key=model_counter.get))
    rounds = _count_rounds([it["msg"] for it in items])
    span_s = 0.0
    if first_sec is not None and last_sec is not None:
        span_s = max(0.0, last_sec - first_sec)
    qf = _conversation_quality(
        user_n=user_n, assistant_n=assistant_n, rounds=rounds,
        tokens_in=tokens_total - tokens_out, tokens_out=tokens_out,
        tokens_total=tokens_total, model_count=len(model_counter),
        span_s=span_s, user_tokens=user_tokens, generated_chars=generated_chars,
    )
    return {
        "id": conv_id,
        "tool": tool,
        "model": model,
        "project": project,
        "turns": len(items),
        "rounds": rounds,
        "user_messages": user_n,
        "assistant_messages": assistant_n,
        "tokens_in": tokens_total - tokens_out,
        "tokens_out": tokens_out,
        "tokens_total": tokens_total,
        "cost_in": round(cost_in_sum, 8),
        "cost_out": round(cost_out_sum, 8),
        "cost_total": round(cost_in_sum + cost_out_sum, 8),
        "first": first,
        "last": last,
        # 质量派生（0-100 + 因子明细 + 透明声明）
        "quality_score": qf["score"],
        "quality_grade": qf["grade"],
        "quality_factors": {
            "question_value": qf["question_value"],
            "rework": qf["rework"],
            "stability": qf["stability"],
            "context_health": qf["context_health"],
        },
        "quality_notice": _QUALITY_NOTICE,
    }


def _empty_total() -> dict:
    return {"files": 0, "turns": 0, "rounds": 0, "user_messages": 0,
            "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "tokens_from_usage": 0,
            "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0,
            "by_model": {}, "by_project": {}, "conversations": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai_sessions.py", description="AI 会话深度统计（可选增强）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('version').VERSION}")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期（默认今天）")
    parser.add_argument("--today", action="store_true", help="今天")
    parser.add_argument("--web", action="store_true", help="同时解析浏览器访问明细中的 Web AI 会话（对话轮次追踪）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        if args.config is None and args.data_root:
            args.config = os.path.join(args.data_root, "config.json")
        cfg = classifier.load_config(args.config)
    except Exception:  # noqa: BLE001
        cfg = {}

    if args.today:
        date_str = datetime.date.today().isoformat()
    elif args.day:
        date_str = args.day
    else:
        date_str = datetime.date.today().isoformat()
    if not _DAY_RE.fullmatch(date_str):
        print(f"[ai_sessions] 日期格式错误: {date_str}（应为 YYYY-MM-DD）", file=sys.stderr)
        return 2

    web_visits: list[dict] | None = None
    if args.web:
        try:
            import browser_history  # noqa: PLC0415
            data_root = args.data_root or cfg.get("data_root") or "."
            web_visits = browser_history.collect(date_str, data_root, cfg).get("visits") or []
        except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地统计
            web_visits = []
    result = collect(date_str, cfg, web_visits=web_visits or None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"# AI 会话深度统计 {date_str}")
    if not result["enabled"]:
        print("未启用：config.json 的 ai_sessions.enabled=false")
        return 0
    if not result["found"]:
        print("（未发现该日期的本地 AI 会话记录；可配置 ai_sessions.paths 指向会话目录。"
              "浏览器 Web AI 会话可用 --web 附带统计）")
        return 0
    for tool, s in result["tools"].items():
        print(f"- {tool}: 文件 {s['files']} 个，消息 {s['turns']}（轮次 {s['rounds']}），"
              f"用户 {s['user_messages']} / 助手 {s['assistant_messages']}，"
              f"生成 {s['generated_lines']} 行 / {s['generated_chars']} 字符，"
              f"Token 进 {s['tokens_in']} / 出 {s['tokens_out']}，"
              f"费用 {_fmt_cost(s.get('cost_total', 0))}")
    print(f"合计: {result['total']['turns']} 条消息（轮次 {result['total']['rounds']}），"
          f"生成 {result['total']['generated_lines']} 行，Token 进 {result['total']['tokens_in']} / "
          f"出 {result['total']['tokens_out']}，"
          f"费用 {_fmt_cost(result['total']['cost_total'])}")
    if result["total"]["by_model"]:
        top_model = sorted(result["total"]["by_model"].items(),
                           key=lambda kv: kv[1]["turns"], reverse=True)[:5]
        print("模型分布: " + "；".join(f"{m} {v['turns']} 条 / {_fmt_cost(v['cost_total'])}" for m, v in top_model))
    if result["total"]["by_project"]:
        top_proj = sorted(result["total"]["by_project"].items(),
                          key=lambda kv: kv[1]["turns"], reverse=True)[:5]
        print("项目分布: " + "；".join(f"{p} {v['turns']} 条 / {_fmt_cost(v['cost_total'])}" for p, v in top_proj))
    web = result.get("web_ai") or {}
    if web.get("found"):
        print(f"Web AI 会话: {web['conversations']} 个会话，{web['turns']} 次页面访问"
              + ("（按工具: " +
                 "；".join(f"{t} {a['conversations']} 会话/{a['turns']} 次"
                           for t, a in web["by_tool"].items()) + "）" if web["by_tool"] else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
