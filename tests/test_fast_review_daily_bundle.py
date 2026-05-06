# -*- coding: utf-8 -*-
"""Tests for fast review daily bundle runner."""

import csv
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


def test_build_strategy_focus_rows_reuses_existing_reason_fields() -> None:
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
        },
        {
            "code": "600002",
            "name": "WatchB",
            "tier": "watch",
            "priority_score": 70.0,
            "signal_keys": "trend_leader",
            "trend_hundred_relation": "trend_only",
            "focus_reason": "trend",
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
    assert "行业爆发带动龙头继续走强" in summary
    assert "板块轮动/政策" in summary
    assert "业绩释放后延续强势" in summary


def test_load_signal_rows_keeps_strategy_focus_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "trend.csv"
    csv_path.write_text(
        (
            "code,name,overall_score,capital_consensus_score,capital_profile_score,"
            "sector_leadership_score,recognizability_score,primary_board_name,risk_flags,"
            "market_expectation_summary,market_expectation_reference_label,event_date\n"
            "600001,Sample,36,2,68,2,1,AI,,2026年EPS一致预期 1.23 元,beat_ref,2026-04-18\n"
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
            rows=[{"code": "300502", "name": "新易盛", "overall_score": "36"}],
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
    assert rows[1]["code"] == "300600"


def test_write_earnings_focus_outputs_writes_csv_and_markdown(tmp_path: Path) -> None:
    rows = [
        {
            "code": "300502",
            "name": "新易盛",
            "earnings_strategy_score": 91.8,
            "market_expectation_reference_label": "beat_ref",
            "market_expectation_summary": "2026年EPS一致预期 17.54 元",
            "event_date": "2026-04-24",
            "trend_resonance_label": "intersection",
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
                    "continuous_max_workers": 1,
                "hundred_day_max_workers": 3,
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
    assert args.continuous_max_workers == 1
    assert args.hundred_day_max_workers == 3
    assert args.trend_max_workers == 4
    assert args.earnings_scan_depth == "low"
    assert args.earnings_recent_event_scope == "latest_report_period"
    assert args.earnings_recent_event_max_age_days == 7


def test_parse_args_uses_fast_review_defaults_when_profile_missing(tmp_path: Path) -> None:
    missing_profile = tmp_path / "missing_profile.json"
    args = fast_bundle.parse_args(["--strategy-profile-file", str(missing_profile)])

    assert args.include_signals == "earnings,hundred_day_high,trend_leader"
    assert args.external_parallelism == 3
    assert args.external_command_idle_timeout_sec == 1800
    assert args.external_command_total_timeout_sec == 14400
    assert args.continuous_max_workers == 2
    assert args.earnings_max_workers == 1
    assert args.earnings_recent_event_max_age_days == 7
    assert args.hundred_day_max_workers == 2
    assert args.trend_max_workers == 2
    assert args.trend_disable_scan_prefilter is False
    assert args.trend_scan_prefilter_min_listed_days == 120
    assert args.trend_scan_prefilter_min_change_pct_60d == 3.0
    assert args.trend_scan_prefilter_min_turnover_rate == 0.8
    assert args.trend_scan_prefilter_require_positive_change is False


def test_run_command_raises_when_external_script_no_output_timeout(monkeypatch) -> None:
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_IDLE_TIMEOUT_SEC", 1)
    monkeypatch.setattr(fast_bundle, "EXTERNAL_COMMAND_TOTAL_TIMEOUT_SEC", 30)
    with pytest.raises(RuntimeError, match="idle timeout"):
        fast_bundle._run_command(
            [
                fast_bundle.sys.executable,
                "-c",
                "import time; time.sleep(5)",
            ]
        )


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
