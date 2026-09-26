# -*- coding: utf-8 -*-
"""Tests for the benchmark index backfill helpers."""

import unittest

import pandas as pd

from scripts.backfill_index_daily import (
    _baostock_code,
    _exchange_for,
    _normalize_code,
    _split_codes,
    run_backfill,
)


class CodeMappingTestCase(unittest.TestCase):
    def test_split_codes(self):
        self.assertEqual(_split_codes("000300, 000905 ,"), ["000300", "000905"])

    def test_normalize_code(self):
        self.assertEqual(_normalize_code("000300"), "000300")
        self.assertEqual(_normalize_code(" sh000300 "), "000300")
        self.assertEqual(_normalize_code("sh.000300"), "000300")
        self.assertEqual(_normalize_code("000905.SZ"), "000905")

    def test_exchange_and_codes(self):
        self.assertEqual(_exchange_for("000300"), "sh")
        self.assertEqual(_baostock_code("000300"), "sh.000300")
        self.assertEqual(_exchange_for("399006"), "sz")
        self.assertEqual(_baostock_code("399006"), "sz.399006")


class _FakeDB:
    def __init__(self):
        self.saved = []

    def save_daily_data(self, df, code, data_source, canonical_id=None):
        self.saved.append(
            {
                "code": code,
                "source": data_source,
                "canonical_id": canonical_id,
                "rows": len(df),
            }
        )
        return 2


class _FakeFetcher:
    def __init__(self, frame=None, error=None):
        self.frame = frame
        self.error = error

    def get_daily_data(self, *, stock_code, start_date, end_date, days):
        if self.error:
            raise RuntimeError(self.error)
        return self.frame


def _frame():
    return pd.DataFrame(
        [
            {
                "date": "2026-09-24",
                "open": 4500.0,
                "high": 4550.0,
                "low": 4480.0,
                "close": 4520.0,
                "volume": 1.0,
                "amount": 1.0,
                "pct_chg": 0.5,
            }
        ]
    )


class RunBackfillTestCase(unittest.TestCase):
    def test_backfill_persists_frame_with_index_canonical(self):
        db = _FakeDB()
        stats = run_backfill(
            db=db,
            codes=["000300"],
            start_date="2024-01-01",
            end_date="2026-09-26",
            fetcher=_FakeFetcher(frame=_frame()),
        )
        self.assertEqual(len(db.saved), 1)
        saved = db.saved[0]
        self.assertEqual(saved["code"], "000300")
        self.assertEqual(saved["source"], "baostock_index_backfill")
        self.assertIsNone(saved["canonical_id"])
        self.assertEqual(saved["rows"], 1)
        self.assertEqual(stats[0]["rows"], 1)
        self.assertEqual(stats[0]["inserted"], 2)
        self.assertEqual(stats[0]["status"], "ok")

    def test_dry_run_skips_write(self):
        db = _FakeDB()
        stats = run_backfill(
            db=db,
            codes=["000300"],
            start_date="2024-01-01",
            end_date="2026-09-26",
            dry_run=True,
            fetcher=_FakeFetcher(frame=_frame()),
        )
        self.assertEqual(db.saved, [])
        self.assertEqual(stats[0]["rows"], 1)
        self.assertEqual(stats[0]["inserted"], 0)

    def test_empty_and_failed_paths(self):
        db = _FakeDB()
        empty_stats = run_backfill(
            db=db,
            codes=["000300"],
            start_date="2024-01-01",
            end_date="2026-09-26",
            fetcher=_FakeFetcher(frame=None),
        )
        self.assertEqual(empty_stats[0]["status"], "empty")
        failed_stats = run_backfill(
            db=db,
            codes=["000300"],
            start_date="2024-01-01",
            end_date="2026-09-26",
            fetcher=_FakeFetcher(error="boom"),
        )
        self.assertTrue(failed_stats[0]["status"].startswith("failed"))
        self.assertEqual(db.saved, [])


if __name__ == "__main__":
    unittest.main()
