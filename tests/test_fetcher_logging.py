import logging
import json
import os
import sys
import tempfile
import time
import types
import unittest
from typing import Optional
from unittest.mock import patch

import pandas as pd
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import BaseFetcher, DataFetchError, DataFetcherManager
from data_provider.efinance_fetcher import EfinanceFetcher


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-03-06", "2026-03-07"],
            "open": [10.0, 10.2],
            "high": [10.5, 10.4],
            "low": [9.8, 10.1],
            "close": [10.3, 10.35],
            "volume": [1000, 1200],
            "amount": [10300, 12420],
            "pct_chg": [1.0, 0.49],
        }
    )


class _SuccessFetcher(BaseFetcher):
    name = "SuccessFetcher"
    priority = 1

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _sample_df()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _FailureFetcher(BaseFetcher):
    name = "FailureFetcher"
    priority = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise DataFetchError(
            "Eastmoney 历史K线接口失败: "
            "endpoint=push2his.eastmoney.com/api/qt/stock/kline/get, "
            "category=remote_disconnect"
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _RecordingFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority
        self.calls = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls.append(stock_code)
        return _sample_df()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _PartialHistoryFetcher(BaseFetcher):
    name = "PartialHistoryFetcher"
    priority = 0

    def __init__(self, responses_by_range=None):
        self.responses_by_range = responses_by_range or {}
        self.calls = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls.append((stock_code, start_date, end_date))
        key = (start_date, end_date)
        if key not in self.responses_by_range:
            raise DataFetchError(f"no history for range {key}")
        return self.responses_by_range[key].copy()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _NoDataFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class TestFetcherLogging(unittest.TestCase):
    def setUp(self) -> None:
        DataFetcherManager.reset_daily_source_health()

    def tearDown(self) -> None:
        DataFetcherManager.reset_daily_source_health()

    def test_fast_scan_manager_keeps_akshare_fallback_after_tushare_no_data(self):
        manager = DataFetcherManager(fetchers=[])
        manager._skip_redundant_history_fallback_for_fast_scan = True

        should_skip = manager._should_skip_fast_scan_tushare_history_fallback(
            fetcher_name="TushareFetcher",
            next_fetcher_name="AkshareFetcher",
            days=62,
            error_type="DataFetchError",
            error_reason="[TushareFetcher] 未获取到 001257 的数据",
        )

        self.assertFalse(should_skip)

    def test_tushare_no_data_plus_akshare_em_backoff_does_not_open_manager_circuit(self):
        tushare = _NoDataFetcher("TushareFetcher", 0)
        akshare = AkshareFetcher(
            sleep_min=0.0,
            sleep_max=0.0,
            stock_history_source_priority=("tencent", "em"),
            stock_history_retry_attempts=1,
        )
        akshare.priority = 1
        manager = DataFetcherManager(fetchers=[tushare, akshare])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=False,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        em_calls = {"count": 0}

        def _em_fail(*args, **kwargs):
            em_calls["count"] += 1
            raise requests.exceptions.ConnectionError("Remote end closed connection without response")

        with patch(
            "src.config.get_config",
            return_value=config,
        ), patch.object(
            akshare,
            "_fetch_stock_data_tx",
            return_value=pd.DataFrame(),
        ), patch.object(
            akshare,
            "_fetch_stock_data_em",
            side_effect=_em_fail,
        ):
            for stock_code in ("001220", "001221", "001222"):
                with self.assertRaises(DataFetchError):
                    manager.get_daily_data(
                        stock_code,
                        start_date="2026-03-01",
                        end_date="2026-03-30",
                    )

        self.assertEqual(em_calls["count"], 1)
        self.assertTrue(akshare._stock_history_em_backoff_until_ts > time.time())
        self.assertTrue(manager._is_daily_source_available(akshare, "cn"))

    def test_daily_request_range_defaults_to_double_calendar_span(self):
        manager = DataFetcherManager(fetchers=[_SuccessFetcher()])

        start_date, end_date = manager._resolve_daily_data_request_range(
            start_date=None,
            end_date="2026-04-24",
            days=100,
        )

        self.assertEqual(start_date, "2025-10-06")
        self.assertEqual(end_date, "2026-04-24")

    def test_daily_request_range_supports_manager_level_calendar_multiplier(self):
        manager = DataFetcherManager(fetchers=[_SuccessFetcher()])
        manager._daily_data_request_calendar_span_multiplier = 1.6

        start_date, end_date = manager._resolve_daily_data_request_range(
            start_date=None,
            end_date="2026-04-24",
            days=100,
        )

        self.assertEqual(start_date, "2025-11-15")
        self.assertEqual(end_date, "2026-04-24")

    def test_base_fetcher_logs_start_and_success(self):
        fetcher = _SuccessFetcher()

        with self.assertLogs("data_provider.base", level="INFO") as captured:
            df = fetcher.get_daily_data("600519", start_date="2026-03-01", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertFalse(df.empty)
        self.assertIn("[SuccessFetcher] 开始获取 600519 日线数据", log_text)
        self.assertIn("[SuccessFetcher] 600519 获取成功:", log_text)
        self.assertIn("rows=2", log_text)

    def test_manager_logs_fallback_and_final_success(self):
        manager = DataFetcherManager(fetchers=[_FailureFetcher(), _SuccessFetcher()])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=False,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            with self.assertLogs("data_provider.base", level="INFO") as captured:
                df, source = manager.get_daily_data("601006", start_date="2026-01-07", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertFalse(df.empty)
        self.assertEqual(source, "SuccessFetcher")
        self.assertIn("[数据源尝试 1/2] [FailureFetcher] 获取 601006...", log_text)
        self.assertIn("[数据源失败 1/2] [FailureFetcher] 601006:", log_text)
        self.assertIn("[数据源切换] 601006: [FailureFetcher] -> [SuccessFetcher]", log_text)
        self.assertIn("[数据源完成] 601006 使用 [SuccessFetcher] 获取成功:", log_text)

    def test_manager_skips_builtin_fetchers_that_do_not_support_hk_daily(self):
        efinance = _RecordingFetcher("EfinanceFetcher", 0)
        pytdx = _RecordingFetcher("PytdxFetcher", 1)
        akshare = _RecordingFetcher("AkshareFetcher", 2)
        yfinance = _RecordingFetcher("YfinanceFetcher", 3)

        manager = DataFetcherManager(fetchers=[efinance, pytdx, akshare, yfinance])
        df, source = manager.get_daily_data("1211.HK", start_date="2026-05-01", end_date="2026-05-08")

        self.assertFalse(df.empty)
        self.assertEqual(source, "AkshareFetcher")
        self.assertEqual(efinance.calls, [])
        self.assertEqual(pytdx.calls, [])
        self.assertEqual(akshare.calls, ["HK01211"])
        self.assertEqual(yfinance.calls, [])

    @patch("data_provider.efinance_fetcher.get_config")
    def test_efinance_rejects_hk_daily_without_calling_eastmoney(self, mock_get_config):
        mock_get_config.return_value = types.SimpleNamespace(enable_eastmoney_patch=False)
        fetcher = EfinanceFetcher(sleep_min=0, sleep_max=0)

        with patch.object(fetcher, "_fetch_stock_data") as mock_fetch_stock_data:
            with self.assertRaises(DataFetchError) as captured:
                fetcher.get_daily_data("1211.HK", start_date="2026-05-01", end_date="2026-05-08")

        mock_fetch_stock_data.assert_not_called()
        self.assertIn("不支持港股日线", str(captured.exception))

    def test_efinance_logs_eastmoney_endpoint_on_remote_disconnect(self):
        fetcher = EfinanceFetcher()
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(
                get_quote_history=lambda **kwargs: (_ for _ in ()).throw(
                    requests.exceptions.ConnectionError("Remote end closed connection without response")
                )
            )
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                with self.assertLogs(level="INFO") as captured:
                    with self.assertRaises(DataFetchError):
                        fetcher.get_daily_data("601006", start_date="2026-01-07", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertIn("Eastmoney 历史K线接口失败:", log_text)
        self.assertIn("endpoint=push2his.eastmoney.com/api/qt/stock/kline/get", log_text)
        self.assertIn("category=remote_disconnect", log_text)
        self.assertIn("[EfinanceFetcher] 601006 获取失败:", log_text)


    def test_akshare_history_backfills_missing_volume_column(self):
        fake_akshare = types.SimpleNamespace(
            stock_zh_a_hist=lambda **kwargs: pd.DataFrame(
                {
                    "date": ["2026-03-06", "2026-03-07"],
                    "open": [10.0, 10.2],
                    "high": [10.5, 10.4],
                    "low": [9.8, 10.1],
                    "close": [10.3, 10.35],
                    "amount": [10300, 12420],
                }
            )
        )

        with patch("data_provider.akshare_fetcher.get_config", return_value=types.SimpleNamespace(enable_eastmoney_patch=False)):
            fetcher = AkshareFetcher(stock_history_source_priority=("em",))
        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                df = fetcher.get_daily_data("601006", start_date="2026-03-06", end_date="2026-03-07")

        self.assertEqual(df["volume"].tolist(), [0.0, 0.0])
        self.assertEqual(len(df), 2)

    def test_akshare_history_retries_remote_disconnect_before_success(self):
        call_count = {"value": 0}

        def _stock_zh_a_hist(**kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise requests.exceptions.ConnectionError("Remote end closed connection without response")
            return pd.DataFrame(
                {
                    "date": ["2026-03-06", "2026-03-07"],
                    "open": [10.0, 10.2],
                    "high": [10.5, 10.4],
                    "low": [9.8, 10.1],
                    "close": [10.3, 10.35],
                    "volume": [1000, 1200],
                    "amount": [10300, 12420],
                }
            )

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist=_stock_zh_a_hist)
        with patch("data_provider.akshare_fetcher.get_config", return_value=types.SimpleNamespace(enable_eastmoney_patch=False)):
            fetcher = AkshareFetcher(stock_history_source_priority=("em",))
        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ), patch("data_provider.akshare_fetcher.time.sleep", return_value=None):
                df = fetcher.get_daily_data("601006", start_date="2026-03-06", end_date="2026-03-07")

        self.assertEqual(call_count["value"], 2)
        self.assertEqual(len(df), 2)

    def test_akshare_history_retry_attempts_can_be_reduced_for_fast_scan(self):
        call_count = {"value": 0}

        def _stock_zh_a_hist(**kwargs):
            call_count["value"] += 1
            raise requests.exceptions.ConnectionError("Remote end closed connection without response")

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist=_stock_zh_a_hist)
        with patch(
            "data_provider.akshare_fetcher.get_config",
            return_value=types.SimpleNamespace(enable_eastmoney_patch=False),
        ):
            fetcher = AkshareFetcher(stock_history_source_priority=("em",))
        fetcher._stock_history_retry_attempts = 1

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ), patch("data_provider.akshare_fetcher.time.sleep", return_value=None):
                with self.assertRaises(requests.exceptions.ConnectionError):
                    fetcher._fetch_stock_data_em("601006", start_date="2026-03-06", end_date="2026-03-07")

        self.assertEqual(call_count["value"], 1)

    def test_manager_history_cache_hits_disk_before_fetcher(self):
        fetcher = _HistoryCacheFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            first_df, first_source = manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-06",
            )
            second_df, second_source = manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-06",
            )

        self.assertEqual(fetcher.calls, [("601006", "2026-03-05", "2026-03-06")])
        self.assertEqual(first_source, "HistoryCacheFetcher")
        self.assertEqual(second_source, "disk_cache:HistoryCacheFetcher")
        self.assertEqual(len(first_df), 2)
        self.assertEqual(len(second_df), 2)

    def test_manager_history_cache_repairs_legacy_akshare_volume_stored_as_amount(self):
        manager = DataFetcherManager(fetchers=[])
        cache_dir = tempfile.mkdtemp()
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=cache_dir,
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            csv_path, metadata_path = manager._get_history_cache_paths("002407")
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "date": ["2026-07-07", "2026-07-08"],
                    "open": [46.24, 46.00],
                    "high": [47.17, 46.17],
                    "low": [44.76, 41.72],
                    "close": [45.35, 42.12],
                    "volume": [0.0, 0.0],
                    "amount": [1654061.0, 1799828.0],
                    "pct_chg": [-1.11, -7.12],
                }
            ).to_csv(csv_path, index=False, encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "stock_code": "002407",
                        "market": "cn",
                        "source": "AkshareFetcher",
                        "updated_at": time.time(),
                        "rows": 2,
                    }
                ),
                encoding="utf-8",
            )

            cached_df, source = manager.get_daily_data(
                "002407",
                start_date="2026-07-07",
                end_date="2026-07-08",
            )

        self.assertEqual(source, "disk_cache:AkshareFetcher")
        self.assertEqual(cached_df["volume"].tolist(), [165406100.0, 179982800.0])
        self.assertAlmostEqual(float(cached_df.iloc[-1]["amount"]), 42.12 * 179982800.0)

    def test_manager_history_cache_force_refresh_bypasses_disk(self):
        fetcher = _HistoryCacheFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date="2026-03-05", end_date="2026-03-06")
            _, source = manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-06",
                force_refresh=True,
            )

        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(source, "HistoryCacheFetcher")

    def test_manager_history_cache_can_skip_derived_indicators(self):
        fetcher = _HistoryCacheFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])
        manager._daily_data_include_derived_indicators = False
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-06",
            )
            cached_df, source = manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-06",
            )

        self.assertEqual(source, "disk_cache:HistoryCacheFetcher")
        self.assertNotIn("ma5", cached_df.columns)
        self.assertNotIn("ma10", cached_df.columns)
        self.assertNotIn("ma20", cached_df.columns)
        self.assertNotIn("volume_ratio", cached_df.columns)

    def test_manager_history_cache_incrementally_refreshes_tail(self):
        fetcher = _HistoryCacheFetcher(
            responses_by_range={
                ("2026-03-05", "2026-03-06"): _history_rows("2026-03-05", "2026-03-06"),
                ("2026-03-06", "2026-03-08"): _history_rows("2026-03-06", "2026-03-07", "2026-03-08"),
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date="2026-03-05", end_date="2026-03-06")
            merged_df, source = manager.get_daily_data(
                "601006",
                start_date="2026-03-05",
                end_date="2026-03-08",
            )

        self.assertEqual(
            fetcher.calls,
            [
                ("601006", "2026-03-05", "2026-03-06"),
                ("601006", "2026-03-06", "2026-03-08"),
            ],
        )
        self.assertEqual(source, "HistoryCacheFetcher")
        self.assertEqual(
            merged_df["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2026-03-05", "2026-03-06", "2026-03-07", "2026-03-08"],
        )

    def test_manager_history_cache_best_effort_hits_for_days_request_with_small_head_gap(self):
        fetcher = _HistoryCacheFetcher(
            responses_by_range={
                ("2026-03-06", "2026-03-08"): _history_rows("2026-03-06", "2026-03-07", "2026-03-08"),
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date="2026-03-06", end_date="2026-03-08")
            second_df, second_source = manager.get_daily_data(
                "601006",
                end_date="2026-03-08",
                days=2,
            )

        self.assertEqual(fetcher.calls, [("601006", "2026-03-06", "2026-03-08")])
        self.assertEqual(second_source, "disk_cache_best_effort:HistoryCacheFetcher")
        self.assertEqual(
            second_df["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2026-03-06", "2026-03-07", "2026-03-08"],
        )

    def test_manager_history_cache_best_effort_stale_hits_for_days_request_with_small_tail_gap(self):
        fetcher = _HistoryCacheFetcher(
            responses_by_range={
                ("2026-03-06", "2026-03-07"): _history_rows("2026-03-06", "2026-03-07"),
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date="2026-03-06", end_date="2026-03-07")
            second_df, second_source = manager.get_daily_data(
                "601006",
                end_date="2026-03-10",
                days=2,
            )

        self.assertEqual(fetcher.calls, [("601006", "2026-03-06", "2026-03-07")])
        self.assertEqual(second_source, "disk_cache_best_effort_stale:HistoryCacheFetcher")
        self.assertEqual(
            second_df["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2026-03-06", "2026-03-07"],
        )

    def test_manager_history_cache_returns_partial_cached_history_when_head_backfill_fails_for_days_request(self):
        cached_rows = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-03-06", "2026-03-07", "2026-03-08"]),
                "open": [10.0, 10.2, 10.4],
                "high": [10.3, 10.5, 10.6],
                "low": [9.9, 10.1, 10.3],
                "close": [10.2, 10.4, 10.5],
                "volume": [1000, 1200, 1300],
                "amount": [10200, 12480, 13650],
                "pct_chg": [1.0, 1.96, 0.96],
            }
        )
        fetcher = _PartialHistoryFetcher(
            responses_by_range={
                ("2026-03-06", "2026-03-08"): cached_rows,
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date="2026-03-06", end_date="2026-03-08")
            second_df, second_source = manager.get_daily_data(
                "601006",
                end_date="2026-03-08",
                days=10,
            )

        self.assertEqual(
            fetcher.calls,
            [
                ("601006", "2026-03-06", "2026-03-08"),
                ("601006", "2026-02-16", "2026-03-05"),
            ],
        )
        self.assertEqual(second_source, "disk_cache_partial_history:PartialHistoryFetcher")
        self.assertEqual(
            second_df["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2026-03-06", "2026-03-07", "2026-03-08"],
        )

    def test_manager_history_cache_ttl_refreshes_current_range(self):
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        fetcher = _HistoryCacheFetcher(
            responses_by_range={
                (today, today): _history_rows(today),
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=0,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date=today, end_date=today)
            manager.get_daily_data("601006", start_date=today, end_date=today)

        self.assertEqual(len(fetcher.calls), 2)

    def test_manager_history_cache_prefers_covered_stale_cache_for_fast_scan(self):
        end_date = pd.Timestamp.now().strftime("%Y-%m-%d")
        start_date = (pd.Timestamp(end_date) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        middle_date_1 = (pd.Timestamp(start_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        middle_date_2 = (pd.Timestamp(start_date) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        fetcher = _HistoryCacheFetcher(
            responses_by_range={
                (start_date, end_date): _history_rows(
                    start_date,
                    middle_date_1,
                    middle_date_2,
                    end_date,
                ),
            }
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        manager._prefer_cached_history_when_covered = True
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=0,
            history_disk_cache_overlap_days=0,
        )

        with patch("src.config.get_config", return_value=config):
            manager.get_daily_data("601006", start_date=start_date, end_date=end_date)
            second_df, second_source = manager.get_daily_data(
                "601006",
                start_date=start_date,
                end_date=end_date,
            )

        self.assertEqual(fetcher.calls, [("601006", start_date, end_date)])
        self.assertEqual(second_source, "disk_cache_stale_covered:HistoryCacheFetcher")
        self.assertEqual(
            second_df["date"].dt.strftime("%Y-%m-%d").tolist(),
            [start_date, middle_date_1, middle_date_2, end_date],
        )

    def test_manager_daily_data_timeout_fails_fast_for_hanging_fetcher(self):
        manager = DataFetcherManager(fetchers=[_HangingFetcher()])
        manager._daily_data_fetch_timeout_seconds = 0.01
        config = types.SimpleNamespace(
            history_disk_cache_enabled=False,
            history_disk_cache_dir=tempfile.mkdtemp(),
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )

        started_at = time.monotonic()
        with patch("src.config.get_config", return_value=config):
            with self.assertRaises(DataFetchError) as raised:
                manager.get_daily_data("601006", start_date="2026-03-01", end_date="2026-03-08")
        elapsed = time.monotonic() - started_at

        self.assertIn("timeout", str(raised.exception).lower())
        self.assertLess(elapsed, 0.15)

    def test_manager_history_failure_cache_reuses_failed_result_across_manager_instances(self):
        cache_dir = tempfile.mkdtemp()
        config = types.SimpleNamespace(
            history_disk_cache_enabled=True,
            history_disk_cache_dir=cache_dir,
            history_disk_cache_ttl_seconds=21600,
            history_disk_cache_overlap_days=0,
        )
        start_date = "2026-03-01"
        end_date = "2026-03-08"

        first_fetcher = _AlwaysFailHistoryFetcher("first history failure")
        first_manager = DataFetcherManager(fetchers=[first_fetcher])
        first_manager._history_failed_disk_cache_ttl_seconds = 1800

        second_fetcher = _AlwaysFailHistoryFetcher("second manager should not hit remote")
        second_manager = DataFetcherManager(fetchers=[second_fetcher])
        second_manager._history_failed_disk_cache_ttl_seconds = 1800

        with patch("src.config.get_config", return_value=config):
            with self.assertRaises(DataFetchError) as first_error:
                first_manager.get_daily_data(
                    "601006",
                    start_date=start_date,
                    end_date=end_date,
                )

            with self.assertRaises(DataFetchError) as second_error:
                second_manager.get_daily_data(
                    "601006",
                    start_date=start_date,
                    end_date=end_date,
                )

        self.assertIn("first history failure", str(first_error.exception))
        self.assertIn("first history failure", str(second_error.exception))
        self.assertEqual(first_fetcher.calls, [("601006", start_date, end_date)])
        self.assertEqual(second_fetcher.calls, [])


if __name__ == "__main__":
    unittest.main()
