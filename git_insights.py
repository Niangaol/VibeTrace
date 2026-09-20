# -*- coding: utf-8 -*-
"""git_insights.py — Git 代码变更分析（ROADMAP Phase 2 · 质量与效率）。

离线、只读、零网络请求：对用户配置的本地 Git 仓库，用 `git log --numstat`
统计指定日期内的提交/增删行/改动文件，从而衡量“代码产出”与“改写/返工”
（修改率 = 删除行 / (新增 + 删除)）——这是 Phase 2「采纳率/留存率/修改率」
中无需 IDE 插件即可离线落地的部分（Git 集成 · 代码变更分析）。

设计原则：
- 纯只读 git 命令（log / rev-parse），绝不改动仓库状态；
- git 缺失、仓库未配置、当日无提交或缺 data 时优雅降级（found=False）；
- 所有命令都有 timeout，异常不影响日报/仪表盘主流程。

CLI：python git_insights.py --day 2026-08-18 [--config path] [--json]
"""

from __future__ import annotations

NOTICE_NO_COMMIT = "已配置 Git 仓库，但当天没有本地提交"  # 空态提示（批内/批外共用）

import argparse  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import contextlib  # noqa: E402
import sys  # noqa: E402

import classifier  # noqa: E402
import paths  # noqa: E402

DEFAULT_CONFIG = os.path.join(paths.default_data_root(), "config.json")

# git 默认配置（合并到 insights.git）
_DEFAULT_GIT = {
    "enabled": True,
    "projects": [],        # [path] 或 {name: path}
    "timeout_s": 10,       # 每个仓库的超时（秒）
    "top_files": 5,        # 每个仓库按变更量展示的文件数
    "auto_discover": True, # 对目录型 project 自动递归发现子仓库（深度≤3）
    "deep": False,         # 是否启用深度分析（author_detail/commit_rhythm/adoption_proxy 等）
}


def git_config(config: dict) -> dict:
    """从完整 config 提取 insights.git 段并补齐默认值。"""
    ins = (config or {}).get("insights")
    git = ins.get("git") if isinstance(ins, dict) and isinstance(ins.get("git"), dict) else {}
    enabled = bool(git.get("enabled", _DEFAULT_GIT["enabled"]))
    if isinstance(ins, dict):
        enabled = enabled and bool(ins.get("enabled", True))
    out = dict(_DEFAULT_GIT)
    out["enabled"] = enabled
    try:
        out["timeout_s"] = max(1.0, float(git.get("timeout_s", _DEFAULT_GIT["timeout_s"]) or 1.0))
    except (TypeError, ValueError):
        pass
    try:
        out["top_files"] = max(1, int(git.get("top_files", _DEFAULT_GIT["top_files"]) or 1))
    except (TypeError, ValueError):
        pass
    out["projects"] = _normalize_projects(git.get("projects"))
    out["auto_discover"] = bool(git.get("auto_discover", _DEFAULT_GIT["auto_discover"]))
    out["deep"] = bool(git.get("deep", _DEFAULT_GIT["deep"]))
    return out


def _normalize_projects(raw) -> list[dict]:
    """把 projects 归一化为 [{name, path}]；支持 list[str] 或 {name: path}。"""
    projects: list[dict] = []
    if isinstance(raw, dict):
        for name, path in raw.items():
            p = str(path or "").strip().strip('"')
            if p:
                projects.append({"name": str(name or "").strip() or os.path.basename(p.rstrip("\\/")), "path": p})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                p = item.strip().strip('"')
                projects.append({"name": os.path.basename(p.rstrip("\\/")), "path": p})
            elif isinstance(item, dict) and item.get("path"):
                p = str(item["path"]).strip().strip('"')
                if p:
                    projects.append({"name": str(item.get("name") or "").strip() or os.path.basename(p.rstrip("\\/")),
                                     "path": p})
    # 去重（按 path）
    seen: set[str] = set()
    out = []
    for proj in projects:
        key = os.path.normcase(os.path.abspath(proj["path"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(proj)
    return out


def _is_repo(path: str) -> bool:
    """path 是否为 git 仓库（含 .git 目录或 .git 文件 / 子模块）。"""
    if not os.path.isdir(path):
        return False
    gitmark = os.path.join(path, ".git")
    return os.path.isdir(gitmark) or os.path.isfile(gitmark)


def _run_git(args: list[str], cwd: str, timeout: float) -> str | None:
    """只读运行 git，成功返回 stdout（str），失败/缺失/超时返回 None。"""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_numstat(raw: str) -> list[dict]:
    """解析 `git log --pretty=... --numstat` 输出为提交列表。

    期望每段以 \x1e 开头：
      \x1e<hash>\x1f<date>\x1f<author>
      <add>\t<del>\t<file>
      ...
      （空行分隔）
    返回 [{hash, date, author, cd, files:[{path, added, deleted}]}]。
    cd = committer date：区间批量查询按它分桶（rebase/amend 后与 author date
    可能不同天）。旧调用方不读 cd，完全兼容。
    """
    commits: list[dict] = []
    if not raw:
        return commits
    blocks = [b for b in raw.split("\x1e") if b.strip()]
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        header = lines[0]
        parts = header.split("\x1f")
        if len(parts) < 3:
            continue
        commit: dict = {"hash": parts[0].strip(), "date": parts[1].strip(),
                        "cd": (parts[3].strip() if len(parts) > 3
                               else parts[1].strip()),
                        "author": parts[2].strip(), "files": []}
        for ln in lines[1:]:
            fields = ln.split("\t")
            if len(fields) < 3:
                continue
            add_s, del_s, fpath = fields[0], fields[1], "\t".join(fields[2:])
            # 二进制/重命名等 git 以 - 表示无需统计
            if add_s == "-" and del_s == "-":
                continue
            try:
                added = int(add_s)
                deleted = int(del_s)
            except (TypeError, ValueError):
                continue
            commit["files"].append({"path": fpath, "added": added, "deleted": deleted})
        commits.append(commit)
    return commits


def auto_discover_repos(search_roots: list[str], max_depth: int = 3) -> list[dict]:
    """在 search_roots 下自动发现 Git 仓库（递归深度 ≤ max_depth）。

    返回 [{name, path}]，按 path 去重。跳过 node_modules/.git/venv/__pycache__/dist/build。
    项目名优先取 git remote origin 最后一段，其次取目录名。
    """
    if not search_roots:
        return []
    seen: set[str] = set()
    repos: list[dict] = []
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next", ".cache"}
    for root in search_roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # 控制递归深度
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth >= max_depth:
                dirnames.clear()
                continue
            # 剪枝跳过目录
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]
            git_mark = os.path.join(dirpath, ".git")
            if os.path.isdir(git_mark) or os.path.isfile(git_mark):
                norm = os.path.normcase(os.path.abspath(dirpath))
                if norm in seen:
                    continue
                seen.add(norm)
                name = os.path.basename(dirpath)
                # 尝试用 remote origin 推断更准确的项目名
                remote = _run_git(["remote", "get-url", "origin"], dirpath, 5.0)
                if remote:
                    remote = remote.strip()
                    name = os.path.splitext(remote.split("/")[-1])[0] or name
                repos.append({"name": name, "path": dirpath})
    return repos


def analyze_repo_deep(repo: dict, day_str: str, timeout: float, top_files: int,
                      ai_project_files: list[str] | None = None) -> dict:
    """扩展分析：在 analyze_repo 基础上新增 author_detail / commit_rhythm /
    adoption_proxy / language_dist / deep_work_blocks。

    保留原有全部字段（向后兼容），新增字段可为 None（无数据时）。
    ai_project_files: ai_sessions 当天涉及的项目文件路径列表（用于 adoption_proxy）。
    """
    base = analyze_repo(repo, day_str, timeout, top_files)
    if base is None:
        return None

    path = repo["path"]
    since = f"{day_str} 00:00:00"
    until = f"{day_str} 23:59:59"
    args = ["log", f"--since={since}", f"--until={until}",
            "--date=iso", "--pretty=format:%x1e%H%x1f%ad%x1f%an", "--numstat"]
    out = _run_git(args, path, timeout)
    if out is None:
        return base
    commits = _parse_numstat(out)
    if not commits:
        return base

    # —— author_detail ——
    author_map: dict[str, dict] = {}
    for c in commits:
        author = c.get("author") or "unknown"
        entry = author_map.setdefault(author, {
            "name": author, "commits": 0, "additions": 0, "deletions": 0,
            "files_touched": 0, "first_ts": None, "last_ts": None})
        entry["commits"] += 1
        for f in c.get("files", []):
            entry["additions"] += f.get("added", 0)
            entry["deletions"] += f.get("deleted", 0)
            entry["files_touched"] += 1
        ts_str = c.get("date")
        ts = None
        if ts_str:
            try:
                ts = datetime.datetime.fromisoformat(ts_str).timestamp()
            except (OSError, ValueError, TypeError):
                pass
        if ts is not None:
            if entry["first_ts"] is None or ts < entry["first_ts"]:
                entry["first_ts"] = ts
            if entry["last_ts"] is None or ts > entry["last_ts"]:
                entry["last_ts"] = ts
    authors_detail = list(author_map.values())

    # —— commit_rhythm ——
    hour_counts: dict[int, int] = {}
    ts_list: list[float] = []
    for c in commits:
        ts_str = c.get("date")
        ts = None
        if ts_str:
            try:
                ts = datetime.datetime.fromisoformat(ts_str).timestamp()
            except (OSError, ValueError, TypeError):
                pass
        if ts is not None:
            ts_list.append(ts)
            try:
                hour = datetime.datetime.fromtimestamp(ts).hour
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
            except (OSError, ValueError, TypeError):
                pass
    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
    avg_interval = None
    ts_sorted: list[float] = []
    if len(ts_list) >= 2:
        ts_sorted = sorted(ts_list)
        intervals = [ts_sorted[i+1] - ts_sorted[i] for i in range(len(ts_sorted)-1)]
        avg_interval = round(sum(intervals) / len(intervals) / 60.0, 1)  # 分钟

    # —— language_dist ——
    ext_map: dict[str, int] = {}
    for c in commits:
        for f in c.get("files", []):
            _, ext = os.path.splitext(f.get("path", ""))
            ext = ext.lower() or "(none)"
            ext_map[ext] = ext_map.get(ext, 0) + 1
    language_dist = dict(sorted(ext_map.items(), key=lambda x: -x[1])[:20])

    # —— deep_work_blocks ——
    deep_work_blocks: list[dict] = []
    if len(ts_sorted) >= 3:
        block_start = ts_sorted[0]
        block_end = ts_sorted[0]
        for i in range(1, len(ts_sorted)):
            gap = ts_sorted[i] - block_end
            if gap <= 30 * 60:  # ≤30 分钟视为连续
                block_end = ts_sorted[i]
            else:
                dur = (block_end - block_start) / 60.0
                if dur >= 15:
                    deep_work_blocks.append({
                        "start": datetime.datetime.fromtimestamp(block_start).isoformat(),
                        "end": datetime.datetime.fromtimestamp(block_end).isoformat(),
                        "duration_min": round(dur, 1),
                        "commits": sum(1 for t in ts_sorted if block_start <= t <= block_end),
                    })
                block_start = block_end = ts_sorted[i]
        # 收尾最后一个块
        dur = (block_end - block_start) / 60.0
        if dur >= 15:
            deep_work_blocks.append({
                "start": datetime.datetime.fromtimestamp(block_start).isoformat(),
                "end": datetime.datetime.fromtimestamp(block_end).isoformat(),
                "duration_min": round(dur, 1),
                "commits": sum(1 for t in ts_sorted if block_start <= t <= block_end),
            })

    # —— adoption_proxy ——
    adoption_proxy = None
    if ai_project_files:
        changed_files = set()
        for c in commits:
            for f in c.get("files", []):
                changed_files.add(f.get("path", ""))
        if changed_files:
            overlap = sum(1 for pf in ai_project_files if any(pf.endswith(f) or f.endswith(pf) for f in changed_files))
            adoption_proxy = round(overlap / max(1, len(changed_files)), 3)

    base.update({
        "authors_detail": authors_detail,
        "commit_rhythm": {
            "commits_per_hour": [{"hour": h, "count": v} for h, v in sorted(hour_counts.items())],
            "peak_hour": peak_hour,
            "avg_interval_min": avg_interval,
        },
        "adoption_proxy": adoption_proxy,
        "language_dist": language_dist,
        "deep_work_blocks": deep_work_blocks,
    })
    return base


def _stats_from_commits(repo: dict, commits: list[dict], top_files: int) -> dict:
    """把提交列表聚合成单日统计（analyze_repo / 区间批量共用）。

    commits 必须是**已按天过滤过**的提交；分桶由调用方负责。
    """
    added = sum(f["added"] for c in commits for f in c["files"])
    deleted = sum(f["deleted"] for c in commits for f in c["files"])
    churn = added + deleted
    file_map: dict[str, dict] = {}
    for c in commits:
        for f in c["files"]:
            entry = file_map.setdefault(
                f["path"], {"path": f["path"], "added": 0, "deleted": 0, "churn": 0})
            entry["added"] += f["added"]
            entry["deleted"] += f["deleted"]
            entry["churn"] += f["added"] + f["deleted"]
    top = sorted(file_map.values(), key=lambda e: -e["churn"])[:top_files]
    authors = sorted({c["author"] for c in commits if c.get("author")})
    modify_ratio = (deleted / churn) if churn > 0 else 0.0
    return {
        "name": repo["name"],
        "path": repo["path"],
        "commit_count": len(commits),
        "lines_added": added,
        "lines_deleted": deleted,
        "churn": churn,
        "files": len(file_map),
        "top_files": top,
        "authors": authors,
        "modify_ratio": round(modify_ratio, 2),
    }


def _git_log_numstat(repo_path: str, since: str, until: str, timeout: float) -> list[dict] | None:
    """跑一次 git log --numstat（区间或单日）；失败返回 None。

    v2.9.11：--pretty 里补 %cd（committer date），供区间查询按天分桶。
    """
    args = ["log", f"--since={since}", f"--until={until}",
            "--date=iso",
            "--pretty=format:%x1e%H%x1f%ad%x1f%an%x1f%cd", "--numstat"]
    out = _run_git(args, repo_path, timeout)
    if out is None:
        return None
    return _parse_numstat(out)


def analyze_repo(repo: dict, day_str: str, timeout: float, top_files: int) -> dict | None:
    """统计单个仓库在 day_str 当天的提交与变更（原有行为不变）。"""
    path = repo["path"]
    if not _is_repo(path):
        return None
    commits = _git_log_numstat(path, f"{day_str} 00:00:00", f"{day_str} 23:59:59", timeout)
    if commits is None:
        return None
    return _stats_from_commits(repo, commits, top_files)




# ---------------------------------------------------------------------------
# 区间批量（v2.9.11 性能修复）
# ---------------------------------------------------------------------------
# growth / report 等多日场景原先对每一天各跑一次 git log 子进程：每个仓库
# 每天一次，auto_discover 开启时每天还递归 walk 目录。
# range_batch(days) 把区间压成一次 git log --since/--until，再按 committer
# date（%cd）分桶回填；块内 git_insights() 直接查表，语义与逐日一致
# （见 tests/unit/test_git_range_batch.py 的等价性断言）。
_RANGE_BATCH = None


def _batch_key(config: dict, days: list[str]) -> tuple:
    """批键：配置指纹 + 日期区间。配置变更或区间不同都另起一批。"""
    import json  # noqa: PLC0415

    try:
        cfg_sig = json.dumps(config or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        cfg_sig = repr(config)
    return (cfg_sig, min(days) if days else "", max(days) if days else "")


@contextlib.contextmanager
def range_batch(days):
    """多日区间批量上下文：块内 git_insights() 共用一次 git log。

    用法（growth._aggregate_week）：
        with git_insights.range_batch(days):
            for day in days:
                git_insights.git_insights(config, day)   # 块内命中批表

    与 ai_sessions.collect_fingerprint_batch 同风格：块外/块内返回结果
    完全一致，只是块内不再重复跑子进程。同区间嵌套时复用外层结果。
    """
    global _RANGE_BATCH
    days = list(days or [])
    if not days:
        yield
        return
    outer = _RANGE_BATCH
    if outer is not None and outer.get("days") == days:
        yield
        return
    _RANGE_BATCH = {"days": days}  # 表格按 _batch_key(config, days) 延迟建并缓存
    try:
        yield
    finally:
        _RANGE_BATCH = outer


def _batched_day_result(config: dict, day_str: str,
                        ai_project_files: list[str] | None) -> dict | None:
    """区间批内取某一天的结果；不在批内则返回 None。

    批表按配置指纹 + 日期区间缓存（_batch_key）：同批内换了
    config（不同仓库/开关 deep）会重建表，不会拿到上一份的缓存。
    """
    batch = _RANGE_BATCH
    if batch is None:
        return None
    key = _batch_key(config, batch["days"])
    entry = batch.get(key)
    if entry is None:
        entry = _build_day_table(batch["days"], config, ai_project_files)
        batch[key] = entry
    return entry.get(day_str)


def _build_day_table(days: list[str], config: dict,
                     ai_project_files: list[str] | None) -> dict:
    """一次跑完区间 git log，按 committer date 分桶成 {day: git_insights 结果}。

    与逐日 git_insights() 严格等价：deep 模式同样走 analyze_repo_deep 并补齐
    deep_work_summary；降级 notice 文案与逐日路径一致；汇总逻辑复用
    _assemble_result，避免两处公式漂移。
    """
    gc = git_config(config)
    # 降级 notice 与逐日 git_insights() 逐字对齐
    if not gc["enabled"]:
        off = _empty_result(gc, "Git 代码分析已关闭")
        return {d: off for d in days}
    if not gc["projects"]:
        nop = _empty_result(gc, "未配置 Git 仓库（insights.git.projects）或已关闭")
        return {d: nop for d in days}

    projects = _resolve_projects(gc)
    d0, d1 = min(days), max(days)
    deep = bool(gc.get("deep"))
    # 先按仓库取区间提交，再按 cd 分桶
    per_day: dict = {d: [] for d in days}
    for proj in projects:
        path = proj.get("path", "") if isinstance(proj, dict) else str(proj)
        if not path or not _is_repo(path):
            continue
        commits = _git_log_numstat(path, f"{d0} 00:00:00", f"{d1} 23:59:59", gc["timeout_s"])
        if not commits:
            continue
        by_day: dict = {}
        for c in commits:
            cd = (c.get("cd") or c.get("date") or "")[:10]
            if cd in per_day:
                by_day.setdefault(cd, []).append(c)
        for day, day_commits in by_day.items():
            per_day[day].append((proj, day_commits))

    table: dict = {}
    for day in days:
        repos: list = []
        for proj, day_commits in per_day[day]:
            if deep:
                # deep 指标按单日计算（author_detail/commit_rhythm/adoption_proxy）
                # 无法从区间结果切分，故逐仓库重跑（仍比逐日少一次目录 walk）
                stats = analyze_repo_deep(proj, day, gc["timeout_s"], gc["top_files"],
                                          ai_project_files=ai_project_files)
            else:
                stats = _stats_from_commits(proj, day_commits, gc["top_files"])
            if stats is not None and stats.get("commit_count", 0) > 0:
                repos.append(stats)
        table[day] = _assemble_result(gc, repos, NOTICE_NO_COMMIT)
    return table


def _empty_result(gc: dict, notice: str) -> dict:
    """套约空态（200 可展示）；批内/批外共用同一结构。"""
    return {"enabled": bool(gc.get("enabled")), "found": False, "repos": [],
            "total": {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
                      "churn": 0, "files": 0, "modify_ratio": 0.0},
            "notice": notice}


def _resolve_projects(gc: dict) -> list[dict]:
    """配置的仓库 + auto_discover 发现结果，按 path 去重。"""
    projects = list(gc.get("projects") or [])
    if gc.get("auto_discover"):
        for proj in list(gc.get("projects") or []):
            p = proj.get("path", "") if isinstance(proj, dict) else str(proj)
            p = os.path.expanduser(os.path.expandvars(p))
            if os.path.isdir(p) and not _is_repo(p):
                projects.extend(auto_discover_repos([p], max_depth=3))
    seen = set()
    unique = []
    for proj in projects:
        p = proj.get("path", "") if isinstance(proj, dict) else str(proj)
        p = os.path.normcase(os.path.abspath(p))
        if p and p not in seen:
            seen.add(p)
            unique.append(proj)
    return unique


def _assemble_result(gc: dict, repos: list[dict], notice: str) -> dict:
    """把单仓统计列表汇总成 git_insights 返回契约。"""
    if not repos:
        return _empty_result(gc, notice)
    total = {
        "commit_count": sum(r["commit_count"] for r in repos),
        "lines_added": sum(r["lines_added"] for r in repos),
        "lines_deleted": sum(r["lines_deleted"] for r in repos),
        "churn": sum(r["churn"] for r in repos),
        "files": sum(r["files"] for r in repos),
    }
    total["modify_ratio"] = (round(total["lines_deleted"] / total["churn"], 2)
                              if total["churn"] > 0 else 0.0)
    out = {"enabled": True, "found": True, "repos": repos, "total": total, "notice": ""}
    if gc.get("deep") and repos:
        blocks = [b for r in repos for b in r.get("deep_work_blocks", [])]
        out["deep_work_summary"] = {
            "total_blocks": len(blocks),
            "total_deep_work_min": round(sum(b["duration_min"] for b in blocks), 1),
        }
    return out


def git_insights(config: dict, day_str: str, ai_project_files: list[str] | None = None) -> dict:
    """汇总指定日期的 Git 产出（ROADMAP Phase 2）。

    支持 auto_discover / deep / ai_project_files；range_batch(days) 块内
    直接取区间批量结果，不再每日跑 git 子进程（v2.9.11，块外行为不变）。
    """
    gc = git_config(config)

    # 批内优先：range_batch 已一次性算好区间，直接查表
    if _RANGE_BATCH is not None and day_str in _RANGE_BATCH["days"]:
        cached = _batched_day_result(config, day_str, ai_project_files)
        if cached is not None:
            return cached

    if not gc["enabled"]:
        return _empty_result(gc, "代码分析已关闭")
    if not gc["projects"]:
        return _empty_result(gc, "未配置 Git 仓库（insights.git.projects）或已关闭")

    projects = _resolve_projects(gc)
    analyze = analyze_repo_deep if gc.get("deep") else analyze_repo
    repos = []
    for proj in projects:
        if gc.get("deep"):
            stats = analyze(proj, day_str, gc["timeout_s"], gc["top_files"],
                            ai_project_files=ai_project_files)
        else:
            stats = analyze(proj, day_str, gc["timeout_s"], gc["top_files"])
        if stats is not None and stats.get("commit_count", 0) > 0:
            repos.append(stats)
    return _assemble_result(gc, repos, "已配置 Git 仓库，但当天没有本地提交")


def main() -> int:
    ap = argparse.ArgumentParser(description="Git 代码变更分析（只读 · 本地）")
    ap.add_argument("--day", required=True, help="日期 YYYY-MM-DD")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config.json 路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()
    config = classifier.load_config(args.config)
    result = git_insights(config, args.day)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"enabled={result['enabled']} found={result['found']}")
        for r in result["repos"]:
            print(f"  {r['name']}: {r['commit_count']} commits, "
                  f"+{r['lines_added']}/-{r['lines_deleted']} ({r['churn']} churn, "
                  f"{r['files']} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
