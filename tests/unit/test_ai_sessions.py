# -*- coding: utf-8 -*-
"""tests/unit/test_ai_sessions.py — Token/成本/解析 单元测试（零依赖、确定性）."""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


def test_estimate_tokens_cjk_and_ascii():
    # 空
    assert ai_sessions.estimate_tokens("") == 0
    assert ai_sessions.estimate_tokens("   ") == 0
    # CJK 每字 1 Token
    assert ai_sessions.estimate_tokens("你好世界") == 4
    # ASCII 4 字符 1 Token（进一法）
    assert ai_sessions.estimate_tokens("hello") == 2  # 5/4 ->2
    assert ai_sessions.estimate_tokens("abcd") == 1
    assert ai_sessions.estimate_tokens("abcde") == 2
    # 混合
    mixed = "hi你好"  # 你好 2 CJK + hi 2 ascii -> 2 + 1 =3
    assert ai_sessions.estimate_tokens(mixed) == 3
    print("  [PASS] estimate_tokens")


def test_fmt_cost_edges():
    assert ai_sessions._fmt_cost(0) == "$0"
    assert ai_sessions._fmt_cost(0.001) == "$0.0010"
    assert ai_sessions._fmt_cost(0.005) == "$0.0050"
    assert ai_sessions._fmt_cost(0.5) == "$0.500"
    assert ai_sessions._fmt_cost(3.5) == "$3.50"
    print("  [PASS] fmt_cost")


def test_model_price_matching():
    table = {"gpt-4o": (5.0, 15.0), "claude": (3.0, 15.0), "deepseek": (1.0, 2.0)}
    # 精确匹配
    assert ai_sessions._model_price(table, "gpt-4o") == (5.0, 15.0)
    # 子串匹配（最长键优先）
    assert ai_sessions._model_price(table, "gpt-4o-mini") == (5.0, 15.0)
    assert ai_sessions._model_price(table, "claude-3-5-sonnet") == (3.0, 15.0)
    # 未命中
    assert ai_sessions._model_price(table, "unknown-model") == (0.0, 0.0)
    assert ai_sessions._model_price(table, "") == (0.0, 0.0)
    print("  [PASS] model_price")


def test_pricing_table_merge_priority(tmp_path):
    # 内置 -> config 覆盖 -> ai_pricing.json 覆盖
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"costs": {"enabled": True, "model_pricing": {"gpt-4o": [10, 20]}}},
    }
    # 此时 config 覆盖生效
    t1 = ai_sessions._pricing_table(cfg)
    assert t1.get("gpt-4o") == (10.0, 20.0)
    # 再放 ai_pricing.json，应覆盖 config
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"gpt-4o": [99, 99]}, fh)
    t2 = ai_sessions._pricing_table(cfg)
    assert t2.get("gpt-4o") == (99.0, 99.0)
    print("  [PASS] pricing_table_merge")


def test_pricing_merge_supports_both_formats(tmp_path):
    root = str(tmp_path / "pricing2")
    os.makedirs(root, exist_ok=True)
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m1": [1, 2], "m2": {"input": 3, "output": 4}})
    assert table["m1"] == (1.0, 2.0)
    assert table["m2"] == (3.0, 4.0)
    # 非法格式忽略
    ai_sessions._merge_pricing(table, {"bad": "string"})
    assert "bad" not in table
    print("  [PASS] pricing_merge_formats")


def test_count_rounds():
    assert ai_sessions._count_rounds([]) == 0
    # user -> assistant 算 1 轮
    msgs = [{"role": "user"}, {"role": "assistant"}]
    assert ai_sessions._count_rounds(msgs) >= 1
    # user -> user -> assistant 仍算 1 轮（配对逻辑）
    msgs2 = [{"role": "user"}, {"role": "user"}, {"role": "assistant"}]
    assert ai_sessions._count_rounds(msgs2) == 1
    print("  [PASS] count_rounds")


def test_web_ai_grouping():
    visits = [
        {"url": "https://chat.openai.com/c/abc123", "domain": "chat.openai.com", "title": "ChatGPT", "time": "2026-08-08T10:00:00", "category": "学习"},
        {"url": "https://chat.openai.com/c/abc123", "domain": "chat.openai.com", "title": "ChatGPT", "time": "2026-08-08T10:05:00", "category": "学习"},
        {"url": "https://claude.ai/chat/xyz12345678", "domain": "claude.ai", "title": "Claude", "time": "2026-08-08T11:00:00", "category": "学习"},
    ]
    out = ai_sessions.web_ai_sessions(visits)
    assert out["found"] is True
    assert out["conversations"] >= 2
    assert any("chat.openai.com" in s.get("tool", "") or "openai" in s.get("tool", "").lower() or s.get("id") == "abc123" for s in out.get("sessions", [])) or out["by_tool"]
    print("  [PASS] web_ai_grouping")


def test_collect_with_synthetic_file(tmp_path):
    """用合成会话文件验证 collect 端到端（文件 -> tokens/rounds/cost）。"""
    root = str(tmp_path / "ai_collect")
    tool_dir = os.path.join(root, "opencode_sessions")
    os.makedirs(tool_dir, exist_ok=True)
    day = "2099-08-08"
    # 写一个最小会话文件：2 条消息 user->assistant，含模型信息
    payload = {
        "messages": [
            {"role": "user", "content": "hello world", "timestamp": f"{day}T10:00:00", "model": "gpt-4o"},
            {"role": "assistant", "content": "你好世界，这是一段较长的回复内容用于估算", "timestamp": f"{day}T10:00:05", "model": "gpt-4o"},
        ]
    }
    p = os.path.join(tool_dir, "session.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    cfg = {
        "data_root": root,
        "ai_sessions": {
            "enabled": True,
            "token_estimation": True,
            "costs": {"enabled": True, "model_pricing": {"gpt-4o": [5, 15]}},
            "web_ai": {"enabled": False},
            "paths": {"opencode": [tool_dir]},
        },
    }
    # 直接用 ai_sessions 的内部路径解析：需让 _config_paths 读取到我们的自定义路径
    # 简化：直接调用 parse_file + 手动验证，再走 collect（collect 会扫描默认路径，需 monkeypatch _config_paths）
    orig = ai_sessions._config_paths
    ai_sessions._config_paths = lambda c: {"opencode": [tool_dir]}
    try:
        result = ai_sessions.collect(day, cfg, web_visits=None)
    finally:
        ai_sessions._config_paths = orig
    assert result["found"] is True
    assert result["total"]["turns"] == 2
    assert result["total"]["rounds"] >= 1
    assert result["total"]["tokens_total"] > 0
    assert result["total"]["cost_total"] > 0
    print("  [PASS] collect_synthetic_file")


def test_parse_file_bad_and_jsonl(tmp_path):
    # 坏 JSON
    bad = tmp_path / "bad.json"
    bad.write_text("{bad json", encoding="utf-8")
    assert ai_sessions.parse_file(str(bad)) == []
    # jsonl
    jl = tmp_path / "a.jsonl"
    jl.write_text(json.dumps({"role": "user", "content": "hi", "timestamp": "2099-01-01T10:00:00"}) + "\n", encoding="utf-8")
    out = ai_sessions.parse_file(str(jl))
    assert len(out) >= 1
    print("  [PASS] parse_file_variants")


def test_collect_web_ai_scalar_tolerated(tmp_path):
    """ai_sessions.web_ai 配成布尔/标量不得崩溃（此前非 dict 会 AttributeError）。"""
    paths = {"t": [str(tmp_path / "nope")]}  # 不存在的目录：不扫真实会话路径
    visits = [{"domain": "chatgpt.com", "url": "https://chatgpt.com/c/abcdef123456",
               "time": "2026-01-01T10:00:00", "title": "t"}]
    r_off = ai_sessions.collect("2026-01-01",
                                {"ai_sessions": {"enabled": True, "web_ai": False, "paths": paths}},
                                web_visits=visits)
    assert r_off["web_ai"]["found"] is False  # false → 视为关闭
    r_on = ai_sessions.collect("2026-01-01",
                               {"ai_sessions": {"enabled": True, "web_ai": True, "paths": paths}},
                               web_visits=visits)
    assert r_on["web_ai"]["found"] is True    # true → 视为开启
    print("  [PASS] collect_web_ai_scalar_tolerated")
