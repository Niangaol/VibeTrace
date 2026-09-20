# -*- coding: utf-8 -*-
"""sqlite_store.py — 可选 SQLite 后端（usage.db）。

设计原则（对应《项目需求与开发文档》§6.5）：
- 每日 JSONL 仍是**原始日志唯一事实源**，本模块只维护一份**额外 SQLite 索引/镜像**，
  用于高效查询、月度/长期聚合，绝不替代或删除 usage.jsonl。
- monitor 写入 JSONL 后 best-effort 同步写入 SQLite；历史数据可用
  `python sqlite_store.py --backfill` 或 `--rebuild` 回填。
- 所有写入失败静默降级，不影响监控主流程。

CLI：
  python sqlite_store.py --status
  python sqlite_store.py --backfill [--day YYYY-MM-DD ...]
  python sqlite_store.py --rebuild
  python sqlite_store.py --query YYYY-MM-DD [--json]
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import sqlite3
import sys

import applog  # noqa: E402
import paths  # noqa: E402

DB_NAME = "usage.db"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sig TEXT NOT NULL UNIQUE,
    day TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    exe TEXT NOT NULL,
    app TEXT,
    title TEXT,
    category TEXT,
    contact TEXT,
    ai_tool TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    browser_category TEXT,
    subcategory TEXT,
    term_tool TEXT,
    window_state TEXT,
    url TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start);
CREATE INDEX IF NOT EXISTS idx_sessions_category ON sessions(category);
CREATE INDEX IF NOT EXISTS idx_sessions_ai ON sessions(ai_tool);
"""


def db_path(data_root: str) -> str:
    """usage.db 的完整路径（位于 data_root 下）。"""
    return os.path.join(data_root or ".", DB_NAME)


def connect(data_root: str) -> sqlite3.Connection:
    """打开 SQLite 连接（row_factory=Row）。"""
    os.makedirs(data_root or ".", exist_ok=True)
    conn = sqlite3.connect(db_path(data_root))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表与索引（幂等）。"""
    conn.executescript(_SCHEMA)
    conn.commit()


def _sig(day: str, rec: dict) -> str:
    """记录签名：基于稳定字段生成，回填/重复写入幂等。"""
    payload = {
        "day": day,
        "start": rec.get("start"),
        "end": rec.get("end"),
        "duration_ms": int(rec.get("duration_ms") or 0),
        "exe": rec.get("exe"),
        "title": rec.get("title"),
        "active": bool(rec.get("active", True)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_from_record(day: str, rec: dict) -> tuple:
    """把一条 JSONL 记录转换为 SQLite 行参数。"""
    return (
        _sig(day, rec),
        day,
        str(rec.get("start") or ""),
        str(rec.get("end") or ""),
        int(rec.get("duration_ms") or 0),
        str(rec.get("exe") or ""),
        rec.get("app"),
        rec.get("title"),
        rec.get("category"),
        rec.get("contact"),
        rec.get("ai_tool"),
        1 if rec.get("active", True) else 0,
        rec.get("browser_category"),
        rec.get("subcategory"),
        rec.get("term_tool"),
        rec.get("window_state"),
        rec.get("url"),
        json.dumps(rec, ensure_ascii=False, sort_keys=True),
    )


def insert_record(conn: sqlite3.Connection, day: str, rec: dict) -> bool:
    """插入一条会话记录（按 sig 幂等）。返回是否新插入。"""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO sessions (
            sig, day, start, end, duration_ms, exe, app, title, category,
            contact, ai_tool, active, browser_category, subcategory,
            term_tool, window_state, url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _row_from_record(day, rec),
    )
    conn.commit()
    return cur.rowcount > 0


def append_record(data_root: str, day: str, rec: dict) -> bool:
    """best-effort 追加一条记录到 SQLite；失败返回 False（不抛异常）。

    性能：连接按 data_root 进程内复用（_CONN_CACHE），init_db 每连接只跑一次——
    避免每条记录都 connect + executescript 的开销（monitor 状态变化才写一条，
    单次不大，但报表回填/长驻守护累计可观）。测试/退出用 close_connections() 释放。
    """
    try:
        conn = _shared_conn(data_root)
        return insert_record(conn, day, rec)
    except Exception:  # noqa: BLE001 —— SQLite 只是额外镜像，失败不影响监控
        return False


_CONN_CACHE: dict[str, "sqlite3.Connection"] = {}
_INITED_KEYS: set[str] = set()


def _conn_key(data_root: str) -> str:
    return os.path.normcase(os.path.abspath(data_root or "."))


def _shared_conn(data_root: str) -> "sqlite3.Connection":
    """取（或创建）data_root 对应的进程内共享连接。

    镜像库放宽持久化：WAL + synchronous=NORMAL——commit 不再逐条 fsync
    （JSONL 才是事实源，镜像可由 --rebuild 重建，断电最多丢最近几条镜像，
    且 verify/backfill 可自愈）。实测写入吞吐提升约一个数量级。
    """
    key = _conn_key(data_root)
    conn = _CONN_CACHE.get(key)
    if conn is None:
        conn = connect(data_root)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception as _exc:  # noqa: BLE001 —— PRAGMA 失败不影响可用性
            applog.note(_exc, "sqlite_store: PRAGMA 设置失败，沿用连接默认参数")
        _CONN_CACHE[key] = conn
    if key not in _INITED_KEYS:
        init_db(conn)
        _INITED_KEYS.add(key)
    return conn


def close_connection(data_root: str) -> None:
    """关闭单个 data_root 的共享连接（删除/重建 usage.db 前必须调用，
    否则旧句柄指向已删除文件，后续写入进孤儿文件）。"""
    key = _conn_key(data_root)
    conn = _CONN_CACHE.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception as _exc:  # noqa: BLE001
            applog.note(_exc, "sqlite_store: 单连接关闭失败")
    _INITED_KEYS.discard(key)


def close_connections() -> None:
    """关闭并清空全部共享连接（测试隔离 / 进程退出前调用，释放 Windows 文件句柄）。"""
    for conn in list(_CONN_CACHE.values()):
        try:
            conn.close()
        except Exception as _exc:  # noqa: BLE001
            applog.note(_exc, "sqlite_store: 关闭全部连接失败")
    _CONN_CACHE.clear()
    _INITED_KEYS.clear()


atexit.register(close_connections)


def _iter_jsonl(data_root: str, day: str):
    """读取某天 usage.jsonl 的原始行（跳过坏行）。"""
    path = os.path.join(data_root, day, "usage.jsonl")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
            except json.JSONDecodeError:
                continue


def _all_days(data_root: str) -> list[str]:
    """扫描 data_root 下所有 YYYY-MM-DD 日期目录（含 usage.jsonl）。"""
    out = []
    if not os.path.isdir(data_root):
        return out
    for name in os.listdir(data_root):
        if _DATE_DIR_RE.fullmatch(name or "") and os.path.isfile(
            os.path.join(data_root, name, "usage.jsonl")
        ):
            out.append(name)
    return sorted(out)


def backfill(data_root: str, days: list[str] | None = None, verbose: bool = False) -> dict:
    """把 JSONL 历史数据回填到 SQLite；返回 {days, inserted, skipped}。"""
    days = days or _all_days(data_root)
    conn = connect(data_root)
    inserted = 0
    skipped = 0
    try:
        init_db(conn)
        for day in days:
            if not _DAY_RE.fullmatch(day or ""):
                continue
            for rec in _iter_jsonl(data_root, day):
                if insert_record(conn, day, rec):
                    inserted += 1
                else:
                    skipped += 1
            if verbose:
                print(f"  [sqlite_store] {day}: 已回填（累计新增 {inserted}）")
    finally:
        conn.close()
    return {"days": len([d for d in days if _DAY_RE.fullmatch(d or "")]),
            "inserted": inserted, "skipped": skipped}


def rebuild(data_root: str, verbose: bool = False) -> dict:
    """删除并重建 usage.db，然后全量回填。"""
    close_connection(data_root)  # 先释放共享句柄，否则删的是孤儿文件
    path = db_path(data_root)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    conn = connect(data_root)
    try:
        init_db(conn)
    finally:
        conn.close()
    return backfill(data_root, verbose=verbose)


def read_day(data_root: str, day: str) -> list[dict]:
    """从 SQLite 读取某天全部会话（按 start 升序）。"""
    conn = connect(data_root)
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT * FROM sessions WHERE day = ? ORDER BY start ASC", (day,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_range(data_root: str, start_day: str, end_day: str) -> list[dict]:
    """从 SQLite 读取日期区间全部会话（含两端，按 start 升序）。"""
    conn = connect(data_root)
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT * FROM sessions WHERE day BETWEEN ? AND ? ORDER BY start ASC",
            (start_day, end_day),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def status(data_root: str) -> dict:
    """返回 usage.db 状态。"""
    path = db_path(data_root)
    if not os.path.isfile(path):
        return {"exists": False, "path": path, "rows": 0, "days": []}
    conn = connect(data_root)
    try:
        init_db(conn)
        rows = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        days = [r["day"] for r in conn.execute(
            "SELECT DISTINCT day FROM sessions ORDER BY day"
        ).fetchall()]
        return {
            "exists": True,
            "path": path,
            "rows": int(rows["n"] or 0),
            "days": days,
        }
    finally:
        conn.close()


def verify(data_root: str) -> dict:
    """对比 JSONL 原始日志与 SQLite 的记录数，返回差异列表。"""
    days = _all_days(data_root)
    conn = connect(data_root)
    total_jsonl = 0
    total_db = 0
    mismatches: list[dict] = []
    try:
        init_db(conn)
        for day in days:
            jsonl_count = sum(1 for _ in _iter_jsonl(data_root, day))
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE day = ?", (day,)
            ).fetchone()
            db_count = int(row["n"] or 0)
            total_jsonl += jsonl_count
            total_db += db_count
            if jsonl_count != db_count:
                mismatches.append({"day": day, "jsonl": jsonl_count, "sqlite": db_count})
    finally:
        conn.close()
    return {
        "days": len(days),
        "jsonl_records": total_jsonl,
        "sqlite_records": total_db,
        "mismatches": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlite_store.py", description="可选 SQLite 后端（usage.db）")
    parser.add_argument("--status", action="store_true", help="查看 usage.db 状态")
    parser.add_argument("--verify", action="store_true", help="对比 JSONL 与 SQLite 记录数")
    parser.add_argument("--backfill", action="store_true", help="把 JSONL 历史数据回填到 SQLite")
    parser.add_argument("--rebuild", action="store_true", help="重建 usage.db 并全量回填")
    parser.add_argument("--query", metavar="YYYY-MM-DD", help="查询某天会话（来自 SQLite）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--verbose", action="store_true", help="显示逐日进度")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config()
        data_root = args.data_root or (cfg.get("data_root") or paths.default_data_root())
    except Exception:  # noqa: BLE001
        data_root = args.data_root or paths.default_data_root()

    if args.status:
        info = status(data_root)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"usage.db: {'存在' if info['exists'] else '不存在'} @ {info['path']}")
            print(f"记录数: {info['rows']}")
            print(f"覆盖日期: {', '.join(info['days']) or '（无）'}")
        return 0

    if args.verify:
        info = verify(data_root)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"校验完成：{info['days']} 天，JSONL {info['jsonl_records']} 条，"
                  f"SQLite {info['sqlite_records']} 条")
            if info["mismatches"]:
                print("差异：")
                for m in info["mismatches"]:
                    print(f"  - {m['day']}: JSONL {m['jsonl']} / SQLite {m['sqlite']}")
            else:
                print("无差异")
        return 0 if not info["mismatches"] else 1

    if args.rebuild:
        result = rebuild(data_root, verbose=args.verbose)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"重建完成：{result['inserted']} 条新增，{result['skipped']} 条跳过，覆盖 {result['days']} 天")
        return 0

    if args.backfill:
        result = backfill(data_root, verbose=args.verbose)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"回填完成：{result['inserted']} 条新增，{result['skipped']} 条跳过，覆盖 {result['days']} 天")
        return 0

    if args.query:
        if not _DAY_RE.fullmatch(args.query or ""):
            print(f"[sqlite_store] 日期格式错误: {args.query}", file=sys.stderr)
            return 2
        rows = read_day(data_root, args.query)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(f"# {args.query}（SQLite 共 {len(rows)} 条）")
            for r in rows:
                print(f"- {r.get('start')} → {r.get('end')} {r.get('app') or r.get('exe')} "
                      f"{r.get('category') or ''} {r.get('duration_ms')}ms")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
