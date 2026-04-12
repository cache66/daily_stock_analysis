#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-shares and surface core stocks by theme/subtheme."""

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

from src.services.kline_selector_service import KlineSelectorService
from src.services.theme_core_mapper_service import ThemeCoreMapperService


logger = logging.getLogger("theme_core_mapper_selector")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "theme_core_candidates"
_PROB_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class ThemeCoreCandidate:
    stock_code: str
    stock_name: str
    theme_key: str
    subtheme_key: str
    stock_role: str
    core_driver_type: str
    theme_core_probability: str
    subtheme_core_probability: str
    theme_core_score: int = 0
    subtheme_core_score: int = 0
    is_direct_beneficiary: bool = False
    directness: str = ""
    leader_type: str = ""
    leader_probability: str = ""
    recognizability_score: int = 0
    relative_strength_score: int = 0
    liquidity_score: int = 0
    combo_reinforcement_score: int = 0
    summary: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "theme_key": self.theme_key,
            "subtheme_key": self.subtheme_key,
            "stock_role": self.stock_role,
            "core_driver_type": self.core_driver_type,
            "theme_core_probability": self.theme_core_probability,
            "subtheme_core_probability": self.subtheme_core_probability,
            "theme_core_score": self.theme_core_score,
            "subtheme_core_score": self.subtheme_core_score,
            "is_direct_beneficiary": self.is_direct_beneficiary,
            "directness": self.directness,
            "leader_type": self.leader_type,
            "leader_probability": self.leader_probability,
            "recognizability_score": self.recognizability_score,
            "relative_strength_score": self.relative_strength_score,
            "liquidity_score": self.liquidity_score,
            "combo_reinforcement_score": self.combo_reinforcement_score,
            "summary": self.summary,
            "warnings": " | ".join(self.warnings),
        }


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_candidate(payload: Dict[str, Any]) -> ThemeCoreCandidate:
    return ThemeCoreCandidate(
        stock_code=str(payload.get("stock_code") or ""),
        stock_name=str(payload.get("stock_name") or ""),
        theme_key=str(payload.get("theme_key") or ""),
        subtheme_key=str(payload.get("subtheme_key") or ""),
        stock_role=str(payload.get("stock_role") or ""),
        core_driver_type=str(payload.get("core_driver_type") or ""),
        theme_core_probability=str(payload.get("theme_core_probability") or ""),
        subtheme_core_probability=str(payload.get("subtheme_core_probability") or ""),
        theme_core_score=int(payload.get("theme_core_score") or 0),
        subtheme_core_score=int(payload.get("subtheme_core_score") or 0),
        is_direct_beneficiary=bool(payload.get("is_direct_beneficiary")),
        directness=str(payload.get("directness") or ""),
        leader_type=str(payload.get("leader_type") or ""),
        leader_probability=str(payload.get("leader_probability") or ""),
        recognizability_score=int(payload.get("recognizability_score") or 0),
        relative_strength_score=int(payload.get("relative_strength_score") or 0),
        liquidity_score=int(payload.get("liquidity_score") or 0),
        combo_reinforcement_score=int(payload.get("combo_reinforcement_score") or 0),
        summary=str(payload.get("summary") or ""),
        warnings=list(payload.get("warnings") or []),
    )


def scan_theme_core_candidates(
    *,
    commodity_hint: str,
    limit: Optional[int] = None,
    max_workers: int = 1,
    top_per_subtheme: int = 1,
    minimum_subtheme_core_probability: str = "medium",
    universe_provider: Optional[Any] = None,
    mapper_service: Optional[Any] = None,
) -> tuple[List[ThemeCoreCandidate], int]:
    selector_service = KlineSelectorService(universe_provider=universe_provider)
    universe = selector_service.get_a_share_universe(limit=limit)
    rows = universe.to_dict(orient="records")
    service = mapper_service or ThemeCoreMapperService(enable_news_search=False)
    selected: List[ThemeCoreCandidate] = []

    def _evaluate(row_data: Dict[str, Any]) -> Optional[ThemeCoreCandidate]:
        payload = service.analyze_stock(
            row_data["code"],
            stock_name=row_data.get("name"),
            commodity_hint=commodity_hint,
            market_hint="cn",
        )
        if payload.get("status") != "ok":
            return None
        candidate = build_candidate(payload)
        if not candidate.theme_key or not candidate.subtheme_key:
            return None
        if _PROB_ORDER.get(candidate.subtheme_core_probability, 0) < _PROB_ORDER.get(minimum_subtheme_core_probability, 0):
            return None
        return candidate

    if max_workers <= 1:
        for row in rows:
            item = _evaluate(row)
            if item is not None:
                selected.append(item)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_evaluate, row) for row in rows]
            for future in as_completed(futures):
                item = future.result()
                if item is not None:
                    selected.append(item)

    selected.sort(
        key=lambda item: (
            item.theme_key,
            item.subtheme_key,
            _PROB_ORDER.get(item.subtheme_core_probability, 0),
            item.subtheme_core_score,
            item.recognizability_score,
            item.relative_strength_score,
            item.liquidity_score,
            item.combo_reinforcement_score,
            item.stock_code,
        ),
        reverse=True,
    )

    grouped: Dict[tuple[str, str], List[ThemeCoreCandidate]] = {}
    for item in selected:
        grouped.setdefault((item.theme_key, item.subtheme_key), []).append(item)

    top_candidates: List[ThemeCoreCandidate] = []
    for _, items in grouped.items():
        top_candidates.extend(items[: max(1, int(top_per_subtheme))])

    top_candidates.sort(
        key=lambda item: (
            _PROB_ORDER.get(item.subtheme_core_probability, 0),
            item.subtheme_core_score,
            item.recognizability_score,
            item.relative_strength_score,
            item.liquidity_score,
            item.combo_reinforcement_score,
            item.stock_code,
        ),
        reverse=True,
    )
    return top_candidates, len(universe)


def write_outputs(
    *,
    commodity_hint: str,
    candidates: List[ThemeCoreCandidate],
    universe_size: int,
    output_dir: Path,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([item.to_record() for item in candidates])
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "code",
                "name",
                "theme_key",
                "subtheme_key",
                "stock_role",
                "subtheme_core_probability",
                "subtheme_core_score",
                "leader_type",
                "leader_probability",
                "summary",
            ]
        )

    csv_path = output_dir / f"{commodity_hint}_theme_core_candidates.csv"
    txt_path = output_dir / f"{commodity_hint}_theme_core_candidates.txt"
    md_path = output_dir / f"{commodity_hint}_theme_core_candidates.md"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    txt_lines = [
        f"Theme core candidates for {commodity_hint}",
        f"Universe Size: {universe_size}",
        f"Selected: {len(candidates)}",
        "",
    ]
    for item in candidates[:200]:
        txt_lines.append(
            f"- {item.stock_code} {item.stock_name} | {item.theme_key} | {item.subtheme_key} | "
            f"{item.stock_role} | {item.subtheme_core_probability}"
        )
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    md_lines = [
        f"# Theme core candidates for {commodity_hint}",
        "",
        f"- Universe Size: {universe_size}",
        f"- Selected: {len(candidates)}",
        "",
        "| Code | Name | Theme | Subtheme | Stock Role | Core Driver | Subtheme Core | Leader | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in candidates[:200]:
        md_lines.append(
            "| {code} | {name} | {theme} | {subtheme} | {stock_role} | {driver} | {core_prob} | {leader_type}/{leader_prob} | {summary} |".format(
                code=item.stock_code,
                name=item.stock_name,
                theme=item.theme_key,
                subtheme=item.subtheme_key,
                stock_role=item.stock_role,
                driver=item.core_driver_type,
                core_prob=item.subtheme_core_probability,
                leader_type=item.leader_type,
                leader_prob=item.leader_probability,
                summary=item.summary.replace("|", "/"),
            )
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {"csv": csv_path, "txt": txt_path, "md": md_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find subtheme core stocks from a broad theme universe.")
    parser.add_argument("--commodity-hint", required=True, help="Theme hint such as optical_fiber or memory.")
    parser.add_argument("--limit", type=int, default=None, help="Optional universe limit for debugging.")
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
    candidates, universe_size = scan_theme_core_candidates(
        commodity_hint=args.commodity_hint,
        limit=args.limit,
        max_workers=max(1, int(args.max_workers)),
        top_per_subtheme=max(1, int(args.top_per_subtheme)),
        minimum_subtheme_core_probability=args.minimum_subtheme_core_probability,
    )
    paths = write_outputs(
        commodity_hint=args.commodity_hint,
        candidates=candidates,
        universe_size=universe_size,
        output_dir=Path(args.output_dir),
    )
    logger.info(
        "Theme core scan complete: commodity_hint=%s universe=%s selected=%s csv=%s",
        args.commodity_hint,
        universe_size,
        len(candidates),
        paths["csv"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
