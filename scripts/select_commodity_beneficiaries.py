#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-shares for commodity price pass-through beneficiaries."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.commodity_pass_through_service import CommodityPassThroughService
from src.services.kline_selector_service import KlineSelectorService


logger = logging.getLogger("commodity_beneficiary_selector")

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "commodity_beneficiaries"
DEFAULT_COMMODITIES = ("optical_fiber", "memory", "hard_disk")
ALLOWED_PROBABILITIES = ("low", "medium", "high")
_PROBABILITY_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class CommodityBeneficiaryCandidate:
    stock_code: str
    stock_name: str
    commodity_key: str
    theme_key: str
    theme_label: str
    subtheme_key: str
    chain_role: str
    stock_role: str
    pass_through_direction: str
    earnings_validation_status: str
    earnings_release_probability: str
    directness: str
    summary: str
    matched_example_bucket: str = ""
    matched_example_name: str = ""
    recognizability_score: int = 0
    sustained_growth_score: int = 0
    liquidity_score: int = 0
    valuation_score: int = 0
    dividend_score: int = 0
    logic_consensus_score: int = 0
    capital_consensus_score: int = 0
    combo_reinforcement_score: int = 0
    ranking_tuple: List[int] = field(default_factory=list)
    factor_breakdown: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    evidence_points: List[str] = field(default_factory=list)
    scores: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "commodity_key": self.commodity_key,
            "theme_key": self.theme_key,
            "theme_label": self.theme_label,
            "subtheme_key": self.subtheme_key,
            "chain_role": self.chain_role,
            "stock_role": self.stock_role,
            "pass_through_direction": self.pass_through_direction,
            "earnings_validation_status": self.earnings_validation_status,
            "earnings_release_probability": self.earnings_release_probability,
            "directness": self.directness,
            "matched_example_bucket": self.matched_example_bucket,
            "matched_example_name": self.matched_example_name,
            "recognizability_score": self.recognizability_score,
            "sustained_growth_score": self.sustained_growth_score,
            "liquidity_score": self.liquidity_score,
            "valuation_score": self.valuation_score,
            "dividend_score": self.dividend_score,
            "logic_consensus_score": self.logic_consensus_score,
            "capital_consensus_score": self.capital_consensus_score,
            "combo_reinforcement_score": self.combo_reinforcement_score,
            "score_total": self.scores.get("total"),
            "score_commodity_match": self.scores.get("commodity_match"),
            "score_subtheme_match": self.scores.get("subtheme_match"),
            "score_chain_role": self.scores.get("chain_role"),
            "score_pass_through": self.scores.get("pass_through"),
            "score_earnings_validation": self.scores.get("earnings_validation"),
            "warnings": " | ".join(self.warnings),
            "summary": self.summary,
            "evidence_points": " | ".join(self.evidence_points[:4]),
        }


@dataclass
class CommodityBeneficiaryRunResult:
    commodity_key: str
    universe_size: int
    evaluated_count: int
    selected: List[CommodityBeneficiaryCandidate] = field(default_factory=list)
    universe_codes: List[str] = field(default_factory=list)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_commodities(raw_value: str) -> List[str]:
    values = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    if not values:
        raise ValueError("at least one commodity is required")
    unknown = [item for item in values if item not in DEFAULT_COMMODITIES]
    if unknown:
        raise ValueError(f"unknown commodities: {', '.join(unknown)}")
    deduped: List[str] = []
    for item in values:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _passes_probability(candidate_probability: str, minimum_probability: str) -> bool:
    return _PROBABILITY_ORDER.get(candidate_probability, 0) >= _PROBABILITY_ORDER.get(minimum_probability, 0)


def _passes_role(role: str, *, include_distribution: bool) -> bool:
    if role in {"upstream", "midstream"}:
        return True
    if include_distribution and role == "distribution":
        return True
    return False


def _passes_directness(directness: str, *, include_distribution: bool) -> bool:
    if directness == "direct_beneficiary":
        return True
    if include_distribution and directness == "indirect_beneficiary":
        return True
    return False


def build_candidate(payload: Dict[str, Any]) -> CommodityBeneficiaryCandidate:
    matched_example = payload.get("matched_example") or {}
    return CommodityBeneficiaryCandidate(
        stock_code=str(payload.get("stock_code") or ""),
        stock_name=str(payload.get("stock_name") or ""),
        commodity_key=str(payload.get("commodity_key") or ""),
        theme_key=str(payload.get("theme_key") or ""),
        theme_label=str(payload.get("theme_label") or ""),
        subtheme_key=str(payload.get("subtheme_key") or ""),
        chain_role=str(payload.get("chain_role") or ""),
        stock_role=str(payload.get("stock_role") or ""),
        pass_through_direction=str(payload.get("pass_through_direction") or ""),
        earnings_validation_status=str(payload.get("earnings_validation_status") or ""),
        earnings_release_probability=str(payload.get("earnings_release_probability") or ""),
        directness=str(payload.get("directness") or ""),
        summary=str(payload.get("summary") or ""),
        matched_example_bucket=str(matched_example.get("bucket") or ""),
        matched_example_name=str(matched_example.get("name") or ""),
        recognizability_score=int(payload.get("recognizability_score") or 0),
        sustained_growth_score=int(((payload.get("factor_breakdown") or {}).get("sustained_growth") or {}).get("score") or 0),
        liquidity_score=int(((payload.get("factor_breakdown") or {}).get("liquidity") or {}).get("score") or 0),
        valuation_score=int(((payload.get("factor_breakdown") or {}).get("valuation") or {}).get("score") or 0),
        dividend_score=int(((payload.get("factor_breakdown") or {}).get("dividend") or {}).get("score") or 0),
        logic_consensus_score=int(payload.get("logic_consensus_score") or 0),
        capital_consensus_score=int(payload.get("capital_consensus_score") or 0),
        combo_reinforcement_score=int(payload.get("combo_reinforcement_score") or 0),
        ranking_tuple=[int(item) for item in (payload.get("ranking_tuple") or []) if isinstance(item, (int, float))],
        factor_breakdown=dict(payload.get("factor_breakdown") or {}),
        warnings=list(payload.get("warnings") or []),
        evidence_points=list(payload.get("evidence_points") or []),
        scores=dict(payload.get("scores") or {}),
    )


def scan_commodity_beneficiaries(
    *,
    commodity_key: str,
    limit: Optional[int] = None,
    max_workers: int = 1,
    minimum_probability: str = "medium",
    include_distribution: bool = False,
    include_counterexamples: bool = False,
    enable_news_search: bool = False,
    universe_provider: Optional[Any] = None,
    analysis_service: Optional[Any] = None,
) -> CommodityBeneficiaryRunResult:
    selector_service = KlineSelectorService(universe_provider=universe_provider)
    universe = selector_service.get_a_share_universe(limit=limit)
    universe_codes = universe["code"].tolist()

    service = analysis_service or CommodityPassThroughService(enable_news_search=enable_news_search)
    selected: List[CommodityBeneficiaryCandidate] = []

    def _evaluate(row_data: Dict[str, Any]) -> Optional[CommodityBeneficiaryCandidate]:
        payload = service.analyze_stock(
            row_data["code"],
            stock_name=row_data.get("name"),
            commodity_hint=commodity_key,
        )
        if payload.get("status") != "ok":
            return None
        if str(payload.get("commodity_key") or "") != commodity_key:
            return None
        candidate = build_candidate(payload)
        if not _passes_probability(candidate.earnings_release_probability, minimum_probability):
            return None
        if not _passes_role(candidate.chain_role, include_distribution=include_distribution):
            return None
        if not _passes_directness(candidate.directness, include_distribution=include_distribution):
            return None
        if not include_counterexamples and candidate.matched_example_bucket == "counterexample":
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
            item.sustained_growth_score,
            item.liquidity_score,
            item.valuation_score,
            item.dividend_score,
            float(item.scores.get("total") or 0.0),
            item.stock_code,
        ),
        reverse=True,
    )

    return CommodityBeneficiaryRunResult(
        commodity_key=commodity_key,
        universe_size=len(universe),
        evaluated_count=len(universe),
        selected=selected,
        universe_codes=universe_codes,
    )


def write_outputs(run_result: CommodityBeneficiaryRunResult, output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = [item.to_record() for item in run_result.selected]
    df = pd.DataFrame(records)

    csv_path = output_dir / f"{run_result.commodity_key}_beneficiaries.csv"
    txt_path = output_dir / f"{run_result.commodity_key}_beneficiaries.txt"
    md_path = output_dir / f"{run_result.commodity_key}_beneficiaries.md"
    universe_path = output_dir / f"{run_result.commodity_key}_a_share_universe.txt"

    if df.empty:
        df = pd.DataFrame(
            columns=[
                "code",
                "name",
                "commodity_key",
                "subtheme_key",
                "chain_role",
                "pass_through_direction",
                "earnings_validation_status",
                "earnings_release_probability",
                "directness",
                "matched_example_bucket",
                "matched_example_name",
                "score_total",
                "summary",
            ]
        )

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    txt_lines = [
        f"Commodity: {run_result.commodity_key}",
        f"Universe Size: {run_result.universe_size}",
        f"Selected: {len(run_result.selected)}",
        "",
    ]
    for item in run_result.selected[:200]:
        txt_lines.append(
            f"- {item.stock_code} {item.stock_name} | {item.subtheme_key} | {item.chain_role} | "
            f"{item.earnings_release_probability} | {item.directness}"
        )
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    md_lines = [
        f"# {run_result.commodity_key} beneficiaries",
        "",
        f"- Universe Size: {run_result.universe_size}",
        f"- Selected: {len(run_result.selected)}",
        "",
        "| Code | Name | Subtheme | Role | Probability | Directness | Example Bucket | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in run_result.selected[:200]:
        md_lines.append(
            "| {code} | {name} | {subtheme} | {role} | {prob} | {directness} | {bucket} | {summary} |".format(
                code=item.stock_code,
                name=item.stock_name,
                subtheme=item.subtheme_key,
                role=item.chain_role,
                prob=item.earnings_release_probability,
                directness=item.directness,
                bucket=item.matched_example_bucket or "--",
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
    parser = argparse.ArgumentParser(description="Scan A-shares for commodity price pass-through beneficiaries.")
    parser.add_argument(
        "--commodities",
        default=",".join(DEFAULT_COMMODITIES),
        help="Comma-separated commodity keys. Defaults to optical_fiber,memory,hard_disk.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional universe limit for debugging.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel worker count. Default 1.")
    parser.add_argument(
        "--minimum-probability",
        default="medium",
        choices=ALLOWED_PROBABILITIES,
        help="Minimum earnings release probability to keep.",
    )
    parser.add_argument(
        "--include-distribution",
        action="store_true",
        help="Keep distribution/indirect beneficiary candidates in addition to direct upstream/midstream names.",
    )
    parser.add_argument(
        "--include-counterexamples",
        action="store_true",
        help="Keep names that match curated counterexample examples. Default false.",
    )
    parser.add_argument(
        "--enable-news-search",
        action="store_true",
        help="Enable news search during batch scan. Default false for speed and determinism.",
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
    output_root = Path(args.output_dir)
    commodities = parse_commodities(args.commodities)

    for commodity_key in commodities:
        logger.info("Running commodity beneficiary scan for %s", commodity_key)
        run_result = scan_commodity_beneficiaries(
            commodity_key=commodity_key,
            limit=args.limit,
            max_workers=args.max_workers,
            minimum_probability=args.minimum_probability,
            include_distribution=bool(args.include_distribution),
            include_counterexamples=bool(args.include_counterexamples),
            enable_news_search=bool(args.enable_news_search),
        )
        paths = write_outputs(run_result, output_root / commodity_key)
        logger.info(
            "Commodity beneficiary scan complete: commodity=%s universe=%s selected=%s csv=%s",
            commodity_key,
            run_result.universe_size,
            len(run_result.selected),
            paths["csv"],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
