#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge sharded K-line selector outputs back into one result set.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_hundred_day_high_candidates import export_results as export_hundred_day_results
from scripts.select_kline_candidates import export_results as export_combo_results
from src.services.kline_selector_service import (
    KlineSelectionEvaluation,
    KlineSelectorCriteria,
    KlineSelectorRunResult,
)


logger = logging.getLogger("merge_kline_shards")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并多个 K 线分片输出目录。")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["combo", "hundred-day-high"],
        help="要合并哪一类策略结果。",
    )
    parser.add_argument(
        "--input-dirs",
        nargs="+",
        required=True,
        help="多个分片输出目录路径。",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="合并后的输出目录。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def checkpoint_filename(mode: str) -> str:
    if mode == "combo":
        return "kline_selector_checkpoint.json"
    return "hundred_day_high_checkpoint.json"


def export_function(mode: str) -> Callable[[KlineSelectorRunResult, Path], None]:
    if mode == "combo":
        return export_combo_results
    return export_hundred_day_results


def dedupe_codes(codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def dedupe_evaluations(items: Iterable[KlineSelectionEvaluation]) -> list[KlineSelectionEvaluation]:
    seen: set[str] = set()
    result: list[KlineSelectionEvaluation] = []
    for item in items:
        if not item.stock_code or item.stock_code in seen:
            continue
        seen.add(item.stock_code)
        result.append(item)
    return result


def load_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    checkpoint_name = checkpoint_filename(args.mode)
    exporter = export_function(args.mode)

    criteria_dict: dict | None = None
    merged_universe_codes: list[str] = []
    merged_selected: list[KlineSelectionEvaluation] = []
    merged_failed: list[KlineSelectionEvaluation] = []
    skipped_market_cap_count = 0
    skipped_prefilter_count = 0

    for raw_dir in args.input_dirs:
        shard_dir = Path(raw_dir)
        checkpoint_path = shard_dir / checkpoint_name
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"checkpoint not found in shard output: {checkpoint_path}")

        payload = load_checkpoint(checkpoint_path)
        if criteria_dict is None:
            criteria_dict = payload.get("criteria") or {}
        elif payload.get("criteria") != criteria_dict:
            raise ValueError(f"criteria mismatch across shard checkpoints: {checkpoint_path}")

        merged_universe_codes.extend(payload.get("universe_codes") or [])
        merged_selected.extend(
            KlineSelectionEvaluation.from_checkpoint_record(item)
            for item in (payload.get("selected") or [])
        )
        merged_failed.extend(
            KlineSelectionEvaluation.from_checkpoint_record(item)
            for item in (payload.get("failed") or [])
        )
        skipped_market_cap_count += int(payload.get("skipped_market_cap_count") or 0)
        skipped_prefilter_count += int(payload.get("skipped_prefilter_count") or 0)

        logger.info(
            "loaded shard checkpoint: dir=%s, universe=%s, selected=%s, failed=%s",
            shard_dir,
            len(payload.get("universe_codes") or []),
            len(payload.get("selected") or []),
            len(payload.get("failed") or []),
        )

    if criteria_dict is None:
        raise RuntimeError("no shard checkpoints were loaded")

    merged_universe_codes = dedupe_codes(merged_universe_codes)
    merged_selected = dedupe_evaluations(merged_selected)
    merged_failed = dedupe_evaluations(merged_failed)

    run_result = KlineSelectorRunResult(
        criteria=KlineSelectorCriteria(**criteria_dict),
        universe_size=len(merged_universe_codes),
        evaluated_count=len(merged_selected) + len(merged_failed),
        skipped_market_cap_count=skipped_market_cap_count,
        skipped_prefilter_count=skipped_prefilter_count,
        universe_codes=merged_universe_codes,
        selected=merged_selected,
        failed=merged_failed,
    )
    output_dir = Path(args.output_dir)
    exporter(run_result, output_dir)

    logger.info(
        "merged shard results written: mode=%s, shards=%s, universe=%s, evaluated=%s, selected=%s, output_dir=%s",
        args.mode,
        len(args.input_dirs),
        run_result.universe_size,
        run_result.evaluated_count,
        len(run_result.selected),
        output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
