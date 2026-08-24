# -*- coding: utf-8 -*-
"""tests/frontend/test_startapp_integrity.py — startApp() 接线完整性静态校验。

静态解析 assets/dashboard.html 中 startApp() 的函数体，提取其中被调用的标识符，
逐一确认它们在 <script> 内有 function/const 定义（防漏接入口/误删初始化）。
已处理防误报：async function、箭头函数、方法调用（.foo()）、new 构造、
JS 关键字、浏览器内建全局。纯标准库 re + 读文件，不起浏览器，确定性。
"""

from __future__ import annotations

import os
import re

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEMPLATE = os.path.join(_PROJECT_ROOT, "assets", "dashboard.html")


def _template() -> str:
    with open(_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.read()


_JS_KEYWORDS = {
    "async", "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "export", "extends", "finally",
    "for", "function", "if", "import", "in", "instanceof", "let", "new", "of",
    "return", "static", "super", "switch", "this", "throw", "try", "typeof",
    "var", "void", "while", "with", "yield",
}

# 浏览器/语言内建全局：脚本内不定义、无需校验
_BROWSER_GLOBALS = {
    "Array", "BigInt", "Boolean", "CustomEvent", "Date", "Error", "Event",
    "Infinity", "Intl", "IntersectionObserver", "JSON", "Map", "Math", "MutationObserver",
    "NaN", "Number", "Object", "Promise", "RegExp", "ResizeObserver", "Set",
    "String", "Symbol", "URL", "URLSearchParams", "clearInterval", "clearTimeout",
    "confirm", "console", "decodeURIComponent", "document", "encodeURIComponent",
    "fetch", "history", "isNaN", "location", "localStorage", "matchMedia",
    "navigator", "parseFloat", "parseInt", "performance", "queueMicrotask",
    "requestAnimationFrame", "setInterval", "setTimeout", "structuredClone", "window",
}


def _startapp_body(html: str) -> str:
    """按花括号配平提取 function startApp(){...} 的函数体（含嵌套箭头函数）。"""
    m = re.search(r"function startApp\(\)\s*\{", html)
    assert m, "未找到 function startApp()"
    i = html.index("{", m.start())
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[i + 1 : j]
    raise AssertionError("startApp() 花括号未闭合")


def _called_identifiers(body: str) -> set[str]:
    """提取函数体中被调用的全局标识符（过滤方法调用 / new 构造 / 关键字 / 内建）。"""
    out: set[str] = set()
    for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", body):
        name = m.group(1)
        pre = body[max(0, m.start() - 24) : m.start()]
        if re.search(r"(\.|\?\.)\s*$", pre):
            continue          # 方法调用：$("#x").click() / Promise.all(...)
        if re.search(r"\bnew\s+$", pre):
            continue          # 构造器：new URLSearchParams(...)
        if name in _JS_KEYWORDS or name in _BROWSER_GLOBALS:
            continue
        out.add(name)
    return out


def _has_definition(html: str, name: str) -> bool:
    """<script> 内是否存在 function/async function/const/let/var 定义。"""
    esc = re.escape(name)
    if re.search(rf"(?:async\s+)?function\s+{esc}\s*\(", html):
        return True
    # const/let/var NAME = （NAME 可能是 $ / $$ 这类非单词字符，尾部用 =/,/空白判定）
    if re.search(rf"\b(?:const|let|var)\s+{esc}(\s*=|,|\s)", html):
        return True
    return False


def test_startapp_called_identifiers_are_defined():
    """startApp() 体内每个被调用标识符都必须在 <script> 有 function/const 定义。"""
    html = _template()
    body = _startapp_body(html)
    called = _called_identifiers(body)
    assert called, "未解析到 startApp() 体内的调用"
    missing = {n for n in called if not _has_definition(html, n)}
    assert not missing, f"startApp() 调用了未定义的标识符：{sorted(missing)}"


def test_startapp_has_expected_wiring_calls():
    """startApp() 必须接线核心初始化，防止精修/重构误删入口。"""
    body = _startapp_body(_template())
    required = [
        "buildHeadControls", "applyTheme", "bindRipple", "wireExportButtons",
        "wireInsights", "wireAiSettings", "wirePricing", "wireAiModule",
        "wireUpdate", "monthInit", "bindGrowthControls", "bindCompareControls",
        "bindQueryControls", "groupsInit", "armLogTimer", "switchView",
    ]
    for name in required:
        assert re.search(rf"\b{name}\s*\(", body), f"startApp() 缺少 {name}() 接线"


def test_startapp_is_invoked():
    """startApp() 必须在初始化 IIFE 中被调用（否则整页不启动）。"""
    html = _template()
    assert re.search(r"\bstartApp\(\)\s*;", html), "startApp() 未被调用"
