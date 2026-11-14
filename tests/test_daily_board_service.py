# -*- coding: utf-8 -*-
"""A1-a 只读总览投影的合同测试。

覆盖方案 §8.1 中与首版直接相关的反例：对象闸门、来源降级透传、
缺口不冒充无机会、UTC 时区、多来源不合并、重跑结果稳定。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from src.services.daily_board_service import (
    Candidate,
    SourceHealth,
    build_board,
    is_security_object,
    render_markdown,
    utc_naive_to_local,
    _render_candidate_table,
)


def _row(signal_type: str, code: str, name: str, metrics: dict | None = None) -> SimpleNamespace:
    metrics = dict(metrics or {})
    if signal_type.startswith("screening__"):
        metrics.setdefault("run_id", f"test-{signal_type}")
    return SimpleNamespace(
        signal_type=signal_type,
        code=code,
        name=name,
        metrics_payload=json.dumps(metrics or {}),
        criteria_payload="{}",
        updated_at=datetime(2026, 11, 6, 18, 17, 50) if signal_type.startswith("screening__") else datetime(2026, 11, 6, 19, 20),
    )


def _ok_health() -> list[SourceHealth]:
    return [
        SourceHealth("matrix", "个人策略矩阵", "ok", "done 记录完好", "2026-11-06 19:52:08", 0, "2026-11-06 19:05:00", "matrix-test"),
        SourceHealth("screening", "筛选预设（三批）", "ok", "done 记录完好", "2026-11-06 18:18:12", 0, "2026-11-06 18:10:00", "screening-test"),
    ]


def _build(rows, audits=None, health=None, failed=None, rate=None, last_dates=None):
    if audits is None:
        sources = {r.signal_type for r in rows if r.signal_type.startswith("screening__")}
        audits = [dict(
            strategy=s.split("__", 1)[-1], as_of="2026-11-06", run_id=f"test-{s}",
            generated_at="2026-11-06 18:18:00", top_n=20,
            candidates=[{"code": r.code} for r in rows if r.signal_type == s],
        ) for s in sources]
    return build_board(
        "2026-11-06",
        signal_rows=rows,
        screening_audits=audits or [],
        health=health if health is not None else _ok_health(),
        rebuild_failed_codes=set(failed or ()),
        rebuild_failure_rate=rate,
        market_regime="defensive",
        last_bar_dates=last_dates,
    )


# ---------------------------------------------------------------- 对象闸门


def test_run_summary_is_not_a_security() -> None:
    assert is_security_object("TL_SUMMARY", {"is_run_summary": True}) is False
    assert is_security_object("TL_SUMMARY", {}) is False


def test_non_six_digit_code_is_not_a_security() -> None:
    assert is_security_object("600519", {}) is True
    assert is_security_object("sh600519", {}) is False
    assert is_security_object("", {}) is False
    assert is_security_object("920001", {"asset_type": "index"}) is False


def test_summary_row_goes_to_diagnostics_not_candidates() -> None:
    rows = [
        _row("trend_leader_unified", "TL_SUMMARY", "趋势龙头运行摘要",
             {"is_run_summary": True, "run_status": "completed_no_hits",
              "strategy_summary": "当日扫描已完成，未命中趋势龙头候选（0）"}),
        _row("hundred_day_high", "000635", "英力特",
             {"close": 9.5, "window_high": 9.58, "new_high_window": 80,
              "history_source": "disk_cache:TushareFetcher"}),
    ]
    board = _build(rows)

    assert [c.code for c in board.candidates] == ["000635"]
    assert [d.code for d in board.diagnostics] == ["TL_SUMMARY"]
    assert "未命中趋势龙头候选" in board.diagnostics[0].detail


# ---------------------------------------------------------------- 降级与标记


def test_rebuild_failed_candidate_is_flagged() -> None:
    rows = [_row("hundred_day_high", "601318", "中国平安", {"close": 50, "window_high": 51})]
    board = _build(rows, failed={"601318"}, rate="85/174 = 48.9%")

    assert board.rebuild_failure_count == 1
    assert "行情重建失败" in board.candidates[0].flags
    assert any("48.9%" in note for note in board.notes)


def test_screening_degradation_is_surfaced() -> None:
    rows = [
        _row("screening__shrink_pullback", "000001", "平安银行",
             {"rank": 3, "score": 61.2, "reason": "本地后置评分"}),
    ]
    audits = [{
        "strategy": "shrink_pullback",
        "snapshot_source": "last_good_cache",
        "degradation": ["LLM ranking failed: fell back to screen_score"],
    }]
    board = _build(rows, audits=audits)
    markdown = render_markdown(board)

    flags = board.candidates[0].flags
    assert "缓存快照" in flags and "采集降级" in flags
    assert "LLM ranking failed" in markdown
    assert "shrink_pullback: LLM ranking failed" in markdown


def test_stale_cache_history_is_flagged_for_breakout() -> None:
    rows = [_row("hundred_day_high", "000635", "英力特",
                 {"close": 9.5, "window_high": 9.58,
                  "history_source": "disk_cache_best_effort_stale:TushareFetcher"})]
    board = _build(rows)
    assert "缓存兜底" in board.candidates[0].flags


def test_normal_disk_cache_is_not_flagged() -> None:
    """disk_cache 是正常读路径，不能因此把全部候选标成可疑。"""
    rows = [_row("hundred_day_high", "000635", "英力特",
                 {"close": 9.5, "window_high": 9.58,
                  "history_source": "disk_cache:TushareFetcher"})]
    board = _build(rows, last_dates={"000635": "2026-11-06"})
    assert board.candidates[0].flags == ()


def test_benign_degradation_is_not_counted_as_degraded() -> None:
    rows = [_row("screening__dual_low", "600016", "民生银行", {"rank": 1, "score": 83.36})]
    audits = [{"strategy": "dual_low", "degradation": ["DSA provider context applied 3 of 3 candidates"]}]
    board = _build(rows, audits=audits)
    assert board.screening_degradations == []


def test_stale_bar_date_is_flagged_per_candidate() -> None:
    rows = [
        _row("hundred_day_high", "000090", "天健集团", {"close": 4.2, "window_high": 4.3}),
        _row("hundred_day_high", "000001", "平安银行", {"close": 11.8, "window_high": 11.9}),
    ]
    board = _build(rows, last_dates={"000090": "2026-10-26", "000001": "2026-11-06"})

    by_code = {c.code: c for c in board.candidates}
    assert "行情截至2026-10-26" in by_code["000090"].flags
    assert by_code["000001"].flags == ()
    assert "1 只候选的本地行情未到 2026-11-06" in render_markdown(board)


def test_missing_local_bar_is_flagged() -> None:
    rows = [_row("screening__dual_low", "600016", "民生银行", {"rank": 1, "score": 83.36})]
    board = _build(rows, last_dates={})
    assert "无本地行情" in board.candidates[0].flags


def test_etf_shell_log_completion_is_parsed(tmp_path) -> None:
    from src.services.daily_board_service import read_task_health

    log = tmp_path / "run-2026-11-06.log"
    log.write_text("x\n=== 2026-11-06 17:30:03 结束 rc=0 ===\n", encoding="utf-8")

    health = read_task_health("etf", "ETF 份额周报", log, "2026-11-06", daily=False)
    assert health.status == "ok"
    assert health.exit_code == 0
    assert health.last_done_local == "2026-11-06 17:30:03"


def test_structure_shell_log_with_exit_is_ok(tmp_path) -> None:
    from src.services.daily_board_service import read_task_health

    log = tmp_path / "structure_scan_daily.log"
    log.write_text(
        "===== start 2026-11-06 19:00:01 =====\n"
        "普通输出行不计入边界\n"
        "===== done 2026-11-06 19:00:39 exit=0 =====\n",
        encoding="utf-8",
    )

    health = read_task_health("structure", "结构扫描", log, "2026-11-06", daily=True)
    assert health.status == "ok"
    assert health.exit_code == 0
    assert health.last_start_local == "2026-11-06 19:00:01"
    assert health.last_done_local == "2026-11-06 19:00:39"


def test_structure_shell_log_without_exit_stays_degraded(tmp_path) -> None:
    """包装器补 exit 前的历史批次确实没有退出码，不能靠猜当成通过。"""
    from src.services.daily_board_service import read_task_health

    log = tmp_path / "structure_scan_daily.log"
    log.write_text(
        "===== start 2026-11-06 19:00:01 =====\n"
        "===== done 2026-11-06 19:00:39 =====\n",
        encoding="utf-8",
    )

    health = read_task_health("structure", "结构扫描", log, "2026-11-06", daily=True)
    assert health.status == "degraded"
    assert health.exit_code is None


def test_structure_shell_log_with_nonzero_exit_is_failed(tmp_path) -> None:
    from src.services.daily_board_service import read_task_health

    log = tmp_path / "structure_scan_daily.log"
    log.write_text(
        "===== start 2026-11-06 19:00:01 =====\n"
        "===== done 2026-11-06 19:00:39 exit=3 =====\n",
        encoding="utf-8",
    )

    health = read_task_health("structure", "结构扫描", log, "2026-11-06", daily=True)
    assert health.status == "failed"
    assert health.exit_code == 3


# ---------------------------------------------------------------- 缺口语义


def test_empty_sources_render_gap_not_no_opportunity() -> None:
    board = _build([])
    markdown = render_markdown(board)

    assert "本日无通过对象闸门的新候选" in markdown
    assert "未接入" in markdown
    assert "无机会" not in markdown


def test_failed_source_is_reported_but_board_still_renders() -> None:
    health = [
        SourceHealth("matrix", "个人策略矩阵", "failed", "done 返回 exit=1", "2026-11-06 19:00:00", 1),
        SourceHealth("screening", "筛选预设（三批）", "missing", "未找到日志文件"),
    ]
    board = _build([], health=health)
    markdown = render_markdown(board)

    assert "失败" in markdown and "无完成记录" in markdown
    assert "① 风险与失效" in markdown and "④ 到期复核" in markdown


# ---------------------------------------------------------------- 时区


def test_utc_naive_is_converted_to_beijing_time() -> None:
    converted = utc_naive_to_local(datetime(2026, 11, 6, 10, 18, 11))
    assert converted is not None
    assert converted.strftime("%Y-%m-%d %H:%M:%S") == "2026-11-06 18:18:11"
    assert converted.utcoffset().total_seconds() == 8 * 3600


def test_aware_datetime_is_converted_without_double_shift() -> None:
    from datetime import timezone

    converted = utc_naive_to_local(datetime(2026, 11, 6, 10, 18, 11, tzinfo=timezone.utc))
    assert converted.strftime("%Y-%m-%d %H:%M:%S") == "2026-11-06 18:18:11"


# ---------------------------------------------------------------- 样本身份


def test_multi_source_hit_keeps_both_samples() -> None:
    rows = [
        _row("hundred_day_high", "600519", "贵州茅台", {"close": 1500, "window_high": 1501}),
        _row("screening__momentum_quality", "600519", "贵州茅台", {"rank": 1, "score": 70}),
    ]
    board = _build(rows)

    assert len(board.candidates) == 2
    assert all("多来源命中" in c.flags for c in board.candidates)


def test_rerender_same_date_is_stable() -> None:
    rows = [
        _row("hundred_day_high", "000635", "英力特", {"close": 9.5, "window_high": 9.58}),
        _row("screening__dual_low", "601398", "工商银行", {"rank": 2, "score": 65}),
    ]
    first = render_markdown(_build(rows))
    second = render_markdown(_build(rows))
    assert first == second


def test_candidate_table_limits_and_reports_total() -> None:
    candidates = [
        Candidate("hundred_day_high", f"{i:06d}", f"票{i}", "理由") for i in range(25)
    ]
    lines = _render_candidate_table(candidates, limit=20)
    body = "\n".join(lines)

    assert body.count("| `") == 20
    assert "显示 20 / 共 25 条" in body


# ---------------------------------------------------------------- track bootstrap


def test_select_track_candidates_orders_multi_source_first() -> None:
    from src.services.daily_board_service import select_track_candidates

    single = Candidate("screening__dual_low", "600016", "民生银行", "rank 1", batch_ready=True)
    multi = Candidate("hundred_day_high", "601398", "工商银行", "新高", ("多来源命中",), batch_ready=True)
    no_bar = Candidate("hundred_day_high", "000001", "平安银行", "新高", ("无本地行情",))

    picks = select_track_candidates([single, multi, no_bar], limit=10)
    assert [c.code for c in picks] == ["601398", "600016"]


def test_select_track_candidates_respects_limit() -> None:
    from src.services.daily_board_service import select_track_candidates

    candidates = [Candidate("hundred_day_high", f"{i:06d}", f"票{i}", "理由", batch_ready=True) for i in range(40)]
    picks = select_track_candidates(candidates, limit=30)
    assert len(picks) == 30
    assert picks[0].code == "000000"


# ---------------------------------------------------------------- due review


def test_due_reviews_render_with_overdue_marker() -> None:
    from src.services.daily_board_service import DueReview

    rows = [_row("hundred_day_high", "000635", "英力特", {"close": 9.5, "window_high": 9.58})]
    board = _build(rows)
    board.due_reviews = [
        DueReview("t1", "000635", "英力特 · hundred_day_high · 2026-11-03", "hundred_day_high", "2026-11-03", 3),
        DueReview("t2", "600016", "民生银行 · dual_low · 2026-11-06", "screening", "2026-11-06", 0),
    ]
    markdown = render_markdown(board)

    assert "共 2 条到期，其中 1 条逾期" in markdown
    assert "| `000635` |" in markdown and "逾期 3 天" in markdown
    assert "| `600016` |" in markdown


def test_due_review_empty_and_error_are_distinguished() -> None:
    board = _build([])
    assert "无到期研究项" in render_markdown(board)

    board.due_review_error = "OperationalError: no such table: research_tracks"
    markdown = render_markdown(board)
    assert "研究跟踪库不可用" in markdown
    assert "不伪造到期项" in markdown


# ---------------------------------------------------------------- news / changes


def test_news_section_renders_items() -> None:
    from src.services.daily_board_service import NewsItem

    board = _build([])
    board.news = [NewsItem("2026-11-05 09:30", "交易所公告", "某公司中标重大合同", "600519")]
    board.news_note = "窗口 2026-11-03~2026-11-06 内 1 条（全表：news_intel 1 条）"

    markdown = render_markdown(board)
    assert "某公司中标重大合同" in markdown
    assert "| 2026-11-05 09:30 |" in markdown


def test_news_section_gap_is_not_no_change() -> None:
    board = _build([])
    board.news_note = "窗口内无资讯记录（全表：news_intel 0 条）—— 属采集未接入，不代表无变化"

    markdown = render_markdown(board)
    assert "不代表无变化" in markdown
    assert "未接入" in markdown


# ---------------------------------------------------------------- 时区/批次


def test_generated_at_naive_is_not_shifted() -> None:
    board = _build([])
    markdown = render_markdown(board, generated_at=datetime(2026, 11, 9, 2, 6, 52))

    assert "生成时间：2026-11-09 02:06:52" in markdown
    assert "Asia/Shanghai" not in markdown


def test_health_detects_start_without_done(tmp_path) -> None:
    log = tmp_path / "matrix.log"
    log.write_text(
        "[matrix_daily] done snapshot=2026-11-06 exit=0 2026-11-06 19:00:00\n"
        "[matrix_daily] start snapshot=2026-11-06 19:05:00\n",
        encoding="utf-8",
    )
    from src.services.daily_board_service import read_task_health

    health = read_task_health("matrix", "个人策略矩阵", log, "2026-11-06", True)
    assert health.status == "running"
    assert "未见 done" in health.detail


def test_all_candidates_without_quotes_are_flagged() -> None:
    rows = [
        _row("hundred_day_high", "000635", "英力特", {"close": 9.5, "window_high": 9.58}),
        _row("screening__dual_low", "600016", "民生银行", {"rank": 1, "score": 83.36}),
    ]
    board = _build(rows, last_dates={})
    assert all("无本地行情" in cand.flags for cand in board.candidates)
