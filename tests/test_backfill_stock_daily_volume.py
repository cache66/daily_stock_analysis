# -*- coding: utf-8 -*-
"""Tests for the stock_daily volume backfill helpers."""

import unittest

from scripts.backfill_stock_daily_volume import _ts_code, run_backfill, run_backfill_by_date


class TsCodeTestCase(unittest.TestCase):
    def test_maps_exchanges(self):
        self.assertEqual(_ts_code("600519"), "600519.SH")
        self.assertEqual(_ts_code("000001"), "000001.SZ")
        self.assertEqual(_ts_code("300750"), "300750.SZ")
        self.assertEqual(_ts_code("430047"), "430047.BJ")
        self.assertEqual(_ts_code("830799"), "830799.BJ")


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, recorder):
        self._recorder = recorder

    def execute(self, statement, params):
        self._recorder.append(params)
        return _FakeResult(1)


class _FakeDB:
    def __init__(self, recorder):
        self._recorder = recorder

    def session_scope(self):
        return _SessionCtx(self._recorder)


class _SessionCtx:
    def __init__(self, recorder):
        self._recorder = recorder

    def __enter__(self):
        return _FakeSession(self._recorder)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


class _FakePro:
    def __init__(self, frame=None, error=None):
        self.frame = frame
        self.error = error

    def daily(self, *, ts_code=None, start_date=None, end_date=None, trade_date=None):
        if self.error:
            raise RuntimeError(self.error)
        return self.frame


class RunBackfillTestCase(unittest.TestCase):
    def test_updates_volume_with_100x_multiplier(self):
        recorder = []
        db = _FakeDB(recorder)
        pro = _FakePro(_FakeFrame([{"trade_date": "20260924", "vol": 1000.0}]))
        stats = run_backfill(
            db=db,
            codes=["600519"],
            start_date="2026-08-04",
            end_date="2026-09-26",
            pro=pro,
            sleep_seconds=0.0,
        )
        self.assertEqual(stats[0]["updated"], 1)
        self.assertEqual(recorder[0]["volume"], 100000.0)
        self.assertEqual(recorder[0]["code"], "600519")
        self.assertEqual(recorder[0]["day"], "2026-09-24")

    def test_dry_run_writes_nothing_and_failed_path(self):
        recorder = []
        db = _FakeDB(recorder)
        stats = run_backfill(
            db=db,
            codes=["600519"],
            start_date="2026-08-04",
            end_date="2026-09-26",
            dry_run=True,
            pro=_FakePro(_FakeFrame([{"trade_date": "20260924", "vol": 1.0}])),
            sleep_seconds=0.0,
        )
        self.assertEqual(stats[0]["updated"], 0)
        self.assertEqual(recorder, [])
        failed = run_backfill(
            db=db,
            codes=["600519"],
            start_date="2026-08-04",
            end_date="2026-09-26",
            pro=_FakePro(error="boom"),
            sleep_seconds=0.0,
        )
        self.assertTrue(failed[0]["status"].startswith("failed"))


class ByDateTestCase(unittest.TestCase):
    def test_by_date_updates_bulk_rows(self):
        recorder = []
        db = _FakeDB(recorder)
        pro = _FakePro(
            _FakeFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260924", "vol": 1000.0},
                    {"ts_code": "600519.SH", "trade_date": "20260924", "vol": 2000.0},
                    {"ts_code": "830799.BJ", "trade_date": "20260924", "vol": 500.0},
                ]
            )
        )
        stats = run_backfill_by_date(db=db, dates=["2026-09-24"], pro=pro, sleep_seconds=0.0)
        self.assertEqual(stats[0]["updated"], 3)
        self.assertEqual(recorder[0]["volume"], 100000.0)
        self.assertEqual(recorder[0]["code"], "000001")
        self.assertEqual(recorder[1]["code"], "600519")
        self.assertEqual(recorder[2]["code"], "830799")
        self.assertEqual(recorder[2]["day"], "2026-09-24")


if __name__ == "__main__":
    unittest.main()
