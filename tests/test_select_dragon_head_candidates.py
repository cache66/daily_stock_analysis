# -*- coding: utf-8 -*-
"""Tests for the dragon-head batch selector."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.select_dragon_head_candidates import (
    scan_dragon_head_candidates,
    write_outputs,
)


def _fake_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "600001", "name": "混合龙头"},
            {"code": "600002", "name": "资金龙头"},
            {"code": "600003", "name": "伪龙头"},
        ]
    )


class _FakeDragonHeadAnalysisService:
    def __init__(self):
        self.manager = _FakeManagerForPrefetch()
        self.seen_scan_contexts = []

    def analyze_stock(
        self,
        stock_code: str,
        *,
        stock_name: str | None = None,
        market_hint: str | None = None,
        scan_context: dict | None = None,
    ):
        self.seen_scan_contexts.append(
            {"stock_code": stock_code, "market_hint": market_hint, "scan_context": scan_context}
        )
        payloads = {
            "600001": {
                "status": "ok",
                "stock_code": "600001",
                "stock_name": "混合龙头",
                "leader_probability": "high",
                "leader_type": "hybrid_leader",
                "recognizability_score": 3,
                "logic_consensus_score": 3,
                "capital_consensus_score": 2,
                "sector_leadership_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "catalyst_score": 2,
                "factor_breakdown": {},
                "ranking_tuple": [3, 3, 2, 2, 2, 8],
                "warnings": [],
                "summary": "hybrid leader",
                "evidence_points": ["e1"],
            },
            "600002": {
                "status": "ok",
                "stock_code": "600002",
                "stock_name": "资金龙头",
                "leader_probability": "medium",
                "leader_type": "capital_leader",
                "recognizability_score": 2,
                "logic_consensus_score": 1,
                "capital_consensus_score": 3,
                "sector_leadership_score": 1,
                "relative_strength_score": 2,
                "liquidity_score": 3,
                "catalyst_score": 1,
                "factor_breakdown": {},
                "ranking_tuple": [2, 1, 2, 3, 1, 7],
                "warnings": [],
                "summary": "capital leader",
                "evidence_points": ["e2"],
            },
            "600003": {
                "status": "ok",
                "stock_code": "600003",
                "stock_name": "伪龙头",
                "leader_probability": "low",
                "leader_type": "pseudo_leader",
                "recognizability_score": 0,
                "logic_consensus_score": 0,
                "capital_consensus_score": 0,
                "sector_leadership_score": 0,
                "relative_strength_score": 1,
                "liquidity_score": 0,
                "catalyst_score": 0,
                "factor_breakdown": {},
                "ranking_tuple": [0, 0, 1, 0, 0, 1],
                "warnings": ["w1"],
                "summary": "pseudo leader",
                "evidence_points": ["e3"],
            },
        }
        return payloads[stock_code]


class _FakeManagerForPrefetch:
    def __init__(self) -> None:
        self.prefetch_calls = []
        self.sector_calls = 0

    def prefetch_realtime_quotes(self, stock_codes):
        self.prefetch_calls.append(list(stock_codes))
        return len(stock_codes)

    def get_sector_rankings(self, n: int = 10):
        self.sector_calls += 1
        return ([{"name": "AI", "change_pct": 3.0}], [])


class DragonHeadSelectorTestCase(unittest.TestCase):
    def test_scan_filters_out_pseudo_by_default(self) -> None:
        result = scan_dragon_head_candidates(
            universe_provider=_fake_universe,
            analysis_service=_FakeDragonHeadAnalysisService(),
            minimum_probability="medium",
            include_pseudo_leaders=False,
        )
        self.assertEqual([item.stock_code for item in result.selected], ["600001", "600002"])

    def test_scan_can_keep_pseudo_leaders_when_enabled(self) -> None:
        result = scan_dragon_head_candidates(
            universe_provider=_fake_universe,
            analysis_service=_FakeDragonHeadAnalysisService(),
            minimum_probability="low",
            include_pseudo_leaders=True,
        )
        self.assertEqual([item.stock_code for item in result.selected], ["600001", "600002", "600003"])

    def test_write_outputs_contains_core_leader_columns(self) -> None:
        result = scan_dragon_head_candidates(
            universe_provider=_fake_universe,
            analysis_service=_FakeDragonHeadAnalysisService(),
            minimum_probability="medium",
            include_pseudo_leaders=False,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_outputs(result, Path(tmpdir))
            self.assertTrue(paths["csv"].exists())
            df = pd.read_csv(paths["csv"])
            self.assertIn("leader_type", df.columns)
            self.assertIn("recognizability_score", df.columns)
            self.assertIn("sector_leadership_score", df.columns)

    def test_scan_prefetches_realtime_and_sector_context_once(self) -> None:
        service = _FakeDragonHeadAnalysisService()

        result = scan_dragon_head_candidates(
            universe_provider=_fake_universe,
            analysis_service=service,
            minimum_probability="medium",
            include_pseudo_leaders=False,
        )

        self.assertEqual(result.evaluated_count, 3)
        self.assertEqual(len(service.manager.prefetch_calls), 1)
        self.assertEqual(service.manager.prefetch_calls[0], ["600001", "600002", "600003"])
        self.assertEqual(service.manager.sector_calls, 1)
        self.assertTrue(all(item["scan_context"] for item in service.seen_scan_contexts))
