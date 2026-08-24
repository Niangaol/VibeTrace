# -*- coding: utf-8 -*-
"""dashboard.py — 本地网页仪表盘（v4：周报/月报/导出/主题/口令/备份）。

- 仅监听 127.0.0.1（不做远程访问），默认端口 8765；
- 纯标准库（http.server），页面与图表内联，零外部依赖、离线可用；
- 数据全部来自本机日期文件夹，不产生任何新数据。

视图：
1. 今日概览：大数字卡片（总活跃/AI编程/社交/浏览器停留/会话数）+ 24 小时活跃分布
   + 14/30 天趋势 + 类别/应用分布 + AI 工具/联系人（鼠标悬停看详情）
2. 日报：选日期渲染当日 report.md（前端 mini-markdown，含表格/标题/列表/代码块）
3. 明细：会话明细与浏览器 URL 明细（均支持关键词过滤）
4. 周报：最近 7 个有数据日聚合回顾
5. 月报：按自然月聚合回顾
6. 洞察：离线规则洞察卡片 + 可选 AI 洞察面板
7. 设置：数据备份下载 / 恢复上传

安全与增强（v4）：
- 可选访问口令（config.json 的 dashboard_token，空/缺失=关闭；开启后所有 /api 需要
  X-Dashboard-Token 头，hmac.compare_digest 常量时间比较）
- 浅色/深色/自动 主题切换（localStorage 持久化 + 跟随系统 prefers-color-scheme）
- 一键导出 CSV/JSON（日报/周报/月报）、备份 zip 下载与回滚恢复

用法：
    python dashboard.py                # 启动，浏览器访问 http://127.0.0.1:8765
    python dashboard.py --port 9000    # 指定端口
    python dashboard.py --open         # 启动后自动打开浏览器
"""

from __future__ import annotations

import argparse
import datetime
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report  # noqa: E402
import version  # noqa: E402
import paths  # noqa: E402

# 从 dashboard.py 外置的纯函数/工具（详见 dashboard_util.py）；除本文件内使用外，
# 部分名称（_days_cache/_days_cache_key/invalidate_days_cache/_available_days 等）
# 也供测试经 dashboard.<name> 访问，故一并 re-export。
from dashboard_util import (  # noqa: E402
    _agg_to_csv,
    _ALLOWED_ROOT_FILES,
    _available_days,
    _backup_zip,
    _collect_known_apps,
    _EXCLUDED_FILE_SUFFIXES,
    _safe_extract_zip,
    _sanitize_csv,
)
from dashboard_util import (  # noqa: E402,F401 —— 仅测试/外部经 dashboard.<name> 引用
    _days_cache,
    _days_cache_key,
    invalidate_days_cache,
)

DEFAULT_PORT = 8765
DEFAULT_DATA_ROOT = paths.default_data_root()

# API 日期参数白名单：防路径穿越（date=../../xxx 会拼进数据目录路径）
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# API 月份参数白名单（YYYY-MM）
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_URL_MAX_ROWS = 200  # 浏览器明细最多回传条数
_RESTORE_MAX_BYTES = 200 * 1024 * 1024  # 恢复上传体积上限

# 默认 CSP（API 响应）；页面响应改用带 per-request nonce 的策略（见 _page_csp）
_DEFAULT_CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'")


def _page_csp(nonce: str) -> str:
    """页面 CSP：脚本仅允许带本次请求 nonce 的内联块，移除 unsafe-inline。"""
    return ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'")


def _load_dashboard_token(data_root: str | None = None, config_path: str | None = None) -> str:
    """读取 dashboard_token（空/缺失 = 关闭口令）。

    优先数据根目录的 config.json（与仪表盘 data_root 语义一致，--data-root 场景正确）；
    其次回退 classifier.load_config()（默认/--config 路径，可移植性/深合并一致）。
    """
    if data_root:
        try:
            p = os.path.join(data_root, "config.json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fh:
                    token = json.load(fh).get("dashboard_token")
                if token:
                    return str(token).strip()
        except Exception:  # noqa: BLE001 —— 数据根配置损坏时回退默认读取
            pass
    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config(config_path)
        token = cfg.get("dashboard_token")
        return str(token).strip() if token else ""
    except Exception:  # noqa: BLE001 —— 配置损坏时口令关闭（不阻断仪表盘）
        return ""


_token_cache: dict = {"key": None, "ts": 0.0, "token": ""}


def _required_token(config_path: str | None = None, data_root: str | None = None) -> str:
    """带短 TTL 的 token 缓存，避免每个请求都重读 config（改配置后 ~5s 生效）。"""
    key = data_root or config_path
    now = time.monotonic()
    if _token_cache["key"] != key or now - _token_cache["ts"] > 5.0:
        _token_cache["token"] = _load_dashboard_token(data_root, config_path)
        _token_cache["key"] = key
        _token_cache["ts"] = now
    return _token_cache["token"]


def _load_config_for_root(root: str, config_path: str | None = None) -> dict:
    """读取与数据根目录一致的完整配置（已深合并默认值）。

    优先级：显式 --config 路径 > <root>/config.json > 默认 config.json。
    """
    import classifier  # noqa: PLC0415
    if config_path:
        return classifier.load_config(config_path)
    local = os.path.join(root, "config.json")
    if os.path.isfile(local):
        return classifier.load_config(local)
    return classifier.load_config()


def _config_file_for_root(root: str, config_path: str | None = None) -> str:
    """设置页保存 AI 配置时实际写入的 config.json 路径。"""
    if config_path:
        return config_path
    return os.path.join(root, "config.json")


def _ai_settings_view(config: dict) -> dict:
    """把完整配置里的 AI 段转成前端可安全展示的结构（API Key 只给“是否已设置”）。"""
    ins = config.get("insights") if isinstance(config.get("insights"), dict) else {}
    ai = ins.get("ai") if isinstance(ins.get("ai"), dict) else {}
    return {
        "enabled": bool(ai.get("enabled")),
        "provider": str(ai.get("provider") or ""),
        "base_url": str(ai.get("base_url") or ""),
        "model": str(ai.get("model") or ""),
        "timeout_s": int(ai.get("timeout_s") or 60),
        "send_raw_titles": bool(ai.get("send_raw_titles")),
        "language": str(ai.get("language") or "zh"),
        "api_key_set": bool(ai.get("api_key")),
    }


def _save_ai_settings(root: str, config_path: str | None, payload: dict) -> dict:
    """把 AI 设置保存到 config.json（原子写），返回保存后的前端视图。

    api_key 为空字符串时保留原值（前端只显示“已设置/未设置”，不回显密钥）。
    """
    import classifier  # noqa: PLC0415
    path = _config_file_for_root(root, config_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            cfg = {}
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError:
        cfg = {}
    cfg.setdefault("insights", {})
    ins = cfg["insights"]
    if not isinstance(ins, dict):
        ins = {}
        cfg["insights"] = ins
    ins.setdefault("ai", {})
    ai = ins["ai"]
    if not isinstance(ai, dict):
        ai = {}
        ins["ai"] = ai
    old_api_key = str(ai.get("api_key") or "")

    ai["enabled"] = bool(payload.get("enabled"))
    ai["provider"] = str(payload.get("provider") or "").strip()
    ai["base_url"] = str(payload.get("base_url") or "").strip()
    ai["model"] = str(payload.get("model") or "").strip()
    try:
        ai["timeout_s"] = max(1, min(600, int(payload.get("timeout_s") or 60)))
    except (TypeError, ValueError):
        ai["timeout_s"] = 60
    ai["send_raw_titles"] = bool(payload.get("send_raw_titles"))
    ai["language"] = str(payload.get("language") or "zh").strip() or "zh"
    # 选择内置预设且用户未手填时，把预设的 base_url/model 落盘，方便界面回显
    try:
        import insights  # noqa: PLC0415
        custom = insights.load_ai_custom(root)
        preset_map = {p["id"]: p for p in
                      insights.list_provider_presets(custom.get("providers"))}
        preset = preset_map.get(ai["provider"].lower(), {})
        if not ai["base_url"]:
            ai["base_url"] = preset.get("base_url", "")
        if not ai["model"]:
            ai["model"] = preset.get("model", "")
    except Exception:  # noqa: BLE001
        pass
    new_key = str(payload.get("api_key") or "").strip()
    if new_key:
        ai["api_key"] = new_key
    elif old_api_key:
        ai["api_key"] = old_api_key
    else:
        ai["api_key"] = ""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    classifier.invalidate_config_cache(path)
    return _ai_settings_view(cfg)


def _save_goals_settings(root: str, config_path: str | None, payload: dict) -> dict:
    """保存每日目标设置到 config.json（原子写），返回归一化后的配置段。

    归一化复用 goals.goals_config（非法数值回退/夹取口径一致）。
    """
    import classifier  # noqa: PLC0415
    import goals  # noqa: PLC0415 —— 惰性导入
    path = _config_file_for_root(root, config_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            cfg = {}
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError:
        cfg = {}
    norm = goals.goals_config({"goals": payload})
    cfg["goals"] = {
        "enabled": norm["enabled"],
        "daily_active_min": norm["daily_active_min"],
        "daily_coding_min": norm["daily_coding_min"],
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    classifier.invalidate_config_cache(path)
    return dict(cfg["goals"])


# ---------------------------------------------------------------------------
# 软件更新（updater.py 的仪表盘侧状态与辅助）
# ---------------------------------------------------------------------------
_UPDATE_CHECK_CACHE: dict = {"ts": 0.0, "result": None}
_UPDATE_STATE: dict = {
    "state": "idle",           # idle | downloading | ready | error | applying
    "downloaded": 0,
    "total": 0,
    "path": None,
    "latest": "",
    "error": None,
}
_UPDATE_LOCK = threading.Lock()


def _update_api_base(config: dict) -> str | None:
    """config.json 的 update.api_base（空则用默认 GitHub API）。"""
    up = config.get("update") if isinstance(config.get("update"), dict) else {}
    return str(up.get("api_base") or "").strip() or None


def _update_progress(got: int, total: int | None) -> None:
    with _UPDATE_LOCK:
        _UPDATE_STATE["downloaded"] = int(got or 0)
        _UPDATE_STATE["total"] = int(total or 0)


def _run_download(asset: dict, dest: str, api_base: str | None = None) -> None:
    try:
        import updater  # noqa: PLC0415 —— 惰性导入
        updater.download(
            str(asset.get("url") or ""), dest,
            expected_size=int(asset.get("size") or 0) or None,
            expected_digest=str(asset.get("digest") or "") or None,
            progress=_update_progress,
            api_base=api_base,
        )
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(state="ready", path=dest, error=None)
    except Exception as exc:  # noqa: BLE001 —— 下载失败转为可展示状态
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(state="error", path=None, error=str(exc))


def _sanitize_restored_config(src_path: str, local_path: str) -> str | None:
    """恢复 config.json 前的安全净化：update.api_base 永远以本机现值为准。

    备份包视为不可信输入：若允许其覆写 update.api_base，恶意备份可把应用内
    更新源改指攻击者域名，构成更新供应链攻击链。本机无该键时直接删除。
    返回净化后的临时文件路径（位于同一临时目录，随恢复流程清理）；
    JSON 解析失败返回 None（调用方跳过恢复该文件，不因坏配置拖垮整个恢复）。
    """
    try:
        with open(src_path, "r", encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(cfg, dict):
        return None
    local_api_base = None
    try:
        with open(local_path, "r", encoding="utf-8-sig") as fh:
            local_cfg = json.load(fh)
        if isinstance(local_cfg, dict) and isinstance(local_cfg.get("update"), dict):
            local_api_base = local_cfg["update"].get("api_base")
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    if isinstance(cfg.get("update"), dict):
        if local_api_base:
            cfg["update"]["api_base"] = str(local_api_base)
        else:
            cfg["update"].pop("api_base", None)
    tmp = src_path + ".sanitized"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except OSError:
        return None
    return tmp


def _merge_restore(data_root: str, tmp: str) -> dict:
    """把临时解压目录合并覆盖到 data_root（逐日期目录 + 配置文件）。"""
    restored_days: list[str] = []
    restored_files: list[str] = []
    if not os.path.isdir(data_root):
        os.makedirs(data_root, exist_ok=True)
    for name in sorted(os.listdir(tmp)):
        src = os.path.join(tmp, name)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and os.path.isdir(src):
            dst = os.path.join(data_root, name)
            os.makedirs(dst, exist_ok=True)
            for fn in os.listdir(src):
                s = os.path.join(src, fn)
                if os.path.isfile(s) and not any(fn.lower().endswith(ext) for ext in _EXCLUDED_FILE_SUFFIXES):
                    shutil.copy2(s, os.path.join(dst, fn))
                    restored_files.append(f"{name}/{fn}")
            restored_days.append(name)
        elif name in _ALLOWED_ROOT_FILES and os.path.isfile(src):
            if name == "config.json":
                sanitized = _sanitize_restored_config(src, os.path.join(data_root, name))
                if sanitized is None:
                    continue  # 坏配置不恢复，其余数据照常
                src = sanitized
            shutil.copy2(src, os.path.join(data_root, name))
            restored_files.append(name)
    return {"days": restored_days, "files": restored_files}


# ---------------------------------------------------------------------------
# 页面模板：外置 assets/dashboard.html（ROADMAP §9.2 #1）
# 运行时加载，兼容源码运行（paths.script_dir()/assets）与 PyInstaller 打包
# （sys._MEIPASS/assets，spec 的 datas 已包含该文件）。文件缺失/读取失败时
# 回退到极简内联兜底页，保证仪表盘不白屏（best-effort，不抛异常）。
# 带 mtime/size 缓存：开发时改 HTML 免重启即生效，生产下等价一次性加载。
# ---------------------------------------------------------------------------
_TEMPLATE_NAME = "dashboard.html"
_FALLBACK_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VibeTrace</title></head><body style="font-family:sans-serif;padding:32px">
<h1>VibeTrace</h1>
<p>页面模板 assets/dashboard.html 缺失或不可读，仪表盘前端无法加载。</p>
<p>数据接口仍可用，例如 <code>/api/dates</code>、<code>/api/day?date=YYYY-MM-DD</code>。</p>
</body></html>
"""

_template_cache: dict = {"path": None, "mtime": None, "size": None, "data": None}


def template_paths() -> list[str]:
    """页面模板候选路径（按优先级）：打包解压目录 > 程序目录 > 本文件目录。"""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(str(meipass), "assets", _TEMPLATE_NAME))
    candidates.append(os.path.join(paths.script_dir(), "assets", _TEMPLATE_NAME))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "assets", _TEMPLATE_NAME))
    out: list[str] = []
    for c in candidates:
        if c not in out:
            out.append(c)
    return out


def load_page_template() -> str:
    """读取页面模板（mtime/size 缓存）；全部候选不可用时返回内联兜底页。"""
    for path in template_paths():
        try:
            st = os.stat(path)
        except OSError:
            continue
        cache = _template_cache
        if (cache["data"] is not None and cache["path"] == path
                and cache["mtime"] == st.st_mtime and cache["size"] == st.st_size):
            return cache["data"]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = fh.read()
        except OSError:
            continue
        _template_cache.update(path=path, mtime=st.st_mtime, size=st.st_size, data=data)
        return data
    return _FALLBACK_TEMPLATE


def _page_html(root: str, auth_enabled: bool, nonce: str = "") -> str:
    """把数据根目录 / 鉴权标记 / 版本号 / CSP nonce 注入模板（与原内联替换逻辑等价）。"""
    return (load_page_template()
            .replace("DATA_ROOT", json.dumps(root).replace("$", "\\$"))
            .replace("AUTH_FLAG", "true" if auth_enabled else "false")
            .replace("APP_VERSION", version.VERSION)
            .replace("__CSP_NONCE__", nonce))




class Handler(BaseHTTPRequestHandler):
    server_version = "VibeTraceDashboard/4.0"

    def log_message(self, fmt, *args):  # 静默，减少刷屏
        pass

    def _send_security_headers(self, extra: dict | None = None, csp: str | None = None) -> None:
        """统一的隐私/安全响应头（CSP / X-Frame-Options 等）；csp 可覆盖默认策略。"""
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", csp or _DEFAULT_CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        """发送 JSON 响应（带统一隐私/安全头）。

        紧凑分隔符：API 响应体普遍数十 KB 起，去掉默认的 `, `/`: ` 空格可减小
        10–20% 体积并加快序列化；JSON 语义不变，前端 JSON.parse 无感。
        """
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_blob(self, data: bytes, content_type: str, filename: str) -> None:
        """发送附件下载（导出 CSV/JSON、备份 zip），带 Content-Disposition。"""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _valid_date(self, query: dict) -> str | None:
        """校验并返回日期参数；非法返回 None。"""
        date = query.get("date", [""])[0]
        return date if _DAY_RE.fullmatch(date) else None

    def _valid_month(self, query: dict) -> str | None:
        """校验并返回月份参数（YYYY-MM）；非法返回 None。"""
        month = query.get("month", [""])[0]
        if not _MONTH_RE.fullmatch(month):
            return None
        try:
            datetime.datetime.strptime(month, "%Y-%m")
            return month
        except ValueError:
            return None

    def _required_token(self) -> str:
        """当前生效的访问口令；'' = 未开启。"""
        config_path = self.server.config_path if hasattr(self.server, "config_path") else None
        return _required_token(config_path, data_root=self.server.data_root)

    def _auth_ok(self) -> bool:
        """校验 X-Dashboard-Token（未开启口令直接放行；开启则 hmac 常量时间比较）。"""
        token = self._required_token()
        if not token:
            return True
        provided = self.headers.get("X-Dashboard-Token", "")
        return hmac.compare_digest(provided, token)

    def _origin_allowed(self, headers) -> bool:
        """同源校验：Origin/Referer 存在时必须匹配本服务（防恶意网页偷读隐私数据）。

        浏览器跨站 fetch/资源请求必然携带 Origin 或 Referer（指向恶意站点），
        校验拒绝即可堵住 CSRF/localhost 数据泄露；curl/无头脚本等无浏览器
        上下文的请求不带这两个头，予以放行（不是攻击向量）。
        """
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        for header in ("Origin", "Referer"):
            value = headers.get(header)
            if not value:
                continue
            try:
                parsed = urllib.parse.urlparse(value.strip())
            except ValueError:
                return False
            if parsed.netloc not in allowed:
                return False
        return True

    # ---- 周报 / 月报 数据构造（复用 report.py 聚合） ----
    def _week_aggregate(self, root: str) -> dict:
        """最近 7 个有数据日聚合；返回 (agg, days)。"""
        days = _available_days(root)[-7:]
        return report.aggregate_days(days, root), days

    def _month_aggregate(self, root: str, month: str) -> dict | None:
        """月度聚合；当月无数据返回 None。"""
        agg = report.aggregate_month(month, root)
        if not agg.get("per_day"):
            return None
        return agg

    def _render_week_md(self, agg: dict) -> str:
        return report._report_from_agg(agg, "电脑使用情况周报（最近 7 个有数据日）")

    def _render_month_md(self, agg: dict) -> str:
        # 与日报链同源：仪表盘触发的月报也透传 --config（server.config_path），
        # 链内解析优先级 config_path > <root>/config.json > 全局默认（见 report._config_for_root）
        return report.generate_month_report_md(agg.get("month", ""), self.server.data_root,
                                               config_path=self.server.config_path)

    def _handle_export(self, query: dict, root: str) -> None:
        """/api/export：CSV/JSON 一键下载（day/week/month）。"""
        ftype = query.get("type", [""])[0]
        scope = query.get("scope", [""])[0]
        if ftype not in ("csv", "json"):
            self._send_json({"error": "invalid type"}, 400)
            return
        agg = None
        filename = "report"
        if scope == "day":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            agg = report.aggregate(date, root)
            filename = f"report_{date}"
        elif scope == "week":
            a, days = self._week_aggregate(root)
            agg = a
            filename = f"week_{days[-1] if days else 'none'}"
        elif scope == "month":
            month = self._valid_month(query)
            if not month:
                self._send_json({"error": "invalid month"}, 400)
                return
            m = self._month_aggregate(root, month)
            if m is None:
                self._send_json({"error": "no data"}, 404)
                return
            agg = m
            filename = f"month_{month}"
        else:
            self._send_json({"error": "invalid scope"}, 400)
            return

        if ftype == "json":
            payload = json.dumps(agg, ensure_ascii=False, default=str).encode("utf-8")
            self._send_blob(payload, "application/json; charset=utf-8", f"{filename}.json")
        else:
            csv = _agg_to_csv(agg)
            # 简单安全清洗：去掉可能被当作公式的单元格前缀（CSV 注入防护）
            csv = _sanitize_csv(csv)
            self._send_blob(csv.encode("utf-8-sig"), "text/csv; charset=utf-8", f"{filename}.csv")

    def do_GET(self):  # noqa: N802
        """GET 分发：同源校验 → 口令校验 → 页面 / 路由表派发。

        各端点实现拆分为 _api_* 方法（见模块底部 _GET_ROUTES 路由表），
        本方法只保留横切关注点（同源 / 鉴权）与分发，不再承载端点逻辑。
        """
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        root = self.server.data_root

        # 同源校验：跨站请求直接拒绝（隐私数据防偷读）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return

        # 访问口令：开启状态所有 /api 一致校验（P1-8）
        auth_enabled = bool(self._required_token())
        if path.startswith("/api/") and not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return

        if path in ("/", "/index.html"):
            self._send_page(root, auth_enabled)
            return

        handler = _GET_ROUTES.get(path)
        if handler is None:
            self._send_json({"error": "not found"}, 404)
            return
        handler(self, query, root)

    # ------------------------------------------------------------------
    # 页面与静态
    # ------------------------------------------------------------------
    def _send_page(self, root: str, auth_enabled: bool) -> None:
        """/ ：注入数据根目录 / 鉴权标记 / 版本号 / per-request CSP nonce 的单页模板。"""
        nonce = secrets.token_urlsafe(16)
        html = _page_html(root, auth_enabled, nonce=nonce)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers(csp=_page_csp(nonce))
        self.end_headers()
        self.wfile.write(body)

    def _api_favicon(self, query: dict, root: str) -> None:
        """/favicon.ico：无图标，204 空响应。"""
        self.send_response(204)
        self.end_headers()

    # ------------------------------------------------------------------
    # 基础数据（概览 / 趋势）
    # ------------------------------------------------------------------
    def _api_dates(self, query: dict, root: str) -> None:
        """/api/dates：全部有数据的日期列表。"""
        self._send_json({"dates": _available_days(root)})

    def _api_days(self, query: dict, root: str) -> None:
        """/api/days?n=14：最近 N 天总活跃与会话数（趋势图）。"""
        n = max(1, min(90, int(query.get("n", ["14"])[0])))
        days = _available_days(root)[-n:]
        out = []
        for d in days:
            # 单日聚合失败不拖垮整个趋势（返回 0，时间轴保持连续）
            try:
                agg = report.aggregate(d, root)
                out.append({"date": d, "total_ms": agg["total_active_ms"], "count": agg["session_count"]})
            except Exception:  # noqa: BLE001
                out.append({"date": d, "total_ms": 0, "count": 0})
        self._send_json({"days": out})

    def _api_day(self, query: dict, root: str) -> None:
        """/api/day?date=：单日聚合。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        self._send_json({"date": date, "aggregate": report.aggregate(date, root)})

    def _api_hourly(self, query: dict, root: str) -> None:
        """/api/hourly?date=：单日 24 小时活跃分布。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        agg = report.aggregate(date, root)
        self._send_json({"date": date, "hourly_ms": agg.get("hourly_ms", [0] * 24)})

    def _api_heatmap(self, query: dict, root: str) -> None:
        """/api/heatmap?days=84：最近 N 天每日总活跃 + 24 小时分布。"""
        try:
            n = max(7, min(90, int(query.get("days", ["84"])[0])))
        except ValueError:
            n = 84
        days = _available_days(root)[-n:]
        out = []
        for d in days:
            # 单日聚合失败不拖垮热力图/总活跃（以 0 兜底，时间轴保持连续）
            try:
                agg = report.aggregate(d, root)
                out.append({
                    "date": d,
                    "total_ms": agg["total_active_ms"],
                    "hourly_ms": agg.get("hourly_ms", [0] * 24),
                })
            except Exception:  # noqa: BLE001
                out.append({"date": d, "total_ms": 0, "hourly_ms": [0] * 24})
        self._send_json({"days": out})

    # ------------------------------------------------------------------
    # 报表（日报 / 周报 / 月报 / 导出 / 备份）
    # ------------------------------------------------------------------
    def _api_report(self, query: dict, root: str) -> None:
        """/api/report?date=：当日 report.md 原文。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        md_path = os.path.join(root, date, "report.md")
        if os.path.isfile(md_path):
            try:
                with open(md_path, "r", encoding="utf-8-sig") as fh:
                    self._send_json({"date": date, "exists": True, "markdown": fh.read()})
                    return
            except OSError:
                pass
        self._send_json({"date": date, "exists": False, "markdown": ""})

    def _api_week(self, query: dict, root: str) -> None:
        """/api/week：最近 7 个有数据日聚合（复用 report 聚合）。"""
        a, days = self._week_aggregate(root)
        payload = {
            "days": days,
            "total_ms": a["total_active_ms"],
            "count": a["session_count"],
            "aggregate": a,
            "markdown": self._render_week_md(a),
        }
        self._send_json(payload)

    def _api_month(self, query: dict, root: str) -> None:
        """/api/month?month=YYYY-MM：自然月聚合。"""
        month = self._valid_month(query)
        if not month:
            self._send_json({"error": "invalid month"}, 400)
            return
        a = self._month_aggregate(root, month)
        if a is None:
            self._send_json({"month": month, "exists": False,
                             "markdown": "", "aggregate": None})
            return
        self._send_json({
            "month": month, "exists": True,
            "total_ms": a["total_active_ms"],
            "active_days": len(a.get("per_day", [])),
            "count": a["session_count"],
            "aggregate": a,
            "markdown": self._render_month_md(a),
        })

    def _api_export(self, query: dict, root: str) -> None:
        """/api/export：CSV/JSON 一键下载（day/week/month）。"""
        self._handle_export(query, root)

    def _api_backup(self, query: dict, root: str) -> None:
        """/api/backup：数据备份 zip 附件下载。"""
        if not os.path.isdir(root):
            self._send_json({"error": "no data"}, 404)
            return
        try:
            data = _backup_zip(root)
            stamp = datetime.date.today().isoformat()
            self._send_blob(data, "application/zip", f"usagemonitor_backup_{stamp}.zip")
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"backup failed: {exc}"}, 500)

    # ------------------------------------------------------------------
    # AI 会话深度 / 时间轴 / 成长 / 对比 / 查询 / 预算
    # ------------------------------------------------------------------
    def _api_ai_sessions(self, query: dict, root: str) -> None:
        """/api/ai-sessions?date=：AI 会话深度统计（本地会话文件 + Web AI 会话）。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import ai_sessions  # noqa: PLC0415
            web_visits = None
            try:
                import browser_history  # noqa: PLC0415
                bh = browser_history.collect(date, root, config)
                web_visits = bh.get("visits") or []
            except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地统计
                web_visits = None
            data = ai_sessions.collect(date, config, web_visits=web_visits)
            self._send_json({"date": date, "ai_sessions": data})
        except Exception as exc:  # noqa: BLE001 —— 会话深度失败不拖垮概览
            self._send_json({"error": f"ai-sessions unavailable: {exc}"}, 500)

    def _api_timeline(self, query: dict, root: str) -> None:
        """/api/timeline?date=：Vibe 时间轴回放（v2.5）三源合并。

        纯派生、best-effort：无数据返回 200 空态；缓存复用 report._agg_cache。
        """
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        project = (query.get("project") or [None])[0] or None
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import timeline  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
            data = timeline.build_timeline(date, root, config, project=project)
            self._send_json({"date": date, "events": data.get("events") or [],
                             "summary": data.get("summary") or {}})
        except Exception as exc:  # noqa: BLE001 —— 时间轴失败不拖垮仪表盘
            self._send_json({"error": f"timeline unavailable: {exc}"}, 500)

    def _api_trend(self, query: dict, root: str) -> None:
        """/api/trend、/api/growth：能力成长曲线（周均值快照，v2.6 P7）。

        纯派生 + 持久化快照（growth_baseline.json）：首次/坏档全量现算（自愈），
        此后增量跳过重算；weeks 为最近 N 周（默认 8，1..52）。
        """
        try:
            weeks = int((query.get("weeks") or ["8"])[0])
        except (TypeError, ValueError):
            weeks = 8
        if not (1 <= weeks <= 52):
            self._send_json({"error": "invalid weeks"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import growth  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
            data = growth.growth_snapshot(root, config)
            data["weeks"] = data.get("weeks") or []
            if weeks < len(data["weeks"]):
                data["weeks"] = data["weeks"][-weeks:]
            self._send_json(data)
        except Exception as exc:  # noqa: BLE001 —— 成长曲线失败不拖垮仪表盘
            self._send_json({"error": f"trend unavailable: {exc}"}, 500)

    def _api_ai_compare(self, query: dict, root: str) -> None:
        """/api/ai-compare、/api/tool-compare：多工具横向对比（v2.6 P6，纯派生）。

        start/end 必填且全匹配 YYYY-MM-DD；end<start 或范围非 1..90 天 → 400；
        project 可选模糊过滤；无数据 → 200 空态；内部异常降级 500 不拖垮仪表盘。
        """
        start = (query.get("start") or [""])[0]
        end = (query.get("end") or [""])[0]
        if not _DAY_RE.fullmatch(start) or not _DAY_RE.fullmatch(end):
            self._send_json({"error": "invalid date"}, 400)
            return
        try:
            d0 = datetime.date.fromisoformat(start)
            d1 = datetime.date.fromisoformat(end)
        except ValueError:
            self._send_json({"error": "invalid date"}, 400)
            return
        if d1 < d0 or (d1 - d0).days + 1 > 90:
            self._send_json({"error": "invalid range"}, 400)
            return
        project = (query.get("project") or [None])[0] or None
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import tool_compare  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
            days = [(d0 + datetime.timedelta(days=i)).isoformat()
                    for i in range((d1 - d0).days + 1)]
            data = tool_compare.compare_tools(days, root, config, project=project)
            self._send_json(data)
        except ValueError:
            self._send_json({"error": "invalid range"}, 400)
        except Exception as exc:  # noqa: BLE001 —— 对比失败不拖垮仪表盘
            self._send_json({"error": f"ai-compare unavailable: {exc}"}, 500)

    def _api_query(self, query: dict, root: str) -> None:
        """/api/query：受限模板查询（非 LLM，docs/VIBECODING_IMPLEMENTATION_GUIDE.md §6.2）。

        两种入口：?q=<自然语言模板> 或指南兼容的 ?tpl=q1&start=...&end=...；
        只做固定模板匹配，参数白名单校验；未命中/非法 → 400；异常降级 500。
        """
        q_text = (query.get("q") or [""])[0].strip()
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import query as _qmod  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
            if q_text:
                result = _qmod.run_query(q_text, root, config)
            else:
                tpl_id = (query.get("tpl") or [""])[0].strip()
                if not tpl_id:
                    self._send_json({"error": "missing q or tpl"}, 400)
                    return
                result = _qmod.run_template(tpl_id, query, root, config)
        except Exception as exc:  # noqa: BLE001 —— 查询解析/执行失败不拖垮仪表盘
            self._send_json({"error": f"query unavailable: {exc}"}, 500)
            return
        if not result.get("ok"):
            self._send_json({"error": result.get("error") or "bad query"}, 400)
            return
        self._send_json(result)

    def _api_budget(self, query: dict, root: str) -> None:
        """/api/budget：成本预算状态（默认关闭，v2.6 P3，纯派生 best-effort）。

        period=daily|monthly；缺省按日期粒度推断；配置未开启/无效/异常 → 200 空态。
        """
        period = (query.get("period") or [""])[0].strip().lower()
        if period not in ("", "daily", "monthly"):
            self._send_json({"error": "invalid period"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        if period == "monthly":
            m = self._valid_month({"month": query.get("date", [""])})
            if not m:
                self._send_json({"error": "invalid date"}, 400)
                return
            date = m
        elif period == "daily":
            d = self._valid_date(query)
            if not d:
                self._send_json({"error": "invalid date"}, 400)
                return
            date = d
        else:
            d = self._valid_date(query)
            if d:
                date, period = d, "daily"
            else:
                m = self._valid_month({"month": query.get("date", [""])})
                if not m:
                    self._send_json({"error": "invalid date"}, 400)
                    return
                date, period = m, "monthly"
        try:
            import budget  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
            self._send_json(budget.budget_status(date, root, config, period=period))
        except Exception as exc:  # noqa: BLE001 —— 兑现 docstring 契约：异常降级为 200 关闭态（曾误发 500）
            self._send_json({"enabled": False, "period": period,
                             "status": "invalid",
                             "error": f"budget unavailable: {exc}"}, 200)

    # ------------------------------------------------------------------
    # 洞察（规则 / AI / 行为 / 人格 / 设置 / Ollama）
    # ------------------------------------------------------------------
    def _api_insights(self, query: dict, root: str) -> None:
        """/api/insights?date=：规则即时计算（离线）；AI 只读缓存（成功才写缓存）。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import insights  # noqa: PLC0415 —— 惰性导入
            prev_day = (datetime.date.fromisoformat(date)
                        - datetime.timedelta(days=1)).isoformat()
            agg = report.aggregate(date, root)
            prev_agg = report.aggregate(prev_day, root)
            rules = insights.rule_insights(agg, config, prev_agg)
            # v2.7「简单学习」：个性化基线异常（Welford/z-score，越用越准）
            try:
                rules.extend(insights.baseline_insights(root, date, agg, config))
            except Exception:  # noqa: BLE001
                pass
            ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
            ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
            ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
            ai = None
            if ai_enabled:
                ai = insights.ai_insights(date, root, config, refresh=False)
                ai["provider"] = str(ai_cfg.get("provider") or "")
            behavior = insights.behavior_insights(agg, config)
            persona = insights.persona_insights(agg, config)
            time_saved = insights.time_saved_insights(agg, config)
            import git_insights  # noqa: PLC0415 —— 只读本地 Git 分析
            git = git_insights.git_insights(config, date)
            # v2.5：AI 会话质量卡片（纯离线派生；失败不影响既有洞察）
            ai_quality = []
            try:
                import ai_sessions as _ai_mod  # noqa: PLC0415
                ai_quality = insights.conversation_quality_insights(
                    _ai_mod.collect(date, config))
            except Exception:  # noqa: BLE001
                ai_quality = []
            self._send_json({
                "date": date, "rules": rules,
                "ai_enabled": ai_enabled, "ai": ai,
                "behavior": behavior,
                "persona": persona,
                "time_saved": time_saved,
                "git": git,
                "ai_quality": ai_quality,
            })
        except Exception as exc:  # noqa: BLE001 —— 洞察失败不拖垮仪表盘
            self._send_json({"error": f"insights unavailable: {exc}"}, 500)

    def _api_adoption(self, query: dict, root: str) -> None:
        """/api/adoption?date=：Git 侧采纳率代理指标（折叠 + 灰色降权展示，仅供参考）。

        数据来自 adoption.py（仅 Git 侧粗代理：retention = 新增/(新增+删除)，
        reworked_ratio = modify_ratio），绝不掺 AI 会话时间窗归因，永远不给
        confidence=high。任何单源失败 → 契约空态 200 可展示，绝不 500。
        """
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import adoption  # noqa: PLC0415 —— 惰性导入，失败不拖垮仪表盘
            result = adoption.adoption_stats(date, root, config)
        except Exception as exc:  # noqa: BLE001 —— 代理指标异常 → 契约空态，绝不 500
            result = {
                "date": date,
                "enabled": False,
                "found": False,
                "notice": f"采纳率代理指标不可用：{exc}",
                "confidence": "low",
                "summary": {"projects": 0, "files": 0, "commit_count": 0,
                            "lines_added": 0, "lines_deleted": 0, "churn": 0,
                            "retention": None, "reworked_ratio": None},
                "projects": [],
            }
        self._send_json(result)

    def _api_insights_settings(self, query: dict, root: str) -> None:
        """/api/insights/settings：AI 设置视图（开关 + provider 预设 + 自定义端点）。"""
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import insights  # noqa: PLC0415
            custom = insights.load_ai_custom(root)
            ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
            ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
            ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
            self._send_json({
                "ai": _ai_settings_view(config),
                "ai_enabled": ai_enabled,
                "presets": insights.list_provider_presets(custom.get("providers")),
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"insights settings unavailable: {exc}"}, 500)

    def _api_insights_ai(self, query: dict, root: str) -> None:
        """/api/insights/ai?date=：读取 AI 洞察缓存（只读，绝不触发付费生成）。

        安全约定：GET 一律 refresh=False——强制重生成走 POST（_api_insights_ai_refresh），
        防止跨站 GET 触发付费 API 调用（成本型 CSRF）与意外重复计费。
        """
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import insights  # noqa: PLC0415
            ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
            ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
            ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
            if not ai_enabled:
                self._send_json({
                    "date": date, "ai_enabled": False,
                    "ai": {
                        "generated_at": None, "model": None, "insights": None,
                        "error": "AI 洞察未开启（config.json: insights.ai.enabled=false）",
                        "provider": str(ai_cfg.get("provider") or ""),
                    },
                })
                return
            ai = insights.ai_insights(date, root, config, refresh=False)
            ai["provider"] = str(ai_cfg.get("provider") or "")
            self._send_json({"date": date, "ai_enabled": True, "ai": ai})
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"insights ai unavailable: {exc}"}, 500)

    def _api_ollama_models(self, query: dict, root: str) -> None:
        """/api/insights/ollama/models：Ollama 本地模型列表（设置页下拉/校验）。"""
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import insights  # noqa: PLC0415
            ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
            ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
            base_url = str(ai_cfg.get("base_url") or "").strip()
            models = insights.ollama_models(base_url or None)
            self._send_json({"models": models, "error": None})
        except Exception as exc:  # noqa: BLE001 —— 连接失败不拖垮设置页
            self._send_json({"models": [], "error": str(exc)})

    # ------------------------------------------------------------------
    # AI 客制化模块 / 定价
    # ------------------------------------------------------------------
    def _api_ai_module(self, query: dict, root: str) -> None:
        """/api/ai/module：AI 洞察客制化模块（持久化于 <root>/ai_custom.json）。"""
        try:
            import insights  # noqa: PLC0415
            custom = insights.load_ai_custom(root)
            self._send_json({
                "custom": custom,
                "sections": insights.PROMPT_SECTION_ITEMS,
                "presets": insights.list_provider_presets(custom.get("providers")),
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"ai module unavailable: {exc}"}, 500)

    def _api_ai_module_export(self, query: dict, root: str) -> None:
        """/api/ai/module/export：导出 AI 客制化模块配置（ai_custom.json 完整内容）。"""
        try:
            import insights  # noqa: PLC0415
            custom = insights.load_ai_custom(root)
            data = json.dumps(custom, ensure_ascii=False, indent=2).encode("utf-8")
            self._send_blob(data, "application/json; charset=utf-8", "ai_custom.json")
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"ai module export failed: {exc}"}, 500)

    def _api_pricing_get(self, query: dict, root: str) -> None:
        """/api/pricing：内置模型定价 + 用户 <root>/ai_pricing.json 覆盖。"""
        try:
            import ai_sessions  # noqa: PLC0415
            builtin = dict(ai_sessions._DEFAULT_PRICING)
            custom: dict = {}
            fp = os.path.join(root, "ai_pricing.json")
            if os.path.isfile(fp):
                try:
                    with open(fp, "r", encoding="utf-8-sig") as fh:
                        loaded = json.load(fh)
                    if isinstance(loaded, dict):
                        custom = loaded
                except Exception:  # noqa: BLE001
                    custom = {}
            self._send_json({
                "builtin_count": len(builtin),
                "builtin": {k: list(v) for k, v in sorted(builtin.items())},
                "custom": custom,
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"pricing read failed: {exc}"}, 500)

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    def _api_update_check(self, query: dict, root: str) -> None:
        """/api/update/check：新版本检测（GitHub Releases API，结果缓存 5 分钟）。"""
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import updater  # noqa: PLC0415 —— 惰性导入
            now = time.monotonic()
            if now - _UPDATE_CHECK_CACHE["ts"] > 300 or _UPDATE_CHECK_CACHE["result"] is None:
                _UPDATE_CHECK_CACHE["result"] = updater.check_for_update(
                    api_base=_update_api_base(config), timeout=8.0)
                _UPDATE_CHECK_CACHE["ts"] = now
            self._send_json(dict(_UPDATE_CHECK_CACHE["result"]))
        except Exception as exc:  # noqa: BLE001
            self._send_json({
                "current": version.VERSION, "latest": "", "has_update": False,
                "notes": "", "published_at": "", "url": "", "asset": None,
                "error": f"检查更新失败：{exc}",
            })

    def _api_update_status(self, query: dict, root: str) -> None:
        """/api/update/status：更新下载/应用状态（前端轮询）。"""
        try:
            import updater  # noqa: PLC0415
            frozen = updater.is_frozen()
        except Exception:  # noqa: BLE001
            frozen = False
        with _UPDATE_LOCK:
            state = dict(_UPDATE_STATE)
        self._send_json({
            "current": version.VERSION,
            "frozen": frozen,
            "dev": not frozen,
            "state": state["state"],
            "downloaded": state["downloaded"],
            "total": state["total"],
            "latest": state["latest"],
            "error": state["error"],
        })

    # ------------------------------------------------------------------
    # 浏览器明细 / 日志 / 分组
    # ------------------------------------------------------------------
    def _api_urls(self, query: dict, root: str) -> None:
        """/api/urls?date=：浏览器 URL 明细（最多 _URL_MAX_ROWS 条）。"""
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        try:
            import browser_history  # noqa: PLC0415 —— 惰性导入
            # 与其他端点一致：按 data_root/--config 取配置（而非全局默认路径）
            config = _load_config_for_root(root, self.server.config_path)
            data = browser_history.collect(date, root, config)
            visits = data.get("visits", [])[:_URL_MAX_ROWS]
            self._send_json({
                "date": date,
                "count": data.get("count", 0),
                "total_duration_s": data.get("total_duration_s", 0),
                "by_category_duration_s": data.get("by_category_duration_s", {}),
                "by_domain_duration_s": data.get("by_domain_duration_s", {}),
                "visits": visits,
            })
        except Exception:  # noqa: BLE001 —— 浏览器数据失败不影响页面其他部分
            self._send_json({"date": date, "count": 0, "total_duration_s": 0,
                             "by_category_duration_s": {}, "by_domain_duration_s": {}, "visits": []})

    def _api_log(self, query: dict, root: str) -> None:
        """/api/log?n=200：统一运行日志 + 最近几天 errors.log（「日志」视图）。"""
        try:
            n = max(10, min(500, int(query.get("n", ["200"])[0])))
        except ValueError:
            n = 200
        try:
            import applog  # noqa: PLC0415
            entries = applog.read_recent(root, n)
            err_days = _available_days(root)[-3:]
            errors = applog.read_errors(root, err_days, n)
        except Exception:  # noqa: BLE001
            entries, errors = [], []
        self._send_json({"entries": entries, "errors": errors})

    def _api_groups(self, query: dict, root: str) -> None:
        """/api/groups：内置+自定义分组、全部已知应用及其当前分类。"""
        try:
            import classifier as _clf  # noqa: PLC0415
            config = _clf.load_config()
            config["data_root"] = root
            groups = _clf.load_app_groups(root)
            groups = _clf.sanitize_groups(config, groups)  # 剔除孤儿分组（如遗留的 AI工具）
            cats = _clf.all_categories(config, groups)
            known = _collect_known_apps(root)
            custom_names = groups.get("app_names", {})
            entries = []
            for exe, name in sorted(known.items(), key=lambda kv: kv[1].lower()):
                entries.append({
                    "exe": exe,
                    "app": custom_names.get(exe) or name,
                    "category": _clf.classify_category(exe, "", config),
                    "overridden": exe in groups["exe_groups"],
                })
            self._send_json({
                "exe_groups": groups["exe_groups"],
                "custom_categories": groups["custom_categories"],
                "app_names": groups.get("app_names", {}),
                "group_meta": groups.get("group_meta", {}),
                "categories": cats,
                "apps": entries,
            })
        except Exception:  # noqa: BLE001
            self._send_json({"error": "groups unavailable"}, 500)

    def _api_groups_export(self, query: dict, root: str) -> None:
        """/api/groups/export：导出应用分组配置（app_groups.json 完整内容）。"""
        try:
            import classifier as _clf  # noqa: PLC0415
            groups = _clf.load_app_groups(root)
            data = json.dumps(groups, ensure_ascii=False, indent=2).encode("utf-8")
            self._send_blob(data, "application/json; charset=utf-8", "app_groups.json")
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"export failed: {exc}"}, 500)

    # ------------------------------------------------------------------
    # POST 端点
    # ------------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        """POST 分发：同源/口令校验 → 二进制恢复分支 → JSON 体解析 → 路由表。"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        root = self.server.data_root
        # 同源校验（与 GET 一致）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return
        # 访问口令：所有 POST 一致校验
        if path.startswith("/api/") and not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return

        # 恢复上传：二进制 zip（Content-Type: application/octet-stream），先于 JSON 解析
        if path == "/api/backup/restore":
            self._api_backup_restore(root)
            return

        # 其余 POST 为 JSON 请求体
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            else:
                body = {}
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}

        # classifier 可用性预检（分组等 POST 端点依赖；失败统一 500，不在各端点重复处理）
        try:
            import classifier as _clf  # noqa: F401,PLC0415 —— 预检导入，成功即可用
        except Exception:  # noqa: BLE001
            self._send_json({"error": "unavailable"}, 500)
            return

        handler = _POST_ROUTES.get(path)
        if handler is None:
            self._send_json({"error": "method not allowed"}, 405)
            return
        handler(self, query, body, root)

    def _api_backup_restore(self, root: str) -> None:
        """/api/backup/restore：恢复上传（二进制 zip，防 zip-slip + 配置净化）。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > _RESTORE_MAX_BYTES:
            # 拒绝前先有界排空请求体并关闭连接：直接回 400 不读 body 的话，
            # keep-alive 会把残留字节当新请求解析，客户端读到 RST/ConnectionAborted。
            try:
                if length > 0:
                    self.rfile.read(min(length, 1 << 20))  # 有界排空（防超大 body 拖死）
            except Exception:  # noqa: BLE001 —— 排空失败也照常拒绝
                pass
            self.close_connection = True
            self._send_json({"error": "bad body"}, 400)
            return
        data = self.rfile.read(length)
        tmp_dir = None
        try:
            tmp_dir = _safe_extract_zip(root, data)
            result = _merge_restore(root, tmp_dir)
            self._send_json({"ok": True, "days": result["days"], "files": result["files"]})
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"restore failed: {exc}"}, 400)
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _api_insights_settings_save(self, query: dict, body: dict, root: str) -> None:
        """POST /api/insights/settings：AI 开关 + provider 预设 + 自定义端点（Key 空=保留）。"""
        enabled = bool(body.get("enabled"))
        provider = str(body.get("provider") or "").strip()
        base_url = str(body.get("base_url") or "").strip()
        model = str(body.get("model") or "").strip()
        try:
            import insights  # noqa: PLC0415
            custom = insights.load_ai_custom(root)
            preset_map = {p["id"]: p for p in
                          insights.list_provider_presets(custom.get("providers"))}
            preset = preset_map.get(provider.lower(), {})
            eff_base = base_url or preset.get("base_url") or ""
            eff_model = model or preset.get("model") or ""
            if enabled and (not eff_base or not eff_model):
                self._send_json({
                    "error": "开启 AI 需要可用的 Base URL 和 Model（请选择预设或填写自定义端点）",
                }, 400)
                return
            ai = _save_ai_settings(root, self.server.config_path, body)
            self._send_json({
                "ok": True, "ai": ai,
                "presets": insights.list_provider_presets(custom.get("providers")),
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"save failed: {exc}"}, 400)

    def _api_pricing_save(self, query: dict, body: dict, root: str) -> None:
        """POST /api/pricing：保存用户模型定价覆盖到 <root>/ai_pricing.json。"""
        data = body.get("pricing") if isinstance(body.get("pricing"), dict) else body
        clean: dict = {}
        for k, v in (data or {}).items():
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    clean[str(k)] = [float(v[0]), float(v[1])]
                except (TypeError, ValueError):
                    pass
            elif isinstance(v, dict) and "input" in v and "output" in v:
                try:
                    clean[str(k)] = {"input": float(v["input"]), "output": float(v["output"])}
                except (TypeError, ValueError):
                    pass
        try:
            fp = os.path.join(root, "ai_pricing.json")
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
            self._send_json({"ok": True, "count": len(clean)})
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"pricing save failed: {exc}"}, 400)

    def _api_ai_module_save(self, query: dict, body: dict, root: str) -> None:
        """POST /api/ai/module：保存 AI 洞察客制化模块（providers + prompt 定制）。"""
        if not (isinstance(body.get("providers"), list)
                or isinstance(body.get("prompt"), dict)):
            self._send_json({"error": "invalid ai module payload"}, 400)
            return
        try:
            import insights  # noqa: PLC0415
            custom = insights.save_ai_custom(root, body)
            self._send_json({
                "ok": True, "custom": custom,
                "sections": insights.PROMPT_SECTION_ITEMS,
                "presets": insights.list_provider_presets(custom.get("providers")),
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"save failed: {exc}"}, 400)

    def _api_ai_module_import(self, query: dict, body: dict, root: str) -> None:
        """POST /api/ai/module/import：导入客制化模块（{"custom":{...}} 或直接对象）。"""
        data = body.get("custom") if isinstance(body.get("custom"), dict) else body
        if not (isinstance(data, dict)
                and (isinstance(data.get("providers"), list)
                     or isinstance(data.get("prompt"), dict))):
            self._send_json({"error": "invalid ai module payload"}, 400)
            return
        try:
            import insights  # noqa: PLC0415
            custom = insights.save_ai_custom(root, data)
            self._send_json({
                "ok": True, "custom": custom,
                "sections": insights.PROMPT_SECTION_ITEMS,
                "presets": insights.list_provider_presets(custom.get("providers")),
            })
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"import failed: {exc}"}, 400)

    def _api_update_download(self, query: dict, body: dict, root: str) -> None:
        """POST /api/update/download：后台线程下载最新 exe（前端轮询 status）。"""
        config = _load_config_for_root(root, self.server.config_path)
        with _UPDATE_LOCK:
            if _UPDATE_STATE["state"] == "downloading":
                self._send_json({"error": "正在下载中，请稍候"}, 409)
                return
        try:
            import updater  # noqa: PLC0415
            result = updater.check_for_update(api_base=_update_api_base(config), timeout=8.0)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"检查更新失败：{exc}"}, 400)
            return
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return
        if not result.get("has_update") or not result.get("asset"):
            self._send_json({"error": "已是最新版本，无需下载"}, 400)
            return
        asset = result["asset"]
        dest_dir = os.path.join(tempfile.gettempdir(), "usagemonitor-update")
        dest = os.path.join(dest_dir, f"VibeTrace-{result['latest']}.exe")
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(state="downloading", downloaded=0, total=0,
                                 path=None, error=None, latest=str(result["latest"]))
        threading.Thread(target=_run_download, args=(asset, dest, _update_api_base(config)), daemon=True).start()
        self._send_json({"ok": True})

    def _api_update_apply(self, query: dict, body: dict, root: str) -> None:
        """POST /api/update/apply：写信号优雅退出 monitor，更新脚本替换 exe 并重启。"""
        # dryrun=true（仅测试/预览）只生成脚本不执行。
        dryrun = bool(body.get("dryrun"))
        with _UPDATE_LOCK:
            state = dict(_UPDATE_STATE)
        if state.get("state") != "ready" or not state.get("path"):
            self._send_json({"error": "没有已下载的更新（请先下载）"}, 400)
            return
        try:
            import updater  # noqa: PLC0415
            if not dryrun:
                updater.request_update(root)  # 通知 monitor 优雅退出
            result = updater.apply_update(state["path"], dry_run=dryrun)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"应用更新失败：{exc}"}, 400)
            return
        if not dryrun:
            with _UPDATE_LOCK:
                _UPDATE_STATE.update(state="applying")
            # 响应发出后关闭仪表盘服务（更新脚本会等待全部进程退出后替换 exe）
            threading.Timer(2.5, lambda: self.server.shutdown()).start()
        self._send_json({"ok": True, "dry_run": dryrun, "script": result.get("script", "")})

    def _api_groups_set(self, query: dict, body: dict, root: str) -> None:
        """POST /api/groups/set：设置/移出应用分组（category 空=移出，自动分类）。"""
        import classifier as _clf  # noqa: PLC0415
        exe = str(body.get("exe", "")).lower()
        cat = str(body.get("category", "")).strip()
        if not exe:
            self._send_json({"error": "exe required"}, 400)
            return
        groups = _clf.load_app_groups(root)
        if cat:
            groups["exe_groups"][exe] = cat
            # 未知分组自动登记为自定义分组
            if cat not in _clf.all_categories(_clf.load_config(), groups):
                groups["custom_categories"].append(cat)
        else:
            groups["exe_groups"].pop(exe, None)
        _clf.save_app_groups(groups, root)
        self._send_json({"ok": True})

    def _api_groups_rename(self, query: dict, body: dict, root: str) -> None:
        """POST /api/groups/rename：客制化显示名（display_name 空=恢复默认）。"""
        import classifier as _clf  # noqa: PLC0415
        exe = str(body.get("exe", "")).lower()
        display_name = str(body.get("display_name", "")).strip()
        if not exe:
            self._send_json({"error": "exe required"}, 400)
            return
        groups = _clf.load_app_groups(root)
        groups.setdefault("app_names", {})
        if display_name:
            groups["app_names"][exe] = display_name
        else:
            groups["app_names"].pop(exe, None)
        _clf.save_app_groups(groups, root)
        self._send_json({"ok": True, "app": display_name})

    def _api_groups_import(self, query: dict, body: dict, root: str) -> None:
        """POST /api/groups/import：导入分组配置（剔除孤儿分组保证自洽）。"""
        import classifier as _clf  # noqa: PLC0415
        data = body.get("groups") if isinstance(body.get("groups"), dict) else body
        if not isinstance(data, dict):
            self._send_json({"error": "invalid groups payload"}, 400)
            return
        groups = {
            "exe_groups": data.get("exe_groups", {}),
            "custom_categories": data.get("custom_categories", []),
            "app_names": data.get("app_names", {}),
            "group_meta": data.get("group_meta", {}),
        }
        # 导入时同样剔除孤儿分组，保证分组系统自洽
        config = _clf.load_config()
        config["data_root"] = root
        groups = _clf.sanitize_groups(config, groups)
        _clf.save_app_groups(groups, root)
        self._send_json({"ok": True, "groups": _clf.load_app_groups(root)})

    def _api_groups_add(self, query: dict, body: dict, root: str) -> None:
        """POST /api/groups/add：新增自定义分组。"""
        import classifier as _clf  # noqa: PLC0415
        name = str(body.get("name", "")).strip()
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        groups = _clf.load_app_groups(root)
        cats = _clf.all_categories(_clf.load_config(), groups)
        if name not in cats:
            groups["custom_categories"].append(name)
            _clf.save_app_groups(groups, root)
        self._send_json({"ok": True, "categories": _clf.all_categories(_clf.load_config(), groups)})

    def _api_groups_delete(self, query: dict, body: dict, root: str) -> None:
        """POST /api/groups/delete：删除自定义分组（含组内应用的覆盖关系）。"""
        import classifier as _clf  # noqa: PLC0415
        name = str(body.get("name", "")).strip()
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        groups = _clf.load_app_groups(root)
        groups["custom_categories"] = [x for x in groups["custom_categories"] if x != name]
        groups["exe_groups"] = {k: v for k, v in groups["exe_groups"].items() if v != name}
        _clf.save_app_groups(groups, root)
        self._send_json({"ok": True})

    def _api_insights_ai_refresh(self, query: dict, body: dict, root: str) -> None:
        """POST /api/insights/ai：强制重生成 AI 洞察（body {"refresh": true}）。

        仅 POST 允许触发付费生成：跨站伪造请求已被同源校验拦截，
        把成本型 CSRF 的触发面从 GET 收敛到显式动作。
        """
        date = self._valid_date(query)
        if not date:
            self._send_json({"error": "invalid date"}, 400)
            return
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import insights  # noqa: PLC0415
            ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
            ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
            ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
            if not ai_enabled:
                self._send_json({
                    "date": date, "ai_enabled": False,
                    "ai": {
                        "generated_at": None, "model": None, "insights": None,
                        "error": "AI 洞察未开启（config.json: insights.ai.enabled=false）",
                        "provider": str(ai_cfg.get("provider") or ""),
                    },
                })
                return
            refresh = bool(body.get("refresh")) or query.get("refresh", [""])[0] in ("1", "true", "yes")
            ai = insights.ai_insights(date, root, config, refresh=refresh)
            ai["provider"] = str(ai_cfg.get("provider") or "")
            self._send_json({"date": date, "ai_enabled": True, "ai": ai})
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"insights ai unavailable: {exc}"}, 500)

    def _api_goals(self, query: dict, root: str) -> None:
        """/api/goals?date=：每日目标进度 + streak（可选功能，关闭时返回空态）。

        date 缺省取今天；纯派生只读，不产生任何数据。
        """
        date = self._valid_date(query) or datetime.date.today().isoformat()
        config = _load_config_for_root(root, self.server.config_path)
        try:
            import goals  # noqa: PLC0415 —— 惰性导入
            self._send_json(goals.today_progress(date, root, config))
        except Exception as exc:  # noqa: BLE001 —— 目标失败不拖垮概览
            self._send_json({"error": f"goals unavailable: {exc}"}, 500)

    def _api_goals_settings_save(self, query: dict, body: dict, root: str) -> None:
        """POST /api/goals/settings：保存每日目标设置（enabled + 两类分钟数）。"""
        try:
            section = _save_goals_settings(root, self.server.config_path, body)
            self._send_json({"ok": True, "goals": section})
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"save failed: {exc}"}, 400)


# ---------------------------------------------------------------------------
# 路由表：路径 → Handler 方法（plain function，调用时显式传 self）。
# 新增端点：实现 _api_xxx 方法后在此登记即可，do_GET/do_POST 无需改动。
# ---------------------------------------------------------------------------
_GET_ROUTES: dict[str, Callable] = {
    "/favicon.ico": Handler._api_favicon,
    "/api/dates": Handler._api_dates,
    "/api/days": Handler._api_days,
    "/api/day": Handler._api_day,
    "/api/hourly": Handler._api_hourly,
    "/api/heatmap": Handler._api_heatmap,
    "/api/report": Handler._api_report,
    "/api/week": Handler._api_week,
    "/api/month": Handler._api_month,
    "/api/export": Handler._api_export,
    "/api/backup": Handler._api_backup,
    "/api/ai-sessions": Handler._api_ai_sessions,
    "/api/timeline": Handler._api_timeline,
    "/api/trend": Handler._api_trend,
    "/api/growth": Handler._api_trend,
    "/api/ai-compare": Handler._api_ai_compare,
    "/api/tool-compare": Handler._api_ai_compare,
    "/api/query": Handler._api_query,
    "/api/budget": Handler._api_budget,
    "/api/insights": Handler._api_insights,
    "/api/adoption": Handler._api_adoption,
    "/api/insights/settings": Handler._api_insights_settings,
    "/api/insights/ai": Handler._api_insights_ai,
    "/api/goals": Handler._api_goals,
    "/api/insights/ollama/models": Handler._api_ollama_models,
    "/api/ai/module": Handler._api_ai_module,
    "/api/ai/module/export": Handler._api_ai_module_export,
    "/api/pricing": Handler._api_pricing_get,
    "/api/update/check": Handler._api_update_check,
    "/api/update/status": Handler._api_update_status,
    "/api/urls": Handler._api_urls,
    "/api/log": Handler._api_log,
    "/api/groups": Handler._api_groups,
    "/api/groups/export": Handler._api_groups_export,
}

_POST_ROUTES: dict[str, Callable] = {
    "/api/insights/settings": Handler._api_insights_settings_save,
    "/api/insights/ai": Handler._api_insights_ai_refresh,
    "/api/goals/settings": Handler._api_goals_settings_save,
    "/api/pricing": Handler._api_pricing_save,
    "/api/ai/module": Handler._api_ai_module_save,
    "/api/ai/module/import": Handler._api_ai_module_import,
    "/api/update/download": Handler._api_update_download,
    "/api/update/apply": Handler._api_update_apply,
    "/api/groups/set": Handler._api_groups_set,
    "/api/groups/rename": Handler._api_groups_rename,
    "/api/groups/import": Handler._api_groups_import,
    "/api/groups/add": Handler._api_groups_add,
    "/api/groups/delete": Handler._api_groups_delete,
}



# ---------------------------------------------------------------------------
# 以下纯函数已外置到 dashboard_util.py（_available_days 及其 mtime/TTL 缓存、
# _days_cache_key/_days_mtime/invalidate_days_cache/_collect_known_apps/
# _sanitize_csv/_month_days_for/_agg_to_csv/_backup_zip/_backup_entries/
# _safe_extract_zip 及 _ALLOWED_ROOT_FILES/_EXCLUDED_FILE_SUFFIXES）。
# 本文件经顶部 `from dashboard_util import ...` 引用；行为与原实现一致。
# ---------------------------------------------------------------------------


def create_server(data_root: str, port: int = DEFAULT_PORT,
                  config_path: str | None = None) -> ThreadingHTTPServer:
    """创建仪表盘服务器（绑定 127.0.0.1）。

    config_path 可指定 config.json 路径（测试用）；缺省由 _required_token 用默认路径。
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.data_root = data_root
    server.config_path = config_path
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard.py", description="本地网页仪表盘（仅 127.0.0.1）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径（默认取 data_root/config.json）")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config(args.config)
        data_root = args.data_root or (cfg.get("data_root") or DEFAULT_DATA_ROOT)
    except Exception:  # noqa: BLE001
        data_root = args.data_root or DEFAULT_DATA_ROOT

    server = create_server(data_root, args.port, config_path=args.config)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[dashboard] 数据目录: {data_root}")
    base_domain = url.rstrip("/")
    if bool(_required_token(args.config)):
        print("[dashboard] 访问口令：已开启（config.json 的 dashboard_token）")
    else:
        print("[dashboard] 访问口令：关闭")
    print(f"[dashboard] 仪表盘已启动: {url}  （Ctrl+C 退出，可带 /?view=week|month|settings）")
    try:
        import applog  # noqa: PLC0415
        applog.configure(data_root)
        applog.get_logger("dashboard").info("仪表盘启动 %s (data_root=%s)", url, data_root)
    except Exception:  # noqa: BLE001
        pass
    del base_domain
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] 已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
