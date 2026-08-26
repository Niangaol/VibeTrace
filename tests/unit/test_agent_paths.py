# -*- coding: utf-8 -*-
"""tests/unit/test_agent_paths.py — Agent 路径探测测试（20+ case，Windows/Linux 路径展开）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


# ---------------------------------------------------------------------------
# 1. _expand：普通路径不变（仅 strip）
# ---------------------------------------------------------------------------
def test_expand_plain_path():
    assert ai_sessions._expand("/usr/local/share") == "/usr/local/share"
    assert ai_sessions._expand("relative/path") == "relative/path"
    print("  [PASS] plain_path")


# ---------------------------------------------------------------------------
# 2. _expand：~ 展开为 HOME
# ---------------------------------------------------------------------------
def test_expand_tilde(monkeypatch, tmp_path):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home, exist_ok=True)
    original_expanduser = os.path.expanduser

    def fake_expanduser(p):
        if p == "~":
            return fake_home
        if p.startswith("~" + os.sep) or p.startswith("~/"):
            return os.path.join(fake_home, p[2:])
        return original_expanduser(p)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    result = ai_sessions._expand("~/.config/opencode")
    expected = os.path.join(fake_home, ".config", "opencode")
    assert os.path.normpath(result) == os.path.normpath(expected), f"{result} != {expected}"
    print("  [PASS] tilde_expand")


# ---------------------------------------------------------------------------
# 3. _expand：%VAR% 环境变量展开（Windows 风格）
# ---------------------------------------------------------------------------
def test_expand_env_var(monkeypatch):
    monkeypatch.setenv("MY_DATA", "/tmp/my-data")
    assert ai_sessions._expand("%MY_DATA%/logs") == "/tmp/my-data/logs"
    print("  [PASS] env_var_expand")


# ---------------------------------------------------------------------------
# 4. _expand：组合 ~ + %VAR%
# ---------------------------------------------------------------------------
def test_expand_combined(monkeypatch, tmp_path):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home, exist_ok=True)
    original_expanduser = os.path.expanduser

    def fake_expanduser(p):
        if p == "~":
            return fake_home
        if p.startswith("~" + os.sep) or p.startswith("~/"):
            return os.path.join(fake_home, p[2:])
        return original_expanduser(p)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    monkeypatch.setenv("SUB", "subdir")
    result = ai_sessions._expand("~/%SUB%")
    expected = os.path.join(fake_home, "subdir")
    assert os.path.normpath(result) == os.path.normpath(expected), f"{result} != {expected}"
    print("  [PASS] combined_expand")


# ---------------------------------------------------------------------------
# 5. _expand：首尾空格去除
# ---------------------------------------------------------------------------
def test_expand_strip():
    assert ai_sessions._expand("  /path  ") == "/path"
    print("  [PASS] strip_whitespace")


# ---------------------------------------------------------------------------
# 6. _expand：空字符串返回空
# ---------------------------------------------------------------------------
def test_expand_empty():
    assert ai_sessions._expand("") == ""
    assert ai_sessions._expand("   ") == ""
    print("  [PASS] empty_string")


# ---------------------------------------------------------------------------
# 7. _default_tool_paths：返回所有工具的展开路径
# ---------------------------------------------------------------------------
def test_default_tool_paths_returns_all():
    paths = ai_sessions._default_tool_paths()
    assert isinstance(paths, dict)
    assert "opencode" in paths
    assert "chatgpt" in paths
    assert "claude" in paths
    print("  [PASS] default_tool_paths_returns_all")


# ---------------------------------------------------------------------------
# 8. _default_tool_paths：每个工具有多个候选路径
# ---------------------------------------------------------------------------
def test_default_tool_paths_multiple_candidates():
    paths = ai_sessions._default_tool_paths()
    for tool, dirs in paths.items():
        assert isinstance(dirs, list)
        assert len(dirs) >= 1
    print("  [PASS] multiple_candidates")


# ---------------------------------------------------------------------------
# 9. _default_tool_paths：路径已展开（无 ~）
# ---------------------------------------------------------------------------
def test_default_tool_paths_expanded():
    paths = ai_sessions._default_tool_paths()
    for dirs in paths.values():
        for d in dirs:
            assert "~" not in d
    print("  [PASS] paths_expanded")


# ---------------------------------------------------------------------------
# 10. _config_paths：未配置时返回默认路径
# ---------------------------------------------------------------------------
def test_config_paths_default():
    cfg = {}
    paths = ai_sessions._config_paths(cfg)
    assert isinstance(paths, dict)
    assert len(paths) > 0
    print("  [PASS] config_paths_default")


# ---------------------------------------------------------------------------
# 11. _config_paths：配置覆盖
# ---------------------------------------------------------------------------
def test_config_paths_override():
    cfg = {
        "ai_sessions": {
            "paths": {
                "custom_tool": ["/custom/path1", "/custom/path2"]
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert "custom_tool" in paths
    assert paths["custom_tool"] == ["/custom/path1", "/custom/path2"]
    print("  [PASS] config_paths_override")


# ---------------------------------------------------------------------------
# 12. _config_paths：空列表工具被跳过
# ---------------------------------------------------------------------------
def test_config_paths_empty_skipped():
    cfg = {
        "ai_sessions": {
            "paths": {
                "empty_tool": []
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert "empty_tool" not in paths
    print("  [PASS] empty_skipped")


# ---------------------------------------------------------------------------
# 13. _config_paths：混合默认 + 自定义
# ---------------------------------------------------------------------------
def test_config_paths_mixed():
    cfg = {
        "ai_sessions": {
            "paths": {
                "opencode": ["/my/opencode"],
                "new_tool": ["/new/tool"]
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert paths["opencode"] == ["/my/opencode"]
    assert paths["new_tool"] == ["/new/tool"]
    print("  [PASS] mixed_paths")


# ---------------------------------------------------------------------------
# 14. _default_tool_paths：Windows 路径含 %APPDATA% / %LOCALAPPDATA%
# ---------------------------------------------------------------------------
def test_default_tool_paths_windows_style():
    paths = ai_sessions._default_tool_paths()
    for d in paths.get("opencode", []):
        assert "~" not in d
    for d in paths.get("claude", []):
        assert "~" not in d
    print("  [PASS] windows_style_expanded")


# ---------------------------------------------------------------------------
# 15. _config_paths：Windows 风格路径展开
# ---------------------------------------------------------------------------
def test_config_paths_windows_style(monkeypatch):
    monkeypatch.setenv("APPDATA", "/fake/appdata")
    monkeypatch.setenv("LOCALAPPDATA", "/fake/local")
    cfg = {
        "ai_sessions": {
            "paths": {
                "my_tool": ["%APPDATA%/MyTool", "%LOCALAPPDATA%/MyTool"]
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert "/fake/appdata/MyTool" in paths["my_tool"]
    assert "/fake/local/MyTool" in paths["my_tool"]
    print("  [PASS] windows_style_config")


# ---------------------------------------------------------------------------
# 16. _default_tool_paths：Linux 路径含 ~
# ---------------------------------------------------------------------------
def test_default_tool_paths_linux_tilde(monkeypatch, tmp_path):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home, exist_ok=True)
    original_expanduser = os.path.expanduser

    def fake_expanduser(p):
        if p == "~":
            return fake_home
        if p.startswith("~" + os.sep) or p.startswith("~/"):
            return os.path.join(fake_home, p[2:])
        return original_expanduser(p)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    paths = ai_sessions._default_tool_paths()
    for dirs in paths.values():
        for d in dirs:
            assert not d.startswith("~")
    print("  [PASS] linux_tilde_expanded")


# ---------------------------------------------------------------------------
# 17. _config_paths：无效类型跳过
# ---------------------------------------------------------------------------
def test_config_paths_invalid_type():
    cfg = {"ai_sessions": {"paths": "not-a-dict"}}
    paths = ai_sessions._config_paths(cfg)
    assert isinstance(paths, dict)
    assert len(paths) > 0
    print("  [PASS] invalid_type_fallback")


# ---------------------------------------------------------------------------
# 18. _config_paths：非 list 值跳过
# ---------------------------------------------------------------------------
def test_config_paths_non_list_value():
    cfg = {
        "ai_sessions": {
            "paths": {
                "bad_tool": "/single/path"
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert "bad_tool" not in paths
    print("  [PASS] non_list_skipped")


# ---------------------------------------------------------------------------
# 19. _expand：Windows 盘符路径（WSL/Linux 下不会出现，但不应崩溃）
# ---------------------------------------------------------------------------
def test_expand_windows_drive():
    result = ai_sessions._expand("C:\\Users\\Test\\AppData")
    assert "C:" in result
    print("  [PASS] windows_drive_path")


# ---------------------------------------------------------------------------
# 20. _default_tool_paths：工具名 key 为字符串
# ---------------------------------------------------------------------------
def test_default_tool_paths_string_keys():
    paths = ai_sessions._default_tool_paths()
    for key in paths:
        assert isinstance(key, str)
    print("  [PASS] string_keys")


# ---------------------------------------------------------------------------
# 21. _config_paths：工具名 key 被 str 化
# ---------------------------------------------------------------------------
def test_config_paths_stringify_keys():
    cfg = {
        "ai_sessions": {
            "paths": {
                123: ["/path"]
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert "123" in paths
    print("  [PASS] stringify_keys")


# ---------------------------------------------------------------------------
# 22. _expand：含空格路径
# ---------------------------------------------------------------------------
def test_expand_spaces():
    result = ai_sessions._expand("/path with spaces/file")
    assert "with spaces" in result
    print("  [PASS] spaces_in_path")


# ---------------------------------------------------------------------------
# 23. _default_tool_paths：空 dirs 被过滤
# ---------------------------------------------------------------------------
def test_default_tool_paths_empty_dirs():
    paths = ai_sessions._default_tool_paths()
    for tool, dirs in paths.items():
        assert len(dirs) >= 1
    print("  [PASS] no_empty_dirs")


# ---------------------------------------------------------------------------
# 24. _config_paths：自定义路径覆盖全部默认
# ---------------------------------------------------------------------------
def test_config_paths_full_override():
    cfg = {
        "ai_sessions": {
            "paths": {
                "opencode": ["/only/this"],
                "chatgpt": ["/only/that"],
            }
        }
    }
    paths = ai_sessions._config_paths(cfg)
    assert paths["opencode"] == ["/only/this"]
    assert paths["chatgpt"] == ["/only/that"]
    print("  [PASS] full_override")


# ---------------------------------------------------------------------------
# 25. _expand：None 输入
# ---------------------------------------------------------------------------
def test_expand_none():
    assert ai_sessions._expand(None) == ""
    print("  [PASS] none_input")
