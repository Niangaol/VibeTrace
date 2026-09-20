# -*- coding: utf-8 -*-
"""tray.py — 托盘图标（纯 ctypes 实现，零第三方依赖；尽力而为，失败由 monitor 降级）。

功能：
- 悬停提示"电脑使用监控"
- 右键菜单：今日概览 / 打开今日日报 / 暂停·继续 / 退出
- 日报生成后气泡提示（show_balloon），点击气泡打开仪表盘日报视图
- 通过 Shell_NotifyIconW + 隐藏消息窗口实现

monitor.py 在 --tray 时惰性导入本模块；任何初始化失败都会抛异常，
由 monitor 捕获后降级为静默守护（守护功能不受影响）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import applog  # noqa: E402
import paths  # noqa: E402

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# ---- 常量 ----
NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
NIF_INFO = 0x10
NIM_SETVERSION = 0x4
NOTIFYICON_VERSION = 3      # 经典版本号：启用气泡事件回调
NIN_BALLOONUSERCLICK = 0x405  # WM_USER + 5：用户点击气泡
NIIF_INFO = 0x1

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_CONTEXTMENU = 0x007B
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_TRAY = WM_APP + 1  # 托盘回调消息

MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

ID_TRAY = 1
IDM_OVERVIEW = 1001
IDM_OPEN_DASHBOARD = 1002
IDM_PAUSE = 1003
IDM_EXIT = 1004
IDM_CHECK_UPDATE = 1005

IDI_APPLICATION = 32512
MB_OK = 0x0000
CW_USEDEFAULT = 0x80000000

TRAY_CLASS = "VibeTraceTrayWnd"
TRAY_TIP = "电脑使用监控"

_hwnd: int = 0  # 隐藏窗口句柄（供测试/消息投递）


# ---- 结构体 ----
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HANDLE),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HANDLE),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HANDLE),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", wt.POINT),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
LRESULT = ctypes.c_long

user32.RegisterClassExW.restype = wt.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.LoadIconW.restype = wt.HANDLE
user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.CreatePopupMenu.restype = wt.HANDLE
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.restype = wt.BOOL
user32.AppendMenuW.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.restype = wt.UINT
user32.TrackPopupMenu.argtypes = [
    wt.HANDLE, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, ctypes.c_void_p,
]
user32.GetCursorPos.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.PostMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DestroyWindow.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.MessageBoxW.restype = ctypes.c_int
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.CheckMenuItem.restype = wt.DWORD
user32.CheckMenuItem.argtypes = [wt.HANDLE, wt.UINT, wt.UINT]
user32.DestroyMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HANDLE]
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

# 状态（由 run() 注入）
def _overview_fn() -> str:
    return "今日概览"


def _set_paused_fn(paused: bool) -> None:
    return None


def _is_paused_fn() -> bool:
    return False


def _open_dashboard_fn(view=None) -> None:
    return None


def _check_update_fn() -> None:
    return None


_stop_event = None
_data_root = paths.default_data_root()


# ---- 托盘图标 ----
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]


def _tray_icon_handle():
    """优先加载 assets/tray.ico（打包 exe 内 / 脚本目录），失败回退系统图标。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    for rel in (os.path.join("assets", "tray.ico"), "tray.ico"):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            h = user32.LoadImageW(None, p, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if h:
                return h
    return user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wt.LPCWSTR))


def _add_icon(hwnd: int) -> None:
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = wt.HWND(hwnd)
    nid.uID = ID_TRAY
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = _tray_icon_handle()
    nid.szTip = TRAY_TIP
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        raise RuntimeError("Shell_NotifyIconW(NIM_ADD) failed — 当前会话可能无托盘")


def _delete_icon(hwnd: int) -> None:
    try:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wt.HWND(hwnd)
        nid.uID = ID_TRAY
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    except Exception as _exc:  # noqa: BLE001
        applog.note(_exc, "tray: 删除托盘图标失败（可能残留，下次创建会覆盖）")


def show_balloon(title: str, text: str) -> None:
    """托盘气泡提示（尽力而为；图标未就绪/平台不支持时静默降级）。

    复用现有隐藏窗口句柄 _hwnd 与 NOTIFYICONDATAW 结构，通过 NIM_MODIFY +
    NIF_INFO 下发气泡标题/正文；展示前先 NIM_SETVERSION 启用气泡点击事件，
    使 NIN_BALLOONUSERCLICK 能送达 _wndproc（点击打开日报视图）。
    Shell_NotifyIcon 不依赖 UI 线程，因此可被 monitor 的通知调度线程安全调用。
    """
    if _hwnd == 0:  # 图标尚未就绪：本轮跳过，等待下一轮调度再试
        return
    try:
        # 启用气泡点击事件回调（失败不影响气泡显示）
        try:
            ver = NOTIFYICONDATAW()
            ver.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            ver.hWnd = wt.HWND(_hwnd)
            ver.uID = ID_TRAY
            ver.uTimeoutOrVersion = NOTIFYICON_VERSION  # uVersion=3
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(ver))
        except Exception as _exc:  # noqa: BLE001
            applog.note(_exc, "tray: 设置气泡超时版本失败，沿用默认")
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wt.HWND(_hwnd)
        nid.uID = ID_TRAY
        nid.uFlags = NIF_INFO
        nid.szInfo = text
        nid.szInfoTitle = title
        nid.uTimeoutOrVersion = 10000  # 经典枚举：气泡停留毫秒数（>= Win2000）
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
    except Exception as _exc:  # noqa: BLE001 —— 气泡失败不影响守护
        applog.note(_exc, "tray: 托盘气泡通知失败")


def _popup_menu(hwnd: int) -> None:
    menu = user32.CreatePopupMenu()
    if not menu:
        return
    try:
        user32.AppendMenuW(menu, MF_STRING, IDM_OVERVIEW, "今日概览")
        user32.AppendMenuW(menu, MF_STRING, IDM_OPEN_DASHBOARD, "打开仪表盘")
        user32.AppendMenuW(menu, MF_STRING, IDM_CHECK_UPDATE, "检查更新")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        pause_label = "继续监控" if _is_paused_fn() else "暂停监控"
        user32.AppendMenuW(menu, MF_STRING, IDM_PAUSE, pause_label)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, wt.HWND(hwnd), None
        )
        _handle_command(hwnd, int(cmd))
    finally:
        user32.DestroyMenu(menu)  # 弹出菜单句柄用 DestroyMenu 释放


def _handle_command(hwnd: int, cmd: int) -> None:
    global _stop_event
    if cmd == IDM_OVERVIEW:
        # 今日概览：打开仪表盘并定位到「今日概览」视图（前端重做后不再用文本弹窗）
        _open_dashboard_fn(view="overview")
    elif cmd == IDM_OPEN_DASHBOARD:
        _open_dashboard_fn()
    elif cmd == IDM_CHECK_UPDATE:
        _check_update_fn()
    elif cmd == IDM_PAUSE:
        _set_paused_fn(not _is_paused_fn())
    elif cmd == IDM_EXIT:
        if _stop_event is not None:
            _stop_event.set()
        user32.PostMessageW(wt.HWND(hwnd), WM_QUIT, 0, 0)


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAY:
        if lparam & 0xFFFF == NIN_BALLOONUSERCLICK:
            # 点击气泡：打开仪表盘日报视图
            _open_dashboard_fn(view="report")
            return 0
        if lparam & 0xFFFF == WM_RBUTTONUP or lparam & 0xFFFF == WM_CONTEXTMENU:
            _popup_menu(hwnd)
        elif lparam & 0xFFFF == WM_LBUTTONUP:
            _handle_command(hwnd, IDM_OVERVIEW)
        return 0
    if msg == WM_COMMAND:
        _handle_command(hwnd, int(wparam & 0xFFFF))
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(wt.HWND(hwnd), msg, wparam, lparam)


_WNDPROC_REF = WNDPROC(_wndproc)  # 防止回调被 GC


def run(
    config: dict,
    overview_fn=None,
    set_paused_fn=None,
    is_paused_fn=None,
    open_dashboard_fn=None,
    check_update_fn=None,
    stop_event=None,
) -> None:
    """启动托盘并阻塞运行，直到用户选择退出。"""
    global _overview_fn, _set_paused_fn, _is_paused_fn, _open_dashboard_fn, _check_update_fn, _stop_event, _data_root, _hwnd

    if overview_fn is not None:
        _overview_fn = overview_fn
    if set_paused_fn is not None:
        _set_paused_fn = set_paused_fn
    if is_paused_fn is not None:
        _is_paused_fn = is_paused_fn
    if open_dashboard_fn is not None:
        _open_dashboard_fn = open_dashboard_fn
    if check_update_fn is not None:
        _check_update_fn = check_update_fn
    if stop_event is not None:
        _stop_event = stop_event
    _data_root = config.get("data_root") or paths.default_data_root()

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = ctypes.cast(_WNDPROC_REF, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = TRAY_CLASS
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        raise RuntimeError("RegisterClassExW failed")

    hwnd = user32.CreateWindowExW(
        0, TRAY_CLASS, TRAY_CLASS, 0,
        CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT,
        None, None, hinst, None,
    )
    if not hwnd:
        raise RuntimeError("CreateWindowExW failed")
    _hwnd = int(hwnd)

    _add_icon(hwnd)
    try:
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        _delete_icon(hwnd)
        user32.DestroyWindow(wt.HWND(hwnd))
        _hwnd = 0


def request_quit() -> None:
    """向托盘消息循环投递 WM_QUIT（供外部/测试调用）。"""
    if _hwnd:
        user32.PostMessageW(wt.HWND(_hwnd), WM_QUIT, 0, 0)


if __name__ == "__main__":
    import threading

    print("tray 自检：消息循环 3 秒后自动退出")
    threading.Timer(3.0, request_quit).start()
    run({"data_root": r"D:\电脑使用情况监控"}, overview_fn=lambda: "自检", stop_event=threading.Event())
    print("tray 自检结束")
