# -*- coding: utf-8 -*-
"""tests/unit/test_find_url_no_copy.py - v2.9.9 免整库拷贝回归钉扎。

历史：find_url_for_session 每次调用都 copy2 整个浏览器 History 到临时目录再查。
Chrome History 常态 50-300MB，5 秒轮询的 monitor 在每次会话关闭时都会付一次
秒级 I/O。现改为直读源库（_query_source_ro），本文件确保这个修复不被回退，
且 -wal 优先 / immutable 兜底 / 错误降级三条路径都守住。
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import browser_history  # noqa: E402


def _make_chrome_db(path: str, rows: list) -> None:
    """构造最小 Chromium History（urls + visits 两表）。"""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute(
        "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, "
        "visit_time INTEGER, visit_duration INTEGER)"
    )
    for i, (vt, dur, url) in enumerate(rows, start=1):
        conn.execute("INSERT INTO urls (id, url) VALUES (?, ?)", (i, url))
        conn.execute(
            "INSERT INTO visits (id, url, visit_time, visit_duration) "
            "VALUES (?, ?, ?, ?)",
            (i, i, vt, dur),
        )
    conn.commit()
    conn.close()


def _window(minutes=1):
    """返回 (start, end, filetime) 三元组。"""
    now = browser_history.datetime.datetime.now()
    start = now - browser_history.datetime.timedelta(minutes=minutes)
    ft = int((now.timestamp() + browser_history._FILETIME_EPOCH_OFFSET) * 1e6)
    return start, now, ft


def test_no_temp_copy_directory_created(tmp_path, monkeypatch):
    """核心不变量：查询期间不得创建任何临时拷贝目录。"""
    created = []
    real_mkdtemp = browser_history.tempfile.mkdtemp

    def spy_mkdtemp(*a, **kw):
        created.append("mkdtemp")
        return real_mkdtemp(*a, **kw)

    monkeypatch.setattr(browser_history.tempfile, "mkdtemp", spy_mkdtemp)
    db = str(tmp_path / "History")
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://example.com/a")])
    hit = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert hit == "https://example.com/a"
    assert created == [], "不应再走整库拷贝路径"


def test_copy_db_not_called(tmp_path, monkeypatch):
    """_copy_db 在 find_url_for_session 链路上不得被调用。"""
    calls = []
    monkeypatch.setattr(browser_history, "_copy_db",
                        lambda *a, **k: calls.append(a) or None)
    db = str(tmp_path / "History")
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://example.com/b")])
    browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert calls == []

def test_wal_present_prefers_mode_ro(tmp_path, monkeypatch):
    """有 -wal 时优先 mode=ro（能重放未 checkpoint 的记录）。"""
    opened = []
    real_connect = sqlite3.connect

    def spy_connect(u, *a, **kw):
        opened.append(u)
        return real_connect(u, *a, **kw)

    monkeypatch.setattr(browser_history.sqlite3, "connect", spy_connect)
    db = str(tmp_path / "History")
    open(db + "-wal", "wb").close()
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://example.com/c")])
    hit = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert hit == "https://example.com/c", hit
    assert any("mode=ro" in u and "immutable" not in u for u in opened), opened


def test_locked_source_falls_back_to_immutable(tmp_path, monkeypatch):
    """mode=ro 被锁时退 immutable=1（不抛异常、不返回空）。"""
    real_connect = sqlite3.connect

    def spy_connect(u, *a, **kw):
        if "immutable" not in u:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(u, *a, **kw)

    db = str(tmp_path / "History")
    open(db + "-wal", "wb").close()
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://example.com/d")])
    monkeypatch.setattr(browser_history.sqlite3, "connect", spy_connect)
    hit = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert hit == "https://example.com/d", hit


def test_sqlite_error_returns_empty_not_raise(tmp_path):
    """源库损坏时返回 []（查询失败不拖垮会话落盘）。"""
    db = str(tmp_path / "History")
    open(db, "wb").write(b"not a sqlite database at all")
    assert browser_history._query_source_ro(db, "SELECT 1", ()) == []


def test_no_wal_skips_mode_ro_attempt(tmp_path, monkeypatch):
    """无 -wal 时直接 immutable（不白等 timeout，也更不易被锁）。"""
    opened = []
    real_connect = sqlite3.connect

    def spy_connect(u, *a, **kw):
        opened.append(u)
        return real_connect(u, *a, **kw)

    db = str(tmp_path / "History")
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://example.com/e")])
    assert not os.path.isfile(db + "-wal")
    monkeypatch.setattr(browser_history.sqlite3, "connect", spy_connect)
    hit = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert hit == "https://example.com/e"
    uris = [u for u in opened if u.startswith("file:")]
    assert uris and all("immutable" in u for u in uris), uris


def test_db_change_is_visible_immediately(tmp_path):
    """库内容变化后立即反映（直读无缓存，不会返回旧 URL）。"""
    db = str(tmp_path / "History")
    start, end, ft = _window()
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://old.example/x")])
    first = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert first == "https://old.example/x"
    _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://new.example/y")])
    second = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
    assert second == "https://new.example/y", "库已变却仍返回旧 URL"


def test_blacklisted_url_masked(tmp_path):
    """命中黑名单的 URL 仍掩蔽为 [已隐藏]（行为不回退）。"""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(browser_history.classifier, "is_blacklisted_title", lambda t, c: True)
    try:
        db = str(tmp_path / "History")
        start, end, ft = _window()
        _make_chrome_db(db, [(ft - 1_000_000, 5_000_000, "https://secret.example/z")])
        hit = browser_history.find_url_for_session(start, end, str(tmp_path), {}, db_paths=[db])
        assert hit == "[已隐藏]"
    finally:
        monkey.undo()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))