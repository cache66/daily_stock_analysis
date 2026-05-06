#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate local board universe template CSV for board_cycle_scan."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_board_cycle_candidates import (  # noqa: E402
    _ensure_records,
    _normalize_universe_records,
    parse_board_names,
)
from src.services.board_cycle_scan_service import BoardCycleScanService  # noqa: E402

DEFAULT_OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "templates" / "board_cycle_scan" / "board_universe_template.csv"
)
TEMPLATE_COLUMNS = [
    "board_name",
    "board_type",
    "code",
    "name",
    "logic_keywords",
    "leader_candidates",
    "belong_boards",
    "revenue_yoy",
    "net_profit_yoy",
    "earnings_report_date",
    "earnings_quality_verdict",
    "earnings_quality_score_total",
    "earnings_cycle_phase",
    "template_status",
    "template_note",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate board universe template CSV for board_cycle_scan."
    )
    parser.add_argument(
        "--boards",
        required=True,
        help="Comma-separated board names, e.g. 锂矿,猪肉",
    )
    parser.add_argument(
        "--board-type",
        default="auto",
        choices=["auto", "concept", "industry"],
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_FILE),
        help="Destination CSV file for the generated template.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _build_success_rows(
    *,
    board_name: str,
    board_type: str,
    records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in _normalize_universe_records(records, board_name=board_name, board_type=board_type):
        rows.append(
            {
                "board_name": item["board_name"],
                "board_type": item["board_type"],
                "code": item["code"],
                "name": item["name"],
                "logic_keywords": "",
                "leader_candidates": "",
                "belong_boards": "",
                "revenue_yoy": "",
                "net_profit_yoy": "",
                "earnings_report_date": "",
                "earnings_quality_verdict": "",
                "earnings_quality_score_total": "",
                "earnings_cycle_phase": "",
                "template_status": "remote_fetched",
                "template_note": "",
            }
        )
    return rows


def _build_placeholder_row(
    *,
    board_name: str,
    board_type: str,
    reason: str,
) -> Dict[str, str]:
    return {
        "board_name": board_name,
        "board_type": board_type,
        "code": "",
        "name": "",
        "logic_keywords": "",
        "leader_candidates": "",
        "belong_boards": "",
        "revenue_yoy": "",
        "net_profit_yoy": "",
        "earnings_report_date": "",
        "earnings_quality_verdict": "",
        "earnings_quality_score_total": "",
        "earnings_cycle_phase": "",
        "template_status": "needs_manual_fill",
        "template_note": reason,
    }


def collect_template_rows(
    *,
    board_names: Sequence[str],
    board_type: str,
    service: BoardCycleScanService,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for board_name in board_names:
        try:
            board_rows = service.fetch_board_universe(board_name=board_name, board_type=board_type)
            records = _ensure_records(board_rows)
            normalized_rows = _build_success_rows(
                board_name=board_name,
                board_type=board_type,
                records=records,
            )
            if normalized_rows:
                rows.extend(normalized_rows)
                continue
            rows.append(
                _build_placeholder_row(
                    board_name=board_name,
                    board_type=board_type,
                    reason="upstream board constituent fetch returned empty rows; fill manually",
                )
            )
        except Exception as exc:
            rows.append(
                _build_placeholder_row(
                    board_name=board_name,
                    board_type=board_type,
                    reason=f"upstream fetch failed: {exc}",
                )
            )
    return rows


def write_template(rows: Sequence[Dict[str, Any]], output_file: Path) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(list(rows))
    if df.empty:
        df = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    else:
        df = df.reindex(columns=TEMPLATE_COLUMNS, fill_value="")
    df.to_csv(output_file, index=False, encoding="utf-8-sig")
    return output_file


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        board_names = parse_board_names(args.boards)
        service = BoardCycleScanService()
        rows = collect_template_rows(
            board_names=board_names,
            board_type=args.board_type,
            service=service,
        )
        output_file = write_template(rows, Path(args.output_file))
        logger.info(
            "board universe template generated: boards=%s, rows=%s, output=%s",
            len(board_names),
            len(rows),
            output_file,
        )
        return 0
    except Exception as exc:
        logger.exception("generate board universe template failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
