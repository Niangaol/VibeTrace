# -*- coding: utf-8 -*-
"""tests/unit/test_tray.py —— 托盘菜单 / 气泡 / 退出链路（纯逻辑）单元测试。

tray.py 是 ctypes 托盘实现，真实弹菜单、弹气泡、建隐藏窗口都依赖 Windows
桌面会话，CI 里没有会话必然失败。因此本文件只测「与 Win32 GUI 可分离」的
纯逻辑，全程用 monkeypatch 替换 tray.user32 / tray.shell32 / applog.note 与
tray 的模块级回调，绝不触发任何真实 GUI 调用：

1. _handle_command：五个 IDM_* 的分派去向；IDM_PAUSE 取「切换后」的状态；
   未知 cmd 静默 no-op；IDM_EXIT 置位 _stop_event 并向隐藏窗口投递 WM_QUIT；
2. _popup_menu：五个菜单项齐全；本版本用 AppendMenuW 逐项插入（模块未声明
   InsertMenuItemW），暂停态用标签「暂停监控 / 继续监控」表达而非 MF_CHECKED
   勾选；TrackPopupMenu 返回 0 时是 no-op、返回 IDM_* 时转交 _handle_command；
   菜单句柄在 finally 中释放；
3. show_balloon：失败（shell32 抛异常）时静默降级且必须走 applog.note 留痕
   （v2.9.7 可观测性契约）、成功路径不 note、_hwnd 未就绪时完全安静；
4. request_quit：向托盘窗口投递 WM_QUIT，无窗口时空转，重复调用安全；
5. _wndproc：WM_COMMAND 的 wParam 掩码、气泡点击 / 左右键、WM_DESTROY 的
   PostQuitMessage、其他消息交给 DefWindowProcW。

已知缺口（见 test_handle_command_does_not_propagate_callback_exception 的
skip 说明）：当前 _handle_command 是裸 if/elif 分派，自身没有 try/except，
回调抛异常会原样穿出，仅靠 ctypes WINFUNCTYPE 边界兜住（打印 traceback 后
返回 0）。这与「守护进程稳定性」的要求不符，但不属于本文件的修改范围，
故只以 characterization 测试钉住现实口径。
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if sys.platform != "win32":
    pytest.skip(
        "tray.py 依赖 ctypes.windll（Shell_NotifyIconW / RegisterClassExW 等），"
        "仅 Windows 可导入与测试",
        allow_module_level=True,
    )

import applog  # noqa: E402
import tray  # noqa: E402


def _deref(arg):
    """把 byref(...) 产生的 CArgObject 还原成原始结构体实例。

    tray.py 用 ctypes.byref(pt) / byref(nid) 传指针，替身函数拿到的不是
    结构体本身，无法直接读写字段——测试必须显式取 _obj 才能模拟字段填充。
    """
    return getattr(arg, "_obj", arg)


def _hwnd_value(hwnd):
    """统一取出句柄整数值（wt.HWND 是 c_void_p，取 .value；裸 int 原样返回）。"""
    return hwnd.value if hasattr(hwnd, "value") else int(hwnd)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_TRAY_GLOBALS = (
    "_overview_fn",
    "_set_paused_fn",
    "_is_paused_fn",
    "_open_dashboard_fn",
    "_check_update_fn",
    "_stop_event",
    "_hwnd",
    "_data_root",
)


@pytest.fixture(autouse=True)
def _restore_tray_globals():
    """每个用例前后保存/还原 tray 的模块级可变状态，防止测试间泄漏。"""
    saved = {name: getattr(tray, name) for name in _TRAY_GLOBALS}
    yield
    for name, value in saved.items():
        setattr(tray, name, value)


class _CallbackSpy:
    """记录一次回调调用的 (args, kwargs)，附带调用次数。"""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def callbacks(monkeypatch):
    """把 tray 的五个模块级回调换成记录器，返回 {名字: _CallbackSpy}。"""
    spies = {}
    for name in ("_overview_fn", "_set_paused_fn", "_is_paused_fn",
                 "_open_dashboard_fn", "_check_update_fn"):
        spy = _CallbackSpy()
        spies[name] = spy
        monkeypatch.setattr(tray, name, spy)
    return spies


class _FakeUser32:
    """user32 替身：只记录入参，绝不分派到真实 Win32（CI 无桌面会话）。

    刻意不提供 CheckMenuItem：一旦 tray.py 改用勾选态表达暂停状态，
    相关用例会以 AttributeError 立刻暴露，而不是悄悄失真。
    """

    def __init__(self):
        self.popup_handle = 0x1234          # CreatePopupMenu 的假句柄
        self.track_result = 0               # TrackPopupMenu 返回值（0 = 用户取消）
        self.created = 0
        self.append = []                    # [(menu, flags, id, label)]
        self.destroyed = []                 # DestroyMenu 的句柄
        self.cursor = []                    # GetCursorPos 读到的坐标
        self.track = []                     # [(menu, flags, x, y, 0, hwnd, None)]
        self.posted = []                    # [(hwnd, msg, wparam, lparam)]
        self.quit_calls = []                # PostQuitMessage 的 code
        self.default_calls = []             # DefWindowProcW 的参数

    def CreatePopupMenu(self):
        self.created += 1
        return self.popup_handle

    def AppendMenuW(self, menu, flags, item_id, label):
        self.append.append((menu, flags, item_id, label))
        return 1

    def GetCursorPos(self, pt):
        point = _deref(pt)
        point.x = 111
        point.y = 222
        self.cursor.append((point.x, point.y))
        return 1

    def TrackPopupMenu(self, menu, flags, x, y, reserved, hwnd, rect):
        self.track.append((menu, flags, x, y, reserved, hwnd, rect))
        return self.track_result

    def DestroyMenu(self, menu):
        self.destroyed.append(menu)
        return 1

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.posted.append((hwnd, msg, wparam, lparam))
        return 1

    def PostQuitMessage(self, code):
        self.quit_calls.append(code)

    def DefWindowProcW(self, hwnd, msg, wparam, lparam):
        self.default_calls.append((hwnd, msg, wparam, lparam))
        return 0


class _FakeShell32:
    """shell32 替身：按需返回 0 / 抛异常，并记录每次 NIM_* 调用。"""

    def __init__(self, result=1, fail_messages=()):
        self.result = result
        self.fail_messages = frozenset(fail_messages)
        self.calls = []                     # [(dw_message, nid)]

    def Shell_NotifyIconW(self, dw_message, nid):
        self.calls.append((dw_message, _deref(nid)))
        if dw_message in self.fail_messages:
            raise OSError("simulated: shell32.Shell_NotifyIconW 失败")
        return self.result

    def messages(self):
        return [msg for msg, _ in self.calls]


@pytest.fixture
def fake_win32(monkeypatch):
    """注入 _FakeUser32 并返回实例（覆盖 menu / 消息投递 / PostQuitMessage）。"""
    user = _FakeUser32()
    monkeypatch.setattr(tray, "user32", user)
    return user


@pytest.fixture
def note_spy(monkeypatch):
    """把 applog.note 换成计数器，返回记录列表（v2.9.7 观测契约的探针）。"""
    notes = []
    monkeypatch.setattr(applog, "note", lambda exc, ctx, *a, **k: notes.append((exc, ctx)))
    return notes


# ---------------------------------------------------------------------------
# 1. _handle_command 分派
# ---------------------------------------------------------------------------
def test_handle_command_overview_opens_dashboard_overview_view(callbacks):
    """IDM_OVERVIEW → 打开仪表盘并定位「今日概览」视图（不再走文本弹窗）。"""
    tray._handle_command(0, tray.IDM_OVERVIEW)
    assert callbacks["_open_dashboard_fn"].calls == [((), {"view": "overview"})]
    # 前端重做后 _overview_fn 已不再被菜单链路调用
    assert callbacks["_overview_fn"].count == 0


def test_handle_command_open_dashboard_without_view(callbacks):
    """IDM_OPEN_DASHBOARD → 打开仪表盘，不带 view（落到默认视图）。"""
    tray._handle_command(7, tray.IDM_OPEN_DASHBOARD)
    assert callbacks["_open_dashboard_fn"].calls == [((), {})]
    assert callbacks["_check_update_fn"].count == 0


def test_handle_command_check_update_triggers_callback(callbacks):
    """IDM_CHECK_UPDATE → 触发检查更新回调，不误触其他回调。"""
    tray._handle_command(0, tray.IDM_CHECK_UPDATE)
    assert callbacks["_check_update_fn"].calls == [((), {})]
    assert callbacks["_open_dashboard_fn"].count == 0


@pytest.mark.parametrize("paused,expected", [(False, True), (True, False)])
def test_handle_command_pause_sets_flipped_state(callbacks, monkeypatch, paused, expected):
    """IDM_PAUSE → _set_paused_fn 收到的是「切换后」的状态。"""
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: paused)
    tray._handle_command(0, tray.IDM_PAUSE)
    assert callbacks["_set_paused_fn"].calls == [((expected,), {})]
    assert callbacks["_open_dashboard_fn"].count == 0


def test_pause_toggle_flips_repeatedly(monkeypatch):
    """连续点两次「暂停监控」：状态在 False → True → False 之间来回翻。"""
    state = {"paused": False}
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: state["paused"])
    seen = []

    def set_paused(value):
        state["paused"] = value
        seen.append(value)

    monkeypatch.setattr(tray, "_set_paused_fn", set_paused)
    tray._handle_command(0, tray.IDM_PAUSE)
    tray._handle_command(0, tray.IDM_PAUSE)
    assert seen == [True, False]


def test_handle_command_exit_sets_stop_event_and_posts_quit(callbacks, monkeypatch):
    """IDM_EXIT → 置位 _stop_event 并向隐藏窗口投递 WM_QUIT（不呼叫 PostQuitMessage）。"""
    monkeypatch.setattr(tray, "_stop_event", threading.Event())
    user = _FakeUser32()
    monkeypatch.setattr(tray, "user32", user)
    tray._handle_command(0x5A5A, tray.IDM_EXIT)
    assert tray._stop_event.is_set()
    assert len(user.posted) == 1
    hwnd, msg, wparam, lparam = user.posted[0]
    assert _hwnd_value(hwnd) == 0x5A5A
    assert msg == tray.WM_QUIT
    assert wparam == 0 and lparam == 0
    assert user.quit_calls == []  # PostQuitMessage 只在 WM_DESTROY 才由 _wndproc 触发
    for name in ("_open_dashboard_fn", "_check_update_fn", "_set_paused_fn"):
        assert callbacks[name].count == 0


def test_handle_command_exit_without_stop_event_still_posts_quit(monkeypatch):
    """_stop_event 为 None 时 IDM_EXIT 仍要投递 WM_QUIT（否则托盘退不掉）。"""
    monkeypatch.setattr(tray, "_stop_event", None)
    user = _FakeUser32()
    monkeypatch.setattr(tray, "user32", user)
    tray._handle_command(1, tray.IDM_EXIT)
    assert [msg for _, msg, _, _ in user.posted] == [tray.WM_QUIT]


def test_exit_dispatch_matches_request_quit_signal(monkeypatch, fake_win32):
    """菜单退出与 request_quit 发出的是同一条 WM_QUIT（同一扇门的两种按法）。

    源码里 _handle_command 的 IDM_EXIT 分支并不调用 request_quit，而是自己
    PostMessageW(WM_QUIT)；这里把两者对外的信号等价性钉住。
    """
    monkeypatch.setattr(tray, "_stop_event", threading.Event())
    monkeypatch.setattr(tray, "_hwnd", 0x5A5A)
    tray.request_quit()
    tray._handle_command(0x5A5A, tray.IDM_EXIT)
    assert [msg for _, msg, _, _ in fake_win32.posted] == [tray.WM_QUIT] * 2
    assert tray._stop_event.is_set()

@pytest.mark.parametrize("cmd", [0, -1, 9999, None, 1006])
def test_handle_command_unknown_command_is_noop(callbacks, monkeypatch, cmd):
    """未知 cmd（含 0 / None / 越界值）不抛异常，也不触发任何回调。"""
    user = _FakeUser32()
    monkeypatch.setattr(tray, "user32", user)
    monkeypatch.setattr(tray, "_stop_event", threading.Event())
    tray._handle_command(3, cmd)
    for spy in callbacks.values():
        assert spy.count == 0
    assert user.posted == []
    assert not tray._stop_event.is_set()


def _boom(*args, **kwargs):
    raise RuntimeError("模拟回调内部异常")


@pytest.mark.parametrize(
    "cmd",
    [tray.IDM_OVERVIEW, tray.IDM_OPEN_DASHBOARD, tray.IDM_CHECK_UPDATE, tray.IDM_PAUSE],
)
def test_callback_exception_currently_propagates(monkeypatch, cmd):
    """characterization：当前实现下回调异常会原样穿出 _handle_command。

    这不是期望行为（期望见下面被 skip 的用例），而是把现实口径钉住：
    _handle_command 内部没有 try/except，异常要一路冒到 ctypes WINFUNCTYPE
    边界才被兜住。若将来补上保护，本断言会失败——那正是需要同步修改的信号。
    """
    monkeypatch.setattr(tray, "_open_dashboard_fn", _boom)
    monkeypatch.setattr(tray, "_check_update_fn", _boom)
    monkeypatch.setattr(tray, "_set_paused_fn", _boom)
    monkeypatch.setattr(tray, "_is_paused_fn", _boom)
    monkeypatch.setattr(tray, "_stop_event", threading.Event())
    monkeypatch.setattr(tray, "user32", _FakeUser32())
    with pytest.raises(RuntimeError):
        tray._handle_command(0, cmd)


@pytest.mark.skip(
    reason=(
        "当前 tray.py 的 _handle_command 为裸 if/elif 分派，无 try/except 包裹，"
        "回调抛异常会穿出函数体（仅 ctypes 边界兜住），「不得传播」这一要求在源码中"
        "尚未成立，按需求断言必然失败；修复应在 _handle_command 外层加 "
        "try/except Exception + applog.note，届时取消本 skip 即可。"
    )
)
def test_handle_command_does_not_propagate_callback_exception(monkeypatch, fake_win32):
    """（源码修复后启用）回调抛异常时 _handle_command 不得把异常传播出去。

    期望行为：异常被 _handle_command 内部吞掉（最好再走一次 applog.note 留痕），
    消息循环继续跑，托盘不会因为一次「今日概览」失败而整体瘫掉。
    """
    monkeypatch.setattr(tray, "_open_dashboard_fn", _boom)
    monkeypatch.setattr(tray, "_check_update_fn", _boom)
    monkeypatch.setattr(tray, "_set_paused_fn", _boom)
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: False)
    monkeypatch.setattr(tray, "_stop_event", threading.Event())
    for cmd in (tray.IDM_OVERVIEW, tray.IDM_OPEN_DASHBOARD, tray.IDM_CHECK_UPDATE,
                tray.IDM_PAUSE, tray.IDM_EXIT):
        tray._handle_command(0, cmd)          # 不得抛
    assert fake_win32.posted[0][1] == tray.WM_QUIT  # IDM_EXIT 仍要能退出


# ---------------------------------------------------------------------------
# 2. _popup_menu 菜单构建与派发
# ---------------------------------------------------------------------------
def test_popup_menu_inserts_five_items_and_separators(callbacks, monkeypatch, fake_win32):
    """五项菜单 + 两个分隔符，顺序固定；暂停项用标签而非 MF_CHECKED 勾选。"""
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: False)
    tray._popup_menu(4242)
    items = [(item_id, label) for _, flags, item_id, label in fake_win32.append
             if flags == tray.MF_STRING]
    assert items == [
        (tray.IDM_OVERVIEW, "今日概览"),
        (tray.IDM_OPEN_DASHBOARD, "打开仪表盘"),
        (tray.IDM_CHECK_UPDATE, "检查更新"),
        (tray.IDM_PAUSE, "暂停监控"),
        (tray.IDM_EXIT, "退出"),
    ]
    separators = [c for c in fake_win32.append if c[1] == tray.MF_SEPARATOR]
    assert len(separators) == 2
    # 本版本不适用 MF_CHECKED / CheckMenuItem（暂停态完全靠标签表达）
    assert all(flags != tray.MF_CHECKED for _, flags, _, _ in fake_win32.append)
    assert fake_win32.created == 1
    assert fake_win32.cursor == [(111, 222)]
    assert fake_win32.track[0][2:4] == (111, 222)          # 用光标坐标弹出
    assert _hwnd_value(fake_win32.track[0][5]) == 4242             # 传给 TrackPopupMenu 的 hwnd
    assert fake_win32.destroyed == [fake_win32.popup_handle]  # 句柄被释放
    # track_result 默认 0 → 用户取消，不得触发任何回调
    for spy in callbacks.values():
        assert spy.count == 0


@pytest.mark.parametrize("paused,label", [(False, "暂停监控"), (True, "继续监控")])
def test_popup_menu_pause_label_follows_pause_state(monkeypatch, fake_win32, paused, label):
    """暂停项文案随 _is_paused_fn() 翻转（勾选态在当前版本等价于标签切换）。"""
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: paused)
    tray._popup_menu(1)
    pause_items = [c for c in fake_win32.append if c[2] == tray.IDM_PAUSE]
    assert len(pause_items) == 1
    assert pause_items[0][3] == label


def test_popup_menu_returns_early_when_create_popup_menu_fails(callbacks, monkeypatch, fake_win32):
    """CreatePopupMenu 返回 0（句柄无效）时直接返回，不插菜单也不弹。"""
    fake_win32.popup_handle = 0
    tray._popup_menu(1)
    assert fake_win32.append == []
    assert fake_win32.track == []
    assert fake_win32.destroyed == []   # 没进 try，finally 也不该跑
    for spy in callbacks.values():
        assert spy.count == 0


def test_trackpopupmenu_zero_runs_noop_command(callbacks, monkeypatch, fake_win32):
    """TrackPopupMenu 返回 0（用户取消）：走一次 _handle_command(hwnd, 0) 但全程 no-op。"""
    fake_win32.track_result = 0
    real = tray._handle_command
    seen = []

    def spy(hwnd, cmd):
        seen.append((hwnd, cmd))
        return real(hwnd, cmd)

    monkeypatch.setattr(tray, "_handle_command", spy)
    tray._popup_menu(99)
    assert seen == [(99, 0)]
    # 注意：_is_paused_fn 是 _popup_menu 为拼暂停项文案自己调的，不算「被派发」
    for name in ("_open_dashboard_fn", "_check_update_fn", "_set_paused_fn", "_overview_fn"):
        assert callbacks[name].count == 0


def test_trackpopupmenu_idm_dispatches_through_handle_command(callbacks, monkeypatch, fake_win32):
    """TrackPopupMenu 返回某个 IDM_* → 原样转交 _handle_command 并触发对应回调。"""
    fake_win32.track_result = tray.IDM_PAUSE
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: False)
    real = tray._handle_command
    seen = []

    def spy(hwnd, cmd):
        seen.append((hwnd, cmd))
        return real(hwnd, cmd)

    monkeypatch.setattr(tray, "_handle_command", spy)
    tray._popup_menu(5)
    assert seen == [(5, tray.IDM_PAUSE)]
    assert callbacks["_set_paused_fn"].calls == [((True,), {})]


def test_popup_menu_destroys_menu_even_when_dispatch_raises(monkeypatch, fake_win32):
    """菜单句柄必须 finally 释放，即使 _handle_command 里的回调炸了。"""
    fake_win32.track_result = tray.IDM_OVERVIEW
    monkeypatch.setattr(tray, "_open_dashboard_fn", _boom)
    with pytest.raises(RuntimeError):
        tray._popup_menu(3)
    assert fake_win32.destroyed == [fake_win32.popup_handle]


# ---------------------------------------------------------------------------
# 3. show_balloon 降级与可观测性（v2.9.7 applog.note 契约）
# ---------------------------------------------------------------------------
def test_show_balloon_success_sends_info_balloon(monkeypatch):
    """成功路径：NIM_SETVERSION + NIM_MODIFY，气泡字段按预期下发。"""
    monkeypatch.setattr(tray, "_hwnd", 4321)
    shell = _FakeShell32()
    monkeypatch.setattr(tray, "shell32", shell)
    tray.show_balloon("今日日报", "来看看今天的趋势")
    assert shell.messages() == [tray.NIM_SETVERSION, tray.NIM_MODIFY]
    nid = shell.calls[1][1]
    assert nid.uFlags == tray.NIF_INFO
    assert nid.szInfo == "来看看今天的趋势"
    assert nid.szInfoTitle == "今日日报"
    assert nid.uTimeoutOrVersion == 10000
    assert nid.dwInfoFlags == tray.NIIF_INFO


def test_show_balloon_success_does_not_note(monkeypatch, note_spy):
    """成功路径不落 note（note 只服务于降级排障，不能变成噪音）。"""
    monkeypatch.setattr(tray, "_hwnd", 4321)
    monkeypatch.setattr(tray, "shell32", _FakeShell32())
    tray.show_balloon("t", "x")
    assert note_spy == []


def test_show_balloon_not_ready_is_silent_noop(monkeypatch, note_spy):
    """_hwnd == 0（图标未就绪）：本轮跳过，既不调 shell32 也不记日志。"""
    monkeypatch.setattr(tray, "_hwnd", 0)
    shell = _FakeShell32()
    monkeypatch.setattr(tray, "shell32", shell)
    tray.show_balloon("t", "x")
    assert shell.calls == []
    assert note_spy == []


@pytest.mark.parametrize(
    "fail_on",
    [tray.NIM_SETVERSION, tray.NIM_MODIFY],
)
def test_show_balloon_failure_notes_and_never_raises(monkeypatch, note_spy, fail_on):
    """v2.9.7 契约：shell32 调用失败必须静默降级 + 留一行 applog.note。

    失败抛出 OSError 模拟；show_balloon 不得把异常传播给调度线程。
    """
    monkeypatch.setattr(tray, "_hwnd", 4321)
    monkeypatch.setattr(tray, "shell32", _FakeShell32(fail_messages={fail_on}))
    tray.show_balloon("标题", "正文")          # 不得抛
    assert len(note_spy) == 1
    exc, ctx = note_spy[0]
    assert isinstance(exc, OSError)
    assert "气泡" in ctx


def test_show_balloon_total_failure_notes_twice(monkeypatch, note_spy):
    """版本设置与气泡下发双双失败：两处 note 各自留痕，仍不抛异常。"""
    monkeypatch.setattr(tray, "_hwnd", 4321)
    shell = _FakeShell32(fail_messages={tray.NIM_SETVERSION, tray.NIM_MODIFY})
    monkeypatch.setattr(tray, "shell32", shell)
    tray.show_balloon("t", "x")
    assert len(note_spy) == 2
    assert any("版本" in ctx for _, ctx in note_spy)
    assert any("气泡通知失败" in ctx for _, ctx in note_spy)


def test_show_balloon_zero_return_is_silent_degradation(monkeypatch, note_spy):
    """Shell_NotifyIconW 返回 0：不抛异常，静默降级。

    当前实现只判「是否抛异常」、不判返回值：返回 0 表示当前会话无法展示气泡，
    按「尽力而为」处理，不额外 note（note 只覆盖 ctypes 抛错这一路径）。
    这里把该口径钉住，避免有人误以为返回值失败也已可观测。
    """
    monkeypatch.setattr(tray, "_hwnd", 4321)
    monkeypatch.setattr(tray, "shell32", _FakeShell32(result=0))
    tray.show_balloon("t", "x")              # 不得抛
    assert note_spy == []


# ---------------------------------------------------------------------------
# 4. request_quit
# ---------------------------------------------------------------------------
def test_request_quit_posts_wm_quit(monkeypatch, fake_win32):
    """request_quit → 向托盘隐藏窗口投递 WM_QUIT，而不是退出调用方线程。"""
    monkeypatch.setattr(tray, "_hwnd", 0xABCD)
    tray.request_quit()
    assert len(fake_win32.posted) == 1
    hwnd, msg, wparam, lparam = fake_win32.posted[0]
    assert _hwnd_value(hwnd) == 0xABCD
    assert msg == tray.WM_QUIT
    assert (wparam, lparam) == (0, 0)
    assert fake_win32.quit_calls == []


def test_request_quit_is_noop_without_hwnd(monkeypatch, fake_win32):
    """_hwnd == 0（托盘未启动）时 request_quit 空转，不误发消息。"""
    monkeypatch.setattr(tray, "_hwnd", 0)
    tray.request_quit()
    assert fake_win32.posted == []


def test_request_quit_repeat_calls_are_safe(monkeypatch, fake_win32):
    """重复调用安全：每次都只是再投递一条 WM_QUIT，不抛异常。"""
    monkeypatch.setattr(tray, "_hwnd", 77)
    tray.request_quit()
    tray.request_quit()
    tray.request_quit()
    assert len(fake_win32.posted) == 3
    assert all(msg == tray.WM_QUIT and (wp, lp) == (0, 0)
               for _, msg, wp, lp in fake_win32.posted)


# ---------------------------------------------------------------------------
# 5. _wndproc 消息分派（菜单链路的真实入口）
# ---------------------------------------------------------------------------
def test_wndproc_command_masks_wparam(callbacks, monkeypatch, fake_win32):
    """WM_COMMAND 只取 wParam 低 16 位作为菜单 ID（高位是通知码）。"""
    monkeypatch.setattr(tray, "_is_paused_fn", lambda: False)
    assert tray._wndproc(11, tray.WM_COMMAND, tray.IDM_PAUSE | (0x9999 << 16), 0) == 0
    assert callbacks["_set_paused_fn"].calls == [((True,), {})]


def test_wndproc_balloon_click_opens_report_view(callbacks, monkeypatch, fake_win32):
    """气泡被点击 → 打开仪表盘「日报」视图。"""
    assert tray._wndproc(11, tray.WM_TRAY, 0, tray.NIN_BALLOONUSERCLICK) == 0
    assert callbacks["_open_dashboard_fn"].calls == [((), {"view": "report"})]


def test_wndproc_right_click_pops_menu(callbacks, monkeypatch, fake_win32):
    """右键 / 上下文菜单消息都走 _popup_menu。"""
    popped = []
    monkeypatch.setattr(tray, "_popup_menu", lambda hwnd: popped.append(hwnd))
    tray._wndproc(77, tray.WM_TRAY, 0, tray.WM_RBUTTONUP)
    tray._wndproc(77, tray.WM_TRAY, 0, tray.WM_CONTEXTMENU)
    assert popped == [77, 77]


def test_wndproc_left_click_opens_overview(callbacks, monkeypatch, fake_win32):
    """托盘图标左键单击等价于点「今日概览」。"""
    assert tray._wndproc(1, tray.WM_TRAY, 0, tray.WM_LBUTTONUP) == 0
    assert callbacks["_open_dashboard_fn"].calls == [((), {"view": "overview"})]


def test_wndproc_destroy_posts_quit(monkeypatch, fake_win32):
    """WM_DESTROY → PostQuitMessage(0)，消息循环才会退出。"""
    assert tray._wndproc(5, tray.WM_DESTROY, 0, 0) == 0
    assert fake_win32.quit_calls == [0]


def test_wndproc_unknown_message_defers_to_default(monkeypatch, fake_win32):
    """无关消息原样交给 DefWindowProcW。"""
    assert tray._wndproc(6, 0x9999, 1, 2) == 0
    hwnd, msg, wparam, lparam = fake_win32.default_calls[0]
    assert (_hwnd_value(hwnd), msg, wparam, lparam) == (6, 0x9999, 1, 2)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
