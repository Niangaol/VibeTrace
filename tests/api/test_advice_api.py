# -*- coding: utf-8 -*-
"""tests/api/test_advice_api.py — /api/advice 契约（可选功能：读取 + 设置保存）。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.conftest import make_record, seed_day  # noqa: E402

_DAY = "2099-04-20"


def test_advice_default_disabled(api_server):
    """默认关闭：GET 返回 enabled=false 空态（含 max_items 供设置页回显）。"""
    client, root = api_server
    s, d, _ = client.get(f"/api/advice?date={_DAY}")
    assert s == 200 and d["enabled"] is False and d["items"] == []
    assert d["max_items"] == 6
    print("  [PASS] advice_default_disabled")


def test_advice_invalid_date_400(api_server):
    """非法日历日 → 400（与其余单日端点一致）。"""
    client, root = api_server
    s, _, _ = client.get("/api/advice?date=2026-13-99")
    assert s == 400
    print("  [PASS] advice_invalid_date_400")


def test_advice_settings_save_and_effect(api_server):
    """POST 保存开关 → config.json 落盘 → GET 反映开启；关闭后回到空态。"""
    client, root = api_server
    # 造一个凌晨活跃日：0-6 点有记录 → 开启后应产出 night_owl 建议
    seed_day(root, _DAY, [make_record(_DAY, 1, 40)])
    s, d, _ = client.post("/api/advice/settings", {"enabled": True, "max_items": 3})
    assert s == 200 and d["ok"] is True, d
    with open(os.path.join(root, "config.json"), "r", encoding="utf-8") as fh:
        saved = json.load(fh)["advice"]
    assert saved == {"enabled": True, "max_items": 3}, saved
    s, d, _ = client.get(f"/api/advice?date={_DAY}")
    assert s == 200 and d["enabled"] is True and d["max_items"] == 3
    assert any(it["id"] == "night_owl" for it in d["items"]), d["items"]
    # 关闭 → 空态
    s, d, _ = client.post("/api/advice/settings", {"enabled": False})
    assert s == 200 and d["ok"] is True
    s, d, _ = client.get(f"/api/advice?date={_DAY}")
    assert s == 200 and d["enabled"] is False and d["items"] == []
    print("  [PASS] advice_settings_save_and_effect")
