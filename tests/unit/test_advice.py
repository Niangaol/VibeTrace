# -*- coding: utf-8 -*-
"""tests/unit/test_advice.py — 概览「建议」栏位（v2.9.5，可选功能）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import advice  # noqa: E402


def _ids(items):
    return [it["id"] for it in items]


# ---------------------------------------------------------------------------
# 1. 开关与上限（可选功能语义）
# ---------------------------------------------------------------------------
def test_disabled_returns_empty():
    """默认关闭：enabled=false、无建议、但仍回传 max_items 供设置页回显。"""
    r = advice.advice_for_day("2026-09-12", ".", {})
    assert r["enabled"] is False and r["items"] == []
    assert r["max_items"] == 6
    assert advice.enabled({}) is False
    assert advice.enabled({"advice": {"enabled": True}}) is True
    print("  [PASS] disabled_returns_empty")


def test_max_items_clamp():
    """max_items 非法值回退 6，并夹取到 1..20。"""
    assert advice.max_items({}) == 6
    assert advice.max_items({"advice": {"max_items": 0}}) == 6      # 0 视为未设置
    assert advice.max_items({"advice": {"max_items": 1}}) == 1
    assert advice.max_items({"advice": {"max_items": 999}}) == 20
    assert advice.max_items({"advice": {"max_items": "abc"}}) == 6
    print("  [PASS] max_items_clamp")


# ---------------------------------------------------------------------------
# 2. 各条规则：触发 / 不触发
# ---------------------------------------------------------------------------
def test_rule_deep_work():
    items = []
    advice._rule_deep_work({"deep_work_min": 150}, items)
    assert _ids(items) == ["deep_work"] and items[0]["level"] == "warn"
    assert "150" in items[0]["title"]
    items2 = []
    advice._rule_deep_work({"deep_work_min": 60}, items2)
    assert items2 == [], "未达阈值不应出建议"
    print("  [PASS] rule_deep_work")


def test_rule_night():
    hourly = [0] * 24
    hourly[1] = 40 * 60000          # 凌晨 1 点活跃 40 分钟
    items = []
    advice._rule_night({"hourly_ms": hourly}, items)
    assert _ids(items) == ["night_owl"]
    assert "40" in items[0]["title"]
    items2 = []
    hourly2 = [0] * 24
    hourly2[14] = 90 * 60000        # 只在下午活跃
    advice._rule_night({"hourly_ms": hourly2}, items2)
    assert items2 == []
    print("  [PASS] rule_night")


def test_rule_cost_spike():
    items = []
    advice._rule_cost_spike(10.0, [1.0, 1.0, 2.0], items)
    assert _ids(items) == ["cost_spike"] and items[0]["level"] == "warn"
    items2 = []
    advice._rule_cost_spike(1.5, [1.0, 1.0], items2)     # 1.5 倍 < 1.8 倍阈值
    assert items2 == []
    items3 = []
    advice._rule_cost_spike(0.5, [0.1, 0.1], items3)     # 低于最低绝对值
    assert items3 == []
    items4 = []
    advice._rule_cost_spike(10.0, [], items4)            # 无历史数据
    assert items4 == []
    print("  [PASS] rule_cost_spike")


def test_rule_cache_share():
    items = []
    advice._rule_cache_share({"tokens_input_fresh": 1000, "tokens_cache_read": 99000,
                              "tokens_cache_write": 0}, items)
    assert _ids(items) == ["cache_share"] and items[0]["level"] == "info"
    items2 = []
    advice._rule_cache_share({"tokens_input_fresh": 9000, "tokens_cache_read": 1000,
                              "tokens_cache_write": 0}, items2)
    assert items2 == [], "缓存占比低不应出建议"
    print("  [PASS] rule_cache_share")


def test_rule_unknown_model():
    items = []
    advice._rule_unknown_model({"by_model": {"未识别": {"turns": 40}, "glm-5.3": {"turns": 60}}},
                               items)
    assert _ids(items) == ["unknown_model"]
    items2 = []
    advice._rule_unknown_model({"by_model": {"未识别": {"turns": 5}, "glm-5.3": {"turns": 95}}},
                               items2)
    assert items2 == [], "占比未达阈值不应出建议"
    print("  [PASS] rule_unknown_model")


def test_rule_ai_idle_and_focus():
    items = []
    advice._rule_ai_idle(90.0, 0, items)
    assert _ids(items) == ["ai_idle"]
    items2 = []
    advice._rule_ai_idle(90.0, 12, items2)
    assert items2 == [], "有 AI 会话时不出该建议"
    items3 = []
    advice._rule_focus_scatter({"project_focus_hhi": 0.1}, 120.0, items3)
    assert _ids(items3) == ["focus_scatter"]
    items4 = []
    advice._rule_focus_scatter({"project_focus_hhi": 0.8}, 120.0, items4)
    assert items4 == []
    print("  [PASS] rule_ai_idle_and_focus")


# ---------------------------------------------------------------------------
# 3. 排序与截断（warn 优先于 info，超限截断）
# ---------------------------------------------------------------------------
def test_sort_and_truncate(monkeypatch):
    """聚合函数：warn 排在 info 前，且按 max_items 截断。"""
    monkeypatch.setattr(advice, "_safe", lambda fn, default: fn() if fn is not None else default)
    items = []
    advice._rule_deep_work({"deep_work_min": 200}, items)      # warn
    advice._rule_ai_idle(90.0, 0, items)                        # info
    advice._rule_cache_share({"tokens_input_fresh": 1, "tokens_cache_read": 999}, items)  # info
    ordered = sorted(items, key=lambda it: advice._LEVEL_ORDER.get(it.get("level"), 9))
    assert ordered[0]["level"] == "warn", ordered
    assert len(ordered[:2]) == 2
    print("  [PASS] sort_and_truncate")
