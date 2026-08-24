# -*- coding: utf-8 -*-
"""monitor.py — 电脑使用情况监控守护进程。

- 每 5 秒轮询前台窗口（Win32），会话状态变化才写一条（静止零写入）；
- 空闲/锁屏不计时（会话在最后一次输入处截断）；
- vibe coding 进程树识别（终端里运行的 opencode / pi agent / claude 等）；
- 微信/QQ/钉钉联系人解析、浏览器 视频/代码/学习 分类；
- 跨天自动生成前一日 report.md / report.csv，并按保留天数清理过期文件夹；
- 支持 --test N（跑 N 秒后退出打印汇总）、--tray（托盘图标）、--foreground。

纯标准库实现，pythonw 静默运行兼容。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classifier  # noqa: E402
import report  # noqa: E402
import version  # noqa: E402
import win32core  # noqa: E402
import paths  # noqa: E402
import sqlite_store  # noqa: E402

_pause = threading.Event()      # 暂停监控（托盘使用）
stop_event = threading.Event()  # 停止守护（托盘退出时置位）

DEFAULT_POLL_INTERVAL = 5
DEFAULT_IDLE_THRESHOLD = 180
DEFAULT_RETENTION = 90


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_config(config_path: str | None = None) -> dict:
    """读取 config.json（缺失时 classifier 使用默认配置）。"""
    return classifier.load_config(config_path)


def set_paused(paused: bool) -> None:
    """暂停/恢复监控（托盘菜单调用）。"""
    if paused:
        _pause.set()
    else:
        _pause.clear()


def is_paused() -> bool:
    return _pause.is_set()


# ---------------------------------------------------------------------------
# 日报生成托盘通知调度
# ---------------------------------------------------------------------------
# 报告任务（UsageMonitorReport）每天 19:30 生成当日 report.md。常驻托盘进程
# 每 60 秒检查一次当日 report.md 的 mtime：当天生成且不早于 19:25（即刚生成）
# 时弹一次气泡；用内存变量记住已处理日期，跨天自动重置，一天只弹一次。
REPORT_DONE_HOUR = 19
REPORT_DONE_MIN = 30
_REPORT_RECENT_AFTER = datetime.time(REPORT_DONE_HOUR, REPORT_DONE_MIN - 5)  # 19:25

# 内存态：当天是否已处理；arming 表示"报告尚未生成、等待生成后补弹"。
_report_notified_day: str | None = None
_report_armed: bool = False


def _today_report_recent(data_root: str, day_str: str) -> bool:
    """当日 report.md 是否"刚生成"：存在、mtime 是今天、且时间不早于 19:25。"""
    path = os.path.join(data_root, day_str, "report.md")
    if not os.path.isfile(path):
        return False
    try:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return False
    if mtime.date() != datetime.date.today():
        return False
    return mtime.time() >= _REPORT_RECENT_AFTER


def check_report_balloon(data_root: str, notify_fn) -> bool:
    """日报生成后弹一次气泡；返回是否触发通知。

    - 启动/跨天首查：若报告在监控启动前就已生成（recent=True）视为"已通知"，
      只登记不补弹，避免重启后误弹；否则武装，等待生成后首次发现再弹。
    - 常态轮询：当日已武装且报告出现"刚生成"时弹一次并解除武装。
    """
    global _report_notified_day, _report_armed
    day_str = datetime.date.today().isoformat()
    recent = _today_report_recent(data_root, day_str)

    if _report_notified_day is None or day_str != _report_notified_day:
        # 启动 / 跨天：登记当天武装状态
        _report_notified_day = day_str
        _report_armed = not recent
        return False

    if _report_armed and recent:
        _report_armed = False
        try:
            notify_fn()
        except Exception:  # noqa: BLE001 —— 通知失败不影响守护
            pass
        return True
    return False


def _hwnd_tray_ready() -> bool:
    """托盘隐藏窗口句柄是否已就绪（show_balloon 依赖它）。"""
    import tray  # noqa: PLC0415
    return tray._hwnd != 0


def _run_balloon_scheduler(data_root: str) -> None:
    """后台守护线程：每 60 秒调用 check_report_balloon 检测日报生成。

    托盘图标惰性导入；图标不可用/未就绪时 show_balloon 静默降级。
    """
    import tray  # noqa: PLC0415 —— 惰性导入

    def _notify() -> None:
        tray.show_balloon("日报已生成", "今日使用报告已生成，点击查看")

    while not stop_event.is_set():
        try:
            if _hwnd_tray_ready():
                check_report_balloon(data_root, _notify)
        except Exception:  # noqa: BLE001 —— 单次检查失败不中断调度
            _log_error(data_root, datetime.date.today().isoformat(),
                       sys.exc_info()[1], "report balloon")
        # 每 60 秒检查一次；被 stop_event 打断后立即退出
        stop_event.wait(60)


def _run_alerts_scheduler(data_root: str, config_path: str | None = None) -> None:
    """告警调度线程入口（v2.7 行动闭环）：预算/连续工作提醒，托盘气泡通知。

    依赖注入见 alerts.run_alert_loop；此处传 paused_fn 与主循环暂停状态联动。
    """
    import alerts  # noqa: PLC0415 —— 惰性导入
    alerts.run_alert_loop(stop_event, data_root, config_path, paused_fn=is_paused)


def _run_update_checker(data_root: str, config: dict, delay: float = 15.0) -> None:
    """启动后延迟检查一次新版本；发现更新且托盘就绪时气泡提示。

    失败静默（离线/限流不影响守护）；config 的 update.api_base 可覆盖检测源。
    """
    time.sleep(max(1.0, delay))
    try:
        up = config.get("update") if isinstance(config.get("update"), dict) else {}
        if not up.get("check_on_startup", True):
            return
        import updater  # noqa: PLC0415 —— 惰性导入
        result = updater.check_for_update(api_base=str(up.get("api_base") or "").strip() or None,
                                          timeout=6.0)
        if result.get("has_update") and _hwnd_tray_ready():
            import tray  # noqa: PLC0415
            tray.show_balloon(
                "发现新版本 v" + str(result.get("latest") or ""),
                "点击托盘「检查更新」或打开仪表盘查看并安装",
            )
    except Exception:  # noqa: BLE001 —— 更新检查失败静默
        pass


# ---------------------------------------------------------------------------
# 日志与写入
# ---------------------------------------------------------------------------
def _log_error(data_root: str, day_str: str, exc: BaseException, context: str = "") -> None:
    """把错误写入当日 errors.log + 统一日志 applog（守护进程静默运行，不打印）。"""
    try:
        day_dir = os.path.join(data_root, day_str or datetime.date.today().isoformat())
        os.makedirs(day_dir, exist_ok=True)
        with open(os.path.join(day_dir, "errors.log"), "a", encoding="utf-8") as fh:
            fh.write(
                f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {context}: {exc}\n"
            )
            # 仅当确实有活动异常时写堆栈（主动记录如 single-instance 拒绝不写）
            if sys.exc_info()[0] is not None:
                fh.write(traceback.format_exc() + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        import applog  # noqa: PLC0415 —— 惰性导入
        applog.get_logger("monitor").error("%s: %s", context or "error", exc)
    except Exception:  # noqa: BLE001
        pass


def append_session_record(day_str: str, record: dict, data_root: str,
                          sqlite_enabled: bool = True) -> None:
    """JSON Lines 追加写一条会话记录到 当日文件夹/usage.jsonl。

    写入后 flush + fsync，最大限度避免断电/崩溃留下半行 JSON
    （低频写入场景下 fsync 开销可忽略）。
    若 config 开启 `sqlite.enabled`（默认 true），best-effort 同步写入 usage.db；
    SQLite 只是额外镜像，失败静默降级，不影响 JSONL 原始日志。
    """
    day_dir = os.path.join(data_root, day_str)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # noqa: BLE001 —— 部分文件系统不支持 fsync
            pass
    if sqlite_enabled:
        sqlite_store.append_record(data_root, day_str, record)


def _round_sec(dt: datetime.datetime) -> datetime.datetime:
    """四舍五入到整秒（写入 ISO 的 start/end 与 duration_ms 严格自洽）。"""
    return (dt + datetime.timedelta(milliseconds=500)).replace(microsecond=0)


def make_record(session: dict, end_dt: datetime.datetime) -> dict | None:
    """会话 dict -> JSON 记录（时长 <= 0 返回 None）。

    start/end 四舍五入到整秒后写入，duration_ms 用同一组值计算，
    保证文件内 duration_ms == end - start 严格相等。
    """
    start_r = _round_sec(session["start"])
    end_r = _round_sec(end_dt)
    duration_ms = int((end_r - start_r).total_seconds() * 1000)
    if duration_ms <= 0:
        return None
    rec = {
        "start": start_r.isoformat(),
        "end": end_r.isoformat(),
        "duration_ms": duration_ms,
        "exe": session["exe"],
        "app": session["app"],
        "title": session["title"],
        "category": session["category"],
        "contact": session["contact"],
        "ai_tool": session["ai_tool"],
        "active": True,
    }
    if session.get("browser_category"):
        rec["browser_category"] = session["browser_category"]
    # 监控维度细化字段（仅在存在时写入）
    for key in ("subcategory", "term_tool", "window_state", "url"):
        if session.get(key):
            rec[key] = session[key]
    return rec


def _close_session(session: dict, end_dt: datetime.datetime, data_root: str,
                   day_str: str, config: dict | None = None) -> dict | None:
    """关闭会话并写入；返回记录（未写入时返回 None）。

    浏览器会话落盘时尽力关联 URL（会话 ↔ 历史时间重叠），失败不影响写入。
    """
    # 浏览器会话：尝试关联当时访问的 URL（维度细化，best-effort）
    if config is not None and session.get("exe") in config.get("browser_exes", []) \
            and not session.get("url"):
        try:
            import browser_history  # noqa: PLC0415 —— 惰性导入
            url = browser_history.find_url_for_session(
                session["start"], end_dt, data_root, config)
            if url:
                session["url"] = url
        except Exception:  # noqa: BLE001 —— URL 关联失败不影响会话写入
            pass
    rec = make_record(session, end_dt)
    if rec is None:
        return None
    sqlite_enabled = True
    if config is not None:
        sqlite_cfg = config.get("sqlite") if isinstance(config.get("sqlite"), dict) else {}
        sqlite_enabled = bool(sqlite_cfg.get("enabled", True))
    append_session_record(day_str, rec, data_root, sqlite_enabled=sqlite_enabled)
    return rec


# ---------------------------------------------------------------------------
# 跨天/清理
# ---------------------------------------------------------------------------
def retention_cleanup(data_root: str, retention_days: int) -> None:
    """删除超过保留期的日期文件夹（仅匹配 YYYY-MM-DD 目录名，避免误删）。"""
    today = datetime.date.today()
    if not os.path.isdir(data_root):
        return
    for name in os.listdir(data_root):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            continue
        try:
            folder_date = datetime.date.fromisoformat(name)
        except ValueError:
            continue
        if (today - folder_date).days > retention_days:
            try:
                shutil.rmtree(os.path.join(data_root, name))
            except OSError:
                pass


def _refresh_inventory(data_root: str, config: dict) -> None:
    """守护启动/跨天时刷新当日软件清单（文档 §6.1：每次启动刷新一次，含新软件补录）。

    全链路扫描约 25ms，可忽略；失败只记日志不影响守护。
    """
    try:
        import inventory  # noqa: PLC0415 —— 惰性导入，避免拖慢启动
        day = datetime.date.today().isoformat()
        inventory.write_inventory(os.path.join(data_root, day), config)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, datetime.date.today().isoformat(), exc, "inventory refresh")


def finalize_day(day_str: str, data_root: str, retention_days: int,
                 config_path: str | None = None) -> None:
    """生成某天 report.md/report.csv；顺带做一次保留期清理。

    config_path 可选：守护进程以 --config 启动时透传给日报链；链内解析优先级为
    config_path > <root>/config.json > 全局默认（见 report._config_for_root）。
    """
    try:
        report.generate_day_report(day_str, data_root, config_path=config_path)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, day_str, exc, f"finalize report {day_str}")
    try:
        retention_cleanup(data_root, retention_days)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, day_str, exc, "retention cleanup")


# ---------------------------------------------------------------------------
# 会话采集
# ---------------------------------------------------------------------------
def _open_session(fg, config: dict, processes: dict, now: datetime.datetime) -> dict:
    """根据前台窗口信息构建会话候选（含分类/联系人/AI工具识别）。"""
    title = fg.title
    hidden = classifier.is_blacklisted_title(title, config)
    if hidden:
        title = "[已隐藏]"
    uwp_name = None
    try:
        uwp_name = win32core.get_uwp_app_name(fg.pid, config.get("uwp_app_names") or {})
    except Exception:  # noqa: BLE001 —— UWP 识别失败不影响主流程
        uwp_name = None
    app = uwp_name or classifier.resolve_app_name(fg.exe, config)
    category = classifier.classify_category(fg.exe, title, config)

    browser_category = None
    contact = None
    if not hidden:  # 标题已隐藏时不做基于标题的深度分类（隐私优先）
        if fg.exe in config.get("browser_exes", []):
            browser_category = classifier.classify_browser(title, config)
        if fg.exe in config.get("social_apps", {}):
            contact = classifier.extract_contact(fg.exe, title, config)

    ai_tool = None
    # 终端 / 编辑器集成终端：需要完整进程树才能识别里面跑的 AI CLI 工具
    if (fg.exe in config.get("terminal_exes", [])
            or fg.exe in config.get("editor_exes", [])):
        ai_tool = classifier.detect_ai_tool(fg.pid, processes, title, config)
    else:
        # 自有窗口的 AI 工具（ChatGPT/Cursor/Windsurf 桌面版等）：
        # 把前台进程自身交给识别器，保证 ai_tool 字段不遗漏
        ai_tool = classifier.detect_ai_tool(
            fg.pid,
            {fg.pid: types.SimpleNamespace(exe=fg.exe, ppid=0, pid=fg.pid)},
            title, config,
        )

    # 进程树/标题已识别出 AI 工具时，类别统一归为 AI编程（与终端场景口径一致）：
    # 例如 VS Code 集成终端里跑 opencode，窗口标题不含关键词，但 ai_tool 已命中。
    if ai_tool is not None and category != "AI编程":
        category = "AI编程"

    # 维度细化：窗口状态 / 二级子分类 / 终端 TUI 工具
    window_state = win32core.get_window_state(fg.hwnd)
    subcategory = browser_category if category == "浏览器" and browser_category else None
    if subcategory is None:
        subcategory = classifier.classify_subcategory(category, fg.exe, title, config)
    term_tool = None
    if ai_tool is None and (fg.exe in config.get("terminal_exes", [])
                            or fg.exe in config.get("editor_exes", [])):
        term_tool = classifier.detect_term_tool(title, config)

    return {
        "start": now,
        "exe": fg.exe,
        "app": app,
        "title": title,
        "category": category,
        "contact": contact,
        "ai_tool": ai_tool,
        "browser_category": browser_category,
        "subcategory": subcategory,
        "window_state": window_state,
        "term_tool": term_tool,
        "last_active": now,
        "signature": (fg.exe, title, category, contact, ai_tool, browser_category),
    }


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def run_daemon(config: dict, test_seconds: int | None = None, verbose: bool = False,
               config_path: str | None = None) -> list[dict]:
    """守护主循环。test_seconds 非空时跑 N 秒后返回本次写入的记录列表。

    仅在 (exe, 标题, 类别, 联系人, AI工具, 浏览器分类) 变化时写一条；静止零写入。
    """
    data_root = config.get("data_root") or paths.default_data_root()
    poll_interval = max(1, int(config.get("poll_interval_s", DEFAULT_POLL_INTERVAL)))
    idle_threshold = max(0, int(config.get("idle_threshold_s", DEFAULT_IDLE_THRESHOLD)))
    retention = max(0, int(config.get("retention_days", DEFAULT_RETENTION)))
    os.makedirs(data_root, exist_ok=True)

    session: dict | None = None
    current_day: str | None = None
    test_records: list[dict] = []
    start_mono = time.monotonic()

    while True:
        try:
            # 配置热重载：显式传入 config_path 时每轮重新读取（classifier.load_config
            # 带 mtime+TTL 缓存，成本可忽略）。data_root 保持首次启动值，避免中途切换
            # 数据目录造成数据分裂；分类/轮询等其余配置即时生效。
            # 未传 config_path（如测试直接调用 run_daemon）保持初始 config 行为。
            if config_path is not None and os.path.isfile(config_path):
                config = load_config(config_path)
                poll_interval = max(1, int(config.get("poll_interval_s", DEFAULT_POLL_INTERVAL)))
                idle_threshold = max(0, int(config.get("idle_threshold_s", DEFAULT_IDLE_THRESHOLD)))
                retention = max(0, int(config.get("retention_days", DEFAULT_RETENTION)))
            now = datetime.datetime.now()
            day_str = now.strftime("%Y-%m-%d")

            # 跨天 / 首次启动
            if current_day is None:
                current_day = day_str
                yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
                if not os.path.isfile(os.path.join(data_root, yesterday, "report.md")):
                    finalize_day(yesterday, data_root, retention, config_path)
                _refresh_inventory(data_root, config)  # 启动时刷新今日软件清单
                try:
                    import applog  # noqa: PLC0415
                    applog.configure(data_root)
                    applog.get_logger("monitor").info(
                        "守护进程启动 (data_root=%s, poll=%ss, idle=%ss)", data_root, poll_interval, idle_threshold)
                except Exception:  # noqa: BLE001
                    pass
            elif day_str != current_day:
                if session is not None:
                    rec = _close_session(session, session["last_active"], data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
                finalize_day(current_day, data_root, retention, config_path)
                _refresh_inventory(data_root, config)  # 跨天：新一天清单
                current_day = day_str
                try:
                    import applog  # noqa: PLC0415
                    applog.get_logger("monitor").info("跨天轮转 -> %s（前一日报表已生成）", day_str)
                except Exception:  # noqa: BLE001
                    pass

            # 暂停
            if _pause.is_set():
                if session is not None:
                    rec = _close_session(session, session["last_active"], data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
                # 暂停分支的 continue 会跳过循环尾部的退出检查，须在此先行补查，
                # 否则「先暂停再退出」时线程永远等不到停止信号（挂起）。
                # 此处 session 已关闭置 None，直接 break 安全。
                if test_seconds is not None and (time.monotonic() - start_mono) >= test_seconds:
                    break
                if stop_event.is_set():
                    break
                _pause.wait(poll_interval)
                continue

            idle_s = win32core.idle_seconds()

            # 空闲：在最后一次输入处截断会话
            if session is not None and idle_s >= idle_threshold:
                rec = _close_session(session, session["last_active"], data_root, current_day, config)
                if rec and test_seconds:
                    test_records.append(rec)
                session = None

            fg = win32core.get_foreground_info()
            if fg is None:
                if session is not None:
                    end = session["last_active"] if idle_s >= idle_threshold else now
                    rec = _close_session(session, end, data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
            else:
                processes: dict = {}
                # 终端 / 编辑器集成终端：需要进程树才能识别里面跑的 AI CLI 工具
                # （编辑器如 VS Code 的集成终端里跑 opencode 同样可识别）
                if (fg.exe in config.get("terminal_exes", [])
                        or fg.exe in config.get("editor_exes", [])):
                    processes = win32core.enum_processes()
                cand = _open_session(fg, config, processes, now)

                if session is not None and cand["signature"] != session["signature"]:
                    end = session["last_active"] if idle_s >= idle_threshold else now
                    rec = _close_session(session, end, data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    if verbose and rec:
                        print(f"[monitor] {rec['start']} {rec['app']} {rec['duration_ms']}ms")
                    session = None

                if session is None and idle_s < idle_threshold:
                    session = cand
                elif session is not None and idle_s < idle_threshold:
                    session["last_active"] = now

            if test_seconds is not None and (time.monotonic() - start_mono) >= test_seconds:
                break
            if stop_event.is_set():
                break
            # 应用内更新信号：dashboard「应用更新」时写入，优雅退出供更新脚本替换 exe
            # （该检查不搬入暂停分支：罕见路径，恢复后的下一轮自然处理，避免重复检查）
            if os.path.isfile(os.path.join(data_root, ".update-requested")):
                try:
                    import applog  # noqa: PLC0415
                    applog.get_logger("monitor").info("收到应用内更新请求，正在优雅退出")
                except Exception:  # noqa: BLE001
                    pass
                break
            time.sleep(poll_interval)

        except Exception as exc:  # noqa: BLE001 —— 单次轮询失败不中断守护
            _log_error(data_root, current_day or day_str, exc, "poll")
            time.sleep(poll_interval)

    if session is not None:
        rec = _close_session(session, session["last_active"], data_root, current_day, config)
        if rec and test_seconds:
            test_records.append(rec)
    return test_records


# ---------------------------------------------------------------------------
# 今日概览（托盘使用）
# ---------------------------------------------------------------------------
def overview_text(data_root: str | None = None) -> str:
    """生成"今日概览"文本：按应用聚合今天已记录的活跃时长。"""
    root = data_root or (load_config().get("data_root") or paths.default_data_root())
    today = datetime.date.today().isoformat()
    by_app: dict[str, int] = {}
    for s in report.read_sessions(today, root):
        dur = int(s.get("duration_ms") or 0)
        if not s.get("active", True):
            continue
        app = s.get("app") or s.get("exe") or "未知"
        by_app[app] = by_app.get(app, 0) + dur
    lines = [f"今日概览 {today}", f"总活跃：{sum(by_app.values()) // 60000} 分钟"]
    for app, ms in sorted(by_app.items(), key=lambda kv: -kv[1])[:8]:
        lines.append(f"  {app}  {ms // 60000} 分钟")
    if not by_app:
        lines.append("  （暂无数据）")
    return "\n".join(lines)


def _find_electron_shell() -> list[str] | None:
    """定位 Electron 桌面壳（独立应用窗口，替代默认浏览器）。

    返回启动命令列表（含 main.js 参数）或 None：
    1) electron-app/dist/*.exe        —— electron-builder 打包的便携版
    2) electron-app/node_modules/electron/dist/electron.exe + main.js —— dev 模式

    兼容源码运行与 PyInstaller 打包运行：打包时 `__file__` 在临时解压目录，
    不能作为项目根；改用 paths.script_dir()（exe 所在目录），并同时探测其父目录
    （exe 放在 dist/ 子目录时，项目根是父目录）与 USAGEMON_PROJECT_DIR。
    """
    roots: list[str] = []
    env_dir = os.environ.get("USAGEMON_PROJECT_DIR")
    if env_dir:
        roots.append(env_dir)
    script_dir = paths.script_dir()
    roots.append(script_dir)
    parent = os.path.dirname(script_dir)
    if parent and parent not in roots:
        roots.append(parent)

    for base in roots:
        app_dir = os.path.join(base, "electron-app")
        # 1) 打包便携版
        dist_dir = os.path.join(app_dir, "dist")
        if os.path.isdir(dist_dir):
            for name in sorted(os.listdir(dist_dir)):
                if name.lower().endswith(".exe"):
                    return [os.path.join(dist_dir, name)]
        # 2) dev 模式（npm install 后）
        electron_exe = os.path.join(app_dir, "node_modules", "electron", "dist", "electron.exe")
        main_js = os.path.join(app_dir, "main.js")
        if os.path.isfile(electron_exe) and os.path.isfile(main_js):
            return [electron_exe, main_js]
    return None


def open_dashboard(data_root: str, port: int = 8765, view: str | None = None,
                   params: dict | None = None) -> None:
    """打开本地仪表盘（幂等）。

    优先 Electron 桌面壳（独立应用窗口，不弹默认浏览器；壳内部自行
    探测/启动仪表盘服务）；无壳时回退：端口空闲则后台起服务 + 开浏览器。
    view 指定初始视图（overview / report / detail），为空用默认视图；
    params 附加查询参数（如 {"update": "1"} 让设置页自动检查更新）。
    """
    import socket
    import webbrowser

    url = f"http://127.0.0.1:{port}/"
    query = []
    if view:
        query.append(f"view={view}")
    if params:
        query.extend(f"{k}={v}" for k, v in params.items() if v is not None)
    if query:
        url += "?" + "&".join(query)

    # 优先 Electron 壳（USAGEMON_USE_BROWSER=1 可强制回退浏览器，调试用）
    if os.environ.get("USAGEMON_USE_BROWSER") != "1":
        shell = _find_electron_shell()
        if shell:
            try:
                env = dict(os.environ)
                # 某些环境（如自动化沙箱）会设置 ELECTRON_RUN_AS_NODE=1，
                # 会让 Electron 以 Node 模式运行而不是打开应用窗口；这里强制移除。
                env.pop("ELECTRON_RUN_AS_NODE", None)
                env["USAGEMON_DATA_ROOT"] = data_root
                env["USAGEMON_PORT"] = str(port)
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(shell, env=env, creationflags=creationflags,
                                 close_fds=True)
                return
            except Exception:  # noqa: BLE001 —— 壳启动失败回退浏览器
                pass

    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        # 已有 dashboard 实例在跑
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        sock.close()

    def _serve() -> None:
        try:
            import dashboard  # noqa: PLC0415 —— 惰性导入
            server = dashboard.create_server(data_root, port)
            server.serve_forever()
        except Exception as exc:  # noqa: BLE001
            _log_error(data_root, datetime.date.today().isoformat(), exc, "dashboard serve")

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(0.4)  # 等服务器绑定端口
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# exe 多工具分派：单文件 VibeTrace.exe 内同时提供 monitor / report / dashboard
# 三个子工具（PyInstaller 打包入口是 monitor.py，通过参数前缀分派到对应模块）。
_REPORT_FLAGS = {
    "--day", "--today", "--week", "--month", "--reclassify",
    "--json", "--write", "--full",
}
_DASHBOARD_FLAGS = {"--dashboard", "--port", "--open"}


def _dispatch(argv: list[str] | None) -> str | None:
    """检测参数是否属于 report / dashboard 子工具；命中返回子工具名。"""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        return None
    first = args_list[0]
    if first == "--report":
        return "report"
    if first == "--dashboard":
        return "dashboard"
    # 兼容直接传 report 专属参数（python monitor.py --today 也走 report）
    if first in _REPORT_FLAGS:
        return "report"
    if first in _DASHBOARD_FLAGS:
        return "dashboard"
    return None


def relaunch_as_admin() -> bool:
    """通过 ShellExecuteW(runas) 以管理员权限重启当前进程。"""
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(sys.argv[1:])
        else:
            exe = sys.executable
            params = f'"{os.path.abspath(__file__)}" ' + " ".join(sys.argv[1:])
        res = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, None, 1
        )
        return int(res) > 32
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    sub = _dispatch(argv)
    if sub == "report":
        args_list = list(argv) if argv is not None else sys.argv[1:]
        if args_list and args_list[0] == "--report":
            args_list = args_list[1:]
        return report.main(args_list)
    if sub == "dashboard":
        args_list = list(argv) if argv is not None else sys.argv[1:]
        if args_list and args_list[0] == "--dashboard":
            args_list = args_list[1:]
        import dashboard  # noqa: PLC0415
        return dashboard.main(args_list)

    parser = argparse.ArgumentParser(prog="monitor.py", description="电脑使用情况监控守护进程")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--test", type=int, metavar="N", help="测试模式：运行 N 秒后退出并打印汇总")
    parser.add_argument("--tray", action="store_true", help="启用托盘图标（不可用时降级为静默守护）")
    parser.add_argument("--foreground", action="store_true", help="前台模式：把写入记录打印到控制台")
    parser.add_argument("--admin", action="store_true",
                        help="以管理员权限运行（非管理员时请求 UAC 提权重启）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args(argv)

    if args.admin and not win32core.is_admin():
        print("需要管理员权限，正在请求 UAC 提权重启...", file=sys.stderr)
        if relaunch_as_admin():
            return 0
        print("提权启动失败，请手动以管理员身份运行", file=sys.stderr)
        return 1

    config = load_config(args.config)
    if args.data_root:
        config["data_root"] = args.data_root
    data_root = config.get("data_root") or paths.default_data_root()
    os.makedirs(data_root, exist_ok=True)
    # 配置热重载来源：显式 --config 或默认路径（文件存在才生效；缺失时保持初始 config）
    hot_reload_path = args.config or os.path.join(paths.default_data_root(), "config.json")

    # 单实例保护：守护模式（非 --test）下已有实例在运行则直接退出，
    # 避免多个 monitor 同时写 usage.jsonl 造成重复记录。
    if not args.test and not win32core.acquire_single_instance("UsageMonitorMutex"):
        _log_error(data_root, datetime.date.today().isoformat(),
                   RuntimeError("another instance is running"), "single-instance")
        return 0

    if args.test:
        records = run_daemon(config, test_seconds=max(1, args.test), verbose=args.foreground,
                             config_path=hot_reload_path)
        print(f"--test 结束：本次运行写入 {len(records)} 条会话记录")
        by_app: dict[str, int] = {}
        for r in records:
            app = r.get("app") or r.get("exe") or "未知"
            by_app[app] = by_app.get(app, 0) + r["duration_ms"]
        for app, ms in sorted(by_app.items(), key=lambda kv: -kv[1]):
            print(f"  {app}: {ms // 1000}s")
        return 0

    # 无参数（双击 exe / 默认运行）时自动启用托盘：桌面环境不可用时降级静默守护。
    # 显式 --foreground 保留纯控制台行为。
    use_tray = args.tray or (not args.foreground)
    if use_tray:
        try:
            import tray  # noqa: PLC0415 —— 惰性导入，托盘不可用时降级
            thread = threading.Thread(
                target=run_daemon, args=(config,), kwargs={"config_path": hot_reload_path}, daemon=True
            )
            thread.start()
            # 日报生成托盘通知调度：独立守护线程，每 60 秒检测 report.md
            threading.Thread(
                target=_run_balloon_scheduler, args=(data_root,), daemon=True
            ).start()
            # 告警调度（v2.7 行动闭环）：预算接近/超支 + 连续工作休息提醒
            threading.Thread(
                target=_run_alerts_scheduler, args=(data_root, hot_reload_path), daemon=True
            ).start()
            # 启动后延迟检查新版本（有更新时托盘气泡提示）
            threading.Thread(
                target=_run_update_checker, args=(data_root, config), daemon=True
            ).start()
            tray.run(
                config,
                overview_fn=lambda: overview_text(data_root),
                set_paused_fn=set_paused,
                is_paused_fn=is_paused,
                open_dashboard_fn=lambda view=None: open_dashboard(data_root, view=view),
                check_update_fn=lambda: open_dashboard(
                    data_root, view="settings", params={"update": "1"}),
                stop_event=stop_event,
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            _log_error(data_root, datetime.date.today().isoformat(), exc, "tray init (degraded)")

    run_daemon(config, verbose=args.foreground, config_path=hot_reload_path)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
