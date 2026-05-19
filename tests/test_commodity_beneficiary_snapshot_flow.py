# -*- coding: utf-8 -*-
"""Tests for commodity beneficiary snapshot persistence flow."""

import os
import sys
import tempfile
import unittest
from datetime import date
import json

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

from scripts.collect_commodity_beneficiary_snapshots import (
    build_criteria_payload,
    build_history_payload,
    build_signal_type,
    persist_selected_candidates,
)
from scripts.select_commodity_beneficiaries import CommodityBeneficiaryCandidate
from src.config import Config
from src.storage import DatabaseManager


class CommodityBeneficiarySnapshotFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_commodity_beneficiary_snapshot.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_build_signal_type_namespaces_commodity_snapshot(self) -> None:
        self.assertEqual(
            build_signal_type("commodity_beneficiary", "optical_fiber"),
            "commodity_beneficiary__optical_fiber",
        )

    def test_persist_selected_candidates_writes_snapshot_and_history(self) -> None:
        signal_type = build_signal_type("commodity_beneficiary", "optical_fiber")
        candidate = CommodityBeneficiaryCandidate(
            stock_code="601869",
            stock_name="长飞光纤",
            commodity_key="optical_fiber",
            theme_key="optical_communication",
            theme_label="optical_communication",
            subtheme_key="preform_and_materials",
            chain_role="upstream",
            stock_role="source_beneficiary",
            pass_through_direction="positive",
            earnings_validation_status="positive",
            earnings_release_probability="high",
            directness="direct_beneficiary",
            summary="direct beneficiary",
            matched_example_bucket="whitelist",
            matched_example_name="长飞光纤",
            warnings=[],
            evidence_points=["e1"],
            scores={"total": 10},
        )
        criteria_payload = build_criteria_payload(
            commodity_key="optical_fiber",
            snapshot_date=date(2026, 4, 10),
            minimum_probability="medium",
            include_distribution=False,
            include_counterexamples=False,
            enable_news_search=False,
            signal_type=signal_type,
        )

        first_df = persist_selected_candidates(
            [candidate],
            signal_type=signal_type,
            snapshot_date=date(2026, 4, 10),
            criteria_payload=criteria_payload,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(len(first_df), 1)

        second_df = persist_selected_candidates(
            [candidate],
            signal_type=signal_type,
            snapshot_date=date(2026, 4, 11),
            criteria_payload=criteria_payload,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(len(second_df), 1)
        self.assertEqual(second_df.iloc[0]["previous_hit_count"], 1)
        self.assertEqual(second_df.iloc[0]["latest_previous_hit_date"], "2026-04-10")

        rows = self.db.get_signal_snapshots(
            signal_type=signal_type,
            code="601869",
            start_date="2026-04-10",
            end_date="2026-04-11",
        )
        self.assertEqual(len(rows), 2)

    def test_build_history_payload_counts_previous_hits(self) -> None:
        signal_type = build_signal_type("commodity_beneficiary", "memory")
        self.db.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date="2026-04-01",
            code="688525",
            name="佰维存储",
            criteria_payload={"signal_type": signal_type},
            metrics_payload={"commodity_key": "memory"},
            history_payload={"previous_hit_count": 0},
        )
        self.db.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date="2026-04-05",
            code="688525",
            name="佰维存储",
            criteria_payload={"signal_type": signal_type},
            metrics_payload={"commodity_key": "memory"},
            history_payload={"previous_hit_count": 1},
        )

        history_payload = build_history_payload(
            self.db,
            signal_type=signal_type,
            stock_code="688525",
            snapshot_date=date(2026, 4, 10),
            lookback_days=365,
        )
        self.assertEqual(history_payload["previous_hit_count"], 2)
        self.assertEqual(history_payload["latest_previous_hit_date"], "2026-04-05")


if __name__ == "__main__":
    unittest.main()


class CommodityBeneficiarySnapshotMetricsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_commodity_beneficiary_snapshot_metrics.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_persist_selected_candidates_keeps_factor_metrics(self) -> None:
        signal_type = build_signal_type("commodity_beneficiary", "optical_fiber")
        candidate = CommodityBeneficiaryCandidate(
            stock_code="601869",
            stock_name="长飞光纤",
            commodity_key="optical_fiber",
            theme_key="optical_communication",
            theme_label="optical_communication",
            subtheme_key="preform_and_materials",
            chain_role="upstream",
            stock_role="source_beneficiary",
            pass_through_direction="positive",
            earnings_validation_status="positive",
            earnings_release_probability="high",
            directness="direct_beneficiary",
            summary="direct beneficiary",
            matched_example_bucket="whitelist",
            matched_example_name="长飞光纤",
            recognizability_score=3,
            sustained_growth_score=2,
            liquidity_score=2,
            valuation_score=1,
            dividend_score=0,
            logic_consensus_score=3,
            capital_consensus_score=2,
            combo_reinforcement_score=3,
            ranking_tuple=[3, 2, 2, 1, 0, 10],
            factor_breakdown={"recognizability": {"score": 3, "label": "very_high", "reasons": ["core"]}},
            warnings=[],
            evidence_points=["e1"],
            scores={"total": 10},
        )
        criteria_payload = build_criteria_payload(
            commodity_key="optical_fiber",
            snapshot_date=date(2026, 4, 10),
            minimum_probability="medium",
            include_distribution=False,
            include_counterexamples=False,
            enable_news_search=False,
            signal_type=signal_type,
        )

        persist_selected_candidates(
            [candidate],
            signal_type=signal_type,
            snapshot_date=date(2026, 4, 10),
            criteria_payload=criteria_payload,
            history_lookback_days=365,
            db=self.db,
        )

        rows = self.db.get_signal_snapshots(
            signal_type=signal_type,
            signal_date="2026-04-10",
            code="601869",
        )
        self.assertEqual(len(rows), 1)
        metrics_payload = json.loads(rows[0].metrics_payload or "{}")
        self.assertEqual(metrics_payload["theme_key"], "optical_communication")
        self.assertEqual(metrics_payload["recognizability_score"], 3)
        self.assertEqual(metrics_payload["logic_consensus_score"], 3)
        self.assertEqual(metrics_payload["capital_consensus_score"], 2)
        self.assertEqual(metrics_payload["combo_reinforcement_score"], 3)
        self.assertEqual(metrics_payload["ranking_tuple"], [3, 2, 2, 1, 0, 10])
