# -*- coding: utf-8 -*-
"""Tests for the commodity beneficiary batch selector."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from unittest.mock import MagicMock

if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.select_commodity_beneficiaries import (
    parse_commodities,
    scan_commodity_beneficiaries,
    write_outputs,
)


def _fake_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "601869", "name": "长飞光纤"},
            {"code": "601138", "name": "工业富联"},
            {"code": "300857", "name": "协创数据"},
        ]
    )


class _FakeAnalysisService:
    def __init__(self):
        self.calls = []

    def analyze_stock(self, stock_code: str, *, stock_name: str | None = None, commodity_hint: str | None = None):
        self.calls.append((stock_code, stock_name, commodity_hint))
        payloads = {
            "601869": {
                "status": "ok",
                "stock_code": "601869",
                "stock_name": "长飞光纤",
                "commodity_key": "optical_fiber",
                "subtheme_key": "preform_and_materials",
                "chain_role": "upstream",
                "pass_through_direction": "positive",
                "earnings_validation_status": "positive",
                "earnings_release_probability": "high",
                "directness": "direct_beneficiary",
                "summary": "direct beneficiary",
                "matched_example": {"bucket": "whitelist", "name": "长飞光纤"},
                "warnings": [],
                "evidence_points": ["e1"],
                "scores": {"total": 10, "commodity_match": 10, "subtheme_match": 10, "chain_role": 9, "pass_through": 5, "earnings_validation": 4},
            },
            "601138": {
                "status": "ok",
                "stock_code": "601138",
                "stock_name": "工业富联",
                "commodity_key": "optical_fiber",
                "subtheme_key": "telecom_equipment_and_network",
                "chain_role": "downstream",
                "pass_through_direction": "negative",
                "earnings_validation_status": "mixed",
                "earnings_release_probability": "low",
                "directness": "pseudo_or_cost_pressure",
                "summary": "counterexample",
                "matched_example": {"bucket": "counterexample", "name": "工业富联"},
                "warnings": ["w1"],
                "evidence_points": ["e2"],
                "scores": {"total": 1},
            },
            "300857": {
                "status": "ok",
                "stock_code": "300857",
                "stock_name": "协创数据",
                "commodity_key": "optical_fiber",
                "subtheme_key": "fiber_and_cable",
                "chain_role": "distribution",
                "pass_through_direction": "positive",
                "earnings_validation_status": "mixed",
                "earnings_release_probability": "medium",
                "directness": "indirect_beneficiary",
                "summary": "distribution beneficiary",
                "matched_example": {"bucket": "whitelist", "name": "协创数据"},
                "warnings": [],
                "evidence_points": ["e3"],
                "scores": {"total": 6},
            },
        }
        return payloads[stock_code]


class CommodityBeneficiarySelectorTestCase(unittest.TestCase):
    def test_parse_commodities_dedupes_and_validates(self) -> None:
        self.assertEqual(parse_commodities("optical_fiber,memory,optical_fiber"), ["optical_fiber", "memory"])
        with self.assertRaises(ValueError):
            parse_commodities("unknown")

    def test_scan_filters_for_direct_candidates_by_default(self) -> None:
        result = scan_commodity_beneficiaries(
            commodity_key="optical_fiber",
            universe_provider=_fake_universe,
            analysis_service=_FakeAnalysisService(),
            include_distribution=False,
            minimum_probability="medium",
        )
        self.assertEqual(result.universe_size, 3)
        self.assertEqual([item.stock_code for item in result.selected], ["601869"])

    def test_scan_can_keep_distribution_when_enabled(self) -> None:
        result = scan_commodity_beneficiaries(
            commodity_key="optical_fiber",
            universe_provider=_fake_universe,
            analysis_service=_FakeAnalysisService(),
            include_distribution=True,
            minimum_probability="medium",
        )
        self.assertEqual([item.stock_code for item in result.selected], ["601869", "300857"])

    def test_write_outputs_emits_csv_txt_md(self) -> None:
        result = scan_commodity_beneficiaries(
            commodity_key="optical_fiber",
            universe_provider=_fake_universe,
            analysis_service=_FakeAnalysisService(),
            include_distribution=True,
            minimum_probability="medium",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_outputs(result, Path(tmpdir))
            self.assertTrue(paths["csv"].exists())
            self.assertTrue(paths["txt"].exists())
            self.assertTrue(paths["md"].exists())
            content = paths["md"].read_text(encoding="utf-8")
            self.assertIn("optical_fiber beneficiaries", content)
            self.assertIn("长飞光纤", content)


if __name__ == "__main__":
    unittest.main()


class _FactorPriorityAnalysisService:
    def analyze_stock(self, stock_code: str, *, stock_name: str | None = None, commodity_hint: str | None = None):
        payloads = {
            "601869": {
                "status": "ok",
                "stock_code": "601869",
                "stock_name": "长飞光纤",
                "commodity_key": "optical_fiber",
                "subtheme_key": "preform_and_materials",
                "chain_role": "upstream",
                "pass_through_direction": "positive",
                "earnings_validation_status": "positive",
                "earnings_release_probability": "high",
                "directness": "direct_beneficiary",
                "summary": "core",
                "recognizability_score": 3,
                "logic_consensus_score": 3,
                "capital_consensus_score": 2,
                "combo_reinforcement_score": 3,
                "ranking_tuple": [3, 2, 2, 1, 0, 10],
                "factor_breakdown": {
                    "sustained_growth": {"score": 2},
                    "liquidity": {"score": 2},
                    "valuation": {"score": 1},
                    "dividend": {"score": 0},
                },
                "matched_example": {"bucket": "whitelist", "name": "长飞光纤"},
                "warnings": [],
                "evidence_points": [],
                "scores": {"total": 10},
            },
            "300857": {
                "status": "ok",
                "stock_code": "300857",
                "stock_name": "协创数据",
                "commodity_key": "optical_fiber",
                "subtheme_key": "fiber_and_cable",
                "chain_role": "distribution",
                "pass_through_direction": "positive",
                "earnings_validation_status": "mixed",
                "earnings_release_probability": "medium",
                "directness": "indirect_beneficiary",
                "summary": "distribution",
                "recognizability_score": 2,
                "logic_consensus_score": 1,
                "capital_consensus_score": 2,
                "combo_reinforcement_score": 1,
                "ranking_tuple": [2, 1, 1, 0, 0, 100],
                "factor_breakdown": {
                    "sustained_growth": {"score": 1},
                    "liquidity": {"score": 1},
                    "valuation": {"score": 0},
                    "dividend": {"score": 0},
                },
                "matched_example": {"bucket": "whitelist", "name": "协创数据"},
                "warnings": [],
                "evidence_points": [],
                "scores": {"total": 100},
            },
            "601138": {
                "status": "ok",
                "stock_code": "601138",
                "stock_name": "工业富联",
                "commodity_key": "optical_fiber",
                "subtheme_key": "telecom_equipment_and_network",
                "chain_role": "downstream",
                "pass_through_direction": "negative",
                "earnings_validation_status": "negative",
                "earnings_release_probability": "low",
                "directness": "pseudo_or_cost_pressure",
                "summary": "counterexample",
                "recognizability_score": 0,
                "logic_consensus_score": 0,
                "capital_consensus_score": 0,
                "combo_reinforcement_score": 0,
                "ranking_tuple": [0, 0, 0, 0, 0, 1],
                "factor_breakdown": {
                    "sustained_growth": {"score": 0},
                    "liquidity": {"score": 0},
                    "valuation": {"score": 0},
                    "dividend": {"score": 0},
                },
                "matched_example": {"bucket": "counterexample", "name": "工业富联"},
                "warnings": [],
                "evidence_points": [],
                "scores": {"total": 1},
            },
        }
        return payloads[stock_code]


class CommodityBeneficiaryFactorPriorityTestCase(unittest.TestCase):
    def test_scan_uses_fixed_factor_priority_before_legacy_total(self) -> None:
        result = scan_commodity_beneficiaries(
            commodity_key="optical_fiber",
            universe_provider=_fake_universe,
            analysis_service=_FactorPriorityAnalysisService(),
            include_distribution=True,
            minimum_probability="medium",
        )
        self.assertEqual([item.stock_code for item in result.selected], ["601869", "300857"])
        self.assertGreater(result.selected[0].recognizability_score, result.selected[1].recognizability_score)

    def test_write_outputs_contains_factor_columns(self) -> None:
        result = scan_commodity_beneficiaries(
            commodity_key="optical_fiber",
            universe_provider=_fake_universe,
            analysis_service=_FactorPriorityAnalysisService(),
            include_distribution=True,
            minimum_probability="medium",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_outputs(result, Path(tmpdir))
            df = pd.read_csv(paths["csv"])
            self.assertIn("theme_key", df.columns)
            self.assertIn("stock_role", df.columns)
            self.assertIn("recognizability_score", df.columns)
            self.assertIn("logic_consensus_score", df.columns)
            self.assertIn("capital_consensus_score", df.columns)
            self.assertIn("combo_reinforcement_score", df.columns)
