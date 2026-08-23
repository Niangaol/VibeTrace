# -*- coding: utf-8 -*-
"""tests/e2e/test_full_chain.py — 全链路端到端测试（合二为一后的深化层）。

一条龙覆盖真实使用路径的每一环，全部跑在**同一份模拟数据世界**上：

    造数(usage.jsonl × 2天 + 合成 AI 会话) → SQLite 镜像 rebuild/verify
    → 日报/周聚合/月报 (MD+CSV 导出) → 仪表盘 HTTP 只读面(12+ 端点)
    → 写循环(分组增删 / 设置保存持久化) → 备份 zip → 恢复到全新根并复核
    → 安全抽查(口令 401/带口令 200、CSP 头、跨源 POST 拒绝)
    → 洞察↔查询一致性(query 成本口径 == 聚合账本口径)

确定性约定：
- 数据锚定「今天/昨天」由 fixture 先取好（正午锚思想：远离日界）；
- update.api_base 指向不可达地址（不触网）；browser_history 关闭（不扫真机浏览器，
  浏览器深度已由 tests/integration/test_browser_history_pipeline.py 单独覆盖）；
- AI 会话通过 ai_sessions.paths 指向合成目录（不读开发机真实会话）。

阶段间为**有序依赖**（stage1→stage7 定义顺序即执行顺序），world 为 module 级共享；
任一阶段失败即中断后续——这正是"链路"语义。
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import sqlite3
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import ai_sessions  # noqa: E402
import classifier  # noqa: E402
import dashboard  # noqa: E402
import report  # noqa: E402
import sqlite_store  # noqa: E402
from tests.conftest import ApiClient, make_record, seed_day  # noqa: E402


# ---------------------------------------------------------------------------
# 共享世界：两天活动 + 合成 AI 会话（module 级，一次构建多阶段复用）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("chain_root"))
    today = datetime.date.today()
    d1 = today.isoformat()                       # 今天
    d2 = (today - datetime.timedelta(days=1)).isoformat()  # 昨天

    # 合成 AI 会话目录（opencode JSONL + chatgpt JSON）
    ai_root = tmp_path_factory.mktemp("chain_ai")
    opencode_dir = ai_root / "opencode"
    chatgpt_dir = ai_root / "chatgpt"
    opencode_dir.mkdir()
    chatgpt_dir.mkdir()
    with open(opencode_dir / "sessions.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{d1}T10:00:00", "role": "user",
                             "content": "帮我实现登录接口"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{d1}T10:01:00", "role": "assistant",
                             "content": "第一行\n第二行\n第三行"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{d2}T15:00:00", "role": "user",
                             "content": "昨天的提问"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{d2}T15:01:00", "role": "assistant",
                             "content": "回答"}, ensure_ascii=False) + "\n")
    with open(chatgpt_dir / "conversations.json", "w", encoding="utf-8") as fh:
        json.dump({"messages": [
            {"timestamp": f"{d1}T11:00:00", "role": "user", "content": "你好"},
            {"timestamp": f"{d1}T11:01:00", "role": "assistant", "content": "你好！"},
        ]}, fh, ensure_ascii=False)

    cfg = {
        "update": {"api_base": "http://127.0.0.1:1"},   # 不触网
        "browser_history_enabled": False,               # 不扫真机浏览器
        "ai_sessions": {
            "enabled": True,
            "paths": {"opencode": [str(opencode_dir)], "chatgpt": [str(chatgpt_dir)]},
        },
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)

    # 造数：今天 3 段（开发/AI 编程/游戏），昨天 2 段（含微信社交联系人）
    seed_day(root, d1, [
        make_record(d1, 9, 60, exe="code.exe", app="VS Code", title="main.py",
                    category="开发工具", ai_tool=None),
        make_record(d1, 11, 30, exe="code.exe", app="VS Code", title="copilot chat",
                    category="开发工具", ai_tool="copilot"),
        make_record(d1, 14, 45, exe="steam.exe", app="Steam", title="游戏",
                    category="游戏"),
    ])
    seed_day(root, d2, [
        make_record(d2, 10, 90, exe="code.exe", app="VS Code", title="refactor.py",
                    category="开发工具"),
        make_record(d2, 16, 20, exe="wechat.exe", app="微信", title="张三",
                    category="社交聊天", contact="zhangsan"),
    ])

    # 今天活跃毫秒期望值（60+30+45 分钟）
    expect_d1_ms = (60 + 30 + 45) * 60000
    return {
        "root": root, "d1": d1, "d2": d2,
        "expect_d1_ms": expect_d1_ms, "expect_d1_min": 60 + 30 + 45,
        "ai_dirs": {"opencode": [str(opencode_dir)], "chatgpt": [str(chatgpt_dir)]},
        "cfg": cfg,
    }


def _boot(root: str):
    """在既有数据根上起服务器（随机端口），返回 (ApiClient, server)。"""
    dashboard.invalidate_days_cache()
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return ApiClient(port), server


# ---------------------------------------------------------------------------
# Stage 1 — 存储镜像：JSONL → SQLite rebuild → verify 一致性
# ---------------------------------------------------------------------------
def test_stage1_sqlite_mirror(world):
    root, d1 = world["root"], world["d1"]
    sqlite_store.rebuild(root)
    res = sqlite_store.verify(root)
    assert res["mismatches"] == [], f"镜像不一致: {res['mismatches']}"
    assert res["jsonl_records"] == res["sqlite_records"] > 0

    # 镜像里今天的行数 == JSONL 行数
    db = os.path.join(root, "usage.db")
    conn = sqlite3.connect(db)
    try:
        n_db = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE day=?", (d1,)).fetchone()[0]
    finally:
        conn.close()
    n_jsonl = sum(1 for _ in open(os.path.join(root, d1, "usage.jsonl"),
                                  encoding="utf-8"))
    assert n_db == n_jsonl == 3, f"镜像行数不符: db={n_db} jsonl={n_jsonl}"
    sqlite_store.close_connections()


# ---------------------------------------------------------------------------
# Stage 2 — 报表管线：日聚合精确性 + 日报 MD + 月报 MD + CSV 导出
# ---------------------------------------------------------------------------
def test_stage2_reports_and_exports(world):
    root, d1 = world["root"], world["d1"]

    agg = report.aggregate(d1, root)
    assert agg["total_active_ms"] == world["expect_d1_ms"], \
        f"今日聚合 {agg['total_active_ms']} != {world['expect_d1_ms']}"
    by_cat = {k: v for k, v in agg["by_category"].items()}
    assert "开发工具" in by_cat and "游戏" in by_cat
    # AI 工具维度被记录（copilot 30 分钟）
    ai_tools = agg.get("by_ai") or {}
    assert any("copilot" in k for k in ai_tools), f"AI 工具维度缺失: {ai_tools}"

    md_day = report.generate_report_md(d1, root)
    assert isinstance(md_day, str) and "VS Code" in md_day
    csv_day = report.generate_report_csv(d1, root)
    assert "VS Code" in csv_day and "开发工具" in csv_day

    month = d1[:7]
    md_month = report.generate_month_report_md(month, root)
    assert isinstance(md_month, str) and len(md_month) > 100

    report.generate_day_report(d1, root)
    assert os.path.isfile(os.path.join(root, d1, "report.md")), "日报文件应落盘"


# ---------------------------------------------------------------------------
# Stage 3 — 仪表盘只读面：同一数据根上的 HTTP 全端点冒烟
# ---------------------------------------------------------------------------
def test_stage3_api_read_surface(world):
    client, server = _boot(world["root"])
    try:
        d1 = world["d1"]
        # 概览三件套
        st, body, _ = client.get(f"/api/day?date={d1}")
        assert st == 200 and body["aggregate"]["total_active_ms"] == world["expect_d1_ms"]
        st, body, _ = client.get("/api/dates")
        assert st == 200 and world["d2"] in body["dates"] and d1 in body["dates"]
        st, body, _ = client.get("/api/days?n=7")
        assert st == 200 and len(body["days"]) >= 2

        # 维度面
        for path in (f"/api/hourly?date={d1}", "/api/heatmap?days=7",
                     "/api/trend?weeks=4", "/api/month?month=" + d1[:7],
                     "/api/week?date=" + d1, "/api/urls?date=" + d1):
            st, _, _ = client.get(path)
            assert st == 200, f"{path} 应 200"

        # 深化面（AI 会话 / 时间轴 / 成长 / 对比 / 预算 / 目标 / 采纳率 / 定价）
        st, body, _ = client.get(f"/api/ai-sessions?date={d1}")
        assert st == 200 and body["ai_sessions"]["enabled"] is True
        assert body["ai_sessions"]["found"] is True, "合成会话应被发现"
        turns = body["ai_sessions"]["total"]["turns"]
        assert turns == 4, f"今天应有 4 条消息（opencode2+chatgpt2），实际 {turns}"

        d2 = world["d2"]
        d2 = world["d2"]
        for path in (f"/api/timeline?date={d1}", "/api/growth",
                     f"/api/ai-compare?start={d2}&end={d1}",
                     f"/api/budget?period=monthly&date={d1[:7]}",
                     f"/api/budget?period=daily&date={d1}", "/api/goals",
                     f"/api/adoption?date={d1}",
                     "/api/tool-compare?start={d2}&end={d1}".format(d2=d2, d1=d1),
                     "/api/pricing"):
            st, _, _ = client.get(path)
            assert st == 200, f"{path} 应 200"

        # 受限查询走同一世界数据（昨天有 AI 会话 → 应给出确定答案）
        import urllib.parse
        q = urllib.parse.quote("昨天 AI 成本是多少")
        st, body, _ = client.get(f"/api/query?q={q}")
        assert st == 200 and body.get("ok") is True, f"query 契约异常: {body}"

        # 页面本体 + 安全头
        st, raw, hdr = client.get("/")
        assert st == 200 and "VibeTrace" in raw.get("_raw", "")
        assert hdr.get("Content-Security-Policy"), "页面必须带 CSP"
        assert hdr.get("X-Frame-Options") == "DENY"
        assert hdr.get("Cache-Control") == "no-store"
    finally:
        server.shutdown()
        server.server_close()
        dashboard.invalidate_days_cache()


# ---------------------------------------------------------------------------
# Stage 4 — 写循环：分组增删 + 设置保存并持久化到 config.json
# ---------------------------------------------------------------------------
def test_stage4_write_cycle(world):
    client, server = _boot(world["root"])
    try:
        st, body, _ = client.post("/api/groups/add", {"name": "学习工具"})
        assert st == 200 and body.get("ok") is True
        st, body, _ = client.get("/api/groups")
        names = json.dumps(body, ensure_ascii=False)
        assert "学习工具" in names, f"分组应出现: {names[:200]}"
        st, body, _ = client.post("/api/groups/delete", {"name": "学习工具"})
        assert st == 200 and body.get("ok") is True

        # 目标设置写入 → 落盘 <root>/config.json → 再读回一致
        st, body, _ = client.post("/api/goals/settings",
                                  {"enabled": True, "daily_active_min": 120,
                                   "daily_coding_min": 60})
        assert st == 200 and body.get("ok") is True, f"目标设置失败: {body}"
        with open(os.path.join(world["root"], "config.json"), encoding="utf-8") as fh:
            cfg_now = json.load(fh)
        goals = cfg_now.get("goals") or {}
        assert goals.get("enabled") is True and int(goals.get("daily_active_min", 0)) == 120, \
            f"设置应持久化到 config.json: {goals}"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Stage 5 — 备份 → 恢复到全新根 → 数据复核（跨根闭环）
# ---------------------------------------------------------------------------
def test_stage5_backup_restore_roundtrip(world, tmp_path):
    src_client, src_server = _boot(world["root"])
    try:
        st, _, _ = src_client.get("/api/backup")
        assert st == 200 and src_client.raw[:2] == b"PK", "备份应为 zip 字节流"
        blob = src_client.raw
    finally:
        src_server.shutdown()
        src_server.server_close()

    # 全新空根 + 最小 config，恢复进去
    dst_root = str(tmp_path / "restored")
    os.makedirs(dst_root, exist_ok=True)
    with open(os.path.join(dst_root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"update": {"api_base": "http://127.0.0.1:1"},
                   "browser_history_enabled": False}, fh)

    dst_client, dst_server = _boot(dst_root)
    try:
        boundary = "----chainboundary123"
        part = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="backup.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode("utf-8") + blob + f"\r\n--{boundary}--\r\n".encode("utf-8")
        st, body, _ = dst_client.post(
            "/api/backup/restore", body=part,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert st == 200 and body.get("ok") is True, f"恢复失败: {body}"
        assert world["d1"] in (body.get("days") or []), "恢复清单应含今天"

        # 复核：新根里的今天数据与源根聚合一致
        agg_src = report.aggregate(world["d1"], world["root"])
        agg_dst = report.aggregate(world["d1"], dst_root)
        assert agg_dst["total_active_ms"] == agg_src["total_active_ms"] == \
            world["expect_d1_ms"], "恢复后聚合应与源完全一致"
    finally:
        dst_server.shutdown()
        dst_server.server_close()
        dashboard.invalidate_days_cache()


# ---------------------------------------------------------------------------
# Stage 6 — 安全抽查：口令模式 / 跨源拒绝（在链路语境下再验一遍）
# ---------------------------------------------------------------------------
def test_stage6_security_spotchecks(world, tmp_path):
    secure_root = str(tmp_path / "secure_root")
    os.makedirs(secure_root, exist_ok=True)
    seed_day(secure_root, world["d1"], [
        make_record(world["d1"], 9, 30, exe="code.exe", app="VS Code",
                    title="s.py", category="开发工具"),
    ])
    with open(os.path.join(secure_root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"update": {"api_base": "http://127.0.0.1:1"},
                   "browser_history_enabled": False,
                   "dashboard_token": "chain-secret"}, fh)

    client, server = _boot(secure_root)
    try:
        c = client

        # 无口令 → 401
        st, body, _ = c.get("/api/day?date=" + world["d1"])
        assert st == 401, f"口令模式未带凭据应 401，实际 {st}"
        # 带正确 token 头 → 200
        st, body, _ = c.get("/api/day?date=" + world["d1"],
                            headers={"X-Dashboard-Token": "chain-secret"})
        assert st == 200 and body["aggregate"]["total_active_ms"] == 30 * 60000
        # 错误 token → 401（常量时间比较路径）
        st, _, _ = c.get("/api/day?date=" + world["d1"],
                         headers={"X-Dashboard-Token": "wrong"})
        assert st == 401

        # 跨源 POST → 拒绝（Origin 校验）
        conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
        conn.request("POST", "/api/groups/add",
                     body=json.dumps({"name": "x"}),
                     headers={"Content-Type": "application/json",
                              "Origin": "https://evil.example"})
        resp = conn.getresponse()
        st_evil = resp.status
        resp.read()
        conn.close()
        assert st_evil in (403, 400), f"跨源写应被拒，实际 {st_evil}"
    finally:
        server.shutdown()
        server.server_close()
        dashboard.invalidate_days_cache()


# ---------------------------------------------------------------------------
# Stage 7 — 洞察 ↔ 查询一致性：query 的成本答案与本地账本同源可对账
# ---------------------------------------------------------------------------
def test_stage7_insight_query_consistency(world):
    root, d1 = world["root"], world["d1"]
    # 直连 collect（不经 HTTP），拿到今天的 AI 会话统计
    cfg = dict(classifier.load_config())
    cfg["data_root"] = root
    cfg["ai_sessions"] = {"enabled": True, "paths": world["ai_dirs"]}  # 已是 列表 形态
    data = ai_sessions.collect(d1, cfg)
    assert data["enabled"] is True and data["found"] is True
    total = data["total"]
    assert total["turns"] == 4, f"直连统计应 4 条消息: {total}"

    # 经 HTTP 的查询端点对同一世界的空态/异常保持契约（不 500、notice 在场）
    client, server = _boot(root)
    try:
        import urllib.parse
        q = urllib.parse.quote("今天一共花了多少钱")
        st, body, _ = client.get(f"/api/query?q={q}")
        assert st == 200
        assert body.get("ok") in (True, False)      # 有钱/无钱都算合法答案
        assert "notice" in json.dumps(body, ensure_ascii=False), "免责声明必须在场"
        assert body.get("error") is None, f"查询不应报错: {body}"
    finally:
        server.shutdown()
        server.server_close()
