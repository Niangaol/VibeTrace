# -*- coding: utf-8 -*-
"""v2.6 · P6 多工具横向对比 —— 派生纯函数层。

对应 docs/VIBECODING_IMPLEMENTATION_GUIDE.md §5.2 与 docs/COMPARE_GROWTH_P67_DESIGN.md §2。

**职责**：跨 N 天聚合 `ai_sessions.collect().tools` + `report.aggregate().by_ai`，
归一化为可按性价比/产出/质量排序的对比表。实时派生、不落盘。

**两漏斗互补**（§2.1）：`collect().tools[tool]` 提供深度漏斗（tokens/cost/generated/
rounds/sessions/quality），`report.aggregate().by_ai[tool]` 提供前台漏斗（分钟级 AI 时长）；
`minutes` 必须来自 by_ai 而非 conversations 时间差（后者系统性低估，TIMELINE 先例同口径）。

**project 过滤**（可选参数 `project`）：按模糊子串（大小写不敏感）把每工具裁剪到
目标项目作用域——by_project 精确维提供 turns/tokens/cost，会话级模糊匹配提供
sessions/rounds/quality；前台 minutes 按 usage 会话 title/app/exe 模糊匹配。
注意：`collect().tools` / `by_project` 均不带 generated_chars/lines 维度，故 project
模式下该维度不可得 → 置 None，`chars_per_dollar`/`chars_per_session` 随之为 None（排最后）。

**铁律**：只读 import `ai_sessions` / `report`，不修改任何既有模块；不写 usage.jsonl / usage.db。

依赖：仅项目内 `ai_sessions`/`report` + Python 标准库（零第三方运行时依赖）。
"""

from __future__ import annotations

import datetime
import math
from typing import Any

import ai_sessions  # 只读复用（collect / quality_grade）
import report  # 只读复用（aggregate → by_ai / sessions）

# ---------------------------------------------------------------------------
# 默认配置（读 config.tool_compare；风格对齐 git_insights.git_config git_insights.py:37）
# ---------------------------------------------------------------------------
_DEFAULT_COMPARE = {
    "enabled": True,
    "sort_by": "chars_per_dollar",   # chars_per_dollar | cost_per_1k_tokens | quality_avg | sessions
    "top": 10,                        # 对比表最多行；0 = 全部
    "min_sessions": 1,                # 少于该会话数的工具行剔除（防噪声）
    "max_days": 90,                   # start~end 范围上限（DoS 防护）
}

# 派生指标（§2.2）：全部带「仅参考」标注，UI 必须展示 notice
_NOTICE = (
    "仅参考：token/成本为本地会话文件估算，非官方账单；minutes 为前台窗口级 AI 时长，"
    "与深度漏斗（tokens/cost/产出）口径不同。"
)

# 汇总求和的工具统计字段（对齐 ai_sessions._empty_tool_stats ai_sessions.py:852）
_SUM_KEYS = (
    "files", "turns", "rounds", "user_messages", "assistant_messages",
    "generated_lines", "generated_chars", "tokens_in", "tokens_out",
    "tokens_total", "cost_in", "cost_out", "cost_total",
)

# 会话质量分档（对齐 ai_sessions._GRADE_NAMES ai_sessions.py:617）
_GRADE_NAMES = ("优", "良", "中", "待优化")


def compare_config(config: dict) -> dict:
    """从完整 config 提取 tool_compare 段并补齐默认值（老用户 config.json 无该段也能跑）。"""
    raw = (config or {}).get("tool_compare")
    sec = raw if isinstance(raw, dict) else {}
    out = dict(_DEFAULT_COMPARE)
    out["enabled"] = bool(sec.get("enabled", _DEFAULT_COMPARE["enabled"]))
    out["sort_by"] = str(sec.get("sort_by") or _DEFAULT_COMPARE["sort_by"])
    for key in ("top", "min_sessions", "max_days"):
        try:
            out[key] = max(0, int(sec.get(key, _DEFAULT_COMPARE[key])))
        except (TypeError, ValueError):
            pass
    return out


def _empty_result(start: str, end: str, days: list[str]) -> dict:
    """契约空态（§2.3）：200 可展示，非 500。"""
    return {
        "start": start, "end": end, "days": len(days),
        "notice": _NOTICE, "tools": [],
        "summary": {"tools": 0, "total_sessions": 0, "total_cost": 0.0, "total_minutes": 0.0},
    }


def _validate_days(days: list[str], cfg: dict) -> list[str]:
    """归一化日期列表：去重、按升序排序、校验范围上限。

    - 非法格式（非 YYYY-MM-DD）→ ValueError（端点映射为 400 invalid range）；
    - 长度 > max_days → ValueError（防 DoS）；
    - 排序幂等：乱序输入输出仍升序。
    """
    clean: list[str] = []
    for d in days or []:
        try:
            datetime.date.fromisoformat(d)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad date {d!r}") from exc
        if d not in clean:
            clean.append(d)
    clean.sort()
    if len(clean) > max(1, cfg.get("max_days", 90)):
        raise ValueError(f"range too large ({len(clean)} days > max_days)")
    return clean


def _fuzzy_match(project: str | None, value: Any) -> bool:
    """模糊项目匹配（substring，大小写不敏感）；空 needle 恒 True（无需过滤）。"""
    needle = (project or "").strip().lower()
    if not needle:
        return True
    return isinstance(value, str) and needle in value.lower()


def _project_row(stats: dict, project: str) -> dict:
    """把某工具某天的全量 tool stats 裁剪为 project 作用域（§2.4 project 过滤）。

    - turns/tokens/co st 来自 `by_project` 模糊命中的键求和（精确、全量）；
    - sessions/rounds/user/assistant 消息数来自会话级模糊匹配（convs 为 top-N 截断后的
      子集，估算口径，notice 已标注「仅参考」）；
    - generated_lines/chars 在 collect 输出中无项目维度 → None（推导为 chars_per_* = None）。
    """
    needle = project or ""
    convs = [c for c in (stats.get("conversations") or []) if _fuzzy_match(needle, c.get("project"))]
    bp = stats.get("by_project") or {}
    matched = [k for k in bp if _fuzzy_match(needle, k)]
    turns = tokens_in = tokens_out = tokens_total = 0
    cost_in = cost_out = cost_total = 0.0
    for k in matched:
        e = bp[k]
        turns += int(e.get("turns") or 0)
        tokens_in += int(e.get("tokens_in") or 0)
        tokens_out += int(e.get("tokens_out") or 0)
        tokens_total += int(e.get("tokens_total") or 0)
        cost_in += float(e.get("cost_in") or 0)
        cost_out += float(e.get("cost_out") or 0)
        cost_total += float(e.get("cost_total") or 0)
    row: dict[str, Any] = {key: 0 for key in _SUM_KEYS}
    row["rounds"] = sum(int(c.get("rounds") or 0) for c in convs)
    row["user_messages"] = sum(int(c.get("user_messages") or 0) for c in convs)
    row["assistant_messages"] = sum(int(c.get("assistant_messages") or 0) for c in convs)
    row["generated_lines"] = None
    row["generated_chars"] = None
    row["turns"] = turns
    row["tokens_in"] = tokens_in
    row["tokens_out"] = tokens_out
    row["tokens_total"] = tokens_total
    row["cost_in"] = cost_in
    row["cost_out"] = cost_out
    row["cost_total"] = cost_total
    row["by_model"] = {}
    row["by_project"] = {k: dict(bp[k]) for k in matched}
    row["conversations"] = convs
    return row


def _project_minutes(agg: dict | None, project: str, tool: str) -> float:
    """project 模式下前台分钟数：usage 会话（ai_tool==tool）按 title/app/exe 模糊匹配求和。"""
    total_ms = 0
    for s in (agg or {}).get("sessions") or []:
        if s.get("ai_tool") != tool:
            continue
        if _fuzzy_match(project, s.get("title")) or _fuzzy_match(project, s.get("app")) \
                or _fuzzy_match(project, s.get("exe")):
            total_ms += int(s.get("duration_ms") or 0)
    return total_ms / 60000.0


def _merge_dim(target: dict, src: dict) -> None:
    """把 src 维度聚合并入 target（镜像 ai_sessions._merge_dim ai_sessions.py:1029 语义）。"""
    for key, e in (src or {}).items():
        t = target.setdefault(key, {"turns": 0, "tokens_in": 0, "tokens_out": 0,
                                    "tokens_total": 0, "cost_in": 0.0, "cost_out": 0.0,
                                    "cost_total": 0.0})
        t["turns"] += int(e.get("turns") or 0)
        t["tokens_in"] += int(e.get("tokens_in") or 0)
        t["tokens_out"] += int(e.get("tokens_out") or 0)
        t["tokens_total"] += int(e.get("tokens_total") or 0)
        t["cost_in"] += float(e.get("cost_in") or 0)
        t["cost_out"] += float(e.get("cost_out") or 0)
        t["cost_total"] += float(e.get("cost_total") or 0)


def _merge_tool_stats(rows: list[dict]) -> dict:
    """把某工具跨天的 collect().tools[tool] stats 逐字段求和（§2.4）。

    - `_SUM_KEYS` 逐项累加；任一行为 None 的字段（project 模式的 generated_*）保持 None
      （上游数据不可得，推导为对应派生指标 None，排最后）；
    - `by_model`/`by_project` 按 `_merge_dim` 语义合并（ai_sessions.py:1029）；
    - `conversations` extend 后先算 sessions/质量（全量口径），再按 turns 截断 top 20
      （与 collect 的展示截断对齐；sessions = 截断前会话条数，防长区间低估）。
    """
    merged: dict[str, Any] = {key: None for key in _SUM_KEYS}
    merged["by_model"] = {}
    merged["by_project"] = {}
    merged["conversations"] = []
    for row in rows:
        for key in _SUM_KEYS:
            v = row.get(key)
            if v is None:
                continue  # 保持 None：该维上游不可得（project 模式 generated_*）
            merged[key] = (merged[key] or 0) + (v if isinstance(v, (int, float)) else 0)
        _merge_dim(merged["by_model"], row.get("by_model"))
        _merge_dim(merged["by_project"], row.get("by_project"))
        merged["conversations"].extend(row.get("conversations") or [])
    convs = merged["conversations"]
    merged["sessions"] = len(convs)
    # 质量（全量口径，截断前统计；无 scored 会话 → None / 零档位）
    scored = [c for c in convs if isinstance(c, dict) and isinstance(c.get("quality_score"), int)]
    if scored:
        merged["quality_avg"] = int(round(sum(c["quality_score"] for c in scored) / len(scored)))
    else:
        merged["quality_avg"] = None
    grade_dist: dict[str, int] = {g: 0 for g in _GRADE_NAMES}
    for c in scored:
        g = c.get("quality_grade") or ai_sessions.quality_grade(c["quality_score"])
        grade_dist[g] = grade_dist.get(g, 0) + 1
    merged["grade_dist"] = grade_dist
    convs.sort(key=lambda c: int(c.get("turns") or 0), reverse=True)
    merged["conversations"] = convs[:20]
    return merged


def _shannon_entropy(counts: list[float]) -> float:
    """计算 Shannon 熵（bits）。"""
    total = sum(counts)
    if total <= 0:
        return 0.0
    ent = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return round(ent, 3)


def _derive_metrics(merged: dict, totals: dict) -> dict:
    """按 §2.2 表派生归一化指标 + share_pct + 排序键（纯函数，除零兜底）。

    merged: {sessions, minutes, tokens_total, cost_total, generated_chars,
             generated_lines, quality_avg, grade_dist, by_model, by_project}
    totals: {cost, sessions, tokens} 全工具总和（share_pct 分母）
    派生：cost_per_1k_tokens / chars_per_dollar / chars_per_session /
           tokens_per_session / share_pct{ cost, sessions, tokens } /
           model_diversity_entropy / prompt_efficiency / focus_hhi
    除零规则：tokens_total<=0 → cost_per_1k_tokens=None；cost_total<=1e-9 或
    generated_chars 不可得（None）→ chars_per_dollar=None；sessions==0 → 0。None 排最后。
    """
    tokens = int(merged.get("tokens_total") or 0)
    cost = float(merged.get("cost_total") or 0)
    chars = merged.get("generated_chars")
    sessions = int(merged.get("sessions") or 0)
    generated_lines = int(merged.get("generated_lines") or 0)

    merged["cost_per_1k_tokens"] = None if tokens <= 0 else round(cost / tokens * 1000, 4)
    if chars is None:
        merged["chars_per_dollar"] = None
        merged["chars_per_session"] = None
    else:
        merged["chars_per_dollar"] = None if cost <= 1e-9 else round(chars / cost, 4)
        merged["chars_per_session"] = 0.0 if sessions <= 0 else round(chars / sessions, 4)
    merged["tokens_per_session"] = 0.0 if sessions <= 0 else round(tokens / sessions, 4)

    # v2.9.1 新增派生指标
    by_model = merged.get("by_model") or {}
    model_counts = [float(v.get("turns") or 0) for v in by_model.values() if isinstance(v, dict)]
    merged["model_diversity_entropy"] = _shannon_entropy(model_counts) if model_counts else None
    merged["prompt_efficiency"] = round(generated_lines / sessions, 1) if sessions > 0 else None
    by_project = merged.get("by_project") or {}
    proj_shares = [float(v.get("turns") or 0) for v in by_project.values() if isinstance(v, dict)]
    total_proj = sum(proj_shares)
    merged["focus_hhi"] = round(sum((s / total_proj) ** 2 for s in proj_shares), 4) if total_proj > 0 else None

    share: dict[str, float] = {}
    for key, val in (("cost", cost), ("sessions", float(sessions)), ("tokens", float(tokens))):
        total = float((totals or {}).get(key) or 0)
        share[key] = round(val / total, 4) if total > 1e-9 else 0.0
    merged["share_pct"] = share
    return merged

def _sort_tools(tools: list[dict], sort_by: str, top: int) -> list[dict]:
    """按排序键降序（None 排最后），截断 top 行（top<=0 不截断）。"""
    if not tools:
        return tools

    def _key(row: dict):
        val = row.get(sort_by)
        return (1 if val is None else 0, -(val if isinstance(val, (int, float)) else 0))

    out = sorted(tools, key=_key)
    if top and top > 0:
        out = out[:top]
    return out


def compare_tools(days: list[str], data_root: str, config: dict,
                  project: str | None = None) -> dict:
    """跨 N 天聚合工具对比（主入口，纯派生，不落盘）。§2.4。

    返回 docs/COMPARE_GROWTH_P67_DESIGN.md §2.3 契约的 dict。
    enabled=false、days 为空或无数据 → 契约空态（200）。
    project 可选：模糊子串过滤到单项目作用域（会话/tokens/成本/时长均收窄）。

    流程：配置兜底 → 日期归一化（ValueError 上抛 400）→ 逐日 best-effort
    collect + aggregate（单日失败仅跳过该日）→ 按 tool 归并 _merge_tool_stats
    → minutes（by_ai 毫秒 / 60000；project 模式按 title 匹配 usage 会话）
    → min_sessions 过滤 → 全量 totals → _derive_metrics → _sort_tools → summary。
    """
    cfg = compare_config(config)
    if not cfg.get("enabled"):
        return _empty_result(days[0] if days else "", days[-1] if days else "", days)
    days = _validate_days(days, cfg)
    if not days:
        return _empty_result("", "", days)
    start, end = days[0], days[-1]

    per_tool: dict[str, list[dict]] = {}
    minutes: dict[str, float] = {}
    for day in days:
        try:  # best-effort：单日任何源失败仅跳过，不拖垮整体（对齐 timeline 降级） 
            col = ai_sessions.collect(day, config)
        except Exception:  # noqa: BLE001
            col = None
        try:
            agg = report.aggregate(day, data_root)
        except Exception:  # noqa: BLE001
            agg = None
        by_ai = (agg or {}).get("by_ai") or {}
        for tool, stats in ((col or {}).get("tools") or {}).items():
            row = _project_row(stats, project) if project else stats
            per_tool.setdefault(tool, []).append(row)
            if project:
                minutes[tool] = minutes.get(tool, 0.0) + _project_minutes(agg, project, tool)
            else:
                minutes[tool] = minutes.get(tool, 0.0) + float(by_ai.get(tool) or 0) / 60000.0

    rows: list[dict] = []
    for tool, day_rows in per_tool.items():
        merged = _merge_tool_stats(day_rows)
        merged["tool"] = tool
        merged["minutes"] = round(minutes.get(tool, 0.0), 2)
        rows.append(merged)

    min_sessions = max(0, int(cfg.get("min_sessions", 1)))
    rows = [r for r in rows if int(r.get("sessions") or 0) >= min_sessions]
    if not rows:
        return _empty_result(start, end, days)

    totals = {
        "cost": sum(float(r.get("cost_total") or 0) for r in rows),
        "sessions": sum(int(r.get("sessions") or 0) for r in rows),
        "tokens": sum(int(r.get("tokens_total") or 0) for r in rows),
    }
    for r in rows:
        _derive_metrics(r, totals)
    tools = _sort_tools(rows, cfg.get("sort_by", "chars_per_dollar"), int(cfg.get("top", 10)))
    return {
        "start": start, "end": end, "days": len(days),
        "notice": _NOTICE, "tools": tools,
        "summary": {
            "tools": len(tools),
            "total_sessions": sum(int(r.get("sessions") or 0) for r in tools),
            "total_cost": round(sum(float(r.get("cost_total") or 0) for r in tools), 2),
            "total_minutes": round(sum(float(r.get("minutes") or 0) for r in tools), 2),
        },
    }