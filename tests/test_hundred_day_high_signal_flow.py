# -*- coding: utf-8 -*-
"""Integration-style tests for hundred-day high signal enrichment/export flow."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from scripts.select_hundred_day_high_candidates import (
    DEFAULT_PROFILE_NAME,
    SIGNAL_TYPE,
    main as hundred_day_high_main,
    build_selected_dataframe,
    build_criteria_payload,
    build_markdown_report,
    enrich_selected_results,
    export_results,
    load_snapshot_run_result,
    persist_selected_results,
    resolve_profile_settings,
)
from scripts.collect_hundred_day_high_profile_snapshots import build_profile_signal_type
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

    def test_persist_selected_results_supports_snapshot_first_then_cause_backfill(self):
        run_result = self._build_run_result()
        snapshot_date = date(2026, 4, 4)
        criteria_payload = build_criteria_payload(
            run_result.criteria,
            prefilter=None,
            snapshot_date=snapshot_date,
        )

        quick_df = persist_selected_results(
            run_result,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=180,
            db=self.db,
        )
        self.assertEqual(len(quick_df), 1)
        self.assertEqual(quick_df.iloc[0]["reason_summary"], "")

        loaded_run_result = load_snapshot_run_result(
            self.db,
            snapshot_date=snapshot_date,
        )
        self.assertEqual([item.stock_code for item in loaded_run_result.selected], ["600519"])

        enriched_df = enrich_selected_results(
            loaded_run_result,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=180,
            db=self.db,
            cause_analysis_service=_FakeCauseAnalysisService("二阶段补全原因摘要"),
            perform_cause_analysis=True,
            persist_snapshot=True,
        )
        self.assertEqual(enriched_df.iloc[0]["reason_summary"], "二阶段补全原因摘要")

        rows = self.db.get_signal_snapshots(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="600519",
        )
        self.assertEqual(len(rows), 1)
        cause_payload = json.loads(rows[0].cause_payload)
        self.assertEqual(cause_payload["reason_summary"], "二阶段补全原因摘要")

    def test_enrich_selected_results_can_preserve_existing_profile_metadata(self):
        snapshot_date = date(2026, 4, 4)
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="600519",
            name="璐靛窞鑼呭彴",
            criteria_payload={"criteria": {"new_high_window": 100}, "profile_name": "momentum_strict"},
            metrics_payload={"close": 1800.0, "latest_high": 1818.0, "history_source": "fake"},
            cause_payload=None,
            history_payload={"previous_hit_count": 0},
        )

        loaded_run_result = load_snapshot_run_result(
            self.db,
            snapshot_date=snapshot_date,
        )
        enrich_selected_results(
            loaded_run_result,
            snapshot_date=snapshot_date,
            criteria_payload=None,
            history_lookback_days=180,
            db=self.db,
            cause_analysis_service=_FakeCauseAnalysisService("preserve profile metadata"),
            perform_cause_analysis=True,
            persist_snapshot=True,
        )

        rows = self.db.get_signal_snapshots(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="600519",
        )
        self.assertEqual(len(rows), 1)
        criteria_payload = json.loads(rows[0].criteria_payload)
        self.assertEqual(criteria_payload["profile_name"], "momentum_strict")

    def test_load_snapshot_run_result_skips_existing_cause_by_default_when_requested(self):
        snapshot_date = date(2026, 4, 4)
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="600519",
            name="贵州茅台",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 1800.0, "latest_high": 1818.0, "history_source": "fake"},
            cause_payload={"reason_summary": "已有原因"},
            history_payload={"previous_hit_count": 1},
        )
        self.db.upsert_signal_snapshot(
            signal_type=SIGNAL_TYPE,
            signal_date=snapshot_date,
            code="000001",
            name="平安银行",
            criteria_payload={"criteria": {"new_high_window": 100}},
            metrics_payload={"close": 12.0, "latest_high": 12.5, "history_source": "fake"},
            cause_payload=None,
            history_payload={"previous_hit_count": 0},
        )

        pending_only = load_snapshot_run_result(
            self.db,
            snapshot_date=snapshot_date,
            include_existing_cause=False,
        )
        all_rows = load_snapshot_run_result(
            self.db,
            snapshot_date=snapshot_date,
            include_existing_cause=True,
        )

        self.assertEqual([item.stock_code for item in pending_only.selected], ["000001"])
        self.assertEqual(
            sorted(item.stock_code for item in all_rows.selected),
            ["000001", "600519"],
        )

    def test_main_cause_analysis_only_passes_force_refresh_flag(self):
        fake_run_result = self._build_run_result()
        with patch.object(sys, "argv", [
            "select_hundred_day_high_candidates.py",
            "--snapshot-date", "2026-04-04",
            "--cause-analysis-only",
            "--force-cause-refresh",
        ]), patch(
            "scripts.select_hundred_day_high_candidates.load_snapshot_run_result",
            return_value=fake_run_result,
        ) as load_mock, patch(
            "scripts.select_hundred_day_high_candidates.enrich_selected_results",
            return_value=build_selected_dataframe(fake_run_result),
        ), patch(
            "scripts.select_hundred_day_high_candidates.export_results"
        ):
            exit_code = hundred_day_high_main()

        self.assertEqual(exit_code, 0)
        self.assertTrue(load_mock.call_args.kwargs["include_existing_cause"])

    def test_build_criteria_payload_includes_profile_name(self):
        run_result = self._build_run_result()
        payload = build_criteria_payload(
            run_result.criteria,
            signal_type="hundred_day_high__momentum_strict",
            prefilter=None,
            snapshot_date=date(2026, 4, 4),
            profile_name="momentum_strict",
        )

        self.assertEqual(payload["profile_name"], "momentum_strict")
        self.assertEqual(payload["signal_type"], "hundred_day_high__momentum_strict")

    def test_resolve_profile_settings_uses_profile_defaults_and_allows_cli_overrides(self):
        args = SimpleNamespace(
            profile="momentum_strict",
            lookback_days=None,
            min_up_ratio=None,
            limit_up_lookback_days=None,
            new_high_window=None,
            max_total_mv_yi=None,
            require_up_day_ratio=None,
            require_recent_limit_up=None,
            disable_spot_prefilter=False,
            min_60d_change_pct_prefilter=None,
            min_turnover_rate_prefilter=None,
            require_positive_change_prefilter=None,
            exclude_st_prefilter=None,
        )

        profile_name, criteria, prefilter = resolve_profile_settings(args)
        self.assertEqual(profile_name, "momentum_strict")
        self.assertEqual(criteria.lookback_days, 8)
        self.assertEqual(criteria.min_up_ratio, 0.67)
        self.assertEqual(criteria.limit_up_lookback_days, 8)
        self.assertEqual(criteria.new_high_window, 110)
        self.assertTrue(criteria.require_up_day_ratio)
        self.assertFalse(criteria.require_recent_limit_up)
        self.assertEqual(criteria.max_total_market_cap, 70.0 * 1e8)
        self.assertIsNotNone(prefilter)
        self.assertEqual(prefilter.min_change_pct_60d, 20.0)
        self.assertEqual(prefilter.min_turnover_rate, 1.2)
        self.assertTrue(prefilter.require_positive_change)
        self.assertTrue(prefilter.exclude_st)

        override_args = SimpleNamespace(
            profile=DEFAULT_PROFILE_NAME,
            lookback_days=None,
            min_up_ratio=None,
            limit_up_lookback_days=None,
            new_high_window=80,
            max_total_mv_yi=650.0,
            require_up_day_ratio=True,
            require_recent_limit_up=True,
            disable_spot_prefilter=False,
            min_60d_change_pct_prefilter=15.0,
            min_turnover_rate_prefilter=2.0,
            require_positive_change_prefilter=True,
            exclude_st_prefilter=True,
        )
        _, override_criteria, override_prefilter = resolve_profile_settings(override_args)
        self.assertEqual(override_criteria.new_high_window, 80)
        self.assertEqual(override_criteria.max_total_market_cap, 650.0 * 1e8)
        self.assertTrue(override_criteria.require_up_day_ratio)
        self.assertTrue(override_criteria.require_recent_limit_up)
        self.assertIsNotNone(override_prefilter)
        self.assertEqual(override_prefilter.min_change_pct_60d, 15.0)
        self.assertEqual(override_prefilter.min_turnover_rate, 2.0)

    def test_build_profile_signal_type_namespaces_profile_runs(self):
        self.assertEqual(
            build_profile_signal_type("hundred_day_high_profile", "momentum_strict"),
            "hundred_day_high_profile__momentum_strict",
        )

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
