# -*- coding: utf-8 -*-
"""tests/unit/test_token_cache_fixes.py — token 统计修复 + 缓存计价 新行为测试。

覆盖本轮改造的五块新行为：
1. Claude Code 信封修复：真实 JSONL 行的 content/usage/model 都能取到
   （外层 {"type":"assistant","message":{...}} 的嵌套结构）；
2. zcode/opencode db usage 注入：message.data 的 tokens 为**包含式**口径
   （input 含 cache.read），拆成互斥四元组 fresh/cache_read/cache_write；
3. Codex token_count：cached_input_tokens 从 input 拆出；user 消息回填模型；
5. collect 新字段：tokens_input_fresh/tokens_cache_read/tokens_cache_write
   全链路透传（total/by_model/by_project/会话明细），估算路径三分项为 0；
6. 缓存计价数学：输入侧三档（新鲜/缓存读/缓存写）分别乘价再相加。

口径说明（帮助理解断言值）：tokens_in = 新鲜输入 + 缓存读 + 缓存写
（三者互斥不重叠），成本按各档单价分别计价后求和，避免重复收费。
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Claude Code 信封修复（parse_file 通用解析 + collect 端到端）
# ---------------------------------------------------------------------------
_CLAUDE_DAY = "2026-09-08"

# 真实 Claude Code JSONL 行（外层信封 type/timestamp，内层 message 才是消息本体）
_CLAUDE_ASSISTANT_LINE = {
    "type": "assistant",
    "timestamp": f"{_CLAUDE_DAY}T10:00:00",
    "message": {
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": "你好"}],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
        },
    },
}


def test_claude_code_envelope_extract_content_usage_model(tmp_path):
    """信封行的 content=你好、usage=(10,20,900,50)、model=claude-sonnet-4-5。"""
    f = tmp_path / "cc.jsonl"
    f.write_text(json.dumps(_CLAUDE_ASSISTANT_LINE) + "\n", encoding="utf-8")
    msgs = ai_sessions.parse_file(str(f))
    assert len(msgs) == 1, f"应解析出 1 条消息，实际 {len(msgs)}"
    m = msgs[0]
    # 内容：从内层 message.content 的文本块列表里取出（修复前是空串）
    assert ai_sessions._message_content(m) == "你好"
    # usage：从内层 message.usage 取互斥四元组 (新鲜进, 出, 缓存读, 缓存写)
    assert ai_sessions._message_usage(m) == (10, 20, 900, 50)
    # 模型：从内层 message.model 取（修复前会退到内容正则 → 未识别）
    assert ai_sessions._message_model(m) == "claude-sonnet-4-5"
    assert ai_sessions._message_role(m) == "assistant"
    print("  [PASS] claude_code_envelope_extract")


def test_collect_claude_code_envelope_tokens_and_cache_fields(tmp_path):
    """端到端 collect：tokens_in=960（10+900+50），三分项/会话明细/成本全链路出数。"""
    d = tmp_path / "claude"
    d.mkdir()
    (d / "proj.jsonl").write_text(json.dumps(_CLAUDE_ASSISTANT_LINE) + "\n",
                                  encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"claude": [str(d)]}}}
    ai_sessions.invalidate_collect_cache()
    total = ai_sessions.collect(_CLAUDE_DAY, cfg)["total"]
    assert total["turns"] == 1
    # 唯一一条 assistant 消息带真实 usage → tokens_from_usage 记 1
    assert total["tokens_from_usage"] == 1
    # tokens_in 计入缓存：10 新鲜 + 900 缓存读 + 50 缓存写 = 960
    assert total["tokens_in"] == 960, f"tokens_in 应为 960，实际 {total['tokens_in']}"
    assert total["tokens_out"] == 20
    # 输入侧三分项（互斥）
    assert total["tokens_input_fresh"] == 10
    assert total["tokens_cache_read"] == 900
    assert total["tokens_cache_write"] == 50
    # by_model 维度透传三分项
    bm = total["by_model"]["claude-sonnet-4-5"]
    assert (bm["tokens_in"], bm["tokens_input_fresh"],
            bm["tokens_cache_read"], bm["tokens_cache_write"]) == (960, 10, 900, 50)
    # 会话明细透传三分项
    conv = total["conversations"][0]
    assert (conv["tokens_input_fresh"], conv["tokens_cache_read"],
            conv["tokens_cache_write"]) == (10, 900, 50)
    # 项目维度（无项目字段 → 未识别桶）也带三分项
    bp = total["by_project"]["未识别"]
    assert (bp["tokens_input_fresh"], bp["tokens_cache_read"],
            bp["tokens_cache_write"]) == (10, 900, 50)
    # 成本按缓存档分档计价：10×3 + 900×0.3 + 50×3.75（/1e6）+ 20×15/1e6
    assert abs(total["cost_in"] - 0.0004875) < 1e-12
    assert abs(total["cost_total"] - 0.0007875) < 1e-12
    print("  [PASS] collect_claude_code_envelope")


# ---------------------------------------------------------------------------
# 5. collect 新字段：空态结构与估算路径
# ---------------------------------------------------------------------------
def test_empty_stats_carry_cache_fields():
    """_empty_tool_stats/_empty_total 都带缓存三分项键，初始值为 0。"""
    for stats in (ai_sessions._empty_tool_stats(), ai_sessions._empty_total()):
        assert stats["tokens_input_fresh"] == 0
        assert stats["tokens_cache_read"] == 0
        assert stats["tokens_cache_write"] == 0
    print("  [PASS] empty_stats_cache_fields")


def test_estimation_path_cache_fields_zero_but_tokens_in_estimated(tmp_path):
    """无 usage 的消息走估算：tokens_in 仍有估算值，但三分项（无缓存概念）为 0。"""
    d = tmp_path / "sess"
    d.mkdir()
    recs = [
        {"role": "user", "content": "你好世界", "timestamp": f"{_CLAUDE_DAY}T10:00:00"},
        {"role": "assistant", "content": "回复内容", "timestamp": f"{_CLAUDE_DAY}T10:00:05"},
    ]
    (d / "a.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs),
                               encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True, "token_estimation": True,
                           "paths": {"t": [str(d)]}}}
    ai_sessions.invalidate_collect_cache()
    total = ai_sessions.collect(_CLAUDE_DAY, cfg)["total"]
    assert total["tokens_from_usage"] == 0
    # 纯 CJK 4 字 → 4 token（weighted 与 simple 同值）；user 进 / assistant 出
    assert total["tokens_in"] == 4 and total["tokens_out"] == 4
    # 估算分支没有缓存概念：三分项必须为 0
    assert total["tokens_input_fresh"] == 0
    assert total["tokens_cache_read"] == 0
    assert total["tokens_cache_write"] == 0
    print("  [PASS] estimation_path_cache_fields_zero")


# ---------------------------------------------------------------------------
# 2. zcode/opencode db usage 注入（包含式拆分）
# ---------------------------------------------------------------------------
_DB_DAY = "2026-06-01"


def _mk_usage_db(db_dir: str) -> str:
    """构造带真实 tokens 用量的 zcode db.sqlite（message.data 含包含式 tokens）。"""
    os.makedirs(db_dir, exist_ok=True)
    path = os.path.join(db_dir, "db.sqlite")
    dt = datetime.datetime.fromisoformat(f"{_DB_DAY}T10:00:00")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT);
        """
    )
    conn.execute("INSERT INTO session VALUES (?, ?)", ("s1", "D:/work/vibetrace"))
    # 本机实测口径：total = input + output，且 input **包含** cache.read
    conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                 ("m1", "s1", int(dt.timestamp() * 1000),
                  json.dumps({"role": "assistant", "modelID": "glm-5.3",
                              "tokens": {"total": 61128, "input": 60934, "output": 194,
                                         "cache": {"read": 58176, "write": 0}}})))
    conn.commit()
    conn.close()
    return path


def test_opencode_db_usage_inclusive_split(tmp_path):
    """db 的 tokens 包含式拆分：fresh=60934-58176=2758，缓存读单列，写 0。"""
    db = _mk_usage_db(str(tmp_path / "db"))
    msgs = ai_sessions._parse_opencode_db(db, _DB_DAY, immutable=False)
    assert len(msgs) == 1, f"应解析 1 条当日消息，实际 {len(msgs)}"
    m = msgs[0]
    assert m["usage"] == {"input_tokens": 2758, "output_tokens": 194,
                          "cache_read_input_tokens": 58176,
                          "cache_creation_input_tokens": 0}
    # 归一化后的 usage 能被 _message_usage 识别为互斥四元组
    assert ai_sessions._message_usage(m) == (2758, 194, 58176, 0)
    print("  [PASS] opencode_db_usage_inclusive_split")


def test_collect_zcode_db_usage_tokens(tmp_path):
    """collect 走 zcode db：tokens_in 还原包含式总量 60934，三分项正确拆分。"""
    cli = tmp_path / "cli"
    _mk_usage_db(str(cli / "db"))
    cfg = {"ai_sessions": {"enabled": True, "paths": {"zcode": [str(cli)]}}}
    ai_sessions.invalidate_collect_cache()
    st = ai_sessions.collect(_DB_DAY, cfg)["tools"]["zcode"]
    assert st["tokens_from_usage"] == 1
    assert st["tokens_in"] == 60934, f"tokens_in 应为 2758+58176=60934，实际 {st['tokens_in']}"
    assert st["tokens_out"] == 194
    assert st["tokens_input_fresh"] == 2758
    assert st["tokens_cache_read"] == 58176
    assert st["tokens_cache_write"] == 0
    print("  [PASS] collect_zcode_db_usage_tokens")


def test_opencode_usage_edges():
    """边界：无 tokens 字段不注入 usage；cache.read 超过 input 时 fresh 夹到 0。"""
    # 无 tokens → usage None（调用方回退内容估算）
    assert ai_sessions._opencode_usage({"role": "user"}) == {"usage": None}
    # 脏数据：cache.read(200) > input(100) → fresh=max(0,-100)=0，不出负数
    u = ai_sessions._opencode_usage(
        {"tokens": {"input": 100, "output": 1, "cache": {"read": 200}}})["usage"]
    assert u["input_tokens"] == 0 and u["cache_read_input_tokens"] == 200
    print("  [PASS] opencode_usage_edges")


# ---------------------------------------------------------------------------
# 3. Codex：cached 从 input 拆出 + user 消息模型回填
# ---------------------------------------------------------------------------
_CODEX_DAY = "2026-01-01"


def _iso_utc(h: int, m: int, s: int = 0) -> str:
    """把本地当日时刻转成 rollout 所用的 UTC ISO-Z 字符串（任意时区下测试自洽）。"""
    dt = datetime.datetime.fromisoformat(f"{_CODEX_DAY}T{h:02d}:{m:02d}:{s:02d}").astimezone(
        datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_rollout(path: str, lines: list[dict]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    return path


def test_codex_cached_input_split_and_user_model_backfill(tmp_path):
    """cached=800 从 input=1000 拆出；reasoning 不重复加；user 消息回填 turn_context 模型。"""
    lines = [
        {"timestamp": _iso_utc(10, 0), "type": "session_meta",
         "payload": {"id": "s-cache", "cwd": "D:/proj/cachedemo"}},
        {"timestamp": _iso_utc(10, 0, 1), "type": "turn_context",
         "payload": {"model": "gpt-5.2-codex"}},
        {"timestamp": _iso_utc(10, 0, 2), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "帮我写加法"}]}},
        {"timestamp": _iso_utc(10, 0, 5), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "def add"}]}},
        {"timestamp": _iso_utc(10, 0, 6), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"last_token_usage": {
                         "input_tokens": 1000, "cached_input_tokens": 800,
                         "output_tokens": 150, "reasoning_output_tokens": 60,
                         "total_tokens": 1150}}}},
    ]
    path = _write_rollout(str(tmp_path / "rollout-cache.jsonl"), lines)
    msgs = ai_sessions._parse_codex_file(path)
    assert len(msgs) == 2
    user, asst = msgs
    # user 消息也回填 turn_context 的模型（不再「未识别」，估算 token 也能计价）
    assert user["role"] == "user" and user["model"] == "gpt-5.2-codex"
    # cached 从 input 拆出：新鲜 200、缓存读 800；reasoning 已含在 output 内不另加
    assert asst["usage"] == {"input_tokens": 200, "output_tokens": 150,
                             "cache_read_input_tokens": 800}
    assert ai_sessions._message_usage(asst) == (200, 150, 800, 0)
    print("  [PASS] codex_cached_input_split")


def test_codex_cached_boundary_cases(tmp_path):
    """边界：cached>input 不拆（保持原值）；cached==input 则新鲜归 0、全部计缓存读。"""
    lines = [
        {"timestamp": _iso_utc(11, 0), "type": "session_meta",
         "payload": {"id": "s-edge", "cwd": "D:/proj/edge"}},
        {"timestamp": _iso_utc(11, 0, 1), "type": "turn_context",
         "payload": {"model": "gpt-5.1"}},
        {"timestamp": _iso_utc(11, 0, 2), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "a1"}]}},
        {"timestamp": _iso_utc(11, 0, 3), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"last_token_usage": {"input_tokens": 100,
                                                   "cached_input_tokens": 500,
                                                   "output_tokens": 10}}}},
        {"timestamp": _iso_utc(11, 1), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "a2"}]}},
        {"timestamp": _iso_utc(11, 1, 1), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"last_token_usage": {"input_tokens": 100,
                                                   "cached_input_tokens": 100,
                                                   "output_tokens": 5}}}},
    ]
    path = _write_rollout(str(tmp_path / "rollout-edge.jsonl"), lines)
    msgs = ai_sessions._parse_codex_file(path)
    assts = [m for m in msgs if m["role"] == "assistant"]
    # cached(500) > input(100)：非法包含关系 → 不拆，input 保持 100、无 cache_read 键
    assert assts[0]["usage"] == {"input_tokens": 100, "output_tokens": 10}
    # cached(100) == input(100)：新鲜归 0，100 全部计缓存读
    assert assts[1]["usage"] == {"input_tokens": 0, "output_tokens": 5,
                                 "cache_read_input_tokens": 100}
    assert ai_sessions._message_usage(assts[1]) == (0, 5, 100, 0)
    print("  [PASS] codex_cached_boundary")


# ---------------------------------------------------------------------------
# 6. 缓存计价数学（三分项 × 各档单价）
# ---------------------------------------------------------------------------
def test_cache_pricing_math_three_tiers(tmp_path):
    """usage=(1000,500,9000,200) × 价 (1,2,0.1,1.25)：
    c_in = 1000×1 + 9000×0.1 + 200×1.25（/1e6）= 0.00215；c_out = 0.001。"""
    d = tmp_path / "cost"
    d.mkdir()
    msg = {"role": "assistant", "model": "test-model", "content": "x",
           "timestamp": f"{_CLAUDE_DAY}T10:00:00",
           "usage": {"input_tokens": 1000, "output_tokens": 500,
                     "cache_read_input_tokens": 9000,
                     "cache_creation_input_tokens": 200}}
    (d / "m.jsonl").write_text(json.dumps(msg) + "\n", encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True,
                           "costs": {"enabled": True,
                                     "model_pricing": {"test-model": [1, 2, 0.1, 1.25]}},
                           "paths": {"t": [str(d)]}}}
    ai_sessions.invalidate_collect_cache()
    total = ai_sessions.collect(_CLAUDE_DAY, cfg)["total"]
    # token 侧：进 = 1000+9000+200 = 10200，三分项各就位
    assert total["tokens_in"] == 10200 and total["tokens_out"] == 500
    assert total["tokens_input_fresh"] == 1000
    assert total["tokens_cache_read"] == 9000
    assert total["tokens_cache_write"] == 200
    # 成本侧：三档分别计价再相加（钉到小数）
    assert abs(total["cost_in"] - 0.00215) < 1e-12, f"cost_in={total['cost_in']}"
    assert abs(total["cost_out"] - 0.001) < 1e-12, f"cost_out={total['cost_out']}"
    assert abs(total["cost_total"] - 0.00315) < 1e-12, f"cost_total={total['cost_total']}"
    # by_model 维度同样分档计价
    bm = total["by_model"]["test-model"]
    assert abs(bm["cost_in"] - 0.00215) < 1e-12
    assert abs(bm["cost_total"] - 0.00315) < 1e-12
    print("  [PASS] cache_pricing_math_three_tiers")


# ---------------------------------------------------------------------------
# 7. 内置 2 元组定价的缓存折扣比真正进入成本（v2.9.5 回归钉扎）
# ---------------------------------------------------------------------------
def test_builtin_cache_ratio_applies_to_cost(tmp_path):
    """内置 2 元组（glm-5.3 = 1.4/4.4）的缓存读按官方折扣比 25% 计费。

    回归：此前 2 元组补 (in, in) → 缓存读按全价输入计。缓存密集型工作流
    （缓存读可达输入的 98%）成本虚高数倍。现按供应商官方命中价折扣。
    """
    d = tmp_path / "ratio"
    d.mkdir()
    msg = {"role": "assistant", "model": "glm-5.3", "content": "x",
           "timestamp": f"{_CLAUDE_DAY}T10:00:00",
           "usage": {"input_tokens": 100, "output_tokens": 1000,
                     "cache_read_input_tokens": 1_000_000}}
    (d / "m.jsonl").write_text(json.dumps(msg) + "\n", encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True, "costs": {"enabled": True},
                           "paths": {"t": [str(d)]}}}
    ai_sessions.invalidate_collect_cache()
    total = ai_sessions.collect(_CLAUDE_DAY, cfg)["total"]
    # glm-5.3 官方价 (in 1.4, out 4.4, 缓存读 0.35, 缓存写 1.4) USD/百万
    exp_in = (100 * 1.4 + 1_000_000 * 0.35) / 1e6
    exp_out = 1000 * 4.4 / 1e6
    assert abs(total["cost_in"] - exp_in) < 1e-12, f"cost_in={total['cost_in']}"
    assert abs(total["cost_total"] - (exp_in + exp_out)) < 1e-12
    # 若仍按全价输入计，cost_in 会是 1.40014（> exp_in 的 0.35000014）
    assert total["cost_in"] < 0.4, "缓存读仍按输入价计费（折扣未生效）"
    print("  [PASS] builtin_cache_ratio_applies_to_cost")
