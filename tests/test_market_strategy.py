# -*- coding: utf-8 -*-
"""Tests for market strategy blueprints."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.core.market_strategy import get_market_strategy_blueprint
from src.market_analyzer import MarketAnalyzer, MarketOverview
from src.services.limit_up_review_service import LimitUpReviewService


class TestMarketStrategyBlueprint(unittest.TestCase):
    """Validate CN/US strategy blueprint basics."""

    def test_cn_blueprint_contains_action_framework(self):
        blueprint = get_market_strategy_blueprint("cn")
        block = blueprint.to_prompt_block()

        self.assertIn("A股市场三段式复盘策略", block)
        self.assertIn("Action Framework", block)
        self.assertIn("进攻", block)

    def test_us_blueprint_contains_regime_strategy(self):
        blueprint = get_market_strategy_blueprint("us")
        block = blueprint.to_prompt_block()

        self.assertIn("US Market Regime Strategy", block)
        self.assertIn("Risk-on", block)
        self.assertIn("Macro & Flows", block)


class TestMarketAnalyzerStrategyPrompt(unittest.TestCase):
    """Validate strategy section is injected into prompt/report."""

    def test_cn_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="cn")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("策略计划", prompt)
        self.assertIn("A股市场三段式复盘策略", prompt)

    def test_us_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="us")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("Strategy Plan", prompt)
        self.assertIn("US Market Regime Strategy", prompt)

    def test_cn_limit_up_review_block_renders_markdown_table(self):
        service = LimitUpReviewService()
        block = service.build_markdown_block(
            [
                {
                    "code": "603538",
                    "name": "美诺华",
                    "board_count": 5,
                    "sealed_amount_yi": 1.23,
                    "limit_up_stats": "5/5",
                    "reason": "近期多次涨停",
                    "historical_limit_up_count": 13,
                    "industry": "化学制药",
                }
            ]
        )

        self.assertIn("今日涨停股复盘表", block)
        self.assertIn("美诺华", block)
        self.assertIn("近期多次涨停", block)
        self.assertIn("13", block)

    def test_cn_prompt_uses_english_shell_when_report_language_is_en(self):
        with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="en")):
            analyzer = MarketAnalyzer(region="cn")

        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("# Today's Market Data", prompt)
        self.assertIn("### 1. Market Summary", prompt)
        self.assertIn("A-share Three-Phase Recap Strategy", prompt)
        self.assertNotIn("### 一、市场总结", prompt)
        self.assertNotIn("A股市场三段式复盘策略", prompt)


if __name__ == "__main__":
    unittest.main()
