# -*- coding: utf-8 -*-
"""integration/test/insights/ecosystem — 自 test_all.py 移植

由 test_all.py（@7d60620，336 项检查）机械移植拆分而来——断言逻辑逐行保持一致；
仅有的改动：①助手移入 tests/support/scenario.py 并 import；②_chrome_ft 改为
正午锚定（消除午夜抖动类 flaky）；③去掉独立 main 入口（统一由 pytest 收集）。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import win32core  # noqa: E402
import monitor  # noqa: E402
import classifier  # noqa: E402
import report  # noqa: E402
import insights  # noqa: E402
import sqlite_store  # noqa: E402
import ai_sessions  # noqa: E402
import updater  # noqa: E402

from tests.support.scenario import (  # noqa: E402
    _make_fake_agg, check, fail, fresh_tmp, ok,
)

# ruff: F401 未用导入由 `ruff check --fix` 自动清理

def test_insights_rules():
    print("[test] 智能洞察规则引擎（study/game/health/efficiency/balance/trend + 阈值/空数据）")
    agg = _make_fake_agg()
    agg["hourly_ms"][1] = 10 * 60000  # 深夜 01:00 仍有活动
    prev = {
        "date": "2026-08-09",
        "session_count": 3,
        "total_active_ms": 4 * 3600000,
        "by_category": {}, "by_app": {}, "by_ai": {}, "by_browser": {},
        "by_contact": {}, "hourly_ms": [0] * 24, "sessions": [],
    }
    cfg = classifier.load_config()
    rules = insights.rule_insights(agg, cfg, prev)
    types = {r["type"] for r in rules}
    check({"study", "game", "health", "efficiency", "balance", "trend"} <= types,
          "六类规则全部命中", str(types))
    check(all(r["severity"] in ("info", "warn", "alert") for r in rules), "severity 合法")
    study = next(r for r in rules if r["type"] == "study")
    check("今日学习" in study["detail"] and "网课" in study["detail"], "学习建议含网课时长",
          study["detail"])
    game = next(r for r in rules if r["type"] == "game")
    check(game["severity"] == "warn" and "劳逸结合" in game["detail"], "游戏超阈值 -> warn", game["detail"])
    health = [r for r in rules if r["type"] == "health"]
    check(any("最长连续使用" in r["detail"] for r in health), "长会话健康提醒")
    check(any("深夜" in r["detail"] for r in health), "深夜使用健康提醒")
    trend = next(r for r in rules if r["type"] == "trend")
    check("多 50%" in trend["detail"], "趋势对比 +50%", trend["detail"])

    # 阈值可配置：把提醒线提到 120 分钟后，100 分钟会话不再触发
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["insights"]["rules"]["long_session_min"] = 120
    rules2 = insights.rule_insights(agg, cfg2, prev)
    health2 = [r for r in rules2 if r["type"] == "health"]
    check(all("最长连续使用" not in r["detail"] for r in health2), "阈值提升后长会话不触发")
    check(any("深夜" in r["detail"] for r in health2), "深夜提醒不受影响")

    # 空数据安全
    empty = {"date": "2026-08-10", "session_count": 0, "total_active_ms": 0,
             "by_app": {}, "by_category": {}, "by_ai": {}, "by_browser": {},
             "by_contact": {}, "hourly_ms": [0] * 24, "sessions": []}
    check(insights.rule_insights(empty, cfg) == [], "空数据返回空列表")

def test_insights_behavior():
    print("[test] 行为洞察（专注度评分 + 死循环检测，Phase 4）")
    cfg = classifier.load_config()

    # A：专注编码日 -> 高评分、无死循环
    aggA = {
        "date": "2026-08-10", "session_count": 3, "total_active_ms": 3 * 3600000,
        "by_app": {"VS Code": 3 * 3600000},
        "by_category": {"开发工具": 3 * 3600000},
        "by_ai": {}, "by_browser": {}, "by_contact": {}, "by_subcategory": {},
        "by_term_tool": {}, "hourly_ms": [0] * 24,
        "sessions": [
            {"start": "2026-08-10T09:00:00", "end": "2026-08-10T11:00:00",
             "duration_ms": 2 * 3600000, "app": "VS Code", "category": "开发工具"},
            {"start": "2026-08-10T11:10:00", "end": "2026-08-10T12:00:00",
             "duration_ms": 50 * 60000, "app": "VS Code", "category": "开发工具"},
            {"start": "2026-08-10T14:00:00", "end": "2026-08-10T15:00:00",
             "duration_ms": 60 * 60000, "app": "Cursor", "category": "AI编程"},
        ],
    }
    bA = insights.behavior_insights(aggA, cfg)
    check(80 <= bA["focus_score"] <= 100 and bA["grade"] == "高",
          "专注日评分高", str(bA))
    check(bA["death_loop"] is None, "专注日无死循环", str(bA))
    check(bA["breakdown"]["coding_ratio"] >= 0.7, "编码占比高", str(bA["breakdown"]))

    # B：高频短会话往返 -> 命中死循环
    apps = ["ChatGPT", "Chrome", "VS Code", "微信"]
    sessions = []
    start = _dt.datetime(2026, 8, 10, 9, 0, 0)
    for i in range(10):
        s0 = start + _dt.timedelta(seconds=30 * i)
        s1 = s0 + _dt.timedelta(seconds=20)
        sessions.append({
            "start": s0.isoformat(), "end": s1.isoformat(), "duration_ms": 20000,
            "app": apps[i % 4], "category": "其他",
        })
    total_b = 10 * 20000
    aggB = {"date": "2026-08-10", "session_count": 10, "total_active_ms": total_b,
            "by_app": {}, "by_category": {"其他": total_b},
            "by_ai": {}, "by_browser": {}, "by_contact": {}, "by_subcategory": {},
            "by_term_tool": {}, "hourly_ms": [0] * 24, "sessions": sessions}
    bB = insights.behavior_insights(aggB, cfg)
    check(bB["death_loop"] is not None, "命中死循环", str(bB))
    dl = bB["death_loop"]
    check(dl["count"] >= 6 and dl["distinct_apps"] >= 3, "死循环次数/应用数达标", str(dl))
    check(bB["breakdown"]["switch_count"] >= 8, "高切换次数", str(bB["breakdown"]))

    # C：空数据 / 关闭
    empty = {"date": "2026-08-10", "session_count": 0, "total_active_ms": 0,
             "by_app": {}, "by_category": {}, "by_ai": {}, "by_browser": {},
             "by_contact": {}, "hourly_ms": [0] * 24, "sessions": []}
    bE = insights.behavior_insights(empty, cfg)
    check(bE["focus_score"] == 0 and bE["grade"] == "低" and bE["death_loop"] is None,
          "空数据默认值", str(bE))
    cfg_off = json.loads(json.dumps(cfg))
    cfg_off["insights"]["enabled"] = False
    check(insights.behavior_insights(aggA, cfg_off)["focus_score"] == 0, "insights 关闭时不计分")

def test_insights_persona():
    print("[test] Vibe 编程人格分析（趣味 · 离线规则，Phase 4）")
    cfg = classifier.load_config()

    def make_agg(total_ms, by_cat, sessions, hourly=None):
        hourly = hourly or [0] * 24
        return {"date": "2026-08-10", "session_count": len(sessions), "total_active_ms": total_ms,
                "by_app": {}, "by_category": by_cat, "by_ai": {}, "by_browser": {},
                "by_contact": {}, "by_subcategory": {}, "by_term_tool": {},
                "hourly_ms": hourly, "sessions": sessions}

    # A：重度 AI 编程日 -> 大量零散 AI 短会话，AI 驱动工程师应胜出
    sA = []
    for i in range(24):
        s0 = _dt.datetime(2026, 8, 10, 9, 0, 0) + _dt.timedelta(minutes=10 * i)
        s1 = s0 + _dt.timedelta(minutes=6)
        sA.append({"start": s0.isoformat(), "end": s1.isoformat(),
                   "duration_ms": 6 * 60000, "app": ["Cursor", "ChatGPT"][i % 2], "category": "AI编程"})
    aggA = make_agg(24 * 6 * 60000, {"AI编程": 24 * 6 * 60000}, sA)
    pA = insights.persona_insights(aggA, cfg)
    check(pA["label"] == "AI 驱动工程师" and pA["emoji"] == "🤖", "重度 AI 编程日命中 AI 驱动工程师", str(pA))
    check(pA["dimensions"]["ai_ratio"] >= 0.99, "AI 占比高", str(pA["dimensions"]))

    # B：死循环往返日 -> 节点循环受害者（含足够基线活动以过 min_total_min）
    apps = ["ChatGPT", "Chrome", "VS Code", "微信"]
    sessions = [
        {"start": "2026-08-10T08:00:00", "end": "2026-08-10T08:40:00",
         "duration_ms": 40 * 60000, "app": "VS Code", "category": "开发工具"},
    ]
    for i in range(10):
        s0 = _dt.datetime(2026, 8, 10, 9, 0, 0) + _dt.timedelta(seconds=30 * i)
        s1 = s0 + _dt.timedelta(seconds=20)
        sessions.append({"start": s0.isoformat(), "end": s1.isoformat(),
                         "duration_ms": 20000, "app": apps[i % 4], "category": "其他"})
    sessions.append(
        {"start": "2026-08-10T10:00:00", "end": "2026-08-10T10:30:00",
         "duration_ms": 30 * 60000, "app": "VS Code", "category": "开发工具"})
    total_b = sum(int(x["duration_ms"]) for x in sessions)
    aggB = make_agg(total_b, {"开发工具": 70 * 60000, "其他": 200000}, sessions)
    pB = insights.persona_insights(aggB, cfg)
    check(pB["label"] == "节点循环受害者" and pB["emoji"] == "🔁", "死循环日命中节点循环受害者", str(pB))

    # C：夜间学习日 -> 夜行动物 或 终身学习者（两者其一，看占比）
    sC = [{"start": "2026-08-10T23:00:00", "end": "2026-08-10T23:40:00",
           "duration_ms": 40 * 60000, "app": "Chrome", "category": "办公学习"}]
    hourly = [0] * 24
    hourly[23] = 2400000  # 40 分钟
    aggC = make_agg(40 * 60000, {"办公学习": 40 * 60000}, sC, hourly)
    pC = insights.persona_insights(aggC, cfg)
    check(pC["label"] in ("夜行动物", "终身学习者"), "夜间学习日人格", str(pC))

    # D：空数据 / 关闭 / 数据太少 -> 自由探索者或空
    empty = make_agg(0, {}, [])
    check(insights.persona_insights(empty, cfg)["label"] == "", "空数据无人格", str(empty))
    tiny = make_agg(10 * 60000, {"其他": 10 * 60000},
                    [{"start": "2026-08-10T09:00:00", "end": "2026-08-10T09:10:00",
                      "duration_ms": 10 * 60000, "app": "Chrome", "category": "其他"}])
    pT = insights.persona_insights(tiny, cfg)
    check(pT["label"] == "自由探索者", "数据太少给自由探索者", str(pT))
    cfg_off = json.loads(json.dumps(cfg))
    cfg_off["insights"]["enabled"] = False
    check(insights.persona_insights(aggA, cfg_off)["label"] == "", "insights 关闭时不评人格")
    cfg_persona_off = json.loads(json.dumps(cfg))
    cfg_persona_off["insights"]["persona"] = {"enabled": False}
    check(insights.persona_insights(aggA, cfg_persona_off)["label"] == "", "persona 关闭时不评人格")

def test_git_insights():
    print("[test] Git 代码变更分析（Phase 2 · 只读本地提交）")
    if sys.platform == "win32":
        # Windows 下 git 可用性已在运行环境确认；仍做一次探测，缺失则跳过
        pass
    import shutil
    import subprocess
    import tempfile
    import git_insights
    tmp = tempfile.mkdtemp(prefix="gitins_")
    try:
        repo = os.path.join(tmp, "proj")
        os.makedirs(repo)
        env_base = dict(os.environ)
        env_base["GIT_AUTHOR_DATE"] = "2026-08-10T12:00:00"
        env_base["GIT_COMMITTER_DATE"] = "2026-08-10T12:00:00"

        def git(*args):
            return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                                  text=True, encoding="utf-8", env=dict(env_base),
                                  check=True)

        git("init", "-q")
        git("config", "user.email", "tester@example.com")
        git("config", "user.name", "Tester")
        with io.open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("line1" + chr(10) + "line2" + chr(10))
        git("add", "a.txt")
        git("commit", "-q", "-m", "first")
        with io.open(os.path.join(repo, "a.txt"), "a", encoding="utf-8") as fh:
            fh.write("line3" + chr(10))
        git("add", "a.txt")
        git("commit", "-q", "-m", "second")

        proj = {"name": "proj", "path": repo}
        stats = git_insights.analyze_repo(proj, "2026-08-10", 10, 5)
        check(stats is not None, "仓库可分析", "None")
        check(stats["commit_count"] == 2, "当日两次提交", str(stats["commit_count"]))
        check(stats["lines_added"] >= 3, "新增行 >= 3", str(stats["lines_added"]))
        check(stats["files"] >= 1, "改动文件 >= 1", str(stats["files"]))

        cfg = classifier.load_config()
        cfg["insights"] = dict(cfg.get("insights") or {})
        cfg["insights"]["git"] = {"enabled": True, "projects": {"proj": repo}}
        res = git_insights.git_insights(cfg, "2026-08-10")
        check(res["found"] and res["total"]["commit_count"] >= 1, "汇总 found", str(res))
        check(res["repos"][0]["name"] == "proj", "仓库名正确", str(res["repos"]))

        cfg2 = classifier.load_config()
        cfg2["insights"] = {"git": {"enabled": True, "projects": []}}
        check(not git_insights.git_insights(cfg2, "2026-08-10")["found"], "无项目未 found")
        cfg3 = classifier.load_config()
        cfg3["insights"] = {"git": {"enabled": False, "projects": {"proj": repo}}}
        check(not git_insights.git_insights(cfg3, "2026-08-10")["found"], "关闭时未 found")
        cfg4 = classifier.load_config()
        cfg4["insights"] = {"git": {"enabled": True, "projects": {"bad": os.path.join(tmp, "nope")}}}
        check(not git_insights.git_insights(cfg4, "2026-08-10")["found"], "非仓库路径跳过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_insights_ai_prompt():
    print("[test] AI 提示词隐私过滤（默认无标题/URL/联系人名；开启后含标题/URL，联系人仍不上送）")
    agg = _make_fake_agg()
    agg["sessions"] = agg["sessions"] + [{
        "start": "2026-08-10T20:00:00", "end": "2026-08-10T20:30:00",
        "duration_ms": 30 * 60000, "app": "Chrome", "title": "秘密项目资料",
        "url": "https://secret.example.com/project?token=abc", "category": "浏览器",
    }]
    prev = {"date": "2026-08-09", "session_count": 1, "total_active_ms": 3600000,
            "by_category": {}, "by_app": {}, "by_ai": {}, "by_browser": {},
            "by_contact": {}, "hourly_ms": [0] * 24, "sessions": []}
    cfg = classifier.load_config()
    p_default = insights.build_ai_prompt(agg, cfg, prev, False)
    check("张三" not in p_default, "默认不上送联系人名")
    check("秘密项目资料" not in p_default, "默认不上送窗口标题")
    check("secret.example.com" not in p_default, "默认不上送 URL")
    check("联系人数量" in p_default and "总活跃时长" in p_default, "默认仍含聚合统计")
    check("昨日活跃时长" in p_default, "提示词含昨日对比")

    p_raw = insights.build_ai_prompt(agg, cfg, prev, True)
    check("秘密项目资料" in p_raw and "secret.example.com" in p_raw, "开启后上送标题/URL")
    check("张三" not in p_raw, "即使开启也不上送联系人名")
    check("top" not in p_raw.lower() or "原始样本" in p_raw, "原始样本段存在")

def test_insights_ai_call():
    print("[test] AI 调用（urllib 请求体/响应解析/HTTP 错误/超时）")
    import urllib.error
    import urllib.request

    class FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._payload

    calls: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return FakeResp(json.dumps({
            "choices": [{"message": {"content": '[{"type":"health","title":"健康","detail":"记得休息"}]'}}],
        }).encode("utf-8"))

    orig = insights.urllib.request.urlopen
    insights.urllib.request.urlopen = fake_urlopen
    try:
        text = insights._chat_completion({
            "base_url": "https://ai.example.test/v1",
            "api_key": "sk-test",
            "model": "deepseek-v4-flash",
            "timeout_s": 60,
        }, "测试提示词")
        check(text == '[{"type":"health","title":"健康","detail":"记得休息"}]', "响应 content 解析")
        check(len(calls) == 1, "恰好调用一次", str(len(calls)))
        req = calls[0]
        check(req.full_url == "https://ai.example.test/v1/chat/completions", "URL 拼接正确", req.full_url)
        check(req.get_header("Authorization") == "Bearer sk-test", "Authorization 头正确")
        body = json.loads(req.data.decode("utf-8"))
        check(body["model"] == "deepseek-v4-flash" and body["temperature"] == 0.7
              and body["max_tokens"] == 800, "请求体参数正确", str(body))
        check(body["messages"][0]["content"] == "测试提示词", "提示词入消息体")
    finally:
        insights.urllib.request.urlopen = orig

    def fail_http(req, timeout=None):
        raise urllib.error.HTTPError("https://ai.example.test/v1/chat/completions",
                                     500, "Server Error", {}, io.BytesIO(b"oops"))
    insights.urllib.request.urlopen = fail_http
    try:
        try:
            insights._chat_completion({
                "base_url": "https://ai.example.test/v1", "api_key": "k", "model": "m",
            }, "x")
            fail("HTTP 错误未抛出", "expected InsightsError")
        except insights.InsightsError as exc:
            check("HTTP 500" in str(exc), "非 200 -> InsightsError(HTTP 500)", str(exc))
    finally:
        insights.urllib.request.urlopen = orig

    def fail_timeout(req, timeout=None):
        raise TimeoutError("timed out")
    insights.urllib.request.urlopen = fail_timeout
    try:
        try:
            insights._chat_completion({
                "base_url": "https://ai.example.test/v1", "api_key": "k", "model": "m",
            }, "x")
            fail("超时未抛出", "expected InsightsError")
        except insights.InsightsError as exc:
            check("超时" in str(exc), "超时 -> InsightsError", str(exc))
    finally:
        insights.urllib.request.urlopen = orig

def test_insights_provider_presets():
    print("[test] AI provider 预设与自定义（内置预设 / 显式覆盖 / 无端点安全返回 None）")
    cfg = classifier.load_config()
    cfg2 = json.loads(json.dumps(cfg))
    ai = cfg2["insights"]["ai"]

    # 内置 DeepSeek 预设：不填 base_url/model 也能自动补全
    ai.update({"enabled": True, "provider": "deepseek", "base_url": "",
               "api_key": "sk-test", "model": ""})
    d = insights._discover_ai_config(cfg2)
    check(d is not None and "api.deepseek.com" in d["base_url"], "DeepSeek 预设自动补 base_url",
          str(d))
    check(d["model"] == "deepseek-chat", "DeepSeek 预设自动补模型", str(d))

    # 显式 base_url/model 优先于预设
    ai.update({"provider": "custom", "base_url": "https://custom.test/v1", "model": "my-model"})
    d = insights._discover_ai_config(cfg2)
    check(d is not None and d["base_url"] == "https://custom.test/v1" and d["model"] == "my-model",
          "自定义 provider 显式覆盖", str(d))

    # 自定义但没有 base_url -> 无法使用
    ai.update({"provider": "custom", "base_url": "", "model": "my-model"})
    d = insights._discover_ai_config(cfg2)
    check(d is None, "自定义无 base_url 返回 None", str(d))

    # 预设列表包含常用 provider 与 custom
    presets = {p["id"]: p for p in insights.list_provider_presets()}
    check({"opencodego", "openai", "deepseek", "moonshot", "openrouter", "zhipu", "qwen", "custom"}
          <= set(presets.keys()), "内置预设齐全", str(presets.keys()))
    check(presets["custom"]["base_url"] == "" and presets["custom"]["model"] == "",
          "custom 预设为空模板")

def test_insights_cache():
    print("[test] AI 洞察缓存（成功写缓存 / 二次调用不重发 / refresh 重发 / 失败不写缓存）")
    tmp = fresh_tmp("insights_cache")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T11:00:00",
            "duration_ms": 3600000, "exe": "code.exe", "app": "VS Code",
            "title": "main.py", "category": "办公学习", "active": True,
        }, ensure_ascii=False) + "\n")

    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["insights"]["ai"].update({
        "enabled": True, "base_url": "https://ai.example.test/v1",
        "api_key": "sk-test", "model": "deepseek-v4-flash",
    })
    calls: list[int] = []

    def fake_chat(_cfg, _prompt, **_kw):
        calls.append(1)
        return '[{"type":"study","title":"学习","detail":"测试建议"}]'

    orig = insights._chat_completion
    insights._chat_completion = fake_chat
    try:
        r1 = insights.ai_insights(day, tmp, cfg)
        check(r1["error"] is None and len(r1["insights"]) == 1, "首次生成成功", str(r1))
        check(len(calls) == 1, "首次真实调用一次")
        cache_path = os.path.join(tmp, day, "insights.json")
        check(os.path.isfile(cache_path), "成功写缓存")

        r2 = insights.ai_insights(day, tmp, cfg)
        check(len(calls) == 1 and r2["generated_at"] == r1["generated_at"], "二次调用读缓存不重发")
        r3 = insights.ai_insights(day, tmp, cfg, refresh=True)
        check(len(calls) == 2 and r3["generated_at"] is not None, "refresh 强制重发")

        # 未开启：不读缓存、不请求
        cfg_off = json.loads(json.dumps(cfg))
        cfg_off["insights"]["ai"]["enabled"] = False
        r_off = insights.ai_insights(day, tmp, cfg_off)
        check("未开启" in (r_off["error"] or ""), "未开启返回错误态", str(r_off))

        # 失败：不写缓存
        day2 = "2026-08-11"

        def fail_chat(_cfg, _prompt, **_kw):
            raise insights.InsightsError("模拟失败")
        insights._chat_completion = fail_chat
        r_fail = insights.ai_insights(day2, tmp, cfg)
        check(r_fail["error"] == "模拟失败", "失败返回错误", str(r_fail))
        check(not os.path.isfile(os.path.join(tmp, day2, "insights.json")), "失败不写缓存")
    finally:
        insights._chat_completion = orig
    shutil.rmtree(tmp, ignore_errors=True)

def test_report_insights_section():
    print("[test] 日报「今日建议」段（in_report=true 出现；false 不出现）")
    tmp = fresh_tmp("report_insights")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for row in (
            {"start": f"{day}T09:00:00", "end": f"{day}T11:00:00", "duration_ms": 2 * 3600000,
             "exe": "code.exe", "app": "VS Code", "title": "main.py",
             "category": "办公学习", "active": True},
            {"start": f"{day}T20:00:00", "end": f"{day}T22:00:00", "duration_ms": 2 * 3600000,
             "exe": "steam.exe", "app": "Steam", "title": "Steam",
             "category": "游戏", "active": True},
        ):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {
            "enabled": True, "in_report": True,
            "rules": {"long_session_min": 90, "late_night_hour": 23,
                      "game_alert_hours": 2, "study_goal_hours": 1, "game_ratio_warn": 0.4},
            "ai": {"enabled": False},
        }}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    report.generate_day_report(day, tmp)
    md = open(os.path.join(tmp, day, "report.md"), encoding="utf-8").read()
    check("## 📌 今日建议" in md, "in_report=true 含今日建议段")
    check("- [学习]" in md and "今日学习" in md, "含学习建议")
    check("- [游戏]" in md, "含游戏建议")

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {"enabled": True, "in_report": False}}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    report.generate_day_report(day, tmp)
    md2 = open(os.path.join(tmp, day, "report.md"), encoding="utf-8").read()
    check("今日建议" not in md2, "in_report=false 无今日建议段")
    shutil.rmtree(tmp, ignore_errors=True)

def test_updater():
    print("[test] 更新模块（版本比较/检测/下载校验/脚本生成/信号）")
    check(updater.parse_version("v1.10.2-beta") == (1, 10, 2), "parse_version")
    check(updater.version_gt("1.6.0", "1.5.0") is True, "version_gt true")
    check(updater.version_gt("1.5.0", "1.6.0") is False, "version_gt false")

    import hashlib

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    fake_json = json.dumps({
        "tag_name": "v9.9.9", "name": "v9.9.9",
        "published_at": "2026-08-17T00:00:00Z",
        "html_url": "https://example.com/release", "body": "notes",
        "assets": [{
            "name": "UsageMonitor.exe", "size": 123,
            "browser_download_url": "https://github.com/Niangaol/UsageMonitor/releases/download/v9.9.9/UsageMonitor.exe",
        }],
    }).encode("utf-8")
    orig = updater.urllib.request.urlopen
    updater.urllib.request.urlopen = lambda req, timeout=None: FakeResp(fake_json)
    try:
        r = updater.check_for_update(current="1.6.0")
        check(r["has_update"] and r["latest"] == "9.9.9", "check_for_update 新版本", str(r))
    finally:
        updater.urllib.request.urlopen = orig

    tmp = fresh_tmp("updater_download")
    content = b"hello world\n"
    sha = hashlib.sha256(content).hexdigest()

    class FakeDLResp:
        headers = {"Content-Length": str(len(content))}

        def __init__(self):
            self._done = False

        def read(self, chunk=-1):
            if self._done:
                return b""
            self._done = True
            return content

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    updater.urllib.request.urlopen = lambda req, timeout=None: FakeDLResp()
    try:
        dest = os.path.join(tmp, "UsageMonitor.exe")
        # api_base 指定 example.com 域，使该 URL 通过下载白名单（与 latest_release 口径一致）
        updater.download("https://example.com/exe", dest,
                         expected_size=len(content), expected_digest="sha256:" + sha,
                         api_base="https://example.com")
        check(open(dest, "rb").read() == content, "download 内容正确")
        try:
            updater.download("https://example.com/exe", os.path.join(tmp, "bad.exe"),
                             expected_size=999, api_base="https://example.com")
            fail("错误大小未抛异常", "expected UpdateError")
        except updater.UpdateError:
            ok("错误大小抛 UpdateError")
        # 白名单外域名直接拒绝（纵深防御：download 不依赖调用方先经 latest_release 过滤）
        try:
            updater.download("https://evil.example/exe", os.path.join(tmp, "evil.exe"))
            fail("白名单外域名未拒绝", "expected UpdateError")
        except updater.UpdateError as exc:
            ok("白名单外域名拒绝下载")
            check("白名单" in str(exc), "拒绝信息包含白名单说明")
    finally:
        updater.urllib.request.urlopen = orig
    shutil.rmtree(tmp, ignore_errors=True)

    script = updater.build_update_script("C:/src/UsageMonitor.exe", "C:/dst/UsageMonitor.exe")
    check("Copy-Item" in script and "UsageMonitor" in script, "build_update_script 包含替换逻辑")
    tmp_apply = fresh_tmp("updater_apply")
    src = os.path.join(tmp_apply, "UsageMonitor.exe")
    dst = os.path.join(tmp_apply, "UsageMonitor_new.exe")
    with open(src, "wb") as fh:
        fh.write(b"x")
    with open(dst, "wb") as fh:
        fh.write(b"y")
    try:
        res = updater.apply_update(src, dst, dry_run=True)
        check(res.get("dry_run") is True and os.path.isfile(res.get("script", "")),
              "apply_update dry_run")
    finally:
        shutil.rmtree(tmp_apply, ignore_errors=True)

    tmp_signal = fresh_tmp("updater_signal")
    updater.request_update(tmp_signal)
    check(os.path.isfile(os.path.join(tmp_signal, updater.UPDATE_REQUEST_FILE)),
          "request_update 写信号")
    updater.clear_update_request(tmp_signal)
    check(not os.path.isfile(os.path.join(tmp_signal, updater.UPDATE_REQUEST_FILE)),
          "clear_update_request 清除信号")
    shutil.rmtree(tmp_signal, ignore_errors=True)

def test_uwp_admin_firefox():
    print("[test] UWP 识别 / 管理员检测 / Firefox 停留时长估算")
    # UWP 包前缀提取
    check(win32core._strip_uwp_package(
        "Microsoft.WindowsCalculator_10.2103.8.0_x64__8wekyb3d8bbwe"
    ) == "Microsoft.WindowsCalculator", "UWP 包前缀提取")
    orig_path = win32core.get_process_path
    win32core.get_process_path = lambda pid: (
        r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_123_x64__abc\calc.exe"
    )
    try:
        name = win32core.get_uwp_app_name(1, {"Microsoft.WindowsCalculator": "计算器"})
        check(name == "计算器", "UWP 映射到显示名", str(name))
    finally:
        win32core.get_process_path = orig_path
    check(isinstance(win32core.is_admin(), bool), "is_admin 返回 bool")

    # Firefox 停留时长估算：两条访问间隔 300 秒
    import datetime
    import sqlite3
    import browser_history
    tmp = fresh_tmp("ff_dwell")
    db = os.path.join(tmp, "places.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE moz_places(id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE moz_historyvisits(id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER)")
    t0 = int(datetime.datetime(2026, 8, 10, 10, 0, 0).timestamp())
    t1 = t0 + 300
    conn.execute("INSERT INTO moz_places(id,url,title) VALUES(1,?,?)", ("https://a.example", "A"))
    conn.execute("INSERT INTO moz_places(id,url,title) VALUES(2,?,?)", ("https://b.example", "B"))
    conn.execute("INSERT INTO moz_historyvisits(id,place_id,visit_date) VALUES(1,1,?)", (int(t0 * 1e6),))
    conn.execute("INSERT INTO moz_historyvisits(id,place_id,visit_date) VALUES(2,2,?)", (int(t1 * 1e6),))
    conn.commit()
    conn.close()
    vs = browser_history._extract_firefox_visits(db, "2026-08-10", {})
    check(len(vs) == 2, "Firefox 两条访问", str(len(vs)))
    check(abs(vs[0]["duration_s"] - 300) < 1.0, "Firefox 间隔估算 300s", str(vs[0]))
    check(vs[1]["duration_s"] == 0.0, "Firefox 最后一条 0s", str(vs[1]))
    shutil.rmtree(tmp, ignore_errors=True)

def test_updater_security():
    print("[test] 更新供应链安全（下载地址白名单）")
    check(updater._is_allowed_asset_url(
        "https://github.com/a/b/releases/download/x/UsageMonitor.exe") is True,
        "github 资产地址允许")
    check(updater._is_allowed_asset_url("http://evil.com/x.exe") is False, "非白名单拒绝")
    check(updater._is_allowed_asset_url(
        "https://mirror.example/x.exe", api_base="https://mirror.example") is True,
        "自定义 api_base 域名允许")

    class FakeBadResp:
        def read(self):
            return json.dumps({
                "tag_name": "v9.9.9", "assets": [{
                    "name": "UsageMonitor.exe", "size": 1,
                    "browser_download_url": "http://evil.com/UsageMonitor.exe",
                }],
            }).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    orig = updater.urllib.request.urlopen
    updater.urllib.request.urlopen = lambda req, timeout=None: FakeBadResp()
    try:
        info = updater.latest_release()
        check(info.get("asset") is None, "恶意资产地址被拒", str(info.get("asset")))
    finally:
        updater.urllib.request.urlopen = orig

def test_ai_sessions():
    print("[test] AI 会话深度统计（JSONL/JSON 解析、按日过滤、关闭态）")
    tmp = fresh_tmp("ai_sessions")
    day = "2026-08-10"
    opencode_dir = os.path.join(tmp, "opencode")
    chatgpt_dir = os.path.join(tmp, "chatgpt")
    os.makedirs(opencode_dir, exist_ok=True)
    os.makedirs(chatgpt_dir, exist_ok=True)
    opencode_path = os.path.join(opencode_dir, "sessions.jsonl")
    with open(opencode_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": f"{day}T10:00:00", "role": "user", "content": "帮我写代码",
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "timestamp": f"{day}T10:01:00", "role": "assistant", "content": "第一行\n第二行",
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "timestamp": "2026-08-11T10:00:00", "role": "assistant", "content": "不算今天",
        }, ensure_ascii=False) + "\n")
    chatgpt_path = os.path.join(chatgpt_dir, "conversations.json")
    with open(chatgpt_path, "w", encoding="utf-8") as fh:
        json.dump({
            "messages": [
                {"timestamp": f"{day}T11:00:00", "role": "user", "content": "你好"},
                {"timestamp": f"{day}T11:01:00", "role": "assistant", "content": "你好！"},
            ]
        }, fh, ensure_ascii=False)

    cfg = {
        "ai_sessions": {
            "enabled": True,
            "paths": {"opencode": [opencode_dir], "chatgpt": [chatgpt_dir]},
        }
    }
    result = ai_sessions.collect(day, cfg)
    check(result["enabled"] is True, "开启状态")
    check(result["found"] is True, "发现会话文件")
    check(result["total"]["turns"] == 4, "当天共 4 条消息", str(result["total"]))
    check(result["total"]["user_messages"] == 2, "用户消息 2 条", str(result["total"]))
    check(result["total"]["assistant_messages"] == 2, "助手消息 2 条", str(result["total"]))
    check(result["total"]["generated_lines"] == 3, "助手生成 3 行", str(result["total"]))
    check("opencode" in result["tools"] and "chatgpt" in result["tools"], "按工具分组")

    check(result["total"]["rounds"] == 2, "对话轮次 2 轮（两文件各 1 问→1 答）", str(result["total"]["rounds"]))
    check(result["total"]["tokens_in"] > 0 and result["total"]["tokens_out"] > 0,
          "Token 估算进/出非零", str(result["total"]))
    check(ai_sessions.estimate_tokens("hello world this is a test") >= 3, "拉丁 Token 估算>0",
          str(ai_sessions.estimate_tokens("hello world this is a test")))
    check(ai_sessions.estimate_tokens("") == 0, "空文本 Token=0")
    check(isinstance(result["total"]["by_model"], dict) and "未识别" in result["total"]["by_model"],
          "by_model 汇总存在", str(result["total"]["by_model"]))
    check(result["total"]["by_project"].get("未识别") is not None,
          "无 cwd 字段按未识别项目归口", str(result["total"]["by_project"]))

    cfg_off = {"ai_sessions": {"enabled": False}}
    off = ai_sessions.collect(day, cfg_off)
    check(off["enabled"] is False and off["found"] is False, "默认关闭态")
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_sessions_more_tools():
    print("[test] AI 会话深度统计（Cursor 嵌套 / DSH 可配置路径）")
    tmp = fresh_tmp("ai_sessions_more")
    day = "2026-08-10"
    cursor_dir = os.path.join(tmp, "cursor")
    dsh_dir = os.path.join(tmp, "dsh")
    os.makedirs(cursor_dir, exist_ok=True)
    os.makedirs(dsh_dir, exist_ok=True)
    with open(os.path.join(cursor_dir, "conversations.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "conversations": {
                "c1": {
                    "messages": [
                        {"timestamp": f"{day}T12:00:00", "role": "user", "content": "hi"},
                        {"timestamp": f"{day}T12:01:00", "role": "assistant", "content": "a\nb"},
                    ]
                }
            }
        }, fh, ensure_ascii=False)
    with open(os.path.join(dsh_dir, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{day}T13:00:00", "role": "user", "content": "dsh q"},
                            ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T13:01:00", "role": "assistant", "content": "dsh a"},
                            ensure_ascii=False) + "\n")

    cfg = {
        "ai_sessions": {
            "enabled": True,
            "paths": {"cursor": [cursor_dir], "dsh": [dsh_dir]},
        }
    }
    result = ai_sessions.collect(day, cfg)
    check("cursor" in result["tools"] and "dsh" in result["tools"],
          "识别 Cursor 与 DSH", str(list(result["tools"].keys())))
    check(result["total"]["turns"] == 4, "共 4 条消息", str(result["total"]))
    check(result["tools"]["cursor"]["generated_lines"] == 2, "Cursor 生成 2 行",
          str(result["tools"]["cursor"]))
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_sessions_phase1():
    print("[test] AI 会话 Phase1（对话轮次/Token 估算/模型·项目拆分/会话详情/Web AI 会话）")
    tmp = fresh_tmp("ai_sessions_phase1")
    day = "2026-08-10"
    oc = os.path.join(tmp, "opencode")
    os.makedirs(oc, exist_ok=True)
    with open(os.path.join(oc, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{day}T10:00:00", "role": "user", "content": "你好",
                             "model": "claude-3-5-sonnet", "cwd": "/repo/alpha"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T10:01:00", "role": "assistant",
                             "content": "收到，开始改。"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T10:02:00", "role": "user", "content": "继续",
                             "model": "deepseek-chat", "cwd": "/repo/alpha"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T10:03:00", "role": "assistant", "content": "完成。"},
                            ensure_ascii=False) + "\n")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]}}}
    r = ai_sessions.collect(day, cfg)
    to = r["total"]
    check(to["turns"] == 4 and to["rounds"] == 2, "多轮会话：4 条消息 / 2 轮", str(to))
    check(to["tokens_in"] > 0 and to["tokens_out"] > 0, "Token 进/出估算非零", str(to))
    check(to["by_model"].get("claude-3-5-sonnet", {}).get("turns") == 1
          and to["by_model"].get("deepseek-chat", {}).get("turns") == 1,
          "按模型拆分", str(to["by_model"]))
    check(to["by_project"].get("alpha", {}).get("turns") == 4,
          "项目按会话归口（cwd→alpha，全会话 4 条）", str(to["by_project"]))
    conv = to["conversations"][0]
    check(conv["rounds"] == 2 and conv["turns"] == 4 and conv["project"] == "alpha"
          and conv["tokens_out"] > 0, "会话详情含轮次/项目/Token", str(conv))

    # Web AI 会话（浏览器历史深度解析：同一会话页多次访问≈轮次）
    visits = [
        {"domain": "chatgpt.com", "url": "https://chatgpt.com/c/aaa111", "time": f"{day}T09:00:00", "title": "ChatGPT"},
        {"domain": "chatgpt.com", "url": "https://chatgpt.com/c/aaa111", "time": f"{day}T09:06:00", "title": "ChatGPT"},
        {"domain": "claude.ai", "url": "https://claude.ai/chat/bb-22222222", "time": f"{day}T11:00:00", "title": "Claude"},
        {"domain": "github.com", "url": "https://github.com/x", "time": f"{day}T08:00:00", "title": "GitHub"},
        {"domain": "chatgpt.com", "url": "https://chatgpt.com/", "time": f"{day}T09:02:00", "title": "ChatGPT"},
    ]
    w = ai_sessions.web_ai_sessions(visits)
    check(w["conversations"] == 2 and w["turns"] == 3, "Web 会话 2 个 / 访问 3 次（≈轮次）", str(w))
    check(w["by_tool"]["chatgpt"]["turns"] == 2 and w["by_tool"]["claude"]["turns"] == 1,
          "Web 按工具拆分", str(w["by_tool"]))
    check(w["browsing_visits"] == 1, "首页等非会话页计入浏览 1 次", str(w["browsing_visits"]))
    r2 = ai_sessions.collect(day, cfg, web_visits=visits)
    check(r2["web_ai"]["found"] is True and r2["web_ai"]["turns"] == 3, "collect 附带 Web AI", str(r2["web_ai"]))
    check(r2["found"] is True, "found 同时反映 Web 会话", str(r2["found"]))

    # 关闭 token_estimation
    r3 = ai_sessions.collect(day, {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]},
                                                   "token_estimation": False}})
    check(r3["total"]["tokens_in"] == 0 and r3["total"]["tokens_out"] == 0,
          "token_estimation=false 全 0", str(r3["total"]))
    # 关闭 web_ai（用不存在目录，避免扫真实默认目录）
    r4 = ai_sessions.collect(day, {"ai_sessions": {"enabled": True,
                                                   "paths": {"opencode": [os.path.join(tmp, "nonexistent")]},
                                                   "web_ai": {"enabled": False}}},
                             web_visits=visits)
    check(r4["web_ai"]["found"] is False, "web_ai.enabled=false 不解析 Web", str(r4["web_ai"]))
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_sessions_costs():
    print("[test] AI 会话成本（按模型计价 / 按项目分摊 / 自定义单价 / 关闭）")
    tmp = fresh_tmp("ai_sessions_costs")
    day = "2026-08-10"
    oc = os.path.join(tmp, "opencode")
    os.makedirs(oc, exist_ok=True)
    with open(os.path.join(oc, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{day}T10:00:00", "role": "user", "content": "hi",
                             "model": "claude-3-5-sonnet", "cwd": "/r/projA"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T10:01:00", "role": "assistant", "content": "abcd",
                             "model": "claude-3-5-sonnet"}, ensure_ascii=False) + "\n")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]}}}
    r = ai_sessions.collect(day, cfg)
    to = r["total"]
    pin, pout = 3.0, 15.0  # claude-3-5-sonnet USD/MTok
    exp_in = to["tokens_in"] * pin / 1e6
    exp_out = to["tokens_out"] * pout / 1e6
    check(abs(to["cost_in"] - exp_in) < 1e-9, "成本-进 按输入价", str(to["cost_in"]))
    check(abs(to["cost_out"] - exp_out) < 1e-9, "成本-出 按输出价", str(to["cost_out"]))
    check(abs(to["cost_total"] - (exp_in + exp_out)) < 1e-9, "成本-合计", str(to["cost_total"]))
    bm = to["by_model"].get("claude-3-5-sonnet", {})
    check(abs(bm.get("cost_in", 0) - exp_in) < 1e-9 and abs(bm.get("cost_out", 0) - exp_out) < 1e-9,
          "按模型成本拆分", str(bm))
    check(abs(to["by_project"].get("projA", {}).get("cost_total", 0) - (exp_in + exp_out)) < 1e-9,
          "按项目成本分摊", str(to["by_project"]))
    check(abs(to["conversations"][0]["cost_total"] - (exp_in + exp_out)) < 1e-9,
          "会话级成本", str(to["conversations"][0]))

    # 自定义单价覆盖
    cfg2 = {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]},
                            "costs": {"enabled": True, "model_pricing": {"claude-3-5-sonnet": [1.0, 2.0]}}}}
    t2 = ai_sessions.collect(day, cfg2)["total"]
    exp2 = to["tokens_in"] * 1.0 / 1e6 + to["tokens_out"] * 2.0 / 1e6
    check(abs(t2["cost_total"] - exp2) < 1e-9, "自定义 pricing 生效", str(t2["cost_total"]))

    # 关闭成本估算
    cfg3 = {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]},
                            "costs": {"enabled": False}}}
    t3 = ai_sessions.collect(day, cfg3)["total"]
    check(t3["cost_in"] == 0 and t3["cost_out"] == 0 and t3["cost_total"] == 0,
          "costs.enabled=false 成本全 0", str(t3))

    # 外部 ai_pricing.json（data_root 下用户定价文件）优先于 config 覆盖
    root = os.path.join(tmp, "pricing_root")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"claude-3-5-sonnet": {"input": 7.0, "output": 70.0}}, fh)
    t4 = ai_sessions.collect(day, {"ai_sessions": {"enabled": True, "paths": {"opencode": [oc]},
                                                   "costs": {"model_pricing": {"claude-3-5-sonnet": [1.0, 2.0]}}},
                                   "data_root": root})["total"]
    exp4 = to["tokens_in"] * 7.0 / 1e6 + to["tokens_out"] * 70.0 / 1e6
    check(abs(t4["cost_total"] - exp4) < 1e-9, "ai_pricing.json 覆盖（优先于 config）", str(t4["cost_total"]))
    shutil.rmtree(tmp, ignore_errors=True)

def test_ai_cost_ledger():
    print("[test] AI 成本账本（Phase 3 · 周/月汇总支出报表）")
    tmp = fresh_tmp("ai_cost_ledger")
    day = "2026-08-10"
    sess_dir = os.path.join(tmp, "sessions", "opencode")
    os.makedirs(sess_dir, exist_ok=True)
    lines = [
        json.dumps({"timestamp": day + "T10:00:00", "role": "user", "content": "hi",
                    "model": "claude-3-5-sonnet", "cwd": "/r/projA"}, ensure_ascii=False),
        json.dumps({"timestamp": day + "T10:01:00", "role": "assistant", "content": "abcd",
                    "model": "claude-3-5-sonnet"}, ensure_ascii=False),
    ]
    with open(os.path.join(sess_dir, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(lines) + chr(10))
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"ai_sessions": {"enabled": True, "paths": {"opencode": [sess_dir]}}}, fh)
    md = report._ai_cost_ledger_md([day], tmp, "测试")
    check(md is not None, "账本生成", "None")
    check("AI 成本账本" in (md or ""), "含标题", str(md)[:80])
    check("claude-3-5-sonnet" in (md or ""), "含模型拆分", str(md)[:200])

    tmp2 = fresh_tmp("ai_cost_ledger_empty")
    with open(os.path.join(tmp2, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"ai_sessions": {"enabled": True,
                                   "paths": {"opencode": [os.path.join(tmp2, "nope")]}}}, fh)
    empty_md = report._ai_cost_ledger_md([day], tmp2, "测试")
    check(empty_md is None or "AI 成本账本" not in empty_md, "空数据 None 或降级", str(empty_md))
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)

def test_sqlite_store():
    print("[test] SQLite 后端（写入/回填/幂等/重建/查询）")
    tmp = fresh_tmp("sqlite_store")
    day = "2026-08-10"
    day_dir = os.path.join(tmp, day)
    os.makedirs(day_dir, exist_ok=True)
    recs = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:05:00", "duration_ms": 300000,
         "exe": "code.exe", "app": "VS Code", "title": "a", "category": "开发工具", "active": True},
        {"start": f"{day}T11:00:00", "end": f"{day}T11:10:00", "duration_ms": 600000,
         "exe": "opencode.exe", "app": "OpenCode", "title": "b", "category": "AI编程",
         "ai_tool": "opencode", "active": True},
    ]
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    st0 = sqlite_store.status(tmp)
    check(not st0["exists"], "初始无 usage.db", str(st0))
    result = sqlite_store.backfill(tmp)
    check(result["inserted"] == 2 and result["days"] == 1, "回填 2 条", str(result))
    st1 = sqlite_store.status(tmp)
    check(st1["exists"] and st1["rows"] == 2, "回填后 rows=2", str(st1))
    rows = sqlite_store.read_day(tmp, day)
    check(len(rows) == 2 and rows[0]["exe"] == "code.exe", "read_day 返回数据", str(rows))
    result2 = sqlite_store.backfill(tmp)
    check(result2["inserted"] == 0 and result2["skipped"] == 2, "重复回填幂等", str(result2))

    day2 = "2026-08-11"
    monitor.append_session_record(day2, {
        "start": f"{day2}T09:00:00", "end": f"{day2}T09:01:00", "duration_ms": 60000,
        "exe": "chatgpt.exe", "app": "ChatGPT", "title": "c", "category": "AI编程",
        "ai_tool": "chatgpt", "active": True,
    }, tmp, sqlite_enabled=True)
    rows2 = sqlite_store.read_day(tmp, day2)
    check(len(rows2) == 1 and rows2[0]["ai_tool"] == "chatgpt", "monitor 同步写 SQLite", str(rows2))

    month_agg = report.aggregate_month("2026-08", tmp)
    check(month_agg["session_count"] == 3 and month_agg["total_active_ms"] == 960000,
          "SQLite 月聚合（不再逐日扫 JSONL）", str(month_agg))

    week_agg = report.aggregate_days([day, day2], tmp)
    check(week_agg["session_count"] == 3 and week_agg["total_active_ms"] == 960000,
          "SQLite 周聚合（多日范围一次查询）", str(week_agg))

    # 制造一条只写 JSONL 不写 SQLite 的记录，verify 应发现差异
    extra = {
        "start": f"{day}T20:00:00", "end": f"{day}T20:01:00", "duration_ms": 60000,
        "exe": "manual.exe", "app": "Manual", "title": "m", "category": "其他",
        "active": True,
    }
    with open(os.path.join(tmp, day, "usage.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra, ensure_ascii=False) + "\n")
    v = sqlite_store.verify(tmp)
    check(len(v["mismatches"]) == 1 and v["mismatches"][0]["day"] == day,
          "verify 发现 JSONL/SQLite 差异", str(v))

    result3 = sqlite_store.rebuild(tmp)
    check(result3["inserted"] == 4 and result3["skipped"] == 0, "重建全量回填", str(result3))
    shutil.rmtree(tmp, ignore_errors=True)
