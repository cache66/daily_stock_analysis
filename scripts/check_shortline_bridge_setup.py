#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect and smoke-check external shortline bridge setup."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shortline_hub.bridge_check import (  # noqa: E402
    build_bridge_check_summary,
    write_bridge_check_artifacts,
)

DEFAULT_WT_ROOT = Path("D:/bb/WonderTrader")
DEFAULT_FG_ROOT = Path("D:/bb/FinGenius")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and smoke-check shortline bridge setup.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--run-id", default="shortline_bridge_check")
    parser.add_argument("--wt-python-executable", default=sys.executable)
    parser.add_argument(
        "--wt-script-path",
        default=str(DEFAULT_WT_ROOT / "bridge" / "wt_export_candidates.py"),
    )
    parser.add_argument("--wt-workdir", default=str(DEFAULT_WT_ROOT))
    parser.add_argument("--fg-python-executable", default=sys.executable)
    parser.add_argument(
        "--fg-script-path",
        default=str(DEFAULT_FG_ROOT / "bridge" / "fg_explain_candidate.py"),
    )
    parser.add_argument("--fg-workdir", default=str(DEFAULT_FG_ROOT))
    parser.add_argument("--orchestrator-python-executable", default=sys.executable)
    parser.add_argument(
        "--orchestrator-script-path",
        default=str(PROJECT_ROOT / "scripts" / "run_shortline_hub.py"),
    )
    parser.add_argument("--run-script-smoke", action="store_true")
    parser.add_argument("--run-orchestrator-smoke", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _resolve_output_dir(raw_output_dir: str, trade_date: str) -> Path:
    if str(raw_output_dir).strip():
        return Path(str(raw_output_dir))
    suffix = trade_date.replace("-", "")
    return PROJECT_ROOT / "data" / "manual_runs" / f"shortline_bridge_check_{suffix}"


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    output_dir = _resolve_output_dir(str(getattr(args, "output_dir", "")), str(args.trade_date))
    summary = build_bridge_check_summary(
        trade_date=str(args.trade_date),
        wt_python_executable=str(args.wt_python_executable),
        wt_script_path=Path(str(args.wt_script_path)),
        wt_workdir=Path(str(args.wt_workdir)) if str(args.wt_workdir).strip() else None,
        fg_python_executable=str(args.fg_python_executable),
        fg_script_path=Path(str(args.fg_script_path)),
        fg_workdir=Path(str(args.fg_workdir)) if str(args.fg_workdir).strip() else None,
        output_dir=output_dir,
        top_n=max(0, int(args.top_n)),
        run_id=str(args.run_id),
        run_script_smoke=bool(args.run_script_smoke),
        should_run_orchestrator_smoke=bool(args.run_orchestrator_smoke),
        orchestrator_python_executable=str(args.orchestrator_python_executable),
        orchestrator_script_path=Path(str(args.orchestrator_script_path)),
    )
    paths = write_bridge_check_artifacts(output_dir, summary)
    logging.info(
        "shortline bridge check finished: overall_status=%s output_dir=%s",
        summary["overall_status"],
        output_dir,
    )
    logging.debug("artifacts=%s", paths)
    return 0 if summary["overall_status"] in {"passed", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
