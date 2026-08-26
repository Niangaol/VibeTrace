# -*- coding: utf-8 -*-
"""tests/unit/test_git_auto_discover.py — git auto_discover 测试（模拟目录结构，验证递归深度≤3、去重、项目名推断）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import git_insights  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_repo(path):
    os.makedirs(os.path.join(path, ".git"), exist_ok=True)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# repo\n")


# ---------------------------------------------------------------------------
# 1. 空输入返回空
# ---------------------------------------------------------------------------
def test_empty_input():
    assert git_insights.auto_discover_repos([]) == []
    print("  [PASS] empty_input")


# ---------------------------------------------------------------------------
# 2. 单层目录发现 .git
# ---------------------------------------------------------------------------
def test_single_layer_discovery(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo1")
    _create_repo(repo)
    result = git_insights.auto_discover_repos([root])
    assert len(result) == 1
    assert result[0]["name"] == "repo1"
    assert result[0]["path"] == repo
    print("  [PASS] single_layer")


# ---------------------------------------------------------------------------
# 3. 递归深度控制：max_depth=2 发现 depth 1，剪枝 depth 2
# ---------------------------------------------------------------------------
def test_max_depth_2_prunes_deeper(tmp_path):
    root = str(tmp_path)
    r0 = os.path.join(root, "r0")
    _create_repo(r0)
    d1 = os.path.join(root, "d1")
    r1 = os.path.join(d1, "r1")
    _create_repo(r1)

    result = git_insights.auto_discover_repos([root], max_depth=2)
    paths = [r["path"] for r in result]
    assert r0 in paths, f"r0 not in {paths}"
    assert r1 not in paths, f"r1 should not be in {paths}"
    print("  [PASS] max_depth_2")


# ---------------------------------------------------------------------------
# 4. 递归深度控制：max_depth=3 发现 depth 2，剪枝 depth 3
# ---------------------------------------------------------------------------
def test_max_depth_3_prunes_deeper(tmp_path):
    root = str(tmp_path)
    r0 = os.path.join(root, "r0")
    _create_repo(r0)
    d1 = os.path.join(root, "d1")
    r1 = os.path.join(d1, "r1")
    _create_repo(r1)
    d2 = os.path.join(d1, "d2")
    r2 = os.path.join(d2, "r2")
    _create_repo(r2)

    result = git_insights.auto_discover_repos([root], max_depth=3)
    paths = [r["path"] for r in result]
    assert r0 in paths, f"r0 not in {paths}"
    assert r1 in paths, f"r1 not in {paths}"
    assert r2 not in paths, f"r2 should not be in {paths}"
    print("  [PASS] max_depth_3")


# ---------------------------------------------------------------------------
# 5. 递归深度控制：max_depth=4 发现 depth 3
# ---------------------------------------------------------------------------
def test_max_depth_4_reaches_depth_3(tmp_path):
    root = str(tmp_path)
    r0 = os.path.join(root, "r0")
    _create_repo(r0)
    d1 = os.path.join(root, "d1")
    r1 = os.path.join(d1, "r1")
    _create_repo(r1)
    d2 = os.path.join(d1, "d2")
    r2 = os.path.join(d2, "r2")
    _create_repo(r2)

    result = git_insights.auto_discover_repos([root], max_depth=4)
    paths = [r["path"] for r in result]
    assert r0 in paths
    assert r1 in paths
    assert r2 in paths
    print("  [PASS] max_depth_4")


# ---------------------------------------------------------------------------
# 6. 去重（相同路径不重复）
# ---------------------------------------------------------------------------
def test_deduplication(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)
    result = git_insights.auto_discover_repos([root, root])
    assert len(result) == 1
    print("  [PASS] dedup_same_root")


# ---------------------------------------------------------------------------
# 7. 项目名推断：优先 remote origin，其次目录名
# ---------------------------------------------------------------------------
def test_name_inference_from_remote(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "myrepo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        if args == ["remote", "get-url", "origin"]:
            return "https://github.com/org/upstream-repo.git\n"
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "upstream-repo"
    print("  [PASS] name_from_remote")


def test_name_fallback_to_dirname(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "myrepo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "myrepo"
    print("  [PASS] name_from_dirname")


# ---------------------------------------------------------------------------
# 8. 跳过目录：node_modules / venv / __pycache__ / dist / build
# ---------------------------------------------------------------------------
def test_skip_dirs(tmp_path, monkeypatch):
    root = str(tmp_path)
    for skip in ["node_modules", "venv", "__pycache__", "dist", "build"]:
        d = os.path.join(root, skip)
        _create_repo(d)
    result = git_insights.auto_discover_repos([root])
    assert len(result) == 0
    print("  [PASS] skip_dirs")


# ---------------------------------------------------------------------------
# 9. 非目录 search_root 被忽略
# ---------------------------------------------------------------------------
def test_non_dir_root(tmp_path):
    f = str(tmp_path / "not_a_dir")
    open(f, "w").close()
    result = git_insights.auto_discover_repos([f])
    assert result == []
    print("  [PASS] non_dir_root")


# ---------------------------------------------------------------------------
# 10. 多个 search_roots 合并
# ---------------------------------------------------------------------------
def test_multiple_roots(tmp_path):
    r1 = str(tmp_path / "root1")
    r2 = str(tmp_path / "root2")
    _create_repo(r1)
    _create_repo(r2)
    result = git_insights.auto_discover_repos([r1, r2])
    assert len(result) == 2
    print("  [PASS] multiple_roots")


# ---------------------------------------------------------------------------
# 11. .git 文件（子模块）也识别
# ---------------------------------------------------------------------------
def test_git_file_submodule(tmp_path):
    root = str(tmp_path)
    repo = os.path.join(root, "submod")
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: ../.git/modules/submod\n")
    result = git_insights.auto_discover_repos([root])
    assert len(result) == 1
    assert result[0]["path"] == repo
    print("  [PASS] git_file")


# ---------------------------------------------------------------------------
# 12. remote origin 带 .git 后缀截断
# ---------------------------------------------------------------------------
def test_remote_git_suffix_stripped(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        if args == ["remote", "get-url", "origin"]:
            return "https://github.com/org/repo.git\n"
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "repo"
    print("  [PASS] remote_git_suffix")


# ---------------------------------------------------------------------------
# 13. remote origin 无路径段时回退目录名
# ---------------------------------------------------------------------------
def test_remote_no_path_segment(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        if args == ["remote", "get-url", "origin"]:
            return "not-a-url\n"
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "not-a-url"
    print("  [PASS] remote_no_path_segment")


# ---------------------------------------------------------------------------
# 14. 同路径不同大小写（Windows）去重
# ---------------------------------------------------------------------------
def test_case_insensitive_dedup(tmp_path):
    root = str(tmp_path)
    repo = os.path.join(root, "Repo")
    _create_repo(repo)
    result = git_insights.auto_discover_repos([root, root])
    assert len(result) == 1
    print("  [PASS] case_dedup")


# ---------------------------------------------------------------------------
# 15. _run_git 超时/失败时回退目录名
# ---------------------------------------------------------------------------
def test_run_git_failure_fallback(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "repo"
    print("  [PASS] run_git_failure")


# ---------------------------------------------------------------------------
# 16. 大量仓库性能/去重
# ---------------------------------------------------------------------------
def test_many_repos_dedup(tmp_path, monkeypatch):
    root = str(tmp_path)
    for i in range(10):
        d = os.path.join(root, f"repo-{i}")
        _create_repo(d)
    result = git_insights.auto_discover_repos([root, root, root])
    assert len(result) == 10
    print("  [PASS] many_repos_dedup")


# ---------------------------------------------------------------------------
# 17. skip_dirs 大小写不敏感
# ---------------------------------------------------------------------------
def test_skip_dirs_case_insensitive(tmp_path):
    root = str(tmp_path)
    for skip in ["NODE_MODULES", "Venv", "Build"]:
        d = os.path.join(root, skip)
        _create_repo(d)
    result = git_insights.auto_discover_repos([root])
    assert len(result) == 0
    print("  [PASS] skip_case_insensitive")


# ---------------------------------------------------------------------------
# 18. 路径归一化（绝对路径）
# ---------------------------------------------------------------------------
def test_paths_are_absolute(tmp_path):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)
    result = git_insights.auto_discover_repos([root])
    assert os.path.isabs(result[0]["path"])
    print("  [PASS] absolute_paths")


# ---------------------------------------------------------------------------
# 19. 结果按 path 去重且顺序保留
# ---------------------------------------------------------------------------
def test_result_order_preserved(tmp_path):
    root = str(tmp_path)
    for name in ["a", "b", "c"]:
        d = os.path.join(root, name)
        _create_repo(d)
    result = git_insights.auto_discover_repos([root])
    assert [r["name"] for r in result] == ["a", "b", "c"]
    print("  [PASS] order_preserved")


# ---------------------------------------------------------------------------
# 20. remote origin 带用户名/密码（截取最后一段）
# ---------------------------------------------------------------------------
def test_remote_with_credentials(tmp_path, monkeypatch):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)

    def fake_run_git(args, cwd, timeout):
        if args == ["remote", "get-url", "origin"]:
            return "git@github.com:org/repo.git\n"
        return None

    monkeypatch.setattr(git_insights, "_run_git", fake_run_git)
    result = git_insights.auto_discover_repos([root])
    assert result[0]["name"] == "repo"
    print("  [PASS] remote_with_credentials")


# ---------------------------------------------------------------------------
# 21. 多个 search_roots 中有重复仓库
# ---------------------------------------------------------------------------
def test_multi_root_duplicate_repos(tmp_path):
    root = str(tmp_path)
    repo = os.path.join(root, "repo")
    _create_repo(repo)
    result = git_insights.auto_discover_repos([root, root])
    assert len(result) == 1
    print("  [PASS] multi_root_duplicate")
