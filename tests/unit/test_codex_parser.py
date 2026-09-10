# -*- coding: utf-8 -*-
"""tests/unit/test_codex_parser.py — Codex CLI rollout 专用解析。

rollout 格式（~/.codex/sessions/**/*.jsonl）：每行 {timestamp(ISO-UTC-Z), type, payload}：
- session_meta → cwd/id；turn_context → model 上下文回填；
- response_item(payload.type=message) → role/content（developer 跳过）；
- event_msg(payload.type=token_count) → info.last_token_usage 单次增量，
  累加归因到最近一条 assistant 消息（total_token_usage 为累计、不可求和）。
时间戳统一转本地时区后输出（文件内为 UTC）。
"""

from __future__ import annotations

import datetime
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402

_DAY = "2026-01-01"


def _iso_utc(h: int, m: int, s: int = 0) -> str:
    """把本地当日时刻转成 rollout 所用的 UTC ISO-Z 字符串（任意时区下测试自洽）。"""
    dt = datetime.datetime.fromisoformat(f"{_DAY}T{h:02d}:{m:02d}:{s:02d}").astimezone(
        datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rollout_lines() -> list[dict]:
    return [
        {"timestamp": _iso_utc(9, 59), "type": "session_meta",
         "payload": {"id": "sess-abc", "session_id": "sess-abc", "cwd": "D:/proj/codexdemo"}},
        {"timestamp": _iso_utc(10, 0), "type": "turn_context",
         "payload": {"cwd": "D:/proj/codexdemo", "model": "deepseek-v4-flash"}},
        {"timestamp": _iso_utc(10, 0, 1), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "帮我写函数"}]}},
        {"timestamp": _iso_utc(10, 0, 5), "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "phase": "final",
                     "content": [{"type": "output_text", "text": "def f(): pass"}]}},
        {"timestamp": _iso_utc(10, 0, 6), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 10},
                              "last_token_usage": {"input_tokens": 100, "output_tokens": 10}}}},
        {"timestamp": _iso_utc(10, 1), "type": "response_item",
         "payload": {"type": "message", "role": "developer",
                     "content": [{"type": "input_text", "text": "<app-context> 注入"}]}},
        {"timestamp": _iso_utc(10, 1, 10), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "完成"}]}},
        {"timestamp": _iso_utc(10, 1, 11), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"last_token_usage": {"input_tokens": 50, "output_tokens": 5}}}},
    ]


def _write_rollout(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for o in _rollout_lines():
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    return path


def test_parse_codex_basic(tmp_path):
    """消息提取/developer 跳过/模型回填/本地时间戳/项目与会话标识。"""
    path = _write_rollout(str(tmp_path / "rollout-2026.jsonl"))
    msgs = ai_sessions._parse_codex_file(path)
    assert len(msgs) == 3, f"user+2 assistant 应 3 条（developer 跳过），实际 {len(msgs)}"
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    # 模型：assistant 与 user 均来自 turn_context 回填（user 也回填，消除
    # 「未识别」零费用桶——Phase 1+2 起估算 token 也按真实单价计成本）
    assert msgs[0]["model"] == "deepseek-v4-flash"
    assert msgs[1]["model"] == "deepseek-v4-flash"
    assert msgs[2]["model"] == "deepseek-v4-flash"
    # UTC → 本地：本地时区下当日时刻，前缀必为查询日
    assert all(m["timestamp"] and m["timestamp"].startswith(_DAY) for m in msgs)
    assert msgs[1]["project"] == "codexdemo"
    assert msgs[1]["conv_id"] == "sess-abc"


def test_parse_codex_usage_attribution(tmp_path):
    """token_count 增量归因到最近 assistant：第一条 100/10、第二条 50/5（不重复累计）。"""
    path = _write_rollout(str(tmp_path / "rollout-2026.jsonl"))
    msgs = ai_sessions._parse_codex_file(path)
    assts = [m for m in msgs if m["role"] == "assistant"]
    assert assts[0]["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert assts[1]["usage"] == {"input_tokens": 50, "output_tokens": 5}
    # _message_usage 能读归一化结果（Phase 1+2 起返回互斥 4 元组，无缓存记 0）
    assert ai_sessions._message_usage(assts[0]) == (100, 10, 0, 0)


def test_collect_codex(tmp_path):
    """collect 走 codex paths：真实 usage 优先（tokens_from_usage=2），成本出数。"""
    sdir = tmp_path / "sessions" / "2026" / "01" / "01"
    _write_rollout(str(sdir / "rollout-2026-01-01T10-00-00-x.jsonl"))
    cfg = {"ai_sessions": {"enabled": True, "paths": {"codex": [str(tmp_path / "sessions")]}}}
    ai_sessions.invalidate_collect_cache()
    data = ai_sessions.collect(_DAY, cfg)
    assert "codex" in data["tools"], f"codex 应在 tools 中，实际 {list(data['tools'])}"
    st = data["tools"]["codex"]
    assert st["turns"] == 3
    assert st["tokens_from_usage"] == 2  # 两条 assistant 均带真实 usage
    # user 消息无 usage → 加权估算（"帮我写函数" 5 个 CJK ≈ 5）；assistant 用真实 150
    assert st["tokens_in"] == 155 and st["tokens_out"] == 15
    assert st["cost_total"] > 0  # deepseek-v4-flash 命中内置定价
    assert st["rounds"] == 1


def test_codex_utc_to_local_day_boundary(tmp_path):
    """UTC 时间戳须转本地再匹配日期：本地凌晨的消息不得因 UTC 是前一日而丢失。"""
    lines = [
        {"timestamp": _iso_utc(0, 30), "type": "session_meta",
         "payload": {"id": "s2", "cwd": "D:/proj/x"}},
        {"timestamp": _iso_utc(0, 30, 30), "type": "turn_context",
         "payload": {"model": "glm-5"}},
        {"timestamp": _iso_utc(0, 31), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "早"}]}},
    ]
    sdir = tmp_path / "b" / "sessions"
    os.makedirs(sdir)
    with open(sdir / "rollout-b.jsonl", "w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    msgs = ai_sessions._parse_codex_file(str(sdir / "rollout-b.jsonl"))
    assert len(msgs) == 1 and msgs[0]["timestamp"].startswith(_DAY)
    assert msgs[0]["model"] == "glm-5"


def test_codex_bad_lines_skipped(tmp_path):
    """损坏 JSON 行跳过不抛；空 content 的 message 跳过。"""
    sdir = tmp_path / "c" / "sessions"
    os.makedirs(sdir)
    raw = "{bad json\n" + "\n".join(json.dumps(o) for o in _rollout_lines()) + "\n"
    with open(sdir / "rollout-c.jsonl", "w", encoding="utf-8") as fh:
        fh.write(raw)
    msgs = ai_sessions._parse_codex_file(str(sdir / "rollout-c.jsonl"))
    assert len(msgs) == 3  # 坏行只影响自身
