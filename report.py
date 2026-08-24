# -*- coding: utf-8 -*-
"""report.py — 日报/周报生成与 CLI 查询。

读取 <data_root>/YYYY-MM-DD/usage.jsonl（JSON Lines 会话记录），
聚合输出中文 Markdown 日报 / 汇总 CSV，支持 --day / --today / --week。

可被 monitor.py 直接 import（跨天时调用 generate_day_report 自动生成日报）。
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import shutil
import sys
import threading
import time
import types
import datetime

import version  # noqa: E402
import paths  # noqa: E402
from collections import OrderedDict

CATEGORY_ORDER = [
    "AI编程", "浏览器", "影音娱乐", "游戏", "社交聊天", "开发工具",
    "办公学习", "系统", "其他",
]

DEFAULT_DATA_ROOT = paths.default_data_root()

_aliases_cache: dict = {"ts": 0.0, "roots": {}}
# 并发安全（同本文件 _AGG_LOCK / ai_sessions 缓存范式）：TTL 判断、过期清空、
# roots 表查/写都在锁内；load_aliases 读盘在锁外。aggregate() 对本函数的调用点
# 在 _AGG_LOCK 临界区之外，两把锁不嵌套，无死锁面。
_ALIASES_LOCK = threading.Lock()

# aggregate() 结果 LRU 缓存：dashboard 14 天趋势 / 月报 31 天 / 周报都会重复全量
# 解析 usage.jsonl，缓存后同一天只解析一次。
# value = (mtime, size, data)；文件 mtime/size 变化即失效（append 写会更新 mtime）。
_AGG_CACHE_MAX = 16
_agg_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
# 并发安全（dashboard 为 ThreadingHTTPServer）：LRU 命中 move_to_end 与插入端
# popitem 驱逐在多线程下会互相踩踏（脏读 / OrderedDict mutated during iteration）。
# 锁内只做查表/改表；read_sessions + 聚合计算在锁外（同 ai_sessions 缓存范式）。
_AGG_LOCK = threading.Lock()


def _get_aliases(data_root: str) -> dict:
    """读取联系人别名表（按数据根目录缓存，5 秒 TTL 后刷新）。"""
    with _ALIASES_LOCK:
        now = time.monotonic()
        if now - _aliases_cache["ts"] > 5.0:
            _aliases_cache["roots"].clear()
            _aliases_cache["ts"] = now
        cached = _aliases_cache["roots"].get(data_root)
        if cached is not None:
            return cached
    aliases: dict = {}
    try:
        import classifier  # noqa: PLC0415
        aliases = classifier.load_aliases(os.path.join(data_root, "aliases.json"))  # 读盘在锁外
    except Exception:  # noqa: BLE001
        aliases = {}
    with _ALIASES_LOCK:
        _aliases_cache["roots"][data_root] = aliases
    return aliases


def _default_data_root() -> str:
    """从 config.json 取 data_root；不可用时回退默认路径。"""
    try:
        import classifier  # noqa: PLC0415 —— 惰性导入避免循环依赖
        return classifier.load_config().get("data_root") or DEFAULT_DATA_ROOT
    except Exception:  # noqa: BLE001
        return DEFAULT_DATA_ROOT


def _config_for_root(data_root: str, config_path: str | None = None) -> dict:
    """日报链统一配置解析：显式 config_path > <data_root>/config.json > 全局默认。

    与 dashboard._load_config_for_root 语义一致：显式传入的 config_path（monitor
    --config / dashboard server.config_path）是用户最强意图，排最前；其次随数据根
    走的 <root>/config.json（设置页保存目标）；都没有才回退全局默认 config.json。
    注意：dashboard 已单向 import report，report 这里不能反向 import dashboard
    复用 _load_config_for_root（会循环导入），故按同一语义自实现。
    """
    import classifier  # noqa: PLC0415 —— 惰性导入避免循环依赖
    if config_path and os.path.isfile(config_path):
        return classifier.load_config(config_path)
    local = os.path.join(data_root, "config.json")
    if os.path.isfile(local):
        return classifier.load_config(local)
    return classifier.load_config()


def read_sessions(date_str: str, data_root: str) -> list[dict]:
    """读取某天 usage.jsonl 的全部会话记录；坏行跳过并告警。"""
    path = os.path.join(data_root, date_str, "usage.jsonl")
    sessions: list[dict] = []
    if not os.path.isfile(path):
        return sessions
    with open(path, "r", encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    sessions.append(obj)
            except json.JSONDecodeError:
                print(f"[report] 跳过坏行 {date_str}:{lineno}", file=sys.stderr)
    return sessions


def _hourly_distribution(sessions: list[dict]) -> list[int]:
    """24 小时活跃毫秒分布：按会话 [start, end] 区间与每小时重叠精确分摊。

    会话跨小时/跨天时按重叠比例拆分到各小时（start/end 为 ISO 字符串）。
    """
    hourly = [0] * 24
    for s in sessions:
        dur = int(s.get("duration_ms") or 0)
        if dur <= 0:
            continue
        try:
            start = datetime.datetime.fromisoformat(s["start"])
            end = datetime.datetime.fromisoformat(s["end"])
        except (KeyError, ValueError):
            continue
        if end <= start:
            continue
        cur = start
        while cur < end:
            hour_start = cur.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + datetime.timedelta(hours=1)
            seg_end = min(end, hour_end)
            if seg_end > cur:
                hourly[hour_start.hour] += int((seg_end - cur).total_seconds() * 1000)
            cur = hour_end
    return hourly


def _aggregate_records(sessions: list[dict], date_str: str, data_root: str,
                       aliases: dict | None = None) -> dict:
    """按 应用/类别/联系人/AI工具/浏览器分类 聚合一组会话记录（毫秒）。

    供 JSONL 与 SQLite 两种数据源复用；调用方不得修改返回的 dict。
    """
    aliases = aliases if aliases is not None else _get_aliases(data_root)
    agg = {
        "date": date_str,
        "session_count": len(sessions),
        "total_active_ms": 0,
        "by_app": {},
        "by_category": {},
        "by_contact": {},
        "by_ai": {},
        "by_browser": {},
        "by_subcategory": {},
        "by_term_tool": {},
        "hourly_ms": _hourly_distribution(sessions),
        "sessions": sorted(sessions, key=lambda s: s.get("start", ""), reverse=True),
    }
    for s in sessions:
        dur = int(s.get("duration_ms") or 0)
        if dur <= 0:
            continue
        active = bool(s.get("active", True))
        if active:
            agg["total_active_ms"] += dur
        app = s.get("app") or s.get("exe") or "未知"
        cat = s.get("category") or "其他"
        agg["by_app"][app] = agg["by_app"].get(app, 0) + dur
        agg["by_category"][cat] = agg["by_category"].get(cat, 0) + dur
        contact = s.get("contact")
        if contact:
            contact = aliases.get(contact, contact)  # 别名映射
            agg["by_contact"].setdefault(app, {})
            agg["by_contact"][app][contact] = agg["by_contact"][app].get(contact, 0) + dur
        ai = s.get("ai_tool")
        if ai:
            agg["by_ai"][ai] = agg["by_ai"].get(ai, 0) + dur
        bc = s.get("browser_category")
        if bc:
            agg["by_browser"][bc] = agg["by_browser"].get(bc, 0) + dur
        sub = s.get("subcategory")
        if sub:
            key_sub = f"{cat}·{sub}" if cat != "浏览器" else sub
            agg["by_subcategory"][key_sub] = agg["by_subcategory"].get(key_sub, 0) + dur
        tool = s.get("term_tool")
        if tool:
            agg["by_term_tool"][tool] = agg["by_term_tool"].get(tool, 0) + dur
    return agg


def aggregate(date_str: str, data_root: str) -> dict:
    """按 应用/类别/联系人/AI工具/浏览器分类 聚合当天时长（毫秒）。

    联系人经 aliases.json 别名映射（如 aaa123 -> 张三）。
    结果带 LRU 缓存（文件 mtime/size 失效）；调用方不得修改返回的 dict。
    """
    path = os.path.join(data_root, date_str, "usage.jsonl")
    try:
        st = os.stat(path)
    except OSError:
        st = None
    key = (date_str, data_root)
    if st is not None:
        with _AGG_LOCK:
            hit = _agg_cache.get(key)
            if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
                _agg_cache.move_to_end(key)
                return hit[2]
    sessions = read_sessions(date_str, data_root)  # 读盘与聚合在锁外
    aliases = _get_aliases(data_root)
    agg = _aggregate_records(sessions, date_str, data_root, aliases)
    if st is not None:
        with _AGG_LOCK:
            _agg_cache[key] = (st.st_mtime, st.st_size, agg)
            _agg_cache.move_to_end(key)
            while len(_agg_cache) > _AGG_CACHE_MAX:
                _agg_cache.popitem(last=False)
    return agg


def _fmt_ms(ms: int) -> str:
    """毫秒 -> 中文时长文本。"""
    total_s = int(ms // 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} 小时")
    if m:
        parts.append(f"{m} 分钟")
    if s and not h and not m:
        parts.append(f"{s} 秒")
    if not parts:
        return "0 秒"
    return " ".join(parts)


def _fmt_sec(ms: int) -> int:
    """毫秒 -> 整秒数（CSV 用）。"""
    return int(ms // 1000)


def generate_report_csv(date_str: str, data_root: str) -> str:
    """生成汇总 CSV 文本（类型,名称,时长秒）。"""
    agg = aggregate(date_str, data_root)
    lines = ["类型,名称,时长秒"]
    for name, ms in sorted(agg["by_app"].items(), key=lambda kv: -kv[1]):
        lines.append(f"应用:{name},{_fmt_sec(ms)}")
    for cat, ms in sorted(agg["by_category"].items(), key=lambda kv: -kv[1]):
        lines.append(f"类别:{cat},{_fmt_sec(ms)}")
    for app, contacts in sorted(agg["by_contact"].items()):
        for contact, ms in sorted(contacts.items(), key=lambda kv: -kv[1]):
            lines.append(f"联系人:{app}/{contact},{_fmt_sec(ms)}")
    for tool, ms in sorted(agg["by_ai"].items(), key=lambda kv: -kv[1]):
        lines.append(f"AI工具:{tool},{_fmt_sec(ms)}")
    for label, ms in sorted(agg["by_browser"].items(), key=lambda kv: -kv[1]):
        lines.append(f"浏览器:{label},{_fmt_sec(ms)}")
    return "\n".join(lines) + "\n"


def _pct(part_ms: int, total_ms: int) -> str:
    if total_ms <= 0:
        return "-"
    return f"{part_ms / total_ms * 100:.1f}%"


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


def _build_session_sections(agg: dict) -> str:
    """日报正文（一~六节：按软件/类别/联系人/AI工具/浏览器分类/会话明细），不含标题。"""
    total = agg["total_active_ms"]
    out: list[str] = []

    def section(title: str, body: str) -> None:
        if body.strip():
            out.append(f"## {title}")
            out.append("")
            out.append(body)

    # 一、按软件
    if agg["by_app"]:
        rows = sorted(agg["by_app"].items(), key=lambda kv: -kv[1])[:15]
        table = _md_table(
            ["软件", "时长", "占比"],
            [[name, _fmt_ms(ms), _pct(ms, total)] for name, ms in rows],
        )
        section("一、按软件", table)
    # 二、按类别
    if agg["by_category"]:
        ordered = []
        rest = dict(agg["by_category"])
        for cat in CATEGORY_ORDER:
            if cat in rest:
                ordered.append((cat, rest.pop(cat)))
        ordered.extend(sorted(rest.items(), key=lambda kv: -kv[1]))
        table = _md_table(
            ["类别", "时长", "占比"],
            [[cat, _fmt_ms(ms), _pct(ms, total)] for cat, ms in ordered],
        )
        section("二、按类别", table)
    # 三、按联系人
    if agg["by_contact"]:
        rows = []
        for app, contacts in sorted(agg["by_contact"].items()):
            for contact, ms in sorted(contacts.items(), key=lambda kv: -kv[1]):
                rows.append([app, contact, _fmt_ms(ms)])
        section("三、按联系人", _md_table(["应用", "联系人", "时长"], rows))
    # 四、按AI工具
    if agg["by_ai"]:
        rows = sorted(agg["by_ai"].items(), key=lambda kv: -kv[1])
        section(
            "四、按AI工具（AI编程时长）",
            _md_table(["AI工具", "时长"], [[tool, _fmt_ms(ms)] for tool, ms in rows]),
        )
    # 五、浏览器分类
    if agg["by_browser"]:
        rows = [[label, _fmt_ms(agg["by_browser"].get(label, 0))] for label in ["视频", "代码", "学习", "其他"] if agg["by_browser"].get(label)]
        section("五、浏览器分类", _md_table(["分类", "时长"], rows))
    # 六、会话明细
    if agg["sessions"]:
        rows = []
        for s in agg["sessions"][:30]:
            note = []
            if s.get("ai_tool"):
                note.append(f"AI:{s['ai_tool']}")
            if s.get("term_tool"):
                note.append(f"终端:{s['term_tool']}")
            if s.get("contact"):
                note.append(f"联系人:{s['contact']}")
            if s.get("browser_category"):
                note.append(s["browser_category"])
            if s.get("subcategory"):
                note.append(f"子类:{s['subcategory']}")
            ws = s.get("window_state")
            if ws and ws != "normal":
                note.append({"fullscreen": "全屏", "maximized": "最大化"}.get(ws, ws))
            url = s.get("url")
            if url and url != "[已隐藏]":
                short = url.split("?", 1)[0]
                if len(short) > 60:
                    short = short[:60] + "…"
                note.append(f"[{short}]")
            elif url == "[已隐藏]":
                note.append("[URL已隐藏]")
            rows.append([
                s.get("start", ""), s.get("end", ""),
                int((s.get("duration_ms") or 0) // 1000),
                s.get("app") or s.get("exe") or "", s.get("title", ""),
                s.get("category", ""), " ".join(note),
            ])
        section("六、会话明细（Top 30）", _md_table(["开始", "结束", "秒数", "应用", "标题", "类别", "备注"], rows))
    # 七、按子分类
    if agg["by_subcategory"]:
        rows = sorted(agg["by_subcategory"].items(), key=lambda kv: -kv[1])
        section("七、按子分类", _md_table(["子分类", "时长"], [[k, _fmt_ms(v)] for k, v in rows]))
    # 八、按终端工具
    if agg["by_term_tool"]:
        rows = sorted(agg["by_term_tool"].items(), key=lambda kv: -kv[1])
        section("八、按终端工具", _md_table(["终端工具", "时长"], [[k, _fmt_ms(v)] for k, v in rows]))

    return "\n".join(out)


def generate_report_md(date_str: str, data_root: str) -> str:
    """生成中文 Markdown 日报文本（会话统计部分）。"""
    agg = aggregate(date_str, data_root)
    out: list[str] = []
    out.append(f"# 电脑使用情况日报 {date_str}")
    out.append("")
    out.append(f"总活跃时长：{_fmt_ms(agg['total_active_ms'])}（会话数：{agg['session_count']}）")
    out.append("")
    body = _build_session_sections(agg)
    if body.strip():
        out.append(body)
    else:
        out.append("（当日无数据）")
    return "\n".join(out)


def _browser_daily(date_str: str, data_root: str, max_rows: int | None = None,
                   config_path: str | None = None) -> tuple[dict | None, str | None]:
    """收集某天浏览器数据 + 生成明细章节；不可用返回 (None, None)。"""
    try:
        import browser_history  # noqa: PLC0415 —— browser_history.classifier 与顶层 classifier 同模块
        config = _config_for_root(data_root, config_path)
        data = browser_history.collect(date_str, data_root, config)
        if not data.get("enabled") or not data["visits"]:
            return None, None
        section = browser_history.report_section(date_str, data_root, config, data=data, max_rows=max_rows)
        return data, section
    except Exception:  # noqa: BLE001
        return None, None


def _ai_sessions_daily(date_str: str, data_root: str, max_rows: int | None = None,
                       config_path: str | None = None) -> str | None:
    """生成日报「AI 会话深度」章节（ROADMAP Phase 1 的可选展示）。

    需 config.json 里 ai_sessions.enabled=true；只读本地会话文件与浏览器访问
    明细，绝不联网。无任何数据时返回 None（日报主体不受影响）。
    """
    try:
        import ai_sessions  # noqa: PLC0415
        config = _config_for_root(data_root, config_path)
        ai_cfg = config.get("ai_sessions") or {}
        if not isinstance(ai_cfg, dict) or not ai_cfg.get("enabled"):
            return None
        web_visits = []
        try:
            import browser_history  # noqa: PLC0415
            web_visits = browser_history.collect(date_str, data_root, config).get("visits") or []
        except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地统计
            web_visits = []
        data = ai_sessions.collect(date_str, config, web_visits=web_visits or None)
        total = data.get("total") or {}
        web = data.get("web_ai") or {}
        if not data.get("found"):
            return None

        out: list[str] = ["## AI 会话深度", ""]
        out.append(f"- 本地会话：消息 {total.get('turns', 0)} 条 / 对话轮次 {total.get('rounds', 0)} 轮，"
                   f"Token 估算 进 {total.get('tokens_in', 0)} / 出 {total.get('tokens_out', 0)}，"
                   f"成本估算 {ai_sessions._fmt_cost(total.get('cost_total', 0))}")
        qs = total.get("quality_summary") or {}
        if qs.get("sessions_scored"):
            # v2.5 质量维度（派生估算，透明声明）
            q_avg = int(qs.get("avg") or 0)
            out.append(f"- 会话质量：已评 {qs['sessions_scored']} 个会话，均分 {q_avg} 分"
                       f"（{ai_sessions.quality_grade(q_avg)}）· 仅本地启发式估算，非采纳率")
            if qs.get("best"):
                out.append(f"  - 最佳：{str(qs['best'])[:40]} · {qs.get('best_score', 0)} 分")
            if qs.get("worst") and qs.get("worst") != qs.get("best"):
                out.append(f"  - 待关注：{str(qs['worst'])[:40]} · {qs.get('worst_score', 0)} 分")
        if data.get("tools"):
            sub = "；".join(
                f"{tool} {st.get('turns', 0)} 条/{st.get('rounds', 0)} 轮"
                for tool, st in data["tools"].items()
            )
            out.append(f"- 按工具：{sub}")
        models = sorted((total.get("by_model") or {}).items(), key=lambda kv: -kv[1]["turns"])[:6]
        if models:
            sub = "；".join(f"{m} {v['turns']} 条 · {ai_sessions._fmt_cost(v['cost_total'])}" for m, v in models)
            out.append(f"- 模型分布：{sub}")
        projects = sorted((total.get("by_project") or {}).items(), key=lambda kv: -kv[1]["turns"])[:6]
        if projects:
            sub = "；".join(f"{p} {v['turns']} 条 · {ai_sessions._fmt_cost(v['cost_total'])}" for p, v in projects)
            out.append(f"- 项目分布：{sub}")
        if web.get("found"):
            out.append(f"- Web AI 会话（浏览器历史深度解析）：{web['conversations']} 个会话 / "
                       f"{web['turns']} 次页面访问（≈轮次，尽力而为）")
        out.append("")

        convs = (total.get("conversations") or [])[: (max_rows or 10)]
        if convs:
            rows = [[
                c.get("tool", ""), str(c.get("id", ""))[:30], c.get("model", "-"),
                c.get("project", "-"), str(c.get("rounds", 0)), str(c.get("turns", 0)),
                str(c.get("tokens_out", 0)),
                str(c.get("quality_score", "-")) if isinstance(c.get("quality_score"), int) else "-",
                ai_sessions._fmt_cost(c.get("cost_total", 0)),
            ] for c in convs]
            out.append(_md_table(["工具", "会话", "模型", "项目", "轮次", "消息", "Token 出", "质量", "成本"], rows))
            out.append("")
        if web.get("found") and web.get("sessions"):
            rows = [[
                s.get("tool", ""), str(s.get("id", ""))[:30], str(s.get("title", "") or "-"),
                str(s.get("visits", 0)),
            ] for s in web["sessions"][: (max_rows or 10)]]
            out.append(_md_table(["Web 工具", "会话 ID", "标题", "访问次数"], rows))
            out.append("")
        out.append("注：Token 为长度折算的估算值；成本为按模型定价表（USD/百万 Token）的估算，"
                   "可用 config 的 ai_sessions.costs.model_pricing 自定义单价；对话轮次为消息序列中 "
                   "user→assistant 陪对数；质量分为消息长度/轮次/配比启发式估算（非真实采纳率）；"
                   "Web 会话访问次数为浏览器侧轮次的近似。所有解析均在本地完成，不上传任何数据。")
        return "\n".join(out)
    except Exception:  # noqa: BLE001
        return None


def _ai_cost_ledger_md(days: list[str], data_root: str, label: str = "周度",
                       config_path: str | None = None) -> str | None:
    """AI 成本与投入账本（ROADMAP Phase 3 · 周/月汇总支出报表）。

    遍历 days，逐日 collect AI 会话深度（本地+可选 Web），聚合出
    消息/轮次/Token/成本，并按 模型 / 项目 / 工具 汇总成本与轮次，
    生成一段 Markdown「AI 成本账本」。仅当 ai_sessions.enabled 且至少
    一天有数据时返回；只读本地、绝不联网。任何异常返回 None。
    config_path 可选：显式配置路径（generate_month_report_md / CLI --week
    透传 --config）；与日报链同源，统一走 _config_for_root 解析，优先级
    config_path > <root>/config.json > 全局默认（缺省 None 保持旧行为）。
    """
    try:
        import ai_sessions  # noqa: PLC0415 —— 配置解析统一走日报链同款助手
        # 与日报链同源：不再自取全局默认配置，按 _config_for_root 统一优先级解析
        config = _config_for_root(data_root, config_path)
        ai_cfg = config.get("ai_sessions") or {}
        if not isinstance(ai_cfg, dict) or not ai_cfg.get("enabled"):
            return None

        acc = {
            "days": [],
            "turns": 0, "rounds": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
            "by_model": {}, "by_project": {}, "by_tool": {},
        }
        for day in days:
            web = []
            try:
                import browser_history  # noqa: PLC0415
                web = browser_history.collect(day, data_root, config).get("visits") or []
            except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地 AI 统计
                web = []
            data = ai_sessions.collect(day, config, web_visits=web or None)
            total = data.get("total") or {}
            if not data.get("found"):
                acc["days"].append({"date": day, "found": False})
                continue
            acc["days"].append({
                "date": day, "found": True,
                "turns": int(total.get("turns") or 0),
                "rounds": int(total.get("rounds") or 0),
                "cost": float(total.get("cost_total") or 0.0),
            })
            acc["turns"] += int(total.get("turns") or 0)
            acc["rounds"] += int(total.get("rounds") or 0)
            acc["tokens_in"] += int(total.get("tokens_in") or 0)
            acc["tokens_out"] += int(total.get("tokens_out") or 0)
            acc["cost"] += float(total.get("cost_total") or 0.0)
            for key, bucket in (("by_model", acc["by_model"]),
                                ("by_project", acc["by_project"])):
                for name, meta in (total.get(key) or {}).items():
                    e = bucket.setdefault(name, {"turns": 0, "rounds": 0, "cost": 0.0})
                    e["turns"] += int(meta.get("turns") or 0)
                    e["rounds"] += int(meta.get("rounds") or 0)
                    e["cost"] += float(meta.get("cost_total") or 0.0)
            for name, meta in (data.get("tools") or {}).items():
                e = acc["by_tool"].setdefault(name, {"turns": 0, "rounds": 0, "cost": 0.0})
                e["turns"] += int(meta.get("turns") or 0)
                e["rounds"] += int(meta.get("rounds") or 0)
                e["cost"] += float(meta.get("cost_total") or 0.0)

        if not any(d.get("found") for d in acc["days"]):
            return None

        out: list[str] = [f"## AI 成本账本（{label}）", ""]
        out.append(f"- 会话消息 {acc['turns']} 条 / 对话轮次 {acc['rounds']} 轮，"
                   f"Token 估算 进 {acc['tokens_in']} / 出 {acc['tokens_out']}，"
                   f"成本估算 {ai_sessions._fmt_cost(acc['cost'])}")
        grouped = [("按模型", acc["by_model"]), ("按项目", acc["by_project"]), ("按工具", acc["by_tool"])]
        for title, bucket in grouped:
            if not bucket:
                continue
            rows = sorted(bucket.items(), key=lambda kv: -kv[1]["cost"])[:8]
            sub = "；".join(
                f"{name} {v['rounds']} 轮 · {ai_sessions._fmt_cost(v['cost'])}" for name, v in rows)
            out.append(f"- {title}：{sub}")
        cost_days = [d for d in acc["days"] if d.get("found")]
        rows = [[d["date"], str(d["turns"]), ai_sessions._fmt_cost(d["cost"])] for d in cost_days]
        out.append("")
        out.append("每日明细：")
        out.append("")
        out.append(_md_table(["日期", "消息", "成本"], rows))
        out.append("")
        out.append("注：Token 为长度折算估算；成本为按模型定价表（USD）估算，可用 "
                   "config 的 ai_sessions.costs.model_pricing 或 ai_pricing.json 自定义单价；"
                   "全部本地计算，不上传。")
        return "\n".join(out)
    except Exception:  # noqa: BLE001
        return None


def _inventory_summary(date_str: str, data_root: str) -> dict | None:
    """读取当日软件清单快照并汇总；无快照返回 None。"""
    path = os.path.join(data_root, date_str, "software_inventory.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            inv = json.load(fh)
        cats: dict[str, int] = {}
        for app in inv.get("apps", []):
            cats[app.get("category", "其他")] = cats.get(app.get("category", "其他"), 0) + 1
        running = sum(1 for app in inv.get("apps", []) if app.get("running"))
        return {
            "count": inv.get("count", len(inv.get("apps", []))),
            "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "running": running,
            "scanned_at": inv.get("scanned_at"),
        }
    except Exception:  # noqa: BLE001
        return None


def _insights_section(agg: dict, date_str: str, data_root: str,
                      config_path: str | None = None) -> str | None:
    """生成日报「今日建议」段（仅离线规则洞察，绝不发起网络请求）。

    insights.enabled && insights.in_report 且规则非空时返回 Markdown 列表，
    否则返回 None。任何异常都静默降级为无建议（日报主体不受影响）。
    """
    try:
        import insights  # noqa: PLC0415
        config = _config_for_root(data_root, config_path)
        ins = config.get("insights")
        if not (isinstance(ins, dict) and ins.get("enabled", True) and ins.get("in_report", True)):
            return None
        prev_day = (datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        prev_agg = aggregate(prev_day, data_root)
        rules = insights.rule_insights(agg, config, prev_agg)
        # v2.7「简单学习」：个性化基线异常（Welford/z-score）
        try:
            rules.extend(insights.baseline_insights(data_root, date_str, agg, config))
        except Exception:  # noqa: BLE001
            pass
        # 行为洞察（Phase 4 · 离线）：专注度评分 + 死循环检测
        behavior = insights.behavior_insights(agg, config)
        behavior_lines: list[str] = []
        if behavior.get("focus_score") or behavior.get("death_loop"):
            bd = behavior.get("breakdown") or {}
            grade = behavior.get("grade") or "—"
            comment = ("节奏好，建议保持" if grade == "高"
                       else "尚可，可减少高频小切换" if grade == "中"
                       else "易分心，建议用整块时间做深度任务")
            focus = int(behavior.get("focus_score") or 0)
            longest = _fmt_ms(int((bd.get("longest_session_s") or 0) * 1000))
            sw_ph = bd.get("switch_per_hour") or 0
            behavior_lines.append(
                f"- [专注] 今日专注度 {focus}/100（{grade}），最长专注 {longest}，"
                f"每小时切换 {sw_ph} 次；{comment}")
            dl = behavior.get("death_loop")
            if dl:
                apps = "、".join(dl.get("apps") or [])
                window = _fmt_ms(int((dl.get("window_s") or 0) * 1000))
                behavior_lines.append(
                    f"- [效率] ⚠ 疑似死循环切换：{dl['count']} 次短会话高频切换"
                    f"（{dl['distinct_apps']} 个应用：{apps}），约 {window} —— 建议合并任务、减少无效往返")
        # Vibe 编程人格（Phase 4 · 趣味）：纯离线规则
        persona = insights.persona_insights(agg, config)
        if persona.get("label"):
            ptraits = " · ".join(persona.get("traits") or [])
            ptraits = f"（{ptraits}）" if ptraits else ""
            emoji = persona.get("emoji", "🧭")
            label = persona.get("label")
            tagline = persona.get("tagline") or ""
            behavior_lines.append(
                f"- [人格] {emoji} 今日 Vibe 人格：{label}{ptraits} —— {tagline}")

        # Git 代码产出（ROADMAP Phase 2 · 只读本地提交分析）
        try:
            import git_insights  # noqa: PLC0415 —— 只读、本地、可失败
            git = git_insights.git_insights(config, date_str)
            if git.get("found") and git.get("total", {}).get("commit_count"):
                gt = git["total"]
                behavior_lines.append(
                    f"- [产出] 本地 Git 提交 {gt['commit_count']} 次，"
                    f"新增 +{gt['lines_added']} / 删除 -{gt['lines_deleted']} 行"
                    f"（变更 {gt['churn']} 行 · {gt['files']} 文件）")
                for repo in (git.get("repos") or [])[:2]:
                    behavior_lines.append(
                        f"  - {repo['name']}: {repo['commit_count']} 次提交，"
                        f"+{repo['lines_added']} / -{repo['lines_deleted']} 行")
        except Exception:  # noqa: BLE001 —— Git 分析缺失/失败不拖垮日报
            pass

        rule_lines = [f"- [{r['title']}] {r['detail']}" for r in rules]
        if not behavior_lines and not rule_lines:
            return None
        return "\n".join(behavior_lines + rule_lines)
    except Exception:  # noqa: BLE001
        return None


def generate_consolidated_md(date_str: str, data_root: str, full_urls: bool = False,
                             config_path: str | None = None) -> str:
    """生成每日汇总 MD：总览 + 会话统计 + 浏览器访问明细 + 软件清单概要。

    这就是"每天一个文件、统计全部数据"的日报主体（写入 report.md）。
    full_urls=True 时不截断浏览器 URL 明细（默认最多 100 条）。
    config_path 为可选显式配置路径（monitor --config 透传）；缺省时日报链内
    统一按 _config_for_root 的优先级解析（config_path > <root>/config.json > 全局默认）。
    """
    agg = aggregate(date_str, data_root)
    browser_data, browser_section = _browser_daily(date_str, data_root, max_rows=None if not full_urls else 10_000,
                                                   config_path=config_path)
    inv = _inventory_summary(date_str, data_root)

    out: list[str] = []
    out.append(f"# 电脑使用情况日报 {date_str}")
    out.append("")

    # 总览
    out.append("## 总览")
    out.append("")
    rows = [
        ["报告生成时间", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["活跃时长（monitor 前台口径，含空闲截断）", _fmt_ms(agg["total_active_ms"])],
        ["会话数", str(agg["session_count"])],
        ["软件清单应用数", f"{inv['count']}（运行中 {inv['running']}）" if inv else "（当日未扫描）"],
        ["浏览器访问条数", str(browser_data["count"]) if browser_data else "0"],
        ["浏览器停留总时长（标签页口径，含挂机）", _fmt_ms(int(browser_data["total_duration_s"] * 1000)) if browser_data else "-"],
    ]
    out.append(_md_table(["指标", "数值"], rows))
    out.append("")

    # 全天活跃分布（按小时，跨小时会话已精确分摊）
    hourly = agg["hourly_ms"]
    if any(hourly):
        out.append("## 全天活跃分布（按小时）")
        out.append("")
        hrows = []
        max_min = max(1.0, max(hourly) / 60000)
        for h in range(24):
            minutes = hourly[h] / 60000
            if minutes < 0.5:
                continue
            # 条形：相对当日最忙小时归一化，每格约 10% 宽度
            blocks = int(round(minutes / max_min * 10))
            hrows.append([f"{h:02d}:00", f"{minutes:.0f} 分钟", "█" * blocks])
        out.append(_md_table(["时段", "活跃", "相对强度"], hrows))
        out.append("")
        out.append("注：会话跨小时时按 [开始, 结束] 与各小时的重叠精确分摊。")
        out.append("")

    # 会话统计（一~六节）
    body = _build_session_sections(agg)
    if body.strip():
        out.append(body)
        out.append("")

    # AI 会话深度（可选：ai_sessions.enabled=true 且有数据）
    ai_section = _ai_sessions_daily(date_str, data_root, config_path=config_path)
    if ai_section:
        out.append(ai_section)
        out.append("")

    # 浏览器访问明细
    if browser_section:
        out.append(browser_section)
        out.append("")

    # 软件清单概要
    if inv:
        out.append("## 软件清单概要")
        out.append("")
        out.append(f"扫描时间：{inv['scanned_at']}，共 {inv['count']} 个应用（运行中 {inv['running']} 个）")
        out.append("")
        out.append(_md_table(["类别", "应用数"], [[cat, str(n)] for cat, n in inv["categories"].items()]))

    # 今日建议（离线规则洞察，仅当 insights.enabled && insights.in_report）
    insights_section = _insights_section(agg, date_str, data_root, config_path=config_path)
    if insights_section:
        out.append("## 📌 今日建议")
        out.append("")
        out.append(insights_section)
        out.append("")

    if len(out) <= 2:  # 完全没有数据
        out.append("（当日无数据）")
    return "\n".join(out)


def generate_day_report(date_str: str, data_root: str, full_urls: bool = False,
                        config_path: str | None = None) -> None:
    """把某天的 report.md / report.csv 写入对应日期文件夹。

    report.md 为每日汇总文件（总览 + 会话统计 + 浏览器明细 + 软件清单概要）。
    config_path 可选：显式配置路径（monitor finalize_day 透传 --config）；
    链内解析优先级见 _config_for_root。
    """
    day_dir = os.path.join(data_root, date_str)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(generate_consolidated_md(date_str, data_root, full_urls=full_urls,
                                          config_path=config_path))
    with open(os.path.join(day_dir, "report.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(generate_report_csv(date_str, data_root))


def _aggregate_days(date_strs: list[str], data_root: str) -> dict:
    """合并多天聚合结果（周报用）。"""
    merged = {
        "date": "~".join(date_strs) if date_strs else "",
        "session_count": 0,
        "total_active_ms": 0,
        "by_app": {}, "by_category": {}, "by_contact": {},
        "by_ai": {}, "by_browser": {}, "hourly_ms": [0] * 24, "sessions": [],
    }
    for ds in date_strs:
        agg = aggregate(ds, data_root)
        merged["session_count"] += agg["session_count"]
        merged["total_active_ms"] += agg["total_active_ms"]
        for i in range(24):
            merged["hourly_ms"][i] += agg["hourly_ms"][i]
        for k in ("by_app", "by_category", "by_ai", "by_browser"):
            for key, ms in agg[k].items():
                merged[k][key] = merged[k].get(key, 0) + ms
        for app, contacts in agg["by_contact"].items():
            merged["by_contact"].setdefault(app, {})
            for contact, ms in contacts.items():
                merged["by_contact"][app][contact] = merged["by_contact"][app].get(contact, 0) + ms
    return merged


def _ensure_sqlite_range(data_root: str, date_strs: list[str]) -> bool:
    """确保 usage.db 与 JSONL 在指定日期区间一致，否则回填缺失日期。

    返回 True 表示 SQLite 可直接用于该区间聚合；False 表示不可用（回退 JSONL）。
    """
    try:
        import sqlite_store  # noqa: PLC0415 —— 惰性导入
        if not os.path.isfile(sqlite_store.db_path(data_root)):
            return False
        conn = sqlite_store.connect(data_root)
        try:
            sqlite_store.init_db(conn)
            need: list[str] = []
            for d in date_strs:
                j = 0
                path = os.path.join(data_root, d, "usage.jsonl")
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8-sig") as fh:
                        j = sum(1 for line in fh if line.strip())
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE day = ?", (d,)
                ).fetchone()
                if j != int(row["n"] or 0):
                    need.append(d)
        finally:
            conn.close()
        if need:
            sqlite_store.backfill(data_root, sorted(need))
        return True
    except Exception:  # noqa: BLE001 —— 校验/回填失败则回退 JSONL
        return False


def aggregate_days(date_strs: list[str], data_root: str) -> dict:
    """合并多天聚合结果（周报/多日导出用）。

    优先使用 SQLite 快速路径：若 usage.db 存在且与 JSONL 一致，一次范围查询聚合；
    否则先回填缺失日期，仍不可用时回退逐日 JSONL 扫描（_aggregate_days）。
    """
    if not date_strs:
        return _aggregate_days([], data_root)
    try:
        import sqlite_store  # noqa: PLC0415 —— 惰性导入
        if _ensure_sqlite_range(data_root, date_strs):
            rows = sqlite_store.query_range(data_root, date_strs[0], date_strs[-1])
            if rows:
                return _aggregate_records(rows, "~".join(date_strs), data_root)
    except Exception:  # noqa: BLE001 —— SQLite 失败回退 JSONL
        pass
    return _aggregate_days(date_strs, data_root)


def _report_from_agg(agg: dict, title: str) -> str:
    """根据聚合结果渲染 Markdown（周报复用）。"""
    total = agg["total_active_ms"]
    out = [f"# {title}", "", f"总活跃时长：{_fmt_ms(total)}（会话数：{agg['session_count']}）", ""]
    if agg["by_app"]:
        rows = sorted(agg["by_app"].items(), key=lambda kv: -kv[1])[:15]
        out.append("## 按软件")
        out.append("")
        out.append(_md_table(["软件", "时长", "占比"], [[n, _fmt_ms(ms), _pct(ms, total)] for n, ms in rows]))
    if agg["by_category"]:
        rest = dict(agg["by_category"])
        ordered = [(c, rest.pop(c)) for c in CATEGORY_ORDER if c in rest]
        ordered.extend(sorted(rest.items(), key=lambda kv: -kv[1]))
        out.append("## 按类别")
        out.append("")
        out.append(_md_table(["类别", "时长", "占比"], [[c, _fmt_ms(ms), _pct(ms, total)] for c, ms in ordered]))
    if agg["by_ai"]:
        rows = sorted(agg["by_ai"].items(), key=lambda kv: -kv[1])
        out.append("## 按AI工具")
        out.append("")
        out.append(_md_table(["AI工具", "时长"], [[t, _fmt_ms(ms)] for t, ms in rows]))
    if len(out) <= 3:
        out.append("（期间无数据）")
    return "\n".join(out)


def _month_days(month_str: str) -> list[str]:
    """某月所有自然日（YYYY-MM-DD）。"""
    year, mon = map(int, month_str.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return [f"{month_str}-{d:02d}" for d in range(1, last + 1)]


def aggregate_month(month_str: str, data_root: str) -> dict:
    """月度聚合：合并当月所有日期数据，并附每日明细。

    若 data_root 下存在 usage.db（SQLite 后端），优先从 SQLite 读取当月数据，
    避免逐日全量扫描 JSONL；SQLite 不可用/数据缺失时回退 JSONL。
    """
    days = _month_days(month_str)
    # SQLite 快速路径（先确保当月数据已回填/与 JSONL 一致）
    try:
        import sqlite_store  # noqa: PLC0415 —— 惰性导入
        if _ensure_sqlite_range(data_root, days):
            rows = sqlite_store.query_range(data_root, days[0], days[-1])
            if rows:
                by_day: dict[str, list[dict]] = {}
                for r in rows:
                    by_day.setdefault(str(r.get("day") or ""), []).append(r)
                agg = _aggregate_records(rows, month_str, data_root)
                agg["month"] = month_str
                per_day = []
                for d in days:
                    day_rows = by_day.get(d, [])
                    if day_rows:
                        a = _aggregate_records(day_rows, d, data_root)
                        per_day.append({"date": d, "total_ms": a["total_active_ms"],
                                        "count": a["session_count"]})
                agg["per_day"] = per_day
                return agg
    except Exception:  # noqa: BLE001 —— SQLite 失败回退 JSONL
        pass

    days = [
        d for d in days
        if os.path.isfile(os.path.join(data_root, d, "usage.jsonl"))
    ]
    agg = _aggregate_days(days, data_root)
    agg["month"] = month_str
    per_day = []
    for d in days:
        a = aggregate(d, data_root)
        if a["session_count"] > 0:
            per_day.append({"date": d, "total_ms": a["total_active_ms"], "count": a["session_count"]})
    agg["per_day"] = per_day
    return agg


def generate_month_report_md(month_str: str, data_root: str,
                             config_path: str | None = None) -> str:
    """生成中文 Markdown 月报文本。

    config_path 可选：显式配置路径（CLI --config / 调用方透传）；与日报链同源，
    链内配置（月度 AI 成本账本等）统一按 _config_for_root 的优先级解析：
    config_path > <root>/config.json > 全局默认（缺省 None 保持旧行为）。
    """
    agg = aggregate_month(month_str, data_root)
    out = [
        f"# 电脑使用情况月报 {month_str}", "",
        f"总活跃时长：{_fmt_ms(agg['total_active_ms'])}（活跃天数：{len(agg['per_day'])}，会话数：{agg['session_count']}）",
        "",
    ]
    if agg["per_day"]:
        rows = [[p["date"], _fmt_ms(p["total_ms"]), p["count"]] for p in agg["per_day"]]
        out.append("## 每日活跃")
        out.append("")
        out.append(_md_table(["日期", "活跃时长", "会话数"], rows))
    if agg["by_app"]:
        rows = sorted(agg["by_app"].items(), key=lambda kv: -kv[1])[:15]
        out.append("## 按软件（Top 15）")
        out.append("")
        out.append(_md_table(["软件", "时长"], [[name, _fmt_ms(ms)] for name, ms in rows]))
    if agg["by_category"]:
        rest = dict(agg["by_category"])
        ordered = [(c, rest.pop(c)) for c in CATEGORY_ORDER if c in rest]
        ordered.extend(sorted(rest.items(), key=lambda kv: -kv[1]))
        out.append("## 按类别")
        out.append("")
        out.append(_md_table(["类别", "时长"], [[cat, _fmt_ms(ms)] for cat, ms in ordered]))
    if agg["by_ai"]:
        rows = sorted(agg["by_ai"].items(), key=lambda kv: -kv[1])
        out.append("## 按AI工具")
        out.append("")
        out.append(_md_table(["AI工具", "时长"], [[tool, _fmt_ms(ms)] for tool, ms in rows]))
    if agg["by_contact"]:
        rows = []
        for app, contacts in sorted(agg["by_contact"].items()):
            for contact, ms in sorted(contacts.items(), key=lambda kv: -kv[1]):
                rows.append([app, contact, _fmt_ms(ms)])
        out.append("## 按联系人")
        out.append("")
        out.append(_md_table(["应用", "联系人", "时长"], rows))
    # ROADMAP Phase 3：月度 AI 成本账本（周/月汇总支出报表）
    # 与日报链同源：config_path 一路透传给成本账本（解析优先级见 _config_for_root）
    ledger = _ai_cost_ledger_md(_month_days(month_str), data_root,
                                f"月度 · {month_str}", config_path=config_path)
    if ledger:
        out.append("")
        out.append(ledger)
    # v2.6 P3：月度成本预算小结（默认关闭；超支/接近才提示，失败静默降级）
    try:
        import budget  # noqa: PLC0415 —— 预算块与日报链同源：统一走 _config_for_root，
        # 不再自解析（原写法会遮蔽同名参数、且吃不到显式 config_path）
        config = _config_for_root(data_root, config_path)
        bmd = budget.budget_summary_md(
            budget.budget_status(month_str, data_root, config, period="monthly"))
        if bmd:
            out.append("")
            out.append(bmd)
    except Exception:  # noqa: BLE001 —— 预算小结失败不影响月报主体
        pass
    if len(out) <= 3:
        out.append("（当月无数据）")
    return "\n".join(out)


def reclassify_day(date_str: str, data_root: str) -> int:
    """用当前配置重新归类某天 usage.jsonl（规则变更后修复历史数据）。

    保留 start/end/duration/exe/app/active/title 等原始字段，仅重算
    category / contact / browser_category / ai_tool（进程树不可回溯，
    以存储的 exe 作为前台进程自身 + 标题识别）。返回变更条数。
    """
    import classifier as _clf  # noqa: PLC0415

    path = os.path.join(data_root, date_str, "usage.jsonl")
    if not os.path.isfile(path):
        return 0
    cfg = _clf.load_config()
    new_lines: list[str] = []
    changed = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue
            if not isinstance(rec, dict):
                new_lines.append(line)
                continue
            title = rec.get("title") or ""
            exe = rec.get("exe") or ""
            if title == "[已隐藏]":  # 隐私掩蔽的标题不参与重分类
                new_lines.append(json.dumps(rec, ensure_ascii=False))
                continue

            own = types.SimpleNamespace(exe=exe, ppid=0, pid=0)
            new_ai = _clf.detect_ai_tool(0, {0: own}, title, cfg)
            new_cat = _clf.classify_category(exe, title, cfg)
            # 与 monitor._open_session 口径一致：AI 工具命中时类别提升为 AI编程
            if new_ai is not None and new_cat != "AI编程":
                new_cat = "AI编程"
            new_bc = None
            if exe in cfg.get("browser_exes", []):
                new_bc = _clf.classify_browser(title, cfg)
            new_contact = rec.get("contact")
            if exe in cfg.get("social_apps", {}):
                new_contact = _clf.extract_contact(exe, title, cfg)

            # 维度细化字段（window_state 依赖窗口句柄，不可重算，保留原值）
            new_sub = None
            if new_cat == "浏览器":
                new_sub = new_bc
            if new_sub is None:
                new_sub = _clf.classify_subcategory(new_cat, exe, title, cfg)
            new_term = None
            if new_ai is None and (exe in cfg.get("terminal_exes", [])
                                   or exe in cfg.get("editor_exes", [])):
                new_term = _clf.detect_term_tool(title, cfg)

            if (rec.get("ai_tool") != new_ai or rec.get("category") != new_cat
                    or rec.get("contact") != new_contact
                    or rec.get("browser_category") != new_bc
                    or rec.get("subcategory") != new_sub
                    or rec.get("term_tool") != new_term):
                rec["ai_tool"] = new_ai
                rec["category"] = new_cat
                rec["contact"] = new_contact
                if new_bc:
                    rec["browser_category"] = new_bc
                else:
                    rec.pop("browser_category", None)
                if new_sub:
                    rec["subcategory"] = new_sub
                else:
                    rec.pop("subcategory", None)
                if new_term:
                    rec["term_tool"] = new_term
                else:
                    rec.pop("term_tool", None)
                changed += 1
            new_lines.append(json.dumps(rec, ensure_ascii=False))

    if changed:
        shutil.copy2(path, path + ".bak")  # 写回前备份
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(new_lines) + "\n")
        os.replace(tmp_path, path)
    return changed


def verify_days(data_root: str, days: list[str] | None = None, repair: bool = False) -> dict:
    """校验（可选修复）日期目录的 usage.jsonl 完整性。

    扫描每个 YYYY-MM-DD 目录的 usage.jsonl：逐行解析 JSON，
    统计好行/坏行；repair=True 时剔除坏行（原文件备份为 usage.jsonl.bak_verify）
    并重建缺失的 report.md/csv。返回汇总。
    """
    if days is None:
        days = sorted(
            d for d in os.listdir(data_root)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and os.path.isdir(os.path.join(data_root, d))
        )
    result = {"days": 0, "bad_lines": 0, "repaired": 0, "rebuilt_reports": 0, "issues": []}
    for day in days:
        path = os.path.join(data_root, day, "usage.jsonl")
        if not os.path.isfile(path):
            continue
        result["days"] += 1
        good: list[str] = []
        bad = 0
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        good.append(line)
                        continue
                except json.JSONDecodeError:
                    pass
                bad += 1
        result["bad_lines"] += bad
        if bad:
            result["issues"].append(f"{day}: {bad} 行损坏")
            if repair:
                shutil.copy2(path, path + ".bak_verify")
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("\n".join(good) + ("\n" if good else ""))
                result["repaired"] += bad
        # 重建缺失的日报
        report_path = os.path.join(data_root, day, "report.md")
        if repair and not os.path.isfile(report_path):
            try:
                generate_day_report(day, data_root)
                result["rebuilt_reports"] += 1
            except Exception as exc:  # noqa: BLE001
                result["issues"].append(f"{day}: 重建日报失败 {exc}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="report.py", description="电脑使用情况日报/周报/月报")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期日报")
    parser.add_argument("--today", action="store_true", help="今天日报")
    parser.add_argument("--week", action="store_true", help="最近 7 天周报")
    parser.add_argument("--month", metavar="YYYY-MM", help="月度汇总（默认当月）")
    parser.add_argument("--full", action="store_true", help="日报浏览器 URL 明细不截断（默认前 100 条）")
    parser.add_argument("--reclassify", action="store_true", help="用当前配置重新归类指定日期 usage.jsonl（规则变更后修复历史数据）")
    parser.add_argument("--verify", action="store_true", help="校验所有日期目录 usage.jsonl 完整性")
    parser.add_argument("--repair", action="store_true", help="与 --verify 连用：剔除坏行并重建缺失日报（坏行前自动备份）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（数据导出）")
    parser.add_argument("--write", action="store_true", help="同时把 report.md/csv 写入日期文件夹")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    args = parser.parse_args(argv)

    data_root = args.data_root or _default_data_root()

    # 统一日志（只在实际执行操作时记录，纯查询不刷屏）
    try:
        import applog  # noqa: PLC0415
        applog.configure(data_root)
        _applog = applog.get_logger("report")
    except Exception:  # noqa: BLE001
        _applog = None

    def _log_info(msg: str) -> None:
        if _applog is not None:
            try:
                _applog.info(msg)
            except Exception:  # noqa: BLE001
                pass

    if args.month:
        month_str = args.month
        try:
            datetime.datetime.strptime(month_str, "%Y-%m")
        except ValueError:
            print(f"[report] 月份格式错误: {month_str}（应为 YYYY-MM）", file=sys.stderr)
            return 2
        agg = aggregate_month(month_str, data_root)
        if args.write:
            _log_info(f"生成月报 {month_str}")
        if args.json:
            print(json.dumps(agg, ensure_ascii=False, indent=2, default=str))
        else:
            # 与日报链同源：CLI 手上有 --config 就显式透传（解析优先级见 _config_for_root）
            print(generate_month_report_md(month_str, data_root, config_path=args.config))
        if args.write:
            day_dir = os.path.join(data_root, month_str)
            os.makedirs(day_dir, exist_ok=True)
            with open(os.path.join(day_dir, "report_month.md"), "w", encoding="utf-8") as fh:
                fh.write(generate_month_report_md(month_str, data_root, config_path=args.config))
            with open(os.path.join(day_dir, "report_month.json"), "w", encoding="utf-8") as fh:
                json.dump(agg, fh, ensure_ascii=False, indent=2, default=str)
        return 0

    if args.week:
        today = datetime.date.today()
        days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        if args.write:
            _log_info(f"生成周报 {days[0]}~{days[-1]}")
            for ds in days:
                try:
                    # 周报链上的逐日日报生成：同样把 --config 显式透传（与日报链同源）
                    generate_day_report(ds, data_root, config_path=args.config)
                except Exception as exc:  # noqa: BLE001
                    print(f"[report] 生成 {ds} 失败: {exc}", file=sys.stderr)
        agg = aggregate_days(days, data_root)
        # ROADMAP Phase 3：周度 AI 成本账本
        # 与日报链同源：CLI 手上有 --config 就显式透传（解析优先级见 _config_for_root）
        week_ledger = _ai_cost_ledger_md(days, data_root, "周度 · 最近7天",
                                         config_path=args.config)
        week_md = _report_from_agg(agg, "电脑使用情况周报（最近 7 天）")
        if week_ledger:
            week_md = week_md + chr(10) + chr(10) + week_ledger
        # v2.6 P3：逐日预算小结（默认关闭；失败静默降级）
        try:
            # 预算块与日报链同源：--config 显式透传（统一走 _config_for_root，不再自解析）
            import budget  # noqa: PLC0415
            config = _config_for_root(data_root, args.config)
            bmd = budget.budget_week_summary(days, data_root, config)
            if bmd:
                week_md = week_md + chr(10) + chr(10) + bmd
        except Exception:  # noqa: BLE001 —— 预算小结失败不影响周报主体
            pass
        if args.json:
            print(json.dumps(agg, ensure_ascii=False, indent=2, default=str))
        else:
            print(week_md)
        if args.write:
            out_dir = os.path.join(data_root, today.isoformat())
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "report_week.md"), "w", encoding="utf-8") as fh:
                fh.write(week_md)
        return 0

    if args.today:
        date_str = datetime.date.today().isoformat()
    elif args.day:
        try:
            datetime.datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"[report] 日期格式错误: {args.day}（应为 YYYY-MM-DD）", file=sys.stderr)
            return 2
        date_str = args.day
    else:
        parser.print_help()
        return 2

    if args.verify:
        result = verify_days(data_root, repair=args.repair)
        print(f"校验完成：{result['days']} 天，坏行 {result['bad_lines']}，"
              f"修复 {result['repaired']}，重建日报 {result['rebuilt_reports']}")
        for issue in result["issues"]:
            print(f"  - {issue}")
        return 0 if result["bad_lines"] == 0 else 1

    if args.reclassify:
        try:
            n = reclassify_day(date_str, data_root)
        except Exception as exc:  # noqa: BLE001
            print(f"[report] 重分类失败: {exc}", file=sys.stderr)
            return 1
        _log_info(f"重分类 {date_str}：{n} 条变更")
        print(f"已按当前配置重分类 {date_str}：{n} 条记录变更（原文件备份为 usage.jsonl.bak）")
        return 0

    if args.write:
        try:
            generate_day_report(date_str, data_root, full_urls=args.full)
            _log_info(f"生成日报 {date_str}" + ("（全量URL）" if args.full else ""))
        except Exception as exc:  # noqa: BLE001
            print(f"[report] 生成 {date_str} 失败: {exc}", file=sys.stderr)
            return 1

    agg = aggregate(date_str, data_root)
    if args.json:
        print(json.dumps(agg, ensure_ascii=False, indent=2, default=str))
        if args.write:
            with open(os.path.join(data_root, date_str, "report.json"), "w", encoding="utf-8") as fh:
                json.dump(agg, fh, ensure_ascii=False, indent=2, default=str)
        return 0
    # 控制台输出与 report.md 文件保持一致（每日汇总 MD）
    print(generate_consolidated_md(date_str, data_root, full_urls=args.full))
    return 0


if __name__ == "__main__":
    # Windows 控制台 GBK 下中文输出保护
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
