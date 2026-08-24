# -*- coding: utf-8 -*-
"""integration/test/week_month/config_flow —— F4 回归钉扎：周/月报与成本账本链透传 config_path。

修复前（QA 评审发现的同病三处）：_ai_cost_ledger_md、generate_month_report_md 与
CLI main 的月报/周报调用仍自行取全局默认配置——用户写在 <data_root>/config.json
（或 --config 显式指定）里的 ai_sessions 等设置对周报/月报成本账本不生效，且与
上一批已接通的日报链（generate_day_report / generate_consolidated_md 等）口径不一致。

修复后：周/月链统一复用日报链同款助手 report._config_for_root 解析配置——优先级
「显式 config_path > <data_root>/config.json > 全局默认」（显式 --config 是用户最强
意图，与 dashboard._load_config_for_root 语义一致）；三个签名尾部追加可选参数
（缺省 None=旧行为，既有调用方零改动），CLI 调用点把手上的 --config 一路透传。
本文件用「根目录 / 显式两份内容相反的配置」钉扎优先级：
①两份并存时月报/成本账本取显式；②不传参时取中间层根目录配置。
追加钉扎（队长中途裁决的范围扩展）：③月报/周报内嵌 budget 小结块同样统一走
_config_for_root（不再自解析、吃得到显式 config_path）；④dashboard 的月报渲染
调用点透传 server.config_path。
测试全程用显式 paths 指向临时目录 + browser_history_enabled=False，
绝不探测真实会话目录与浏览器库。
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import report  # noqa: E402

from tests.support.scenario import check, fresh_tmp  # noqa: E402

_MODEL = "claude-3-5-sonnet"


def _write_cfg(path: str, **overrides) -> str:
    """写一份最小 config.json（浏览器/洞察全关，保证测试不碰真实目录）。"""
    cfg = {
        "ai_sessions": {"enabled": False},
        "browser_history_enabled": False,
        "insights": {"enabled": False},
        "poll_interval_s": 1,
    }
    cfg.update(overrides)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return path


def _seed_usage(root: str, day: str) -> None:
    """构造一条标准 usage.jsonl 记录（让月报主体有数据可聚合）。"""
    day_dir = os.path.join(root, day)
    os.makedirs(day_dir, exist_ok=True)
    rec = {
        "start": f"{day}T09:00:00", "end": f"{day}T09:30:00",
        "duration_ms": 30 * 60000,
        "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "开发工具",
        "contact": None, "ai_tool": None, "active": True,
    }
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _seed_ai_sessions(root: str, day: str) -> str:
    """在 <root>/sessions/opencode 下种一天的 AI 会话文件，返回该目录。"""
    sess_dir = os.path.join(root, "sessions", "opencode")
    os.makedirs(sess_dir, exist_ok=True)
    lines = [
        json.dumps({"timestamp": f"{day}T10:00:00", "role": "user", "content": "hi",
                    "model": _MODEL, "cwd": "/r/projA"}, ensure_ascii=False),
        json.dumps({"timestamp": f"{day}T10:01:00", "role": "assistant", "content": "abcd",
                    "model": _MODEL}, ensure_ascii=False),
    ]
    with open(os.path.join(sess_dir, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(lines) + chr(10))
    return sess_dir


def _explicit_cfg(tag: str, root: str, sess_dir: str) -> str:
    """显式 config_path：ai_sessions 开（与根目录配置内容相反）+ paths 锚定临时目录。"""
    return _write_cfg(os.path.join(fresh_tmp(tag), "custom.json"),
                      data_root=root,
                      ai_sessions={"enabled": True, "_marker": "explicit",
                                   "paths": {"opencode": [sess_dir]}})


def _run_main(argv: list[str]) -> str:
    """跑 report.main 并捕获 stdout；断言退出码为 0 后返回输出文本。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report.main(list(argv))
    check(rc == 0, f"main 退出码 0（{' '.join(argv[:2])}）", f"实际 {rc}")
    return buf.getvalue()


def _month_scenario(tag: str) -> tuple[str, str]:
    """搭一个「根配置关 / 数据与 AI 会话都在」的月份场景，返回 (root, explicit)。"""
    day = "2099-07-15"
    root = fresh_tmp(f"{tag}_root")
    _write_cfg(os.path.join(root, "config.json"), data_root=root)  # 根目录中间层：AI 关
    _seed_usage(root, day)
    sess_dir = _seed_ai_sessions(root, day)
    explicit = _explicit_cfg(f"{tag}_cfg", root, sess_dir)  # 显式层：AI 开（内容相反）
    return root, explicit


def test_month_report_honors_root_and_explicit_config():
    """① 月报链：不传参取根目录配置（关）；显式 config_path 压过根目录（开）。"""
    print("[test] 月报 config_path 透传：根配置 vs 显式配置优先级钉扎")
    month = "2099-07"
    root, explicit = _month_scenario("wmcf_month")

    # 场景 A（中间层）：不传参 → 走 <root>/config.json（AI 关）→ 月报无成本账本
    md = report.generate_month_report_md(month, root)
    check(md is not None and "AI 成本账本" not in md,
          "不传参时月报按根目录配置行事（AI 关 → 无账本章节）")

    # 场景 B（显式钉扎）：两份配置并存且内容相反 → 显式 config_path 必须胜出
    md_x = report.generate_month_report_md(month, root, config_path=explicit)
    check("AI 成本账本" in md_x, "显式 config_path 压过根目录配置（账本章节出现）")
    check(_MODEL in md_x, "成本账本真读了显式配置指向的会话数据（含模型拆分）")


def test_cost_ledger_honors_explicit_config():
    """② 成本账本直调：同样遵守「显式 > 根目录」优先级。"""
    print("[test] 成本账本 config_path 直调钉扎")
    day = "2099-07-15"
    root, explicit = _month_scenario("wmcf_ledger")

    ledger_off = report._ai_cost_ledger_md([day], root)
    check(ledger_off is None, "不传参走根目录配置（AI 关）→ 账本 None")
    ledger_on = report._ai_cost_ledger_md([day], root, "测试", config_path=explicit)
    check(ledger_on is not None and "AI 成本账本" in (ledger_on or ""),
          "显式 config_path 生效（账本生成）")
    check(_MODEL in (ledger_on or ""), "账本含显式配置指向数据的模型拆分")


def test_cli_month_forwards_config():
    """③ CLI --month：不带 --config 无账本；带 --config 时打印与落盘都有（覆盖两个调用点）。"""
    print("[test] CLI 月报 --config 透传（print 与 --write 两条路径）")
    month = "2099-07"
    root, explicit = _month_scenario("wmcf_cli_month")

    out = _run_main(["--month", month, "--data-root", root])
    check("AI 成本账本" not in out, "CLI 不带 --config：按根目录配置（无账本）")

    out2 = _run_main(["--month", month, "--data-root", root,
                      "--config", explicit, "--write"])
    check("AI 成本账本" in out2, "CLI 带 --config：打印的月报含账本")
    written_path = os.path.join(root, month, "report_month.md")
    check(os.path.isfile(written_path), "--write 落盘 report_month.md")
    with open(written_path, "r", encoding="utf-8") as fh:
        written = fh.read()
    check("AI 成本账本" in written, "--write 写入的月报同样走了显式配置")


def test_cli_week_forwards_config():
    """④ CLI --week：账本跟随 --config；周报链逐日日报也收到显式配置。"""
    print("[test] CLI 周报 --config 透传（账本 + 逐日日报两处调用点）")
    today = datetime.date.today().isoformat()
    root = fresh_tmp("wmcf_cli_week_root")
    _write_cfg(os.path.join(root, "config.json"), data_root=root)  # 根目录：AI 关
    sess_dir = _seed_ai_sessions(root, today)
    explicit = _explicit_cfg("wmcf_cli_week_cfg", root, sess_dir)

    # 第一遍：--write 但不带 --config → 根目录配置（关）：stdout 无账本、今日日报无 AI 章节
    out = _run_main(["--week", "--data-root", root, "--write"])
    check("AI 成本账本" not in out, "CLI 周报不带 --config：无账本章节")
    day_md_path = os.path.join(root, today, "report.md")
    check(os.path.isfile(day_md_path), "周报 --write 逐日日报已落盘")
    with open(day_md_path, "r", encoding="utf-8") as fh:
        day_md = fh.read()
    check("AI 会话深度" not in day_md, "逐日日报按根目录配置行事（无 AI 章节）")

    # 第二遍：同一路径带 --config 重跑 → stdout 有账本、今日日报出现 AI 章节（逐日透传钉扎）
    out2 = _run_main(["--week", "--data-root", root, "--config", explicit, "--write"])
    check("AI 成本账本" in out2, "CLI 周报带 --config：账本章节出现")
    with open(day_md_path, "r", encoding="utf-8") as fh:
        day_md2 = fh.read()
    check("AI 会话深度" in day_md2, "--config 透传到周报链的逐日日报生成")


def test_budget_blocks_honor_explicit_config():
    """⑤ budget 小结块（月报内 + 周报 CLI 内）：统一 _config_for_root 后吃得到显式配置。"""
    print("[test] 月报/周报预算块 config_path 统一解析钉扎")
    import budget as budget_mod

    month = "2099-07"
    root, explicit = _month_scenario("wmcf_budget_month")

    # 月报块：钉扎 generate_month_report_md 内部 budget_status 收到的 config
    seen: list[dict] = []
    real_status, real_md = budget_mod.budget_status, budget_mod.budget_summary_md

    def _spy_status(date, data_root, config, period=None):
        seen.append(config)
        return real_status(date, data_root, config, period=period)

    budget_mod.budget_status = _spy_status  # type: ignore[method-assign]
    budget_mod.budget_summary_md = lambda status: None  # type: ignore[method-assign]
    try:
        report.generate_month_report_md(month, root)  # 不传参 → 根目录配置
        check(bool(seen) and all(not (c.get("ai_sessions") or {}).get("_marker") for c in seen),
              "月报预算块不传参时收到根目录配置（无 marker）")
        seen.clear()
        report.generate_month_report_md(month, root, config_path=explicit)
        check(bool(seen) and all((c.get("ai_sessions") or {}).get("_marker") == "explicit" for c in seen),
              "月报预算块显式 config_path 胜出（收到 marker 配置）")
    finally:
        budget_mod.budget_status = real_status  # type: ignore[method-assign]
        budget_mod.budget_summary_md = real_md  # type: ignore[method-assign]

    # 周报块：CLI --week 路径内 budget_week_summary 收到的 config
    today = datetime.date.today().isoformat()
    wroot = fresh_tmp("wmcf_budget_week_root")
    _write_cfg(os.path.join(wroot, "config.json"), data_root=wroot)  # 根目录：AI 关
    sess_dir = _seed_ai_sessions(wroot, today)
    wexplicit = _explicit_cfg("wmcf_budget_week_cfg", wroot, sess_dir)

    seen_w: list[dict] = []
    real_ws = budget_mod.budget_week_summary

    def _spy_ws(days_, data_root_, config):
        seen_w.append(config)
        return real_ws(days_, data_root_, config)

    budget_mod.budget_week_summary = _spy_ws  # type: ignore[method-assign]
    try:
        _run_main(["--week", "--data-root", wroot])
        check(bool(seen_w) and all(not (c.get("ai_sessions") or {}).get("_marker") for c in seen_w),
              "周报预算块不传参时收到根目录配置")
        seen_w.clear()
        _run_main(["--week", "--data-root", wroot, "--config", wexplicit])
        check(bool(seen_w) and all((c.get("ai_sessions") or {}).get("_marker") == "explicit" for c in seen_w),
              "周报预算块显式 --config 胜出")
    finally:
        budget_mod.budget_week_summary = real_ws  # type: ignore[method-assign]


def test_dashboard_month_render_forwards_server_config():
    """⑥ dashboard._render_month_md（授权闭环点）：server.config_path 透传给月报链。

    _render_month_md 是只读 self.server 的纯方法，用假 self 无绑定直调，
    不起 HTTP 服务；spy 掉 report.generate_month_report_md 捕获透传参数。
    """
    print("[test] 仪表盘月报渲染透传 server.config_path")
    import dashboard  # noqa: PLC0415 —— 惰性导入（其余用例无需加载仪表盘模块）

    root, explicit = _month_scenario("wmcf_dash")
    captured: dict = {}
    real_gen = report.generate_month_report_md

    def _spy(month_str, data_root, config_path=None):
        captured["args"] = (month_str, data_root, config_path)
        return "MD"

    report.generate_month_report_md = _spy  # type: ignore[method-assign]
    try:
        fake_self = types.SimpleNamespace(
            server=types.SimpleNamespace(data_root=root, config_path=explicit))
        md = dashboard.Handler._render_month_md(fake_self, {"month": "2099-07"})
    finally:
        report.generate_month_report_md = real_gen  # type: ignore[method-assign]

    check(md == "MD" and captured.get("args") == ("2099-07", root, explicit),
          "仪表盘月报渲染把 server.config_path 一并透传", str(captured))


def teardown_module(module) -> None:
    """清理本文件产生的临时目录（fresh_tmp 都挂在 TEMP/usage_monitor_tests 下）。"""
    base = os.environ.get("TEMP", r"C:\Windows\Temp")
    tmp_root = os.path.join(base, "usage_monitor_tests")
    for name in os.listdir(tmp_root):
        if name.startswith(("wmcf_month", "wmcf_ledger", "wmcf_cli_")):
            shutil.rmtree(os.path.join(tmp_root, name), ignore_errors=True)
