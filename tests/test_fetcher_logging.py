import logging
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


class TestFetcherLogging(unittest.TestCase):
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
