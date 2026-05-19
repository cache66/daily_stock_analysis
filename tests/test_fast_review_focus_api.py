# -*- coding: utf-8 -*-
"""API contract tests for fast-review focus query endpoint."""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.services.fast_review_focus_service import (
    DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_SECONDS,
    FastReviewFocusService,
)
from src.storage import DatabaseManager


class FastReviewFocusApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        auth._auth_enabled = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "fast_review_focus_api_test.db"
        self.manual_runs_root = self.data_dir / "manual_runs"
        self.manual_runs_root.mkdir(parents=True, exist_ok=True)

        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.client = TestClient(create_app(static_dir=self.data_dir / "empty-static"))

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _write_focus_csv(self, run_name: str, snapshot_date: str) -> Path:
        review_dir = self.manual_runs_root / run_name / snapshot_date / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        focus_csv = review_dir / "fast_review_strategy_focus.csv"
        with focus_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "code",
                    "name",
                    "tier",
                    "ab_bucket",
                    "priority_score",
                    "signal_keys",
                    "signal_types",
                    "trend_hundred_relation",
                    "focus_reason",
                    "reason_summary",
                    "industry_logic",
                    "news_logic",
                    "technical_logic",
                    "trend_label",
                    "selection_mode",
                    "risk_flags",
                    "review_stage_type",
                    "review_stage_label",
                    "review_stage_reason",
                    "driver_type",
                    "driver_label",
                    "driver_reason",
                    "event_date",
                    "today_change_pct",
                    "pe_ratio",
                    "report_date",
                    "report_period_label",
                    "revenue_amount",
                    "net_profit_amount",
                    "business_labels",
                    "business_summary",
                    "chain_role_label",
                    "theme_label",
                    "theme_source",
                    "earnings_anchor",
                    "supply_demand_bias",
                    "authority_judgement",
                    "authority_level",
                    "authority_reason_summary",
                    "authority_evidence_digest",
                    "announcement_evidence_summary",
                    "earnings_evidence_summary",
                    "research_evidence_summary",
                    "authority_time_window_days",
                    "preferred_industry_label",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "code": "603629",
                    "name": "利通电子",
                    "tier": "core",
                    "ab_bucket": "A",
                    "priority_score": "329.2",
                    "signal_keys": "trend_leader,earnings,hundred_day_high",
                    "signal_types": "earnings_surprise,hundred_day_high,trend_leader_unified",
                    "trend_hundred_relation": "intersection",
                    "focus_reason": "trend; hundred_day_overlap; earnings=68",
                    "reason_summary": "recent earnings + price confirmation",
                    "industry_logic": "board strength remains visible",
                    "news_logic": "no extra event needed",
                    "technical_logic": "near new high with follow-through",
                    "trend_label": "near_new_high",
                    "selection_mode": "strict",
                    "risk_flags": "['weak_capital_flow']",
                    "review_stage_type": "delivery_confirmed",
                    "review_stage_label": "兑现",
                    "review_stage_reason": "recent earnings signal already has price/trend confirmation",
                    "driver_type": "earnings_delivery",
                    "driver_label": "业绩兑现型",
                    "driver_reason": "recent earnings event + strong price confirmation",
                    "event_date": "2026-04-28",
                    "today_change_pct": "6.8",
                    "pe_ratio": "18.4",
                    "report_date": "2026-03-31",
                    "report_period_label": "2026Q1",
                    "revenue_amount": "1080000000",
                    "net_profit_amount": "260000000",
                    "business_labels": "算力设备,液冷",
                    "business_summary": "算力设备/液冷，偏AI基础设施",
                    "chain_role_label": "AI基础设施",
                    "theme_label": "AI算力链映射",
                    "theme_source": "business_summary+news_title",
                    "earnings_anchor": "2026Q1@2026-04-28",
                    "supply_demand_bias": "earnings",
                    "authority_judgement": "公告确认",
                    "authority_level": "announcement",
                    "authority_reason_summary": "近7天公告确认订单与扩产逻辑，涨势更偏基本面兑现。",
                    "authority_evidence_digest": "公告: 扩产与订单；财报: 2026Q1净利2.6亿；研报: 机构继续强化AI基础设施逻辑",
                    "announcement_evidence_summary": "近7天公告涉及扩产和订单，和股价上行逻辑一致",
                    "earnings_evidence_summary": "2026Q1净利润2.6亿元，季度业绩保持高增长",
                    "research_evidence_summary": "机构近期持续强化AI基础设施景气和订单兑现逻辑",
                    "authority_time_window_days": "7",
                    "preferred_industry_label": "AI基础设施",
                }
            )
            writer.writerow(
                {
                    "code": "002929",
                    "name": "润建股份",
                    "tier": "watch",
                    "ab_bucket": "B",
                    "priority_score": "118.0",
                    "signal_keys": "hundred_day_high",
                    "signal_types": "hundred_day_high",
                    "trend_hundred_relation": "hundred_only",
                    "focus_reason": "hundred_day_only",
                    "reason_summary": "theme rotation without earnings support",
                    "industry_logic": "rotation continues",
                    "news_logic": "topic heat only",
                    "technical_logic": "new high but no strong delivery evidence",
                    "trend_label": "",
                    "selection_mode": "",
                    "risk_flags": "[]",
                    "review_stage_type": "pure_rotation",
                    "review_stage_label": "纯轮动",
                    "review_stage_reason": "price action is active, but no stable delivery clue is visible",
                    "driver_type": "theme_sentiment",
                    "driver_label": "题材情绪型",
                    "driver_reason": "topic mapping is stronger than confirmed earnings delivery",
                    "event_date": "",
                    "today_change_pct": "-1.2",
                    "pe_ratio": "",
                    "report_date": "",
                    "report_period_label": "",
                    "revenue_amount": "",
                    "net_profit_amount": "",
                    "business_labels": "",
                    "business_summary": "",
                    "chain_role_label": "",
                    "theme_label": "",
                    "theme_source": "",
                    "earnings_anchor": "",
                    "supply_demand_bias": "",
                    "authority_judgement": "",
                    "authority_level": "",
                    "authority_reason_summary": "",
                    "authority_evidence_digest": "",
                    "announcement_evidence_summary": "",
                    "earnings_evidence_summary": "",
                    "research_evidence_summary": "",
                    "authority_time_window_days": "",
                    "preferred_industry_label": "AI基础设施",
                }
            )
        return focus_csv

    def test_fast_review_focus_endpoint_returns_summary_and_items(self) -> None:
        self._write_focus_csv("fast_review_focus_case", "2026-05-06")

        with patch("src.services.fast_review_focus_service.DEFAULT_MANUAL_RUNS_ROOT", self.manual_runs_root):
            response = self.client.get(
                "/api/v1/signals/fast-review-focus",
                params={"snapshot_date": "2026-05-06"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["snapshot_date"], "2026-05-06")
        self.assertEqual(payload["total"], 2)
        self.assertTrue(payload["source_run_dir"].endswith("fast_review_focus_case"))
        self.assertEqual(payload["ab_summary"], {"A": 1, "B": 1})
        self.assertEqual(payload["stage_summary"]["兑现"], 1)
        self.assertEqual(payload["stage_summary"]["纯轮动"], 1)
        self.assertEqual(payload["driver_summary"]["业绩兑现型"], 1)
        self.assertEqual(payload["items"][0]["code"], "603629")
        self.assertEqual(payload["items"][0]["review_stage_label"], "兑现")
        self.assertEqual(payload["items"][0]["driver_label"], "业绩兑现型")
        self.assertEqual(payload["items"][0]["signal_keys"], ["trend_leader", "earnings", "hundred_day_high"])
        self.assertEqual(payload["items"][0]["today_change_pct"], 6.8)
        self.assertEqual(payload["items"][0]["pe_ratio"], 18.4)
        self.assertEqual(payload["items"][0]["report_date"], "2026-03-31")
        self.assertEqual(payload["items"][0]["report_period_label"], "2026Q1")
        self.assertEqual(payload["items"][0]["revenue_amount"], 1080000000.0)
        self.assertEqual(payload["items"][0]["net_profit_amount"], 260000000.0)
        self.assertEqual(payload["items"][0]["business_labels"], ["算力设备", "液冷"])
        self.assertEqual(payload["items"][0]["business_summary"], "算力设备/液冷，偏AI基础设施")
        self.assertEqual(payload["items"][0]["chain_role_label"], "AI基础设施")
        self.assertEqual(payload["items"][0]["theme_label"], "AI算力链映射")
        self.assertEqual(payload["items"][0]["theme_source"], "business_summary+news_title")
        self.assertEqual(payload["items"][0]["earnings_anchor"], "2026Q1@2026-04-28")
        self.assertEqual(payload["items"][0]["supply_demand_bias"], "earnings")
        self.assertEqual(payload["items"][0]["authority_judgement"], "公告确认")
        self.assertEqual(payload["items"][0]["authority_level"], "announcement")
        self.assertIn("近7天公告确认订单与扩产逻辑", payload["items"][0]["authority_reason_summary"])
        self.assertEqual(payload["items"][0]["display_authority_judgement"], "公告确认")
        self.assertTrue(payload["items"][0]["display_authority_summary"])
        self.assertTrue(payload["items"][0]["display_reason_summary"])
        self.assertIn("扩产和订单", payload["items"][0]["announcement_evidence_summary"])
        self.assertEqual(payload["items"][0]["authority_time_window_days"], 7)
        self.assertEqual(payload["items"][0]["preferred_industry_label"], "AI基础设施")
        self.assertTrue(payload["items"][0]["peer_group_label"])
        self.assertTrue(payload["items"][0]["peer_resonance_summary"])
        self.assertTrue(payload["items"][0]["leader_position_summary"])
        self.assertIsNone(payload["items"][1]["authority_judgement"])
        self.assertIsNone(payload["items"][1]["authority_level"])
        self.assertIsNone(payload["items"][1]["authority_time_window_days"])
        self.assertEqual(payload["items"][1]["ab_bucket"], "B")

    def test_focus_service_builds_display_authority_from_strong_earnings_evidence_when_raw_authority_is_missing(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = service._row_to_item(
            {
                "code": "300476",
                "name": "胜宏科技",
                "tier": "watch",
                "ab_bucket": "B",
                "priority_score": "188.0",
                "signal_keys": "trend_leader",
                "signal_types": "trend_leader_unified",
                "reason_summary": "当前更像是 PCB 方向的结构性走强；主线判断更偏 AI主线扩散（依据：业务标签 / 业绩披露）；当前未检索到足够稳定的公开消息催化，消息面暂按中性处理；短线先按技术突破与资金轮动延续看待；业务主线可先按 PCB，偏AI算力供应链 跟踪。",
                "industry_logic": "当前更像是 PCB 方向的结构性走强；主线判断更偏 AI主线扩散（依据：业务标签 / 业绩披露）",
                "news_logic": "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理",
                "technical_logic": "短线先按技术突破与资金轮动延续看待；业务主线可先按 PCB，偏AI算力供应链 跟踪",
                "business_summary": "PCB，偏AI算力供应链",
                "authority_judgement": "",
                "authority_level": "",
                "authority_reason_summary": "",
                "earnings_evidence_summary": "2026Q1，2026-03-31，净利润12.88亿元，营收同比+28.0%，净利同比+40.0%",
                "report_date": "2026-03-31",
                "report_period_label": "2026Q1",
                "net_profit_amount": "1288000000",
                "today_change_pct": "6.09",
            }
        )

        self.assertIsNone(item["authority_judgement"])
        self.assertEqual(item["display_authority_judgement"], "财报确认")
        self.assertIn("财报确认", item["display_authority_summary"] or "")
        self.assertIn("净利润12.88亿元", item["display_authority_summary"] or "")
        self.assertNotIn("当前未检索到足够稳定的公开消息催化", item["display_reason_summary"] or "")
        self.assertNotIn("业务主线可先按", item["display_reason_summary"] or "")

    def test_focus_service_compacts_display_reason_summary_with_mainline_and_real_trigger(self) -> None:
        summary = FastReviewFocusService._build_display_reason_summary(
            reason_summary=(
                "当前更像是 光模块/光通信 方向的结构性走强；"
                "业务辨识度更偏 光模块/光通信，偏AI算力供应链；"
                "当日强势池入选理由是 trend leader；"
                "更像AI主线行业景气扩散下的分支走强；"
                "压短样本 命中 trend_leader_unified 信号。"
            ),
            industry_logic="当前更像是 光模块/光通信 方向的结构性走强；主线判断更偏 AI主线扩散（依据：业务标签 / 业绩披露）",
            news_logic="当前未检索到足够稳定的公开消息催化，消息面暂按中性处理",
            technical_logic="短线先按技术突破与资金轮动延续看待；压短样本 命中 trend_leader_unified 信号",
        )

        self.assertIsNotNone(summary)
        self.assertIn("主线判断更偏", summary or "")
        self.assertIn("命中 trend_leader_unified 信号", summary or "")
        self.assertNotIn("当前未检索到足够稳定的公开消息催化", summary or "")
        self.assertNotIn("更像AI主线行业景气扩散下的分支走强", summary or "")

    def test_fast_review_focus_endpoint_rejects_invalid_date(self) -> None:
        with patch("src.services.fast_review_focus_service.DEFAULT_MANUAL_RUNS_ROOT", self.manual_runs_root):
            response = self.client.get(
                "/api/v1/signals/fast-review-focus",
                params={"snapshot_date": "bad-date"},
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            self.assertEqual(detail["error"], "invalid_request")
        else:
            self.assertEqual(payload["error"], "invalid_request")

    def test_focus_service_builds_peer_resonance_and_turning_point_support_summaries(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "胜宏科技",
                "priority_score": 277.39,
                "today_change_pct": 6.09,
                "preferred_industry_label": "PCB",
                "business_summary": "PCB，偏AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "拐点",
            },
            {
                "code": "002463",
                "name": "沪电股份",
                "priority_score": 251.10,
                "today_change_pct": 4.88,
                "preferred_industry_label": "PCB",
                "business_summary": "PCB，偏AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "半兑现",
            },
            {
                "code": "603920",
                "name": "世运电路",
                "priority_score": 219.80,
                "today_change_pct": 3.21,
                "preferred_industry_label": "PCB",
                "business_summary": "PCB，偏AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "拐点",
            },
        ]

        service._annotate_peer_context(items)

        self.assertEqual(items[0]["peer_group_label"], "PCB")
        self.assertIn("同日", items[0]["peer_resonance_summary"])
        self.assertIn("3只", items[0]["peer_resonance_summary"])
        self.assertRegex(items[0]["leader_position_summary"], "龙头|前")
        self.assertIn("拐点", items[0]["turning_point_peer_summary"])
        self.assertIn("半兑现", items[0]["turning_point_peer_summary"])

    def test_focus_service_normalizes_optical_ai_variants_into_same_peer_group(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "002222",
                "name": "福晶科技",
                "priority_score": 295.78,
                "today_change_pct": 10.0,
                "preferred_industry_label": "光模块",
                "business_summary": "光模块，偏AI算力供应链",
                "chain_role_label": "AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "拐点",
            },
            {
                "code": "002281",
                "name": "光迅科技",
                "priority_score": 279.32,
                "today_change_pct": 10.0,
                "preferred_industry_label": "光模块/光通信",
                "business_summary": "光模块/光通信，偏AI算力供应链",
                "chain_role_label": "AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "拐点",
            },
            {
                "code": "300548",
                "name": "长芯博创",
                "priority_score": 250.0,
                "today_change_pct": 0.52,
                "preferred_industry_label": "光模块/光通信/半导体",
                "business_summary": "光模块/光通信/半导体，偏AI算力供应链",
                "chain_role_label": "AI算力供应链",
                "theme_label": "AI算力 / 半导体",
                "review_stage_label": "拐点",
            },
        ]

        service._annotate_peer_context(items)

        for item in items:
            self.assertEqual(item["peer_group_label"], "光模块/光通信")
            self.assertIn("3只", item["peer_resonance_summary"])

    def test_focus_service_normalizes_material_and_telecom_power_variants_before_grouping(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

        material_item = {
            "code": "603618",
            "name": "杭电股份",
            "preferred_industry_label": "光通信/电力设备/铜箔",
            "business_summary": "光通信/电力设备/铜箔，偏AI上游材料链",
            "chain_role_label": "AI上游材料链",
            "theme_label": "",
        }
        telecom_power_items = [
            {
                "code": "600522",
                "name": "中天科技",
                "priority_score": 310.0,
                "today_change_pct": 6.57,
                "preferred_industry_label": "光通信/电力设备",
                "business_summary": "光通信/电力设备",
                "review_stage_label": "拐点",
            },
            {
                "code": "600487",
                "name": "亨通光电",
                "priority_score": 180.0,
                "today_change_pct": 1.72,
                "preferred_industry_label": "海缆/电力设备/光通信",
                "business_summary": "海缆/电力设备/光通信",
                "review_stage_label": "拐点",
            },
            {
                "code": "002491",
                "name": "通鼎互联",
                "priority_score": 170.0,
                "today_change_pct": 10.01,
                "preferred_industry_label": "通信设备/电力设备",
                "business_summary": "通信设备/电力设备",
                "review_stage_label": "拐点",
            },
        ]

        self.assertEqual(
            service._resolve_peer_group_label(material_item),
            "AI上游材料/电子材料",
        )

        service._annotate_peer_context(telecom_power_items)
        for item in telecom_power_items:
            self.assertEqual(item["peer_group_label"], "通信/电力设备")
            self.assertIn("3只", item["peer_resonance_summary"])

    def test_focus_service_derives_peer_group_from_business_description_when_labels_missing(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

        self.assertEqual(
            service._resolve_peer_group_label(
                {
                    "preferred_industry_label": "",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "主营业务显示公司主要从事 卫星及相关产品的研发、设计、制造、销售；航天技术应用及相关产品的研发、设计、制造、销售及综合信息服务。",
                }
            ),
            "卫星/航天",
        )
        self.assertEqual(
            service._resolve_peer_group_label(
                {
                    "preferred_industry_label": "",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "主营业务显示公司主要从事 机器人、AI机器视觉、工业软件、智能制造领域的研发、设计、制造、应用和销售服务。",
                }
            ),
            "机器人/智能制造",
        )

    def test_focus_service_derives_additional_peer_groups_from_business_keywords(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

        cases = [
            (
                "主营业务显示公司主要从事 减隔震产品的生产、销售、安装和建筑减隔震技术咨询服务。",
                "建筑减隔震/基建",
            ),
            (
                "主营业务显示公司主要从事 发动机尾气后处理产品及与大气环保相关产品的研发、生产和销售。",
                "环保/汽车尾气",
            ),
            (
                "主营业务显示公司主要从事 超细复合纤维面料及制成品的研发、生产、销售。",
                "纺织/新材料",
            ),
            (
                "主营业务显示公司主要从事 表面工程技术的研究及新型环保表面工程专用化学品与专用设备的研发、生产和销售。",
                "表面处理/化学品",
            ),
            (
                "主营业务显示公司主要从事 磁悬浮流体机械及磁悬浮轴承、高速电机、高速驱动等核心部件的研发、生产、销售。",
                "磁悬浮/高端装备",
            ),
        ]

        for technical_logic, expected_group in cases:
            self.assertEqual(
                service._resolve_peer_group_label(
                    {
                        "preferred_industry_label": "",
                        "chain_role_label": "",
                        "theme_label": "",
                        "business_labels": [],
                        "business_summary": "",
                        "technical_logic": technical_logic,
                    }
                ),
                expected_group,
            )

    def test_focus_service_normalizes_remaining_long_text_single_name_groups(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

        cases = [
            (
                {
                    "preferred_industry_label": "房地产开发业务和建筑施工业务",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "房地产开发业务和建筑施工业务",
                    "technical_logic": "",
                },
                "房地产/建筑施工",
            ),
            (
                {
                    "preferred_industry_label": "海上运输业务",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "海上运输业务",
                    "technical_logic": "",
                },
                "航运",
            ),
            (
                {
                    "preferred_industry_label": "软磁材料及磁心的研发、生产和销售",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "软磁材料及磁心的研发、生产和销售",
                    "technical_logic": "",
                },
                "软磁材料/磁性材料",
            ),
            (
                {
                    "preferred_industry_label": "软磁铁氧体磁粉的研发、生产和销售",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "软磁铁氧体磁粉的研发、生产和销售",
                    "technical_logic": "",
                },
                "软磁材料/磁性材料",
            ),
            (
                {
                    "preferred_industry_label": "锂电/精密组件",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "锂电/精密组件",
                    "technical_logic": "",
                },
                "锂电/精密组件",
            ),
            (
                {
                    "preferred_industry_label": "环保设备Ⅲ",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "",
                },
                "再生资源/环保设备",
            ),
            (
                {
                    "preferred_industry_label": "聚酯树脂系列产品的生产销售",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "",
                },
                "树脂/化工材料",
            ),
            (
                {
                    "preferred_industry_label": "特种环保纸的研发、生产及销售",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "",
                },
                "特种环保纸",
            ),
            (
                {
                    "preferred_industry_label": "光学元器件的研发、生产和销售",
                    "chain_role_label": "",
                    "theme_label": "",
                    "business_labels": [],
                    "business_summary": "",
                    "technical_logic": "",
                },
                "光学",
            ),
        ]

        for item, expected_group in cases:
            self.assertEqual(service._resolve_peer_group_label(item), expected_group)

    def test_focus_service_enrich_items_treats_blank_strings_as_missing(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "300476",
            "name": "胜宏科技",
            "today_change_pct": "",
            "pe_ratio": "",
            "report_date": "",
            "report_period_label": "",
            "revenue_amount": "",
            "net_profit_amount": "",
        }

        class _FakeAkshareFetcher:
            def get_realtime_quote(self, stock_code, source="em"):
                return {"pct_change": 3.72, "pe_ratio": 71.0}

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service.manager, "get_realtime_quote", return_value={"pct_change": 3.72, "pe_ratio": 71.0}):
                with patch.object(
                    service.manager,
                    "get_earnings_fundamental_context",
                    return_value={
                        "status": "ok",
                        "earnings": {
                            "data": {
                                "financial_report": {
                                    "report_date": "2026-03-31",
                                    "revenue": 1230000000,
                                    "net_profit_parent": 245000000,
                                }
                            }
                        },
                    },
                ):
                    service.enrich_items([item])

        self.assertEqual(item["today_change_pct"], 3.72)
        self.assertEqual(item["pe_ratio"], 71.0)
        self.assertEqual(item["report_date"], "2026-03-31")
        self.assertEqual(item["report_period_label"], "2026Q1")
        self.assertEqual(item["revenue_amount"], 1230000000.0)
        self.assertEqual(item["net_profit_amount"], 245000000.0)

    def test_focus_service_scales_total_quote_budget_with_missing_visible_rows(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

        small_batch_budget = service._build_total_quote_budget_seconds(
            [
                {"code": "300476", "today_change_pct": None, "pe_ratio": None},
                {"code": "300442", "today_change_pct": None, "pe_ratio": None},
            ]
        )
        visible_batch_budget = service._build_total_quote_budget_seconds(
            [
                {"code": f"{300000 + idx}", "today_change_pct": None, "pe_ratio": None}
                for idx in range(16)
            ]
        )

        self.assertEqual(small_batch_budget, DEFAULT_EXPORT_TOTAL_QUOTE_BUDGET_SECONDS)
        self.assertEqual(visible_batch_budget, 64.0)

    def test_focus_service_derives_explanation_structure_fields_from_legacy_text_columns(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = service._row_to_item(
            {
                "code": "002281",
                "name": "光迅科技",
                "reason_summary": "增长指标命中；业务侧先按 光模块/光通信，偏AI算力供应链 跟踪",
                "industry_logic": "当前更像是 通信设备 方向的结构性走强；同时存在 AI算力链映射 的海外主题映射",
                "news_logic": "当前未检索到足够稳定的公开消息催化，消息面暂按中性处理",
                "technical_logic": "业务主线可先按 光模块/光通信，偏AI算力供应链 跟踪",
                "cause_tags": "earnings,supply_demand,overseas_theme",
                "event_date": "2026-05-07",
                "report_period_label": "2026Q1",
            }
        )

        self.assertEqual(item["business_labels"], ["光模块", "光通信"])
        self.assertEqual(item["business_summary"], "光模块/光通信，偏AI算力供应链")
        self.assertEqual(item["chain_role_label"], "AI算力供应链")
        self.assertEqual(item["theme_label"], "AI算力链映射")
        self.assertEqual(item["earnings_anchor"], "2026Q1@2026-05-07")
        self.assertEqual(item["supply_demand_bias"], "supply_demand")

    def test_focus_service_rebuilds_canonical_earnings_anchor_when_existing_field_is_event_only(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = service._row_to_item(
            {
                "code": "688235",
                "name": "百济神州",
                "reason_summary": "增长指标命中，当前先按业绩驱动看待",
                "industry_logic": "创新药方向景气度抬升",
                "technical_logic": "事件后价格跟随",
                "cause_tags": "earnings,sector_rotation,overseas_theme",
                "event_date": "2026-05-07",
                "report_date": "2026-03-31",
                "report_period_label": "2026Q1",
                "earnings_anchor": "2026-05-07",
            }
        )

        self.assertEqual(item["earnings_anchor"], "2026Q1@2026-05-07")

    def test_focus_service_does_not_mark_trend_rows_as_earnings_bias_from_cause_tag_alone(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = service._row_to_item(
            {
                "code": "000988",
                "name": "华工科技",
                "reason_summary": "当前更像是 光通信 方向的结构性走强",
                "industry_logic": "业务辨识度更偏 光模块/光通信，偏AI算力供应链",
                "technical_logic": "业务主线可先按 光模块/光通信，偏AI算力供应链 跟踪",
                "cause_tags": "earnings,sector_rotation,overseas_theme",
                "event_date": "",
                "report_date": "",
                "report_period_label": "",
            }
        )

        self.assertIsNone(item["supply_demand_bias"])

    def test_focus_service_prefers_akshare_em_snapshot_for_multi_a_share_quote_enrichment(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "胜宏科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
            {
                "code": "002281",
                "name": "光迅科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
        ]

        class _FakeAkshareFetcher:
            def __init__(self, *args, **kwargs) -> None:
                self.calls = []

            def get_realtime_quote(self, stock_code, source="em"):
                self.calls.append((stock_code, source))
                payloads = {
                    "300476": {"pct_change": 3.72, "pe_ratio": 71.0},
                    "002281": {"pct_change": 8.93, "pe_ratio": 127.92},
                }
                return payloads.get(stock_code)

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", return_value=True):
                with patch.object(
                    service.manager,
                    "get_realtime_quote",
                    side_effect=AssertionError("should not use manager realtime fallback when batch snapshot is available"),
                ):
                    with patch.object(
                        service.manager,
                        "get_earnings_fundamental_context",
                        return_value={},
                    ):
                        service.enrich_items(items)

        self.assertEqual(items[0]["today_change_pct"], 3.72)
        self.assertEqual(items[0]["pe_ratio"], 71.0)
        self.assertEqual(items[1]["today_change_pct"], 8.93)
        self.assertEqual(items[1]["pe_ratio"], 127.92)

    def test_focus_service_uses_lightweight_tencent_quote_when_em_cache_is_cold(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "300476",
            "name": "鑳滃畯绉戞妧",
            "today_change_pct": None,
            "pe_ratio": None,
            "report_date": None,
            "report_period_label": None,
            "revenue_amount": None,
            "net_profit_amount": None,
        }

        class _FakeAkshareFetcher:
            def get_realtime_quote(self, stock_code, source="em"):
                if source == "tencent":
                    return {"pct_change": 3.72, "pe_ratio": 71.0}
                raise AssertionError(f"unexpected source: {source}")

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", return_value=False):
                with patch.object(
                    service.manager,
                    "get_realtime_quote",
                    side_effect=AssertionError("should not fall back to manager when lightweight tencent quote succeeds"),
                ):
                    with patch.object(service.manager, "get_earnings_fundamental_context", return_value={}):
                        service.enrich_items([item])

        self.assertEqual(item["today_change_pct"], 3.72)
        self.assertEqual(item["pe_ratio"], 71.0)

    def test_focus_service_warms_em_batch_quote_cache_when_cold_and_multiple_rows_need_quotes(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "閼虫粌鐣粔鎴炲Η",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
            {
                "code": "002281",
                "name": "鍏夎繀绉戞妧",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
        ]

        class _FakeAkshareFetcher:
            em_calls = []

            def __init__(self, *args, **kwargs) -> None:
                return None

            def get_realtime_quote(self, stock_code, source="em"):
                if source == "em":
                    self.__class__.em_calls.append(stock_code)
                    payloads = {
                        "300476": {"pct_change": 3.72, "pe_ratio": 71.0},
                        "002281": {"pct_change": 8.93, "pe_ratio": 127.92},
                    }
                    return payloads.get(stock_code)
                if source == "tencent":
                    return None
                raise AssertionError(f"unexpected source: {source}")

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", return_value=False):
                with patch.object(
                    service.manager,
                    "get_realtime_quote",
                    side_effect=AssertionError("should not fall back to manager when batch EM warmup succeeds"),
                ):
                    service.enrich_market_fields(items)

        self.assertEqual(_FakeAkshareFetcher.em_calls, ["300476", "002281"])
        self.assertEqual(items[0]["today_change_pct"], 3.72)
        self.assertEqual(items[0]["pe_ratio"], 71.0)
        self.assertEqual(items[1]["today_change_pct"], 8.93)
        self.assertEqual(items[1]["pe_ratio"], 127.92)

    def test_focus_service_prefetches_tencent_batch_quotes_before_deadline_when_em_batch_warmup_fails(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "閼虫粌鐣粔鎴炲Η",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
            {
                "code": "002281",
                "name": "鍏夎繀绉戞妧",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
        ]

        class _FakeAkshareFetcher:
            calls = []

            def __init__(self, *args, **kwargs) -> None:
                return None

            def get_realtime_quote(self, stock_code, source="em"):
                self.__class__.calls.append((stock_code, source))
                if source == "em":
                    return None
                if source == "tencent":
                    payloads = {
                        "300476": {"pct_change": 3.72, "pe_ratio": 71.0},
                        "002281": {"pct_change": 8.93, "pe_ratio": 127.92},
                    }
                    return payloads.get(stock_code)
                raise AssertionError(f"unexpected source: {source}")

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", return_value=False):
                with patch.object(service, "_build_total_quote_budget_seconds", return_value=0.0):
                    with patch.object(
                        service.manager,
                        "get_realtime_quote",
                        side_effect=AssertionError("should not reach manager fallback when batch tencent prefetch succeeds"),
                    ):
                        service.enrich_market_fields(items)

        self.assertEqual(items[0]["today_change_pct"], 3.72)
        self.assertEqual(items[0]["pe_ratio"], 71.0)
        self.assertEqual(items[1]["today_change_pct"], 8.93)
        self.assertEqual(items[1]["pe_ratio"], 127.92)
        self.assertIn(("300476", "em"), _FakeAkshareFetcher.calls)
        self.assertEqual(sum(1 for _, source in _FakeAkshareFetcher.calls if source == "tencent"), 2)
        self.assertTrue(("300476", "tencent") in _FakeAkshareFetcher.calls)
        self.assertTrue(("002281", "tencent") in _FakeAkshareFetcher.calls)

    def test_focus_service_skips_repeated_em_reads_after_cold_warmup_miss(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "胜宏科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
            {
                "code": "002281",
                "name": "光迅科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
        ]

        class _FakeAkshareFetcher:
            calls = []

            def __init__(self, *args, **kwargs) -> None:
                return None

            def get_realtime_quote(self, stock_code, source="em"):
                self.__class__.calls.append((stock_code, source))
                if source == "em":
                    return None
                if source == "tencent":
                    payloads = {
                        "300476": {"pct_change": 3.72, "pe_ratio": 71.0},
                        "002281": {"pct_change": 8.93, "pe_ratio": 127.92},
                    }
                    return payloads.get(stock_code)
                raise AssertionError(f"unexpected source: {source}")

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", side_effect=[False, False]):
                with patch.object(
                    service.manager,
                    "get_realtime_quote",
                    side_effect=AssertionError("should not reach manager fallback when batch tencent prefetch succeeds"),
                ):
                    service.enrich_market_fields(items)

        self.assertEqual(items[0]["today_change_pct"], 3.72)
        self.assertEqual(items[1]["today_change_pct"], 8.93)
        self.assertEqual(sum(1 for _, source in _FakeAkshareFetcher.calls if source == "em"), 1)
        self.assertEqual(sum(1 for _, source in _FakeAkshareFetcher.calls if source == "tencent"), 2)
        self.assertNotIn(("002281", "em"), _FakeAkshareFetcher.calls)

    def test_focus_service_does_not_repeat_lightweight_tencent_after_batch_miss(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        items = [
            {
                "code": "300476",
                "name": "胜宏科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
            {
                "code": "002281",
                "name": "光迅科技",
                "today_change_pct": None,
                "pe_ratio": None,
                "report_date": None,
                "report_period_label": None,
                "revenue_amount": None,
                "net_profit_amount": None,
            },
        ]

        class _FakeAkshareFetcher:
            calls = []

            def __init__(self, *args, **kwargs) -> None:
                return None

            def get_realtime_quote(self, stock_code, source="em"):
                self.__class__.calls.append((stock_code, source))
                if source == "em":
                    return None
                if source == "tencent":
                    payloads = {
                        "300476": {"pct_change": 3.72, "pe_ratio": 71.0},
                    }
                    return payloads.get(stock_code)
                raise AssertionError(f"unexpected source: {source}")

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(service, "_is_em_quote_cache_warm", side_effect=[False, False]):
                with patch.object(
                    service.manager,
                    "get_realtime_quote",
                    side_effect=[
                        {"pct_change": -2.71, "pe_ratio": 63.5},
                    ],
                ) as manager_realtime_quote:
                    service.enrich_market_fields(items)

        self.assertEqual(items[0]["today_change_pct"], 3.72)
        self.assertEqual(items[0]["pe_ratio"], 71.0)
        self.assertEqual(items[1]["today_change_pct"], -2.71)
        self.assertEqual(items[1]["pe_ratio"], 63.5)
        self.assertEqual(manager_realtime_quote.call_count, 1)
        self.assertEqual(
            [call for call in _FakeAkshareFetcher.calls if call == ("002281", "tencent")],
            [("002281", "tencent")],
        )

    def test_focus_service_skips_report_and_valuation_enrichment_when_total_budgets_are_exhausted(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "300476",
            "name": "鑳滃畯绉戞妧",
            "today_change_pct": None,
            "pe_ratio": None,
            "report_date": None,
            "report_period_label": None,
            "revenue_amount": None,
            "net_profit_amount": None,
        }

        class _FakeAkshareFetcher:
            def get_realtime_quote(self, stock_code, source="em"):
                return {"pct_change": 3.72, "pe_ratio": 71.0}

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch(
                "src.services.fast_review_focus_service.DEFAULT_EXPORT_TOTAL_EARNINGS_BUDGET_SECONDS",
                0.0,
            ):
                with patch(
                    "src.services.fast_review_focus_service.DEFAULT_EXPORT_TOTAL_VALUATION_BUDGET_SECONDS",
                    0.0,
                ):
                    with patch.object(service.manager, "get_realtime_quote", return_value={"pct_change": 3.72}):
                        with patch.object(
                            service.manager,
                            "get_earnings_fundamental_context",
                            side_effect=AssertionError("should skip report enrichment after total budget exhaustion"),
                        ):
                            with patch.object(
                                service.manager,
                                "get_fundamental_context",
                                side_effect=AssertionError("should skip valuation enrichment after total budget exhaustion"),
                            ):
                                service.enrich_items([item])

        self.assertEqual(item["today_change_pct"], 3.72)
        self.assertEqual(item["pe_ratio"], 71.0)
        self.assertIsNone(item["report_date"])
        self.assertIsNone(item["report_period_label"])
        self.assertIsNone(item["revenue_amount"])
        self.assertIsNone(item["net_profit_amount"])

    def test_focus_service_prefers_event_aligned_quarter_over_stale_annual_report_for_quick_report_samples(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "688235",
            "name": "百济神州",
            "event_date": "2026-05-07",
            "today_change_pct": None,
            "pe_ratio": None,
            "report_date": None,
            "report_period_label": None,
            "revenue_amount": None,
            "net_profit_amount": None,
        }

        class _FakeAkshareFetcher:
            def get_realtime_quote(self, stock_code, source="em"):
                return {"pct_change": -2.71, "pe_ratio": 120.61}

        with patch("src.services.fast_review_focus_service.AkshareFetcher", _FakeAkshareFetcher, create=True):
            with patch.object(
                service.manager,
                "get_earnings_fundamental_context",
                return_value={
                    "status": "ok",
                    "earnings": {
                        "data": {
                            "financial_report": {
                                "report_date": "2025-12-31",
                                "revenue": 38224999000.0,
                                "net_profit_parent": 1460710000.0,
                            },
                            "quick_report_announcement_date": "2026-05-07",
                        }
                    },
                },
            ):
                service.enrich_items([item])

        self.assertEqual(item["today_change_pct"], -2.71)
        self.assertEqual(item["pe_ratio"], 120.61)
        self.assertEqual(item["report_date"], "2026-03-31")
        self.assertEqual(item["report_period_label"], "2026Q1")
        self.assertIsNone(item["revenue_amount"])
        self.assertIsNone(item["net_profit_amount"])

    def test_focus_service_corrects_existing_stale_annual_report_snapshot_for_quick_report_samples(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "688235",
            "name": "百济神州",
            "event_date": "2026-05-07",
            "today_change_pct": -2.71,
            "pe_ratio": 120.61,
            "report_date": "2025-12-31",
            "report_period_label": "2025FY",
            "revenue_amount": 38224999000.0,
            "net_profit_amount": 1460710000.0,
        }

        with patch.object(
            service.manager,
            "get_earnings_fundamental_context",
            return_value={
                "status": "ok",
                "earnings": {
                    "data": {
                        "financial_report": {
                            "report_date": "2025-12-31",
                            "revenue": 38224999000.0,
                            "net_profit_parent": 1460710000.0,
                        },
                        "quick_report_announcement_date": "2026-05-07",
                    }
                },
            },
        ):
            service.enrich_items([item])

        self.assertEqual(item["report_date"], "2026-03-31")
        self.assertEqual(item["report_period_label"], "2026Q1")
        self.assertIsNone(item["revenue_amount"])
        self.assertIsNone(item["net_profit_amount"])

    def test_focus_service_backfills_snapshot_fields_from_authority_earnings_summary_without_refetch(self) -> None:
        service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)
        item = {
            "code": "300476",
            "name": "胜宏科技",
            "event_date": "2026-05-09",
            "today_change_pct": 6.09,
            "pe_ratio": 75.32,
            "report_date": None,
            "report_period_label": None,
            "revenue_amount": None,
            "net_profit_amount": None,
            "authority_judgement": "财报确认",
            "authority_reason_summary": "财报确认：2026Q1，2026-03-31，净利润12.88亿元，营收同比+28.0%，净利同比+40.0%，上涨更偏业绩兑现驱动。",
            "earnings_evidence_summary": "2026Q1，2026-03-31，净利润12.88亿元，营收同比+28.0%，净利同比+40.0%",
        }

        with patch.object(
            service.manager,
            "get_earnings_fundamental_context",
            side_effect=AssertionError("should not refetch earnings context when authority summary already carries the snapshot"),
        ):
            service.enrich_items([item])

        self.assertEqual(item["report_date"], "2026-03-31")
        self.assertEqual(item["report_period_label"], "2026Q1")
        self.assertIsNone(item["revenue_amount"])
        self.assertEqual(item["net_profit_amount"], 1288000000.0)


if __name__ == "__main__":
    unittest.main()
