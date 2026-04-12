# -*- coding: utf-8 -*-
"""
Tests for the isolated K-line selector service.
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.services.kline_selector_service import (  # noqa: E402
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectionEvaluation,
    KlineSelectorService,
)


def _round_price_to_tick(value: float) -> float:
    return math.floor(value * 100 + 0.5) / 100.0


def build_history(with_limit_up: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2025-09-01", periods=120)
    closes = []
    close_value = 10.0
    for _ in range(110):
        closes.append(round(close_value, 2))
        close_value += 0.09

    recent_closes = []
    prev_close = closes[-1]
    if with_limit_up:
        recent_closes.append(_round_price_to_tick(prev_close * 1.10))
    else:
        recent_closes.append(round(prev_close + 0.30, 2))
    recent_closes.extend([22.20, 22.00, 22.40, 22.80, 22.70, 23.10, 23.40, 23.80, 24.20])
    closes.extend(recent_closes)

    highs = closes.copy()
    highs[-1] = closes[-1] + 0.25
    opens = [round(c * 0.99, 2) for c in closes]
    lows = [round(c * 0.98, 2) for c in closes]
    pct_chg = [None]
    for index in range(1, len(closes)):
        prev = closes[index - 1]
        pct_chg.append(round((closes[index] - prev) / prev * 100, 2))

    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "pct_chg": pct_chg,
        }
    )


class DummyQuote:
    def __init__(self, total_mv):
        self.total_mv = total_mv


class FakeManager:
    def __init__(self, history_by_code=None, quote_caps=None):
        self.history_by_code = history_by_code or {}
        self.quote_caps = quote_caps or {}
        self.history_calls = []

    def get_daily_data(self, stock_code: str, days: int):
        self.history_calls.append((stock_code, days))
        return self.history_by_code.get(stock_code, pd.DataFrame()), "fake"

    def get_realtime_quote(self, stock_code: str):
        return DummyQuote(self.quote_caps.get(stock_code))


class TestKlineSelectorService(unittest.TestCase):
    def test_get_a_share_universe_filters_bse_and_duplicates(self):
        service = KlineSelectorService(
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600519", "920748", "000001", "600519"],
                    "name": ["alpha", "bse_sample", "pingan", "alpha_dup"],
                    "total_mv": [200e9, 20e9, 120e9, 200e9],
                }
            )
        )

        universe = service.get_a_share_universe()

        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(universe["name"].tolist(), ["pingan", "alpha"])

    def test_get_a_share_universe_filters_indices_and_etfs(self):
        service = KlineSelectorService(
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["000001", "510300", "600519", "399001", "301236"],
                    "name": ["上证综合指数", "沪深300ETF", "贵州茅台", "深证成指", "软通动力"],
                    "total_mv": [None, None, 200e9, None, 30e9],
                }
            )
        )

        universe = service.get_a_share_universe()

        self.assertEqual(universe["code"].tolist(), ["301236", "600519"])
        self.assertEqual(universe["name"].tolist(), ["软通动力", "贵州茅台"])

    def test_evaluate_stock_passes_all_rules(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600001": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria()

        evaluation = service.evaluate_stock(
            stock_code="600001",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.metrics["up_days"], 8)
        self.assertEqual(evaluation.metrics["lookback_days"], 10)
        self.assertEqual(evaluation.metrics["up_ratio"], 0.8)
        self.assertTrue(evaluation.metrics["recent_limit_up_dates"])
        self.assertEqual(evaluation.metrics["new_high_window"], 100)
        self.assertEqual(evaluation.total_market_cap, 40e9)

    def test_evaluate_stock_fails_without_recent_limit_up(self):
        history = build_history(with_limit_up=False)
        manager = FakeManager(history_by_code={"600002": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria()

        evaluation = service.evaluate_stock(
            stock_code="600002",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertFalse(evaluation.passed)
        self.assertIn("limit-up", evaluation.failure_reason)

    def test_build_rules_supports_standalone_hundred_day_high_strategy(self):
        service = KlineSelectorService(manager=FakeManager())
        criteria = KlineSelectorCriteria(
            require_up_day_ratio=False,
            require_recent_limit_up=False,
            require_new_high=True,
        )

        rule_names = [rule.name for rule in service.build_rules(criteria)]

        self.assertEqual(rule_names, ["max_market_cap", "hundred_day_high"])

    def test_evaluate_stock_can_pass_with_only_hundred_day_high_rule(self):
        history = build_history(with_limit_up=False)
        manager = FakeManager(history_by_code={"600012": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria(
            require_up_day_ratio=False,
            require_recent_limit_up=False,
            require_new_high=True,
        )

        evaluation = service.evaluate_stock(
            stock_code="600012",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertTrue(evaluation.passed)
        self.assertEqual(set(evaluation.rule_results.keys()), {"max_market_cap", "hundred_day_high"})

    def test_scan_market_prefilters_large_caps_before_history_fetch(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600003": history, "600004": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600003", "600004"],
                    "name": ["large_cap", "selected"],
                    "total_mv": [600e8, 40e9],
                }
            ),
        )
        criteria = KlineSelectorCriteria(max_total_market_cap=50e9)

        run_result = service.scan_market(criteria=criteria)

        self.assertEqual(run_result.universe_size, 2)
        self.assertEqual(run_result.skipped_market_cap_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual([item.stock_code for item in run_result.selected], ["600004"])
        self.assertEqual(manager.history_calls, [("600004", criteria.history_days_required)])

    def test_scan_market_supports_parallel_manager_factory(self):
        history = build_history(with_limit_up=True)
        created_managers = []

        def manager_factory():
            manager = FakeManager(
                history_by_code={
                    "600005": history,
                    "600006": history,
                }
            )
            created_managers.append(manager)
            return manager

        service = KlineSelectorService(
            manager_factory=manager_factory,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600005", "600006"],
                    "name": ["parallel_a", "parallel_b"],
                    "total_mv": [40e9, 41e9],
                }
            ),
        )

        run_result = service.scan_market(criteria=KlineSelectorCriteria(), max_workers=2)

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(
            sorted(item.stock_code for item in run_result.selected),
            ["600005", "600006"],
        )
        self.assertGreaterEqual(len(created_managers), 1)
        self.assertEqual(sum(len(manager.history_calls) for manager in created_managers), 2)

    def test_scan_market_prefilter_reduces_history_fetches(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600007": history})
        criteria = KlineSelectorCriteria()
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600007", "600008"],
                    "name": ["prefilter_hit", "prefilter_skip"],
                    "total_mv": [40e9, 41e9],
                    "change_pct_60d": [15.0, 3.0],
                }
            ),
        )

        run_result = service.scan_market(
            criteria=criteria,
            prefilter=KlineSelectorPrefilter(min_change_pct_60d=10.0),
        )

        self.assertEqual(run_result.skipped_prefilter_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600007", criteria.history_days_required)])

    def test_scan_market_invokes_on_evaluation_callback(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600070": history, "600071": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600070", "600071"],
                    "name": ["callback_a", "callback_b"],
                    "total_mv": [40e9, 41e9],
                }
            ),
        )
        callback_events = []

        run_result = service.scan_market(
            criteria=KlineSelectorCriteria(),
            on_evaluation=lambda evaluation, completed, total: callback_events.append(
                (evaluation.stock_code, evaluation.passed, completed, total)
            ),
        )

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(len(callback_events), 2)
        self.assertEqual({event[0] for event in callback_events}, {"600070", "600071"})
        self.assertTrue(all(event[1] for event in callback_events))
        self.assertTrue(all(event[3] == 2 for event in callback_events))

    def test_apply_universe_shard_slices_codes_deterministically(self):
        universe = pd.DataFrame(
            {
                "code": ["000001", "000002", "000003", "000004", "000005"],
                "name": ["a", "b", "c", "d", "e"],
                "total_mv": [10e9, 11e9, 12e9, 13e9, 14e9],
            }
        )

        shard = KlineSelectorService.apply_universe_shard(universe, shard_count=3, shard_index=1)

        self.assertEqual(shard["code"].tolist(), ["000002", "000005"])

    def test_scan_market_only_evaluates_the_requested_shard(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(
            history_by_code={
                "600100": history,
                "600101": history,
                "600102": history,
                "600103": history,
            }
        )
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600100", "600101", "600102", "600103"],
                    "name": ["a", "b", "c", "d"],
                    "total_mv": [40e9, 41e9, 42e9, 43e9],
                }
            ),
        )

        run_result = service.scan_market(
            criteria=KlineSelectorCriteria(),
            shard_count=2,
            shard_index=1,
        )
        history_days_required = KlineSelectorCriteria().history_days_required

        self.assertEqual(run_result.universe_codes, ["600101", "600103"])
        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(
            manager.history_calls,
            [
                ("600101", history_days_required),
                ("600103", history_days_required),
            ],
        )

    def test_scan_market_resume_uses_checkpoint(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600010": history, "600011": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600010", "600011"],
                    "name": ["resume_a", "resume_b"],
                    "total_mv": [40e9, 41e9],
                    "change_pct_60d": [15.0, 15.0],
                }
            ),
        )
        criteria = KlineSelectorCriteria()
        prefilter = KlineSelectorPrefilter(min_change_pct_60d=10.0)

        partial_selected = KlineSelectionEvaluation(
            stock_code="600010",
            stock_name="resume_a",
            passed=True,
            history_source="fake",
            total_market_cap=40e9,
            metrics={
                "up_days": 8,
                "lookback_days": 10,
                "up_ratio": 0.8,
                "recent_limit_up_dates": ["2025-12-01"],
                "latest_high": 24.45,
                "window_high": 24.45,
                "new_high_window": 100,
            },
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "resume.json"
            service._save_checkpoint(
                checkpoint_path=checkpoint_path,
                criteria=criteria,
                prefilter=prefilter,
                universe=service.get_a_share_universe(),
                skipped_market_cap_count=0,
                skipped_prefilter_count=0,
                selected=[partial_selected],
                failed=[],
            )

            run_result = service.scan_market(
                criteria=criteria,
                prefilter=prefilter,
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(sorted(item.stock_code for item in run_result.selected), ["600010", "600011"])
        self.assertEqual(manager.history_calls, [("600011", criteria.history_days_required)])

    def test_scan_market_resume_keeps_skip_counts_stable(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600021": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600020", "600021", "600022"],
                    "name": ["large_cap", "resume_target", "prefilter_skip"],
                    "total_mv": [600e8, 40e9, 41e9],
                    "change_pct_60d": [20.0, 15.0, 3.0],
                }
            ),
        )
        criteria = KlineSelectorCriteria(max_total_market_cap=50e9)
        prefilter = KlineSelectorPrefilter(min_change_pct_60d=10.0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "resume_skip_counts.json"
            service._save_checkpoint(
                checkpoint_path=checkpoint_path,
                criteria=criteria,
                prefilter=prefilter,
                universe=service.get_a_share_universe(),
                skipped_market_cap_count=1,
                skipped_prefilter_count=1,
                selected=[],
                failed=[],
            )

            run_result = service.scan_market(
                criteria=criteria,
                prefilter=prefilter,
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(run_result.skipped_market_cap_count, 1)
        self.assertEqual(run_result.skipped_prefilter_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600021", criteria.history_days_required)])


if __name__ == "__main__":
    unittest.main()
