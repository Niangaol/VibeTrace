# -*- coding: utf-8 -*-
"""tests/integration/test_browser_history_pipeline.py — Chromium/Firefox 历史端到端."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import browser_history  # noqa: E402
import classifier  # noqa: E402


def _day_noon_ft(day_str: str, offset_s: int = 0) -> int:
    """以「指定日的正午」为锚的 FILETIME 微秒时间戳（测试确定性用）。

    造数时间戳与查询/断言用的日界字符串必须同源且远离日界：若用 time.time()
    锚定，测试在午夜附近运行时数据会落到 date.today() 的相邻日，日界分摊逻辑
    把份额切走，total_duration_s 偶发不等于期望值（曾出现 174.9 vs 180.0）。
    正午锚点距两个日界各约 12 小时裕量；day_str 由调用方先取好再传入，
    保证造数与查询永远指向同一天。
    """
    year, month, day = map(int, day_str.split("-"))
    noon = datetime.datetime(year, month, day, 12, 0)
    return int((noon.timestamp() + offset_s + 11644473600) * 1e6)


def test_chromium_collect_and_report(tmp_path):
    """合成 Chromium History -> collect 分类/时长 -> report_section 含汇总."""
    tmp = str(tmp_path / "bh1")
    os.makedirs(tmp, exist_ok=True)
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL, visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://www.bilibili.com/video/av1", "bilibili 视频"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://github.com/user/repo", "GitHub 代码"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/login?password=123", "敏感页"))
    # 先取日字符串再造数：时间戳锚定该日正午，与查询/断言单一来源（防午夜抖动）
    cfg = classifier.load_config()
    today = datetime.date.today().isoformat()
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (_day_noon_ft(today, -120),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (2, ?, 60000000)", (_day_noon_ft(today, -60),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (3, ?, 0)", (_day_noon_ft(today, 0),))
    conn.commit()
    conn.close()

    data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    assert data["count"] == 3
    by_url = {v["url"].split("?")[0]: v for v in data["visits"]}
    assert by_url["https://www.bilibili.com/video/av1"]["category"] == "视频"
    assert by_url["https://github.com/user/repo"]["category"] == "代码"
    # 黑名单掩蔽
    assert any(v["url"] == "[已隐藏]" for v in data["visits"])
    assert data["total_duration_s"] == 180.0
    # report_section
    section = browser_history.report_section(today, tmp, cfg, db_paths=[db])
    assert section is not None and "浏览器访问明细" in section
    assert "bilibili" in section
    assert "password" not in section
    print("  [PASS] chromium_collect_and_report")


def test_disabled_flag(tmp_path):
    tmp = str(tmp_path / "bh2")
    os.makedirs(tmp, exist_ok=True)
    cfg = classifier.load_config()
    cfg["browser_history_enabled"] = False
    today = datetime.date.today().isoformat()
    data = browser_history.collect(today, tmp, cfg, db_paths=[])
    assert data["enabled"] is False and data["count"] == 0
    print("  [PASS] disabled_flag")


def test_find_url_for_session_overlap(tmp_path):
    """find_url_for_session 按时间重叠匹配."""
    tmp2 = str(tmp_path / "bh3")
    os.makedirs(tmp2, exist_ok=True)
    db = os.path.join(tmp2, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL, visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    now = datetime.datetime.now()
    ft = int((now.timestamp() + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/page", "page"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (ft,))
    conn.commit()
    conn.close()
    cfg = classifier.load_config()
    hit = browser_history.find_url_for_session(now - datetime.timedelta(minutes=1), now, tmp2, cfg, db_paths=[db])
    assert hit == "https://example.com/page"
    miss = browser_history.find_url_for_session(now - datetime.timedelta(hours=3), now - datetime.timedelta(hours=2), tmp2, cfg, db_paths=[db])
    assert miss is None
    print("  [PASS] find_url_overlap")


def test_cross_day_split(tmp_path):
    """跨天访问按日界分摊两天."""
    tmp3 = str(tmp_path / "bh4")
    os.makedirs(tmp3, exist_ok=True)
    db = os.path.join(tmp3, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL, visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    y2330 = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=1), datetime.time(23, 30))
    ft_cross = int((time.mktime(y2330.timetuple()) + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://www.icourse163.org/course/cross", "跨天课"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, ?)", (ft_cross, 2 * 3600 * 1_000_000))
    conn.commit()
    conn.close()
    cfg = classifier.load_config()
    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    t_data = browser_history.collect(today, tmp3, cfg, db_paths=[db])
    y_data = browser_history.collect(yesterday, tmp3, cfg, db_paths=[db])
    assert any("cross" in v["url"] for v in t_data["visits"])
    assert any("cross" in v["url"] for v in y_data["visits"])
    # 时长分摊
    t_cross = [v for v in t_data["visits"] if "cross" in v["url"]][0]
    y_cross = [v for v in y_data["visits"] if "cross" in v["url"]][0]
    assert t_cross["duration_s"] == 5400.0
    assert y_cross["duration_s"] == 1800.0
    print("  [PASS] cross_day_split")
