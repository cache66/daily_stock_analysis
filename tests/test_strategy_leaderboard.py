# -*- coding: utf-8 -*-
"""Tests for the strategy leaderboard orchestration helpers."""

import unittest

from scripts.run_strategy_leaderboard import _monthly_breakdown, _window_metrics, build_markdown


class MonthlyBreakdownTestCase(unittest.TestCase):
    def test_groups_by_month_and_counts_flags(self) -> None:
        rows = [
            {"signal_date": "2026-08-04", "stock_return_after_cost_pct": 5.0},
            {"signal_date": "2026-08-05", "stock_return_after_cost_pct": 3.0},
            {"signal_date": "2026-08-06", "stock_return_after_cost_pct": -4.0},
            {"signal_date": "2026-09-01", "stock_return_after_cost_pct": -1.0},
            {"signal_date": "2026-09-02", "stock_return_after_cost_pct": -2.5},
            {"signal_date": "2026-09-03", "stock_return_after_cost_pct": 0.5},  # 中性带内
            {"signal_date": None, "stock_return_after_cost_pct": 9.0},  # 跳过
        ]
        breakdown = _monthly_breakdown(rows, neutral_band_pct=2.0)
        self.assertEqual([item["month"] for item in breakdown], ["2026-08", "2026-09"])

        august = breakdown[0]
        self.assertEqual(august["n"], 3)
        # 赢=2 (>2.0)，输=1 (<-2.0) → 66.67%
        self.assertAlmostEqual(august["win_rate_after_cost_pct"], 66.67, places=2)
        self.assertTrue(august["win_rate_over_half"])
        self.assertTrue(august["mean_positive"])

        september = breakdown[1]
        self.assertEqual(september["n"], 3)
        # 赢=0，输=2（0.5 在中性带内不计）→ 0%
        self.assertEqual(september["win_rate_after_cost_pct"], 0.0)
        self.assertFalse(september["win_rate_over_half"])
        self.assertFalse(september["mean_positive"])


class WindowMetricsTestCase(unittest.TestCase):
    def test_extracts_known_keys(self) -> None:
        report = {
            "window_summaries": [
                {"eval_window_days": 3, "completed_count": 10, "win_rate_after_cost_pct": 55.0},
            ]
        }
        metrics = _window_metrics(report, 3)
        self.assertEqual(metrics["completed_count"], 10)
        self.assertEqual(metrics["win_rate_after_cost_pct"], 55.0)
        self.assertIsNone(metrics["avg_excess_return_after_cost_pct"])
        # 缺失窗口 → 全 None
        self.assertIsNone(_window_metrics(report, 5)["completed_count"])


class MarkdownSmokeTestCase(unittest.TestCase):
    def test_renders_main_table(self) -> None:
        data = {
            "generated_at": "2026-09-26 16:00:00",
            "filters": {
                "start_date": "2026-08-04",
                "end_date": "2026-09-24",
                "windows": [1, 3, 5],
                "stability_window": 3,
                "tradability_filter": "entry",
                "entry_mode": "daily",
                "benchmark_code": "000905",
            },
            "trade_cost_model": {
                "slippage_bps": 10.0,
                "fee_bps": 3.0,
                "turnover_penalty_bps": 5.0,
                "total_trade_cost_bps": 31.0,
            },
            "lines": [
                {
                    "signal_type": "hundred_day_high",
                    "snapshot_count": 100,
                    "windows": {
                        "1": {"avg_stock_return_after_cost_pct": 0.1, "win_rate_after_cost_pct": 50.0},
                        "3": {
                            "avg_stock_return_after_cost_pct": 0.5,
                            "win_rate_after_cost_pct": 52.0,
                            "avg_excess_return_after_cost_pct": -0.1,
                            "beat_benchmark_rate_pct": 47.0,
                        },
                        "5": {"avg_stock_return_after_cost_pct": 1.0, "win_rate_after_cost_pct": 53.0},
                    },
                    "random": {
                        "windows": {
                            "3": {"avg_stock_return_after_cost_pct": -0.05, "win_rate_after_cost_pct": 49.0}
                        }
                    },
                    "monthly": [
                        {
                            "month": "2026-08",
                            "n": 40,
                            "win_rate_after_cost_pct": 55.0,
                            "avg_return_after_cost_pct": 0.8,
                            "win_rate_over_half": True,
                            "mean_positive": True,
                        }
                    ],
                    "portion_positive_months": {"win_rate_over_half": 1, "mean_positive": 1, "total": 1},
                }
            ],
        }
        markdown = build_markdown(data)
        self.assertIn("# 个人策略 Leaderboard v2", markdown)
        self.assertIn("| hundred_day_high | 100 |", markdown)
        self.assertIn("0.5", markdown)
        self.assertIn("### hundred_day_high", markdown)
        self.assertIn("| 2026-08 | 40 | 55.0 | 0.8 | Y | Y |", markdown)


if __name__ == "__main__":
    unittest.main()
