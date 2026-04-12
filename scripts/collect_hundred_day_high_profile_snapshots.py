#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect daily hundred-day-high snapshots for multiple profiles."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_hundred_day_high_candidates import DEFAULT_PROFILE_NAME, PROFILE_PRESETS


DEFAULT_SIGNAL_TYPE_PREFIX = "hundred_day_high_profile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run hundred-day-high snapshot collection for multiple profiles.",
    )
    parser.add_argument(
        "--profiles",
        default=",".join(sorted(PROFILE_PRESETS.keys())),
        help="Comma-separated profiles to collect. Defaults to all built-in profiles.",
    )
    parser.add_argument(
        "--signal-type-prefix",
        default=DEFAULT_SIGNAL_TYPE_PREFIX,
        help=f"Prefix used to derive per-profile signal_type values. Default {DEFAULT_SIGNAL_TYPE_PREFIX}.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Optional snapshot date passed through to the selector.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional selector limit per profile.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Selector max-workers per profile.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "profile_snapshots"),
        help="Base output directory. Each profile gets its own child folder.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=str(PROJECT_ROOT / "data" / "profile_snapshots"),
        help="Base checkpoint directory.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=20,
        help="Checkpoint cadence per profile run.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Selector log level.",
    )
    parser.add_argument(
        "--with-cause-analysis",
        action="store_true",
        help="Enable cause analysis. By default the collector only stores fast snapshots.",
    )
    return parser.parse_args()


def parse_profiles(value: str) -> List[str]:
    profiles = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if not profiles:
        raise ValueError("at least one profile is required")
    unknown = [item for item in profiles if item not in PROFILE_PRESETS]
    if unknown:
        raise ValueError(f"unknown profiles: {', '.join(unknown)}")
    return profiles


def build_profile_signal_type(prefix: str, profile_name: str) -> str:
    cleaned_prefix = str(prefix or DEFAULT_SIGNAL_TYPE_PREFIX).strip() or DEFAULT_SIGNAL_TYPE_PREFIX
    return f"{cleaned_prefix}__{profile_name}"


def main() -> int:
    args = parse_args()
    profiles = parse_profiles(args.profiles)
    selector_script = PROJECT_ROOT / "scripts" / "select_hundred_day_high_candidates.py"
    output_root = Path(args.output_dir)
    checkpoint_root = Path(args.checkpoint_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    checkpoint_root.mkdir(parents=True, exist_ok=True)

    for profile in profiles:
        signal_type = build_profile_signal_type(args.signal_type_prefix, profile)
        command = [
            sys.executable,
            str(selector_script),
            "--profile",
            profile,
            "--signal-type",
            signal_type,
            "--max-workers",
            str(args.max_workers),
            "--output-dir",
            str(output_root / profile),
            "--checkpoint-path",
            str(checkpoint_root / f"{signal_type}_checkpoint.json"),
            "--checkpoint-every",
            str(args.checkpoint_every),
            "--log-level",
            args.log_level,
        ]
        if args.snapshot_date:
            command.extend(["--snapshot-date", args.snapshot_date])
        if args.limit is not None:
            command.extend(["--limit", str(args.limit)])
        if not args.with_cause_analysis:
            command.append("--skip-cause-analysis")

        print(f"[collector] profile={profile} signal_type={signal_type}")
        subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
