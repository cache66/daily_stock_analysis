# -*- coding: utf-8 -*-
"""Tests for the personal strategy matrix runner service."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.services.personal_strategy_run_service import (
    DEFAULT_EXTENDED_INCLUDE_SIGNALS,
    PersonalStrategyMatrixRunOptions,
    PersonalStrategyMatrixRunService,
)


def test_build_plan_defaults_to_prewarm_then_bundle(tmp_path: Path) -> None:
    service = PersonalStrategyMatrixRunService(project_root=tmp_path, python_executable="python-test")
    options = PersonalStrategyMatrixRunOptions(
        snapshot_date="2026-07-10",
        output_dir=tmp_path / "manual_runs",
        prewarm_output_root=tmp_path / "cache_prewarm",
        strategy_profile_file=tmp_path / "profile.json",
        bundle_limit=1200,
        persist_snapshots=False,
    )

    plan = service.build_plan(options)

    assert plan["snapshot_date"] == "2026-07-10"
    assert plan["stock_overview_csv"].endswith(
        "manual_runs/2026-07-10/review/fast_review_stock_overview.csv"
    )
    assert [command["name"] for command in plan["commands"]] == [
        "cache_prewarm",
        "fast_review_bundle",
    ]
    prewarm_argv = plan["commands"][0]["argv"]
    bundle_argv = plan["commands"][1]["argv"]
    assert prewarm_argv[:2] == ["python-test", "scripts/warm_local_strategy_cache.py"]
    assert "--snapshot-date" in prewarm_argv
    assert "--top-n" in prewarm_argv
    assert "--earnings-top-n" in prewarm_argv
    assert bundle_argv[:2] == ["python-test", "scripts/run_fast_review_bundle.py"]
    assert "--strategy-profile-file" in bundle_argv
    assert "--safe-mode" in bundle_argv
    assert "--skip-persist-snapshots" in bundle_argv
    assert bundle_argv[bundle_argv.index("--limit") + 1] == "1200"


def test_build_plan_can_use_extended_signal_scope(tmp_path: Path) -> None:
    service = PersonalStrategyMatrixRunService(project_root=tmp_path, python_executable="python-test")
    options = PersonalStrategyMatrixRunOptions(
        snapshot_date="2026-07-10",
        output_dir=tmp_path / "manual_runs",
        prewarm_output_root=tmp_path / "cache_prewarm",
        include_signals=DEFAULT_EXTENDED_INCLUDE_SIGNALS,
    )

    plan = service.build_plan(options)

    bundle_argv = plan["commands"][-1]["argv"]
    assert "--include-signals" in bundle_argv
    assert bundle_argv[bundle_argv.index("--include-signals") + 1] == DEFAULT_EXTENDED_INCLUDE_SIGNALS


def test_run_writes_summary_and_stops_after_failed_command(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": list(argv), "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = PersonalStrategyMatrixRunService(project_root=tmp_path, python_executable="python-test")
    options = PersonalStrategyMatrixRunOptions(
        snapshot_date="2026-07-10",
        output_dir=tmp_path / "manual_runs",
        prewarm_output_root=tmp_path / "cache_prewarm",
    )

    summary = service.run(options)

    assert summary["status"] == "failed"
    assert len(calls) == 1
    assert summary["commands"][0]["return_code"] == 1
    assert summary["commands"][1]["status"] == "skipped_after_failure"
    summary_path = Path(summary["summary_path"])
    assert summary_path.exists()
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["commands"][0]["name"] == "cache_prewarm"


def test_dry_run_does_not_execute_or_write_summary(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        raise AssertionError("dry-run must not execute subprocess.run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = PersonalStrategyMatrixRunService(project_root=tmp_path, python_executable="python-test")
    options = PersonalStrategyMatrixRunOptions(
        snapshot_date="2026-07-10",
        output_dir=tmp_path / "manual_runs",
        prewarm_output_root=tmp_path / "cache_prewarm",
    )

    summary = service.run(options, dry_run=True)

    assert summary["status"] == "dry_run"
    assert not Path(summary["summary_path"]).exists()
