# -*- coding: utf-8 -*-
"""Tests for the signal portfolio (equal-weight rolling) analysis helpers."""

import json
import unittest
from datetime import date
from types import SimpleNamespace

from scripts.analyze_signal_portfolio import (
    _monthly_compounded,
    _select_daily_samples,
    _select_random_samples,
    _total_cost_pct,
    build_markdown,
    run_line,
)


class _FakeBar:
    def __init__(self, day, close, volume=1000.0, high=None, low=None, pct_chg=0.0):
        self.date = day
        self.close = close
        self.volume = volume
        self.high = close if high is None else high
        self.low = close if low is None else low
        self.pct_chg = pct_chg


class _FakeRepo:
    def __init__(self, bars_by_code):
        self._bars = {
            key: sorted(value, key=lambda bar: bar.date)
            for key, value in bars_by_code.items()
        }

    def get_start_daily(self, *, code, analysis_date):
        candidates = [bar for bar in self._bars.get(code, []) if bar.date <= analysis_date]
        return candidates[-1] if candidates else None

    def get_daily_on_date(self, *, code, target_date):
        for bar in self._bars.get(code, []):
            if bar.date == target_date:
                return bar
        return None

    def get_forward_bars(self, *, code, analysis_date, eval_window_days):
        return [
            bar for bar in self._bars.get(code, []) if bar.date > analysis_date
        ][:eval_window_days]

    def get_range(self, *, code, start_date, end_date):
        return [
            bar
            for bar in self._bars.get(code, [])
            if start_date <= bar.date <= end_date
        ]


def _row(code, day, *, overall_score=None, name="", **extra_metrics):
    metrics = {"close": 1.0}
    if overall_score is not None:
        metrics["overall_score"] = overall_score
    metrics.update(extra_metrics)
    return SimpleNamespace(
        code=code,
        name=name,
        signal_date=day,
        metrics_payload=json.dumps(metrics),
    )


D1 = date(2026, 8, 4)
D2 = date(2026, 8, 5)
D3 = date(2026, 8, 6)


def test_select_random_samples_is_seeded_and_independent_of_scores():
    rows = [
        _row(f"{i:06d}", D1, overall_score=float(100 - i), breakout_quality_score=float(i))
        for i in range(10)
    ]
    picked = _select_random_samples(rows, top_n=3)
    assert len(picked) == 3
    assert [r.code for r in picked] == [r.code for r in _select_random_samples(rows, top_n=3)]
    # 种子不同 → 抽取结果不同
    other = _select_random_samples(rows, top_n=3, seed=1)
    assert {r.code for r in other} != {r.code for r in picked}
    # top_n<=0 时保留全部且确定
    assert len(_select_random_samples(rows, top_n=0)) == 10


def _two_code_repo():
    return _FakeRepo(
        {
            "AAA": [_FakeBar(D1, 10.0), _FakeBar(D2, 11.0), _FakeBar(D3, 12.1)],
            "BBB": [_FakeBar(D1, 20.0), _FakeBar(D2, 20.0), _FakeBar(D3, 18.0)],
        }
    )


class EqualWeightSeriesTestCase(unittest.TestCase):
    def test_builds_equal_weight_daily_series_and_metrics(self):
        repo = _two_code_repo()
        samples = [_row("AAA", D1), _row("BBB", D1)]
        result = run_line(
            samples,
            stock_repo=repo,
            window=2,
            cost_pct=0.0,
            tradability_filter="off",
        )
        series = result["series"]
        self.assertEqual([item["date"] for item in series], ["2026-08-05", "2026-08-06"])
        # 08-05: (+10% + 0%) / 2 = 5%；08-06: (+10% + -10%) / 2 = 0%
        self.assertAlmostEqual(series[0]["ret_pct"], 5.0, places=4)
        self.assertAlmostEqual(series[1]["ret_pct"], 0.0, places=4)
        self.assertEqual(series[0]["positions"], 2)
        metrics = result["metrics"]
        self.assertAlmostEqual(metrics["total_return_after_cost_pct"], 5.0, places=4)
        self.assertEqual(metrics["trading_days"], 2)
        self.assertAlmostEqual(metrics["avg_positions"], 2.0, places=2)
        self.assertAlmostEqual(metrics["daily_win_rate_pct"], 50.0, places=2)
        self.assertEqual(result["usable_samples"], 2)

    def test_cost_applied_on_entry_day(self):
        repo = _two_code_repo()
        samples = [_row("AAA", D1), _row("BBB", D1)]
        cost_pct = _total_cost_pct(10.0, 3.0, 5.0)  # 31bps -> 0.31(%)
        self.assertAlmostEqual(cost_pct, 0.31, places=6)
        result = run_line(
            samples,
            stock_repo=repo,
            window=2,
            cost_pct=cost_pct,
            tradability_filter="off",
        )
        self.assertAlmostEqual(result["series"][0]["ret_pct"], 5.0 - 0.31, places=4)
        self.assertAlmostEqual(
            result["metrics"]["total_return_after_cost_pct"], 4.69, places=2
        )

    def test_tradability_filter_skips_limit_up_and_suspended(self):
        repo = _FakeRepo(
            {
                "AAA": [
                    _FakeBar(D1, 10.0, high=10.0, low=10.0, pct_chg=10.0),
                    _FakeBar(D2, 11.0),
                    _FakeBar(D3, 12.1),
                ],
                "BBB": [
                    _FakeBar(D1, 20.0, volume=0.0),
                    _FakeBar(D2, 20.0),
                    _FakeBar(D3, 18.0),
                ],
                "CCC": [_FakeBar(D1, 5.0), _FakeBar(D2, 5.0), _FakeBar(D3, 5.0)],
            }
        )
        samples = [_row("AAA", D1), _row("BBB", D1), _row("CCC", D1)]
        result = run_line(
            samples,
            stock_repo=repo,
            window=2,
            cost_pct=0.0,
            tradability_filter="entry",
        )
        self.assertEqual(result["skipped"]["untradable_entry"], 2)
        self.assertEqual(result["usable_samples"], 1)
        self.assertEqual(result["series"][0]["positions"], 1)


class SelectionTestCase(unittest.TestCase):
    def test_top_n_selection_prefers_higher_score(self):
        samples = [
            _row("AAA", D1, overall_score=90.0),
            _row("BBB", D1, overall_score=50.0),
            _row("CCC", D2, overall_score=10.0),
        ]
        selected = _select_daily_samples(samples, top_n=1)
        self.assertEqual([item.code for item in selected], ["AAA", "CCC"])

    def test_rank_by_uses_custom_metric(self):
        samples = [
            _row("AAA", D1, breakout_quality_score=10.0),
            _row("BBB", D1, breakout_quality_score=99.0),
            _row("CCC", D1, overall_score=90.0),
        ]
        selected = _select_daily_samples(
            samples, top_n=1, rank_by="breakout_quality_score"
        )
        self.assertEqual([item.code for item in selected], ["BBB"])

    def test_duplicate_rows_are_deduplicated(self):
        samples = [_row("AAA", D1), _row("AAA", D1), _row("BBB", D1)]
        selected = _select_daily_samples(samples, top_n=0)
        self.assertEqual(len(selected), 2)


class MonthlyCompoundedTestCase(unittest.TestCase):
    def test_compounds_within_month(self):
        series = [
            {"date": "2026-08-05", "ret_pct": 0.1, "positions": 1},
            {"date": "2026-08-06", "ret_pct": 0.2, "positions": 1},
            {"date": "2026-09-01", "ret_pct": -0.5, "positions": 1},
        ]
        monthly = _monthly_compounded(series)
        self.assertEqual([item["month"] for item in monthly], ["2026-08", "2026-09"])
        self.assertAlmostEqual(monthly[0]["ret_pct"], 0.3, places=2)
        self.assertAlmostEqual(monthly[1]["ret_pct"], -0.5, places=2)


class MarkdownSmokeTestCase(unittest.TestCase):
    def test_markdown_contains_summary_tables(self):
        result = {
            "generated_at": "2026-09-26 18:00:00",
            "filters": {
                "signal_type": "trend_leader_unified",
                "start_date": "2026-08-04",
                "end_date": "2026-09-24",
                "window": 5,
                "top_n": 0,
                "tradability_filter": "entry",
                "entry_mode": "daily",
                "benchmark_code": "000905",
                "slippage_bps": 10.0,
                "fee_bps": 3.0,
                "turnover_penalty_bps": 5.0,
                "total_trade_cost_bps": 31.0,
                "with_random": True,
            },
            "line": {
                "samples": 10,
                "usable_samples": 8,
                "skipped": {
                    "missing_start_price": 1,
                    "untradable_entry": 1,
                    "missing_forward_bars": 0,
                },
                "metrics": {
                    "total_return_after_cost_pct": 3.2,
                    "max_drawdown_after_cost_pct": 2.1,
                    "calmar_ratio_after_cost": 1.52,
                    "trading_days": 30,
                    "avg_positions": 4.5,
                    "daily_win_rate_pct": 55.0,
                    "best_day_pct": 2.0,
                    "worst_day_pct": -1.8,
                },
                "monthly": [{"month": "2026-08", "ret_pct": 1.5}],
                "series": [{"date": "2026-08-05", "ret_pct": 0.1, "positions": 2}],
            },
            "random": None,
            "benchmark": {
                "code": "000905",
                "metrics": {
                    "total_return_after_cost_pct": 1.0,
                    "max_drawdown_after_cost_pct": 3.0,
                    "calmar_ratio_after_cost": 0.33,
                    "trading_days": 30,
                    "avg_positions": 1,
                    "daily_win_rate_pct": 50.0,
                    "best_day_pct": 1.5,
                    "worst_day_pct": -2.0,
                },
                "monthly": [{"month": "2026-08", "ret_pct": 0.5}],
                "series": [],
            },
        }
        markdown = build_markdown(result)
        self.assertIn("# 组合层净值分析：trend_leader_unified", markdown)
        self.assertIn("| 本线 |", markdown)
        self.assertIn("3.2", markdown)
        self.assertIn("| 随机对照 | — | — | — | — | — | — |", markdown)
        self.assertIn("| 基准买持（000905） |", markdown)
        self.assertIn("| 2026-08 | 1.5 | — | 0.5 |", markdown)


if __name__ == "__main__":
    unittest.main()
