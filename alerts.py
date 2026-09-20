# -*- coding: utf-8 -*-
"""alerts.py — 告警调度（v2.7「行动闭环」）。

把 budget（成本预算）与连续工作时长变成**主动提醒**：独立守护线程周期检查，
命中条件时经托盘气泡通知（复用 tray.show_balloon，Win10/11 自动转 Toast 通知）。

三类告警：
1. 预算接近（ratio ≥ 80%，budget.status == "warn"）
2. 预算超支（ratio ≥ 100%，budget.status == "exceed"）
3. 连续工作休息提醒（持续活跃 ≥ rest_after_min 分钟且期间无足够空闲）

设计要点：
- 纯函数 evaluate_alerts / update_work 承载全部判定逻辑，便于无头测试；
- run_alert_loop 只做调度与注入依赖（notify/idle/budget/paused 均可注入），
  不反向依赖 monitor（避免循环导入），暂停状态由调用方传入 paused_fn；
- 预算检查内部会扫描 AI 会话文件（较重），按 budget_check_min 限频；
- 告警去重：rest 类按 cooldown_min 冷却；budget 类按「日期+等级」每日至多一次。

配置段（config.json 的 "alerts"，缺省用代码内默认值，支持热重载）：
    "alerts": {
        "enabled": true,
        "check_interval_s": 60,
        "budget_check_min": 15,
        "budget_warn": true,
        "budget_exceed": true,
        "rest_reminder": true,
        "rest_after_min": 120,
        "idle_reset_s": 300,
        "cooldown_min": 60
    }
"""

from __future__ import annotations
import applog  # v2.9.8：降级观测出口（applog.note）

import datetime
import sys
import time
from collections.abc import Callable


# ---------------------------------------------------------------------------
# 配置归一化
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "enabled": True,
    "check_interval_s": 60,     # 轮询间隔（秒）
    "budget_check_min": 15,     # 预算状态刷新限频（分钟；内部扫 AI 会话文件较重）
    "budget_warn": True,        # 预算接近气泡开关
    "budget_exceed": True,      # 预算超支气泡开关
    "rest_reminder": True,      # 连续工作休息提醒开关
    "rest_after_min": 120,      # 连续活跃多久后提醒（分钟）
    "idle_reset_s": 300,        # 空闲超过该秒数视为已休息，累计清零
    "cooldown_min": 60,         # rest 类告警最小重复间隔（分钟）
}
# 数值下限（防止配置手滑导致忙轮询或永不提醒）
_CLAMPS = {
    "check_interval_s": (10, 3600),
    "budget_check_min": (1, 720),
    "rest_after_min": (5, 1440),
    "idle_reset_s": (30, 7200),
    "cooldown_min": (1, 1440),
}


def alerts_config(config: dict | None) -> dict:
    """归一化 alerts 配置段：缺省回退默认值、非法值修正、数值夹取。"""
    raw = config.get("alerts") if isinstance(config, dict) and isinstance(config.get("alerts"), dict) else {}
    out = dict(_DEFAULTS)
    for key, default in _DEFAULTS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, bool):
            out[key] = bool(value)
        else:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue  # 非法数值保持默认
    for key, (lo, hi) in _CLAMPS.items():
        out[key] = max(lo, min(hi, int(out[key])))
    return out


# ---------------------------------------------------------------------------
# 状态与纯函数判定
# ---------------------------------------------------------------------------
class AlertState:
    """告警调度器的可变状态（工作时长累计 + 已触发记录）。"""

    def __init__(self) -> None:
        self.work_seconds: float = 0.0          # 连续活跃累计秒数（空闲达标即清零）
        self.fired: dict[str, float] = {}       # key -> 最近触发时刻（monotonic）
        self.last_tick: float = 0.0             # 上次累计时刻（首跳不累计）
        self.last_budget_check: float = 0.0     # 上次预算刷新时刻（限频）

    def reset(self) -> None:
        self.work_seconds = 0.0
        self.fired.clear()
        self.last_tick = 0.0
        self.last_budget_check = 0.0


def update_work(state: AlertState, idle_seconds: float, dt_seconds: float, cfg: dict) -> float:
    """按本轮空闲情况更新连续工作累计：空闲达标清零，否则累加。

    返回更新后的 work_seconds。
    """
    if idle_seconds >= cfg["idle_reset_s"]:
        state.work_seconds = 0.0
    else:
        state.work_seconds += max(0.0, float(dt_seconds))
    return state.work_seconds


def _fmt_hours_cn(seconds: float) -> str:
    """秒 → 中文可读时长（1 小时 25 分钟）。"""
    total_min = max(0, int(round(seconds / 60)))
    h, m = divmod(total_min, 60)
    if h <= 0:
        return f"{m} 分钟"
    if m == 0:
        return f"{h} 小时"
    return f"{h} 小时 {m} 分钟"


def evaluate_alerts(cfg: dict, state: AlertState, now: float,
                    budget_st: dict | None = None, paused: bool = False) -> list[dict]:
    """根据当前状态判定应触发的告警（纯决策，不发通知）。

    返回 [{key, title, text}]；副作用仅限在 state.fired 记录触发时间。
    - 总开关关闭或暂停中 → 恒返回空；
    - rest：累计达阈值且冷却期已过 → 触发；
    - budget：等级为 warn/exceed 且对应开关开启、当日该等级未触发过 → 触发
      （按 start 日期入键，跨天自动重新武装）。
    """
    out: list[dict] = []
    if not cfg.get("enabled") or paused:
        return out

    # 1) 连续工作休息提醒
    if cfg.get("rest_reminder") and state.work_seconds >= cfg["rest_after_min"] * 60:
        cooldown_s = cfg["cooldown_min"] * 60
        last = state.fired.get("rest")
        if last is None or now - last >= cooldown_s:
            state.fired["rest"] = now
            out.append({
                "key": "rest",
                "title": "该休息一下了",
                "text": f"已连续工作 {_fmt_hours_cn(state.work_seconds)}，起来活动一下吧",
            })

    # 2) 预算告警（warn / exceed 各自每日至多一次）
    if isinstance(budget_st, dict) and budget_st.get("enabled"):
        level = budget_st.get("status")
        if level in ("warn", "exceed") and cfg.get(f"budget_{level}"):
            day_key = f"budget_{level}_{budget_st.get('start') or ''}"
            if state.fired.get(day_key) is None:
                state.fired[day_key] = now
                spent = float(budget_st.get("spent") or 0.0)
                amount = float(budget_st.get("budget") or 0.0)
                ratio = int(round(float(budget_st.get("ratio") or 0.0) * 100))
                label = "今日 AI 成本" if budget_st.get("period") == "daily" else "本月 AI 成本"
                title = "AI 成本接近预算" if level == "warn" else "AI 成本已超预算"
                out.append({
                    "key": f"budget_{level}",
                    "title": title,
                    "text": f"{label} {spent:.2f}/{amount:.2f} USD（{ratio}%），可在仪表盘查看明细",
                })
    return out


# ---------------------------------------------------------------------------
# 调度线程
# ---------------------------------------------------------------------------
class _Ctx:
    """调度线程的依赖注入容器（便于测试替换通知/空闲/预算/暂停来源）。"""

    def __init__(self, data_root: str, config_path: str | None = None,
                 notify_fn: Callable[[str, str], None] | None = None,
                 idle_fn: Callable[[], float] | None = None,
                 budget_fn: Callable[[str, str, dict], dict] | None = None,
                 paused_fn: Callable[[], bool] | None = None) -> None:
        self.data_root = data_root
        self.config_path = config_path
        self._notify_fn = notify_fn
        self._idle_fn = idle_fn
        self._budget_fn = budget_fn
        self._paused_fn = paused_fn

    def load_config(self) -> dict:
        """读完整配置（classifier 自带 TTL 缓存，热重载 ~5s 生效）。"""
        import classifier  # noqa: PLC0415
        return classifier.load_config(self.config_path)

    def notify(self, title: str, text: str) -> None:
        if self._notify_fn is not None:
            self._notify_fn(title, text)
            return
        import tray  # noqa: PLC0415 —— 图标未就绪时 show_balloon 内部静默跳过
        tray.show_balloon(title, text)

    def idle_seconds(self) -> float:
        if self._idle_fn is not None:
            return float(self._idle_fn())
        import win32core  # noqa: PLC0415
        return float(win32core.idle_seconds())

    def budget_status(self, date: str, root: str, config: dict) -> dict:
        if self._budget_fn is not None:
            return self._budget_fn(date, root, config)
        import budget  # noqa: PLC0415
        return budget.budget_status(date, root, config)

    def paused(self) -> bool:
        if self._paused_fn is not None:
            return bool(self._paused_fn())
        return False


def _tick(ctx: _Ctx, state: AlertState, now: float | None = None) -> dict:
    """单次检查：累计工作时长 → 必要时刷新预算状态 → 评估并通知。

    返回本次使用的配置（供主循环决定等待间隔）。
    """
    now = time.monotonic() if now is None else now
    config = ctx.load_config()
    cfg = alerts_config(config)

    if not cfg["enabled"] or ctx.paused():
        # 关闭/暂停时不累计、不评估（保留 fired 历史，恢复后冷却照常生效）
        return cfg

    # 工作时长累计：dt 以真实时钟差为准；首跳（last_tick==0）只对齐时刻不累计
    dt = 0.0 if state.last_tick <= 0.0 else max(0.0, now - state.last_tick)
    update_work(state, ctx.idle_seconds(), dt, cfg)
    state.last_tick = now

    # 预算状态限频刷新（内部扫 AI 会话文件，代价较高）
    budget_st = None
    need_budget = cfg["budget_warn"] or cfg["budget_exceed"]
    if need_budget:
        if now - state.last_budget_check >= cfg["budget_check_min"] * 60:
            today = datetime.date.today().isoformat()
            try:
                budget_st = ctx.budget_status(today, ctx.data_root, config)
                state.last_budget_check = now
            except Exception:  # noqa: BLE001 —— 预算失败不影响 rest 提醒
                budget_st = None

    for alert in evaluate_alerts(cfg, state, now, budget_st, paused=False):
        try:
            ctx.notify(alert["title"], alert["text"])
        except Exception as _exc:  # noqa: BLE001 —— 单条通知失败不中断调度
            applog.note(_exc, "alerts: 告警配置加载失败，本轮跳过")
    return cfg


def run_alert_loop(stop_event, data_root: str, config_path: str | None = None,
                   **injects) -> None:
    """告警调度线程主循环（monitor 启动，daemon=True）。

    注入参数见 _Ctx；未提供时使用托盘气泡 / Win32 空闲 / budget 模块默认实现。
    """
    ctx = _Ctx(data_root, config_path, **injects)
    state = AlertState()
    while not stop_event.is_set():
        try:
            cfg = _tick(ctx, state)
        except Exception:  # noqa: BLE001 —— 单次检查失败不中断调度
            try:
                import applog  # noqa: PLC0415
                applog.get_logger("alerts").error("alert tick failed: %s",
                                                  sys.exc_info()[1])
            except Exception as _exc:  # noqa: BLE001
                applog.note(_exc, "alerts: 告警调度执行失败，本轮跳过")
            cfg = alerts_config(ctx.load_config()) if ctx else {}
        interval = max(10, int(cfg.get("check_interval_s", 60))) if cfg else 60
        stop_event.wait(interval)
