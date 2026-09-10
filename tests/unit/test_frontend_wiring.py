# -*- coding: utf-8 -*-
"""tests/unit/test_frontend_wiring.py — 前端与后端端点接线一致性（防功能做完没入口）。"""

from __future__ import annotations

import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_TEMPLATE = os.path.join(_PROJECT_ROOT, "assets", "dashboard.html")
_DASHBOARD = os.path.join(_PROJECT_ROOT, "dashboard.py")


def _template() -> str:
    with open(_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.read()


def _dashboard_src() -> str:
    with open(_DASHBOARD, "r", encoding="utf-8") as fh:
        return fh.read()


def test_all_nav_views_have_section_and_loader():
    """每个 nav 项都要有对应 section 与 loader，避免点击空白页。"""
    html = _template()
    views = set(re.findall(r'data-view="([a-z-]+)"', html))
    assert views, "未解析到 nav 项"
    loaders_block = re.search(r"const loaders = \{(.*?)\};", html, re.S)
    assert loaders_block, "未找到 loaders 注册表"
    loaders = set(re.findall(r"(\w+)\s*:", loaders_block.group(1)))
    for v in sorted(views):
        assert f'id="view-{v}"' in html, f"nav 项 {v} 缺少 <section id=view-{v}>"
        assert v in loaders, f"nav 项 {v} 未在 loaders 注册"


def test_titles_cover_all_views():
    """TITLES 必须覆盖所有 nav 项（否则页面标题为 undefined）。"""
    html = _template()
    views = set(re.findall(r'data-view="([a-z-]+)"', html))
    titles_block = re.search(r"const TITLES = \{(.*?)\};", html, re.S)
    assert titles_block
    titles = set(re.findall(r"(\w+)\s*:", titles_block.group(1)))
    missing = views - titles
    assert not missing, f"TITLES 缺少：{missing}"


def test_key_endpoints_are_called_by_frontend():
    """核心分析端点必须被前端调用，防止后端做完前端没接。"""
    html = _template()
    required = [
        "/api/timeline",   # P2
        "/api/budget",     # P3
        "/api/ai-compare", # P4
        "/api/trend",      # P5
        "/api/query",      # P7
        "/api/insights",
        "/api/ai-sessions",
    ]
    for ep in required:
        assert ep in html, f"前端未调用端点 {ep}"


def test_frontend_endpoints_exist_in_backend():
    """前端调用的 /api/* 都必须在 dashboard.py 有路由，避免 404。"""
    html = _template()
    src = _dashboard_src()
    called = set(re.findall(r'api\("(/api/[a-z0-9/_-]+)', html))
    assert called, "未解析到前端 api() 调用"
    for ep in sorted(called):
        assert f'"{ep}"' in src, f"前端调用了 {ep}，但后端无该路由"


def test_compare_view_has_table_columns():
    """对比视图必须有表体容器与关键列，保证渲染目标存在。"""
    html = _template()
    assert 'id="cmpBody"' in html
    assert 'id="cmpMeta"' in html
    for col in ["工具", "会话", "成本", "质量均分"]:
        assert col in html, f"对比表缺少列 {col}"


def test_query_panel_present():
    """快速提问面板输入框与按钮存在。"""
    html = _template()
    assert 'id="qInput"' in html
    assert 'id="qGo"' in html
    assert 'id="qAnswer"' in html


def test_loadoverview_only_awaits_day_endpoint():
    """概览除首个 /api/day 外不得串行 await，其余面板并发加载（heatmap 保持非阻塞）。"""
    html = _template()
    m = re.search(r"async function loadOverview\(\)\{(.*?)\n\}", html, re.S)
    assert m, "未找到 loadOverview 函数"
    body = m.group(1)
    # 卡片渲染依赖单日聚合，必须先 await /api/day
    assert 'await api("/api/day?date="' in body, "loadOverview 必须先 await /api/day"
    # 其余面板各自渲染各自 DOM，无顺序依赖，一律非阻塞
    for ep in ["/api/urls", "/api/ai-sessions", "/api/budget", "/api/goals", "/api/days"]:
        assert f'await api("{ep}' not in body, f"loadOverview 仍在串行 await {ep}"
        assert f'api("{ep}' in body, f"loadOverview 丢失 {ep} 调用"
    # heatmap 刻意不 await 的既定写法必须原样保留
    assert 'api("/api/heatmap?days=28&tokens=1").then(' in body, "heatmap 非阻塞调用被改动"


def test_compare_iso_uses_local_date():
    """B2：对比页日期必须走本地时区 localDateStr，禁止 toISOString（UTC 会让东八区零点倒退一天）。"""
    html = _template()
    m = re.search(r"async function loadCompare\(\)\{(.*?)\n\}", html, re.S)
    assert m, "未找到 loadCompare 函数"
    body = m.group(1)
    assert "toISOString" not in body, "loadCompare 仍使用 UTC 取日期，东八区会偏移一天"
    assert "localDateStr" in body, "loadCompare 未复用 localDateStr 生成本地日期"


def test_compare_default_range_and_loading_feedback():
    """对比页默认 14 天（首次请求慢，不让用户开页就等大区间），且有耗时预期文案与失败提示。"""
    html = _template()
    assert "let cmpDays = 14;" in html, "对比页默认区间应为 14 天"
    assert '<button class="btn primary" data-cmp="14">' in html, "默认高亮的区间按钮应为 14 天"
    m = re.search(r"async function loadCompare\(\)\{(.*?)\n\}", html, re.S)
    assert m, "未找到 loadCompare 函数"
    body = m.group(1)
    assert "对比计算中" in body, "对比请求期间缺少耗时预期加载文案"
    assert "对比加载失败" in body, "对比请求失败缺少错误提示（裸 await 会卡在加载中）"


def test_loadoverview_callbacks_guard_against_stale_day():
    """换日后旧一轮慢响应必须作废：非阻塞回调带日期守卫，防旧日期数据覆盖新面板。"""
    html = _template()
    m = re.search(r"async function loadOverview\(\)\{(.*?)\n\}", html, re.S)
    assert m, "未找到 loadOverview 函数"
    body = m.group(1)
    assert "const day = state.day;" in body, "loadOverview 未在开头捕获本轮日期"
    # urls / heatmap / ai-sessions / budget / goals / days 六个非阻塞请求，至少各一处守卫
    assert body.count("state.day !== day") >= 6, \
        "非阻塞回调缺少日期守卫（state.day !== day 应至少出现 6 次）"
