#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the shortline hub orchestration skeleton."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shortline_hub.adapters.fingenius_adapter import (  # noqa: E402
    FinGeniusProcessAdapter,
    StubFinGeniusAdapter,
)
from src.shortline_hub.adapters.process_utils import ProcessAdapterConfig  # noqa: E402
from src.shortline_hub.adapters.wondertrader_adapter import (  # noqa: E402
    StubWonderTraderAdapter,
    WonderTraderProcessAdapter,
)
from src.shortline_hub.explain_cache import ShortlineExplainCache  # noqa: E402
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator  # noqa: E402
from src.shortline_hub.report_builder import write_shortline_artifacts  # noqa: E402
from src.shortline_hub.snapshot_sync import persist_shortline_run_to_snapshots  # noqa: E402
from src.shortline_hub.tracking import (  # noqa: E402
    build_tracking_rows_from_results,
    load_tracking_history,
    update_tracking_history,
    write_tracking_history_csv,
    write_tracking_history_json,
)
from src.shortline_hub.watchlist_loader import (  # noqa: E402
    ManualWatchlistScanner,
    load_watchlist_symbols,
)
from src.storage import DatabaseManager  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "shortline_hub"
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime" / "shortline_hub"
DEFAULT_EXPLAIN_CACHE_PATH = DEFAULT_RUNTIME_DIR / "explain_cache" / "shortline_explain_cache.json"
DEFAULT_TRACKING_HISTORY_PATH = DEFAULT_RUNTIME_DIR / "tracking" / "shortline_tracking_history.json"
DEFAULT_WT_SOURCE_MODE = "prefer_real_engine"


def _resolve_explain_cache_mode(args: argparse.Namespace) -> str:
    configured = str(getattr(args, "explain_cache_mode", "auto") or "auto").strip().lower()
    if configured and configured != "auto":
        return configured
    if bool(getattr(args, "fg_enable_big_deal", False)):
        return "full"
    return "light"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the shortline hub orchestration skeleton.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--run-id", default="shortline_hub_demo")
    parser.add_argument("--mode", default="stub", choices=["stub", "process"])
    parser.add_argument("--wt-python-executable", default=sys.executable)
    parser.add_argument("--wt-script-path", default="")
    parser.add_argument("--wt-workdir", default="")
    parser.add_argument("--wt-runtime-dir", default=str(DEFAULT_RUNTIME_DIR / "wondertrader"))
    parser.add_argument("--wt-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--wt-source-mode",
        default=DEFAULT_WT_SOURCE_MODE,
        choices=["prefer_real_engine", "prefer_bridge_data"],
    )
    parser.add_argument("--fg-python-executable", default=sys.executable)
    parser.add_argument("--fg-script-path", default="")
    parser.add_argument("--fg-workdir", default="")
    parser.add_argument("--fg-runtime-dir", default=str(DEFAULT_RUNTIME_DIR / "fingenius"))
    parser.add_argument("--fg-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--fg-enable-big-deal",
        action="store_true",
        help="Enable the slow BigDealAnalysisTool in FinGenius process mode.",
    )
    parser.add_argument(
        "--enable-explain-cache",
        dest="enable_explain_cache",
        action="store_true",
        help="Enable same-day shortline explanation cache.",
    )
    parser.add_argument(
        "--disable-explain-cache",
        dest="enable_explain_cache",
        action="store_false",
        help="Disable shortline explanation cache.",
    )
    parser.set_defaults(enable_explain_cache=False)
    parser.add_argument("--explain-cache-path", default=str(DEFAULT_EXPLAIN_CACHE_PATH))
    parser.add_argument("--explain-cache-mode", default="auto")
    parser.add_argument("--manual-watchlist", action="store_true")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbols-file", default="")
    parser.add_argument("--tracking-history-path", default=str(DEFAULT_TRACKING_HISTORY_PATH))
    parser.add_argument("--persist-snapshot", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _build_orchestrator(args: argparse.Namespace) -> ShortlineHubOrchestrator:
    mode = str(getattr(args, "mode", "stub") or "stub")
    manual_watchlist = bool(getattr(args, "manual_watchlist", False))
    if manual_watchlist:
        symbols = load_watchlist_symbols(
            symbols=str(getattr(args, "symbols", "") or ""),
            symbols_file=str(getattr(args, "symbols_file", "") or ""),
        )
        if not symbols:
            raise ValueError("--manual-watchlist requires --symbols or --symbols-file")
        scanner = ManualWatchlistScanner(symbols=symbols)
    elif mode == "process":
        if not str(getattr(args, "wt_script_path", "")).strip():
            raise ValueError("--wt-script-path is required in process mode")
        if not str(getattr(args, "fg_script_path", "")).strip():
            raise ValueError("--fg-script-path is required in process mode")
        scanner = WonderTraderProcessAdapter(
            config=ProcessAdapterConfig(
                python_executable=str(getattr(args, "wt_python_executable", sys.executable)),
                script_path=Path(str(getattr(args, "wt_script_path", ""))),
                runtime_dir=Path(str(getattr(args, "wt_runtime_dir", DEFAULT_RUNTIME_DIR / "wondertrader"))),
                workdir=Path(str(getattr(args, "wt_workdir", "")))
                if str(getattr(args, "wt_workdir", "")).strip()
                else None,
                timeout_seconds=int(getattr(args, "wt_timeout_seconds", 120)),
            ),
            source_mode=str(getattr(args, "wt_source_mode", DEFAULT_WT_SOURCE_MODE) or DEFAULT_WT_SOURCE_MODE),
        )
    else:
        scanner = StubWonderTraderAdapter()
    if mode == "process":
        explainer = FinGeniusProcessAdapter(
            config=ProcessAdapterConfig(
                python_executable=str(getattr(args, "fg_python_executable", sys.executable)),
                script_path=Path(str(getattr(args, "fg_script_path", ""))),
                runtime_dir=Path(str(getattr(args, "fg_runtime_dir", DEFAULT_RUNTIME_DIR / "fingenius"))),
                workdir=Path(str(getattr(args, "fg_workdir", "")))
                if str(getattr(args, "fg_workdir", "")).strip()
                else None,
                timeout_seconds=int(getattr(args, "fg_timeout_seconds", 120)),
            ),
            enable_big_deal=bool(getattr(args, "fg_enable_big_deal", False)),
        )
    else:
        explainer = StubFinGeniusAdapter()
    explain_cache = None
    if bool(getattr(args, "enable_explain_cache", False)):
        explain_cache = ShortlineExplainCache(
            Path(str(getattr(args, "explain_cache_path", DEFAULT_EXPLAIN_CACHE_PATH)))
        )
    return ShortlineHubOrchestrator(
        scanner=scanner,
        explainer=explainer,
        explain_cache=explain_cache,
        explain_cache_mode=_resolve_explain_cache_mode(args),
        db_manager=DatabaseManager.get_instance(),
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    orchestrator = _build_orchestrator(args)
    tracking_history_path = Path(
        str(getattr(args, "tracking_history_path", DEFAULT_TRACKING_HISTORY_PATH))
    )
    previous_tracking_rows = load_tracking_history(tracking_history_path)
    result = orchestrator.run(
        run_id=str(args.run_id),
        trade_date=str(args.trade_date),
        top_n=max(0, int(args.top_n)),
        tracking_history_rows=previous_tracking_rows,
    )
    tracking_history_rows = update_tracking_history(
        previous_rows=previous_tracking_rows,
        current_rows=build_tracking_rows_from_results(result.combined_results),
    )
    paths = write_shortline_artifacts(
        output_dir=Path(str(args.output_dir)),
        result=result,
        tracking_history_rows=tracking_history_rows,
    )
    write_tracking_history_json(tracking_history_path, tracking_history_rows)
    write_tracking_history_csv(tracking_history_path.with_suffix(".csv"), tracking_history_rows)
    logging.info(
        "shortline hub finished: run_id=%s candidates=%s output_dir=%s",
        result.run_id,
        len(result.candidates),
        Path(str(args.output_dir)),
    )
    if bool(getattr(args, "persist_snapshot", False)):
        try:
            persisted = persist_shortline_run_to_snapshots(
                db_manager=DatabaseManager.get_instance(),
                result=result,
                source="run_shortline_hub",
            )
            logging.info("shortline snapshot persisted rows=%s", persisted)
        except Exception:
            logging.exception("persist shortline snapshot failed")
    logging.debug("artifacts=%s", paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
