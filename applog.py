# -*- coding: utf-8 -*-
"""applog.py — 轻量滚动日志系统（纯标准库，零依赖）。

统一记录 monitor / dashboard / report 的运行日志：
    <data_root>/logs/app.log         当前日志（单文件上限 1MB）
    <data_root>/logs/app.log.1..5    滚动备份（保留 5 份）

仪表盘「日志」视图通过 /api/log 读取展示。

用法：
    import applog
    applog.configure(data_root)          # 启动时初始化（幂等）
    log = applog.get_logger("monitor")   # 模块名显示在日志行
    log.info("守护进程启动")
    log.warning("配置缺失，使用默认值")
    log.error("轮询异常: %s", exc)
"""

from __future__ import annotations

import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler

_configured = False
_default_root: str | None = None


class _LogFormatter(logging.Formatter):
    """`2026-08-13 09:00:00 [INFO] [monitor] 消息`（紧凑单行）。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{ts} [{record.levelname[:4]:<4}] [{record.name}] {record.getMessage()}"


def configure(data_root: str | None = None) -> None:
    """初始化日志（幂等）：写入 <data_root>/logs/app.log。"""
    global _configured, _default_root
    root_dir = data_root or _default_root
    if root_dir:
        _default_root = root_dir
    if _configured:
        return
    try:
        logs_dir = os.path.join(_default_root or os.getcwd(), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        logger = logging.getLogger("usagemon")
        if logger.handlers:
            _configured = True
            return
        handler = RotatingFileHandler(
            os.path.join(logs_dir, "app.log"),
            maxBytes=1024 * 1024,  # 1MB
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(_LogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _configured = True
    except Exception:  # noqa: BLE001 —— 日志失败绝不能影响主体功能
        pass


def get_logger(name: str) -> logging.Logger:
    """获取子 logger（name 显示在日志行，如 monitor / dashboard / report）。"""
    if not _configured:
        configure(_default_root)
    return logging.getLogger(f"usagemon.{name}")



# ---------------------------------------------------------------------------
# 「有意降级」异常的观测出口
# ---------------------------------------------------------------------------
# 全项目 30+ 处按设计必须继续执行的 except Exception: ... pass（DNS 失败、
# 第三方会话文件损坏、Win32 调用被拒、SQLite 快路回退等）历史上完全无痕迹，
# 表现为「统计数字对不上却查不到原因」。这些点统一改走 note() 留一行日志：
# 行为不变（仍然不抛、仍然降级），只是从不可观测变为可观测。
_DEGRADE = logging.getLogger("usagemon.降级")
_DEGRADE.addHandler(logging.NullHandler())   # 未 configure() 前保持安静
_DEGRADE.setLevel(logging.WARNING)           # 只放行 warning 及以上
_DEGRADE.propagate = True                    # configure() 后随 usagemon handler 落盘

_NOTE_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def note(exc, ctx, level="warning"):
    """记录被有意吞掉的异常（ctx 描述位置与业务影响）。只记录，不抛出。

    - configure() 之后：随 applog 写入 <data_root>/logs/app.log，
      仪表盘「日志」视图可直接看到，便于排查数据偏差。
    - configure() 之前（CLI / 测试环境）：NullHandler 兜底，无任何输出，
      保持原有安静行为。
    - 本函数自身永不抛异常——观测失败绝不能影响主体功能。
    """
    try:
        _DEGRADE.log(_NOTE_LEVELS.get(level, logging.WARNING), "%s: %r", ctx, exc)
    except Exception:  # noqa: BLE001
        pass


def log_path(data_root: str) -> str:
    """当前日志文件路径（未初始化时按目录推断）。"""
    return os.path.join(data_root, "logs", "app.log")


def read_recent(data_root: str, n: int = 300) -> list[str]:
    """读取最近 n 行日志（从文件尾部向前），用于仪表盘展示。

    流式实现：deque(maxlen=n) 逐行迭代，内存中只保留最近 n 行，
    占用与日志总行数无关（大文件下相比 readlines() 显著更低）。
    n <= 0 时返回 []（“最近 n 行”对非正 n 无意义）。
    """
    path = log_path(data_root)
    if not os.path.isfile(path):
        return []
    if n <= 0:
        return []
    tail: deque[str] = deque(maxlen=n)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                tail.append(line)
    except OSError:
        return []
    return [ln.rstrip("\n") for ln in tail]


def read_errors(data_root: str, day_dirs: list[str], n: int = 300) -> list[str]:
    """汇总最近几天 errors.log 的内容（带日期前缀），用于仪表盘展示。"""
    out: list[str] = []
    for day in day_dirs:
        p = os.path.join(data_root, day, "errors.log")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if line.strip():
                        out.append(f"{day} {line}")
        except OSError:
            continue
    return out[-n:]


if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mkdtemp(prefix="applog_test_")
    configure(tmp)
    log = get_logger("test")
    log.info("hello")
    log.warning("warn %s", "x")
    try:
        1 / 0
    except ZeroDivisionError as exc:
        log.error("boom: %s", exc)
    for ln in read_recent(tmp, 10):
        print(ln)
