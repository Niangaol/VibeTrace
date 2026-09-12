# -*- coding: utf-8 -*-
"""advice.py — 概览「建议」栏位（可选功能，默认关闭）。

从当日聚合与会话深度数据里提炼**可执行的短建议**，每条都带数字依据（拒绝空话）。
不调用任何 LLM、不联网、只读本地数据；可解释性优先。

复用既有框架（不另起判断逻辑）：
- 行为类指标：insights.activitywatch_metrics（专注时长 / 深度工作块 / 项目集中度）
- 目标进度：goals.today_progress（仅当 goals.enabled）
- 会话与成本：ai_sessions.collect

配置（config.json → advice 段）：
    "advice": {"enabled": false, "max_items": 6}

接口：
    advice_for_day(date_str, data_root, config) ->
        {"enabled": bool, "date": str, "items": [{"id", "level", "title", "detail"}]}
    level 取值 alert / warn / info（排序优先级依次降低）
"""

from __future__ import annotations

_LEVEL_ORDER = {"alert": 0, "warn": 1, "info": 2}
_NIGHT_HOURS = range(0, 6)          # 0-6 点视为深夜
_DEEP_WORK_WARN_MIN = 120           # 连续深度工作提示阈值（分钟）
_NIGHT_WARN_MIN = 30                # 深夜活跃提示阈值（分钟）
_COST_SPIKE_RATIO = 1.8             # 今日成本 / 近 7 日均值 的异常倍数
_COST_MIN_USD = 1.0                 # 成本提示的最低绝对值（避免小额噪音）
_CACHE_SHARE_INFO = 0.90            # 缓存读占输入比例（正向说明阈值）
_UNKNOWN_TURNS_WARN = 0.30          # 未识别模型消息占比
_FOCUS_MIN_FOR_HINT = 60            # 编码少于该分钟数不做专注类建议（避免噪音）
_HHI_LOW = 0.25                     # 项目集中度偏低阈值


def _section(config) -> dict:
    sec = (config or {}).get("advice")
    return sec if isinstance(sec, dict) else {}


def enabled(config) -> bool:
    """建议系统开关（默认关闭：可选功能）。"""
    return bool(_section(config).get("enabled"))


def max_items(config) -> int:
    """单次返回的建议条数上限（1..20，默认 6）。"""
    try:
        n = int(_section(config).get("max_items") or 6)
    except (TypeError, ValueError):
        n = 6
    return max(1, min(20, n))


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 —— 单条数据源失败不影响其他建议
        return default


def _item(rid: str, level: str, title: str, detail: str) -> dict:
    return {"id": rid, "level": level, "title": title, "detail": detail}


def _rule_deep_work(aw: dict, items: list) -> None:
    """连续深度工作过久 → 休息。"""
    minutes = int(aw.get("deep_work_min") or 0)
    if minutes >= _DEEP_WORK_WARN_MIN:
        items.append(_item(
            "deep_work", "warn",
            f"已连续深度工作约 {minutes} 分钟",
            "建议起身活动 5-10 分钟：休息后注意力和代码质量都会回升。"))


def _rule_night(agg: dict, items: list) -> None:
    """深夜活跃 → 作息建议。"""
    hourly = agg.get("hourly_ms") or []
    if not isinstance(hourly, list) or len(hourly) < 24:
        return
    night_ms = sum(int(hourly[h] or 0) for h in _NIGHT_HOURS)
    minutes = round(night_ms / 60000.0)
    if minutes >= _NIGHT_WARN_MIN:
        items.append(_item(
            "night_owl", "warn",
            f"凌晨 0-6 点活跃 {minutes} 分钟",
            "长期熬夜会拉低次日效率；可尝试把高耗任务挪到白天。"))


def _rule_cost_spike(today_cost: float, past_costs: list, items: list) -> None:
    """今日成本显著高于近 7 日均值 → 成本提示。"""
    past = [c for c in past_costs if c > 0]
    if today_cost < _COST_MIN_USD or not past:
        return
    avg = sum(past) / len(past)
    if avg <= 0 or today_cost < avg * _COST_SPIKE_RATIO:
        return
    title = ("今日成本 $" + format(today_cost, ".2f") + "，是近 7 日均值 $"
             + format(avg, ".2f") + " 的 " + format(today_cost / avg, ".1f") + " 倍")
    items.append(_item(
        "cost_spike", "warn", title,
        "可在「设置 · 模型价格」核对单价，或检查是否有超长上下文会话。"))


def _rule_cache_share(total: dict, items: list) -> None:
    """缓存占输入比例极高 → 正向说明（同时解释「Token 入」口径）。"""
    fresh = int(total.get("tokens_input_fresh") or 0)
    cr = int(total.get("tokens_cache_read") or 0)
    cw = int(total.get("tokens_cache_write") or 0)
    denom = fresh + cr + cw
    if denom <= 0 or cr <= 0:
        return
    share = cr / denom
    if share >= _CACHE_SHARE_INFO:
        items.append(_item(
            "cache_share", "info",
            f"缓存命中占输入 {share * 100:.0f}%",
            "说明上下文被高效复用：缓存读按缓存单价计费，成本效率较好"
            "（概览「Token 入」只统计新鲜输入，不含这部分）。"))


def _rule_unknown_model(total: dict, items: list) -> None:
    """未识别模型占比偏高 → 建议补模型信息。"""
    by_model = total.get("by_model") or {}
    if not isinstance(by_model, dict) or not by_model:
        return
    turns_total = sum(int((v or {}).get("turns") or 0) for v in by_model.values())
    if turns_total <= 0:
        return
    unknown = sum(int((v or {}).get("turns") or 0)
                  for k, v in by_model.items()
                  if "未识别" in str(k) or "unknown" in str(k).lower())
    share = unknown / turns_total
    if share >= _UNKNOWN_TURNS_WARN:
        items.append(_item(
            "unknown_model", "info",
            f"未识别模型的会话占 {share * 100:.0f}%",
            "这些消息按字符折算估算、且无单价（成本记 0）；可在「设置 · 模型价格」"
            "补充模型名或单价，让成本统计更准。"))


def _rule_ai_idle(focus_min: float, turns: int, items: list) -> None:
    """编码时间不短但当日没有 AI 会话 → 建议尝试 AI 辅助。"""
    if focus_min >= _FOCUS_MIN_FOR_HINT and turns == 0:
        items.append(_item(
            "ai_idle", "info",
            f"今日编码约 {int(focus_min)} 分钟，暂无 AI 会话记录",
            "遇到样板代码、报错定位或重构时，交给 AI 起草往往比手写更快。"))


def _rule_focus_scatter(aw: dict, focus_min: float, items: list) -> None:
    """项目集中度偏低 → 专注建议。"""
    if focus_min < _FOCUS_MIN_FOR_HINT:
        return
    hhi = float(aw.get("project_focus_hhi") or 0.0)
    if 0 < hhi < _HHI_LOW:
        items.append(_item(
            "focus_scatter", "info",
            f"项目集中度偏低（HHI {hhi:.2f}）",
            "今日注意力分散在多个项目/窗口中，试着给单个任务留出更长的连续时间。"))


def _rule_goals(date_str: str, data_root: str, config: dict, items: list) -> None:
    """目标未达成（仅在 goals 功能开启时）→ 进度提示。"""
    import goals  # noqa: PLC0415 —— 惰性导入，失败不影响其他建议
    prog = goals.today_progress(date_str, data_root, config)
    if not prog.get("enabled"):
        return
    for g in prog.get("goals") or []:
        if g.get("met"):
            continue
        gap = max(0, int(g.get("target_min") or 0) - int(g.get("actual_min") or 0))
        items.append(_item(
            "goal_gap", "info",
            f"目标「{g.get('name')}」还差 {gap} 分钟",
            f"当前 {g.get('actual_min')} / {g.get('target_min')} 分钟，今天还有时间补上。"))


def _past_costs(date_str: str, config: dict, ai_sessions_mod) -> list:
    """前 7 日各自的成本（仅取有数据的日）；用于成本异常对比。

    批作用域共享一次目录指纹（同 query/budget 先例），避免逐日重复全树扫描。
    """
    import datetime  # noqa: PLC0415
    try:
        base = datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return []
    out: list = []
    with ai_sessions_mod.collect_fingerprint_batch():
        for off in range(1, 8):
            day = (base - datetime.timedelta(days=off)).isoformat()
            data = _safe(lambda d=day: ai_sessions_mod.collect(d, config, web_visits=[]), {}) or {}
            cost = float(((data.get("total") or {}).get("cost_total")) or 0.0)
            if cost > 0:
                out.append(cost)
    return out


def advice_for_day(date_str: str, data_root: str, config: dict) -> dict:
    """当日建议（可选功能；关闭时返回 enabled=false 空态，零额外开销）。

    响应带 max_items，供设置页回显（关闭时也返回，保证设置页可编辑）。
    """
    out: dict = {"enabled": enabled(config), "date": date_str,
                 "max_items": max_items(config), "items": []}
    if not out["enabled"]:
        return out

    import report  # noqa: PLC0415
    import ai_sessions  # noqa: PLC0415
    import insights  # noqa: PLC0415

    agg = _safe(lambda: report.aggregate(date_str, data_root), {}) or {}
    ai = _safe(lambda: ai_sessions.collect(date_str, config, web_visits=[]), {}) or {}
    total = ai.get("total") or {}
    aw = _safe(lambda: insights.activitywatch_metrics(agg, config), {}) or {}
    focus_min = float(aw.get("focus_time") or 0)
    turns = int(total.get("turns") or 0)

    _rule_deep_work(aw, out["items"])
    _rule_night(agg, out["items"])
    _rule_cost_spike(float(total.get("cost_total") or 0.0),
                     _past_costs(date_str, config, ai_sessions),
                     out["items"])
    _rule_cache_share(total, out["items"])
    _rule_unknown_model(total, out["items"])
    _rule_ai_idle(focus_min, turns, out["items"])
    _rule_focus_scatter(aw, focus_min, out["items"])
    _safe(lambda: _rule_goals(date_str, data_root, config, out["items"]), None)

    items = sorted(out["items"], key=lambda it: _LEVEL_ORDER.get(it.get("level"), 9))
    out["items"] = items[:out["max_items"]]
    return out
