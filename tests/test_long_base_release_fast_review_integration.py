# -*- coding: utf-8 -*-
"""Integration tests for long-base-release fast-review wiring."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import scripts.run_fast_review_bundle as fast_bundle


def test_build_long_base_release_command_forwards_skip_db_persist() -> None:
    args = SimpleNamespace(
        long_base_release_signal_type="long_base_release",
        long_base_release_profile="default",
        long_base_release_max_workers=3,
        persist_snapshots=False,
        limit=60,
        log_level="INFO",
    )

    command = fast_bundle.build_long_base_release_command(
        args,
        snapshot_date=date(2026, 5, 20),
        output_dir=Path("tmp/long_base_release"),
    )

    assert command[1].endswith("select_long_base_release_candidates.py")
    assert "--signal-type" in command
    assert command[command.index("--signal-type") + 1] == "long_base_release"
    assert "--profile" in command
    assert command[command.index("--profile") + 1] == "default"
    assert "--max-workers" in command
    assert command[command.index("--max-workers") + 1] == "3"
    assert "--limit" in command
    assert command[command.index("--limit") + 1] == "60"
    assert "--skip-db-persist" in command


def test_main_registers_long_base_release_external_signal(monkeypatch, tmp_path: Path) -> None:
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
            "2026-05-20",
            "--output-dir",
            str(tmp_path),
            "--include-signals",
            "long_base_release",
            "--skip-persist-snapshots",
        ],
    )

    rc = fast_bundle.main()

    assert rc == 0
    assert captured["keys"] == ["long_base_release"]
    assert captured["signal_types"] == ["long_base_release"]
