# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_contract.py — Dashboard /api/* 契约。"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import browser_history  # noqa: E402
import dashboard  # noqa: E402
import timeline  # noqa: E402


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    hdr = dict(r.getheaders())
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {"_raw": body.decode("utf-8", errors="ignore")}
    return r.status, data, hdr


def test_api_dates_and_day(tmp_path):
    tmp_root = str(tmp_path / "api1")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-02"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    with open(os.path.join(tmp_root, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T10:00:00", "end": f"{day}T10:01:00", "duration_ms": 60000, "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "开发工具", "contact": None, "ai_tool": None, "active": True}, ensure_ascii=False) + "\n")
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", "/api/dates")
        assert s == 200, f"/api/dates status {s} {d}"
        assert isinstance(d.get("dates"), list)

        s2, d2, _ = _req(port, "GET", f"/api/day?date={day}")
        assert s2 == 200, f"/api/day status {s2} {d2}"
        assert "aggregate" in d2 or "sessions" in d2 or "total" in d2
        print("  [PASS] api_dates_and_day")
    finally:
        server.shutdown()
        server.server_close()


def test_api_report_and_heatmap(tmp_path):
    tmp_root = str(tmp_path / "api2")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-03"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", f"/api/report?date={day}")
        assert s in (200, 404), f"/api/report status {s}"
        s2, d2, _ = _req(port, "GET", "/api/heatmap?days=7")
        assert s2 == 200
        assert "days" in d2
        print("  [PASS] api_report_and_heatmap")
    finally:
        server.shutdown()
        server.server_close()


def test_api_unknown_returns_404(tmp_path):
    tmp_root = str(tmp_path / "api3")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", "/api/not_exist_zzz")
        assert s == 404
        print("  [PASS] api_unknown_returns_404")
    finally:
        server.shutdown()
        server.server_close()


def test_api_insights_includes_time_saved(tmp_path):
    tmp_root = str(tmp_path / "api4")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-06"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    with open(os.path.join(tmp_root, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T10:00:00", "end": f"{day}T11:00:00", "duration_ms": 3600000, "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "AI编程", "contact": None, "ai_tool": "opencode", "active": True}, ensure_ascii=False) + "\n")
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", f"/api/insights?date={day}")
        assert s == 200, f"/api/insights status {s} {d}"
        assert "time_saved" in d, f"missing time_saved {d.keys()}"
        assert "saved_ms" in d["time_saved"]
        print("  [PASS] api_insights_includes_time_saved")
    finally:
        server.shutdown()
        server.server_close()


def test_api_heatmap_unified_metric(tmp_path):
    """热力图统一口径（v2.9.5）：概览与趋势同源，只返回 hourly_ms，不再有 tokens 变体。

    回归：此前概览走 ?tokens=1 的逐日 ai_sessions.collect（hourly_tokens，28 天），
    与趋势（hourly_ms，84 天）不一致；现统一为总活跃时长口径 + 共享响应缓存。
    """
    tmp_root = str(tmp_path / "api2b")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-03"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", "/api/heatmap?days=7")
        assert s == 200
        row = [x for x in d["days"] if x["date"] == day][0]
        assert len(row["hourly_ms"]) == 24, "行必须带 24 小时总活跃分布"
        assert "hourly_tokens" not in row, "tokens 变体已下线，不应再返回 hourly_tokens"
        # 兼容旧参数写法：tokens=1 不再改变响应结构（同口径）
        s2, d2, _ = _req(port, "GET", "/api/heatmap?days=7&tokens=1")
        assert s2 == 200 and d2["days"] == d["days"], "tokens 参数不应再影响结果"
        # 共享响应缓存命中：同键第二次请求结果一致
        s3, d3, _ = _req(port, "GET", "/api/heatmap?days=7")
        assert s3 == 200 and d3["days"] == d["days"]
        print("  [PASS] api_heatmap_unified_metric")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 通用 TTL+SWR 响应缓存（_send_json_cached）契约
# ---------------------------------------------------------------------------


def _seed_day(root, day, minutes=5):
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    rec = {"start": f"{day}T09:00:00", "end": f"{day}T09:{max(minutes, 0):02d}:00",
           "duration_ms": minutes * 60000, "exe": "chrome.exe", "app": "Chrome",
           "title": "example.com", "category": "浏览器", "contact": None,
           "ai_tool": None, "active": True}
    with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_response_cache_hit_second_request_serves_cache_without_recompute(tmp_path, monkeypatch):
    """同一 /api/urls 请求连发两次：底层 collect 只调 1 次，两次响应完全一致（命中缓存）。"""
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "cache_hit")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-02-03"
    _seed_day(tmp_root, day)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = []

    def fake_collect(date, root, config=None, **kw):
        calls.append(date)
        return {"count": 2, "total_duration_s": 120, "by_category_duration_s": {"浏览器": 120},
                "by_domain_duration_s": {"example.com": 120}, "visits": []}

    monkeypatch.setattr(browser_history, "collect", fake_collect)
    try:
        s1, d1, _ = _req(port, "GET", f"/api/urls?date={day}")
        s2, d2, _ = _req(port, "GET", f"/api/urls?date={day}")
        assert s1 == 200 and s2 == 200, f"status {s1}/{s2}"
        assert d1 == d2, f"两次响应应一致: {d1} vs {d2}"
        assert d1["count"] == 2 and d1["date"] == day
        assert len(calls) == 1, f"collect 应只被调 1 次（第二次走缓存），实际 {len(calls)} 次"
        print("  [PASS] response_cache_hit_second_request_serves_cache_without_recompute")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_response_cache_swr_stale_hit_then_background_refresh(tmp_path, monkeypatch):
    """过期缓存条目：请求立即拿到旧值，后台单飞刷新完成后缓存更新为新值、刷新标记清理。"""
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "cache_swr")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-02-04"
    _seed_day(tmp_root, day)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def fake_v1(date, root, config=None, **kw):
        return {"count": 1, "total_duration_s": 60, "by_category_duration_s": {},
                "by_domain_duration_s": {}, "visits": []}

    monkeypatch.setattr(browser_history, "collect", fake_v1)
    try:
        s1, d1, _ = _req(port, "GET", f"/api/urls?date={day}")
        assert s1 == 200 and d1["count"] == 1, f"冷态请求应写入缓存: {s1} {d1}"
        with dashboard._RESPONSE_CACHE_LOCK:
            key = next(k for k in dashboard._RESPONSE_CACHE if k[2] == "urls")
            # 手工把缓存条目 ts 改旧（等价于 TTL 过期），负载仍是 v1
            dashboard._RESPONSE_CACHE[key] = (
                time.time() - dashboard._RESPONSE_CACHE_TTL_S - 10, d1)

        def slow_v2(date, root, config=None, **kw):
            time.sleep(0.2)  # 模拟重算：确保响应 2 返回时刷新仍在途
            return {"count": 999, "total_duration_s": 999, "by_category_duration_s": {},
                    "by_domain_duration_s": {}, "visits": []}

        monkeypatch.setattr(browser_history, "collect", slow_v2)
        s2, d2, _ = _req(port, "GET", f"/api/urls?date={day}")
        assert s2 == 200, f"过期请求应回 200 旧值: {s2}"
        assert d2["count"] == 1, f"SWR 应立即回旧值(v1)，实际 {d2}"
        with dashboard._RESPONSE_CACHE_LOCK:
            assert key in dashboard._RESPONSE_CACHE_REFRESHING, "应置位单飞刷新标记"

        # 等后台刷新完成（上限 5s）
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with dashboard._RESPONSE_CACHE_LOCK:
                if key not in dashboard._RESPONSE_CACHE_REFRESHING:
                    break
            time.sleep(0.02)
        with dashboard._RESPONSE_CACHE_LOCK:
            assert key not in dashboard._RESPONSE_CACHE_REFRESHING, "刷新后标记应被清理"
            refreshed = dashboard._RESPONSE_CACHE.get(key)
        assert refreshed is not None and refreshed[1]["count"] == 999, \
            f"后台刷新应把缓存更新为新值，实际 {refreshed}"
        s3, d3, _ = _req(port, "GET", f"/api/urls?date={day}")
        assert s3 == 200 and d3["count"] == 999, f"刷新后请求应拿新值: {s3} {d3}"
        print("  [PASS] response_cache_swr_stale_hit_then_background_refresh")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_response_cache_error_response_not_cached(tmp_path, monkeypatch):
    """/api/ai-sessions 底层 collect 抛异常 → 500，且该 key 不进缓存（错误响应不入缓存）。"""
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "cache_err")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-02-05"
    _seed_day(tmp_root, day)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def boom(date, config, web_visits=None):
        raise RuntimeError("boom-cache-test")

    monkeypatch.setattr(ai_sessions, "collect", boom)
    try:
        s, d, _ = _req(port, "GET", f"/api/ai-sessions?date={day}")
        assert s == 500, f"collect 抛异常应回 500: {s} {d}"
        assert "error" in d, f"错误响应应带 error 字段: {d}"
        with dashboard._RESPONSE_CACHE_LOCK:
            leaked = [k for k in dashboard._RESPONSE_CACHE if k[2] == "ai-sessions"]
        assert not leaked, f"错误响应不应入缓存，泄漏 key: {leaked}"
        print("  [PASS] response_cache_error_response_not_cached")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_api_days_invalid_n_falls_back_to_14(tmp_path):
    """/api/days?n=abc：非法 n 回退 14（回归 B1，不再 500），结果与默认 n=14 一致。"""
    tmp_root = str(tmp_path / "days_b1")
    os.makedirs(tmp_root, exist_ok=True)
    for i in range(1, 16):  # 造 15 天数据，n=14 才能截出 14 条
        _seed_day(tmp_root, f"2099-01-{i:02d}")
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", "/api/days?n=abc")
        assert s == 200, f"?n=abc 应回 200 而非 500: {s} {d}"
        assert len(d["days"]) == 14, f"?n=abc 应按 14 天处理，实际 {len(d['days'])} 天"
        s2, d2, _ = _req(port, "GET", "/api/days")
        assert s2 == 200 and d2["days"] == d["days"], "n=abc 应与默认 n=14 结果完全一致"
        s3, d3, _ = _req(port, "GET", "/api/days?n=5")
        assert s3 == 200 and len(d3["days"]) == 5, "合法 n=5 仍应生效"
        print("  [PASS] api_days_invalid_n_falls_back_to_14")
    finally:
        server.shutdown()
        server.server_close()


def test_api_timeline_cache_key_isolated_by_project(tmp_path, monkeypatch):
    """timeline 缓存键含 project：alpha 命中不重算，beta 各拿各的值，互不串缓存。"""
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "timeline_iso")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-02-06"
    _seed_day(tmp_root, day)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = []

    def fake_build(date, data_root, config, project=None, **kw):
        calls.append(project)
        return {"date": date, "events": [{"project": project}],
                "summary": {"project": project, "n": len(calls)}}

    monkeypatch.setattr(timeline, "build_timeline", fake_build)
    try:
        s1, d1, _ = _req(port, "GET", f"/api/timeline?date={day}&project=alpha")
        assert s1 == 200 and d1["summary"]["project"] == "alpha"
        s2, d2, _ = _req(port, "GET", f"/api/timeline?date={day}&project=alpha")
        assert s2 == 200 and d2 == d1, "同 project 第二次应命中缓存返回同值"
        s3, d3, _ = _req(port, "GET", f"/api/timeline?date={day}&project=beta")
        assert s3 == 200 and d3["summary"]["project"] == "beta", \
            f"不同 project 应各自计算，不能串到 alpha 的缓存: {d3}"
        s4, d4, _ = _req(port, "GET", f"/api/timeline?date={day}&project=alpha")
        assert s4 == 200 and d4 == d1, "alpha 的缓存不应被 beta 请求挤掉/污染"
        assert len(calls) == 2, f"只应冷算 alpha/beta 各一次，实际 {calls}"
        with dashboard._RESPONSE_CACHE_LOCK:
            tkeys = [k for k in dashboard._RESPONSE_CACHE if k[2] == "timeline"]
        assert len(tkeys) == 2 and {k[4] for k in tkeys} == {"alpha", "beta"}, \
            f"缓存里应有两个按 project 隔离的 key: {tkeys}"
        print("  [PASS] api_timeline_cache_key_isolated_by_project")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_ai_compare_response_cache_hit_second_request_serves_cache_without_recompute(tmp_path, monkeypatch):
    """/api/ai-compare 同区间连发两次：compare_tools 只调 1 次，两次响应一致（命中缓存）。

    背景：14 天区间实测冷态 44.5s、二次仍 47.3s——区间端点曾无响应缓存
    （底层指纹缓存被活跃会话 WAL 追加打穿，逐日 collect 反复重算）。
    现接入通用 _send_json_cached（键含 start/end/project），二次请求直接回缓存。
    """
    import tool_compare  # noqa: E402
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "cache_ai_compare")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = []

    def fake_compare(days, root, config=None, project=None):
        calls.append((tuple(days), project))
        return {"start": days[0], "end": days[-1], "days": len(days),
                "notice": "n", "tools": [],
                "summary": {"tools": 0, "total_sessions": 0,
                            "total_cost": 0.0, "total_minutes": 0.0}}

    monkeypatch.setattr(tool_compare, "compare_tools", fake_compare)
    url = "/api/ai-compare?start=2099-02-01&end=2099-02-14"
    try:
        s1, d1, _ = _req(port, "GET", url)
        s2, d2, _ = _req(port, "GET", url)
        assert s1 == 200 and s2 == 200, f"status {s1}/{s2}"
        assert d1 == d2, f"两次响应应一致: {d1} vs {d2}"
        assert d1["days"] == 14 and d1["start"] == "2099-02-01" and d1["end"] == "2099-02-14"
        assert len(calls) == 1, f"compare_tools 应只被调 1 次（第二次走缓存），实际 {len(calls)} 次"
        # start/end/project 全在键里：不同区间/过滤不得串缓存
        s3, d3, _ = _req(port, "GET", url + "&project=demo")
        assert s3 == 200 and d3["days"] == 14, f"不同 project 应重算: {s3} {d3}"
        assert len(calls) == 2, f"project 变化应重算，实际 {len(calls)} 次"
        s4, d4, _ = _req(port, "GET", url)
        assert s4 == 200 and d4 == d1, "无 project 的缓存不应被 project 请求挤掉/污染"
        with dashboard._RESPONSE_CACHE_LOCK:
            ckeys = [k for k in dashboard._RESPONSE_CACHE if k[2] == "ai-compare"]
        assert len(ckeys) == 2 and {k[5] for k in ckeys} == {None, "demo"}, \
            f"缓存里应有两个按 project 隔离的 key: {ckeys}"
        print("  [PASS] ai_compare_response_cache_hit_second_request_serves_cache_without_recompute")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_response_cache_single_flight_concurrent_cold_requests(tmp_path, monkeypatch):
    """8 线程同时冷请求同一重端点 key：单飞保证 compute 只被调 1 次、人人拿到同值。

    背景：冷路径曾把「查刷新标记」和「置位标记」分在两次加锁（两线程可同时
    判定无标记后双双 compute），且等待超时后无条件并行接管。修复后「查缓存 +
    原子抢标记（CAS）」在同一临界区完成，抢不到标记就一直等在途结果。
    """
    import tool_compare  # noqa: E402
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "cache_single_flight")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = []
    calls_lock = threading.Lock()

    def slow_compare(days, root, config=None, project=None):
        with calls_lock:
            calls.append(days[0])
        time.sleep(0.3)  # 模拟重算：放大竞态窗口，若有并行重算必然被计数捕获
        return {"start": days[0], "end": days[-1], "days": len(days),
                "notice": "n", "tools": [],
                "summary": {"tools": 0, "total_sessions": 0,
                            "total_cost": 0.0, "total_minutes": 0.0}}

    monkeypatch.setattr(tool_compare, "compare_tools", slow_compare)
    url = "/api/ai-compare?start=2099-03-01&end=2099-03-14"
    results = [None] * 8
    barrier = threading.Barrier(8)  # 8 路请求对齐同时进冷路径，最大化竞态压力

    def hit(i):
        barrier.wait(timeout=10)
        results[i] = _req(port, "GET", url)

    try:
        threads = [threading.Thread(target=hit, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert not any(t.is_alive() for t in threads), "全部请求应在超时内完成"
        assert len(calls) == 1, f"8 路并发冷请求应只 compute 1 次（单飞），实际 {len(calls)} 次"
        assert all(r is not None and r[0] == 200 for r in results), \
            [None if r is None else r[0] for r in results]
        payloads = [r[1] for r in results]
        assert all(p == payloads[0] for p in payloads), "并发请求应拿到同一份响应"
        assert payloads[0]["days"] == 14 and payloads[0]["start"] == "2099-03-01"
        with dashboard._RESPONSE_CACHE_LOCK:
            assert dashboard._RESPONSE_CACHE_REFRESHING.get(
                next(k for k in dashboard._RESPONSE_CACHE)) is None, "落缓存后单飞标记应已释放"
        print("  [PASS] response_cache_single_flight_concurrent_cold_requests")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()


def test_api_invalid_semantic_date_rejected(tmp_path):
    """非法日历日（格式合法但日历不存在）应回 400 而非 200 空数据。

    回归：_valid_date 此前只校验 YYYY-MM-DD 正则，"2026-13-99"/"2026-02-30"
    这类日期穿透，各单日端点以空数据 200 响应，破坏 400 契约。
    """
    dashboard.invalidate_response_cache()
    tmp_root = str(tmp_path / "date_semantic")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/api/ai-sessions?date=2026-13-99", "/api/insights?date=2026-02-30",
                     "/api/timeline?date=2026-13-01", "/api/budget?date=2026-00-10"):
            s, d, _ = _req(port, "GET", path)
            assert s == 400, f"{path} 应回 400，实际 {s} {d}"
        # 日历真实存在的日期（含未来日 2099）仍应 200 空态
        s, d, _ = _req(port, "GET", "/api/ai-sessions?date=2099-02-03")
        assert s == 200, f"合法日期应回 200，实际 {s} {d}"
        s, d, _ = _req(port, "GET", "/api/ai-compare?start=2026-02-30&end=2026-03-01")
        assert s == 400, f"ai-compare 语义非法 start 应回 400，实际 {s} {d}"
        print("  [PASS] api_invalid_semantic_date_rejected")
    finally:
        dashboard.invalidate_response_cache()
        server.shutdown()
        server.server_close()
