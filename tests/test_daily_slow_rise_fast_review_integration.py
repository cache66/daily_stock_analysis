# -*- coding: utf-8 -*-
"""Integration tests for daily slow-rise fast-review wiring."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_fast_review_bundle as fast_bundle


def test_build_daily_slow_rise_command_forwards_skip_db_persist() -> None:
    args = SimpleNamespace(
        daily_signal_type="daily_slow_rise",
        daily_profile="balanced",
        daily_max_workers=2,
        persist_snapshots=False,
        limit=80,
        log_level="INFO",
    )

    command = fast_bundle.build_daily_slow_rise_command(
        args,
        snapshot_date=date(2026, 5, 19),
        output_dir=Path("tmp/daily_slow_rise"),
    )

    assert command[1].endswith("select_daily_slow_rise_candidates.py")
    assert "--signal-type" in command
    assert command[command.index("--signal-type") + 1] == "daily_slow_rise"
    assert "--profile" in command
    assert command[command.index("--profile") + 1] == "balanced"
    assert "--max-workers" in command
    assert command[command.index("--max-workers") + 1] == "2"
    assert "--limit" in command
    assert command[command.index("--limit") + 1] == "80"
    assert "--skip-db-persist" in command


def test_main_registers_daily_slow_rise_external_signal(monkeypatch, tmp_path: Path) -> None:
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
            "2026-05-19",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "daily_slow_rise",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    assert rc == 0
    assert captured["keys"] == ["daily_slow_rise"]
    assert captured["signal_types"] == ["daily_slow_rise"]
