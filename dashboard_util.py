# -*- coding: utf-8 -*-
"""dashboard_util.py — dashboard.py 的纯函数/工具模块（零第三方运行时依赖）。

从 dashboard.py 拆出的与 HTTP 无关的纯函数：备份 zip 打包/安全解压、聚合转 CSV、
CSV 注入防护、可用日期（带 mtime/TTL 缓存）、已知应用收集、某月可用日期等。
行为与原实现完全一致；dashboard.py 通过 `from dashboard_util import ...` 引用。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile

# 备份 zip 允许包含的顶层条目：日期目录 或 已知数据文件（其余一律拒绝，防解压注入）
_ALLOWED_ROOT_FILES = {
    "config.json", "app_groups.json", "aliases.json",
    "report_week.md", "report_month.json", "report_month.md",
}
# 备份 zip 打包时排除的大日志/临时/备份文件
_EXCLUDED_FILE_SUFFIXES = (".log", ".bak", ".bak_verify", ".tmp", ".pyc")


def _month_days_for(data_root: str, month_str: str) -> list[str]:
    """返回某月内实际存在 usage.jsonl 的日期（升序），用于导出/统计。"""
    out = []
    for name in os.listdir(data_root) if os.path.isdir(data_root) else []:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and name.startswith(month_str + "-"):
            if os.path.isfile(os.path.join(data_root, name, "usage.jsonl")):
                out.append(name)
    return sorted(out)


def _agg_to_csv(agg: dict, title_line: str | None = None) -> str:
    """把任意聚合结果渲染成汇总 CSV（类型,名称,时长秒），周/月报通用。

    与 report.generate_report_csv 的口径一致：应用/类别/联系人/AI工具/浏览器分类。
    """
    lines: list[str] = []
    if title_line:
        lines.append("# " + title_line.replace("# ", "").replace(",", ""))
        lines.append("")
    lines.append("类型,名称,时长秒")
    for name, ms in sorted(agg.get("by_app", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"应用:{name},{int(ms // 1000)}")
    for cat, ms in sorted(agg.get("by_category", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"类别:{cat},{int(ms // 1000)}")
    for app, contacts in sorted(agg.get("by_contact", {}).items()):
        for contact, ms in sorted(contacts.items(), key=lambda kv: -kv[1]):
            lines.append(f"联系人:{app}/{contact},{int(ms // 1000)}")
    for tool, ms in sorted(agg.get("by_ai", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"AI工具:{tool},{int(ms // 1000)}")
    for label, ms in sorted(agg.get("by_browser", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"浏览器:{label},{int(ms // 1000)}")
    return "\n".join(lines) + "\n"


def _backup_entries(data_root: str) -> list[str]:
    """枚举要打包的条目：日期目录 + 允许的根配置文件；排除日志/临时/备份文件。"""
    entries: list[str] = []
    if os.path.isdir(data_root):
        for name in sorted(os.listdir(data_root)):
            full = os.path.join(data_root, name)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and os.path.isdir(full):
                entries.append(name)
            elif name in _ALLOWED_ROOT_FILES and os.path.isfile(full):
                entries.append(name)
    return entries


def _backup_zip(data_root: str) -> bytes:
    """把 data_root 内容打包为 zip 字节（日期目录 + 配置文件），排除大日志/临时/备份。

    用于 /api/backup 附件下载。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        entries = _backup_entries(data_root)
        for rel in entries:
            src = os.path.join(data_root, rel)
            if os.path.isfile(src):
                zf.write(src, rel)
            elif os.path.isdir(src):
                for dirpath, _dirnames, filenames in os.walk(src):
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        arch = os.path.join(rel, os.path.relpath(full, src))
                        zf.write(full, arch.replace("\\", "/"))
    return buf.getvalue()


def _safe_extract_zip(data_root: str, zip_bytes: bytes) -> str:
    """把备份 zip 解压到临时目录并校验（路径穿越/恶意条目拦截），返回临时目录路径。

    仅保留日期目录与白名单根文件；zip 外的其他条目一律丢弃（不覆盖攻击者文件）。
    """
    tmp = tempfile.mkdtemp(prefix="usemon_restore_")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            # 防路径穿越：拒绝绝对路径与 ../ 上级引用
            if name.startswith("/") or ".." in name.split("/"):
                continue
            top = name.split("/", 1)[0]
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", top) or top in _ALLOWED_ROOT_FILES):
                continue
            if info.is_dir():
                continue
            # 排除日志/临时/备份文件
            if any(name.lower().endswith(s) for s in _EXCLUDED_FILE_SUFFIXES):
                continue
            dest = os.path.normpath(os.path.join(tmp, name))
            if not dest.startswith(os.path.normpath(tmp) + os.sep) and dest != tmp:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    # 把白名单根文件的顶层名规范化后同 tmp 一起返回（由调用方合并到 data_root）
    return tmp


# ---------------------------------------------------------------------------
# _available_days 结果缓存（按 data_root 分桶）
# 范式同 report._agg_cache / classifier.load_config：目录 mtime 变化（新增/
# 删除日期文件夹必更新父目录 mtime）或超过 TTL（5s，同 _aliases_cache）时
# 重扫，避免单次请求内多次调用（/api/days + /api/dates、_collect_known_apps
# 两次切片）与长历史安装（数百日期文件夹）重复 os.listdir。返回浅拷贝，
# 避免调用方修改（如 /api/dates 外部消费）污染缓存。
# ---------------------------------------------------------------------------
_days_cache: dict[str, dict] = {}  # normcase(abs root) -> {"mtime", "ts", "data"}
_DAYS_TTL = 5.0  # 秒：mtime 未变化时也强制重扫的最长时间
# 并发安全（dashboard 为 ThreadingHTTPServer）：一把模块级锁保护日期缓存组的
# 全部读写（命中读 / 失效丢弃 / 重扫回填）。重扫（os.listdir）也留在锁内：
# 目录枚举本身很便宜，且可顺带避免并发请求对同一数据根的重复扫描（防惊群）。
# _days_mtime 只 stat 文件系统、不碰共享表，自身无需持锁。
_DAYS_LOCK = threading.Lock()


def _days_cache_key(data_root: str) -> str:
    """规范化数据根目录为缓存键（相对/绝对归一化，Windows 忽略大小写）。"""
    return os.path.normcase(os.path.abspath(data_root))


def _days_mtime(data_root: str) -> float:
    """取数据根目录 mtime；目录不存在返回 0.0（之后重建 mtime 变化即可感知）。"""
    try:
        return os.path.getmtime(data_root)
    except OSError:
        return 0.0


def invalidate_days_cache(data_root: str | None = None) -> None:
    """强制丢弃日期列表缓存；data_root 为空时清空全部。供写盘场景与测试调用。"""
    global _days_cache
    with _DAYS_LOCK:
        if data_root is None:
            _days_cache.clear()
        else:
            _days_cache.pop(_days_cache_key(data_root), None)


def _available_days(data_root: str) -> list[str]:
    """列出数据根目录下所有 YYYY-MM-DD 文件夹（升序），带 mtime/TTL 缓存。

    目录 mtime 变化或距上次扫描超过 _DAYS_TTL 秒时重扫；否则返回缓存浅拷贝。
    新增日期文件夹会更新目录 mtime，因此缓存失效后可感知新数据。
    并发：全程持有 _DAYS_LOCK（含重扫），保证查-弃-扫-回填原子。
    """
    key = _days_cache_key(data_root)
    with _DAYS_LOCK:
        now = time.monotonic()
        entry = _days_cache.get(key)
        if entry is not None and now - entry["ts"] < _DAYS_TTL:
            if _days_mtime(data_root) == entry["mtime"]:
                return list(entry["data"])
            _days_cache.pop(key, None)  # mtime 变化：丢弃旧缓存，走下方重扫

        days: list[str] = []
        if os.path.isdir(data_root):
            for name in os.listdir(data_root):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
                    days.append(name)
        days.sort()
        _days_cache[key] = {"mtime": _days_mtime(data_root), "ts": now, "data": days}
        return list(days)


def _collect_known_apps(data_root: str) -> dict[str, str]:
    """收集"已知应用"：软件清单 exe + 最近 14 天 usage.jsonl 出现的 exe。

    返回 {exe(小写): 显示名}（供分组管理界面列出全部可分组应用）。
    """
    known: dict[str, str] = {}

    def _add(exe: str, name: str = "") -> None:
        exe = (exe or "").lower()
        if not exe:
            return
        if exe not in known or not known[exe]:
            known[exe] = name or exe

    # 1) 软件清单（今日 + 最近几天）
    for day in _available_days(data_root)[-7:]:
        inv_path = os.path.join(data_root, day, "software_inventory.json")
        if not os.path.isfile(inv_path):
            continue
        try:
            with open(inv_path, "r", encoding="utf-8") as fh:
                inv = json.load(fh)
            for app in inv.get("apps", []):
                if isinstance(app, dict):
                    _add(app.get("exe"), app.get("name", ""))
        except Exception:  # noqa: BLE001
            continue
    # 2) 最近 14 天 usage.jsonl
    for day in _available_days(data_root)[-14:]:
        usage_path = os.path.join(data_root, day, "usage.jsonl")
        if not os.path.isfile(usage_path):
            continue
        try:
            with open(usage_path, "r", encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            _add(rec.get("exe"), rec.get("app", ""))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return known


def _sanitize_csv(csv_text: str) -> str:
    """CSV 注入防护：把以 = + - @ 或 tab 开头的单元格值前缀为 '（防 Excel 公式执行）。"""
    def clean(field: str) -> str:
        f = field.strip()
        if f[:1] in ("=", "+", "-", "@", "\t"):
            return "'" + field
        return field
    out = []
    for line in csv_text.split("\n"):
        if line.startswith("#"):
            out.append(line)
            continue
        out.append(",".join(clean(c) for c in line.split(",")))
    return "\n".join(out)
