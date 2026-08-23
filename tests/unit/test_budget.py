# -*- coding: utf-8 -*-
"""tests/unit/test_budget.py — 成本预算告警（v2.6 · P3）单元测试。

纯派生逻辑：monkeypatch `budget._collect_day` 注入成本，覆盖
预算关闭 / 未超 / 接近 / 超支 / 空数据 / 错误配置 / period 推断 / 周报汇总。
不触发真实数据扫描（_collect_day 一律注入，与 timeline 测试惯例一致）。
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import budget  # noqa: E402

ROOT = "X:/vt-unit"  # _collect_day 被注入后不会真读盘
DAY = "2026-08-20"
MONTH = "2026-08"
BASE = {"insights": {"budget": {"enabled": True, "daily": 10.0, "monthly": 200.0}}}
DISABLED = {"insights": {"budget": {"enabled": False, "daily": 10.0, "monthly": 200.0}}}


def _fake_collect(costs: dict):
    """按日期返回固定成本的 _collect_day 替身；未录日期 → 无数据。"""
    def _inner(date_str, _data_root, _config):
        if date_str not in costs:
            return {"found": False, "cost": 0.0, "by_tool": {}, "by_project": {}}
        c = float(costs[date_str])
        return {"found": True, "cost": c,
                "by_tool": {"opencode": c}, "by_project": {"Demo": c}}
    return _inner


def _st(monkeypatch, costs, config=None, date=DAY, period=None):
    monkeypatch.setattr(budget, "_collect_day", _fake_collect(costs))
    return budget.budget_status(date, ROOT, config if config is not None else BASE, period=period)


# ---------------------------------------------------------------------------
# 预算关闭（默认不打扰）
# ---------------------------------------------------------------------------
def test_disabled_by_default_no_budget_section():
    st = budget.budget_status(DAY, ROOT, {"insights": {}})
    assert st["enabled"] is False and st["status"] == "disabled"
    assert st["spent"] == 0.0 and st["budget"] == 10.0  # 预算值来自默认配置，但功能关闭


def test_disabled_skips_collect(monkeypatch):
    calls = []

    def fake(date_str, _r, _c):
        calls.append(date_str)
        return {"found": False, "cost": 0.0, "by_tool": {}, "by_project": {}}
    monkeypatch.setattr(budget, "_collect_day", fake)
    st = budget.budget_status(DAY, ROOT, DISABLED, period="daily")
    assert st["status"] == "disabled"
    assert calls == [], f"预算关闭时不应触发成本聚合: {calls}"


# ---------------------------------------------------------------------------
# 未超 / 接近 / 超支 与边界
# ---------------------------------------------------------------------------
def test_daily_ok(monkeypatch):
    st = _st(monkeypatch, {DAY: 1.2})
    assert st["status"] == "ok" and st["period"] == "daily"
    assert st["spent"] == 1.2 and st["budget"] == 10.0
    assert abs(st["ratio"] - 0.12) < 1e-9
    assert abs(st["remaining"] - 8.8) < 1e-9


def test_daily_warn(monkeypatch):
    st = _st(monkeypatch, {DAY: 8.5})
    assert st["status"] == "warn" and abs(st["ratio"] - 0.85) < 1e-9


def test_daily_warn_boundary_at_80_percent(monkeypatch):
    # ratio == 0.8 → 恰好进入 warn（>=0.8）
    st = _st(monkeypatch, {DAY: 8.0})
    assert st["status"] == "warn"


def test_daily_exceed(monkeypatch):
    st = _st(monkeypatch, {DAY: 12.34})
    assert st["status"] == "exceed"
    assert st["remaining"] < 0
    assert st["days"] == [{"date": DAY, "cost": 12.34}]


def test_daily_exceed_boundary_at_100_percent(monkeypatch):
    # ratio == 1.0 → 超支（>=1.0）
    st = _st(monkeypatch, {DAY: 10.0})
    assert st["status"] == "exceed"


def test_empty_data_ok(monkeypatch):
    # 无任何成本数据 → spent=0, 状态 ok（不告警）
    st = _st(monkeypatch, {})  # 所有日期无数据
    assert st["status"] == "ok" and st["spent"] == 0.0 and st["days"] == []


def test_dims_and_days_detail(monkeypatch):
    st = _st(monkeypatch, {DAY: 3.0})
    assert st["by_tool"] == {"opencode": 3.0}
    assert st["by_project"] == {"Demo": 3.0}
    assert len(st["days"]) == 1 and st["days"][0]["date"] == DAY


# ---------------------------------------------------------------------------
# 月度聚合（月初自然复位：范围 = 当月全部自然日）
# ---------------------------------------------------------------------------
def test_monthly_aggregation_ok(monkeypatch):
    costs = {"2026-08-10": 40.0, "2026-08-11": 50.0, "2026-08-20": 60.0}
    st = _st(monkeypatch, costs, date=MONTH)
    assert st["status"] == "ok" and st["period"] == "monthly"
    assert st["spent"] == 150.0 and st["budget"] == 200.0
    assert st["start"] == "2026-08-01" and st["end"] == "2026-08-31"
    # 明细只含找到数据的天
    assert [d["date"] for d in st["days"]] == ["2026-08-10", "2026-08-11", "2026-08-20"]


def test_monthly_aggregation_exceed(monkeypatch):
    costs = {"2026-08-05": 100.0, "2026-08-15": 90.0, "2026-08-25": 80.0}
    st = _st(monkeypatch, costs, date=MONTH)
    assert st["status"] == "exceed" and st["spent"] == 270.0


def test_monthly_empty(monkeypatch):
    st = _st(monkeypatch, {}, date=MONTH)
    assert st["status"] == "ok" and st["spent"] == 0.0


def test_month_days_leap_and_invalid():
    assert len(budget._month_days("2026-02")) == 28
    assert len(budget._month_days("2024-02")) == 29
    assert len(budget._month_days("2026-12")) == 31
    assert budget._month_days("bad") == []
    assert budget._month_days("2026-13") == []


# ---------------------------------------------------------------------------
# 错误配置（不打扰原则：无效档位视为未配置/关闭）
# ---------------------------------------------------------------------------
def test_invalid_daily_budget_disables_daily(monkeypatch):
    cfg = {"insights": {"budget": {"enabled": True, "daily": "abc", "monthly": 200.0}}}
    st = _st(monkeypatch, {DAY: 5.0}, config=cfg, period="daily")
    assert st["status"] == "disabled" and st["budget"] == 0.0
    assert any("daily" in i for i in st["issues"])


def test_nonpositive_budget_disables(monkeypatch):
    cfg = {"insights": {"budget": {"enabled": True, "daily": 0, "monthly": -1}}}
    st = _st(monkeypatch, {DAY: 5.0}, config=cfg, period="monthly")
    assert st["status"] == "disabled"
    assert len(st["issues"]) == 2  # daily=0 与 monthly=-1 都无效


def test_bad_budget_type_falls_back(monkeypatch):
    cfg = {"insights": {"budget": "not-a-dict"}}
    st = _st(monkeypatch, {DAY: 5.0}, config=cfg, period="daily")
    assert st["status"] == "disabled" and st["enabled"] is False


def test_invalid_date_daily_requires_full_day(monkeypatch):
    st = _st(monkeypatch, {}, date="2026-08", period="daily")
    assert st["status"] == "invalid"
    assert any("YYYY-MM-DD" in i for i in st["issues"])


def test_invalid_date_monthly(monkeypatch):
    st = _st(monkeypatch, {}, date="not-a-month", period="monthly")
    assert st["status"] == "invalid"


def test_invalid_period(monkeypatch):
    st = _st(monkeypatch, {}, period="weekly")
    assert st["status"] == "invalid"
    assert any("period" in i for i in st["issues"])


def test_valid_budget_on_explicit_monthly_with_day_date(monkeypatch):
    # 显式 period=monthly + 完整日期 → 宽容截取月份聚合（函数层语义）
    st = _st(monkeypatch, {DAY: 5.0}, date=DAY, period="monthly")
    assert st["period"] == "monthly" and st["spent"] == 5.0
    assert st["start"] == "2026-08-01"


# ---------------------------------------------------------------------------
# 文案层（报告小结 / 周报汇总）
# ---------------------------------------------------------------------------
def test_summary_md_states(monkeypatch):
    ok = _st(monkeypatch, {DAY: 1.0}, config=DISABLED, period="daily")
    assert budget.budget_summary_md(ok) is None  # disabled 不显示
    for cost, expect in ((1.0, "正常"), (8.0, "接近预算"), (12.0, "超支")):
        st = _st(monkeypatch, {DAY: cost}, period="daily")
        md = budget.budget_summary_md(st)
        assert md is not None and "AI 成本预算" in md and expect in md
    assert budget.budget_summary_md(None) is None
    assert budget.budget_summary_md({"status": "invalid"}) is None


def test_week_summary(monkeypatch):
    days = [f"2026-08-{d:02d}" for d in range(14, 21)]
    costs = {"2026-08-16": 12.0, "2026-08-17": 9.0, "2026-08-18": 8.5}  # 超支 / 接近(90%) / 接近(85%)
    monkeypatch.setattr(budget, "_collect_day", _fake_collect(costs))
    md = budget.budget_week_summary(days, ROOT, BASE)
    assert md is not None
    assert "超支 1 天" in md and "接近 2 天" in md and "正常 0 天" in md and "无数据 4 天" in md
    assert "08-16" in md  # 超支日明细
    assert "合计" in md and "$29.50" in md


def test_week_summary_disabled_returns_none():
    assert budget.budget_week_summary(["2026-08-14"], ROOT, DISABLED) is None


# ---------------------------------------------------------------------------
# _collect_day 与 ai_sessions/browser_history 的真实映射（非注入路径）
# ---------------------------------------------------------------------------
def test_collect_day_maps_sources(monkeypatch):
    import ai_sessions  # noqa: PLC0415
    import browser_history  # noqa: PLC0415

    def fake_bh(day, root, cfg):
        return {"visits": [{"url": "https://chat.example.com", "title": "x", "time": f"{day}T10:00:00"}]}

    def fake_collect(day, cfg, web_visits=None):
        assert web_visits, "应传入 web 访问"
        return {
            "found": True, "date": day, "enabled": True,
            "tools": {"opencode": {"cost_total": 2.0, "turns": 4}},
            "total": {"cost_total": 2.5, "by_project": {"Demo": {"cost_total": 2.5}}},
            "web_ai": {"found": False, "by_tool": {}},
        }
    monkeypatch.setattr(browser_history, "collect", fake_bh)
    monkeypatch.setattr(ai_sessions, "collect", fake_collect)
    out = budget._collect_day("2026-08-20", ROOT, BASE)
    assert out["found"] is True and out["cost"] == 2.5
    assert out["by_tool"] == {"opencode": 2.0}
    assert out["by_project"] == {"Demo": 2.5}


def test_collect_day_not_found_and_crash(monkeypatch):
    import ai_sessions  # noqa: PLC0415
    import browser_history  # noqa: PLC0415

    monkeypatch.setattr(browser_history, "collect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bh boom")))
    monkeypatch.setattr(ai_sessions, "collect",
                        lambda *a, **k: {"found": False, "total": {}})
    out = budget._collect_day("2026-08-20", ROOT, BASE)
    assert out["found"] is False and out["cost"] == 0.0  # Web 失败降级，不抛

    def boom(*a, **k):
        raise RuntimeError("collect boom")
    monkeypatch.setattr(ai_sessions, "collect", boom)
    out = budget._collect_day("2026-08-20", ROOT, BASE)
    assert out["found"] is False and out["cost"] == 0.0  # 解析异常降级

def test_month_days_boundary_months_do_not_raise():
    """9999-12 月末 +4 天跨 date 上限曾抛 OverflowError（except 漏接）→ 应按无效月返回空。"""
    assert budget._month_days("9999-12") == []
    assert budget._month_days("0000-01") == []
    # 正常月份不受影响：平年2月/闰年2月/大月
    assert len(budget._month_days("2026-02")) == 28
    assert len(budget._month_days("2028-02")) == 29
    assert len(budget._month_days("2026-12")) == 31


def test_budget_status_overflow_month_returns_invalid(tmp_path):
    """/api/budget 的 monthly 档传入边界月 → invalid 空态而非异常。"""
    cfg = budget.budget_config({"budget": {"enabled": True, "monthly": 10}})
    st = budget.budget_status("9999-12", str(tmp_path), cfg, period="monthly")
    assert st["status"] == "invalid"
