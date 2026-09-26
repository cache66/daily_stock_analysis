# -*- coding: utf-8 -*-
"""Tests for the PIT contamination impact quantifier."""

import csv
import tempfile
import unittest
from pathlib import Path

from scripts.quantify_pit_contamination_impact import _load_codes, _summarize


class LoadCodesTestCase(unittest.TestCase):
    def test_loads_unique_codes_and_skips_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anomalies.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["code", "date", "prev_close", "close", "ret_pct"])
                writer.writerow(["600001", "2026-08-05", "1", "1", "0"])
                writer.writerow(["600001", "2026-08-06", "1", "1", "0"])
                writer.writerow(["", "2026-08-07", "1", "1", "0"])
            self.assertEqual(_load_codes(str(path)), {"600001"})


class SummarizeTestCase(unittest.TestCase):
    ROWS = [
        ("hundred_day_high", "2026-08-05", "600001"),
        ("hundred_day_high", "2026-08-05", "300001"),
        ("hundred_day_high", "2026-08-06", "300001"),
        ("daily_slow_rise", "2026-08-06", "600002"),
    ]

    def test_flags_types_over_threshold(self):
        summary = _summarize(self.ROWS, {"300001"}, threshold=0.05)
        self.assertEqual(summary["by_type"]["hundred_day_high"]["affected_rows"], 2)
        self.assertEqual(summary["by_type"]["hundred_day_high"]["affected_codes_count"], 1)
        self.assertAlmostEqual(summary["by_type"]["hundred_day_high"]["affected_pct"], 66.67, places=2)
        self.assertEqual(summary["by_type"]["daily_slow_rise"]["affected_rows"], 0)
        self.assertTrue(summary["rerun_recommended"])
        self.assertEqual(summary["rerun_recommended_types"], ["hundred_day_high"])
        self.assertEqual(summary["overall"]["affected_rows"], 2)

    def test_boundary_equal_to_threshold_not_flagged(self):
        rows = [
            ("trend_leader_unified", "2026-08-05", f"6000{idx:02d}") for idx in range(20)
        ]
        # 1/20 = 5% == 阈值 → 不触发（严格大于才重跑）
        summary = _summarize(rows, {"600000"}, threshold=0.05)
        self.assertFalse(summary["rerun_recommended"])
        self.assertEqual(summary["overall"]["affected_pct"], 5.0)

    def test_no_anomalies_means_all_clear(self):
        summary = _summarize(self.ROWS, set(), threshold=0.05)
        self.assertFalse(summary["rerun_recommended"])
        self.assertEqual(summary["overall"]["affected_rows"], 0)
        self.assertEqual(summary["overall"]["affected_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
