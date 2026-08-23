# -*- coding: utf-8 -*-
"""tests/support/scenario.py — 由 test_all.py 机械移植的共享测试支撑。

来源：test_all.py @ 7d60620 的助手段（ok/check/fresh_tmp/FG/FakeClock/run_scenario）
与 _make_fake_agg。check() 失败即 raise AssertionError，天然兼容 pytest。
run_scenario 对 win32core / monitor 时间做的猴子补丁均在 finally 中完整恢复，
各测试相互独立、可任意顺序执行。
"""

from __future__ import annotations

import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import classifier  # noqa: E402
import monitor  # noqa: E402
import win32core  # noqa: E402


TMP_ROOT = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "usage_monitor_tests")

PASSED = 0


def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  [PASS] {name}")


def fail(name: str, detail: str) -> None:
    print(f"  [FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def check(cond: bool, name: str, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def fresh_tmp(name: str) -> str:
    path = os.path.join(TMP_ROOT, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


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


def run_scenario(name: str, fg_list: list, idle_list=None, seconds: int = 9,
                 poll: int = 1, idle_threshold: int = 180,
                 process_tree: dict | None = None,
                 fg_pid_for_tree: int = 999) -> tuple[list[dict], str]:
    """在临时数据根目录跑一段 run_daemon，返回（写入记录列表, 数据根）。"""
    tmp = fresh_tmp(name)
    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["poll_interval_s"] = poll
    cfg["idle_threshold_s"] = idle_threshold
    # 测试隔离：finalize_day 会触发报表的 AI 会话/浏览器深度统计，若沿用全局
    # 配置将扫描开发机真实会话目录（分钟级且不确定）。场景测试一律关闭。
    cfg["ai_sessions"] = {"enabled": False}
    cfg["browser_history_enabled"] = False

    monitor.stop_event.clear()  # 防止前序测试残留的停止信号
    monitor.set_paused(False)

    clock = FakeClock(fg_list, idle_list)
    real_fg = win32core.get_foreground_info
    real_idle = win32core.idle_seconds
    real_procs = win32core.enum_processes
    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    if process_tree is not None:
        win32core.enum_processes = lambda: dict(process_tree)
    # 确定性时间：用伪时钟替代墙钟，避免 time.sleep 抖动导致 flaky（5000ms）
    import datetime as _dt_mod

    real_dt_class = monitor.datetime.datetime
    real_mono = monitor.time.monotonic
    real_sleep = monitor.time.sleep

    # 检测是否已有外部伪时间（如 test_day_rollover 的跨天伪造）
    is_custom_fake = hasattr(real_dt_class, "_cur") or getattr(real_dt_class, "__name__", "") == "FakeDT"

    fake_mono = [0.0]
    fake_dt = [None]
    _FakeDT = None
    if not is_custom_fake:
        start_dt = _dt_mod.datetime.now().replace(microsecond=0)
        fake_dt[0] = start_dt

        class _FakeDTCls(real_dt_class):  # type: ignore[valid-type]
            @classmethod
            def now(cls, tz=None):
                return fake_dt[0]

        _FakeDT = _FakeDTCls
        monitor.datetime.datetime = _FakeDT  # type: ignore[attr-defined]

        def _fake_sleep(secs):
            # 不真睡，推进伪时间
            fake_dt[0] = fake_dt[0] + _dt_mod.timedelta(seconds=float(secs))
            fake_mono[0] += float(secs)
    else:
        # 已有跨天伪造：sleep 只推进 monotonic，不重复推进日期（避免双倍步进）
        def _fake_sleep(secs):  # type: ignore[no-redef]
            fake_mono[0] += float(secs)

    def _fake_mono():
        return fake_mono[0]

    monitor.time.monotonic = _fake_mono  # type: ignore[attr-defined]
    monitor.time.sleep = _fake_sleep  # type: ignore[attr-defined]
    try:
        recs = monitor.run_daemon(cfg, test_seconds=seconds, verbose=False)
    finally:
        win32core.get_foreground_info = real_fg
        win32core.idle_seconds = real_idle
        win32core.enum_processes = real_procs
        if _FakeDT is not None:
            monitor.datetime.datetime = real_dt_class  # type: ignore[attr-defined]
        monitor.time.monotonic = real_mono  # type: ignore[attr-defined]
        monitor.time.sleep = real_sleep  # type: ignore[attr-defined]
    return recs, tmp


def _make_fake_agg(total_h: float = 6.0) -> dict:
    """构造足以触发全部规则类型的聚合结果。"""
    hour_ms = 3600000
    total_ms = int(total_h * hour_ms)
    return {
        "date": "2026-08-10",
        "session_count": 4,
        "total_active_ms": total_ms,
        "by_app": {"VS Code": 2 * hour_ms, "Steam": 3 * hour_ms, "微信": hour_ms},
        "by_category": {
            "办公学习": 1 * hour_ms,
            "游戏": 3 * hour_ms,
            "AI编程": hour_ms,
            "社交聊天": hour_ms,
            "浏览器": hour_ms,
        },
        "by_contact": {"微信": {"张三": 30 * 60000}},
        "by_ai": {"opencode": hour_ms},
        "by_browser": {"学习": 30 * 60000, "视频": 30 * 60000},
        "by_subcategory": {},
        "by_term_tool": {},
        "hourly_ms": [0] * 24,
        "sessions": [
            {"start": "2026-08-10T09:00:00", "end": "2026-08-10T10:40:00",
             "duration_ms": 100 * 60000, "app": "VS Code", "category": "办公学习"},
            {"start": "2026-08-10T14:00:00", "end": "2026-08-10T15:00:00",
             "duration_ms": 60 * 60000, "app": "Steam", "category": "游戏"},
        ],
    }




def _day_noon_ft(day_str: str, offset_s: int = 0) -> int:
    """以「指定日正午」为锚的 FILETIME 微秒时间戳（消除午夜抖动的确定性造数）。

    造数时间戳必须与查询用的日界字符串同源且远离日界：用 time.time() 锚定时，
    午夜附近运行会让数据落到相邻日，日界分摊切走时长份额（历史 flaky：
    total_duration_s 出现过 174.9 vs 180.0）。正午锚距两个日界各约 12 小时。
    """
    import datetime as _dtmod
    year, month, day = map(int, day_str.split("-"))
    noon = _dtmod.datetime(year, month, day, 12, 0)
    return int((noon.timestamp() + offset_s + 11644473600) * 1e6)
