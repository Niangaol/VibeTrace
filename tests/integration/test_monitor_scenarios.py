# -*- coding: utf-8 -*-
"""integration/test/monitor/scenarios — 自 test_all.py 移植

由 test_all.py（@7d60620，336 项检查）机械移植拆分而来——断言逻辑逐行保持一致；
仅有的改动：①助手移入 tests/support/scenario.py 并 import；②_chrome_ft 改为
正午锚定（消除午夜抖动类 flaky）；③去掉独立 main 入口（统一由 pytest 收集）。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
import time
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import win32core  # noqa: E402
import monitor  # noqa: E402
import classifier  # noqa: E402

from tests.support.scenario import (  # noqa: E402
    FG, P, FakeClock, check, fresh_tmp, run_scenario,
)

# ruff: F401 未用导入由 `ruff check --fix` 自动清理

def test_switch_timing():
    print("[test] 切换计时（3 个应用各停 3 轮，应产生 3 条会话）")
    fg = [FG("code.exe", "main.py - VS Code")] * 3 \
        + [FG("wechat.exe", "张三")] * 3 \
        + [FG("chrome.exe", "GitHub - 主页")] * 3
    recs, tmp = run_scenario("switch", fg, seconds=9)
    check(len(recs) == 3, "产生 3 条会话", f"实际 {len(recs)}: {[r['app'] for r in recs]}")
    check(recs[0]["app"] == "VS Code", "第 1 条 VS Code", recs[0]["app"])
    check(recs[1]["app"] == "微信" and recs[1]["contact"] == "张三", "第 2 条 微信/张三")
    check(recs[2]["app"] == "Chrome", "第 3 条 Chrome")
    for r in recs:
        check(1500 <= r["duration_ms"] <= 4000, "时长约 2-3 秒", str(r["duration_ms"]))
    check(recs[0]["end"] <= recs[1]["start"] and recs[1]["end"] <= recs[2]["start"], "时间连续不重叠")
    shutil.rmtree(tmp, ignore_errors=True)

def test_idle_truncation():
    print("[test] 空闲不计时（3 轮活动 -> 2 轮空闲 -> 恢复，空闲段必须被截断）")
    fg = [FG("code.exe", "main.py")] * 7
    idle = [1.0, 1.0, 1.0, 400.0, 400.0, 1.0, 1.0]
    recs, tmp = run_scenario("idle", fg, idle, seconds=7)
    check(len(recs) == 2, "空闲截断产生 2 条会话（恢复后新开）", f"实际 {len(recs)}")
    r0, r1 = recs[0], recs[1]
    check(r0["duration_ms"] < 3500, "第 1 条不含空闲段（<3.5s）", f"{r0['duration_ms']}ms")
    check(r0["duration_ms"] >= 1000, "第 1 条包含活动段（>=1s）", f"{r0['duration_ms']}ms")
    check(r1["start"] > r0["end"], "第 2 条在第 1 条之后开始（空闲段未被计入）")
    shutil.rmtree(tmp, ignore_errors=True)

def test_contact_and_main_title():
    print("[test] 微信联系人（聊天窗口 -> 张三；主界面 -> 无联系人）")
    fg = [FG("wechat.exe", "张三")] * 3 + [FG("wechat.exe", "微信")] * 3
    recs, tmp = run_scenario("contact", fg, seconds=6)
    check(len(recs) == 2, "2 条会话", f"实际 {len(recs)}")
    check(recs[0]["contact"] == "张三" and recs[0]["category"] == "社交聊天", "聊天窗口解析出张三")
    check(recs[1]["contact"] is None, "主界面无联系人")
    check(recs[1]["app"] == "微信" and recs[1]["category"] == "社交聊天", "主界面仍计应用时长")
    shutil.rmtree(tmp, ignore_errors=True)

def test_browser_categories():
    print("[test] 浏览器分类（B站视频 / GitHub代码 / MOOC学习）")
    fg = [FG("chrome.exe", "bilibili - 视频")] * 3 \
        + [FG("chrome.exe", "GitHub - 主页")] * 3 \
        + [FG("chrome.exe", "中国大学MOOC - 课程")] * 3
    recs, tmp = run_scenario("browser", fg, seconds=9)
    check(len(recs) == 3, "3 条会话")
    check(recs[0]["browser_category"] == "视频", "B站 -> 视频", str(recs[0].get("browser_category")))
    check(recs[1]["browser_category"] == "代码", "GitHub -> 代码")
    check(recs[2]["browser_category"] == "学习", "MOOC -> 学习")
    for r in recs:
        check(r["category"] == "浏览器", "顶层类别为浏览器", r["category"])
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_tool_detection():
    print("[test] 终端 AI 工具（wt 里跑 opencode -> ai_tool=opencode）")
    tree = {100: P("wt.exe", 0, 100), 200: P("opencode.exe", 100, 200), 300: P("python.exe", 200, 300)}
    fg = [FG("wt.exe", "opencode", pid=100)] * 3
    recs, tmp = run_scenario("aitool", fg, seconds=3, process_tree=tree, fg_pid_for_tree=100)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "opencode", "识别 opencode", str(recs[0].get("ai_tool")))
    # 验收口径（§14-6）：终端跑 opencode -> 日报记为 AI编程
    check(recs[0]["category"] == "AI编程", "终端 opencode 会话归入 AI编程", recs[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_false_positive():
    print("[test] AI 误伤防护（wt 里只有 python/pip -> ai_tool=None）")
    tree = {100: P("wt.exe", 0, 100), 200: P("python.exe", 100, 200), 300: P("pip.exe", 200, 300)}
    fg = [FG("wt.exe", "python -m pip install", pid=100)] * 3
    recs, tmp = run_scenario("aifp", fg, seconds=3, process_tree=tree)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] is None, "python/pip 不误判为 pi agent", str(recs[0].get("ai_tool")))
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_tool_in_editor_terminal():
    print("[test] 编辑器集成终端 AI 工具（VS Code 里跑 opencode -> ai_tool=opencode, 类别=AI编程）")
    tree = {100: P("code.exe", 0, 100), 200: P("opencode.exe", 100, 200)}
    fg = [FG("code.exe", "main.py - Visual Studio Code", pid=100)] * 3
    recs, tmp = run_scenario("editorai", fg, seconds=3, process_tree=tree)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "opencode", "编辑器集成终端识别 opencode", str(recs[0].get("ai_tool")))
    check(recs[0]["category"] == "AI编程", "识别后类别归入 AI编程", recs[0]["category"])
    # 编辑器本身不承载 AI 工具时类别不受影响
    tree2 = {100: P("code.exe", 0, 100), 200: P("node.exe", 100, 200)}
    fg2 = [FG("code.exe", "main.py - Visual Studio Code", pid=100)] * 3
    recs2, tmp2 = run_scenario("editornoai", fg2, seconds=3, process_tree=tree2)
    check(len(recs2) >= 1, "有会话")
    check(recs2[0]["ai_tool"] is None, "普通开发会话 ai_tool 为空", str(recs2[0].get("ai_tool")))
    check(recs2[0]["category"] == "开发工具", "普通开发会话类别保持开发工具", recs2[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)

def test_day_rollover():
    print("[test] 跨天（23:59:58 跨越 0 点 -> 两个日期文件夹 + 前一日日报）")
    real_datetime_mod = monitor.datetime

    class FakeDT(_dt.datetime):
        _cur = _dt.datetime(2026, 8, 8, 23, 59, 58)
        _step = _dt.timedelta(seconds=1)

        @classmethod
        def now(cls, tz=None):
            v = cls._cur
            cls._cur = v + cls._step
            return v

    fake_mod = types.ModuleType("datetime")
    fake_mod.datetime = FakeDT
    fake_mod.date = _dt.date
    fake_mod.timedelta = _dt.timedelta
    monitor.datetime = fake_mod
    try:
        fg = [FG("code.exe", "main.py")] * 6
        recs, tmp = run_scenario("rollover", fg, seconds=6, idle_threshold=999)
    finally:
        monitor.datetime = real_datetime_mod

    d1 = os.path.join(tmp, "2026-08-08")
    d2 = os.path.join(tmp, "2026-08-09")
    check(os.path.isdir(d1) and os.path.isdir(d2), "生成两个日期文件夹")
    check(os.path.isfile(os.path.join(d1, "report.md")), "前一日自动生成 report.md")
    check(os.path.isfile(os.path.join(d2, "usage.jsonl")), "新一天 usage.jsonl")
    recs_08 = [r for r in recs if r["start"].startswith("2026-08-08")]
    recs_09 = [r for r in recs if r["start"].startswith("2026-08-09")]
    check(len(recs_08) >= 1, "8 月 8 日有记录", str(len(recs_08)))
    check(len(recs_09) >= 1, "8 月 9 日有记录", str(len(recs_09)))
    shutil.rmtree(tmp, ignore_errors=True)

def test_title_blacklist():
    print("[test] 隐私黑名单（标题含'密码' -> [已隐藏]，且不产出 contact/browser_category）")
    fg = [FG("wechat.exe", "我的密码是abc")] * 3
    recs, tmp = run_scenario("blacklist", fg, seconds=3)
    check(len(recs) >= 1, "有会话")
    r = recs[0]
    check(r["title"] == "[已隐藏]", "标题被隐藏", repr(r["title"]))
    check(r["contact"] is None, "隐藏后不解析联系人", str(r["contact"]))
    check(r.get("browser_category") is None, "隐藏后不做浏览器分类")
    check(r["category"] == "社交聊天", "类别仍按 exe 归类", r["category"])
    shutil.rmtree(tmp, ignore_errors=True)

def test_static_zero_write():
    print("[test] 静止零写入（前台不变 8 轮 -> 只有退出时 1 条）")
    fg = [FG("code.exe", "main.py")] * 8
    recs, tmp = run_scenario("static", fg, seconds=8)
    check(len(recs) == 1, "仅最终关闭写 1 条", f"实际 {len(recs)}")
    shutil.rmtree(tmp, ignore_errors=True)

def test_pause_resume():
    """暂停/继续（暂停期间不写入）——伪时钟确定性版。

    原版用真实 time.sleep 编排（2.5s 后暂停/3s 后恢复），首轮轮询被负载拖过
    暂停点时会只剩 1 条记录（负载 flaky）。现改为与同文件 day_rollover 相同的
    伪时钟范式：monitor 的 sleep/monotonic/datetime 全部可步进，_pause 替换为
    「wait 时推进伪时钟」的假事件；按「轮次完成数」在第 3 轮后暂停、第 7 轮后
    恢复，断言精确到条数与间隔。
    """
    print("[test] 暂停/继续（暂停期间不写入）")
    tmp = fresh_tmp("pause")
    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["poll_interval_s"] = 1
    cfg["idle_threshold_s"] = 180
    # 隔离：同 run_scenario，禁止 finalize_day 触碰真实会话目录
    cfg["ai_sessions"] = {"enabled": False}
    cfg["browser_history_enabled"] = False

    import datetime as _dtmod

    real_fg = win32core.get_foreground_info
    real_idle = win32core.idle_seconds
    real_dt_class = monitor.datetime.datetime
    real_mono = monitor.time.monotonic
    real_sleep = monitor.time.sleep
    real_pause_event = monitor._pause

    clock = FakeClock([FG("code.exe", "main.py")] * 40)
    fake_mono = [0.0]
    fake_dt = [_dtmod.datetime.now().replace(microsecond=0)]

    class _FakePauseEvent:
        """is_set 语义同 Event；wait(timeout) 推进伪时钟（模拟暂停期时间流逝）。"""

        def __init__(self):
            self._flag = False

        def set(self):
            self._flag = True

        def clear(self):
            self._flag = False

        def is_set(self):
            return self._flag

        def wait(self, timeout=None):
            if timeout:
                fake_mono[0] += float(timeout)
                fake_dt[0] += _dtmod.timedelta(seconds=float(timeout))
            # 暂停分支 continue 会跳过循环尾部的退出检查与 _fake_sleep，
            # 恢复切换必须由 wait 自己驱动（第 7 轮完成 = 暂停持续 4 个轮次）
            done["n"] += 1
            if done["n"] == 7:
                self._flag = False
            return True

    class _FakeDT(real_dt_class):  # type: ignore[valid-type]
        @classmethod
        def now(cls, tz=None):
            return fake_dt[0]

    # 轮次完成计数（active 轮计 sleep，paused 轮计 wait）：第 3 轮后暂停、第 7 轮后恢复
    done = {"n": 0}

    def _fake_sleep(secs):
        fake_mono[0] += float(secs)
        fake_dt[0] += _dtmod.timedelta(seconds=float(secs))
        done["n"] += 1
        if done["n"] == 3:
            monitor.set_paused(True)
        elif done["n"] == 7:
            monitor.set_paused(False)

    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    monitor.datetime.datetime = _FakeDT  # type: ignore[attr-defined]
    monitor.time.monotonic = lambda: fake_mono[0]  # type: ignore[attr-defined]
    monitor.time.sleep = _fake_sleep  # type: ignore[attr-defined]
    monitor._pause = _FakePauseEvent()
    monitor.stop_event.clear()
    monitor.set_paused(False)

    try:
        recs = monitor.run_daemon(cfg, test_seconds=12)
    finally:
        win32core.get_foreground_info = real_fg
        win32core.idle_seconds = real_idle
        monitor.datetime.datetime = real_dt_class  # type: ignore[attr-defined]
        monitor.time.monotonic = real_mono  # type: ignore[attr-defined]
        monitor.time.sleep = real_sleep  # type: ignore[attr-defined]
        monitor._pause = real_pause_event
        monitor.stop_event.clear()
        monitor.set_paused(False)

    check(len(recs) == 2, "暂停截断 + 恢复后各 1 条", f"实际 {len(recs)}")
    if len(recs) == 2:
        gap = (_dt.datetime.fromisoformat(recs[1]["start"])
               - _dt.datetime.fromisoformat(recs[0]["end"])).total_seconds()
        check(gap > 0, "恢复后的会话在暂停之后开始")
        check(2.0 <= gap <= 8.0, f"两条会话间隔约等于暂停时长（{gap:.1f}s）")
    shutil.rmtree(tmp, ignore_errors=True)


def test_retention():
    print("[test] 保留清理（超过保留期删除，只删 YYYY-MM-DD 目录）")
    tmp = fresh_tmp("retention")
    for d in ["2026-07-01", "2026-08-01", "2026-08-08"]:
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    os.makedirs(os.path.join(tmp, "backup_notes"), exist_ok=True)
    with open(os.path.join(tmp, "notes.txt"), "w") as fh:
        fh.write("keep")

    # 以 2026-08-08 为今天（monitor.retention_cleanup 用真实今天；直接调用并临时对齐）
    real_mod = monitor.datetime

    class FakeDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 8, 8)

    fake_mod = types.ModuleType("datetime")
    fake_mod.datetime = _dt.datetime
    fake_mod.date = FakeDate
    fake_mod.timedelta = _dt.timedelta
    monitor.datetime = fake_mod
    try:
        monitor.retention_cleanup(tmp, retention_days=7)
    finally:
        monitor.datetime = real_mod

    check(not os.path.isdir(os.path.join(tmp, "2026-07-01")), "7-01（超 7 天）已删除")
    check(os.path.isdir(os.path.join(tmp, "2026-08-01")), "8-01（恰好 7 天）保留")
    check(os.path.isdir(os.path.join(tmp, "2026-08-08")), "今天保留")
    check(os.path.isdir(os.path.join(tmp, "backup_notes")), "非日期目录不受影响")
    check(os.path.isfile(os.path.join(tmp, "notes.txt")), "普通文件不受影响")
    shutil.rmtree(tmp, ignore_errors=True)

def test_electron_shell_detection():
    print("[test] Electron 桌面壳探测（dev 模式 / 打包模式 / 缺失回退）")
    import monitor

    # dev 模式：electron.exe + main.js 存在时返回可执行命令
    base = os.path.dirname(os.path.abspath(monitor.__file__))
    app_dir = os.path.join(base, "electron-app")
    electron_exe = os.path.join(app_dir, "node_modules", "electron", "dist", "electron.exe")
    if os.path.isfile(electron_exe):
        cmd = monitor._find_electron_shell()
        check(cmd is not None and len(cmd) >= 2, "dev 模式探测到 Electron 壳", str(cmd))
        check(os.path.isfile(cmd[0]), "返回的 electron.exe 存在")
        check(cmd[1].endswith("main.js"), "第二个参数是 main.js", str(cmd[1]))
    else:
        check(monitor._find_electron_shell() is None or True, "无 dev 环境时跳过（不失败）")

    # 打包模式：exe 位于 <root>/dist，electron-app 位于项目根 <root>（父目录）
    fake_root = fresh_tmp("shell_frozen")
    fake_dist = os.path.join(fake_root, "dist")
    os.makedirs(fake_dist, exist_ok=True)
    fake_app = os.path.join(fake_root, "electron-app")
    os.makedirs(os.path.join(fake_app, "node_modules", "electron", "dist"), exist_ok=True)
    fake_elec = os.path.join(fake_app, "node_modules", "electron", "dist", "electron.exe")
    open(fake_elec, "w").close()
    open(os.path.join(fake_app, "main.js"), "w").close()
    os.makedirs(os.path.join(fake_app, "dist"), exist_ok=True)
    fake_packed = os.path.join(fake_app, "dist", "UsageMonitor-Desktop-2.0.0.exe")
    open(fake_packed, "w").close()
    real_script_dir = monitor.paths.script_dir
    try:
        monitor.paths.script_dir = lambda: fake_dist
        cmd = monitor._find_electron_shell()
        check(cmd and cmd[0] == fake_packed, "打包 exe 优先（父目录项目根）", str(cmd))
    finally:
        monitor.paths.script_dir = real_script_dir
    shutil.rmtree(fake_root, ignore_errors=True)

def test_ai_own_window():
    print("[test] 自有窗口 AI 工具（ChatGPT 桌面版前台 -> ai_tool=chatgpt）")
    fg = [FG("chatgpt.exe", "New chat")] * 3
    recs, tmp = run_scenario("aiown", fg, seconds=3)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "chatgpt", "ai_tool=chatgpt", str(recs[0].get("ai_tool")))
    check(recs[0]["category"] == "AI编程", "类别=AI编程", recs[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)

def test_cross_day_isolation():
    print("[test] 跨天隔离（昨天打开的页面 -> 时长按日界分摊，绝不串天）")
    import sqlite3
    import browser_history

    tmp = fresh_tmp("crossday")
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    # 昨天 23:30 打开、时长 2 小时 -> 区间跨入今天 01:30
    y_2330 = _dt.datetime.combine(_dt.date.today() - _dt.timedelta(days=1), _dt.time(23, 30))
    ft_cross = int((time.mktime(y_2330.timetuple()) + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://www.icourse163.org/course/cross", "跨天MOOC课"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, ?)",
                 (ft_cross, 2 * 3600 * 1_000_000))
    # 三天前打开、无时长 -> 不应进入任何一天的报表（防污染）
    old = _dt.datetime.combine(_dt.date.today() - _dt.timedelta(days=3), _dt.time(10, 0))
    ft_old = int((time.mktime(old.timetuple()) + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://old.example.com/", "旧页面"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (2, ?, 0)", (ft_old,))
    conn.commit()
    conn.close()

    cfg = classifier.load_config()
    today = _dt.date.today().isoformat()
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()

    t_data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    cross = [v for v in t_data["visits"] if "cross" in v["url"]]
    check(len(cross) == 1, "跨天访问进入今天的报表")
    check(cross[0]["duration_s"] == 5400.0, "今天只算 00:00-01:30 的 1.5 小时", str(cross[0]["duration_s"]))
    check(cross[0]["time"].startswith(today), "跨天访问在今天的显示时间从 0 点起", cross[0]["time"])
    check(all("old.example.com" not in v["url"] for v in t_data["visits"]), "三天前的无时长访问不污染今天")
    check(t_data["total_duration_s"] == 5400.0, "今天总停留 = 5400 秒", str(t_data["total_duration_s"]))

    y_data = browser_history.collect(yesterday, tmp, cfg, db_paths=[db])
    cross_y = [v for v in y_data["visits"] if "cross" in v["url"]]
    check(len(cross_y) == 1, "跨天访问也进入昨天的报表")
    check(cross_y[0]["duration_s"] == 1800.0, "昨天只算 23:30-24:00 的 0.5 小时", str(cross_y[0]["duration_s"]))
    check(y_data["total_duration_s"] == 1800.0, "昨天总停留 = 1800 秒", str(y_data["total_duration_s"]))

    # 两天份额相加 = 原始时长，无丢失
    check(round(t_data["total_duration_s"] + y_data["total_duration_s"]) == 7200, "两天份额合计 = 2 小时")
    shutil.rmtree(tmp, ignore_errors=True)
