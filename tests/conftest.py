# -*- coding: utf-8 -*-
"""tests/conftest.py — pytest 全局 fixtures。

复用 test_all.py 的 FG / P / FakeClock 思路，改用 pytest monkeypatch/fixtures 风格。
零第三方依赖（仅 pytest）。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import threading

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import classifier  # noqa: E402
import dashboard  # noqa: E402
import monitor  # noqa: E402
import win32core  # noqa: E402

TMP_ROOT = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "usage_monitor_pytest")


# ---------------------------------------------------------------------------
# 数据模型（与 test_all.py 的 FG / P 等价）
# ---------------------------------------------------------------------------
class FG:
    def __init__(self, exe: str, title: str, pid: int = 999):
        self.exe = exe
        self.title = title
        self.pid = pid
        self.hwnd = 1


class P:
    def __init__(self, exe: str, ppid: int, pid: int):
        self.exe = exe
        self.ppid = ppid
        self.pid = pid


class FakeClock:
    """按轮询序号输出前台窗口与空闲秒数（最后一个元素重复）。

    每个轮询中 monitor 先调 idle_seconds() 再调 get_foreground_info()，
    因此 idle_now 不推进索引，fg_now 推进（两者对齐同一轮）。
    """

    def __init__(self, fg_list: list, idle_list: list | None = None):
        self.fg = fg_list
        self.idle = idle_list or [1.0]
        self.i = 0

    def fg_now(self):
        i = min(self.i, len(self.fg) - 1)
        self.i += 1
        return self.fg[i]

    def idle_now(self):
        i = min(self.i, len(self.idle) - 1)
        return self.idle[i]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_root() -> str:
    """提供隔离的临时数据根目录；测试结束后自动清理。"""
    path = os.path.join(TMP_ROOT, "pytest_tmp")
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def mock_config(monkeypatch) -> dict:
    """返回一份可安全修改的配置副本，data_root 指向临时目录。"""
    cfg = classifier.load_config()
    # 深拷贝避免污染全局缓存
    cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
    cfg.setdefault("data_root", os.path.join(TMP_ROOT, "pytest_tmp"))
    cfg.setdefault("poll_interval_s", 1)
    cfg.setdefault("idle_threshold_s", 180)
    return cfg


@pytest.fixture
def fake_clock():
    """返回 FakeClock 工厂函数。"""
    def _make(fg_list, idle_list=None):
        return FakeClock(fg_list, idle_list)
    return _make


@pytest.fixture
def mock_win32(monkeypatch):
    """猴子补丁 win32core 的 get_foreground_info / idle_seconds / enum_processes。

    用法：
        clock = FakeClock([FG("a.exe", "t")] * 3)
        mock_win32(clock)
        # ... 调用 monitor.run_daemon ...
    """
    def _apply(clock: FakeClock | None = None, process_tree: dict | None = None):
        if clock is not None:
            monkeypatch.setattr(win32core, "get_foreground_info", clock.fg_now)
            monkeypatch.setattr(win32core, "idle_seconds", clock.idle_now)
        if process_tree is not None:
            monkeypatch.setattr(win32core, "enum_processes", lambda: dict(process_tree))
    return _apply


@pytest.fixture(autouse=True)
def _reset_monitor_state(monkeypatch):
    """每个测试前重置 monitor 的停止事件与暂停状态，防止测试间泄漏。"""
    monitor.stop_event.clear()
    monitor.set_paused(False)
    yield
    monitor.stop_event.clear()
    monitor.set_paused(False)
    # 释放 SQLite 共享连接（Windows 下打开的句柄会阻塞临时目录清理）
    import sqlite_store
    sqlite_store.close_connections()
    # 清 ai_sessions 结果/指纹缓存：指纹带短 TTL，跨测试同目录改文件会读到旧值
    import ai_sessions
    ai_sessions.invalidate_collect_cache()


# ---------------------------------------------------------------------------
# Dashboard 测试设施：种子数据 + 本地服务器 + HTTP 客户端
# ---------------------------------------------------------------------------
def make_record(date: str, start_h: int, minutes: float, exe: str = "code.exe",
                app: str = "VS Code", title: str = "a.py", category: str = "开发工具",
                contact: str | None = None, ai_tool: str | None = None,
                active: bool = True) -> dict:
    """构造一条标准 usage.jsonl 会话记录（start_h 为起始小时，可带小数）。"""
    start_min = int(round(start_h * 60))
    end_min = start_min + int(round(minutes))
    fmt = lambda m: f"{date}T{m // 60:02d}:{m % 60:02d}:00"  # noqa: E731
    return {
        "start": fmt(start_min), "end": fmt(end_min),
        "duration_ms": int(minutes * 60000),
        "exe": exe, "app": app, "title": title, "category": category,
        "contact": contact, "ai_tool": ai_tool, "active": active,
    }


def seed_day(root: str, date: str, records: list[dict]) -> str:
    """向 <root>/<date>/usage.jsonl 写入会话记录，返回日期目录路径。"""
    day_dir = os.path.join(root, date)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return day_dir


class ApiClient:
    """本地仪表盘 HTTP 客户端（http.client，零依赖）。

    request() 返回 (status, json_or_{"_raw": text}, headers)；
    二进制响应（zip 等）保留在 self.raw 供后续断言。
    """

    def __init__(self, port: int):
        self.port = port
        self.raw = b""

    def request(self, method: str, path: str, headers: dict | None = None,
                body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        data = None
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                data = bytes(body)
            else:
                data = body if isinstance(body, str) else json.dumps(body)
                data = data.encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        hdr = dict(resp.getheaders())
        conn.close()
        self.raw = raw
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"_raw": raw.decode("utf-8", errors="ignore")}
        return resp.status, parsed, hdr

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.request("POST", path, body=body, **kw)


@pytest.fixture
def api_server(tmp_path):
    """启动绑定随机端口的仪表盘服务器；yield (ApiClient, data_root)。

    - 预写 config.json 把 update.api_base 指向不可达地址，/api/update/check 快速失败不触网；
    - 启动前清空 dashboard 的日期缓存，避免跨测试污染；
    - 结束后自动 shutdown。
    """
    root = str(tmp_path / "api_root")
    os.makedirs(root, exist_ok=True)
    # update.api_base 指向不可达地址：/api/update/check 快速失败不触网；
    # ai_sessions.enabled=false：隔离开发机真实 AI 会话目录（月报成本账本扫描耗时且不确定）
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"update": {"api_base": "http://127.0.0.1:1"},
                   "ai_sessions": {"enabled": False}}, fh)
    dashboard.invalidate_days_cache()
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = ApiClient(port)
    try:
        yield client, root
    finally:
        server.shutdown()
        server.server_close()
        dashboard.invalidate_days_cache()
