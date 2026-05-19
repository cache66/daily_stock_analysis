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
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.fundamental_adapter import AkshareFundamentalAdapter
from scripts.select_earnings_surprise_candidates import (
    FULL_FUNDAMENTAL_BLOCKS,
    DEFAULT_STRATEGY_PROFILE as DEFAULT_EARNINGS_STRATEGY_PROFILE,
    EarningsSurpriseCriteria,
    SIGNAL_TYPE as EARNINGS_SIGNAL_TYPE,
    evaluate_earnings_surprise_candidate,
    get_strategy_profile_preset,
    load_or_fetch_signal_fundamental_snapshot,
)
from src.services.kline_selector_service import (
    KlineSelectionEvaluation,
    KlineSelectorCriteria,
    KlineSelectorPrefilter,
    KlineSelectorRunResult,
    KlineSelectorService,
)
from src.services.shared_signal_factors_service import SharedSignalFactorsService
from src.services.signal_cause_analysis_service import SignalCauseAnalysisService
from src.storage import DatabaseManager


logger = logging.getLogger("hundred_day_high_selector")

SIGNAL_TYPE = "hundred_day_high"
DEFAULT_HISTORY_LOOKBACK_DAYS = 180
DEFAULT_PROFILE_NAME = "breakout_balanced"
EARNINGS_BALANCED_PROFILE_NAME = "breakout_balanced_with_earnings"
EARNINGS_BALANCED_SIGNAL_TYPE = "hundred_day_high__earnings_balanced"
EARNINGS_METRIC_KEYS: List[str] = [
    "revenue_yoy",
    "net_profit_yoy",
    "roe",
    "earnings_strategy_score",
    "earnings_strategy_label",
    "earnings_strategy_gate_status",
    "earnings_quality_signal",
    "earnings_quality_score",
    "earnings_quality_verdict",
    "earnings_quality_cycle_phase",
    "earnings_quality_quarterly_trend",
    "earnings_quality_dual_positive_streak",
    "earnings_reason_summary",
]
BREAKOUT_QUALITY_METRIC_KEYS: List[str] = [
    "breakout_quality_score",
    "breakout_contraction_ratio",
    "breakout_volume_ratio",
    "distance_to_new_high_pct",
    "minervini_template_score",
    "minervini_template_passed",
    "breakout_follow_through_score",
]
CHART_PATTERN_METRIC_KEYS: List[str] = [
    "chart_pattern_label",
    "chart_pattern_score",
    "chart_pattern_summary",
    "base_breakout_score",
    "healthy_trend_score",
]
OUTPUT_DIR_LOCK_FILENAME = "hundred_day_high_run.lock"
INDUSTRY_STRENGTH_METRIC_KEYS: List[str] = [
    "industry_strength_score",
    "industry_strength_confirmed",
    "industry_strength_label",
    "industry_strength_board_names",
    "industry_strength_confirmation_hint",
]
QUALITY_OVERLAY_METRIC_KEYS: List[str] = [
    "earnings_continuity_available",
    "earnings_continuity_score",
    "quality_overlay_available",
    "quality_overlay_score",
    "quality_overlay_label",
    "quality_overlay_source",
    "earnings_revenue_positive_quarter_streak",
    "earnings_profit_positive_quarter_streak",
    "earnings_roe_positive_quarter_streak",
    "earnings_financial_series_continuity_score",
    "earnings_financial_series_quarter_count",
]

PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
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
    EARNINGS_BALANCED_PROFILE_NAME: {
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


def resolve_checkpoint_path(
    output_dir: Path,
    checkpoint_path: Optional[Path],
    *,
    shard_count: int,
    shard_index: int,
) -> Path:
    if checkpoint_path is None:
        return output_dir / "hundred_day_high_checkpoint.json"
    if shard_count <= 1:
        return checkpoint_path
    suffix = _shard_suffix(shard_count, shard_index)
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{suffix}{checkpoint_path.suffix}")


def _read_output_dir_lock_payload(lock_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _is_pid_running(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def hold_output_dir_lock(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / OUTPUT_DIR_LOCK_FILENAME
    lock_payload = {
        "pid": os.getpid(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
    }
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            existing_payload = _read_output_dir_lock_payload(lock_path)
            existing_pid = existing_payload.get("pid")
            if attempt == 0 and not _is_pid_running(existing_pid):
                try:
                    lock_path.unlink()
                    continue
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
            raise RuntimeError(
                f"output_dir is already in use: {output_dir} "
                f"(lock={lock_path}, pid={existing_pid or 'unknown'})"
            )
    else:
        raise RuntimeError(f"failed to acquire output_dir lock: {output_dir}")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(lock_payload, handle, ensure_ascii=False, indent=2)
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def profile_requires_earnings_confirmation(profile_name: str) -> bool:
    return str(profile_name or "").strip() == EARNINGS_BALANCED_PROFILE_NAME


def resolve_runtime_signal_type(*, signal_type: str, profile_name: str) -> str:
    normalized_signal_type = str(signal_type or SIGNAL_TYPE).strip() or SIGNAL_TYPE
    if normalized_signal_type != SIGNAL_TYPE:
        return normalized_signal_type
    if profile_requires_earnings_confirmation(profile_name):
        return EARNINGS_BALANCED_SIGNAL_TYPE
    return SIGNAL_TYPE


def _build_balanced_earnings_criteria() -> EarningsSurpriseCriteria:
    preset = get_strategy_profile_preset(DEFAULT_EARNINGS_STRATEGY_PROFILE)
    return EarningsSurpriseCriteria(
        strategy_profile=preset["name"],
        min_revenue_yoy=preset["min_revenue_yoy"],
        min_net_profit_yoy=preset["min_net_profit_yoy"],
        min_roe=preset["min_roe"],
        require_positive_text=bool(preset["require_positive_text"]),
        require_growth_thresholds=bool(preset["require_growth_thresholds"]),
        strategy_direct_pass_score=float(preset["strategy_direct_pass_score"]),
        strategy_watch_pass_score=float(preset["strategy_watch_pass_score"]),
        require_quality_confirmation_for_watch=bool(preset.get("require_quality_confirmation_for_watch", True)),
        max_total_market_cap=None,
        dedupe_by_event_key=False,
    )


def _extract_earnings_metrics(metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = metrics or {}
    extracted = {key: source.get(key) for key in EARNINGS_METRIC_KEYS if key in source}
    for key in QUALITY_OVERLAY_METRIC_KEYS:
        if key in source:
            extracted[key] = source.get(key)
    if source.get("reason_summary") is not None:
        extracted["earnings_reason_summary"] = source.get("reason_summary")
    return extracted


def _normalize_earnings_evaluation_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return {
            "passed": bool(result.get("passed", False)),
            "failure_reason": str(result.get("failure_reason", "") or ""),
            "metrics": dict(result.get("metrics") or {}),
        }
    return {
        "passed": bool(getattr(result, "passed", False)),
        "failure_reason": str(getattr(result, "failure_reason", "") or ""),
        "metrics": dict(getattr(result, "metrics", {}) or {}),
    }


def _evaluate_selected_candidate_with_balanced_earnings(
    evaluation: KlineSelectionEvaluation,
    *,
    snapshot_date: date,
    db: DatabaseManager,
    adapter: Optional[AkshareFundamentalAdapter] = None,
) -> Dict[str, Any]:
    resolved_adapter = adapter or AkshareFundamentalAdapter()
    latest_price = (evaluation.metrics or {}).get("close")
    payload = load_or_fetch_signal_fundamental_snapshot(
        db=db,
        cache_signal_type=EARNINGS_SIGNAL_TYPE,
        snapshot_date=snapshot_date,
        stock_code=evaluation.stock_code,
        stock_name=evaluation.stock_name,
        total_market_cap=evaluation.total_market_cap,
        latest_price=latest_price,
        adapter=resolved_adapter,
        recent_event_payload=None,
        scan_depth="high",
        required_blocks=FULL_FUNDAMENTAL_BLOCKS,
    )
    bundle_payload = dict(payload.get("bundle_payload") or {})
    earnings_evaluation = evaluate_earnings_surprise_candidate(
        stock_code=evaluation.stock_code,
        stock_name=payload.get("stock_name") or evaluation.stock_name,
        bundle_payload=bundle_payload,
        criteria=_build_balanced_earnings_criteria(),
        total_market_cap=evaluation.total_market_cap,
        latest_price=latest_price,
        snapshot_date=snapshot_date,
        signal_type=EARNINGS_SIGNAL_TYPE,
        db=None,
    )
    normalized_result = _normalize_earnings_evaluation_result(earnings_evaluation)
    quality_overlay = SharedSignalFactorsService.build_quality_overlay_factors(bundle_payload)
    merged_metrics = dict(normalized_result.get("metrics") or {})
    for key in QUALITY_OVERLAY_METRIC_KEYS:
        if key in quality_overlay:
            merged_metrics[key] = quality_overlay.get(key)
    normalized_result["metrics"] = merged_metrics
    return normalized_result


def filter_selected_results_by_earnings_balanced(
    run_result: KlineSelectorRunResult,
    *,
    snapshot_date: date,
    db: DatabaseManager,
    earnings_evaluator: Optional[Any] = None,
) -> KlineSelectorRunResult:
    if not run_result.selected:
        return run_result

    retained: List[KlineSelectionEvaluation] = []
    rejected: List[KlineSelectionEvaluation] = []
    resolved_evaluator = earnings_evaluator or (
        lambda evaluation, *, snapshot_date, db: _evaluate_selected_candidate_with_balanced_earnings(
            evaluation,
            snapshot_date=snapshot_date,
            db=db,
        )
    )
    for evaluation in run_result.selected:
        try:
            earnings_result = _normalize_earnings_evaluation_result(
                resolved_evaluator(evaluation, snapshot_date=snapshot_date, db=db)
            )
        except Exception as exc:
            logger.warning("earnings_filter_failed_evaluation code=%s err=%s", evaluation.stock_code, exc)
            rejected.append(
                KlineSelectionEvaluation(
                    stock_code=evaluation.stock_code,
                    stock_name=evaluation.stock_name,
                    passed=False,
                    history_source=evaluation.history_source,
                    total_market_cap=evaluation.total_market_cap,
                    failure_reason="earnings filter evaluation failed",
                    metrics=dict(evaluation.metrics or {}),
                    rule_results=copy.deepcopy(evaluation.rule_results),
                )
            )
            continue

        merged_metrics = dict(evaluation.metrics or {})
        merged_metrics.update(_extract_earnings_metrics(earnings_result.get("metrics")))
        if earnings_result.get("passed"):
            evaluation.metrics = merged_metrics
            retained.append(evaluation)
            continue

        rejected.append(
            KlineSelectionEvaluation(
                stock_code=evaluation.stock_code,
                stock_name=evaluation.stock_name,
                passed=False,
                history_source=evaluation.history_source,
                total_market_cap=evaluation.total_market_cap,
                failure_reason=str(earnings_result.get("failure_reason") or "earnings filter rejected"),
                metrics=merged_metrics,
                rule_results=copy.deepcopy(evaluation.rule_results),
            )
        )

    if not rejected and len(retained) == len(run_result.selected):
        return run_result

    return KlineSelectorRunResult(
        criteria=run_result.criteria,
        universe_size=run_result.universe_size,
        evaluated_count=run_result.evaluated_count,
        skipped_market_cap_count=run_result.skipped_market_cap_count,
        skipped_prefilter_count=run_result.skipped_prefilter_count,
        skipped_listed_days_count=getattr(run_result, "skipped_listed_days_count", 0),
        universe_codes=list(run_result.universe_codes),
        selected=retained,
        failed=list(run_result.failed) + rejected,
        phase_metrics=dict(getattr(run_result, "phase_metrics", {}) or {}),
    )


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
        "--min-listed-days-prefilter",
        type=int,
        default=None,
        help="现货预过滤：最低上市天数，默认与当前历史要求对齐。",
    )
    parser.add_argument(
        "--disable-listed-days-prefilter",
        action="store_true",
        help="关闭上市天数快速短路。",
    )
    parser.add_argument(
        "--disable-shared-scan-shell",
        action="store_true",
        help="Disable service-level shared scan shell for diagnostics.",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=None,
        help="可选 checkpoint 路径；未传时默认使用 output_dir 下的 hundred_day_high_checkpoint.json，开启分片时会自动追加 shard 后缀。",
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
        min_listed_days = None
        if not bool(getattr(args, "disable_listed_days_prefilter", False)):
            min_listed_days = (
                int(args.min_listed_days_prefilter)
                if getattr(args, "min_listed_days_prefilter", None) is not None
                else criteria.history_days_required
            )
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
            min_listed_days=min_listed_days,
        )

    return profile_name, criteria, prefilter


def _rolling_ma_value(series: pd.Series, *, window: int, min_periods: int, offset: int = 0) -> Optional[float]:
    if series is None or series.empty:
        return None
    rolling = series.rolling(window=window, min_periods=min_periods).mean()
    target_index = -1 - max(0, int(offset))
    if len(rolling) < abs(target_index):
        return None
    value = rolling.iloc[target_index]
    if pd.isna(value):
        return None
    return float(value)


def _compute_minervini_template_metrics(close: pd.Series, high: pd.Series, low: pd.Series) -> Dict[str, Any]:
    latest_close = float(close.iloc[-1])
    ma50 = _rolling_ma_value(close, window=50, min_periods=20)
    ma150 = _rolling_ma_value(close, window=150, min_periods=60)
    ma200 = _rolling_ma_value(close, window=200, min_periods=80)
    ma200_prev_20 = _rolling_ma_value(close, window=200, min_periods=80, offset=20)

    low_52w = float(low.tail(min(252, len(low))).min()) if not low.empty else None
    high_52w = float(high.tail(min(252, len(high))).max()) if not high.empty else None
    distance_to_52w_high_pct = (
        ((high_52w - latest_close) / high_52w * 100.0)
        if high_52w is not None and high_52w > 0
        else None
    )

    score = 0.0
    if ma50 is not None and latest_close > ma50:
        score += 2.0
    if ma50 is not None and ma150 is not None and ma50 > ma150:
        score += 2.0
    if ma150 is not None and ma200 is not None and ma150 > ma200:
        score += 2.0
    if ma200 is not None and ma200_prev_20 is not None and ma200 >= ma200_prev_20:
        score += 2.0
    if low_52w is not None and low_52w > 0 and latest_close >= low_52w * 1.25:
        score += 1.0
    if distance_to_52w_high_pct is not None and distance_to_52w_high_pct <= 25.0:
        score += 1.0

    minervini_score = round(min(10.0, score), 2)
    return {
        "minervini_template_score": minervini_score,
        "minervini_template_passed": bool(minervini_score >= 6.0),
    }


def _compute_breakout_follow_through_score(close: pd.Series, *, breakout_high: Optional[float]) -> float:
    if close is None or len(close) < 2:
        return 0.0
    latest_close = float(close.iloc[-1])
    score = 0.0

    prior_close = float(close.iloc[-2])
    if prior_close > 0:
        one_day_return_pct = (latest_close / prior_close - 1.0) * 100.0
        if one_day_return_pct >= 0:
            score += 1.5
        if one_day_return_pct >= 1.0:
            score += 0.5

    if len(close) >= 4:
        base_close = float(close.iloc[-4])
        if base_close > 0:
            three_day_return_pct = (latest_close / base_close - 1.0) * 100.0
            if three_day_return_pct >= 1.0:
                score += 2.0
            if three_day_return_pct >= 2.0:
                score += 1.0

    if breakout_high is not None and breakout_high > 0 and latest_close >= breakout_high * 0.98:
        score += 2.0

    return round(min(6.0, max(0.0, score)), 2)


def _empty_chart_pattern_metrics() -> Dict[str, Any]:
    return {
        "chart_pattern_label": "plain_breakout",
        "chart_pattern_score": 0.0,
        "chart_pattern_summary": "图形一般",
        "base_breakout_score": 0.0,
        "healthy_trend_score": 0.0,
    }


def _compute_trailing_max_drawdown_pct(close: pd.Series) -> float:
    if close is None or len(close) < 2:
        return 0.0
    rolling_peak = close.cummax().replace(0, pd.NA)
    drawdown_pct = ((rolling_peak - close) / rolling_peak * 100.0).fillna(0.0)
    return round(float(drawdown_pct.max() or 0.0), 2)


def classify_hundred_day_chart_pattern(
    history: pd.DataFrame,
    *,
    breakout_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _empty_chart_pattern_metrics()
    if history is None or history.empty:
        return payload

    numeric = history.copy()
    for column in ("close", "high", "low"):
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    if "volume" in numeric.columns:
        numeric["volume"] = pd.to_numeric(numeric["volume"], errors="coerce")
    numeric = numeric.dropna(subset=["close", "high", "low"]).copy()
    if len(numeric) < 60:
        return payload

    metrics = dict(breakout_metrics or {})
    if not metrics:
        metrics = compute_breakout_quality_metrics(numeric)

    close = numeric["close"].astype(float)
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    close_20 = close.tail(min(20, len(close)))
    close_60 = close.tail(min(60, len(close)))

    latest_close = float(close.iloc[-1])
    base_20 = float(close_20.iloc[0]) if len(close_20) else latest_close
    base_60 = float(close_60.iloc[0]) if len(close_60) else latest_close
    return_20_pct = ((latest_close / base_20 - 1.0) * 100.0) if base_20 > 0 else 0.0
    return_60_pct = ((latest_close / base_60 - 1.0) * 100.0) if base_60 > 0 else 0.0

    positive_days_20 = float((close_20.diff().fillna(0.0) > 0).mean()) if len(close_20) >= 2 else 0.0
    drawdown_20_pct = _compute_trailing_max_drawdown_pct(close_20)
    drawdown_60_pct = _compute_trailing_max_drawdown_pct(close_60)

    latest_ma20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else None
    latest_ma60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else None
    ma20_prev = float(ma20.iloc[-11]) if len(ma20) >= 11 and pd.notna(ma20.iloc[-11]) else latest_ma20
    ma20_slope_pct = ((latest_ma20 / ma20_prev - 1.0) * 100.0) if latest_ma20 and ma20_prev else 0.0

    range_60_pct = (
        (float(close_60.max()) - float(close_60.min())) / float(close_60.min()) * 100.0
        if len(close_60) and float(close_60.min()) > 0
        else 0.0
    )
    contraction_ratio = float(metrics.get("breakout_contraction_ratio") or 1.0)
    volume_ratio = float(metrics.get("breakout_volume_ratio") or 0.0)
    distance_to_new_high_pct = float(metrics.get("distance_to_new_high_pct") or 99.0)
    follow_through_score = float(metrics.get("breakout_follow_through_score") or 0.0)
    breakout_quality_score = float(metrics.get("breakout_quality_score") or 0.0)

    base_breakout_score = 0.0
    if distance_to_new_high_pct <= 1.0:
        base_breakout_score += 4.0
    if contraction_ratio <= 0.85:
        base_breakout_score += 4.0
    if contraction_ratio <= 0.70:
        base_breakout_score += 2.0
    if volume_ratio >= 1.2:
        base_breakout_score += 2.0
    if volume_ratio >= 1.5:
        base_breakout_score += 1.0
    if contraction_ratio <= 0.78 and volume_ratio >= 1.2:
        base_breakout_score += 2.0
    if follow_through_score >= 3.0:
        base_breakout_score += 2.0
    if distance_to_new_high_pct <= 0.6 and follow_through_score >= 5.0:
        base_breakout_score += 1.0
    if breakout_quality_score >= 10.0:
        base_breakout_score += 2.0
    if range_60_pct <= 25.0:
        base_breakout_score += 1.0

    healthy_trend_score = 0.0
    if return_20_pct >= 6.0:
        healthy_trend_score += 3.0
    if return_60_pct >= 15.0:
        healthy_trend_score += 3.0
    if positive_days_20 >= 0.55:
        healthy_trend_score += 2.0
    if drawdown_20_pct <= 6.0:
        healthy_trend_score += 3.0
    if drawdown_60_pct <= 12.0:
        healthy_trend_score += 2.0
    if latest_ma20 is not None and latest_ma60 is not None and latest_close >= latest_ma20 >= latest_ma60:
        healthy_trend_score += 3.0
    if ma20_slope_pct >= 1.5:
        healthy_trend_score += 2.0

    base_breakout_score = round(min(18.0, max(0.0, base_breakout_score)), 2)
    healthy_trend_score = round(min(16.0, max(0.0, healthy_trend_score)), 2)

    if (
        base_breakout_score >= 14.0
        and contraction_ratio <= 0.78
        and breakout_quality_score >= 10.0
    ) or (base_breakout_score >= 11.0 and base_breakout_score > healthy_trend_score):
        payload.update(
            {
                "chart_pattern_label": "base_breakout",
                "chart_pattern_score": base_breakout_score,
                "chart_pattern_summary": "横盘突破型",
                "base_breakout_score": base_breakout_score,
                "healthy_trend_score": healthy_trend_score,
            }
        )
        return payload

    if healthy_trend_score >= 10.0:
        payload.update(
            {
                "chart_pattern_label": "healthy_trend",
                "chart_pattern_score": healthy_trend_score,
                "chart_pattern_summary": "健康慢涨型",
                "base_breakout_score": base_breakout_score,
                "healthy_trend_score": healthy_trend_score,
            }
        )
        return payload

    payload.update(
        {
            "chart_pattern_label": "plain_breakout",
            "chart_pattern_score": round(max(base_breakout_score, healthy_trend_score), 2),
            "chart_pattern_summary": "图形一般",
            "base_breakout_score": base_breakout_score,
            "healthy_trend_score": healthy_trend_score,
        }
    )
    return payload


def compute_breakout_quality_metrics(history: pd.DataFrame) -> Dict[str, Any]:
    empty_payload = {
        "breakout_quality_score": 0.0,
        "breakout_contraction_ratio": None,
        "breakout_volume_ratio": None,
        "distance_to_new_high_pct": None,
        "minervini_template_score": 0.0,
        "minervini_template_passed": False,
        "breakout_follow_through_score": 0.0,
    }
    if history is None or history.empty:
        return empty_payload

    numeric = history.copy()
    for column in ("close", "high", "low"):
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    if "volume" in numeric.columns:
        numeric["volume"] = pd.to_numeric(numeric["volume"], errors="coerce")
    numeric = numeric.dropna(subset=["close", "high", "low"]).copy()
    if numeric.empty:
        return empty_payload

    close = numeric["close"]
    high = numeric["high"]
    low = numeric["low"]
    latest_close = float(close.iloc[-1])
    high_window = float(high.tail(min(100, len(high))).max())
    distance_to_new_high_pct = ((high_window - latest_close) / high_window * 100.0) if high_window > 0 else None

    range_pct = ((high - low) / close.replace(0, pd.NA) * 100.0).fillna(0.0)
    recent_range_pct = float(range_pct.tail(5).mean()) if len(range_pct) >= 5 else float(range_pct.mean() or 0.0)
    prior_window = range_pct.iloc[-20:-5] if len(range_pct) >= 20 else range_pct.iloc[:-5]
    prior_range_pct = float(prior_window.mean()) if not prior_window.empty else recent_range_pct
    contraction_ratio = (recent_range_pct / prior_range_pct) if prior_range_pct and prior_range_pct > 0 else 1.0

    volume_ratio = None
    if "volume" in numeric.columns:
        volume_series = pd.to_numeric(numeric["volume"], errors="coerce").dropna()
        if len(volume_series) >= 6:
            recent_volume = float(volume_series.iloc[-1])
            prior_volume = float(volume_series.iloc[-6:-1].mean())
            if prior_volume > 0:
                volume_ratio = recent_volume / prior_volume

    minervini_metrics = _compute_minervini_template_metrics(close, high, low)
    follow_through_score = _compute_breakout_follow_through_score(close, breakout_high=high_window)

    quality_score = 0.0
    if distance_to_new_high_pct is not None and distance_to_new_high_pct <= 1.0:
        quality_score += 4.0
    if contraction_ratio <= 0.85:
        quality_score += 4.0
    if contraction_ratio <= 0.70:
        quality_score += 2.0
    if volume_ratio is not None and volume_ratio >= 1.2:
        quality_score += 3.0
    if volume_ratio is not None and volume_ratio >= 1.5:
        quality_score += 3.0
    if minervini_metrics["minervini_template_passed"]:
        quality_score += 2.0
    if follow_through_score >= 3.0:
        quality_score += 2.0

    payload = {
        "breakout_quality_score": round(min(20.0, quality_score), 2),
        "breakout_contraction_ratio": round(contraction_ratio, 4),
        "breakout_volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
        "distance_to_new_high_pct": round(distance_to_new_high_pct, 2) if distance_to_new_high_pct is not None else None,
        "breakout_follow_through_score": follow_through_score,
    }
    payload.update(minervini_metrics)
    return payload


def _min_breakout_quality_score_for_profile(profile_name: str) -> float:
    normalized = str(profile_name or DEFAULT_PROFILE_NAME).strip()
    if normalized == "momentum_strict":
        return 8.0
    if normalized in {DEFAULT_PROFILE_NAME, EARNINGS_BALANCED_PROFILE_NAME}:
        return 6.0
    if normalized == "breakout_loose":
        return 4.0
    return 0.0


def _empty_breakout_quality_metrics() -> Dict[str, Any]:
    return {
        "breakout_quality_score": 0.0,
        "breakout_contraction_ratio": None,
        "breakout_volume_ratio": None,
        "distance_to_new_high_pct": None,
        "minervini_template_score": 0.0,
        "minervini_template_passed": False,
        "breakout_follow_through_score": 0.0,
    }


def _empty_breakout_and_chart_pattern_metrics() -> Dict[str, Any]:
    payload = _empty_breakout_quality_metrics()
    payload.update(_empty_chart_pattern_metrics())
    return payload


def _compute_breakout_quality_for_stock(
    stock_code: str,
    *,
    manager: Any,
) -> tuple[bool, Dict[str, Any]]:
    try:
        history_df, _ = manager.get_daily_data(stock_code, days=180)
        prepared = KlineSelectorService._prepare_history(history_df)
        breakout_metrics = compute_breakout_quality_metrics(prepared)
        chart_pattern_metrics = classify_hundred_day_chart_pattern(
            prepared,
            breakout_metrics=breakout_metrics,
        )
        merged_metrics = dict(breakout_metrics)
        merged_metrics.update(chart_pattern_metrics)
        return True, merged_metrics
    except Exception as exc:
        logger.debug("breakout quality enrich failed for %s: %s", stock_code, exc)
        return False, _empty_breakout_and_chart_pattern_metrics()


def _build_breakout_quality_metrics_map(
    evaluations: List[KlineSelectionEvaluation],
    *,
    service: KlineSelectorService,
    max_workers: int,
) -> tuple[Dict[str, tuple[bool, Dict[str, Any]]], Dict[str, Any]]:
    if not evaluations:
        return {}, {
            "breakout_quality_parallel_enabled": False,
            "breakout_quality_parallel_workers": 0,
        }

    resolved_max_workers = max(1, int(max_workers or 1))
    manager_factory = getattr(service, "_manager_factory", None)
    primary_manager = getattr(service, "manager", None)
    parallel_enabled = bool(callable(manager_factory) and resolved_max_workers > 1 and len(evaluations) > 1)
    metrics_by_code: Dict[str, tuple[bool, Dict[str, Any]]] = {}

    if primary_manager is None and not parallel_enabled:
        for evaluation in evaluations:
            metrics_by_code[evaluation.stock_code] = (False, _empty_breakout_and_chart_pattern_metrics())
        return metrics_by_code, {
            "breakout_quality_parallel_enabled": False,
            "breakout_quality_parallel_workers": 0,
        }

    if not parallel_enabled:
        for evaluation in evaluations:
            metrics_by_code[evaluation.stock_code] = _compute_breakout_quality_for_stock(
                evaluation.stock_code,
                manager=primary_manager,
            )
        return metrics_by_code, {
            "breakout_quality_parallel_enabled": False,
            "breakout_quality_parallel_workers": 1,
        }

    worker_count = min(resolved_max_workers, len(evaluations))

    def _worker(stock_code: str) -> tuple[str, bool, Dict[str, Any]]:
        manager = manager_factory()
        quality_available, breakout_metrics = _compute_breakout_quality_for_stock(
            stock_code,
            manager=manager,
        )
        return stock_code, quality_available, breakout_metrics

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hundred-day-quality") as executor:
        future_map = {
            executor.submit(_worker, evaluation.stock_code): evaluation.stock_code
            for evaluation in evaluations
        }
        for future in as_completed(future_map):
            stock_code = future_map[future]
            try:
                resolved_code, quality_available, breakout_metrics = future.result()
                metrics_by_code[resolved_code] = (quality_available, breakout_metrics)
            except Exception as exc:
                logger.debug("breakout quality parallel worker failed for %s: %s", stock_code, exc)
                metrics_by_code[stock_code] = (False, _empty_breakout_and_chart_pattern_metrics())

    return metrics_by_code, {
        "breakout_quality_parallel_enabled": True,
        "breakout_quality_parallel_workers": worker_count,
    }


def _enrich_run_result_with_breakout_quality(
    run_result: KlineSelectorRunResult,
    *,
    service: KlineSelectorService,
    profile_name: str,
    max_workers: int = 1,
) -> KlineSelectorRunResult:
    if not run_result.selected:
        return run_result

    enrich_started_at = datetime.now()
    quality_floor = _min_breakout_quality_score_for_profile(profile_name)
    retained: List[KlineSelectionEvaluation] = []
    rejected: List[KlineSelectionEvaluation] = []
    metrics_by_code, enrich_phase_metrics = _build_breakout_quality_metrics_map(
        run_result.selected,
        service=service,
        max_workers=max_workers,
    )

    for evaluation in run_result.selected:
        quality_available, breakout_metrics = metrics_by_code.get(
            evaluation.stock_code,
            (False, _empty_breakout_and_chart_pattern_metrics()),
        )

        merged_metrics = dict(evaluation.metrics or {})
        merged_metrics.update(breakout_metrics)
        quality_score = float(merged_metrics.get("breakout_quality_score") or 0.0)
        evaluation.metrics = merged_metrics
        if (not quality_available) or quality_score >= quality_floor:
            retained.append(evaluation)
            continue

        rejected.append(
            KlineSelectionEvaluation(
                stock_code=evaluation.stock_code,
                stock_name=evaluation.stock_name,
                passed=False,
                history_source=evaluation.history_source,
                total_market_cap=evaluation.total_market_cap,
                failure_reason=f"breakout quality score {quality_score:.1f} < required {quality_floor:.1f}",
                metrics=merged_metrics,
                rule_results=copy.deepcopy(evaluation.rule_results),
            )
        )

    if not rejected and len(retained) == len(run_result.selected):
        run_result.phase_metrics = dict(getattr(run_result, "phase_metrics", {}) or {})
        run_result.phase_metrics.update(
            {
                **enrich_phase_metrics,
                "breakout_quality_enrichment_elapsed_sec": round(
                    (datetime.now() - enrich_started_at).total_seconds(),
                    4,
                ),
            }
        )
        return run_result

    merged_phase_metrics = dict(getattr(run_result, "phase_metrics", {}) or {})
    merged_phase_metrics.update(
        {
            **enrich_phase_metrics,
            "breakout_quality_enrichment_elapsed_sec": round(
                (datetime.now() - enrich_started_at).total_seconds(),
                4,
            ),
        }
    )
    return KlineSelectorRunResult(
        criteria=run_result.criteria,
        universe_size=run_result.universe_size,
        evaluated_count=run_result.evaluated_count,
        skipped_market_cap_count=run_result.skipped_market_cap_count,
        skipped_prefilter_count=run_result.skipped_prefilter_count,
        skipped_listed_days_count=getattr(run_result, "skipped_listed_days_count", 0),
        universe_codes=list(run_result.universe_codes),
        selected=retained,
        failed=list(run_result.failed) + rejected,
        phase_metrics=merged_phase_metrics,
    )


def build_selected_dataframe(run_result: KlineSelectorRunResult) -> pd.DataFrame:
    """Convert selected results into a sorted dataframe for export/enrichment."""
    selected_df = pd.DataFrame([item.to_record() for item in run_result.selected])
    if not selected_df.empty:
        metrics_by_code = {item.stock_code: dict(item.metrics or {}) for item in run_result.selected}
        for key in EARNINGS_METRIC_KEYS:
            selected_df[key] = selected_df["code"].map(lambda code: metrics_by_code.get(code, {}).get(key))
        for key in QUALITY_OVERLAY_METRIC_KEYS:
            selected_df[key] = selected_df["code"].map(lambda code: metrics_by_code.get(code, {}).get(key))
        for key in BREAKOUT_QUALITY_METRIC_KEYS:
            selected_df[key] = selected_df["code"].map(lambda code: metrics_by_code.get(code, {}).get(key))
        for key in CHART_PATTERN_METRIC_KEYS:
            selected_df[key] = selected_df["code"].map(lambda code: metrics_by_code.get(code, {}).get(key))
        for key in INDUSTRY_STRENGTH_METRIC_KEYS:
            selected_df[key] = selected_df["code"].map(lambda code: metrics_by_code.get(code, {}).get(key))
        selected_df = selected_df.sort_values(
            by=["breakout_quality_score", "latest_high", "total_market_cap_yi", "code"],
            ascending=[False, False, True, True],
        ).reset_index(drop=True)
    return selected_df


def build_signal_metrics_payload(
    evaluation: KlineSelectionEvaluation,
    *,
    snapshot_date: date,
) -> Dict[str, Any]:
    """Build a durable metrics payload for one selected signal hit."""
    metrics = evaluation.metrics or {}
    payload = {
        "signal_date": snapshot_date.isoformat(),
        "close": metrics.get("close"),
        "latest_high": metrics.get("latest_high"),
        "window_high": metrics.get("window_high"),
        "new_high_window": metrics.get("new_high_window"),
        "total_market_cap": evaluation.total_market_cap,
        "history_source": evaluation.history_source,
    }
    for key in BREAKOUT_QUALITY_METRIC_KEYS:
        if key in metrics:
            payload[key] = metrics.get(key)
    for key in CHART_PATTERN_METRIC_KEYS:
        if key in metrics:
            payload[key] = metrics.get(key)
    for key in QUALITY_OVERLAY_METRIC_KEYS:
        if key in metrics:
            payload[key] = metrics.get(key)
    for key in INDUSTRY_STRENGTH_METRIC_KEYS:
        if key in metrics:
            payload[key] = metrics.get(key)
    payload.update(_extract_earnings_metrics(metrics))
    return payload


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


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_board_names(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        names: List[str] = []
        for item in raw_value:
            if isinstance(item, dict):
                name = _safe_text(item.get("name"))
            else:
                name = _safe_text(item)
            if name and name not in names:
                names.append(name)
        return names[:5]
    if isinstance(raw_value, str):
        return [name for name in (_safe_text(raw_value),) if name]
    return []


def _split_theme_label_to_boards(theme_label: Any) -> List[str]:
    label = _safe_text(theme_label)
    if not label:
        return []
    normalized = label
    for separator in ("、", ",", "|"):
        normalized = normalized.replace(separator, "/")
    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    boards: List[str] = []
    for part in parts:
        if part and part not in boards:
            boards.append(part)
    return boards[:5]


def _build_industry_strength_metrics(
    evaluation: KlineSelectionEvaluation,
    cause_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    metrics = dict(evaluation.metrics or {})
    industry_label = _safe_text((cause_payload or {}).get("industry") or metrics.get("industry_strength_label"))
    board_names = _normalize_board_names(metrics.get("industry_strength_board_names"))
    if not board_names:
        board_names = _split_theme_label_to_boards((cause_payload or {}).get("theme_label"))

    peer_count: Optional[float] = None
    raw_peer_count = metrics.get("industry_peer_count")
    if raw_peer_count is not None:
        try:
            peer_count = float(raw_peer_count)
        except (TypeError, ValueError):
            peer_count = None
    if peer_count is None and board_names:
        peer_count = float(len(board_names))

    board_payload = [{"name": name} for name in board_names]
    signal_factors = SharedSignalFactorsService.build_industry_strength_factors(
        {
            "industry": industry_label or None,
            "industry_peer_count": peer_count,
            "belong_boards": board_payload,
        },
        contextual_payload={
            "industry": industry_label or None,
            "industry_peer_count": peer_count,
            "belong_boards": board_payload,
        },
    )
    result = {key: signal_factors.get(key) for key in INDUSTRY_STRENGTH_METRIC_KEYS}
    if result.get("industry_strength_score") in (None, 0, 0.0) and industry_label and board_names:
        result["industry_strength_score"] = float(len(board_names))
    if not result.get("industry_strength_confirmed") and industry_label and board_names:
        result["industry_strength_confirmed"] = True
    if not result.get("industry_strength_label") and industry_label:
        result["industry_strength_label"] = industry_label
    if not result.get("industry_strength_board_names") and board_names:
        result["industry_strength_board_names"] = board_names
    return result


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

        industry_strength_metrics = _build_industry_strength_metrics(evaluation, cause_payload)
        merged_metrics = dict(evaluation.metrics or {})
        merged_metrics.update(industry_strength_metrics)
        evaluation.metrics = merged_metrics

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
            "industry_strength_confirmed": bool(industry_strength_metrics.get("industry_strength_confirmed")),
            "industry_strength_score": industry_strength_metrics.get("industry_strength_score"),
            "industry_strength_label": industry_strength_metrics.get("industry_strength_label"),
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
        "industry_strength_confirmed",
        "industry_strength_score",
        "industry_strength_label",
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
        f"- 因上市天数短路跳过: {run_result.skipped_listed_days_count}",
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
            "| 代码 | 名称 | 行业 | 最新 high | 收盘 | 图形标签 | 突破质量 | 行业强度 | 上涨原因 | 标签 | 海外主题 | 上次命中 | 历史次数 | 距上次(天) | 数据源 |",
            "|------|------|------|----------:|-----:|----------|----------:|----------:|----------|------|----------|----------|----------:|-----------:|--------|",
        ]
    )
    for row in selected_df.itertuples(index=False):
        lines.append(
            "| {code} | {name} | {industry} | {latest_high} | {close} | {chart_pattern_summary} | {breakout_quality_score} | {industry_strength_score} | {reason_summary} | {cause_tags} | {theme_label} | {latest_previous_hit_date} | {previous_hit_count} | {days_since_previous_hit} | {history_source} |".format(
                code=_markdown_cell(row.code),
                name=_markdown_cell(row.name),
                industry=_markdown_cell(getattr(row, "industry", "")),
                latest_high=f"{float(row.latest_high):.2f}" if pd.notna(row.latest_high) else "-",
                close=f"{float(row.close):.2f}" if pd.notna(row.close) else "-",
                chart_pattern_summary=_markdown_cell(getattr(row, "chart_pattern_summary", "")),
                breakout_quality_score=f"{float(getattr(row, 'breakout_quality_score', 0.0)):.2f}"
                if pd.notna(getattr(row, "breakout_quality_score", None))
                else "-",
                industry_strength_score=f"{float(getattr(row, 'industry_strength_score', 0.0)):.2f}"
                if pd.notna(getattr(row, "industry_strength_score", None))
                else "-",
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
        "breakout_quality_score",
        "breakout_contraction_ratio",
        "breakout_volume_ratio",
        "distance_to_new_high_pct",
        "minervini_template_score",
        "minervini_template_passed",
        "breakout_follow_through_score",
        "chart_pattern_label",
        "chart_pattern_score",
        "chart_pattern_summary",
        "base_breakout_score",
        "healthy_trend_score",
        "history_source",
        "reason_summary",
        "industry_logic",
        "news_logic",
        "technical_logic",
        "cause_tags",
        "theme_label",
        "industry_strength_confirmed",
        "industry_strength_score",
        "industry_strength_label",
        "latest_previous_hit_date",
        "previous_hit_count",
        "days_since_previous_hit",
        "revenue_yoy",
        "net_profit_yoy",
        "roe",
        "earnings_strategy_score",
        "earnings_strategy_gate_status",
        "earnings_quality_signal",
        "earnings_quality_score",
        "earnings_quality_verdict",
        "earnings_continuity_available",
        "earnings_continuity_score",
        "quality_overlay_available",
        "quality_overlay_score",
        "quality_overlay_label",
        "quality_overlay_source",
        "earnings_revenue_positive_quarter_streak",
        "earnings_profit_positive_quarter_streak",
        "earnings_roe_positive_quarter_streak",
        "earnings_financial_series_continuity_score",
        "earnings_financial_series_quarter_count",
        "earnings_reason_summary",
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
    if (
        checkpoint_path is not None
        and checkpoint_path.exists()
        and checkpoint_path.resolve() != exported_checkpoint_path.resolve()
    ):
        exported_checkpoint_path.write_text(
            checkpoint_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    logger.info("已写出 A 股样本列表: %s", universe_txt_path)
    logger.info("已写出百日新高结果 CSV: %s", csv_path)
    logger.info("已写出百日新高结果 TXT: %s", txt_path)
    logger.info("已写出百日新高结果 Markdown: %s", md_path)
    if checkpoint_path is not None and checkpoint_path.exists():
        if checkpoint_path.resolve() == exported_checkpoint_path.resolve():
            logger.info("checkpoint 已保存在输出目录: %s", exported_checkpoint_path)
        else:
            logger.info("已复制 checkpoint 到输出目录: %s", exported_checkpoint_path)


def _safe_count(value: Any) -> int:
    try:
        numeric = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, numeric)


def _apply_shared_scan_shell_stats(
    run_result: KlineSelectorRunResult,
    prepared_universe: Any,
    *,
    prefilter: KlineSelectorPrefilter | None,
) -> KlineSelectorRunResult:
    filter_stats = dict(getattr(prepared_universe, "filter_stats", {}) or {})
    prefilter_stats = dict(getattr(prepared_universe, "prefilter_stats", {}) or {})
    skipped_prefilter_count = sum(
        _safe_count(prefilter_stats.get(key))
        for key in ("removed_change_60d", "removed_turnover_rate", "removed_negative_change")
    )
    if prefilter is not None and prefilter.exclude_st:
        skipped_prefilter_count += _safe_count(filter_stats.get("removed_st"))

    run_result.skipped_prefilter_count += skipped_prefilter_count
    run_result.skipped_listed_days_count += _safe_count(prefilter_stats.get("removed_listed_days"))
    run_result.universe_size = _safe_count(
        getattr(prepared_universe, "sharded_universe_size", run_result.universe_size)
    )
    phase_metrics = dict(getattr(run_result, "phase_metrics", {}) or {})
    phase_metrics.update(
        {
            "shared_scan_shell_enabled": True,
            "scan_shell_base_universe_size": _safe_count(getattr(prepared_universe, "base_universe_size", 0)),
            "scan_shell_sharded_universe_size": _safe_count(
                getattr(prepared_universe, "sharded_universe_size", 0)
            ),
            "scan_shell_prepared_universe_size": _safe_count(
                getattr(prepared_universe, "prepared_universe_size", 0)
            ),
            "scan_shell_filter_stats": filter_stats,
            "scan_shell_prefilter_stats": prefilter_stats,
        }
    )
    run_result.phase_metrics = phase_metrics
    return run_result


def scan_hundred_day_high_candidates(
    *,
    criteria: KlineSelectorCriteria,
    snapshot_date: date,
    profile_name: str = DEFAULT_PROFILE_NAME,
    limit: int | None = None,
    max_workers: int = 1,
    shard_count: int = 1,
    shard_index: int = 0,
    prefilter: KlineSelectorPrefilter | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 50,
    resume: bool = False,
    on_evaluation: Any = None,
    service: KlineSelectorService | None = None,
    shared_scan_shell_enabled: bool = True,
) -> KlineSelectorRunResult:
    service = service or KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    universe = service.get_spot_enriched_a_share_universe(limit=limit, as_of_date=snapshot_date)
    use_shared_scan_shell = bool(shared_scan_shell_enabled) and hasattr(service, "prepare_scan_universe")
    if use_shared_scan_shell:
        prepared_universe = service.prepare_scan_universe(
            universe=universe,
            prefilter=prefilter,
            exclude_st=bool(prefilter.exclude_st) if prefilter is not None else False,
            shard_count=shard_count,
            shard_index=shard_index,
            cached_quote_universe=service._read_spot_universe_reference_cache()
            if hasattr(service, "_read_spot_universe_reference_cache")
            else None,
            hydrated_quote_cache_writer=service._write_spot_universe_reference_cache
            if hasattr(service, "_write_spot_universe_reference_cache")
            else None,
            quote_hydration_workers=max(1, int(max_workers)),
            as_of_date=snapshot_date,
        )
        run_result = service.scan_market(
            criteria=criteria,
            max_workers=max_workers,
            shard_count=1,
            shard_index=0,
            prefilter=None,
            checkpoint_path=checkpoint_path,
            checkpoint_every=checkpoint_every,
            resume=resume,
            on_evaluation=on_evaluation,
            universe=prepared_universe.prepared_universe,
            as_of_date=snapshot_date,
        )
        _apply_shared_scan_shell_stats(run_result, prepared_universe, prefilter=prefilter)
        logger.info(
            "hundred_day_high shared scan shell prepared: base=%s sharded=%s prepared=%s filter=%s prefilter=%s",
            run_result.phase_metrics.get("scan_shell_base_universe_size"),
            run_result.phase_metrics.get("scan_shell_sharded_universe_size"),
            run_result.phase_metrics.get("scan_shell_prepared_universe_size"),
            run_result.phase_metrics.get("scan_shell_filter_stats"),
            run_result.phase_metrics.get("scan_shell_prefilter_stats"),
        )
        return _enrich_run_result_with_breakout_quality(
            run_result,
            service=service,
            profile_name=profile_name,
            max_workers=max_workers,
        )

    run_result = service.scan_market(
        criteria=criteria,
        max_workers=max_workers,
        shard_count=shard_count,
        shard_index=shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=checkpoint_every,
        resume=resume,
        on_evaluation=on_evaluation,
        universe=universe,
        as_of_date=snapshot_date,
    )
    return _enrich_run_result_with_breakout_quality(
        run_result,
        service=service,
        profile_name=profile_name,
        max_workers=max_workers,
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    output_dir = resolve_output_dir(Path(args.output_dir), args.shard_count, args.shard_index)
    checkpoint_arg = Path(args.checkpoint_path) if args.checkpoint_path else None
    checkpoint_path = resolve_checkpoint_path(
        output_dir,
        checkpoint_arg,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
    )

    try:
        snapshot_date = parse_snapshot_date(args.snapshot_date)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    if args.skip_cause_analysis and args.cause_analysis_only:
        logger.error("--skip-cause-analysis cannot be used together with --cause-analysis-only")
        return 2

    profile_name, criteria, prefilter = resolve_profile_settings(args)
    runtime_signal_type = resolve_runtime_signal_type(
        signal_type=str(getattr(args, "signal_type", SIGNAL_TYPE) or SIGNAL_TYPE).strip() or SIGNAL_TYPE,
        profile_name=profile_name,
    )

    service = KlineSelectorService(manager_factory=KlineSelectorService.build_fast_a_share_manager)
    criteria_payload = build_criteria_payload(
        criteria,
        signal_type=runtime_signal_type,
        prefilter=prefilter,
        snapshot_date=snapshot_date,
        profile_name=profile_name,
    )
    db = DatabaseManager.get_instance()
    try:
        output_dir_lock = hold_output_dir_lock(output_dir)
        output_dir_lock.__enter__()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2
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
            output_dir_lock.__exit__(None, None, None)
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
        output_dir_lock.__exit__(None, None, None)
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
        if profile_requires_earnings_confirmation(profile_name):
            return
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
    run_result = scan_hundred_day_high_candidates(
        criteria=criteria,
        snapshot_date=snapshot_date,
        profile_name=profile_name,
        limit=args.limit,
        max_workers=args.max_workers,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        prefilter=prefilter,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
        on_evaluation=handle_incremental_snapshot,
        service=service,
        shared_scan_shell_enabled=not bool(args.disable_shared_scan_shell),
    )
    if profile_requires_earnings_confirmation(profile_name):
        logger.info(
            "百日新高业绩过滤: 开始按 balanced 业绩口径收口，pre_filter_selected=%s, snapshot_date=%s",
            len(run_result.selected),
            snapshot_date.isoformat(),
        )
        run_result = filter_selected_results_by_earnings_balanced(
            run_result,
            snapshot_date=snapshot_date,
            db=db,
        )
        logger.info(
            "百日新高业绩过滤完成: selected=%s, rejected=%s, snapshot_date=%s",
            len(run_result.selected),
            len(run_result.failed),
            snapshot_date.isoformat(),
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
        "skipped_by_prefilter=%s, skipped_by_listed_days=%s, selected=%s, snapshot_date=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        run_result.skipped_market_cap_count,
        run_result.skipped_prefilter_count,
        run_result.skipped_listed_days_count,
        len(run_result.selected),
        snapshot_date.isoformat(),
    )
    output_dir_lock.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
