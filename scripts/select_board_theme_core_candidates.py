#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan one board's constituents and surface theme-core stocks inside that board."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider import DataFetcherManager
from scripts.select_theme_core_candidates import (
    scan_theme_core_candidates,
    write_outputs as write_theme_core_outputs,
)


logger = logging.getLogger("board_theme_core_selector")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_theme_core_candidates"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def fetch_board_universe(
    *,
    board_name: str,
    board_type: str = "auto",
    manager: Optional[DataFetcherManager] = None,
) -> pd.DataFrame:
    manager = manager or DataFetcherManager()
    df = manager.get_board_constituents(board_name, board_type=board_type)
    if df is None or df.empty:
        raise RuntimeError(f"board constituents empty: {board_name} ({board_type})")
    return df


def scan_board_theme_core_candidates(
    *,
    board_name: str,
    board_type: str = "auto",
    commodity_hint: Optional[str] = None,
    limit: Optional[int] = None,
    max_workers: int = 1,
    top_per_subtheme: int = 1,
    minimum_subtheme_core_probability: str = "medium",
    manager: Optional[DataFetcherManager] = None,
    mapper_service: Optional[Any] = None,
) -> tuple[pd.DataFrame, list[Any], int]:
    board_df = fetch_board_universe(board_name=board_name, board_type=board_type, manager=manager)
    if limit is not None and limit > 0:
        board_df = board_df.head(limit).reset_index(drop=True)

    def _provider() -> pd.DataFrame:
        return board_df[["code", "name"]].copy()

    candidates, universe_size = scan_theme_core_candidates(
        commodity_hint=commodity_hint or "",
        limit=None,
        max_workers=max_workers,
        top_per_subtheme=top_per_subtheme,
        minimum_subtheme_core_probability=minimum_subtheme_core_probability,
        universe_provider=_provider,
        mapper_service=mapper_service,
    )
    return board_df, candidates, universe_size


def write_board_outputs(
    *,
    board_name: str,
    board_type: str,
    commodity_hint: Optional[str],
    board_df: pd.DataFrame,
    candidates: list[Any],
    universe_size: int,
    output_dir: Path,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_board_name = board_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    universe_csv = output_dir / f"{normalized_board_name}_constituents.csv"
    board_df.to_csv(universe_csv, index=False, encoding="utf-8-sig")

    core_paths = write_theme_core_outputs(
        commodity_hint=commodity_hint or normalized_board_name,
        candidates=candidates,
        universe_size=universe_size,
        output_dir=output_dir,
    )
    readme_path = output_dir / f"{normalized_board_name}_summary.txt"
    readme_lines = [
        f"Board Name: {board_name}",
        f"Board Type: {board_type}",
        f"Commodity Hint: {commodity_hint or '--'}",
        f"Universe Size: {universe_size}",
        f"Selected: {len(candidates)}",
        f"Constituents CSV: {universe_csv.name}",
        f"Theme Core CSV: {core_paths['csv'].name}",
    ]
    readme_path.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    return {
        "constituents_csv": universe_csv,
        "summary_txt": readme_path,
        **core_paths,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze one board's constituents and surface subtheme core stocks.")
    parser.add_argument("--board-name", required=True, help="Board/module name, e.g. CPO or 光通信.")
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
    parser.add_argument("--limit", type=int, default=None, help="Optional limit inside the board universe.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument("--top-per-subtheme", type=int, default=1, help="Keep top N stocks per subtheme.")
    parser.add_argument(
        "--minimum-subtheme-core-probability",
        choices=["low", "medium", "high"],
        default="medium",
        help="Minimum subtheme-core probability to keep.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    board_df, candidates, universe_size = scan_board_theme_core_candidates(
        board_name=args.board_name,
        board_type=args.board_type,
        commodity_hint=args.commodity_hint,
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        top_per_subtheme=max(1, int(args.top_per_subtheme)),
        minimum_subtheme_core_probability=args.minimum_subtheme_core_probability,
    )
    paths = write_board_outputs(
        board_name=args.board_name,
        board_type=args.board_type,
        commodity_hint=args.commodity_hint,
        board_df=board_df,
        candidates=candidates,
        universe_size=universe_size,
        output_dir=Path(args.output_dir),
    )
    logger.info(
        "Board theme-core scan complete: board=%s(%s) universe=%s selected=%s csv=%s",
        args.board_name,
        args.board_type,
        universe_size,
        len(candidates),
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
