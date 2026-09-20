# -*- coding: utf-8 -*-
"""tests/unit/test_applog_note.py — 降级观测出口（v2.9.7）回归钉扎。

覆盖四件事：
1. note() 只记录不抛——即使 logger 本身出问题也不能影响主体降级分支；
2. 未 configure() 前必须完全安静（NullHandler 兜底，无 stdout/stderr 泄漏）；
3. configure() 之后必须落盘到 <data_root>/logs/app.log，并能被 read_recent 读到
   （这样仪表盘「日志」视图与排查数据偏差才有事实源）；
4. configure() 幂等：重复调用不会重复挂 handler，也不会改写既有日志目标
   （这是 v2.9.7 前既有约定，note() 不得破坏它）。
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import applog  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_applog():
    """每个用例前重置 applog 全局状态（configure() 本身是幂等的单例式初始化）。"""
    root_logger = logging.getLogger("usagemon")
    saved = list(root_logger.handlers)
    saved_flag = applog._configured
    saved_root = applog._default_root
    root_logger.handlers.clear()
    applog._configured = False
    applog._default_root = None
    try:
        yield
    finally:
        for h in list(root_logger.handlers):
            h.close()
        root_logger.handlers.clear()
        root_logger.handlers.extend(saved)
        applog._configured = saved_flag
        applog._default_root = saved_root


def test_note_never_raises():
    """note() 自身永不抛异常（观测失败不能影响主体功能）。"""
    applog.note(ValueError("boom"), "unit: note_never_raises")
    applog.note(RuntimeError("x"), "unit: y", level="debug")
    applog.note(OSError("z"), "unit: w", level="error")


def test_note_silent_before_configure(capsys):
    """configure() 之前：零输出。

    0 输出是硬要求——CLI / 测试环境中 note() 不得污染 stdout/stderr。
    """
    applog.note(OSError("nope"), "unit: silent_before_configure")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_note_lands_in_applog_after_configure(tmp_path):
    """configure() 之后：写入 <data_root>/logs/app.log 且可回读。"""
    root = str(tmp_path / "data")
    applog.configure(root)
    applog.note(ValueError("定价文件损坏"), "unit: note_after_configure")
    path = applog.log_path(root)
    assert os.path.isfile(path), path
    text = open(path, "r", encoding="utf-8").read()
    assert "unit: note_after_configure" in text
    assert "定价文件损坏" in text


def test_read_recent_picks_up_note(tmp_path):
    """note() 的记录能被 read_recent 读回（仪表盘日志视图链路）。"""
    root = str(tmp_path / "data")
    applog.configure(root)
    applog.note(KeyError("missing"), "unit: read_recent_link")
    recent = applog.read_recent(root, 50)
    assert any("unit: read_recent_link" in ln for ln in recent), recent


def test_configure_is_idempotent(tmp_path):
    """重复 configure() 不重复挂 handler，也不改写日志目标。"""
    root = str(tmp_path / "data")
    applog.configure(root)
    first = len(logging.getLogger("usagemon").handlers)
    applog.configure(str(tmp_path / "other"))
    assert len(logging.getLogger("usagemon").handlers) == first
    applog.note(ValueError("idem"), "unit: idempotent")
    assert os.path.isfile(applog.log_path(root))


def test_note_debug_filtered_by_info_root(tmp_path):
    """level=debug 在 INFO 级别的 root logger 下不落盘（避免噪音）。"""
    root = str(tmp_path / "data")
    applog.configure(root)
    applog.note(ValueError("quiet"), "unit: debug_level", level="debug")
    text = open(applog.log_path(root), "r", encoding="utf-8").read()
    assert "unit: debug_level" not in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))