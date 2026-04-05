# -*- coding: utf-8 -*-
"""Tests for signal summary performance trend report helpers."""

import tempfile
import unittest
from pathlib import Path

from scripts.report_signal_summary_perf_trend import (
    build_markdown_report,
    filter_reports,
    load_reports,
    summarize_reports,
)


class SignalSummaryPerfTrendScriptTestCase(unittest.TestCase):
    def test_load_filter_summarize_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "signal_summary_perf_history.jsonl"
            history_path.write_text(
                "\n".join(
                    [
                        '{"observed_at":"2026-04-05T11:57:18","mode":"report_only","filters":{"signal_type":"hundred_day_high"},"after":{"snapshot_count":3,"daily_summary_count":1,"streak_snapshot_count":3},"elapsed_seconds":0.0,"throughput_snapshot_rows_per_second":null}',
                        '{"observed_at":"2026-04-05T12:00:00","mode":"rebuild","filters":{"signal_type":"hundred_day_high"},"after":{"snapshot_count":30,"daily_summary_count":10,"streak_snapshot_count":30},"elapsed_seconds":1.5,"throughput_snapshot_rows_per_second":20.0}',
                        '{"observed_at":"2026-04-05T12:05:00","mode":"rebuild","filters":{"signal_type":"other_signal"},"after":{"snapshot_count":10,"daily_summary_count":5,"streak_snapshot_count":10},"elapsed_seconds":1.0,"throughput_snapshot_rows_per_second":10.0}',
                    ]
                ),
                encoding="utf-8",
            )

            rows = load_reports(history_path)
            filtered = filter_reports(rows, signal_type="hundred_day_high", mode="rebuild")
            summary = summarize_reports(filtered)
            markdown = build_markdown_report(
                filtered,
                summary,
                input_path=history_path,
                signal_type="hundred_day_high",
                mode="rebuild",
            )

            self.assertEqual(len(rows), 3)
            self.assertEqual(len(filtered), 1)
            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["latest_snapshot_count"], 30)
            self.assertEqual(summary["avg_elapsed_seconds"], 1.5)
            self.assertEqual(summary["avg_throughput"], 20.0)
            self.assertIn("Signal Summary Performance Trend", markdown)
            self.assertIn("2026-04-05T12:00:00", markdown)
            self.assertIn("| observed_at | mode | snapshot_count |", markdown)


if __name__ == "__main__":
    unittest.main()
