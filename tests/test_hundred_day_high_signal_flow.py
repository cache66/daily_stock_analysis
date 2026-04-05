# -*- coding: utf-8 -*-
"""Integration-style tests for hundred-day high signal enrichment/export flow."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from scripts.select_hundred_day_high_candidates import (
    SIGNAL_TYPE,
    build_criteria_payload,
    build_markdown_report,
    enrich_selected_results,
    export_results,
)
from src.config import Config
from src.services.kline_selector_service import (
    KlineSelectionEvaluation,
    KlineSelectorCriteria,
    KlineSelectorRunResult,
)
from src.storage import DatabaseManager


class _FakeCauseAnalysisService:
    def __init__(self, summary: str):
        self.summary = summary

    def analyze_signal(self, stock_code: str, stock_name: str, *, signal_type: str, metrics_payload):
        return {
            "analysis_status": "llm",
            "industry": "白酒",
            "reason_summary": self.summary,
            "industry_logic": "白酒板块维持强势。",
            "news_logic": "近期没有新的利空，消息面偏中性。",
            "technical_logic": "股价创近期新高，短线按突破延续看待。",
            "cause_tags": ["sector_rotation"],
            "theme_label": "消费涨价 / 食品饮料",
            "us_proxy_examples": ["STZ"],
            "mapping_evidence": ["白酒"],
            "evidence_points": ["消费板块维持强势。"],
            "fact_vs_inference": {"facts": ["所属行业为白酒。"], "inferences": ["上涨更像消费主线延续。"]},
            "news_items": [],
            "fundamental_context": {},
        }


class HundredDayHighSignalFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_hundred_day_high_signal_flow.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _build_run_result(self) -> KlineSelectorRunResult:
        evaluation = KlineSelectionEvaluation(
            stock_code="600519",
            stock_name="贵州茅台",
            passed=True,
            history_source="fake",
            total_market_cap=220_000_000_000.0,
            metrics={
                "close": 1800.0,
                "latest_high": 1818.0,
                "window_high": 1818.0,
                "new_high_window": 100,
            },
        )
        criteria = KlineSelectorCriteria(
            require_up_day_ratio=False,
            require_recent_limit_up=False,
            require_new_high=True,
        )
        return KlineSelectorRunResult(
            criteria=criteria,
            universe_size=1,
            evaluated_count=1,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            universe_codes=["600519"],
            selected=[evaluation],
            failed=[],
        )

    def test_enrich_selected_results_persists_and_updates_same_day_snapshot(self):
        snapshot_date = date(2026, 4, 4)
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date="2026-04-02",
            code="600519",
            name="贵州茅台",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 1700.0},
            history_payload={"previous_hit_count": 0},
        )

        run_result = self._build_run_result()
        criteria_payload = build_criteria_payload(
            run_result.criteria,
            prefilter=None,
            snapshot_date=snapshot_date,
        )

        first_df = enrich_selected_results(
            run_result,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=180,
            db=self.db,
            cause_analysis_service=_FakeCauseAnalysisService("第一次原因摘要"),
            perform_cause_analysis=True,
            persist_snapshot=True,
        )
        second_df = enrich_selected_results(
            run_result,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=180,
            db=self.db,
            cause_analysis_service=_FakeCauseAnalysisService("第二次原因摘要"),
            perform_cause_analysis=True,
            persist_snapshot=True,
        )

        self.assertEqual(first_df.iloc[0]["previous_hit_count"], 1)
        self.assertEqual(first_df.iloc[0]["latest_previous_hit_date"], "2026-04-02")
        self.assertEqual(second_df.iloc[0]["reason_summary"], "第二次原因摘要")
        self.assertEqual(second_df.iloc[0]["industry_logic"], "白酒板块维持强势。")
        self.assertEqual(second_df.iloc[0]["news_logic"], "近期没有新的利空，消息面偏中性。")
        self.assertEqual(second_df.iloc[0]["technical_logic"], "股价创近期新高，短线按突破延续看待。")

        rows = self.db.get_signal_snapshots(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="600519",
        )
        self.assertEqual(len(rows), 1)
        cause_payload = json.loads(rows[0].cause_payload)
        self.assertEqual(cause_payload["reason_summary"], "第二次原因摘要")
        self.assertEqual(cause_payload["industry_logic"], "白酒板块维持强势。")
        self.assertEqual(cause_payload["news_logic"], "近期没有新的利空，消息面偏中性。")
        self.assertEqual(cause_payload["technical_logic"], "股价创近期新高，短线按突破延续看待。")

    def test_build_markdown_report_includes_new_summary_columns(self):
        run_result = self._build_run_result()
        criteria_payload = build_criteria_payload(
            run_result.criteria,
            prefilter=None,
            snapshot_date=date(2026, 4, 4),
        )
        selected_df = enrich_selected_results(
            run_result,
            snapshot_date=date(2026, 4, 4),
            criteria_payload=criteria_payload,
            history_lookback_days=180,
            db=self.db,
            cause_analysis_service=_FakeCauseAnalysisService("消费主线继续强化"),
            perform_cause_analysis=True,
            persist_snapshot=False,
        )

        markdown = build_markdown_report(
            run_result,
            selected_df,
            "2026-04-04 18:00:00",
            snapshot_date=date(2026, 4, 4),
            history_lookback_days=180,
        )

        self.assertIn("上涨原因", markdown)
        self.assertIn("消费主线继续强化", markdown)
        self.assertIn("距上次(天)", markdown)
        self.assertIn("逻辑拆解", markdown)
        self.assertIn("行业逻辑", markdown)
        self.assertIn("消息逻辑", markdown)
        self.assertIn("技术逻辑", markdown)

    def test_export_results_keeps_csv_headers_when_no_selection(self):
        run_result = KlineSelectorRunResult(
            criteria=KlineSelectorCriteria(
                require_up_day_ratio=False,
                require_recent_limit_up=False,
                require_new_high=True,
            ),
            universe_size=2,
            evaluated_count=2,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            universe_codes=["600001", "600002"],
            selected=[],
            failed=[],
        )

        output_dir = Path(self._temp_dir.name) / "export_empty"
        export_results(
            run_result,
            output_dir,
            snapshot_date=date(2026, 4, 4),
            history_lookback_days=180,
            checkpoint_path=None,
            selected_df=None,
        )

        csv_text = (output_dir / "hundred_day_high_candidates.csv").read_text(encoding="utf-8-sig")
        self.assertIn("reason_summary", csv_text)
        self.assertIn("industry_logic", csv_text)
        self.assertIn("news_logic", csv_text)
        self.assertIn("technical_logic", csv_text)
        self.assertIn("latest_previous_hit_date", csv_text)


if __name__ == "__main__":
    unittest.main()
