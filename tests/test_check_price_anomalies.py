# -*- coding: utf-8 -*-
"""Tests for the price-anomaly check helpers and scan_abnormal_jumps extension."""

import csv
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from scripts.check_price_anomalies import (
    _compare_pairs,
    _load_baseline_pairs,
    _prefix_bucket,
    _shift_date,
    _summarize,
)
from scripts.rebuild_stock_daily_qfq import scan_abnormal_jumps


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, params=None):
        return _FakeResult(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    @contextmanager
    def session_scope(self):
        yield _FakeSession(self._rows)


class PrefixBucketTestCase(unittest.TestCase):
    def test_maps_known_prefixes(self):
        self.assertEqual(_prefix_bucket("688001"), "科创板")
        self.assertEqual(_prefix_bucket("830001"), "北交所/新三板")
        self.assertEqual(_prefix_bucket("920001"), "北交所/新三板")
        self.assertEqual(_prefix_bucket("300750"), "创业板")
        self.assertEqual(_prefix_bucket("600519"), "沪深主板")
        self.assertEqual(_prefix_bucket("000001"), "沪深主板")
        self.assertEqual(_prefix_bucket("123456"), "其他")


class CompareTestCase(unittest.TestCase):
    def test_compare_counts_and_samples(self):
        baseline = {("AAA", "2026-06-11"), ("BBB", "2026-06-12")}
        current = {("BBB", "2026-06-12"), ("CCC", "2026-06-13")}
        compare = _compare_pairs(baseline, current)
        self.assertEqual(compare["fixed_count"], 1)
        self.assertEqual(compare["new_count"], 1)
        self.assertEqual(compare["common_count"], 1)
        self.assertEqual(compare["fixed_samples"], ["AAA@2026-06-11"])
        self.assertEqual(compare["new_samples"], ["CCC@2026-06-13"])


class LoadBaselineTestCase(unittest.TestCase):
    def test_loads_code_date_pairs_and_skips_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["code", "date", "prev_close", "close", "ret_pct"])
                writer.writerow(["000004", "2026-06-23", "2.76", "0.31", "-88.77"])
                writer.writerow(["", "2026-06-24", "1", "1", "0"])
            pairs = _load_baseline_pairs(str(path))
            self.assertEqual(pairs, {("000004", "2026-06-23")})


class SummarizeTestCase(unittest.TestCase):
    def test_groups_by_month_and_bucket(self):
        rows = [
            {"code": "600000", "date": "2026-06-11"},
            {"code": "600001", "date": "2026-06-12"},
            {"code": "300001", "date": "2026-07-01"},
        ]
        summary = _summarize(rows)
        self.assertEqual(summary["by_month"]["2026-06"]["rows"], 2)
        self.assertEqual(summary["by_month"]["2026-07"]["rows"], 1)
        self.assertEqual(summary["by_bucket"]["沪深主板"]["rows"], 2)
        self.assertEqual(summary["by_bucket"]["创业板"]["rows"], 1)


class ShiftDateTestCase(unittest.TestCase):
    def test_shift_date(self):
        self.assertEqual(_shift_date("2026-01-15", -45), "2025-12-01")
        self.assertEqual(_shift_date("2026-01-05", -45), "2025-11-21")
        self.assertEqual(_shift_date("2026-01-05", 0), "2026-01-05")


class ScanIncludeRowsTestCase(unittest.TestCase):
    def test_include_rows_returns_full_rows_and_default_keeps_contract(self):
        rows = [
            ("AAA", "2026-06-11", 25.12, 15.37, -38.8),
            ("BBB", "2026-06-12", 10.0, 21.0, 110.0),
        ]
        db = _FakeDB(rows)
        result = scan_abnormal_jumps(
            db, start_date="2026-01-01", end_date="2026-09-26", include_rows=True
        )
        self.assertEqual(result["abnormal_count"], 2)
        self.assertEqual(result["codes_affected"], 2)
        self.assertEqual(result["rows"][0]["code"], "AAA")
        self.assertAlmostEqual(result["rows"][0]["prev_close"], 25.12, places=2)
        self.assertAlmostEqual(result["rows"][0]["ret_pct"], -38.8, places=1)
        default = scan_abnormal_jumps(db, start_date="2026-01-01", end_date="2026-09-26")
        self.assertNotIn("rows", default)


if __name__ == "__main__":
    unittest.main()
