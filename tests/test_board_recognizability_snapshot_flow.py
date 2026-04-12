# -*- coding: utf-8 -*-
"""Tests for board recognizability ranking collection and persistence."""

import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.collect_board_recognizability_rankings import (
    DEFAULT_SIGNAL_TYPE_PREFIX,
    BoardRecognizabilityCandidate,
    build_signal_type,
    collect_board_rankings,
    persist_board_rankings,
)
from src.config import Config
from src.storage import DatabaseManager


class BoardRecognizabilitySnapshotFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_board_recognizability.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_build_signal_type_is_stable_and_bounded(self) -> None:
        signal_type = build_signal_type("半导体", prefix=DEFAULT_SIGNAL_TYPE_PREFIX)
        self.assertEqual(signal_type, build_signal_type("半导体", prefix=DEFAULT_SIGNAL_TYPE_PREFIX))
        self.assertTrue(signal_type.startswith(f"{DEFAULT_SIGNAL_TYPE_PREFIX}__"))
        self.assertLessEqual(len(signal_type), 64)

    def test_collect_board_rankings_groups_and_orders_candidates(self) -> None:
        rows = [
            {
                "code": "688001",
                "name": "芯片A",
                "metrics_payload": {
                    "total_market_cap": 10_000_000_000,
                    "latest_high": 50.0,
                    "close": 48.0,
                },
                "cause_payload": {
                    "industry": "半导体",
                    "reason_summary": "A",
                },
                "history_payload": {
                    "previous_hit_count": 2,
                    "days_since_previous_hit": 1,
                },
            },
            {
                "code": "688002",
                "name": "芯片B",
                "metrics_payload": {
                    "total_market_cap": 30_000_000_000,
                    "latest_high": 80.0,
                    "close": 78.0,
                },
                "cause_payload": {
                    "industry": "半导体",
                    "reason_summary": "B",
                },
                "history_payload": {
                    "previous_hit_count": 1,
                    "days_since_previous_hit": 2,
                },
            },
            {
                "code": "000001",
                "name": "银行A",
                "metrics_payload": {
                    "total_market_cap": 50_000_000_000,
                    "latest_high": 12.0,
                    "close": 11.8,
                },
                "cause_payload": {
                    "industry": "银行",
                    "reason_summary": "C",
                },
                "history_payload": {
                    "previous_hit_count": 0,
                },
            },
        ]
        for row in rows:
            self.db.upsert_signal_snapshot(
                signal_type="hundred_day_high",
                signal_date="2026-04-09",
                code=row["code"],
                name=row["name"],
                criteria_payload={"signal_type": "hundred_day_high"},
                metrics_payload=row["metrics_payload"],
                cause_payload=row["cause_payload"],
                history_payload=row["history_payload"],
            )

        resolved_date, selected, board_sizes = collect_board_rankings(
            db=self.db,
            source_signal_type="hundred_day_high",
            snapshot_date=date(2026, 4, 9),
            top_n=2,
        )

        self.assertEqual(resolved_date, date(2026, 4, 9))
        self.assertEqual(board_sizes["半导体"], 2)
        half_board = [item for item in selected if item.board_name == "半导体"]
        self.assertEqual(len(half_board), 2)
        self.assertEqual(half_board[0].stock_code, "688001")
        self.assertEqual(half_board[0].board_rank, 1)
        self.assertEqual(half_board[1].stock_code, "688002")

    def test_persist_board_rankings_uses_board_namespaced_signal_types(self) -> None:
        candidate = BoardRecognizabilityCandidate(
            board_name="半导体",
            stock_code="688001",
            stock_name="芯片A",
            source_signal_type="hundred_day_high",
            source_signal_date="2026-04-09",
            previous_hit_count=2,
            days_since_previous_hit=1,
            is_consecutive_signal=True,
            total_market_cap=10_000_000_000,
            total_market_cap_yi=100.0,
            close=48.0,
            latest_high=50.0,
            board_rank=1,
            board_candidate_count=3,
            source_reason_summary="半导体强势",
        )

        first_df = persist_board_rankings(
            [candidate],
            snapshot_date=date(2026, 4, 9),
            signal_type_prefix=DEFAULT_SIGNAL_TYPE_PREFIX,
            top_n=3,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(len(first_df), 1)

        second_df = persist_board_rankings(
            [candidate],
            snapshot_date=date(2026, 4, 10),
            signal_type_prefix=DEFAULT_SIGNAL_TYPE_PREFIX,
            top_n=3,
            history_lookback_days=365,
            db=self.db,
        )
        self.assertEqual(second_df.iloc[0]["ranking_previous_hit_count"], 1)
        self.assertEqual(second_df.iloc[0]["latest_previous_hit_date"], "2026-04-09")

        signal_type = build_signal_type("半导体", prefix=DEFAULT_SIGNAL_TYPE_PREFIX)
        rows = self.db.get_signal_snapshots(signal_type=signal_type, code="688001", days=10)
        self.assertEqual(len(rows), 2)
