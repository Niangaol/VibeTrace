# -*- coding: utf-8 -*-
"""derived.py - 派生指标的共用取数框架（v2.9.8）。

背景：growth / tool_compare / query / budget / insights / advice 六个模块各自
手写逐日 report.aggregate(day) + ai_sessions.collect(day)，同样循环抄了
13 处、229 行。每加一个派生指标就要在 N 个地方同步，改一处漏一处
（与 metrics_util 收编熵/HHI 之前完全同型）。

本模块把按天取齐多源数据收敛为 day_bundle()/series() 两个入口，
配合 cached_endpoint() 做端点级参数校验与降级。

铁律（改动前务必读）：
1. 每源每天恰调用一次——test_growth.py 用 env["counts"] 精确断言调用次数。
2. 必须用运行时属性访问 report.aggregate(...)，不要 from report import aggregate：
   100+ 处测试 monkeypatch 打的是模块属性，属性访问才能被替换生效。
3. best-effort 语义分级：AGG/AI 两源失败即该日作废（与原逐日循环一致），
   GIT/WEB 两源失败仅记 note 不作废。
4. 绝不在 day_bundle 内再包 collect_fingerprint_batch()——那会与调用方
   已有的批作用域嵌套，改变指纹批的计时语义。

依赖方向：derived 到 report/ai_sessions/insights/git_insights/browser_history/
applog/metrics_util，全部为惰性导入，无模块级业务 import（避免循环依赖）。
"""

from __future__ import annotations

import datetime
import threading

import applog

# need 位掩码：调用方按需取源，未请求的源一律不 import 不调用（零额外开销）
NEED_AGG = 1 << 0   # report.aggregate 单日 usage 聚合
NEED_AI = 1 << 1    # ai_sessions.collect 单日 AI 会话深度统计
NEED_GIT = 1 << 2  # git_insights.git_insights 当日 git 统计
NEED_WEB = 1 << 3  # browser_history.collect 当日浏览器访问明细

_MODULE_CACHE = {}
_MODULE_LOCK = threading.Lock()


def _mod(name):
    """惰性取业务模块（属性访问路径，保证测试 monkeypatch 生效）。"""
    m = _MODULE_CACHE.get(name)
    if m is None:
        with _MODULE_LOCK:
            m = _MODULE_CACHE.get(name)
            if m is None:
                m = __import__(name)
                _MODULE_CACHE[name] = m
    return m


def _days_back(date_str, n):
    """含当日在内的前 n 天日期串（倒序：当日在前）。非法日期返回 []。"""
    try:
        base = datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return []
    return [(base - datetime.timedelta(days=i)).isoformat() for i in range(n)]

def day_bundle(day, data_root, config=None, need=None, web_visits=None):
    """取某一天的多源数据；AGG/AI 源失败即整日作废返回 None。

    need 未请求的源不会出现在返回 dict 里（也不会 import 对应模块），
    因此调用方必须用 .get() 访问，不要用 ["agg"] 直接下标。
    """
    if need is None:
        need = NEED_AGG | NEED_AI
    out = {"date": day}

    if need & NEED_AGG:
        try:
            report = _mod("report")
            out["agg"] = report.aggregate(day, data_root)
        except Exception as exc:  # noqa: BLE001
            applog.note(exc, "derived: day_bundle 聚合失败 " + str(day))
            return None

    if need & NEED_AI:
        try:
            ai_sessions = _mod("ai_sessions")
            if web_visits is None:
                # 不传 web_visits 关键字：兼容只收 (day, config) 的轻量实现
                # （各处测试 fake 与部分调用方均为此形态）。
                out["ai"] = ai_sessions.collect(day, config or {})
            else:
                out["ai"] = ai_sessions.collect(day, config or {}, web_visits=web_visits)
        except Exception as exc:  # noqa: BLE001
            applog.note(exc, "derived: day_bundle AI 统计失败 " + str(day))
            return None

    if need & NEED_GIT:
        try:
            git_insights = _mod("git_insights")
            out["git"] = git_insights.git_insights(config or {}, day)
        except Exception as exc:  # noqa: BLE001
            applog.note(exc, "derived: day_bundle git 统计失败 " + str(day))
            out["git"] = None

    if need & NEED_WEB:
        try:
            browser_history = _mod("browser_history")
            out["web"] = browser_history.collect(day, data_root, config or {})
        except Exception as exc:  # noqa: BLE001
            applog.note(exc, "derived: day_bundle 浏览器统计失败 " + str(day))
            out["web"] = None

    return out


def series(date_str=None, data_root=None, n=7, config=None, need=None,
          include_today=True, web_visits_factory=None, days=None):
    """取连续 n 天的日捆列表（跳过作废日，顺序为近到远）。

    days 显式传入时优先使用（growth 周日期有空洞、query/budget 的区间
    也未必是「回溯 n 天」形状）；此时 date_str/n/include_today 全部忽略。
    include_today=False 时从第二天开始（advice._past_costs 的语义）。
    web_visits_factory(day) 仅 NEED_WEB 时使用，把浏览器明细喂给
    ai_sessions.collect（budget 的既有用法）。
    """
    if need is None:
        need = NEED_AGG | NEED_AI
    if days is not None:
        # 显式日期列表：原样使用，不做连续性与排序假设
        days = list(days)
    else:
        days = _days_back(date_str, n)
        if not include_today and days:
            days = days[1:]
    out = []
    for day in days:
        wv = None
        if web_visits_factory is not None:
            try:
                wv = web_visits_factory(day)
            except Exception as exc:  # noqa: BLE001
                applog.note(exc, "derived: series 浏览器取数失败 " + str(day))
                wv = None
        b = day_bundle(day, data_root, config, need=need, web_visits=wv)
        if b is not None:
            out.append(b)
    return out

def cached_endpoint(max_days=90, need=None, empty=None, arg_name="days"):
    """端点装饰器：校验 days 参数 -> 调 series() -> 失败降级为空态。

    用于按天数取序列的 /api/* 端点，替代各处手写的
    try: n = int(qs) except: n = 14 再加上下限钳制。

    empty：降级时返回的结构（调用方给契约空态）；为 None 时返回 []。
    被装饰函数签名须为 f(date_str, data_root, bundles, config=None, **kw)。
    """
    import functools

    if need is None:
        need = NEED_AGG | NEED_AI

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(date_str, data_root, days, config=None, **kw):
            try:
                n = int(days)
            except (TypeError, ValueError):
                n = 14
            if n < 1:
                n = 14
            if n > max_days:
                n = max_days
            try:
                bundles = series(date_str, data_root, n, config, need=need)
                return fn(date_str, data_root, bundles, config, **kw)
            except Exception as exc:  # noqa: BLE001
                applog.note(exc, "derived: cached_endpoint 降级 " + fn.__name__)
                return empty if empty is not None else []
        return wrapper
    return deco


if __name__ == "__main__":
    b = day_bundle("2026-09-19", "D:/_vt_smoke")
    print("day_bundle ->", None if b is None else sorted(b.keys()))
    s = series("2026-09-19", "D:/_vt_smoke", 3)
    print("series len ->", len(s))