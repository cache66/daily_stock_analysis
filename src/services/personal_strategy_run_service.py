# -*- coding: utf-8 -*-
"""Run-plan service for the stock-centered personal strategy matrix."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGY_PROFILE_FILE = PROJECT_ROOT / "config" / "local_strategy_profile.json"
DEFAULT_MATRIX_OUTPUT_DIR = PROJECT_ROOT / "data" / "manual_runs" / "personal_strategy_matrix"
DEFAULT_PREWARM_OUTPUT_ROOT = PROJECT_ROOT / "data" / "runtime" / "cache_prewarm"
DEFAULT_EXTENDED_INCLUDE_SIGNALS = (
    "earnings,hundred_day_high,daily_slow_rise,long_base_release,"
    "trend_leader,monthly_slow_rise,continuous_up"
)


@dataclass(frozen=True)
class PersonalStrategyMatrixRunOptions:
    """Options for a single local personal-strategy matrix run."""

    snapshot_date: str = date.today().isoformat()
    output_dir: Path = DEFAULT_MATRIX_OUTPUT_DIR
    strategy_profile_file: Path = DEFAULT_STRATEGY_PROFILE_FILE
    prewarm_output_root: Path = DEFAULT_PREWARM_OUTPUT_ROOT
    prewarm_enabled: bool = True
    prewarm_top_n: int = 0
    prewarm_earnings_top_n: int = 300
    prewarm_kline_days: int = 160
    prewarm_limit: Optional[int] = None
    prewarm_event_catalog_max_age_minutes: int = 15
    force_refresh_event_catalog: bool = False
    bundle_limit: Optional[int] = None
    include_signals: str = ""
    safe_mode: bool = True
    persist_snapshots: bool = True
    log_level: str = "INFO"


def normalize_snapshot_date(value: Any) -> str:
    """Normalize date-like input to YYYY-MM-DD text."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return date.today().isoformat()
    return datetime.strptime(text, "%Y-%m-%d").date().isoformat()


def _optional_positive_int(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    normalized = int(value)
    if normalized <= 0:
        return None
    return normalized


class PersonalStrategyMatrixRunService:
    """Build and run the cache-prewarm + fast-review bundle workflow."""

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        python_executable: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.python_executable = str(python_executable or sys.executable or "python")

    def build_plan(self, options: PersonalStrategyMatrixRunOptions) -> Dict[str, Any]:
        snapshot_date = normalize_snapshot_date(options.snapshot_date)
        output_dir = Path(options.output_dir)
        prewarm_output_root = Path(options.prewarm_output_root)
        commands: List[Dict[str, Any]] = []

        if options.prewarm_enabled:
            commands.append(
                self._command_payload(
                    name="cache_prewarm",
                    argv=self._build_prewarm_command(options, snapshot_date=snapshot_date),
                )
            )

        commands.append(
            self._command_payload(
                name="fast_review_bundle",
                argv=self._build_bundle_command(options, snapshot_date=snapshot_date),
            )
        )

        day_dir = output_dir / snapshot_date
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot_date": snapshot_date,
            "output_dir": str(output_dir),
            "day_dir": str(day_dir),
            "summary_path": str(day_dir / "personal_strategy_matrix_run_summary.json"),
            "prewarm_enabled": bool(options.prewarm_enabled),
            "prewarm_output_dir": str(prewarm_output_root / snapshot_date),
            "prewarm_summary_path": str(prewarm_output_root / snapshot_date / "summary.json"),
            "stock_overview_csv": str(day_dir / "review" / "fast_review_stock_overview.csv"),
            "matrix_api_path": (
                "/api/v1/signals/personal-strategy-matrix"
                f"?snapshot_date={snapshot_date}"
            ),
            "commands": commands,
        }

    def run(
        self,
        options: PersonalStrategyMatrixRunOptions,
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        summary = self.build_plan(options)
        summary["dry_run"] = bool(dry_run)
        if dry_run:
            summary["status"] = "dry_run"
            return summary

        executed_commands: List[Dict[str, Any]] = []
        status = "succeeded"
        started_at = time.perf_counter()

        planned_commands = list(summary["commands"])
        for index, command in enumerate(planned_commands):
            command_started_at = time.perf_counter()
            completed = subprocess.run(
                command["argv"],
                cwd=str(self.project_root),
                check=False,
            )
            result = dict(command)
            result["return_code"] = int(completed.returncode)
            result["elapsed_sec"] = round(time.perf_counter() - command_started_at, 3)
            executed_commands.append(result)
            if completed.returncode != 0:
                status = "failed"
                for skipped_command in planned_commands[index + 1 :]:
                    skipped = dict(skipped_command)
                    skipped["status"] = "skipped_after_failure"
                    skipped["return_code"] = None
                    skipped["elapsed_sec"] = 0.0
                    executed_commands.append(skipped)
                break

        summary["commands"] = executed_commands
        summary["status"] = status
        summary["elapsed_sec"] = round(time.perf_counter() - started_at, 3)
        self.write_summary(summary)
        return summary

    def write_summary(self, summary: Dict[str, Any]) -> Path:
        summary_path = Path(str(summary["summary_path"]))
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary_path

    def _build_prewarm_command(
        self,
        options: PersonalStrategyMatrixRunOptions,
        *,
        snapshot_date: str,
    ) -> List[str]:
        command = [
            self.python_executable,
            "scripts/warm_local_strategy_cache.py",
            "--snapshot-date",
            snapshot_date,
            "--output-root",
            str(Path(options.prewarm_output_root)),
            "--top-n",
            str(int(options.prewarm_top_n)),
            "--earnings-top-n",
            str(int(options.prewarm_earnings_top_n)),
            "--kline-days",
            str(int(options.prewarm_kline_days)),
            "--event-catalog-max-age-minutes",
            str(int(options.prewarm_event_catalog_max_age_minutes)),
            "--log-level",
            str(options.log_level),
        ]
        limit = _optional_positive_int(options.prewarm_limit)
        if limit is not None:
            command.extend(["--limit", str(limit)])
        if options.force_refresh_event_catalog:
            command.append("--force-refresh-event-catalog")
        return command

    def _build_bundle_command(
        self,
        options: PersonalStrategyMatrixRunOptions,
        *,
        snapshot_date: str,
    ) -> List[str]:
        command = [
            self.python_executable,
            "scripts/run_fast_review_bundle.py",
            "--strategy-profile-file",
            str(Path(options.strategy_profile_file)),
            "--snapshot-date",
            snapshot_date,
            "--output-dir",
            str(Path(options.output_dir)),
            "--log-level",
            str(options.log_level),
        ]
        limit = _optional_positive_int(options.bundle_limit)
        if limit is not None:
            command.extend(["--limit", str(limit)])
        include_signals = str(options.include_signals or "").strip()
        if include_signals:
            command.extend(["--include-signals", include_signals])
        if options.safe_mode:
            command.append("--safe-mode")
        if not options.persist_snapshots:
            command.append("--skip-persist-snapshots")
        return command

    def _command_payload(self, *, name: str, argv: List[str]) -> Dict[str, Any]:
        return {
            "name": name,
            "argv": argv,
            "command": shlex.join(argv),
            "cwd": str(self.project_root),
        }
