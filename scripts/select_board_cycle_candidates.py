#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI for board cycle candidate selection and artifact export."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.board_cycle_scan_service import (  # noqa: E402
    BoardCycleBoardResult,
    BoardCycleScanService,
    BoardStockEvaluation,
)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_cycle_scan"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "board_cycle_scan_cache"
DEFAULT_BOARD_UNIVERSE_SEED_DIR = PROJECT_ROOT / "data" / "board_cycle_scan_seed"
DEFAULT_BOARD_UNIVERSE_SEED_FILE = DEFAULT_BOARD_UNIVERSE_SEED_DIR / "board_universe.csv"
DEFAULT_BOARD_UNIVERSE_SEED_META_FILE = (
    DEFAULT_BOARD_UNIVERSE_SEED_DIR / "board_universe_meta.json"
)
BOARD_UPDATE_SCORE_DELTA_THRESHOLD = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan boards and export board-cycle candidates."
    )
    parser.add_argument(
        "--boards",
        required=True,
        help="Comma-separated board names, e.g. 锂矿,白酒",
    )
    parser.add_argument(
        "--board-type",
        default="auto",
        choices=["auto", "concept", "industry"],
    )
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--top-per-board", type=int, default=3)
    parser.add_argument("--limit-per-board", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument(
        "--board-universe-file",
        default=None,
        help="Optional local CSV file with board_name/code/name rows.",
    )
    parser.add_argument(
        "--board-universe-cache-dir",
        default=None,
        help="Optional local cache directory for board constituent CSV files.",
    )
    parser.add_argument(
        "--use-local-cache",
        action="store_true",
        help="Allow falling back to cached board constituent CSV files when remote fetch is empty.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def parse_board_names(raw_value: str) -> List[str]:
    values = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    if not values:
        raise ValueError("at least one board is required")

    deduped: List[str] = []
    for item in values:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _split_text_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    values: List[str] = []
    for item in re.split(r"[;,|/]+|,", text):
        cleaned = item.strip().strip("[](){}").strip("'\"").strip()
        if cleaned:
            values.append(cleaned)
    return values


def _normalize_universe_records(
    records: Sequence[Mapping[str, Any]],
    *,
    board_name: str,
    board_type: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in records:
        payload = dict(row)
        code = str(payload.get("code") or payload.get("stock_code") or "").strip()
        name = str(payload.get("name") or payload.get("stock_name") or "").strip()
        if not code or not name:
            continue
        normalized.append(
            {
                **payload,
                "code": code,
                "name": name,
                "board_name": str(payload.get("board_name") or board_name).strip(),
                "board_type": str(payload.get("board_type") or board_type).strip(),
                "logic_keywords": _split_text_values(payload.get("logic_keywords")),
                "leader_candidates": _split_text_values(payload.get("leader_candidates")),
                "belong_boards": _split_text_values(payload.get("belong_boards")),
            }
        )
    return normalized


def _normalize_stock_result(item: BoardStockEvaluation) -> Dict[str, Any]:
    payload = asdict(item)
    payload["related_boards"] = ",".join(item.related_boards)
    return payload


def _render_board_summary_markdown(
    board_results: Sequence[BoardCycleBoardResult],
    *,
    note_lines: Optional[Sequence[str]] = None,
) -> str:
    lines = ["# Board Cycle Summary", ""]
    for note in note_lines or []:
        text = str(note or "").strip()
        if text:
            lines.append(text)
    if note_lines:
        lines.append("")
    lines.extend(
        [
            "| board_name | board_type | label | cycle_score | leader_ratio | earnings_ratio | leaders | earnings_supported | reason |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for item in board_results:
        lines.append(
            "| {board_name} | {board_type} | {label} | {score:.2f} | {leader_ratio:.2%} | {earnings_ratio:.2%} | {leaders} | {earnings} | {reason} |".format(
                board_name=item.board_name,
                board_type=item.board_type,
                label=item.board_cycle_label,
                score=item.board_cycle_score,
                leader_ratio=item.leader_ratio,
                earnings_ratio=item.earnings_supported_ratio,
                leaders=", ".join(item.top_leaders) or "-",
                earnings=", ".join(item.top_earnings_supported) or "-",
                reason=str(item.board_reason_summary or "").replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _render_stock_candidates_markdown(stock_results: Sequence[BoardStockEvaluation]) -> str:
    lines = [
        "# Board Stock Candidates",
        "",
        "| board_name | rank | stock_code | stock_name | role | score | primary_board | related_boards | reason |",
        "| --- | ---: | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for item in stock_results:
        lines.append(
            "| {board_name} | {rank} | {stock_code} | {stock_name} | {role} | {score:.2f} | {primary} | {related} | {reason} |".format(
                board_name=item.board_name,
                rank=item.board_rank,
                stock_code=item.stock_code,
                stock_name=item.stock_name,
                role=item.stock_role,
                score=item.board_stock_score,
                primary=item.primary_board or "-",
                related=", ".join(item.related_boards) or "-",
                reason=str(item.selection_reason or "").replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _parse_run_summary_context(file_path: Path) -> Dict[str, str]:
    if not file_path.exists():
        return {}
    context: Dict[str, str] = {}
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        context[str(key).strip()] = str(value).strip()
    return context


def _find_previous_output_dir(
    output_dir: Path,
    *,
    board_names: Sequence[str],
) -> Optional[Path]:
    board_summary_path = output_dir / "board_summary.csv"
    if board_summary_path.exists():
        return output_dir

    parent_dir = output_dir.parent
    if not parent_dir.exists():
        return None

    candidate_dirs: List[Path] = []
    for child in parent_dir.iterdir():
        if child == output_dir or not child.is_dir():
            continue
        if (child / "board_summary.csv").exists():
            candidate_dirs.append(child)

    if not candidate_dirs:
        return None

    current_board_names = {str(item or "").strip() for item in board_names if str(item or "").strip()}
    if current_board_names:
        filtered_candidate_dirs: List[Path] = []
        for candidate_dir in candidate_dirs:
            candidate_rows = _read_board_summary_rows(candidate_dir / "board_summary.csv")
            candidate_board_names = {
                str(row.get("board_name") or "").strip() for row in candidate_rows if row
            }
            if current_board_names & candidate_board_names:
                filtered_candidate_dirs.append(candidate_dir)
        candidate_dirs = filtered_candidate_dirs

    if not candidate_dirs:
        return None

    candidate_dirs.sort(
        key=lambda item: ((item / "board_summary.csv").stat().st_mtime, item.name),
    )
    return candidate_dirs[-1]


def _read_board_summary_rows(file_path: Path) -> List[Dict[str, Any]]:
    if not file_path.exists():
        return []
    return pd.read_csv(file_path).fillna("").to_dict(orient="records")


def _board_result_to_tracking_row(
    item: BoardCycleBoardResult,
    *,
    run_id: str,
    snapshot_date: str,
    generated_at: str,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "snapshot_date": snapshot_date,
        "generated_at": generated_at,
        "board_name": item.board_name,
        "board_type": item.board_type,
        "board_cycle_label": item.board_cycle_label,
        "board_cycle_score": round(float(item.board_cycle_score or 0.0), 2),
        "leader_count": int(item.leader_count or 0),
        "leader_ratio": round(float(item.leader_ratio or 0.0), 4),
        "earnings_supported_count": int(item.earnings_supported_count or 0),
        "earnings_supported_ratio": round(float(item.earnings_supported_ratio or 0.0), 4),
        "top_leaders": ", ".join(item.top_leaders or []),
        "top_earnings_supported": ", ".join(item.top_earnings_supported or []),
        "board_reason_summary": str(item.board_reason_summary or ""),
    }


def _summary_row_to_tracking_row(
    row: Mapping[str, Any],
    *,
    run_id: str,
    snapshot_date: str,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "snapshot_date": snapshot_date,
        "generated_at": "",
        "board_name": str(row.get("board_name") or ""),
        "board_type": str(row.get("board_type") or ""),
        "board_cycle_label": str(row.get("board_cycle_label") or ""),
        "board_cycle_score": round(float(row.get("board_cycle_score") or 0.0), 2),
        "leader_count": int(row.get("leader_count") or 0),
        "leader_ratio": round(float(row.get("leader_ratio") or 0.0), 4),
        "earnings_supported_count": int(row.get("earnings_supported_count") or 0),
        "earnings_supported_ratio": round(float(row.get("earnings_supported_ratio") or 0.0), 4),
        "top_leaders": ", ".join(_split_text_values(row.get("top_leaders"))),
        "top_earnings_supported": ", ".join(_split_text_values(row.get("top_earnings_supported"))),
        "board_reason_summary": str(row.get("board_reason_summary") or ""),
    }


def _load_previous_history_rows(previous_output_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if previous_output_dir is None:
        return []

    history_path = previous_output_dir / "board_tracking_history.csv"
    if history_path.exists():
        return pd.read_csv(history_path).fillna("").to_dict(orient="records")

    board_summary_path = previous_output_dir / "board_summary.csv"
    if not board_summary_path.exists():
        return []

    previous_context = _parse_run_summary_context(previous_output_dir / "run_summary.txt")
    snapshot_date = str(previous_context.get("snapshot_date") or "")
    run_id = previous_output_dir.name or str(previous_output_dir)
    return [
        _summary_row_to_tracking_row(row, run_id=run_id, snapshot_date=snapshot_date)
        for row in _read_board_summary_rows(board_summary_path)
    ]


def _build_previous_board_map(previous_output_dir: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if previous_output_dir is None:
        return {}
    rows = _read_board_summary_rows(previous_output_dir / "board_summary.csv")
    board_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        board_name = str(row.get("board_name") or "").strip()
        if board_name:
            board_map[board_name] = row
    return board_map


def _build_board_change_rows(
    board_results: Sequence[BoardCycleBoardResult],
    *,
    previous_output_dir: Optional[Path],
    current_run_id: str,
) -> List[Dict[str, Any]]:
    previous_board_map = _build_previous_board_map(previous_output_dir)
    previous_run_id = previous_output_dir.name if previous_output_dir is not None else ""

    change_rows: List[Dict[str, Any]] = []
    for item in board_results:
        previous_row = previous_board_map.get(str(item.board_name or ""))
        current_top_leaders = list(item.top_leaders or [])
        current_top_earnings_supported = list(item.top_earnings_supported or [])
        if previous_row is None:
            change_rows.append(
                {
                    "board_name": item.board_name,
                    "board_type": item.board_type,
                    "previous_run_id": previous_run_id,
                    "current_run_id": current_run_id,
                    "is_first_run": True,
                    "board_updated": False,
                    "previous_board_cycle_label": "",
                    "current_board_cycle_label": item.board_cycle_label,
                    "previous_board_cycle_score": "",
                    "current_board_cycle_score": round(float(item.board_cycle_score or 0.0), 2),
                    "board_cycle_score_delta": "",
                    "score_change_exceeds_threshold": False,
                    "previous_leader_count": "",
                    "current_leader_count": int(item.leader_count or 0),
                    "previous_earnings_supported_count": "",
                    "current_earnings_supported_count": int(item.earnings_supported_count or 0),
                    "previous_top_leaders": "",
                    "current_top_leaders": ", ".join(current_top_leaders),
                    "top_leaders_changed": False,
                    "previous_top_earnings_supported": "",
                    "current_top_earnings_supported": ", ".join(current_top_earnings_supported),
                    "update_reasons": "first_run",
                }
            )
            continue

        previous_top_leaders = _split_text_values(previous_row.get("top_leaders"))
        previous_top_earnings_supported = _split_text_values(
            previous_row.get("top_earnings_supported")
        )
        previous_label = str(previous_row.get("board_cycle_label") or "")
        previous_score = round(float(previous_row.get("board_cycle_score") or 0.0), 2)
        current_score = round(float(item.board_cycle_score or 0.0), 2)
        score_delta = round(current_score - previous_score, 2)
        previous_earnings_count = int(previous_row.get("earnings_supported_count") or 0)
        current_earnings_count = int(item.earnings_supported_count or 0)
        label_changed = previous_label != item.board_cycle_label
        score_changed = abs(score_delta) >= BOARD_UPDATE_SCORE_DELTA_THRESHOLD
        leaders_changed = previous_top_leaders != current_top_leaders
        earnings_count_changed = previous_earnings_count != current_earnings_count

        update_reasons: List[str] = []
        if label_changed:
            update_reasons.append("label_changed")
        if score_changed:
            update_reasons.append("score_changed")
        if leaders_changed:
            update_reasons.append("leaders_changed")
        if earnings_count_changed:
            update_reasons.append("earnings_count_changed")

        change_rows.append(
            {
                "board_name": item.board_name,
                "board_type": item.board_type,
                "previous_run_id": previous_run_id,
                "current_run_id": current_run_id,
                "is_first_run": False,
                "board_updated": bool(update_reasons),
                "previous_board_cycle_label": previous_label,
                "current_board_cycle_label": item.board_cycle_label,
                "previous_board_cycle_score": previous_score,
                "current_board_cycle_score": current_score,
                "board_cycle_score_delta": score_delta,
                "score_change_exceeds_threshold": score_changed,
                "previous_leader_count": int(previous_row.get("leader_count") or 0),
                "current_leader_count": int(item.leader_count or 0),
                "previous_earnings_supported_count": previous_earnings_count,
                "current_earnings_supported_count": current_earnings_count,
                "previous_top_leaders": ", ".join(previous_top_leaders),
                "current_top_leaders": ", ".join(current_top_leaders),
                "top_leaders_changed": leaders_changed,
                "previous_top_earnings_supported": ", ".join(previous_top_earnings_supported),
                "current_top_earnings_supported": ", ".join(current_top_earnings_supported),
                "update_reasons": ",".join(update_reasons) if update_reasons else "stable",
            }
        )

    return change_rows


def _render_board_change_summary_markdown(change_rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Board Change Summary",
        "",
        "| board_name | first_run | updated | label | score_delta | leaders_changed | reasons |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in change_rows:
        if bool(row.get("is_first_run")):
            label_text = str(row.get("current_board_cycle_label") or "")
        else:
            label_text = "{prev} -> {curr}".format(
                prev=str(row.get("previous_board_cycle_label") or "-"),
                curr=str(row.get("current_board_cycle_label") or "-"),
            )
        score_delta = row.get("board_cycle_score_delta")
        score_delta_text = "-" if score_delta in ("", None) else f"{float(score_delta):.2f}"
        lines.append(
            "| {board_name} | {first_run} | {updated} | {label} | {score_delta} | {leaders_changed} | {reasons} |".format(
                board_name=str(row.get("board_name") or ""),
                first_run=str(bool(row.get("is_first_run"))).lower(),
                updated=str(bool(row.get("board_updated"))).lower(),
                label=label_text.replace("|", "/"),
                score_delta=score_delta_text,
                leaders_changed=str(bool(row.get("top_leaders_changed"))).lower(),
                reasons=str(row.get("update_reasons") or "").replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    *,
    board_results: Sequence[BoardCycleBoardResult],
    stock_results: Sequence[BoardStockEvaluation],
    output_dir: Path,
    run_context: Optional[Mapping[str, Any]] = None,
    warning_messages: Optional[Sequence[str]] = None,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    board_summary_csv = output_dir / "board_summary.csv"
    board_summary_md = output_dir / "board_summary.md"
    board_stock_candidates_csv = output_dir / "board_stock_candidates.csv"
    board_stock_candidates_md = output_dir / "board_stock_candidates.md"
    board_change_summary_csv = output_dir / "board_change_summary.csv"
    board_change_summary_md = output_dir / "board_change_summary.md"
    board_tracking_history_csv = output_dir / "board_tracking_history.csv"
    run_summary_txt = output_dir / "run_summary.txt"
    previous_output_dir = _find_previous_output_dir(
        output_dir,
        board_names=[item.board_name for item in board_results],
    )
    current_run_id = output_dir.name or str(output_dir)
    generated_at = datetime.now().isoformat(timespec="seconds")
    snapshot_date = str(dict(run_context or {}).get("snapshot_date") or "")

    pd.DataFrame([asdict(item) for item in board_results]).to_csv(
        board_summary_csv,
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([_normalize_stock_result(item) for item in stock_results]).to_csv(
        board_stock_candidates_csv,
        index=False,
        encoding="utf-8-sig",
    )

    board_summary_notes = [
        f"Note: {message}"
        for message in (warning_messages or [])
        if str(message or "").strip()
    ]
    board_summary_md.write_text(
        _render_board_summary_markdown(board_results, note_lines=board_summary_notes),
        encoding="utf-8",
    )
    board_stock_candidates_md.write_text(
        _render_stock_candidates_markdown(stock_results),
        encoding="utf-8",
    )
    change_rows = _build_board_change_rows(
        board_results,
        previous_output_dir=previous_output_dir,
        current_run_id=current_run_id,
    )
    pd.DataFrame(change_rows).to_csv(
        board_change_summary_csv,
        index=False,
        encoding="utf-8-sig",
    )
    board_change_summary_md.write_text(
        _render_board_change_summary_markdown(change_rows),
        encoding="utf-8",
    )
    tracking_history_rows = _load_previous_history_rows(previous_output_dir)
    tracking_history_rows.extend(
        [
            _board_result_to_tracking_row(
                item,
                run_id=current_run_id,
                snapshot_date=snapshot_date,
                generated_at=generated_at,
            )
            for item in board_results
        ]
    )
    pd.DataFrame(tracking_history_rows).to_csv(
        board_tracking_history_csv,
        index=False,
        encoding="utf-8-sig",
    )
    context_lines = []
    for key, value in dict(run_context or {}).items():
        context_lines.append(f"{key}={value}")
    if previous_output_dir is not None:
        context_lines.append(f"previous_run_id={previous_output_dir.name}")
    context_lines.append(
        "updated_boards="
        + str(sum(1 for row in change_rows if bool(row.get("board_updated"))))
    )
    for message in warning_messages or []:
        text = str(message or "").strip()
        if text:
            context_lines.append(f"warning={text}")
    run_summary_txt.write_text(
        "\n".join(
            context_lines
            + [
                f"boards={len(board_results)}",
                f"stock_candidates={len(stock_results)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "board_summary_csv": board_summary_csv,
        "board_summary_md": board_summary_md,
        "board_stock_candidates_csv": board_stock_candidates_csv,
        "board_stock_candidates_md": board_stock_candidates_md,
        "board_change_summary_csv": board_change_summary_csv,
        "board_change_summary_md": board_change_summary_md,
        "board_tracking_history_csv": board_tracking_history_csv,
        "run_summary_txt": run_summary_txt,
    }


def _ensure_records(board_rows: Any) -> List[Dict[str, Any]]:
    if isinstance(board_rows, pd.DataFrame):
        return board_rows.to_dict(orient="records")
    if isinstance(board_rows, Iterable):
        return [dict(item) for item in board_rows]
    return []


def _read_board_universe_file(
    file_path: Path,
    *,
    board_names: Sequence[str],
    board_type: str,
) -> Dict[str, List[Dict[str, Any]]]:
    if not file_path.exists():
        raise FileNotFoundError(f"board universe file not found: {file_path}")
    df = pd.read_csv(file_path, dtype=str).fillna("")
    if "board_name" not in df.columns or "code" not in df.columns or "name" not in df.columns:
        raise ValueError("board universe file must contain board_name/code/name columns")

    board_name_set = set(board_names)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for current_board_name, current_df in df.groupby("board_name", dropna=False):
        board_name_text = str(current_board_name or "").strip()
        if board_name_text not in board_name_set:
            continue
        grouped[board_name_text] = _normalize_universe_records(
            current_df.to_dict(orient="records"),
            board_name=board_name_text,
            board_type=board_type,
        )
    return grouped


def _read_json_file(file_path: Path) -> Dict[str, Any]:
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_board_universe_file(
    raw_value: Any,
) -> tuple[Optional[Path], str, Dict[str, Any]]:
    if raw_value:
        return Path(str(raw_value)), "explicit", {}
    if DEFAULT_BOARD_UNIVERSE_SEED_FILE.exists():
        return (
            DEFAULT_BOARD_UNIVERSE_SEED_FILE,
            "official_seed",
            _read_json_file(DEFAULT_BOARD_UNIVERSE_SEED_META_FILE),
        )
    return None, "none", {}


def _official_seed_is_stale(meta: Mapping[str, Any]) -> bool:
    imported_at = str(meta.get("imported_at") or meta.get("last_updated_at") or "").strip()
    if not imported_at:
        return False
    try:
        imported_dt = datetime.fromisoformat(imported_at)
    except Exception:
        return False
    expire_after_days = max(1, int(meta.get("expire_after_days") or 3))
    return datetime.now(timezone.utc) - imported_dt > timedelta(days=expire_after_days)


def _resolve_cache_dir(args: argparse.Namespace) -> Optional[Path]:
    raw_value = getattr(args, "board_universe_cache_dir", None)
    if raw_value:
        return Path(str(raw_value))
    if bool(getattr(args, "use_local_cache", False)):
        return DEFAULT_CACHE_DIR
    return None


def _board_cache_path(cache_dir: Path, board_name: str) -> Path:
    return cache_dir / f"{board_name}.csv"


def _load_cached_board_universe(
    cache_dir: Optional[Path],
    *,
    board_name: str,
    board_type: str,
) -> List[Dict[str, Any]]:
    if cache_dir is None:
        return []
    cache_path = _board_cache_path(cache_dir, board_name)
    if not cache_path.exists():
        return []
    df = pd.read_csv(cache_path, dtype=str).fillna("")
    return _normalize_universe_records(
        df.to_dict(orient="records"),
        board_name=board_name,
        board_type=board_type,
    )


def _write_cached_board_universe(
    cache_dir: Optional[Path],
    *,
    board_name: str,
    records: Sequence[Mapping[str, Any]],
) -> None:
    if cache_dir is None or not records:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _board_cache_path(cache_dir, board_name)
    serialized_records: List[Dict[str, Any]] = []
    for item in records:
        payload = dict(item)
        for key in ("logic_keywords", "leader_candidates", "belong_boards"):
            value = payload.get(key)
            if isinstance(value, list):
                payload[key] = ";".join(str(part).strip() for part in value if str(part).strip())
        serialized_records.append(payload)
    pd.DataFrame(serialized_records).to_csv(
        cache_path,
        index=False,
        encoding="utf-8-sig",
    )


def _fetch_board_records(
    *,
    board_name: str,
    board_type: str,
    service: BoardCycleScanService,
    file_universes: Mapping[str, Sequence[Mapping[str, Any]]],
    cache_dir: Optional[Path],
    use_local_cache: bool,
    warning_messages: List[str],
) -> tuple[List[Dict[str, Any]], str]:
    if board_name in file_universes:
        records = _normalize_universe_records(
            list(file_universes[board_name]),
            board_name=board_name,
            board_type=board_type,
        )
        if records:
            _write_cached_board_universe(cache_dir, board_name=board_name, records=records)
            return records, "file"

    try:
        remote_rows = service.fetch_board_universe(
            board_name=board_name,
            board_type=board_type,
        )
        records = _normalize_universe_records(
            _ensure_records(remote_rows),
            board_name=board_name,
            board_type=board_type,
        )
    except Exception as exc:
        if not use_local_cache:
            raise
        cached_records = _load_cached_board_universe(
            cache_dir,
            board_name=board_name,
            board_type=board_type,
        )
        if cached_records:
            warning_messages.append(
                f"Using cached board universe for {board_name} because remote fetch failed: {exc}"
            )
            return cached_records, "cache"
        raise

    if records:
        _write_cached_board_universe(cache_dir, board_name=board_name, records=records)
        return records, "remote"

    if use_local_cache:
        cached_records = _load_cached_board_universe(
            cache_dir,
            board_name=board_name,
            board_type=board_type,
        )
        if cached_records:
            warning_messages.append(
                f"Using cached board universe for {board_name} because upstream board constituent fetch returned no usable rows."
            )
            return cached_records, "cache"

    return records, "remote_empty"


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
        board_results: List[BoardCycleBoardResult] = []
        stock_results: List[BoardStockEvaluation] = []
        warning_messages: List[str] = []
        use_local_cache = bool(getattr(args, "use_local_cache", False))
        cache_dir = _resolve_cache_dir(args)
        board_universe_file, board_universe_file_mode, board_universe_seed_meta = (
            _resolve_board_universe_file(getattr(args, "board_universe_file", None))
        )
        file_universes = (
            _read_board_universe_file(
                board_universe_file,
                board_names=board_names,
                board_type=args.board_type,
            )
            if board_universe_file
            else {}
        )
        if board_universe_file_mode == "official_seed" and _official_seed_is_stale(
            board_universe_seed_meta
        ):
            warning_messages.append(
                "Official board universe seed may be stale: {file_path} imported_at={imported_at} expire_after_days={expire_after_days}".format(
                    file_path=board_universe_file,
                    imported_at=board_universe_seed_meta.get("imported_at", ""),
                    expire_after_days=board_universe_seed_meta.get("expire_after_days", 3),
                )
            )
        run_context = {
            "snapshot_date": args.snapshot_date or "",
            "max_workers": int(args.max_workers or 1),
            "board_type": args.board_type,
            "top_per_board": int(args.top_per_board or 0),
            "limit_per_board": "" if args.limit_per_board is None else int(args.limit_per_board),
            "board_universe_file": str(board_universe_file or ""),
            "board_universe_file_mode": board_universe_file_mode,
            "board_universe_cache_dir": str(cache_dir or ""),
            "use_local_cache": str(use_local_cache).lower(),
        }

        for board_name in board_names:
            records, source = _fetch_board_records(
                board_name=board_name,
                board_type=args.board_type,
                service=service,
                file_universes=file_universes,
                cache_dir=cache_dir,
                use_local_cache=use_local_cache,
                warning_messages=warning_messages,
            )
            if args.limit_per_board:
                records = records[: max(1, int(args.limit_per_board))]
            if not records:
                warning_messages.append(
                    "This run produced empty results for {board_name} because upstream "
                    "board constituent fetch did not return usable rows. "
                    "`board_cycle_label=idle` and `constituent_count=0` here are not valid market conclusions.".format(
                        board_name=board_name
                    )
                )

            board_result, evaluations = service.scan_board_rows(
                board_name=board_name,
                board_type=args.board_type,
                stock_rows=records,
            )
            board_result.board_reason_summary = (
                f"{board_result.board_reason_summary} [source={source}]".strip()
            )
            board_results.append(board_result)
            stock_results.extend(evaluations)

        stock_results = service.resolve_primary_boards_for_results(stock_results)
        stock_results = service.limit_stock_results_per_board(
            stock_results,
            top_per_board=max(0, int(args.top_per_board or 0)),
        )
        write_outputs(
            board_results=board_results,
            stock_results=stock_results,
            output_dir=Path(args.output_dir),
            run_context=run_context,
            warning_messages=warning_messages,
        )
        logger.info(
            "board cycle scan completed: boards=%s, stock_candidates=%s, snapshot_date=%s, max_workers=%s",
            len(board_results),
            len(stock_results),
            args.snapshot_date or "",
            int(args.max_workers or 1),
        )
        return 0
    except Exception as exc:
        logger.exception("board cycle scan failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
