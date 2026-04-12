# -*- coding: utf-8 -*-
"""Tests for ThemeCoreMapperService."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.theme_core_mapper_service import ThemeCoreMapperService


class _FakeCommodityService:
    def __init__(self, payloads):
        self.payloads = payloads

    def analyze_stock(self, stock_code: str, *, stock_name=None, commodity_hint=None):
        return dict(self.payloads[stock_code])


class _FakeDragonService:
    def __init__(self, payloads):
        self.payloads = payloads

    def analyze_stock(self, stock_code: str, *, stock_name=None, market_hint=None):
        return dict(self.payloads[stock_code])


class ThemeCoreMapperServiceTestCase(unittest.TestCase):
    def test_source_beneficiary_maps_to_price_pass_through_core(self):
        service = ThemeCoreMapperService(
            commodity_service=_FakeCommodityService(
                {
                    "601869": {
                        "status": "ok",
                        "stock_code": "601869",
                        "stock_name": "长飞光纤",
                        "theme_key": "optical_communication",
                        "theme_label": "optical_communication",
                        "commodity_key": "optical_fiber",
                        "subtheme_key": "preform_and_materials",
                        "subtheme_label": "preform_and_materials",
                        "stock_role": "source_beneficiary",
                        "chain_role": "upstream",
                        "directness": "direct_beneficiary",
                        "pass_through_direction": "positive",
                        "earnings_release_probability": "high",
                        "combo_reinforcement_score": 3,
                        "warnings": [],
                        "evidence_points": ["c1"],
                    }
                }
            ),
            dragon_service=_FakeDragonService(
                {
                    "601869": {
                        "status": "ok",
                        "leader_type": "hybrid_leader",
                        "leader_probability": "high",
                        "recognizability_score": 3,
                        "logic_consensus_score": 3,
                        "capital_consensus_score": 2,
                        "relative_strength_score": 2,
                        "liquidity_score": 2,
                        "sector_leadership_score": 1,
                        "evidence_points": ["d1"],
                    }
                }
            ),
        )

        payload = service.analyze_stock("601869", stock_name="长飞光纤", commodity_hint="optical_fiber")

        self.assertEqual(payload["core_driver_type"], "price_pass_through")
        self.assertEqual(payload["subtheme_core_probability"], "high")
        self.assertTrue(payload["is_direct_beneficiary"])

    def test_prosperity_core_can_be_high_subtheme_core_without_direct_benefit(self):
        service = ThemeCoreMapperService(
            commodity_service=_FakeCommodityService(
                {
                    "300308": {
                        "status": "ok",
                        "stock_code": "300308",
                        "stock_name": "中际旭创",
                        "theme_key": "optical_communication",
                        "theme_label": "optical_communication",
                        "commodity_key": "optical_fiber",
                        "subtheme_key": "optical_module_and_cpo",
                        "subtheme_label": "optical_module_and_cpo",
                        "stock_role": "prosperity_core",
                        "chain_role": "weak_proxy",
                        "directness": "pseudo_or_cost_pressure",
                        "pass_through_direction": "mixed",
                        "earnings_release_probability": "low",
                        "combo_reinforcement_score": 0,
                        "warnings": [],
                        "evidence_points": ["c1"],
                    }
                }
            ),
            dragon_service=_FakeDragonService(
                {
                    "300308": {
                        "status": "ok",
                        "leader_type": "capital_leader",
                        "leader_probability": "high",
                        "recognizability_score": 2,
                        "logic_consensus_score": 1,
                        "capital_consensus_score": 3,
                        "relative_strength_score": 3,
                        "liquidity_score": 3,
                        "sector_leadership_score": 1,
                        "evidence_points": ["d1"],
                    }
                }
            ),
        )

        payload = service.analyze_stock("300308", stock_name="中际旭创", commodity_hint="optical_fiber")

        self.assertEqual(payload["stock_role"], "prosperity_core")
        self.assertEqual(payload["core_driver_type"], "subtheme_prosperity")
        self.assertEqual(payload["subtheme_core_probability"], "high")
        self.assertFalse(payload["is_direct_beneficiary"])


if __name__ == "__main__":
    unittest.main()
