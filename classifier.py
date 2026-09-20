# -*- coding: utf-8 -*-
"""classifier.py — 纯逻辑分类引擎（无任何 OS 依赖，可在任意平台导入测试）。

职责：
- 应用类别分类（exe 关键词 -> 标题关键词，按配置顺序匹配）
- 浏览器站点分类（视频 / 代码 / 学习 / 其他，优先级 学习 > 代码 > 视频）
- 社交软件联系人提取（微信/QQ/钉钉 等窗口标题 -> 联系人/群名）
- vibe coding AI 工具识别（进程树 BFS + 终端标题，带防误伤保护）
- 标题隐私黑名单、联系人别名

所有匹配大小写不敏感（统一小写）。exe 一律带扩展名小写（如 "wechat.exe"）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import applog  # noqa: E402
import paths  # noqa: E402

CATEGORY_ORDER = [
    "AI编程",
    "浏览器",
    "影音娱乐",
    "游戏",
    "安全防护",
    "社交聊天",
    "开发工具",
    "办公学习",
    "设计创作",
    "网盘下载",
    "网络工具",
    "系统",
    "其他",
]

# 安全匹配顺序：AI编程 优先于 开发工具（避免 opencode 命中 "code"）；
# 浏览器/影音娱乐 优先于 社交聊天（避免 qqbrowser/qqmusic 命中 "qq"）。
_CATEGORY_ORDER_FOR_MATCH = CATEGORY_ORDER

DEFAULT_CONFIG_PATH = os.path.join(paths.script_dir(), "config.default.json")


def _load_builtin_defaults() -> dict:
    """从 config.default.json 加载内置默认规则（唯一事实源，与仓库同步）。

    文件缺失时使用精简兜底（只保证模块可导入；正常运行请保留该文件）。
    """
    try:
        with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception as _exc:  # noqa: BLE001
        applog.note(_exc, "classifier: 内置默认规则加载失败，使用精简兜底规则")
    return {
        "data_root": "",
        "apps": {},
        "categories": [{"name": "其他", "exe": [], "title": []}],
        "social_apps": {}, "social_main_titles": [],
        "browser_exes": [], "terminal_exes": [],
        "ai_keywords": [], "ai_tool_names": {}, "ai_title_keywords": [],
        "browser_categories": {}, "browser_category_priority": [],
        "terminal_tools": [], "subcategories": [],
        "title_blacklist": [], "browser_history_enabled": True,
        "browser_history": {},
    }


DEFAULT_CONFIG = _load_builtin_defaults()


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 优先；categories 按 DEFAULT 顺序重排（安全顺序）。"""
    out = dict(base)
    for key, value in override.items():
        if key == "categories":
            # 以配置文件的类别定义替换默认定义，但按默认顺序重排，保证匹配优先级
            by_name = {c["name"]: c for c in value if isinstance(c, dict) and "name" in c}
            reordered = []
            for name in CATEGORY_ORDER:
                if name in by_name:
                    reordered.append(by_name.pop(name))
            reordered.extend(by_name.values())
            out["categories"] = reordered
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# 配置热重载缓存（config.json / config.default.json 修改后免重启生效）
# 缓存模式沿用上方 _groups_cache 的写法：mtime 变化或超过 TTL 才重新读盘。
# 注意：这里可能出现多个不同 path 的调用（如 dashboard 用默认路径、CLI 传入
# --config），故缓存按「规范化绝对路径」分桶；返回时浅拷贝一层，避免调用方
# 修改（如 dashboard 里 config["data_root"] = root）污染缓存。
# ---------------------------------------------------------------------------
_config_cache: dict[str, dict] = {}  # abs_path -> {"mtime": ..., "ts": ..., "data": ...}
_CONFIG_TTL = 3.0  # 秒：mtime 未变化时也强制刷新的最长时间


def _config_cache_key(path: str) -> str:
    """规范化配置路径，作为缓存键（相对/绝对均归一化）。"""
    return os.path.normcase(os.path.abspath(path))


def _config_mtime(path: str) -> float:
    """取配置路径与内置默认配置中「较新」的 mtime，作为重载依据。
    任一缺失则用 0.0 表示该来源无影响（缺 config 仍能兜底默认规则）。"""
    m = 0.0
    for p in (path, DEFAULT_CONFIG_PATH):
        try:
            m = max(m, os.path.getmtime(p))
        except OSError:
            pass
    return m


def invalidate_config_cache(path: str | None = None) -> None:
    """强制丢弃配置缓存；path 为空时清空全部缓存。供保存场景/测试调用。"""
    global _config_cache
    if path is None:
        _config_cache.clear()
    else:
        _config_cache.pop(_config_cache_key(path), None)


def load_config(path: str | None = None) -> dict:
    """读取 config.json 并深合并到默认配置；文件缺失/损坏时回退默认并告警。

    热重载：config.json 或 config.default.json 的 mtime 变化，或距上次读取超过
    _CONFIG_TTL 秒时重新读盘合并；否则返回缓存。返回值为缓存数据的浅拷贝，
    避免调用方修改（如 data_root 赋值）污染后续请求。

    可移植性：data_root 为空或相对路径时解析为程序目录（paths.script_dir，
    打包 exe 时即 exe 所在目录，避免写进 _MEIPASS 临时目录）。
    """
    if path is None:
        path = os.path.join(paths.default_data_root(), "config.json")
    key = _config_cache_key(path)
    now = time.monotonic()
    entry = _config_cache.get(key)
    # 命中缓存且未超 TTL 时，先检查 mtime 是否有变化再决定是否复用
    if entry is not None and now - entry["ts"] < _CONFIG_TTL:
        if _config_mtime(path) == entry["mtime"]:
            return dict(entry["data"])
        # mtime 变化：删掉旧缓存，走下方重新加载
        _config_cache.pop(key, None)

    # 重新读盘时应重载内置默认规则：config.default.json 的 mtime 已纳入重载判断，
    # 这样默认规则的更新也会随热重载生效，而非停留在模块导入时的旧快照。
    defaults = _load_builtin_defaults()
    if not os.path.isfile(path):
        print(f"[classifier] 配置不存在，使用默认配置: {path}", file=sys.stderr)
        data = dict(defaults)
        _config_cache[key] = {"mtime": _config_mtime(path), "ts": now, "data": data}
        return dict(data)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        if not isinstance(user_cfg, dict):
            raise ValueError("config 顶层必须是 JSON 对象")
        cfg = _deep_merge(defaults, user_cfg)
    except Exception as exc:  # noqa: BLE001 —— 配置损坏不应阻断监控
        print(f"[classifier] 配置解析失败，使用默认配置: {exc}", file=sys.stderr)
        cfg = dict(defaults)
    root = cfg.get("data_root") or ""
    if not root or not os.path.isabs(root):
        cfg["data_root"] = paths.script_dir()
    # 缓存内部 dict；对外返回浅拷贝，避免 data_root 等赋值污染缓存
    _config_cache[key] = {"mtime": _config_mtime(path), "ts": now, "data": cfg}
    return dict(cfg)


def load_aliases(path: str | None = None) -> dict:
    """读取联系人别名表 aliases.json；不存在/损坏时返回空表。"""
    if path is None:
        path = os.path.join(paths.default_data_root(), "aliases.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# 基础匹配
# ---------------------------------------------------------------------------
def resolve_app_name(exe: str, config: dict) -> str:
    """exe -> 显示名；用户 app_groups.json 的 app_names 优先，其次配置 apps，最后去扩展名。"""
    exe_l = (exe or "").lower()
    groups = load_app_groups(config.get("data_root"))
    custom = (groups.get("app_names") or {}).get(exe_l)
    if custom:
        return custom
    mapped = (config.get("apps") or {}).get(exe_l)
    if mapped:
        return mapped
    stem = exe_l[:-4] if exe_l.endswith(".exe") else exe_l
    return stem.title() if stem else exe_l


# ---------------------------------------------------------------------------
# 应用分组覆盖层（用户自定义：把应用放进/移出分组，新增/删除分组）
# 数据文件：<data_root>/app_groups.json ——
#   {
#     "exe_groups": {"steam.exe": "游戏"},
#     "custom_categories": ["我的分组"],
#     "app_names": {"steam.exe": "Steam 自定义名"},
#     "group_meta": {"我的分组": {"description": "..."}}
#   }
# ---------------------------------------------------------------------------
_groups_cache: dict = {"ts": 0.0, "data": None}
_GROUPS_TTL = 5.0  # 秒


def app_groups_path(data_root: str) -> str:
    return os.path.join(data_root, "app_groups.json")


def load_app_groups(data_root: str | None = None) -> dict:
    """读取用户分组覆盖层（5 秒 TTL 缓存）。缺失/损坏返回空结构。"""
    global _groups_cache
    now = time.monotonic()
    if _groups_cache["data"] is not None and now - _groups_cache["ts"] < _GROUPS_TTL:
        return _groups_cache["data"]
    groups: dict = {
        "exe_groups": {},
        "custom_categories": [],
        "app_names": {},
        "group_meta": {},
    }
    root = data_root
    if root is None:
        root = paths.default_data_root()
    path = app_groups_path(root)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                eg = data.get("exe_groups")
                if isinstance(eg, dict):
                    groups["exe_groups"] = {
                        str(k).lower(): str(v) for k, v in eg.items() if str(v)
                    }
                cc = data.get("custom_categories")
                if isinstance(cc, list):
                    groups["custom_categories"] = [str(x) for x in cc if str(x)]
                an = data.get("app_names")
                if isinstance(an, dict):
                    groups["app_names"] = {
                        str(k).lower(): str(v) for k, v in an.items() if str(v)
                    }
                gm = data.get("group_meta")
                if isinstance(gm, dict):
                    groups["group_meta"] = {
                        str(k): dict(v) for k, v in gm.items() if isinstance(v, dict)
                    }
        except Exception as _exc:  # noqa: BLE001
            applog.note(_exc, "classifier: 应用分组落盘失败，沿用内存分组")
    _groups_cache["ts"] = now
    _groups_cache["data"] = groups
    return groups


def save_app_groups(groups: dict, data_root: str) -> None:
    """原子写覆盖层（临时文件 + os.replace），并刷新缓存。"""
    global _groups_cache
    clean: dict = {
        "exe_groups": {str(k).lower(): str(v) for k, v in groups.get("exe_groups", {}).items() if str(v)},
        "custom_categories": [str(x) for x in groups.get("custom_categories", []) if str(x)],
        "app_names": {str(k).lower(): str(v) for k, v in groups.get("app_names", {}).items() if str(v)},
        "group_meta": {
            str(k): {str(kk): str(vv) for kk, vv in v.items()} if isinstance(v, dict) else {}
            for k, v in groups.get("group_meta", {}).items()
        },
    }
    path = app_groups_path(data_root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    _groups_cache["data"] = clean
    _groups_cache["ts"] = time.monotonic()


def all_categories(config: dict, groups: dict | None = None) -> list[str]:
    """内置分组 + 用户自定义分组（完整列表，供管理界面使用）。"""
    cats = [c.get("name", "其他") for c in config.get("categories", [])]
    if groups is None:
        groups = load_app_groups(config.get("data_root"))
    for name in groups.get("custom_categories", []):
        if name not in cats:
            cats.append(name)
    return cats


def sanitize_groups(config: dict, groups: dict) -> dict:
    """剔除指向未知类别（孤儿分组）的 exe_groups，返回清理后的副本。

    历史上的 app_groups.json 可能残留指向不存在的分组名（如 'AI工具'）
    的映射；这类映射既会伪装成类别、又无法在分组管理里选择/移动。
    这里把目标不在「内置 ∪ 自定义类别」之内的 exe_groups 移除，保持分组数据自洽。
    """
    known = set(all_categories(config, groups))
    out = dict(groups)
    eg = {k: v for k, v in (groups.get("exe_groups") or {}).items() if v in known}
    out["exe_groups"] = eg
    return out


def classify_category(exe: str, title: str, config: dict) -> str:
    """按 用户分组覆盖(exe 精确) -> 配置关键词(exe/title) 匹配，返回类别名。

    用户可通过仪表盘「分组」视图把应用放进/移出任意分组（含自定义分组），
    覆盖层优先级最高且实时生效（无需重启）。为防止“孤儿分组”（指向
    不存在的类别，例如历史遗留的 'AI工具'）伪装成类别渗透进报告，这里
    只信任指向「已登记类别」（内置 ∪ 自定义）的覆盖层；其余一律忽略并回退
    到自动分类。"""
    exe_l = (exe or "").lower()
    title_l = (title or "").lower()
    groups = load_app_groups(config.get("data_root"))
    known = all_categories(config, groups)
    # 1) 用户覆盖层：仅当目标是已登记类别时才生效（否则忽略孤儿分组）
    override = groups.get("exe_groups", {}).get(exe_l)
    if override and override in known:
        return override
    # 2) 配置规则
    for cat in config.get("categories", []):
        name = cat.get("name", "其他")
        for kw in cat.get("exe", []):
            if kw and kw.lower() in exe_l:
                return name
        for kw in cat.get("title", []):
            if kw and kw.lower() in title_l:
                return name
    return "其他"


def classify_browser(title: str, config: dict) -> str:
    """浏览器站点分类：按 browser_category_priority 顺序（学习 > 代码 > 视频）。"""
    title_l = (title or "").lower()
    cats = config.get("browser_categories", {})
    for label in config.get("browser_category_priority", ["学习", "代码", "视频"]):
        for kw in cats.get(label, []):
            if kw and kw.lower() in title_l:
                return label
    return "其他"


# ---------------------------------------------------------------------------
# 社交联系人
# ---------------------------------------------------------------------------
def is_social_main_title(exe: str, title: str, config: dict) -> bool:
    """标题是否为社交软件主界面（"微信" 等），不产生联系人记录。"""
    title_l = (title or "").strip().lower()
    return any(t.lower() == title_l for t in config.get("social_main_titles", []))


def extract_contact(exe: str, title: str, config: dict) -> str | None:
    """从社交软件窗口标题解析联系人/群名；主界面或无法解析返回 None。

    钉钉标题形如 "与 李四 的会话"，微信/QQ 标题即联系人/群名。
    """
    exe_l = (exe or "").lower()
    if exe_l not in config.get("social_apps", {}):
        return None
    raw = (title or "").strip()
    if not raw or is_social_main_title(exe, raw, config):
        return None
    contact = raw
    # 钉钉式标题清洗
    for prefix in ("与 ", "和 "):
        if contact.startswith(prefix):
            contact = contact[len(prefix):]
            break
    for suffix in (" 的会话", " 的聊天", "的会话", "的聊天"):
        if contact.endswith(suffix):
            contact = contact[: -len(suffix)]
            break
    contact = contact.strip()
    return contact if contact else None


def resolve_alias(contact: str, aliases: dict) -> str:
    """联系人别名映射；无别名返回原名。"""
    if not contact:
        return contact
    return aliases.get(contact, contact)


# ---------------------------------------------------------------------------
# AI 工具识别
# ---------------------------------------------------------------------------
def match_ai_keyword(text: str, config: dict) -> str | None:
    """判断文本是否命中 AI 工具关键词，返回规范化工具名；未命中返回 None。

    防误伤：关键词含 "pi" 且文本含 python/pip/pypi 时跳过；
    长度 < 4 的关键词只做整词匹配（避免 "pi" 匹配到任意含 pi 的进程）。
    """
    text_l = (text or "").lower()
    if not text_l:
        return None
    names = config.get("ai_tool_names", {})
    for kw in config.get("ai_keywords", []):
        kw_l = kw.lower()
        matched = kw_l == text_l or (len(kw_l) >= 4 and kw_l in text_l)
        if not matched:
            continue
        if kw_l.startswith("pi") and any(
            bad in text_l for bad in ("python", "pip", "pypi")
        ):
            continue
        return names.get(kw, kw)
    return None


def detect_ai_tool(foreground_pid: int, processes: dict, title: str, config: dict) -> str | None:
    """识别前台进程归属的 AI 工具。

    1) 前台进程自身 exe 匹配；
    2) 进程树 BFS 子孙匹配（终端里跑 CLI 工具，优先最深层）；
    3) 终端窗口标题关键词兜底。
    processes: {pid: ProcessInfo-like(pid,ppid,exe)}；可为 {}（跳过树匹配）。
    """
    def _stem(exe: str) -> str:
        exe_l = (exe or "").lower()
        return exe_l[:-4] if exe_l.endswith(".exe") else exe_l

    # 1) 自身
    own = processes.get(foreground_pid) if isinstance(processes, dict) else None
    if own is not None:
        hit = match_ai_keyword(_stem(getattr(own, "exe", "")), config)
        if hit:
            return hit

    # 2) 子孙 BFS（取最深层命中）
    children: dict[int, list[int]] = {}
    for info in (processes.values() if isinstance(processes, dict) else []):
        ppid = getattr(info, "ppid", None)
        pid = getattr(info, "pid", None)
        if ppid is not None and pid is not None:
            children.setdefault(ppid, []).append(pid)

    best: tuple[int, str] | None = None  # (depth, tool)
    queue = [(foreground_pid, 0)]
    seen = {foreground_pid}
    while queue:
        pid, depth = queue.pop(0)
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            info = processes.get(child)
            if info is not None:
                hit = match_ai_keyword(_stem(getattr(info, "exe", "")), config)
                if hit and (best is None or depth + 1 > best[0]):
                    best = (depth + 1, hit)
            queue.append((child, depth + 1))
    if best is not None:
        return best[1]

    # 3) 标题：先走常规关键词，再走标题专用关键词（如 π）
    hit = match_ai_keyword(title, config)
    if hit:
        return hit
    title_l = (title or "").lower()
    for kw in config.get("ai_title_keywords", []):
        kw_l = kw.lower()
        if kw_l and kw_l in title_l:
            if kw_l.startswith("pi") and any(
                bad in title_l for bad in ("python", "pip", "pypi")
            ):
                continue
            return config.get("ai_tool_names", {}).get(kw, kw)
    return None


# ---------------------------------------------------------------------------
# 终端 TUI 工具识别 / 二级子分类
# ---------------------------------------------------------------------------
def detect_term_tool(title: str, config: dict) -> str | None:
    """从终端窗口标题识别 TUI 工具（vim/git/lazygit/htop…），返回工具名。

    统一用词边界匹配，且跳过路径成分（前导 \\ / : ），
    避免 "D:\\git-stuff - pwsh" / "C:\\Python311 - pwsh" 这类路径标题误判。
    """
    title_l = (title or "").lower()
    if not title_l:
        return None
    for tool in config.get("terminal_tools", []):
        for kw in tool.get("title", []):
            kw_l = (kw or "").lower()
            if not kw_l:
                continue
            for m in re.finditer(r"(?<![\\/:])" + re.escape(kw_l), title_l):
                start, end = m.start(), m.end()
                left_ok = start == 0 or not (title_l[start - 1].isalnum() or title_l[start - 1] == "_")
                right_ok = end >= len(title_l) or not (title_l[end].isalnum() or title_l[end] == "_")
                if left_ok and right_ok:
                    return tool["name"]
    return None


def classify_subcategory(category: str, exe: str, title: str, config: dict) -> str | None:
    """大类确定后按 exe 关键词细分（影音娱乐->游戏、开发工具->编辑器/终端…）。"""
    exe_l = (exe or "").lower()
    for sub in config.get("subcategories", []):
        if sub.get("category") != category:
            continue
        for kw in sub.get("exe", []):
            if kw and kw.lower() in exe_l:
                return sub["name"]
    return None


# ---------------------------------------------------------------------------
# 隐私黑名单
# ---------------------------------------------------------------------------
def is_blacklisted_title(title: str, config: dict) -> bool:
    """标题命中 title_blacklist 任一正则时返回 True（应隐藏为 [已隐藏]）。"""
    title_l = (title or "")
    if not title_l:
        return False
    for pattern in config.get("title_blacklist", []):
        try:
            if re.search(pattern, title_l):
                return True
        except re.error:
            continue
    return False


# ---------------------------------------------------------------------------
# 自测 / --sync-config
# ---------------------------------------------------------------------------
def sync_config() -> int:
    """对比 config.default.json（内置规则）与当前 config.json，报告差异。

    单一事实源：新增/修改分类规则改 config.default.json（或直接改 config.json
    覆盖）；本命令帮助发现"默认规则更新了但 config.json 没跟上"的遗漏。
    """
    default_path = DEFAULT_CONFIG_PATH
    user_path = os.path.join(paths.default_data_root(), "config.json")
    try:
        with open(default_path, "r", encoding="utf-8") as fh:
            default = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync-config] 读取默认配置失败: {exc}")
        return 1
    if not os.path.isfile(user_path):
        print(f"[sync-config] 用户配置不存在（{user_path}），建议复制默认配置：")
        print(f"  copy {default_path} {user_path}")
        return 0
    try:
        with open(user_path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync-config] 读取用户配置失败: {exc}")
        return 1

    missing = [k for k in default if k not in user]       # 默认有、用户没有（新增规则未同步）
    extra = [k for k in user if k not in default]          # 用户自定义（正常）
    both = [k for k in default if k in user]
    changed = [k for k in both if default[k] != user[k]]   # 两侧都存在但值不同（用户覆盖/默认更新）

    print(f"默认配置: {default_path}")
    print(f"用户配置: {user_path}")
    print(f"  用户自定义新增键（正常）: {len(extra)}  {extra}")
    print(f"  默认新增、用户未同步（需注意）: {len(missing)}  {missing}")
    print(f"  两侧均有但值不同（用户覆盖或默认已更新）: {len(changed)}")
    for k in changed[:12]:
        print(f"    - {k}")
    if missing or changed:
        print("提示：若默认规则已更新，可在 config.json 中补充对应键，或删除 config.json 后重新复制。")
        return 1
    print("配置一致。")
    return 0


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) > 1 and _sys.argv[1] == "--sync-config":
        _sys.exit(sync_config())
    cfg = DEFAULT_CONFIG

    def mk(exe: str, ppid: int) -> object:
        class _P:
            pass
        p = _P()
        p.exe = exe
        p.ppid = ppid
        return p

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            raise AssertionError("FAILED: " + msg)

    expect(classify_category("opencode.exe", "", cfg) == "AI编程", "1")
    expect(classify_category("wechat.exe", "张三", cfg) == "社交聊天", "2")
    expect(classify_category("chrome.exe", "YouTube - 主页", cfg) == "浏览器", "3")
    expect(classify_category("Code.exe", "VS Code", cfg) == "开发工具", "4")
    expect(classify_category("winword.exe", "Word", cfg) == "办公学习", "5")
    expect(classify_browser("【合集】Python 教程 - bilibili", cfg) == "学习", "6")
    expect(classify_browser("GitHub - 主页", cfg) == "代码", "7")
    expect(classify_browser("YouTube - 主页", cfg) == "视频", "8")
    expect(extract_contact("wechat.exe", "张三", cfg) == "张三", "9")
    expect(extract_contact("dingtalk.exe", "与 李四 的会话", cfg) == "李四", "10")
    expect(extract_contact("wechat.exe", "微信", cfg) is None, "11")
    expect(is_social_main_title("wechat.exe", "微信", cfg) is True, "12")
    expect(match_ai_keyword("python.exe", cfg) is None, "13")
    expect(match_ai_keyword("pi-agent.exe", cfg) == "pi agent", "14")
    expect(match_ai_keyword("opencode.exe", cfg) == "opencode", "15")
    # π (pi agent) 终端标题识别 + pintia 防误伤
    expect(detect_ai_tool(100, {100: mk("wt.exe", 0)}, "π - niangao", cfg) == "pi agent", "15b")
    expect(detect_ai_tool(100, {100: mk("wt.exe", 0)}, "PTA | 程序设计 (pintia.cn)", cfg) is None, "15c")
    expect(
        detect_ai_tool(100, {100: mk("wt.exe", 0), 200: mk("opencode.exe", 100)}, "opencode", cfg)
        == "opencode",
        "16",
    )
    expect(
        detect_ai_tool(100, {100: mk("wt.exe", 0), 200: mk("python.exe", 100), 300: mk("pip.exe", 200)}, "", cfg)
        is None,
        "17",
    )
    expect(is_blacklisted_title("我的密码是abc123", cfg) is True, "18")
    expect(resolve_app_name("WeChat.exe", cfg) == "微信", "19")
    expect(resolve_alias("aaa123", {"aaa123": "张三"}) == "张三", "20")
    # 终端 TUI 工具识别
    expect(detect_term_tool("git status - niangao", cfg) == "git", "21")
    expect(detect_term_tool("vim - niangao", cfg) == "vim", "22")
    expect(detect_term_tool(r"D:\git-stuff - pwsh", cfg) is None, "23")
    expect(detect_term_tool(r"C:\Python311 - pwsh", cfg) is None, "24")
    expect(detect_term_tool("lazygit - niangao", cfg) == "lazygit", "25")
    expect(detect_term_tool("npm run dev - niangao", cfg) == "npm", "26")
    # 二级子分类
    expect(classify_subcategory("影音娱乐", "potplayer.exe", "", cfg) == "视频播放", "27")
    expect(classify_subcategory("影音娱乐", "qqmusic.exe", "", cfg) == "音乐", "28")
    expect(classify_subcategory("游戏", "steam.exe", "", cfg) == "游戏平台", "28b")
    expect(classify_subcategory("开发工具", "Code.exe", "", cfg) == "编辑器", "29")
    expect(classify_subcategory("开发工具", "wt.exe", "", cfg) == "终端", "30")
    expect(classify_subcategory("开发工具", "git.exe", "", cfg) is None, "31")
    expect(classify_subcategory("社交聊天", "wechat.exe", "", cfg) is None, "32")
    print("ALL TESTS PASSED")
