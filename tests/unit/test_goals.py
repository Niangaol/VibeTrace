# -*- coding: utf-8 -*-
"""tests/unit/test_goals.py — 每日目标与 streak（配置归一化/进度/连续达成回推）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import goals  # noqa: E402

from tests.conftest import make_record, seed_day  # noqa: E402


def _cfg(**kw):
    return {"goals": {"enabled": True, "daily_active_min": 60, "daily_coding_min": 30, **kw}}


def test_goals_config_normalization():
    # 缺省：关闭
    cfg = goals.goals_config({})
    assert cfg == {"enabled": False, "daily_active_min": 0, "daily_coding_min": 0}
    # 非法数值回退 0、夹取 [0,1440]
    cfg2 = goals.goals_config({"goals": {"enabled": 1, "daily_active_min": -5,
                                         "daily_coding_min": "abc"}})
    assert cfg2["enabled"] is True
    assert cfg2["daily_active_min"] == 0
    assert cfg2["daily_coding_min"] == 0
    cfg3 = goals.goals_config({"goals": {"daily_active_min": 99999}})
    assert cfg3["daily_active_min"] == 1440
    print("  [PASS] goals_config_normalization")


def test_goal_defs_zero_means_off():
    defs = goals.goal_defs(goals.goals_config({"goals": {"enabled": True}}))
    assert defs == [], "全 0 目标不应生成定义"
    defs2 = goals.goal_defs(goals.goals_config({"goals": {"enabled": True,
                                                         "daily_active_min": 60}}))
    assert len(defs2) == 1 and defs2[0]["id"] == "active"
    print("  [PASS] goal_defs_zero_means_off")


def test_today_progress_disabled_short_circuit(tmp_path):
    out = goals.today_progress("2099-04-01", str(tmp_path), {"goals": {"enabled": False}})
    assert out["enabled"] is False and out["goals"] == []
    print("  [PASS] today_progress_disabled_short_circuit")


def test_today_progress_met_and_unmet(tmp_path):
    root = str(tmp_path)
    day = "2099-04-02"
    # 总活跃 75 分钟（达标 ≥60）；编码仅 25 分钟（未达 ≥30，游戏不计入编码）
    seed_day(root, day, [
        make_record(day, 9, 50, exe="steam.exe", app="Steam", category="游戏"),
        make_record(day, 11, 25),
    ])
    out = goals.today_progress(day, root, _cfg())
    assert out["enabled"] is True and len(out["goals"]) == 2
    by_id = {g["id"]: g for g in out["goals"]}
    assert by_id["active"]["actual_min"] == 75 and by_id["active"]["met"] is True
    assert by_id["coding"]["actual_min"] == 25 and by_id["coding"]["met"] is False
    assert out["all_met"] is False
    print("  [PASS] today_progress_met_and_unmet")


def test_streak_consecutive_and_gap_breaks(tmp_path):
    root = str(tmp_path)
    cfg = _cfg(daily_active_min=60, daily_coding_min=0)
    # 连续三天达标（每天 60 分钟）
    for d in ("2099-04-10", "2099-04-11", "2099-04-12"):
        seed_day(root, d, [make_record(d, 9, 60)])
    streak, met = goals.compute_streak("2099-04-12", root, goals.goals_config(cfg))
    assert streak == 3 and met is True
    # 中断一天（13 日无数据）→ 从 12 日回看为 1
    streak2, _ = goals.compute_streak("2099-04-14", root, goals.goals_config(cfg))
    assert streak2 == 0, "缺数据的自然日应断签（14 日无数据，今日未达成且昨日缺失）"
    seed_day(root, "2099-04-14", [make_record("2099-04-14", 9, 60)])
    streak3, _ = goals.compute_streak("2099-04-14", root, goals.goals_config(cfg))
    assert streak3 == 1, "13 日缺失应断签，只计当日"
    print("  [PASS] streak_consecutive_and_gap_breaks")


def test_streak_sees_newly_seeded_day_under_coarse_mtime(tmp_path, monkeypatch):
    """CI 快速盘回归：目录 mtime 时钟粒度内「播种→读取」也必须看到新日期。

    历史（v2.9.12 修复）：goals 曾自持一份 _DAYS_CACHE，只靠 mtime 失效；
    CI 快速盘同一时钟刻度内播种新日期后 mtime 不变，缓存不失效，
    compute_streak 拿到旧日期列表 → 当日已达成却判 streak=0（真实 CI 失败）。
    现委托 dashboard_util 单一实现 + seed_day 主动失效，与时钟粒度无关。
    """
    import dashboard_util

    root = str(tmp_path)
    cfg = _cfg(daily_active_min=60, daily_coding_min=0)
    for d in ("2099-04-10", "2099-04-11", "2099-04-12"):
        seed_day(root, d, [make_record(d, 9, 60)])
    # 先建缓存（此时 04-14 不存在）
    streak0, _ = goals.compute_streak("2099-04-14", root, goals.goals_config(cfg))
    assert streak0 == 0
    # 模拟粗粒度文件系统时钟：目录 mtime 恒定，缓存无法靠 mtime 自然失效
    monkeypatch.setattr(dashboard_util, "_days_mtime", lambda dr: 1000.0)
    # 同一时钟刻度内播种新日期并读取
    seed_day(root, "2099-04-14", [make_record("2099-04-14", 9, 60)])
    streak3, met3 = goals.compute_streak("2099-04-14", root, goals.goals_config(cfg))
    assert met3 is True and streak3 == 1, "粗粒度时钟下播种新日期后应只计当日"
    print("  [PASS] streak_sees_newly_seeded_day_under_coarse_mtime")


def test_streak_today_unmet_keeps_yesterday(tmp_path):
    root = str(tmp_path)
    cfg = _cfg(daily_active_min=60, daily_coding_min=0)
    for d in ("2099-04-20", "2099-04-21"):
        seed_day(root, d, [make_record(d, 9, 60)])
    # 今天（22 日）只有 10 分钟 → 未达成不断签，streak 保持昨天的 2
    seed_day(root, "2099-04-22", [make_record("2099-04-22", 9, 10)])
    streak, met = goals.compute_streak("2099-04-22", root, goals.goals_config(cfg))
    assert streak == 2 and met is False
    print("  [PASS] streak_today_unmet_keeps_yesterday")


def test_streak_empty_goals_is_zero(tmp_path):
    root = str(tmp_path)
    seed_day(root, "2099-04-30", [make_record("2099-04-30", 9, 600)])
    streak, met = goals.compute_streak("2099-04-30", root,
                                       goals.goals_config({"goals": {"enabled": True}}))
    assert (streak, met) == (0, False), "无生效目标不构成 streak"
    print("  [PASS] streak_empty_goals_is_zero")
