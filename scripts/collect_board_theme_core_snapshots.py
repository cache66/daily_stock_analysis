#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect dated board constituent snapshots and board-scoped theme core outputs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_board_theme_core_candidates import (
    scan_board_theme_core_candidates,
    write_board_outputs,
)


logger = logging.getLogger("board_theme_core_snapshot_collector")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_theme_core_snapshots"


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


def build_snapshot_dir(*, root: Path, snapshot_date: date, board_name: str) -> Path:
    safe_name = str(board_name or "").strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
    return root / snapshot_date.isoformat() / safe_name


def write_snapshot_manifest(
    *,
    board_name: str,
    board_type: str,
    commodity_hint: Optional[str],
    snapshot_date: date,
    universe_size: int,
    selected_count: int,
    output_dir: Path,
) -> Path:
    manifest = {
        "board_name": board_name,
        "board_type": board_type,
        "commodity_hint": commodity_hint,
        "snapshot_date": snapshot_date.isoformat(),
        "universe_size": universe_size,
        "selected_count": selected_count,
    }
    path = output_dir / "snapshot_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect dated board snapshots and theme-core outputs.")
    parser.add_argument("--board-name", required=True, help="Board/module name, e.g. CPO or 通信设备.")
    parser.add_argument(
        "--board-type",
        default="auto",
        choices=["auto", "concept", "industry"],
        help="Board type. Default auto.",
    )
    parser.add_argument(
        "--commodity-hint",
        default=None,
        help="Optional theme hint such as optical_fiber, memory, or hard_disk.",
    )
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date in YYYY-MM-DD format. Default today.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit inside the board universe.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument("--top-per-subtheme", type=int, default=1, help="Keep top N stocks per subtheme.")
    parser.add_argument(
        "--minimum-subtheme-core-probability",
        choices=["low", "medium", "high"],
        default="medium",
        help="Minimum subtheme-core probability to keep.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Snapshot root directory.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    board_df, candidates, universe_size = scan_board_theme_core_candidates(
        board_name=args.board_name,
        board_type=args.board_type,
        commodity_hint=args.commodity_hint,
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        top_per_subtheme=max(1, int(args.top_per_subtheme)),
        minimum_subtheme_core_probability=args.minimum_subtheme_core_probability,
    )
    snapshot_dir = build_snapshot_dir(
        root=Path(args.output_dir),
        snapshot_date=snapshot_date,
        board_name=args.board_name,
    )
    paths = write_board_outputs(
        board_name=args.board_name,
        board_type=args.board_type,
        commodity_hint=args.commodity_hint,
        board_df=board_df,
        candidates=candidates,
        universe_size=universe_size,
        output_dir=snapshot_dir,
    )
    manifest_path = write_snapshot_manifest(
        board_name=args.board_name,
        board_type=args.board_type,
        commodity_hint=args.commodity_hint,
        snapshot_date=snapshot_date,
        universe_size=universe_size,
        selected_count=len(candidates),
        output_dir=snapshot_dir,
    )
    logger.info(
        "Board snapshot collected: board=%s(%s) date=%s universe=%s selected=%s manifest=%s csv=%s",
        args.board_name,
        args.board_type,
        snapshot_date.isoformat(),
        universe_size,
        len(candidates),
        manifest_path,
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
