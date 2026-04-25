# -*- coding: utf-8 -*-
"""Tests for reproducible trend leader benchmark reporting."""

import json
import tempfile
import unittest
from pathlib import Path


class TrendLeaderBenchmarkReportTestCase(unittest.TestCase):
    def test_build_benchmark_report_calculates_improvement_and_counts(self) -> None:
        from scripts.benchmark_trend_leader_v1 import build_benchmark_report

        report = build_benchmark_report(
            snapshot_date="2026-04-21",
            baseline={
                "label": "baseline",
                "elapsed_seconds": 100.0,
                "selected_count": 0,
                "fallback_count": 0,
                "prefilter_stats": {"before": 200, "after": 200},
            },
            optimized={
                "label": "optimized",
                "elapsed_seconds": 65.0,
                "selected_count": 3,
                "fallback_count": 3,
                "prefilter_stats": {"before": 200, "after": 80},
            },
        )

        self.assertEqual(report["snapshot_date"], "2026-04-21")
        self.assertEqual(report["baseline"]["elapsed_seconds"], 100.0)
        self.assertEqual(report["optimized"]["elapsed_seconds"], 65.0)
        self.assertEqual(report["improvement_seconds"], 35.0)
        self.assertEqual(report["improvement_pct"], 35.0)
        self.assertEqual(report["optimized"]["selected_count"], 3)

    def test_write_benchmark_reports_emits_json_and_markdown(self) -> None:
        from scripts.benchmark_trend_leader_v1 import write_benchmark_reports

        temp_dir = tempfile.TemporaryDirectory()
        try:
            output_dir = Path(temp_dir.name)
            report = {
                "snapshot_date": "2026-04-21",
                "baseline": {
                    "label": "baseline",
                    "elapsed_seconds": 100.0,
                    "selected_count": 0,
                    "fallback_count": 0,
                    "prefilter_stats": {"before": 200, "after": 200},
                },
                "optimized": {
                    "label": "optimized",
                    "elapsed_seconds": 65.0,
                    "selected_count": 3,
                    "fallback_count": 3,
                    "prefilter_stats": {"before": 200, "after": 80},
                },
                "improvement_seconds": 35.0,
                "improvement_pct": 35.0,
            }

            json_path, md_path = write_benchmark_reports(output_dir=output_dir, report=report)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["improvement_pct"], 35.0)

            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("| mode | elapsed_seconds | selected_count | fallback_count |", markdown)
            self.assertIn("| baseline | 100.0 | 0 | 0 |", markdown)
            self.assertIn("| optimized | 65.0 | 3 | 3 |", markdown)
            self.assertIn("35.0%", markdown)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
