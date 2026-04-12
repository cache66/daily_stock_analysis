# -*- coding: utf-8 -*-
"""Tests for theme core candidate selector."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.select_theme_core_candidates import (
    scan_theme_core_candidates,
    write_outputs,
)


def _fake_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "601869", "name": "长飞光纤"},
            {"code": "300308", "name": "中际旭创"},
            {"code": "600487", "name": "亨通光电"},
        ]
    )


class _FakeThemeCoreMapperService:
    def analyze_stock(self, stock_code: str, *, stock_name=None, commodity_hint=None, market_hint=None):
        payloads = {
            "601869": {
                "status": "ok",
                "stock_code": "601869",
                "stock_name": "长飞光纤",
                "theme_key": "optical_communication",
                "subtheme_key": "preform_and_materials",
                "stock_role": "source_beneficiary",
                "core_driver_type": "price_pass_through",
                "theme_core_probability": "high",
                "subtheme_core_probability": "high",
                "theme_core_score": 3,
                "subtheme_core_score": 3,
                "is_direct_beneficiary": True,
                "directness": "direct_beneficiary",
                "leader_type": "hybrid_leader",
                "leader_probability": "high",
                "recognizability_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "combo_reinforcement_score": 3,
                "summary": "source core",
                "warnings": [],
            },
            "300308": {
                "status": "ok",
                "stock_code": "300308",
                "stock_name": "中际旭创",
                "theme_key": "optical_communication",
                "subtheme_key": "optical_module_and_cpo",
                "stock_role": "prosperity_core",
                "core_driver_type": "subtheme_prosperity",
                "theme_core_probability": "high",
                "subtheme_core_probability": "high",
                "theme_core_score": 3,
                "subtheme_core_score": 3,
                "is_direct_beneficiary": False,
                "directness": "pseudo_or_cost_pressure",
                "leader_type": "capital_leader",
                "leader_probability": "high",
                "recognizability_score": 2,
                "relative_strength_score": 3,
                "liquidity_score": 3,
                "combo_reinforcement_score": 0,
                "summary": "cpo core",
                "warnings": [],
            },
            "600487": {
                "status": "ok",
                "stock_code": "600487",
                "stock_name": "亨通光电",
                "theme_key": "optical_communication",
                "subtheme_key": "preform_and_materials",
                "stock_role": "manufacturing_beneficiary",
                "core_driver_type": "price_pass_through",
                "theme_core_probability": "medium",
                "subtheme_core_probability": "medium",
                "theme_core_score": 2,
                "subtheme_core_score": 2,
                "is_direct_beneficiary": True,
                "directness": "direct_beneficiary",
                "leader_type": "logic_leader",
                "leader_probability": "medium",
                "recognizability_score": 2,
                "relative_strength_score": 1,
                "liquidity_score": 1,
                "combo_reinforcement_score": 1,
                "summary": "secondary core",
                "warnings": [],
            },
        }
        return payloads[stock_code]


class ThemeCoreCandidateSelectorTestCase(unittest.TestCase):
    def test_scan_keeps_top_candidate_per_subtheme(self):
        candidates, universe_size = scan_theme_core_candidates(
            commodity_hint="optical_fiber",
            universe_provider=_fake_universe,
            mapper_service=_FakeThemeCoreMapperService(),
            top_per_subtheme=1,
        )

        self.assertEqual(universe_size, 3)
        self.assertEqual([item.stock_code for item in candidates], ["601869", "300308"])

    def test_write_outputs_contains_theme_and_role_columns(self):
        candidates, universe_size = scan_theme_core_candidates(
            commodity_hint="optical_fiber",
            universe_provider=_fake_universe,
            mapper_service=_FakeThemeCoreMapperService(),
            top_per_subtheme=1,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_outputs(
                commodity_hint="optical_fiber",
                candidates=candidates,
                universe_size=universe_size,
                output_dir=Path(tmpdir),
            )
            df = pd.read_csv(paths["csv"])
            self.assertIn("theme_key", df.columns)
            self.assertIn("subtheme_key", df.columns)
            self.assertIn("stock_role", df.columns)


if __name__ == "__main__":
    unittest.main()
