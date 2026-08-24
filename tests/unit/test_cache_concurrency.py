# -*- coding: utf-8 -*-
"""tests/unit/test_cache_concurrency.py — 模块级缓存并发安全回归钉扎。

dashboard 是 ThreadingHTTPServer（多线程并发处理请求），而 report._agg_cache、
report._aliases_cache、ai_sessions 的 _COLLECT_CACHE/_PARSE_CACHE 与
dashboard_util 的 days-cache 组都是模块级共享表——加锁前 LRU 的 move_to_end/
popitem 与 TTL 过期清空在并发下会互相踩踏（OrderedDict mutated during
iteration / KeyError / 脏读）。

本测试在共享 tmp 数据根上开 8 线程并发 hammer 三条读取链路
（report.aggregate / dashboard_util._available_days / ai_sessions.collect），
钉扎两个不变量：
1) 零异常——并发下不再出现任何脏读崩溃；
2) 结果一致——各线程对同一天/同一数据根拿到完全相同的聚合与日期列表。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import dashboard_util  # noqa: E402
import report  # noqa: E402

_DAYS = ["2099-12-01", "2099-12-02", "2099-12-03"]


def _mk_record(day: str, start_h: int, minutes: float = 30.0) -> dict:
    """构造一条带类别/联系人维度的 usage.jsonl 会话记录（同 conftest.make_record 口径）。"""
    s = int(start_h * 60)
    e = s + int(minutes)

    def fmt(m: int) -> str:
        return f"{day}T{m // 60:02d}:{m % 60:02d}:00"

    return {"start": fmt(s), "end": fmt(e), "duration_ms": int(minutes * 60000),
            "exe": "code.exe", "app": "VS Code", "title": "a.py",
            "category": "开发工具", "contact": f"u{start_h}", "ai_tool": None,
            "active": True}


def _seed_day(root: str, day: str, records: list[dict]) -> None:
    """同 tests/conftest.py 的 seed_day：写 <root>/<day>/usage.jsonl 并失效 days-cache。"""
    day_dir = os.path.join(root, day)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _make_fixture(tmp_path) -> str:
    """共享 tmp 数据根：3 天 usage 数据 + 一个 tmp 内的 AI 会话目录（hermetic 不碰真机）。"""
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    for d in _DAYS:
        _seed_day(root, d, [_mk_record(d, h) for h in range(4)])
    sess = os.path.join(root, "sess")
    os.makedirs(sess, exist_ok=True)
    for i in range(12):  # 多个小文件：配合调低的条目上限让解析缓存常态处于插入+驱逐 churn
        with open(os.path.join(sess, f"s{i}.jsonl"), "w", encoding="utf-8") as fh:
            for k in range(3):
                fh.write(json.dumps(
                    {"timestamp": f"{_DAYS[0]}T09:{k:02d}:00",
                     "role": "user" if k % 2 == 0 else "assistant",
                     "content": f"hello {k}", "model": "m1", "cwd": "/repo/demo"},
                    ensure_ascii=False) + "\n")
    dashboard_util.invalidate_days_cache()
    ai_sessions.invalidate_collect_cache()
    return root


def test_module_caches_concurrent_hammer(tmp_path, monkeypatch):
    """8 线程 × 各 30 次并发锤三条缓存链路：断言零异常且各线程结果一致。

    调低 _AGG_CACHE_MAX / _PARSE_CACHE_MAX_ENTRIES（仅本测试内生效，monkeypatch
    自动还原）制造持续驱逐，正面锤「命中 move_to_end vs 插入端 popitem / sum
    遍历驱逐循环」这些历史上最危险的并发窗口。
    """
    root = _make_fixture(tmp_path)
    sess = os.path.join(root, "sess")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"t": [sess]}}, "data_root": root}
    monkeypatch.setattr(ai_sessions, "_PARSE_CACHE_MAX_ENTRIES", 8)
    monkeypatch.setattr(report, "_AGG_CACHE_MAX", 2)

    n_threads, iters = 8, 30
    barrier = threading.Barrier(n_threads)
    errors: list[str] = []
    agg_seen: dict = defaultdict(set)  # day -> {线程本地 json 快照}
    days_seen: set = set()
    collect_seen: set = set()
    lock = threading.Lock()

    def _record_err(ctx: str, exc: BaseException) -> None:
        with lock:
            errors.append(f"{ctx}: {exc!r}")

    def agg_worker(tid: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(iters):
                d = _DAYS[(tid + i) % len(_DAYS)]
                agg = report.aggregate(d, root)
                snap = json.dumps(agg, sort_keys=True, ensure_ascii=False)
                with lock:
                    agg_seen[d].add(snap)
        except Exception as exc:  # noqa: BLE001 —— 并发回归就是要收集一切异常
            _record_err("aggregate", exc)

    def days_worker() -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(iters):
                if i % 10 == 9:
                    dashboard_util.invalidate_days_cache(root)  # 锤失效丢弃路径
                ds = tuple(dashboard_util._available_days(root))
                with lock:
                    days_seen.add(ds)
        except Exception as exc:  # noqa: BLE001
            _record_err("_available_days", exc)

    def collect_worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(iters):
                res = ai_sessions.collect(_DAYS[0], cfg)
                snap = json.dumps(res, sort_keys=True, ensure_ascii=False)
                with lock:
                    collect_seen.add(snap)
        except Exception as exc:  # noqa: BLE001
            _record_err("collect", exc)

    threads = [threading.Thread(target=agg_worker, args=(t,)) for t in range(4)]
    threads += [threading.Thread(target=days_worker) for _ in range(2)]
    threads += [threading.Thread(target=collect_worker) for _ in range(2)]
    assert len(threads) == n_threads
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "工作线程 30s 内未结束"

    # ① 零异常（加锁前此处会随机炸 OrderedDict mutated during iteration 等）
    assert not errors, f"并发下出现异常: {errors}"
    # ② 结果一致：同一天聚合口径在各线程完全一致，且与单线程参照相同
    for d in _DAYS:
        ref = json.dumps(report.aggregate(d, root), sort_keys=True, ensure_ascii=False)
        assert agg_seen[d] == {ref}, f"{d} 各线程聚合结果不一致"
        assert len(json.loads(ref).get("by_app", {})) == 1, "聚合结果应有内容"
    assert days_seen == {tuple(sorted(_DAYS))}, f"日期列表不一致: {days_seen}"
    assert len(collect_seen) == 1, f"collect 结果出现 {len(collect_seen)} 种口径"


def test_collect_cache_concurrent_invalidate(tmp_path):
    """并发 invalidate_collect_cache 与 collect 读互踩：清两表与查/写并发零异常。"""
    root = _make_fixture(tmp_path)
    sess = os.path.join(root, "sess")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"t": [sess]}}, "data_root": root}

    errors: list[str] = []
    stop = threading.Event()

    def collect_worker() -> None:
        try:
            while not stop.is_set():
                ai_sessions.collect(_DAYS[0], cfg)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"collect: {exc!r}")

    workers = [threading.Thread(target=collect_worker) for _ in range(4)]
    for t in workers:
        t.start()
    try:
        for _ in range(50):  # 主线程高频清缓存，与读者互踩
            ai_sessions.invalidate_collect_cache()
    finally:
        stop.set()
        for t in workers:
            t.join(timeout=30)
    assert not errors, f"invalidate 与 collect 并发下出现异常: {errors}"


def test_aliases_cache_concurrent_hammer(tmp_path, monkeypatch):
    """并发锤 report._aliases_cache（5s TTL 组）：零异常且各线程结果一致。

    用假时钟让一半调用越过 TTL，交替触发「TTL 内命中读」与「过期清空重扫」
    两条路径——过期 clear() 与并发读/写互踩正是无锁时最危险的窗口
    （check-then-read 间隙可抛 KeyError）。
    """
    root = str(tmp_path / "adata")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "aliases.json"), "w", encoding="utf-8") as fh:
        json.dump({"u0": "张三", "u1": "李四"}, fh)

    real_monotonic = time.monotonic
    state = {"t": real_monotonic(), "n": 0}
    clk_lock = threading.Lock()

    def fake_monotonic() -> float:
        with clk_lock:
            state["n"] += 1
            state["t"] += 6.0 if state["n"] % 2 == 0 else 0.001  # 一半调用越过 5s TTL
            return state["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    n_threads, iters = 6, 60
    barrier = threading.Barrier(n_threads)
    errors: list[str] = []
    seen: set = set()
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(iters):
                a = report._get_aliases(root)
                snap = json.dumps(a, sort_keys=True, ensure_ascii=False)
                with lock:
                    seen.add(snap)
        except Exception as exc:  # noqa: BLE001 —— 并发回归就是要收集一切异常
            with lock:
                errors.append(f"_get_aliases: {exc!r}")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"并发下出现异常: {errors}"
    expected = json.dumps({"u0": "张三", "u1": "李四"}, sort_keys=True, ensure_ascii=False)
    assert seen == {expected}, f"别名表各线程结果不一致: {seen}"
