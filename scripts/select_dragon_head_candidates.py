#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-shares for high-recognizability core dragon-head candidates."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.dragon_head_analysis_service import DragonHeadAnalysisService
from src.services.kline_selector_service import KlineSelectorService


logger = logging.getLogger("dragon_head_selector")

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "dragon_head_candidates"
LEADER_TYPE_ORDER = {
    "pseudo_leader": 0,
    "logic_leader": 1,
    "capital_leader": 1,
    "hybrid_leader": 2,
}
PROBABILITY_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass
class DragonHeadCandidate:
    stock_code: str
    stock_name: str
    leader_probability: str
    leader_type: str
    recognizability_score: int = 0
    logic_consensus_score: int = 0
    capital_consensus_score: int = 0
    sector_leadership_score: int = 0
    relative_strength_score: int = 0
    liquidity_score: int = 0
    catalyst_score: int = 0
    ranking_tuple: List[int] = field(default_factory=list)
    factor_breakdown: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    warnings: List[str] = field(default_factory=list)
    evidence_points: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "leader_probability": self.leader_probability,
            "leader_type": self.leader_type,
            "recognizability_score": self.recognizability_score,
            "logic_consensus_score": self.logic_consensus_score,
            "capital_consensus_score": self.capital_consensus_score,
            "sector_leadership_score": self.sector_leadership_score,
            "relative_strength_score": self.relative_strength_score,
            "liquidity_score": self.liquidity_score,
            "catalyst_score": self.catalyst_score,
            "summary": self.summary,
            "warnings": " | ".join(self.warnings),
            "evidence_points": " | ".join(self.evidence_points[:4]),
        }


@dataclass
class DragonHeadRunResult:
    universe_size: int
    evaluated_count: int
    selected: List[DragonHeadCandidate] = field(default_factory=list)
    universe_codes: List[str] = field(default_factory=list)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _passes_probability(leader_probability: str, minimum_probability: str) -> bool:
    return PROBABILITY_ORDER.get(leader_probability, 0) >= PROBABILITY_ORDER.get(minimum_probability, 0)


def _passes_leader_type(leader_type: str, *, include_pseudo: bool) -> bool:
    if include_pseudo:
        return True
    return leader_type in {"logic_leader", "capital_leader", "hybrid_leader"}


def build_candidate(payload: Dict[str, Any]) -> DragonHeadCandidate:
    return DragonHeadCandidate(
        stock_code=str(payload.get("stock_code") or ""),
        stock_name=str(payload.get("stock_name") or ""),
        leader_probability=str(payload.get("leader_probability") or ""),
        leader_type=str(payload.get("leader_type") or ""),
        recognizability_score=int(payload.get("recognizability_score") or 0),
        logic_consensus_score=int(payload.get("logic_consensus_score") or 0),
        capital_consensus_score=int(payload.get("capital_consensus_score") or 0),
        sector_leadership_score=int(payload.get("sector_leadership_score") or 0),
        relative_strength_score=int(payload.get("relative_strength_score") or 0),
        liquidity_score=int(payload.get("liquidity_score") or 0),
        catalyst_score=int(payload.get("catalyst_score") or 0),
        ranking_tuple=[int(item) for item in (payload.get("ranking_tuple") or []) if isinstance(item, (int, float))],
        factor_breakdown=dict(payload.get("factor_breakdown") or {}),
        summary=str(payload.get("summary") or ""),
        warnings=list(payload.get("warnings") or []),
        evidence_points=list(payload.get("evidence_points") or []),
    )


def build_scan_context(service: Any, universe_codes: List[str]) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    manager = getattr(service, "manager", None)
    if manager is None:
        return context

    if universe_codes and hasattr(manager, "prefetch_realtime_quotes"):
        try:
            manager.prefetch_realtime_quotes(universe_codes)
        except Exception as exc:
            logger.debug("Dragon head realtime prefetch skipped: %s", exc)

    if hasattr(manager, "get_sector_rankings"):
        try:
            rankings = manager.get_sector_rankings(10)
        except Exception as exc:
            logger.debug("Dragon head sector prefetch skipped: %s", exc)
        else:
            if isinstance(rankings, tuple) and len(rankings) == 2:
                context["sector_rankings"] = rankings

    return context


def scan_dragon_head_candidates(
    *,
    limit: Optional[int] = None,
    max_workers: int = 1,
    minimum_probability: str = "medium",
    include_pseudo_leaders: bool = False,
    enable_news_search: bool = False,
    fast_mode: bool = True,
    universe_provider: Optional[Any] = None,
    analysis_service: Optional[Any] = None,
) -> DragonHeadRunResult:
    selector_service = KlineSelectorService(universe_provider=universe_provider)
    universe = selector_service.get_a_share_universe(limit=limit)
    universe_codes = universe["code"].tolist()

    service = analysis_service or DragonHeadAnalysisService(
        enable_news_search=enable_news_search,
        fast_mode=fast_mode,
    )
    scan_context = build_scan_context(service, universe_codes)
    selected: List[DragonHeadCandidate] = []

    def _evaluate(row_data: Dict[str, Any]) -> Optional[DragonHeadCandidate]:
        payload = service.analyze_stock(
            row_data["code"],
            stock_name=row_data.get("name"),
            market_hint="cn",
            scan_context=scan_context,
        )
        if payload.get("status") != "ok":
            return None
        candidate = build_candidate(payload)
        if not _passes_probability(candidate.leader_probability, minimum_probability):
            return None
        if not _passes_leader_type(candidate.leader_type, include_pseudo=include_pseudo_leaders):
            return None
        return candidate

    rows = universe.to_dict(orient="records")
    if max_workers <= 1:
        for row in rows:
            candidate = _evaluate(row)
            if candidate is not None:
                selected.append(candidate)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_evaluate, row) for row in rows]
            for future in as_completed(futures):
                candidate = future.result()
                if candidate is not None:
                    selected.append(candidate)

    selected.sort(
        key=lambda item: (
            item.recognizability_score,
            item.sector_leadership_score,
            item.relative_strength_score,
            item.liquidity_score,
            item.catalyst_score,
            LEADER_TYPE_ORDER.get(item.leader_type, 0),
            item.stock_code,
        ),
        reverse=True,
    )

    return DragonHeadRunResult(
        universe_size=len(universe),
        evaluated_count=len(universe),
        selected=selected,
        universe_codes=universe_codes,
    )


def write_outputs(run_result: DragonHeadRunResult, output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = [item.to_record() for item in run_result.selected]
    df = pd.DataFrame(records)

    csv_path = output_dir / "dragon_head_candidates.csv"
    txt_path = output_dir / "dragon_head_candidates.txt"
    md_path = output_dir / "dragon_head_candidates.md"
    universe_path = output_dir / "a_share_universe_no_bse.txt"

    if df.empty:
        df = pd.DataFrame(
            columns=[
                "code",
                "name",
                "leader_probability",
                "leader_type",
                "recognizability_score",
                "logic_consensus_score",
                "capital_consensus_score",
                "sector_leadership_score",
                "relative_strength_score",
                "liquidity_score",
                "catalyst_score",
                "summary",
            ]
        )

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    txt_lines = [
        "Dragon Head Candidates",
        f"Universe Size: {run_result.universe_size}",
        f"Selected: {len(run_result.selected)}",
        "",
    ]
    for item in run_result.selected[:200]:
        txt_lines.append(
            f"- {item.stock_code} {item.stock_name} | {item.leader_type} | {item.leader_probability} | "
            f"recognizability={item.recognizability_score}"
        )
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    md_lines = [
        "# Dragon Head Candidates",
        "",
        f"- Universe Size: {run_result.universe_size}",
        f"- Selected: {len(run_result.selected)}",
        "",
        "| Code | Name | Leader Type | Probability | Recognizability | Sector | Relative Strength | Liquidity | Catalyst | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in run_result.selected[:200]:
        md_lines.append(
            "| {code} | {name} | {leader_type} | {prob} | {recognizability} | {sector} | {relative} | {liquidity} | {catalyst} | {summary} |".format(
                code=item.stock_code,
                name=item.stock_name,
                leader_type=item.leader_type,
                prob=item.leader_probability,
                recognizability=item.recognizability_score,
                sector=item.sector_leadership_score,
                relative=item.relative_strength_score,
                liquidity=item.liquidity_score,
                catalyst=item.catalyst_score,
                summary=item.summary.replace("|", "/"),
            )
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    universe_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    return {
        "csv": csv_path,
        "txt": txt_path,
        "md": md_path,
        "universe": universe_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan A-shares for high-recognizability dragon-head candidates.")
    parser.add_argument("--limit", type=int, default=None, help="Optional universe limit for debugging.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument(
        "--minimum-probability",
        default="medium",
        choices=["low", "medium", "high"],
        help="Minimum leader probability to keep.",
    )
    parser.add_argument(
        "--include-pseudo-leaders",
        action="store_true",
        help="Keep pseudo leaders in the result set. Default false.",
    )
    parser.add_argument(
        "--enable-news-search",
        action="store_true",
        help="Enable news search during scan. Default false for speed and determinism.",
    )
    parser.add_argument(
        "--full-analysis",
        action="store_true",
        help="Disable fast mode and fetch supporting evidence more aggressively during scan.",
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
    output_dir = Path(args.output_dir)
    run_result = scan_dragon_head_candidates(
        limit=args.limit,
        max_workers=args.max_workers,
        minimum_probability=args.minimum_probability,
        include_pseudo_leaders=bool(args.include_pseudo_leaders),
        enable_news_search=bool(args.enable_news_search),
        fast_mode=not bool(args.full_analysis),
    )
    paths = write_outputs(run_result, output_dir)
    logger.info(
        "Dragon head scan complete: universe=%s selected=%s csv=%s",
        run_result.universe_size,
        len(run_result.selected),
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
