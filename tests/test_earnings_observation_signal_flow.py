# -*- coding: utf-8 -*-
"""Tests for earnings observation lifecycle snapshot building."""

import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from scripts.collect_earnings_observation_snapshots import (
    ACTIVE_SIGNAL_TYPE,
    REGISTRY_SIGNAL_TYPE,
    _evaluate_trend_states,
    build_observation_snapshots,
)


class EarningsObservationSignalFlowTestCase(unittest.TestCase):
    def test_evaluate_trend_states_uses_fast_a_share_manager(self) -> None:
        fast_manager = object()

        with patch(
            "scripts.collect_earnings_observation_snapshots.KlineSelectorService.build_fast_a_share_manager",
            return_value=fast_manager,
        ) as build_fast_manager, patch(
            "scripts.collect_earnings_observation_snapshots._evaluate_single_trend_state",
            side_effect=lambda manager, *, code, **_: {
                "manager_is_fast": manager is fast_manager,
                "code": code,
            },
        ) as evaluate_single:
            states = _evaluate_trend_states(["600519", "000001", "600519", ""])

        build_fast_manager.assert_called_once_with()
        self.assertEqual(evaluate_single.call_count, 2)
        self.assertEqual(states["000001"]["code"], "000001")
        self.assertEqual(states["600519"]["code"], "600519")
        self.assertTrue(states["000001"]["manager_is_fast"])
        self.assertTrue(states["600519"]["manager_is_fast"])

    def test_build_observation_snapshots_initializes_active_watch_from_good_quarter(self) -> None:
        registry_rows, active_rows = build_observation_snapshots(
            snapshot_date=date(2026, 4, 24),
            previous_registry=[],
            earnings_evaluations_by_code={
                "600519": {
                    "code": "600519",
                    "name": "贵州茅台",
                    "passed": True,
                    "event_date": "2026-04-20",
                    "report_date": "2026-03-31",
                    "reason_summary": "季报通过 balanced 门槛",
                    "earnings_strategy_score": 68.0,
                },
            },
            trend_states_by_code={
                "600519": {
                    "passed": True,
                    "close": 1820.0,
                    "ma20": 1760.0,
                    "ma60": 1650.0,
                    "distance_to_high_pct": 2.3,
                    "trend_score": 92.0,
                },
            },
            max_observation_days=240,
        )

        self.assertEqual(len(registry_rows), 1)
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(registry_rows[0]["signal_type"], REGISTRY_SIGNAL_TYPE)
        self.assertEqual(active_rows[0]["signal_type"], ACTIVE_SIGNAL_TYPE)
        self.assertEqual(registry_rows[0]["code"], "600519")
        self.assertEqual(registry_rows[0]["metrics_payload"]["status"], "active")
        self.assertEqual(registry_rows[0]["metrics_payload"]["bad_quarter_streak"], 0)
        self.assertEqual(registry_rows[0]["history_payload"]["first_watch_date"], "2026-04-24")
        self.assertEqual(registry_rows[0]["history_payload"]["last_qualified_earnings_date"], "2026-04-20")

    def test_build_observation_snapshots_keeps_registry_but_drops_active_when_trend_turns_weak(self) -> None:
        registry_rows, active_rows = build_observation_snapshots(
            snapshot_date=date(2026, 5, 6),
            previous_registry=[
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "metrics_payload": {
                        "status": "active",
                        "bad_quarter_streak": 0,
                    },
                    "history_payload": {
                        "first_watch_date": "2026-04-24",
                        "last_active_date": "2026-04-30",
                        "last_qualified_earnings_date": "2026-04-20",
                    },
                }
            ],
            earnings_evaluations_by_code={},
            trend_states_by_code={
                "600519": {
                    "passed": False,
                    "close": 1700.0,
                    "ma20": 1750.0,
                    "ma60": 1680.0,
                    "distance_to_high_pct": 11.8,
                    "trend_score": 24.0,
                },
            },
            max_observation_days=240,
        )

        self.assertEqual(len(registry_rows), 1)
        self.assertEqual(active_rows, [])
        self.assertEqual(registry_rows[0]["metrics_payload"]["status"], "inactive")
        self.assertEqual(registry_rows[0]["history_payload"]["previous_status"], "active")

    def test_build_observation_snapshots_reactivates_inactive_watch_when_trend_recovers(self) -> None:
        registry_rows, active_rows = build_observation_snapshots(
            snapshot_date=date(2026, 5, 8),
            previous_registry=[
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "metrics_payload": {
                        "status": "inactive",
                        "bad_quarter_streak": 0,
                    },
                    "history_payload": {
                        "first_watch_date": "2026-04-24",
                        "last_active_date": "2026-04-30",
                        "last_qualified_earnings_date": "2026-04-20",
                    },
                }
            ],
            earnings_evaluations_by_code={},
            trend_states_by_code={
                "600519": {
                    "passed": True,
                    "close": 1810.0,
                    "ma20": 1768.0,
                    "ma60": 1665.0,
                    "distance_to_high_pct": 1.7,
                    "trend_score": 88.0,
                },
            },
            max_observation_days=240,
        )

        self.assertEqual(len(active_rows), 1)
        self.assertEqual(registry_rows[0]["metrics_payload"]["status"], "active")
        self.assertEqual(registry_rows[0]["history_payload"]["previous_status"], "inactive")
        self.assertEqual(registry_rows[0]["history_payload"]["last_active_date"], "2026-05-08")

    def test_build_observation_snapshots_removes_name_after_two_bad_quarters(self) -> None:
        registry_rows, active_rows = build_observation_snapshots(
            snapshot_date=date(2026, 8, 31),
            previous_registry=[
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "metrics_payload": {
                        "status": "inactive",
                        "bad_quarter_streak": 1,
                    },
                    "history_payload": {
                        "first_watch_date": "2026-04-24",
                        "last_active_date": "2026-05-08",
                        "last_qualified_earnings_date": "2026-04-20",
                        "last_report_period": "2026-03-31",
                    },
                }
            ],
            earnings_evaluations_by_code={
                "600519": {
                    "code": "600519",
                    "name": "贵州茅台",
                    "passed": False,
                    "event_date": "2026-08-30",
                    "report_date": "2026-06-30",
                    "reason_summary": "新季度未通过 balanced 门槛",
                    "earnings_strategy_score": 22.0,
                },
            },
            trend_states_by_code={
                "600519": {
                    "passed": True,
                    "close": 1760.0,
                    "ma20": 1730.0,
                    "ma60": 1660.0,
                    "distance_to_high_pct": 4.0,
                    "trend_score": 80.0,
                },
            },
            max_observation_days=240,
        )

        self.assertEqual(active_rows, [])
        self.assertEqual(registry_rows[0]["metrics_payload"]["status"], "removed")
        self.assertEqual(registry_rows[0]["metrics_payload"]["bad_quarter_streak"], 2)
        self.assertEqual(
            registry_rows[0]["history_payload"]["removal_reason"],
            "two_consecutive_bad_quarters",
        )
