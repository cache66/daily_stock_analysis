# -*- coding: utf-8 -*-
"""Tests for fast review daily bundle runner."""

import csv
import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_fast_review_bundle as fast_bundle


def _install_stub_cause_service(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            return {
                "reason_summary": f"{stock_code} breakout",
                "cause_tags": ["other"],
                "industry_logic": "",
                "news_logic": "",
                "technical_logic": "",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _StubCauseService, raising=False)


def test_normalize_include_signals_expands_alias_and_deduplicates() -> None:
    signals = fast_bundle.normalize_include_signals(
        "earnings,continuous_up,trend_leader,continuous_up_ratio,unknown"
    )
    assert signals == [
        "earnings",
        "continuous_up_ratio",
        "continuous_up_streak",
        "trend_leader",
    ]


def test_apply_exclude_signals_removes_expanded_targets() -> None:
    include = fast_bundle.normalize_include_signals("earnings,trend_leader,continuous_up")
    filtered = fast_bundle.apply_exclude_signals(include, "continuous_up,trend_leader")
    assert filtered == ["earnings"]


def test_build_commands_forward_skip_db_persist() -> None:
    args = SimpleNamespace(
        earnings_strategy_profile="balanced",
        earnings_scan_depth="low",
        earnings_recent_event_scope="latest_report_period",
        earnings_recent_event_max_age_days=7,
        earnings_max_workers=1,
        earnings_capital_profile_ttl_seconds=86400,
        hundred_day_signal_type="hundred_day_high",
        hundred_day_disable_spot_prefilter=False,
        hundred_day_prefilter_min_listed_days=120,
        hundred_day_disable_listed_days_prefilter=False,
        hundred_day_prefilter_min_change_pct_60d=12.0,
        hundred_day_prefilter_min_turnover_rate=0.8,
        hundred_day_prefilter_require_positive_change=True,
        hundred_day_prefilter_exclude_st=True,
        hundred_day_max_workers=2,
        monthly_signal_type="monthly_slow_rise",
        monthly_profile="balanced",
        monthly_max_workers=2,
        trend_signal_type="trend_leader_unified",
        trend_max_workers=2,
        trend_fallback_top_n=20,
        trend_watch_top_n=12,
        trend_disable_second_stage_enrichment=True,
        trend_shard_count=1,
        trend_shard_index=0,
        trend_disable_scan_prefilter=False,
        trend_scan_prefilter_min_listed_days=120,
        trend_scan_prefilter_min_change_pct_60d=4.0,
        trend_scan_prefilter_min_turnover_rate=1.0,
        trend_scan_prefilter_require_positive_change=True,
        hundred_day_skip_cause_analysis=True,
        persist_snapshots=False,
        max_workers=1,
        limit=100,
        progress_every=25,
        exclude_st=True,
        exclude_kcb=True,
        exclude_cyb=True,
        universe_codes_file=None,
        log_level="INFO",
    )
    snapshot_date = date(2026, 4, 20)

    earnings_cmd = fast_bundle.build_earnings_command(
        args,
        snapshot_date=snapshot_date,
        output_dir=Path("tmp/earnings"),
    )
    hundred_cmd = fast_bundle.build_hundred_day_high_command(
        args,
        snapshot_date=snapshot_date,
        output_dir=Path("tmp/hundred"),
    )
    monthly_cmd = fast_bundle.build_monthly_slow_rise_command(
        args,
        snapshot_date=snapshot_date,
        output_dir=Path("tmp/monthly"),
    )
    trend_cmd = fast_bundle.build_trend_leader_command(
        args,
        snapshot_date=snapshot_date,
        output_dir=Path("tmp/trend"),
    )

    assert "--skip-db-persist" in earnings_cmd
    assert "--scan-depth" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--scan-depth") + 1] == "low"
    assert "--recent-event-scope" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--recent-event-scope") + 1] == "latest_report_period"
    assert "--recent-event-max-age-days" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--recent-event-max-age-days") + 1] == "7"
    assert "--max-workers" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--max-workers") + 1] == "1"
    assert "--capital-profile-ttl-seconds" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--capital-profile-ttl-seconds") + 1] == "86400"
    assert "--limit" in earnings_cmd
    assert earnings_cmd[earnings_cmd.index("--limit") + 1] == "100"
    assert "--skip-db-persist" in hundred_cmd
    assert "--skip-db-persist" in monthly_cmd
    assert "--skip-db-persist" in trend_cmd
    assert "--skip-cause-analysis" in hundred_cmd
    assert "--checkpoint-path" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--checkpoint-path") + 1] == str(
        Path("tmp/hundred") / "hundred_day_high_checkpoint.json"
    )
    assert "--min-listed-days-prefilter" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--min-listed-days-prefilter") + 1] == "120"
    assert "--min-60d-change-pct-prefilter" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--min-60d-change-pct-prefilter") + 1] == "12.0"
    assert "--min-turnover-rate-prefilter" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--min-turnover-rate-prefilter") + 1] == "0.8"
    assert "--require-positive-change-prefilter" in hundred_cmd
    assert "--exclude-st-prefilter" in hundred_cmd
    assert "--max-workers" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--max-workers") + 1] == "2"
    assert "--limit" not in hundred_cmd
    assert "--profile" in monthly_cmd
    assert monthly_cmd[monthly_cmd.index("--profile") + 1] == "balanced"
    assert "--max-workers" in monthly_cmd
    assert monthly_cmd[monthly_cmd.index("--max-workers") + 1] == "2"
    assert "--limit" in monthly_cmd
    assert monthly_cmd[monthly_cmd.index("--limit") + 1] == "100"
    assert "--disable-second-stage-news-search" in trend_cmd
    assert "--disable-second-stage-business-profile" in trend_cmd
    assert "--enrich-top-n" in trend_cmd
    assert trend_cmd[trend_cmd.index("--enrich-top-n") + 1] == "0"
    assert "--watch-top-n" in trend_cmd
    assert trend_cmd[trend_cmd.index("--watch-top-n") + 1] == "12"
    assert "--max-workers" in trend_cmd
    assert trend_cmd[trend_cmd.index("--max-workers") + 1] == "2"
    assert "--shard-count" in trend_cmd
    assert trend_cmd[trend_cmd.index("--shard-count") + 1] == "1"
    assert "--shard-index" in trend_cmd
    assert trend_cmd[trend_cmd.index("--shard-index") + 1] == "0"
    assert "--scan-prefilter-min-change-pct-60d" in trend_cmd
    assert trend_cmd[trend_cmd.index("--scan-prefilter-min-change-pct-60d") + 1] == "4.0"
    assert "--scan-prefilter-min-listed-days" in trend_cmd
    assert trend_cmd[trend_cmd.index("--scan-prefilter-min-listed-days") + 1] == "120"
    assert "--scan-prefilter-min-turnover-rate" in trend_cmd
    assert trend_cmd[trend_cmd.index("--scan-prefilter-min-turnover-rate") + 1] == "1.0"
    assert "--scan-prefilter-require-positive-change" in trend_cmd
    assert "--limit" in trend_cmd
    assert trend_cmd[trend_cmd.index("--limit") + 1] == "100"


def test_apply_signal_output_limits_clips_hundred_day_only() -> None:
    hundred_rows = [{"code": f"{600000 + idx}", "name": f"H{idx}"} for idx in range(35)]
    trend_rows = [{"code": "300001", "name": "TrendA"}]
    results = [
        fast_bundle.SignalResult(
            key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
            signal_type="hundred_day_high",
            label="hundred",
            rows=hundred_rows,
            csv_path=Path("hundred.csv"),
            duration_sec=1.0,
        ),
        fast_bundle.SignalResult(
            key=fast_bundle.SIGNAL_TREND_LEADER,
            signal_type="trend_leader_unified",
            label="trend",
            rows=trend_rows,
            csv_path=Path("trend.csv"),
            duration_sec=1.0,
        ),
    ]

    clipped = fast_bundle._apply_signal_output_limits(
        results,
        hundred_day_output_limit=30,
    )

    assert len(clipped) == 2
    assert len(clipped[0].rows) == 30
    assert clipped[0].rows[0]["code"] == "600000"
    assert clipped[0].rows[-1]["code"] == "600029"
    assert len(clipped[1].rows) == 1


def test_main_clips_hundred_day_outputs_at_bundle_layer(monkeypatch, tmp_path: Path) -> None:
    _install_stub_cause_service(monkeypatch)
    hundred_rows = [
        {
            "code": f"{600000 + idx}",
            "name": f"Hundred{idx}",
            "overall_score": "18",
        }
        for idx in range(35)
    ]

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        assert [job.key for job in jobs] == [fast_bundle.SIGNAL_HUNDRED_DAY_HIGH]
        return (
            [
                fast_bundle.SignalResult(
                    key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                    signal_type="hundred_day_high",
                    label="hundred",
                    rows=hundred_rows,
                    csv_path=jobs[0].csv_path,
                    duration_sec=1.0,
                )
            ],
            [],
        )

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(fast_bundle, "_collect_continuous_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "hundred_day_high",
            "--hundred-day-output-limit",
            "10",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    unified_csv = tmp_path / "2026-04-20" / "review" / "fast_review_candidates.csv"
    strategy_focus_csv = tmp_path / "2026-04-20" / "review" / "fast_review_strategy_focus.csv"
    assert rc == 0
    assert unified_csv.exists()
    assert strategy_focus_csv.exists()

    with unified_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        unified_rows = list(csv.DictReader(handle))
    with strategy_focus_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        focus_rows = list(csv.DictReader(handle))

    assert len(unified_rows) == 10
    assert len(focus_rows) == 10
    assert unified_rows[0]["code"] == "600000"
    assert unified_rows[-1]["code"] == "600009"
    assert focus_rows[0]["code"] == "600000"
    assert focus_rows[-1]["code"] == "600009"


def test_build_trend_command_can_disable_prefilter() -> None:
    args = SimpleNamespace(
        trend_signal_type="trend_leader_unified",
        trend_max_workers=2,
        trend_fallback_top_n=20,
        trend_disable_second_stage_enrichment=False,
        trend_shard_count=1,
        trend_shard_index=0,
        trend_disable_scan_prefilter=True,
        trend_scan_prefilter_min_listed_days=120,
        trend_scan_prefilter_min_change_pct_60d=4.0,
        trend_scan_prefilter_min_turnover_rate=1.0,
        trend_scan_prefilter_require_positive_change=True,
        persist_snapshots=True,
        max_workers=1,
        limit=None,
        progress_every=25,
        exclude_st=False,
        exclude_kcb=False,
        exclude_cyb=False,
        universe_codes_file=None,
        log_level="INFO",
    )
    trend_cmd = fast_bundle.build_trend_leader_command(
        args,
        snapshot_date=date(2026, 4, 20),
        output_dir=Path("tmp/trend"),
    )
    assert "--disable-scan-prefilter" in trend_cmd
    assert "--scan-prefilter-min-listed-days" not in trend_cmd
    assert "--scan-prefilter-min-change-pct-60d" not in trend_cmd
    assert "--scan-prefilter-min-turnover-rate" not in trend_cmd


def test_main_runs_selected_external_signals(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def _mock_run_command(command):
        commands.append(list(command))
        return "ok"

    def _mock_load_signal_rows_from_csv(*, csv_path, signal_type, signal_label):
        return [
            {
                "signal_type": signal_type,
                "signal_label": signal_label,
                "code": "600001",
                "name": "sample",
            }
        ]

    monkeypatch.setattr(fast_bundle, "_run_command", _mock_run_command)
    monkeypatch.setattr(fast_bundle, "_load_signal_rows_from_csv", _mock_load_signal_rows_from_csv)
    monkeypatch.setattr(fast_bundle, "_collect_continuous_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "earnings,trend_leader",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    assert rc == 0
    assert len(commands) == 2
    assert "--skip-db-persist" in commands[0]
    assert "--skip-db-persist" in commands[1]

    summary_md = tmp_path / "2026-04-20" / "review" / "fast_review_summary.md"
    unified_csv = tmp_path / "2026-04-20" / "review" / "fast_review_candidates.csv"
    assert summary_md.exists()
    assert unified_csv.exists()


def test_main_registers_monthly_external_signal(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        captured["keys"] = [job.key for job in jobs]
        captured["signal_types"] = [job.signal_type for job in jobs]
        return [], []

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(fast_bundle, "_collect_continuous_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "monthly_slow_rise",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    assert rc == 0
    assert captured["keys"] == ["monthly_slow_rise"]
    assert captured["signal_types"] == ["monthly_slow_rise"]


def test_main_warns_when_earnings_runs_without_persist(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(fast_bundle, "_collect_continuous_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "earnings",
            "--skip-persist-snapshots",
        ],
    )

    with caplog.at_level("WARNING"):
        rc = fast_bundle.main()

    assert rc == 0
    assert "earnings" in caplog.text
    assert "--skip-persist-snapshots" in caplog.text
    assert "same-day/cross-day cache" in caplog.text


def test_run_external_signal_jobs_keeps_job_order(monkeypatch, tmp_path: Path) -> None:
    jobs = [
        fast_bundle.ExternalSignalJob(
            key="trend_leader",
            signal_type="trend_leader_unified",
            signal_label="趋势龙头",
            command=["python", "trend.py"],
            csv_path=tmp_path / "trend.csv",
        ),
        fast_bundle.ExternalSignalJob(
            key="earnings",
            signal_type="earnings_surprise",
            signal_label="业绩",
            command=["python", "earnings.py"],
            csv_path=tmp_path / "earnings.csv",
        ),
    ]

    def _mock_run_external_signal(*, key, signal_type, signal_label, command, csv_path):
        return fast_bundle.SignalResult(
            key=key,
            signal_type=signal_type,
            label=signal_label,
            rows=[{"code": "600001", "name": "sample"}],
            csv_path=csv_path,
            duration_sec=1.0,
        )

    monkeypatch.setattr(fast_bundle, "_run_external_signal", _mock_run_external_signal)

    results, skipped = fast_bundle._run_external_signal_jobs(jobs, external_parallelism=2)
    assert [item.key for item in results] == ["trend_leader", "earnings"]
    assert skipped == []


def test_run_external_signal_jobs_marks_no_rows_as_skipped(monkeypatch, tmp_path: Path) -> None:
    jobs = [
        fast_bundle.ExternalSignalJob(
            key="trend_leader",
            signal_type="trend_leader_unified",
            signal_label="trend",
            command=["python", "trend.py"],
            csv_path=tmp_path / "trend.csv",
        ),
        fast_bundle.ExternalSignalJob(
            key="earnings",
            signal_type="earnings_surprise",
            signal_label="earnings",
            command=["python", "earnings.py"],
            csv_path=tmp_path / "earnings.csv",
        ),
    ]

    def _mock_run_external_signal(*, key, signal_type, signal_label, command, csv_path):
        rows = [{"code": "600001", "name": "sample"}] if key == "trend_leader" else []
        return fast_bundle.SignalResult(
            key=key,
            signal_type=signal_type,
            label=signal_label,
            rows=rows,
            csv_path=csv_path,
            duration_sec=1.0,
        )

    monkeypatch.setattr(fast_bundle, "_run_external_signal", _mock_run_external_signal)

    results, skipped = fast_bundle._run_external_signal_jobs(jobs, external_parallelism=2)
    assert [item.key for item in results] == ["trend_leader"]
    assert len(skipped) == 1
    assert skipped[0].key == "earnings"
    assert skipped[0].reason == "no_rows"


def test_run_external_signal_retries_once_when_sqlite_locked(monkeypatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    def _mock_run_command(_command):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("sqlite error: database is locked")
        return "ok"

    csv_path = tmp_path / "trend.csv"
    csv_path.write_text("code,name\n600001,sample\n", encoding="utf-8")
    monkeypatch.setattr(fast_bundle, "_run_command", _mock_run_command)
    monkeypatch.setattr(fast_bundle.time, "sleep", lambda *_args, **_kwargs: None)

    result = fast_bundle._run_external_signal(
        key="trend_leader",
        signal_type="trend_leader_unified",
        signal_label="trend",
        command=["python", "trend.py"],
        csv_path=csv_path,
    )

    assert calls["count"] == 2
    assert result.key == "trend_leader"
    assert len(result.rows) == 1


def test_build_summary_markdown_contains_skipped_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key="trend_leader",
                signal_type="trend_leader_unified",
                label="trend",
                rows=[{"code": "600001", "name": "A"}],
                csv_path=tmp_path / "trend.csv",
                duration_sec=12.3,
            )
        ],
        skipped_signals=[
            fast_bundle.SkippedSignal(
                key="earnings",
                signal_type="earnings_surprise",
                reason="no_rows",
                detail="no candidate rows loaded from csv",
            )
        ],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
    )

    assert "Skipped / No-result Signals" in summary
    assert "earnings" in summary
    assert "no_rows" in summary


def test_build_strategy_focus_rows_prioritizes_overlap_earnings_capital_and_board(monkeypatch) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "OverlapCore",
                    "overall_score": "36",
                    "capital_consensus_score": "2",
                    "sector_leadership_score": "2",
                    "recognizability_score": "2",
                    "primary_board_name": "AI",
                },
                {
                    "code": "600002",
                    "name": "HighTrendOnly",
                    "overall_score": "45",
                    "capital_consensus_score": "0",
                    "sector_leadership_score": "0",
                    "recognizability_score": "0",
                },
                {
                    "code": "600003",
                    "name": "RiskyFallback",
                    "overall_score": "40",
                    "selection_mode": "fallback",
                    "risk_flags": "hard_risk",
                },
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[{"code": "600001", "name": "OverlapCore"}],
            csv_path=Path("hundred.csv"),
        ),
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600001",
                    "name": "OverlapCore",
                    "earnings_strategy_score": "72",
                    "market_expectation_summary": "2026年EPS一致预期 1.23 元",
                    "market_expectation_reference_label": "beat_ref",
                    "event_date": "2026-04-18",
                }
            ],
            csv_path=Path("earnings.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600001"
    assert rows[0]["tier"] == "core"
    assert rows[0]["trend_hundred_relation"] == "intersection"
    assert rows[0]["earnings_strategy_score"] == 72.0
    assert rows[0]["market_expectation_reference_label"] == "beat_ref"
    assert rows[0]["market_expectation_summary"] == "2026年EPS一致预期 1.23 元"
    assert rows[0]["primary_board_name"] == "AI"
    assert rows[-1]["code"] == "600003"
    assert rows[-1]["tier"] == "low_priority"


def test_build_strategy_focus_rows_marks_trend_hundred_intersection_and_difference(monkeypatch) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {"code": "600001", "name": "Both", "overall_score": "35"},
                {"code": "600002", "name": "TrendOnly", "overall_score": "32"},
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {"code": "600001", "name": "Both"},
                {"code": "600003", "name": "HundredOnly"},
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = {row["code"]: row for row in fast_bundle._build_strategy_focus_rows(signal_results)}

    assert rows["600001"]["trend_hundred_relation"] == "intersection"
    assert rows["600002"]["trend_hundred_relation"] == "trend_only"
    assert rows["600003"]["trend_hundred_relation"] == "hundred_only"


def test_build_strategy_focus_rows_assigns_review_display_groups(monkeypatch) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {"code": "600001", "name": "Both", "overall_score": "35"},
                {"code": "600002", "name": "TrendOnly", "overall_score": "32"},
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "600001",
                    "name": "Both",
                    "chart_pattern_label": "healthy_trend",
                    "chart_pattern_score": "16",
                    "breakout_quality_score": "14",
                },
                {
                    "code": "600003",
                    "name": "HundredOnly",
                    "chart_pattern_label": "base_breakout",
                    "chart_pattern_score": "16",
                    "breakout_quality_score": "14",
                },
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = {row["code"]: row for row in fast_bundle._build_strategy_focus_rows(signal_results)}

    assert rows["600001"]["review_display_group"] == "intersection"
    assert rows["600001"]["review_display_group_label"] == "交叉强样本"
    assert rows["600002"]["review_display_group"] == "trend_continuation"
    assert rows["600002"]["review_display_group_label"] == "纯趋势延续"
    assert rows["600003"]["review_display_group"] == "hundred_strong_chart"
    assert rows["600003"]["review_display_group_label"] == "纯百日新高"


def test_build_strategy_focus_rows_does_not_treat_trend_residual_earnings_as_turning_point_when_earnings_signal_empty(
    monkeypatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "TrendResidual",
                    "overall_score": "32",
                    "earnings_strategy_score": "50",
                    "earnings_strategy_gate_status": "passed_watch_with_confirmation",
                    "report_date": "2026-03-31",
                    "report_period_label": "2026Q1",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["driver_type"] == "theme_sentiment_driven"
    assert rows[0]["review_stage_type"] == "pure_rotation"
    assert rows[0]["review_context_label"] == "业绩空窗期"
    assert "earnings 候选为 0" in rows[0]["review_context_reason"]


def test_build_strategy_focus_rows_prioritizes_strong_hundred_day_chart_patterns_when_earnings_signal_empty(
    monkeypatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "TrendResidual",
                    "overall_score": "36",
                    "earnings_strategy_score": "50",
                    "earnings_strategy_gate_status": "passed_watch_with_confirmation",
                    "report_date": "2026-03-31",
                    "report_period_label": "2026Q1",
                }
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "600002",
                    "name": "BaseBreakout",
                    "breakout_quality_score": "15",
                    "chart_pattern_label": "base_breakout",
                    "chart_pattern_score": "16",
                    "chart_pattern_summary": "妯洏绐佺牬鍨?",
                    "base_breakout_score": "15",
                    "healthy_trend_score": "8",
                    "pct_change": "9.9",
                }
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600002"
    assert rows[0]["trend_hundred_relation"] == "hundred_only"
    assert rows[0]["chart_pattern_label"] == "base_breakout"


def test_build_strategy_focus_rows_rebalances_blank_earnings_window_to_surface_chart_driven_hundred_only_names(
    monkeypatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "IntersectA",
                    "overall_score": "36",
                    "earnings_strategy_score": "50",
                },
                {
                    "code": "600002",
                    "name": "TrendOnlyA",
                    "overall_score": "41",
                    "earnings_strategy_score": "50",
                },
                {
                    "code": "600003",
                    "name": "TrendOnlyB",
                    "overall_score": "40",
                    "earnings_strategy_score": "50",
                },
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "600001",
                    "name": "IntersectA",
                    "breakout_quality_score": "10",
                    "chart_pattern_label": "healthy_trend",
                    "chart_pattern_score": "16",
                    "healthy_trend_score": "16",
                },
                {
                    "code": "600004",
                    "name": "HundredOnlyA",
                    "breakout_quality_score": "14",
                    "chart_pattern_label": "healthy_trend",
                    "chart_pattern_score": "16",
                    "healthy_trend_score": "16",
                },
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600001"
    assert rows[1]["code"] == "600004"
    assert rows[1]["trend_hundred_relation"] == "hundred_only"
    assert rows[2]["code"] == "600002"


def test_build_strategy_focus_rows_blank_earnings_window_can_promote_chart_driven_hundred_only_name_above_high_scoring_trend_only(
    monkeypatch,
) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "TrendOnlyA",
                    "overall_score": "40",
                    "earnings_strategy_score": "50",
                    "capital_consensus_score": "3",
                    "sector_leadership_score": "2",
                    "recognizability_score": "2",
                },
                {
                    "code": "600002",
                    "name": "TrendOnlyB",
                    "overall_score": "39",
                    "earnings_strategy_score": "50",
                    "capital_consensus_score": "3",
                    "sector_leadership_score": "2",
                    "recognizability_score": "2",
                },
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "600003",
                    "name": "HundredOnlyA",
                    "breakout_quality_score": "14",
                    "chart_pattern_label": "healthy_trend",
                    "chart_pattern_score": "16",
                    "healthy_trend_score": "16",
                },
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600003"
    assert rows[0]["trend_hundred_relation"] == "hundred_only"


def test_build_strategy_focus_rows_reuses_existing_reason_fields_for_non_alias_names() -> None:
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "ReuseA",
                    "overall_score": "36",
                    "reason_summary": "板块轮动带动走强",
                    "cause_tags": "sector_rotation,policy",
                    "industry_logic": "行业强势",
                    "news_logic": "消息面催化",
                    "technical_logic": "新高延续",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"] == "板块轮动带动走强"
    assert rows[0]["cause_tags"] == "sector_rotation,policy"
    assert rows[0]["cause_tags_zh"] == "板块轮动/政策"
    assert rows[0]["industry_logic"] == "行业强势"
    assert rows[0]["news_logic"] == "消息面催化"
    assert rows[0]["technical_logic"] == "新高延续"


def test_build_strategy_focus_rows_refreshes_existing_reason_fields_for_alias_names(monkeypatch) -> None:
    class _FakeCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            assert stock_code == "002281"
            assert stock_name == "AliasA"
            assert signal_type == "trend_leader_unified"
            return {
                "reason_summary": "alias-refreshed",
                "cause_tags": ["sector_rotation", "overseas_theme"],
                "industry_logic": "alias-industry",
                "news_logic": "alias-news",
                "technical_logic": "alias-technical",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "002281",
                    "name": "AliasA",
                    "overall_score": "36",
                    "reason_summary": "stale-reason",
                    "cause_tags": "sector_rotation",
                    "industry_logic": "stale-industry",
                    "news_logic": "stale-news",
                    "technical_logic": "stale-technical",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"] == "alias-refreshed"
    assert rows[0]["cause_tags"] == "sector_rotation,overseas_theme"
    assert rows[0]["industry_logic"] == "alias-industry"
    assert rows[0]["news_logic"] == "alias-news"
    assert rows[0]["technical_logic"] == "alias-technical"


def test_build_strategy_focus_rows_reuses_one_cause_service_instance_per_bundle(monkeypatch) -> None:
    class _CountingCauseService:
        init_count = 0
        analyze_count = 0

        def __init__(self, *args, **kwargs) -> None:
            self.__class__.init_count += 1

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            self.__class__.analyze_count += 1
            return {
                "reason_summary": f"{stock_code}-refreshed",
                "cause_tags": ["sector_rotation"],
                "industry_logic": f"{stock_code}-industry",
                "news_logic": f"{stock_code}-news",
                "technical_logic": f"{stock_code}-technical",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _CountingCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "002281",
                    "name": "AliasA",
                    "overall_score": "36",
                    "reason_summary": "stale-a",
                },
                {
                    "code": "002384",
                    "name": "AliasB",
                    "overall_score": "35",
                    "reason_summary": "stale-b",
                },
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = {row["code"]: row for row in fast_bundle._build_strategy_focus_rows(signal_results)}

    assert _CountingCauseService.init_count == 1
    assert _CountingCauseService.analyze_count == 2
    assert rows["002281"]["reason_summary"] == "002281-refreshed"
    assert rows["002384"]["reason_summary"] == "002384-refreshed"


def test_build_strategy_focus_rows_augments_existing_earnings_reason_with_business_hint(monkeypatch) -> None:
    class _FakeCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            assert stock_code == "600045"
            assert stock_name == "EarningsA"
            assert signal_type == "earnings_surprise"
            return {
                "reason_summary": "fallback-business-view",
                "cause_tags": ["earnings"],
                "industry_logic": "materials-industry",
                "news_logic": "neutral-news",
                "technical_logic": "business-follow",
                "business_profile": {
                    "product_type": "alloy contacts / relays",
                },
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600045",
                    "name": "EarningsA",
                    "earnings_strategy_score": "67.5",
                    "reason_summary": "growth-hit",
                    "cause_tags": "earnings",
                    "event_date": "2026-04-30",
                }
            ],
            csv_path=Path("earnings.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"].startswith("growth-hit")
    assert "alloy contacts / relays" in rows[0]["reason_summary"]
    assert "业绩驱动" in rows[0]["reason_summary"]
    assert rows[0]["cause_tags"] == "earnings"


def test_build_strategy_focus_rows_uses_earnings_reason_source_even_with_hundred_overlay(monkeypatch) -> None:
    class _FakeCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            assert stock_code == "603045"
            assert stock_name == "福达合金"
            assert signal_type == "earnings_surprise"
            return {
                "reason_summary": "fallback-business-view",
                "cause_tags": ["earnings", "sector_rotation"],
                "industry_logic": "other-metal-materials",
                "news_logic": "neutral-news",
                "technical_logic": "business-follow",
                "business_profile": {
                    "product_type": "触头材料 / 复层触头 / 触头元件",
                },
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "603045",
                    "name": "福达合金",
                    "earnings_strategy_score": "67.5",
                    "reason_summary": "增长指标命中：营收同比 92.7%、净利润同比 3635.1%",
                    "cause_tags": "",
                    "event_date": "2026-04-30",
                }
            ],
            csv_path=Path("earnings.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[
                {
                    "code": "603045",
                    "name": "福达合金",
                    "breakout_quality_score": "14",
                }
            ],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"].startswith("增长指标命中")
    assert "触头材料 / 复层触头 / 触头元件" in rows[0]["reason_summary"]
    assert "业绩驱动" in rows[0]["reason_summary"]
    assert rows[0]["cause_tags"] == "earnings,sector_rotation"
    assert rows[0]["industry_logic"] == "other-metal-materials"
    assert rows[0]["news_logic"] == "neutral-news"
    assert rows[0]["technical_logic"] == "business-follow"


def test_build_strategy_focus_rows_enriches_missing_reason_fields(monkeypatch) -> None:
    class _FakeCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            assert stock_code == "600001"
            assert stock_name == "EnrichA"
            assert signal_type == "trend_leader_unified"
            assert isinstance(metrics_payload, dict)
            return {
                "reason_summary": "业绩释放叠加板块共振",
                "cause_tags": ["earnings", "sector_rotation"],
                "industry_logic": "行业景气上行",
                "news_logic": "未检索到稳定新闻，按中性处理",
                "technical_logic": "强势突破延续",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[{"code": "600001", "name": "EnrichA", "overall_score": "36"}],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"] == "业绩释放叠加板块共振"
    assert rows[0]["cause_tags"] == "earnings,sector_rotation"
    assert rows[0]["cause_tags_zh"] == "业绩/板块轮动"
    assert rows[0]["industry_logic"] == "行业景气上行"
    assert rows[0]["news_logic"] == "未检索到稳定新闻，按中性处理"
    assert rows[0]["technical_logic"] == "强势突破延续"


def test_build_strategy_focus_rows_propagates_explanation_structure_fields(monkeypatch) -> None:
    class _FakeCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            assert stock_code == "002384"
            assert stock_name == "东山精密"
            assert signal_type == "trend_leader_unified"
            return {
                "reason_summary": "AI上游材料链景气驱动",
                "cause_tags": ["supply_demand", "sector_rotation"],
                "industry_logic": "AI上游材料景气扩散",
                "news_logic": "供需偏紧线索增强",
                "technical_logic": "强势突破延续",
                "business_labels": ["PCB", "光模块", "电子材料"],
                "business_summary": "PCB/光模块/电子材料，偏AI上游材料链",
                "chain_role_label": "AI上游材料链",
                "theme_label": "AI算力链映射",
                "theme_source": "business_summary+news_title",
                "authority_judgement": "鍏憡纭",
                "authority_level": "announcement",
                "authority_reason_summary": "鍏憡澶у崟涓庝笟鍔℃櫙姘旂浉浜掑嵃璇?",
                "authority_evidence_digest": "鍏憡: 绛捐澶у崟 / 璐㈡姤: 2026Q1 / 鐮旀姤: 鏈烘瀯缁х画鐪嬪",
                "announcement_evidence_summary": "鍏憡鎻愬埌绛捐閲嶅ぇ璁㈠崟",
                "earnings_evidence_summary": "2026Q1 鍑€鍒╂鼎淇濇寔澧為暱",
                "research_evidence_summary": "鏈烘瀯鐮旀姤缁х画寮哄寲 AI 鏅皵",
                "authority_time_window_days": 7,
                "earnings_anchor": "2026Q1@2026-05-07",
                "supply_demand_bias": "supply_demand",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "002384",
                    "name": "东山精密",
                    "overall_score": "42",
                    "event_date": "2026-05-07",
                    "report_period_label": "2026Q1",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["business_labels"] == "PCB,光模块,电子材料"
    assert rows[0]["business_summary"] == "PCB/光模块/电子材料，偏AI上游材料链"
    assert rows[0]["chain_role_label"] == "AI上游材料链"
    assert rows[0]["theme_label"] == "AI算力链映射"
    assert rows[0]["theme_source"] == "business_summary+news_title"
    assert rows[0]["authority_judgement"] == "鍏憡纭"
    assert rows[0]["authority_level"] == "announcement"
    assert rows[0]["announcement_evidence_summary"] == "鍏憡鎻愬埌绛捐閲嶅ぇ璁㈠崟"
    assert rows[0]["authority_time_window_days"] == 7
    assert rows[0]["earnings_anchor"] == "2026Q1@2026-05-07"
    assert rows[0]["supply_demand_bias"] == "supply_demand"


def test_build_strategy_focus_rows_propagates_market_and_earnings_snapshot_fields(monkeypatch) -> None:
    _install_stub_cause_service(monkeypatch)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "MetricA",
                    "overall_score": "42",
                    "pct_change": "6.8",
                    "pe_ratio": "18.4",
                }
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "600001",
                    "name": "MetricA",
                    "event_date": "2026-04-30",
                    "report_date": "2026-03-31",
                    "report_period_label": "2026Q1",
                    "revenue_amount": "1080000000",
                    "net_profit_amount": "260000000",
                }
            ],
            csv_path=Path("earnings.csv"),
        ),
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["today_change_pct"] == 6.8
    assert rows[0]["pe_ratio"] == 18.4
    assert rows[0]["report_date"] == "2026-03-31"
    assert rows[0]["report_period_label"] == "2026Q1"
    assert rows[0]["revenue_amount"] == 1080000000.0
    assert rows[0]["net_profit_amount"] == 260000000.0


def test_build_strategy_focus_rows_fails_open_when_reason_enrichment_errors(monkeypatch) -> None:
    class _BoomCauseService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _BoomCauseService, raising=False)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[{"code": "600001", "name": "FallbackA", "overall_score": "36"}],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600001"
    assert rows[0]["reason_summary"] == rows[0]["focus_reason"]
    assert rows[0]["cause_tags"] == ""
    assert rows[0]["cause_tags_zh"] == ""


def test_write_strategy_focus_outputs_enriches_missing_market_and_earnings_fields(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        @classmethod
        def _normalize_missing_fields(cls, item):
            return None

        def enrich_items(self, items):
            items[0]["today_change_pct"] = 3.72
            items[0]["pe_ratio"] = 71.0
            items[0]["report_date"] = "2026-03-31"
            items[0]["report_period_label"] = "2026Q1"
            items[0]["revenue_amount"] = 1230000000.0
            items[0]["net_profit_amount"] = 245000000.0
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)
    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    rows = [
        {
            "code": "300476",
            "name": "胜宏科技",
            "tier": "watch",
            "ab_bucket": "B",
            "priority_score": 167.95,
            "signal_keys": "trend_leader",
            "signal_types": "trend_leader_unified_watchlist",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": "PCB 主线延续",
            "cause_tags": "sector_rotation,overseas_theme",
            "cause_tags_zh": "板块轮动/海外映射",
            "industry_logic": "当前更像是 印制电路板 方向的结构性走强",
            "news_logic": "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理",
            "technical_logic": "胜宏科技 命中 trend_leader_unified 信号",
            "review_stage_type": "turning_point",
            "review_stage_label": "拐点",
            "driver_type": "turning_point_watch",
            "driver_label": "拐点观察型",
            "driver_reason": "watch",
        }
    ]

    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    assert written_rows[0]["today_change_pct"] == "3.72"
    assert written_rows[0]["pe_ratio"] == "71.0"
    assert written_rows[0]["report_date"] == "2026-03-31"
    assert written_rows[0]["report_period_label"] == "2026Q1"
    assert written_rows[0]["revenue_amount"] == "1230000000.0"
    assert written_rows[0]["net_profit_amount"] == "245000000.0"
    md_text = md_path.read_text(encoding="utf-8")
    assert "涨幅 3.72%" in md_text
    assert "PE 71.0" in md_text
    assert "2026Q1" in md_text
    assert "营收 12.30亿" in md_text
    assert "净利 2.45亿" in md_text


def test_write_strategy_focus_outputs_prefills_hidden_rows_from_authority_summary(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        @classmethod
        def _normalize_missing_fields(cls, item):
            if str(item.get("authority_reason_summary") or "").strip():
                item["report_period_label"] = "2026Q1"
                item["net_profit_amount"] = 80841900.0

        def enrich_items(self, items):
            for item in items:
                item["today_change_pct"] = 3.0
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = []
    for idx in range(26):
        rows.append(
            {
                "code": f"W{idx:03d}",
                "name": f"Watch{idx}",
                "tier": "watch",
                "ab_bucket": "B",
                "priority_score": float(100 - idx),
                "signal_keys": "hundred_day_high",
                "signal_types": "hundred_day_high",
                "trend_hundred_relation": "hundred_only",
                "focus_reason": "hundred_day_high",
                "reason_summary": f"Watch{idx} summary",
                "cause_tags": "earnings",
                "cause_tags_zh": "业绩",
                "industry_logic": "",
                "news_logic": "",
                "technical_logic": "",
                "review_stage_type": "pure_rotation",
                "review_stage_label": "纯轮动",
                "driver_type": "theme_sentiment_driven",
                "driver_label": "题材情绪型",
                "driver_reason": "watch",
                "authority_reason_summary": "",
                "report_period_label": "",
                "net_profit_amount": "",
            }
        )
    rows[-1]["code"] = "603618"
    rows[-1]["name"] = "杭电股份"
    rows[-1]["authority_reason_summary"] = "财报确认：2026Q1，2026-03-31，净利润8084.19万元，营收同比+11.1%，净利同比+280.0%，上涨更偏业绩兑现驱动。"

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    hidden_row = next(row for row in written_rows if row["code"] == "603618")
    assert hidden_row["report_period_label"] == "2026Q1"
    assert hidden_row["net_profit_amount"] == "80841900.0"


def test_write_strategy_focus_outputs_persists_peer_check_fields(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        @classmethod
        def _normalize_missing_fields(cls, item):
            return None

        def enrich_items(self, items):
            items[0]["peer_group_label"] = "PCB"
            items[0]["peer_resonance_summary"] = "同日 PCB 方向有3只进入焦点池，胜宏科技位列龙头。"
            items[0]["leader_position_summary"] = "当前在 PCB 焦点组内位列龙头。"
            items[0]["turning_point_peer_summary"] = "同组已有2只处在拐点，说明更像行业扩散初段。"
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    rows = [
        {
            "code": "300476",
            "name": "胜宏科技",
            "tier": "core",
            "ab_bucket": "B",
            "priority_score": 167.95,
            "signal_keys": "trend_leader",
            "signal_types": "trend_leader_unified_watchlist",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": "PCB 主线延续",
            "review_stage_type": "turning_point",
            "review_stage_label": "拐点",
            "driver_type": "turning_point_watch",
            "driver_label": "拐点观察型",
            "driver_reason": "watch",
        }
    ]

    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    assert written_rows[0]["peer_group_label"] == "PCB"
    assert "3只" in written_rows[0]["peer_resonance_summary"]
    assert "龙头" in written_rows[0]["leader_position_summary"]
    assert "拐点" in written_rows[0]["turning_point_peer_summary"]


def test_write_strategy_focus_outputs_persists_display_reason_summary_and_uses_it_in_markdown(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "enrich_market_fields",
        lambda self, items: items,
    )
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "enrich_items",
        lambda self, items: items,
    )

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    rows = [
        {
            "code": "300476",
            "name": "胜宏科技",
            "tier": "core",
            "ab_bucket": "B",
            "priority_score": 167.95,
            "signal_keys": "trend_leader",
            "signal_types": "trend_leader_unified_watchlist",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": (
                "当前更像是 PCB 方向的结构性走强；主线判断更偏 AI主线扩散（依据：业务标签 / 业绩披露）；"
                "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理；短线先按技术突破与资金轮动延续看待。"
            ),
            "industry_logic": "当前更像是 PCB 方向的结构性走强；主线判断更偏 AI主线扩散（依据：业务标签 / 业绩披露）",
            "news_logic": "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理",
            "technical_logic": "短线先按技术突破与资金轮动延续看待；胜宏科技 命中 trend_leader_unified 信号",
            "driver_type": "turning_point_watch",
            "driver_label": "拐点观察型",
            "driver_reason": "watch",
        }
    ]

    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))
    markdown = md_path.read_text(encoding="utf-8")

    assert written_rows[0]["display_reason_summary"]
    assert "当前未检索到足够稳定的公开消息催化" not in written_rows[0]["display_reason_summary"]
    assert written_rows[0]["display_reason_summary"] in markdown
    assert "当前未检索到足够稳定的公开消息催化" not in markdown


def test_write_strategy_focus_outputs_deemphasizes_broad_ai_mainline_when_business_is_specific(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "enrich_market_fields",
        lambda self, items: items,
        raising=False,
    )
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "enrich_items",
        lambda self, items: items,
        raising=False,
    )

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    rows = [
        {
            "code": "300476",
            "name": "胜宏科技",
            "tier": "watch",
            "ab_bucket": "B",
            "priority_score": 157.05,
            "signal_keys": "trend_leader",
            "signal_types": "trend_leader_unified",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": "当前更像是 PCB 方向的结构性走强；业务辨识度更偏 PCB，偏AI算力供应链；主线判断更偏 AI主线扩散（依据：业务标签 / 主题映射 / 业绩披露）；胜宏科技 命中 trend_leader_unified 信号。",
            "industry_logic": "当前更像是 PCB 方向的结构性走强；业务辨识度更偏 PCB，偏AI算力供应链；主线判断更偏 AI主线扩散（依据：业务标签 / 主题映射 / 业绩披露）",
            "technical_logic": "胜宏科技 命中 trend_leader_unified 信号；短线先按技术突破与资金轮动延续看待；业务主线可先按 PCB，偏AI算力供应链 跟踪",
            "business_summary": "PCB，偏AI算力供应链",
            "preferred_industry_label": "PCB",
            "driver_type": "turning_point_watch",
            "driver_label": "拐点观察型",
            "driver_reason": "watch",
        }
    ]

    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    display_reason = written_rows[0]["display_reason_summary"]
    assert "当前更像是 PCB 方向走强" in display_reason
    assert "业务更偏 PCB，偏AI算力供应链" in display_reason
    assert "AI主线扩散" not in display_reason
    assert "胜宏科技 命中 trend_leader_unified 信号" in display_reason


def test_write_strategy_focus_outputs_normalizes_wide_industry_clause_before_export(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "enrich_market_fields",
        lambda self, items: items,
        raising=False,
    )
    monkeypatch.setattr(
        fast_bundle.FastReviewFocusService,
        "_annotate_peer_context",
        lambda self, items: None,
        raising=False,
    )

    rows = [
        {
            "code": "603618",
            "name": "杭电股份",
            "tier": "watch",
            "priority_score": 40.0,
            "reason_summary": "当前更像是 光通信/电力设备/铜箔 方向走强；业务更偏 光通信/电力设备/铜箔，偏AI上游材料链；主线判断更偏 AI上游材料扩散（依据：业务标签 / 业绩披露）；杭电股份 命中 hundred_day_high 信号。",
            "industry_logic": "当前更像是 光通信/电力设备/铜箔 方向的结构性走强；业务辨识度更偏 光通信/电力设备/铜箔，偏AI上游材料链；宽口径行业标签仍归在 电线电缆的研发、生产、销售和服务，但交易辨识度更偏 光通信/电力设备/铜箔；更接近AI上游材料链景气扩散，仍属景气驱动，不像纯题材空转；更像AI主线行业景气向上游材料链扩散；主线判断更偏 AI上游材料扩散（依据：业务标签 / 业绩披露）",
            "business_summary": "光通信/电力设备/铜箔，偏AI上游材料链",
            "preferred_industry_label": "光通信/电力设备/铜箔",
            "cause_tags": "earnings,sector_rotation",
            "cause_tags_zh": "业绩/板块轮动",
            "driver_type": "theme_sentiment_driven",
            "driver_label": "题材情绪型",
            "driver_reason": "watch",
            "review_stage_type": "pure_rotation",
            "review_stage_label": "纯轮动",
            "today_change_pct": "",
            "pe_ratio": "",
        }
    ]

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    written_rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig")))
    assert "宽口径行业标签仍归在 电线电缆的研发、生产、销售和服务" not in written_rows[0]["industry_logic"]
    assert "宽口径行业标签仍归在 电力设备" in written_rows[0]["industry_logic"]


def test_write_strategy_focus_outputs_backfills_hidden_rows_market_fields_without_expanding_heavy_scope(
    monkeypatch, tmp_path: Path
) -> None:
    captured = {
        "quote_count": 0,
        "quote_codes": [],
        "heavy_count": 0,
        "heavy_codes": [],
    }

    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        @classmethod
        def _normalize_missing_fields(cls, item):
            return None

        def enrich_market_fields(self, items):
            captured["quote_count"] = len(items)
            captured["quote_codes"] = [item.get("code") for item in items]
            for item in items:
                item["today_change_pct"] = 4.26
                item["pe_ratio"] = 29.8
            return items

        def enrich_items(self, items):
            captured["heavy_count"] = len(items)
            captured["heavy_codes"] = [item.get("code") for item in items]
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = []
    for idx in range(26):
        rows.append(
            {
                "code": f"W{idx:03d}",
                "name": f"Watch{idx}",
                "tier": "watch",
                "ab_bucket": "B",
                "priority_score": float(100 - idx),
                "signal_keys": "hundred_day_high",
                "signal_types": "hundred_day_high",
                "trend_hundred_relation": "hundred_only",
                "focus_reason": "hundred_day_high",
                "reason_summary": f"Watch{idx} summary",
                "cause_tags": "",
                "cause_tags_zh": "",
                "industry_logic": "",
                "news_logic": "",
                "technical_logic": "",
                "review_stage_type": "pure_rotation",
                "review_stage_label": "纯轮动",
                "driver_type": "theme_sentiment_driven",
                "driver_label": "题材情绪型",
                "driver_reason": "watch",
                "today_change_pct": "",
                "pe_ratio": "",
            }
        )
    rows[-1]["code"] = "603618"
    rows[-1]["name"] = "杭电股份"

    csv_path = tmp_path / "fast_review_strategy_focus.csv"
    md_path = tmp_path / "fast_review_strategy_focus.md"
    fast_bundle._write_strategy_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    hidden_row = next(row for row in written_rows if row["code"] == "603618")
    assert hidden_row["today_change_pct"] == "4.26"
    assert hidden_row["pe_ratio"] == "29.8"
    assert captured["quote_count"] == 26
    assert captured["quote_codes"][-1] == "603618"
    assert captured["heavy_count"] == 10
    assert captured["heavy_codes"] == [f"W{idx:03d}" for idx in range(10)]


def test_write_strategy_focus_outputs_limits_export_enrichment_scope(monkeypatch, tmp_path: Path) -> None:
    captured = {"count": 0, "codes": []}

    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        @classmethod
        def _normalize_missing_fields(cls, item):
            return None

        def enrich_items(self, items):
            captured["count"] = len(items)
            captured["codes"] = [item.get("code") for item in items]
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = []
    for idx in range(12):
        rows.append({"code": f"C{idx:03d}", "name": f"Core{idx}", "tier": "core", "priority_score": 200 - idx})
    for idx in range(12):
        rows.append({"code": f"W{idx:03d}", "name": f"Watch{idx}", "tier": "watch", "priority_score": 100 - idx})
    for idx in range(8):
        rows.append({"code": f"L{idx:03d}", "name": f"Low{idx}", "tier": "low_priority", "priority_score": 50 - idx})

    fast_bundle._write_strategy_focus_outputs(
        rows=rows,
        csv_path=tmp_path / "fast_review_strategy_focus.csv",
        md_path=tmp_path / "fast_review_strategy_focus.md",
    )

    assert captured["count"] == 25
    assert captured["codes"][:10] == [f"C{idx:03d}" for idx in range(10)]
    assert captured["codes"][10:20] == [f"W{idx:03d}" for idx in range(10)]
    assert captured["codes"][20:] == [f"L{idx:03d}" for idx in range(5)]


def test_build_summary_markdown_includes_concise_strategy_focus(tmp_path: Path) -> None:
    focus_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "tier": "core",
            "priority_score": 121.5,
            "signal_keys": "trend_leader,hundred_day_high,earnings",
            "trend_hundred_relation": "intersection",
            "focus_reason": "trend+hundred+earnings",
            "ab_bucket": "A",
            "today_change_pct": 7.12,
            "pe_ratio": 28.4,
            "report_period_label": "2026Q1",
            "revenue_amount": 4560000000.0,
            "net_profit_amount": 780000000.0,
            "driver_label": "业绩兑现型",
        },
        {
            "code": "600002",
            "name": "WatchB",
            "tier": "watch",
            "priority_score": 70.0,
            "signal_keys": "trend_leader",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "ab_bucket": "B",
            "driver_label": "题材情绪型",
        },
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=focus_rows,
    )

    assert "策略精简焦点" in summary
    assert "trend_leader_unified ∩ hundred_day_high：`1`" in summary
    assert "trend_leader_unified only：`1`" in summary
    assert "CoreA" in summary
    assert "WatchB" in summary
    assert "涨幅 7.12%" in summary
    assert "PE 28.4" in summary
    assert "2026Q1" in summary
    assert "营收 45.60亿" in summary
    assert "净利 7.80亿" in summary


def test_build_summary_markdown_marks_review_context_for_strategy_focus(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "600001",
                "name": "CoreA",
                "tier": "watch",
                "priority_score": 80.0,
                "signal_keys": "hundred_day_high",
                "trend_hundred_relation": "hundred_only",
                "ab_bucket": "B",
                "driver_label": "棰樻潗鎯呯华鍨?",
                "review_stage_label": "绾疆鍔?",
                "review_context_label": "业绩空窗期",
                "review_context_reason": "当日 earnings 候选为 0，前排优先看交叉强样本与强图形百日新高。",
            }
        ],
    )

    assert "业绩空窗期" in summary
    assert "当日 earnings 候选为 0" in summary


def test_build_summary_markdown_shows_source_and_export_count_when_clipped(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[{"code": "600001", "name": "A"} for _ in range(30)],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=226,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[],
    )

    assert "| hundred_day_high | hundred_day_high | 226 (export 30) | 12.30 |" in summary


def test_build_summary_markdown_includes_hundred_day_high_spotlight_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[
                    {"code": "600001", "name": "OverlapA", "pct_change": 6.1, "turnover_rate": 3.2},
                    {"code": "600002", "name": "HundredB", "pct_change": 9.9, "turnover_rate": 6.8},
                    {"code": "600003", "name": "HundredC", "pct_change": 4.2, "turnover_rate": 2.1},
                ],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=3,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "600001",
                "name": "OverlapA",
                "today_change_pct": 6.1,
                "pe_ratio": 25.6,
                "report_period_label": "2026Q1",
                "net_profit_amount": 320000000.0,
            }
        ],
    )

    assert "百日新高 Top 3" in summary
    assert "OverlapA" in summary
    assert "HundredB" in summary
    assert "HundredC" in summary
    assert "涨幅 6.10% / 换手 3.20% / PE 25.6 / 2026Q1 / 净利 3.20亿" in summary
    assert "涨幅 9.90% / 换手 6.80%" in summary


def test_build_summary_markdown_includes_hundred_day_high_spotlight_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[
                    {"code": "600001", "name": "OverlapA", "pct_change": 6.1, "turnover_rate": 3.2},
                    {"code": "600002", "name": "HundredB", "pct_change": 9.9, "turnover_rate": 6.8},
                    {"code": "600003", "name": "HundredC", "pct_change": 4.2, "turnover_rate": 2.1},
                ],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=3,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "600001",
                "name": "OverlapA",
                "today_change_pct": 6.1,
                "pe_ratio": 25.6,
                "report_period_label": "2026Q1",
                "net_profit_amount": 320000000.0,
            }
        ],
    )

    assert "百日新高 Top 3" in summary
    assert "OverlapA" in summary
    assert "HundredB" in summary
    assert "HundredC" in summary
    assert "涨幅 6.10% / 换手 3.20% / PE 25.6 / 2026Q1" in summary
    assert "涨幅 9.90% / 换手 6.80%" in summary


def test_build_hundred_day_snapshot_summary_falls_back_to_breakout_fields() -> None:
    snapshot = fast_bundle._build_hundred_day_snapshot_summary(
        {
            "close": 62.14,
            "total_market_cap_yi": 84.17,
            "breakout_quality_score": 14.0,
            "minervini_template_score": 10.0,
        }
    )

    assert snapshot == "收盘 62.14 / 市值 84.17亿 / 突破 14.0 / 模板 10.0"


def test_build_hundred_day_snapshot_summary_includes_chart_pattern_summary() -> None:
    snapshot = fast_bundle._build_hundred_day_snapshot_summary(
        {
            "today_change_pct": 6.8,
            "turnover_rate": 4.2,
            "chart_pattern_summary": "横盘突破型",
            "breakout_quality_score": 15.0,
        }
    )

    assert snapshot == "涨幅 6.80% / 换手 4.20% / 横盘突破型 / 突破 15.0"


def test_build_hundred_day_snapshot_summary_prefers_business_hint_before_pe() -> None:
    snapshot = fast_bundle._build_hundred_day_snapshot_summary(
        {
            "today_change_pct": 3.8,
            "business_summary": "PCB/光模块/电子材料，偏AI上游材料链",
            "chart_pattern_summary": "健康慢涨型",
            "pe_ratio": 88.6,
            "report_period_label": "2026Q1",
            "net_profit_amount": 1110000000.0,
        }
    )

    assert snapshot == "涨幅 3.80% / PCB/光模块/电子材料 / 健康慢涨型 / PE 88.6"


def test_hundred_day_spotlight_prioritizes_focus_intersections_and_a_bucket(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[
                    {"code": "600003", "name": "HundredOnly", "pct_change": 9.5},
                    {"code": "600001", "name": "IntersectA", "pct_change": 1.2},
                    {"code": "600002", "name": "IntersectB", "pct_change": 7.8},
                ],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=3,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "600001",
                "name": "IntersectA",
                "bucket": "A类",
                "score": 295.0,
                "signals": "trend_leader,hundred_day_high",
                "today_change_pct": 1.2,
                "pe_ratio": 55.0,
                "report_period_label": "2026Q1",
                "net_profit_amount": 250000000.0,
            },
            {
                "code": "600002",
                "name": "IntersectB",
                "bucket": "B类",
                "score": 220.0,
                "signals": "trend_leader,hundred_day_high",
                "today_change_pct": 7.8,
                "pe_ratio": 42.0,
                "report_period_label": "2026Q1",
                "net_profit_amount": 180000000.0,
            },
        ],
    )

    spotlight_start = summary.index("## 百日新高 Top 3")
    strong_section_pos = summary.index("### 交叉强样本", spotlight_start)
    pure_section_pos = summary.index("### 纯百日新高", spotlight_start)
    intersect_a_pos = summary.index("| 600001 | IntersectA |", spotlight_start)
    intersect_b_pos = summary.index("| 600002 | IntersectB |", spotlight_start)
    hundred_only_pos = summary.index("| 600003 | HundredOnly |", spotlight_start)

    assert strong_section_pos < pure_section_pos
    assert strong_section_pos < intersect_a_pos < intersect_b_pos < pure_section_pos < hundred_only_pos


def test_hundred_day_spotlight_prioritizes_better_chart_patterns_within_pure_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[
                    {
                        "code": "600003",
                        "name": "PlainC",
                        "pct_change": 9.9,
                        "chart_pattern_label": "plain_breakout",
                        "chart_pattern_summary": "图形一般",
                    },
                    {
                        "code": "600002",
                        "name": "HealthyB",
                        "pct_change": 6.6,
                        "chart_pattern_label": "healthy_trend",
                        "chart_pattern_summary": "健康慢涨型",
                    },
                    {
                        "code": "600001",
                        "name": "BaseA",
                        "pct_change": 4.4,
                        "chart_pattern_label": "base_breakout",
                        "chart_pattern_summary": "横盘突破型",
                    },
                ],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=3,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[],
    )

    spotlight_start = summary.index("## 百日新高 Top 3")
    pure_section_pos = summary.index("### 纯百日新高", spotlight_start)
    base_pos = summary.index("| 600001 | BaseA |", pure_section_pos)
    healthy_pos = summary.index("| 600002 | HealthyB |", pure_section_pos)
    plain_pos = summary.index("| 600003 | PlainC |", pure_section_pos)

    assert pure_section_pos < base_pos < healthy_pos < plain_pos
    assert "横盘突破型" in summary
    assert "健康慢涨型" in summary
    assert "图形一般" in summary


def test_hundred_day_spotlight_uses_combined_limit_across_two_sections(tmp_path: Path) -> None:
    original_limit = fast_bundle.DEFAULT_HUNDRED_DAY_SUMMARY_SPOTLIGHT_LIMIT
    fast_bundle.DEFAULT_HUNDRED_DAY_SUMMARY_SPOTLIGHT_LIMIT = 4
    try:
        summary = fast_bundle._build_summary_markdown(
            snapshot_date=date(2026, 4, 20),
            signal_results=[
                fast_bundle.SignalResult(
                    key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                    signal_type="hundred_day_high",
                    label="hundred",
                    rows=[
                        {"code": "600001", "name": "IntersectA", "pct_change": 8.8},
                        {"code": "600002", "name": "IntersectB", "pct_change": 7.7},
                        {"code": "600003", "name": "PureC", "pct_change": 6.6},
                        {"code": "600004", "name": "PureD", "pct_change": 5.5},
                        {"code": "600005", "name": "PureE", "pct_change": 4.4},
                    ],
                    csv_path=tmp_path / "hundred.csv",
                    duration_sec=12.3,
                    source_row_count=5,
                )
            ],
            skipped_signals=[],
            unified_csv=tmp_path / "fast_review_candidates.csv",
            resonance_csv=tmp_path / "fast_review_resonance.csv",
            resonance_md=tmp_path / "fast_review_resonance.md",
            resonance_rows=[],
            suggested_windows="1,3,5,10",
            strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
            strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
            strategy_focus_rows=[
                {
                    "code": "600001",
                    "name": "IntersectA",
                    "bucket": "A类",
                    "score": 295.0,
                    "signals": "trend_leader,hundred_day_high",
                },
                {
                    "code": "600002",
                    "name": "IntersectB",
                    "bucket": "B类",
                    "score": 220.0,
                    "signals": "trend_leader,hundred_day_high",
                },
            ],
        )
    finally:
        fast_bundle.DEFAULT_HUNDRED_DAY_SUMMARY_SPOTLIGHT_LIMIT = original_limit

    assert "## 百日新高 Top 4" in summary
    assert "### 交叉强样本（top 2 / 2）" in summary
    assert "### 纯百日新高（top 2 / 3）" in summary
    assert "| 600005 | PureE |" not in summary


def test_build_summary_markdown_includes_trend_continuation_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "002281",
                "name": "光迅科技",
                "tier": "watch",
                "ab_bucket": "B",
                "priority_score": 136.01,
                "trend_hundred_relation": "trend_only",
                "review_display_group": "trend_continuation",
                "review_display_group_label": "纯趋势延续",
                "review_stage_label": "拐点",
                "today_change_pct": 6.82,
                "pe_ratio": 163.4,
                "report_period_label": "2026Q1",
                "net_profit_amount": 240000000.0,
                "display_reason_summary": "当前更像是 光模块/光通信 方向走强",
            }
        ],
    )

    assert "## 纯趋势延续 Top 1" in summary
    assert "### 纯趋势延续（top 1 / 1）" in summary
    assert "| 002281 | 光迅科技 | B类 | 拐点 |" in summary
    assert "当前更像是 光模块/光通信 方向走强" in summary


def test_build_summary_markdown_places_hundred_day_spotlight_before_strategy_focus_section(tmp_path: Path) -> None:
    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[
            fast_bundle.SignalResult(
                key=fast_bundle.SIGNAL_HUNDRED_DAY_HIGH,
                signal_type="hundred_day_high",
                label="hundred",
                rows=[{"code": "600001", "name": "HundredA", "pct_change": 8.8}],
                csv_path=tmp_path / "hundred.csv",
                duration_sec=12.3,
                source_row_count=1,
            )
        ],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[
            {
                "code": "300476",
                "name": "胜宏科技",
                "tier": "watch",
                "ab_bucket": "B",
                "priority_score": 120.0,
                "signal_keys": "trend_leader",
                "signal_types": "trend_leader_unified",
                "focus_reason": "trend",
                "display_reason_summary": "当前更像是 PCB 方向走强；胜宏科技 命中 trend_leader_unified 信号。",
                "cause_tags_zh": "业绩/板块轮动",
            }
        ],
    )

    hundred_pos = summary.index("## 百日新高 Top 1")
    focus_pos = summary.index("## 策略精简焦点")
    assert hundred_pos < focus_pos


def test_build_summary_markdown_includes_earnings_focus_section(tmp_path: Path) -> None:
    earnings_focus_rows = [
        {
            "code": "300502",
            "name": "新易盛",
            "earnings_strategy_score": 91.8,
            "market_expectation_reference_label": "beat_ref",
            "market_expectation_summary": "2026年EPS一致预期 17.54 元（19家机构）",
            "event_date": "2026-04-24",
            "trend_resonance_label": "intersection",
            "today_change_pct": 6.8,
            "pe_ratio": 18.4,
            "report_period_label": "2026Q1",
            "net_profit_amount": 260000000.0,
            "reason_summary": "增长指标命中；业务侧先按 光模块/光通信 跟踪",
            "display_reason_summary": "当前更像是 光模块/光通信 方向走强；主线判断更偏 AI主线扩散；新易盛 命中 earnings 信号。",
            "cause_tags_zh": "业绩/板块轮动",
        }
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 28),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[],
        earnings_focus_csv=tmp_path / "fast_review_earnings_focus.csv",
        earnings_focus_md=tmp_path / "fast_review_earnings_focus.md",
        earnings_focus_rows=earnings_focus_rows,
    )

    assert "今日业绩焦点 15 只" in summary
    assert "新易盛" in summary
    assert "beat_ref" in summary
    assert "2026年EPS一致预期 17.54 元" in summary
    assert "当前更像是 光模块/光通信 方向走强" in summary
    assert "增长指标命中；业务侧先按 光模块/光通信 跟踪" not in summary
    assert "业绩/板块轮动" in summary
    assert "snapshot" in summary
    assert "涨幅 6.80%" in summary
    assert "PE 18.4" in summary
    assert "2026Q1" in summary
    assert "净利 2.60亿" in summary


def test_build_summary_markdown_includes_rise_reason_section(tmp_path: Path) -> None:
    focus_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "tier": "core",
            "priority_score": 121.5,
            "signal_keys": "trend_leader,hundred_day_high",
            "trend_hundred_relation": "intersection",
            "focus_reason": "trend+hundred",
            "reason_summary": "行业爆发带动龙头继续走强",
            "display_reason_summary": "当前更像是 PCB 方向走强；主线判断更偏 AI主线扩散；CoreA 命中 trend_leader_unified 信号。",
            "cause_tags_zh": "板块轮动/政策",
        },
        {
            "code": "600002",
            "name": "WatchB",
            "tier": "watch",
            "priority_score": 70.0,
            "signal_keys": "trend_leader",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
            "reason_summary": "业绩释放后延续强势",
            "display_reason_summary": "当前更像是 光模块 方向走强；WatchB 命中 trend_leader_unified 信号。",
            "cause_tags_zh": "业绩",
        },
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=focus_rows,
    )

    assert "强势股上涨原因摘要" in summary
    assert "当前更像是 PCB 方向走强" in summary
    assert "板块轮动/政策" in summary
    assert "当前更像是 光模块 方向走强" in summary


def test_load_manual_review_label_rows_filters_by_snapshot_date(tmp_path: Path) -> None:
    labels_path = tmp_path / "manual_labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "snapshot_date": "2026-05-05",
                        "code": "600001",
                        "name": "CoreA",
                        "expected_ab_bucket": "A",
                        "expected_driver_label": "业绩兑现型",
                    },
                    {
                        "snapshot_date": "2026-05-06",
                        "code": "600002",
                        "name": "OtherDay",
                        "expected_ab_bucket": "B",
                        "expected_driver_label": "题材情绪型",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows = fast_bundle._load_manual_review_label_rows(
        labels_path=labels_path,
        snapshot_date=date(2026, 5, 5),
    )

    assert len(rows) == 1
    assert rows[0]["code"] == "600001"
    assert rows[0]["expected_ab_bucket"] == "A"
    assert rows[0]["expected_driver_label"] == "业绩兑现型"


def test_build_manual_review_calibration_rows_compares_expected_and_actual() -> None:
    strategy_focus_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "ab_bucket": "A",
            "driver_type": "earnings_delivery",
            "driver_label": "业绩兑现型",
            "tier": "core",
            "signal_keys": "trend_leader,hundred_day_high,earnings",
        },
        {
            "code": "600002",
            "name": "WatchB",
            "ab_bucket": "B",
            "driver_type": "event_driven",
            "driver_label": "事件驱动型",
            "tier": "watch",
            "signal_keys": "trend_leader",
        },
    ]
    manual_rows = [
        {
            "snapshot_date": "2026-05-05",
            "code": "600001",
            "name": "CoreA",
            "expected_ab_bucket": "A",
            "expected_driver_type": "earnings_delivery",
            "expected_driver_label": "业绩兑现型",
            "notes": "confirmed earnings case",
        },
        {
            "snapshot_date": "2026-05-05",
            "code": "600002",
            "name": "WatchB",
            "expected_ab_bucket": "B",
            "expected_driver_type": "theme_sentiment_driven",
            "expected_driver_label": "题材情绪型",
            "notes": "should stay theme-driven",
        },
        {
            "snapshot_date": "2026-05-05",
            "code": "600003",
            "name": "MissedC",
            "expected_ab_bucket": "B",
            "expected_driver_type": "turning_point_watch",
            "expected_driver_label": "拐点观察型",
            "notes": "missing from outputs",
        },
    ]

    rows = fast_bundle._build_manual_review_calibration_rows(
        strategy_focus_rows=strategy_focus_rows,
        manual_label_rows=manual_rows,
    )

    assert rows[0]["status"] == "matched"
    assert rows[0]["ab_match"] is True
    assert rows[0]["driver_match"] is True

    assert rows[1]["status"] == "mismatch"
    assert rows[1]["ab_match"] is True
    assert rows[1]["driver_match"] is False
    assert rows[1]["actual_driver_label"] == "事件驱动型"

    assert rows[2]["status"] == "missing_in_results"
    assert rows[2]["actual_driver_label"] == ""


def test_build_summary_markdown_includes_manual_review_calibration_section(tmp_path: Path) -> None:
    calibration_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "status": "matched",
            "expected_ab_bucket": "A",
            "actual_ab_bucket": "A",
            "expected_driver_label": "业绩兑现型",
            "actual_driver_label": "业绩兑现型",
            "notes": "confirmed earnings case",
        },
        {
            "code": "600002",
            "name": "WatchB",
            "status": "mismatch",
            "expected_ab_bucket": "B",
            "actual_ab_bucket": "B",
            "expected_driver_label": "题材情绪型",
            "actual_driver_label": "事件驱动型",
            "notes": "too aggressive",
        },
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 5, 5),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=[],
        manual_review_csv=tmp_path / "fast_review_manual_calibration.csv",
        manual_review_md=tmp_path / "fast_review_manual_calibration.md",
        manual_review_rows=calibration_rows,
    )

    assert "人工复盘校准" in summary
    assert "matched" in summary
    assert "mismatch" in summary
    assert "事件驱动型" in summary


def test_load_signal_rows_keeps_strategy_focus_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "trend.csv"
    csv_path.write_text(
        (
            "code,name,overall_score,capital_consensus_score,capital_profile_score,"
            "sector_leadership_score,recognizability_score,primary_board_name,risk_flags,"
            "market_expectation_summary,market_expectation_reference_label,event_date,"
            "today_change_pct,pe_ratio,report_date,report_period_label,revenue_amount,net_profit_amount\n"
            "600001,Sample,36,2,68,2,1,AI,,2026年EPS一致预期 1.23 元,beat_ref,2026-04-18,5.2,18.6,2026-03-31,2026Q1,1080000000,260000000\n"
        ),
        encoding="utf-8",
    )

    rows = fast_bundle._load_signal_rows_from_csv(
        csv_path=csv_path,
        signal_type="trend_leader_unified",
        signal_label="trend",
    )

    assert rows[0]["capital_consensus_score"] == "2"
    assert rows[0]["capital_profile_score"] == "68"
    assert rows[0]["sector_leadership_score"] == "2"
    assert rows[0]["recognizability_score"] == "1"
    assert rows[0]["primary_board_name"] == "AI"
    assert rows[0]["market_expectation_summary"] == "2026年EPS一致预期 1.23 元"
    assert rows[0]["market_expectation_reference_label"] == "beat_ref"
    assert rows[0]["event_date"] == "2026-04-18"
    assert rows[0]["today_change_pct"] == "5.2"
    assert rows[0]["pe_ratio"] == "18.6"
    assert rows[0]["report_date"] == "2026-03-31"
    assert rows[0]["report_period_label"] == "2026Q1"
    assert rows[0]["revenue_amount"] == "1080000000"
    assert rows[0]["net_profit_amount"] == "260000000"


def test_load_signal_rows_keeps_hundred_day_chart_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "hundred.csv"
    csv_path.write_text(
        (
            "code,name,breakout_quality_score,chart_pattern_label,chart_pattern_score,"
            "chart_pattern_summary,base_breakout_score,healthy_trend_score\n"
            "600001,Sample,14,base_breakout,16,妯洏绐佺牬鍨?,15,8\n"
        ),
        encoding="utf-8",
    )

    rows = fast_bundle._load_signal_rows_from_csv(
        csv_path=csv_path,
        signal_type="hundred_day_high",
        signal_label="hundred",
    )

    assert rows[0]["breakout_quality_score"] == "14"
    assert rows[0]["chart_pattern_label"] == "base_breakout"
    assert rows[0]["chart_pattern_score"] == "16"
    assert rows[0]["chart_pattern_summary"] == "妯洏绐佺牬鍨?"
    assert rows[0]["base_breakout_score"] == "15"
    assert rows[0]["healthy_trend_score"] == "8"


def test_load_signal_rows_keeps_daily_slow_rise_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "daily_slow_rise.csv"
    csv_path.write_text(
        (
            "code,name,trend_pattern_label,advance_return_pct,advance_max_drawdown_pct,max_single_day_gain_pct\n"
            "000811,冰轮环境,base_to_trend,114.5434,3.7352,10.0299\n"
        ),
        encoding="utf-8",
    )

    rows = fast_bundle._load_signal_rows_from_csv(
        csv_path=csv_path,
        signal_type="daily_slow_rise",
        signal_label="daily",
    )

    assert rows[0]["trend_pattern_label"] == "base_to_trend"
    assert rows[0]["advance_return_pct"] == "114.5434"
    assert rows[0]["advance_max_drawdown_pct"] == "3.7352"
    assert rows[0]["max_single_day_gain_pct"] == "10.0299"


def test_append_daily_slow_rise_markdown_uses_selector_metrics() -> None:
    lines: list[str] = []
    signal_results = [
        fast_bundle.SignalResult(
            key=fast_bundle.SIGNAL_DAILY_SLOW_RISE,
            signal_type="daily_slow_rise",
            label="daily",
            rows=[
                {
                    "code": "000811",
                    "name": "冰轮环境",
                    "trend_pattern_label": "base_to_trend",
                    "advance_return_pct": "114.5434",
                    "advance_max_drawdown_pct": "3.7352",
                    "max_single_day_gain_pct": "10.0299",
                }
            ],
            csv_path=Path("daily_slow_rise.csv"),
        )
    ]

    fast_bundle._append_daily_slow_rise_markdown(lines, signal_results=signal_results, limit=5)
    markdown = "\n".join(lines)

    assert "base_to_trend" in markdown
    assert "114.54" in markdown
    assert "3.74" in markdown
    assert "10.03" in markdown


def test_build_earnings_focus_rows_prioritizes_earnings_and_trend_resonance() -> None:
    signal_results = [
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="earnings",
            rows=[
                {
                    "code": "300502",
                    "name": "新易盛",
                    "earnings_strategy_score": "91.8",
                    "market_expectation_reference_label": "beat_ref",
                    "market_expectation_summary": "2026年EPS一致预期 17.54 元",
                    "event_date": "2026-04-24",
                    "report_period_label": "2026Q1",
                    "net_profit_amount": "260000000",
                },
                {
                    "code": "300600",
                    "name": "普通业绩",
                    "earnings_strategy_score": "88.0",
                    "market_expectation_reference_label": "unknown",
                    "market_expectation_summary": "",
                    "event_date": "2026-04-27",
                },
            ],
            csv_path=Path("earnings.csv"),
        ),
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "300502",
                    "name": "新易盛",
                    "overall_score": "36",
                    "today_change_pct": "6.8",
                    "pe_ratio": "18.4",
                }
            ],
            csv_path=Path("trend.csv"),
        ),
        fast_bundle.SignalResult(
            key="hundred_day_high",
            signal_type="hundred_day_high",
            label="hundred",
            rows=[{"code": "300502", "name": "新易盛"}],
            csv_path=Path("hundred.csv"),
        ),
    ]

    rows = fast_bundle._build_earnings_focus_rows(signal_results, snapshot_date=date(2026, 4, 28))

    assert rows[0]["code"] == "300502"
    assert rows[0]["market_expectation_reference_label"] == "beat_ref"
    assert rows[0]["trend_resonance_label"] == "intersection"
    assert rows[0]["days_since_event"] == 4
    assert rows[0]["today_change_pct"] == 6.8
    assert rows[0]["pe_ratio"] == 18.4
    assert rows[0]["report_period_label"] == "2026Q1"
    assert rows[0]["net_profit_amount"] == 260000000.0
    assert rows[1]["code"] == "300600"


def test_write_earnings_focus_outputs_writes_csv_and_markdown(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def enrich_items(self, items):
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = [
        {
            "code": "300502",
            "name": "新易盛",
            "earnings_strategy_score": 91.8,
            "market_expectation_reference_label": "beat_ref",
            "market_expectation_summary": "2026年EPS一致预期 17.54 元",
            "event_date": "2026-04-24",
            "trend_resonance_label": "intersection",
            "today_change_pct": 6.8,
            "pe_ratio": 18.4,
            "report_period_label": "2026Q1",
            "net_profit_amount": 260000000.0,
            "reason_summary": "增长指标命中；业务侧先按 光模块/光通信 跟踪",
            "cause_tags_zh": "业绩/板块轮动",
            "business_labels": "光模块,光通信",
            "business_summary": "光模块/光通信，偏AI算力供应链",
            "chain_role_label": "AI算力供应链",
            "theme_label": "AI算力链映射",
            "theme_source": "business_summary+news_title",
            "earnings_anchor": "2026Q1@2026-04-24",
            "supply_demand_bias": "earnings",
        }
    ]
    csv_path = tmp_path / "fast_review_earnings_focus.csv"
    md_path = tmp_path / "fast_review_earnings_focus.md"

    fast_bundle._write_earnings_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    assert csv_path.exists()
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "新易盛" in content
    assert "beat_ref" in content
    assert "增长指标命中" in content
    assert "业绩/板块轮动" in content
    assert "snapshot" in content
    assert "涨幅 6.80%" in content
    assert "PE 18.4" in content
    assert "2026Q1" in content
    assert "净利 2.60亿" in content

    written_rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig")))
    assert written_rows[0]["today_change_pct"] == "6.8"
    assert written_rows[0]["pe_ratio"] == "18.4"
    assert written_rows[0]["report_period_label"] == "2026Q1"
    assert written_rows[0]["net_profit_amount"] == "260000000.0"
    assert written_rows[0]["business_labels"] == "光模块,光通信"
    assert written_rows[0]["business_summary"] == "光模块/光通信，偏AI算力供应链"
    assert written_rows[0]["chain_role_label"] == "AI算力供应链"
    assert written_rows[0]["theme_label"] == "AI算力链映射"
    assert written_rows[0]["theme_source"] == "business_summary+news_title"
    assert written_rows[0]["earnings_anchor"] == "2026Q1@2026-04-24"
    assert written_rows[0]["supply_demand_bias"] == "earnings"


def test_resolve_focus_explanation_structure_fields_prefers_canonical_earnings_anchor() -> None:
    fields = fast_bundle._resolve_focus_explanation_structure_fields(
        payload={"earnings_anchor": "2026-05-07"},
        reason_summary="增长指标命中，当前先按业绩驱动看待",
        industry_logic="创新药方向景气度抬升",
        technical_logic="事件后价格跟随",
        cause_tags="earnings,sector_rotation,overseas_theme",
        event_date="2026-05-07",
        report_period_label="2026Q1",
        report_date="2026-03-31",
        existing_row={},
    )

    assert fields["earnings_anchor"] == "2026Q1@2026-05-07"


def test_resolve_focus_explanation_structure_fields_does_not_mark_trend_rows_as_earnings_bias() -> None:
    fields = fast_bundle._resolve_focus_explanation_structure_fields(
        payload={},
        reason_summary="当前更像是 光通信 方向的结构性走强",
        industry_logic="业务辨识度更偏 光模块/光通信，偏AI算力供应链",
        technical_logic="业务主线可先按 光模块/光通信，偏AI算力供应链 跟踪",
        cause_tags="earnings,sector_rotation,overseas_theme",
        event_date="",
        report_period_label="",
        report_date="",
        existing_row={},
    )

    assert fields["supply_demand_bias"] == ""


def test_write_earnings_focus_outputs_enriches_missing_market_and_earnings_fields(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def enrich_items(self, items):
            items[0]["today_change_pct"] = 4.26
            items[0]["pe_ratio"] = 29.8
            items[0]["report_date"] = "2026-03-31"
            items[0]["report_period_label"] = "2026Q1"
            items[0]["revenue_amount"] = 4266000000.0
            items[0]["net_profit_amount"] = 638000000.0
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = [
        {
            "code": "000988",
            "name": "华工科技",
            "earnings_strategy_score": 55.9,
            "earnings_strategy_gate_status": "passed_strategy_score",
            "market_expectation_reference_label": "unknown",
            "event_date": "2026-05-07",
            "trend_resonance_label": "earnings_only",
            "today_change_pct": "",
            "pe_ratio": "",
            "report_date": "",
            "report_period_label": "",
            "revenue_amount": "",
            "net_profit_amount": "",
            "reason_summary": "业绩触发后继续观察",
            "cause_tags_zh": "业绩/板块轮动",
        }
    ]
    csv_path = tmp_path / "fast_review_earnings_focus.csv"
    md_path = tmp_path / "fast_review_earnings_focus.md"

    fast_bundle._write_earnings_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    written_rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig")))
    assert written_rows[0]["today_change_pct"] == "4.26"
    assert written_rows[0]["pe_ratio"] == "29.8"
    assert written_rows[0]["report_date"] == "2026-03-31"
    assert written_rows[0]["report_period_label"] == "2026Q1"
    assert written_rows[0]["revenue_amount"] == "4266000000.0"
    assert written_rows[0]["net_profit_amount"] == "638000000.0"

    content = md_path.read_text(encoding="utf-8")
    assert "涨幅 4.26%" in content
    assert "PE 29.8" in content
    assert "2026Q1" in content
    assert "营收 42.66亿" in content
    assert "净利 6.38亿" in content


def test_write_earnings_focus_outputs_persists_peer_check_fields(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def enrich_items(self, items):
            items[0]["peer_group_label"] = "光模块"
            items[0]["peer_resonance_summary"] = "同日 光模块 方向有3只进入焦点池，光迅科技位列龙头。"
            items[0]["leader_position_summary"] = "当前在 光模块 焦点组内位列龙头。"
            items[0]["turning_point_peer_summary"] = "同组已有2只处在拐点，仍需继续确认。"
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    csv_path = tmp_path / "fast_review_earnings_focus.csv"
    md_path = tmp_path / "fast_review_earnings_focus.md"
    rows = [
        {
            "code": "002281",
            "name": "光迅科技",
            "earnings_strategy_score": 82.1,
            "earnings_strategy_gate_status": "passed_strategy_score",
            "event_date": "2026-04-30",
            "reason_summary": "光模块业绩兑现",
            "cause_tags": "earnings,sector_rotation",
            "cause_tags_zh": "业绩/板块轮动",
            "report_date": "2026-03-31",
            "report_period_label": "2026Q1",
        }
    ]

    fast_bundle._write_earnings_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        written_rows = list(csv.DictReader(handle))

    assert written_rows[0]["peer_group_label"] == "光模块"
    assert "3只" in written_rows[0]["peer_resonance_summary"]
    assert "龙头" in written_rows[0]["leader_position_summary"]
    assert "拐点" in written_rows[0]["turning_point_peer_summary"]


def test_write_earnings_focus_outputs_rebuilds_canonical_earnings_anchor_after_enrichment(monkeypatch, tmp_path: Path) -> None:
    class _FakeFocusService:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def enrich_items(self, items):
            items[0]["report_date"] = "2026-03-31"
            items[0]["report_period_label"] = "2026Q1"
            return items

    monkeypatch.setattr(fast_bundle, "FastReviewFocusService", _FakeFocusService, raising=False)

    rows = [
        {
            "code": "688235",
            "name": "鐧炬祹绁炲窞",
            "earnings_strategy_score": 55.9,
            "event_date": "2026-05-07",
            "report_date": "",
            "report_period_label": "",
            "earnings_anchor": "2026-05-07",
            "cause_tags": "earnings,sector_rotation,overseas_theme",
            "reason_summary": "澧為暱鎸囨爣鍛戒腑锛屽綋鍓嶅厛鎸変笟缁╅┍鍔ㄧ湅寰?",
            "cause_tags_zh": "涓氱哗/鏉垮潡杞姩/娴峰鏄犲皠",
        }
    ]
    csv_path = tmp_path / "fast_review_earnings_focus.csv"
    md_path = tmp_path / "fast_review_earnings_focus.md"

    fast_bundle._write_earnings_focus_outputs(rows=rows, csv_path=csv_path, md_path=md_path)

    written_rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig")))
    assert written_rows[0]["earnings_anchor"] == "2026Q1@2026-05-07"


def test_parse_args_applies_strategy_profile_defaults(tmp_path: Path) -> None:
    profile_path = tmp_path / "local_strategy_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "include_signals": ["earnings", "trend_leader"],
                    "exclude_signals": ["continuous_up"],
                    "external_parallelism": 3,
                    "external_command_idle_timeout_sec": 120,
                    "external_command_total_timeout_sec": 900,
                    "external_command_heartbeat_sec": 60,
                    "continuous_max_workers": 1,
                "hundred_day_max_workers": 3,
                "hundred_day_prefilter_min_change_pct_60d": 14.0,
                "hundred_day_prefilter_min_turnover_rate": 1.1,
                "hundred_day_prefilter_require_positive_change": True,
                "hundred_day_prefilter_exclude_st": True,
                "trend_max_workers": 4,
                "earnings_scan_depth": "low",
                "earnings_recent_event_scope": "latest_report_period",
                "earnings_recent_event_max_age_days": 7,
            }
        },
        ensure_ascii=False,
    ),
        encoding="utf-8",
    )

    args = fast_bundle.parse_args(["--strategy-profile-file", str(profile_path)])
    assert args.include_signals == "earnings,trend_leader"
    assert args.exclude_signals == "continuous_up"
    assert args.external_parallelism == 3
    assert args.external_command_idle_timeout_sec == 120
    assert args.external_command_total_timeout_sec == 900
    assert args.external_command_heartbeat_sec == 60
    assert args.continuous_max_workers == 1
    assert args.hundred_day_max_workers == 3
    assert args.hundred_day_prefilter_min_change_pct_60d == 14.0
    assert args.hundred_day_prefilter_min_turnover_rate == 1.1
    assert args.hundred_day_prefilter_require_positive_change is True
    assert args.hundred_day_prefilter_exclude_st is True
    assert args.trend_max_workers == 4
    assert args.earnings_scan_depth == "low"
    assert args.earnings_recent_event_scope == "latest_report_period"
    assert args.earnings_recent_event_max_age_days == 7


def test_parse_args_uses_fast_review_defaults_when_profile_missing(tmp_path: Path) -> None:
    missing_profile = tmp_path / "missing_profile.json"
    args = fast_bundle.parse_args(["--strategy-profile-file", str(missing_profile)])

    assert args.include_signals == "earnings,hundred_day_high,trend_leader,daily_slow_rise"
    assert args.daily_profile == "accelerating"
    assert args.external_parallelism == 3
    assert args.external_command_idle_timeout_sec == 1800
    assert args.external_command_total_timeout_sec == 14400
    assert args.external_command_heartbeat_sec == 300
    assert args.continuous_max_workers == 2
    assert args.earnings_max_workers == 1
    assert args.earnings_recent_event_max_age_days == 7
    assert args.hundred_day_max_workers == 2
    assert args.daily_max_workers == 4
    assert args.hundred_day_prefilter_min_listed_days == 120
    assert args.hundred_day_prefilter_min_change_pct_60d == 12.0
    assert args.hundred_day_prefilter_min_turnover_rate == 0.8
    assert args.hundred_day_prefilter_require_positive_change is True
    assert args.hundred_day_prefilter_exclude_st is True
    assert args.trend_max_workers == 2
    assert args.trend_disable_scan_prefilter is False
    assert args.trend_scan_prefilter_min_listed_days == 120
    assert args.trend_scan_prefilter_min_change_pct_60d == 3.0
    assert args.trend_scan_prefilter_min_turnover_rate == 0.8
    assert args.trend_scan_prefilter_require_positive_change is False


def test_parse_args_uses_repo_strategy_profile_tighter_trend_prefilter_defaults() -> None:
    args = fast_bundle.parse_args([])

    assert args.include_signals == "earnings,hundred_day_high,trend_leader,daily_slow_rise"
    assert args.daily_profile == "accelerating"
    assert args.daily_max_workers == 4
    assert args.hundred_day_prefilter_min_listed_days == 120
    assert args.hundred_day_prefilter_min_change_pct_60d == 12.0
    assert args.hundred_day_prefilter_min_turnover_rate == 0.8
    assert args.hundred_day_prefilter_require_positive_change is True
    assert args.hundred_day_prefilter_exclude_st is True
    assert args.trend_disable_scan_prefilter is False
    assert args.trend_scan_prefilter_min_listed_days == 120
    assert args.trend_scan_prefilter_min_change_pct_60d == 17.0
    assert args.trend_scan_prefilter_min_turnover_rate == 1.5
    assert args.trend_scan_prefilter_require_positive_change is True


def test_run_command_raises_when_external_script_no_output_timeout(monkeypatch) -> None:
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC", 1)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC", 30)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_HEARTBEAT_SEC", 0)
    with pytest.raises(RuntimeError, match="idle timeout"):
        fast_bundle._run_command(
            [
                fast_bundle.sys.executable,
                "-c",
                "import time; time.sleep(5)",
            ]
        )


def test_run_command_allows_post_start_silence_with_heartbeat(monkeypatch, capsys) -> None:
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC", 1)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC", 10)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_HEARTBEAT_SEC", 1)

    output = fast_bundle._run_command(
        [
            fast_bundle.sys.executable,
            "-c",
            "import sys, time; print('started', flush=True); time.sleep(2)",
        ]
    )

    captured = capsys.readouterr()
    assert "started" in output
    assert "external heartbeat" in captured.out


def test_run_command_uses_utf8_decoding_for_child_stdout(monkeypatch) -> None:
    captured = {}

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO("进度正常\n")
            self._returncode = 0

        def poll(self):
            return self._returncode

        def wait(self, timeout=None):
            return self._returncode

        def terminate(self):
            self._returncode = -15

        def kill(self):
            self._returncode = -9

    def _fake_popen(
        command,
        cwd,
        stdout,
        stderr,
        text,
        bufsize,
        encoding=None,
        errors=None,
        env=None,
    ):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["text"] = text
        captured["bufsize"] = bufsize
        captured["encoding"] = encoding
        captured["errors"] = errors
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(fast_bundle.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC", 30)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC", 30)

    output = fast_bundle._run_command([fast_bundle.sys.executable, "-c", "print('ok')"])

    assert "进度正常" in output
    assert captured["text"] is True
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"


def test_run_command_falls_back_when_console_cannot_encode_child_stdout(monkeypatch) -> None:
    original_stdout = fast_bundle.sys.stdout

    class _FakeStdout:
        encoding = "gbk"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, text):
            raise UnicodeEncodeError("gbk", "✅", 0, 1, "illegal multibyte sequence")

        def flush(self):
            return None

    fake_stdout = _FakeStdout()
    monkeypatch.setattr(fast_bundle.sys, "stdout", fake_stdout)
    try:
        output = fast_bundle._run_command([fast_bundle.sys.executable, "-c", "print('✅ done')"])
    finally:
        monkeypatch.setattr(fast_bundle.sys, "stdout", original_stdout)

    assert "✅ done" in output
    assert fake_stdout.buffer.getvalue().decode("gbk", errors="replace").strip() == "? done"


def test_collect_continuous_signals_uses_continuous_max_workers(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class _FakeService:
        build_fast_a_share_manager = object()

        def __init__(self, *args, **kwargs) -> None:
            return None

        def scan_market(self, *, max_workers, **kwargs):
            captured["max_workers"] = max_workers
            return SimpleNamespace(selected=[], evaluated_count=0)

    monkeypatch.setattr(fast_bundle, "KlineSelectorService", _FakeService)
    args = SimpleNamespace(
        continuous_up_lookback_days=10,
        continuous_up_streak_days=3,
        continuous_up_min_ratio=0.7,
        continuous_max_total_mv_yi=500.0,
        disable_continuous_spot_prefilter=True,
        progress_every=0,
        limit=10,
        max_workers=8,
        continuous_max_workers=2,
        exclude_st=False,
        exclude_kcb=False,
        exclude_cyb=False,
    )
    fast_bundle._collect_continuous_signals(
        args,
        snapshot_date=date(2026, 4, 21),
        signal_dir=tmp_path,
        include_ratio=True,
        include_streak=False,
    )
    assert captured["max_workers"] == 2


def test_collect_continuous_signals_uses_lightweight_history_requirement(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class _FakeService:
        build_fast_a_share_manager = object()

        def __init__(self, *args, **kwargs) -> None:
            return None

        def get_spot_enriched_a_share_universe(self, *, limit=None, as_of_date=None):
            captured["limit"] = limit
            captured["as_of_date"] = as_of_date
            return []

        def scan_market(self, *, criteria, prefilter, universe, max_workers, **kwargs):
            captured["history_days_required"] = criteria.history_days_required
            captured["min_listed_days"] = prefilter.min_listed_days
            captured["universe"] = universe
            captured["max_workers"] = max_workers
            return SimpleNamespace(selected=[], evaluated_count=0, skipped_listed_days_count=0)

    monkeypatch.setattr(fast_bundle, "KlineSelectorService", _FakeService)
    args = SimpleNamespace(
        continuous_up_lookback_days=10,
        continuous_up_streak_days=3,
        continuous_up_min_ratio=0.7,
        continuous_max_total_mv_yi=500.0,
        disable_continuous_spot_prefilter=False,
        progress_every=0,
        limit=10,
        max_workers=8,
        continuous_max_workers=2,
        exclude_st=False,
        exclude_kcb=False,
        exclude_cyb=False,
    )

    fast_bundle._collect_continuous_signals(
        args,
        snapshot_date=date(2026, 4, 21),
        signal_dir=tmp_path,
        include_ratio=True,
        include_streak=False,
    )

    assert captured["history_days_required"] == 15
    assert captured["min_listed_days"] == 15
    assert captured["max_workers"] == 2


def test_collect_continuous_signals_uses_observation_labels(monkeypatch, tmp_path: Path) -> None:
    class _FakeService:
        build_fast_a_share_manager = object()

        def __init__(self, *args, **kwargs) -> None:
            return None

        def get_spot_enriched_a_share_universe(self, *, limit=None, as_of_date=None):
            return []

        def scan_market(self, **kwargs):
            evaluation = SimpleNamespace(
                stock_code="600001",
                stock_name="sample",
                metrics={
                    "up_ratio": 0.8,
                    "up_days": 8,
                    "lookback_days": 10,
                    "current_up_streak": 4,
                    "close": 12.34,
                },
                total_market_cap=123.0 * 1e8,
            )
            return SimpleNamespace(selected=[evaluation], evaluated_count=1, skipped_listed_days_count=0)

    monkeypatch.setattr(fast_bundle, "KlineSelectorService", _FakeService)
    args = SimpleNamespace(
        continuous_up_lookback_days=10,
        continuous_up_streak_days=3,
        continuous_up_min_ratio=0.7,
        continuous_max_total_mv_yi=500.0,
        disable_continuous_spot_prefilter=False,
        progress_every=0,
        limit=10,
        max_workers=8,
        continuous_max_workers=2,
        exclude_st=False,
        exclude_kcb=False,
        exclude_cyb=False,
    )

    results = fast_bundle._collect_continuous_signals(
        args,
        snapshot_date=date(2026, 4, 21),
        signal_dir=tmp_path,
        include_ratio=True,
        include_streak=True,
    )

    ratio_rows = results[fast_bundle.SIGNAL_CONTINUOUS_UP_RATIO].rows
    streak_rows = results[fast_bundle.SIGNAL_CONTINUOUS_UP_STREAK].rows
    assert ratio_rows[0]["signal_label"] == "上涨节奏观察(上涨占比)"
    assert ratio_rows[0]["strategy_summary"] == "观察近10日上涨占比 80.00%"
    assert streak_rows[0]["signal_label"] == "上涨节奏观察(连涨天数)"
    assert streak_rows[0]["strategy_summary"] == "观察当前连涨 4 天"


def test_build_resonance_rows_deduplicates_by_code() -> None:
    signal_results = [
        fast_bundle.SignalResult(
            key="earnings",
            signal_type="earnings_surprise",
            label="业绩",
            rows=[{"code": "600001", "name": "A"}, {"code": "600002", "name": "B"}],
            csv_path=Path("a.csv"),
        ),
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="趋势龙头",
            rows=[{"code": "600001", "name": "A"}],
            csv_path=Path("b.csv"),
        ),
    ]

    resonance_rows = fast_bundle._build_resonance_rows(signal_results)
    assert len(resonance_rows) == 2
    assert resonance_rows[0]["code"] == "600001"
    assert resonance_rows[0]["resonance_count"] == 2
    assert resonance_rows[0]["primary_signal"] == "trend_leader"
    assert resonance_rows[0]["secondary_signals"] == "earnings"


def test_main_keeps_requested_external_parallelism_when_persist_enabled(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        captured["jobs"] = len(jobs)
        captured["external_parallelism"] = external_parallelism
        return [], []

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(fast_bundle, "_collect_continuous_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "earnings,trend_leader",
            "--external-parallelism",
            "3",
        ],
    )

    rc = fast_bundle.main()

    assert rc == 0
    assert captured["jobs"] == 2
    assert captured["external_parallelism"] == 3


def test_main_writes_strategy_focus_outputs(monkeypatch, tmp_path: Path) -> None:
    _install_stub_cause_service(monkeypatch)

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        results = []
        for job in jobs:
            rows = []
            if job.key == fast_bundle.SIGNAL_TREND_LEADER:
                rows = [
                    {
                        "code": "600001",
                        "name": "CoreA",
                        "overall_score": "36",
                        "capital_consensus_score": "2",
                        "primary_board_name": "AI",
                    }
                ]
            elif job.key == fast_bundle.SIGNAL_HUNDRED_DAY_HIGH:
                rows = [{"code": "600001", "name": "CoreA"}]
            elif job.key == fast_bundle.SIGNAL_EARNINGS:
                rows = [
                    {
                        "code": "600001",
                        "name": "CoreA",
                        "earnings_strategy_score": "70",
                        "market_expectation_reference_label": "beat_ref",
                        "market_expectation_summary": "2026年EPS一致预期 1.23 元",
                        "event_date": "2026-04-18",
                    }
                ]
            results.append(
                fast_bundle.SignalResult(
                    key=job.key,
                    signal_type=job.signal_type,
                    label=job.signal_label,
                    rows=rows,
                    csv_path=job.csv_path,
                    duration_sec=1.0,
                )
            )
        return results, []

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "earnings,hundred_day_high,trend_leader",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    focus_csv = tmp_path / "2026-04-20" / "review" / "fast_review_strategy_focus.csv"
    focus_md = tmp_path / "2026-04-20" / "review" / "fast_review_strategy_focus.md"
    earnings_focus_csv = tmp_path / "2026-04-20" / "review" / "fast_review_earnings_focus.csv"
    earnings_focus_md = tmp_path / "2026-04-20" / "review" / "fast_review_earnings_focus.md"
    summary_md = tmp_path / "2026-04-20" / "review" / "fast_review_summary.md"
    assert rc == 0
    assert focus_csv.exists()
    assert focus_md.exists()
    assert earnings_focus_csv.exists()
    assert earnings_focus_md.exists()
    assert "CoreA" in earnings_focus_md.read_text(encoding="utf-8")
    assert "CoreA" in focus_md.read_text(encoding="utf-8")
    summary_text = summary_md.read_text(encoding="utf-8")
    assert "策略精简焦点" in summary_text
    assert "今日业绩焦点 15 只" in summary_text


def test_main_writes_manual_review_calibration_outputs(monkeypatch, tmp_path: Path) -> None:
    _install_stub_cause_service(monkeypatch)
    labels_path = tmp_path / "manual_labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "snapshot_date": "2026-04-20",
                        "code": "600001",
                        "name": "CoreA",
                        "expected_ab_bucket": "A",
                        "expected_driver_type": "earnings_delivery",
                        "expected_driver_label": "业绩兑现型",
                        "notes": "golden sample",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        results = []
        for job in jobs:
            rows = []
            if job.key == fast_bundle.SIGNAL_TREND_LEADER:
                rows = [{"code": "600001", "name": "CoreA", "overall_score": "36"}]
            elif job.key == fast_bundle.SIGNAL_HUNDRED_DAY_HIGH:
                rows = [{"code": "600001", "name": "CoreA"}]
            elif job.key == fast_bundle.SIGNAL_EARNINGS:
                rows = [
                    {
                        "code": "600001",
                        "name": "CoreA",
                        "earnings_strategy_score": "70",
                        "earnings_strategy_gate_status": "pass",
                        "market_expectation_reference_label": "beat_ref",
                        "event_date": "2026-04-18",
                    }
                ]
            results.append(
                fast_bundle.SignalResult(
                    key=job.key,
                    signal_type=job.signal_type,
                    label=job.signal_label,
                    rows=rows,
                    csv_path=job.csv_path,
                    duration_sec=1.0,
                )
            )
        return results, []

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "earnings,hundred_day_high,trend_leader",
            "--manual-review-labels-file",
            str(labels_path),
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    calibration_csv = tmp_path / "2026-04-20" / "review" / "fast_review_manual_calibration.csv"
    calibration_md = tmp_path / "2026-04-20" / "review" / "fast_review_manual_calibration.md"
    summary_md = tmp_path / "2026-04-20" / "review" / "fast_review_summary.md"
    assert rc == 0
    assert calibration_csv.exists()
    assert calibration_md.exists()
    assert "golden sample" in calibration_csv.read_text(encoding="utf-8-sig")
    assert "人工复盘校准" in summary_md.read_text(encoding="utf-8")


def test_main_includes_trend_watchlist_in_strategy_focus_only(monkeypatch, tmp_path: Path) -> None:
    _install_stub_cause_service(monkeypatch)
    trend_dir = tmp_path / "2026-04-20" / "signals" / fast_bundle.SIGNAL_TREND_LEADER
    trend_dir.mkdir(parents=True, exist_ok=True)
    trend_csv = trend_dir / "trend_leader_unified_candidates.csv"
    trend_csv.write_text("code,name\n600001,StrictA\n", encoding="utf-8")
    (trend_dir / "trend_leader_unified_watchlist.csv").write_text(
        (
            "code,name,overall_score,selection_mode,risk_flags\n"
            "600002,WatchOnly,18,watch,trend_watch_pool\n"
        ),
        encoding="utf-8",
    )

    def _mock_run_external_signal_jobs(jobs, *, external_parallelism):
        results = []
        for job in jobs:
            rows = []
            csv_path = job.csv_path
            if job.key == fast_bundle.SIGNAL_TREND_LEADER:
                rows = [
                    {
                        "code": "600001",
                        "name": "StrictA",
                        "overall_score": "36",
                        "selection_mode": "strict",
                    }
                ]
                csv_path = trend_csv
            results.append(
                fast_bundle.SignalResult(
                    key=job.key,
                    signal_type=job.signal_type,
                    label=job.signal_label,
                    rows=rows,
                    csv_path=csv_path,
                    duration_sec=1.0,
                )
            )
        return results, []

    monkeypatch.setattr(fast_bundle, "_run_external_signal_jobs", _mock_run_external_signal_jobs)
    monkeypatch.setattr(
        fast_bundle.sys,
        "argv",
        [
            "run_fast_review_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "trend_leader",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    focus_csv = tmp_path / "2026-04-20" / "review" / "fast_review_strategy_focus.csv"
    unified_csv = tmp_path / "2026-04-20" / "review" / "fast_review_candidates.csv"
    assert rc == 0
    focus_text = focus_csv.read_text(encoding="utf-8-sig")
    unified_text = unified_csv.read_text(encoding="utf-8-sig")
    assert "600002" in focus_text
    assert "WatchOnly" in focus_text
    assert "600002" not in unified_text


def test_fast_review_summary_can_include_shortline_focus_section(tmp_path: Path) -> None:
    shortline_summary = tmp_path / "shortline_summary.json"
    shortline_summary.write_text(
        json.dumps(
            {
                "trade_date": "2026-05-03",
                "top_pick_count": 1,
                "watchlist_count": 1,
                "high_risk_mover_count": 0,
                "top_symbols": ["300083 RiseA"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_md = tmp_path / "fast_review_summary.md"

    fast_bundle.append_shortline_focus_section(
        summary_md=summary_md,
        shortline_summary_path=shortline_summary,
    )

    content = summary_md.read_text(encoding="utf-8")
    assert "短线观察" in content
    assert "300083 RiseA" in content


def test_build_shortline_summary_from_snapshots_falls_back_to_high_risk_symbols() -> None:
    class FakeDb:
        def count_signal_snapshots(self, *, signal_type, signal_date):
            mapping = {
                "shortline_top_pick": 0,
                "shortline_watchlist": 0,
                "shortline_high_risk_mover": 2,
            }
            return mapping.get(signal_type, 0)

        def get_signal_snapshots(self, *, signal_type, signal_date, limit):
            if signal_type == "shortline_high_risk_mover":
                return [
                    SimpleNamespace(code="300083", name="创世纪"),
                    SimpleNamespace(code="688256", name="寒武纪"),
                ]
            return []

    summary = fast_bundle._build_shortline_summary_from_snapshots(
        snapshot_date=date(2026, 5, 3),
        db=FakeDb(),
    )

    assert summary is not None
    assert summary["high_risk_mover_count"] == 2
    assert summary["top_symbols"] == ["300083 创世纪", "688256 寒武纪"]
