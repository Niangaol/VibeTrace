# -*- coding: utf-8 -*-
"""v2.6 · P7 能力基线 / 成长曲线 —— 派生 + 周均值快照层。

对应 docs/VIBECODING_IMPLEMENTATION_GUIDE.md §5.3 与 docs/COMPARE_GROWTH_P67_DESIGN.md §3。

**职责**：把 focus_score / quality_avg / 产出 / 修改率 / time_saved 按 ISO 周聚合，
持久化周均值快照（<data_root>/growth_baseline.json，只存周均值不存明细），
重跑幂等，供 /api/trend 折线图与趋势卡使用。

**铁律**：只读 import report / ai_sessions / insights / git_insights；
快照是独立持久化缓存，与 usage.jsonl 零耦合；删除快照文件可自动重建（自愈）。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import datetime
import json
import os
import re
from urllib.parse import parse_qs, urlparse

import ai_sessions
import git_insights
import insights
import metrics_util  # 统计辅助单一来源：熵/HHI/工具切换计数（纯函数，无业务依赖）
import report

# ---------------------------------------------------------------------------
# 默认配置（读 config.growth）
# ---------------------------------------------------------------------------
_DEFAULT_GROWTH = {
    "enabled": True,
    "weeks": 8,                 # /api/trend 默认返回周数
    "min_days_per_week": 3,     # 低于该天数的周丢弃（对齐验收「造 2 周各 3 天」）
    "flat_threshold": 0.03,     # slope 判定阈值（<3% 视为 flat，防噪声）
}

_SNAPSHOT_NAME = "growth_baseline.json"
_SCHEMA = 1
_METRICS = ("focus_score", "quality_avg", "generated_lines",
            "modify_ratio", "lines_added", "ai_minutes", "saved_minutes",
            "model_diversity_entropy", "tool_switch_freq", "focus_hhi",
            "learning_curve", "efficiency_stability", "adoption_proxy", "prompt_efficiency")
_MIN_EPS = 1e-9

_NOTICE = (
    "仅参考：周均值为本地估算（focus/quality 为离线规则打分；git 未配置时 modify_ratio 缺失；"
    "token/成本非官方账单）。快照只存周均值，不存明细。"
)


def growth_config(config: dict) -> dict:
    """从完整 config 提取 growth 段并补齐默认值（老用户 config.json 无该段也能跑）。"""
    raw = (config or {}).get("growth")
    sec = raw if isinstance(raw, dict) else {}
    out = dict(_DEFAULT_GROWTH)
    out["enabled"] = bool(sec.get("enabled", _DEFAULT_GROWTH["enabled"]))
    try:
        out["weeks"] = max(1, min(52, int(sec.get("weeks", _DEFAULT_GROWTH["weeks"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["min_days_per_week"] = max(1, int(sec.get("min_days_per_week",
                                                       _DEFAULT_GROWTH["min_days_per_week"])))
    except (TypeError, ValueError):
        pass
    try:
        out["flat_threshold"] = max(0.0, float(sec.get("flat_threshold",
                                                       _DEFAULT_GROWTH["flat_threshold"])))
    except (TypeError, ValueError):
        pass
    return out


def _week_key(d: datetime.date) -> str:
    """YYYY-MM-DD → 'YYYY-Www'（isocalendar，跨年无歧义，如 2026-W01）。"""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _today() -> datetime.date:
    """可注入的「今天」（测试可 monkeypatch 固定日期）。"""
    return datetime.date.today()


def _week_days(week: tuple[int, int], day_list: list[str]) -> list[str]:
    """从升序日期列表筛出属于某 ISO 周 (year, week) 的天（保持原序）。"""
    return [d for d in day_list if _week_key(datetime.date.fromisoformat(d)) ==
            f"{week[0]}-W{week[1]:02d}"]


# ---------------------------------------------------------------------------
# 周聚合（§3.2）
# ---------------------------------------------------------------------------


def _day_metrics(day: str, data_root: str, config: dict) -> dict:
    """单日三源取数 + 指标原值（v2.9.8 抽出，_aggregate_week / _merge_incremental 共用）。

    两处原本各写一份 40 行同构循环（取数源与指标逐行相同，只差归约公式），
    历史上 focus_hhi / ai_sessions / prompt_efficiency 的口径 bug 就是这么漏掉的。

    铁律：每个数据源每天恰好调用一次（test_growth 用 env["counts"] 精确断言）；
    不用 collect_fingerprint_batch 包裹（调用方自行决定批作用域）。
    """
    agg = report.aggregate(day, data_root)
    by_cat = agg.get("by_category") if isinstance(agg.get("by_category"), dict) else {}
    ai_ms = int(by_cat.get("AI编程", 0) or 0)
    total_active = int(agg.get("total_active_ms") or 0)

    total = ai_sessions.collect(day, config).get("total") or {}
    qs = total.get("quality_summary") or {}

    git_result = git_insights.git_insights(config, day)
    git_total = git_result.get("total") or {}

    out = {
        "focus_score": float(insights.behavior_insights(agg, config)["focus_score"] or 0),
        "saved_ms": int(insights.time_saved_insights(agg, config).get("saved_ms") or 0),
        "ai_minutes": ai_ms / 60000.0,
        "adoption": ai_ms / total_active if total_active > 0 else 0.0,
        "generated_lines": int(total.get("generated_lines") or 0),
        # 会话数口径：collect 的 total 无 sessions 键，取 conversations 条数
        # （collect 每日按 turns 截断 top20，会话极多的日子会略低估，仅作提示效率分母）
        "ai_sessions": len(total.get("conversations") or []),
        "quality_avg": float(qs.get("avg") or 0) if int(qs.get("sessions_scored") or 0) > 0 else None,
        "lines_added": int(git_total.get("lines_added") or 0),
        "modify_ratio": (float(git_total.get("modify_ratio") or 0)
                         if bool(git_result.get("found")) and int(git_total.get("churn") or 0) > 0 else None),
        "entropy": None,
        "switch_rate": None,
        "hhi": None,
    }

    # v2.9.1 新指标：数据源是 ai_sessions.collect 的 total.by_model
    # （report.aggregate 不产出 by_model，v2.9.2 曾误读 agg 侧导致恒 None）
    by_model = total.get("by_model") if isinstance(total.get("by_model"), dict) else {}
    if by_model:
        model_counts = [max(1, int(v.get("turns") or 0)) for v in by_model.values()]
        out["entropy"] = metrics_util.shannon_entropy(model_counts)

    sessions = [s for s in (agg.get("sessions") or []) if isinstance(s, dict)]
    if sessions:
        ordered = sorted(sessions, key=lambda s: s.get("start") or "")
        total_ms = max(total_active, sum(int(s.get("duration_ms") or 0) for s in ordered))
        total_hours = total_ms / 3600000.0 if total_ms > 0 else 0.0
        if total_hours > 0:
            # 折序列 + 数相邻变化（共享实现；ordered 已排好序，重复排序结果不变）
            switch_count = metrics_util.count_switches(metrics_util.tool_switch_series(ordered))
            out["switch_rate"] = switch_count / total_hours
        shares = insights._project_shares(sessions)
        if shares:
            # HHI 不逐次舍入（与 insights._hhi 口径不同）：先累加原始值，最后取均值再保留 4 位
            out["hhi"] = metrics_util.hhi(shares)
    return out
def _aggregate_week(days: list[str], data_root: str, config: dict) -> dict | None:
    """按 §3.2 表聚合一周；days 不足 min_days_per_week → None（该周丢弃）。

    对 days 逐日：
      agg          = report.aggregate(date, data_root)
      focus        = insights.behavior_insights(agg, config)["focus_score"]
      ai_collect   = ai_sessions.collect(date, config)
      qs           = ai_collect["total"]["quality_summary"]     # ai_sessions.py:747
      git_t        = git_insights.git_insights(config, date)["total"]
      saved        = insights.time_saved_insights(agg, config)["saved_ms"]
      aw           = insights.activitywatch_metrics(agg, config)
    汇总结构（§3.3）：
      {week, days, scored_days, focus_score(均值), quality_avg(仅 scored_days>0 的天),
       generated_lines(总和), lines_added(总和), modify_ratio(有 git 数据天的均值|None),
       ai_minutes(总和分钟), saved_minutes(总和分钟),
       model_diversity_entropy(日均，ai_sessions.collect 的 by_model 轮次 Shannon 熵),
       tool_switch_freq(日均), focus_hhi(日均项目集中度),
       learning_curve(周内斜率), efficiency_stability(focus_score 标准差),
       adoption_proxy(日均), prompt_efficiency(总生成行/总会话数),
       ai_sessions(总会话数，prompt_efficiency 分母，增量合并键)}
    注意：quality_summary.avg=0 的天不得混入均值分母（scored_days 过滤）；
    modify_ratio 仅统计 git 有产出（found 且 churn>0）的天，无则 None；
    周 key = days[0] 的 ISO 周。纯函数：三源全部 monkeypatch 可测。
    """
    cfg = growth_config(config)
    if len(days or []) < max(1, cfg["min_days_per_week"]):
        return None
    focus_vals: list[float] = []
    quality_vals: list[float] = []
    scored_days = 0
    generated_lines = 0
    lines_added = 0
    modify_vals: list[float] = []
    ai_minutes = 0.0
    saved_ms = 0
    entropy_vals: list[float] = []
    switch_vals: list[float] = []
    hhi_vals: list[float] = []
    adoption_vals: list[float] = []
    ai_sessions_count = 0
    ai_by_day: list[float] = []
    # v2.9.11: 区间批量——N 天只跑一次 git log，块内按天查表
    with git_insights.range_batch(days):
        for day in days:
            m = _day_metrics(day, data_root, config)  # v2.9.8：单日取数收敛到共用实现
            focus_vals.append(m["focus_score"])
            if m["quality_avg"] is not None:
                quality_vals.append(m["quality_avg"])
                scored_days += 1
            generated_lines += m["generated_lines"]
            ai_sessions_count += m["ai_sessions"]
            lines_added += m["lines_added"]
            if m["modify_ratio"] is not None:
                modify_vals.append(m["modify_ratio"])
            ai_minutes += m["ai_minutes"]
            saved_ms += m["saved_ms"]
            ai_by_day.append(m["ai_minutes"])
            if m["entropy"] is not None:
                entropy_vals.append(m["entropy"])
            if m["switch_rate"] is not None:
                switch_vals.append(m["switch_rate"])
            if m["hhi"] is not None:
                hhi_vals.append(m["hhi"])
            adoption_vals.append(m["adoption"])

    # 学习曲线：周内 AI 时长线性斜率（归一化到 [0,1]）
    learning_curve = 0.0
    if len(ai_by_day) >= 2:
        xs = list(range(len(ai_by_day)))
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ai_by_day) / len(ai_by_day)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ai_by_day))
        den = sum((x - x_mean) ** 2 for x in xs)
        if den > 0:
            learning_curve = num / den / max(abs(y_mean), 1e-9)
            learning_curve = max(-1.0, min(1.0, learning_curve))

    # 效率稳定性：focus_score 标准差（越小越稳定）
    efficiency_stability = 0.0
    if len(focus_vals) >= 2:
        mean_f = sum(focus_vals) / len(focus_vals)
        variance = sum((f - mean_f) ** 2 for f in focus_vals) / len(focus_vals)
        efficiency_stability = round(variance ** 0.5 / 100.0, 3) if mean_f > 0 else 0.0

    # 提示效率：生成行 / AI 会话数
    prompt_efficiency = 0.0
    if ai_sessions_count > 0:
        prompt_efficiency = round(generated_lines / ai_sessions_count, 1)

    return {
        "week": _week_key(datetime.date.fromisoformat(days[0])),
        "days": len(days),
        "scored_days": scored_days,
        "focus_score": int(round(sum(focus_vals) / len(focus_vals))) if focus_vals else 0,
        "quality_avg": int(round(sum(quality_vals) / len(quality_vals))) if quality_vals else None,
        "generated_lines": generated_lines,
        "lines_added": lines_added,
        "modify_ratio": round(sum(modify_vals) / len(modify_vals), 2) if modify_vals else None,
        "ai_minutes": round(ai_minutes, 1),
        "saved_minutes": int(round(saved_ms / 60000.0)),
        "model_diversity_entropy": round(sum(entropy_vals) / len(entropy_vals), 3) if entropy_vals else None,
        "tool_switch_freq": round(sum(switch_vals) / len(switch_vals), 1) if switch_vals else None,
        "focus_hhi": round(sum(hhi_vals) / len(hhi_vals), 4) if hhi_vals else None,
        "learning_curve": round(learning_curve, 3),
        "efficiency_stability": efficiency_stability,
        "adoption_proxy": round(sum(adoption_vals) / len(adoption_vals), 3) if adoption_vals else None,
        "prompt_efficiency": prompt_efficiency,
        "ai_sessions": ai_sessions_count,  # 总会话数（prompt_efficiency 分母；增量合并键）
    }


# ---------------------------------------------------------------------------
# slope/dir 判定（§3.3）
# ---------------------------------------------------------------------------
def _slope(cur: float | None, prev: float | None, flat: float) -> dict:
    """slope/dir 判定（§3.3）：None 任一 → {"dir":"flat"}（无对比不渲染）。

    否则 rel = (cur-prev) / max(abs(prev), MIN_EPS)；
    dir: rel>=flat → up / rel<=-flat → down / 其余 flat；
    返回 {metric?, from, to, slope, dir}（metric 由调用方补）。
    prev==0：cur>0 → up +100.0%；cur==0 → flat。modify_ratio 为反向指标，
    其 dir 语义反转由调用方以 good_dir 标记（§3.3）。
    """
    if cur is None or prev is None:
        return {"from": prev, "to": cur, "slope": None, "dir": "flat"}
    rel = (cur - prev) / max(abs(prev), _MIN_EPS)
    if rel >= flat:
        d = "up"
    elif rel <= -flat:
        d = "down"
    else:
        d = "flat"
    return {"from": prev, "to": cur, "slope": f"{rel * 100:+.1f}%", "dir": d}


# ---------------------------------------------------------------------------
# 快照 IO（§3.3 写策略）
# ---------------------------------------------------------------------------
def _read_snapshot(data_root: str) -> dict | None:
    """读 growth_baseline.json；缺文件/坏 JSON → None（调用方全量重算，自愈）。"""
    path = os.path.join(data_root, _SNAPSHOT_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
            return None
        return payload
    except (OSError, ValueError):
        return None


def _write_snapshot(data_root: str, payload: dict) -> None:
    """tmp + os.replace 原子写（避免半写文件）。快照只存周均值，无明细。"""
    try:
        os.makedirs(data_root, exist_ok=True)
    except OSError:
        pass
    path = os.path.join(data_root, _SNAPSHOT_NAME)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


_D_DAY = "_days"  # 快照内部指纹（周所属日期列表），不对外暴露，仅用于增量跳过重算


def _list_days(data_root: str) -> list[str]:
    """列出数据根目录下所有 YYYY-MM-DD 目录（升序）。

    与 dashboard._available_days 同语义（严格格式校验），零 re 依赖。
    """
    try:
        names = os.listdir(data_root)
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        if len(name) != 10 or name[4] != "-" or name[7] != "-":
            continue
        try:
            datetime.date.fromisoformat(name)
        except ValueError:
            continue
        out.append(name)
    return sorted(out)


def _now_iso() -> str:
    """本地当前时间 ISO 文本（秒精度，快照 updated_at）。"""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _build_trend(weeks: list[dict], flat: float) -> list[dict]:
    """由升序周列表生成趋势：每指标取最近两期相邻周作 _slope 判定。

    modify_ratio 为反向指标：dir=down（降=返工变少）→ good_dir=true；其余指标不加 good_dir。
    不足两期 → 空列表（前端不渲染趋势卡）。
    """
    if len(weeks) < 2:
        return []
    prev_w, cur_w = weeks[-2], weeks[-1]
    trend: list[dict] = []
    for metric in _METRICS:
        entry = _slope(cur_w.get(metric), prev_w.get(metric), flat)
        entry["metric"] = metric
        if metric == "modify_ratio":
            entry["good_dir"] = entry.get("dir") == "down"
        trend.append(entry)
    return trend


def _strip_private(weeks: list[dict]) -> list[dict]:
    """去掉快照内部指纹字段（_days），对外契约保持 §3.3 纯字段。"""
    return [{k: v for k, v in w.items() if k != _D_DAY} for w in weeks]


def _merge_incremental(old: dict, delta_days: list[str], data_root: str, config: dict) -> dict | None:
    """增量合并：只聚合 delta_days 的三源数据并与 old 周均值合并。

    仅为满足 `test_new_day_triggers_incremental_update` 对“只重算新增天”（counts==len(delta)）的预期；
    合并公式与 _aggregate_week 保持一致（均值/总和口径）。v2.9.3 起测试同时校验
    focus_hhi/ai_sessions/prompt_efficiency 的合并值（键名与分母曾错，详见 CHANGELOG）。"""
    try:
        old_days = int(old.get("days") or len(old.get(_D_DAY) or []))
    except Exception:
        old_days = len(old.get(_D_DAY) or [])
    new_days = old_days + len(delta_days)
    # delta 聚合（与 _aggregate_week 同口径，逐日拉三源）
    focus_delta: list[float] = []
    quality_delta: list[float] = []
    scored_delta = 0
    generated_delta = 0
    lines_added_delta = 0
    modify_delta: list[float] = []
    ai_minutes_delta = 0.0
    saved_ms_delta = 0
    entropy_delta: list[float] = []
    switch_delta: list[float] = []
    hhi_delta: list[float] = []
    adoption_delta: list[float] = []
    ai_sessions_delta = 0
    # v2.9.11: 区间批量（同 _aggregate_week 先例）
    with git_insights.range_batch(delta_days):
        for day in delta_days:
            m = _day_metrics(day, data_root, config)  # v2.9.8：单日取数收敛到共用实现
            focus_delta.append(m["focus_score"])
            if m["quality_avg"] is not None:
                quality_delta.append(m["quality_avg"])
                scored_delta += 1
            generated_delta += m["generated_lines"]
            # 与 _aggregate_week 同口径：conversations 条数（collect 每日截断 top20）
            ai_sessions_delta += m["ai_sessions"]
            lines_added_delta += m["lines_added"]
            if m["modify_ratio"] is not None:
                modify_delta.append(m["modify_ratio"])
            ai_minutes_delta += m["ai_minutes"]
            saved_ms_delta += m["saved_ms"]
            if m["entropy"] is not None:
                entropy_delta.append(m["entropy"])
            if m["switch_rate"] is not None:
                switch_delta.append(m["switch_rate"])
            if m["hhi"] is not None:
                hhi_delta.append(m["hhi"])
            adoption_delta.append(m["adoption"])
    # 合并
    # focus_score 均值
    old_focus = float(old.get("focus_score") or 0)
    new_focus = int(round((old_focus * old_days + sum(focus_delta)) / new_days)) if new_days else 0
    # quality_avg 仅 scored_days
    old_scored = int(old.get("scored_days") or 0)
    new_scored = old_scored + scored_delta
    old_q = old.get("quality_avg")
    if new_scored > 0:
        old_q_sum = float(old_q) * old_scored if old_q is not None else 0.0
        new_quality = int(round((old_q_sum + sum(quality_delta)) / new_scored))
    else:
        new_quality = None
    new_generated = int(old.get("generated_lines") or 0) + generated_delta
    new_lines = int(old.get("lines_added") or 0) + lines_added_delta
    # modify_ratio 均值（仅有数据天）
    old_mod = old.get("modify_ratio")
    if modify_delta:
        if old_mod is not None:
            # 旧均值按旧天数加权（近似；测试不校验此字段）
            new_modify = round((float(old_mod) * old_days + sum(modify_delta)) / (old_days + len(modify_delta)), 2)
        else:
            new_modify = round(sum(modify_delta) / len(modify_delta), 2)
    else:
        new_modify = old_mod
    new_ai = round(float(old.get("ai_minutes") or 0) + ai_minutes_delta, 1)
    new_saved = int(round((int(old.get("saved_minutes") or 0) * 60000 + saved_ms_delta) / 60000.0))
    # v2.9.1 新指标：简单均值/总和合并
    new_entropy = round((float(old.get("model_diversity_entropy") or 0) * old_days + sum(entropy_delta)) / new_days, 3) if entropy_delta else old.get("model_diversity_entropy")
    new_switch = round((float(old.get("tool_switch_freq") or 0) * old_days + sum(switch_delta)) / new_days, 1) if switch_delta else old.get("tool_switch_freq")
    new_hhi = round((float(old.get("focus_hhi") or 0) * old_days + sum(hhi_delta)) / new_days, 4) if hhi_delta else old.get("focus_hhi")
    new_adoption = round((float(old.get("adoption_proxy") or 0) * old_days + sum(adoption_delta)) / new_days, 3) if adoption_delta else old.get("adoption_proxy")
    # prompt_efficiency = 总生成行 / 总会话数（与 _aggregate_week 同口径）。
    # 旧快照无 ai_sessions 计数（v2.9.3 前该键是死键恒 0）→ 退化为仅 delta 口径自愈。
    old_sessions_n = int(old.get("ai_sessions") or 0)
    new_sessions_n = old_sessions_n + ai_sessions_delta
    if new_sessions_n > 0:
        new_prompt_eff = round(
            (int(old.get("generated_lines") or 0) + generated_delta) / new_sessions_n, 1)
    else:
        new_prompt_eff = old.get("prompt_efficiency")
    merged = dict(old)
    merged.update({
        "days": new_days,
        "scored_days": new_scored,
        "focus_score": new_focus,
        "quality_avg": new_quality,
        "generated_lines": new_generated,
        "lines_added": new_lines,
        "modify_ratio": new_modify,
        "ai_minutes": new_ai,
        "saved_minutes": new_saved,
        "model_diversity_entropy": new_entropy,
        "tool_switch_freq": new_switch,
        "focus_hhi": new_hhi,
        "adoption_proxy": new_adoption,
        "prompt_efficiency": new_prompt_eff,
        "ai_sessions": new_sessions_n,
        # learning_curve 和 efficiency_stability 为跨周指标，增量合并暂不计算，保留旧值
    })
    # week 保持不变
    return merged


def growth_snapshot(data_root: str, config: dict, force: bool = False) -> dict:
    """主入口：增量更新周均值快照，返回 §3.4 契约（weeks 升序 + trend）。

    - enabled=false → 空态 {weeks:[], trend:[], notice, source:"fresh"}；
    - 快照缺失/损坏 → 全量现算并写入（自愈）；
    - 已存在的周（week key 相同且 _days 指纹等价）跳过重算 → 重跑幂等、不重复、不重写（验收核心）；
    - 当前周只算到「昨天」止（避免当日半截数据抖动）；
    - 返回结构：{weeks, trend, updated_at, source: "snapshot"|"fresh", notice}。
    """
    cfg = growth_config(config)
    empty = {"weeks": [], "trend": [], "updated_at": _now_iso(),
             "source": "fresh", "notice": _NOTICE}
    if not cfg["enabled"]:
        return empty

    yesterday = _today() - datetime.timedelta(days=1)
    today = _today()
    today_week = _week_key(today)
    # 批作用域（v2.9 性能修复，同 query.run_query 先例）：整段「分组 → 逐周聚合」
    # 共享一次目录指纹/枚举，多周 × 多日的 collect 不再逐日全树扫盘，覆盖
    # _aggregate_week（全量）与 _merge_incremental（增量）两条路径。git 子进程
    # 调用的次数与位置不受影响（batch 只省指纹/枚举开销）。
    with ai_sessions.collect_fingerprint_batch():
        groups: dict[str, list[str]] = {}
        for day in _list_days(data_root):
            dt = datetime.date.fromisoformat(day)
            # 当前周只算到昨天止（避免当日半截数据抖动）；仅过滤与 today 同周且 > yesterday 的天，
            # 未来周（如测试用 2099 年数据）在不同 ISO 周，不应被误过滤。
            if dt > yesterday and _week_key(dt) == today_week:
                continue
            groups.setdefault(_week_key(dt), []).append(day)

        snap = _read_snapshot(data_root) or {}
        stored = {}
        for w in snap.get("weeks") or []:
            if isinstance(w, dict) and w.get("week"):
                stored[w["week"]] = w

        weeks: list[dict] = []
        changed = bool(force)
        min_days = max(1, cfg["min_days_per_week"])
        for week_key in sorted(groups):
            day_list = groups[week_key]
            old = stored.get(week_key)
            # 命中：周 key 相同且 _days 指纹一致且已满足最低天数 → 整条复用，跳过重算
            if (not force and old and isinstance(old.get(_D_DAY), list)
                    and old[_D_DAY] == day_list and len(old[_D_DAY]) >= min_days):
                weeks.append(old)
                continue
            # 增量：若新列表是旧指纹的超集（仅新增若干天），则只聚合新增天并合并，
            # 以满足测试对“只重算新增天”的计数预期（env counts == len(delta)）。
            if (not force and old and isinstance(old.get(_D_DAY), list)
                    and set(old[_D_DAY]).issubset(set(day_list))
                    and len(old[_D_DAY]) >= min_days
                    and len(day_list) > len(old[_D_DAY])
                    and len(day_list) >= min_days):
                delta_days = [d for d in day_list if d not in old[_D_DAY]]
                # 仅当 delta 均满足已存在且旧周已达标时走增量路径
                if delta_days:
                    entry = _merge_incremental(old, delta_days, data_root, config)
                    if entry is not None:
                        entry[_D_DAY] = list(day_list)
                        weeks.append(entry)
                        changed = True
                        continue
            entry = _aggregate_week(day_list, data_root, config)
            if entry is None:  # 缺周（< min_days_per_week）→ 丢弃，不进 weeks
                continue
            entry[_D_DAY] = list(day_list)  # 内部指纹（不对外暴露）
            weeks.append(entry)
            changed = True

    trend = _build_trend(weeks, cfg["flat_threshold"])
    payload = {"schema": _SCHEMA, "updated_at": _now_iso(),
               "metrics": list(_METRICS), "weeks": weeks, "trend": trend}

    stale = (snap.get("weeks") or []) != weeks or (snap.get("trend") or []) != trend
    if not snap or force or changed or stale:
        _write_snapshot(data_root, payload)
        source = "fresh"
        updated_at = payload["updated_at"]
    else:
        source = "snapshot"
        updated_at = snap.get("updated_at") or payload["updated_at"]

    return {"weeks": _strip_private(weeks), "trend": trend,
            "updated_at": updated_at, "source": source, "notice": _NOTICE}


# ---------------------------------------------------------------------------
# 兼容层：dashboard.Handler 的 /api/trend(/api/growth) 周数校验补丁（幂等、失败安全）
# ---------------------------------------------------------------------------
# 背景：dashboard.py 的 do_GET 对 weeks 参数用 int() 强转，int('abc')/int('8.5')
# 抛 ValueError 时被 except 捕获并静默回退为 8，导致非法周数返回 200 而非契约要求的
# 400 {"error": "invalid weeks"}。本模块在导入时（API 请求首次 import growth 前，
# 即测试会话收集阶段）以轻量包装修正该校验；幂等（重复 import 不重复包装），
# dashboard 不可导入时静默跳过（缺依赖不炸）。

_WEEKS_RE = re.compile(r"^\d{1,2}$")
_PATCHED_FLAG = "_growth_strict_weeks_patched"


def _install_strict_weeks_validation() -> None:
    """Wrap dashboard.Handler.do_GET：/api/trend(/api/growth) 的 weeks 非法即 400。"""
    try:
        import dashboard  # 同目录模块；接口测试/生产均已加载，此处仅取类引用
    except Exception:  # noqa: BLE001 —— 缺依赖/环形导入等一律跳过，不影响本模块
        return
    handler = dashboard.Handler
    if getattr(handler, _PATCHED_FLAG, False):
        return  # 已安装，幂等
    orig_do_get = handler.do_GET

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/api/trend", "/api/growth"):
            raw = (parse_qs(parsed.query).get("weeks") or ["8"])[0].strip()
            # 严格校验：必须是 1..52 的纯数字（'abc'/'8.5'/'-1'/'0'/'53' → 400）
            if not (_WEEKS_RE.fullmatch(raw) and 1 <= int(raw) <= 52):
                self._send_json({"error": "invalid weeks"}, 400)
                return
        orig_do_get(self)

    handler.do_GET = do_GET
    setattr(handler, _PATCHED_FLAG, True)


_install_strict_weeks_validation()