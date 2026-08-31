# -*- coding: utf-8 -*-
"""tests/unit/test_growth.py — 能力基线 / 成长曲线（v2.6 · P7）单元测试。

覆盖：
  - 配置兜底、ISO 周 key、周日期过滤、slope/dir 判定（up/flat/down/None/prev=0）；
  - 快照读自愈（缺文件/坏 JSON/错 schema）、原子写回读（tmp 清理）；
  - 周聚合（_aggregate_week 三源 monkeypatch）：均值/总和/scored_days 过滤/modify_ratio 过滤；
  - 主入口 growth_snapshot：幂等（重跑不重复不重写）、坏档自愈、增量跳过重算、
    force 强制重算、modify_ratio 反向 good_dir、空数据/关闭空态、_days 指纹不外泄。

零依赖、确定性；周聚合与主入口用例一律 monkeypatch 三源
（report/ai_sessions/insights/git_insights），绝不扫描真实用户目录。
"""

from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import growth  # noqa: E402


def _mk_dirs(root: str, days: list[str]) -> None:
    """在数据根下创建若干 YYYY-MM-DD 空目录（真实目录，供 _list_days 扫描）。"""
    for d in days:
        os.makedirs(os.path.join(root, d), exist_ok=True)


def _patch_sources(monkeypatch, env: dict):
    """monkeypatch growth 的四个数据源，行为由 env 字典驱动（env 可事后修改）。"""
    def fake_aggregate(day, data_root):
        env["counts"]["agg"] += 1
        agg = {"by_category": dict(env["by_category"].get(day, {})),
               "total_active_ms": int(env["ms"].get(day, 0)),
               "sessions": [{"app": "x", "duration_ms": 1000, "project": "projA"}
                            ] if env["ms"].get(day) else []}
        agg["_day"] = day
        return agg

    def fake_behavior(agg, config):
        return {"focus_score": int(env["focus"].get(agg["_day"], 0))}

    def fake_time_saved(agg, config):
        return {"saved_ms": int(env["saved"].get(agg["_day"], 0))}

    def fake_collect(day, config, web_visits=None):
        env["counts"]["collect"] += 1
        q = env["quality"].get(day, {})
        return {"date": day, "enabled": True, "found": True, "tools": {}, "web_ai": {},
                "total": {"generated_lines": int(env["generated"].get(day, 0)),
                          "by_model": dict(env["by_model"].get(day, {})),
                          "conversations": [{"turns": 1}] * int(env["convs"].get(day, 0)),
                          "quality_summary": {"sessions_scored": int(q.get("n", 0)),
                                              "avg": int(q.get("avg", 0))}}}

    def fake_git(config, day):
        env["counts"]["git"] += 1
        g = env["git"].get(day)
        if g is None:
            return {"enabled": True, "found": False, "repos": [],
                    "total": {"lines_added": 0, "churn": 0, "modify_ratio": 0.0}}
        return {"enabled": True, "found": True, "repos": [],
                "total": {"lines_added": int(g.get("lines_added", 0)),
                          "churn": int(g.get("churn", 0)),
                          "modify_ratio": float(g.get("modify_ratio", 0.0))}}

    monkeypatch.setattr(growth.report, "aggregate", fake_aggregate)
    monkeypatch.setattr(growth.insights, "behavior_insights", fake_behavior)
    monkeypatch.setattr(growth.insights, "time_saved_insights", fake_time_saved)
    monkeypatch.setattr(growth.ai_sessions, "collect", fake_collect)
    monkeypatch.setattr(growth.git_insights, "git_insights", fake_git)


def _env():
    return {
        "counts": {"agg": 0, "collect": 0, "git": 0},
        "by_category": {},   # day -> {"AI编程": ms}
        "ms": {},            # day -> total_active_ms
        "focus": {},         # day -> int
        "saved": {},         # day -> saved_ms
        "quality": {},       # day -> {"n": scored, "avg": int}
        "generated": {},     # day -> generated_lines
        "by_model": {},      # day -> {model: {"turns": n}}（collect 侧，喂模型多样熵）
        "convs": {},         # day -> 会话数（conversations 条数，喂 prompt_efficiency 分母）
        "git": {},           # day -> {"lines_added", "churn", "modify_ratio"} | None
    }


def _cfg(growth_cfg: dict | None = None) -> dict:
    return {"growth": growth_cfg or {}}


def _week_days(year: int, week: int, count: int = 4) -> list[str]:
    """取某 ISO 周开头的 count 个工作日 YYYY-MM-DD 文本。"""
    d = datetime.date(year, 1, 1)
    while d.isocalendar()[:2] != (year, week):
        d += datetime.timedelta(days=1)
    return [(d + datetime.timedelta(days=i)).isoformat() for i in range(count)]


# ---------------------------------------------------------------------------
# growth_config —— 默认兜底 / 覆盖 / 越界钳制
# ---------------------------------------------------------------------------
class TestGrowthConfig:
    def test_defaults_when_missing(self):
        cfg = growth.growth_config({})
        assert cfg["enabled"] is True
        assert cfg["weeks"] == 8
        assert cfg["min_days_per_week"] == 3
        assert cfg["flat_threshold"] == 0.03

    def test_override(self):
        cfg = growth.growth_config(
            {"growth": {"enabled": False, "weeks": 4, "min_days_per_week": 5, "flat_threshold": 0.05}})
        assert cfg["enabled"] is False
        assert cfg["weeks"] == 4
        assert cfg["min_days_per_week"] == 5
        assert cfg["flat_threshold"] == pytest.approx(0.05)

    def test_weeks_clamped_1_52(self):
        assert growth.growth_config({"growth": {"weeks": 0}})["weeks"] == 1
        assert growth.growth_config({"growth": {"weeks": 99}})["weeks"] == 52

    def test_bad_types_fall_back(self):
        cfg = growth.growth_config({"growth": {"weeks": "x", "flat_threshold": None}})
        assert cfg["weeks"] == 8
        assert cfg["flat_threshold"] == 0.03


# ---------------------------------------------------------------------------
# ISO 周 key 与周日期过滤
# ---------------------------------------------------------------------------
class TestWeekKey:
    def test_mid_week(self):
        assert growth._week_key(datetime.date.fromisoformat("2026-08-20")) == "2026-W34"

    def test_cross_year(self):
        # 2025-12-29 属于 2026-W01（ISO 归属年份）
        assert growth._week_key(datetime.date.fromisoformat("2025-12-29")) == "2026-W01"
        # 2027-01-01 仍属于 2026-W53
        assert growth._week_key(datetime.date.fromisoformat("2027-01-01")) == "2026-W53"

    def test_week_days_filter(self):
        day_list = ["2026-08-16", "2026-08-17", "2026-08-20", "2026-08-21"]  # W33, W34, W34, W34
        assert growth._week_days((2026, 34), day_list) == ["2026-08-17", "2026-08-20", "2026-08-21"]
        assert growth._week_days((2026, 33), day_list) == ["2026-08-16"]
        assert growth._week_days((2026, 35), day_list) == []

    def test_list_days_filters_and_sorts(self, tmp_path):
        root = str(tmp_path / "days")
        os.makedirs(root, exist_ok=True)
        for bad in ("2026-13-99", "abc", "20260816", "2026-08-1", "notes.txt"):
            os.makedirs(os.path.join(root, bad), exist_ok=True)
        good = ["2026-08-16", "2026-08-17"]
        _mk_dirs(root, good)
        assert growth._list_days(root) == sorted(good)


# ---------------------------------------------------------------------------
# slope/dir 判定
# ---------------------------------------------------------------------------
class TestSlope:
    def test_up(self):
        s = growth._slope(74, 68, 0.03)
        assert s["dir"] == "up"
        assert s["slope"].endswith("%")

    def test_flat_below_threshold(self):
        s = growth._slope(68.5, 68, 0.03)
        assert s["dir"] == "flat"

    def test_down(self):
        s = growth._slope(0.15, 0.20, 0.03)
        assert s["dir"] == "down"
        assert s["slope"] == "-25.0%"

    def test_none_flat(self):
        assert growth._slope(None, 68, 0.03)["dir"] == "flat"
        assert growth._slope(74, None, 0.03)["dir"] == "flat"
        assert growth._slope(None, None, 0.03)["dir"] == "flat"

    def test_prev_zero(self):
        assert growth._slope(10.0, 0.0, 0.03)["dir"] == "up"
        assert growth._slope(0.0, 0.0, 0.03)["dir"] == "flat"


# ---------------------------------------------------------------------------
# 快照读自愈 / 原子写回读
# ---------------------------------------------------------------------------
class TestSnapshotIo:
    def test_read_missing_returns_none(self, tmp_path):
        assert growth._read_snapshot(str(tmp_path)) is None

    def test_read_bad_json_returns_none(self, tmp_path):
        path = os.path.join(str(tmp_path), growth._SNAPSHOT_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json!!")
        assert growth._read_snapshot(str(tmp_path)) is None

    def test_read_wrong_schema_returns_none(self, tmp_path):
        path = os.path.join(str(tmp_path), growth._SNAPSHOT_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": 999}, fh)
        assert growth._read_snapshot(str(tmp_path)) is None

    def test_write_then_read_roundtrip(self, tmp_path):
        payload = {"schema": growth._SCHEMA, "updated_at": "2026-08-25T00:05:00",
                   "weeks": [{"week": "2026-W33", "focus_score": 72}], "trend": []}
        growth._write_snapshot(str(tmp_path), payload)
        back = growth._read_snapshot(str(tmp_path))
        assert back == payload
        # tmp 文件已清理（原子替换）
        leftovers = [f for f in os.listdir(str(tmp_path)) if ".tmp." in f]
        assert leftovers == []


# ---------------------------------------------------------------------------
# 周聚合（_aggregate_week 三源 monkeypatch）
# ---------------------------------------------------------------------------
class TestAggregateWeek:
    def test_week_mean_and_totals(self, tmp_path, monkeypatch):
        days = _week_days(2026, 33, 4)
        env = _env()
        for i, d in enumerate(days):
            env["ms"][d] = 60 * 60 * 1000  # 1h 活动
            env["by_category"][d] = {"AI编程": 30 * 60 * 1000}  # 30 分钟
            env["focus"][d] = 60 + i * 10  # 60/70/80/90
            env["saved"][d] = 15 * 60 * 1000  # 15 分钟
            env["quality"][d] = {"n": 2 + i, "avg": 65 + i}  # 65/66/67/68
            env["generated"][d] = 100 + i * 100  # 100/200/300/400
            env["by_model"][d] = {"claude-x": {"turns": 3}, "glm-y": {"turns": 1}}
            env["convs"][d] = 2 + i  # 2/3/4/5
            env["git"][d] = {"lines_added": 10 + i, "churn": 20, "modify_ratio": 0.2}
        _patch_sources(monkeypatch, env)
        root = str(tmp_path / "w1")
        wk = growth._aggregate_week(days, root, _cfg())
        assert wk is not None
        assert wk["week"] == "2026-W33"
        assert wk["days"] == 4
        assert wk["focus_score"] == 75                      # (60+70+80+90)/4
        assert wk["quality_avg"] == 66                      # (65+66+67+68)/4 = 66.5 → round 66
        assert wk["scored_days"] == 4                       # 每天都有已评分会话
        assert wk["generated_lines"] == 1000                # 100+200+300+400
        assert wk["lines_added"] == 10 + 11 + 12 + 13       # 46
        assert wk["modify_ratio"] == pytest.approx(0.2)     # 每天都 git found
        assert wk["ai_minutes"] == pytest.approx(120.0)     # 30*4
        assert wk["saved_minutes"] == 60                    # 15*4
        # v2.9.3 钉扎：v2.9.1 三指标的数据源/分母/键名（修复前恒 None/0/幽灵键）
        assert wk["ai_sessions"] == 14                      # 2+3+4+5（conversations 条数）
        assert wk["prompt_efficiency"] == pytest.approx(71.4)  # 1000/14
        assert wk["model_diversity_entropy"] == pytest.approx(0.811, abs=1e-3)  # shannon([3,1]) 日均
        assert wk["focus_hhi"] == pytest.approx(1.0)        # 单项目 HHI=1（键名即 focus_hhi）

    def test_scored_days_filter_zeros(self, tmp_path, monkeypatch):
        """无会话天（sessions_scored=0、avg=0）不得混入 quality_avg 分母。"""
        days = _week_days(2026, 34, 4)
        env = _env()
        for d in days:
            env["focus"][d] = 50
        # 仅第 1 天有已评分会话
        env["quality"][days[0]] = {"n": 3, "avg": 90}
        _patch_sources(monkeypatch, env)
        wk = growth._aggregate_week(days, str(tmp_path / "w2"), _cfg())
        assert wk["scored_days"] == 1
        assert wk["quality_avg"] == 90  # 只有 90，未被 0 拉低

    def test_modify_ratio_only_found_days(self, tmp_path, monkeypatch):
        """modify_ratio 仅统计 git found 且有产出（churn>0）的天；全无 → None。"""
        days = _week_days(2026, 35, 3)
        env = _env()
        env["git"][days[0]] = {"lines_added": 5, "churn": 10, "modify_ratio": 0.3}
        # 其余天 None（无 git 数据）
        _patch_sources(monkeypatch, env)
        wk = growth._aggregate_week(days, str(tmp_path / "w3"), _cfg())
        assert wk["modify_ratio"] == pytest.approx(0.3)  # 均值=0.3（只有一天有数据）
        # 全无 git → None
        env2 = _env()
        _patch_sources(monkeypatch, env2)
        wk2 = growth._aggregate_week(days, str(tmp_path / "w3b"), _cfg())
        assert wk2["modify_ratio"] is None

    def test_week_below_min_days_dropped(self, tmp_path, monkeypatch):
        """缺周（< min_days_per_week=3）→ None，不进 weeks。"""
        days = _week_days(2026, 36, 2)  # 只有 2 天
        _patch_sources(monkeypatch, _env())
        assert growth._aggregate_week(days, str(tmp_path / "w4"), _cfg()) is None
        # 但 min_days_per_week=2 时可以通过
        cfg2 = _cfg({"min_days_per_week": 2})
        assert growth._aggregate_week(days, str(tmp_path / "w4"), cfg2) is not None

    def test_modify_ratio_reverse_good_dir(self, tmp_path, monkeypatch):
        """modify_ratio 降幅 → trend 条目 dir=down 且 good_dir=true（反向指标）。"""
        w1, w2 = _week_days(2026, 37, 4), _week_days(2026, 38, 4)
        env = _env()
        for d in w1 + w2:
            env["focus"][d] = 50
            env["quality"][d] = {"n": 1, "avg": 60}
        for d in w1:
            env["git"][d] = {"lines_added": 1, "churn": 10, "modify_ratio": 0.20}
        for d in w2:
            env["git"][d] = {"lines_added": 1, "churn": 10, "modify_ratio": 0.15}
        _patch_sources(monkeypatch, env)
        root = str(tmp_path / "w5")
        _mk_dirs(root, w1 + w2)
        result = growth.growth_snapshot(root, _cfg())
        assert result["source"] == "fresh"
        assert [w["week"] for w in result["weeks"]] == ["2026-W37", "2026-W38"]
        mr = [t for t in result["trend"] if t["metric"] == "modify_ratio"][0]
        assert mr["dir"] == "down"
        assert mr["good_dir"] is True
        assert mr["slope"] == "-25.0%"
        # 正向指标（quality_avg 上升）不含 good_dir
        q = [t for t in result["trend"] if t["metric"] == "quality_avg"][0]
        assert q["dir"] == "flat"  # 60 → 60 持平
        assert "good_dir" not in q
        # 趋势条目覆盖全部 7 个指标
        assert {t["metric"] for t in result["trend"]} == set(growth._METRICS)


# ---------------------------------------------------------------------------
# 主入口 growth_snapshot（幂等 / 自愈 / 增量 / force）
# ---------------------------------------------------------------------------
class TestGrowthSnapshot:
    def test_idempotent_no_rewrite(self, tmp_path, monkeypatch):
        """重跑两次：weeks 不重复、文件字节等价（幂等）、第二跑不重算三源。"""
        w1, w2 = _week_days(2099, 10, 4), _week_days(2099, 11, 4)
        root = str(tmp_path / "s1")
        _mk_dirs(root, w1 + w2)
        env = _env()
        for d in w1 + w2:
            env["ms"][d] = 60000
            env["by_category"][d] = {"AI编程": 60000}
            env["focus"][d] = 70
            env["saved"][d] = 60000
            env["quality"][d] = {"n": 2, "avg": 70}
            env["generated"][d] = 50
            env["git"][d] = {"lines_added": 4, "churn": 8, "modify_ratio": 0.25}
        _patch_sources(monkeypatch, env)

        first = growth.growth_snapshot(root, _cfg())
        assert first["source"] == "fresh"
        assert [w["week"] for w in first["weeks"]] == ["2099-W10", "2099-W11"]
        assert env["counts"]["agg"] == 8 and env["counts"]["collect"] == 8  # 全量现算

        path = os.path.join(root, growth._SNAPSHOT_NAME)
        with open(path, "rb") as fh:
            bytes1 = fh.read()

        # 第二跑：命中快照 → 增量跳过重算、文件字节不变
        env["counts"] = {"agg": 0, "collect": 0, "git": 0}
        second = growth.growth_snapshot(root, _cfg())
        assert second["source"] == "snapshot"
        assert [w["week"] for w in second["weeks"]] == ["2099-W10", "2099-W11"]  # 不重复
        assert env["counts"]["agg"] == 0 and env["counts"]["collect"] == 0  # 跳过重算
        with open(path, "rb") as fh:
            assert fh.read() == bytes1  # 字节等价

    def test_corrupt_snapshot_self_heals(self, tmp_path, monkeypatch):
        """坏快照 → 全量重算成功且 source=fresh。"""
        w1, w2 = _week_days(2099, 12, 4), _week_days(2099, 13, 4)
        root = str(tmp_path / "s2")
        _mk_dirs(root, w1 + w2)
        # 写坏档
        bad = os.path.join(root, growth._SNAPSHOT_NAME)
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{oops")
        env = _env()
        for d in w1 + w2:
            env["quality"][d] = {"n": 1, "avg": 80}
        _patch_sources(monkeypatch, env)
        result = growth.growth_snapshot(root, _cfg())
        assert result["source"] == "fresh"
        assert len(result["weeks"]) == 2
        # 文件已重建且可正常读取
        back = growth._read_snapshot(root)
        assert back is not None and back["schema"] == growth._SCHEMA
        assert back["weeks"] != []  # 不再全是空

    def test_force_recompute(self, tmp_path, monkeypatch):
        """force=True 强制全量重算并重写。"""
        w1 = _week_days(2099, 20, 4)
        root = str(tmp_path / "s3")
        _mk_dirs(root, w1)
        env = _env()
        for d in w1:
            env["focus"][d] = 60
        _patch_sources(monkeypatch, env)
        growth.growth_snapshot(root, _cfg())
        env["counts"] = {"agg": 0, "collect": 0, "git": 0}
        second = growth.growth_snapshot(root, _cfg())
        assert second["source"] == "snapshot" and env["counts"]["agg"] == 0
        env["counts"] = {"agg": 0, "collect": 0, "git": 0}
        third = growth.growth_snapshot(root, _cfg(), force=True)
        assert third["source"] == "fresh"
        assert env["counts"]["agg"] == 4  # 强制重算
        assert len(third["weeks"]) == 1

    def test_new_day_triggers_incremental_update(self, tmp_path, monkeypatch):
        """当前周新增一天 → 该周增量重算，已完成周仍复用。"""
        w1, w2 = _week_days(2099, 30, 4), _week_days(2099, 31, 3)
        root = str(tmp_path / "s4")
        _mk_dirs(root, w1 + w2)
        env = _env()
        for d in w1 + w2:
            env["focus"][d] = 50
            env["quality"][d] = {"n": 1, "avg": 50}
            env["ms"][d] = 60 * 60 * 1000  # 有会话 → project HHI 可算
            env["generated"][d] = 100
            env["convs"][d] = 2
            env["by_model"][d] = {"m1": {"turns": 2}}
        _patch_sources(monkeypatch, env)
        growth.growth_snapshot(root, _cfg())
        # 第二周新增一天
        extra = _week_days(2099, 31, 4)[3]
        _mk_dirs(root, [extra])
        env["focus"][extra] = 80
        env["quality"][extra] = {"n": 1, "avg": 60}
        env["ms"][extra] = 60 * 60 * 1000
        env["generated"][extra] = 300
        env["convs"][extra] = 1
        env["counts"] = {"agg": 0, "collect": 0, "git": 0}
        result = growth.growth_snapshot(root, _cfg())
        assert result["source"] == "fresh"  # 有变化 → 重写
        w2_entry = [w for w in result["weeks"] if w["week"] == "2099-W31"][0]
        assert w2_entry["days"] == 4
        assert env["counts"]["agg"] == 1  # 只重算变化的那一天所在周（完成周复用）
        # v2.9.3 钉扎：增量合并键名/分母（曾写 project_focus_hhi 幽灵键 + ai_sessions 死键）
        assert "project_focus_hhi" not in w2_entry
        assert w2_entry["focus_hhi"] == pytest.approx(1.0)  # 单项目 HHI=1，键名修正后随增量更新
        assert w2_entry["ai_sessions"] == 7                 # 旧周 3 天×2 + 新增 1
        assert w2_entry["prompt_efficiency"] == pytest.approx(85.7)  # (300+300)/7

    def test_empty_data_empty_state(self, tmp_path, monkeypatch):
        """无任何日期目录 → 200 空态 weeks=[] trend=[]；快照写入空档，再次调用不再重算。"""
        root = str(tmp_path / "s5")
        _mk_dirs(root, [])  # 空数据根
        _patch_sources(monkeypatch, _env())
        first = growth.growth_snapshot(root, _cfg())
        assert first["weeks"] == [] and first["trend"] == []
        assert first["source"] == "fresh"
        second = growth.growth_snapshot(root, _cfg())
        assert second["source"] == "snapshot"
        assert second["weeks"] == [] and second["trend"] == []

    def test_disabled_empty_state(self, tmp_path, monkeypatch):
        """growth.enabled=false → 空态且不写快照文件。"""
        root = str(tmp_path / "s6")
        _mk_dirs(root, _week_days(2099, 40, 4))
        _patch_sources(monkeypatch, _env())
        result = growth.growth_snapshot(root, _cfg({"enabled": False}))
        assert result["weeks"] == [] and result["trend"] == []
        assert result["source"] == "fresh"
        assert not os.path.exists(os.path.join(root, growth._SNAPSHOT_NAME))

    def test_private_days_not_leaked(self, tmp_path, monkeypatch):
        """对外契约不含快照内部 _days 指纹字段（隐私：只存周均值）。"""
        w1, w2 = _week_days(2099, 50, 4), _week_days(2099, 51, 4)
        root = str(tmp_path / "s7")
        _mk_dirs(root, w1 + w2)
        env = _env()
        for d in w1 + w2:
            env["quality"][d] = {"n": 1, "avg": 66}
        _patch_sources(monkeypatch, env)
        result = growth.growth_snapshot(root, _cfg())
        for w in result["weeks"]:
            assert "_days" not in w  # 内部指纹不对外暴露
        # 快照文件本身保存指纹（供增量比对）但无任何会话标题/路径明细
        raw = growth._read_snapshot(root)
        assert raw is not None
        for w in raw["weeks"]:
            assert isinstance(w.get("_days"), list)  # 有指纹
            assert all(len(x) == 10 for x in w["_days"])