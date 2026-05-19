# -*- coding: utf-8 -*-
"""Tests for dragon-head snapshot persistence flow."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.collect_dragon_head_snapshots import (
    DEFAULT_SIGNAL_TYPE,
    build_criteria_payload,
    build_history_payload,
    persist_selected_candidates,
)
from scripts.select_dragon_head_candidates import DragonHeadCandidate
from src.config import Config
from src.storage import DatabaseManager


class DragonHeadSnapshotFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_dragon_head_snapshot.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_persist_selected_candidates_writes_snapshot_and_history(self) -> None:
        candidate = DragonHeadCandidate(
            stock_code="600001",
            stock_name="混合龙头",
            leader_probability="high",
            leader_type="hybrid_leader",
            recognizability_score=3,
            logic_consensus_score=3,
            capital_consensus_score=2,
            sector_leadership_score=3,
            relative_strength_score=2,
            liquidity_score=2,
            catalyst_score=2,
            ranking_tuple=[3, 3, 2, 2, 2, 8],
            factor_breakdown={"recognizability": {"score": 3, "label": "very_high"}},
            summary="hybrid leader",
            warnings=[],
            evidence_points=["e1"],
        )
        criteria_payload = build_criteria_payload(
            snapshot_date=date(2026, 4, 11),
            minimum_probability="medium",
            include_pseudo_leaders=False,
            enable_news_search=False,
            signal_type=DEFAULT_SIGNAL_TYPE,
        )

        first_df = persist_selected_candidates(
            [candidate],
            signal_type=DEFAULT_SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 11),
            criteria_payload=criteria_payload,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(len(first_df), 1)

        second_df = persist_selected_candidates(
            [candidate],
            signal_type=DEFAULT_SIGNAL_TYPE,
            snapshot_date=date(2026, 4, 12),
            criteria_payload=criteria_payload,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(second_df.iloc[0]["previous_hit_count"], 1)
        self.assertEqual(second_df.iloc[0]["latest_previous_hit_date"], "2026-04-11")

        rows = self.db.get_signal_snapshots(
            signal_type=DEFAULT_SIGNAL_TYPE,
            code="600001",
            start_date="2026-04-11",
            end_date="2026-04-12",
        )
        self.assertEqual(len(rows), 2)
        metrics_payload = json.loads(rows[0].metrics_payload or "{}")
        self.assertEqual(metrics_payload["leader_type"], "hybrid_leader")
        self.assertEqual(metrics_payload["recognizability_score"], 3)
        self.assertEqual(metrics_payload["sector_leadership_score"], 3)

    def test_build_history_payload_counts_previous_hits(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type=DEFAULT_SIGNAL_TYPE,
            signal_date="2026-04-01",
            code="600002",
            name="资金龙头",
            criteria_payload={"signal_type": DEFAULT_SIGNAL_TYPE},
            metrics_payload={"leader_type": "capital_leader"},
            history_payload={"previous_hit_count": 0},
        )
        self.db.upsert_signal_snapshot(
            signal_type=DEFAULT_SIGNAL_TYPE,
            signal_date="2026-04-05",
            code="600002",
            name="资金龙头",
            criteria_payload={"signal_type": DEFAULT_SIGNAL_TYPE},
            metrics_payload={"leader_type": "capital_leader"},
            history_payload={"previous_hit_count": 1},
        )

        history_payload = build_history_payload(
            self.db,
            signal_type=DEFAULT_SIGNAL_TYPE,
            stock_code="600002",
            snapshot_date=date(2026, 4, 11),
            lookback_days=365,
        )
        self.assertEqual(history_payload["previous_hit_count"], 2)
        self.assertEqual(history_payload["latest_previous_hit_date"], "2026-04-05")
