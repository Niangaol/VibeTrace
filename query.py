# -*- coding: utf-8 -*-
"""v2.6 · P7 受限模板查询（对应 docs/VIBECODING_IMPLEMENTATION_GUIDE.md §6.2 功能 B）。

**职责**：把固定问题模板（如「昨天 opencode 花了多少钱」「本周哪个项目成本最高」）
用正则 + 固定词表做**模式匹配**（非 LLM、不联网、零第三方依赖），归一化为
{模板 ID, 参数}，再路由到既有只读派生函数（ai_sessions.collect / report.aggregate /
insights.behavior_insights / git_insights.git_insights / tool_compare / growth），
返回可 JSON 序列化的 {answer, data} 结构化结果。

**强调受限**（防注入 / 防 DoS）：
- 不以任意自由文本为输入——必须命中内置模板；未命中 → ok=False（端点 400）；
- 周期只允许固定词表（昨天/前天/本周/上月/最近 N 天…）+ YYYY-MM-DD 绝对日期，
  白名单正则校验；范围上限 max_days（默认 92）拦截超长区间；
- tool/project 仅用作对本地聚合结果键的模糊子串匹配，绝不拼 SQL / 拼路径。

**入口**：
- `run_query(q, data_root, config, today=None)`  —— /api/query?q=<自然语言模板>
- `run_template(tpl, query, data_root, config)`   —— /api/query?tpl=q1&start=&end=（指南 §6.2 兼容）

依赖：仅项目内 ai_sessions / report / insights / git_insights + Python 标准库。
tool_compare / growth 惰性加载（`_MODS` 缓存，单元测试可整体替换为 fake）。
"""

from __future__ import annotations

import datetime
import json
import re
import copy

import ai_sessions  # 只读复用（collect → 成本/tokens/产出/质量）
import git_insights  # 只读复用（git_insights → Git 产出）
import insights  # 只读复用（behavior_insights → focus_score）
import report  # 只读复用（aggregate → by_ai 前台分钟 / sessions）

# ---------------------------------------------------------------------------
# 默认配置（读 config.query；风格对齐 tool_compare.compare_config）
# ---------------------------------------------------------------------------
_DEFAULT_QUERY = {
    "enabled": True,   # 总开关：false 时返回友好空态（200）
    "max_days": 92,    # 模板/显式日期范围上限（对标 tool_compare.max_days 90，留余量）
    "top": 10,         # 排位类模板（q2）展示行数上限
    "flat_threshold": 0.03,  # 专注度趋势平坦判定阈值（<3% 视为平稳）
}

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# 周期词表（受限白名单）与「今天」注入点
# ---------------------------------------------------------------------------
_P_DAY = r"(?:今天|今日|昨天|昨日|前天)"
_P_WEEK = r"(?:本周|这周|上周)"
_P_MONTH = r"(?:本月|这个月|上月|上个月)"
_P_RECENT = r"最近\s*\d{1,4}\s*天"
_PERIOD_RE = f"(?P<period>{_P_DAY}|{_P_WEEK}|{_P_MONTH}|{_P_RECENT})"
_TOOL_RE = r"[^\s？?！!，,。.;；:：（）()【】]+"


def _today() -> datetime.date:
    """可注入的「今天」（测试可 monkeypatch / run_query 传 today）。"""
    return datetime.date.today()


def query_config(config: dict) -> dict:
    """从完整 config 提取 query 段并补齐默认值（老用户 config.json 无该段也能跑）。"""
    raw = (config or {}).get("query")
    sec = raw if isinstance(raw, dict) else {}
    out = dict(_DEFAULT_QUERY)
    out["enabled"] = bool(sec.get("enabled", _DEFAULT_QUERY["enabled"]))
    try:
        out["max_days"] = max(1, min(366, int(sec.get("max_days", _DEFAULT_QUERY["max_days"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["top"] = max(1, min(50, int(sec.get("top", _DEFAULT_QUERY["top"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["flat_threshold"] = max(0.0, float(sec.get("flat_threshold",
                                                       _DEFAULT_QUERY["flat_threshold"])))
    except (TypeError, ValueError):
        pass
    return out


def _dlist(start_d: datetime.date, end_d: datetime.date) -> list[str]:
    """闭区间日期列表（含首尾，升序）。"""
    return [(start_d + datetime.timedelta(days=i)).isoformat()
            for i in range((end_d - start_d).days + 1)]


def _resolve_period(text: str, today: datetime.date, cfg: dict) -> tuple[list[str], str]:
    """把周期词表/绝对日期解析为闭区间日期列表 + 人类可读标签。

    - 今天/本周/本月含当天（半天数据抖动可接受，best-effort 逐日跳过）；
    - 昨天/前天/上周/上月/最近 N 天为已完结周期（不含今天，避免半截数据，
      与 growth 快照「当前周只算到昨天」口径一致）；
    - 最近 N 天：end=昨天，N 钳制到 1..max_days；
    - 非法 → ValueError（端点映射为 400 invalid period）。
    """
    t = re.sub(r"\s+", "", text or "")
    max_days = max(1, cfg.get("max_days", 92))
    if t in ("今天", "今日"):
        return _dlist(today, today), "今天"
    if t in ("昨天", "昨日"):
        d = today - datetime.timedelta(days=1)
        return _dlist(d, d), "昨天"
    if t == "前天":
        d = today - datetime.timedelta(days=2)
        return _dlist(d, d), "前天"
    if t in ("本周", "这周"):
        monday = today - datetime.timedelta(days=today.weekday())
        return _dlist(monday, today), ("本周" if t == "本周" else "这周")
    if t == "上周":
        monday = today - datetime.timedelta(days=today.weekday() + 7)
        return _dlist(monday, monday + datetime.timedelta(days=6)), "上周"
    if t in ("本月", "这个月"):
        first = today.replace(day=1)
        return _dlist(first, today), ("本月" if t == "本月" else "这个月")
    if t in ("上月", "上个月"):
        last = today.replace(day=1) - datetime.timedelta(days=1)
        first = last.replace(day=1)
        return _dlist(first, last), ("上月" if t == "上月" else "上个月")
    m = re.fullmatch(r"最近(\d{1,4})天", t)
    if m:
        n = max(1, min(int(m.group(1)), max_days))
        end_d = today - datetime.timedelta(days=1)
        start_d = end_d - datetime.timedelta(days=n - 1)
        return _dlist(start_d, end_d), f"最近 {n} 天（截至昨天）"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        day = _parse_date(m.group(1))
        return _dlist(day, day), day.isoformat()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:到|至|~)(\d{4}-\d{2}-\d{2})", t)
    if m:
        d0, d1 = _parse_date(m.group(1)), _parse_date(m.group(2))
        if d1 < d0:
            raise ValueError(f"end before start: {t!r}")
        if (d1 - d0).days + 1 > max_days:
            raise ValueError(f"range too large: {t!r}")
        return _dlist(d0, d1), f"{d0.isoformat()} 至 {d1.isoformat()}"
    raise ValueError(f"unrecognized period {text!r}")


def _parse_date(text: str) -> datetime.date:
    """严格日期解析；非法 → ValueError（沿用 dashboard._DAY_RE 全匹配校验语义）。"""
    if not _DAY_RE.fullmatch(text):
        raise ValueError(f"bad date {text!r}")
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"bad date {text!r}") from exc


# ---------------------------------------------------------------------------
# 惰性模块装载（tool_compare / growth 按需 import；测试可整体替换）
# ---------------------------------------------------------------------------
_MODS: dict[str, object] = {}


def _mod(name: str) -> object:
    if name not in _MODS:
        _MODS[name] = __import__(name)
    return _MODS[name]


# ---------------------------------------------------------------------------
# 受限数据源访问（best-effort：单日失败仅跳过该日，不拖垮整次查询）
# ---------------------------------------------------------------------------
def _collect(day: str, config: dict) -> dict | None:
    try:
        return ai_sessions.collect(day, config)
    except Exception:  # noqa: BLE001
        return None


def _agg(day: str, data_root: str) -> dict | None:
    try:
        return report.aggregate(day, data_root)
    except Exception:  # noqa: BLE001
        return None


def _fmt_money(usd: float) -> str:
    return f"${float(usd or 0):,.2f}"


# ---------------------------------------------------------------------------
# 模板注册表（受限固定模板表；模式按 T1..T5 顺序匹配，首个命中生效）
# ---------------------------------------------------------------------------
_NOTICE = (
    "仅参考：token/成本为本地会话文件估算，非官方账单；minutes 为前台窗口级 AI 时长，"
    "口径与深度漏斗（tokens/cost/产出）不同。"
)
_NOTICE_TOP = _NOTICE + " 排位按成本降序，无真实采纳率归因。"
_NOTICE_FOCUS = "仅参考：专注度为离线规则打分，趋势为邻近样本对比，非精确因果。"
_NOTICE_OUTPUT = "仅参考：AI 生成行数为本地启发式估算；Git 行数来自本地仓库提交统计。"
_NOTICE_ACTIVITY = "仅参考：会话次数/时长来自前台窗口观测，不含后台进程。"

TEMPLATES: list[dict] = [
    {
        "id": "q1",
        "title": "AI 成本统计",
        "scope": "cost",
        "notice": _NOTICE,
        "examples": ["昨天 opencode 花了多少钱", "上周 AI 成本是多少", "本周一共花了多少钱"],
        "patterns": [
            # 无工具：全体 AI 成本（优先，防「AI」被误捕为工具名）
            re.compile(rf"^{_PERIOD_RE}\s*(?:的)?\s*(?:AI\s*)?"
                       r"(?:成本|费用|花费|花钱|花了?\s*多少钱)(?:是多少|多少)?$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai)\s*一共?\s*花了?\s*多少钱$"),
            # 带工具：某工具的 AI 成本
            re.compile(rf"^{_PERIOD_RE}\s*(?P<tool>{_TOOL_RE})\s*花了?\s*多少钱$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?P<tool>{_TOOL_RE})\s*的?\s*(?:AI\s*)?"
                       r"(?:成本|费用|花费)(?:是多少|多少)?$"),
        ],
    },
    {
        "id": "q2",
        "title": "成本排位",
        "scope": "top",
        "notice": _NOTICE_TOP,
        "examples": ["本周哪个项目成本最高", "上周哪个工具最贵", "上周成本最高的项目是"],
        "patterns": [
            # 哪个/什么 项目·工具·模型 + 成本类词 + 最高级（严格锚定，禁止尾部残渣）
            re.compile(rf"^{_PERIOD_RE}\s*(?:哪个|什么|哪些)\s*(?P<scope>项目|工具|模型)"
                       r"\s*(?:的)?\s*(?:成本|费用|开销|花费|花钱|费钱)\s*(?:最高|最多|最大|最贵)$"),
            # 哪个工具最贵（无成本词）
            re.compile(rf"^{_PERIOD_RE}\s*(?:哪个|什么|哪些)\s*(?P<scope>项目|工具|模型)\s*最贵$"),
            # 上周成本最高的项目是 / 成本最高的是项目
            re.compile(rf"^{_PERIOD_RE}\s*(?:成本|费用|开销|花费)\s*最高\s*的\s*(?:是\s*)?"
                       r"(?P<scope>项目|工具|模型)(?:是)?$"),
            # 成本最高的 3 个项目
            re.compile(rf"^{_PERIOD_RE}\s*成本\s*最高\s*的\s*(?P<n>\d{{1,2}})\s*个\s*"
                       r"(?P<scope>项目|工具|模型)$"),
            # 上周成本最高的是哪个项目
            re.compile(rf"^{_PERIOD_RE}\s*(?:成本|费用|开销|花费)\s*最高\s*的\s*是\s*"
                       r"哪个\s*(?P<scope>项目|工具|模型)$"),
        ],
    },
    {
        "id": "q3",
        "title": "专注度趋势",
        "scope": "focus",
        "notice": _NOTICE_FOCUS,
        "examples": ["上周专注度趋势", "最近 7 天专注度怎么样", "本周专注度变化"],
        "patterns": [
            re.compile(rf"^{_PERIOD_RE}\s*(?:的)?\s*(?:专注度|专注|focus)"
                       r"\s*(?:趋势|变化|走势|曲线|怎么样|如何|状况|情况)?$",
                       re.IGNORECASE),
        ],
    },
    {
        "id": "q4",
        "title": "AI 产出 vs Git 产出",
        "scope": "output",
        "notice": _NOTICE_OUTPUT,
        "examples": ["本周 AI 产出 vs Git 产出", "昨天 AI 写了多少行代码", "上周 AI vs Git"],
        "patterns": [
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai|人工智能)\s*(?:产出|生成|写了?)?"
                       r"\s*(?:vs|VS|对比|和|与)\s*(?:Git|git|代码提交|提交)"
                       r"\s*(?:产出|情况|对比)?$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai|人工智能)\s*(?:生成|写了?|产出)"
                       r"\s*多少\s*行\s*(?:代码)?$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai|人工智能)\s*(?:vs|对比|和|与)\s*(?:Git|git)$"),
        ],
    },
    {
        "id": "q5",
        "title": "AI 活跃概况",
        "scope": "activity",
        "notice": _NOTICE_ACTIVITY,
        "examples": ["昨天 AI 用了多久", "本周 AI 会话情况", "昨天 AI 活跃了多久"],
        "patterns": [
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai)\s*(?:用了\s*(?:多久|多长时间)|时长"
                       r"|活跃了?\s*(?:多久|多长时间)|活跃|会话|几次|多少次|多少个\s*会话"
                       r"|使用情况|使用概况)$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI|ai)\s*会话\s*(?:情况|统计|次数|数量|概况)$"),
            re.compile(rf"^{_PERIOD_RE}\s*AI\s*活跃\s*(?:情况|分钟|时间)?$"),
        ],
    },
    {
        "id": "q6",
        "title": "产出对比（两周期）",
        "scope": "compare",
        "notice": _NOTICE_OUTPUT,
        "examples": ["今日产出 vs 昨日", "今天和昨天产出对比", "本周产出比上周多吗"],
        "patterns": [
            # 今日产出 vs 昨日 / 本周 AI 产出 vs 上周
            re.compile(rf"^{_PERIOD_RE}\s*(?:的)?\s*(?:AI\s*)?(?:产出|生成|写了?)\s*"
                       rf"(?:vs|VS|和|与|对比)\s*(?P<cmp>{_P_DAY}|{_P_WEEK}|{_P_MONTH}|{_P_RECENT})$"),
            # 今天和昨天产出对比 / 本周与上周 AI 产出对比
            re.compile(rf"^{_PERIOD_RE}\s*(?:和|与)\s*(?P<cmp>{_P_DAY}|{_P_WEEK}|{_P_MONTH}|{_P_RECENT})"
                       r"\s*(?:的)?\s*(?:AI\s*)?产出\s*对比$"),
            # 本周产出比上周多吗 / 今日产出对比昨日
            re.compile(rf"^{_PERIOD_RE}\s*产出\s*(?:比|对比)\s*(?P<cmp>{_P_DAY}|{_P_WEEK}|{_P_MONTH}|{_P_RECENT})"
                       r"\s*(?:多|少|高|低|多吗|少吗)?$"),
        ],
    },
    {
        "id": "q7",
        "title": "专注度最佳日",
        "scope": "focus_best",
        "notice": _NOTICE_FOCUS,
        "examples": ["本周专注度最好的一天", "上周哪天专注度最高", "本月专注度最好的日子"],
        "patterns": [
            re.compile(rf"^{_PERIOD_RE}\s*(?:专注度|专注)\s*(?:最好|最高|最佳|最高分)"
                       r"\s*的\s*(?:一天|日子|日)$"),
            re.compile(rf"^{_PERIOD_RE}\s*(?:哪|哪天|哪一天|哪些天)\s*(?:的)?\s*专注度"
                       r"\s*(?:最好|最高|最佳)$"),
            re.compile(rf"^{_PERIOD_RE}\s*专注度\s*(?:最高|最好)\s*(?:的|的是)\s*哪天$"),
        ],
    },
    {
        "id": "q8",
        "title": "成本趋势",
        "scope": "cost_trend",
        "notice": _NOTICE,
        "examples": ["本周成本趋势", "最近 7 天 AI 成本变化", "本周费用走势"],
        "patterns": [
            re.compile(rf"^{_PERIOD_RE}\s*(?:AI\s*)?(?:成本|费用|开销)\s*"
                       r"(?:趋势|变化|走势|波动)(?:怎么样|如何)?$"),
            re.compile(rf"^{_PERIOD_RE}\s*AI\s*成本\s*是\s*怎么\s*变化的?$"),
        ],
    },
]

_ID_TO_TPL = {t["id"]: t for t in TEMPLATES}


def template_list() -> list[dict]:
    """对外可展示的模板元数据（前端「查询」页下拉用，纯只读）。"""
    return [{"id": t["id"], "title": t["title"], "notice": t["notice"],
             "examples": list(t["examples"])} for t in TEMPLATES]


# ---------------------------------------------------------------------------
# 各模板解析器（返回可 JSON 序列化的 data dict；均 best-effort 空态友好）
# ---------------------------------------------------------------------------
def _resolve_cost(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T1：AI 成本统计（复用 ai_sessions.collect + report.aggregate）。

    tool 缺省=全体；带 tool 时只统计该工具（对 collect().tools 键模糊子串匹配）。
    返回 {start, end, days, rows[], totals{}, by_tool[], notice}。
    """
    tool = (params.get("tool") or "").strip().lower() or None
    rows: list[dict] = []
    totals = {"cost": 0.0, "tokens": 0, "rounds": 0, "minutes": 0.0}
    by_tool: dict[str, dict] = {}
    for day in days:
        col, agg = _collect(day, config), _agg(day, data_root)
        by_ai = ((agg or {}).get("by_ai") or {}) if isinstance(agg, dict) else {}
        if tool:
            stats = None
            for key, val in ((col or {}).get("tools") or {}).items():
                if tool in str(key).lower():
                    stats = val
                    break
            row = {
                "cost": float((stats or {}).get("cost_total") or 0),
                "tokens": int((stats or {}).get("tokens_total") or 0),
                "rounds": int((stats or {}).get("rounds") or 0),
                "minutes": round(sum(ms for t, ms in by_ai.items()
                                     if tool in str(t).lower()) / 60000.0, 1),
            }
        else:
            total = (col or {}).get("total") or {}
            row = {
                "cost": float(total.get("cost_total") or 0),
                "tokens": int(total.get("tokens_total") or 0),
                "rounds": int(total.get("rounds") or 0),
                "minutes": round(sum(float(ms) for ms in by_ai.values()) / 60000.0, 1),
            }
            for key, val in ((col or {}).get("tools") or {}).items():
                e = by_tool.setdefault(str(key), {"cost": 0.0, "tokens": 0, "rounds": 0})
                e["cost"] += float(val.get("cost_total") or 0)
                e["tokens"] += int(val.get("tokens_total") or 0)
                e["rounds"] += int(val.get("rounds") or 0)
        rows.append({"date": day, **row})
        for k in ("cost", "tokens", "rounds", "minutes"):
            totals[k] += float(row.get(k) or 0)
    totals = {"cost": round(totals["cost"], 2), "tokens": int(totals["tokens"]),
              "rounds": int(totals["rounds"]), "minutes": round(totals["minutes"], 1)}
    ranked = sorted(
        ({"tool": k, "cost": round(v["cost"], 2), "tokens": v["tokens"],
          "rounds": v["rounds"]} for k, v in by_tool.items()),
        key=lambda r: r["cost"], reverse=True)
    if not (totals["cost"] or totals["tokens"] or totals["rounds"] or totals["minutes"]):
        rows, ranked = [], []  # 全零 → 空态（200 可展示，与「无数据返回空态」验收一致）
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "tool": tool, "rows": rows,
            "totals": totals, "by_tool": ranked, "notice": _NOTICE}


def _resolve_top(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T2：成本排位（复用 tool_compare / collect 的 by_project/by_model，按成本降序取 top）。

    scope: 工具→tool_compare.compare_tools（含 quality/性价比派生指标）；
           项目→collect().total.by_project 跨天求和；模型→by_model 跨天求和。
    返回 {start, end, days, scope, ranking[], top|null, top_n[], notice}。
    """
    scope = (params.get("scope") or "工具")
    n = max(1, min(int(params.get("n") or 1), 10))
    cfg = query_config(config)
    top_n = max(1, int(cfg.get("top", 10)))
    if scope == "工具":
        res = {}
        try:  # tool_compare 惰性复用；失败降级为空排位（不 500）
            mod = _mod("tool_compare")
            cfg_cmp = copy.deepcopy(config)
            cfg_cmp["tool_compare"] = {"enabled": True, "sort_by": "cost_total",
                                       "top": top_n, "min_sessions": 0}
            res = mod.compare_tools(days, data_root, cfg_cmp)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            res = {}
        ranking = [
            {"name": r.get("tool"), "cost": round(float(r.get("cost_total") or 0), 2),
             "tokens": int(r.get("tokens_total") or 0),
             "sessions": int(r.get("sessions") or 0),
             "quality_avg": r.get("quality_avg"),
             "cost_per_1k_tokens": r.get("cost_per_1k_tokens"),
             "chars_per_dollar": r.get("chars_per_dollar")}
            for r in (res.get("tools") or [])
        ]
        notice = res.get("notice") or _NOTICE_TOP
        source = "tool_compare"
    else:  # 项目 / 模型：collect().total 的维度聚合跨天求和
        merged: dict[str, dict] = {}
        for day in days:
            col = _collect(day, config)
            total = (col or {}).get("total") or {}
            bucket = (total.get("by_project") if scope == "项目"
                      else total.get("by_model")) or {}
            for key, e in bucket.items():
                key = str(key)
                if not key:
                    continue
                t = merged.setdefault(key, {"turns": 0, "tokens": 0, "cost": 0.0})
                t["turns"] += int(e.get("turns") or 0)
                t["tokens"] += int(e.get("tokens_total") or 0)
                t["cost"] += float(e.get("cost_total") or 0)
        ranking = [{"name": k, "cost": round(v["cost"], 2), "tokens": v["tokens"],
                    "turns": v["turns"]} for k, v in merged.items()]
        ranking.sort(key=lambda r: r["cost"], reverse=True)
        notice = _NOTICE_TOP
        source = f"collect.by_{'project' if scope == '项目' else 'model'}"
    ranking = ranking[:top_n]
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "scope": scope, "ranking": ranking,
            "top": ranking[0] if ranking else None,
            "top_n": ranking[:n] if ranking else [], "source": source, "notice": notice}


def _focus_per_day(days: list[str], data_root: str, config: dict) -> list[dict]:
    """逐日 focus_score（仅 total_active_ms>0 的天计分；q3 / q7 共用）。"""
    rows: list[dict] = []
    for day in days:
        agg = _agg(day, data_root)
        focus = 0
        if isinstance(agg, dict) and int(agg.get("total_active_ms") or 0) > 0:
            try:
                focus = int(insights.behavior_insights(agg, config).get("focus_score") or 0)
            except Exception:  # noqa: BLE001
                focus = 0
        rows.append({"date": day, "focus_score": focus})
    return rows


def _resolve_focus(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T3：专注度趋势（复用 insights.behavior_insights + growth.growth_snapshot 周快照）。

    逐日 focus_score（仅 total_active_ms>0 的天计平均）；趋势 = 首尾有数据样本的相对变化；
    weekly 为 growth 周均值快照中与区间重叠的周（best-effort，缺失忽略）。
    """
    cfg = query_config(config)
    flat = float(cfg.get("flat_threshold", 0.03))
    rows = _focus_per_day(days, data_root, config)
    vals = [r["focus_score"] for r in rows if int(r["focus_score"]) > 0]
    stats: dict = {"days_with_data": len(vals), "avg": 0, "min": 0, "max": 0,
                   "latest": rows[-1]["focus_score"] if rows else 0,
                   "trend": "flat", "slope": None}
    if vals:
        stats["avg"] = int(round(sum(vals) / len(vals)))
        stats["min"] = min(vals)
        stats["max"] = max(vals)
        first, last = vals[0], vals[-1]
        rel = (last - first) / max(abs(first), 1e-9)
        if rel >= flat:
            stats["trend"] = "up"
        elif rel <= -flat:
            stats["trend"] = "down"
        stats["slope"] = f"{rel * 100:+.1f}%"
    else:
        rows = []  # 无有效活跃日 → 空态
    # 周快照（growth.growth_snapshot 与区间重叠的周；惰性复用）
    weekly: list[dict] = []
    try:
        mod = _mod("growth")
        weeks = mod.growth_snapshot(data_root, config).get("weeks") or []  # type: ignore[attr-defined]
        d0 = datetime.date.fromisoformat(days[0])
        d1 = datetime.date.fromisoformat(days[-1])
        weekly = [w for w in weeks
                  if (wm := _week_monday(w.get("week"))) is not None
                  and wm <= d1 and wm + datetime.timedelta(days=6) >= d0]
    except Exception:  # noqa: BLE001 —— growth 不可用/无快照时 weekly 留空
        weekly = []
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "rows": rows, "stats": stats,
            "weekly": weekly, "notice": _NOTICE_FOCUS}


def _week_monday(week_key: str | None) -> datetime.date | None:
    """'YYYY-Www' → 该周周一（ISO 周历）；非法返回 None。"""
    if not isinstance(week_key, str):
        return None
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week_key)
    if not m:
        return None
    try:
        return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except (ValueError, TypeError):
        return None


def _resolve_output(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T4：AI 产出 vs Git 产出（复用 ai_sessions.collect + git_insights.git_insights）。"""
    rows: list[dict] = []
    totals = {"ai_lines": 0, "ai_chars": 0, "git_lines": 0, "git_commits": 0}
    git_days = 0
    for day in days:
        col = _collect(day, config)
        total = (col or {}).get("total") or {}
        ai_lines = int(total.get("generated_lines") or 0)
        ai_chars = int(total.get("generated_chars") or 0)
        git_lines = git_commits = found = 0
        try:
            g = git_insights.git_insights(config, day)
            gt = g.get("total") or {}
            found = 1 if g.get("found") else 0
            git_lines = int(gt.get("lines_added") or 0)
            git_commits = int(gt.get("commit_count") or 0)
        except Exception:  # noqa: BLE001 —— Git 未配置/分析失败视为 0
            found = 0
        git_days += found
        rows.append({"date": day, "ai_lines": ai_lines, "ai_chars": ai_chars,
                     "git_lines": git_lines, "git_commits": git_commits})
        totals["ai_lines"] += ai_lines
        totals["ai_chars"] += ai_chars
        totals["git_lines"] += git_lines
        totals["git_commits"] += git_commits
    if not (totals["ai_lines"] or totals["ai_chars"] or totals["git_lines"]):
        rows = []  # 全零 → 空态（answer 提示未找到）
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "rows": rows,
            "totals": totals, "git_configured": git_days > 0, "notice": _NOTICE_OUTPUT}


def _resolve_activity(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T5：AI 活跃概况（复用 report.aggregate 的 by_ai 分钟 + ai_tool 会话数）。"""
    rows: list[dict] = []
    totals = {"minutes": 0.0, "sessions": 0}
    tool_minutes: dict[str, float] = {}
    for day in days:
        agg = _agg(day, data_root)
        by_ai = ((agg or {}).get("by_ai") or {}) if isinstance(agg, dict) else {}
        minutes = sum(float(ms) for ms in by_ai.values()) / 60000.0
        sessions = 0
        for s in ((agg or {}).get("sessions") or []):
            if isinstance(s, dict) and s.get("ai_tool"):
                sessions += 1
        for t, ms in by_ai.items():
            tool_minutes[str(t)] = tool_minutes.get(str(t), 0.0) + float(ms) / 60000.0
        rows.append({"date": day, "minutes": round(minutes, 1), "sessions": sessions})
        totals["minutes"] += minutes
        totals["sessions"] += sessions
    if not (totals["minutes"] or totals["sessions"]):
        rows = []  # 全零 → 空态（answer 提示未找到）
    totals = {"minutes": round(totals["minutes"], 1), "sessions": int(totals["sessions"])}
    by_tool = sorted(({"tool": k, "minutes": round(v, 1)} for k, v in tool_minutes.items()),
                     key=lambda r: r["minutes"], reverse=True)
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "rows": rows, "totals": totals,
            "by_tool": by_tool, "notice": _NOTICE_ACTIVITY}


def _resolve_compare(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T6：产出对比（两周期）—— 复用 T4 的 AI/Git 产出统计，比较 base 与对比周期。

    cmp_days/cmp_label 由 run_query 解析周期后注入 params；对比周期缺失时按空态处理。
    返回 {start, end, days, compare_*, base, compare, delta_*, notice}。
    """
    cmp_days = params.get("cmp_days") or []
    base = _resolve_output(days, params, data_root, config)
    cmp = _resolve_output(cmp_days, params, data_root, config)
    bt, ct = base.get("totals") or {}, cmp.get("totals") or {}

    def _pick(src: dict) -> dict:
        return {"ai_lines": int(src.get("ai_lines") or 0),
                "ai_chars": int(src.get("ai_chars") or 0),
                "git_lines": int(src.get("git_lines") or 0),
                "git_commits": int(src.get("git_commits") or 0)}

    b, c = _pick(bt), _pick(ct)
    return {
        "start": days[0] if days else "", "end": days[-1] if days else "",
        "days": len(days),
        "compare_start": cmp_days[0] if cmp_days else "",
        "compare_end": cmp_days[-1] if cmp_days else "",
        "compare_days": len(cmp_days),
        "compare_label": params.get("cmp_label") or "对比周期",
        "base": b, "compare": c,
        "delta_ai_lines": b["ai_lines"] - c["ai_lines"],
        "delta_git_lines": b["git_lines"] - c["git_lines"],
        "notice": _NOTICE_OUTPUT,
    }


def _resolve_focus_best(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T7：专注度最佳日 —— 逐日 focus_score，取最高的一天（并列取最早）。

    仅 total_active_ms>0 的天计入；无数据 → best=None 空态（200 可展示，不 500）。
    """
    rows = _focus_per_day(days, data_root, config)
    with_data = [r for r in rows if int(r.get("focus_score") or 0) > 0]
    best = None
    if with_data:
        # 并列取日期最早：分数降序、日期升序
        best = max(with_data, key=lambda r: (int(r["focus_score"]), -int(r["date"].replace("-", ""))))
    return {
        "start": days[0] if days else "", "end": days[-1] if days else "",
        "days": len(days),
        "rows": with_data,
        "days_with_data": len(with_data),
        "best": best,
        "notice": _NOTICE_FOCUS,
    }


def _resolve_cost_trend(days: list[str], params: dict, data_root: str, config: dict) -> dict:
    """T8：成本趋势 —— 逐日成本/ tokens，趋势=首尾有成本样本的相对方向，含单日最高。

    全零 → rows=[] 空态（200 可展示，不 500）。
    """
    rows: list[dict] = []
    for day in days:
        col = _collect(day, config)
        total = (col or {}).get("total") or {}
        cost = float(total.get("cost_total") or 0)
        tokens = int(total.get("tokens_total") or 0)
        rows.append({"date": day, "cost": round(cost, 2), "tokens": tokens})
    costed = [r for r in rows if r["cost"] > 0 or r["tokens"] > 0]
    if not costed:
        rows = []  # 全零 → 空态
    totals = {"cost": round(sum(r["cost"] for r in rows), 2),
              "tokens": sum(r["tokens"] for r in rows)}
    trend = "flat"
    if len(costed) >= 2:
        first, last = costed[0]["cost"], costed[-1]["cost"]
        if last > first:
            trend = "up"
        elif last < first:
            trend = "down"
    top_day = max(costed, key=lambda r: (r["cost"], r["tokens"])) if costed else None
    return {"start": days[0] if days else "", "end": days[-1] if days else "",
            "days": len(days), "rows": rows, "totals": totals,
            "trend": trend, "top_day": top_day, "notice": _NOTICE}


_RESOLVERS = {
    "cost": _resolve_cost,
    "top": _resolve_top,
    "focus": _resolve_focus,
    "output": _resolve_output,
    "activity": _resolve_activity,
    "compare": _resolve_compare,
    "focus_best": _resolve_focus_best,
    "cost_trend": _resolve_cost_trend,
}


# ---------------------------------------------------------------------------
# answer 文案生成（非 LLM，纯模板拼接）
# ---------------------------------------------------------------------------
def _answer_cost(data: dict, params: dict, label: str) -> str:
    t = data.get("totals") or {}
    tool = (params.get("tool") or "").strip()
    if not (t.get("cost") or t.get("tokens") or t.get("minutes")):
        return f"{label}未找到{'「' + tool + '」' if tool else 'AI'}花费数据（周期内无 AI 会话记录）"
    who = f"「{tool}」" if tool else "AI 工具"
    parts = [f"{label}{who}共花费约 {_fmt_money(t.get('cost'))}"]
    if int(t.get("tokens") or 0):
        parts.append(f"tokens 约 {int(t['tokens']):,}")
    if float(t.get("minutes") or 0):
        parts.append(f"AI 前台活跃约 {float(t['minutes']):.0f} 分钟")
    return "，".join(parts)


def _answer_top(data: dict, params: dict, label: str) -> str:
    scope = data.get("scope") or "工具"
    top_n = data.get("top_n") or []
    if not top_n:
        return f"{label}未找到{scope}成本数据（周期内无 AI 会话记录）"
    if len(top_n) == 1:
        top = top_n[0]
        tok = f"，tokens 约 {int(top['tokens']):,}" if int(top.get("tokens") or 0) else ""
        return f"{label}成本最高的{scope}是「{top.get('name')}」：约 {_fmt_money(top.get('cost'))}{tok}"
    names = "、".join(f"「{r.get('name')}」" for r in top_n)
    return f"{label}成本最高的 {len(top_n)} 个{scope}依次为 {names}"


def _answer_focus(data: dict, params: dict, label: str) -> str:
    st = data.get("stats") or {}
    if not int(st.get("days_with_data") or 0):
        return f"{label}未找到专注度数据（周期内无有效活跃记录）"
    trend = {"up": "上升", "down": "下降", "flat": "平稳"}.get(st.get("trend"), "平稳")
    slope = f"（{st['slope']}）" if st.get("slope") else ""
    return (f"{label}专注度平均 {st.get('avg')} 分（最高 {st.get('max')}，"
            f"趋势{trend}{slope}）")


def _answer_output(data: dict, params: dict, label: str) -> str:
    t = data.get("totals") or {}
    ai = int(t.get("ai_lines") or 0)
    if not ai and not bool(data.get("git_configured")):
        return f"{label}未找到 AI/Git 产出数据（周期内无 AI 会话记录）"
    s = f"{label}AI 生成约 {ai:,} 行代码"
    if bool(data.get("git_configured")):
        s += f"，Git 新增约 {int(t.get('git_lines') or 0):,} 行（{int(t.get('git_commits') or 0)} 次提交）"
    else:
        s += "（Git 未配置/无提交，仅 AI 侧统计）"
    return s


def _answer_activity(data: dict, params: dict, label: str) -> str:
    t = data.get("totals") or {}
    minutes = float(t.get("minutes") or 0)
    sessions = int(t.get("sessions") or 0)
    if not minutes and not sessions:
        return f"{label}未找到 AI 活跃数据（周期内无 AI 前台会话）"
    tools = len(data.get("by_tool") or [])
    return f"{label}AI 活跃约 {minutes:.0f} 分钟、{sessions} 次会话（{tools} 个工具）"


def _answer_compare(data: dict, params: dict, label: str) -> str:
    b = data.get("base") or {}
    c = data.get("compare") or {}
    cmp_label = data.get("compare_label") or "对比周期"
    if not any(b.get(k) or 0 for k in ("ai_lines", "git_lines")) and \
            not any(c.get(k) or 0 for k in ("ai_lines", "git_lines")):
        return f"{label}与{cmp_label}均未找到产出数据"
    ai, cai = int(b.get("ai_lines") or 0), int(c.get("ai_lines") or 0)
    git, cgit = int(b.get("git_lines") or 0), int(c.get("git_lines") or 0)
    s = f"{label} AI 生成 {ai:,} 行（{cmp_label} {cai:,} 行，差 {ai - cai:+,}）"
    if git or cgit:
        s += f"；Git 新增 {git:,} 行（{cmp_label} {cgit:,} 行，差 {git - cgit:+,}）"
    else:
        s += "；Git 未配置/无提交"
    return s


def _answer_focus_best(data: dict, params: dict, label: str) -> str:
    best = data.get("best")
    if not best:
        return f"{label}未找到专注度数据（周期内无有效活跃记录）"
    return (f"{label}专注度最好的一天是 {best.get('date')}"
            f"（{best.get('focus_score')} 分，共 {data.get('days_with_data', 0)} 天有数据）")


def _answer_cost_trend(data: dict, params: dict, label: str) -> str:
    t = data.get("totals") or {}
    if not (t.get("cost") or t.get("tokens")):
        return f"{label}未找到 AI 成本数据（周期内无 AI 会话记录）"
    trend = {"up": "上升", "down": "下降", "flat": "平稳"}.get(data.get("trend"), "平稳")
    s = f"{label} AI 成本共约 {_fmt_money(t.get('cost'))}，趋势{trend}"
    top = data.get("top_day")
    if top:
        s += f"，单日最高约 {_fmt_money(top.get('cost'))}（{top.get('date')}）"
    return s


_ANSWERS = {
    "cost": _answer_cost,
    "top": _answer_top,
    "focus": _answer_focus,
    "output": _answer_output,
    "activity": _answer_activity,
    "compare": _answer_compare,
    "focus_best": _answer_focus_best,
    "cost_trend": _answer_cost_trend,
}


def _run_resolver(tpl: dict, days: list[str], params: dict,
                  data_root: str, config: dict) -> dict:
    """执行模板解析器并包上通用外壳（start/end/answer/tpl/notice）。"""
    data = _RESOLVERS[tpl["scope"]](days, params, data_root, config)
    label = params.get("period_label") or "所选周期"
    answer = _ANSWERS[tpl["scope"]](data, params, label)
    result = {
        "ok": True,
        "tpl": tpl["id"],
        "title": tpl["title"],
        "answer": answer,
        "data": data,
        "start": data.get("start") or (days[0] if days else ""),
        "end": data.get("end") or (days[-1] if days else ""),
        "days": len(days),
        "params": {k: v for k, v in params.items() if k != "period_label"},
    }
    return result


# ---------------------------------------------------------------------------
# 主入口：自然语言模板（接收 /api/query?q=...）
# ---------------------------------------------------------------------------
def run_query(q, data_root: str, config: dict,
              today: datetime.date | None = None) -> dict:
    """受限模板查询主入口。

    返回 {ok: True, tpl, title, answer, data, start, end, days, params}
    或 {ok: False, error}（空/未命中/非法周期 → 端点映射 400）。
    data 一律可 JSON 序列化；无数据时 ok=True 返回空态与「未找到…」文案。
    """
    cfg = query_config(config)
    if not cfg.get("enabled"):
        return {"ok": True, "tpl": None,
                "answer": "查询功能未启用（config.query.enabled=false）",
                "data": {}, "start": "", "end": "", "days": 0, "params": {}}
    if not isinstance(q, str):
        return {"ok": False, "error": "empty question"}
    qq = q.strip().strip("？！!?。. ").strip()
    if not qq:
        return {"ok": False, "error": "empty question"}
    today = today or _today()
    # 指纹批作用域：一次查询内多日收集只遍历一遍 AI 会话目录树。
    # （_collect_cached 逐日 stat 全树的成本在大目录下 ~2s/次，90 天趋势曾 ≈3 分钟）
    import ai_sessions  # noqa: PLC0415 —— 惰性导入，与其他模板解析器一致

    def _dispatch() -> dict:
        for tpl in TEMPLATES:
            for pattern in tpl["patterns"]:
                match = pattern.match(qq)
                if not match:
                    continue
                gd = match.groupdict()
                try:
                    days, label = _resolve_period(gd.get("period") or "", today, cfg)
                except ValueError as exc:
                    return {"ok": False, "error": f"invalid period: {exc}"}
                tool = (gd.get("tool") or "").strip() or None
                if tool and tool.lower() in ("ai", "all", "全部"):
                    tool = None  # 防「AI」被误捕为工具名（防御）
                params = {
                    "period": (gd.get("period") or "").strip(),
                    "period_label": label,
                    "tool": tool,
                    "project": None,
                    "scope": gd.get("scope") or "工具",
                    "n": int(gd.get("n") or 1) if gd.get("n") else 1,
                }
                # 双周期模板（q6 产出对比）：把对比周期一并解析进 params
                if gd.get("cmp"):
                    try:
                        cmp_days, cmp_label = _resolve_period(gd["cmp"], today, cfg)
                    except ValueError as exc:
                        return {"ok": False, "error": f"invalid period: {exc}"}
                    params["cmp_days"] = cmp_days
                    params["cmp_label"] = cmp_label
                return _run_resolver(tpl, days, params, data_root, config)
        return {"ok": False, "error": "unsupported question"}

    with ai_sessions.collect_fingerprint_batch():
        return _dispatch()


# ---------------------------------------------------------------------------
# 兼容入口：显式模板模式（接收 /api/query?tpl=q1&start=...&end=...，指南 §6.2）
# ---------------------------------------------------------------------------
def run_template(tpl_id: str, query: dict, data_root: str, config: dict,
                 today: datetime.date | None = None) -> dict:
    """按模板 ID 显式执行（query 为 parse_qs 风格的参数 dict，值是 list）。

    参数：start/end（YYYY-MM-DD，可省略→默认最近 7/14 天截至昨天）、
    tool/project（可选过滤）、n（可选 top 数，q2）、scope（可选，q2 默认 工具）。
    校验失败/未知模板 → {ok: False, error}（端点映射 400）。
    """
    cfg = query_config(config)
    if not cfg.get("enabled"):
        return {"ok": True, "tpl": tpl_id,
                "answer": "查询功能未启用（config.query.enabled=false）",
                "data": {}, "start": "", "end": "", "days": 0, "params": {}}
    tpl = _ID_TO_TPL.get(tpl_id)
    if tpl is None:
        return {"ok": False, "error": f"unknown template {tpl_id!r}"}
    today = today or _today()
    start_raw = (query.get("start") or [""])[0].strip() if query else ""
    end_raw = (query.get("end") or [""])[0].strip() if query else ""
    if start_raw or end_raw:
        if not (_DAY_RE.fullmatch(start_raw) and _DAY_RE.fullmatch(end_raw)):
            return {"ok": False, "error": "invalid date"}
        try:
            d0, d1 = datetime.date.fromisoformat(start_raw), datetime.date.fromisoformat(end_raw)
        except ValueError:
            return {"ok": False, "error": "invalid date"}
        if d1 < d0:
            return {"ok": False, "error": "invalid range"}
        if (d1 - d0).days + 1 > int(cfg.get("max_days", 92)):
            return {"ok": False, "error": "range too large"}
        days = _dlist(d0, d1)
        label = f"{d0.isoformat()} 至 {d1.isoformat()}"
    else:
        default_n = 14 if tpl["scope"] == "focus" else 7
        end_d = today - datetime.timedelta(days=1)
        start_d = end_d - datetime.timedelta(days=default_n - 1)
        days = _dlist(start_d, end_d)
        label = f"最近 {default_n} 天（截至昨天）"
    if not query:
        tool = project = None
        n, scope = 1, "工具"
    else:
        tool = (query.get("tool") or [None])[0] or None
        if tool and tool.lower() in ("ai", "all", "全部"):
            tool = None
        project = (query.get("project") or [None])[0] or None
        try:
            n = max(1, min(int((query.get("n") or ["1"])[0]), 10))
        except (TypeError, ValueError):
            n = 1
        scope = (query.get("scope") or [None])[0] or "工具"
        if scope not in ("项目", "工具", "模型"):
            scope = "工具"
    params = {"period": label, "period_label": label, "tool": tool,
              "project": project, "scope": scope, "n": n}
    return _run_resolver(tpl, days, params, data_root, config)


# ---------------------------------------------------------------------------
# 兼容层：dashboard.Handler 的 /api/query 参数校验补丁（幂等、失败安全）
# ---------------------------------------------------------------------------
# 背景：/api/query 打开前显式校验 q/tpl 与参数，端点内部已实现——此处不需补丁。
# 保留该命名空间供未来 wire 层复用（与 growth._install_strict_weeks_validation 对称）。


def main(argv: list[str] | None = None) -> int:
    """CLI 冒烟：python query.py "昨天 opencode 花了多少钱" [--config path] [--data-root root]"""
    import argparse

    import classifier  # noqa: PLC0415 —— 仅 CLI 用

    ap = argparse.ArgumentParser(description="受限模板查询（非 LLM）")
    ap.add_argument("q", help="问题模板，如「昨天 opencode 花了多少钱」")
    ap.add_argument("--config", default="", help="config.json 路径（默认走 classifier.load_config）")
    ap.add_argument("--data-root", default="", help="数据根目录（默认 config.data_root）")
    args = ap.parse_args(argv)
    config = classifier.load_config(args.config) if args.config else classifier.load_config()
    root = args.data_root or str(config.get("data_root") or "")
    result = run_query(args.q, root, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())