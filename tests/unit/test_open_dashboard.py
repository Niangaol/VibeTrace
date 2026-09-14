# -*- coding: utf-8 -*-
"""tests/unit/test_open_dashboard.py — 托盘单击「打开仪表盘」的优先级（Electron 壳优先）。

回归背景：v2.9.5 曾把默认反转为「浏览器优先」，导致单击托盘图标不再唤出 Electron
桌面应用窗口（用户报障）。本文件钉扎正确行为：
- 有 Electron 壳 → 启动壳，且**不开浏览器**；
- 无壳 / USAGEMON_USE_BROWSER=1 → 回退浏览器。
"""

from __future__ import annotations

import os
import socket
import sys
import webbrowser

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import monitor  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_open_dashboard_prefers_electron_shell(monkeypatch):
    """有 Electron 壳时必须启动壳（独立应用窗口），绝不回退浏览器。"""
    monkeypatch.delenv("USAGEMON_USE_BROWSER", raising=False)
    monkeypatch.setattr(monitor, "_find_electron_shell",
                        lambda: ["C:/fake/electron.exe", "main.js"])
    popen_calls: list = []
    monkeypatch.setattr(monitor.subprocess, "Popen",
                        lambda cmd, **kw: popen_calls.append(cmd))
    opened: list = []
    monkeypatch.setattr(webbrowser, "open", lambda url, **kw: opened.append(url))

    monitor.open_dashboard("C:/fake/root", port=_free_port(), view="overview")

    assert popen_calls == [["C:/fake/electron.exe", "main.js"]], \
        f"有壳时应启动 Electron 壳，实际 {popen_calls}"
    assert opened == [], "有 Electron 壳时不应打开浏览器"
    print("  [PASS] open_dashboard_prefers_electron_shell")


def test_open_dashboard_browser_when_no_shell(monkeypatch):
    """找不到 Electron 壳时回退浏览器（端口已被占用分支：只开浏览器，不起服务）。"""
    monkeypatch.delenv("USAGEMON_USE_BROWSER", raising=False)
    monkeypatch.setattr(monitor, "_find_electron_shell", lambda: None)
    popen_calls: list = []
    monkeypatch.setattr(monitor.subprocess, "Popen",
                        lambda cmd, **kw: popen_calls.append(cmd))
    opened: list = []
    monkeypatch.setattr(webbrowser, "open", lambda url, **kw: opened.append(url))

    # 先占住端口：走「已有仪表盘实例」分支，避免测试里真的起服务线程
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        monitor.open_dashboard("C:/fake/root", port=port)
    finally:
        srv.close()

    assert popen_calls == [], "无壳时不应尝试启动 Electron"
    assert opened and opened[0].startswith(f"http://127.0.0.1:{port}"), \
        f"无壳时应回退浏览器，实际 {opened}"
    print("  [PASS] open_dashboard_browser_when_no_shell")


def test_open_dashboard_force_browser_env(monkeypatch):
    """USAGEMON_USE_BROWSER=1 强制走浏览器（即使有壳）——调试逃生开关。"""
    monkeypatch.setenv("USAGEMON_USE_BROWSER", "1")
    monkeypatch.setattr(monitor, "_find_electron_shell",
                        lambda: ["C:/fake/electron.exe", "main.js"])
    popen_calls: list = []
    monkeypatch.setattr(monitor.subprocess, "Popen",
                        lambda cmd, **kw: popen_calls.append(cmd))
    opened: list = []
    monkeypatch.setattr(webbrowser, "open", lambda url, **kw: opened.append(url))

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        monitor.open_dashboard("C:/fake/root", port=port)
    finally:
        srv.close()

    assert popen_calls == [], "强制浏览器开关生效时不应启动 Electron"
    assert opened, "应打开浏览器"
    print("  [PASS] open_dashboard_force_browser_env")
