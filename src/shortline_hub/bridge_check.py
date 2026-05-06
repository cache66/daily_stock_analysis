# -*- coding: utf-8 -*-
"""Bridge setup inspection and smoke checks for shortline hub."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from src.shortline_hub.adapters.process_utils import write_json_payload


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


FRESH_DATA_MAX_AGE_DAYS = 3.0


def _describe_data_freshness(preferred_match: Path | None) -> dict[str, Any]:
    if preferred_match is None or not preferred_match.exists():
        return {
            "data_freshness_status": "missing",
            "data_file_modified_at": "",
            "data_file_age_days": None,
        }

    modified_at = datetime.fromtimestamp(preferred_match.stat().st_mtime)
    age_days = round((datetime.now() - modified_at).total_seconds() / 86400.0, 2)
    return {
        "data_freshness_status": "fresh" if age_days <= FRESH_DATA_MAX_AGE_DAYS else "stale",
        "data_file_modified_at": modified_at.isoformat(timespec="seconds"),
        "data_file_age_days": age_days,
    }


def _preferred_data_candidates(bridge_name: str, bridge_data_dir: Path, trade_date: str) -> list[Path]:
    if bridge_name == "wondertrader":
        names = [
            f"wt_candidates_{trade_date}.json",
            "wt_candidates_latest.json",
            f"wt_candidates_{trade_date}.csv",
            "wt_candidates_latest.csv",
        ]
    else:
        names = [
            f"fg_explanations_{trade_date}.json",
            "fg_explanations_latest.json",
        ]
    return [bridge_data_dir / name for name in names]


def inspect_bridge(
    *,
    bridge_name: str,
    python_executable: str,
    script_path: Path,
    workdir: Path | None,
    trade_date: str,
) -> dict[str, Any]:
    resolved_script = Path(script_path)
    bridge_dir = resolved_script.parent
    bridge_data_dir = bridge_dir / "bridge_data"
    preferred_candidates = _preferred_data_candidates(bridge_name, bridge_data_dir, trade_date)
    preferred_match = next((path for path in preferred_candidates if path.exists()), None)
    summary = {
        "bridge_name": bridge_name,
        "python_executable": str(python_executable),
        "script_path": str(resolved_script),
        "script_exists": resolved_script.exists(),
        "workdir": str(workdir) if workdir else "",
        "workdir_exists": bool(workdir and workdir.exists()),
        "bridge_dir": str(bridge_dir),
        "bridge_dir_exists": bridge_dir.exists(),
        "bridge_data_dir": str(bridge_data_dir),
        "bridge_data_dir_exists": bridge_data_dir.exists(),
        "preferred_data_candidates": [str(path) for path in preferred_candidates],
        "preferred_data_file": str(preferred_match) if preferred_match else "",
        "data_file_found": preferred_match is not None,
    }
    summary.update(_describe_data_freshness(preferred_match))
    return summary


def _run_command(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd) if cwd else "",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "passed": completed.returncode == 0,
    }


def _sample_candidate(trade_date: str) -> dict[str, Any]:
    return {
        "candidate_id": f"{trade_date}-300001",
        "symbol": "300001",
        "name": "sample_candidate",
        "trade_date": trade_date,
        "scan_source": "wondertrader_process",
        "trigger_type": "momentum_breakout",
        "trigger_reason": "sample signal",
        "trigger_score": 88.0,
        "price": 23.4,
        "change_pct": 6.5,
        "volume_ratio": 2.4,
        "turnover_rate": 4.8,
        "board_name": "sample_board",
        "risk_flags": [],
    }


def run_wondertrader_script_smoke(
    *,
    python_executable: str,
    script_path: Path,
    workdir: Path | None,
    trade_date: str,
    top_n: int,
    smoke_dir: Path,
) -> dict[str, Any]:
    smoke_dir.mkdir(parents=True, exist_ok=True)
    request_path = smoke_dir / "wondertrader_request.json"
    output_path = smoke_dir / "wondertrader_output.json"
    write_json_payload(
        request_path,
        {
            "trade_date": trade_date,
            "top_n": max(0, int(top_n)),
        },
    )
    result = _run_command(
        [str(python_executable), str(script_path), str(request_path), str(output_path)],
        cwd=workdir,
    )
    result.update(
        {
            "status": "passed" if result["passed"] and output_path.exists() else "failed",
            "request_path": str(request_path),
            "output_path": str(output_path),
            "output_exists": output_path.exists(),
        }
    )
    return result


def run_fingenius_script_smoke(
    *,
    python_executable: str,
    script_path: Path,
    workdir: Path | None,
    trade_date: str,
    smoke_dir: Path,
    candidate_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    smoke_dir.mkdir(parents=True, exist_ok=True)
    request_path = smoke_dir / "fingenius_request.json"
    output_path = smoke_dir / "fingenius_output.json"
    write_json_payload(
        request_path,
        {
            "candidate": candidate_payload or _sample_candidate(trade_date),
        },
    )
    result = _run_command(
        [str(python_executable), str(script_path), str(request_path), str(output_path)],
        cwd=workdir,
    )
    result.update(
        {
            "status": "passed" if result["passed"] and output_path.exists() else "failed",
            "request_path": str(request_path),
            "output_path": str(output_path),
            "output_exists": output_path.exists(),
        }
    )
    return result


def run_orchestrator_smoke(
    *,
    orchestrator_python_executable: str,
    orchestrator_script_path: Path,
    trade_date: str,
    top_n: int,
    run_id: str,
    wt_python_executable: str,
    wt_script_path: Path,
    wt_workdir: Path | None,
    fg_python_executable: str,
    fg_script_path: Path,
    fg_workdir: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    orchestrator_output_dir = output_dir / "orchestrator_smoke"
    runtime_root = output_dir / "runtime"
    command = [
        str(orchestrator_python_executable),
        str(orchestrator_script_path),
        "--mode",
        "process",
        "--trade-date",
        trade_date,
        "--top-n",
        str(max(0, int(top_n))),
        "--run-id",
        run_id,
        "--output-dir",
        str(orchestrator_output_dir),
        "--wt-python-executable",
        str(wt_python_executable),
        "--wt-script-path",
        str(wt_script_path),
        "--wt-runtime-dir",
        str(runtime_root / "wondertrader"),
        "--fg-python-executable",
        str(fg_python_executable),
        "--fg-script-path",
        str(fg_script_path),
        "--fg-runtime-dir",
        str(runtime_root / "fingenius"),
    ]
    if wt_workdir:
        command.extend(["--wt-workdir", str(wt_workdir)])
    if fg_workdir:
        command.extend(["--fg-workdir", str(fg_workdir)])
    result = _run_command(command)
    report_path = orchestrator_output_dir / "shortline_report.md"
    result.update(
        {
            "status": "passed" if result["passed"] and report_path.exists() else "failed",
            "output_dir": str(orchestrator_output_dir),
            "report_path": str(report_path),
            "report_exists": report_path.exists(),
        }
    )
    return result


def build_bridge_check_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Shortline Bridge Check",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- trade_date: {summary['trade_date']}",
        f"- overall_status: {summary['overall_status']}",
        "",
        "## Checks",
        "",
    ]
    for bridge_key in ("wondertrader", "fingenius"):
        item = summary["checks"][bridge_key]
        lines.extend(
            [
                f"### {bridge_key}",
                f"- script_exists: {item['script_exists']}",
                f"- workdir_exists: {item['workdir_exists']}",
                f"- bridge_data_dir_exists: {item['bridge_data_dir_exists']}",
                f"- data_file_found: {item['data_file_found']}",
                f"- preferred_data_file: {item['preferred_data_file'] or '(none)'}",
                f"- data_freshness_status: {item['data_freshness_status']}",
                f"- data_file_modified_at: {item['data_file_modified_at'] or '(none)'}",
                f"- data_file_age_days: {item['data_file_age_days'] if item['data_file_age_days'] is not None else '(none)'}",
                "",
            ]
        )
    if summary["smoke"]:
        lines.append("## Smoke")
        lines.append("")
        for smoke_name, smoke_result in summary["smoke"].items():
            lines.append(f"### {smoke_name}")
            lines.append(f"- status: {smoke_result['status']}")
            lines.append(f"- returncode: {smoke_result.get('returncode', '')}")
            if smoke_result.get("stderr"):
                lines.append(f"- stderr: {smoke_result['stderr']}")
            lines.append("")
    return "\n".join(lines)


def write_bridge_check_artifacts(output_dir: Path, summary: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "bridge_setup_summary.json"
    report_path = output_dir / "bridge_setup_report.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(build_bridge_check_markdown(summary), encoding="utf-8")
    return {
        "summary_json": summary_path,
        "report_md": report_path,
    }


def evaluate_overall_status(summary: dict[str, Any]) -> str:
    checks = list(summary["checks"].values())
    smoke_results = list(summary["smoke"].values())
    required_check_failed = any(
        not item["script_exists"] or not item["workdir_exists"] for item in checks
    )
    smoke_failed = any(item["status"] != "passed" for item in smoke_results)
    has_green_runtime_smoke = bool(smoke_results) and all(
        item["status"] == "passed" for item in smoke_results
    )
    if required_check_failed or smoke_failed:
        return "failed"
    if any(item["data_freshness_status"] == "stale" for item in checks):
        return "warning"
    if any(not item["data_file_found"] for item in checks):
        return "passed" if has_green_runtime_smoke else "warning"
    return "passed"


def build_bridge_check_summary(
    *,
    trade_date: str,
    wt_python_executable: str,
    wt_script_path: Path,
    wt_workdir: Path | None,
    fg_python_executable: str,
    fg_script_path: Path,
    fg_workdir: Path | None,
    output_dir: Path,
    top_n: int,
    run_id: str,
    run_script_smoke: bool,
    should_run_orchestrator_smoke: bool,
    orchestrator_python_executable: str,
    orchestrator_script_path: Path,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    checks = {
        "wondertrader": inspect_bridge(
            bridge_name="wondertrader",
            python_executable=wt_python_executable,
            script_path=wt_script_path,
            workdir=wt_workdir,
            trade_date=trade_date,
        ),
        "fingenius": inspect_bridge(
            bridge_name="fingenius",
            python_executable=fg_python_executable,
            script_path=fg_script_path,
            workdir=fg_workdir,
            trade_date=trade_date,
        ),
    }
    smoke: dict[str, Any] = {}
    smoke_dir = output_dir / "smoke"
    wt_candidate_payload: dict[str, Any] | None = None
    if run_script_smoke:
        smoke["wondertrader_script"] = run_wondertrader_script_smoke(
            python_executable=wt_python_executable,
            script_path=wt_script_path,
            workdir=wt_workdir,
            trade_date=trade_date,
            top_n=top_n,
            smoke_dir=smoke_dir,
        )
        if smoke["wondertrader_script"]["output_exists"]:
            try:
                wt_payload = json.loads(
                    Path(smoke["wondertrader_script"]["output_path"]).read_text(encoding="utf-8")
                )
                if isinstance(wt_payload, list) and wt_payload:
                    wt_candidate_payload = wt_payload[0]
            except (json.JSONDecodeError, OSError, TypeError):
                wt_candidate_payload = None
        smoke["fingenius_script"] = run_fingenius_script_smoke(
            python_executable=fg_python_executable,
            script_path=fg_script_path,
            workdir=fg_workdir,
            trade_date=trade_date,
            smoke_dir=smoke_dir,
            candidate_payload=wt_candidate_payload,
        )
    if should_run_orchestrator_smoke:
        smoke["orchestrator"] = run_orchestrator_smoke(
            orchestrator_python_executable=orchestrator_python_executable,
            orchestrator_script_path=orchestrator_script_path,
            trade_date=trade_date,
            top_n=top_n,
            run_id=run_id,
            wt_python_executable=wt_python_executable,
            wt_script_path=wt_script_path,
            wt_workdir=wt_workdir,
            fg_python_executable=fg_python_executable,
            fg_script_path=fg_script_path,
            fg_workdir=fg_workdir,
            output_dir=output_dir,
        )
    summary = {
        "generated_at": _iso_now(),
        "trade_date": trade_date,
        "output_dir": str(output_dir),
        "checks": checks,
        "smoke": smoke,
    }
    summary["overall_status"] = evaluate_overall_status(summary)
    return summary
