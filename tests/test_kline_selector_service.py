# -*- coding: utf-8 -*-
"""
Tests for the isolated K-line selector service.
"""

import math
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.services.kline_selector_service import (  # noqa: E402
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineRuleResult,
    KlineSelectionEvaluation,
    KlineSelectorService,
)


def _round_price_to_tick(value: float) -> float:
    return math.floor(value * 100 + 0.5) / 100.0


def build_history(with_limit_up: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2025-09-01", periods=120)
    closes = []
    close_value = 10.0
    for _ in range(110):
        closes.append(round(close_value, 2))
        close_value += 0.09

    recent_closes = []
    prev_close = closes[-1]
    if with_limit_up:
        recent_closes.append(_round_price_to_tick(prev_close * 1.10))
    else:
        recent_closes.append(round(prev_close + 0.30, 2))
    recent_closes.extend([22.20, 22.00, 22.40, 22.80, 22.70, 23.10, 23.40, 23.80, 24.20])
    closes.extend(recent_closes)

    highs = closes.copy()
    highs[-1] = closes[-1] + 0.25
    opens = [round(c * 0.99, 2) for c in closes]
    lows = [round(c * 0.98, 2) for c in closes]
    pct_chg = [None]
    for index in range(1, len(closes)):
        prev = closes[index - 1]
        pct_chg.append(round((closes[index] - prev) / prev * 100, 2))

    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "pct_chg": pct_chg,
        }
    )


class DummyQuote:
    def __init__(self, total_mv):
        self.total_mv = total_mv


class FakeManager:
    def __init__(self, history_by_code=None, quote_caps=None):
        self.history_by_code = history_by_code or {}
        self.quote_caps = quote_caps or {}
        self.history_calls = []

    def get_daily_data(self, stock_code: str, days: int):
        self.history_calls.append((stock_code, days))
        return self.history_by_code.get(stock_code, pd.DataFrame()), "fake"

    def get_realtime_quote(self, stock_code: str):
        return DummyQuote(self.quote_caps.get(stock_code))


class TestKlineSelectorService(unittest.TestCase):
    def tearDown(self) -> None:
        KlineSelectorService._spot_universe_cache = None
        KlineSelectorService._listing_metadata_cache = None
        if hasattr(KlineSelectorService, "_spot_universe_reference_cache_memory"):
            KlineSelectorService._spot_universe_reference_cache_memory = None

    def test_fetch_universe_dataframe_prefers_tushare_before_akshare_spot(self):
        service = KlineSelectorService()
        tushare_df = pd.DataFrame({"code": ["000001"], "name": ["pingan"]})

        with patch.object(
            service,
            "_fetch_universe_from_tushare",
            return_value=tushare_df,
        ) as tushare_mock, patch.object(
            service,
            "_fetch_universe_from_akshare_spot",
            side_effect=RuntimeError("akshare should not be called first"),
        ) as akshare_mock:
            universe_df = service._fetch_universe_dataframe()

        self.assertEqual(universe_df["code"].tolist(), ["000001"])
        self.assertEqual(tushare_mock.call_count, 1)
        self.assertEqual(akshare_mock.call_count, 0)

    def test_get_a_share_universe_filters_bse_and_duplicates(self):
        service = KlineSelectorService(
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600519", "920748", "000001", "600519"],
                    "name": ["alpha", "bse_sample", "pingan", "alpha_dup"],
                    "total_mv": [200e9, 20e9, 120e9, 200e9],
                }
            )
        )

        universe = service.get_a_share_universe()

        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(universe["name"].tolist(), ["pingan", "alpha"])

    def test_get_a_share_universe_filters_indices_and_etfs(self):
        service = KlineSelectorService(
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["000001", "510300", "600519", "399001", "301236"],
                    "name": ["上证综合指数", "沪深300ETF", "贵州茅台", "深证成指", "软通动力"],
                    "total_mv": [None, None, 200e9, None, 30e9],
                }
            )
        )

        universe = service.get_a_share_universe()

        self.assertEqual(universe["code"].tolist(), ["301236", "600519"])
        self.assertEqual(universe["name"].tolist(), ["软通动力", "贵州茅台"])

    def test_get_a_share_universe_preserves_list_date_and_listed_days(self):
        service = KlineSelectorService(
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600519", "301236"],
                    "name": ["贵州茅台", "软通动力"],
                    "list_date": ["20010827", "2022-03-15"],
                }
            )
        )

        universe = service.get_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertIn("list_date", universe.columns)
        self.assertIn("listed_days", universe.columns)
        self.assertEqual(str(universe.loc[0, "list_date"].date()), "2022-03-15")
        self.assertEqual(int(universe.loc[0, "listed_days"]), 1499)
        self.assertEqual(str(universe.loc[1, "list_date"].date()), "2001-08-27")

    def test_prepare_history_skips_reparsing_for_prepared_manager_output(self):
        history = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-01", "2026-04-02", "2026-04-03"]),
                "open": [10.0, 10.3, 10.5],
                "high": [10.2, 10.6, 10.8],
                "low": [9.9, 10.1, 10.4],
                "close": [10.1, 10.5, 10.7],
                "pct_chg": [0.5, 3.96, 1.9],
                "volume": [1000.0, 1200.0, 1300.0],
                "amount": [10000.0, 12600.0, 13910.0],
            }
        )

        with patch(
            "src.services.kline_selector_service.pd.to_datetime",
            side_effect=AssertionError("should not reparse prepared history"),
        ), patch(
            "src.services.kline_selector_service.pd.to_numeric",
            side_effect=AssertionError("should not renormalize prepared history"),
        ):
            prepared = KlineSelectorService._prepare_history(history)

        self.assertEqual(prepared["date"].tolist(), history["date"].tolist())
        self.assertTrue(pd.isna(prepared.loc[0, "prev_close"]))
        self.assertAlmostEqual(float(prepared.loc[1, "prev_close"]), 10.1, places=6)
        self.assertAlmostEqual(float(prepared.loc[2, "prev_close"]), 10.5, places=6)

    def test_prepare_scan_universe_applies_filters_and_prefilter_stats(self):
        service = KlineSelectorService()
        universe = pd.DataFrame(
            [
                {"code": "600001", "name": "正常股", "pct_change": 1.0, "turnover_rate": 2.0},
                {"code": "688001", "name": "科创股", "pct_change": 1.0, "turnover_rate": 2.0},
                {"code": "600002", "name": "*ST测试", "pct_change": 1.0, "turnover_rate": 2.0},
            ]
        )

        result = service.prepare_scan_universe(
            universe=universe,
            prefilter=KlineSelectorPrefilter(require_positive_change=True),
            exclude_st=True,
            exclude_kcb=True,
        )

        self.assertEqual(result.base_universe_size, 3)
        self.assertEqual(result.sharded_universe_size, 1)
        self.assertEqual(result.prepared_universe_size, 1)
        self.assertEqual(result.prepared_universe["code"].tolist(), ["600001"])
        self.assertEqual(result.filter_stats["removed_st"], 1)
        self.assertEqual(result.filter_stats["removed_kcb"], 1)
        self.assertEqual(result.prefilter_stats["after"], 1)

    def test_prepare_scan_universe_skips_pct_change_hydration_when_partial_values_already_exist(self):
        class PartialQuoteManager:
            def __init__(self):
                self.requested_codes = []

            def get_realtime_quote(self, stock_code: str):
                self.requested_codes.append(stock_code)
                raise AssertionError(f"pct_change hydration should be skipped for {stock_code}")

        manager = PartialQuoteManager()
        service = KlineSelectorService(manager=manager)
        universe = pd.DataFrame(
            [
                {"code": "600001", "name": "alpha", "pct_change": 1.2, "turnover_rate": 1.1},
                {"code": "600002", "name": "beta", "pct_change": None, "turnover_rate": 1.3},
            ]
        )

        result = service.prepare_scan_universe(
            universe=universe,
            prefilter=KlineSelectorPrefilter(
                min_change_pct_60d=3.0,
                require_positive_change=True,
            ),
        )

        self.assertEqual(manager.requested_codes, [])
        self.assertEqual(result.prefilter_stats["quote_requested_rows"], 0)
        self.assertEqual(result.prefilter_stats["quote_hydrated_rows"], 0)
        self.assertEqual(result.prepared_universe["code"].tolist(), ["600001", "600002"])

    def test_get_spot_enriched_a_share_universe_retries_once_before_success(self):
        service = KlineSelectorService()
        spot_df = pd.DataFrame(
            {
                "代码": ["600519"],
                "名称": ["贵州茅台"],
                "总市值": [220_000_000_000.0],
                "最新价": [1800.0],
                "60日涨跌幅": [25.0],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=[RuntimeError("temporary"), spot_df],
            ) as spot_mock, patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(spot_mock.call_count, 2)
        self.assertEqual(universe["code"].tolist(), ["600519"])
        self.assertEqual(float(universe.iloc[0]["change_pct_60d"]), 25.0)

    def test_get_spot_enriched_a_share_universe_falls_back_to_cached_spot_snapshot(self):
        service = KlineSelectorService()
        spot_df = pd.DataFrame(
            {
                "代码": ["600519"],
                "名称": ["贵州茅台"],
                "总市值": [220_000_000_000.0],
                "最新价": [1800.0],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=[spot_df, RuntimeError("offline"), RuntimeError("offline")],
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "get_a_share_universe",
                side_effect=AssertionError("generic fallback should not be used when cache exists"),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ):
                first = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))
                second = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(first["code"].tolist(), ["600519"])
        self.assertEqual(second["code"].tolist(), ["600519"])

    def test_get_spot_enriched_a_share_universe_reuses_cached_quote_fields_when_new_spot_snapshot_is_sparse(self):
        service = KlineSelectorService()
        rich_spot_df = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "total_mv": [220_000_000_000.0],
                "latest_price": [1800.0],
                "pct_change": [2.5],
                "turnover_rate": [1.3],
            }
        )
        sparse_spot_df = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "total_mv": [220_000_000_000.0],
                "latest_price": [1801.0],
                "pct_change": [None],
                "turnover_rate": [None],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=[rich_spot_df, sparse_spot_df],
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                first = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))
                KlineSelectorService._spot_universe_cache = None
                second = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(first["code"].tolist(), ["600519"])
        self.assertEqual(second["code"].tolist(), ["600519"])
        self.assertEqual(float(second.iloc[0]["latest_price"]), 1801.0)
        self.assertEqual(float(second.iloc[0]["pct_change"]), 2.5)
        self.assertEqual(float(second.iloc[0]["turnover_rate"]), 1.3)

    def test_read_spot_universe_reference_cache_preserves_leading_zero_codes(self):
        service = KlineSelectorService()

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            pd.DataFrame(
                {
                    "code": ["000001", "001201", "600519"],
                    "name": ["pingan", "dongrui", "maotai"],
                    "pct_change": [1.2, 2.3, 3.4],
                    "turnover_rate": [0.8, 1.5, 0.9],
                }
            ).to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 3}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                cached = service._read_spot_universe_reference_cache()

        self.assertEqual(cached["code"].tolist(), ["000001", "001201", "600519"])

    def test_read_spot_universe_reference_cache_reuses_memory_copy_within_process(self):
        service = KlineSelectorService()

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            pd.DataFrame(
                {
                    "code": ["000001", "600519"],
                    "name": ["pingan", "maotai"],
                    "pct_change": [1.2, 3.4],
                    "turnover_rate": [0.8, 0.9],
                }
            ).to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ), patch(
                "src.services.kline_selector_service.pd.read_csv",
                wraps=pd.read_csv,
            ) as read_csv_mock:
                first = service._read_spot_universe_reference_cache()
                second = service._read_spot_universe_reference_cache()

        self.assertEqual(read_csv_mock.call_count, 1)
        self.assertEqual(first["code"].tolist(), ["000001", "600519"])
        self.assertEqual(second["code"].tolist(), ["000001", "600519"])

    def test_get_spot_enriched_a_share_universe_merges_disk_cached_spot_quotes_on_generic_fallback(self):
        service = KlineSelectorService()
        spot_df = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "total_mv": [220_000_000_000.0],
                "latest_price": [1800.0],
                "pct_change": [2.5],
                "turnover_rate": [1.3],
            }
        )
        generic_universe = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "list_date": [pd.Timestamp("2001-08-27")],
                "listed_days": [9000],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=[spot_df, RuntimeError("offline"), RuntimeError("offline")],
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "get_a_share_universe",
                return_value=generic_universe.copy(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                first = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))
                KlineSelectorService._spot_universe_cache = None
                second = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(first["code"].tolist(), ["600519"])
        self.assertEqual(second["code"].tolist(), ["600519"])
        self.assertEqual(float(second.iloc[0]["latest_price"]), 1800.0)
        self.assertEqual(float(second.iloc[0]["pct_change"]), 2.5)
        self.assertEqual(float(second.iloc[0]["turnover_rate"]), 1.3)

    def test_get_spot_enriched_a_share_universe_uses_disk_cached_spot_snapshot_before_generic_fallback(self):
        service = KlineSelectorService()
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001", "600519"],
                "name": ["pingan", "maotai"],
                "list_date": ["1991-04-03", "2001-08-27"],
                "listed_days": [10000, 9000],
                "latest_price": [12.3, 1800.0],
                "pct_change": [1.2, 2.5],
                "turnover_rate": [0.8, 1.3],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("offline"),
            ), patch.object(
                service,
                "get_a_share_universe",
                side_effect=AssertionError("generic fallback should not be used when disk spot snapshot exists"),
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(float(universe.iloc[0]["pct_change"]), 1.2)
        self.assertEqual(float(universe.iloc[1]["turnover_rate"]), 1.3)

    def test_get_spot_enriched_a_share_universe_can_prefer_disk_reference_cache(self):
        service = KlineSelectorService()
        service._prefer_spot_universe_reference_cache = True
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001", "600519"],
                "name": ["pingan", "maotai"],
                "list_date": ["1991-04-03", "2001-08-27"],
                "listed_days": [10000, 9000],
                "latest_price": [12.3, 1800.0],
                "pct_change": [1.2, 2.5],
                "turnover_rate": [0.8, 1.3],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("live spot should not be fetched when disk reference is preferred"),
            ) as spot_mock, patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(spot_mock.call_count, 0)
        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(float(universe.iloc[0]["pct_change"]), 1.2)
        self.assertEqual(float(universe.iloc[1]["turnover_rate"]), 1.3)

    def test_get_spot_enriched_a_share_universe_can_prefer_stale_disk_reference_cache(self):
        service = KlineSelectorService()
        service._prefer_spot_universe_reference_cache = True
        service._prefer_stale_spot_universe_reference_cache = True
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001", "600519"],
                "name": ["pingan", "maotai"],
                "list_date": ["1991-04-03", "2001-08-27"],
                "listed_days": [10000, 9000],
                "latest_price": [12.3, 1800.0],
                "pct_change": [1.2, 2.5],
                "turnover_rate": [0.8, 1.3],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            stale_written_at = datetime(2026, 4, 21, 9, 30, 0)
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": stale_written_at.isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("live spot should not be fetched when stale disk reference is preferred"),
            ) as spot_mock, patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                side_effect=AssertionError("listing metadata should not be fetched when stale preferred snapshot is complete"),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_ttl_seconds",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(spot_mock.call_count, 0)
        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(float(universe.iloc[0]["pct_change"]), 1.2)
        self.assertEqual(float(universe.iloc[1]["turnover_rate"]), 1.3)

    def test_get_spot_enriched_a_share_universe_skips_second_live_retry_when_disk_cache_exists(self):
        service = KlineSelectorService()
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001"],
                "name": ["pingan"],
                "pct_change": [1.2],
                "turnover_rate": [0.8],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 1}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("offline"),
            ) as spot_mock, patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(spot_mock.call_count, 1)
        self.assertEqual(universe["code"].tolist(), ["000001"])

    def test_get_spot_enriched_a_share_universe_uses_stale_disk_cache_for_failure_fallback(self):
        service = KlineSelectorService()
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001", "600519"],
                "name": ["pingan", "maotai"],
                "list_date": ["1991-04-03", "2001-08-27"],
                "listed_days": [10000, 9000],
                "latest_price": [12.3, 1800.0],
                "pct_change": [1.2, 2.5],
                "turnover_rate": [0.8, 1.3],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            stale_written_at = datetime(2026, 4, 21, 9, 30, 0)
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": stale_written_at.isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("offline"),
            ) as spot_mock, patch.object(
                service,
                "get_a_share_universe",
                side_effect=AssertionError("generic fallback should not be used when stale disk spot snapshot exists"),
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                side_effect=AssertionError("listing metadata should not be fetched when stale spot snapshot is complete"),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_ttl_seconds",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(spot_mock.call_count, 1)
        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])
        self.assertEqual(float(universe.iloc[0]["pct_change"]), 1.2)
        self.assertEqual(float(universe.iloc[1]["turnover_rate"]), 1.3)

    def test_get_spot_enriched_a_share_universe_skips_listing_metadata_fetch_when_spot_snapshot_is_already_complete(self):
        service = KlineSelectorService()
        cached_spot_df = pd.DataFrame(
            {
                "code": ["000001", "600519"],
                "name": ["pingan", "maotai"],
                "list_date": ["1991-04-03", "2001-08-27"],
                "listed_days": [10000, 9000],
                "pct_change": [1.2, 2.5],
                "turnover_rate": [0.8, 1.3],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            cached_spot_df.to_csv(cache_csv, index=False, encoding="utf-8-sig")
            cache_meta.write_text(
                json.dumps({"written_at": datetime.now().isoformat(), "rows": 2}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=RuntimeError("offline"),
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                side_effect=AssertionError("listing metadata should not be fetched when spot snapshot is complete"),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                1,
                create=True,
            ):
                universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(universe["code"].tolist(), ["000001", "600519"])

    def test_get_spot_enriched_a_share_universe_merges_listing_metadata_on_generic_fallback(self):
        service = KlineSelectorService()
        generic_universe = pd.DataFrame(
            {
                "code": ["301682"],
                "name": ["recent_ipo"],
                "total_mv": [8_000_000_000.0],
            }
        )
        listing_df = pd.DataFrame(
            {
                "code": ["301682"],
                "name": ["recent_ipo"],
                "list_date": ["2025-05-01"],
            }
        )

        with patch.object(
            service,
            "_fetch_universe_from_akshare_spot",
            side_effect=RuntimeError("offline"),
        ), patch.object(
            service,
            "get_a_share_universe",
            return_value=generic_universe.copy(),
        ), patch.object(
            service,
            "_fetch_listing_dates_dataframe",
            return_value=listing_df,
        ), patch.object(
            service,
            "_read_spot_universe_reference_cache",
            return_value=pd.DataFrame(),
        ):
            universe = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(universe["code"].tolist(), ["301682"])
        self.assertEqual(str(universe.iloc[0]["list_date"].date()), "2025-05-01")
        self.assertEqual(int(universe.iloc[0]["listed_days"]), 356)

    def test_get_spot_enriched_a_share_universe_ignores_tiny_disk_cached_spot_snapshot(self):
        service = KlineSelectorService()
        spot_df = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "total_mv": [220_000_000_000.0],
                "latest_price": [1800.0],
                "pct_change": [2.5],
                "turnover_rate": [1.3],
            }
        )
        generic_universe = pd.DataFrame(
            {
                "code": ["600519"],
                "name": ["maotai"],
                "list_date": [pd.Timestamp("2001-08-27")],
                "listed_days": [9000],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_csv = Path(tmp_dir) / "spot_cache.csv"
            cache_meta = Path(tmp_dir) / "spot_cache.json"
            with patch.object(
                service,
                "_fetch_universe_from_akshare_spot",
                side_effect=[spot_df, RuntimeError("offline"), RuntimeError("offline")],
            ), patch.object(
                service,
                "_fetch_listing_dates_dataframe",
                return_value=pd.DataFrame(),
            ), patch.object(
                service,
                "get_a_share_universe",
                return_value=generic_universe.copy(),
            ), patch.object(
                service,
                "_get_spot_universe_reference_cache_paths",
                return_value=(cache_csv, cache_meta),
                create=True,
            ), patch.object(
                service,
                "_spot_universe_reference_cache_min_rows",
                2,
                create=True,
            ):
                service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))
                KlineSelectorService._spot_universe_cache = None
                second = service.get_spot_enriched_a_share_universe(as_of_date=date(2026, 4, 22))

        self.assertEqual(second["code"].tolist(), ["600519"])
        self.assertTrue("latest_price" not in second.columns or pd.isna(second.iloc[0].get("latest_price")))

    def test_evaluate_stock_passes_all_rules(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600001": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria()

        evaluation = service.evaluate_stock(
            stock_code="600001",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.metrics["up_days"], 8)
        self.assertEqual(evaluation.metrics["lookback_days"], 10)
        self.assertEqual(evaluation.metrics["up_ratio"], 0.8)
        self.assertTrue(evaluation.metrics["recent_limit_up_dates"])
        self.assertEqual(evaluation.metrics["new_high_window"], 100)
        self.assertEqual(evaluation.total_market_cap, 40e9)

    def test_evaluate_stock_fails_without_recent_limit_up(self):
        history = build_history(with_limit_up=False)
        manager = FakeManager(history_by_code={"600002": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria()

        evaluation = service.evaluate_stock(
            stock_code="600002",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertFalse(evaluation.passed)
        self.assertIn("limit-up", evaluation.failure_reason)

    def test_build_rules_supports_standalone_hundred_day_high_strategy(self):
        service = KlineSelectorService(manager=FakeManager())
        criteria = KlineSelectorCriteria(
            require_up_day_ratio=False,
            require_recent_limit_up=False,
            require_new_high=True,
        )

        rule_names = [rule.name for rule in service.build_rules(criteria)]

        self.assertEqual(rule_names, ["max_market_cap", "hundred_day_high"])

    def test_evaluate_stock_can_pass_with_only_hundred_day_high_rule(self):
        history = build_history(with_limit_up=False)
        manager = FakeManager(history_by_code={"600012": history})
        service = KlineSelectorService(manager=manager)
        criteria = KlineSelectorCriteria(
            require_up_day_ratio=False,
            require_recent_limit_up=False,
            require_new_high=True,
        )

        evaluation = service.evaluate_stock(
            stock_code="600012",
            stock_name="sample",
            criteria=criteria,
            prefetched_total_market_cap=40e9,
        )

        self.assertTrue(evaluation.passed)
        self.assertEqual(set(evaluation.rule_results.keys()), {"max_market_cap", "hundred_day_high"})

    def test_evaluate_stock_records_phase_metrics(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600013": history})
        service = KlineSelectorService(manager=manager)

        class _PassingRule:
            name = "pass_rule"
            description = "always pass"

            def evaluate(self, _ctx):
                return KlineRuleResult(name=self.name, passed=True, message="ok")

        with patch(
            "src.services.kline_selector_service.time.perf_counter",
            side_effect=[100.0, 101.0, 105.0, 105.0, 106.0, 106.0, 107.0, 107.0, 109.0, 110.0],
        ):
            evaluation = service.evaluate_stock(
                stock_code="600013",
                stock_name="timing_case",
                criteria=KlineSelectorCriteria(),
                prefetched_total_market_cap=40e9,
                rules=[_PassingRule()],
            )

        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.phase_metrics["history_fetch_elapsed_sec"], 4.0)
        self.assertEqual(evaluation.phase_metrics["history_prepare_elapsed_sec"], 1.0)
        self.assertEqual(evaluation.phase_metrics["market_cap_resolve_elapsed_sec"], 0.0)
        self.assertEqual(evaluation.phase_metrics["rule_evaluate_elapsed_sec"], 1.0)
        self.assertEqual(evaluation.phase_metrics["evaluation_elapsed_sec"], 7.0)

    def test_evaluate_stock_skips_market_cap_resolve_after_other_rules_fail(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600016": history})
        service = KlineSelectorService(manager=manager)

        class _FailingRule:
            name = "history_only_fail"
            description = "always fail before market-cap rule"

            def evaluate(self, _ctx):
                return KlineRuleResult(name=self.name, passed=False, message="history rule failed")

        with patch.object(service, "_resolve_total_market_cap", side_effect=AssertionError("should not resolve market cap")):
            evaluation = service.evaluate_stock(
                stock_code="600016",
                stock_name="lazy_cap_case",
                criteria=KlineSelectorCriteria(),
                prefetched_total_market_cap=None,
                rules=[_FailingRule(), service.build_rules(KlineSelectorCriteria())[0]],
            )

        self.assertFalse(evaluation.passed)
        self.assertEqual(evaluation.failure_reason, "history rule failed")
        self.assertEqual(evaluation.phase_metrics["market_cap_resolve_elapsed_sec"], 0.0)

    def test_scan_market_prefilters_large_caps_before_history_fetch(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600003": history, "600004": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600003", "600004"],
                    "name": ["large_cap", "selected"],
                    "total_mv": [600e8, 40e9],
                }
            ),
        )
        criteria = KlineSelectorCriteria(max_total_market_cap=50e9)

        run_result = service.scan_market(criteria=criteria)

        self.assertEqual(run_result.universe_size, 2)
        self.assertEqual(run_result.skipped_market_cap_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual([item.stock_code for item in run_result.selected], ["600004"])
        self.assertEqual(manager.history_calls, [("600004", criteria.history_days_required)])

    def test_scan_market_supports_parallel_manager_factory(self):
        history = build_history(with_limit_up=True)
        created_managers = []

        def manager_factory():
            manager = FakeManager(
                history_by_code={
                    "600005": history,
                    "600006": history,
                }
            )
            created_managers.append(manager)
            return manager

        service = KlineSelectorService(
            manager_factory=manager_factory,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600005", "600006"],
                    "name": ["parallel_a", "parallel_b"],
                    "total_mv": [40e9, 41e9],
                }
            ),
        )

        run_result = service.scan_market(criteria=KlineSelectorCriteria(), max_workers=2)

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(
            sorted(item.stock_code for item in run_result.selected),
            ["600005", "600006"],
        )
        self.assertGreaterEqual(len(created_managers), 1)
        self.assertEqual(sum(len(manager.history_calls) for manager in created_managers), 2)

    def test_scan_market_prefilter_reduces_history_fetches(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600007": history})
        criteria = KlineSelectorCriteria()
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600007", "600008"],
                    "name": ["prefilter_hit", "prefilter_skip"],
                    "total_mv": [40e9, 41e9],
                    "change_pct_60d": [15.0, 3.0],
                }
            ),
        )

        run_result = service.scan_market(
            criteria=criteria,
            prefilter=KlineSelectorPrefilter(min_change_pct_60d=10.0),
        )

        self.assertEqual(run_result.skipped_prefilter_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600007", criteria.history_days_required)])

    def test_scan_market_aggregates_evaluation_phase_metrics(self):
        service = KlineSelectorService(
            manager=FakeManager(),
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600014", "600015"],
                    "name": ["phase_a", "phase_b"],
                    "total_mv": [40e9, 41e9],
                }
            ),
        )
        evaluation_a = KlineSelectionEvaluation(
            stock_code="600014",
            stock_name="phase_a",
            passed=True,
            phase_metrics={
                "history_fetch_elapsed_sec": 4.0,
                "history_prepare_elapsed_sec": 1.0,
                "market_cap_resolve_elapsed_sec": 0.5,
                "rule_evaluate_elapsed_sec": 2.0,
                "evaluation_elapsed_sec": 7.5,
            },
        )
        evaluation_b = KlineSelectionEvaluation(
            stock_code="600015",
            stock_name="phase_b",
            passed=False,
            failure_reason="blocked",
            phase_metrics={
                "history_fetch_elapsed_sec": 6.0,
                "history_prepare_elapsed_sec": 2.0,
                "market_cap_resolve_elapsed_sec": 1.5,
                "rule_evaluate_elapsed_sec": 3.0,
                "evaluation_elapsed_sec": 12.5,
            },
        )

        with patch.object(service, "evaluate_stock", side_effect=[evaluation_a, evaluation_b]):
            run_result = service.scan_market(criteria=KlineSelectorCriteria(), max_workers=1)

        self.assertEqual(run_result.phase_metrics["selection_phase_metrics_sample_count"], 2)
        self.assertEqual(run_result.phase_metrics["selection_history_fetch_elapsed_sec_sum"], 10.0)
        self.assertEqual(run_result.phase_metrics["selection_history_prepare_elapsed_sec_sum"], 3.0)
        self.assertEqual(run_result.phase_metrics["selection_market_cap_resolve_elapsed_sec_sum"], 2.0)
        self.assertEqual(run_result.phase_metrics["selection_rule_evaluate_elapsed_sec_sum"], 5.0)
        self.assertEqual(run_result.phase_metrics["selection_evaluation_elapsed_sec_sum"], 20.0)
        self.assertEqual(run_result.phase_metrics["selection_history_fetch_elapsed_sec_avg"], 5.0)
        self.assertEqual(run_result.phase_metrics["selection_rule_evaluate_elapsed_sec_avg"], 2.5)

    def test_scan_market_short_circuits_listed_days_before_history_fetch(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600031": history})
        criteria = KlineSelectorCriteria()
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600030", "600031"],
                    "name": ["recent_ipo", "seasoned"],
                    "total_mv": [20e9, 21e9],
                    "listed_days": [90, 180],
                }
            ),
        )

        run_result = service.scan_market(
            criteria=criteria,
            prefilter=KlineSelectorPrefilter(min_listed_days=120),
        )

        self.assertEqual(run_result.skipped_listed_days_count, 1)
        self.assertEqual(run_result.skipped_prefilter_count, 0)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600031", criteria.history_days_required)])

    def test_prefilter_skip_reason_distinguishes_listed_days_short_circuit(self):
        reason = KlineSelectorService._get_prefilter_skip_reason(
            "recent_ipo",
            {"listed_days": 45},
            KlineSelectorPrefilter(min_listed_days=120),
        )

        self.assertIsNotNone(reason)
        self.assertIn("listed_days_prefilter", str(reason))
        self.assertIn("need >= 120", str(reason))

    def test_scan_market_does_not_fetch_listing_metadata_when_listed_days_missing(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600032": history})
        criteria = KlineSelectorCriteria()
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600032"],
                    "name": ["missing_listed_days"],
                    "total_mv": [20e9],
                }
            ),
        )

        with patch.object(service, "_fetch_listing_dates_dataframe", side_effect=AssertionError("should not fetch")):
            run_result = service.scan_market(
                criteria=criteria,
                prefilter=KlineSelectorPrefilter(min_listed_days=120),
            )

        self.assertEqual(run_result.skipped_listed_days_count, 0)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600032", criteria.history_days_required)])

    def test_scan_market_invokes_on_evaluation_callback(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600070": history, "600071": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600070", "600071"],
                    "name": ["callback_a", "callback_b"],
                    "total_mv": [40e9, 41e9],
                }
            ),
        )
        callback_events = []

        run_result = service.scan_market(
            criteria=KlineSelectorCriteria(),
            on_evaluation=lambda evaluation, completed, total: callback_events.append(
                (evaluation.stock_code, evaluation.passed, completed, total)
            ),
        )

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(len(callback_events), 2)
        self.assertEqual({event[0] for event in callback_events}, {"600070", "600071"})
        self.assertTrue(all(event[1] for event in callback_events))
        self.assertTrue(all(event[3] == 2 for event in callback_events))

    def test_apply_universe_shard_slices_codes_deterministically(self):
        universe = pd.DataFrame(
            {
                "code": ["000001", "000002", "000003", "000004", "000005"],
                "name": ["a", "b", "c", "d", "e"],
                "total_mv": [10e9, 11e9, 12e9, 13e9, 14e9],
            }
        )

        shard = KlineSelectorService.apply_universe_shard(universe, shard_count=3, shard_index=1)

        self.assertEqual(shard["code"].tolist(), ["000002", "000005"])

    def test_scan_market_only_evaluates_the_requested_shard(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(
            history_by_code={
                "600100": history,
                "600101": history,
                "600102": history,
                "600103": history,
            }
        )
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600100", "600101", "600102", "600103"],
                    "name": ["a", "b", "c", "d"],
                    "total_mv": [40e9, 41e9, 42e9, 43e9],
                }
            ),
        )

        run_result = service.scan_market(
            criteria=KlineSelectorCriteria(),
            shard_count=2,
            shard_index=1,
        )
        history_days_required = KlineSelectorCriteria().history_days_required

        self.assertEqual(run_result.universe_codes, ["600101", "600103"])
        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(
            manager.history_calls,
            [
                ("600101", history_days_required),
                ("600103", history_days_required),
            ],
        )

    def test_scan_market_resume_uses_checkpoint(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600010": history, "600011": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600010", "600011"],
                    "name": ["resume_a", "resume_b"],
                    "total_mv": [40e9, 41e9],
                    "change_pct_60d": [15.0, 15.0],
                }
            ),
        )
        criteria = KlineSelectorCriteria()
        prefilter = KlineSelectorPrefilter(min_change_pct_60d=10.0)

        partial_selected = KlineSelectionEvaluation(
            stock_code="600010",
            stock_name="resume_a",
            passed=True,
            history_source="fake",
            total_market_cap=40e9,
            metrics={
                "up_days": 8,
                "lookback_days": 10,
                "up_ratio": 0.8,
                "recent_limit_up_dates": ["2025-12-01"],
                "latest_high": 24.45,
                "window_high": 24.45,
                "new_high_window": 100,
            },
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "resume.json"
            service._save_checkpoint(
                checkpoint_path=checkpoint_path,
                criteria=criteria,
                prefilter=prefilter,
                universe=service.get_a_share_universe(),
                skipped_market_cap_count=0,
                skipped_prefilter_count=0,
                skipped_listed_days_count=0,
                selected=[partial_selected],
                failed=[],
            )

            run_result = service.scan_market(
                criteria=criteria,
                prefilter=prefilter,
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(run_result.evaluated_count, 2)
        self.assertEqual(sorted(item.stock_code for item in run_result.selected), ["600010", "600011"])
        self.assertEqual(manager.history_calls, [("600011", criteria.history_days_required)])

    def test_scan_market_resume_keeps_skip_counts_stable(self):
        history = build_history(with_limit_up=True)
        manager = FakeManager(history_by_code={"600021": history})
        service = KlineSelectorService(
            manager=manager,
            universe_provider=lambda: pd.DataFrame(
                {
                    "code": ["600020", "600021", "600022"],
                    "name": ["large_cap", "resume_target", "prefilter_skip"],
                    "total_mv": [600e8, 40e9, 41e9],
                    "change_pct_60d": [20.0, 15.0, 3.0],
                }
            ),
        )
        criteria = KlineSelectorCriteria(max_total_market_cap=50e9)
        prefilter = KlineSelectorPrefilter(min_change_pct_60d=10.0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "resume_skip_counts.json"
            service._save_checkpoint(
                checkpoint_path=checkpoint_path,
                criteria=criteria,
                prefilter=prefilter,
                universe=service.get_a_share_universe(),
                skipped_market_cap_count=1,
                skipped_prefilter_count=1,
                skipped_listed_days_count=0,
                selected=[],
                failed=[],
            )

            run_result = service.scan_market(
                criteria=criteria,
                prefilter=prefilter,
                checkpoint_path=checkpoint_path,
                checkpoint_every=1,
                resume=True,
            )

        self.assertEqual(run_result.skipped_market_cap_count, 1)
        self.assertEqual(run_result.skipped_prefilter_count, 1)
        self.assertEqual(run_result.evaluated_count, 1)
        self.assertEqual(manager.history_calls, [("600021", criteria.history_days_required)])


if __name__ == "__main__":
    unittest.main()
