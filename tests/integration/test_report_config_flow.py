# -*- coding: utf-8 -*-
"""integration/test/report/config_flow —— B2 回归钉扎：报表链透传 config_path。

修复前：monitor.finalize_day → report.generate_day_report → generate_consolidated_md
→ _ai_sessions_daily 全程不接配置，report 内部自行拿全局默认配置——用户写在
<data_root>/config.json（或 --config 显式指定）里的 ai_sessions/browser 等设置对
每日报表不生效，且与仪表盘（_load_config_for_root，语义正确）口径不一致。

修复后：日报链统一走 report._config_for_root 解析配置——优先级
「显式 config_path > <data_root>/config.json > 全局默认」（与 dashboard
_load_config_for_root 语义一致：显式 --config 是用户最强意图）；finalize_day 与
run_daemon 的两处调用点把 config_path 一路透传。本文件用 spy 钉扎两条断言：
①日报生成只按根目录/显式配置行事，绝不扫描真实会话目录；②守护进程的
config_path 原样抵达 finalize_day→generate_day_report。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import classifier  # noqa: E402
import monitor  # noqa: E402
import report  # noqa: E402

from tests.support.scenario import check, fresh_tmp  # noqa: E402

# ruff: F401 未用导入由 `ruff check --fix` 自动清理


def _rec(day: str) -> dict:
    """构造一条标准 usage.jsonl 会话记录（同 conftest.make_record 的形状）。"""
    return {
        "start": f"{day}T09:00:00", "end": f"{day}T09:30:00",
        "duration_ms": 30 * 60000,
        "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "开发工具",
        "contact": None, "ai_tool": None, "active": True,
    }


def _seed(root: str, day: str) -> None:
    day_dir = os.path.join(root, day)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_rec(day), ensure_ascii=False) + "\n")


def _write_cfg(path: str, **overrides) -> str:
    """写一份最小 config.json（默认全关，保证测试不碰真实目录）。"""
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


class _SpyToolPaths:
    """替换 ai_sessions._default_tool_paths：计数调用并返回空路径表（零真实扫描）。"""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {}


def test_day_report_honors_root_and_explicit_config():
    """① 根目录 config.json / 显式 config_path 对日报生效，且不扫真实目录。

    优先级钉扎（captain 最终口径，与 dashboard._load_config_for_root 一致）：
    显式 config_path > <root>/config.json > 全局默认——场景 B 让两份配置并存
    且内容相反，断言显式那份胜出。
    """
    print("[test] 报表配置流：<root>/config.json 与显式 config_path 均生效")
    day = "2099-05-10"

    # 场景 A：数据根自带 config.json（ai_sessions.enabled=false），无显式参数
    # → 走中间层根目录配置，日报零真实目录探测
    root_a = fresh_tmp("rcfg_root")
    _write_cfg(os.path.join(root_a, "config.json"))
    _seed(root_a, day)
    spy = _SpyToolPaths()
    real_paths = ai_sessions._default_tool_paths
    ai_sessions._default_tool_paths = spy  # type: ignore[method-assign]
    try:
        report.generate_day_report(day, root_a)
    finally:
        ai_sessions._default_tool_paths = real_paths  # type: ignore[method-assign]
    check(spy.n == 0, "ai_sessions 关闭时日报零探测（不碰真实会话目录）", f"实际调用 {spy.n} 次")
    check(os.path.isfile(os.path.join(root_a, day, "report.md")), "report.md 正常生成")

    # 场景 B：数据根与显式 config_path 并存且内容相反（根=关、显式=开+marker）
    # → 显式 config_path 必须压过根目录 config.json
    root_b = fresh_tmp("rcfg_explicit_root")
    _write_cfg(os.path.join(root_b, "config.json"))  # 根目录：ai_sessions 关
    explicit_cfg = _write_cfg(os.path.join(fresh_tmp("rcfg_explicit_cfg"), "custom.json"),
                              ai_sessions={"enabled": True, "_marker": "explicit"})
    _seed(root_b, day)
    seen_cfgs: list[dict] = []
    real_collect = ai_sessions.collect
    real_paths_b = ai_sessions._default_tool_paths

    def _spy_collect(date_str, config, web_visits=None):
        seen_cfgs.append(config)
        return real_collect(date_str, config, web_visits=web_visits)

    ai_sessions.collect = _spy_collect  # type: ignore[method-assign]
    ai_sessions._default_tool_paths = _SpyToolPaths()  # type: ignore[method-assign]
    try:
        report.generate_day_report(day, root_b, config_path=explicit_cfg)
    finally:
        ai_sessions.collect = real_collect  # type: ignore[method-assign]
        ai_sessions._default_tool_paths = real_paths_b  # type: ignore[method-assign]
    check(bool(seen_cfgs), "显式启用时 collect 被调用")
    check(any((c.get("ai_sessions") or {}).get("_marker") == "explicit" for c in seen_cfgs),
          "显式 config_path 压过根目录 config.json（collect 收到 marker 配置）")


def test_run_daemon_forwards_config_path_to_finalize():
    """② run_daemon(config_path=X) 把 X 原样透传 finalize_day→generate_day_report。"""
    print("[test] 守护进程 config_path 透传到日报链")
    tmp = fresh_tmp("rcfg_daemon")
    x_cfg = _write_cfg(os.path.join(tmp, "explicit.json"), data_root=tmp)
    cfg = classifier.load_config(x_cfg)
    cfg["data_root"] = tmp  # 双保险：data_root 锚定临时目录

    fin_calls: list = []
    gen_calls: list = []
    real_fin = monitor.finalize_day
    real_gen = report.generate_day_report

    def _spy_fin(day_str, root, retention, config_path=None):
        # 只记录、不打断：真实 finalize_day 继续走，才能验证它向下游透传
        fin_calls.append(config_path)
        return real_fin(day_str, root, retention, config_path=config_path)

    monitor.finalize_day = _spy_fin  # type: ignore[method-assign]
    report.generate_day_report = (  # type: ignore[method-assign]
        lambda day_str, root, full_urls=False, config_path=None: gen_calls.append(config_path))
    try:
        monitor.run_daemon(cfg, test_seconds=2, verbose=False, config_path=x_cfg)
    finally:
        monitor.finalize_day = real_fin  # type: ignore[method-assign]
        report.generate_day_report = real_gen  # type: ignore[method-assign]

    check(len(fin_calls) >= 1, "启动补昨日触发 finalize_day", f"实际 {len(fin_calls)} 次")
    check(all(p == x_cfg for p in fin_calls), "config_path 原样透传到 finalize_day")
    check(len(gen_calls) >= 1 and all(p == x_cfg for p in gen_calls),
          "finalize_day 继续透传到 generate_day_report")
    shutil.rmtree(tmp, ignore_errors=True)
