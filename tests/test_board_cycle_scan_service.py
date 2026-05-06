# -*- coding: utf-8 -*-
"""Failing tests for BoardCycleScanService contract."""

import importlib
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _FakeManager:
    def __init__(self, *, stock_name: str, boards, fundamental_context) -> None:
        self._stock_name = stock_name
        self._boards = boards
        self._fundamental_context = fundamental_context

    def get_stock_name(self, stock_code: str, allow_realtime: bool = True):
        return self._stock_name

    def get_belong_boards(self, stock_code: str):
        return self._boards

    def get_fundamental_context(self, stock_code: str):
        return self._fundamental_context

    def build_failed_fundamental_context(self, stock_code: str, reason: str):
        return {"status": "failed", "errors": [reason]}


def _load_service_class():
    module = importlib.import_module("src.services.board_cycle_scan_service")
    return getattr(module, "BoardCycleScanService")


def _build_service(*, stock_name: str, boards, fundamental_context):
    service_cls = _load_service_class()
    manager = _FakeManager(stock_name=stock_name, boards=boards, fundamental_context=fundamental_context)
    return service_cls(manager=manager)


def _make_evaluation(**overrides):
    payload = {
        "stock_code": "000000",
        "stock_name": "sample",
        "board_name": "锂矿",
        "board_type": "industry",
        "stock_role": "watch",
        "board_stock_score": 1.0,
        "logic_match_score": 0.0,
        "board_leader_score": 0.0,
        "earnings_support_score": 0.0,
        "earnings_supported": False,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class BoardCycleScanServiceTestCase(unittest.TestCase):
    def test_score_board_stock_classifies_leader_role_from_logic_and_earnings_support(self):
        service = _build_service(
            stock_name="赣锋锂业",
            boards=[
                {"name": "锂矿", "type": "industry"},
                {"name": "固态电池", "type": "concept"},
            ],
            fundamental_context={
                "status": "ok",
                "growth": {"data": {"revenue_yoy": 18.0, "net_profit_yoy": 26.0}},
                "earnings": {"data": {"financial_report": {"report_date": "2026-03-31"}}},
                "earnings_quality": {
                    "data": {
                        "verdict": "good",
                        "score_total": 76,
                        "cycle_analysis": {"phase": "reaccelerating"},
                    }
                },
            },
        )

        evaluation = service._score_board_stock(
            stock_code="002460",
            stock_name="赣锋锂业",
            board_name="锂矿",
            board_type="industry",
            board_context={
                "board_name": "锂矿",
                "board_type": "industry",
                "logic_keywords": ["锂", "锂盐", "锂矿"],
                "leader_candidates": ["赣锋锂业", "天齐锂业"],
            },
        )

        self.assertEqual(evaluation.stock_role, "leader")
        self.assertGreaterEqual(evaluation.board_leader_score, evaluation.earnings_support_score)
        self.assertGreater(evaluation.logic_match_score, 0)
        self.assertGreater(evaluation.board_stock_score, evaluation.earnings_support_score)

    def test_score_board_returns_strengthening_cycle_label_from_evaluations(self):
        service = _build_service(
            stock_name="赣锋锂业",
            boards=[{"name": "锂矿", "type": "industry"}],
            fundamental_context={"status": "ok"},
        )

        evaluations = [
            _make_evaluation(
                stock_code="002460",
                stock_name="赣锋锂业",
                board_name="锂矿",
                stock_role="leader",
                board_stock_score=9.0,
                logic_match_score=3.0,
                board_leader_score=4.0,
                earnings_support_score=2.0,
                earnings_supported=True,
            ),
            _make_evaluation(
                stock_code="002466",
                stock_name="天齐锂业",
                board_name="锂矿",
                stock_role="watch",
                board_stock_score=4.0,
                logic_match_score=1.0,
                board_leader_score=1.0,
                earnings_support_score=0.0,
                earnings_supported=False,
            ),
        ]

        board_score = service._score_board(
            board_name="锂矿",
            board_type="industry",
            evaluations=evaluations,
        )

        self.assertEqual(board_score.board_cycle_label, "strengthening")
        self.assertEqual(board_score.leader_count, 1)
        self.assertEqual(board_score.earnings_supported_count, 1)
        self.assertGreater(board_score.board_cycle_score, 0)

    def test_resolve_primary_boards_routes_multi_board_stock_to_logic_matched_primary_board(self):
        service = _build_service(
            stock_name="赣锋锂业",
            boards=[
                {"name": "锂矿", "type": "industry"},
                {"name": "新能源车", "type": "concept"},
            ],
            fundamental_context={"status": "ok"},
        )

        evaluations = [
            _make_evaluation(
                stock_code="002460",
                stock_name="赣锋锂业",
                board_name="锂矿",
                stock_role="leader",
                board_stock_score=9.0,
                logic_match_score=3.0,
                board_leader_score=4.0,
                earnings_support_score=2.0,
                earnings_supported=True,
            ),
            _make_evaluation(
                stock_code="002460",
                stock_name="赣锋锂业",
                board_name="新能源车",
                board_type="concept",
                stock_role="watch",
                board_stock_score=5.0,
                logic_match_score=1.0,
                board_leader_score=1.0,
                earnings_support_score=1.0,
                earnings_supported=True,
            ),
        ]

        resolved = service._resolve_primary_boards(
            stock_code="002460",
            stock_name="赣锋锂业",
            evaluations=evaluations,
        )

        reason = str(resolved["primary_board_reason"] or "")
        self.assertEqual(resolved["primary_board"], "锂矿")
        self.assertTrue(reason)
        self.assertIn("logic_match", reason)

    def test_score_board_stock_keeps_fail_open_when_earnings_data_is_missing(self):
        service = _build_service(
            stock_name="融捷股份",
            boards=[{"name": "锂矿", "type": "industry"}],
            fundamental_context={
                "status": "ok",
                "growth": {"data": {"revenue_yoy": 12.0, "net_profit_yoy": 8.0}},
                "earnings": {"data": {}},
                "earnings_quality": {"data": {}},
            },
        )

        evaluation = service._score_board_stock(
            stock_code="002192",
            stock_name="融捷股份",
            board_name="锂矿",
            board_type="industry",
            board_context={
                "board_name": "锂矿",
                "board_type": "industry",
                "logic_keywords": ["锂", "矿"],
                "leader_candidates": ["赣锋锂业", "天齐锂业"],
            },
        )

        self.assertEqual(evaluation.stock_role, "watch")
        self.assertEqual(evaluation.earnings_support_score, 0)
        self.assertGreater(evaluation.board_stock_score, 0)


if __name__ == "__main__":
    unittest.main()
