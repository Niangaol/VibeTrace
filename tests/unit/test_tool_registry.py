# -*- coding: utf-8 -*-
"""tests/unit/test_tool_registry.py — 工具注册表与命名打通（Phase 3）。

覆盖：
- canonical_tool_key / resolve_tool：两套命名（collect 键 ↔ 进程显示名 ↔ 别名）归一；
- TOOLS 完整性：21 个本地工具 + 纯 Web AI 工具，字段约束；
- tool_compare join 修复：by_ai 显示名（"pi agent"/"claude code"）归并到 collect 键；
- _iter_tool_messages 行为锁：zcode 独占目录不再 walk、opencode db+json 双源；
- model_vendor：模型前缀 → 厂商（最长前缀优先；dsh 不是模型前缀）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import tool_compare  # noqa: E402
import tool_registry  # noqa: E402

_DAY = "2026-01-01"


# ---------------------------------------------------------------------------
# 命名归一
# ---------------------------------------------------------------------------
class TestCanonicalNames:
    def test_canonical_strips_separators(self):
        # 小写 + 去空格/横杠/下划线/点：所有分隔风格收敛到同一串
        assert tool_registry.canonical_tool_key("pi agent") == "piagent"
        assert tool_registry.canonical_tool_key("pi-agent") == "piagent"
        assert tool_registry.canonical_tool_key("pi_agent") == "piagent"
        assert tool_registry.canonical_tool_key("Pi.Agent") == "piagent"
        assert tool_registry.canonical_tool_key("  CLAUDE CODE ") == "claudecode"

    def test_resolve_pi_family(self):
        for name in ("pi_agent", "pi agent", "pi-agent", "pi", "PI_AGENT", "Pi Agent"):
            spec = tool_registry.resolve_tool(name)
            assert spec is not None and spec.key == "pi_agent", name
            assert spec.parser == "pi" and spec.cache_tag == "pi"

    def test_resolve_claude_family(self):
        # "claude code" 是 claude 的进程侧别名（同一工具的 CLI 入口）
        for name in ("claude", "claude code", "Claude Code", "claude-code"):
            spec = tool_registry.resolve_tool(name)
            assert spec is not None and spec.key == "claude", name

    def test_resolve_exact_key_and_unknown(self):
        assert tool_registry.resolve_tool("zcode").key == "zcode"
        assert tool_registry.resolve_tool("nope-xyz") is None
        assert tool_registry.resolve_tool("") is None

    def test_registry_is_pure_data_module(self):
        # 防循环依赖：tool_registry 的真实 import 语句里不得出现 ai_sessions
        # （用 AST 检查，避免 docstring 里的文字误报）
        import ast
        import importlib.util
        spec = importlib.util.find_spec("tool_registry")
        tree = ast.parse(open(spec.origin, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert "ai_sessions" not in imported
        assert imported <= {"__future__", "dataclasses"}, imported


# ---------------------------------------------------------------------------
# TOOLS 完整性
# ---------------------------------------------------------------------------
class TestToolsTable:
    LOCAL_KEYS = {
        "opencode", "chatgpt", "claude", "cursor", "windsurf", "trae", "deepseek",
        "pi_agent", "dsh", "qwen", "glm", "doubao", "kimi", "marscode", "codebuddy",
        "minimax", "stepfun", "yi", "baichuan", "zcode", "codex",
    }
    WEB_ONLY_KEYS = {"gemini", "perplexity", "copilot", "metaso"}

    def test_local_tools_have_paths(self):
        # 21 个本地工具每个都有默认扫描目录；键与历史 _DEFAULT_PATHS 完全一致
        local = {k for k, s in tool_registry.TOOLS.items() if s.default_paths}
        assert local == self.LOCAL_KEYS

    def test_web_only_tools_registered(self):
        for key in self.WEB_ONLY_KEYS:
            spec = tool_registry.TOOLS[key]
            assert spec.web_domains and not spec.default_paths, key

    def test_field_integrity(self):
        for key, spec in tool_registry.TOOLS.items():
            assert spec.key == key
            assert spec.display_name.strip()
            if spec.default_paths:
                assert all(p.strip() for p in spec.default_paths)
            if spec.parser is not None:
                assert spec.parser in ("codex", "dsh", "pi")
                assert spec.cache_tag == spec.parser
            if spec.cache_size_exempt:
                assert spec.cache_tag in ("dsh", "codex")
        # 别名不得与其他工具的 key 冲突（否则 resolve 有歧义）
        for spec in tool_registry.TOOLS.values():
            for alias in spec.aliases:
                hit = tool_registry.resolve_tool(alias)
                assert hit is not None and hit.key == spec.key, alias

    def test_generated_tables_match_registry(self):
        # ai_sessions 的两张表由注册表生成：键集合一致、Web 域名同源
        assert set(ai_sessions._DEFAULT_PATHS) == self.LOCAL_KEYS
        assert len(ai_sessions._WEB_AI_TOOLS) == 11
        for key, domains in ai_sessions._WEB_AI_TOOLS.items():
            assert tool_registry.TOOLS[key].web_domains == domains

    def test_sqlite_flags_behavior_aligned(self):
        # zcode：独占（有 db 不再 walk）；opencode：双源（读完 db 继续 walk）
        zc = tool_registry.TOOLS["zcode"]
        assert zc.sqlite_file == "db/db.sqlite" and zc.sqlite_exclusive and not zc.sqlite_immutable
        oc = tool_registry.TOOLS["opencode"]
        assert oc.sqlite_file == "opencode.db" and not oc.sqlite_exclusive and oc.sqlite_immutable

    def test_cache_exempt_tags_derived(self):
        assert ai_sessions._CACHE_SIZE_EXEMPT_TAGS == frozenset({"dsh", "codex"})


# ---------------------------------------------------------------------------
# tool_compare join：by_ai 显示名 → collect 键的分钟归并（Phase 3 修复）
# ---------------------------------------------------------------------------
def _stat(tool: str, day: str = "d1", tokens: int = 1000, cost: float = 0.1) -> dict:
    return {"files": 1, "turns": 2, "rounds": 1, "user_messages": 1,
            "assistant_messages": 1, "generated_lines": 10, "generated_chars": 100,
            "tokens_in": tokens // 2, "tokens_out": tokens // 2, "tokens_total": tokens,
            "tokens_input_fresh": 0, "tokens_cache_read": 0, "tokens_cache_write": 0,
            "cost_in": cost / 2, "cost_out": cost / 2, "cost_total": cost,
            "by_model": {"m1": {"turns": 2, "tokens_total": tokens}},
            "by_project": {"p1": {"turns": 2, "tokens_total": tokens}},
            "conversations": [{"id": f"{tool}-c1", "turns": 1, "project": "p1",
                               "user_messages": 1, "assistant_messages": 1,
                               "quality_score": 80}]}


def _patch_sources(monkeypatch, per_day, minutes_ms):
    def fake_collect(day, config):
        return {"date": day, "enabled": True, "found": bool(per_day.get(day)),
                "tools": per_day.get(day, {}), "web_ai": {}, "total": {}}

    def fake_aggregate(day, data_root):
        return {"by_ai": dict(minutes_ms.get(day, {})), "sessions": []}

    monkeypatch.setattr(tool_compare.ai_sessions, "collect", fake_collect)
    monkeypatch.setattr(tool_compare.report, "aggregate", fake_aggregate)


class TestCompareJoin:
    def test_display_names_merge_into_collect_keys(self, monkeypatch):
        """by_ai 用进程显示名（"pi agent"/"claude code"）→ minutes 归并到 collect 键。"""
        _patch_sources(monkeypatch,
                       {_DAY: {"pi_agent": _stat("pi_agent"), "claude": _stat("claude")}},
                       {_DAY: {"pi agent": 60000, "claude code": 120000}})
        res = tool_compare.compare_tools([_DAY], "<root>", {"tool_compare": {"min_sessions": 0}})
        by_tool = {t["tool"]: t for t in res["tools"]}
        # 修复前 by_ai.get("pi_agent") 恒 None → minutes=0；修复后归并显示名毫秒数
        assert by_tool["pi_agent"]["minutes"] == 1.0
        assert by_tool["claude"]["minutes"] == 2.0

    def test_same_tool_multiple_display_names_merge(self, monkeypatch):
        """同一工具在 by_ai 的多个显示名（"claude" 与 "claude code"）毫秒数求和。"""
        _patch_sources(monkeypatch,
                       {_DAY: {"claude": _stat("claude")}},
                       {_DAY: {"claude": 60000, "claude code": 60000}})
        res = tool_compare.compare_tools([_DAY], "<root>", {"tool_compare": {"min_sessions": 0}})
        assert res["tools"][0]["minutes"] == 2.0

    def test_unknown_by_ai_keys_kept_verbatim(self, monkeypatch):
        """解析不了的 by_ai 键保持原样匹配（自定义工具行为不回退）。"""
        _patch_sources(monkeypatch,
                       {_DAY: {"mytool": _stat("mytool")}},
                       {_DAY: {"mytool": 120000, "other": 999999}})
        res = tool_compare.compare_tools([_DAY], "<root>", {"tool_compare": {"min_sessions": 0}})
        by_tool = {t["tool"]: t for t in res["tools"]}
        assert by_tool["mytool"]["minutes"] == 2.0  # 120000ms；other 不串门


# ---------------------------------------------------------------------------
# _iter_tool_messages 行为锁（注册表驱动后须与原 if/elif 完全一致）
# ---------------------------------------------------------------------------
def _mk_sqlite_db(db_dir: str, name: str, when_ms: int) -> str:
    """构造 opencode 同源三表 fixture（session/message/part）。"""
    os.makedirs(db_dir, exist_ok=True)
    path = os.path.join(db_dir, name)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT);
        """
    )
    conn.execute("INSERT INTO session VALUES ('s1', 'D:/proj/demo')")
    conn.execute("INSERT INTO message VALUES ('m1', 's1', ?, ?)",
                 (when_ms, json.dumps({"role": "user"})))
    conn.execute("INSERT INTO part VALUES ('p1', 'm1', ?)",
                 (json.dumps({"type": "text", "text": "写个函数"}),))
    conn.commit()
    conn.close()
    return path


def _local_ms(day: str, h: int) -> int:
    import datetime
    return int(datetime.datetime.fromisoformat(f"{day}T{h:02d}:00:00").timestamp() * 1000)


class TestIterToolMessagesLocks:
    def test_zcode_db_dir_exclusive_no_walk(self, tmp_path):
        """zcode 目录有 db/db.sqlite 时不再通用 walk：log 噪声 JSONL 不进统计。"""
        import datetime
        day = datetime.date.today().isoformat()  # 当日，避开 dsh 式未来日期问题
        cli = tmp_path / "cli"
        _mk_sqlite_db(str(cli / "db"), "db.sqlite", _local_ms(day, 10))
        noise = cli / "log"
        noise.mkdir()
        (noise / f"zcode-{day}.jsonl").write_text(
            json.dumps({"level": "info", "msg": "noise", "ts": _local_ms(day, 11)}) + "\n",
            encoding="utf-8")
        cfg = {"ai_sessions": {"enabled": True, "paths": {"zcode": [str(cli)]}}}
        ai_sessions.invalidate_collect_cache()
        data = ai_sessions.collect(day, cfg)
        st = data["tools"]["zcode"]
        assert st["turns"] == 1, f"只应有 sqlite 的 1 条消息，实际 {st['turns']}"

    def test_opencode_db_and_json_dual_source(self, tmp_path):
        """opencode 双源：先读 opencode.db，同目录 jsonl 也继续通用解析。"""
        import datetime
        day = datetime.date.today().isoformat()
        oc = tmp_path / "oc"
        oc.mkdir()
        _mk_sqlite_db(str(oc), "opencode.db", _local_ms(day, 10))
        (oc / "sessions.jsonl").write_text(json.dumps(
            {"timestamp": f"{day}T12:00:00", "role": "user", "content": "hi",
             "message": {"model": "claude-opus-5"}}) + "\n", encoding="utf-8")
        cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [str(oc)]}}}
        ai_sessions.invalidate_collect_cache()
        data = ai_sessions.collect(day, cfg)
        st = data["tools"]["opencode"]
        assert st["turns"] == 2, f"db+jsonl 双源应解析 2 条，实际 {st['turns']}"
        assert "claude-opus-5" in st["by_model"]

    def test_unknown_tool_generic_and_pi_prefix(self):
        """未注册工具走通用流程；"pi" 前缀的未注册键仍按 pi 解析（历史行为）。"""
        msgs_generic = ai_sessions._iter_tool_messages("mytool", [], _DAY, set())
        msgs_pi = ai_sessions._iter_tool_messages("pixie", [], _DAY, set())
        assert msgs_generic == [] and msgs_pi == []  # 空目录不抛即可（路由不炸）

    def test_alias_key_routes_like_canonical(self, monkeypatch, tmp_path):
        """config.paths 用别名键（如 "pi-agent"）时与 "pi_agent" 走同一解析流程。"""
        import datetime
        day = datetime.date.today().isoformat()
        d = tmp_path / "pi"
        d.mkdir()
        # pi 专用格式（model_change 上下文）与通用格式各放一份，验证 pi 解析生效
        (d / "s.jsonl").write_text(json.dumps(
            {"type": "message", "role": "user", "content": "hi",
             "timestamp": f"{day}T10:00:00"}) + "\n", encoding="utf-8")
        cfg = {"ai_sessions": {"enabled": True, "paths": {"pi-agent": [str(d)]}}}
        ai_sessions.invalidate_collect_cache()
        data = ai_sessions.collect(day, cfg)
        assert "pi-agent" in data["tools"]  # 输出键保持 config 原名（不改名）
        assert data["tools"]["pi-agent"]["turns"] == 1


# ---------------------------------------------------------------------------
# 模型厂商映射
# ---------------------------------------------------------------------------
class TestModelVendor:
    def test_longest_prefix_wins(self):
        assert tool_registry.model_vendor("codestral-2505") == "Mistral"
        assert tool_registry.model_vendor("gpt-5.4") == "OpenAI"
        assert tool_registry.model_vendor("o3-mini") == "OpenAI"
        assert tool_registry.model_vendor("claude-opus-5") == "Anthropic"
        assert tool_registry.model_vendor("glm-5.3") == "Zhipu"
        assert tool_registry.model_vendor("kimi-k3") == "Moonshot"
        assert tool_registry.model_vendor("step-3") == "StepFun"
        assert tool_registry.model_vendor("yi-large") == "01.AI"
        assert tool_registry.model_vendor("hunyuan-turbo") == "Tencent"
        assert tool_registry.model_vendor("ernie-5") == "Baidu"

    def test_dsh_is_not_a_model_prefix(self):
        # dsh 是工具名不是模型名：前端旧 bug 不在后端注册表复现
        assert tool_registry.model_vendor("dsh") is None
        prefixes = [p for p, _ in tool_registry.MODEL_VENDOR_PREFIXES]
        assert "dsh" not in prefixes

    def test_unknown_returns_none(self):
        assert tool_registry.model_vendor("") is None
        assert tool_registry.model_vendor("zzz-unknown") is None
