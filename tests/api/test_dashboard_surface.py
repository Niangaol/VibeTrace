# -*- coding: utf-8 -*-
"""api/test/dashboard/surface — 自 test_all.py 移植

由 test_all.py（@7d60620，336 项检查）机械移植拆分而来——断言逻辑逐行保持一致；
仅有的改动：①助手移入 tests/support/scenario.py 并 import；②_chrome_ft 改为
正午锚定（消除午夜抖动类 flaky）；③去掉独立 main 入口（统一由 pytest 收集）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import classifier  # noqa: E402

from tests.support.scenario import (  # noqa: E402
    check, fresh_tmp,
)

# ruff: F401 未用导入由 `ruff check --fix` 自动清理

def test_dashboard_api():
    print("[test] 仪表盘 API（端点 + 同源安全校验 + 错误码）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dashapi")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "wechat.exe", "app": "微信", "title": "张三", "category": "社交聊天",
            "contact": "张三", "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")

    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        body = r.read().decode("utf-8", errors="replace")
        conn.close()
        return r.status, body

    try:
        # 无浏览器上下文的请求（curl/脚本）放行
        s, _ = req("GET", "/api/dates")
        check(s == 200, "无 Origin 放行", str(s))
        # 恶意 Origin（跨站 fetch）拒绝
        s, _ = req("GET", "/api/dates", {"Origin": "https://evil.example"})
        check(s == 403, "恶意 Origin 拒绝", str(s))
        # 恶意 Referer 拒绝
        s, _ = req("GET", "/api/dates", {"Referer": "https://evil.example/page"})
        check(s == 403, "恶意 Referer 拒绝", str(s))
        # 合法 Origin 放行
        s, _ = req("GET", "/api/dates", {"Origin": f"http://127.0.0.1:{port}"})
        check(s == 200, "合法 Origin 放行", str(s))
        # 页面响应带安全头
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/")
        r = conn.getresponse()
        headers = dict(r.getheaders())
        conn.close()
        check(headers.get("X-Frame-Options") == "DENY", "X-Frame-Options: DENY")
        check("Content-Security-Policy" in headers, "CSP 存在")
        # 端点
        s, _ = req("GET", "/api/day?date=2026-08-08")
        check(s == 200, "api/day 正常", str(s))
        s, _ = req("GET", "/api/day?date=bad")
        check(s == 400, "非法日期 400", str(s))
        s, _ = req("GET", "/nope")
        check(s == 404, "未知路径 404", str(s))
        s, _ = req("POST", "/api/dates")
        check(s == 405, "POST 405", str(s))
        # 路径穿越被拒（不存在的日期 -> 400 而非泄露路径）
        s, _ = req("GET", "/api/day?date=../2026-08-08")
        check(s == 400, "路径穿越日期被拒", str(s))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

def test_dashboard_days_resilience():
    print("[test] 仪表盘 /api/days 单日聚合失败不拖垮趋势")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_days")
    day_good = "2026-08-08"
    day_bad = "2026-08-09"
    os.makedirs(os.path.join(tmp, day_good), exist_ok=True)
    os.makedirs(os.path.join(tmp, day_bad), exist_ok=True)
    with open(os.path.join(tmp, day_good, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day_good}T10:00:00", "end": f"{day_good}T10:02:00",
            "duration_ms": 120000, "exe": "code.exe", "app": "VS Code", "title": "a",
            "category": "开发工具", "ai_tool": None, "active": True,
        }, ensure_ascii=False) + chr(10))
    # 坏日：usage.jsonl 含非法 UTF-8 字节 -> aggregate 会抛 UnicodeDecodeError
    with open(os.path.join(tmp, day_bad, "usage.jsonl"), "wb") as fh:
        fh.write(bytes([0xfe, 0xff, 0x81, 0]) + b" bad" + bytes([10]))

    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/days?n=14")
    r = conn.getresponse()
    body = json.loads(r.read().decode("utf-8", "replace"))
    conn.close()
    check(r.status == 200, "api/days 返回 200", str(r.status))
    by = {d["date"]: d for d in body.get("days", [])}
    check(by.get(day_bad, {}).get("total_ms") == 0, "坏日以 0 兜底不丢时间轴", str(by.get(day_bad)))
    check(by.get(day_good, {}).get("total_ms") == 120000, "好日数据正常", str(by.get(day_good)))

    # 热力图同样不因单日失败而 500
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/heatmap?days=14")
    r2 = conn.getresponse()
    hm = json.loads(r2.read().decode("utf-8", "replace"))
    conn.close()
    check(r2.status == 200, "api/heatmap 返回 200", str(r2.status))
    hm_by = {d["date"]: d for d in hm.get("days", [])}
    check(hm_by.get(day_bad, {}).get("total_ms") == 0, "热力图坏日以 0 兜底", str(hm_by.get(day_bad)))
    check(hm_by.get(day_good, {}).get("total_ms") == 120000, "热力图好日数据正常", str(hm_by.get(day_good)))
    server.shutdown()
    server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

def test_dashboard_update_api():
    print("[test] 仪表盘更新 API（status/check/download/apply 错误态）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_update")
    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read().decode("utf-8", errors="replace")
        conn.close()
        return r.status, data

    try:
        s, body = req("GET", "/api/update/status")
        status = json.loads(body)
        check(s == 200 and status.get("state") == "idle" and status.get("dev") is True,
              "update/status 正常", body)
        import updater
        orig = updater.check_for_update
        updater.check_for_update = lambda *a, **k: {
            "current": "2.1.0", "latest": "", "has_update": False,
            "notes": "", "published_at": "", "url": "", "asset": None, "error": None,
        }
        try:
            s, body = req("GET", "/api/update/check")
            check(s == 200 and json.loads(body).get("has_update") is False,
                  "update/check 无更新", body)
            s, body = req("POST", "/api/update/download",
                          {"Content-Type": "application/json"}, "{}")
            check(s == 400 and "无需下载" in json.loads(body).get("error", ""),
                  "update/download 无更新拒绝", body)
            s, body = req("POST", "/api/update/apply",
                          {"Content-Type": "application/json"}, "{}")
            check(s == 400 and "没有已下载" in json.loads(body).get("error", ""),
                  "update/apply 未下载拒绝", body)
        finally:
            updater.check_for_update = orig
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

def test_dashboard_insights_api():
    print("[test] 仪表盘洞察 API（/api/insights 结构 + /api/insights/ai 错误态 + 非法日期 400）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_insights")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for row in (
            {"start": f"{day}T09:00:00", "end": f"{day}T10:00:00", "duration_ms": 3600000,
             "exe": "code.exe", "app": "VS Code", "title": "main.py",
             "category": "办公学习", "active": True},
            {"start": f"{day}T20:00:00", "end": f"{day}T23:00:00", "duration_ms": 3 * 3600000,
             "exe": "steam.exe", "app": "Steam", "title": "Steam",
             "category": "游戏", "active": True},
        ):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {
            "enabled": True, "in_report": True,
            "rules": {"long_session_min": 90, "late_night_hour": 23,
                      "game_alert_hours": 2, "study_goal_hours": 1, "game_ratio_warn": 0.4},
            "ai": {"enabled": False},
        }}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()

    server = dashboard.create_server(tmp, port=0, config_path=config_path)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        conn.close()
        return r.status, data

    try:
        s, d = req(f"/api/insights?date={day}")
        check(s == 200, "/api/insights 200", str(s))
        check(d["date"] == day and isinstance(d["rules"], list) and len(d["rules"]) >= 2,
              "规则结构正确", str(d))
        check(d["ai_enabled"] is False and d["ai"] is None, "AI 关闭 -> ai_enabled=false / ai=null")
        check(any(r["type"] == "study" for r in d["rules"]), "规则含学习")
        check(any(r["type"] == "game" for r in d["rules"]), "规则含游戏")

        s, d = req(f"/api/insights/ai?date={day}")
        check(s == 200 and d["ai_enabled"] is False, "/api/insights/ai 未开启错误态 200")
        check("未开启" in (d.get("ai") or {}).get("error", ""), "错误态文案", str(d))

        s, _ = req("/api/insights?date=bad")
        check(s == 400, "/api/insights 非法日期 400", str(s))
        s, _ = req("/api/insights/ai?date=../2026-08-10")
        check(s == 400, "/api/insights/ai 路径穿越日期 400", str(s))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

def test_dashboard_ai_settings_api():
    print("[test] 仪表盘 AI 设置 API（开关 + 预设 + 保存/保留密钥 + 校验）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_ai_settings")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({
            "insights": {
                "ai": {
                    "enabled": False, "provider": "opencodego",
                    "base_url": "", "api_key": "old-secret",
                    "model": "deepseek-v4-flash", "timeout_s": 60,
                    "send_raw_titles": False, "language": "zh",
                }
            }
        }, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    server = dashboard.create_server(tmp, port=0, config_path=config_path)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path,
                     body=json.dumps(body, ensure_ascii=False) if body is not None else None,
                     headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        conn.close()
        return r.status, data

    try:
        s, d = req("GET", "/api/insights/settings")
        check(s == 200 and d["ai"]["enabled"] is False, "GET 设置返回当前关闭状态", str(d))
        check(d["ai"]["api_key_set"] is True, "已有 API Key 只返回已设置标志", str(d))
        check("api_key" not in d["ai"], "不回显真实 API Key", str(d))
        check(any(p["id"] == "deepseek" for p in d["presets"]), "预设列表含 DeepSeek")

        # 开启并选择 DeepSeek 预设，不填 base/model 也会自动落盘预设值；空 key 保留旧值
        s, d = req("POST", "/api/insights/settings", {
            "enabled": True, "provider": "deepseek", "base_url": "",
            "api_key": "", "model": "", "timeout_s": 90,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 200 and d.get("ok") is True, "保存 AI 设置成功", str(d))
        check(d["ai"]["enabled"] is True and d["ai"]["provider"] == "deepseek", "开关与 provider 已保存")
        check("api.deepseek.com" in d["ai"]["base_url"] and d["ai"]["model"] == "deepseek-chat",
              "预设 base/model 已落盘", str(d))
        check(d["ai"]["api_key_set"] is True, "空 API Key 保留旧值", str(d))

        # 开启自定义但没有 Base URL -> 400
        s, d = req("POST", "/api/insights/settings", {
            "enabled": True, "provider": "custom", "base_url": "",
            "api_key": "", "model": "m", "timeout_s": 60,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 400, "开启自定义无 Base URL 被拒", str(d))

        # 关闭开关允许空端点
        s, d = req("POST", "/api/insights/settings", {
            "enabled": False, "provider": "custom", "base_url": "",
            "api_key": "", "model": "", "timeout_s": 60,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 200 and d["ai"]["enabled"] is False, "关闭开关可保存", str(d))
        # 再次读取确认密钥仍在
        s, d = req("GET", "/api/insights/settings")
        check(d["ai"]["api_key_set"] is True, "关闭后密钥仍保留", str(d))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)
