# -*- coding: utf-8 -*-
"""Tests for signal performance bundle runner."""

from pathlib import Path
from types import SimpleNamespace

import scripts.run_signal_performance_bundle as perf_bundle


def test_parse_signal_types_deduplicates_and_preserves_order() -> None:
    signals = perf_bundle.parse_signal_types("trend_leader_unified,earnings_surprise,trend_leader_unified")
    assert signals == ["trend_leader_unified", "earnings_surprise"]


def test_build_eval_command_contains_core_flags(tmp_path: Path) -> None:
    args = SimpleNamespace(
        start_date="2026-04-01",
        end_date="2026-04-20",
        windows="1,3,5,10",
        limit=200,
        slippage_bps=10.0,
        fee_bps=5.0,
        turnover_penalty_bps=2.0,
        detail_limit=5,
        score_buckets="0,40,60,80,100",
        neutral_band_pct=2.0,
        fill_max_attempts=180,
        fill_missing_daily_data=True,
        log_level="INFO",
    )
    json_path = tmp_path / "a.json"
    md_path = tmp_path / "a.md"
    command = perf_bundle.build_eval_command(
        args=args,
        signal_type="trend_leader_unified",
        output_json=json_path,
        output_md=md_path,
    )
    assert "evaluate_signal_snapshot_performance.py" in command[1]
    assert "--signal-type" in command
    assert command[command.index("--signal-type") + 1] == "trend_leader_unified"
    assert command[command.index("--windows") + 1] == "1,3,5,10"
    assert command[command.index("--start-date") + 1] == "2026-04-01"
    assert command[command.index("--end-date") + 1] == "2026-04-20"
    assert command[command.index("--output-json") + 1] == str(json_path)
    assert command[command.index("--output-md") + 1] == str(md_path)
    assert command[command.index("--fill-max-attempts") + 1] == "180"
    assert "--fill-missing-daily-data" in command


def test_build_window_summary_rows_flattens_report() -> None:
    rows = perf_bundle.build_window_summary_rows(
        signal_type="trend_leader_unified",
        report={
            "window_summaries": [
                {
                    "eval_window_days": 1,
                    "completed_count": 12,
                    "win_rate_pct": 58.3,
                    "avg_stock_return_pct": 1.8,
                },
                {
                    "eval_window_days": 3,
                    "completed_count": 11,
                    "win_rate_pct": 54.5,
                    "avg_stock_return_pct": 2.2,
                },
            ]
        },
    )
    assert len(rows) == 2
    assert rows[0]["signal_type"] == "trend_leader_unified"
    assert rows[0]["window"] == 1
    assert rows[1]["window"] == 3
