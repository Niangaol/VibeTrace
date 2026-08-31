# -*- coding: utf-8 -*-
"""tests/unit/test_zcode_support.py — zcode 适配（db.sqlite + v2/sessions 通用解析）。

zcode 双数据源（路径风格与真实布局同构）：
- <cli>/db/db.sqlite：opencode 同源 schema（session/message/part，message.data 含
  role/modelID，part.data 为 {type:"text",text}），活跃 WAL 库 → mode=ro 读取；
- v2/sessions/<hash>/<taskId>.json：顶层 meta+messages，role/content(ms epoch)/
  turnIndex，通用解析器可直接解析；无 model 字段 → 如实"未识别"。
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402

_DAY = "2026-01-01"


def _ms(h: int, m: int = 0, s: int = 0) -> int:
    """本地时区的当日毫秒 epoch（与 _parse_opencode_db 的 fromtimestamp 口径一致）。"""
    dt = datetime.datetime.fromisoformat(f"{_DAY}T{h:02d}:{m:02d}:{s:02d}")
    return int(dt.timestamp() * 1000)


def _mk_zcode_db(db_dir: str) -> str:
    """构造 zcode db.sqlite 三表 fixture（与真实库 schema 同构）。"""
    os.makedirs(db_dir, exist_ok=True)
    path = os.path.join(db_dir, "db.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT);
        """
    )
    conn.execute("INSERT INTO session VALUES (?, ?)", ("s1", "D:/proj/demo"))
    conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                 ("m1", "s1", _ms(10), json.dumps({"role": "user"})))
    conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                 ("m2", "s1", _ms(10, 0, 5),
                  json.dumps({"role": "assistant", "modelID": "GLM-5.3",
                              "providerID": "builtin:test"})))
    conn.execute("INSERT INTO part VALUES (?, ?, ?)",
                 ("p1", "m1", json.dumps({"type": "text", "text": "写个函数"})))
    conn.execute("INSERT INTO part VALUES (?, ?, ?)",
                 ("p2", "m2", json.dumps({"type": "text", "text": "def f(): pass"})))
    conn.commit()
    conn.close()
    return path


def test_parse_zcode_db_messages(tmp_path):
    """_parse_opencode_db(immutable=False) 读 zcode 库：role/model/文本/项目。"""
    db = _mk_zcode_db(str(tmp_path / "db"))
    msgs = ai_sessions._parse_opencode_db(db, _DAY, immutable=False)
    assert len(msgs) == 2, f"应解析 2 条当日消息，实际 {len(msgs)}"
    assert msgs[0]["role"] == "user"
    assert "写个函数" in msgs[0]["content"]
    assert msgs[1]["model"] == "glm-5.3"  # modelID 归一化（小写）
    assert "def f()" in msgs[1]["content"]
    assert msgs[1]["project"] == "demo"  # session.directory 末段
    assert msgs[1]["conv_id"] == "s1"


def test_collect_zcode_sqlite(tmp_path):
    """collect 走 zcode paths：cli 目录识别 db/db.sqlite，全链路出数。"""
    cli = tmp_path / "cli"
    _mk_zcode_db(str(cli / "db"))
    cfg = {"ai_sessions": {"enabled": True, "paths": {"zcode": [str(cli)]}}}
    data = ai_sessions.collect(_DAY, cfg)
    assert "zcode" in data["tools"], f"zcode 应在 tools 中，实际 {list(data['tools'])}"
    st = data["tools"]["zcode"]
    assert st["turns"] == 2
    assert st["user_messages"] == 1 and st["assistant_messages"] == 1
    assert st["rounds"] == 1
    assert "glm-5.3" in st["by_model"]
    # 成本：glm-5.3 经子串匹配命中既有 glm-5 定价（不编造新价格）
    assert st["cost_total"] > 0


def test_collect_zcode_v2_sessions(tmp_path):
    """v2/sessions 的 meta+messages JSON 走通用解析（role/content/ms timestamp）。"""
    sdir = tmp_path / "v2" / "sessions" / "hash1"
    os.makedirs(sdir)
    payload = {
        "meta": {"taskId": "task-1", "workspacePath": "D:/proj/demo2",
                 "createdAt": _ms(11), "updatedAt": _ms(11, 1), "status": "completed"},
        "messages": [
            {"role": "user", "content": "你好", "timestamp": _ms(11), "turnIndex": 0},
            {"role": "assistant", "content": "你好！有什么可以帮你？",
             "timestamp": _ms(11, 0, 30), "turnIndex": 0},
        ],
    }
    (sdir / "task-1.json").write_text(json.dumps(payload, ensure_ascii=False),
                                      encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True,
                           "paths": {"zcode": [str(tmp_path / "v2" / "sessions")]}}}
    data = ai_sessions.collect(_DAY, cfg)
    st = data["tools"]["zcode"]
    assert st["turns"] == 2
    assert st["rounds"] == 1
    assert st["generated_chars"] > 0


def test_zcode_default_paths_registered():
    """_default_tool_paths() 注册 zcode（默认探测路径存在）。"""
    paths = ai_sessions._default_tool_paths()
    assert "zcode" in paths, f"zcode 应在默认路径表中，实际 {sorted(paths)}"


def test_zcode_db_fingerprint_tracks_wal(tmp_path):
    """cli 目录只指纹 db.sqlite(-wal)：touch wal 即失效 collect 缓存。"""
    cli = tmp_path / "cli"
    _mk_zcode_db(str(cli / "db"))
    cfg = {"ai_sessions": {"enabled": True, "paths": {"zcode": [str(cli)]}}}
    ai_sessions.invalidate_collect_cache()
    r1 = ai_sessions.collect(_DAY, cfg)
    assert r1["tools"]["zcode"]["turns"] == 2
    r2 = ai_sessions.collect(_DAY, cfg)
    assert r2["tools"] is r1["tools"]  # 指纹未变 → 命中缓存（同一共享对象）
    wal = cli / "db" / "db.sqlite-wal"
    wal.write_text("x", encoding="utf-8")
    os.utime(wal, None)
    r3 = ai_sessions.collect(_DAY, cfg)
    assert r3["tools"] is not r1["tools"]  # wal 变化 → 缓存失效重算
    assert r3["tools"]["zcode"]["turns"] == 2


def test_zcode_cli_dir_skips_walk_noise(tmp_path):
    """cli 目录下 log/rollout 等 JSON 不进通用 walk（只认 db/db.sqlite）。"""
    cli = tmp_path / "cli"
    _mk_zcode_db(str(cli / "db"))
    log_dir = cli / "log"
    os.makedirs(log_dir)
    ts = _ms(12)
    (log_dir / f"zcode-{_DAY}.jsonl").write_text(
        json.dumps({"level": "info", "msg": "noise", "ts": ts}) + "\n", encoding="utf-8")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"zcode": [str(cli)]}}}
    ai_sessions.invalidate_collect_cache()
    data = ai_sessions.collect(_DAY, cfg)
    st = data["tools"]["zcode"]
    # 只有 sqlite 的 2 条消息；log 噪声行不计数
    assert st["turns"] == 2, f"log 噪声不应计入，实际 turns={st['turns']}"
