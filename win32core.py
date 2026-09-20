# -*- coding: utf-8 -*-
"""win32core.py — 极薄 Win32 API 封装（纯标准库 ctypes，零第三方依赖）。

供 monitor.py（每 5 秒轮询前台窗口）与 inventory.py（进程枚举）使用。
所有 API 失败时降级为安全默认值（None / "" / 0 / {}），绝不上抛异常，
以应对锁屏、权限拒绝、UAC 提权窗口等场景。

Windows 10/11 x64, Python 3.10+。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import applog  # noqa: E402
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_NAME_WIN32 = 0x00000000
MAX_PATH = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ---------------------------------------------------------------------------
# Win32 结构体
# ---------------------------------------------------------------------------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("dwTime", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_ulonglong),  # ULONG_PTR (x64 下为 8 字节)
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * MAX_PATH),
    ]


# ---------------------------------------------------------------------------
# 函数签名声明（确保 64 位句柄 / DWORD 正确传递）
# ---------------------------------------------------------------------------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype = wt.HWND
user32.GetForegroundWindow.argtypes = []

user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]

user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

user32.GetLastInputInfo.restype = wt.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]

kernel32.GetTickCount.restype = wt.DWORD
kernel32.GetTickCount.argtypes = []

kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

kernel32.Process32NextW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
]

ERROR_ALREADY_EXISTS = 183

# 单实例互斥锁专用：必须 use_last_error=True 才能通过 ctypes.get_last_error()
# 拿到 CreateMutexW 的 GetLastError（默认 ctypes.windll 拿不到，检测会失效）。
_ker32e = ctypes.WinDLL("kernel32", use_last_error=True)
_ker32e.CreateMutexW.restype = wt.HANDLE
_ker32e.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class ProcessInfo:
    """进程信息（exe 一律小写，如 "opencode.exe"）。"""
    pid: int
    ppid: int
    exe: str


@dataclass
class ForegroundInfo:
    """前台窗口信息（exe 一律小写）。"""
    hwnd: int
    pid: int
    exe: str
    title: str


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _basename(path: str) -> str:
    """按 \\ 或 / 取路径最后一段。"""
    return path.replace("/", "\\").rsplit("\\", 1)[-1]


# ---------------------------------------------------------------------------
# 前台窗口
# ---------------------------------------------------------------------------
def get_foreground_window() -> int:
    """返回前台窗口句柄；无窗口时返回 0。"""
    try:
        hwnd = user32.GetForegroundWindow()
        return int(hwnd) if hwnd else 0
    except Exception:
        return 0


def get_window_pid(hwnd: int) -> int:
    """返回窗口所属进程 PID；失败返回 0。"""
    try:
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def get_window_title(hwnd: int) -> str:
    """返回窗口标题；空窗口 / 失败返回 ""。"""
    try:
        length = int(user32.GetWindowTextLengthW(wt.HWND(hwnd)))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        n = int(user32.GetWindowTextW(wt.HWND(hwnd), buf, length + 1))
        return buf.value[:n]
    except Exception:
        return ""


def get_foreground_info() -> ForegroundInfo | None:
    """采集前台窗口信息；无前台窗口（锁屏/无桌面）返回 None。"""
    hwnd = get_foreground_window()
    if not hwnd:
        return None
    pid = get_window_pid(hwnd)
    if not pid:
        return None
    return ForegroundInfo(
        hwnd=hwnd,
        pid=pid,
        exe=get_exe_name(pid),
        title=get_window_title(hwnd),
    )


# ---------------------------------------------------------------------------
# 进程枚举
# ---------------------------------------------------------------------------
_process_cache: dict = {"ts": 0.0, "data": {}}
_PROCESS_CACHE_TTL = 6.0  # 秒：进程表快照缓存 TTL（> 默认 5s 轮询，静态时避免重复枚举）

_exe_cache: dict[int, tuple[float, str]] = {}  # pid -> (时间戳, exe)
# 进程生命周期内 pid->exe 不可变，60s TTL 只影响 pid 复用场景（可忽略）；
# 前台窗口不变时轮询完全走缓存，静态 CPU ≈ 0%
_EXE_CACHE_TTL = 60.0

_path_cache: dict[int, tuple[float, str]] = {}  # pid -> (时间戳, 完整路径)
_PATH_CACHE_TTL = 60.0


def enum_processes() -> dict[int, ProcessInfo]:
    """一次性快照全部进程，返回 {pid: ProcessInfo}；失败返回 {}。

    使用 CreateToolhelp32Snapshot（毫秒级），不用 WMI。
    结果带 2 秒 TTL 缓存：monitor 每 5 秒轮询时依然总是拿到新快照，
    但同一轮内的多次调用（get_exe_name 等）不再重复枚举。
    """
    now = time.monotonic()
    if now - _process_cache["ts"] < _PROCESS_CACHE_TTL:
        return _process_cache["data"]
    result: dict[int, ProcessInfo] = {}
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            return result
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                if pid not in (0, 4):  # 跳过 Idle / System
                    result[pid] = ProcessInfo(
                        pid=pid,
                        ppid=int(entry.th32ParentProcessID),
                        exe=entry.szExeFile.split("\x00", 1)[0].lower(),
                    )
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception as _exc:
        applog.note(_exc, "win32core: 进程枚举失败，沿用旧缓存（数据可能过期）")
    _process_cache["ts"] = now
    _process_cache["data"] = result
    return result


def get_exe_name(pid: int) -> str:
    """返回进程 exe 文件名（小写，含扩展名）；失败返回 ""。

    带 10 秒 TTL 的 pid->exe 缓存：前台窗口不变时轮询零枚举开销；
    pid 变化（切换窗口）时走一次进程表快照。
    """
    if pid <= 0:
        return ""
    now = time.monotonic()
    cached = _exe_cache.get(pid)
    if cached is not None and now - cached[0] <= _EXE_CACHE_TTL:
        return cached[1]
    info = enum_processes().get(pid)
    if info is not None:
        _exe_cache[pid] = (now, info.exe)
        return info.exe
    # 兜底：OpenProcess + QueryFullProcessImageNameW
    try:
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, wt.DWORD(pid)
        )
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(MAX_PATH)
            size = wt.DWORD(MAX_PATH)
            if kernel32.QueryFullProcessImageNameW(
                handle, PROCESS_NAME_WIN32, buf, ctypes.byref(size)
            ):
                exe = _basename(buf.value).lower()
                _exe_cache[pid] = (now, exe)
                return exe
        finally:
            kernel32.CloseHandle(handle)
    except Exception as _exc:
        applog.note(_exc, "win32core: 进程名查询失败，返回空")
    return ""


def get_process_path(pid: int) -> str:
    """返回进程完整路径；失败返回 ""（带 60s TTL 缓存）。"""
    if pid <= 0:
        return ""
    now = time.monotonic()
    cached = _path_cache.get(pid)
    if cached is not None and now - cached[0] <= _PATH_CACHE_TTL:
        return cached[1]
    try:
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, wt.DWORD(pid)
        )
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(MAX_PATH)
            size = wt.DWORD(MAX_PATH)
            if kernel32.QueryFullProcessImageNameW(
                handle, PROCESS_NAME_WIN32, buf, ctypes.byref(size)
            ):
                path = buf.value
                _path_cache[pid] = (now, path)
                return path
        finally:
            kernel32.CloseHandle(handle)
    except Exception as _exc:
        applog.note(_exc, "win32core: 进程路径查询失败，返回空")
    return ""


def _strip_uwp_package(segment: str) -> str:
    """把 WindowsApps 目录段转为包前缀。

    示例：Microsoft.WindowsCalculator_10.2103.8.0_x64__8wekyb3d8bbwe
          -> Microsoft.WindowsCalculator
    """
    seg = str(segment or "").strip()
    if not seg:
        return ""
    # 去掉版本/架构/哈希后缀：从第一个 "_数字." 或末尾 "_" 段截断
    for sep in ("_10.", "_8.", "_1.", "_2."):
        idx = seg.find(sep)
        if idx > 0:
            return seg[:idx]
    # 通用：去掉最后一个 _ 后的内容（package 名通常不含下划线）
    if "_" in seg:
        return seg.rsplit("_", 1)[0]
    return seg


def get_uwp_app_name(pid: int, mapping: dict | None = None) -> str | None:
    """识别 UWP/商店应用显示名。

    通过进程完整路径判断是否位于 WindowsApps 目录，并提取包前缀；
    优先使用 config 的 `uwp_app_names` 映射，未映射时返回包前缀本身。
    """
    path = get_process_path(pid)
    if not path:
        return None
    marker = "\\WindowsApps\\"
    if marker not in path:
        return None
    tail = path.split(marker, 1)[1]
    segment = tail.split("\\", 1)[0]
    package = _strip_uwp_package(segment)
    if not package:
        return None
    if isinstance(mapping, dict):
        for prefix, name in mapping.items():
            if package == prefix or package.startswith(str(prefix)):
                return str(name)
    return package


def is_admin() -> bool:
    """当前进程是否以管理员权限运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_process_running(pid: int) -> bool:
    """进程是否存在（句柄可打开即视为存在）。"""
    if pid <= 0:
        return False
    try:
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, wt.DWORD(pid)
        )
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 单实例互斥锁
# ---------------------------------------------------------------------------
_mutex_handle = None


def acquire_single_instance(name: str) -> bool:
    """尝试获取命名互斥锁，防止多个监控实例同时写数据。

    返回 True 表示获取成功（本进程成为唯一实例）；
    返回 False 表示已有实例在运行（调用方应直接退出）。
    获取失败时保守放行（不因锁问题阻断监控）。
    """
    global _mutex_handle
    if _mutex_handle:
        return True
    try:
        handle = _ker32e.CreateMutexW(None, False, name)
        if not handle:
            return True  # 创建失败（权限等）不阻止
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# 窗口状态（最大化 / 全屏 / 普通）
# ---------------------------------------------------------------------------
SW_SHOWMAXIMIZED = 3
SM_CXSCREEN = 0
SM_CYSCREEN = 1


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wt.UINT),
        ("flags", wt.UINT),
        ("showCmd", wt.UINT),
        ("ptMinPosition", wt.POINT),
        ("ptMaxPosition", wt.POINT),
        ("rcNormalPosition", wt.RECT),
    ]


user32.GetWindowPlacement.restype = wt.BOOL
user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]


def get_window_state(hwnd: int) -> str:
    """窗口状态：fullscreen / maximized / normal（尽力而为，失败返回 normal）。"""
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(wt.HWND(hwnd), ctypes.byref(wp)):
            if int(wp.showCmd) == SW_SHOWMAXIMIZED:
                return "maximized"
        rect = wt.RECT()
        if user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            sw = int(user32.GetSystemMetrics(SM_CXSCREEN))
            sh = int(user32.GetSystemMetrics(SM_CYSCREEN))
            if width >= sw and height >= sh:
                return "fullscreen"
        return "normal"
    except Exception:  # noqa: BLE001
        return "normal"


# ---------------------------------------------------------------------------
# 空闲检测
# ---------------------------------------------------------------------------
def get_tick_count() -> int:
    """系统启动以来的毫秒数（DWORD，会回绕）。"""
    try:
        return int(kernel32.GetTickCount())
    except Exception:
        return 0


def get_last_input_tick() -> int:
    """最后一次键盘/鼠标输入的 tick；失败返回 0。"""
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if user32.GetLastInputInfo(ctypes.byref(info)):
            return int(info.dwTime)
        return 0
    except Exception:
        return 0


def idle_seconds() -> float:
    """距上次键盘/鼠标输入经过的秒数（处理 32 位 tick 回绕）。"""
    now = get_tick_count()
    last = get_last_input_tick()
    delta = now - last
    if delta < 0:
        delta += 2 ** 32
    return delta / 1000.0


if __name__ == "__main__":
    fg = get_foreground_info()
    print("foreground:", fg)
    print("idle_seconds:", round(idle_seconds(), 1))
    procs = enum_processes()
    print("process_count:", len(procs))
    if fg is not None:
        print("fg_exe_lookup:", procs.get(fg.pid))
