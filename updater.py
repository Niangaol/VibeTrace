# -*- coding: utf-8 -*-
"""updater.py — 新版本检测与应用内更新（纯标准库，零第三方依赖）。

检测源：GitHub Releases API（默认 latest release），可用 config.json 的
`update.api_base` 覆盖（测试 / 镜像用）。

更新流程（配合 monitor / dashboard）：
1. monitor 启动后后台检查一次，有新版本时托盘气泡提示；
   仪表盘「设置 → 软件更新」也可手动检查 / 下载 / 安装。
2. dashboard 下载新 exe 到 %TEMP%\\usagemonitor-update\\ 并校验
   （Content-Length 大小 + GitHub 提供的 SHA256 digest，digest 缺失时仅校验大小）。
3. 应用更新：先写 <data_root>/.update-requested 信号文件，monitor 主循环发现后
   优雅退出；再启动 PowerShell 更新脚本——等待所有 VibeTrace.exe 退出（60s
   兜底强杀）→ 替换目标 exe → 重启 → 自删脚本与临时文件。

CLI：python updater.py --check [--api-base ...] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse

import applog  # noqa: E402
import version  # noqa: E402

GITHUB_LATEST_URL = "https://api.github.com/repos/Niangaol/VibeTrace/releases/latest"
ASSET_NAME = "VibeTrace.exe"
UPDATE_REQUEST_FILE = ".update-requested"
_UA = f"VibeTrace/{version.VERSION}"


class UpdateError(RuntimeError):
    """更新相关失败（中文可读信息）。"""


def is_frozen() -> bool:
    """是否打包 exe 运行（应用内更新仅打包版支持）。"""
    return bool(getattr(sys, "frozen", False))


def parse_version(text: str) -> tuple[int, ...] | None:
    """从任意字符串提取主版本号（如 v1.10.2-beta -> (1,10,2)）。"""
    match = re.search(r"(\d+(?:\.\d+)*)", str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def version_gt(a: str, b: str) -> bool:
    """a 版本是否严格高于 b（数值比较，忽略预发布后缀）。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return False
    width = max(len(pa), len(pb))
    pa += (0,) * (width - len(pa))
    pb += (0,) * (width - len(pb))
    return pa > pb


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------
def _is_allowed_asset_url(url: str, api_base: str | None = None) -> bool:
    """校验更新资产下载地址是否允许。

    默认只接受 GitHub 官方下载域名；配置 `update.api_base` 时也允许该域名
    （测试/镜像用），并限制为 http/https。
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.hostname in (
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    ):
        return True
    if api_base:
        try:
            base_host = urlparse(str(api_base).strip()).hostname
        except ValueError:
            base_host = None
        if base_host and parsed.hostname == base_host:
            return True
    return False


def latest_release(api_base: str | None = None, timeout: float = 8.0) -> dict:
    """GET 最新 Release 元数据（含匹配 ASSET_NAME 的资产信息）。

    失败抛 UpdateError（中文可读信息）；assets 中无 VibeTrace.exe 时
    asset 为 None（调用方视为“无可用更新”）。
    """
    url = str(api_base or GITHUB_LATEST_URL).strip().rstrip("/")
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=max(3.0, timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"检查更新失败：服务器返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"无法连接更新服务器：{exc.reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("检查更新超时，请稍后重试") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError("更新服务器返回的不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("更新服务器返回的数据格式不正确")

    asset = None
    for item in (payload.get("assets") or []):
        if isinstance(item, dict) and str(item.get("name") or "") in (ASSET_NAME, "UsageMonitor.exe"):
            candidate = {
                "name": ASSET_NAME,
                "size": int(item.get("size") or 0),
                "digest": str(item.get("digest") or "").strip(),
                "url": str(item.get("browser_download_url") or "").strip(),
            }
            if _is_allowed_asset_url(candidate["url"], api_base):
                asset = candidate
            break
    return {
        "tag": str(payload.get("tag_name") or ""),
        "name": str(payload.get("name") or payload.get("tag_name") or ""),
        "url": str(payload.get("html_url") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "notes": str(payload.get("body") or ""),
        "asset": asset,
    }


def check_for_update(current: str | None = None, api_base: str | None = None,
                     timeout: float = 8.0) -> dict:
    """检查是否有新版本。

    返回 {current, latest, has_update, notes, published_at, url, asset, error}；
    网络/解析失败时 error 为中文描述，has_update=False（不抛异常，便于调用方直接展示）。
    """
    current = str(current or version.VERSION).strip()
    try:
        info = latest_release(api_base, timeout)
    except UpdateError as exc:
        return {"current": current, "latest": "", "has_update": False,
                "notes": "", "published_at": "", "url": "", "asset": None,
                "error": str(exc)}
    # tag 通常形如 "v1.7.0"，展示层统一拼 "v" 前缀，这里去掉避免 "vv1.7.0"
    latest = str(info["tag"]).strip().lstrip("v")
    has_update = bool(latest) and bool(info["asset"]) and version_gt(latest, current)
    return {
        "current": current,
        "latest": latest,
        "has_update": has_update,
        "notes": info["notes"],
        "published_at": info["published_at"],
        "url": info["url"],
        "asset": info["asset"],
        "error": None,
    }


# ---------------------------------------------------------------------------
# 下载 / 校验
# ---------------------------------------------------------------------------
def download(url: str, dest: str, expected_size: int | None = None,
             expected_digest: str | None = None, progress=None,
             timeout: float = 120.0, chunk: int = 65536,
             api_base: str | None = None) -> str:
    """流式下载到 dest（先写 .part 再原子替换），可选校验大小与 SHA256。

    progress(got, total) 回调（total 可能为 None）；失败抛 UpdateError 并清理残留。
    下载前复核 URL 白名单（纵深防御：不依赖调用方先经 latest_release 过滤），
    api_base 与 latest_release 的语义一致（config.json 的 update.api_base）。
    """
    if not url:
        raise UpdateError("下载地址为空")
    if not _is_allowed_asset_url(url, api_base):
        raise UpdateError("下载地址不在允许的域名白名单内，已中止更新")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    got = 0
    total: int | None = None
    sha = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=max(10.0, timeout)) as resp:
            try:
                total = int(resp.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                total = None
            with open(part, "wb") as fh:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    fh.write(data)
                    got += len(data)
                    sha.update(data)
                    if progress is not None:
                        try:
                            progress(got, total)
                        except Exception as _exc:  # noqa: BLE001 —— 进度回调失败不影响下载
                            applog.note(_exc, "updater: 下载进度回调失败，忽略本次回调")
    except urllib.error.HTTPError as exc:
        _cleanup(part)
        raise UpdateError(f"下载失败：服务器返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        _cleanup(part)
        raise UpdateError(f"下载失败：{exc.reason}") from exc
    except TimeoutError as exc:
        _cleanup(part)
        raise UpdateError("下载超时，请检查网络后重试") from exc
    except OSError as exc:
        _cleanup(part)
        raise UpdateError(f"写入下载文件失败：{exc}") from exc

    if expected_size is not None and got != expected_size:
        _cleanup(part)
        raise UpdateError(f"下载文件大小不符（{got} 字节 ≠ 预期 {expected_size} 字节）")
    if expected_digest:
        actual = "sha256:" + sha.hexdigest()
        if actual.lower() != str(expected_digest).strip().lower():
            _cleanup(part)
            raise UpdateError("下载文件校验失败（SHA256 不匹配），已中止更新")
    os.replace(part, dest)
    return dest


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 应用更新（信号 + 更新脚本）
# ---------------------------------------------------------------------------
def update_request_path(data_root: str) -> str:
    """monitor 优雅退出的信号文件路径。"""
    return os.path.join(data_root or ".", UPDATE_REQUEST_FILE)


def request_update(data_root: str) -> None:
    """写更新信号：monitor 主循环发现后优雅退出（停止写记录）。"""
    try:
        with open(update_request_path(data_root), "w", encoding="utf-8") as fh:
            fh.write("1")
    except OSError:
        pass


def clear_update_request(data_root: str) -> None:
    """清除更新信号（更新脚本启动新实例前调用）。"""
    try:
        os.remove(update_request_path(data_root))
    except OSError:
        pass


def build_update_script(src: str, dst: str, process_name: str = "VibeTrace") -> str:
    """生成 PowerShell 更新脚本内容。

    等待所有 <process_name> 进程退出（60s 超时后强杀兜底）→ 替换 exe →
    重启 → 清理信号文件 / 临时文件 / 脚本自身。编码 UTF-8 with BOM。
    process_name 供测试注入（生产恒为 VibeTrace）。
    """
    def q(path: str) -> str:
        return str(path).replace("'", "''")

    return (
        "# VibeTrace 应用内更新脚本（自动生成，完成后自删）\r\n"
        "$ErrorActionPreference = 'SilentlyContinue'\r\n"
        f"$src = '{q(src)}'\r\n"
        f"$dst = '{q(dst)}'\r\n"
        "# 等待所有 VibeTrace 进程退出（最长 60 秒）\r\n"
        "$deadline = (Get-Date).AddSeconds(60)\r\n"
        "while ((Get-Date) -lt $deadline) {\r\n"
        f"    $running = @(Get-Process -Name '{q(process_name)}' -ErrorAction SilentlyContinue)\r\n"
        "    if ($running.Count -eq 0) { break }\r\n"
        "    Start-Sleep -Milliseconds 500\r\n"
        "}\r\n"
        "# 超时兜底：强制结束残留进程\r\n"
        f"$left = @(Get-Process -Name '{q(process_name)}' -ErrorAction SilentlyContinue)\r\n"
        "if ($left.Count -gt 0) { Stop-Process -Name '" + q(process_name) + "' -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800 }\r\n"
        "if (Test-Path $src) {\r\n"
        "    Copy-Item -Force $src $dst\r\n"
        "}\r\n"
        "if (Test-Path $dst) {\r\n"
        "    Start-Process -FilePath $dst -WorkingDirectory (Split-Path -Parent $dst)\r\n"
        "}\r\n"
        "# 清理信号文件 / 临时下载 / 脚本自身\r\n"
        "Remove-Item -Force $src -ErrorAction SilentlyContinue\r\n"
        "Remove-Item -Force $PSCommandPath -ErrorAction SilentlyContinue\r\n"
    )


def apply_update(src: str, dst: str | None = None, dry_run: bool = False) -> dict:
    """生成并启动更新脚本（dry_run=True 只生成不执行，供测试/预览）。

    非打包环境（开发模式）默认拒绝；dry_run 用于界面预览与测试。
    """
    if not is_frozen() and not dry_run:
        raise UpdateError("当前是开发模式（非打包 exe），不支持应用内更新")
    dst = dst or sys.executable
    if not os.path.isfile(src):
        raise UpdateError(f"更新文件不存在：{src}")
    if not os.path.isfile(dst):
        raise UpdateError(f"目标程序不存在：{dst}")
    script = build_update_script(src, dst)
    script_path = os.path.join(tempfile.gettempdir(), f"usagemonitor-update-{os.getpid()}.ps1")
    with open(script_path, "w", encoding="utf-8-sig") as fh:
        fh.write(script)
    if dry_run:
        return {"script": script_path, "dry_run": True}
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True, stdin=None, stdout=None, stderr=None,
        )
    except OSError as exc:
        raise UpdateError(f"无法启动更新脚本：{exc}") from exc
    return {"script": script_path, "dry_run": False}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="updater.py", description="新版本检测与应用内更新")
    parser.add_argument("--check", action="store_true", help="检查最新版本")
    parser.add_argument("--api-base", default=None, help="Release API 地址（默认 GitHub）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if args.check:
        result = check_for_update(api_base=args.api_base)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["error"]:
                print(f"检查失败：{result['error']}")
                return 1
            if result["has_update"]:
                print(f"发现新版本：{result['latest']}（当前 {result['current']}）")
                print(f"发布时间：{result['published_at']}")
            else:
                print(f"已是最新版本：{result['current']}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
