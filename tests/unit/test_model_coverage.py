# -*- coding: utf-8 -*-
"""tests/unit/test_model_coverage.py — 模型正则识别覆盖（60+ case）。

_clean_model 策略：小写、去引号/换行、去日期/包装后缀（-preview/-latest/-custom），
保留模型语义后缀（-pro/-flash/-lite/-plus/-long/-chat/-coder/-math/-reasoner/-r1/-v3/-v4 等）。
"""

from __future__ import annotations

import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402

_RE = ai_sessions._KNOWN_MODEL_RE


def _match(text: str | None) -> str | None:
    if not text:
        return None
    m = _RE.search(text)
    return ai_sessions._clean_model(m.group(0)) if m else None


# ---------------------------------------------------------------------------
# 1. GPT 系列（5.x / 4o / 4 / 3.5 / 3）
# ---------------------------------------------------------------------------
def test_gpt_5x_variants():
    cases = [
        ("gpt-5", "gpt-5"),
        ("gpt-5.5", "gpt-5.5"),
        ("gpt-5-mini", "gpt-5-mini"),
        ("gpt-5-turbo", "gpt-5-turbo"),
        ("gpt-5-custom", "gpt-5"),
        ("GPT-5", "gpt-5"),
        ("gpt_5", "gpt_5"),
        ("model: gpt-5.5-preview", "gpt-5.5"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_gpt_4o_variants():
    cases = [
        ("gpt-4o", "gpt-4o"),
        ("gpt-4o-mini", "gpt-4o-mini"),
        ("gpt-4o-custom", "gpt-4o"),
        ("gpt-4o-2024-05-13", "gpt-4o"),
        ("GPT-4o", "gpt-4o"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_gpt_4x_variants():
    cases = [
        ("gpt-4", "gpt-4"),
        ("gpt-4-turbo", "gpt-4-turbo"),
        ("gpt-4.5", "gpt-4.5"),
        ("gpt-4.5-turbo", "gpt-4.5-turbo"),
        ("gpt-4.1", "gpt-4.1"),
        ("gpt-4.1-mini", "gpt-4.1-mini"),
        ("gpt-4.1-nano", "gpt-4.1-nano"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_gpt_35_3_variants():
    cases = [
        ("gpt-3.5", "gpt-3.5"),
        ("gpt-3.5-turbo", "gpt-3.5-turbo"),
        ("gpt-3", "gpt-3"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_o_series():
    cases = [
        ("o1", "o1"),
        ("o1-mini", "o1-mini"),
        ("o3", "o3"),
        ("o3-mini", "o3-mini"),
        ("o3-pro", "o3-pro"),
        ("o4-mini", "o4-mini"),
        ("o4", "o4"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 2. Claude 系列（5 / 4 / 3 / opus / sonnet / haiku）
# ---------------------------------------------------------------------------
def test_claude_5_variants():
    cases = [
        ("claude-5", "claude-5"),
        ("claude-5-sonnet", "claude-5-sonnet"),
        ("claude-5-opus", "claude-5-opus"),
        ("claude-5-haiku", "claude-5-haiku"),
        ("claude-5.1", "claude-5.1"),
        ("claude-5.1-sonnet", "claude-5.1-sonnet"),
        ("Claude-5", "claude-5"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_claude_4_variants():
    cases = [
        ("claude-4", "claude-4"),
        ("claude-4-sonnet", "claude-4-sonnet"),
        ("claude-4-opus", "claude-4-opus"),
        ("claude-4.5", "claude-4.5"),
        ("claude-4.5-sonnet", "claude-4.5-sonnet"),
        ("claude-4.6", "claude-4.6"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_claude_3_variants():
    cases = [
        ("claude-3", "claude-3"),
        ("claude-3-sonnet", "claude-3-sonnet"),
        ("claude-3.5", "claude-3.5"),
        ("claude-3.5-sonnet", "claude-3.5-sonnet"),
        ("claude-3.7", "claude-3.7"),
        ("claude-3-haiku", "claude-3-haiku"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_claude_family_names():
    cases = [
        ("claude-opus", "claude-opus"),
        ("claude-opus-4", "claude-opus-4"),
        ("claude-sonnet", "claude-sonnet"),
        ("claude-sonnet-4", "claude-sonnet-4"),
        ("claude-haiku", "claude-haiku"),
        ("claude-haiku-4", "claude-haiku-4"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 3. Gemini 系列（3 / 2.5 / flash / pro / lite）
# ---------------------------------------------------------------------------
def test_gemini_3_variants():
    cases = [
        ("gemini-3", "gemini-3"),
        ("gemini-3-pro", "gemini-3-pro"),
        ("gemini-3-flash", "gemini-3-flash"),
        ("gemini-3-flash-lite", "gemini-3-flash-lite"),
        ("gemini-3.1", "gemini-3.1"),
        ("gemini-3.1-pro", "gemini-3.1-pro"),
        ("gemini-3.6", "gemini-3.6"),
        ("gemini-3.6-flash", "gemini-3.6-flash"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_gemini_25_variants():
    cases = [
        ("gemini-2.5", "gemini-2.5"),
        ("gemini-2.5-pro", "gemini-2.5-pro"),
        ("gemini-2.5-flash", "gemini-2.5-flash"),
        ("gemini-2.5-flash-lite", "gemini-2.5-flash-lite"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 4. Grok 系列（4 / 3）
# ---------------------------------------------------------------------------
def test_grok_variants():
    cases = [
        ("grok-4", "grok-4"),
        ("grok-4-pro", "grok-4-pro"),
        ("grok-4-mini", "grok-4-mini"),
        ("grok-3", "grok-3"),
        ("grok-3-pro", "grok-3-pro"),
        ("grok-3-mini", "grok-3-mini"),
        ("Grok-4", "grok-4"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 5. GLM 系列（5.2 / 5 / 4）
# ---------------------------------------------------------------------------
def test_glm_variants():
    cases = [
        ("glm-5.2", "glm-5.2"),
        ("glm-5.2-turbo", "glm-5.2-turbo"),
        ("glm-5", "glm-5"),
        ("glm-5-turbo", "glm-5-turbo"),
        ("glm-4", "glm-4"),
        ("glm-4-air", "glm-4-air"),
        ("GLM-5", "glm-5"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 6. DeepSeek 系列
# ---------------------------------------------------------------------------
def test_deepseek_variants():
    cases = [
        ("deepseek", "deepseek"),
        ("deepseek-chat", "deepseek-chat"),
        ("deepseek-reasoner", "deepseek-reasoner"),
        ("deepseek-r1", "deepseek-r1"),
        ("deepseek-v3", "deepseek-v3"),
        ("deepseek-v3.1", "deepseek-v3.1"),
        ("deepseek-coder", "deepseek-coder"),
        ("deepseek-math", "deepseek-math"),
        ("DeepSeek-V3", "deepseek-v3"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 7. Qwen 系列
# ---------------------------------------------------------------------------
def test_qwen_variants():
    cases = [
        ("qwen", "qwen"),
        ("qwen-max", "qwen-max"),
        ("qwen3-max", "qwen3-max"),
        ("qwen-plus", "qwen-plus"),
        ("qwen-turbo", "qwen-turbo"),
        ("qwen2.5", "qwen2.5"),
        ("Qwen-Max", "qwen-max"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 8. LLaMA 系列
# ---------------------------------------------------------------------------
def test_llama_variants():
    cases = [
        ("llama", "llama"),
        ("llama-3", "llama-3"),
        ("llama-3.1", "llama-3.1"),
        ("llama-4", "llama-4"),
        ("llama-4-scout", "llama-4-scout"),
        ("Llama-3", "llama-3"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 9. Mistral / CodeStral 系列
# ---------------------------------------------------------------------------
def test_mistral_variants():
    cases = [
        ("mistral", "mistral"),
        ("mistral-large", "mistral-large"),
        ("mistral-medium", "mistral-medium"),
        ("mistral-small", "mistral-small"),
        ("mistral-nemo", "mistral-nemo"),
        ("codestral", "codestral"),
        ("codestral-22b", "codestral-22b"),
        ("Mistral-Large", "mistral-large"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 10. 其他模型（moonshot / kimi / doubao / spark / ernie / command / codex / gemma）
# ---------------------------------------------------------------------------
def test_other_models():
    cases = [
        ("moonshot", "moonshot"),
        ("moonshot-v1", "moonshot-v1"),
        ("kimi", "kimi"),
        ("kimi-chat", "kimi-chat"),
        ("doubao", "doubao"),
        ("doubao-pro", "doubao-pro"),
        ("spark", "spark"),
        ("spark-3.5", "spark-3.5"),
        ("ernie", "ernie"),
        ("ernie-4.0", "ernie-4.0"),
        ("command", "command"),
        ("command-r", "command-r"),
        ("command-r-plus", "command-r-plus"),
        ("codex", "codex"),
        ("codex-cushman", "codex-cushman"),
        ("gemma", "gemma"),
        ("gemma-2", "gemma-2"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 11. 内容里嵌入模型名
# ---------------------------------------------------------------------------
def test_model_in_content():
    cases = [
        ("The model is gpt-4o-mini", "gpt-4o-mini"),
        ("Using claude-3-5-sonnet for this task", "claude-3-5-sonnet"),
        ("Response from gemini-2.5-flash", "gemini-2.5-flash"),
        ("deepseek-v3 generated this", "deepseek-v3"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 12. 大小写不敏感
# ---------------------------------------------------------------------------
def test_case_insensitive():
    assert _match("GPT-4o") == "gpt-4o"
    assert _match("Claude-3-Opus") == "claude-3-opus"
    assert _match("GEMINI-2.5-PRO") == "gemini-2.5-pro"
    assert _match("DeepSeek-R1") == "deepseek-r1"


# ---------------------------------------------------------------------------
# 13. 不匹配项（负向验证）
# ---------------------------------------------------------------------------
def test_no_match():
    assert _match("hello world") is None
    assert _match("python-3.12") is None
    assert _match("npm-package") is None
    assert _match("my-model-v1") is None
    assert _match("") is None
    assert _match("Claude is a name, not a model") is None


# ---------------------------------------------------------------------------
# 14. 优先级/贪婪（最长匹配）
# ---------------------------------------------------------------------------
def test_greedy_match():
    assert _match("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert _match("claude-3-5-sonnet-20240620") == "claude-3-5-sonnet"


# ---------------------------------------------------------------------------
# 15. 带前后缀的上下文
# ---------------------------------------------------------------------------
def test_context_suffixes():
    cases = [
        ("model=gpt-4o", "gpt-4o"),
        ('"model": "claude-3-opus"', "claude-3-opus"),
        ("model_id: gemini-2.5-pro", "gemini-2.5-pro"),
        ("delta.model -> o3-mini", "o3-mini"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 16. 空值/None 安全
# ---------------------------------------------------------------------------
def test_empty_or_none():
    assert _match("") is None
    pass  # _match(None) handled in function


# ---------------------------------------------------------------------------
# 17. 新模型：Gemma / Minimax / Yi / Step / Baichuan / Hunyuan
# ---------------------------------------------------------------------------
def test_newer_models():
    cases = [
        ("gemma-2", "gemma-2"),
        ("minimax", "minimax"),
        ("minimax-m1", "minimax-m1"),
        ("yi-large", "yi-large"),
        ("step-1", "step-1"),
        ("baichuan-4", "baichuan-4"),
        ("hunyuan", "hunyuan"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


def test_hunyuan():
    assert _match("hunyuan") == "hunyuan"
    assert _match("Hunyuan-Turb") == "hunyuan-turb"


def test_codex():
    assert _match("codex") == "codex"
    assert _match("codex-cushman") == "codex-cushman"


# ---------------------------------------------------------------------------
# 18. GPT-5.4 / 5.6 等新版本
# ---------------------------------------------------------------------------
def test_gpt_54_variants():
    cases = [
        ("gpt-5.4", "gpt-5.4"),
        ("gpt-5.4-mini", "gpt-5.4-mini"),
        ("gpt-5.6", "gpt-5.6"),
        ("gpt-5.6-turbo", "gpt-5.6-turbo"),
    ]
    for text, expected in cases:
        assert _match(text) == expected, f"{text} -> {_match(text)} != {expected}"


# ---------------------------------------------------------------------------
# 19. 贪婪匹配（最长有效匹配）
# ---------------------------------------------------------------------------
def test_greedy_match():
    assert _match("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert _match("claude-3-5-sonnet-20240620") == "claude-3-5-sonnet"
    assert _match("gemini-2.5-pro-latest") == "gemini-2.5-pro"
