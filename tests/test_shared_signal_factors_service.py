# -*- coding: utf-8 -*-
"""Tests for the shared signal factors service."""

import sys
import unittest
from unittest.mock import MagicMock

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.services.shared_signal_factors_service import SharedSignalFactorsService  # noqa: E402


class SharedSignalFactorsServiceTestCase(unittest.TestCase):
    def test_build_quality_overlay_factors_reuses_financial_series_continuity(self) -> None:
        service = SharedSignalFactorsService()

        payload = service.build_quality_overlay_factors(
            {
                "earnings": {
                    "financial_report_series": [
                        {"report_date": "2026-03-31", "revenue_yoy": 25.0, "net_profit_yoy": 30.0, "roe": 12.0},
                        {"report_date": "2025-12-31", "revenue_yoy": 18.0, "net_profit_yoy": 20.0, "roe": 10.0},
                        {"report_date": "2025-09-30", "revenue_yoy": 12.0, "net_profit_yoy": 15.0, "roe": 8.0},
                    ]
                }
            }
        )

        self.assertTrue(payload["earnings_continuity_available"])
        self.assertEqual(payload["revenue_positive_quarter_streak"], 3)
        self.assertEqual(payload["profit_positive_quarter_streak"], 3)
        self.assertGreater(float(payload["earnings_continuity_score"]), 0.0)
        self.assertEqual(payload["quality_overlay_score"], payload["earnings_continuity_score"])
        self.assertEqual(payload["quality_overlay_source"], "earnings_financial_report_series")

    def test_build_industry_strength_factors_produces_generic_and_earnings_aliases(self) -> None:
        service = SharedSignalFactorsService()

        payload = service.build_industry_strength_factors(
            bundle_payload={
                "industry": "电力设备",
                "industry_peer_count": 3,
                "belong_boards": [{"name": "光伏"}, {"name": "储能"}],
            },
            contextual_payload={},
        )

        self.assertEqual(payload["industry_strength_label"], "电力设备")
        self.assertTrue(payload["industry_strength_confirmed"])
        self.assertEqual(payload["industry_strength_score"], 3.0)
        self.assertEqual(payload["earnings_industry"], "电力设备")
        self.assertTrue(payload["earnings_industry_confirmed"])

    def test_build_capital_factors_delegates_to_capital_profile_service(self) -> None:
        capital_profile_service = MagicMock()
        capital_profile_service.build_stock_profile.return_value = {
            "capital_profile_score": 63.0,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        }
        service = SharedSignalFactorsService(capital_profile_service=capital_profile_service)

        payload = service.build_capital_factors(
            "600001",
            stock_name="测试样本",
            latest_price=10.5,
            total_market_cap=55e8,
        )

        self.assertEqual(payload["capital_profile_score"], 63.0)
        capital_profile_service.build_stock_profile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
