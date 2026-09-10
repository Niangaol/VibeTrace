# -*- coding: utf-8 -*-
"""tests/unit/test_ai_sessions_refined.py — Token 估算精进与真实 usage 字段优先。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


def test_weighted_estimator_buckets():
    # 空文本为 0
    assert ai_sessions.estimate_tokens_weighted("") == 0
    assert ai_sessions.estimate_tokens_weighted("  \n\t ") == 0
    # 纯 CJK：与 simple 口径一致（1 字/Token）
    assert ai_sessions.estimate_tokens_weighted("你好世界") == 4
    # 符号密集（代码/JSON）：加权口径应高于 simple 的 4字符/Token 低估
    code = "{}(){};===" * 10
    assert ai_sessions.estimate_tokens_weighted(code) > ai_sessions.estimate_tokens(code)
    # 空白密集：加权口径应低于 simple
    spaces = "a b c d e f g h i j" * 5
    assert ai_sessions.estimate_tokens_weighted(spaces) <= ai_sessions.estimate_tokens(spaces)
    print("  [PASS] weighted_estimator_buckets")


def test_message_usage_nested_and_flat():
    # 嵌套 usage（Claude Code 风格）
    m1 = {"usage": {"input_tokens": 15000, "output_tokens": 500}}
    assert ai_sessions._message_usage(m1) == (15000, 500, 0, 0)
    # prompt/completion 命名（OpenAI 风格）
    m2 = {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    assert ai_sessions._message_usage(m2) == (100, 20, 0, 0)
    # 平铺字段
    m3 = {"tokens_in": 7, "tokens_out": 3}
    assert ai_sessions._message_usage(m3) == (7, 3, 0, 0)
    # 缺失 → None；负值/非数值被忽略
    assert ai_sessions._message_usage({"role": "user", "content": "hi"}) is None
    assert ai_sessions._message_usage({"usage": {"input_tokens": -5}}) is None
    print("  [PASS] message_usage_nested_and_flat")


def _write_fixture(root: str, day: str, rows: list[dict]) -> None:
    sess_dir = os.path.join(root, "sess")
    os.makedirs(sess_dir, exist_ok=True)
    with open(os.path.join(sess_dir, "s.jsonl"), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_collect_prefers_real_usage_over_estimation(tmp_path):
    """消息带 usage 时用真实值计 token 与成本，不再按内容估算。"""
    root = str(tmp_path)
    day = "2099-08-01"
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "user",
         "content": "一句话", "model": "m1"},
        {"timestamp": f"{day}T09:01:00", "role": "assistant",
         "content": "x" * 400, "model": "m1",
         "usage": {"input_tokens": 15000, "output_tokens": 800}},
    ])
    cfg = {"ai_sessions": {"enabled": True, "paths": {"test_tool": [str(root / "sess")] if False else [os.path.join(str(root), "sess")]}}}
    data = ai_sessions.collect(day, cfg)
    total = data["total"]
    # 混合语义：assistant 带真实 usage（in=15000/out=800）；
    # user 消息无 usage → 仍按内容估算（"一句话" 3 个 CJK 字 = 3 token）
    assert total["tokens_in"] == 15003 and total["tokens_out"] == 800
    assert total["tokens_total"] == 15803
    assert total["tokens_from_usage"] == 1
    # 成本按真实 token 计（定价表无 m1 → 单价 0，仅验证结构）
    assert isinstance(total["cost_total"], float)
    print("  [PASS] collect_prefers_real_usage_over_estimation")


def test_collect_simple_mode_fallback(tmp_path):
    """token_estimation_mode=simple 回退历史口径（4 字符/Token）。"""
    root = str(tmp_path)
    day = "2099-08-02"
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "assistant",
         "content": "abcd", "model": "m1"},  # simple: 1 token
    ])
    cfg = {"ai_sessions": {"enabled": True, "token_estimation_mode": "simple",
                           "paths": {"t": [os.path.join(str(root), "sess")]}}}
    data = ai_sessions.collect(day, cfg)
    assert data["total"]["tokens_out"] == 1
    # weighted 模式下同内容：字母 4×0.25=1.0 → 也是 1，换符号内容区分
    _write_fixture(root, day + "b", [])
    print("  [PASS] collect_simple_mode_fallback")


def test_collect_weighted_mode_counts_symbols_higher(tmp_path):
    root = str(tmp_path)
    day = "2099-08-03"
    content = "{}(){};==" * 8  # 64 个符号字符
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "assistant",
         "content": content, "model": "m1"},
    ])
    cfg_w = {"ai_sessions": {"enabled": True, "token_estimation_mode": "weighted",
                             "paths": {"t": [os.path.join(str(root), "sess")]}}}
    cfg_s = dict(cfg_w, ai_sessions=dict(cfg_w["ai_sessions"], token_estimation_mode="simple"))
    w = ai_sessions.collect(day, cfg_w)["total"]["tokens_out"]
    s = ai_sessions.collect(day, cfg_s)["total"]["tokens_out"]
    assert w > s, f"符号密集文本 weighted({w}) 应高于 simple({s})"
    print("  [PASS] collect_weighted_mode_counts_symbols_higher")


def _write_pi_fixture(root: str, day: str, rows: list[dict]) -> None:
    """写 pi_agent 会话 jsonl（message.usage 为 input/output 风格）。"""
    sess_dir = os.path.join(root, "pi_sess")
    os.makedirs(sess_dir, exist_ok=True)
    with open(os.path.join(sess_dir, "p.jsonl"), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_pi_parse_preserves_message_usage(tmp_path):
    """pi 消息的 message.usage（input/output）被保留且被 _message_usage 识别。"""
    day = "2099-08-10"
    root = str(tmp_path)
    _write_pi_fixture(root, day, [
        {"type": "session", "timestamp": f"{day}T08:00:00", "cwd": "/proj"},
        {"type": "model_change", "timestamp": f"{day}T08:00:01",
         "provider": "stepplan", "modelId": "step-3.7-flash"},
        {"type": "message", "timestamp": f"{day}T08:00:02",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "hi"}],
                     "provider": "stepplan", "model": "step-3.7-flash",
                     "usage": {"input": 8064, "output": 182}}},
    ])
    msgs = ai_sessions._parse_pi_file(os.path.join(root, "pi_sess", "p.jsonl"))
    assert len(msgs) == 1
    assert msgs[0]["usage"] == {"input": 8064, "output": 182}
    assert msgs[0]["model"] == "step-3.7-flash"  # 每消息 model 优先
    assert ai_sessions._message_usage(msgs[0]) == (8064, 182, 0, 0)
    print("  [PASS] pi_parse_preserves_message_usage")


def test_pi_collect_uses_real_usage(tmp_path):
    """pi_agent 会话走 collect 时 token 用真实 message.usage，非内容估算。"""
    day = "2099-08-11"
    root = str(tmp_path)
    _write_pi_fixture(root, day, [
        {"type": "session", "timestamp": f"{day}T08:00:00", "cwd": "/proj"},
        {"type": "model_change", "timestamp": f"{day}T08:00:01",
         "provider": "stepplan", "modelId": "step-3.7-flash"},
        {"type": "message", "timestamp": f"{day}T08:00:02",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "x" * 400}],
                     "provider": "stepplan", "model": "step-3.7-flash",
                     "usage": {"input": 8064, "output": 182}}},
    ])
    cfg = {"ai_sessions": {"enabled": True,
                           "paths": {"pi_agent": [os.path.join(root, "pi_sess")]}}}
    data = ai_sessions.collect(day, cfg)
    st = data["tools"].get("pi_agent")
    assert st is not None, f"pi_agent 应在 tools 中，实际 {list(data['tools'])}"
    assert st["tokens_in"] == 8064
    assert st["tokens_out"] == 182
    assert st["tokens_from_usage"] == 1
    assert st["by_model"]["step-3.7-flash"]["turns"] == 1
    print("  [PASS] pi_collect_uses_real_usage")


def test_pi_per_message_model(tmp_path):
    """pi 每消息自带 model 时按各自 model 归属，而非全部用 model_change 上下文。"""
    day = "2099-08-12"
    root = str(tmp_path)
    _write_pi_fixture(root, day, [
        {"type": "session", "timestamp": f"{day}T08:00:00", "cwd": "/proj"},
        {"type": "model_change", "timestamp": f"{day}T08:00:01",
         "provider": "stepplan", "modelId": "ctx-model"},
        {"type": "message", "timestamp": f"{day}T08:00:02",
         "message": {"role": "assistant", "content": "a",
                     "provider": "stepplan", "model": "model-a",
                     "usage": {"input": 100, "output": 10}}},
        {"type": "message", "timestamp": f"{day}T08:00:03",
         "message": {"role": "assistant", "content": "b",
                     "provider": "stepplan", "model": "model-b",
                     "usage": {"input": 200, "output": 20}}},
    ])
    cfg = {"ai_sessions": {"enabled": True,
                           "paths": {"pi_agent": [os.path.join(root, "pi_sess")]}}}
    st = ai_sessions.collect(day, cfg)["tools"]["pi_agent"]
    assert st["by_model"]["model-a"]["turns"] == 1
    assert st["by_model"]["model-b"]["turns"] == 1
    assert "ctx-model" not in st["by_model"]  # 每消息 model 覆盖上下文
    print("  [PASS] pi_per_message_model")
