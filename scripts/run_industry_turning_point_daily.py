#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run daily industry turning-point collection as independent commands."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


logger = logging.getLogger("industry_turning_point_daily")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "industry_turning_point_daily"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> date:
    text = str(value or "").strip()
    if not text:
        return date.today()
    return date.fromisoformat(text)


def normalize_board_targets(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        payload = value
    else:
        text = str(value or "").strip()
        if not text:
            payload = []
        else:
            try:
                payload = json.loads(text)
            except Exception as exc:
                raise ValueError(f"invalid --board-theme-targets-json: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("board theme targets must be a JSON array")
    normalized: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        board_name = str(item.get("board_name") or "").strip()
        if not board_name:
            continue
        normalized.append(
            {
                "board_name": board_name,
                "board_type": str(item.get("board_type") or "auto").strip() or "auto",
                "commodity_hint": str(item.get("commodity_hint") or "").strip() or None,
            }
        )
    return normalized


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily independent runner for board recognizability + board theme-core snapshots.",
    )
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date in YYYY-MM-DD, default today.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Output root. Default {DEFAULT_OUTPUT_DIR}.")

    parser.add_argument("--source-signal-type", default="hundred_day_high", help="Source signal type for recognizability.")
    parser.add_argument("--recognizability-top-n", type=int, default=3, help="Top-N each board for recognizability.")
    parser.add_argument(
        "--recognizability-signal-type-prefix",
        default="board_recognizability",
        help="Persisted signal type prefix for recognizability snapshots.",
    )
    parser.add_argument("--history-lookback-days", type=int, default=365, help="History lookback for recognizability.")
    parser.add_argument("--recognizability-skip-db-persist", action="store_true", help="Skip DB persistence for recognizability.")

    parser.add_argument(
        "--board-theme-targets-json",
        default="[]",
        help='Board theme targets JSON list, e.g. [{"board_name":"CPO","board_type":"concept"}].',
    )
    parser.add_argument("--board-theme-limit", type=int, default=None, help="Optional limit for each board theme run.")
    parser.add_argument("--board-theme-max-workers", type=int, default=1, help="Worker count for board theme scan.")
    parser.add_argument("--board-theme-top-per-subtheme", type=int, default=1, help="Top N each subtheme.")
    parser.add_argument(
        "--board-theme-minimum-subtheme-core-probability",
        choices=["low", "medium", "high"],
        default="medium",
        help="Minimum subtheme-core probability.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def _run_command(command: Sequence[str]) -> None:
    process = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            "command failed:\n"
            + " ".join(command)
            + "\n\noutput:\n"
            + "".join(output_lines)
        )


def build_recognizability_command(
    *,
    args: argparse.Namespace,
    snapshot_date: date,
    output_dir: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "collect_board_recognizability_rankings.py"),
        "--source-signal-type",
        str(args.source_signal_type),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--top-n",
        str(max(1, int(args.recognizability_top_n))),
        "--signal-type-prefix",
        str(args.recognizability_signal_type_prefix),
        "--history-lookback-days",
        str(max(1, int(args.history_lookback_days))),
        "--output-dir",
        str(output_dir),
        "--log-level",
        str(args.log_level),
    ]
    if bool(args.recognizability_skip_db_persist):
        command.append("--skip-db-persist")
    return command


def build_board_theme_command(
    *,
    args: argparse.Namespace,
    target: Dict[str, Any],
    snapshot_date: date,
    output_dir: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "collect_board_theme_core_snapshots.py"),
        "--board-name",
        str(target.get("board_name")),
        "--board-type",
        str(target.get("board_type") or "auto"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--max-workers",
        str(max(1, int(args.board_theme_max_workers))),
        "--top-per-subtheme",
        str(max(1, int(args.board_theme_top_per_subtheme))),
        "--minimum-subtheme-core-probability",
        str(args.board_theme_minimum_subtheme_core_probability),
        "--output-dir",
        str(output_dir),
        "--log-level",
        str(args.log_level),
    ]
    commodity_hint = target.get("commodity_hint")
    if commodity_hint:
        command.extend(["--commodity-hint", str(commodity_hint)])
    if args.board_theme_limit is not None and int(args.board_theme_limit) > 0:
        command.extend(["--limit", str(int(args.board_theme_limit))])
    return command


def _build_summary_markdown(
    *,
    snapshot_date: date,
    recognizability_command: Sequence[str],
    board_theme_commands: Sequence[Sequence[str]],
) -> str:
    lines: List[str] = []
    lines.append(f"# 行业拐点日更汇总（{snapshot_date.isoformat()}）")
    lines.append("")
    lines.append("## 已执行命令")
    lines.append("")
    lines.append("```bash")
    lines.append(" ".join(recognizability_command))
    for command in board_theme_commands:
        lines.append(" ".join(command))
    lines.append("```")
    lines.append("")
    lines.append(f"- 板块主题任务数：`{len(board_theme_commands)}`")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    board_targets = normalize_board_targets(args.board_theme_targets_json)

    output_root = Path(args.output_dir)
    day_dir = output_root / snapshot_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    recognizability_output = day_dir / "board_recognizability"
    board_theme_output = day_dir / "board_theme_core"
    recognizability_command = build_recognizability_command(
        args=args,
        snapshot_date=snapshot_date,
        output_dir=recognizability_output,
    )
    logger.info("run recognizability collector")
    _run_command(recognizability_command)

    board_theme_commands: List[Sequence[str]] = []
    for target in board_targets:
        command = build_board_theme_command(
            args=args,
            target=target,
            snapshot_date=snapshot_date,
            output_dir=board_theme_output,
        )
        board_theme_commands.append(command)
        logger.info("run board theme collector: board_name=%s", target.get("board_name"))
        _run_command(command)

    summary_md = day_dir / "industry_turning_point_daily_summary.md"
    summary_md.write_text(
        _build_summary_markdown(
            snapshot_date=snapshot_date,
            recognizability_command=recognizability_command,
            board_theme_commands=board_theme_commands,
        ),
        encoding="utf-8",
    )

    print(f"snapshot_date={snapshot_date.isoformat()}")
    print(f"board_theme_targets={len(board_targets)}")
    print(f"summary_md={summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
