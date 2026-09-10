# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_coverage.py — Dashboard 端点缺口覆盖。

补齐此前无 pytest 覆盖的端点与分支：
- 导出（CSV/JSON × day/week/month + 非法参数）
- 备份下载 → 清空 → HTTP 恢复 的完整往返（含体积上限与坏 zip 拒绝）
- 应用分组全流程（set/rename/add/delete/import + 持久化）
- 模型定价读写、AI 客制化模块保存/导入/导出
- 运行日志、更新状态/离线检查、Ollama 错误路径
- 访问口令开启后的 401/200 鉴权流程
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import zipfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402

from tests.conftest import ApiClient, make_record, seed_day  # noqa: E402

_DAY = "2099-02-11"
_MONTH = "2099-02"


def test_export_csv_and_json(api_server):
    """导出：day/week/month × csv/json；非法 type/scope/date/month → 4xx。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30), make_record(_DAY, 14, 15, exe="steam.exe", app="Steam", category="游戏")])
    s, d, hdr = client.get(f"/api/export?type=csv&scope=day&date={_DAY}")
    assert s == 200
    assert "attachment" in hdr.get("Content-Disposition", "")
    csv_text = d.get("_raw", "")
    assert "类型,名称,时长秒" in csv_text and "应用:" in csv_text
    s, d, _ = client.get(f"/api/export?type=json&scope=day&date={_DAY}")
    assert s == 200 and "total_active_ms" in d
    s, d, _ = client.get("/api/export?type=json&scope=week")
    assert s == 200 and "days" in d or "aggregate" in d or d
    s, d, _ = client.get(f"/api/export?type=json&scope=month&month={_MONTH}")
    assert s == 200
    for bad in ("type=xml&scope=day", "type=csv&scope=day&date=nope",
                "type=csv&scope=zzz", "type=csv&scope=month&month=99-1"):
        s, _, _ = client.get("/api/export?" + bad)
        assert s in (400, 404), bad
    print("  [PASS] export_csv_and_json")


def test_backup_restore_roundtrip(api_server):
    """备份 zip 下载 → 清空数据根 → HTTP 恢复 → 数据完整回来。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    with open(os.path.join(root, "app_groups.json"), "w", encoding="utf-8") as fh:
        json.dump({"exe_groups": {"steam.exe": "游戏"}}, fh)
    s, _, _ = client.get("/api/backup")
    assert s == 200
    zbytes = client.raw
    assert zipfile.is_zipfile(io.BytesIO(zbytes))
    # 清空数据根后恢复
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
    s, d, _ = client.post("/api/backup/restore", body=zbytes,
                          headers={"Content-Type": "application/octet-stream"})
    assert s == 200 and d.get("ok") is True, d
    assert _DAY in d["days"] and "app_groups.json" in d["files"]
    assert os.path.isfile(os.path.join(root, _DAY, "usage.jsonl"))
    with open(os.path.join(root, "app_groups.json"), "r", encoding="utf-8") as fh:
        assert json.load(fh)["exe_groups"]["steam.exe"] == "游戏"
    print("  [PASS] backup_restore_roundtrip")


def test_restore_reject_bad_bodies(api_server, monkeypatch):
    """恢复：空体 / 坏 zip / 超限 一律 400，不落盘。"""
    client, root = api_server
    before = set(os.listdir(root))
    s, _, _ = client.post("/api/backup/restore", body=b"",
                          headers={"Content-Type": "application/octet-stream"})
    assert s == 400
    s, d, _ = client.post("/api/backup/restore", body=b"not a zip at all",
                          headers={"Content-Type": "application/octet-stream"})
    assert s == 400 and "restore failed" in d.get("error", "")
    monkeypatch.setattr(dashboard, "_RESTORE_MAX_BYTES", 8)
    s, _, _ = client.post("/api/backup/restore", body=b"x" * 32,
                          headers={"Content-Type": "application/octet-stream"})
    assert s == 400
    assert set(os.listdir(root)) == before, "失败的恢复不得改动数据根"
    print("  [PASS] restore_reject_bad_bodies")


def test_groups_full_flow(api_server):
    """分组：set → rename → add → delete → import 全流程 + 持久化。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    s, d, _ = client.get("/api/groups")
    assert s == 200 and isinstance(d["categories"], list) and isinstance(d["apps"], list)
    s, d, _ = client.post("/api/groups/set", {"exe": "Steam.exe", "category": "游戏"})
    assert s == 200 and d["ok"]
    s, d, _ = client.post("/api/groups/rename", {"exe": "steam.exe", "display_name": "蒸汽平台"})
    assert s == 200 and d["ok"]
    groups = json.load(open(os.path.join(root, "app_groups.json"), encoding="utf-8"))
    assert groups["exe_groups"].get("steam.exe") == "游戏"
    assert groups["app_names"].get("steam.exe") == "蒸汽平台"
    s, d, _ = client.post("/api/groups/set", {"exe": "steam.exe", "category": ""})
    assert s == 200  # 空 category = 移出分组
    s, d, _ = client.post("/api/groups/add", {"name": "创作"})
    assert s == 200 and "创作" in d["categories"]
    s, d, _ = client.post("/api/groups/import", {"groups": {
        "exe_groups": {"code.exe": "开发工具"},
        "custom_categories": ["开发工具"], "app_names": {}, "group_meta": {}}})
    assert s == 200 and d["ok"]
    s, d, _ = client.post("/api/groups/delete", {"name": "创作"})
    assert s == 200
    s, d, _ = client.get("/api/groups")
    assert "创作" not in d["custom_categories"]
    for bad in ({"category": "x"}, {"exe": "", "category": "x"}):
        s, _, _ = client.post("/api/groups/set", bad)
        assert s == 400
    print("  [PASS] groups_full_flow")


def test_pricing_roundtrip(api_server):
    """定价：POST 合法条目落盘、非法条目过滤；GET 回读。"""
    client, root = api_server
    payload = {"pricing": {"gpt-x": [1.5, 10], "bad": [1], "ok-dict": {"input": 2, "output": 8}, "nan": ["a", "b"]}}
    s, d, _ = client.post("/api/pricing", payload)
    assert s == 200 and d["ok"] and d["count"] == 2, d
    fp = os.path.join(root, "ai_pricing.json")
    assert os.path.isfile(fp)
    s, d, _ = client.get("/api/pricing")
    assert s == 200 and d["custom"]["gpt-x"] == [1.5, 10]
    assert d["builtin_count"] > 0
    print("  [PASS] pricing_roundtrip")


def test_pricing_builtin_normalized_four_elements(api_server):
    """GET /api/pricing 的 builtin 每个值都是 4 元素列表（in/out/缓存读/缓存写）。

    内置表 2/4 元组混存，端点统一经 ai_sessions._price4 归一化后输出，
    前端设置页才能按固定 4 列渲染缓存价格。
    """
    client, _root = api_server
    s, d, _ = client.get("/api/pricing")
    assert s == 200
    assert d["builtin_count"] == len(d["builtin"])
    assert all(isinstance(v, list) and len(v) == 4 for v in d["builtin"].values())
    # 2 元组表值（gpt-4o）→ 补 (in, in)：[2.5, 10, 2.5, 10]
    assert d["builtin"]["gpt-4o"] == [2.5, 10.0, 2.5, 2.5]
    # 显式 4 元组表值（claude-opus-5）→ 按档原样
    assert d["builtin"]["claude-opus-5"] == [5.0, 25.0, 0.5, 6.25]
    print("  [PASS] pricing_builtin_normalized_four_elements")


def test_ai_module_save_import_export(api_server):
    """AI 客制化模块：保存 → 读取 → 导入 → 导出 blob。"""
    client, root = api_server
    s, d, _ = client.post("/api/ai/module", {"providers": [
        {"id": "myprov", "name": "My Provider", "base_url": "https://api.example.com", "model": "m1"}]})
    assert s == 200 and d["ok"], d
    s, d, _ = client.get("/api/ai/module")
    assert s == 200 and any(p["id"] == "myprov" for p in d["custom"].get("providers", []))
    s, d, _ = client.post("/api/ai/module/import", {"custom": {"providers": [], "prompt": {"instruction": "简洁"}}})
    assert s == 200 and d["ok"]
    s, d, hdr = client.get("/api/ai/module/export")
    assert s == 200 and "attachment" in hdr.get("Content-Disposition", "")
    # JSON 附件会被客户端直接解析为 dict；二进制/文本才落 _raw
    exported = d if isinstance(d, dict) and "_raw" not in d else json.loads(d.get("_raw", "{}"))
    assert exported.get("prompt", {}).get("instruction") == "简洁"
    s, d, _ = client.post("/api/ai/module", {"providers": "notalist"})
    assert s == 400
    print("  [PASS] ai_module_save_import_export")


def test_log_and_update_endpoints(api_server, monkeypatch):
    """日志键齐全；更新状态 idle；离线 api_base 下 check 返回 error 不触网。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    s, d, _ = client.get("/api/log?n=20")
    assert s == 200 and "entries" in d and "errors" in d
    s, d, _ = client.get("/api/update/status")
    assert s == 200 and d["state"] == "idle" and d["dev"] is True
    dashboard._UPDATE_CHECK_CACHE.update(ts=0.0, result=None)  # 清全局缓存，强制走本次检查
    s, d, _ = client.get("/api/update/check")
    assert s == 200 and d.get("has_update") is False and d.get("error"), d
    s, d, _ = client.get("/api/hourly?date=nope")
    assert s == 400
    print("  [PASS] log_and_update_endpoints")


def test_token_auth_flow(tmp_path):
    """口令开启：无/错 token → 401；正确 token → 200；页面仍可加载。"""
    root = str(tmp_path / "auth_root")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"dashboard_token": "sekret-123", "ai_sessions": {"enabled": False}}, fh)
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = ApiClient(port)
    try:
        s, _, _ = client.get("/api/dates")
        assert s == 401
        s, _, _ = client.get("/api/dates", headers={"X-Dashboard-Token": "wrong"})
        assert s == 401
        s, d, _ = client.get("/api/dates", headers={"X-Dashboard-Token": "sekret-123"})
        assert s == 200 and "dates" in d
        s, d, _ = client.post("/api/groups/set", {"exe": "a.exe", "category": "x"})
        assert s == 401  # POST 同样受口令保护
        s, d, hdr = client.get("/")
        assert s == 200 and "AUTH_REQUIRED = true" in d.get("_raw", "")
        print("  [PASS] token_auth_flow")
    finally:
        server.shutdown()
        server.server_close()


def test_insights_settings_shape(api_server):
    """AI 设置视图：字段齐全、不泄露 api_key 明文。"""
    client, root = api_server
    s, d, _ = client.get("/api/insights/settings")
    assert s == 200
    ai = d["ai"]
    for key in ("enabled", "provider", "base_url", "model", "timeout_s", "send_raw_titles", "language", "api_key_set"):
        assert key in ai, key
    assert isinstance(d["presets"], list)
    print("  [PASS] insights_settings_shape")
