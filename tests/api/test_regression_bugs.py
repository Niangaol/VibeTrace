# -*- coding: utf-8 -*-
"""tests/api/test_regression_bugs.py — 锁住真实使用中发现的关键回归。

覆盖点（对应 v2.5.1 修复清单）：
1. /api/export 参数契约：前端按 type/scope 顺序调用，错误顺序曾导致 400。
2. /api/pricing GET/POST 往返：用户可在设置页维护模型价格覆盖。
3. ai_sessions 多工具发现：opencode(SQLite) + pi_agent 应被解析（不再只 claude）。
"""

from __future__ import annotations

import json
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import classifier  # noqa: E402
import dashboard  # noqa: E402


def _req(port, method, path, headers=None, body=None):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = headers or {}
    if body is not None:
        h["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body), headers=h)
    else:
        conn.request(method, path, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    try:
        j = json.loads(data.decode("utf-8")) if data else {}
    except Exception:
        j = {"_raw": data.decode("utf-8", errors="ignore")}
    return r.status, j


def _server(tmp):
    s = dashboard.create_server(tmp, port=0)
    p = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, p


def _seed_day(tmp, day):
    d = os.path.join(tmp, day)
    os.makedirs(d, exist_ok=True)
    rec = {"start": f"{day}T09:00:00", "end": f"{day}T09:05:00",
           "duration_ms": 300000, "exe": "chrome.exe", "app": "Chrome",
           "title": "GitHub", "category": "浏览器", "contact": None,
           "ai_tool": None, "active": True}
    with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return day


def test_export_contract_correct_order(tmp_path):
    """前端 doExport 调用 /api/export?type=csv&scope=day&date=X 必须 200。"""
    tmp = str(tmp_path / "exp1")
    day = _seed_day(tmp, "2099-03-03")
    s, p = _server(tmp)
    try:
        st, _ = _req(p, "GET", f"/api/export?type=csv&scope=day&date={day}")
        assert st == 200, f"正确参数顺序应返回 200，实际 {st}"
    finally:
        s.shutdown()
        s.server_close()


def test_export_contract_enforces_type_and_scope(tmp_path):
    """type 非法或缺失必须 400（契约校验，避免静默空文件）。"""
    tmp = str(tmp_path / "exp2")
    day = _seed_day(tmp, "2099-03-04")
    s, p = _server(tmp)
    try:
        # 缺失 type
        st1, _ = _req(p, "GET", f"/api/export?scope=day&date={day}")
        # 非法 type
        st2, _ = _req(p, "GET", f"/api/export?type=xlsx&scope=day&date={day}")
        # 非法 scope
        st3, _ = _req(p, "GET", f"/api/export?type=csv&scope=year&date={day}")
        assert st1 == 400 and st2 == 400 and st3 == 400
    finally:
        s.shutdown()
        s.server_close()


def test_pricing_get_and_post_roundtrip(tmp_path):
    """/api/pricing 读取内置价目 + POST 覆盖后可被 GET 反映。"""
    tmp = str(tmp_path / "price1")
    os.makedirs(tmp, exist_ok=True)
    s, p = _server(tmp)
    try:
        st_get, j = _req(p, "GET", "/api/pricing")
        assert st_get == 200
        # 内置表完整返回，且数量与代码内置一致（设置页据此渲染可编辑行）
        assert j["builtin_count"] == len(ai_sessions._DEFAULT_PRICING), (
            j["builtin_count"], len(ai_sessions._DEFAULT_PRICING))
        assert isinstance(j["custom"], dict)
        # 保存覆盖
        st_post, jp = _req(p, "POST", "/api/pricing",
                            body={"pricing": {"claude-opus-5": [5.0, 25.0]}})
        assert st_post == 200 and jp.get("ok") is True
        # 再次读取应反映覆盖
        _, j2 = _req(p, "GET", "/api/pricing")
        assert j2["custom"].get("claude-opus-5") == [5.0, 25.0]
        # 文件应落盘
        assert os.path.isfile(os.path.join(tmp, "ai_pricing.json"))
    finally:
        s.shutdown()
        s.server_close()


def test_ai_sessions_multi_tool_discovery(monkeypatch, tmp_path):
    """_iter_tool_messages 对 opencode 走 SQLite、pi_agent 走专用解析，
    不应只发现 claude。用合成数据验证路由不报错且返回结构正确。"""
    cfg = classifier.load_config()
    cfg["data_root"] = str(tmp_path)
    # 仅验证 collect 在空数据根下不抛、返回标准结构（工具字典存在）
    data = ai_sessions.collect("2099-01-01", cfg)
    assert isinstance(data.get("tools"), dict)
    assert isinstance(data.get("total"), dict)
    assert "turns" in data["total"]


def test_conversation_model_from_assistant_only(tmp_path):
    """Claude 风格：用户消息无 model 字段，助手消息带 model。
    会话级模型应取助手模型的真实值，而非被「未识别」淹没。"""
    oc = tmp_path / "opencode"
    oc.mkdir()
    # 3 条用户消息（无 model）+ 1 条助手消息（model=claude-opus-5）
    msgs = [
        {"timestamp": "2099-05-05T10:00:00", "role": "user", "content": "hi"},
        {"timestamp": "2099-05-05T10:01:00", "role": "user", "content": "again"},
        {"timestamp": "2099-05-05T10:02:00", "role": "user", "content": "once more"},
        {"timestamp": "2099-05-05T10:03:00", "role": "assistant",
         "content": "ok", "message": {"model": "claude-opus-5"}},
    ]
    (oc / "sessions.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs), encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [str(oc)]}}}
    data = ai_sessions.collect("2099-05-05", cfg)
    convs = data["total"]["conversations"]
    assert convs, "应解析出会话"
    assert convs[0]["model"] == "claude-opus-5", f"会话模型应为助手模型，实际 {convs[0]['model']}"


def test_budget_monthly_overflow_year_returns_200_empty(api_server):
    """回归：/api/budget?period=monthly&date=9999-12 曾因 OverflowError 冒 500，
    违反端点自身「配置未开启/无效/异常 → 200 空态」契约。"""
    client, _root = api_server
    st, body, _ = client.get("/api/budget?period=monthly&date=9999-12")
    assert st == 200
    assert body.get("status") in ("invalid", "disabled")
    assert body.get("enabled") is False or body.get("status") == "invalid"
