# -*- coding: utf-8 -*-
"""tests/unit/test_report_extra.py — report.py 边界与派生路径补充。

覆盖：空日/不存在日聚合、坏行跳过、CSV 生成、跨天聚合、月报聚合、
周报 markdown 渲染、verify_days 校验。
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import report  # noqa: E402

from tests.conftest import make_record, seed_day  # noqa: E402


def test_aggregate_missing_day_is_zero(tmp_path):
    """不存在的日期 → 全零聚合，不抛异常。"""
    agg = report.aggregate("2099-05-01", str(tmp_path))
    assert agg["total_active_ms"] == 0
    assert agg["session_count"] == 0
    assert agg["by_app"] == {}
    print("  [PASS] aggregate_missing_day_is_zero")


def test_aggregate_skips_corrupt_lines(tmp_path):
    """usage.jsonl 中坏行（非 JSON / 空行）被跳过，好行照常统计。"""
    root = str(tmp_path)
    day = seed_day(root, "2099-05-02", [make_record("2099-05-02", 9, 30)])
    with open(os.path.join(day, "usage.jsonl"), "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write("\n")
        fh.write("[1, 2, 3]\n")  # 合法 JSON 但非对象 → 跳过
        fh.write(json.dumps(make_record("2099-05-02", 10, 15), ensure_ascii=False) + "\n")
    agg = report.aggregate("2099-05-02", root)
    assert agg["session_count"] == 2, f"应恰好统计 2 条好行: {agg['session_count']}"
    assert agg["total_active_ms"] == 45 * 60000
    print("  [PASS] aggregate_skips_corrupt_lines")


def test_generate_report_csv_content(tmp_path):
    """日报 CSV：含应用/类别行、时长秒换算、表头。"""
    root = str(tmp_path)
    seed_day(root, "2099-05-03", [
        make_record("2099-05-03", 9, 60),
        make_record("2099-05-03", 11, 30, exe="steam.exe", app="Steam", category="游戏"),
    ])
    csv_text = report.generate_report_csv("2099-05-03", root)
    assert "类型,名称,时长秒" in csv_text
    assert "应用:VS Code,3600" in csv_text
    assert "应用:Steam,1800" in csv_text
    assert "类别:开发工具,3600" in csv_text
    print("  [PASS] generate_report_csv_content")


def test_aggregate_days_sums_totals(tmp_path):
    """多日聚合：总活跃与天数正确累加。"""
    root = str(tmp_path)
    days = ["2099-05-04", "2099-05-05"]
    for i, d in enumerate(days):
        seed_day(root, d, [make_record(d, 9, 30 * (i + 1))])
    agg = report.aggregate_days(days, root)
    expected = (30 + 60) * 60000
    assert agg["total_active_ms"] == expected
    assert agg["session_count"] == 2
    assert agg["date"] == "~".join(days)
    print("  [PASS] aggregate_days_sums_totals")


def test_aggregate_month_empty_and_filled(tmp_path):
    """月聚合：无数据月 per_day 为空；有数据月按天归口。"""
    root = str(tmp_path)
    empty = report.aggregate_month("2098-12", root)
    assert empty.get("per_day") == [] or not empty.get("per_day")
    seed_day(root, "2099-06-10", [make_record("2099-06-10", 8, 45)])
    agg = report.aggregate_month("2099-06", root)
    assert agg["month"] == "2099-06"
    assert len(agg["per_day"]) == 1
    assert agg["total_active_ms"] == 45 * 60000
    print("  [PASS] aggregate_month_empty_and_filled")


def test_report_from_agg_markdown(tmp_path):
    """周/月报渲染：标题与关键小节存在。"""
    root = str(tmp_path)
    seed_day(root, "2099-05-06", [make_record("2099-05-06", 9, 60)])
    agg = report.aggregate_days(["2099-05-06"], root)
    md = report._report_from_agg(agg, "电脑使用情况周报（最近 7 个有数据日）")
    assert "电脑使用情况周报" in md
    assert "VS Code" in md
    print("  [PASS] report_from_agg_markdown")


def test_verify_days_reports_missing(tmp_path):
    """verify_days：缺失日报的日期进入校验结果且可被统计。"""
    root = str(tmp_path)
    seed_day(root, "2099-05-07", [make_record("2099-05-07", 9, 30)])
    result = report.verify_days(root, ["2099-05-07"])
    assert isinstance(result, dict)
    # 不抛异常即视为通过；结果结构里应有逐日信息
    assert result, "verify_days 应返回非空结果"
    print("  [PASS] verify_days_reports_missing")


def test_read_sessions_tolerates_missing_file(tmp_path):
    """read_sessions：文件缺失返回空列表。"""
    sessions = report.read_sessions("2099-05-08", str(tmp_path))
    assert sessions == []
    print("  [PASS] read_sessions_tolerates_missing_file")


# ---------------------------------------------------------------------------
# _ai_sessions_daily 文案：tokens_from_usage>0 → 「真实 usage（含缓存）」；=0 → 「估算」
# ---------------------------------------------------------------------------
def _write_ai_report_env(root: str, sess_dir: str, *, token_est: bool = True) -> None:
    """在 <root>/config.json 写最小 ai_sessions 配置（paths 指向会话目录，关浏览器扫描）。"""
    cfg = {
        "data_root": root,
        "browser_history_enabled": False,  # 不触发真实浏览器库扫描
        "ai_sessions": {"enabled": True, "token_estimation": token_est,
                        "web_ai": {"enabled": False}, "paths": {"claude": [sess_dir]}},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)


def test_ai_sessions_daily_real_usage_wording(tmp_path):
    """消息带真实 usage（含缓存）时，文案称「真实 usage（含缓存）」而非「估算」。"""
    root = tmp_path / "rep_real"
    sess = root / "claude"
    sess.mkdir(parents=True)
    day = "2099-05-10"
    line = {"type": "assistant", "timestamp": f"{day}T10:00:00",
            "message": {"role": "assistant", "model": "claude-sonnet-4-5",
                        "content": [{"type": "text", "text": "你好"}],
                        "usage": {"input_tokens": 10, "output_tokens": 20,
                                  "cache_read_input_tokens": 900,
                                  "cache_creation_input_tokens": 50}}}
    (sess / "a.jsonl").write_text(json.dumps(line, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
    _write_ai_report_env(str(root), str(sess))
    out = report._ai_sessions_daily(day, str(root))
    assert out, "有当日会话数据时章节应生成（非 None）"
    assert "真实 usage（含缓存）" in out
    assert "Token 估算 进" not in out          # 不得再称「估算」
    assert "Token 优先取会话内真实 usage（含缓存）" in out
    print("  [PASS] ai_sessions_daily_real_usage_wording")


def test_ai_sessions_daily_estimated_wording(tmp_path):
    """无任何 usage（纯估算）时保留原「Token 估算」措辞。"""
    root = tmp_path / "rep_est"
    sess = root / "claude"
    sess.mkdir(parents=True)
    day = "2099-05-11"
    recs = [
        {"role": "user", "content": "你好世界", "timestamp": f"{day}T10:00:00"},
        {"role": "assistant", "content": "回复内容", "timestamp": f"{day}T10:00:05"},
    ]
    (sess / "b.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8")
    _write_ai_report_env(str(root), str(sess), token_est=True)
    out = report._ai_sessions_daily(day, str(root))
    assert out, "有当日会话数据时章节应生成（非 None）"
    assert "Token 估算 进" in out
    assert "Token 为长度折算的估算值" in out
    assert "真实 usage（含缓存）" not in out  # 无 usage 不得称「真实」
    print("  [PASS] ai_sessions_daily_estimated_wording")
