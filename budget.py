# -*- coding: utf-8 -*-
"""budget.py — 成本预算告警（v2.6 · P3）。

纯派生模块：聚合 `ai_sessions` 的每日成本估算，对照 `config.insights.budget`
（daily / monthly 两档、默认关闭）判定 正常(ok) / 接近(warn) / 超支(exceed)
三态，供 `report.py` 周/月报预算小结与 `dashboard.py` 概览预算 banner 使用。

约束：零第三方依赖、只读本地、best-effort——任一环节失败都降级为
「关闭/无数据」空态，绝不阻断主流程、绝不伪装精确（成本本身是估算值）。

接口：
- `budget_config(config)`          — 规范化读取预算配置段
- `budget_status(date, ...)`       — 单档预算状态（日期或月份粒度）
- `budget_summary_md(status)`      — 单档状态 → Markdown 小结段
- `budget_week_summary(days, ...)` — 逐日 daily 汇总（周报用）
"""

from __future__ import annotations

import datetime
import re

# ---------------------------------------------------------------------------
# 默认预算配置（读 config.insights.budget；老用户 config.json 无该段也自动兜底）
# ---------------------------------------------------------------------------
_DEFAULT_BUDGET = {
    "enabled": False,   # 默认关闭：不打扰（隐私/无感）
    "daily": 10.0,      # 每日预算（USD，估算口径）
    "monthly": 200.0,   # 每月预算（USD，估算口径）
}

# 接近预警阈值：spent/budget >= 0.8 即告警（对齐设计文档 §5.1 warn_at=0.8）
_WARN_AT = 0.8

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def budget_config(config: dict) -> dict:
    """读取并规范化 `insights.budget` 配置段。

    返回 `{"enabled", "daily", "monthly", "issues"}`：
    - enabled 以 truthy 解释（缺省 False，默认关闭）；
    - daily/monthly 必须是正数，非数字/非正数视为无效档（置 0，记入 issues），
      此时即使 enabled=true，对应档位也不会参与告警（不打扰原则）。
    """
    ins = config.get("insights") if isinstance(config.get("insights"), dict) else {}
    raw = ins.get("budget")
    raw = raw if isinstance(raw, dict) else {}
    out = {
        "enabled": bool(raw.get("enabled", _DEFAULT_BUDGET["enabled"])),
        "daily": 0.0,
        "monthly": 0.0,
        "issues": [],
    }
    for key in ("daily", "monthly"):
        try:
            value = float(raw.get(key, _DEFAULT_BUDGET[key]))
        except (TypeError, ValueError):
            value = 0.0
        if value <= 0:
            out["issues"].append(f"预算 {key}={raw.get(key)!r} 无效（需为正数），该档位视为未配置")
        else:
            out[key] = value
    return out


def _month_days(month_str: str) -> list[str]:
    """某月全部自然日（YYYY-MM → 列表）。月份非法时返回空列表。"""
    try:
        year, mon = map(int, month_str.split("-"))
        last = datetime.date(year, mon, 1).replace(day=28) + datetime.timedelta(days=4)
        last = last - datetime.timedelta(days=last.day)
        return [f"{month_str}-{d:02d}" for d in range(1, last.day + 1)]
    except (TypeError, ValueError, OverflowError):
        # OverflowError：9999-12 这类边界月，+4 天跨过 date 上限(9999-12-31)；
        # 与非法月份同样按「无效」处理，返回空列表由上层转 invalid 空态。
        return []


def _empty_status(date: str, cfg: dict, period: str, status: str) -> dict:
    """空态/无效态的基础结构（不聚合成本，避免不必要扫描）。"""
    return {
        "enabled": cfg["enabled"],
        "period": period,
        "status": status,          # disabled | invalid
        "budget": 0.0,
        "spent": 0.0,
        "remaining": 0.0,
        "ratio": 0.0,
        "start": date,
        "end": date,
        "days": [],
        "by_tool": {},
        "by_project": {},
        "issues": list(cfg["issues"]),
    }


def _collect_day(date_str: str, data_root: str, config: dict) -> dict:
    """单日 AI 成本（本地会话 + Web AI 会话，best-effort）。

    返回 `{"found", "cost", "by_tool", "by_project"}`。任何导入/解析异常
    都视为当日无数据（found=False），不抛异常。
    """
    out = {"found": False, "cost": 0.0, "by_tool": {}, "by_project": {}}
    try:
        import ai_sessions  # noqa: PLC0415 —— 惰性导入，失败只影响本模块
        import browser_history  # noqa: PLC0415
        try:
            web = browser_history.collect(date_str, data_root, config).get("visits") or []
        except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地 AI 统计
            web = []
        data = ai_sessions.collect(date_str, config, web_visits=web or None)
        if not data.get("found"):
            return out
        total = data.get("total") or {}
        out["found"] = True
        out["cost"] = float(total.get("cost_total") or 0.0)
        for name, meta in (total.get("by_project") or {}).items():
            if isinstance(meta, dict):
                out["by_project"][name] = out["by_project"].get(name, 0.0) + (
                    float(meta.get("cost_total") or 0.0))
        for name, meta in (data.get("tools") or {}).items():
            if isinstance(meta, dict):
                out["by_tool"][name] = out["by_tool"].get(name, 0.0) + (
                    float(meta.get("cost_total") or 0.0))
    except Exception:  # noqa: BLE001 —— 解析失败降级为空数据，不阻断主流程
        pass
    return out


def budget_status(date: str, data_root: str, config: dict, period: str | None = None) -> dict:
    """预算状态判定（每日档或每月档，纯派生、无副作用）。

    - `date="YYYY-MM-DD"` → 当日 daily 档；`date="YYYY-MM"` → 当月 monthly 档；
    - `period` 可显式指定 "daily"/"monthly" 覆盖粒度推断（daily 需要完整日期，
      monthly 取 date 的月份部分，宽容截断）。
    - 返回结构（对齐设计文档 §5.1）：
      {enabled, period, status, budget, spent, remaining, ratio,
       start, end, days, by_tool, by_project, issues}
      status: "ok" | "warn" | "exceed" | "disabled" | "invalid"
    """
    cfg = budget_config(config)
    target = period or ("daily" if _DAY_RE.fullmatch(date or "") else "monthly")
    if target not in ("daily", "monthly"):
        st = _empty_status(date or "", cfg, "monthly", "invalid")
        st["issues"].append(f"period={target!r} 非法（仅支持 daily/monthly）")
        return st

    # 归一化范围
    if target == "daily":
        try:
            datetime.datetime.strptime(date, "%Y-%m-%d")
        except (TypeError, ValueError):
            st = _empty_status(str(date or ""), cfg, target, "invalid")
            st["issues"].append(f"date={date!r} 非法：daily 档需要 YYYY-MM-DD")
            return st
        days = [date]
        start = end = date
    else:
        month = (date or "")[:7]
        days = _month_days(month)
        if not days:
            st = _empty_status(str(date or ""), cfg, target, "invalid")
            st["issues"].append(f"date={date!r} 非法：monthly 档需要 YYYY-MM")
            return st
        start, end = days[0], days[-1]

    budget = cfg.get("daily" if target == "daily" else "monthly") or 0.0
    # 功能未开启或对应档位未配置 → 关闭态（不聚合，避免无谓扫描）
    if not cfg["enabled"] or budget <= 0:
        st = _empty_status(start, cfg, target, "disabled")
        st["budget"] = budget
        return st

    spent = 0.0
    day_rows: list[dict] = []
    by_tool: dict[str, float] = {}
    by_project: dict[str, float] = {}
    for d in days:
        try:
            day = _collect_day(d, data_root, config)
        except Exception:  # noqa: BLE001 —— 单日聚合异常视为当日无数据，不阻断整段预算
            continue
        if not day.get("found"):
            continue
        day_rows.append({"date": d, "cost": day["cost"]})
        spent += day["cost"]
        for name, cost in (day.get("by_tool") or {}).items():
            by_tool[name] = by_tool.get(name, 0.0) + float(cost)
        for name, cost in (day.get("by_project") or {}).items():
            by_project[name] = by_project.get(name, 0.0) + float(cost)

    ratio = spent / budget if budget > 0 else 0.0
    if ratio >= 1.0:
        status = "exceed"
    elif ratio >= _WARN_AT:
        status = "warn"
    else:
        status = "ok"
    return {
        "enabled": True,
        "period": target,
        "status": status,
        "budget": budget,
        "spent": round(spent, 6),
        "remaining": round(budget - spent, 6),
        "ratio": round(ratio, 6),
        "start": start,
        "end": end,
        "days": day_rows,
        "by_tool": by_tool,
        "by_project": by_project,
        "issues": list(cfg["issues"]),
    }


# ---------------------------------------------------------------------------
# 文案层（report.py 周/月报章节 + dashboard banner 共用）
# ---------------------------------------------------------------------------
_STATUS_LABEL = {"ok": "正常", "warn": "接近预算（≥80%）", "exceed": "超支"}


def _fmt_ratio(ratio: float) -> str:
    return f"{max(0.0, ratio) * 100:.0f}%"


def budget_summary_md(status: dict) -> str | None:
    """单档预算状态 → Markdown 小结段（月报/日报用）。

    状态为 disabled/invalid/空 → 返回 None（报告不显示该章节）。
    """
    if not status or status.get("status") not in ("ok", "warn", "exceed"):
        return None
    label = {"daily": "每日", "monthly": "月度"}.get(status.get("period"), "")
    out = [
        f"## AI 成本预算（{label}）", "",
        f"- 预算 {_fmt_usd(status.get('budget'))} ｜ 已用 {_fmt_usd(status.get('spent'))} "
        f"｜ 剩余 {_fmt_usd(status.get('remaining'))} ｜ 达 {_fmt_ratio(status.get('ratio'))}",
        f"- 状态：{_STATUS_LABEL.get(status.get('status'), status.get('status'))}",
    ]
    if status.get("issues"):
        out.append("- 配置提示：" + "；".join(status["issues"]))
    return "\n".join(out)


def budget_week_summary(days: list[str], data_root: str, config: dict) -> str | None:
    """逐日 daily 档汇总（周报用）。

    返回 Markdown 段：7 天合计 / 各状态天数 / 超支日明细。
    预算未开启或 daily 档无效 → 返回 None（周报不显示该章节）。
    """
    cfg = budget_config(config)
    if not cfg["enabled"] or cfg["daily"] <= 0:
        return None
    states: dict[str, list] = {"ok": [], "warn": [], "exceed": [], "nodata": []}
    total_spent = 0.0
    for day in days:
        st = budget_status(day, data_root, config, period="daily")
        if st.get("status") in ("ok", "warn", "exceed"):
            if st["status"] == "ok" and not st.get("days"):
                states["nodata"].append(day)  # 没花钱且无任何会话记录 → 无数据
            else:
                states.setdefault(st["status"], []).append(st)
            total_spent += float(st.get("spent") or 0.0)
        else:
            states["nodata"].append(day)

    lines: list[str] = ["## AI 成本预算（每日 · 最近 {n} 天）".format(n=len(days)), ""]
    lines.append(
        f"- 预算 {_fmt_usd(cfg['daily'])}/天 ｜ 合计 {_fmt_usd(total_spent)} "
        f"｜ 超支 {len(states['exceed'])} 天 / 接近 {len(states['warn'])} 天 "
        f"/ 正常 {len(states['ok'])} 天 / 无数据 {len(states['nodata'])} 天")
    if states["exceed"]:
        rows = sorted(states["exceed"], key=lambda s: -float(s.get("ratio") or 0))
        sub = "；".join(
            f"{s['start'][-5:]}（{_fmt_usd(s['spent'])}/{_fmt_usd(s['budget'])}，{_fmt_ratio(s['ratio'])}）"
            for s in rows)
        lines.append(f"- 超支日：{sub}")
    if states["warn"]:
        rows = sorted(states["warn"], key=lambda s: -float(s.get("ratio") or 0))
        sub = "；".join(
            f"{s['start'][-5:]}（{_fmt_ratio(s['ratio'])}）"
            for s in rows)
        lines.append(f"- 接近预算日：{sub}")
    if cfg["issues"]:
        lines.append("- 配置提示：" + "；".join(cfg["issues"]))
    return "\n".join(lines)


def _fmt_usd(value) -> str:
    """美元格式化（与 ai_sessions._fmt_cost 风格一致，就地实现避免跨模块耦合）。"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v == 0:
        return "$0"
    if v < 0.01:
        return f"${v:.4f}"
    if v < 1:
        return f"${v:.3f}"
    return f"${v:.2f}"


if __name__ == "__main__":
    # 手动演练：python budget.py --day 2026-08-20 [--data-root ...]
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="budget.py", description="成本预算状态（纯派生查询）")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="每日档状态")
    parser.add_argument("--month", metavar="YYYY-MM", help="每月档状态")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args()

    try:
        import classifier  # noqa: PLC0415
        import paths  # noqa: PLC0415
        root = args.data_root or paths.default_data_root()
        config = classifier.load_config(
            None if root == paths.default_data_root() else
            os.path.join(root, "config.json"))
    except Exception:  # noqa: BLE001
        config, root = {}, args.data_root or ""

    if args.month:
        res = budget_status(args.month, root, config, period="monthly")
    elif args.day:
        res = budget_status(args.day, root, config, period="daily")
    else:
        print("用法：--day YYYY-MM-DD | --month YYYY-MM [--data-root DIR]")
        raise SystemExit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2))