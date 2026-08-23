# -*- coding: utf-8 -*-
"""integration/test/report/content — 自 test_all.py 移植

由 test_all.py（@7d60620，336 项检查）机械移植拆分而来——断言逻辑逐行保持一致；
仅有的改动：①助手移入 tests/support/scenario.py 并 import；②_chrome_ft 改为
正午锚定（消除午夜抖动类 flaky）；③去掉独立 main 入口（统一由 pytest 收集）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import monitor  # noqa: E402
import classifier  # noqa: E402
import report  # noqa: E402
import inventory  # noqa: E402

from tests.support.scenario import (  # noqa: E402
    FG, P, _day_noon_ft, check, fresh_tmp, run_scenario,
)

# ruff: F401 未用导入由 `ruff check --fix` 自动清理

def test_report_pipeline():
    print("[test] 报表管线（run_daemon 后 generate_day_report -> report.md/csv 正确）")
    fg = [FG("wechat.exe", "张三")] * 3 + [FG("wt.exe", "opencode", pid=100)] * 3
    tree = {100: P("wt.exe", 0, 100), 200: P("opencode.exe", 100, 200)}
    recs, tmp = run_scenario("report_pipe", fg, seconds=6, process_tree=tree)
    check(len(recs) == 2, "2 条会话")
    day = recs[0]["start"][:10]
    report.generate_day_report(day, tmp)
    md_path = os.path.join(tmp, day, "report.md")
    csv_path = os.path.join(tmp, day, "report.csv")
    check(os.path.isfile(md_path), "report.md 生成")
    check(os.path.isfile(csv_path), "report.csv 生成")
    md = open(md_path, encoding="utf-8").read()
    check("## 总览" in md and "活跃时长" in md, "汇总日报含总览表")
    check("微信" in md and "张三" in md, "日报含 微信/张三")
    check("opencode" in md, "日报含 opencode")
    # 会话记录自洽：duration_ms == end - start
    import datetime as _dt2
    for r in recs:
        t0 = _dt2.datetime.fromisoformat(r["start"])
        t1 = _dt2.datetime.fromisoformat(r["end"])
        calc = int((t1 - t0).total_seconds() * 1000)
        check(calc == r["duration_ms"], f"duration_ms 与 start/end 自洽 ({r['app']})",
              f"{r['duration_ms']} vs {calc}")
    csv = open(csv_path, encoding="utf-8-sig").read()
    check("联系人:微信/张三" in csv, "CSV 含联系人汇总")
    shutil.rmtree(tmp, ignore_errors=True)

def test_inventory():
    print("[test] 软件清单（注册表/进程扫描 -> 分类 -> 写 JSON/CSV）")
    cfg = classifier.load_config()
    inv = inventory.collect_inventory(cfg)
    check(inv["count"] >= 20, "扫描到至少 20 个应用", f"实际 {inv['count']}")
    cats = {a["category"] for a in inv["apps"]}
    check(cats <= set(classifier.CATEGORY_ORDER), "类别都在合法集合内", str(cats))
    tmp = fresh_tmp("inventory")
    written = inventory.write_inventory(tmp, cfg)
    check(os.path.isfile(os.path.join(tmp, "software_inventory.json")), "JSON 写出")
    check(os.path.isfile(os.path.join(tmp, "software_inventory.csv")), "CSV 写出")
    data = json.load(open(os.path.join(tmp, "software_inventory.json"), encoding="utf-8"))
    check(data["count"] == written["count"], "JSON 计数一致")
    check({"date", "scanned_at", "count", "apps"} <= set(data.keys()), "schema 字段齐全")
    shutil.rmtree(tmp, ignore_errors=True)

def test_month_and_json():
    print("[test] 月度汇总 + JSON 导出（--month / --json 逻辑）")
    tmp = fresh_tmp("month")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    lines = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
         "exe": "wechat.exe", "app": "微信", "title": "张三", "category": "社交聊天",
         "contact": "张三", "ai_tool": None, "active": True},
    ]
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    agg = report.aggregate_month("2026-08", tmp)
    check(agg["total_active_ms"] == 120000, "月度聚合时长正确", str(agg["total_active_ms"]))
    check(len(agg["per_day"]) == 1 and agg["per_day"][0]["date"] == day, "每日明细正确")
    md = report.generate_month_report_md("2026-08", tmp)
    check("电脑使用情况月报 2026-08" in md and "微信" in md and "张三" in md, "月报内容正确")
    j = json.loads(json.dumps(agg, ensure_ascii=False, default=str))
    check(j["by_contact"]["微信"]["张三"] == 120000, "JSON 导出结构正确")
    shutil.rmtree(tmp, ignore_errors=True)

def test_browser_history():
    print("[test] 浏览器历史（合成 Chromium SQLite：分类 + 黑名单掩蔽 + 停留时长）")
    import sqlite3
    import browser_history

    tmp = fresh_tmp("history")
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
            title TEXT, visit_count INTEGER DEFAULT 0, typed_count INTEGER DEFAULT 0,
            last_visit_time INTEGER, hidden INTEGER DEFAULT 0);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, from_visit INTEGER, transition INTEGER,
            segment_id INTEGER, visit_duration INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://www.bilibili.com/video/av1", "测试视频页面"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://github.com/user/repo", "我的仓库"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://example.com/login?password=123", "密码修改页"))
    # 先取日字符串再造数：时间戳锚定该日正午，与查询/断言单一来源（防午夜抖动）
    cfg = classifier.load_config()
    today = _dt.date.today().isoformat()
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (_day_noon_ft(today, -120),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (2, ?, 60000000)", (_day_noon_ft(today, -60),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (3, ?, 0)", (_day_noon_ft(today, 0),))
    conn.commit()
    conn.close()

    data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    check(data["count"] == 3, "提取 3 条访问", f"实际 {data['count']}")
    cats = {v["url"].split("?", 1)[0]: v["category"] for v in data["visits"]}
    check(cats.get("https://www.bilibili.com/video/av1") == "视频", "bilibili -> 视频", str(cats))
    check(cats.get("https://github.com/user/repo") == "代码", "github -> 代码", str(cats))
    masked = [v for v in data["visits"] if v["url"] == "[已隐藏]"]
    check(len(masked) == 1, "命中黑名单的 URL 掩蔽为 [已隐藏]")
    check(all(v["time"].startswith(today) for v in data["visits"]), "访问时间换算为本地时间")

    # 停留时长
    dur_by_url = {v["url"].split("?", 1)[0]: v["duration_s"] for v in data["visits"]}
    check(dur_by_url.get("https://www.bilibili.com/video/av1") == 120.0, "bilibili 停留 120 秒", str(dur_by_url))
    check(dur_by_url.get("https://github.com/user/repo") == 60.0, "github 停留 60 秒")
    check(data["total_duration_s"] == 180.0, "总停留 180 秒", str(data["total_duration_s"]))
    check(data["by_category_duration_s"].get("视频") == 120.0, "视频分类停留 120 秒", str(data.get("by_category_duration_s")))
    check(data["by_domain_duration_s"].get("www.bilibili.com") == 120.0, "域名停留聚合正确")

    section = browser_history.report_section(today, tmp, cfg, db_paths=[db])
    check(section is not None and "浏览器访问明细" in section, "日报章节可生成")
    check("bilibili" in section and "视频" in section, "章节含 URL 与分类")
    check("停留总时长" in section and "3 分钟" in section, "章节含停留时长汇总")
    check("密码" not in section and "example.com" not in section, "章节不含被掩蔽的敏感内容")

    # 无 visit_duration 列的兼容性（回退为 0）
    db2 = os.path.join(tmp, "History_old.db")
    conn2 = sqlite3.connect(db2)
    conn2.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL);
    """)
    conn2.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://www.icourse163.org/course/1", "MOOC课"))
    conn2.execute("INSERT INTO visits (url, visit_time) VALUES (1, ?)", (_day_noon_ft(today, 0),))
    conn2.commit()
    conn2.close()
    data2 = browser_history.collect(today, tmp, cfg, db_paths=[db2])
    check(data2["count"] == 1 and data2["visits"][0]["duration_s"] == 0.0, "旧 schema 无时长列时兼容")

    # 禁用开关
    cfg2 = dict(cfg)
    cfg2["browser_history_enabled"] = False
    data3 = browser_history.collect(today, tmp, cfg2, db_paths=[db])
    check(data3["enabled"] is False and data3["count"] == 0, "browser_history_enabled=false 时跳过")
    shutil.rmtree(tmp, ignore_errors=True)

def test_reclassify():
    print("[test] 重分类（规则变更后修复历史记录：π 终端会话 -> pi agent）")
    tmp = fresh_tmp("reclassify")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    lines = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
         "exe": "wt.exe", "app": "Windows Terminal", "title": "π - niangao",
         "category": "开发工具", "contact": None, "ai_tool": None, "active": True},
        {"start": f"{day}T10:05:00", "end": f"{day}T10:06:00", "duration_ms": 60000,
         "exe": "tabbit browser.exe", "app": "Tabbit Browser", "title": "[已隐藏]",
         "category": "浏览器", "contact": None, "ai_tool": None, "active": True},
    ]
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n = report.reclassify_day(day, tmp)
    check(n == 1, "1 条记录变更", str(n))
    recs = [json.loads(line) for line in open(os.path.join(tmp, day, "usage.jsonl"), encoding="utf-8") if line.strip()]
    pi = [r for r in recs if "π" in r["title"]][0]
    check(pi["ai_tool"] == "pi agent", "π 会话重分类为 pi agent", str(pi["ai_tool"]))
    check(pi["category"] == "AI编程", "π 会话类别重分类为 AI编程", pi["category"])
    hidden = [r for r in recs if r["title"] == "[已隐藏]"][0]
    check(hidden["ai_tool"] is None and hidden["category"] == "浏览器", "已隐藏记录不受影响")
    check(os.path.isfile(os.path.join(tmp, day, "usage.jsonl.bak")), "写回前已备份")
    shutil.rmtree(tmp, ignore_errors=True)

def test_dimension_refinements():
    print("[test] 维度细化（终端工具 / 窗口状态 / 子分类 / 会话URL关联）")
    import sqlite3
    import browser_history

    # 1) 终端 TUI 工具 + 子分类 + 窗口状态
    fg = [FG("wt.exe", "git status - niangao", pid=100)] * 3
    recs, tmp = run_scenario("dims", fg, seconds=3)
    r = recs[0]
    check(r.get("term_tool") == "git", "终端标题识别 git", str(r.get("term_tool")))
    check(r.get("subcategory") == "终端", "wt 子分类=终端", str(r.get("subcategory")))
    check(r.get("window_state") in ("normal", "maximized", "fullscreen"), "窗口状态字段存在", str(r.get("window_state")))
    shutil.rmtree(tmp, ignore_errors=True)

    # 2) 路径标题不误判（D:\git-stuff）
    fg2 = [FG("wt.exe", r"D:\git-stuff - pwsh", pid=100)] * 3
    recs2, tmp2 = run_scenario("dims2", fg2, seconds=3)
    check(recs2[0].get("term_tool") is None, "路径标题不误判 git", str(recs2[0].get("term_tool")))
    shutil.rmtree(tmp2, ignore_errors=True)

    # 3) 游戏顶级类别下的子分类（用户 config 中 游戏 已是独立大类）
    fg3 = [FG("steam.exe", "Steam")] * 3
    recs3, tmp3 = run_scenario("dims3", fg3, seconds=3)
    check(recs3[0].get("category") == "游戏", "steam 顶级类别=游戏", str(recs3[0].get("category")))
    check(recs3[0].get("subcategory") == "游戏平台", "steam 子分类=游戏平台", str(recs3[0].get("subcategory")))
    shutil.rmtree(tmp3, ignore_errors=True)

    # 4) 浏览器会话 subcategory=browser_category + URL 关联（monkeypatch 查找函数）
    real_fn = browser_history.find_url_for_session
    browser_history.find_url_for_session = lambda *a, **k: "https://www.bilibili.com/video/BV1test"
    try:
        fg4 = [FG("chrome.exe", "bilibili 视频 - 主页")] * 3
        recs4, tmp4 = run_scenario("dims4", fg4, seconds=3)
    finally:
        browser_history.find_url_for_session = real_fn
    r4 = recs4[0]
    check(r4.get("subcategory") == "视频", "浏览器子分类=视频", str(r4.get("subcategory")))
    check(r4.get("url") == "https://www.bilibili.com/video/BV1test", "会话关联 URL", str(r4.get("url")))
    shutil.rmtree(tmp4, ignore_errors=True)

    # 5) find_url_for_session 合成库直接测试（重叠匹配 / 无重叠 / 黑名单掩蔽）
    tmp5 = fresh_tmp("dims5")
    db = os.path.join(tmp5, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    now = _dt.datetime.now()
    ft = int((now.timestamp() + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/page", "页面"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (ft,))
    conn.commit()
    conn.close()
    cfg = classifier.load_config()
    hit = browser_history.find_url_for_session(
        now - _dt.timedelta(minutes=1), now, tmp5, cfg, db_paths=[db])
    check(hit == "https://example.com/page", "时间重叠匹配 URL", str(hit))
    miss = browser_history.find_url_for_session(
        now - _dt.timedelta(hours=3), now - _dt.timedelta(hours=2, minutes=59), tmp5, cfg, db_paths=[db])
    check(miss is None, "无重叠返回 None", str(miss))
    db2 = os.path.join(tmp5, "History2")
    conn2 = sqlite3.connect(db2)
    conn2.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    conn2.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/login?password=123", "登录"))
    conn2.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (ft,))
    conn2.commit()
    conn2.close()
    hit2 = browser_history.find_url_for_session(
        now - _dt.timedelta(minutes=1), now, tmp5, cfg, db_paths=[db2])
    check(hit2 == "[已隐藏]", "命中黑名单 URL 掩蔽", str(hit2))
    shutil.rmtree(tmp5, ignore_errors=True)

def test_contact_aliases():
    print("[test] 联系人别名（aliases.json: aaa123 -> 张三）")
    tmp = fresh_tmp("aliases")
    with open(os.path.join(tmp, "aliases.json"), "w", encoding="utf-8") as fh:
        json.dump({"aaa123": "张三"}, fh, ensure_ascii=False)
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "wechat.exe", "app": "微信", "title": "aaa123", "category": "社交聊天",
            "contact": "aaa123", "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")
    agg = report.aggregate(day, tmp)
    check(agg["by_contact"].get("微信", {}).get("张三") == 120000, "聚合后显示别名张三",
          str(agg["by_contact"]))
    check("aaa123" not in agg["by_contact"].get("微信", {}), "原始 ID 不出现")
    md = report.generate_report_md(day, tmp)
    check("张三" in md, "日报显示别名")
    shutil.rmtree(tmp, ignore_errors=True)

def test_app_groups():
    print("[test] 应用分组自定义（覆盖层分类 + API 增删改移出）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("groups")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "steam.exe", "app": "Steam", "title": "Steam", "category": "游戏",
            "contact": None, "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")
    with open(os.path.join(tmp, day, "software_inventory.json"), "w", encoding="utf-8") as fh:
        json.dump({"date": day, "count": 2, "apps": [
            {"name": "Steam", "exe": "steam.exe", "category": "游戏", "source": ["registry"], "running": False},
            {"name": "WeChat", "exe": "wechat.exe", "category": "社交聊天", "source": ["registry"], "running": False},
        ]}, fh, ensure_ascii=False)

    cfg = classifier.load_config()
    cfg["data_root"] = tmp

    # 1) 覆盖层分类：steam -> 自定义分组
    classifier.save_app_groups(
        {"exe_groups": {"steam.exe": "我的分组"}, "custom_categories": ["我的分组"]}, tmp)
    check(classifier.classify_category("steam.exe", "", cfg) == "我的分组", "覆盖层优先", "游戏")
    check(classifier.classify_category("wechat.exe", "", cfg) == "社交聊天", "未覆盖应用不受影响")
    check("我的分组" in classifier.all_categories(cfg), "自定义分组出现在列表中")

    # 2) API：GET /api/groups
    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8"))
        conn.close()
        return r.status, data

    try:
        s, d = req("GET", "/api/groups")
        check(s == 200 and "apps" in d and "categories" in d, "GET /api/groups")
        check(any(a["exe"] == "steam.exe" and a["category"] == "我的分组" for a in d["apps"]),
              "API 反映覆盖层分类")
        check(any(a["exe"] == "wechat.exe" for a in d["apps"]), "已知应用列表含清单+usage exe")

        # 3) set 移出（恢复自动）
        s, d = req("POST", "/api/groups/set", {"exe": "steam.exe", "category": ""})
        check(s == 200 and d.get("ok") is True, "POST set 移出")
        check(classifier.classify_category("steam.exe", "", cfg) == "游戏", "移出后恢复自动分类")

        # 4) set 到内置分组
        req("POST", "/api/groups/set", {"exe": "steam.exe", "category": "影音娱乐"})
        check(classifier.classify_category("steam.exe", "", cfg) == "影音娱乐", "设置到内置分组")

        # 5) add 新分组（未知分组自动登记）
        s, d = req("POST", "/api/groups/add", {"name": "学习工具"})
        check(s == 200 and "学习工具" in d.get("categories", []), "新增分组")
        # 6) delete 分组（组内应用恢复自动）
        req("POST", "/api/groups/set", {"exe": "steam.exe", "category": "学习工具"})
        check(classifier.classify_category("steam.exe", "", cfg) == "学习工具", "移到新分组")
        req("POST", "/api/groups/delete", {"name": "学习工具"})
        check(classifier.classify_category("steam.exe", "", cfg) == "游戏", "删分组后恢复自动")
        s, d = req("GET", "/api/groups")
        check("学习工具" not in d["categories"], "分组已删除")

        # 7) 自定义显示名（客制化）
        s, d = req("POST", "/api/groups/rename", {"exe": "steam.exe", "display_name": "Steam 自定义名"})
        check(s == 200 and d.get("ok") is True, "重命名 API")
        s, d = req("GET", "/api/groups")
        steam = next(a for a in d["apps"] if a["exe"] == "steam.exe")
        check(steam["app"] == "Steam 自定义名", "显示名在列表中生效", str(steam))
        check(classifier.resolve_app_name("steam.exe", cfg) == "Steam 自定义名", "resolve_app_name 使用自定义名")

        # 8) 导出配置
        s, d = req("GET", "/api/groups/export")
        check(s == 200 and d.get("app_names", {}).get("steam.exe") == "Steam 自定义名",
              "导出包含自定义显示名", str(d))

        # 9) 导入配置（整份覆盖）
        import_groups = {
            "exe_groups": {"wechat.exe": "我的分组"},
            "custom_categories": ["我的分组"],
            "app_names": {"wechat.exe": "微信自定义"},
            "group_meta": {"我的分组": {"description": "测试描述"}},
        }
        s, d = req("POST", "/api/groups/import", import_groups)
        check(s == 200 and d.get("ok") is True, "导入 API")
        s, d = req("GET", "/api/groups")
        wechat = next(a for a in d["apps"] if a["exe"] == "wechat.exe")
        check(wechat["app"] == "微信自定义" and wechat["category"] == "我的分组",
              "导入后显示名/分组生效", str(wechat))
        check(d.get("group_meta", {}).get("我的分组", {}).get("description") == "测试描述",
              "导入后分组元数据生效", str(d.get("group_meta")))

        # 10) 恶意 Origin 对 POST 同样拒绝
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/groups/set", body='{"exe":"a","category":"b"}',
                     headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
        r = conn.getresponse()
        check(r.status == 403, "POST 恶意 Origin 拒绝", str(r.status))
        conn.close()

        # 11) 孤儿分组（指向不存在类别的 exe_groups）被剔除，不伪装成类别
        classifier.save_app_groups(
            {"exe_groups": {"steam.exe": "AI工具", "wechat.exe": "我的分组"},
             "custom_categories": ["我的分组"]}, tmp)
        orphan = classifier.sanitize_groups(cfg, classifier.load_app_groups(tmp))
        check("steam.exe" not in orphan["exe_groups"]
              and orphan["exe_groups"].get("wechat.exe") == "我的分组",
              "sanitize_groups 剔除孤儿映射、保留合法映射", str(orphan["exe_groups"]))
        check(classifier.classify_category("steam.exe", "", cfg) == "游戏",
              "孤儿映射被忽略，恢复自动分类")
        s, d = req("GET", "/api/groups")
        check(any(a["exe"] == "steam.exe" and a["category"] == "游戏" for a in d["apps"]),
              "API 不把孤儿分组伪装成类别")
        # 导入含孤儿映射时同样剔除
        s, d = req("POST", "/api/groups/import",
                   {"exe_groups": {"steam.exe": "AI工具"}, "custom_categories": []})
        check(s == 200 and "steam.exe" not in d.get("groups", {}).get("exe_groups", {}),
              "导入时剔除孤儿分组", str(d.get("groups")))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

def test_report_balloon_once_per_day():
    print("[test] 日报生成托盘通知调度（一天一次 / 晚启动不补弹 / 时间门槛）")
    root = fresh_tmp("balloon")
    day = _dt.date.today().isoformat()
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "report.md")

    def touch(t: _dt.datetime) -> None:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# x")
        os.utime(p, (t.timestamp(), t.timestamp()))

    # 场景 1：19:30 前启动 -> 武装；报告生成后弹一次，且不重复
    monitor._report_notified_day = None
    monitor._report_armed = False
    check(monitor.check_report_balloon(root, lambda: None) is False, "启动首查不弹（武装）")
    check(monitor._report_armed is True, "武装状态登记")
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(19, 30)))
    fired: list[int] = []
    check(monitor.check_report_balloon(root, lambda: fired.append(1)) is True, "生成后首次发现弹一次")
    check(monitor.check_report_balloon(root, lambda: fired.append(2)) is False, "同一天不重复弹")
    check(fired == [1], "恰好弹一次", str(fired))

    # 场景 2：19:30 后启动（报告已存在）-> 只登记不补弹
    monitor._report_notified_day = None
    monitor._report_armed = False
    fired = []
    check(monitor.check_report_balloon(root, lambda: fired.append(1)) is False, "晚启动不补弹")
    check(fired == [], "晚启动零通知", str(fired))

    # 场景 3：时间门槛——早于 19:25 生成的报告不算"刚生成"
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(8, 0)))
    check(monitor._today_report_recent(root, day) is False, "早于 19:25 不算刚生成")
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(19, 30)))
    check(monitor._today_report_recent(root, day) is True, "19:30 生成识别为刚生成")
    monitor._report_notified_day = None
    monitor._report_armed = False
    shutil.rmtree(root, ignore_errors=True)
