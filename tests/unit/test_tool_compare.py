# -*- coding: utf-8 -*-
"""tests/unit/test_tool_compare.py — 多工具横向对比（v2.6 · P6）单元测试。

覆盖：
  配置兜底、日期列表归一化（排序幂等/去重/非法/超范围）、排序与 top 截断、契约空态；
  聚合链路（compare_tools + _merge_tool_stats + _derive_metrics，monkeypatch 两数据源）：
  跨天求和/session·minutes 口径、除零兜底（0/0 → None/0）、share_pct、默认排序
  chars_per_dollar 降序且 None 排最后、enabled=false 空态、project 模糊过滤
  （会话/tokens/成本/分钟收窄，generated_* 不可得 → None）。

零依赖、确定性；不触发真实数据扫描（collect 相关用例一律 monkeypatch）。
"""

from __future__ import annotations

import datetime
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import tool_compare  # noqa: E402


# ---------------------------------------------------------------------------
# compare_config —— 默认兜底 / 覆盖 / 坏类型
# ---------------------------------------------------------------------------
class TestCompareConfig:
    def test_defaults_when_missing(self):
        cfg = tool_compare.compare_config({})
        assert cfg["enabled"] is True
        assert cfg["sort_by"] == "chars_per_dollar"
        assert cfg["top"] == 10
        assert cfg["min_sessions"] == 1
        assert cfg["max_days"] == 90

    def test_override(self):
        cfg = tool_compare.compare_config(
            {"tool_compare": {"enabled": False, "sort_by": "quality_avg", "top": 3, "max_days": 30}})
        assert cfg["enabled"] is False
        assert cfg["sort_by"] == "quality_avg"
        assert cfg["top"] == 3
        assert cfg["max_days"] == 30

    def test_bad_types_fall_back(self):
        cfg = tool_compare.compare_config({"tool_compare": {"top": "x", "max_days": None}})
        assert cfg["top"] == 10
        assert cfg["max_days"] == 90

    def test_negative_top_clamped(self):
        cfg = tool_compare.compare_config({"tool_compare": {"top": -5}})
        assert cfg["top"] == 0  # <=0 表示不截断


# ---------------------------------------------------------------------------
# _validate_days —— 排序幂等 / 去重 / 非法 / 超范围
# ---------------------------------------------------------------------------
class TestValidateDays:
    def test_sorts_and_dedups(self):
        days = tool_compare._validate_days(["2026-08-20", "2026-08-10", "2026-08-10"], tool_compare.compare_config({}))
        assert days == ["2026-08-10", "2026-08-20"]

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            tool_compare._validate_days(["2026-08-20", "not-a-date"], tool_compare.compare_config({}))

    def test_too_many_days_raises(self):
        # 91 个唯一日期 > max_days=90（注意先去重再判长度，重复日期不计入）
        days = [(datetime.date(2025, 1, 1) + datetime.timedelta(days=i)).isoformat() for i in range(91)]
        with pytest.raises(ValueError):
            tool_compare._validate_days(days, tool_compare.compare_config({}))

    def test_duplicates_within_limit_ok(self):
        # 10 个唯一日期重复 10 次：去重后 10 个 ≤ 90，合法
        days = [f"2026-08-{d:02d}" for d in range(1, 11)] * 10
        assert len(tool_compare._validate_days(days, tool_compare.compare_config({}))) == 10

    def test_empty_ok(self):
        assert tool_compare._validate_days([], tool_compare.compare_config({})) == []


# ---------------------------------------------------------------------------
# _sort_tools —— None 排最后 / top 截断
# ---------------------------------------------------------------------------
class TestSortTools:
    def test_none_sorted_last(self):
        rows = [
            {"tool": "a", "chars_per_dollar": None},
            {"tool": "b", "chars_per_dollar": 10.0},
            {"tool": "c", "chars_per_dollar": 5.0},
        ]
        out = tool_compare._sort_tools(rows, "chars_per_dollar", 0)
        assert [r["tool"] for r in out] == ["b", "c", "a"]

    def test_top_truncates(self):
        rows = [{"tool": f"t{i}", "chars_per_dollar": float(i)} for i in range(10)]
        out = tool_compare._sort_tools(rows, "chars_per_dollar", 3)
        assert len(out) == 3


# ---------------------------------------------------------------------------
# 契约空态
# ---------------------------------------------------------------------------
class TestEmptyResult:
    def test_empty_contract(self):
        res = tool_compare._empty_result("2026-08-10", "2026-08-20", ["2026-08-10"])
        assert res["tools"] == []
        assert res["summary"] == {"tools": 0, "total_sessions": 0, "total_cost": 0.0, "total_minutes": 0.0}
        assert "仅参考" in res["notice"]


# ---------------------------------------------------------------------------
# 构造助手（实现阶段新增：compare_tools 聚合链路全部 monkeypatch 数据源）
# ---------------------------------------------------------------------------
def _stat(tool, day, *, tokens=12000, cost=0.3, chars=60000, lines=800,
          rounds=8, n_conv=4, project="VibeTrace", scores=None):
    """某工具某天 collect().tools[tool] 的形态（对齐 _conversation_summary 输出）。"""
    scores = scores if scores is not None else [70, 72, 88, 60]
    convs = []
    for i in range(n_conv):
        sc = scores[i % len(scores)]
        convs.append({
            "id": f"{tool}-{day}-{i}", "tool": tool, "model": "deepseek",
            "project": project, "turns": 5, "rounds": rounds // max(n_conv, 1),
            "user_messages": 2, "assistant_messages": 3,
            "tokens_total": tokens // max(n_conv, 1), "cost_total": cost / max(n_conv, 1),
            "quality_score": sc, "quality_grade": "优" if sc >= 80 else "良",
        })
    return {
        "files": 1, "turns": 20, "rounds": rounds, "user_messages": 8,
        "assistant_messages": 12, "generated_lines": lines, "generated_chars": chars,
        "tokens_in": tokens // 2, "tokens_out": tokens // 2, "tokens_total": tokens,
        "cost_in": cost / 2, "cost_out": cost / 2, "cost_total": cost,
        "by_model": {},
        "by_project": {project: {"turns": 20, "tokens_in": tokens // 2,
                                 "tokens_out": tokens // 2, "tokens_total": tokens,
                                 "cost_in": cost / 2, "cost_out": cost / 2,
                                 "cost_total": cost}},
        "conversations": convs,
    }


def _patch_sources(monkeypatch, per_day, minutes_ms, sessions=None):
    """monkeypatch tool_compare 的两数据源；per_day: {day: {tool: stats}}。"""
    def fake_collect(day, config):
        return {"date": day, "enabled": True, "found": bool(per_day.get(day)),
                "tools": per_day.get(day, {}), "web_ai": {}, "total": {}}

    def fake_aggregate(day, data_root):
        return {"by_ai": dict(minutes_ms.get(day, {})), "sessions": sessions or []}

    monkeypatch.setattr(tool_compare.ai_sessions, "collect", fake_collect)
    monkeypatch.setattr(tool_compare.report, "aggregate", fake_aggregate)


# ---------------------------------------------------------------------------
# 实现阶段补齐（原骨架期占位，现已实现）
# ---------------------------------------------------------------------------
class TestCompareToolsPending:
    def test_two_tools_two_days_aggregation(self, monkeypatch):
        """两个工具跨 2 天：tokens/cost/generated_chars/rounds 求和、sessions=会话条数、minutes=by_ai。"""
        opencode = _stat("opencode", "d1", tokens=12000, cost=0.3, chars=60000, lines=800,
                         rounds=8, n_conv=4, scores=[70, 72, 88, 60])
        chatgpt = _stat("chatgpt", "d1", tokens=6000, cost=0.2, chars=30000, lines=400,
                        rounds=6, n_conv=2, scores=[70, 71])
        opencode2 = _stat("opencode", "d2", tokens=8000, cost=0.2, chars=40000, lines=600,
                          rounds=6, n_conv=3, scores=[90, 80, 70])
        _patch_sources(monkeypatch,
                       {"2026-08-10": {"opencode": opencode, "chatgpt": chatgpt},
                        "2026-08-11": {"opencode": opencode2}},
                       {"2026-08-10": {"opencode": 600000, "chatgpt": 300000},
                        "2026-08-11": {"opencode": 300000}})
        # days 乱序传入 → 内部仍按升序聚合（幂等）
        res = tool_compare.compare_tools(["2026-08-11", "2026-08-10"], "<root>", {})
        assert res["start"] == "2026-08-10" and res["end"] == "2026-08-11"
        assert res["days"] == 2
        by_tool = {t["tool"]: t for t in res["tools"]}
        assert set(by_tool) == {"opencode", "chatgpt"}
        oc = by_tool["opencode"]
        assert oc["sessions"] == 7
        assert oc["minutes"] == 15.0  # (600000 + 300000)ms / 60000
        assert oc["tokens_total"] == 20000
        assert oc["cost_total"] == 0.5
        assert oc["generated_chars"] == 100000
        assert oc["generated_lines"] == 1400
        assert oc["rounds"] == 14
        assert oc["quality_avg"] == 76  # mean(70,72,88,60,90,80,70)=75.71 → round
        assert oc["grade_dist"] == {"优": 3, "良": 4, "中": 0, "待优化": 0}  # >=80: 88/90/80
        assert oc["chars_per_dollar"] == 200000
        assert oc["cost_per_1k_tokens"] == 0.025  # 0.5 / 20000 * 1000
        cg = by_tool["chatgpt"]
        assert cg["sessions"] == 2 and cg["minutes"] == 5.0 and cg["tokens_total"] == 6000
        # 默认排序：chars_per_dollar 降序 → opencode 在前
        assert [t["tool"] for t in res["tools"]] == ["opencode", "chatgpt"]
        # summary 与展示行一致
        assert res["summary"] == {"tools": 2, "total_sessions": 9,
                                  "total_cost": 0.7, "total_minutes": 20.0}

    def test_zero_cost_zero_tokens_no_crash(self, monkeypatch):
        """cost=0/tokens=0/sessions=0 不抛异常，派生指标 None/0/中性值。"""
        zero = _stat("zero", "d1", tokens=0, cost=0.0, chars=0, lines=0, rounds=0, n_conv=0)
        _patch_sources(monkeypatch, {"2026-08-10": {"zero": zero}},
                       {"2026-08-10": {"zero": 0}})
        res = tool_compare.compare_tools(["2026-08-10"], "<root>",
                                         {"tool_compare": {"min_sessions": 0}})
        assert res["tools"]
        z = res["tools"][0]
        assert z["cost_per_1k_tokens"] is None      # tokens==0
        assert z["chars_per_dollar"] is None        # cost_total <= 1e-9
        assert z["chars_per_session"] == 0.0       # sessions==0
        assert z["tokens_per_session"] == 0.0
        assert z["quality_avg"] is None
        assert z["grade_dist"] == {"优": 0, "良": 0, "中": 0, "待优化": 0}
        assert z["share_pct"] == {"cost": 0.0, "sessions": 0.0, "tokens": 0.0}

    def test_share_pct(self, monkeypatch):
        """share_pct 与总额比例正确；总量 0 → 全 0。"""
        a = _stat("a", "d1", tokens=3000, cost=0.03, chars=100, rounds=6, n_conv=3, scores=[70, 70, 70])
        b = _stat("b", "d1", tokens=1000, cost=0.01, chars=100, rounds=2, n_conv=1, scores=[70])
        _patch_sources(monkeypatch, {"2026-08-10": {"a": a, "b": b}},
                       {"2026-08-10": {"a": 30000, "b": 10000}})
        res = tool_compare.compare_tools(["2026-08-10"], "<root>", {})
        by_tool = {t["tool"]: t for t in res["tools"]}
        assert by_tool["a"]["share_pct"] == pytest.approx(
            {"cost": 0.75, "sessions": 0.75, "tokens": 0.75})
        assert by_tool["b"]["share_pct"] == pytest.approx(
            {"cost": 0.25, "sessions": 0.25, "tokens": 0.25})
        # 总量 0（仅有全零工具）→ 全 0
        zero = _stat("zero", "d1", tokens=0, cost=0.0, chars=0, rounds=0, n_conv=0)
        _patch_sources(monkeypatch, {"2026-08-10": {"zero": zero}},
                       {"2026-08-10": {"zero": 0}})
        res2 = tool_compare.compare_tools(["2026-08-10"], "<root>",
                                          {"tool_compare": {"min_sessions": 0}})
        assert res2["tools"][0]["share_pct"] == {"cost": 0.0, "sessions": 0.0, "tokens": 0.0}

    def test_default_sort_by_chars_per_dollar(self, monkeypatch):
        """默认按 chars_per_dollar 降序，None 排最后。"""
        high = _stat("high", "d1", tokens=10000, cost=0.1, chars=100000, rounds=8,
                     n_conv=4, scores=[70, 70, 70, 70])
        low = _stat("low", "d1", tokens=10000, cost=0.2, chars=2000, rounds=8,
                    n_conv=4, scores=[70, 70, 70, 70])
        nocost = _stat("nocost", "d1", tokens=5000, cost=0.0, chars=10000, rounds=8,
                       n_conv=4, scores=[70, 70, 70, 70])
        _patch_sources(monkeypatch,
                       {"2026-08-10": {"high": high, "low": low, "nocost": nocost}},
                       {"2026-08-10": {"high": 1000, "low": 1000, "nocost": 1000}})
        res = tool_compare.compare_tools(["2026-08-10"], "<root>", {})
        assert [t["tool"] for t in res["tools"]] == ["high", "low", "nocost"]
        assert res["tools"][0]["chars_per_dollar"] > res["tools"][1]["chars_per_dollar"]
        assert res["tools"][2]["chars_per_dollar"] is None

    def test_disabled_returns_empty(self, monkeypatch):
        """enabled=false → 契约空态。"""
        _patch_sources(monkeypatch, {"2026-08-10": {"x": _stat("x", "d1")}},
                       {"2026-08-10": {"x": 60000}})
        res = tool_compare.compare_tools(["2026-08-10"], "<root>",
                                         {"tool_compare": {"enabled": False}})
        assert res["tools"] == []
        assert res["summary"] == {"tools": 0, "total_sessions": 0, "total_cost": 0.0,
                                   "total_minutes": 0.0}
        assert "仅参考" in res["notice"]

    def test_project_filter(self, monkeypatch):
        """project 过滤：会话/分钟收窄到目标项目；无项目维度的 generated_* → None 排最后。"""
        oc = _stat("opencode", "d1", tokens=12000, cost=0.3, chars=60000, rounds=8,
                   n_conv=4, project="VibeTrace", scores=[70, 72, 88, 60])
        cg = _stat("chatgpt", "d1", tokens=6000, cost=0.2, chars=30000, rounds=6,
                   n_conv=2, project="SideProj", scores=[80, 66])
        sessions = [
            {"ai_tool": "opencode", "title": "VibeTrace/a.py", "app": "code",
             "exe": "code", "duration_ms": 600000},
            {"ai_tool": "opencode", "title": "SideProj/b.py", "app": "code",
             "exe": "code", "duration_ms": 600000},
        ]
        _patch_sources(monkeypatch, {"2026-08-10": {"opencode": oc, "chatgpt": cg}},
                       {"2026-08-10": {"opencode": 1200000, "chatgpt": 600000}},
                       sessions=sessions)
        # 模糊子串 + 大小写不敏感（"TRACE" 命中 VibeTrace）
        res = tool_compare.compare_tools(["2026-08-10"], "<root>", {}, project="TRACE")
        assert [t["tool"] for t in res["tools"]] == ["opencode"]  # chatgpt 无命中会话 → 剔除
        oc_row = res["tools"][0]
        assert oc_row["sessions"] == 4
        assert oc_row["minutes"] == 10.0  # 仅 title 含 VibeTrace 的 600000ms
        assert oc_row["tokens_total"] == 12000   # by_project 精确维
        assert oc_row["cost_total"] == 0.3
        assert oc_row["generated_chars"] is None  # collect 无项目产出维度 → 不可得
        assert oc_row["chars_per_dollar"] is None  # None 排最后
        assert oc_row["chars_per_session"] is None
        assert oc_row["cost_per_1k_tokens"] == pytest.approx(0.025)
        # 无 project 参数时不收窄
        res2 = tool_compare.compare_tools(["2026-08-10"], "<root>", {})
        assert {t["tool"] for t in res2["tools"]} == {"opencode", "chatgpt"}
        assert res2["tools"][0]["generated_chars"] == 60000

# ---------------------------------------------------------------------------
# 缓存三分项透传（v2.9+）：tokens_input_fresh / tokens_cache_read / tokens_cache_write
# ---------------------------------------------------------------------------
class TestMergeCacheFields:
    """_merge_tool_stats / _merge_dim 对带/不带新字段的 day rows 都能容错求和。"""

    def test_merge_tool_stats_cache_fields_summed(self):
        """新旧两天的行混并：带新字段的求和，缺字段的行贡献 0，不抛 KeyError。"""
        row_new = {  # v2.9+ 新结构：带缓存三分项
            "turns": 3, "tokens_in": 960, "tokens_out": 20, "tokens_total": 980,
            "tokens_input_fresh": 10, "tokens_cache_read": 900, "tokens_cache_write": 50,
            "cost_total": 0.001, "by_model": {}, "by_project": {}, "conversations": [],
        }
        row_old = {  # 旧结构：没有三分项键
            "turns": 2, "tokens_in": 100, "tokens_out": 10, "tokens_total": 110,
            "cost_total": 0.002, "by_model": {}, "by_project": {}, "conversations": [],
        }
        merged = tool_compare._merge_tool_stats([row_new, row_old])
        assert merged["turns"] == 5
        assert merged["tokens_in"] == 1060 and merged["tokens_total"] == 1090
        # 新字段只来自 row_new（row_old 缺键按 0 计，不抛异常）
        assert merged["tokens_input_fresh"] == 10
        assert merged["tokens_cache_read"] == 900
        assert merged["tokens_cache_write"] == 50

    def test_merge_tool_stats_all_old_rows_keep_none(self):
        """全部为旧行（任何一天都没有三分项）：字段保持 None（下游以 or 0 消费）。"""
        row_old = {"turns": 1, "tokens_in": 5, "tokens_total": 5,
                   "by_model": {}, "by_project": {}, "conversations": []}
        merged = tool_compare._merge_tool_stats([row_old, dict(row_old)])
        assert merged["tokens_input_fresh"] is None
        assert merged["tokens_cache_read"] is None
        assert merged["tokens_cache_write"] is None

    def test_merge_dim_cache_fields_tolerance(self):
        """by_model 维度合并：旧条目缺缓存键不炸，新条目正常累加。"""
        target: dict = {}
        # 旧结构条目（无 tokens_input_fresh 等键）
        tool_compare._merge_dim(target, {"m": {"turns": 2, "tokens_in": 5, "tokens_total": 5}})
        # 新结构条目（带三分项）
        tool_compare._merge_dim(target, {"m": {"turns": 3, "tokens_in": 7, "tokens_total": 7,
                                               "tokens_input_fresh": 7,
                                               "tokens_cache_read": 0,
                                               "tokens_cache_write": 0}})
        m = target["m"]
        assert m["turns"] == 5 and m["tokens_in"] == 12
        assert m["tokens_input_fresh"] == 7
        assert m["tokens_cache_read"] == 0 and m["tokens_cache_write"] == 0
