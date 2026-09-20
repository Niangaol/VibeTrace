# -*- coding: utf-8 -*-
"""tests/unit/test_git_range_batch.py - v2.9.11 区间批量等价性钉扉。

range_batch(days) 把 N 天的逐日 git log 压成一次区间查询。本文件确保：
  1. 区间结果与逐日调用逐字段等价（含跨日 amend 的 committer date 分桶）；
  2. 批内每天只跑一次 git、只 walk 一次目录（性能收益真实存在）；
  3. 批外行为与旧版完全一致；
  4. 同区间嵌套复用、异常后状态恢复。
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import git_insights  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_batch():
    saved = git_insights._RANGE_BATCH
    git_insights._RANGE_BATCH = None
    try:
        yield
    finally:
        git_insights._RANGE_BATCH = saved


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _commit(repo, day, msg, fname=None):
    """在指定日期（ISO）造一个提交。"""
    fname = fname or ("f_%s.txt" % msg)
    with open(os.path.join(repo, fname), "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")
    _git(repo, "add", "-A")
    d = "%sT12:00:00" % day
    env = dict(os.environ, GIT_AUTHOR_DATE=d, GIT_COMMITTER_DATE=d)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True,
                   capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    if not _can_spawn_git():
        pytest.skip("当前进程无法启动 git 子进程（WinError 6）")
    r = str(tmp_path / "repo")
    os.makedirs(r)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "Tester")
    return r



def _can_spawn_git() -> bool:
    """探测当前进程能启启动子进程（Windows 下 stdin 句柄无效时不能）。"""
    try:
        subprocess.run(["git", "--version"], capture_output=True,
                       timeout=10, check=False)
        return True
    except OSError:
        return False


requires_git = pytest.mark.skipif(
    not _can_spawn_git(),
    reason="需要能启动 git 子进程：当前进程继承的 stdin 句柄无效（WinError 6）")

CFG = {"insights": {"enabled": True, "git": {
    "enabled": True, "projects": [], "auto_discover": False, "deep": False,
    "timeout_s": 10, "top_files": 5}}}


def _cfg_with(repo_path):
    c = copy.deepcopy(CFG)
    c["insights"]["git"]["projects"] = [{"name": "r", "path": repo_path}]
    return c


@requires_git
def test_range_equals_per_day(repo):
    """核心：区间批量结果与逐日调用逐字段一致。"""
    _commit(repo, "2026-09-14", "a")
    _commit(repo, "2026-09-15", "b")
    _commit(repo, "2026-09-16", "c")
    _commit(repo, "2026-09-16", "c2", "c2.txt")
    _commit(repo, "2026-09-18", "d")
    days = ["2026-09-14", "2026-09-15", "2026-09-16",
            "2026-09-17", "2026-09-18"]
    cfg = _cfg_with(repo)
    per_day = {d: git_insights.git_insights(cfg, d) for d in days}
    with git_insights.range_batch(days):
        batched = {d: git_insights.git_insights(cfg, d) for d in days}
    for d in days:
        assert batched[d] == per_day[d], d


@requires_git
def test_empty_days_inside_range(repo):
    """区间内没有提交的日期：与逐日一致返回 found=False。"""
    _commit(repo, "2026-09-15", "x")
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    cfg = _cfg_with(repo)
    with git_insights.range_batch(days):
        r = git_insights.git_insights(cfg, "2026-09-14")
    assert r["found"] is False
    assert r["total"]["commit_count"] == 0

@requires_git
def test_batch_runs_git_once_per_repo(repo, monkeypatch):
    """批内 N 天只跑一次 git log（性能收益的真实性）。"""
    for d in ("2026-09-14", "2026-09-15", "2026-09-16"):
        _commit(repo, d, "c" + d)
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    cfg = _cfg_with(repo)
    calls = []
    real_run = git_insights._run_git

    def spy(args, cwd, timeout):
        calls.append(tuple(args))
        return real_run(args, cwd, timeout)

    monkeypatch.setattr(git_insights, "_run_git", spy)
    with git_insights.range_batch(days):
        for d in days:
            git_insights.git_insights(cfg, d)
    log_calls = [c for c in calls if c and c[0] == "log"]
    assert len(log_calls) == 1, log_calls


@requires_git
def test_batch_walks_dirs_once(repo, monkeypatch):
    """auto_discover 开启时，批内只 walk 一次。"""
    _commit(repo, "2026-09-15", "x")
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    cfg = copy.deepcopy(CFG)
    cfg["insights"]["git"]["auto_discover"] = True
    cfg["insights"]["git"]["projects"] = [os.path.dirname(repo)]
    walks = []
    real_walk = git_insights.auto_discover_repos

    def spy(roots, max_depth=3):
        walks.append(tuple(roots))
        return real_walk(roots, max_depth=max_depth)

    monkeypatch.setattr(git_insights, "auto_discover_repos", spy)
    with git_insights.range_batch(days):
        for d in days:
            git_insights.git_insights(cfg, d)
    assert len(walks) == 1, walks


@requires_git
def test_outside_batch_unchanged(repo):
    """批外调用与旧版行为一致。"""
    for d in ("2026-09-14", "2026-09-15"):
        _commit(repo, d, "c" + d)
    cfg = _cfg_with(repo)
    a = git_insights.git_insights(cfg, "2026-09-14")
    b = git_insights.git_insights(cfg, "2026-09-15")
    assert a["total"]["commit_count"] == 1
    assert b["total"]["commit_count"] == 1
    assert git_insights._RANGE_BATCH is None


@requires_git
def test_nested_same_range_reuses(repo):
    """同区间嵌套：内层不会重算。"""
    _commit(repo, "2026-09-15", "x")
    days = ["2026-09-15"]
    cfg = _cfg_with(repo)
    with git_insights.range_batch(days):
        with git_insights.range_batch(days):
            r = git_insights.git_insights(cfg, "2026-09-15")
        assert r["found"] is True
    assert git_insights._RANGE_BATCH is None


@requires_git
def test_batch_state_restored_after_exception(repo):
    """块内抛异常后 _RANGE_BATCH 必须复位。"""
    _commit(repo, "2026-09-15", "x")
    days = ["2026-09-15"]
    cfg = _cfg_with(repo)
    with pytest.raises(RuntimeError):
        with git_insights.range_batch(days):
            raise RuntimeError("boom")
    assert git_insights._RANGE_BATCH is None
    r = git_insights.git_insights(cfg, "2026-09-15")
    assert r["total"]["commit_count"] == 1


@requires_git
def test_committer_date_bucketing(repo):
    """按 committer date 分桶：author date 不同天也不算错天。"""
    f = os.path.join(repo, "late.txt")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    _git(repo, "add", "-A")
    env = dict(os.environ,
               GIT_AUTHOR_DATE="2026-09-14T12:00:00",
               GIT_COMMITTER_DATE="2026-09-15T12:00:00")
    subprocess.run(["git", "commit", "-m", "late"], cwd=repo, check=True,
                   capture_output=True, text=True, env=env)
    cfg = _cfg_with(repo)
    with git_insights.range_batch(["2026-09-14", "2026-09-15"]):
        d14 = git_insights.git_insights(cfg, "2026-09-14")
        d15 = git_insights.git_insights(cfg, "2026-09-15")
    assert d14["found"] is False
    assert d15["found"] is True
    assert d15["total"]["commit_count"] == 1


def test_parse_numstat_cd_backward_compatible():
    """旧格式（无 cd 字段）也能解析，cd 回退为 date。"""
    raw = "\x1eabc123\x1f2026-09-15T12:00:00+08:00\x1fTester\n5\t3\ta.py\n"
    commits = git_insights._parse_numstat(raw)
    assert len(commits) == 1
    assert commits[0]["cd"] == commits[0]["date"]
    assert commits[0]["author"] == "Tester"


def test_parse_numstat_cd_new_format():
    """新格式（带 cd）解析出 committer date。"""
    raw = ("\x1eabc\x1f2026-09-14T12:00:00+08:00\x1fTester"
           "\x1f2026-09-15T12:00:00+08:00\n5\t3\ta.py\n")
    commits = git_insights._parse_numstat(raw)
    assert commits[0]["date"].startswith("2026-09-14")
    assert commits[0]["cd"].startswith("2026-09-15")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

# ---------------------------------------------------------------------------
# 不依赖 git 子进程的等价性验证（CI 无桌面会话也能跑）
# ---------------------------------------------------------------------------
FAKE_COMMITS = [
    {"hash": "h1", "date": "2026-09-14T10:00:00", "cd": "2026-09-14T10:00:00",
     "author": "A", "files": [{"path": "a.py", "added": 10, "deleted": 2}]},
    {"hash": "h2", "date": "2026-09-15T10:00:00", "cd": "2026-09-15T10:00:00",
     "author": "A", "files": [{"path": "b.py", "added": 5, "deleted": 0}]},
    {"hash": "h3", "date": "2026-09-15T11:00:00", "cd": "2026-09-15T11:00:00",
     "author": "B", "files": [{"path": "c.py", "added": 3, "deleted": 7}]},
    {"hash": "h4", "date": "2026-09-14T09:00:00", "cd": "2026-09-16T09:00:00",
     "author": "A", "files": [{"path": "d.py", "added": 1, "deleted": 1}]},
]

ZERO_TOTAL = {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
              "churn": 0, "files": 0, "modify_ratio": 0.0}


def _expected_per_day(day):
    """按 committer date 逐日聚合的期望值（独立于实现）。"""
    sel = [c for c in FAKE_COMMITS if (c.get("cd") or c["date"])[:10] == day]
    if not sel:
        return {"found": False, "total": dict(ZERO_TOTAL)}
    added = sum(f["added"] for c in sel for f in c["files"])
    deleted = sum(f["deleted"] for c in sel for f in c["files"])
    churn = added + deleted
    return {
        "found": True,
        "total": {
            "commit_count": len(sel),
            "lines_added": added,
            "lines_deleted": deleted,
            "churn": churn,
            "files": len({f["path"] for c in sel for f in c["files"]}),
            "modify_ratio": round(deleted / churn, 2) if churn > 0 else 0.0,
        },
    }


@pytest.fixture
def fake_git(monkeypatch):
    """把 git 子进程换成预置 commits，专注验证分桶/汇总。"""
    monkeypatch.setattr(git_insights, "_git_log_numstat",
                        lambda path, since, until, timeout: list(FAKE_COMMITS))
    monkeypatch.setattr(git_insights, "_is_repo", lambda p: True)


CFG_FAKE = {"insights": {"enabled": True, "git": {
    "enabled": True, "projects": [{"name": "r", "path": "X:/fake"}],
    "auto_discover": False, "deep": False, "timeout_s": 5, "top_files": 5}}}


def test_parse_numstat_old_format_cd_falls_back():
    raw = "\u001eh1\u001f2026-09-14T10:00:00\u001fA\n10\t2\ta.py\n"
    c = git_insights._parse_numstat(raw)[0]
    assert c["cd"] == c["date"]
    assert c["author"] == "A"


def test_parse_numstat_new_format_has_cd():
    raw = ("\u001eh1\u001f2026-09-14T10:00:00\u001fA"
           "\u001f2026-09-16T09:00:00\n10\t2\ta.py\n")
    c = git_insights._parse_numstat(raw)[0]
    assert c["date"].startswith("2026-09-14")
    assert c["cd"].startswith("2026-09-16")


def test_build_day_table_matches_per_day(fake_git):
    days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    table = git_insights._build_day_table(days, CFG_FAKE, None)
    for d in days:
        exp = _expected_per_day(d)
        assert table[d]["found"] == exp["found"], d
        for k, v in exp["total"].items():
            assert table[d]["total"][k] == v, (d, k)


def test_bucketing_uses_committer_date(fake_git):
    days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    t = git_insights._build_day_table(days, CFG_FAKE, None)
    assert t["2026-09-14"]["total"]["commit_count"] == 1
    assert t["2026-09-15"]["total"]["commit_count"] == 2
    assert t["2026-09-16"]["total"]["commit_count"] == 1
    assert t["2026-09-17"]["found"] is False
    assert t["2026-09-14"]["total"]["lines_added"] == 10
    assert t["2026-09-16"]["total"]["churn"] == 2
    assert t["2026-09-15"]["total"]["modify_ratio"] == round(7 / 15, 2)


def test_range_batch_scope_and_reset():
    days = ["2026-09-15"]
    assert git_insights._RANGE_BATCH is None
    with git_insights.range_batch(days):
        assert git_insights._RANGE_BATCH["days"] == days
    assert git_insights._RANGE_BATCH is None
    with pytest.raises(RuntimeError):
        with git_insights.range_batch(days):
            raise RuntimeError("boom")
    assert git_insights._RANGE_BATCH is None
    with git_insights.range_batch([]):
        assert git_insights._RANGE_BATCH is None


def test_nested_same_range_reuses_outer():
    days = ["2026-09-15"]
    with git_insights.range_batch(days):
        outer = id(git_insights._RANGE_BATCH)
        with git_insights.range_batch(days):
            assert id(git_insights._RANGE_BATCH) == outer
    assert git_insights._RANGE_BATCH is None


def test_batch_hit_skips_git(monkeypatch):
    days = ["2026-09-15"]
    calls = []
    monkeypatch.setattr(git_insights, "_git_log_numstat",
                        lambda *a, **k: calls.append(a) or list(FAKE_COMMITS))
    monkeypatch.setattr(git_insights, "_is_repo", lambda p: True)
    with git_insights.range_batch(days):
        r1 = git_insights.git_insights(CFG_FAKE, "2026-09-15")
        n1 = len(calls)
        r2 = git_insights.git_insights(CFG_FAKE, "2026-09-15")
    assert r1 == r2
    assert len(calls) == n1 == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))