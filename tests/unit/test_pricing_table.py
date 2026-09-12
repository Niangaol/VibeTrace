# -*- coding: utf-8 -*-
"""tests/unit/test_pricing_table.py — 定价表查询测试（50+ case，精确匹配 + 子串 fallback）。

Phase 1+2 起 _model_price 统一返回四元组 (in, out, cache_read, cache_write)：
- 4 元组表值原样；3 元组补缓存写 = 输入价；
- 2 元组的 cache_read 取该供应商官方缓存命中折扣比（v2.9.5：智谱 GLM-5.3 25%、
  GLM-5.3-Flash 20%、阿里云百炼 qwen 系隐式缓存 20%）；无官方口径的模型按输入价计
  （即成本上限估算，不臆造折扣）；
- 未命中 (0,0,0,0)。内置 claude/deepseek/gpt-5.x/codex 系为显式 4 元组缓存档。
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


# ---------------------------------------------------------------------------
# 1. _model_price：精确匹配
# ---------------------------------------------------------------------------
def test_model_price_exact_match():
    table = {"gpt-4o": (5.0, 15.0), "claude-3-5-sonnet": (3.0, 15.0), "deepseek-r1": (1.0, 2.0)}
    assert ai_sessions._model_price(table, "gpt-4o") == (5.0, 15.0, 5.0, 5.0)
    assert ai_sessions._model_price(table, "claude-3-5-sonnet") == (3.0, 15.0, 3.0, 3.0)
    assert ai_sessions._model_price(table, "deepseek-r1") == (1.0, 2.0, 1.0, 1.0)
    print("  [PASS] exact_match")


# ---------------------------------------------------------------------------
# 2. _model_price：子串 fallback（最长键优先）
# ---------------------------------------------------------------------------
def test_model_price_substring_fallback():
    table = {"gpt-4o": (5.0, 15.0), "gpt-4": (30.0, 60.0), "claude": (3.0, 15.0)}
    assert ai_sessions._model_price(table, "gpt-4o-mini") == (5.0, 15.0, 5.0, 5.0)
    assert ai_sessions._model_price(table, "gpt-4-turbo") == (30.0, 60.0, 30.0, 30.0)
    assert ai_sessions._model_price(table, "claude-3-5-sonnet") == (3.0, 15.0, 3.0, 3.0)
    print("  [PASS] substring_fallback")


# ---------------------------------------------------------------------------
# 3. _model_price：大小写不敏感
# ---------------------------------------------------------------------------
def test_model_price_case_insensitive():
    table = {"gpt-4o": (5.0, 15.0)}
    assert ai_sessions._model_price(table, "GPT-4o") == (5.0, 15.0, 5.0, 5.0)
    assert ai_sessions._model_price(table, "Gpt-4o") == (5.0, 15.0, 5.0, 5.0)
    assert ai_sessions._model_price(table, "gpt-4o") == (5.0, 15.0, 5.0, 5.0)
    print("  [PASS] case_insensitive")


# ---------------------------------------------------------------------------
# 4. _model_price：未命中返回零值
# ---------------------------------------------------------------------------
def test_model_price_miss():
    table = {"gpt-4o": (5.0, 15.0)}
    assert ai_sessions._model_price(table, "unknown-model") == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._model_price(table, "") == (0.0, 0.0, 0.0, 0.0)
    # gpt-4o-unknown 包含 gpt-4o，故命中
    assert ai_sessions._model_price(table, "gpt-4o-unknown") == (5.0, 15.0, 5.0, 5.0)
    print("  [PASS] miss")


# ---------------------------------------------------------------------------
# 5. _merge_pricing：列表格式 [in, out]
# ---------------------------------------------------------------------------
def test_merge_pricing_list_format():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"gpt-4o": [5.0, 15.0]})
    assert table["gpt-4o"] == (5.0, 15.0)
    print("  [PASS] list_format")


# ---------------------------------------------------------------------------
# 6. _merge_pricing：字典格式 {input, output}
# ---------------------------------------------------------------------------
def test_merge_pricing_dict_format():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"gpt-4o": {"input": 5.0, "output": 15.0}})
    assert table["gpt-4o"] == (5.0, 15.0)
    print("  [PASS] dict_format")


# ---------------------------------------------------------------------------
# 7. _merge_pricing：键小写化
# ---------------------------------------------------------------------------
def test_merge_pricing_lowercase_keys():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"GPT-4o": [5.0, 15.0]})
    assert "gpt-4o" in table
    assert table["gpt-4o"] == (5.0, 15.0)
    print("  [PASS] lowercase_keys")


# ---------------------------------------------------------------------------
# 8. _merge_pricing：非法格式忽略
# ---------------------------------------------------------------------------
def test_merge_pricing_ignore_invalid():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"bad": "string"})
    assert "bad" not in table
    ai_sessions._merge_pricing(table, {"bad2": [1]})
    assert "bad2" not in table
    # 空 dict 被解析为 (0, 0)
    ai_sessions._merge_pricing(table, {"bad3": {}})
    assert table["bad3"] == (0.0, 0.0)
    print("  [PASS] ignore_invalid")


# ---------------------------------------------------------------------------
# 9. _pricing_table：内置默认值存在
# ---------------------------------------------------------------------------
def test_pricing_table_builtin():
    cfg = {}
    table = ai_sessions._pricing_table(cfg)
    assert "gpt-4o" in table
    assert "claude-3-5-sonnet" in table
    assert "gemini-2.5-pro" in table
    print("  [PASS] builtin_defaults")


# ---------------------------------------------------------------------------
# 10. _pricing_table：config 覆盖
# ---------------------------------------------------------------------------
def test_pricing_table_config_override(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"costs": {"enabled": True, "model_pricing": {"gpt-4o": [10, 20]}}},
    }
    table = ai_sessions._pricing_table(cfg)
    assert table["gpt-4o"] == (10.0, 20.0)
    print("  [PASS] config_override")


# ---------------------------------------------------------------------------
# 11. _pricing_table：ai_pricing.json 覆盖 config
# ---------------------------------------------------------------------------
def test_pricing_table_file_override(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"costs": {"enabled": True, "model_pricing": {"gpt-4o": [10, 20]}}},
    }
    t1 = ai_sessions._pricing_table(cfg)
    assert t1.get("gpt-4o") == (10.0, 20.0)
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"gpt-4o": [99, 99]}, fh)
    t2 = ai_sessions._pricing_table(cfg)
    assert t2.get("gpt-4o") == (99.0, 99.0)
    print("  [PASS] file_override")


# ---------------------------------------------------------------------------
# 12. _pricing_table：无 data_root 时不读文件
# ---------------------------------------------------------------------------
def test_pricing_table_no_data_root(tmp_path):
    cfg = {"data_root": str(tmp_path), "ai_sessions": {"costs": {"enabled": True}}}
    table = ai_sessions._pricing_table(cfg)
    assert isinstance(table, dict)
    print("  [PASS] no_data_root")


# ---------------------------------------------------------------------------
# 13. _pricing_table：ai_pricing.json 损坏时静默降级
# ---------------------------------------------------------------------------
def test_pricing_table_corrupt_file(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        fh.write("not json {{{")
    cfg = {"data_root": root, "ai_sessions": {"costs": {"enabled": True}}}
    table = ai_sessions._pricing_table(cfg)
    assert isinstance(table, dict)
    print("  [PASS] corrupt_file")


# ---------------------------------------------------------------------------
# 14. _model_price：内置表子串匹配
# ---------------------------------------------------------------------------
def test_model_price_builtin_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "gpt-4o") == (2.5, 10.0, 2.5, 2.5)
    assert ai_sessions._model_price(table, "gpt-4o-mini") == (0.15, 0.6, 0.15, 0.15)
    assert ai_sessions._model_price(table, "claude-3-5-sonnet") == (3.0, 15.0, 0.3, 3.75)
    assert ai_sessions._model_price(table, "gemini-2.5-pro") == (1.25, 10.0, 1.25, 1.25)
    print("  [PASS] builtin_substring")


# ---------------------------------------------------------------------------
# 15. _model_price：覆盖表子串匹配（新键优先于旧键）
# ---------------------------------------------------------------------------
def test_model_price_newer_key_wins():
    table = {"gpt": (1.0, 2.0), "gpt-4o": (3.0, 12.0)}
    assert ai_sessions._model_price(table, "gpt-4o-mini") == (3.0, 12.0, 3.0, 3.0)
    print("  [PASS] newer_key_wins")


# ---------------------------------------------------------------------------
# 16. _merge_pricing：mixed dict+list
# ---------------------------------------------------------------------------
def test_merge_pricing_mixed_formats():
    table: dict = {}
    ai_sessions._merge_pricing(table, {
        "m1": [1, 2],
        "m2": {"input": 3, "output": 4},
        "m3": {"input": 5.5, "output": 6.6},
    })
    assert table["m1"] == (1.0, 2.0)
    assert table["m2"] == (3.0, 4.0)
    assert table["m3"] == (5.5, 6.6)
    print("  [PASS] mixed_formats")


# ---------------------------------------------------------------------------
# 17. _merge_pricing：数值容错
# ---------------------------------------------------------------------------
def test_merge_pricing_numeric_coercion():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m1": {"input": "1", "output": "2"}})
    assert table["m1"] == (1.0, 2.0)
    ai_sessions._merge_pricing(table, {"m2": [1.5, 2.5]})
    assert table["m2"] == (1.5, 2.5)
    print("  [PASS] numeric_coercion")


# ---------------------------------------------------------------------------
# 18. _pricing_table：priority order builtin < config < file
# ---------------------------------------------------------------------------
def test_pricing_table_priority_chain(tmp_path):
    root = str(tmp_path / "pri")
    os.makedirs(root, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"costs": {"enabled": True, "model_pricing": {"m": [2, 4]}}},
    }
    t1 = ai_sessions._pricing_table(cfg)
    assert t1.get("m") == (2.0, 4.0)
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"m": [7, 14]}, fh)
    t2 = ai_sessions._pricing_table(cfg)
    assert t2.get("m") == (7.0, 14.0)
    print("  [PASS] priority_chain")


# ---------------------------------------------------------------------------
# 19. _model_price：未命中但内置有相关键
# ---------------------------------------------------------------------------
def test_model_price_partial_miss():
    table = {"gpt-4o": (5.0, 15.0), "claude-3-5-sonnet": (3.0, 15.0)}
    assert ai_sessions._model_price(table, "gpt-4") == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._model_price(table, "gpt-3.5") == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] partial_miss")


# ---------------------------------------------------------------------------
# 20. _model_price：None / empty input
# ---------------------------------------------------------------------------
def test_model_price_none_empty():
    table = {"gpt-4o": (5.0, 15.0)}
    assert ai_sessions._model_price(table, None) == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._model_price(table, "") == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._model_price({}, "gpt-4o") == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] none_empty")


# ---------------------------------------------------------------------------
# 21. _pricing_table：disabled costs
# ---------------------------------------------------------------------------
def test_pricing_table_disabled():
    cfg = {"ai_sessions": {"costs": {"enabled": False}}}
    table = ai_sessions._pricing_table(cfg)
    assert isinstance(table, dict)
    print("  [PASS] disabled_costs")


# ---------------------------------------------------------------------------
# 22. _merge_pricing：空 dict
# ---------------------------------------------------------------------------
def test_merge_pricing_empty():
    table: dict = {}
    ai_sessions._merge_pricing(table, {})
    assert table == {}
    print("  [PASS] empty_dict")


# ---------------------------------------------------------------------------
# 23. _merge_pricing：非 dict 输入
# ---------------------------------------------------------------------------
def test_merge_pricing_non_dict():
    table: dict = {}
    ai_sessions._merge_pricing(table, [])
    assert table == {}
    ai_sessions._merge_pricing(table, None)
    assert table == {}
    print("  [PASS] non_dict_input")


# ---------------------------------------------------------------------------
# 24. _pricing_file：有文件返回路径
# ---------------------------------------------------------------------------
def test_pricing_file_exists(tmp_path):
    root = str(tmp_path)
    fp = os.path.join(root, "ai_pricing.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump({}, fh)
    cfg = {"data_root": root}
    assert ai_sessions._pricing_file(cfg) == fp
    print("  [PASS] pricing_file_exists")


# ---------------------------------------------------------------------------
# 25. _pricing_file：无文件返回 None
# ---------------------------------------------------------------------------
def test_pricing_file_missing(tmp_path):
    cfg = {"data_root": str(tmp_path)}
    assert ai_sessions._pricing_file(cfg) is None
    print("  [PASS] pricing_file_missing")


# ---------------------------------------------------------------------------
# 26. _pricing_file：空 data_root
# ---------------------------------------------------------------------------
def test_pricing_file_empty_root():
    cfg = {}
    assert ai_sessions._pricing_file(cfg) is None
    assert ai_sessions._pricing_file({"data_root": ""}) is None
    print("  [PASS] empty_root")


# ---------------------------------------------------------------------------
# 27. _model_price：builtin deepseek 子串
# ---------------------------------------------------------------------------
def test_model_price_deepseek_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "deepseek-chat") == (0.257, 1.029, 0.0257, 0.257)
    assert ai_sessions._model_price(table, "deepseek-r1") == (0.7, 2.5, 0.07, 0.7)
    assert ai_sessions._model_price(table, "deepseek-v3") == (0.27, 1.1, 0.027, 0.27)
    print("  [PASS] deepseek_substring")


# ---------------------------------------------------------------------------
# 28. _model_price：builtin gemini 子串
# ---------------------------------------------------------------------------
def test_model_price_gemini_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "gemini-2.5-pro") == (1.25, 10.0, 1.25, 1.25)
    assert ai_sessions._model_price(table, "gemini-2.5-flash") == (0.3, 2.5, 0.3, 0.3)
    assert ai_sessions._model_price(table, "gemini-3-flash") == (0.5, 3.0, 0.5, 0.5)
    print("  [PASS] gemini_substring")


# ---------------------------------------------------------------------------
# 29. _model_price：builtin qwen 子串
# ---------------------------------------------------------------------------
def test_model_price_qwen_substring():
    table = ai_sessions._pricing_table({})
    # qwen 系走阿里云百炼隐式缓存官方折扣：命中价 = 输入价 20%
    assert ai_sessions._model_price(table, "qwen-max") == (1.6, 6.4, 0.32, 1.6)
    assert ai_sessions._model_price(table, "qwen-plus") == (0.26, 0.78, 0.052, 0.26)
    assert ai_sessions._model_price(table, "qwen-turbo") == (0.3, 0.6, 0.06, 0.3)
    print("  [PASS] qwen_substring")


# ---------------------------------------------------------------------------
# 30. _model_price：builtin glm 子串
# ---------------------------------------------------------------------------
def test_model_price_glm_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "glm-5") == (0.6, 1.92, 0.6, 0.6)
    assert ai_sessions._model_price(table, "glm-4") == (0.5, 1.4, 0.5, 0.5)
    # glm-5.3-flash 官方缓存命中价 = 输入价 20%（Z.ai: cached $0.03 / input $0.15）
    assert ai_sessions._model_price(table, "glm-5.3-flash") == (0.075, 0.25, 0.015, 0.075)
    # glm-5.3 官方缓存命中价 = 输入价 25%（智谱: 2 元 / 8 元）
    assert ai_sessions._model_price(table, "glm-5.3") == (1.4, 4.4, 0.35, 1.4)
    assert ai_sessions._model_price(table, "glm-5.2") == (0.966, 3.036, 0.966, 0.966)
    print("  [PASS] glm_substring")


# ---------------------------------------------------------------------------
# 31. _model_price：builtin grok 子串
# ---------------------------------------------------------------------------
def test_model_price_grok_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "grok-4") == (1.25, 2.5, 1.25, 1.25)
    assert ai_sessions._model_price(table, "grok-3") == (3.0, 15.0, 3.0, 3.0)
    assert ai_sessions._model_price(table, "grok-4-heavy") == (1.25, 2.5, 1.25, 1.25)
    print("  [PASS] grok_substring")


# ---------------------------------------------------------------------------
# 32. _model_price：builtin llama 子串
# ---------------------------------------------------------------------------
def test_model_price_llama_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "llama-4") == (0.2, 0.7, 0.2, 0.2)
    assert ai_sessions._model_price(table, "llama-3") == (0.4, 0.4, 0.4, 0.4)
    print("  [PASS] llama_substring")


# ---------------------------------------------------------------------------
# 33. _model_price：builtin command 子串
# ---------------------------------------------------------------------------
def test_model_price_command_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "command-r") == (0.15, 0.6, 0.15, 0.15)
    assert ai_sessions._model_price(table, "command-a") == (2.5, 10.0, 2.5, 2.5)
    print("  [PASS] command_substring")


# ---------------------------------------------------------------------------
# 34. _model_price：builtin codex 子串
# ---------------------------------------------------------------------------
def test_model_price_codex_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "codex") == (1.75, 14.0, 0.175, 0.0)
    assert ai_sessions._model_price(table, "codex-cushman") == (1.75, 14.0, 0.175, 0.0)
    print("  [PASS] codex_substring")


# ---------------------------------------------------------------------------
# 35. _merge_pricing：覆盖已存在键
# ---------------------------------------------------------------------------
def test_merge_pricing_overwrite():
    table: dict = {"gpt-4o": (1.0, 2.0)}
    ai_sessions._merge_pricing(table, {"gpt-4o": [5.0, 15.0]})
    assert table["gpt-4o"] == (5.0, 15.0)
    print("  [PASS] overwrite")


# ---------------------------------------------------------------------------
# 36. _merge_pricing：None 值忽略
# ---------------------------------------------------------------------------
def test_merge_pricing_none_values():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m": None})
    assert "m" not in table
    print("  [PASS] none_values")


# ---------------------------------------------------------------------------
# 37. _model_price：builtin mistral 子串
# ---------------------------------------------------------------------------
def test_model_price_mistral_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "mistral-large") == (2.0, 6.0, 2.0, 2.0)
    assert ai_sessions._model_price(table, "mistral-small") == (0.15, 0.6, 0.15, 0.15)
    assert ai_sessions._model_price(table, "mistral-large-3") == (0.5, 1.5, 0.5, 0.5)
    print("  [PASS] mistral_substring")


# ---------------------------------------------------------------------------
# 38. _model_price：builtin moonshot / kimi 子串
# ---------------------------------------------------------------------------
def test_model_price_moonshot_kimi_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "moonshot-v3") == (1.0, 3.0, 1.0, 1.0)
    assert ai_sessions._model_price(table, "kimi-k3") == (3.0, 15.0, 3.0, 3.0)
    print("  [PASS] moonshot_kimi_substring")


# ---------------------------------------------------------------------------
# 39. _model_price：builtin doubao / ernie 子串
# ---------------------------------------------------------------------------
def test_model_price_doubao_ernie_substring():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "doubao-pro") == (0.3, 0.6, 0.3, 0.3)
    assert ai_sessions._model_price(table, "ernie-4.5") == (0.42, 1.25, 0.42, 0.42)
    print("  [PASS] doubao_ernie_substring")


# ---------------------------------------------------------------------------
# 40. _pricing_table：file 损坏且 config 为空时仍返回 builtin
# ---------------------------------------------------------------------------
def test_pricing_table_file_broken_fallback(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        fh.write("{broken")
    cfg = {"data_root": root, "ai_sessions": {"costs": {"enabled": True}}}
    table = ai_sessions._pricing_table(cfg)
    assert "gpt-4o" in table
    print("  [PASS] broken_file_fallback")


# ---------------------------------------------------------------------------
# 41. _model_price：zero-length table
# ---------------------------------------------------------------------------
def test_model_price_empty_table():
    assert ai_sessions._model_price({}, "gpt-4o") == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] empty_table")


# ---------------------------------------------------------------------------
# 42. _merge_pricing：partial list (len < 2)
# ---------------------------------------------------------------------------
def test_merge_pricing_short_list():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m": [1]})
    assert "m" not in table
    print("  [PASS] short_list")


# ---------------------------------------------------------------------------
# 43. _model_price：unicode / special chars
# ---------------------------------------------------------------------------
def test_model_price_unicode():
    table = {"m-模型": (1.0, 2.0)}
    assert ai_sessions._model_price(table, "m-模型") == (1.0, 2.0, 1.0, 1.0)
    assert ai_sessions._model_price(table, "unknown") == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] unicode")


# ---------------------------------------------------------------------------
# 44. _pricing_table：data_root with ~
# ---------------------------------------------------------------------------
def test_pricing_table_expanduser(monkeypatch, tmp_path):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("HOME", fake_home)
    cfg = {"data_root": "~/pricing", "ai_sessions": {"costs": {"enabled": True}}}
    table = ai_sessions._pricing_table(cfg)
    assert isinstance(table, dict)
    print("  [PASS] expanduser")


# ---------------------------------------------------------------------------
# 45. _model_price：overlap between keys
# ---------------------------------------------------------------------------
def test_model_price_key_overlap():
    table = {"gpt": (1.0, 2.0), "gpt-4o": (3.0, 12.0), "gpt-4o-mini": (5.0, 20.0)}
    assert ai_sessions._model_price(table, "gpt-4o-mini") == (5.0, 20.0, 5.0, 5.0)
    assert ai_sessions._model_price(table, "gpt-4o") == (3.0, 12.0, 3.0, 3.0)
    assert ai_sessions._model_price(table, "gpt-4") == (1.0, 2.0, 1.0, 1.0)
    print("  [PASS] key_overlap")


# ---------------------------------------------------------------------------
# 46. _merge_pricing：empty string key
# ---------------------------------------------------------------------------
def test_merge_pricing_empty_key():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"": [1, 2]})
    assert "" in table
    assert table[""] == (1.0, 2.0)
    print("  [PASS] empty_key")


# ---------------------------------------------------------------------------
# 47. _pricing_table：config nested missing
# ---------------------------------------------------------------------------
def test_pricing_table_nested_missing():
    cfg = {}
    table = ai_sessions._pricing_table(cfg)
    assert isinstance(table, dict)
    assert "gpt-4o" in table
    print("  [PASS] nested_missing")


# ---------------------------------------------------------------------------
# 48. _model_price：builtin o-series
# ---------------------------------------------------------------------------
def test_model_price_o_series():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "o3-mini") == (1.1, 4.4, 1.1, 1.1)
    assert ai_sessions._model_price(table, "o4-mini") == (1.1, 4.4, 1.1, 1.1)
    assert ai_sessions._model_price(table, "o3-pro") == (20.0, 80.0, 20.0, 20.0)
    print("  [PASS] o_series")


# ---------------------------------------------------------------------------
# 49. _merge_pricing：zero / negative values allowed
# ---------------------------------------------------------------------------
def test_merge_pricing_zero_negative():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m1": [0, 0], "m2": [-1, -2]})
    assert table["m1"] == (0.0, 0.0)
    assert table["m2"] == (-1.0, -2.0)
    print("  [PASS] zero_negative")


# ---------------------------------------------------------------------------
# 50. _pricing_table：multiple file reloads
# ---------------------------------------------------------------------------
def test_pricing_table_reload(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    fp = os.path.join(root, "ai_pricing.json")
    cfg = {"data_root": root, "ai_sessions": {"costs": {"enabled": True}}}
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump({"x": [1, 2]}, fh)
    assert ai_sessions._pricing_table(cfg).get("x") == (1.0, 2.0)
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump({"x": [3, 4]}, fh)
    assert ai_sessions._pricing_table(cfg).get("x") == (3.0, 4.0)
    print("  [PASS] reload")


# ---------------------------------------------------------------------------
# 51. _model_price：builtin claude family
# ---------------------------------------------------------------------------
def test_model_price_claude_family():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "claude-opus-5") == (5.0, 25.0, 0.5, 6.25)
    assert ai_sessions._model_price(table, "claude-sonnet-5") == (2.0, 10.0, 0.2, 2.5)
    assert ai_sessions._model_price(table, "claude-haiku") == (0.25, 1.25, 0.025, 0.3125)
    print("  [PASS] claude_family")


# ---------------------------------------------------------------------------
# 52. _model_price：builtin gpt family
# ---------------------------------------------------------------------------
def test_model_price_gpt_family():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "gpt-5") == (1.25, 10.0, 0.125, 0.0)
    assert ai_sessions._model_price(table, "gpt-5-mini") == (0.25, 2.0, 0.025, 0.0)
    assert ai_sessions._model_price(table, "gpt-4.1") == (2.0, 8.0, 2.0, 2.0)
    assert ai_sessions._model_price(table, "gpt-4-turbo") == (10.0, 30.0, 10.0, 10.0)
    assert ai_sessions._model_price(table, "gpt-3.5-turbo") == (0.5, 1.5, 0.5, 0.5)
    print("  [PASS] gpt_family")


# ---------------------------------------------------------------------------
# 53. _merge_pricing：4 元素列表 = 含缓存档的完整定价（Phase 1+2 新格式）
# ---------------------------------------------------------------------------
def test_merge_pricing_long_list():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m": [1, 2, 3, 4]})
    assert table["m"] == (1.0, 2.0, 3.0, 4.0)
    print("  [PASS] long_list")


# ---------------------------------------------------------------------------
# 54. _pricing_table：file with extra keys
# ---------------------------------------------------------------------------
def test_pricing_table_file_extra_keys(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {"data_root": root, "ai_sessions": {"costs": {"enabled": True}}}
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"new-model": [1, 2], "gpt-4o": [99, 99]}, fh)
    table = ai_sessions._pricing_table(cfg)
    assert table["new-model"] == (1.0, 2.0)
    assert table["gpt-4o"] == (99.0, 99.0)
    print("  [PASS] file_extra_keys")


# ---------------------------------------------------------------------------
# 55. _model_price：unicode normalization
# ---------------------------------------------------------------------------
def test_model_price_unicode_key():
    table = {"m-模型": (1.0, 2.0)}
    assert ai_sessions._model_price(table, "m-模型") == (1.0, 2.0, 1.0, 1.0)
    print("  [PASS] unicode_key")


# ---------------------------------------------------------------------------
# 56. _merge_pricing：dict with missing fields
# ---------------------------------------------------------------------------
def test_merge_pricing_dict_missing_fields():
    table: dict = {}
    ai_sessions._merge_pricing(table, {"m": {"input": 1}})
    assert table["m"] == (1.0, 0.0)
    ai_sessions._merge_pricing(table, {"m2": {"output": 2}})
    assert table["m2"] == (0.0, 2.0)
    print("  [PASS] dict_missing_fields")


# ---------------------------------------------------------------------------
# 57. _model_price：builtin gemma / doubao / moonshot / kimi
# ---------------------------------------------------------------------------
def test_model_price_common_models():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "gemma") == (0.2, 0.6, 0.2, 0.2)
    assert ai_sessions._model_price(table, "gemma-2") == (0.2, 0.6, 0.2, 0.2)   # 无专键 → 兜底 gemma
    assert ai_sessions._model_price(table, "doubao") == (0.3, 0.6, 0.3, 0.3)
    assert ai_sessions._model_price(table, "moonshot") == (1.0, 3.0, 1.0, 1.0)
    assert ai_sessions._model_price(table, "kimi") == (3.0, 15.0, 3.0, 3.0)     # kimi-k3 现价
    print("  [PASS] common_models")


# ---------------------------------------------------------------------------
# 58. _pricing_table：file with dict format
# ---------------------------------------------------------------------------
def test_pricing_table_file_dict_format(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {"data_root": root, "ai_sessions": {"costs": {"enabled": True}}}
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"m": {"input": 1.5, "output": 2.5}}, fh)
    table = ai_sessions._pricing_table(cfg)
    assert table["m"] == (1.5, 2.5)
    print("  [PASS] file_dict_format")


# ---------------------------------------------------------------------------
# 59. _model_price：builtin qwen variants
# ---------------------------------------------------------------------------
def test_model_price_qwen_variants():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "qwen-turbo") == (0.3, 0.6, 0.06, 0.3)
    assert ai_sessions._model_price(table, "qwen-plus") == (0.26, 0.78, 0.052, 0.26)
    assert ai_sessions._model_price(table, "qwen-max") == (1.6, 6.4, 0.32, 1.6)
    print("  [PASS] qwen_variants")


# ---------------------------------------------------------------------------
# 60. _model_price：builtin deepseek variants
# ---------------------------------------------------------------------------
def test_model_price_deepseek_variants():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "deepseek-r1") == (0.7, 2.5, 0.07, 0.7)
    assert ai_sessions._model_price(table, "deepseek-v4-flash") == (0.089, 0.177, 0.0089, 0.089)
    assert ai_sessions._model_price(table, "deepseek-v4-flash-free") == (0.089, 0.177, 0.0089, 0.089)
    assert ai_sessions._model_price(table, "deepseek-chat") == (0.257, 1.029, 0.0257, 0.257)
    assert ai_sessions._model_price(table, "deepseek-v3") == (0.27, 1.1, 0.027, 0.27)
    print("  [PASS] deepseek_variants")


# ---------------------------------------------------------------------------
# 61. _merge_pricing：overwrite from file then config
# ---------------------------------------------------------------------------
def test_merge_pricing_file_then_config(tmp_path):
    root = str(tmp_path / "pricing")
    os.makedirs(root, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"costs": {"enabled": True, "model_pricing": {"m": [2, 4]}}},
    }
    with open(os.path.join(root, "ai_pricing.json"), "w", encoding="utf-8") as fh:
        json.dump({"m": [7, 14]}, fh)
    table = ai_sessions._pricing_table(cfg)
    assert table["m"] == (7.0, 14.0)
    print("  [PASS] file_then_config")


# ---------------------------------------------------------------------------
# 62. _model_price：builtin kimi / moonshot / hunyuan / ernie / baichuan / minimax / step / yi
# ---------------------------------------------------------------------------
def test_model_price_other_models():
    table = ai_sessions._pricing_table({})
    assert ai_sessions._model_price(table, "kimi") == (3.0, 15.0, 3.0, 3.0)      # kimi-k3 现价
    assert ai_sessions._model_price(table, "kimi-k2") == (0.57, 2.3, 0.57, 0.57)
    assert ai_sessions._model_price(table, "moonshot") == (1.0, 3.0, 1.0, 1.0)
    assert ai_sessions._model_price(table, "hunyuan") == (0.2, 0.9, 0.2, 0.2)
    assert ai_sessions._model_price(table, "ernie") == (0.57, 2.57, 0.57, 0.57)
    assert ai_sessions._model_price(table, "baichuan") == (0.5, 1.5, 0.5, 0.5)
    assert ai_sessions._model_price(table, "minimax") == (0.3, 1.2, 0.3, 0.3)
    assert ai_sessions._model_price(table, "step-1") == (0.5, 2.0, 0.5, 0.5)
    assert ai_sessions._model_price(table, "yi") == (1.0, 3.0, 1.0, 1.0)
    print("  [PASS] other_models")


# ---------------------------------------------------------------------------
# 63. _price4：2 元组自动补 (in, out, in, in)（未配缓存档按输入价计，保守高估）
# ---------------------------------------------------------------------------
def test_price4_two_tuple_expands_to_input_price():
    # 2 元组 (in, out) 自动补 (in, in)：缓存读/写都按输入价计（保守高估）
    assert ai_sessions._price4((1.0, 2.0)) == (1.0, 2.0, 1.0, 1.0)
    assert ai_sessions._price4((2.5, 10.0)) == (2.5, 10.0, 2.5, 2.5)
    # list 表值同样归一化
    assert ai_sessions._price4([2.5, 10.0]) == (2.5, 10.0, 2.5, 2.5)
    print("  [PASS] price4_two_tuple_expands")


# ---------------------------------------------------------------------------
# 64. _price4：显式 4 元组按档原样取；非法/过短值归零
# ---------------------------------------------------------------------------
def test_price4_four_tuple_passthrough_and_invalid():
    assert ai_sessions._price4((1.0, 2.0, 0.1, 1.25)) == (1.0, 2.0, 0.1, 1.25)
    assert ai_sessions._price4([3.0, 15.0, 0.3, 3.75]) == (3.0, 15.0, 0.3, 3.75)
    # 非法值：过短 / 非数值 / None → (0, 0, 0, 0)
    assert ai_sessions._price4((1.0,)) == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._price4("bad") == (0.0, 0.0, 0.0, 0.0)
    assert ai_sessions._price4(None) == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] price4_four_tuple_passthrough")


# ---------------------------------------------------------------------------
# 65. _model_price：显式 4 元组按档取（缓存读/写不再退化为输入价）
# ---------------------------------------------------------------------------
def test_model_price_explicit_four_tuple_tiers():
    table = {"m": (1.0, 2.0, 0.1, 1.25)}
    assert ai_sessions._model_price(table, "m") == (1.0, 2.0, 0.1, 1.25)
    # 子串命中同样返回显式 4 元组
    assert ai_sessions._model_price(table, "m-pro") == (1.0, 2.0, 0.1, 1.25)
    print("  [PASS] model_price_four_tuple_tiers")


# ---------------------------------------------------------------------------
# 66. _model_price：claude 系缓存档 = 读 0.1×输入、写 1.25×输入
# ---------------------------------------------------------------------------
def test_model_price_claude_cache_ratios():
    table = ai_sessions._pricing_table({})
    for name, in_out in (("claude-sonnet-4-5", (3.0, 15.0)),
                         ("claude-opus-5", (5.0, 25.0)),
                         ("claude-haiku-4-5", (1.0, 5.0))):
        p = ai_sessions._model_price(table, name)
        assert p[:2] == in_out, f"{name} 进出价应 {in_out}，实际 {p[:2]}"
        assert abs(p[2] - 0.1 * p[0]) < 1e-9, f"{name} 缓存读应 0.1×输入，实际 {p[2]}"
        assert abs(p[3] - 1.25 * p[0]) < 1e-9, f"{name} 缓存写应 1.25×输入，实际 {p[3]}"
    print("  [PASS] claude_cache_ratios")


# ---------------------------------------------------------------------------
# 67. _model_price：deepseek 裸键兜底（未知新版本型号不再 0 价）
# ---------------------------------------------------------------------------
def test_model_price_deepseek_bare_key_fallback():
    table = ai_sessions._pricing_table({})
    # 表里没有 "deepseek-v4.1-flash"（只有 v4-flash/v4-pro 等）→ 落到裸键 "deepseek"
    p = ai_sessions._model_price(table, "deepseek-v4.1-flash-expires-on-0910")
    assert p == (0.27, 1.10, 0.027, 0.27), f"deepseek 裸键兜底价，实际 {p}"
    # 与已知家族型号同价（对齐 v3.x 主力价）
    assert p == ai_sessions._model_price(table, "deepseek-v3")
    # 完全未知的非 deepseek 家族型号仍是 0 价（不能误兜底）
    assert ai_sessions._model_price(table, "zzz-brand-new-ai") == (0.0, 0.0, 0.0, 0.0)
    print("  [PASS] deepseek_bare_key_fallback")


# ---------------------------------------------------------------------------
# 68. _merge_pricing：四种格式兼容（[in,out] / [in,out,cr,cw] /
#     {input,output} / {input,output,cache_read,cache_write}）
# ---------------------------------------------------------------------------
def test_merge_pricing_all_four_formats():
    table: dict = {}
    ai_sessions._merge_pricing(table, {
        "a-list2": [1, 2],
        "b-list4": [1, 2, 0.1, 1.25],
        "c-dict2": {"input": 3, "output": 4},
        "d-dict4": {"input": 5, "output": 6, "cache_read": 0.5, "cache_write": 6.25},
    })
    assert table["a-list2"] == (1.0, 2.0)
    assert table["b-list4"] == (1.0, 2.0, 0.1, 1.25)
    assert table["c-dict2"] == (3.0, 4.0)
    assert table["d-dict4"] == (5.0, 6.0, 0.5, 6.25)
    # dict 只给 cache_read 时 cache_write 按输入价补（保守高估，与 2 元组口径一致）
    ai_sessions._merge_pricing(table, {"e-part": {"input": 2, "output": 4, "cache_read": 0.2}})
    assert table["e-part"] == (2.0, 4.0, 0.2, 2.0)
    print("  [PASS] merge_pricing_all_four_formats")
