#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build board-level recognizability rankings from persisted signal snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DatabaseManager


logger = logging.getLogger("board_recognizability_collector")

DEFAULT_SOURCE_SIGNAL_TYPE = "hundred_day_high"
DEFAULT_SIGNAL_TYPE_PREFIX = "board_recognizability"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_recognizability_rankings"
_GENERIC_LEADER_TYPES = {"hybrid_leader", "logic_leader", "capital_leader", "pseudo_leader"}


@dataclass
class BoardRecognizabilityCandidate:
    board_name: str
    stock_code: str
    stock_name: str
    source_signal_type: str
    source_signal_date: str
    source_reason_summary: str = ""
    source_theme_label: str = ""
    source_industry_logic: str = ""
    source_news_logic: str = ""
    source_technical_logic: str = ""
    previous_hit_count: int = 0
    days_since_previous_hit: Optional[int] = None
    is_consecutive_signal: bool = False
    total_market_cap: Optional[float] = None
    total_market_cap_yi: Optional[float] = None
    close: Optional[float] = None
    latest_high: Optional[float] = None
    window_high: Optional[float] = None
    new_high_window: Optional[int] = None
    leader_probability: str = ""
    leader_type: str = ""
    recognizability_score: int = 0
    logic_consensus_score: int = 0
    capital_consensus_score: int = 0
    sector_leadership_score: int = 0
    relative_strength_score: int = 0
    liquidity_score: int = 0
    catalyst_score: int = 0
    board_rank: int = 0
    board_candidate_count: int = 0

    def to_record(self) -> Dict[str, Any]:
        return {
            "board_name": self.board_name,
            "board_rank": self.board_rank,
            "board_candidate_count": self.board_candidate_count,
            "code": self.stock_code,
            "name": self.stock_name,
            "source_signal_type": self.source_signal_type,
            "source_signal_date": self.source_signal_date,
            "source_theme_label": self.source_theme_label,
            "source_reason_summary": self.source_reason_summary,
            "previous_hit_count": self.previous_hit_count,
            "days_since_previous_hit": self.days_since_previous_hit,
            "is_consecutive_signal": self.is_consecutive_signal,
            "total_market_cap": self.total_market_cap,
            "total_market_cap_yi": self.total_market_cap_yi,
            "close": self.close,
            "latest_high": self.latest_high,
            "window_high": self.window_high,
            "new_high_window": self.new_high_window,
            "leader_probability": self.leader_probability,
            "leader_type": self.leader_type,
            "recognizability_score": self.recognizability_score,
            "logic_consensus_score": self.logic_consensus_score,
            "capital_consensus_score": self.capital_consensus_score,
            "sector_leadership_score": self.sector_leadership_score,
            "relative_strength_score": self.relative_strength_score,
            "liquidity_score": self.liquidity_score,
            "catalyst_score": self.catalyst_score,
        }


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _safe_json_loads(value: Optional[str]) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _extract_board_name(
    cause_payload: Dict[str, Any],
    metrics_payload: Dict[str, Any],
    criteria_payload: Dict[str, Any],
) -> str:
    for key in ("industry", "board_name"):
        candidate = str(cause_payload.get(key, "") or "").strip()
        if candidate and candidate not in _GENERIC_LEADER_TYPES:
            return candidate

    for key in ("board_name", "industry"):
        candidate = str(metrics_payload.get(key, "") or "").strip()
        if candidate and candidate not in _GENERIC_LEADER_TYPES:
            return candidate

    for key in ("board_name", "industry"):
        candidate = str(criteria_payload.get(key, "") or "").strip()
        if candidate and candidate not in _GENERIC_LEADER_TYPES:
            return candidate

    belong_boards = cause_payload.get("belong_boards")
    if isinstance(belong_boards, list):
        for item in belong_boards:
            if isinstance(item, dict):
                candidate = str(item.get("name", "") or item.get("board_name", "") or "").strip()
                if candidate:
                    return candidate

    fundamental_context = cause_payload.get("fundamental_context")
    if isinstance(fundamental_context, dict):
        board_data = fundamental_context.get("boards", {})
        if isinstance(board_data, dict):
            items = board_data.get("data", [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        candidate = str(item.get("name", "") or item.get("board_name", "") or "").strip()
                        if candidate:
                            return candidate
    return ""


def _ranking_tuple(candidate: BoardRecognizabilityCandidate) -> Tuple[Any, ...]:
    probability_order = {"": 0, "low": 1, "medium": 2, "high": 3}
    leader_type_order = {
        "": 0,
        "pseudo_leader": 0,
        "logic_leader": 1,
        "capital_leader": 1,
        "hybrid_leader": 2,
    }
    return (
        probability_order.get(candidate.leader_probability, 0),
        candidate.recognizability_score,
        candidate.logic_consensus_score,
        candidate.capital_consensus_score,
        candidate.sector_leadership_score,
        candidate.relative_strength_score,
        candidate.liquidity_score,
        candidate.catalyst_score,
        candidate.previous_hit_count,
        1 if candidate.is_consecutive_signal else 0,
        candidate.total_market_cap_yi or 0.0,
        candidate.latest_high or 0.0,
        candidate.close or 0.0,
        leader_type_order.get(candidate.leader_type, 0),
        candidate.stock_code,
    )


def build_signal_type(board_name: str, *, prefix: str = DEFAULT_SIGNAL_TYPE_PREFIX) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(board_name or "").strip().lower()).strip("_")
    digest = hashlib.sha1(str(board_name or "").encode("utf-8")).hexdigest()[:10]
    if not normalized:
        normalized = "board"
    max_normalized_len = max(8, 64 - len(prefix) - len(digest) - 3)
    normalized = normalized[:max_normalized_len].strip("_") or "board"
    return f"{prefix}__{normalized}_{digest}"


def resolve_source_snapshot_date(
    db: DatabaseManager,
    *,
    source_signal_type: str,
    snapshot_date: Optional[date],
) -> date:
    if snapshot_date is not None:
        return snapshot_date
    rows = db.get_signal_snapshots(
        signal_type=source_signal_type,
        days=3650,
        limit=1,
    )
    if not rows:
        raise RuntimeError(f"no persisted snapshots found for source signal type: {source_signal_type}")
    resolved_date = getattr(rows[0], "signal_date", None)
    if resolved_date is None:
        raise RuntimeError(f"latest snapshot date missing for source signal type: {source_signal_type}")
    return resolved_date


def _row_to_candidate(row: Any, *, source_signal_type: str, source_signal_date: date) -> Optional[BoardRecognizabilityCandidate]:
    metrics_payload = _safe_json_loads(getattr(row, "metrics_payload", None))
    cause_payload = _safe_json_loads(getattr(row, "cause_payload", None))
    history_payload = _safe_json_loads(getattr(row, "history_payload", None))
    criteria_payload = _safe_json_loads(getattr(row, "criteria_payload", None))

    board_name = _extract_board_name(cause_payload, metrics_payload, criteria_payload)
    stock_code = str(getattr(row, "code", "") or "").strip()
    if not board_name or not stock_code:
        return None

    total_market_cap = _to_float(metrics_payload.get("total_market_cap"))
    days_since_previous_hit = _to_int(history_payload.get("days_since_previous_hit"))
    return BoardRecognizabilityCandidate(
        board_name=board_name,
        stock_code=stock_code,
        stock_name=str(getattr(row, "name", "") or "").strip(),
        source_signal_type=source_signal_type,
        source_signal_date=source_signal_date.isoformat(),
        source_reason_summary=str(cause_payload.get("reason_summary", "") or "").strip(),
        source_theme_label=str(cause_payload.get("theme_label", "") or "").strip(),
        source_industry_logic=str(cause_payload.get("industry_logic", "") or "").strip(),
        source_news_logic=str(cause_payload.get("news_logic", "") or "").strip(),
        source_technical_logic=str(cause_payload.get("technical_logic", "") or "").strip(),
        previous_hit_count=_to_int(history_payload.get("previous_hit_count")) or 0,
        days_since_previous_hit=days_since_previous_hit,
        is_consecutive_signal=days_since_previous_hit is not None and 0 < days_since_previous_hit <= 4,
        total_market_cap=total_market_cap,
        total_market_cap_yi=(round(total_market_cap / 1e8, 2) if total_market_cap is not None else None),
        close=_to_float(metrics_payload.get("close")),
        latest_high=_to_float(metrics_payload.get("latest_high")),
        window_high=_to_float(metrics_payload.get("window_high")),
        new_high_window=_to_int(metrics_payload.get("new_high_window")),
        leader_probability=str(metrics_payload.get("leader_probability", "") or "").strip(),
        leader_type=str(metrics_payload.get("leader_type", "") or "").strip(),
        recognizability_score=_to_int(metrics_payload.get("recognizability_score")) or 0,
        logic_consensus_score=_to_int(metrics_payload.get("logic_consensus_score")) or 0,
        capital_consensus_score=_to_int(metrics_payload.get("capital_consensus_score")) or 0,
        sector_leadership_score=_to_int(metrics_payload.get("sector_leadership_score")) or 0,
        relative_strength_score=_to_int(metrics_payload.get("relative_strength_score")) or 0,
        liquidity_score=_to_int(metrics_payload.get("liquidity_score")) or 0,
        catalyst_score=_to_int(metrics_payload.get("catalyst_score")) or 0,
    )


def collect_board_rankings(
    *,
    db: Optional[DatabaseManager] = None,
    source_signal_type: str = DEFAULT_SOURCE_SIGNAL_TYPE,
    snapshot_date: Optional[date] = None,
    top_n: int = 3,
) -> tuple[date, List[BoardRecognizabilityCandidate], Dict[str, int]]:
    db = db or DatabaseManager.get_instance()
    resolved_date = resolve_source_snapshot_date(
        db,
        source_signal_type=source_signal_type,
        snapshot_date=snapshot_date,
    )
    rows = db.get_signal_snapshots(
        signal_type=source_signal_type,
        signal_date=resolved_date,
    )
    grouped: Dict[str, List[BoardRecognizabilityCandidate]] = {}
    for row in rows:
        candidate = _row_to_candidate(
            row,
            source_signal_type=source_signal_type,
            source_signal_date=resolved_date,
        )
        if candidate is None:
            continue
        grouped.setdefault(candidate.board_name, []).append(candidate)

    board_sizes = {board_name: len(items) for board_name, items in grouped.items()}
    selected: List[BoardRecognizabilityCandidate] = []
    for board_name, items in grouped.items():
        items.sort(key=_ranking_tuple, reverse=True)
        for index, item in enumerate(items[: max(1, int(top_n))], start=1):
            item.board_rank = index
            item.board_candidate_count = len(items)
            selected.append(item)

    selected.sort(
        key=lambda item: (
            item.board_candidate_count,
            -item.board_rank,
            _ranking_tuple(item),
        ),
        reverse=True,
    )
    return resolved_date, selected, board_sizes


def build_criteria_payload(
    *,
    signal_type: str,
    board_name: str,
    snapshot_date: date,
    source_signal_type: str,
    source_signal_date: date,
    top_n: int,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "board_name": board_name,
        "snapshot_date": snapshot_date.isoformat(),
        "source_signal_type": source_signal_type,
        "source_signal_date": source_signal_date.isoformat(),
        "criteria": {
            "top_n": int(top_n),
        },
    }


def build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    recent_hit_dates = [row.signal_date.isoformat() for row in history_rows if row.signal_date]
    latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
    days_since_previous_hit = None
    if latest_previous_hit_date:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": int(lookback_days),
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def _build_metrics_payload(candidate: BoardRecognizabilityCandidate) -> Dict[str, Any]:
    return {
        "board_name": candidate.board_name,
        "board_rank": candidate.board_rank,
        "board_candidate_count": candidate.board_candidate_count,
        "source_signal_type": candidate.source_signal_type,
        "source_signal_date": candidate.source_signal_date,
        "total_market_cap": candidate.total_market_cap,
        "total_market_cap_yi": candidate.total_market_cap_yi,
        "close": candidate.close,
        "latest_high": candidate.latest_high,
        "window_high": candidate.window_high,
        "new_high_window": candidate.new_high_window,
        "leader_probability": candidate.leader_probability,
        "leader_type": candidate.leader_type,
        "recognizability_score": candidate.recognizability_score,
        "logic_consensus_score": candidate.logic_consensus_score,
        "capital_consensus_score": candidate.capital_consensus_score,
        "sector_leadership_score": candidate.sector_leadership_score,
        "relative_strength_score": candidate.relative_strength_score,
        "liquidity_score": candidate.liquidity_score,
        "catalyst_score": candidate.catalyst_score,
    }


def _build_cause_payload(candidate: BoardRecognizabilityCandidate) -> Dict[str, Any]:
    summary = (
        f"{candidate.board_name} 板块辨识度第 {candidate.board_rank} 名；"
        f"来源于 {candidate.source_signal_type} 快照；"
        f"最近历史命中 {candidate.previous_hit_count} 次。"
    )
    return {
        "industry": candidate.board_name,
        "reason_summary": summary,
        "industry_logic": candidate.source_industry_logic or f"{candidate.board_name} 板块当日共有 {candidate.board_candidate_count} 只候选，当前个股排第 {candidate.board_rank}。",
        "news_logic": candidate.source_news_logic,
        "technical_logic": candidate.source_technical_logic or candidate.source_reason_summary,
        "cause_tags": ["board_recognizability", candidate.source_signal_type],
        "theme_label": candidate.source_theme_label,
    }


def persist_board_rankings(
    selected: Iterable[BoardRecognizabilityCandidate],
    *,
    snapshot_date: date,
    signal_type_prefix: str,
    top_n: int,
    history_lookback_days: int,
    db: Optional[DatabaseManager] = None,
) -> pd.DataFrame:
    db = db or DatabaseManager.get_instance()
    records: List[Dict[str, Any]] = []
    for candidate in selected:
        signal_type = build_signal_type(candidate.board_name, prefix=signal_type_prefix)
        criteria_payload = build_criteria_payload(
            signal_type=signal_type,
            board_name=candidate.board_name,
            snapshot_date=snapshot_date,
            source_signal_type=candidate.source_signal_type,
            source_signal_date=date.fromisoformat(candidate.source_signal_date),
            top_n=top_n,
        )
        history_payload = build_history_payload(
            db,
            signal_type=signal_type,
            stock_code=candidate.stock_code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )
        metrics_payload = _build_metrics_payload(candidate)
        cause_payload = _build_cause_payload(candidate)
        db.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date=snapshot_date,
            code=candidate.stock_code,
            name=candidate.stock_name,
            criteria_payload=criteria_payload,
            metrics_payload=metrics_payload,
            cause_payload=cause_payload,
            history_payload=history_payload,
        )
        record = candidate.to_record()
        record.update(
            {
                "signal_type": signal_type,
                "snapshot_date": snapshot_date.isoformat(),
                "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
                "ranking_previous_hit_count": history_payload.get("previous_hit_count", 0),
                "ranking_days_since_previous_hit": history_payload.get("days_since_previous_hit"),
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("\r", " ").replace("|", "/").strip()
    return text or "-"


def write_outputs(df: pd.DataFrame, *, snapshot_date: date, output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "board_recognizability_topn.csv"
    txt_path = output_dir / "board_recognizability_topn.txt"
    md_path = output_dir / "board_recognizability_topn.md"

    export_df = df.copy()
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    txt_lines = [
        "Board Recognizability Rankings",
        f"Snapshot Date: {snapshot_date.isoformat()}",
        f"Rows: {len(export_df)}",
        "",
    ]
    for board_name, group in export_df.groupby("board_name", sort=False):
        txt_lines.append(f"[{board_name}]")
        for _, row in group.sort_values(by=["board_rank", "code"]).iterrows():
            txt_lines.append(
                f"- #{row['board_rank']} {row['code']} {row['name']} | "
                f"mcap_yi={_markdown_cell(row.get('total_market_cap_yi'))} | "
                f"prev_hits={_markdown_cell(row.get('previous_hit_count'))} | "
                f"theme={_markdown_cell(row.get('source_theme_label'))}"
            )
        txt_lines.append("")
    txt_path.write_text("\n".join(txt_lines).strip() + "\n", encoding="utf-8")

    md_lines = [
        "# Board Recognizability Rankings",
        "",
        f"- Snapshot Date: `{snapshot_date.isoformat()}`",
        f"- Rows: `{len(export_df)}`",
        "",
    ]
    for board_name, group in export_df.groupby("board_name", sort=False):
        md_lines.extend(
            [
                f"## {board_name}",
                "",
                "| Rank | Code | Name | Prev Hits | MCap(yi) | Theme | Source Summary |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for _, row in group.sort_values(by=["board_rank", "code"]).iterrows():
            md_lines.append(
                "| {rank} | {code} | {name} | {prev} | {mcap} | {theme} | {summary} |".format(
                    rank=_markdown_cell(row.get("board_rank")),
                    code=_markdown_cell(row.get("code")),
                    name=_markdown_cell(row.get("name")),
                    prev=_markdown_cell(row.get("previous_hit_count")),
                    mcap=_markdown_cell(row.get("total_market_cap_yi")),
                    theme=_markdown_cell(row.get("source_theme_label")),
                    summary=_markdown_cell(row.get("source_reason_summary")),
                )
            )
        md_lines.append("")
    md_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")
    return {"csv": csv_path, "txt": txt_path, "md": md_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build board-level recognizability rankings from persisted snapshots.")
    parser.add_argument(
        "--source-signal-type",
        default=DEFAULT_SOURCE_SIGNAL_TYPE,
        help=f"Persisted source signal type. Default {DEFAULT_SOURCE_SIGNAL_TYPE}.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Source snapshot date in YYYY-MM-DD format. Default latest available date for the source signal type.",
    )
    parser.add_argument("--top-n", type=int, default=3, help="Keep top N stocks per board. Default 3.")
    parser.add_argument(
        "--signal-type-prefix",
        default=DEFAULT_SIGNAL_TYPE_PREFIX,
        help=f"Prefix used for persisted per-board signal types. Default {DEFAULT_SIGNAL_TYPE_PREFIX}.",
    )
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"History lookback days for board-specific ranking persistence. Default {DEFAULT_HISTORY_LOOKBACK_DAYS}.",
    )
    parser.add_argument(
        "--skip-db-persist",
        action="store_true",
        help="Skip database persistence and only export files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory. Default {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    db = DatabaseManager.get_instance()
    requested_date = parse_snapshot_date(args.snapshot_date)
    resolved_date, selected, board_sizes = collect_board_rankings(
        db=db,
        source_signal_type=str(args.source_signal_type or DEFAULT_SOURCE_SIGNAL_TYPE).strip() or DEFAULT_SOURCE_SIGNAL_TYPE,
        snapshot_date=requested_date,
        top_n=max(1, int(args.top_n)),
    )
    output_dir = Path(args.output_dir) / resolved_date.isoformat()
    prefix = str(args.signal_type_prefix or DEFAULT_SIGNAL_TYPE_PREFIX).strip() or DEFAULT_SIGNAL_TYPE_PREFIX
    if args.skip_db_persist:
        persisted_df = pd.DataFrame([item.to_record() for item in selected])
        persisted_df["snapshot_date"] = resolved_date.isoformat()
        persisted_df["signal_type"] = persisted_df["board_name"].map(
            lambda value: build_signal_type(value, prefix=prefix)
        )
    else:
        persisted_df = persist_board_rankings(
            selected,
            snapshot_date=resolved_date,
            signal_type_prefix=prefix,
            top_n=max(1, int(args.top_n)),
            history_lookback_days=max(1, int(args.history_lookback_days)),
            db=db,
        )
    paths = write_outputs(persisted_df, snapshot_date=resolved_date, output_dir=output_dir)
    logger.info(
        "Board recognizability ranking run complete: source_signal_type=%s source_date=%s boards=%s selected=%s csv=%s",
        args.source_signal_type,
        resolved_date.isoformat(),
        len(board_sizes),
        len(persisted_df),
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
