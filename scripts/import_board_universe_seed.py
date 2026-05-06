#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import a maintained local board universe seed into the project-owned path."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_cycle_scan_seed"
LIST_COLUMNS = ("logic_keywords", "leader_candidates", "belong_boards")
REQUIRED_COLUMNS = ("board_name", "code", "name")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a maintained local board universe CSV into the official project seed path."
    )
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-label", default="manual_import")
    parser.add_argument("--expire-after-days", type=int, default=3)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def get_output_paths(output_dir: Path) -> Dict[str, Path]:
    return {
        "csv": output_dir / "board_universe.csv",
        "meta": output_dir / "board_universe_meta.json",
        "summary": output_dir / "run_summary.txt",
    }


def _normalize_list_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized: list[str] = []
    for item in re.split(r"[;,|/]+|,", text):
        cleaned = item.strip().strip("[](){}").strip("'\"").strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return ";".join(normalized)


def _write_meta(meta_path: Path, payload: Dict[str, Any]) -> None:
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_summary(summary_path: Path, payload: Dict[str, Any]) -> None:
    lines = [f"{key}={value}" for key, value in payload.items()]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def import_board_universe_seed(
    *,
    input_file: Path,
    output_dir: Path,
    source_label: str,
    expire_after_days: int,
) -> Dict[str, Any]:
    if not input_file.exists():
        raise FileNotFoundError(f"input file not found: {input_file}")

    df = pd.read_csv(input_file, dtype=str).fillna("")
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError("board universe seed must contain board_name/code/name columns")

    normalized = df.copy()
    if "board_type" not in normalized.columns:
        normalized["board_type"] = ""
    for column in REQUIRED_COLUMNS + ("board_type",):
        normalized[column] = normalized[column].astype(str).str.strip()
    for column in LIST_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
        normalized[column] = normalized[column].apply(_normalize_list_text)

    normalized = normalized[
        (normalized["board_name"] != "")
        & (normalized["code"] != "")
        & (normalized["name"] != "")
    ].copy()
    normalized = normalized.drop_duplicates(subset=["board_name", "code"], keep="first")
    if normalized.empty:
        raise ValueError("board universe seed is empty after normalization")

    priority_columns = ["board_name", "board_type", "code", "name", *LIST_COLUMNS]
    extra_columns = [column for column in normalized.columns if column not in priority_columns]
    normalized = normalized[priority_columns + extra_columns]

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = get_output_paths(output_dir)
    normalized.to_csv(paths["csv"], index=False, encoding="utf-8-sig")

    imported_at = datetime.now(timezone.utc).isoformat()
    meta = {
        "source_label": str(source_label or "").strip() or "manual_import",
        "source_file": str(input_file.resolve()),
        "imported_at": imported_at,
        "expire_after_days": max(1, int(expire_after_days)),
        "row_count": int(len(normalized)),
        "board_count": int(normalized["board_name"].nunique()),
    }
    _write_meta(paths["meta"], meta)
    _write_summary(
        paths["summary"],
        {
            "status": "imported",
            "source_label": meta["source_label"],
            "source_file": meta["source_file"],
            "imported_at": imported_at,
            "expire_after_days": meta["expire_after_days"],
            "row_count": meta["row_count"],
            "board_count": meta["board_count"],
        },
    )
    return {
        "status": "imported",
        "row_count": meta["row_count"],
        "board_count": meta["board_count"],
        "paths": paths,
    }


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        result = import_board_universe_seed(
            input_file=Path(args.input_file),
            output_dir=Path(args.output_dir),
            source_label=args.source_label,
            expire_after_days=args.expire_after_days,
        )
    except Exception as exc:
        logging.error("Failed to import board universe seed: %s", exc)
        return 1

    logging.info(
        "Board universe seed imported: row_count=%s board_count=%s",
        result.get("row_count"),
        result.get("board_count"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
