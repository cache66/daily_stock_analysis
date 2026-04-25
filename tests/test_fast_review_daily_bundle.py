# -*- coding: utf-8 -*-
"""Tests for fast review daily bundle runner."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_fast_review_bundle as fast_bundle


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
        hundred_day_signal_type="hundred_day_high",
        hundred_day_max_workers=2,
        trend_signal_type="trend_leader_unified",
        trend_max_workers=2,
        trend_fallback_top_n=20,
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
    assert "--skip-db-persist" in hundred_cmd
    assert "--skip-db-persist" in trend_cmd
    assert "--skip-cause-analysis" in hundred_cmd
    assert "--max-workers" in hundred_cmd
    assert hundred_cmd[hundred_cmd.index("--max-workers") + 1] == "2"
    assert "--disable-second-stage-news-search" in trend_cmd
    assert "--disable-second-stage-business-profile" in trend_cmd
    assert "--enrich-top-n" in trend_cmd
    assert trend_cmd[trend_cmd.index("--enrich-top-n") + 1] == "0"
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


def test_parse_args_uses_fast_review_defaults_when_profile_missing(tmp_path: Path) -> None:
    missing_profile = tmp_path / "missing_profile.json"
    args = fast_bundle.parse_args(["--strategy-profile-file", str(missing_profile)])

    assert args.include_signals == "earnings,hundred_day_high,trend_leader"
    assert args.external_parallelism == 3
    assert args.external_command_idle_timeout_sec == 900
    assert args.external_command_total_timeout_sec == 14400
    assert args.continuous_max_workers == 2
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
