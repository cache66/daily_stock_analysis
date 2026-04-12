#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hundred-day new-high selector runner.

Scans the A-share market (excluding BSE) for stocks whose latest K-line high
reaches the highest high within the configured recent window, then persists a
daily signal snapshot, performs cause analysis, and backfills recent hit
history for the same signal type.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.kline_selector_service import (
    KlineSelectionEvaluation,
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorRunResult,
    KlineSelectorService,
)
from src.services.signal_cause_analysis_service import SignalCauseAnalysisService
from src.storage import DatabaseManager


logger = logging.getLogger("hundred_day_high_selector")

SIGNAL_TYPE = "hundred_day_high"
DEFAULT_HISTORY_LOOKBACK_DAYS = 180
DEFAULT_PROFILE_NAME = "breakout_balanced"

PROFILE_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "breakout_balanced": {
        "criteria": {
            "lookback_days": 8,
            "min_up_ratio": 0.625,
            "limit_up_lookback_days": 8,
            "new_high_window": 100,
            "require_up_day_ratio": True,
            "require_recent_limit_up": False,
            "require_new_high": True,
            "max_total_market_cap": 400.0 * 1e8,
        },
        "prefilter": {
            "min_change_pct_60d": 12.0,
            "min_turnover_rate": 0.8,
            "require_positive_change": True,
            "exclude_st": True,
        },
    },
    "momentum_strict": {
        "criteria": {
            "lookback_days": 8,
            "min_up_ratio": 0.67,
            "limit_up_lookback_days": 8,
            "new_high_window": 110,
            "require_up_day_ratio": True,
            "require_recent_limit_up": False,
            "require_new_high": True,
            "max_total_market_cap": 70.0 * 1e8,
        },
        "prefilter": {
            "min_change_pct_60d": 20.0,
            "min_turnover_rate": 1.2,
            "require_positive_change": True,
            "exclude_st": True,
        },
    },
    "breakout_loose": {
        "criteria": {
            "lookback_days": 12,
            "min_up_ratio": 0.58,
            "limit_up_lookback_days": 12,
            "new_high_window": 80,
            "require_up_day_ratio": False,
            "require_recent_limit_up": False,
            "require_new_high": True,
            "max_total_market_cap": 600.0 * 1e8,
        },
        "prefilter": {
            "min_change_pct_60d": 8.0,
            "min_turnover_rate": 0.5,
            "require_positive_change": True,
            "exclude_st": True,
        },
    },
}


def _shard_suffix(shard_count: int, shard_index: int) -> str:
    return f"shard_{shard_index + 1:02d}_of_{shard_count:02d}"


def resolve_output_dir(output_dir: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return output_dir
    return output_dir / _shard_suffix(shard_count, shard_index)


def resolve_checkpoint_path(checkpoint_path: Path, shard_count: int, shard_index: int) -> Path:
    if shard_count <= 1:
        return checkpoint_path
    suffix = _shard_suffix(shard_count, shard_index)
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{suffix}{checkpoint_path.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="筛选 A 股百日新高候选股，并归档每日信号、归因和历史复现信息。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅分析前 N 只股票，便于调试。")
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="将标准化后的 A 股股票池切分成 N 个分片，便于多进程并行跑全市场。",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="当前执行第几个分片，从 0 开始；需配合 --shard-count 使用。",
    )
    parser.add_argument(
        "--signal-type",
        default=SIGNAL_TYPE,
        help=f"落库信号类型标识，默认 {SIGNAL_TYPE}。",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        choices=sorted(PROFILE_PRESETS.keys()),
        help=f"筛选预设，默认 {DEFAULT_PROFILE_NAME}。",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="上涨占比规则的回看交易日数；默认跟随 profile。",
    )
    parser.add_argument(
        "--min-up-ratio",
        type=float,
        default=None,
        help="上涨占比规则阈值；默认跟随 profile。",
    )
    parser.add_argument(
        "--limit-up-lookback-days",
        type=int,
        default=None,
        help="涨停规则的回看交易日数；默认跟随 profile。",
    )
    parser.add_argument(
        "--new-high-window",
        type=int,
        default=None,
        help="新高窗口交易日数，默认 100。",
    )
    parser.add_argument(
        "--max-total-mv-yi",
        type=float,
        default=None,
        help="总市值上限，单位亿，默认 500。",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="信号归档日期，格式 YYYY-MM-DD；默认今天。",
    )
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"历史同口径信号回看窗口，默认 {DEFAULT_HISTORY_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--skip-cause-analysis",
        action="store_true",
        help="跳过上涨原因分析，仅做筛选、历史回看和落库。",
    )
    parser.add_argument(
        "--cause-analysis-only",
        action="store_true",
        help="只对当日已落库快照补全原因分析，不重新扫描全市场。",
    )
    parser.add_argument(
        "--force-cause-refresh",
        action="store_true",
        help="在 --cause-analysis-only 模式下强制重跑已有归因的股票；默认会跳过已补归因的快照。",
    )
    parser.add_argument(
        "--disable-news-search",
        action="store_true",
        help="归因阶段跳过新闻搜索，只用已有结构化信息生成快速摘要。",
    )
    parser.add_argument(
        "--disable-llm-reason-card",
        action="store_true",
        help="归因阶段不调用 LLM 压缩原因卡，直接输出结构化 fallback 摘要。",
    )
    parser.add_argument(
        "--skip-db-persist",
        action="store_true",
        help="跳过数据库快照写入，仅导出文件。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data"),
        help="输出目录，默认 data/；开启分片时会自动追加 shard 子目录。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="并发抓取 K 线的 worker 数，默认 1（当前 Windows 环境更稳）。",
    )
    parser.add_argument(
        "--min-60d-change-pct-prefilter",
        type=float,
        default=None,
        help="现货预过滤：60 日涨跌幅阈值，默认 10。",
    )
    parser.add_argument(
        "--min-turnover-rate-prefilter",
        type=float,
        default=None,
        help="现货预过滤：换手率阈值，默认不启用。",
    )
    parser.add_argument(
        "--require-positive-change-prefilter",
        action="store_true",
        help="现货预过滤：仅保留当日涨跌幅为正的股票。",
    )
    parser.add_argument(
        "--exclude-st-prefilter",
        action="store_true",
        help="现货预过滤：排除 ST 股票。",
    )
    parser.set_defaults(
        require_positive_change_prefilter=None,
        exclude_st_prefilter=None,
    )
    parser.add_argument(
        "--allow-non-positive-change-prefilter",
        dest="require_positive_change_prefilter",
        action="store_false",
        help="现货预过滤：允许当日涨跌幅非正的股票。",
    )
    parser.add_argument(
        "--include-st-prefilter",
        dest="exclude_st_prefilter",
        action="store_false",
        help="现货预过滤：保留 ST 股票。",
    )
    up_ratio_group = parser.add_mutually_exclusive_group()
    up_ratio_group.add_argument(
        "--require-up-day-ratio",
        dest="require_up_day_ratio",
        action="store_true",
        help="启用最近上涨占比规则。",
    )
    up_ratio_group.add_argument(
        "--skip-up-day-ratio-rule",
        dest="require_up_day_ratio",
        action="store_false",
        help="跳过最近上涨占比规则。",
    )
    limit_up_group = parser.add_mutually_exclusive_group()
    limit_up_group.add_argument(
        "--require-recent-limit-up",
        dest="require_recent_limit_up",
        action="store_true",
        help="启用最近涨停规则。",
    )
    limit_up_group.add_argument(
        "--skip-recent-limit-up-rule",
        dest="require_recent_limit_up",
        action="store_false",
        help="跳过最近涨停规则。",
    )
    parser.set_defaults(
        require_up_day_ratio=None,
        require_recent_limit_up=None,
    )
    parser.add_argument(
        "--disable-spot-prefilter",
        action="store_true",
        help="禁用现货预过滤。",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=str(PROJECT_ROOT / "data" / "hundred_day_high_checkpoint.json"),
        help="checkpoint 路径；开启分片时会自动追加 shard 后缀。",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=50,
        help="每处理多少只股票保存一次 checkpoint，默认 50。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="如果 checkpoint 已存在，则从上次进度继续运行。",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> date:
    """Parse a snapshot date from CLI input, defaulting to today."""
    if value is None or str(value).strip() == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid snapshot date: {text}") from exc


def resolve_profile_settings(args: argparse.Namespace) -> tuple[str, KlineSelectorCriteria, Optional[KlineSelectorPrefilter]]:
    """Resolve a named profile plus any explicit CLI overrides."""
    profile_name = str(getattr(args, "profile", DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME)
    preset = PROFILE_PRESETS.get(profile_name, PROFILE_PRESETS[DEFAULT_PROFILE_NAME])
    criteria_defaults = dict(preset["criteria"])
    prefilter_defaults = dict(preset["prefilter"])

    criteria = KlineSelectorCriteria(
        lookback_days=args.lookback_days if args.lookback_days is not None else int(criteria_defaults["lookback_days"]),
        min_up_ratio=args.min_up_ratio if args.min_up_ratio is not None else float(criteria_defaults["min_up_ratio"]),
        limit_up_lookback_days=(
            args.limit_up_lookback_days
            if args.limit_up_lookback_days is not None
            else int(criteria_defaults["limit_up_lookback_days"])
        ),
        new_high_window=args.new_high_window if args.new_high_window is not None else int(criteria_defaults["new_high_window"]),
        require_up_day_ratio=(
            args.require_up_day_ratio
            if args.require_up_day_ratio is not None
            else bool(criteria_defaults["require_up_day_ratio"])
        ),
        require_recent_limit_up=(
            args.require_recent_limit_up
            if args.require_recent_limit_up is not None
            else bool(criteria_defaults["require_recent_limit_up"])
        ),
        require_new_high=True,
        max_total_market_cap=(
            (args.max_total_mv_yi * 1e8)
            if args.max_total_mv_yi is not None
            else float(criteria_defaults["max_total_market_cap"])
        ),
    )

    prefilter = None
    if not args.disable_spot_prefilter:
        prefilter = KlineSelectorPrefilter(
            min_change_pct_60d=(
                args.min_60d_change_pct_prefilter
                if args.min_60d_change_pct_prefilter is not None
                else prefilter_defaults["min_change_pct_60d"]
            ),
            min_turnover_rate=(
                args.min_turnover_rate_prefilter
                if args.min_turnover_rate_prefilter is not None
                else prefilter_defaults["min_turnover_rate"]
            ),
            require_positive_change=(
                args.require_positive_change_prefilter
                if args.require_positive_change_prefilter is not None
                else bool(prefilter_defaults["require_positive_change"])
            ),
            exclude_st=(
                args.exclude_st_prefilter
                if args.exclude_st_prefilter is not None
                else bool(prefilter_defaults["exclude_st"])
            ),
        )

    return profile_name, criteria, prefilter


def build_selected_dataframe(run_result: KlineSelectorRunResult) -> pd.DataFrame:
    """Convert selected results into a sorted dataframe for export/enrichment."""
    selected_df = pd.DataFrame([item.to_record() for item in run_result.selected])
    if not selected_df.empty:
        selected_df = selected_df.sort_values(
            by=["latest_high", "total_market_cap_yi", "code"],
            ascending=[False, True, True],
        ).reset_index(drop=True)
    return selected_df


def build_signal_metrics_payload(
    evaluation: KlineSelectionEvaluation,
    *,
    snapshot_date: date,
) -> Dict[str, Any]:
    """Build a durable metrics payload for one selected signal hit."""
    metrics = evaluation.metrics or {}
    return {
        "signal_date": snapshot_date.isoformat(),
        "close": metrics.get("close"),
        "latest_high": metrics.get("latest_high"),
        "window_high": metrics.get("window_high"),
        "new_high_window": metrics.get("new_high_window"),
        "total_market_cap": evaluation.total_market_cap,
        "history_source": evaluation.history_source,
    }


def build_criteria_payload(
    criteria: KlineSelectorCriteria,
    *,
    signal_type: str = SIGNAL_TYPE,
    prefilter: Optional[KlineSelectorPrefilter],
    snapshot_date: date,
    profile_name: str = DEFAULT_PROFILE_NAME,
) -> Dict[str, Any]:
    """Serialize run-time筛选条件，便于同口径后续扩展。"""
    return {
        "signal_type": str(signal_type or SIGNAL_TYPE),
        "snapshot_date": snapshot_date.isoformat(),
        "profile_name": str(profile_name or DEFAULT_PROFILE_NAME),
        "criteria": asdict(criteria),
        "prefilter": prefilter.to_dict() if prefilter is not None else None,
    }


def build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    """Build recent same-signal history stats for one stock."""
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    recent_hit_dates = [row.signal_date.isoformat() for row in history_rows if row.signal_date]
    latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
    days_since_previous_hit = None
    if latest_previous_hit_date is not None:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days

    return {
        "lookback_days": lookback_days,
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def persist_selected_snapshot(
    evaluation: KlineSelectionEvaluation,
    *,
    signal_type: str = SIGNAL_TYPE,
    snapshot_date: date,
    criteria_payload: Optional[Dict[str, Any]],
    history_lookback_days: int,
    db: DatabaseManager,
    cause_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist one selected signal hit and return the derived payloads."""
    metrics_payload = build_signal_metrics_payload(
        evaluation,
        snapshot_date=snapshot_date,
    )
    history_payload = build_history_payload(
        db,
        signal_type=signal_type,
        stock_code=evaluation.stock_code,
        snapshot_date=snapshot_date,
        lookback_days=history_lookback_days,
    )
    db.upsert_signal_snapshot(
        signal_type=signal_type,
        signal_date=snapshot_date,
        code=evaluation.stock_code,
        name=evaluation.stock_name,
        criteria_payload=criteria_payload,
        metrics_payload=metrics_payload,
        cause_payload=cause_payload,
        history_payload=history_payload,
    )
    return {
        "metrics_payload": metrics_payload,
        "history_payload": history_payload,
        "cause_payload": cause_payload,
    }


def persist_selected_results(
    run_result: KlineSelectorRunResult,
    *,
    signal_type: str = SIGNAL_TYPE,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    db: DatabaseManager,
) -> pd.DataFrame:
    """
    Stage 1: persist same-day snapshots first so `/signals` can show progress
    before expensive cause analysis finishes.
    """
    selected_df = build_selected_dataframe(run_result)
    if selected_df.empty:
        return selected_df

    enrichment_by_code: Dict[str, Dict[str, Any]] = {}
    for evaluation in run_result.selected:
        payloads = persist_selected_snapshot(
            evaluation,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=history_lookback_days,
            db=db,
            cause_payload=None,
        )
        history_payload = payloads["history_payload"]
        enrichment_by_code[evaluation.stock_code] = {
            "industry": "",
            "reason_summary": "",
            "industry_logic": "",
            "news_logic": "",
            "technical_logic": "",
            "cause_tags": "",
            "theme_label": "",
            "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
            "previous_hit_count": history_payload.get("previous_hit_count", 0),
            "days_since_previous_hit": history_payload.get("days_since_previous_hit"),
        }

    for field in (
        "industry",
        "reason_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "cause_tags",
        "theme_label",
        "latest_previous_hit_date",
        "previous_hit_count",
        "days_since_previous_hit",
    ):
        selected_df[field] = selected_df["code"].map(
            lambda code: enrichment_by_code.get(code, {}).get(field)
        )
    return selected_df


def _criteria_from_snapshot_row(row: Any) -> KlineSelectorCriteria:
    criteria_payload = json.loads(getattr(row, "criteria_payload", "{}") or "{}")
    criteria_dict = criteria_payload.get("criteria") if isinstance(criteria_payload, dict) else None
    if isinstance(criteria_dict, dict):
        return KlineSelectorCriteria(**criteria_dict)
    return KlineSelectorCriteria(
        require_up_day_ratio=False,
        require_recent_limit_up=False,
        require_new_high=True,
    )


def load_snapshot_run_result(
    db: DatabaseManager,
    *,
    signal_type: str = SIGNAL_TYPE,
    snapshot_date: date,
    include_existing_cause: bool = True,
) -> KlineSelectorRunResult:
    """Load already-persisted same-day snapshots back into a run-result shape."""
    rows = db.get_signal_snapshots(
        signal_type=signal_type,
        signal_date=snapshot_date,
    )
    if not include_existing_cause:
        rows = [
            row for row in rows
            if not str(getattr(row, "cause_payload", "") or "").strip()
        ]
    if not rows:
        return KlineSelectorRunResult(
            criteria=KlineSelectorCriteria(
                require_up_day_ratio=False,
                require_recent_limit_up=False,
                require_new_high=True,
            ),
            universe_size=0,
            evaluated_count=0,
            skipped_market_cap_count=0,
            skipped_prefilter_count=0,
            universe_codes=[],
            selected=[],
            failed=[],
        )

    selected: List[KlineSelectionEvaluation] = []
    for row in rows:
        metrics = json.loads(getattr(row, "metrics_payload", "{}") or "{}")
        selected.append(
            KlineSelectionEvaluation(
                stock_code=getattr(row, "code", ""),
                stock_name=getattr(row, "name", "") or "",
                passed=True,
                history_source=str(metrics.get("history_source", "") or ""),
                total_market_cap=metrics.get("total_market_cap"),
                metrics=metrics,
            )
        )

    return KlineSelectorRunResult(
        criteria=_criteria_from_snapshot_row(rows[0]),
        universe_size=len(rows),
        evaluated_count=len(rows),
        skipped_market_cap_count=0,
        skipped_prefilter_count=0,
        universe_codes=[item.stock_code for item in selected],
        selected=selected,
        failed=[],
    )


def enrich_selected_results(
    run_result: KlineSelectorRunResult,
    *,
    signal_type: str = SIGNAL_TYPE,
    snapshot_date: date,
    criteria_payload: Optional[Dict[str, Any]],
    history_lookback_days: int,
    db: DatabaseManager,
    cause_analysis_service: Optional[SignalCauseAnalysisService] = None,
    perform_cause_analysis: bool = True,
    persist_snapshot: bool = True,
    enable_news_search: bool = True,
    enable_reason_card_llm: bool = True,
) -> pd.DataFrame:
    """
    Enrich selected stocks with history stats, cause analysis, and optional DB persistence.
    """
    selected_df = build_selected_dataframe(run_result)
    if selected_df.empty:
        return selected_df

    cause_service = cause_analysis_service or SignalCauseAnalysisService(
        enable_news_search=enable_news_search,
        enable_reason_card_llm=enable_reason_card_llm,
    )
    enrichment_by_code: Dict[str, Dict[str, Any]] = {}

    for evaluation in run_result.selected:
        metrics_payload = build_signal_metrics_payload(
            evaluation,
            snapshot_date=snapshot_date,
        )
        history_payload = build_history_payload(
            db,
            signal_type=signal_type,
            stock_code=evaluation.stock_code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )

        cause_payload: Optional[Dict[str, Any]] = None
        if perform_cause_analysis:
            try:
                cause_payload = cause_service.analyze_signal(
                    evaluation.stock_code,
                    evaluation.stock_name,
                    signal_type=signal_type,
                    metrics_payload=metrics_payload,
                )
            except Exception as exc:
                logger.warning(
                    "百日新高上涨原因分析失败，继续保留结构化结果: %s(%s) err=%s",
                    evaluation.stock_name,
                    evaluation.stock_code,
                    exc,
                )
                cause_payload = {
                    "analysis_status": "analysis_unavailable",
                    "industry": "",
                    "reason_summary": "",
                    "cause_tags": ["other"],
                    "theme_label": "",
                    "us_proxy_examples": [],
                    "mapping_evidence": [],
                    "evidence_points": [],
                    "fact_vs_inference": {"facts": [], "inferences": [str(exc)]},
                    "news_items": [],
                    "fundamental_context": {},
                }

        if persist_snapshot:
            persist_selected_snapshot(
                evaluation,
                signal_type=signal_type,
                snapshot_date=snapshot_date,
                criteria_payload=criteria_payload,
                history_lookback_days=history_lookback_days,
                db=db,
                cause_payload=cause_payload,
            )

        enrichment_by_code[evaluation.stock_code] = {
            "industry": str((cause_payload or {}).get("industry", "") or "").strip(),
            "reason_summary": str((cause_payload or {}).get("reason_summary", "") or "").strip(),
            "industry_logic": str((cause_payload or {}).get("industry_logic", "") or "").strip(),
            "news_logic": str((cause_payload or {}).get("news_logic", "") or "").strip(),
            "technical_logic": str((cause_payload or {}).get("technical_logic", "") or "").strip(),
            "cause_tags": ",".join((cause_payload or {}).get("cause_tags", []) or []),
            "theme_label": str((cause_payload or {}).get("theme_label", "") or "").strip(),
            "latest_previous_hit_date": history_payload.get("latest_previous_hit_date"),
            "previous_hit_count": history_payload.get("previous_hit_count", 0),
            "days_since_previous_hit": history_payload.get("days_since_previous_hit"),
        }

    for field in (
        "industry",
        "reason_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "cause_tags",
        "theme_label",
        "latest_previous_hit_date",
        "previous_hit_count",
        "days_since_previous_hit",
    ):
        selected_df[field] = selected_df["code"].map(
            lambda code: enrichment_by_code.get(code, {}).get(field)
        )
    return selected_df


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("\r", " ").replace("|", "/").strip()
    return text or "-"


def build_markdown_report(
    run_result: KlineSelectorRunResult,
    selected_df: pd.DataFrame,
    generated_at: str,
    *,
    snapshot_date: date,
    history_lookback_days: int,
) -> str:
    criteria = run_result.criteria
    lines = [
        "# 百日新高筛选结果",
        "",
        f"- 生成时间: {generated_at}",
        f"- 信号日期: {snapshot_date.isoformat()}",
        f"- A 股样本数（排除北交所）: {run_result.universe_size}",
        f"- 实际分析数: {run_result.evaluated_count}",
        f"- 因市值预过滤跳过: {run_result.skipped_market_cap_count}",
        f"- 因现货预过滤跳过: {run_result.skipped_prefilter_count}",
        f"- 命中数量: {len(run_result.selected)}",
        "",
        "## 当前规则",
        "",
        f"- 最新 K 线 high 创 {criteria.new_high_window} 日新高",
        f"- 总市值 <= {criteria.max_total_market_cap / 1e8:.2f} 亿",
        f"- 历史复现窗口: 近 {history_lookback_days} 天",
        "",
    ]

    if selected_df.empty:
        lines.extend(["## 命中结果", "", "本次没有找到满足百日新高条件的股票。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## 命中结果",
            "",
            "| 代码 | 名称 | 行业 | 最新 high | 收盘 | 上涨原因 | 标签 | 海外主题 | 上次命中 | 历史次数 | 距上次(天) | 数据源 |",
            "|------|------|------|----------:|-----:|----------|------|----------|----------|----------:|-----------:|--------|",
        ]
    )
    for row in selected_df.itertuples(index=False):
        lines.append(
            "| {code} | {name} | {industry} | {latest_high} | {close} | {reason_summary} | {cause_tags} | {theme_label} | {latest_previous_hit_date} | {previous_hit_count} | {days_since_previous_hit} | {history_source} |".format(
                code=_markdown_cell(row.code),
                name=_markdown_cell(row.name),
                industry=_markdown_cell(getattr(row, "industry", "")),
                latest_high=f"{float(row.latest_high):.2f}" if pd.notna(row.latest_high) else "-",
                close=f"{float(row.close):.2f}" if pd.notna(row.close) else "-",
                reason_summary=_markdown_cell(getattr(row, "reason_summary", "")),
                cause_tags=_markdown_cell(getattr(row, "cause_tags", "")),
                theme_label=_markdown_cell(getattr(row, "theme_label", "")),
                latest_previous_hit_date=_markdown_cell(getattr(row, "latest_previous_hit_date", "")),
                previous_hit_count=_markdown_cell(getattr(row, "previous_hit_count", 0)),
                days_since_previous_hit=_markdown_cell(getattr(row, "days_since_previous_hit", "")),
                history_source=_markdown_cell(getattr(row, "history_source", "")),
            )
        )
    lines.extend(["", "## 逻辑拆解", ""])
    for row in selected_df.itertuples(index=False):
        lines.extend(
            [
                f"### {row.code} {row.name}",
                f"- 行业逻辑：{_markdown_cell(getattr(row, 'industry_logic', ''))}",
                f"- 消息逻辑：{_markdown_cell(getattr(row, 'news_logic', ''))}",
                f"- 技术逻辑：{_markdown_cell(getattr(row, 'technical_logic', ''))}",
                "",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def export_results(
    run_result: KlineSelectorRunResult,
    output_dir: Path,
    *,
    snapshot_date: date,
    history_lookback_days: int,
    checkpoint_path: Optional[Path] = None,
    selected_df: Optional[pd.DataFrame] = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    universe_txt_path = output_dir / "a_share_universe_no_bse.txt"
    universe_txt_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    selected_df = selected_df if selected_df is not None else build_selected_dataframe(run_result)

    csv_path = output_dir / "hundred_day_high_candidates.csv"
    txt_path = output_dir / "hundred_day_high_candidates.txt"
    md_path = output_dir / "hundred_day_high_candidates.md"
    exported_checkpoint_path = output_dir / "hundred_day_high_checkpoint.json"

    export_columns = [
        "code",
        "name",
        "industry",
        "total_market_cap",
        "total_market_cap_yi",
        "close",
        "latest_high",
        "window_high",
        "new_high_window",
        "history_source",
        "reason_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "cause_tags",
        "theme_label",
        "latest_previous_hit_date",
        "previous_hit_count",
        "days_since_previous_hit",
        "failure_reason",
    ]
    for column in export_columns:
        if column not in selected_df.columns:
            selected_df[column] = None
    selected_df = selected_df.reindex(columns=export_columns)

    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "",
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(
            run_result,
            selected_df,
            generated_at,
            snapshot_date=snapshot_date,
            history_lookback_days=history_lookback_days,
        ),
        encoding="utf-8",
    )
    if checkpoint_path is not None and checkpoint_path.exists():
        exported_checkpoint_path.write_text(
            checkpoint_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    logger.info("已写出 A 股样本列表: %s", universe_txt_path)
    logger.info("已写出百日新高结果 CSV: %s", csv_path)
    logger.info("已写出百日新高结果 TXT: %s", txt_path)
    logger.info("已写出百日新高结果 Markdown: %s", md_path)
    if checkpoint_path is not None and checkpoint_path.exists():
        logger.info("已复制 checkpoint 到输出目录: %s", exported_checkpoint_path)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    output_dir = resolve_output_dir(Path(args.output_dir), args.shard_count, args.shard_index)
    checkpoint_path = resolve_checkpoint_path(
        Path(args.checkpoint_path),
        args.shard_count,
        args.shard_index,
    )

    try:
        snapshot_date = parse_snapshot_date(args.snapshot_date)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    if args.skip_cause_analysis and args.cause_analysis_only:
        logger.error("--skip-cause-analysis cannot be used together with --cause-analysis-only")
        return 2

    runtime_signal_type = str(getattr(args, "signal_type", SIGNAL_TYPE) or SIGNAL_TYPE).strip() or SIGNAL_TYPE
    profile_name, criteria, prefilter = resolve_profile_settings(args)

    service = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    criteria_payload = build_criteria_payload(
        criteria,
        signal_type=runtime_signal_type,
        prefilter=prefilter,
        snapshot_date=snapshot_date,
        profile_name=profile_name,
    )
    db = DatabaseManager.get_instance()
    if args.cause_analysis_only:
        logger.info(
            "开始运行百日新高归因补全: snapshot_date=%s, history_lookback_days=%s, output_dir=%s",
            snapshot_date.isoformat(),
            args.history_lookback_days,
            output_dir,
        )
        run_result = load_snapshot_run_result(
            db,
            signal_type=runtime_signal_type,
            snapshot_date=snapshot_date,
            include_existing_cause=args.force_cause_refresh,
        )
        if not run_result.selected:
            if args.force_cause_refresh:
                logger.warning("当日没有已落库的百日新高快照，无法执行归因补全: %s", snapshot_date.isoformat())
            else:
                logger.info("当日待补归因的百日新高快照为空，已跳过: %s", snapshot_date.isoformat())
            export_results(
                run_result,
                output_dir,
                snapshot_date=snapshot_date,
                history_lookback_days=args.history_lookback_days,
                checkpoint_path=checkpoint_path,
                selected_df=build_selected_dataframe(run_result),
            )
            return 0

        selected_df = enrich_selected_results(
            run_result,
            signal_type=runtime_signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=None,
            history_lookback_days=args.history_lookback_days,
            db=db,
            perform_cause_analysis=True,
            persist_snapshot=not args.skip_db_persist,
            enable_news_search=not args.disable_news_search,
            enable_reason_card_llm=not args.disable_llm_reason_card,
        )
        export_results(
            run_result,
            output_dir,
            snapshot_date=snapshot_date,
            history_lookback_days=args.history_lookback_days,
            checkpoint_path=checkpoint_path,
            selected_df=selected_df,
        )
        logger.info(
            "百日新高归因补全完成: snapshot_date=%s, selected=%s",
            snapshot_date.isoformat(),
            len(run_result.selected),
        )
        return 0
    logger.info(
        "开始运行百日新高策略: snapshot_date=%s, new_high_window=%s, max_total_mv=%.2f亿, limit=%s, "
        "max_workers=%s, shard=%s/%s, history_lookback_days=%s, spot_prefilter=%s, "
        "output_dir=%s, checkpoint=%s, resume=%s, cause_analysis=%s, db_persist=%s, mode=%s, profile=%s",
        snapshot_date.isoformat(),
        criteria.new_high_window,
        criteria.max_total_market_cap / 1e8,
        args.limit or "ALL",
        args.max_workers,
        args.shard_index + 1,
        args.shard_count,
        args.history_lookback_days,
        "disabled" if prefilter is None else prefilter.to_dict(),
        output_dir,
        checkpoint_path,
        args.resume,
        not args.skip_cause_analysis,
        not args.skip_db_persist,
        "scan_then_enrich",
        profile_name,
    )
    partial_persisted_codes: set[str] = set()

    def handle_incremental_snapshot(
        evaluation: KlineSelectionEvaluation,
        completed: int,
        total_eligible: int,
    ) -> None:
        if args.skip_db_persist or not evaluation.passed or evaluation.stock_code in partial_persisted_codes:
            return
        persist_selected_snapshot(
            evaluation,
            signal_type=runtime_signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=args.history_lookback_days,
            db=db,
            cause_payload=None,
        )
        partial_persisted_codes.add(evaluation.stock_code)
        if completed == 1 or completed % 100 == 0 or completed == total_eligible:
            logger.info(
                "百日新高阶段 1/2: 已增量落库 %s 只（当前进度 %s/%s）",
                len(partial_persisted_codes),
                completed,
                total_eligible,
            )
    run_result = service.scan_market(
        criteria=criteria,
        limit=args.limit,
        max_workers=args.max_workers,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
        on_evaluation=handle_incremental_snapshot,
    )
    if not args.skip_db_persist:
        logger.info(
            "百日新高阶段 1/2 完成: 开始补齐全部已命中快照，selected=%s, snapshot_date=%s",
            len(run_result.selected),
            snapshot_date.isoformat(),
        )
        selected_df = persist_selected_results(
            run_result,
            signal_type=runtime_signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=args.history_lookback_days,
            db=db,
        )
    else:
        selected_df = build_selected_dataframe(run_result)

    if args.skip_cause_analysis:
        logger.info("百日新高阶段 2/2 已跳过；可稍后使用 --cause-analysis-only 对同日快照补全归因。")
    else:
        logger.info(
            "百日新高阶段 2/2: 开始补全归因与结构化摘要，selected=%s, snapshot_date=%s",
            len(run_result.selected),
            snapshot_date.isoformat(),
        )
        selected_df = enrich_selected_results(
            run_result,
            signal_type=runtime_signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=args.history_lookback_days,
            db=db,
            perform_cause_analysis=True,
            persist_snapshot=not args.skip_db_persist,
            enable_news_search=not args.disable_news_search,
            enable_reason_card_llm=not args.disable_llm_reason_card,
        )
    export_results(
        run_result,
        output_dir,
        snapshot_date=snapshot_date,
        history_lookback_days=args.history_lookback_days,
        checkpoint_path=checkpoint_path,
        selected_df=selected_df,
    )

    logger.info(
        "百日新高筛选完成: universe=%s, evaluated=%s, skipped_by_market_cap=%s, "
        "skipped_by_prefilter=%s, selected=%s, snapshot_date=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        run_result.skipped_market_cap_count,
        run_result.skipped_prefilter_count,
        len(run_result.selected),
        snapshot_date.isoformat(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
