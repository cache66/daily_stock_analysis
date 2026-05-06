#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed sample bridge_data and compare bridge-data mode with fallback mode."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shortline_hub.adapters.fingenius_adapter import FinGeniusProcessAdapter  # noqa: E402
from src.shortline_hub.adapters.process_utils import ProcessAdapterConfig  # noqa: E402
from src.shortline_hub.adapters.wondertrader_adapter import WonderTraderProcessAdapter  # noqa: E402
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator  # noqa: E402
from src.shortline_hub.report_builder import write_shortline_artifacts  # noqa: E402
from src.shortline_hub.schemas import ShortlineRunResult  # noqa: E402

DEFAULT_WT_ROOT = Path("D:/bb/WonderTrader")
DEFAULT_FG_ROOT = Path("D:/bb/FinGenius")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "manual_runs" / "shortline_bridge_data_compare"
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime" / "shortline_bridge_compare"
DEFAULT_WT_SOURCE_MODE = "prefer_real_engine"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed sample bridge_data and compare bridge-data mode with fallback mode."
    )
    parser.add_argument("--bridge-data-trade-date", required=True)
    parser.add_argument("--fallback-trade-date", required=True)
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--run-id-prefix", default="shortline_bridge_compare")
    parser.add_argument("--seed-sample-data", action="store_true")
    parser.add_argument("--wt-python-executable", default=sys.executable)
    parser.add_argument(
        "--wt-script-path",
        default=str(DEFAULT_WT_ROOT / "bridge" / "wt_export_candidates.py"),
    )
    parser.add_argument("--wt-workdir", default=str(DEFAULT_WT_ROOT))
    parser.add_argument(
        "--wt-runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR / "wondertrader"),
    )
    parser.add_argument("--wt-timeout-seconds", type=int, default=120)
    parser.add_argument("--fg-python-executable", default=sys.executable)
    parser.add_argument(
        "--fg-script-path",
        default=str(DEFAULT_FG_ROOT / "bridge" / "fg_explain_candidate.py"),
    )
    parser.add_argument("--fg-workdir", default=str(DEFAULT_FG_ROOT))
    parser.add_argument(
        "--fg-runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR / "fingenius"),
    )
    parser.add_argument("--fg-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_sample_bridge_data(trade_date: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = [
        {
            "candidate_id": f"{trade_date}-688001",
            "symbol": "688001",
            "name": "sample_real_alpha",
            "trade_date": trade_date,
            "scan_source": "wondertrader_export",
            "trigger_type": "manual_export",
            "trigger_reason": "loaded from bridge_data sample export",
            "trigger_score": 93.0,
            "price": 45.6,
            "change_pct": 8.9,
            "volume_ratio": 3.1,
            "turnover_rate": 7.2,
            "board_name": "chip",
            "setup_tag": "强势放量抢筹",
            "risk_flags": [],
        },
        {
            "candidate_id": f"{trade_date}-300308",
            "symbol": "300308",
            "name": "sample_real_beta",
            "trade_date": trade_date,
            "scan_source": "wondertrader_export",
            "trigger_type": "manual_export",
            "trigger_reason": "loaded from bridge_data sample export",
            "trigger_score": 88.0,
            "price": 12.3,
            "change_pct": 6.5,
            "volume_ratio": 2.4,
            "turnover_rate": 8.6,
            "board_name": "ai_device",
            "setup_tag": "板块核心跟涨",
            "risk_flags": ["high_volatility"],
        },
    ]
    explanations = {
        f"{trade_date}-688001": {
            "candidate_id": f"{trade_date}-688001",
            "hot_money_summary": "sample real hot money for 688001",
            "big_deal_summary": "sample real big deal for 688001",
            "chip_commentary": "sample real chip view for 688001",
            "sentiment_commentary": "sample real sentiment for 688001",
            "risk_commentary": "sample real risk for 688001",
            "short_term_view": "sample real short-term view for 688001",
            "confidence_label": "high",
        },
        f"{trade_date}-300308": {
            "candidate_id": f"{trade_date}-300308",
            "hot_money_summary": "sample real hot money for 300308",
            "big_deal_summary": "sample real big deal for 300308",
            "chip_commentary": "sample real chip view for 300308",
            "sentiment_commentary": "sample real sentiment for 300308",
            "risk_commentary": "sample real risk for 300308",
            "short_term_view": "sample real short-term view for 300308",
            "confidence_label": "medium",
        },
    }
    return candidates, explanations


def seed_sample_bridge_data(*, trade_date: str, wt_script_path: Path, fg_script_path: Path) -> dict[str, str]:
    candidates, explanations = _build_sample_bridge_data(trade_date)
    wt_path = wt_script_path.parent / "bridge_data" / f"wt_candidates_{trade_date}.json"
    fg_path = fg_script_path.parent / "bridge_data" / f"fg_explanations_{trade_date}.json"
    _write_json(wt_path, candidates)
    _write_json(fg_path, explanations)
    return {
        "wt_candidates_file": str(wt_path),
        "fg_explanations_file": str(fg_path),
    }


def _build_orchestrator(args: argparse.Namespace) -> ShortlineHubOrchestrator:
    return _build_orchestrator_for_wt_source_mode(args=args, wt_source_mode=DEFAULT_WT_SOURCE_MODE)


def _build_orchestrator_for_wt_source_mode(
    *,
    args: argparse.Namespace,
    wt_source_mode: str,
) -> ShortlineHubOrchestrator:
    scanner = WonderTraderProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=str(args.wt_python_executable),
            script_path=Path(str(args.wt_script_path)),
            runtime_dir=Path(str(args.wt_runtime_dir)),
            workdir=Path(str(args.wt_workdir)) if str(args.wt_workdir).strip() else None,
            timeout_seconds=int(args.wt_timeout_seconds),
        ),
        source_mode=str(wt_source_mode or DEFAULT_WT_SOURCE_MODE),
    )
    explainer = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=str(args.fg_python_executable),
            script_path=Path(str(args.fg_script_path)),
            runtime_dir=Path(str(args.fg_runtime_dir)),
            workdir=Path(str(args.fg_workdir)) if str(args.fg_workdir).strip() else None,
            timeout_seconds=int(args.fg_timeout_seconds),
        )
    )
    return ShortlineHubOrchestrator(scanner=scanner, explainer=explainer)


def _run_once(
    *,
    orchestrator: ShortlineHubOrchestrator,
    run_id: str,
    trade_date: str,
    top_n: int,
    output_dir: Path,
) -> ShortlineRunResult:
    result = orchestrator.run(run_id=run_id, trade_date=trade_date, top_n=top_n)
    write_shortline_artifacts(output_dir=output_dir, result=result)
    return result


def _build_run_summary(result: ShortlineRunResult, artifact_dir: Path) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "trade_date": result.trade_date,
        "candidate_count": len(result.candidates),
        "scan_sources": sorted({item.scan_source for item in result.candidates}),
        "symbols": [item.symbol for item in result.candidates],
        "setup_tags": [item.setup_tag for item in result.candidates],
        "confidence_labels": [item.confidence_label for item in result.explanations],
        "artifact_dir": str(artifact_dir),
    }


def build_compare_summary(
    *,
    bridge_data_result: ShortlineRunResult,
    bridge_data_dir: Path,
    fallback_result: ShortlineRunResult,
    fallback_dir: Path,
    seeded_paths: dict[str, str] | None,
) -> dict[str, Any]:
    bridge_sources = Counter(item.scan_source for item in bridge_data_result.candidates)
    fallback_sources = Counter(item.scan_source for item in fallback_result.candidates)
    bridge_symbols = {item.symbol for item in bridge_data_result.candidates}
    fallback_symbols = {item.symbol for item in fallback_result.candidates}
    return {
        "bridge_data_seeded": seeded_paths is not None,
        "seeded_paths": seeded_paths or {},
        "bridge_data_run": _build_run_summary(bridge_data_result, bridge_data_dir),
        "fallback_run": _build_run_summary(fallback_result, fallback_dir),
        "comparison": {
            "bridge_data_scan_source_counts": dict(bridge_sources),
            "fallback_scan_source_counts": dict(fallback_sources),
            "shared_symbols": sorted(bridge_symbols & fallback_symbols),
            "bridge_data_only_symbols": sorted(bridge_symbols - fallback_symbols),
            "fallback_only_symbols": sorted(fallback_symbols - bridge_symbols),
        },
    }


def build_compare_markdown(summary: dict[str, Any]) -> str:
    bridge = summary["bridge_data_run"]
    fallback = summary["fallback_run"]
    comparison = summary["comparison"]
    lines = [
        "# Shortline Bridge Data Compare",
        "",
        f"- bridge_data_seeded: {summary['bridge_data_seeded']}",
        f"- bridge_data_trade_date: {bridge['trade_date']}",
        f"- fallback_trade_date: {fallback['trade_date']}",
        "",
        "## Bridge Data Run",
        "",
        f"- run_id: {bridge['run_id']}",
        f"- candidate_count: {bridge['candidate_count']}",
        f"- scan_sources: {', '.join(bridge['scan_sources']) or '(none)'}",
        f"- symbols: {', '.join(bridge['symbols']) or '(none)'}",
        "",
        "## Fallback Run",
        "",
        f"- run_id: {fallback['run_id']}",
        f"- candidate_count: {fallback['candidate_count']}",
        f"- scan_sources: {', '.join(fallback['scan_sources']) or '(none)'}",
        f"- symbols: {', '.join(fallback['symbols']) or '(none)'}",
        "",
        "## Diff",
        "",
        f"- shared_symbols: {', '.join(comparison['shared_symbols']) or '(none)'}",
        f"- bridge_data_only_symbols: {', '.join(comparison['bridge_data_only_symbols']) or '(none)'}",
        f"- fallback_only_symbols: {', '.join(comparison['fallback_only_symbols']) or '(none)'}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    output_dir = Path(str(args.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    bridge_data_orchestrator = _build_orchestrator_for_wt_source_mode(
        args=args,
        wt_source_mode="prefer_bridge_data",
    )
    fallback_orchestrator = _build_orchestrator_for_wt_source_mode(
        args=args,
        wt_source_mode="prefer_real_engine",
    )

    seeded_paths: dict[str, str] | None = None
    if bool(args.seed_sample_data):
        seeded_paths = seed_sample_bridge_data(
            trade_date=str(args.bridge_data_trade_date),
            wt_script_path=Path(str(args.wt_script_path)),
            fg_script_path=Path(str(args.fg_script_path)),
        )

    bridge_data_dir = output_dir / "bridge_data_run"
    fallback_dir = output_dir / "fallback_run"
    bridge_data_result = _run_once(
        orchestrator=bridge_data_orchestrator,
        run_id=f"{args.run_id_prefix}_bridge_data",
        trade_date=str(args.bridge_data_trade_date),
        top_n=max(0, int(args.top_n)),
        output_dir=bridge_data_dir,
    )
    fallback_result = _run_once(
        orchestrator=fallback_orchestrator,
        run_id=f"{args.run_id_prefix}_fallback",
        trade_date=str(args.fallback_trade_date),
        top_n=max(0, int(args.top_n)),
        output_dir=fallback_dir,
    )

    summary = build_compare_summary(
        bridge_data_result=bridge_data_result,
        bridge_data_dir=bridge_data_dir,
        fallback_result=fallback_result,
        fallback_dir=fallback_dir,
        seeded_paths=seeded_paths,
    )
    _write_json(output_dir / "compare_summary.json", summary)
    (output_dir / "compare_report.md").write_text(
        build_compare_markdown(summary),
        encoding="utf-8",
    )
    logging.info(
        "shortline bridge-data compare finished: bridge_date=%s fallback_date=%s output_dir=%s",
        args.bridge_data_trade_date,
        args.fallback_trade_date,
        output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
