# -*- coding: utf-8 -*-
"""goals.py — 每日目标与连续达成（v2.7「行动与目标」，可选功能，默认关闭）。

用户在设置页开启并配置两类目标：
- 总活跃时长目标（daily_active_min，分钟）
- 编码时长目标（daily_coding_min，分钟；口径 = 开发工具 + AI编程 两类）

进度与 streak 均为**纯派生**：从历史日聚合即时回推，不落任何状态文件。
因此修改目标后 streak 按新目标重算（文档已注明此语义）。

配置段（config.json 的 "goals"）：
    "goals": {
        "enabled": false,
        "daily_active_min": 480,
        "daily_coding_min": 240
    }

API（dashboard.py 注册）：
- GET  /api/goals?date=YYYY-MM-DD → 进度 + streak
- POST /api/goals/settings        → 保存目标设置（原子写 config.json）
"""

from __future__ import annotations

import datetime
import re

import report

# 编码口径：这两类的合计视为「编码时长」
CODING_CATEGORIES = ("开发工具", "AI编程")

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STREAK_LOOKBACK = 90  # streak 最长回看天数（性能护栏）

_DEFAULTS = {"enabled": False, "daily_active_min": 0, "daily_coding_min": 0}


def goals_config(config: dict | None) -> dict:
    """归一化 goals 配置段：缺省关闭、非法数值回退 0、夹取 [0, 1440]。"""
    raw = config.get("goals") if isinstance(config, dict) and isinstance(config.get("goals"), dict) else {}
    out = dict(_DEFAULTS)
    out["enabled"] = bool(raw.get("enabled", False))
    for key in ("daily_active_min", "daily_coding_min"):
        try:
            out[key] = max(0, min(1440, int(raw.get(key, 0))))
        except (TypeError, ValueError):
            out[key] = 0
    return out


def goal_defs(cfg: dict) -> list[dict]:
    """当前生效的目标定义（target_min>0 才算启用）。"""
    defs: list[dict] = []
    if cfg["daily_active_min"] > 0:
        defs.append({"id": "active", "name": "总活跃", "target_min": cfg["daily_active_min"]})
    if cfg["daily_coding_min"] > 0:
        defs.append({"id": "coding", "name": "编码（开发工具+AI编程）",
                     "target_min": cfg["daily_coding_min"]})
    return defs


def _actual_for(goal_id: str, agg: dict) -> int:
    """某目标在给定聚合下的实际分钟数。"""
    if goal_id == "active":
        return int(agg.get("total_active_ms") or 0) // 60000
    ms = sum(int((agg.get("by_category") or {}).get(c, 0)) for c in CODING_CATEGORIES)
    return ms // 60000


def _day_met(agg: dict, defs: list[dict]) -> bool:
    """单日是否全部目标达成（无启用目标时恒 False——空目标不构成 streak）。"""
    if not defs:
        return False
    return all(_actual_for(g["id"], agg) >= g["target_min"] for g in defs)


# 日期列表缓存统一收敛到 dashboard_util._available_days（单一实现，seed_day 的
# invalidate_days_cache 才能覆盖到——v2.9.12 修复：goals 曾自持一份 _DAYS_CACHE，
# 只靠 mtime 失效，CI 快速盘同一时钟刻度内「播种→读取」拿到旧列表，streak 误判）。


def _available_days(data_root: str) -> list[str]:
    """数据根目录下全部 YYYY-MM-DD 文件夹（升序）——委托 dashboard_util 单一实现。

    缓存口径（mtime + TTL）与失效入口（invalidate_days_cache）都以
    dashboard_util 为唯一事实源，避免两份缓存各自漂移（v2.9.12）。
    """
    import dashboard_util  # noqa: PLC0415  单一实现（纯标准库，无循环依赖）

    return dashboard_util._available_days(data_root)


def compute_streak(date: str, data_root: str, cfg: dict,
                   lookback: int = _STREAK_LOOKBACK) -> tuple[int, bool]:
    """从 date 起按自然日回推连续全达标天数。

    - 当日未达成不断签：streak 从昨天起算（避免白天看着归零）；
    - 缺数据的自然日视为未达成（断签）；
    - 返回 (streak, today_met)，lookback 上限防止长历史下逐日聚合过重。
    """
    defs = goal_defs(cfg)
    if not defs or not _DAY_RE.fullmatch(date or ""):
        return 0, False
    try:
        cur = datetime.date.fromisoformat(date)
    except ValueError:
        return 0, False
    have = set(_available_days(data_root))
    streak = 0
    today_met = False
    for i in range(max(1, lookback)):
        iso = cur.isoformat()
        met = _day_met(report.aggregate(iso, data_root), defs) if iso in have else False
        if i == 0:
            today_met = met
            if not met:
                cur -= datetime.timedelta(days=1)
                continue  # 当日未达成：从昨天继续（不断签）
            streak += 1
        elif met:
            streak += 1
        else:
            break
        cur -= datetime.timedelta(days=1)
    return streak, today_met


def today_progress(date: str, data_root: str, config: dict | None) -> dict:
    """当日目标进度 + streak（纯派生；功能关闭时返回 enabled=false 空态）。"""
    cfg = goals_config(config)
    out: dict = {
        "date": date,
        "enabled": bool(cfg["enabled"]),
        "goals": [],
        "all_met": False,
        "streak": {"current": 0, "today_met": False},
    }
    if not out["enabled"] or not _DAY_RE.fullmatch(date or ""):
        return out
    defs = goal_defs(cfg)
    if not defs:
        return out
    agg = report.aggregate(date, data_root)
    goals = []
    for g in defs:
        actual = _actual_for(g["id"], agg)
        goals.append({
            "id": g["id"],
            "name": g["name"],
            "target_min": g["target_min"],
            "actual_min": actual,
            "ratio": round(min(1.0, actual / g["target_min"]), 4),
            "met": actual >= g["target_min"],
        })
    streak, today_met = compute_streak(date, data_root, cfg)
    out["goals"] = goals
    out["all_met"] = all(g["met"] for g in goals)
    out["streak"] = {"current": streak, "today_met": today_met}
    return out
