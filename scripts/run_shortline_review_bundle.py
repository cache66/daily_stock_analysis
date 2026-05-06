#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-entry shortline review bundle for one-machine daily usage."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "manual_runs" / "shortline_review_bundle_latest"
DEFAULT_MANUAL_RUNS_ROOT = PROJECT_ROOT / "data" / "manual_runs"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "data" / "runtime" / "shortline_hub"
DEFAULT_REPO_PYTHON = sys.executable or "python"
DEFAULT_WT_SCRIPT_PATH = Path("D:/bb/WonderTrader/bridge/wt_export_candidates.py")
DEFAULT_FG_SCRIPT_PATH = Path("D:/bb/FinGenius/bridge/fg_explain_candidate.py")
DEFAULT_WT_WORKDIR = Path("D:/bb/WonderTrader")
DEFAULT_FG_WORKDIR = Path("D:/bb/FinGenius")
DEFAULT_FG_PYTHON = "D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe"
DEFAULT_EXPLAIN_CACHE_PATH = DEFAULT_RUNTIME_ROOT / "explain_cache" / "shortline_explain_cache.json"
DEFAULT_TRACKING_HISTORY_PATH = DEFAULT_RUNTIME_ROOT / "tracking" / "shortline_tracking_history.json"
DEFAULT_WT_SOURCE_MODE = "prefer_real_engine"
ARTIFACT_INDEX_DIRNAME = "shortline_review_bundle_index"

logger = logging.getLogger("shortline_review_bundle")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run shortline daily + runs summary + fast review into one fixed bundle output."
    )
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--repo-python-executable", default=DEFAULT_REPO_PYTHON)
    parser.add_argument("--wt-python-executable", default=DEFAULT_REPO_PYTHON)
    parser.add_argument("--fg-python-executable", default=DEFAULT_FG_PYTHON)
    parser.add_argument("--wt-script-path", default=str(DEFAULT_WT_SCRIPT_PATH))
    parser.add_argument("--fg-script-path", default=str(DEFAULT_FG_SCRIPT_PATH))
    parser.add_argument("--wt-workdir", default=str(DEFAULT_WT_WORKDIR))
    parser.add_argument("--fg-workdir", default=str(DEFAULT_FG_WORKDIR))
    parser.add_argument("--wt-runtime-dir", default=str(DEFAULT_RUNTIME_ROOT / "wondertrader"))
    parser.add_argument("--fg-runtime-dir", default=str(DEFAULT_RUNTIME_ROOT / "fingenius"))
    parser.add_argument(
        "--wt-source-mode",
        default=DEFAULT_WT_SOURCE_MODE,
        choices=["prefer_real_engine", "prefer_bridge_data"],
    )
    parser.add_argument(
        "--enable-explain-cache",
        dest="enable_explain_cache",
        action="store_true",
        help="Enable shortline explain cache for the shortline child step. Default enabled.",
    )
    parser.add_argument(
        "--disable-explain-cache",
        dest="enable_explain_cache",
        action="store_false",
        help="Disable shortline explain cache for the shortline child step.",
    )
    parser.set_defaults(enable_explain_cache=True)
    parser.add_argument("--explain-cache-path", default=str(DEFAULT_EXPLAIN_CACHE_PATH))
    parser.add_argument("--explain-cache-mode", default="light")
    parser.add_argument("--tracking-history-path", default=str(DEFAULT_TRACKING_HISTORY_PATH))
    parser.add_argument("--runs-root", default=str(DEFAULT_MANUAL_RUNS_ROOT))
    parser.add_argument("--runs-summary-limit", type=int, default=12)
    parser.add_argument(
        "--quality-profile",
        default="standard",
        choices=["standard", "strict", "off"],
    )
    parser.add_argument("--fast-review-include-signals", default="hundred_day_high")
    parser.add_argument("--fast-review-limit", type=int, default=5)
    parser.add_argument(
        "--fast-review-persist-snapshots",
        dest="fast_review_persist_snapshots",
        action="store_true",
        help="Persist fast-review snapshots to DB. Default enabled to preserve existing behavior.",
    )
    parser.add_argument(
        "--skip-fast-review-persist-snapshots",
        dest="fast_review_persist_snapshots",
        action="store_false",
        help="Do not persist fast-review snapshots to DB.",
    )
    parser.set_defaults(fast_review_persist_snapshots=True)
    parser.add_argument(
        "--persist-snapshot",
        dest="persist_snapshot",
        action="store_true",
        help="Persist same-day shortline snapshot before fast review. Default enabled.",
    )
    parser.add_argument(
        "--skip-persist-snapshot",
        dest="persist_snapshot",
        action="store_false",
        help="Do not persist shortline snapshots in the shortline leg.",
    )
    parser.set_defaults(persist_snapshot=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only materialize bundle commands and manifest without executing child steps.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _default_run_id(trade_date: str) -> str:
    return f"shortline_bundle_{str(trade_date).replace('-', '')}"


def _run_command(*, command: list[str], workdir: Path, stdout_path: Path, stderr_path: Path) -> int:
    completed = subprocess.run(
        command,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    stdout_line_count = len((completed.stdout or "").splitlines())
    stderr_line_count = len((completed.stderr or "").splitlines())
    if stdout_line_count or stderr_line_count:
        logger.info(
            "bundle child output captured: command=%s stdout_lines=%s stderr_lines=%s stdout_log=%s stderr_log=%s",
            " ".join(command),
            stdout_line_count,
            stderr_line_count,
            _relpath_text(stdout_path),
            _relpath_text(stderr_path),
        )
    if completed.returncode != 0:
        raise RuntimeError(
            "bundle step failed: command={cmd} returncode={rc}".format(
                cmd=" ".join(command),
                rc=completed.returncode,
            )
    )
    return int(completed.returncode)


def _build_step_manifest(
    *,
    step_name: str,
    command: list[str],
    workdir: Path,
    output_dir: Path,
    logs_dir: Path,
    status: str,
) -> dict[str, Any]:
    stdout_path = logs_dir / f"{step_name}.stdout.log"
    stderr_path = logs_dir / f"{step_name}.stderr.log"
    started_at = datetime.now().isoformat(timespec="seconds")
    return {
        "name": step_name,
        "status": status,
        "command": list(command),
        "workdir": str(workdir),
        "output_dir": _relpath_text(output_dir),
        "stdout_log_path": _relpath_text(stdout_path),
        "stderr_log_path": _relpath_text(stderr_path),
        "returncode": None,
        "elapsed_ms": 0,
        "started_at": started_at,
        "finished_at": started_at,
    }


def _tail_lines(path: Path, *, limit: int = 5) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    trimmed = [line.rstrip() for line in lines if str(line or "").strip()]
    return trimmed[-limit:]


def _build_step_diagnostics(*, stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    stdout_lines = _tail_lines(stdout_path)
    stderr_lines = _tail_lines(stderr_path)
    return {
        "stdout_excerpt": "\n".join(stdout_lines),
        "stderr_excerpt": "\n".join(stderr_lines),
        "stdout_line_count": len(stdout_lines),
        "stderr_line_count": len(stderr_lines),
    }


def _parse_bool_text(value: str) -> bool | None:
    lowered = str(value or "").strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


_FAST_REVIEW_SIGNAL_DONE_RE = re.compile(
    r"fast review signal done: key=(?P<key>[^ ]+) count=(?P<count>\d+) csv=(?P<csv>.+?) elapsed=(?P<elapsed>[0-9.]+)s"
)
_DATED_BUNDLE_DIR_RE = re.compile(r"shortline_review_bundle_\d{8}$")


def _parse_fast_review_runtime_summary(stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "signals": {},
        "signal_elapsed_sec": {},
        "skipped_signals": [],
        "output_paths": {},
    }
    try:
        stdout_lines = stdout_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        stdout_lines = []
    try:
        stderr_lines = stderr_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        stderr_lines = []

    for raw_line in stdout_lines:
        line = str(raw_line or "").strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key.startswith("signal_") and key.endswith("_elapsed_sec"):
            signal_key = key[len("signal_") : -len("_elapsed_sec")]
            try:
                summary["signal_elapsed_sec"][signal_key] = float(value)
            except ValueError:
                continue
        elif key == "include_signals":
            summary["include_signals"] = value
        elif key == "persist_snapshots":
            parsed = _parse_bool_text(value)
            if parsed is not None:
                summary["persist_snapshots"] = parsed
        elif key == "skipped_signals_count":
            try:
                summary["skipped_signals_count"] = int(value)
            except ValueError:
                continue
        elif key == "skipped_signal":
            parts = value.split("|", 3)
            summary["skipped_signals"].append(
                {
                    "signal_key": parts[0] if len(parts) > 0 else "",
                    "signal_type": parts[1] if len(parts) > 1 else "",
                    "reason": parts[2] if len(parts) > 2 else "",
                    "detail": parts[3] if len(parts) > 3 else "",
                }
            )
        elif key in {
            "summary_md",
            "latest_md",
            "unified_csv",
            "strategy_focus_md",
            "strategy_focus_csv",
            "resonance_md",
            "resonance_csv",
            "earnings_focus_md",
            "earnings_focus_csv",
        }:
            summary["output_paths"][key] = value
        elif key == "shortline_focus_attached":
            parsed = _parse_bool_text(value)
            if parsed is not None:
                summary[key] = parsed
    for raw_line in stderr_lines:
        line = str(raw_line or "").strip()
        if not line:
            continue
        matched = _FAST_REVIEW_SIGNAL_DONE_RE.search(line)
        if not matched:
            continue
        signal_key = str(matched.group("key") or "").strip()
        try:
            elapsed = float(matched.group("elapsed"))
        except ValueError:
            continue
        try:
            count = int(matched.group("count"))
        except ValueError:
            count = 0
        csv_path = str(matched.group("csv") or "").strip()
        summary["signal_elapsed_sec"][signal_key] = elapsed
        summary["signals"][signal_key] = {
            "elapsed_sec": elapsed,
            "count": count,
            "csv_path": csv_path,
        }
    summary["total_signal_elapsed_sec"] = round(
        sum(float(item.get("elapsed_sec") or 0.0) for item in summary.get("signals", {}).values()),
        2,
    )
    summary["total_signal_count"] = int(
        sum(int(item.get("count") or 0) for item in summary.get("signals", {}).values())
    )
    skipped_reason_counts: dict[str, int] = {}
    for item in summary.get("skipped_signals", []):
        reason = str(item.get("reason") or "").strip() or "unknown"
        skipped_reason_counts[reason] = int(skipped_reason_counts.get(reason, 0)) + 1
    summary["skipped_reason_counts"] = skipped_reason_counts
    return summary


def _classify_output_dir(output_dir: Path) -> str:
    resolved = output_dir.resolve()
    default_latest = DEFAULT_OUTPUT_DIR.resolve()
    name = resolved.name.lower()
    if resolved == default_latest:
        return "default_latest"
    if "smoke" in name:
        return "smoke"
    if "dryrun" in name or "dry_run" in name:
        return "dry_run"
    if _DATED_BUNDLE_DIR_RE.fullmatch(resolved.name):
        return "dated"
    return "custom"


def _build_artifact_governance(*, output_dir: Path, trade_date: str, run_id: str) -> dict[str, Any]:
    index_root = (DEFAULT_MANUAL_RUNS_ROOT / ARTIFACT_INDEX_DIRNAME).resolve()
    pointer_dir = (index_root / trade_date / run_id).resolve()
    pointer_json_path = (pointer_dir / "bundle_pointer.json").resolve()
    latest_json_path = (index_root / "latest.json").resolve()
    return {
        "layout_version": "shortline_bundle_artifacts_v1",
        "output_class": _classify_output_dir(output_dir),
        "index_root": _relpath_text(index_root),
        "pointer_dir_path": _relpath_text(pointer_dir),
        "pointer_json_path": _relpath_text(pointer_json_path),
        "latest_json_path": _relpath_text(latest_json_path),
    }


def _write_artifact_governance_pointers(*, manifest: dict[str, Any], governance: dict[str, Any], output_dir: Path) -> None:
    index_root = (DEFAULT_MANUAL_RUNS_ROOT / ARTIFACT_INDEX_DIRNAME).resolve()
    pointer_dir = (index_root / str(manifest.get("trade_date") or "") / str(manifest.get("run_id") or "")).resolve()
    pointer_dir.mkdir(parents=True, exist_ok=True)
    pointer_json_path = pointer_dir / "bundle_pointer.json"
    latest_json_path = index_root / "latest.json"
    pointer_payload = {
        "trade_date": str(manifest.get("trade_date") or ""),
        "run_id": str(manifest.get("run_id") or ""),
        "status": str(manifest.get("status") or "success"),
        "execution_status": str(manifest.get("execution_status") or "success"),
        "overall_status": str(manifest.get("overall_status") or "success"),
        "generated_at": str(manifest.get("generated_at") or ""),
        "dry_run": bool(manifest.get("dry_run") or False),
        "output_class": str(governance.get("output_class") or ""),
        "output_dir": str(output_dir.resolve()),
        "manifest_path": str((output_dir / "bundle_manifest.json").resolve()),
        "report_path": str((output_dir / "bundle_report.md").resolve()),
    }
    pointer_json_path.write_text(json.dumps(pointer_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json_path.parent.mkdir(parents=True, exist_ok=True)
    latest_json_path.write_text(json.dumps(pointer_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_step(
    *,
    step_name: str,
    command: list[str],
    workdir: Path,
    output_dir: Path,
    logs_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    step_manifest = _build_step_manifest(
        step_name=step_name,
        command=command,
        workdir=workdir,
        output_dir=output_dir,
        logs_dir=logs_dir,
        status="dry_run" if dry_run else "pending",
    )
    if dry_run:
        return step_manifest

    stdout_path = logs_dir / f"{step_name}.stdout.log"
    stderr_path = logs_dir / f"{step_name}.stderr.log"
    started_monotonic = time.perf_counter()
    try:
        returncode = _run_command(
            command=command,
            workdir=workdir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    except Exception as exc:
        elapsed_ms = int(round((time.perf_counter() - started_monotonic) * 1000))
        step_manifest.update(
            {
                "status": "failed",
                "elapsed_ms": elapsed_ms,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error_message": str(exc),
                "diagnostics": _build_step_diagnostics(stdout_path=stdout_path, stderr_path=stderr_path),
            }
        )
        return step_manifest

    elapsed_ms = int(round((time.perf_counter() - started_monotonic) * 1000))
    step_manifest.update(
        {
            "status": "success",
            "returncode": int(returncode),
            "elapsed_ms": elapsed_ms,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "diagnostics": _build_step_diagnostics(stdout_path=stdout_path, stderr_path=stderr_path),
        }
    )
    return step_manifest


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _relpath_text(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _format_compact_counts(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in payload.items())


def _format_compact_list(payload: Any) -> str:
    if not isinstance(payload, list) or not payload:
        return "-"
    return ", ".join(str(item) for item in payload)


def build_shortline_command(
    *,
    args: argparse.Namespace,
    run_id: str,
    output_dir: Path,
) -> list[str]:
    command = [
        str(getattr(args, "repo_python_executable", DEFAULT_REPO_PYTHON)),
        str(Path("scripts") / "run_shortline_hub.py"),
        "--mode",
        "process",
        "--trade-date",
        str(getattr(args, "trade_date", "")),
        "--top-n",
        str(max(0, int(getattr(args, "top_n", 3)))),
        "--run-id",
        run_id,
        "--output-dir",
        str(output_dir),
        "--wt-python-executable",
        str(getattr(args, "wt_python_executable", DEFAULT_REPO_PYTHON)),
        "--wt-script-path",
        str(getattr(args, "wt_script_path", DEFAULT_WT_SCRIPT_PATH)),
        "--wt-workdir",
        str(getattr(args, "wt_workdir", DEFAULT_WT_WORKDIR)),
        "--wt-runtime-dir",
        str(getattr(args, "wt_runtime_dir", DEFAULT_RUNTIME_ROOT / "wondertrader")),
        "--wt-source-mode",
        str(getattr(args, "wt_source_mode", DEFAULT_WT_SOURCE_MODE) or DEFAULT_WT_SOURCE_MODE),
        "--fg-python-executable",
        str(getattr(args, "fg_python_executable", DEFAULT_FG_PYTHON)),
        "--fg-script-path",
        str(getattr(args, "fg_script_path", DEFAULT_FG_SCRIPT_PATH)),
        "--fg-workdir",
        str(getattr(args, "fg_workdir", DEFAULT_FG_WORKDIR)),
        "--fg-runtime-dir",
        str(getattr(args, "fg_runtime_dir", DEFAULT_RUNTIME_ROOT / "fingenius")),
        "--log-level",
        str(getattr(args, "log_level", "INFO")),
    ]
    if bool(getattr(args, "persist_snapshot", False)):
        command.append("--persist-snapshot")
    if bool(getattr(args, "enable_explain_cache", True)):
        command.extend(
            [
                "--enable-explain-cache",
                "--explain-cache-path",
                str(Path(str(getattr(args, "explain_cache_path", DEFAULT_EXPLAIN_CACHE_PATH))).resolve()),
                "--explain-cache-mode",
                str(getattr(args, "explain_cache_mode", "light") or "light"),
            ]
        )
    tracking_history_path = str(getattr(args, "tracking_history_path", "") or "").strip()
    if tracking_history_path:
        command.extend(
            [
                "--tracking-history-path",
                str(Path(tracking_history_path).resolve()),
            ]
        )
    return command


def build_runs_summary_command(*, args: argparse.Namespace, output_dir: Path) -> list[str]:
    return [
        str(getattr(args, "repo_python_executable", DEFAULT_REPO_PYTHON)),
        str(Path("scripts") / "summarize_shortline_runs.py"),
        "--runs-root",
        str(getattr(args, "runs_root", DEFAULT_MANUAL_RUNS_ROOT)),
        "--output-dir",
        str(output_dir),
        "--limit",
        str(max(0, int(getattr(args, "runs_summary_limit", 12)))),
        "--quality-profile",
        str(getattr(args, "quality_profile", "standard") or "standard"),
        "--log-level",
        str(getattr(args, "log_level", "INFO")),
    ]


def build_fast_review_command(*, args: argparse.Namespace, output_dir: Path) -> list[str]:
    command = [
        str(getattr(args, "repo_python_executable", DEFAULT_REPO_PYTHON)),
        str(Path("scripts") / "run_fast_review_bundle.py"),
        "--snapshot-date",
        str(getattr(args, "trade_date", "")),
        "--output-dir",
        str(output_dir),
        "--include-signals",
        str(getattr(args, "fast_review_include_signals", "hundred_day_high")),
        "--limit",
        str(max(0, int(getattr(args, "fast_review_limit", 5)))),
        "--log-level",
        str(getattr(args, "log_level", "INFO")),
    ]
    if bool(getattr(args, "fast_review_persist_snapshots", True)):
        command.append("--persist-snapshots")
    else:
        command.append("--skip-persist-snapshots")
    return command


def _tracking_history_ready_for_trade_date(
    trade_date: str,
    trade_date_counts: dict[str, Any],
) -> bool:
    normalized_trade_date = str(trade_date or "").strip()
    if not normalized_trade_date:
        return False
    available_trade_dates = [
        str(value).strip()
        for value in (trade_date_counts or {}).keys()
        if str(value).strip()
    ]
    return any(value < normalized_trade_date for value in available_trade_dates)


def evaluate_bundle_quality(
    *,
    dry_run: bool,
    quality_profile: str,
    wt_source_mode: str,
    current_trade_date: str,
    shortline_summary: dict[str, Any],
    runs_summary_payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_profile = str(quality_profile or "standard").strip().lower() or "standard"
    if normalized_profile not in {"standard", "strict", "off"}:
        normalized_profile = "standard"
    trade_date_counts = dict(runs_summary_payload.get("trade_date_counts") or {})
    tracking_history_ready = _tracking_history_ready_for_trade_date(
        current_trade_date,
        trade_date_counts,
    )
    quality_summary = {
        "profile": normalized_profile,
        "verdict": "pass",
        "failed_checks": [],
        "warning_checks": [],
        "passed_checks": [],
        "recommendations": [],
        "signals": {
            "tracking_history_ready": bool(tracking_history_ready),
            "source_real_engine_expected_passed": None,
        },
    }
    if dry_run or normalized_profile == "off":
        quality_summary["verdict"] = "off"
        if dry_run:
            quality_summary["recommendations"] = ["Dry run skipped quality evaluation."]
        return quality_summary

    candidate_count = int(shortline_summary.get("candidate_count") or 0)
    explanation_count = int(shortline_summary.get("explanation_count") or 0)
    scan_source_counts = dict(shortline_summary.get("scan_source_counts") or {})
    explain_cache_enabled = bool(shortline_summary.get("explain_cache_enabled") or False)
    explain_cache_hit_count = int(shortline_summary.get("explain_cache_hit_count") or 0)
    explain_cache_miss_count = int(shortline_summary.get("explain_cache_miss_count") or 0)
    explain_parallel_workers = int(shortline_summary.get("explain_parallel_workers") or 0)
    tracking_repeat_symbol_count = int(shortline_summary.get("tracking_repeat_symbol_count") or 0)
    latest_run_quality = dict(runs_summary_payload.get("latest_run_quality") or {})

    failed_checks: list[str] = []
    warning_checks: list[str] = []
    passed_checks: list[str] = []
    recommendations: list[str] = []

    if str(shortline_summary.get("report_path") or "").strip() and str(
        shortline_summary.get("summary_json_path") or ""
    ).strip():
        passed_checks.append("artifacts_complete")
    else:
        failed_checks.append("artifacts_complete")
        recommendations.append("Check shortline artifact paths because expected outputs are missing.")

    if candidate_count == explanation_count:
        passed_checks.append("explanations_match_candidates")
    else:
        failed_checks.append("explanations_match_candidates")
        recommendations.append("Check candidate/explanation counts because the shortline child is incomplete.")

    if str(wt_source_mode or "").strip() == "prefer_real_engine" and candidate_count > 0:
        real_engine_count = int(scan_source_counts.get("wondertrader_real_engine") or 0)
        if len(scan_source_counts) == 1 and real_engine_count == candidate_count:
            passed_checks.append("source_real_engine_expected")
            quality_summary["signals"]["source_real_engine_expected_passed"] = True
        else:
            failed_checks.append("source_real_engine_expected")
            recommendations.append("Inspect scan_source_counts and wt_source_mode for source drift.")
            quality_summary["signals"]["source_real_engine_expected_passed"] = False
    else:
        passed_checks.append("source_real_engine_expected")

    if candidate_count > 0 and tracking_history_ready and tracking_repeat_symbol_count <= 0:
        warning_checks.append("tracking_continuity_weak")
        recommendations.append("Review tracking continuity before trusting one-day-only candidates.")
    else:
        passed_checks.append("tracking_continuity_weak")

    if explain_cache_enabled and explain_cache_hit_count < explain_cache_miss_count:
        warning_checks.append("cache_health_low")
        recommendations.append("Warm or inspect explain cache because misses exceeded hits.")
    else:
        passed_checks.append("cache_health_low")

    if explain_cache_miss_count >= 2 and explain_parallel_workers <= 1:
        warning_checks.append("parallelism_underused")
        recommendations.append("Check explain parallel worker settings because cache misses fell back to serial execution.")
    else:
        passed_checks.append("parallelism_underused")

    for check_name in latest_run_quality.get("failed_checks") or []:
        if check_name not in failed_checks:
            failed_checks.append(str(check_name))
    for check_name in latest_run_quality.get("warning_checks") or []:
        normalized_check = str(check_name)
        if normalized_check not in warning_checks:
            warning_checks.append(normalized_check)
    for note in latest_run_quality.get("recommendations") or []:
        text = str(note or "").strip()
        if text and text not in recommendations:
            recommendations.append(text)

    if failed_checks:
        verdict = "fail"
    elif warning_checks:
        verdict = "degraded"
    else:
        verdict = "pass"
    if normalized_profile == "strict" and not failed_checks and len(warning_checks) >= 2:
        verdict = "fail"
        recommendations.append("Strict profile escalated multiple warnings into a fail verdict.")

    quality_summary["verdict"] = verdict
    quality_summary["failed_checks"] = failed_checks
    quality_summary["warning_checks"] = warning_checks
    quality_summary["passed_checks"] = passed_checks
    quality_summary["recommendations"] = recommendations
    return quality_summary


def _build_bundle_statuses(
    *,
    dry_run: bool,
    failed_step_name: str,
    quality_summary: dict[str, Any],
) -> tuple[str, str, str]:
    if failed_step_name:
        return "failed", "failed", "failed"
    execution_status = "dry_run" if dry_run else "success"
    legacy_status = "success"
    if dry_run:
        return legacy_status, execution_status, "dry_run"
    if str((quality_summary or {}).get("verdict") or "").strip() == "fail":
        return legacy_status, execution_status, "quality_failed"
    return legacy_status, execution_status, "success"


def build_bundle_report(*, manifest: dict[str, Any]) -> str:
    shortline = manifest.get("shortline_run") or {}
    runs_summary = manifest.get("runs_summary") or {}
    fast_review = manifest.get("fast_review") or {}
    quality_summary = manifest.get("quality_summary") or {}
    steps = manifest.get("steps") or {}
    failure = manifest.get("failure") or {}
    artifact_governance = manifest.get("artifact_governance") or {}
    lines = [
        "# Shortline Review Bundle",
        "",
        f"- trade_date: {manifest.get('trade_date', '')}",
        f"- run_id: {manifest.get('run_id', '')}",
        f"- generated_at: {manifest.get('generated_at', '')}",
        f"- dry_run: {manifest.get('dry_run', False)}",
        f"- status: {manifest.get('status', 'success')}",
        f"- execution_status: {manifest.get('execution_status', '-')}",
        f"- overall_status: {manifest.get('overall_status', '-')}",
        f"- quality_verdict: {quality_summary.get('verdict', '-')}",
        f"- quality_profile: {quality_summary.get('profile', '-')}",
        f"- quality_failed_due_to: {_format_compact_list(manifest.get('quality_failed_due_to'))}",
        f"- tracking_history_ready: {(quality_summary.get('signals') or {}).get('tracking_history_ready', '-')}",
        f"- source_real_engine_expected_passed: {(quality_summary.get('signals') or {}).get('source_real_engine_expected_passed', '-')}",
        f"- failed_checks: {_format_compact_list(quality_summary.get('failed_checks'))}",
        f"- warning_checks: {_format_compact_list(quality_summary.get('warning_checks'))}",
        f"- recommendations: {_format_compact_list(quality_summary.get('recommendations'))}",
        "",
        "## Shortline Runtime",
        "",
        f"- explanation_count: {shortline.get('explanation_count', 0)}",
        f"- tier_counts: {_format_compact_counts(shortline.get('review_tier_counts'))}",
        f"- scan_sources: {_format_compact_counts(shortline.get('scan_source_counts'))}",
        f"- upstream_tools: {_format_compact_counts(shortline.get('upstream_tool_hit_counts'))}",
        f"- explain_elapsed_ms: total={shortline.get('total_explain_elapsed_ms', 0)}, orchestrator={shortline.get('orchestrator_explain_elapsed_ms', 0)}",
        f"- cache: enabled={shortline.get('explain_cache_enabled', False)}, mode={shortline.get('explain_cache_mode', '') or '-'}, hits={shortline.get('explain_cache_hit_count', 0)}, misses={shortline.get('explain_cache_miss_count', 0)}",
        f"- parallel_workers: {shortline.get('explain_parallel_workers', 0)}",
        f"- tracking: repeat_symbol_count={shortline.get('tracking_repeat_symbol_count', 0)}, longest_streak_days={shortline.get('tracking_longest_streak_days', 0)}",
        "",
        "## Runs Summary Runtime",
        "",
        f"- trade_date_counts: {_format_compact_counts(runs_summary.get('trade_date_counts'))}",
        f"- upstream_tool_counts: {_format_compact_counts(runs_summary.get('upstream_tool_counts'))}",
        f"- cache_totals: hits={runs_summary.get('total_explain_cache_hits', 0)}, misses={runs_summary.get('total_explain_cache_misses', 0)}, cache_enabled_runs={runs_summary.get('cache_enabled_run_count', 0)}",
        f"- max_parallel_workers: {runs_summary.get('max_explain_parallel_workers', 0)}",
        f"- anomaly_counts: {_format_compact_counts(runs_summary.get('anomaly_counts'))}",
        f"- latest_run_anomalies: {_format_compact_list(runs_summary.get('latest_run_anomalies'))}",
        f"- latest_run_snapshot: candidate_count={runs_summary.get('latest_run_candidate_count', 0)}, total_explain_elapsed_ms={runs_summary.get('latest_run_total_explain_elapsed_ms', 0)}",
        f"- latest_run_top_symbols: {_format_compact_list(runs_summary.get('latest_run_top_symbols'))}",
        f"- latest_run_upstream_tools: {_format_compact_list(runs_summary.get('latest_run_used_upstream_tools'))}",
        "",
    ]
    runtime_summary = fast_review.get("runtime_summary") or {}
    if runtime_summary:
        lines.extend(
            [
                "## Fast Review Runtime",
                "",
                f"- include_signals: {runtime_summary.get('include_signals', '-')}",
                f"- persist_snapshots: {runtime_summary.get('persist_snapshots', '-')}",
                f"- total_signal_count: {runtime_summary.get('total_signal_count', 0)}",
                f"- total_signal_elapsed_sec: {runtime_summary.get('total_signal_elapsed_sec', 0)}",
                f"- skipped_signals_count: {runtime_summary.get('skipped_signals_count', 0)}",
            ]
        )
        skipped_reason_counts = runtime_summary.get("skipped_reason_counts") or {}
        if skipped_reason_counts:
            parts = [f"{key}={value}" for key, value in skipped_reason_counts.items()]
            lines.append(f"- skipped_reason_counts: {', '.join(parts)}")
        for signal_key, elapsed in (runtime_summary.get("signal_elapsed_sec") or {}).items():
            lines.append(f"- signal_elapsed[{signal_key}]: {elapsed}")
        lines.append("")
    if failure:
        failed_step_key = str(failure.get("step") or "")
        failed_step_details = steps.get(failed_step_key) or {}
        lines.extend(
            [
                "## Failed Step",
                "",
                f"- step: {failure.get('step', '-')}",
                f"- error: {failure.get('error_message', '-')}",
                f"- stdout_log: {failed_step_details.get('stdout_log_path', '-')}",
                f"- stderr_log: {failed_step_details.get('stderr_log_path', '-')}",
                f"- stdout_excerpt: {failure.get('stdout_excerpt', '-')}",
                f"- stderr_excerpt: {failure.get('stderr_excerpt', '-')}",
                f"- pending_steps: {', '.join(_pending_step_names(steps)) or '-'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Shortline",
            "",
            f"- output_dir: {shortline.get('output_dir', '-')}",
            f"- report: {shortline.get('report_path', '-')}",
            f"- candidate_count: {shortline.get('candidate_count', 0)}",
            "",
            "## Runs Summary",
            "",
            f"- output_dir: {runs_summary.get('output_dir', '-')}",
            f"- markdown: {runs_summary.get('summary_md_path', '-')}",
            f"- run_count: {runs_summary.get('run_count', 0)}",
            f"- latest_run_id: {runs_summary.get('latest_run_id', '-')}",
            "",
            "## Fast Review",
            "",
            f"- output_dir: {fast_review.get('output_dir', '-')}",
            f"- latest_md: {fast_review.get('latest_md_path', '-')}",
            f"- dated_md: {fast_review.get('summary_md_path', '-')}",
            "",
            "## Artifact Governance",
            "",
            f"- output_class: {artifact_governance.get('output_class', '-')}",
            f"- pointer_json: {artifact_governance.get('pointer_json_path', '-')}",
            f"- latest_json: {artifact_governance.get('latest_json_path', '-')}",
            "",
        ]
    )
    lines.extend(
        [
            "## Steps",
            "",
        ]
    )
    for step_key in ("shortline", "runs_summary", "fast_review"):
        step = steps.get(step_key) or {}
        lines.extend(
            [
                f"- {step_key}: status={step.get('status', '-')}, elapsed_ms={step.get('elapsed_ms', 0)}",
                f"  stdout={step.get('stdout_log_path', '-')}",
                f"  stderr={step.get('stderr_log_path', '-')}",
            ]
        )
    return "\n".join(lines)


def _pending_step_names(steps: dict[str, Any]) -> list[str]:
    pending: list[str] = []
    for step_key in ("shortline", "runs_summary", "fast_review"):
        step = steps.get(step_key) or {}
        if str(step.get("status") or "") == "pending":
            pending.append(step_key)
    return pending


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_id = str(args.run_id or "").strip() or _default_run_id(str(args.trade_date))
    output_dir = Path(str(args.output_dir))
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    else:
        output_dir = output_dir.resolve()
    shortline_dir = (output_dir / "shortline_run").resolve()
    runs_summary_dir = (output_dir / "shortline_runs_summary").resolve()
    fast_review_dir = (output_dir / "fast_review").resolve()
    logs_dir = (output_dir / "logs").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    shortline_command = build_shortline_command(args=args, run_id=run_id, output_dir=shortline_dir)
    runs_summary_command = build_runs_summary_command(args=args, output_dir=runs_summary_dir)
    fast_review_command = build_fast_review_command(args=args, output_dir=fast_review_dir)

    logger.info("bundle start: trade_date=%s run_id=%s output_dir=%s", args.trade_date, run_id, output_dir)
    dry_run = bool(getattr(args, "dry_run", False))
    step_specs = [
        ("shortline", shortline_command, shortline_dir),
        ("runs_summary", runs_summary_command, runs_summary_dir),
        ("fast_review", fast_review_command, fast_review_dir),
    ]
    steps = {
        step_name: _build_step_manifest(
            step_name=step_name,
            command=command,
            workdir=PROJECT_ROOT,
            output_dir=step_output_dir,
            logs_dir=logs_dir,
            status="dry_run" if dry_run else "pending",
        )
        for step_name, command, step_output_dir in step_specs
    }
    failed_step_name = ""
    for step_name, command, step_output_dir in step_specs:
        step_result = _run_step(
            step_name=step_name,
            command=command,
            workdir=PROJECT_ROOT,
            output_dir=step_output_dir,
            logs_dir=logs_dir,
            dry_run=dry_run,
        )
        steps[step_name] = step_result
        if step_result.get("status") == "failed":
            failed_step_name = step_name
            break

    shortline_summary = {}
    runs_summary_payload = {}
    if not dry_run:
        shortline_summary = _load_json(shortline_dir / "run_summary.json")
        runs_summary_payload = _load_json(runs_summary_dir / "shortline_runs_summary.json")
    fast_review_summary_path = fast_review_dir / str(args.trade_date) / "review" / "fast_review_summary.md"
    fast_review_latest_md = fast_review_dir / "fast_review_summary_latest.md"
    fast_review_runtime_summary = {}
    if not dry_run:
        fast_review_runtime_summary = _parse_fast_review_runtime_summary(
            logs_dir / "fast_review.stdout.log",
            logs_dir / "fast_review.stderr.log",
        )
    artifact_governance = _build_artifact_governance(
        output_dir=output_dir,
        trade_date=str(args.trade_date),
        run_id=run_id,
    )

    manifest = {
        "trade_date": str(args.trade_date),
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "output_dir": _relpath_text(output_dir),
        "logs_dir": _relpath_text(logs_dir),
        "commands": {
            "shortline": shortline_command,
            "runs_summary": runs_summary_command,
            "fast_review": fast_review_command,
        },
        "artifact_governance": artifact_governance,
        "steps": steps,
        "shortline_run": {
            "output_dir": _relpath_text(shortline_dir),
            "run_id": str(shortline_summary.get("run_id") or run_id),
            "candidate_count": int(shortline_summary.get("candidate_count") or 0),
            "explanation_count": int(shortline_summary.get("explanation_count") or 0),
            "top_pick_count": int(shortline_summary.get("top_pick_count") or 0),
            "watchlist_count": int(shortline_summary.get("watchlist_count") or 0),
            "high_risk_mover_count": int(shortline_summary.get("high_risk_mover_count") or 0),
            "scan_source_counts": dict(shortline_summary.get("scan_source_counts") or {}),
            "review_tier_counts": dict(shortline_summary.get("review_tier_counts") or {}),
            "upstream_tool_hit_counts": dict(shortline_summary.get("upstream_tool_hit_counts") or {}),
            "total_explain_elapsed_ms": int(shortline_summary.get("total_explain_elapsed_ms") or 0),
            "orchestrator_explain_elapsed_ms": int(shortline_summary.get("orchestrator_explain_elapsed_ms") or 0),
            "explain_cache_enabled": bool(shortline_summary.get("explain_cache_enabled") or False),
            "explain_cache_mode": str(shortline_summary.get("explain_cache_mode") or ""),
            "explain_cache_hit_count": int(shortline_summary.get("explain_cache_hit_count") or 0),
            "explain_cache_miss_count": int(shortline_summary.get("explain_cache_miss_count") or 0),
            "explain_parallel_workers": int(shortline_summary.get("explain_parallel_workers") or 0),
            "tracking_repeat_symbol_count": int(shortline_summary.get("tracking_repeat_symbol_count") or 0),
            "tracking_longest_streak_days": int(shortline_summary.get("tracking_longest_streak_days") or 0),
            "report_path": _relpath_text(shortline_dir / "shortline_report.md"),
            "summary_json_path": _relpath_text(shortline_dir / "run_summary.json"),
        },
        "runs_summary": {
            "output_dir": _relpath_text(runs_summary_dir),
            "run_count": int(runs_summary_payload.get("run_count") or 0),
            "latest_run_id": str(runs_summary_payload.get("latest_run_id") or ""),
            "trade_date_counts": dict(runs_summary_payload.get("trade_date_counts") or {}),
            "upstream_tool_counts": dict(runs_summary_payload.get("upstream_tool_counts") or {}),
            "cache_enabled_run_count": int(runs_summary_payload.get("cache_enabled_run_count") or 0),
            "total_explain_cache_hits": int(runs_summary_payload.get("total_explain_cache_hits") or 0),
            "total_explain_cache_misses": int(runs_summary_payload.get("total_explain_cache_misses") or 0),
            "max_explain_parallel_workers": int(runs_summary_payload.get("max_explain_parallel_workers") or 0),
            "anomaly_counts": dict(runs_summary_payload.get("anomaly_counts") or {}),
            "latest_run_anomalies": list(runs_summary_payload.get("latest_run_anomalies") or []),
            "latest_run_quality": dict(runs_summary_payload.get("latest_run_quality") or {}),
            "quality_verdict_counts": dict(runs_summary_payload.get("quality_verdict_counts") or {}),
            "latest_run_candidate_count": int(((runs_summary_payload.get("runs") or [{}])[0] or {}).get("candidate_count") or 0),
            "latest_run_total_explain_elapsed_ms": int(((runs_summary_payload.get("runs") or [{}])[0] or {}).get("total_explain_elapsed_ms") or 0),
            "latest_run_top_symbols": list(((runs_summary_payload.get("runs") or [{}])[0] or {}).get("top_symbols") or []),
            "latest_run_used_upstream_tools": list(((runs_summary_payload.get("runs") or [{}])[0] or {}).get("used_upstream_tools") or []),
            "summary_md_path": _relpath_text(runs_summary_dir / "shortline_runs_summary.md"),
            "summary_json_path": _relpath_text(runs_summary_dir / "shortline_runs_summary.json"),
        },
        "fast_review": {
            "output_dir": _relpath_text(fast_review_dir),
            "summary_md_path": _relpath_text(fast_review_summary_path),
            "latest_md_path": _relpath_text(fast_review_latest_md),
            "runtime_summary": fast_review_runtime_summary,
        },
    }
    quality_summary = evaluate_bundle_quality(
        dry_run=dry_run,
        quality_profile=str(getattr(args, "quality_profile", "standard") or "standard"),
        wt_source_mode=str(getattr(args, "wt_source_mode", DEFAULT_WT_SOURCE_MODE) or DEFAULT_WT_SOURCE_MODE),
        current_trade_date=str(args.trade_date),
        shortline_summary=manifest.get("shortline_run") or {},
        runs_summary_payload=manifest.get("runs_summary") or {},
    )
    status, execution_status, overall_status = _build_bundle_statuses(
        dry_run=dry_run,
        failed_step_name=failed_step_name,
        quality_summary=quality_summary,
    )
    manifest["quality_summary"] = quality_summary
    manifest["quality_failed_due_to"] = list(quality_summary.get("failed_checks") or [])
    manifest["status"] = status
    manifest["execution_status"] = execution_status
    manifest["overall_status"] = overall_status
    if failed_step_name:
        failed_step = steps.get(failed_step_name) or {}
        diagnostics = failed_step.get("diagnostics") or {}
        manifest["failed_step"] = failed_step_name
        manifest["failure"] = {
            "step": failed_step_name,
            "error_message": str(failed_step.get("error_message") or ""),
            "stderr_excerpt": str(diagnostics.get("stderr_excerpt") or ""),
            "stdout_excerpt": str(diagnostics.get("stdout_excerpt") or ""),
            "stdout_log_path": str(failed_step.get("stdout_log_path") or ""),
            "stderr_log_path": str(failed_step.get("stderr_log_path") or ""),
            "pending_steps": _pending_step_names(steps),
        }
    manifest_path = output_dir / "bundle_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle_report_path = output_dir / "bundle_report.md"
    bundle_report_path.write_text(build_bundle_report(manifest=manifest), encoding="utf-8")
    _write_artifact_governance_pointers(manifest=manifest, governance=artifact_governance, output_dir=output_dir)
    logger.info("bundle finished: manifest=%s", manifest_path)
    return 1 if failed_step_name else 0


if __name__ == "__main__":
    raise SystemExit(main())
