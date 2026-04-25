# -*- coding: utf-8 -*-
"""Unit tests for TushareFetcher.get_stock_list(), _fetch_raw_data(), _normalize_data(), get_chip_distribution().

This test file is intentionally isolated from other test modules.
It loads repo-root `.env` and stubs optional runtime deps so it can run
in minimal CI environments without network calls.

Run (repo root):

- With pytest: ``python3 -m pytest tests/test_tushare_fetcher_get_stock_list.py``
  (install once: ``pip install -r requirements-dev.txt`` or ``pip install pytest``)
- Without pytest: ``python3 tests/test_tushare_fetcher_get_stock_list.py``
"""

import importlib.util
import os
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

if "fake_useragent" not in sys.modules:
    sys.modules["fake_useragent"] = MagicMock()

from data_provider.base import DataFetchError, RateLimitError
from data_provider.tushare_fetcher import TushareFetcher

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
except ImportError:
    pass


class TestTushareFetcherGetStockList(unittest.TestCase):
    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        # Avoid real API initialization; we inject a mocked _api below.
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        fetcher._test_cache_dir = Path(tempfile.mkdtemp())
        fetcher._get_reference_cache_dir = lambda: fetcher._test_cache_dir
        return fetcher

    def test_get_stock_list_a_share_only(self) -> None:
        fetcher = self._make_fetcher()

        fetcher._api.stock_basic.return_value = pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "name": ["贵州茅台", "平安银行"],
                "industry": ["白酒", "银行"],
                "area": ["贵州", "深圳"],
                "market": ["主板", "主板"],
            }
        )

        with patch.object(fetcher, "_check_rate_limit"):
            df = fetcher.get_stock_list()

        self.assertIsNotNone(df)
        assert df is not None
        self.assertEqual(
            set(df.columns.tolist()),
            {"code", "name", "industry", "area", "market"},
        )
        self.assertEqual(len(df), 2)
        self.assertEqual(set(df["code"].tolist()), {"600519", "000001"})
        self.assertEqual(fetcher._stock_name_cache.get("600519"), "贵州茅台")

        fetcher._api.stock_basic.assert_called_once()
        self.assertFalse(fetcher._api.hk_basic.called)

    def test_get_stock_list_returns_none_when_empty(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.stock_basic.return_value = pd.DataFrame()

        with patch.object(fetcher, "_check_rate_limit"):
            df = fetcher.get_stock_list()

        self.assertIsNone(df)

    def test_get_stock_list_prefers_fresh_local_cache(self) -> None:
        fetcher = self._make_fetcher()

        cache_path = fetcher._test_cache_dir / "tushare_stock_basic_list.csv"
        os.makedirs(fetcher._test_cache_dir, exist_ok=True)
        pd.DataFrame(
                {
                    "ts_code": ["600519.SH", "000001.SZ"],
                    "code": ["600519", "000001"],
                    "name": ["贵州茅台", "平安银行"],
                    "industry": ["白酒", "银行"],
                    "area": ["贵州", "深圳"],
                    "market": ["主板", "主板"],
                }
        ).to_csv(cache_path, index=False, encoding="utf-8-sig")

        df = fetcher.get_stock_list()

        self.assertIsNotNone(df)
        assert df is not None
        self.assertEqual(df["code"].tolist(), ["600519", "000001"])
        self.assertEqual(fetcher._stock_name_cache.get("600519"), "贵州茅台")
        fetcher._api.stock_basic.assert_not_called()

    def test_get_stock_list_falls_back_to_stale_cache_when_api_unavailable(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.stock_basic.side_effect = Exception("quota exceeded")

        cache_path = fetcher._test_cache_dir / "tushare_stock_basic_list.csv"
        os.makedirs(fetcher._test_cache_dir, exist_ok=True)
        pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "code": ["600519"],
                    "name": ["贵州茅台"],
                    "industry": ["白酒"],
                    "area": ["贵州"],
                    "market": ["主板"],
                }
        ).to_csv(cache_path, index=False, encoding="utf-8-sig")
        stale_ts = os.path.getmtime(cache_path) - 9 * 24 * 3600
        os.utime(cache_path, (stale_ts, stale_ts))

        with patch.object(fetcher, "_check_rate_limit"):
            df = fetcher.get_stock_list()

        self.assertIsNotNone(df)
        assert df is not None
        self.assertEqual(df.iloc[0]["name"], "贵州茅台")
        fetcher._api.stock_basic.assert_called_once()


class TestTushareFetcherTradeCalendarCache(unittest.TestCase):
    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        fetcher._test_cache_dir = Path(tempfile.mkdtemp())
        fetcher._get_reference_cache_dir = lambda: fetcher._test_cache_dir
        return fetcher

    @staticmethod
    def _china_now() -> datetime:
        return datetime(2026, 4, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    @staticmethod
    def _trade_calendar_frame() -> pd.DataFrame:
        dates = pd.date_range("2026-03-30", "2026-04-19", freq="D").strftime("%Y%m%d")
        return pd.DataFrame(
            {
                "exchange": ["SSE"] * len(dates),
                "cal_date": list(dates),
                "is_open": ["1"] * len(dates),
            }
        )

    def test_get_trade_dates_prefers_fresh_local_cache(self) -> None:
        fetcher = self._make_fetcher()
        cache_path = fetcher._test_cache_dir / "tushare_trade_cal_sse.csv"
        os.makedirs(fetcher._test_cache_dir, exist_ok=True)
        self._trade_calendar_frame().to_csv(cache_path, index=False, encoding="utf-8-sig")

        with patch.object(fetcher, "_get_china_now", return_value=self._china_now()):
            trade_dates = fetcher._get_trade_dates("20260419")

        self.assertEqual(trade_dates[0], "20260419")
        self.assertEqual(trade_dates[-1], "20260330")
        fetcher._api.trade_cal.assert_not_called()

    def test_get_trade_dates_falls_back_to_stale_cache_when_api_fails(self) -> None:
        fetcher = self._make_fetcher()
        cache_path = fetcher._test_cache_dir / "tushare_trade_cal_sse.csv"
        os.makedirs(fetcher._test_cache_dir, exist_ok=True)
        self._trade_calendar_frame().to_csv(cache_path, index=False, encoding="utf-8-sig")
        stale_ts = os.path.getmtime(cache_path) - 3 * 24 * 3600
        os.utime(cache_path, (stale_ts, stale_ts))

        with patch.object(fetcher, "_get_china_now", return_value=self._china_now()):
            with patch.object(fetcher, "_call_api_with_rate_limit", side_effect=Exception("quota exceeded")) as api_mock:
                trade_dates = fetcher._get_trade_dates("20260419")

        self.assertEqual(trade_dates[0], "20260419")
        self.assertEqual(trade_dates[-1], "20260330")
        api_mock.assert_called_once_with(
            "trade_cal",
            exchange="SSE",
            start_date="20260330",
            end_date="20260419",
        )

    def test_get_trade_dates_falls_back_to_weekdays_when_api_denied_without_cache(self) -> None:
        fetcher = self._make_fetcher()
        cache_path = fetcher._test_cache_dir / "tushare_trade_cal_sse.csv"

        with patch.object(fetcher, "_get_china_now", return_value=self._china_now()):
            with patch.object(fetcher, "_call_api_with_rate_limit", side_effect=Exception("没有接口访问权限")) as api_mock:
                trade_dates = fetcher._get_trade_dates("20260419")

        self.assertTrue(trade_dates)
        self.assertEqual(trade_dates[0], "20260417")
        self.assertEqual(trade_dates[-1], "20260330")
        self.assertTrue(cache_path.exists())
        api_mock.assert_called_once_with(
            "trade_cal",
            exchange="SSE",
            start_date="20260330",
            end_date="20260419",
        )


class TestTushareFetcherFetchRawData(unittest.TestCase):
    """TushareFetcher._fetch_raw_data: API routing and error handling."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_fetch_raw_data_a_share_uses_daily(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.daily.return_value = pd.DataFrame({"trade_date": ["20260101"]})

        with patch.object(fetcher, "_check_rate_limit"):
            out = fetcher._fetch_raw_data("600519", "2026-01-01", "2026-01-05")

        self.assertIsNotNone(out)
        fetcher._api.daily.assert_called_once_with(
            ts_code="600519.SH",
            start_date="20260101",
            end_date="20260105",
        )
        fetcher._api.fund_daily.assert_not_called()
        fetcher._api.hk_daily.assert_not_called()

    def test_fetch_raw_data_etf_uses_fund_daily(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.fund_daily.return_value = pd.DataFrame({"trade_date": ["20260101"]})

        with patch.object(fetcher, "_check_rate_limit"):
            out = fetcher._fetch_raw_data("510050", "20260101", "20260105")

        self.assertIsNotNone(out)
        fetcher._api.fund_daily.assert_called_once_with(
            ts_code="510050.SH",
            start_date="20260101",
            end_date="20260105",
        )
        fetcher._api.daily.assert_not_called()
        fetcher._api.hk_daily.assert_not_called()

    def test_fetch_raw_data_hk_uses_hk_daily(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.hk_daily.return_value = pd.DataFrame({"trade_date": ["20260102"]})

        with patch.object(fetcher, "_check_rate_limit"):
            out = fetcher._fetch_raw_data("HK00700", "2026-01-01", "2026-01-05")

        self.assertIsNotNone(out)
        fetcher._api.hk_daily.assert_called_once_with(
            ts_code="00700.HK",
            start_date="20260101",
            end_date="20260105",
        )
        fetcher._api.daily.assert_not_called()
        fetcher._api.fund_daily.assert_not_called()

    def test_fetch_raw_data_us_raises(self) -> None:
        fetcher = self._make_fetcher()
        with patch.object(fetcher, "_check_rate_limit"):
            with self.assertRaises(DataFetchError) as ctx:
                fetcher._fetch_raw_data("AAPL", "2026-01-01", "2026-01-05")
        self.assertIn("不支持美股", str(ctx.exception))
        fetcher._api.daily.assert_not_called()

    def test_fetch_raw_data_api_unconfigured_raises(self) -> None:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        # __init__ leaves _api None when _init_api is a no-op mock
        self.assertIsNone(fetcher._api)
        with self.assertRaises(DataFetchError) as ctx:
            fetcher._fetch_raw_data("600519", "2026-01-01", "2026-01-05")
        self.assertIn("未初始化", str(ctx.exception))

    def test_fetch_raw_data_quota_exception_becomes_rate_limit(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.daily.side_effect = Exception("quota exceeded")

        with patch.object(fetcher, "_check_rate_limit"):
            with self.assertRaises(RateLimitError):
                fetcher._fetch_raw_data("600519", "20260101", "20260105")

    def test_convert_stock_code_normalizes(self) -> None:
        fetcher = self._make_fetcher()
        self.assertEqual(fetcher._convert_stock_code("HK00700"), "HK00700")
    

    def test_convert_stock_code_for_tushare_normalizes_hk(self) -> None:
        fetcher = self._make_fetcher()
        self.assertEqual(fetcher._convert_hk_stock_code_for_tushare("HK00700"), "00700.HK")
        self.assertEqual(fetcher._convert_hk_stock_code_for_tushare("00700.HK"), "00700.HK")
        self.assertEqual(fetcher._convert_hk_stock_code_for_tushare("600519"), "600519.SH")


class TestTushareFetcherNormalizeData(unittest.TestCase):
    """TushareFetcher._normalize_data: A-share vol/amount scaling vs HK passthrough."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    @staticmethod
    def _sample_daily_frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_date": ["20260102"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "vol": [100.0],
                "amount": [50.0],
                "pct_chg": [1.0],
            }
        )

    def test_normalize_data_a_share_multiplies_volume_and_amount(self) -> None:
        fetcher = self._make_fetcher()
        out = fetcher._normalize_data(self._sample_daily_frame(), "600519")
        self.assertEqual(out.iloc[0]["volume"], 10000.0)
        self.assertEqual(out.iloc[0]["amount"], 50000.0)
        self.assertEqual(out.iloc[0]["code"], "600519")

    def test_normalize_data_hk_skips_volume_amount_scaling(self) -> None:
        fetcher = self._make_fetcher()
        out = fetcher._normalize_data(self._sample_daily_frame(), "HK00700")
        self.assertEqual(out.iloc[0]["volume"], 100.0)
        self.assertEqual(out.iloc[0]["amount"], 50.0)
        self.assertEqual(out.iloc[0]["code"], "HK00700")

    def test_normalize_data_hk_suffix_skips_scaling(self) -> None:
        fetcher = self._make_fetcher()
        out = fetcher._normalize_data(self._sample_daily_frame(), "00700.HK")
        self.assertEqual(out.iloc[0]["volume"], 100.0)
        self.assertEqual(out.iloc[0]["amount"], 50.0)

    def test_normalize_data_etf_scales_like_a_share(self) -> None:
        fetcher = self._make_fetcher()
        out = fetcher._normalize_data(self._sample_daily_frame(), "510050")
        self.assertEqual(out.iloc[0]["volume"], 10000.0)
        self.assertEqual(out.iloc[0]["amount"], 50000.0)


class TestTushareFetcherChipDistribution(unittest.TestCase):
    """get_chip_distribution: HK early exit."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_get_chip_distribution_returns_none_for_hk_canonical(self) -> None:
        fetcher = self._make_fetcher()
        with patch.object(fetcher, "_call_api_with_rate_limit") as api_mock:
            self.assertIsNone(fetcher.get_chip_distribution("HK00700"))
        api_mock.assert_not_called()

    def test_get_chip_distribution_returns_none_for_hk_ts_suffix(self) -> None:
        fetcher = self._make_fetcher()
        with patch.object(fetcher, "_call_api_with_rate_limit") as api_mock:
            self.assertIsNone(fetcher.get_chip_distribution("00700.HK"))
        api_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

