# -*- coding: utf-8 -*-
"""Tests for the reusable capital profile service."""

import unittest

import pandas as pd

from src.services.capital_profile_service import CapitalProfileService


class _FakeManager:
    def __init__(self, *, history_df: pd.DataFrame, quote_payload: dict, capital_flow_payload: dict):
        self._history_df = history_df
        self._quote_payload = quote_payload
        self._capital_flow_payload = capital_flow_payload
        self.capital_flow_budget_seconds = None

    def get_daily_data(self, _stock_code: str, days: int):
        return self._history_df.tail(days).copy(), "fake"

    def get_realtime_quote(self, _stock_code: str):
        return dict(self._quote_payload)

    def get_capital_flow_context(self, _stock_code: str, budget_seconds=None):
        self.capital_flow_budget_seconds = budget_seconds
        return dict(self._capital_flow_payload)


def _build_history() -> pd.DataFrame:
    closes = [10.0 + index * 0.18 for index in range(25)]
    rows = []
    previous_close = None
    for index, close_price in enumerate(closes):
        pct_chg = None
        if previous_close is not None and previous_close > 0:
            pct_chg = round((close_price - previous_close) / previous_close * 100.0, 2)
        rows.append(
            {
                "date": pd.Timestamp("2026-03-16") + pd.offsets.BDay(index),
                "open": round(close_price * 0.99, 2),
                "high": round(close_price * 1.01, 2),
                "low": round(close_price * 0.985, 2),
                "close": round(close_price, 2),
                "volume": 12_000_000 + index * 100_000,
                "amount": 850_000_000 + index * 20_000_000,
                "turnover_rate": 1.8 + index * 0.03,
                "pct_chg": pct_chg,
            }
        )
        previous_close = close_price
    return pd.DataFrame(rows)


class CapitalProfileServiceTestCase(unittest.TestCase):
    def test_build_stock_profile_scores_active_names(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 14.5,
                "change_pct": 4.8,
                "amount": 1_650_000_000,
                "turnover_rate": 2.6,
                "total_mv": 42_000_000_000,
            },
            capital_flow_payload={
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": 92_000_000,
                        "inflow_5d": 260_000_000,
                        "inflow_10d": 420_000_000,
                    }
                },
            },
        )

        payload = CapitalProfileService(manager=manager).build_stock_profile("600001", stock_name="测试股份")

        self.assertGreaterEqual(payload["capital_consensus_score"], 2)
        self.assertGreaterEqual(payload["capital_profile_score"], 55.0)
        self.assertGreaterEqual(payload["capital_flow_score"], 2)
        self.assertGreaterEqual(payload["relative_strength_score"], 2)
        self.assertGreaterEqual(payload["liquidity_score"], 2)
        self.assertIn("资金共识", payload["capital_profile_summary"])
        self.assertIn("5 日净流入", payload["capital_profile_summary"])

    def test_build_stock_profile_fail_open_when_capital_flow_missing(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 11.2,
                "change_pct": 0.5,
                "amount": 120_000_000,
                "turnover_rate": 0.9,
                "total_mv": 65_000_000_000,
            },
            capital_flow_payload={"status": "not_supported", "data": {"stock_flow": {}}},
        )

        payload = CapitalProfileService(manager=manager).build_stock_profile("600002")

        self.assertEqual(payload["capital_flow_status"], "not_supported")
        self.assertEqual(payload["capital_flow_score"], 0)
        self.assertIsNone(payload["main_net_inflow"])

    def test_build_stock_profile_exposes_continuity_and_structure_scores(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 14.5,
                "change_pct": 3.6,
                "amount": 1_520_000_000,
                "turnover_rate": 2.3,
                "total_mv": 41_000_000_000,
            },
            capital_flow_payload={
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": 82_000_000,
                        "inflow_5d": 280_000_000,
                        "inflow_10d": 460_000_000,
                    }
                },
            },
        )

        payload = CapitalProfileService(manager=manager).build_stock_profile("600003", stock_name="连续流入样本")

        self.assertIn("capital_flow_continuity_score", payload)
        self.assertIn("capital_structure_score", payload)
        self.assertGreaterEqual(payload["capital_flow_continuity_score"], 2)
        self.assertGreaterEqual(payload["capital_structure_score"], 1)

    def test_capital_flow_score_uses_small_cap_tier_thresholds(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 12.3,
                "change_pct": 2.1,
                "amount": 980_000_000,
                "turnover_rate": 2.1,
                "total_mv": 8_000_000_000,
            },
            capital_flow_payload={
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": 25_000_000,
                        "inflow_5d": 90_000_000,
                        "inflow_10d": 150_000_000,
                    }
                },
            },
        )

        payload = CapitalProfileService(manager=manager).build_stock_profile("600111", stock_name="灏忓競鍊煎湪娴佸叆")

        self.assertEqual(payload["capital_flow_score"], 3)

    def test_capital_flow_score_for_large_cap_requires_higher_absolute_inflow(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 26.8,
                "change_pct": 1.3,
                "amount": 2_600_000_000,
                "turnover_rate": 1.1,
                "total_mv": 150_000_000_000,
            },
            capital_flow_payload={
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": 90_000_000,
                        "inflow_5d": 200_000_000,
                        "inflow_10d": 320_000_000,
                    }
                },
            },
        )

        payload = CapitalProfileService(manager=manager).build_stock_profile("600222", stock_name="澶у競鍊肩浉瀵瑰皬娴佸叆")

        self.assertLessEqual(payload["capital_flow_score"], 1)

    def test_build_stock_profile_forwards_capital_flow_budget_seconds(self) -> None:
        manager = _FakeManager(
            history_df=_build_history(),
            quote_payload={
                "price": 12.6,
                "change_pct": 1.2,
                "amount": 320_000_000,
                "turnover_rate": 1.4,
                "total_mv": 22_000_000_000,
            },
            capital_flow_payload={
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": 28_000_000,
                        "inflow_5d": 72_000_000,
                        "inflow_10d": 110_000_000,
                    }
                },
            },
        )

        CapitalProfileService(manager=manager).build_stock_profile(
            "600333",
            capital_flow_budget_seconds=0.35,
        )

        self.assertEqual(manager.capital_flow_budget_seconds, 0.35)


if __name__ == "__main__":
    unittest.main()
