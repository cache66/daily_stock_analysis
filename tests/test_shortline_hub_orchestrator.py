# -*- coding: utf-8 -*-
"""Tests for shortline hub orchestrator."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from src.shortline_hub.adapters.fingenius_adapter import (
    FinGeniusProcessAdapter,
    ProcessAdapterConfig,
    StubFinGeniusAdapter,
)
from src.shortline_hub.adapters.wondertrader_adapter import (
    StubWonderTraderAdapter,
    WonderTraderProcessAdapter,
)
from src.shortline_hub.explain_cache import ShortlineExplainCache
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator
from src.shortline_hub.report_builder import build_shortline_report_markdown
from src.shortline_hub.schemas import ShortlineCandidate, ShortlineExplanation
from src.storage import DatabaseManager


def test_orchestrator_combines_candidates_and_explanations() -> None:
    orchestrator = ShortlineHubOrchestrator(
        scanner=StubWonderTraderAdapter(),
        explainer=StubFinGeniusAdapter(),
    )

    result = orchestrator.run(
        run_id="demo_run",
        trade_date="2026-05-02",
        top_n=2,
    )

    assert result.run_id == "demo_run"
    assert len(result.candidates) == 2
    assert len(result.explanations) == 2
    assert len(result.combined_results) == 2
    assert result.combined_results[0].hot_money_summary != ""


def test_orchestrator_combined_result_preserves_extended_market_metrics() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="test_stock",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test reason",
                    trigger_score=99.0,
                    price=24.47,
                    change_pct=20.01,
                    volume_ratio=0.0,
                    turnover_rate=16.65,
                    board_name="semiconductor",
                    risk_flags=["missing_volume_history"],
                    change_pct_60d=142.27,
                    amount=368278000.0,
                )
            ][:top_n]

    orchestrator = ShortlineHubOrchestrator(
        scanner=_Scanner(),
        explainer=StubFinGeniusAdapter(),
    )

    result = orchestrator.run(run_id="metric_run", trade_date="2026-05-03", top_n=1)

    assert result.combined_results[0].price == 24.47
    assert result.combined_results[0].change_pct_60d == 142.27
    assert result.combined_results[0].amount == 368278000.0


def test_wondertrader_process_adapter_reads_candidates_from_external_script(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "wt_mock.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
trade_date = payload["trade_date"]
top_n = int(payload["top_n"])
rows = [
    {
        "candidate_id": f"{trade_date}-600001",
        "symbol": "600001",
        "name": "sample",
        "trade_date": trade_date,
        "scan_source": "wondertrader_process",
        "trigger_type": "limit_up_relay",
        "trigger_reason": "mock external scan",
        "trigger_score": 91.0,
        "price": 8.88,
        "change_pct": 9.91,
        "change_pct_60d": 18.2,
        "amount": 520000000.0,
        "volume_ratio": 2.3,
        "turnover_rate": 6.2,
        "board_name": "steel",
        "risk_flags": [],
    }
][:top_n]
output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = WonderTraderProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )

    results = adapter.scan_candidates(trade_date="2026-05-02", top_n=1)

    assert len(results) == 1
    assert results[0].symbol == "600001"
    assert results[0].scan_source == "wondertrader_process"
    assert results[0].change_pct_60d == 18.2
    assert results[0].amount == 520000000.0


def test_fingenius_process_adapter_reads_explanation_from_external_script(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_mock.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": f"{candidate['symbol']} mock hot money",
    "big_deal_summary": f"{candidate['symbol']} mock big deal",
    "chip_commentary": f"{candidate['symbol']} mock chip",
    "sentiment_commentary": f"{candidate['board_name']} mock sentiment",
    "risk_commentary": "mock risk",
    "short_term_view": "mock view",
    "confidence_label": "medium",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert result.candidate_id == candidate.candidate_id
    assert result.hot_money_summary == "600001 mock hot money"


def test_fingenius_process_adapter_reads_metadata_from_external_script(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_mock_metadata.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "metadata hot",
    "big_deal_summary": "metadata big",
    "chip_commentary": "metadata chip",
    "sentiment_commentary": "metadata sentiment",
    "risk_commentary": "metadata risk",
    "short_term_view": "metadata view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool", "BigDealAnalysisTool"],
    "tool_error_count": 1,
    "tool_errors": ["ChipAnalysisTool: chip failed"],
    "explain_elapsed_ms": 47,
    "upstream_tool_elapsed_ms": {
        "HotMoneyTool": 28,
        "ChipAnalysisTool": 7,
        "BigDealAnalysisTool": 12
    },
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert result.protocol_version == "shortline_fg_v1"
    assert result.explanation_source == "upstream_tools"
    assert result.used_upstream_tools == ["HotMoneyTool", "BigDealAnalysisTool"]
    assert result.tool_error_count == 1
    assert result.tool_errors == ["ChipAnalysisTool: chip failed"]
    assert result.explain_elapsed_ms == 47
    assert result.upstream_tool_elapsed_ms == {
        "HotMoneyTool": 28,
        "ChipAnalysisTool": 7,
        "BigDealAnalysisTool": 12,
    }


def test_fingenius_process_adapter_defaults_legacy_unknown_for_old_output(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_mock_old.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "old hot",
    "big_deal_summary": "old big",
    "chip_commentary": "old chip",
    "sentiment_commentary": "old sentiment",
    "risk_commentary": "old risk",
    "short_term_view": "old view",
    "confidence_label": "medium",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert result.protocol_version == ""
    assert result.explanation_source == "legacy_unknown"
    assert result.used_upstream_tools == []
    assert result.tool_error_count == 0
    assert result.tool_errors == []
    assert result.explain_elapsed_ms == 0
    assert result.upstream_tool_elapsed_ms == {}


def test_fingenius_process_adapter_passes_bridge_options_to_external_script(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_bridge_options.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
bridge_options = payload.get("bridge_options") or {}
result = {
    "candidate_id": payload["candidate"]["candidate_id"],
    "hot_money_summary": "bridge options hot",
    "big_deal_summary": json.dumps(bridge_options, ensure_ascii=False, sort_keys=True),
    "chip_commentary": "bridge options chip",
    "sentiment_commentary": "bridge options sentiment",
    "risk_commentary": "bridge options risk",
    "short_term_view": "bridge options view",
    "confidence_label": "high",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        ),
        enable_big_deal=True,
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert '{"enable_big_deal": true}' == result.big_deal_summary


def test_shortline_run_summary_includes_explanation_source_metadata() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-600001",
                    symbol="600001",
                    name="sample_a",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_relay",
                    trigger_reason="mock",
                    trigger_score=91.0,
                    price=8.88,
                    change_pct=9.91,
                    volume_ratio=2.3,
                    turnover_rate=6.2,
                    board_name="steel",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300001",
                    symbol="300001",
                    name="sample_b",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="momentum_breakout",
                    trigger_reason="mock",
                    trigger_score=88.0,
                    price=18.5,
                    change_pct=5.6,
                    volume_ratio=1.6,
                    turnover_rate=4.1,
                    board_name="power",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def __init__(self) -> None:
            self.calls = 0

        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            self.calls += 1
            if self.calls == 1:
                return ShortlineExplanation(
                    candidate_id=candidate.candidate_id,
                    hot_money_summary="hot A",
                    big_deal_summary="big A",
                    chip_commentary="chip A",
                    sentiment_commentary="sentiment A",
                    risk_commentary="risk A",
                    short_term_view="view A",
                    confidence_label="high",
                    protocol_version="shortline_fg_v1",
                    explanation_source="bridge_data",
                    used_upstream_tools=[],
                    tool_error_count=0,
                    tool_errors=[],
                    explain_elapsed_ms=12,
                    upstream_tool_elapsed_ms={},
                )
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot B",
                big_deal_summary="big B",
                chip_commentary="chip B",
                sentiment_commentary="sentiment B",
                risk_commentary="risk B",
                short_term_view="view B",
                confidence_label="medium",
                protocol_version="shortline_fg_v1",
                explanation_source="upstream_tools",
                used_upstream_tools=["HotMoneyTool", "BigDealAnalysisTool"],
                tool_error_count=1,
                tool_errors=["ChipAnalysisTool: chip failed"],
                explain_elapsed_ms=34,
                upstream_tool_elapsed_ms={
                    "HotMoneyTool": 21,
                    "ChipAnalysisTool": 4,
                    "BigDealAnalysisTool": 9,
                },
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="summary_run",
        trade_date="2026-05-02",
        top_n=2,
    )

    summary = result.summary_dict()

    assert summary["explanation_source_counts"] == {
        "bridge_data": 1,
        "upstream_tools": 1,
    }
    assert summary["bridge_data_hit_count"] == 1
    assert summary["upstream_tools_hit_count"] == 1
    assert summary["heuristic_fallback_count"] == 0
    assert summary["legacy_unknown_count"] == 0
    assert summary["upstream_tool_hit_counts"] == {
        "BigDealAnalysisTool": 1,
        "HotMoneyTool": 1,
    }
    assert summary["tool_error_count"] == 1
    assert summary["total_explain_elapsed_ms"] == 46
    assert summary["avg_explain_elapsed_ms"] == 23
    assert summary["max_explain_elapsed_ms"] == 34
    assert summary["min_explain_elapsed_ms"] == 12
    assert summary["upstream_tool_elapsed_totals_ms"] == {
        "BigDealAnalysisTool": 9,
        "ChipAnalysisTool": 4,
        "HotMoneyTool": 21,
    }
    assert summary["orchestrator_explain_elapsed_ms"] >= 0


def test_shortline_orchestrator_reuses_same_day_explain_cache(tmp_path: Path) -> None:
    class _StaticScanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="cache_case",
                    trade_date=trade_date,
                    scan_source="manual_watchlist",
                    trigger_type="manual_watchlist",
                    trigger_reason="manual_watchlist",
                    trigger_score=0.0,
                    price=0.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="",
                    setup_tag="手工观察",
                    risk_flags=[],
                )
            ][:top_n]

    class _CountingExplainer:
        def __init__(self) -> None:
            self.call_count = 0

        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            self.call_count += 1
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="sentiment",
                risk_commentary="risk",
                short_term_view="view",
                confidence_label="high",
            )

    cache = ShortlineExplainCache(tmp_path / "cache.json")
    explainer = _CountingExplainer()
    orchestrator = ShortlineHubOrchestrator(
        scanner=_StaticScanner(),
        explainer=explainer,
        explain_cache=cache,
        explain_cache_mode="light",
    )

    orchestrator.run(run_id="cache_r1", trade_date="2026-05-03", top_n=1)
    orchestrator.run(run_id="cache_r2", trade_date="2026-05-03", top_n=1)

    assert explainer.call_count == 1


def test_shortline_orchestrator_applies_tracking_history_to_combined_results() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="tracked_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test",
                    trigger_score=98.0,
                    price=10.19,
                    change_pct=20.02,
                    change_pct_60d=5.93,
                    amount=4003210000.0,
                    volume_ratio=5.66,
                    turnover_rate=26.82,
                    board_name="machine_tool",
                    setup_tag="limit_up",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="AI算力产业催化持续发酵",
                risk_commentary="risk",
                short_term_view="AI算力与产业链扩散",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="tracking_apply_run",
        trade_date="2026-05-03",
        top_n=1,
        tracking_history_rows=[
            {
                "trade_date": "2026-05-02",
                "symbol": "300083",
                "review_tier": "watchlist",
                "appear_streak_days": 1,
                "last_seen_dates": ["2026-05-02"],
            }
        ],
    )

    item = result.combined_results[0]
    summary = result.summary_dict()

    assert item.tracking_appear_streak_days == 2
    assert item.tracking_last_seen_dates == ["2026-05-02", "2026-05-03"]
    assert item.tracking_tier_transition == "watchlist->top_pick"
    assert summary["tracking_repeat_symbol_count"] == 1
    assert summary["tracking_longest_streak_days"] == 2


def test_shortline_run_summary_reports_cache_and_parallel_stats(tmp_path: Path) -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300083",
                    symbol="300083",
                    name="cached_case",
                    trade_date=trade_date,
                    scan_source="manual_watchlist",
                    trigger_type="manual_watchlist",
                    trigger_reason="manual_watchlist",
                    trigger_score=0.0,
                    price=0.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="",
                    setup_tag="手工观察",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-688256",
                    symbol="688256",
                    name="uncached_a",
                    trade_date=trade_date,
                    scan_source="manual_watchlist",
                    trigger_type="manual_watchlist",
                    trigger_reason="manual_watchlist",
                    trigger_score=0.0,
                    price=0.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="",
                    setup_tag="手工观察",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-600000",
                    symbol="600000",
                    name="uncached_b",
                    trade_date=trade_date,
                    scan_source="manual_watchlist",
                    trigger_type="manual_watchlist",
                    trigger_reason="manual_watchlist",
                    trigger_score=0.0,
                    price=0.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="",
                    setup_tag="手工观察",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary=f"{candidate.symbol} hot",
                big_deal_summary=f"{candidate.symbol} big",
                chip_commentary=f"{candidate.symbol} chip",
                sentiment_commentary=f"{candidate.symbol} sentiment",
                risk_commentary=f"{candidate.symbol} risk",
                short_term_view=f"{candidate.symbol} view",
                confidence_label="high",
                explain_elapsed_ms=15,
            )

    cache = ShortlineExplainCache(tmp_path / "cache.json")
    cache.put(
        trade_date="2026-05-03",
        symbol="300083",
        mode="light",
        explanation=ShortlineExplanation(
            candidate_id="2026-05-03-300083",
            hot_money_summary="cached hot",
            big_deal_summary="cached big",
            chip_commentary="cached chip",
            sentiment_commentary="cached sentiment",
            risk_commentary="cached risk",
            short_term_view="cached view",
            confidence_label="high",
            explain_elapsed_ms=9,
        ),
    )
    result = ShortlineHubOrchestrator(
        scanner=_Scanner(),
        explainer=_Explainer(),
        explain_cache=cache,
        explain_cache_mode="light",
    ).run(run_id="cache_stats_run", trade_date="2026-05-03", top_n=3)

    summary = result.summary_dict()
    report = build_shortline_report_markdown(result)

    assert summary["explain_cache_enabled"] is True
    assert summary["explain_cache_mode"] == "light"
    assert summary["explain_cache_hit_count"] == 1
    assert summary["explain_cache_miss_count"] == 2
    assert summary["explain_parallel_workers"] == 2
    assert "cache: enabled=True, mode=light, hits=1, misses=2" in report
    assert "parallel_workers: 2" in report


def test_report_builder_renders_markdown_summary() -> None:
    orchestrator = ShortlineHubOrchestrator(
        scanner=StubWonderTraderAdapter(),
        explainer=StubFinGeniusAdapter(),
    )
    result = orchestrator.run(run_id="demo_run", trade_date="2026-05-02", top_n=1)

    report = build_shortline_report_markdown(result)

    assert "# Shortline Hub Report" in report
    assert "## 结果概览" in report
    assert "候选来源分布" in report
    assert "## 候选概览" in report
    assert "## 逐票说明" in report
    assert "setup_tag" in report
    assert "000001" in report or "600000" in report


def test_report_builder_renders_extended_market_metrics() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300632",
                    symbol="300632",
                    name="test_stock",
                    trade_date=trade_date,
                    scan_source="wondertrader_real_engine",
                    trigger_type="limit_up_momentum",
                    trigger_reason="test reason",
                    trigger_score=99.0,
                    price=24.47,
                    change_pct=20.01,
                    change_pct_60d=63.7884,
                    amount=368278.0,
                    volume_ratio=0.0,
                    turnover_rate=16.65,
                    board_name="semiconductor",
                    setup_tag="test_tag",
                    risk_flags=["missing_volume_history"],
                )
            ][:top_n]

    orchestrator = ShortlineHubOrchestrator(
        scanner=_Scanner(),
        explainer=StubFinGeniusAdapter(),
    )
    result = orchestrator.run(run_id="metric_run", trade_date="2026-05-03", top_n=1)

    report = build_shortline_report_markdown(result)

    assert "60日涨幅" in report
    assert "63.79%" in report
    assert "成交额" in report
    assert "36.83万" in report
def test_orchestrator_combined_result_exposes_driver_support_defaults() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300000",
                    symbol="300000",
                    name="neutral_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="manual_watchlist",
                    trigger_reason="neutral",
                    trigger_score=0.0,
                    price=10.0,
                    change_pct=0.0,
                    change_pct_60d=0.0,
                    amount=0.0,
                    volume_ratio=0.0,
                    turnover_rate=0.0,
                    board_name="普通设备",
                    setup_tag="中性观察",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="中性资金",
                big_deal_summary="中性大单",
                chip_commentary="中性筹码",
                sentiment_commentary="暂无明确催化",
                risk_commentary="中性风险",
                short_term_view="看承接和换手",
                confidence_label="low",
            )

    orchestrator = ShortlineHubOrchestrator(
        scanner=_Scanner(),
        explainer=_Explainer(),
    )

    result = orchestrator.run(
        run_id="driver_defaults_run",
        trade_date="2026-05-02",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.driver_type == "flow_only"
    assert item.driver_confidence == "low"
    assert item.driver_support_score == 0.0
    assert item.driver_evidence == []

    payload = item.to_dict()
    assert payload["driver_type"] == "flow_only"
    assert payload["driver_confidence"] == "low"
    assert payload["driver_support_score"] == 0.0
    assert payload["driver_evidence"] == []


def test_orchestrator_applies_driver_support_classification() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300001",
                    symbol="300001",
                    name="driver_case",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="limit_up_momentum",
                    trigger_reason="订单放量",
                    trigger_score=96.0,
                    price=12.3,
                    change_pct=10.5,
                    change_pct_60d=22.0,
                    amount=380000000.0,
                    volume_ratio=2.6,
                    turnover_rate=14.2,
                    board_name="半导体",
                    setup_tag="test_tag",
                    risk_flags=[],
                )
            ][:top_n]

    class _Explainer:
        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="资金围绕半导体博弈",
                big_deal_summary="big",
                chip_commentary="chip",
                sentiment_commentary="业绩超预期，一季报净利润大增",
                risk_commentary="risk",
                short_term_view="业绩超预期，订单高增长",
                confidence_label="high",
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="driver_support_run",
        trade_date="2026-05-03",
        top_n=1,
    )

    item = result.combined_results[0]
    assert item.driver_type == "earnings_driver"
    assert item.driver_confidence == "high"
    assert item.driver_support_score > 0
    assert "业绩超预期" in item.driver_evidence
