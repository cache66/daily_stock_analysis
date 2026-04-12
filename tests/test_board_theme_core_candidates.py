# -*- coding: utf-8 -*-
"""Tests for board constituents and board-scoped theme-core scans."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import BaseFetcher, DataFetcherManager
from scripts.select_board_theme_core_candidates import (
    fetch_board_universe,
    scan_board_theme_core_candidates,
    write_board_outputs,
)


class _BoardConstituentFetcher(BaseFetcher):
    name = "BoardConstituentFetcher"
    priority = 0

    def __init__(self) -> None:
        self.calls = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    def get_board_constituents(self, board_name: str, *, board_type: str = "auto"):
        self.calls.append((board_name, board_type))
        return pd.DataFrame(
            [
                {"代码": "601869", "名称": "长飞光纤"},
                {"代码": "300308", "名称": "中际旭创"},
            ]
        )


class _FakeThemeMapper:
    def analyze_stock(self, stock_code: str, *, stock_name=None, commodity_hint=None, market_hint=None):
        payloads = {
            "601869": {
                "status": "ok",
                "stock_code": "601869",
                "stock_name": "长飞光纤",
                "theme_key": "optical_communication",
                "subtheme_key": "preform_and_materials",
                "stock_role": "source_beneficiary",
                "core_driver_type": "price_pass_through",
                "theme_core_probability": "high",
                "subtheme_core_probability": "high",
                "theme_core_score": 3,
                "subtheme_core_score": 3,
                "is_direct_beneficiary": True,
                "directness": "direct_beneficiary",
                "leader_type": "hybrid_leader",
                "leader_probability": "high",
                "recognizability_score": 3,
                "relative_strength_score": 2,
                "liquidity_score": 2,
                "combo_reinforcement_score": 3,
                "summary": "source core",
                "warnings": [],
            },
            "300308": {
                "status": "ok",
                "stock_code": "300308",
                "stock_name": "中际旭创",
                "theme_key": "optical_communication",
                "subtheme_key": "optical_module_and_cpo",
                "stock_role": "prosperity_core",
                "core_driver_type": "subtheme_prosperity",
                "theme_core_probability": "high",
                "subtheme_core_probability": "high",
                "theme_core_score": 3,
                "subtheme_core_score": 3,
                "is_direct_beneficiary": False,
                "directness": "pseudo_or_cost_pressure",
                "leader_type": "capital_leader",
                "leader_probability": "high",
                "recognizability_score": 2,
                "relative_strength_score": 3,
                "liquidity_score": 3,
                "combo_reinforcement_score": 0,
                "summary": "cpo core",
                "warnings": [],
            },
        }
        return payloads[stock_code]


class BoardThemeCoreCandidateTestCase(unittest.TestCase):
    def test_fetch_board_universe_normalizes_constituents(self):
        fetcher = _BoardConstituentFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])

        df = fetch_board_universe(board_name="CPO", board_type="concept", manager=manager)

        self.assertEqual(list(df["code"]), ["601869", "300308"])
        self.assertEqual(fetcher.calls, [("CPO", "concept")])

    def test_scan_board_theme_core_candidates_uses_board_universe(self):
        fetcher = _BoardConstituentFetcher()
        manager = DataFetcherManager(fetchers=[fetcher])

        board_df, candidates, universe_size = scan_board_theme_core_candidates(
            board_name="CPO",
            board_type="concept",
            commodity_hint="optical_fiber",
            manager=manager,
            mapper_service=_FakeThemeMapper(),
            top_per_subtheme=1,
        )

        self.assertEqual(universe_size, 2)
        self.assertEqual(len(board_df), 2)
        self.assertEqual([item.stock_code for item in candidates], ["601869", "300308"])

    def test_write_board_outputs_emits_constituent_csv_and_core_csv(self):
        board_df = pd.DataFrame(
            [
                {"code": "601869", "name": "长飞光纤", "board_name": "CPO", "board_type": "concept"},
                {"code": "300308", "name": "中际旭创", "board_name": "CPO", "board_type": "concept"},
            ]
        )
        _, candidates, universe_size = scan_board_theme_core_candidates(
            board_name="CPO",
            board_type="concept",
            commodity_hint="optical_fiber",
            manager=DataFetcherManager(fetchers=[_BoardConstituentFetcher()]),
            mapper_service=_FakeThemeMapper(),
            top_per_subtheme=1,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_board_outputs(
                board_name="CPO",
                board_type="concept",
                commodity_hint="optical_fiber",
                board_df=board_df,
                candidates=candidates,
                universe_size=universe_size,
                output_dir=Path(tmpdir),
            )
            self.assertTrue(paths["constituents_csv"].exists())
            self.assertTrue(paths["csv"].exists())


if __name__ == "__main__":
    unittest.main()
