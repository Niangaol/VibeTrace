# -*- coding: utf-8 -*-
"""tests/unit/test_dsh_parser.py — DSH 会话解析（zstd + user/assistant message + usage）。

DSH 会话存于 ~/.dsh/sessions/<proj>/<sid>/session.jsonl.zstd（Zstandard 压缩的 JSONL）：
- request/header 行提供 provider/model 上下文
- user/message / assistant/message 行的 data 里含 role/content，assistant 含
  data.usage.inputTokens/outputTokens（真实 token 用量）
解析后消息与 _parse_pi_file / parse_file 同构（含 usage 字段）。
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import ai_sessions  # noqa: E402

_HAS_ZSTD = ai_sessions._HAS_ZSTD


def _write_zstd_fixture(root: str, name: str, lines: list[dict]) -> str:
    """把 JSONL 行列表 zstd 压缩写入 <root>/<name>，返回文件路径。"""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, name)
    raw = "\n".join(json.dumps(o, ensure_ascii=False) for o in lines) + "\n"
    if _HAS_ZSTD:
        import zstandard
        with zstandard.open(path, "wt", encoding="utf-8") as fh:
            fh.write(raw)
    else:
        # 无 zstandard 时写纯文本（测试 _parse_dsh_file 的降级路径不受影响）
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
    return path


def _dsh_session(day: str, cwd: str = "/mnt/d/VibeTrace", sid: str = "session-test-0001") -> list[dict]:
    """构造一个含 request/header + user + assistant（带 usage）的 DSH 会话。"""
    def ms(t: str) -> int:
        return int(__import__("datetime").datetime.fromisoformat(t).timestamp() * 1000)
    return [
        {"type": "session", "version": 0, "id": sid,
         "createdAt": ms(f"{day}T08:00:00"), "cwd": cwd},
        {"type": "request/header", "seq": 1, "time": ms(f"{day}T08:00:01"),
         "data": {"header": {"config": {"provider": "opencode",
                                        "model": "deepseek-v4-flash-free"}}}},
        {"type": "user/message", "seq": 2, "time": ms(f"{day}T08:00:02"),
         "data": {"role": "user", "content": [{"type": "text", "text": "写一个函数"}]}},
        {"type": "assistant/message", "seq": 3, "time": ms(f"{day}T08:00:05"),
         "data": {"turn": 1, "step": 1,
                  "message": {"role": "assistant",
                              "content": [{"type": "text", "text": "def f():\n    pass"}]},
                  "usage": {"inputTokens": 2202, "outputTokens": 140}}},
        {"type": "tool/call", "seq": 4, "time": ms(f"{day}T08:00:06"),
         "data": {"tool": "bash", "input": "echo hi"}},
        {"type": "assistant/message", "seq": 5, "time": ms(f"{day}T08:00:09"),
         "data": {"turn": 1, "step": 2,
                  "message": {"role": "assistant",
                              "content": [{"type": "text", "text": "完成"}]},
                  "usage": {"inputTokens": 3000, "outputTokens": 30}}},
    ]


def test_walk_dsh_files_only_zstd(tmp_path):
    """_walk_dsh_files 只返回 session.jsonl.zstd，忽略其他 json。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    root = str(tmp_path / "dsh")
    os.makedirs(os.path.join(root, "sess", "s1"))
    _write_zstd_fixture(os.path.join(root, "sess", "s1"), "session.jsonl.zstd", [])
    # 干扰文件：普通 json / 其他 zstd / 非会话文件
    with open(os.path.join(root, "sess", "other.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(root, "sess", "data.ndjson"), "w", encoding="utf-8") as f:
        f.write("{}\n")
    files = ai_sessions._walk_dsh_files([root])
    assert len(files) == 1, f"应只找到 session.jsonl.zstd，实际 {len(files)}: {files}"
    assert files[0].endswith("session.jsonl.zstd")
    print("  [PASS] walk_dsh_files_only_zstd")


def test_parse_dsh_basic_session(tmp_path):
    """_parse_dsh_file 解析 session/user/assistant + request/header 模型上下文。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    day = "2026-01-01"
    path = _write_zstd_fixture(str(tmp_path), "session.jsonl.zstd", _dsh_session(day))
    msgs = ai_sessions._parse_dsh_file(path, "")
    assert len(msgs) == 3, f"应解析 3 条消息（2 user/assistant 各 1 + 1 assistant），实际 {len(msgs)}"
    # 顺序：user, assistant, assistant
    assert msgs[0]["role"] == "user"
    assert "写一个函数" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["model"] == "deepseek-v4-flash-free", f"模型应来自 request/header，实际 {msgs[1]['model']}"
    assert msgs[1]["project"] == "VibeTrace"
    assert msgs[1]["conv_id"] == "session-test-0001"
    assert msgs[1]["timestamp"].startswith(day)
    print("  [PASS] parse_dsh_basic_session")


def test_parse_dsh_usage_normalized(tmp_path):
    """assistant 的 data.usage.inputTokens/outputTokens 归一化为 input_tokens/output_tokens。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    day = "2026-01-02"
    path = _write_zstd_fixture(str(tmp_path), "session.jsonl.zstd", _dsh_session(day))
    msgs = ai_sessions._parse_dsh_file(path, "")
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant) == 2
    assert assistant[0]["usage"] == {"input_tokens": 2202, "output_tokens": 140}
    assert assistant[1]["usage"] == {"input_tokens": 3000, "output_tokens": 30}
    # _message_usage 能识别归一化后的键（Phase 1+2 起返回互斥 4 元组）
    assert ai_sessions._message_usage(assistant[0]) == (2202, 140, 0, 0)
    print("  [PASS] parse_dsh_usage_normalized")


def test_parse_dsh_model_context_switch(tmp_path):
    """多个 request/header（不同 model）时，assistant/message 关联到各自生效的 model。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    day = "2026-01-03"
    def ms(t: str) -> int:
        return int(__import__("datetime").datetime.fromisoformat(t).timestamp() * 1000)
    lines = [
        {"type": "session", "version": 0, "id": "sid1", "createdAt": ms(f"{day}T08:00:00"), "cwd": "/proj"},
        {"type": "request/header", "seq": 1, "time": ms(f"{day}T08:00:01"),
         "data": {"header": {"config": {"provider": "opencode", "model": "model-a"}}}},
        {"type": "assistant/message", "seq": 2, "time": ms(f"{day}T08:00:02"),
         "data": {"message": {"role": "assistant", "content": "a1"},
                  "usage": {"inputTokens": 100, "outputTokens": 10}}},
        {"type": "request/header", "seq": 3, "time": ms(f"{day}T08:01:00"),
         "data": {"header": {"config": {"provider": "opencode", "model": "model-b"}}}},
        {"type": "assistant/message", "seq": 4, "time": ms(f"{day}T08:01:01"),
         "data": {"message": {"role": "assistant", "content": "b1"},
                  "usage": {"inputTokens": 200, "outputTokens": 20}}},
    ]
    path = _write_zstd_fixture(str(tmp_path), "session.jsonl.zstd", lines)
    msgs = ai_sessions._parse_dsh_file(path, "")
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistants) == 2
    assert assistants[0]["model"] == "model-a"
    assert assistants[1]["model"] == "model-b"
    print("  [PASS] parse_dsh_model_context_switch")


def test_parse_dsh_bad_lines_skipped(tmp_path):
    """损坏 JSON 行跳过，不抛异常。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    day = "2026-01-04"
    import zstandard
    lines = _dsh_session(day)
    # 中间插入一条损坏 JSON 行
    raw = ""
    for i, o in enumerate(lines):
        if i == 2:
            raw += "{bad json\n"
        else:
            raw += json.dumps(o) + "\n"
    path = os.path.join(str(tmp_path), "session.jsonl.zstd")
    os.makedirs(str(tmp_path), exist_ok=True)
    with zstandard.open(path, "wt", encoding="utf-8") as fh:
        fh.write(raw)
    msgs = ai_sessions._parse_dsh_file(path, "")  # 不应抛
    assert isinstance(msgs, list)
    assert len(msgs) >= 2  # 坏行前后仍解析出消息
    print("  [PASS] parse_dsh_bad_lines_skipped")


def test_parse_dsh_no_zstd_module(monkeypatch, tmp_path):
    """zstandard 缺失时 _parse_dsh_file 返回 []，不抛。"""
    day = "2026-01-05"
    path = _write_zstd_fixture(str(tmp_path), "session.jsonl.zstd", _dsh_session(day))
    monkeypatch.setattr(ai_sessions, "_HAS_ZSTD", False)
    assert ai_sessions._parse_dsh_file(path, "") == []
    print("  [PASS] parse_dsh_no_zstd_module")


def test_collect_dsh_integration(tmp_path):
    """collect 通过 ai_sessions.paths.dsh 走 DSH 解析，token 用真实 usage。"""
    if not _HAS_ZSTD:
        pytest.skip("zstandard 未安装")
    day = "2026-01-06"
    dsh_root = str(tmp_path / "dsh")
    _write_zstd_fixture(os.path.join(dsh_root, "proj", "s1"), "session.jsonl.zstd",
                        _dsh_session(day))
    cfg = {"ai_sessions": {"enabled": True, "paths": {"dsh": [dsh_root]}}}
    data = ai_sessions.collect(day, cfg)
    tools = data["tools"]
    assert "dsh" in tools, f"dsh 应在 tools 中，实际 {list(tools)}"
    st = tools["dsh"]
    # 1 user（内容估算）+ 2 assistant（真实 usage）
    assert st["turns"] == 3
    assert st["tokens_from_usage"] == 2
    assert st["tokens_in"] == 5202 + ai_sessions.estimate_tokens_weighted("写一个函数")
    assert st["tokens_out"] == 170  # 140 + 30
    by_model = st["by_model"]
    # user + 2 assistant 均归属当前模型（user 也按该模型计一轮）
    assert by_model["deepseek-v4-flash-free"]["turns"] == 3
    print("  [PASS] collect_dsh_integration")


def test_message_usage_camelcase_and_pi_keys():
    """_message_usage 识别 pi 的 input/output 与 dsh 的 inputTokens/outputTokens。"""
    assert ai_sessions._message_usage({"usage": {"input": 8064, "output": 182}}) == (8064, 182, 0, 0)
    assert ai_sessions._message_usage({"usage": {"inputTokens": 13154, "outputTokens": 121}}) == (13154, 121, 0, 0)
    # totalTokens 兜底：in/out 全缺时输出侧 0
    assert ai_sessions._message_usage({"usage": {"totalTokens": 8246}}) == (8246, 0, 0, 0)
    # 顶层平铺 pi 键
    assert ai_sessions._message_usage({"input": 5, "output": 3}) == (5, 3, 0, 0)
    print("  [PASS] message_usage_camelcase_and_pi_keys")
