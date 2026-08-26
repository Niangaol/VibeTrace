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

import argparse
import json
import os
import subprocess
import sys

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
    返回 [{hash, date, author, files:[{path, added, deleted}]}]。
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
        ts = c.get("ts")
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
        ts = c.get("ts")
        if ts is not None:
            ts_list.append(ts)
            try:
                hour = datetime.datetime.fromtimestamp(ts).hour
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
            except (OSError, ValueError, TypeError):
                pass
    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
    avg_interval = None
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


def analyze_repo(repo: dict, day_str: str, timeout: float, top_files: int) -> dict:
    """统计单个仓库在 day_str 当天（本地时区 00:00:00–23:59:59）的提交与变更。

    返回含 commit_count / lines_added / lines_deleted / churn / files /
    top_files / authors / modify_ratio 的 dict；非仓库或失败时返回 None。
    """
    path = repo["path"]
    if not _is_repo(path):
        return None
    since = f"{day_str} 00:00:00"
    until = f"{day_str} 23:59:59"
    args = ["log", f"--since={since}", f"--until={until}",
            "--date=iso", "--pretty=format:%x1e%H%x1f%ad%x1f%an", "--numstat"]
    out = _run_git(args, path, timeout)
    if out is None:
        return None
    commits = _parse_numstat(out)
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
        "path": path,
        "commit_count": len(commits),
        "lines_added": added,
        "lines_deleted": deleted,
        "churn": churn,
        "files": len(file_map),
        "top_files": top,
        "authors": authors,
        "modify_ratio": round(modify_ratio, 2),
    }


def git_insights(config: dict, day_str: str, ai_project_files: list[str] | None = None) -> dict:
    """汇总指定日期的 Git 产出（ROADMAP Phase 2 · 代码变更分析）。

    支持：
    - auto_discover：对目录型 project 自动递归发现子仓库（深度≤3）
    - deep：启用深度分析（author_detail/commit_rhythm/adoption_proxy 等）
    - ai_project_files：ai_sessions 当天涉及的项目文件路径（用于 adoption_proxy 计算）
    """
    gc = git_config(config)
    empty = {"enabled": gc["enabled"], "found": False, "repos": [],
             "total": {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
                       "churn": 0, "files": 0, "modify_ratio": 0.0},
             "notice": "未配置 Git 仓库（insights.git.projects）或已关闭"}
    if not gc["enabled"]:
        empty["notice"] = "Git 代码分析已关闭（insights.enabled=false 或 insights.git.enabled=false）"
        return empty
    if not gc["projects"]:
        return empty

    # —— 自动发现仓库 ——
    projects = list(gc["projects"])
    if gc.get("auto_discover"):
        for proj in gc["projects"]:
            p = proj.get("path", "") if isinstance(proj, dict) else str(proj)
            p = os.path.expanduser(os.path.expandvars(p))
            if os.path.isdir(p) and not _is_repo(p):
                discovered = auto_discover_repos([p], max_depth=3)
                projects.extend(discovered)
    # 去重（按 path）
    seen_paths: set[str] = set()
    unique_projects: list[dict] = []
    for proj in projects:
        p = proj.get("path", "") if isinstance(proj, dict) else str(proj)
        p = os.path.normcase(os.path.abspath(p))
        if p and p not in seen_paths:
            seen_paths.add(p)
            unique_projects.append(proj)

    analyze = analyze_repo_deep if gc.get("deep") else analyze_repo
    repos: list[dict] = []
    for proj in unique_projects:
        if gc.get("deep"):
            stats = analyze(proj, day_str, gc["timeout_s"], gc["top_files"], ai_project_files=ai_project_files)
        else:
            stats = analyze(proj, day_str, gc["timeout_s"], gc["top_files"])
        if stats is not None and stats.get("commit_count", 0) > 0:
            repos.append(stats)

    if not repos:
        return {"enabled": True, "found": False, "repos": [],
                "total": {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
                          "churn": 0, "files": 0, "modify_ratio": 0.0},
                "notice": "已配置 Git 仓库，但当天没有本地提交"}
    total = {
        "commit_count": sum(r["commit_count"] for r in repos),
        "lines_added": sum(r["lines_added"] for r in repos),
        "lines_deleted": sum(r["lines_deleted"] for r in repos),
        "churn": sum(r["churn"] for r in repos),
        "files": sum(r["files"] for r in repos),
    }
    total["modify_ratio"] = round(
        total["lines_deleted"] / total["churn"], 2) if total["churn"] > 0 else 0.0
    result = {"enabled": True, "found": True, "repos": repos, "total": total, "notice": ""}
    # deep 模式下补充汇总指标
    if gc.get("deep") and repos:
        all_blocks = [b for r in repos for b in r.get("deep_work_blocks", [])]
        result["deep_work_summary"] = {
            "total_blocks": len(all_blocks),
            "total_deep_work_min": round(sum(b["duration_min"] for b in all_blocks), 1),
        }
    return result


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
