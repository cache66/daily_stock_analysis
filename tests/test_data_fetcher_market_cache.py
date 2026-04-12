# -*- coding: utf-8 -*-
"""Tests for market-level caches inside DataFetcherManager."""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import BaseFetcher, DataFetcherManager


class _MarketCacheFetcher(BaseFetcher):
    name = "MarketCacheFetcher"
    priority = 0

    def __init__(self) -> None:
        self.board_calls = 0
        self.sector_calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    def get_belong_board(self, stock_code: str):
        self.board_calls += 1
        return [{"name": "光通信", "type": "行业"}]

    def get_sector_rankings(self, n: int = 5):
        self.sector_calls += 1
        return (
            [{"name": "光通信", "change_pct": 3.2}],
            [{"name": "煤炭", "change_pct": -1.1}],
        )


class DataFetcherMarketCacheTestCase(unittest.TestCase):
    def test_get_belong_boards_uses_ttl_cache(self) -> None:
        fetcher = _MarketCacheFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])

        first = manager.get_belong_boards("601869")
        second = manager.get_belong_boards("601869")

        self.assertEqual(first, second)
        self.assertEqual(fetcher.board_calls, 1)

    def test_get_sector_rankings_uses_ttl_cache(self) -> None:
        fetcher = _MarketCacheFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])

        first = manager.get_sector_rankings(10)
        second = manager.get_sector_rankings(10)

        self.assertEqual(first, second)
        self.assertEqual(fetcher.sector_calls, 1)


if __name__ == "__main__":
    unittest.main()
