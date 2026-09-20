# -*- coding: utf-8 -*-
"""tests/unit/test_derived.py - 派生取数框架（v2.9.8）回归钉扎。

derived 是 growth / tool_compare / query / budget / insights / advice 六模块
共用的取数入口，其「每源每天恰调一次」「失败即该日作废」「未请求的源不调」
三条语义一旦被破坏，六个下游会同时静默出错。本文件逐条钉死。
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import derived  # noqa: E402


class FakeMod:
    """记录调用次数的假业务模块。"""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self.result = result if result is not None else {}
        self.raises = raises

    def aggregate(self, day, data_root):
        self.calls.append(("aggregate", day, data_root))
        if self.raises:
            raise self.raises
        return {"total_active_ms": 100, "session_count": 1}

    def collect(self, day, config=None, web_visits=None):
        self.calls.append(("collect", day, web_visits))
        if self.raises:
            raise self.raises
        return dict(self.result)


@pytest.fixture(autouse=True)
def _clear_cache():
    saved = dict(derived._MODULE_CACHE)
    derived._MODULE_CACHE.clear()
    try:
        yield
    finally:
        derived._MODULE_CACHE.clear()
        derived._MODULE_CACHE.update(saved)


class TestDayBundle:
    def test_default_need_gets_agg_and_ai_only(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        b = derived.day_bundle("2026-09-19", "R")
        assert b is not None
        assert b["date"] == "2026-09-19"
        assert "agg" in b and "ai" in b
        assert "git" not in b and "web" not in b

    def test_each_source_called_exactly_once_per_day(self, monkeypatch):
        """铁律 1：每源每天恰调一次（test_growth 据此精确断言）。"""
        agg, ai = FakeMod(), FakeMod()
        monkeypatch.setitem(derived._MODULE_CACHE, "report", agg)
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", ai)
        derived.day_bundle("2026-09-19", "R")
        assert len(agg.calls) == 1
        assert len(ai.calls) == 1

    def test_unrequested_source_not_imported(self, monkeypatch):
        """未请求的源连 import 都不做（零额外开销）。"""
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        derived.day_bundle("2026-09-19", "R", need=derived.NEED_AGG)
        assert "git_insights" not in derived._MODULE_CACHE
        assert "browser_history" not in derived._MODULE_CACHE

    def test_agg_failure_voids_whole_day(self, monkeypatch):
        """语义 3：AGG 失败即整日作废返回 None。"""
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod(raises=OSError("boom")))
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        assert derived.day_bundle("2026-09-19", "R") is None

    def test_ai_failure_voids_whole_day(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod(raises=ValueError("x")))
        assert derived.day_bundle("2026-09-19", "R") is None

    def test_git_failure_only_nulls_that_source(self, monkeypatch):
        """语义 3：GIT 失败仅记 note 不作废该日。"""
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "git_insights", FakeMod(raises=OSError("no repo")))
        b = derived.day_bundle("2026-09-19", "R", need=derived.NEED_GIT)
        assert b is not None
        assert b["git"] is None

    def test_web_visits_forwarded_to_collect(self, monkeypatch):
        seen = {}

        class Ai(FakeMod):
            def collect(self, day, config, web_visits=None):
                seen["wv"] = web_visits
                return {}

        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", Ai())
        derived.day_bundle("2026-09-19", "R", web_visits=[{"url": "u"}])
        assert seen["wv"] == [{"url": "u"}]

class TestSeries:
    def test_length_and_order(self, monkeypatch):
        agg = FakeMod()
        monkeypatch.setitem(derived._MODULE_CACHE, "report", agg)
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        s = derived.series("2026-09-19", "R", 3)
        assert [x["date"] for x in s] == ["2026-09-19", "2026-09-18", "2026-09-17"]
        assert len(agg.calls) == 3

    def test_include_today_false_drops_first(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        s = derived.series("2026-09-19", "R", 4, include_today=False)
        assert [x["date"] for x in s] == ["2026-09-18", "2026-09-17", "2026-09-16"]

    def test_voided_days_skipped(self, monkeypatch):
        class Flaky(FakeMod):
            def aggregate(self, day, data_root):
                self.calls.append(day)
                if day == "2026-09-18":
                    raise OSError("skip")
                return {}

        monkeypatch.setitem(derived._MODULE_CACHE, "report", Flaky())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        s = derived.series("2026-09-19", "R", 3)
        assert [x["date"] for x in s] == ["2026-09-19", "2026-09-17"]

    def test_bad_date_returns_empty(self):
        assert derived.series("not-a-date", "R", 3) == []

    def test_web_factory_failure_degrades(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "browser_history", FakeMod())

        def bad(day):
            raise OSError("no browser")

        s = derived.series("2026-09-19", "R", 2, need=derived.NEED_WEB,
                           web_visits_factory=bad)
        # 工厂失败只降级 web_visits（喂给 AI 的那份），不影响当日成捆
        assert len(s) == 2
        assert all("web" in x for x in s)


class TestDaysBack:
    def test_inclusive_of_today(self):
        assert derived._days_back("2026-09-19", 1) == ["2026-09-19"]

    def test_crosses_month_boundary(self):
        assert derived._days_back("2026-09-02", 3) == ["2026-09-02", "2026-09-01", "2026-08-31"]

    @pytest.mark.parametrize("bad", [None, "", "abc", "2026-13-99"])
    def test_invalid_returns_empty(self, bad):
        assert derived._days_back(bad, 5) == []


class TestCachedEndpoint:
    def test_clamps_and_passes_bundles(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())

        @derived.cached_endpoint(max_days=5)
        def ep(date_str, data_root, bundles, config=None):
            return {"days": [b["date"] for b in bundles]}

        assert ep("2026-09-19", "R", 2) == {"days": ["2026-09-19", "2026-09-18"]}
        assert ep("2026-09-19", "R", 999) == {"days": ["2026-09-19", "2026-09-18",
                                                     "2026-09-17", "2026-09-16", "2026-09-15"]}

    def test_non_numeric_days_falls_back_to_14(self, monkeypatch):
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        got = {}

        @derived.cached_endpoint()
        def ep(date_str, data_root, bundles, config=None):
            got["n"] = len(bundles)
            return got

        ep("2026-09-19", "R", "abc")
        assert got["n"] == 14

    def test_series_failure_returns_empty_state(self, monkeypatch):
        """降级不得 500：返回调用方给的契约空态。"""
        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod(raises=OSError("db")))
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())

        @derived.cached_endpoint(empty={"days": [], "notice": "degraded"})
        def ep(date_str, data_root, bundles, config=None):
            raise AssertionError("should not be called")

        assert ep("2026-09-19", "R", 7) == {"days": [], "notice": "degraded"}


def test_no_module_level_business_imports():
    """铁律：无模块级业务 import（全部惰性），避免循环依赖。"""
    src = open(os.path.join(_PROJECT_ROOT, "derived.py"), encoding="utf-8").read()
    for mod in ("report", "ai_sessions", "insights", "git_insights", "browser_history"):
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            assert not s.startswith("import " + mod), line
            assert not s.startswith("from " + mod + " import"), line

class TestGitSource:
    """NEED_GIT 路径回归（v2.9.8 曾因调用签名错误导致 Git 数据恒 0）。"""

    def test_git_source_called_with_config_first(self, monkeypatch):
        """铁律：git_insights 真实签名为 (config, day_str)，不是 (day, data_root)。

        历史 bug：derived 曾写成 git_insights(day, data_root)，异常被 day_bundle 
        捕获后 git 置 None，q4/q6 的 Git 行数会静默变 0。
        """
        seen = {}

        class Git(FakeMod):
            def git_insights(self, config, day_str, ai_project_files=None):
                seen["config"] = config
                seen["day"] = day_str
                return {"commits": 3}

        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "git_insights", Git())
        b = derived.day_bundle("2026-09-19", "R", {"insights": {}}, need=derived.NEED_GIT)
        assert b is not None
        assert b["git"] == {"commits": 3}
        assert seen["day"] == "2026-09-19"
        assert seen["config"] == {"insights": {}}

    def test_git_signature_matches_real_module(self):
        """与真实 git_insights 的参数名对齐（防将来签名再变）。"""
        import inspect

        import git_insights
        params = list(inspect.signature(git_insights.git_insights).parameters)
        assert params[0] == "config", params
        assert params[1] == "day_str", params

    def test_git_none_config_does_not_raise(self, monkeypatch):
        """config 为 None 时传 {}，不让 git_config 里 .get 炸掉。"""

        class Git(FakeMod):
            def git_insights(self, config, day_str, ai_project_files=None):
                assert isinstance(config, dict)
                return {"commits": 0}

        monkeypatch.setitem(derived._MODULE_CACHE, "report", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "ai_sessions", FakeMod())
        monkeypatch.setitem(derived._MODULE_CACHE, "git_insights", Git())
        b = derived.day_bundle("2026-09-19", "R", None, need=derived.NEED_GIT)
        assert b is not None
        assert b["git"] == {"commits": 0}
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))