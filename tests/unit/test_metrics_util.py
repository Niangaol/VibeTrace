# -*- coding: utf-8 -*-
"""tests/unit/test_metrics_util.py - 统计辅助单一来源的回归钉扎。

metrics_util 被 ai_sessions / budget / growth / insights / tool_compare 五个
核心模块复用，此前 0 测试覆盖——任何一个函数的边界行为变化都会同时
污染五个下游口径。本文件把每个函数的「文档承诺」逐条钉死，
并显式记录「不四舍五入由调用方决定」这类刻意设计，防止后人误改。
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import metrics_util  # noqa: E402


class TestMergeDim:
    def test_merges_new_key_from_zero(self):
        t = {}
        metrics_util.merge_dim(t, {"gpt": {"turns": 2, "tokens_in": 10}})
        assert t["gpt"]["turns"] == 2
        assert t["gpt"]["tokens_in"] == 10
        assert t["gpt"]["tokens_out"] == 0
        assert t["gpt"]["cost_total"] == 0.0

    def test_accumulates_existing_key(self):
        """同键二次 merge：int/float 逐字段精确相加。"""
        t = {"gpt": {"turns": 1, "tokens_in": 5, "cost_out": 0.5}}
        metrics_util.merge_dim(t, {"gpt": {"turns": 3, "tokens_in": 7, "cost_out": 0.25}})
        assert t["gpt"]["turns"] == 4
        assert t["gpt"]["tokens_in"] == 12
        assert t["gpt"]["cost_out"] == pytest.approx(0.75)

    def test_second_merge_of_same_key_does_not_raise(self):
        """同一键连续 merge 两次不抛异常（既有实现；见 review-metrics-util.md 断言 4）。"""
        t = {}
        metrics_util.merge_dim(t, {"a": {"cost_out": 1.0}})
        metrics_util.merge_dim(t, {"a": {"cost_out": 1.0}})
        assert t["a"]["cost_out"] == pytest.approx(2.0)
        assert t["a"]["turns"] == 0

    def test_all_ten_fields_present_on_new_key(self):
        """新建桶必须含全部 10 个可累加字段（7 int 从 0 起 + 3 float 0.0）。"""
        t = {}
        metrics_util.merge_dim(t, {"m": {"turns": 1}})
        expected = set(metrics_util._MERGE_INT_KEYS) | set(metrics_util._MERGE_FLOAT_KEYS)
        assert expected <= set(t["m"].keys()), expected - set(t["m"].keys())

    def test_missing_fields_treated_as_zero(self):
        t = {}
        metrics_util.merge_dim(t, {"m": {"turns": 1}})
        for k in ("tokens_in", "tokens_out", "tokens_total",
                  "tokens_input_fresh", "tokens_cache_read", "tokens_cache_write"):
            assert t["m"][k] == 0

    def test_none_values_treated_as_zero(self):
        t = {}
        metrics_util.merge_dim(t, {"m": {"turns": None, "tokens_in": None, "cost_total": None}})
        assert t["m"]["turns"] == 0
        assert t["m"]["tokens_in"] == 0
        assert t["m"]["cost_total"] == 0.0

    def test_empty_src_is_noop(self):
        t = {"a": {"turns": 1}}
        metrics_util.merge_dim(t, {})
        assert t == {"a": {"turns": 1}}

    def test_none_src_is_noop(self):
        t = {"a": {"turns": 1}}
        metrics_util.merge_dim(t, None)
        assert t == {"a": {"turns": 1}}

    def test_int_coercion_of_numeric_strings(self):
        t = {}
        metrics_util.merge_dim(t, {"m": {"turns": "3"}})
        assert t["m"]["turns"] == 3

    def test_all_merge_keys_present_after_merge(self):
        t = {}
        metrics_util.merge_dim(t, {"m": {"turns": 1}})
        for k in metrics_util._MERGE_INT_KEYS:
            assert k in t["m"]
        for k in metrics_util._MERGE_FLOAT_KEYS:
            assert k in t["m"]


class TestShannonEntropy:
    def test_empty_list_is_zero(self):
        assert metrics_util.shannon_entropy([]) == 0.0

    def test_zero_total_is_zero(self):
        assert metrics_util.shannon_entropy([0, 0, 0]) == 0.0

    def test_single_category_is_zero(self):
        assert metrics_util.shannon_entropy([10]) == 0.0
        assert metrics_util.shannon_entropy([10, 0, 0]) == 0.0

    def test_two_even_categories_is_one_bit(self):
        assert metrics_util.shannon_entropy([5, 5]) == 1.0

    def test_four_even_categories_is_two_bits(self):
        assert metrics_util.shannon_entropy([1, 1, 1, 1]) == 2.0

    def test_rounded_to_three_decimals(self):
        v = metrics_util.shannon_entropy([1, 2, 3, 4, 5])
        assert v == round(v, 3)

    def test_negative_counts_silently_skipped_but_shrink_total(self):
        """【钉死怪异现状】负数项被 c > 0 跳过，却仍计入 total 分母。

        实测 shannon_entropy([-1, 2]) == -2.0——熵在数学上不可能为负。
        这是既有实现的行为，本测试钉死它以便将来显式修（修时必须同步
        insights / growth / tool_compare 三方的下游口径）。勿删。
        """
        assert metrics_util.shannon_entropy([-1, 2]) == -2.0
        assert metrics_util.shannon_entropy([5, -3]) == -3.305
        assert metrics_util.shannon_entropy([-1, -2]) == 0.0
        assert metrics_util.shannon_entropy([5, 5, -1]) == 0.942

    def test_more_even_is_higher(self):
        assert metrics_util.shannon_entropy([9, 1]) < metrics_util.shannon_entropy([5, 5])


class TestHhi:
    def test_empty_is_zero(self):
        assert metrics_util.hhi([]) == 0

    def test_full_concentration_is_one(self):
        assert metrics_util.hhi([1.0]) == 1.0

    def test_not_rounded_by_design(self):
        """刻意设计：本函数不四舍五入，由调用方决定保留几位。

        若这里加上 round，会同时改变 insights / tool_compare（4 位）
        与 growth（累加后取均值）的口径，属破坏性变更。
        """
        v = metrics_util.hhi([0.33333, 0.33333, 0.33334])
        assert v == pytest.approx(0.33333333340000004, rel=1e-12)
        assert v != round(v, 4)  # 刻意不四舍五入

    def test_two_even_halves(self):
        assert metrics_util.hhi([0.5, 0.5]) == pytest.approx(0.5)

    def test_smaller_shares_lower_hhi(self):
        assert metrics_util.hhi([0.2, 0.2, 0.2, 0.2, 0.2]) < metrics_util.hhi([0.5, 0.5])

    def test_not_normalized_contract(self):
        """契约：入参必须是占比，函数不校验、不归一化。"""
        assert metrics_util.hhi([3, 1]) == 10.0
        assert metrics_util.hhi([0.5, 0.5]) == pytest.approx(0.5)

    def test_negative_share_squares_positive(self):
        """负份额直接平方（不 clamp），属既有实现。"""
        assert metrics_util.hhi([-0.1, 1.1]) == pytest.approx(1.22)


class TestFmtUsd:
    @pytest.mark.parametrize("raw,expected", [
        (0, "$0"),
        (None, "$0"),
        ("", "$0"),
        (0.0, "$0"),
        ("abc", "$0"),
    ])
    def test_zero_and_garbage(self, raw, expected):
        assert metrics_util.fmt_usd(raw) == expected

    def test_sub_cent_uses_four_decimals(self):
        assert metrics_util.fmt_usd(0.004) == "$0.0040"

    def test_sub_dollar_uses_three_decimals(self):
        assert metrics_util.fmt_usd(0.5) == "$0.500"
        # 0.009 < 0.01 走 4 位分支（既有实现）
        assert metrics_util.fmt_usd(0.009) == "$0.0090"
        assert metrics_util.fmt_usd(0.01) == "$0.010"

    def test_above_dollar_uses_two_decimals(self):
        assert metrics_util.fmt_usd(1) == "$1.00"
        assert metrics_util.fmt_usd(12.345) == "$12.35"
        assert metrics_util.fmt_usd(1234.5) == "$1234.50"

    def test_string_number_parsed(self):
        assert metrics_util.fmt_usd("1.5") == "$1.50"
        assert metrics_util.fmt_usd("0.5") == "$0.500"

    def test_negative_falls_into_four_decimal_branch(self):
        """【钉死】负数 v < 0.01 成立，落 4 位分支（既有实现）。"""
        assert metrics_util.fmt_usd(-5) == "$-5.0000"


class TestSwitchSeries:
    def test_empty(self):
        assert metrics_util.tool_switch_series([]) == []
        assert metrics_util.count_switches([]) == 0

    def test_priority_ai_then_term_then_app(self):
        recs = [{"ai_tool": "a"}, {"term_tool": "t"}, {"app": "p"}]
        assert metrics_util.tool_switch_series(recs) == ["a", "t", "p"]

    def test_missing_all_is_unknown(self):
        assert metrics_util.tool_switch_series([{"x": 1}]) == ["未知"]

    def test_sorted_by_start(self):
        recs = [{"start": "2026-09-02", "app": "b"}, {"start": "2026-09-01", "app": "a"}]
        assert metrics_util.tool_switch_series(recs) == ["a", "b"]

    def test_empty_start_sorts_first(self):
        recs = [{"start": "2026-09-02", "app": "b"}, {"app": "a"}]
        assert metrics_util.tool_switch_series(recs) == ["a", "b"]

    def test_count_switches_adjacent_changes(self):
        assert metrics_util.count_switches(["a", "a", "b", "b", "c"]) == 2
        assert metrics_util.count_switches(["a", "a", "a"]) == 0
        assert metrics_util.count_switches(["a"]) == 0
        assert metrics_util.count_switches(["a", "b"]) == 1

    def test_series_and_count_integration(self):
        recs = [
            {"start": "2026-09-01T10:00", "ai_tool": "opencode"},
            {"start": "2026-09-01T11:00", "ai_tool": "opencode"},
            {"start": "2026-09-01T12:00", "ai_tool": "codex"},
        ]
        s = metrics_util.tool_switch_series(recs)
        assert s == ["opencode", "opencode", "codex"]
        assert metrics_util.count_switches(s) == 1


def test_no_business_module_dependency():
    """铁律：metrics_util 不得 import 任何业务模块（避免循环依赖）。"""
    src = open(os.path.join(_PROJECT_ROOT, "metrics_util.py"), encoding="utf-8").read()
    business = ("insights", "growth", "tool_compare", "budget", "ai_sessions",
                "report", "dashboard", "monitor", "classifier", "query", "paths")
    for mod in business:
        assert ("import " + mod) not in src, mod
        assert ("from " + mod + " import") not in src, mod


def test_grade_names_matches_ai_sessions_contract():
    """GRADE_NAMES 与 ai_sessions.quality_grade 的分档一一对应。"""
    import ai_sessions
    for i, name in enumerate(metrics_util.GRADE_NAMES):
        # 同源：ai_sessions._GRADE_NAMES 就是 metrics_util.GRADE_NAMES 本身
        assert ai_sessions._GRADE_NAMES is metrics_util.GRADE_NAMES
        assert ai_sessions._GRADE_BOUNDS == (80, 65, 45)
        # 分档顺序：优/良/中/待优化（索引 0..3）
        assert ai_sessions.quality_grade(90) == metrics_util.GRADE_NAMES[0]
        assert ai_sessions.quality_grade(70) == metrics_util.GRADE_NAMES[1]
        assert ai_sessions.quality_grade(50) == metrics_util.GRADE_NAMES[2]
        assert ai_sessions.quality_grade(10) == metrics_util.GRADE_NAMES[3]
    def test_partial_float_bucket_does_not_raise(self):
        """【回归】调用方自建的桶缺 float 键时不得 KeyError。

        修复前：t[fk] += float(...) 无容错，target={"m": {"cost_in": 1.0}} 时
        第二次 merge 抛 KeyError: "cost_out"——与 docstring「字段缺失按 0 处理」矛盾。
        """
        t = {"m": {"cost_in": 1.0}}  # 缺 cost_out / cost_total
        metrics_util.merge_dim(t, {"m": {"cost_out": 2.0}})
        assert t["m"]["cost_in"] == pytest.approx(1.0)
        assert t["m"]["cost_out"] == pytest.approx(2.0)
        assert t["m"]["cost_total"] == pytest.approx(0.0)

    def test_new_bucket_covers_all_keys(self):
        """桶字段集单一来源：_new_bucket() 与两个 KEYS 元组严格一致。"""
        b = metrics_util._new_bucket()
        assert set(b) == set(metrics_util._MERGE_INT_KEYS) | set(metrics_util._MERGE_FLOAT_KEYS)
        for k in metrics_util._MERGE_INT_KEYS:
            assert b[k] == 0
        for k in metrics_util._MERGE_FLOAT_KEYS:
            assert b[k] == 0.0