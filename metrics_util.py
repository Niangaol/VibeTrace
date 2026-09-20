# -*- coding: utf-8 -*-
"""metrics_util.py — 统计辅助函数的单一来源（零第三方运行时依赖）。

历史上香农熵、HHI、维度合并、美元格式化等小工具在 insights / growth /
tool_compare / budget / ai_sessions 各抄了一份，改一处漏一处（缓存三分项
tokens_input_fresh 等就曾在两份 _merge_dim 里漏同步）。本模块把这些
「纯函数」收编为唯一实现，各业务模块经此 import 或留薄包装转发。

铁律：本模块不得 import 任何业务模块（只依赖标准库），避免循环依赖。
"""

from __future__ import annotations

import math

# 会话质量分档的中文名（与评分阈值 quality_grade 的分档一一对应）
GRADE_NAMES = ("优", "良", "中", "待优化")

# _merge_dim 里需要累加的整数型 token 字段（缓存三分项对旧结构 dict 用 .get 容错）
_MERGE_INT_KEYS = (
    "turns", "tokens_in", "tokens_out", "tokens_total",
    "tokens_input_fresh", "tokens_cache_read", "tokens_cache_write",
)
_MERGE_FLOAT_KEYS = ("cost_in", "cost_out", "cost_total")


def _new_bucket() -> dict:
    """新建一个全 0 统计桶（字段集由 _MERGE_INT_KEYS / _MERGE_FLOAT_KEYS 决定）。

    历史上桶字段在这里硬编码一份，与下方两个 KEYS 元组重复定义——曾因
    漏同步缓存三分项而出过事故。现收敛为同一处生成，加字段只改 KEYS。
    """
    bucket: dict = {ck: 0 for ck in _MERGE_INT_KEYS}
    bucket.update({fk: 0.0 for fk in _MERGE_FLOAT_KEYS})
    return bucket


def merge_dim(target: dict, src: dict) -> None:
    """把同维度的统计 dict（by_model / by_project）逐字段累加进 target。

    通俗讲：src 里每个键（如某个模型名）的计数，都加到 target 同名键上；
    target 没有的键就新建一个全 0 的桶再加。字段缺失时按 0 处理
    （旧数据没有缓存三分项等新字段也不能报错）。
    """
    for key, e in (src or {}).items():
        t = target.setdefault(key, _new_bucket())
        for ck in _MERGE_INT_KEYS:
            t.setdefault(ck, 0)
            t[ck] += int(e.get(ck) or 0)
        for fk in _MERGE_FLOAT_KEYS:
            t.setdefault(fk, 0.0)  # 调用方自建的桶可能不含全部 float 键（对齐 docstring 承诺）
            t[fk] += float(e.get(fk) or 0)


def shannon_entropy(counts: list[float]) -> float:
    """计算 Shannon 熵（单位 bit，保留 3 位小数），衡量「多样性」。

    counts 是各类别的次数（如各模型的会话数）：全集中在一类时熵为 0，
    越均匀分布熵越大。空列表或总数为 0 时返回 0.0。
    """
    total = sum(counts)
    if total <= 0:
        return 0.0
    ent = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * math.log2(p)
    return round(ent, 3)


def hhi(shares: list[float]) -> float:
    """赫芬达尔-赫希曼指数（HHI）：各份额平方和，衡量「集中度」。

    shares 是各项目/工具的占比列表；全部集中在一项时 HHI=1，越分散越接近 0。
    注意：本函数不四舍五入，由调用方按各自口径决定保留几位
    （insights/tool_compare 逐次保留 4 位，growth 先累加原始值最后取均值再舍入）。
    """
    return sum(s * s for s in shares)


def fmt_usd(value) -> str:
    """把美元金额格式化成展示字符串；解析失败按 0 处理。

    规则：0 显示 $0；不足 1 分显示 4 位小数；不足 1 美元显示 3 位；其余 2 位。
    """
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


def tool_switch_series(records: list[dict]) -> list[str]:
    """把会话记录按开始时间排序后折成「工具名序列」。

    每条记录的工具名取 ai_tool / term_tool / app 中第一个非空值，都没有记「未知」。
    只负责折序列，不负责计数（配合 count_switches 使用）。
    """
    ordered = sorted(records, key=lambda r: r.get("start") or "")
    return [r.get("ai_tool") or r.get("term_tool") or r.get("app") or "未知"
            for r in ordered]


def count_switches(series: list[str]) -> int:
    """统计序列里相邻元素变化的次数（工具切换频率的分子）。"""
    return sum(1 for a, b in zip(series, series[1:]) if a != b)
