# -*- coding: utf-8 -*-
"""FinGenius-style explanation adapter."""

from __future__ import annotations

from src.shortline_hub.adapters.process_utils import (
    ProcessAdapterConfig,
    build_runtime_paths,
    read_json_payload,
    run_external_python_process,
    write_json_payload,
)
from src.shortline_hub.schemas import ShortlineCandidate, ShortlineExplanation


class StubFinGeniusAdapter:
    """Deterministic stub that simulates FinGenius explanation output."""

    def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
        risk_text = (
            f"{candidate.name} 当前主要风险在 {', '.join(candidate.risk_flags)}，若板块承接转弱，短线波动会放大。"
            if candidate.risk_flags
            else "当前未见明显额外风险提示，重点观察次日承接和板块扩散。"
        )
        board_label = candidate.board_name or "当前强势方向"
        setup_label = candidate.setup_tag or candidate.trigger_type
        return ShortlineExplanation(
            candidate_id=candidate.candidate_id,
            hot_money_summary=f"{candidate.name} 当前更像 {board_label} 方向里的主动走强样本，资金关注度偏高。",
            big_deal_summary=f"{candidate.name} 先按 {setup_label} 结构理解，重点看换手和量比是否继续配合。",
            chip_commentary=f"{candidate.name} 当前筹码更偏交易型，若量价继续同步，短线结构还有延续空间。",
            sentiment_commentary=f"{board_label} 仍是这只票最直接的情绪锚点，强弱切换先看板块。",
            risk_commentary=risk_text,
            short_term_view=f"短线先看 {board_label} 能否继续扩散，再看 {setup_label} 是否持续得到承接。",
            confidence_label="high" if candidate.trigger_score >= 80 else "medium",
        )


class FinGeniusProcessAdapter:
    """Call an external FinGenius-compatible script via local process."""

    def __init__(self, *, config: ProcessAdapterConfig, enable_big_deal: bool = False) -> None:
        self.config = config
        self.enable_big_deal = bool(enable_big_deal)

    def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
        request_path, output_path = build_runtime_paths(self.config.runtime_dir, "fingenius")
        write_json_payload(
            request_path,
            {
                "candidate": candidate.to_dict(),
                "bridge_options": {
                    "enable_big_deal": self.enable_big_deal,
                },
            },
        )
        run_external_python_process(
            config=self.config,
            request_path=request_path,
            output_path=output_path,
        )
        payload = read_json_payload(output_path)
        used_upstream_tools = payload.get("used_upstream_tools") or []
        if isinstance(used_upstream_tools, str):
            used_upstream_tools = [used_upstream_tools]
        tool_errors = payload.get("tool_errors") or []
        if isinstance(tool_errors, str):
            tool_errors = [tool_errors]
        upstream_tool_elapsed_ms = payload.get("upstream_tool_elapsed_ms") or {}
        if not isinstance(upstream_tool_elapsed_ms, dict):
            upstream_tool_elapsed_ms = {}
        return ShortlineExplanation(
            candidate_id=str(payload["candidate_id"]),
            hot_money_summary=str(payload["hot_money_summary"]),
            big_deal_summary=str(payload["big_deal_summary"]),
            chip_commentary=str(payload["chip_commentary"]),
            sentiment_commentary=str(payload["sentiment_commentary"]),
            risk_commentary=str(payload["risk_commentary"]),
            short_term_view=str(payload["short_term_view"]),
            confidence_label=str(payload["confidence_label"]),
            protocol_version=str(payload.get("protocol_version") or ""),
            explanation_source=str(payload.get("explanation_source") or "legacy_unknown"),
            used_upstream_tools=[
                str(item).strip() for item in used_upstream_tools if str(item).strip()
            ],
            tool_error_count=max(0, int(payload.get("tool_error_count") or 0)),
            tool_errors=[str(item).strip() for item in tool_errors if str(item).strip()],
            explain_elapsed_ms=max(0, int(payload.get("explain_elapsed_ms") or 0)),
            upstream_tool_elapsed_ms={
                str(key).strip(): max(0, int(value or 0))
                for key, value in upstream_tool_elapsed_ms.items()
                if str(key).strip()
            },
        )
