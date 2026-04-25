# -*- coding: utf-8 -*-
"""Tests for signal snapshot performance evaluation script."""

import os
import tempfile
import unittest
from datetime import date

from scripts.evaluate_signal_snapshot_performance import build_report, parse_eval_windows
from src.config import Config
from src.storage import DatabaseManager, StockDaily


class SignalSnapshotPerformanceReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_snapshot_performance.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_snapshot(
        self,
        *,
        signal_date: str,
        code: str,
        close: float,
        latest_high: float,
        profile_name: str = "breakout_balanced",
        score_value: float | None = None,
    ) -> None:
        metrics_payload = {
            "close": close,
            "latest_high": latest_high,
            "window_high": latest_high,
            "new_high_window": 100,
        }
        if score_value is not None:
            metrics_payload["overall_score"] = score_value
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date=signal_date,
            code=code,
            name=f"name_{code}",
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": profile_name},
            metrics_payload=metrics_payload,
            history_payload={"previous_hit_count": 0},
        )

    def _seed_daily_bars(self, *, code: str, rows: list[tuple[date, float, float, float]]) -> None:
        with self.db.session_scope() as session:
            for bar_date, high, low, close in rows:
                session.add(
                    StockDaily(
                        code=code,
                        date=bar_date,
                        high=high,
                        low=low,
                        close=close,
                    )
                )

    def test_parse_eval_windows_deduplicates_and_preserves_order(self) -> None:
        self.assertEqual(parse_eval_windows("1, 3,3,5"), [1, 3, 5])

    def test_build_report_summarizes_forward_returns_by_window(self) -> None:
        self._seed_snapshot(signal_date="2026-04-01", code="600001", close=10.0, latest_high=10.2)
        self._seed_snapshot(signal_date="2026-04-02", code="600002", close=20.0, latest_high=20.4)

        self._seed_daily_bars(
            code="600001",
            rows=[
                (date(2026, 4, 2), 11.5, 10.8, 11.0),
                (date(2026, 4, 3), 12.5, 11.5, 12.0),
                (date(2026, 4, 4), 13.0, 11.9, 12.5),
            ],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[
                (date(2026, 4, 3), 20.4, 19.2, 19.5),
            ],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-02",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1, 3],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 2)
        self.assertEqual(len(report["window_summaries"]), 2)

        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["eval_window_days"], 1)
        self.assertEqual(one_day["completed_count"], 2)
        self.assertEqual(one_day["insufficient_count"], 0)
        self.assertEqual(one_day["win_rate_pct"], 50.0)
        self.assertAlmostEqual(one_day["avg_stock_return_pct"], 3.75)
        self.assertAlmostEqual(one_day["median_stock_return_pct"], 3.75)
        self.assertAlmostEqual(one_day["avg_max_runup_pct"], 8.5)
        self.assertAlmostEqual(one_day["avg_worst_drawdown_pct"], 2.0)
        self.assertEqual(one_day["best_cases"][0]["code"], "600001")
        self.assertEqual(one_day["worst_cases"][0]["code"], "600002")

        three_day = report["window_summaries"][1]
        self.assertEqual(three_day["eval_window_days"], 3)
        self.assertEqual(three_day["completed_count"], 1)
        self.assertEqual(three_day["insufficient_count"], 1)
        self.assertEqual(three_day["win_rate_pct"], 100.0)
        self.assertAlmostEqual(three_day["avg_stock_return_pct"], 25.0)
        self.assertAlmostEqual(three_day["median_stock_return_pct"], 25.0)
        self.assertAlmostEqual(three_day["avg_max_runup_pct"], 30.0)
        self.assertAlmostEqual(three_day["avg_worst_drawdown_pct"], 8.0)

    def test_build_report_can_filter_by_profile_name(self) -> None:
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600001",
            close=10.0,
            latest_high=10.2,
            profile_name="breakout_balanced",
        )
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600002",
            close=20.0,
            latest_high=20.4,
            profile_name="momentum_strict",
        )
        self._seed_daily_bars(
            code="600001",
            rows=[(date(2026, 4, 2), 10.8, 10.0, 10.5)],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[(date(2026, 4, 2), 22.0, 20.0, 21.0)],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name="momentum_strict",
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 1)
        self.assertEqual(report["filters"]["profile_name"], "momentum_strict")
        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["completed_count"], 1)
        self.assertEqual(one_day["best_cases"][0]["code"], "600002")

    def test_build_report_supports_cost_adjusted_returns_and_score_buckets(self) -> None:
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600001",
            close=10.0,
            latest_high=10.2,
            score_value=72.0,
        )
        self._seed_snapshot(
            signal_date="2026-04-02",
            code="600002",
            close=20.0,
            latest_high=20.4,
            score_value=88.0,
        )
        self._seed_daily_bars(
            code="600001",
            rows=[(date(2026, 4, 2), 11.2, 10.2, 11.0)],
        )
        self._seed_daily_bars(
            code="600002",
            rows=[(date(2026, 4, 3), 20.4, 18.8, 19.0)],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-02",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
            slippage_bps=20.0,
            fee_bps=30.0,
            turnover_penalty_bps=0.0,
            score_bucket_edges=[0.0, 60.0, 80.0, 100.0],
        )

        one_day = report["window_summaries"][0]
        self.assertAlmostEqual(one_day["avg_stock_return_pct"], 2.5)
        self.assertAlmostEqual(one_day["avg_stock_return_after_cost_pct"], 1.5)
        self.assertAlmostEqual(one_day["median_stock_return_after_cost_pct"], 1.5)
        self.assertAlmostEqual(one_day["profit_factor_after_cost"], 1.5)
        self.assertAlmostEqual(one_day["max_drawdown_after_cost_pct"], 6.0)
        self.assertIsNotNone(one_day["calmar_ratio_after_cost"])

        buckets = one_day["score_bucket_summary"]
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0]["bucket"], "60-80")
        self.assertEqual(buckets[0]["completed_count"], 1)
        self.assertEqual(buckets[1]["bucket"], "80-100")
        self.assertEqual(buckets[1]["completed_count"], 1)

    def test_build_report_skips_run_summary_snapshot_rows(self) -> None:
        self._seed_snapshot(
            signal_date="2026-04-01",
            code="600001",
            close=10.0,
            latest_high=10.2,
        )
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-01",
            code="TL_SUMMARY",
            name="run-summary",
            criteria_payload={"scope": "summary"},
            metrics_payload={"is_run_summary": True, "selected_count": 0},
            history_payload={"previous_hit_count": 0},
        )
        self._seed_daily_bars(
            code="600001",
            rows=[(date(2026, 4, 2), 10.8, 10.0, 10.5)],
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 1)
        self.assertEqual(report["window_summaries"][0]["completed_count"], 1)

    def test_build_report_handles_missing_start_price_without_crash(self) -> None:
        self.db.upsert_signal_snapshot(
            signal_type="hundred_day_high",
            signal_date="2026-04-01",
            code="600009",
            name="name_600009",
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": "breakout_balanced"},
            metrics_payload={"latest_high": 10.2},
            history_payload={"previous_hit_count": 0},
        )

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1],
            neutral_band_pct=2.0,
            detail_limit=2,
        )

        self.assertEqual(report["snapshot_count"], 1)
        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["completed_count"], 0)
        self.assertEqual(one_day["insufficient_count"], 1)
        self.assertEqual(one_day["diagnostics"]["first_hit"].get("(none)"), 1)

    def test_build_report_can_fill_missing_forward_bars_via_filler_callback(self) -> None:
        self._seed_snapshot(signal_date="2026-04-01", code="600010", close=10.0, latest_high=10.5)
        self._seed_daily_bars(
            code="600010",
            rows=[
                (date(2026, 4, 2), 10.8, 10.2, 10.6),
            ],
        )
        # Ensure market-level trading-day horizon is reachable for a 3-day window.
        self._seed_daily_bars(
            code="600099",
            rows=[
                (date(2026, 4, 3), 10.2, 9.9, 10.0),
                (date(2026, 4, 4), 10.3, 10.0, 10.1),
            ],
        )

        call_counter = {"count": 0}

        def _fill_missing_daily_data(*, code: str, analysis_date: date, eval_window_days: int, stock_repo) -> bool:
            self.assertEqual(code, "600010")
            self.assertEqual(analysis_date.isoformat(), "2026-04-01")
            self.assertEqual(eval_window_days, 3)
            call_counter["count"] += 1
            self._seed_daily_bars(
                code=code,
                rows=[
                    (date(2026, 4, 3), 11.1, 10.4, 10.9),
                    (date(2026, 4, 4), 11.4, 10.8, 11.2),
                ],
            )
            return True

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[3],
            neutral_band_pct=2.0,
            detail_limit=2,
            fill_missing_daily_data=True,
            missing_daily_data_filler=_fill_missing_daily_data,
        )

        self.assertEqual(call_counter["count"], 1)
        one_day = report["window_summaries"][0]
        self.assertEqual(one_day["completed_count"], 1)
        self.assertEqual(one_day["insufficient_count"], 0)

    def test_build_report_uses_single_fill_attempt_per_code_date_across_windows(self) -> None:
        self._seed_snapshot(signal_date="2026-04-01", code="600011", close=10.0, latest_high=10.5)
        self._seed_daily_bars(
            code="600011",
            rows=[
                (date(2026, 4, 2), 10.8, 10.2, 10.6),
            ],
        )
        # Ensure market-level trading-day horizon is reachable for the max 5-day window.
        self._seed_daily_bars(
            code="600099",
            rows=[
                (date(2026, 4, 3), 10.2, 9.9, 10.0),
                (date(2026, 4, 4), 10.3, 10.0, 10.1),
                (date(2026, 4, 5), 10.4, 10.1, 10.2),
                (date(2026, 4, 6), 10.5, 10.2, 10.3),
            ],
        )

        call_state = {"count": 0, "windows": []}

        def _fill_missing_daily_data(*, code: str, analysis_date: date, eval_window_days: int, stock_repo) -> bool:
            self.assertEqual(code, "600011")
            self.assertEqual(analysis_date.isoformat(), "2026-04-01")
            call_state["count"] += 1
            call_state["windows"].append(int(eval_window_days))
            self._seed_daily_bars(
                code=code,
                rows=[
                    (date(2026, 4, 3), 11.0, 10.4, 10.8),
                    (date(2026, 4, 4), 11.2, 10.5, 10.9),
                    (date(2026, 4, 5), 11.4, 10.6, 11.0),
                    (date(2026, 4, 6), 11.5, 10.7, 11.1),
                ],
            )
            return True

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-01",
            end_date="2026-04-01",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[1, 3, 5],
            neutral_band_pct=2.0,
            detail_limit=2,
            fill_missing_daily_data=True,
            missing_daily_data_filler=_fill_missing_daily_data,
        )

        self.assertEqual(call_state["count"], 1)
        self.assertEqual(call_state["windows"], [5])
        self.assertEqual([item["completed_count"] for item in report["window_summaries"]], [1, 1, 1])

    def test_build_report_labels_insufficient_forward_horizon_and_skips_filler(self) -> None:
        today = date.today().isoformat()
        self._seed_snapshot(signal_date=today, code="600012", close=10.0, latest_high=10.5)

        call_counter = {"count": 0}

        def _fill_missing_daily_data(*, code: str, analysis_date: date, eval_window_days: int, stock_repo) -> bool:
            call_counter["count"] += 1
            return False

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date=today,
            end_date=today,
            code=None,
            codes=None,
            limit=None,
            eval_windows=[10],
            neutral_band_pct=2.0,
            detail_limit=2,
            fill_missing_daily_data=True,
            missing_daily_data_filler=_fill_missing_daily_data,
        )

        one_window = report["window_summaries"][0]
        self.assertEqual(one_window["completed_count"], 0)
        self.assertEqual(one_window["insufficient_count"], 1)
        self.assertEqual(call_counter["count"], 0)
        self.assertEqual(
            one_window.get("insufficient_reason_counts", {}).get("insufficient_forward_horizon"),
            1,
        )

    def test_build_report_uses_trading_day_horizon_to_skip_unreachable_fill(self) -> None:
        # 2026-04-20 to 2026-04-25 only has 4 trading bars in this local fixture.
        # For a 5-day window, filler should be skipped and marked as insufficient_forward_horizon.
        self._seed_snapshot(signal_date="2026-04-20", code="600021", close=10.0, latest_high=10.5)
        self._seed_daily_bars(
            code="600021",
            rows=[
                (date(2026, 4, 21), 10.8, 10.2, 10.6),
                (date(2026, 4, 22), 10.9, 10.3, 10.7),
                (date(2026, 4, 23), 11.0, 10.4, 10.8),
                (date(2026, 4, 24), 11.1, 10.5, 10.9),
            ],
        )

        call_counter = {"count": 0}

        def _fill_missing_daily_data(*, code: str, analysis_date: date, eval_window_days: int, stock_repo) -> bool:
            call_counter["count"] += 1
            return False

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-20",
            end_date="2026-04-20",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[5],
            neutral_band_pct=2.0,
            detail_limit=2,
            fill_missing_daily_data=True,
            missing_daily_data_filler=_fill_missing_daily_data,
        )

        one_window = report["window_summaries"][0]
        self.assertEqual(one_window["completed_count"], 0)
        self.assertEqual(one_window["insufficient_count"], 1)
        self.assertEqual(call_counter["count"], 0)
        self.assertEqual(
            one_window.get("insufficient_reason_counts", {}).get("insufficient_forward_horizon"),
            1,
        )

    def test_build_report_respects_fill_max_attempts_budget(self) -> None:
        self._seed_snapshot(signal_date="2026-04-20", code="600031", close=10.0, latest_high=10.5)
        self._seed_snapshot(signal_date="2026-04-20", code="600032", close=10.0, latest_high=10.5)

        # Seed market dates so horizon is reachable for 3-day window, but each symbol still lacks bars.
        self._seed_daily_bars(
            code="600031",
            rows=[(date(2026, 4, 21), 10.8, 10.2, 10.6)],
        )
        self._seed_daily_bars(
            code="600032",
            rows=[(date(2026, 4, 21), 10.8, 10.2, 10.6)],
        )
        self._seed_daily_bars(
            code="600099",
            rows=[
                (date(2026, 4, 22), 10.2, 9.8, 10.0),
                (date(2026, 4, 23), 10.3, 9.9, 10.1),
            ],
        )

        call_counter = {"count": 0}

        def _fill_missing_daily_data(*, code: str, analysis_date: date, eval_window_days: int, stock_repo) -> bool:
            call_counter["count"] += 1
            return False

        report = build_report(
            db=self.db,
            signal_type="hundred_day_high",
            profile_name=None,
            start_date="2026-04-20",
            end_date="2026-04-20",
            code=None,
            codes=None,
            limit=None,
            eval_windows=[3],
            neutral_band_pct=2.0,
            detail_limit=2,
            fill_missing_daily_data=True,
            missing_daily_data_filler=_fill_missing_daily_data,
            fill_max_attempts=1,
        )

        one_window = report["window_summaries"][0]
        self.assertEqual(one_window["completed_count"], 0)
        self.assertEqual(one_window["insufficient_count"], 2)
        self.assertEqual(call_counter["count"], 1)
        self.assertEqual(report.get("fill_stats", {}).get("fill_attempted_count"), 1)
        self.assertEqual(report.get("fill_stats", {}).get("fill_max_attempts"), 1)


if __name__ == "__main__":
    unittest.main()
