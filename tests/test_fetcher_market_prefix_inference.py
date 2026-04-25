# -*- coding: utf-8 -*-
"""Regression tests for market-prefix inference in fallback fetchers."""

import sys
import unittest
from unittest.mock import MagicMock

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher


class TestFetcherMarketPrefixInference(unittest.TestCase):
    def test_baostock_convert_stock_code_supports_new_sz_and_sh_prefixes(self) -> None:
        fetcher = BaostockFetcher()
        self.assertEqual(fetcher._convert_stock_code("001389"), "sz.001389")
        self.assertEqual(fetcher._convert_stock_code("301396"), "sz.301396")
        self.assertEqual(fetcher._convert_stock_code("605099"), "sh.605099")

    def test_yfinance_convert_stock_code_supports_new_sz_and_sh_prefixes(self) -> None:
        fetcher = YfinanceFetcher()
        self.assertEqual(fetcher._convert_stock_code("001389"), "001389.SZ")
        self.assertEqual(fetcher._convert_stock_code("301396"), "301396.SZ")
        self.assertEqual(fetcher._convert_stock_code("605099"), "605099.SS")


if __name__ == "__main__":
    unittest.main()
