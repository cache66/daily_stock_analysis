#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate persisted K-line signal snapshots with forward returns."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config
from src.core.backtest_engine import BacktestEngine, EvaluationConfig
from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager
from scripts.select_hundred_day_high_candidates import DEFAULT_PROFILE_NAME, PROFILE_PRESETS


logger = logging.getLogger("evaluate_signal_snapshot_performance")

DEFAULT_SIGNAL_TYPE = "hundred_day_high"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "data" / "signal_snapshot_performance_report.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "data" / "signal_snapshot_performance_report.md"
DEFAULT_WINDOWS = "1,3,5,10"
DEFAULT_SCORE_BUCKET_EDGES = "0,40,60,80,100"
DEFAULT_FILL_MAX_ATTEMPTS = 200
MissingDailyDataFiller = Callable[..., bool]
TradingDaysElapsedCounter = Callable[[date], int]
_DEFAULT_FILL_MANAGER: Optional[Any] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate persisted signal snapshots with forward-return summaries.",
    )
    parser.add_argument(
        "--signal-type",
        default=DEFAULT_SIGNAL_TYPE,
        help=f"Signal type to evaluate, default {DEFAULT_SIGNAL_TYPE}.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=sorted(PROFILE_PRESETS.keys()),
        help=f"Optional profile filter. Use {DEFAULT_PROFILE_NAME} / momentum_strict / breakout_loose, etc.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Optional inclusive snapshot start date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Optional inclusive snapshot end date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--code",
        default=None,
        help="Optional single stock code filter.",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="Optional comma-separated stock codes filter.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max snapshot rows to evaluate after DB filtering.",
    )
    parser.add_argument(
        "--windows",
        default=DEFAULT_WINDOWS,
        help=f"Comma-separated forward windows, default {DEFAULT_WINDOWS}.",
    )
    parser.add_argument(
        "--neutral-band-pct",
        type=float,
        default=None,
        help="Neutral band used for win/loss classification. Defaults to config value.",
    )
    parser.add_argument(
        "--output-json",
        default=str(DEFAULT_OUTPUT_JSON),
        help=f"Path to JSON report, default {DEFAULT_OUTPUT_JSON}.",
    )
    parser.add_argument(
        "--output-md",
        default=str(DEFAULT_OUTPUT_MD),
        help=f"Path to Markdown report, default {DEFAULT_OUTPUT_MD}.",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=5,
        help="Top/bottom detail rows to keep per window in the JSON report.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="Per-side slippage in bps. Round-trip cost = 2 * (slippage + fee).",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=0.0,
        help="Per-side commission/tax in bps. Round-trip cost = 2 * (slippage + fee).",
    )
    parser.add_argument(
        "--turnover-penalty-bps",
        type=float,
        default=0.0,
        help="Extra round-trip turnover penalty in bps (applied once per trade).",
    )
    parser.add_argument(
        "--score-buckets",
        default=DEFAULT_SCORE_BUCKET_EDGES,
        help=f"Comma-separated score bucket edges, default {DEFAULT_SCORE_BUCKET_EDGES}.",
    )
    parser.add_argument(
        "--fill-max-attempts",
        type=int,
        default=DEFAULT_FILL_MAX_ATTEMPTS,
        help=(
            "Max unique (code, signal_date) fill attempts when --fill-missing-daily-data is enabled. "
            "Use negative value for unlimited."
        ),
    )
    parser.set_defaults(fill_missing_daily_data=False)
    parser.add_argument(
        "--fill-missing-daily-data",
        dest="fill_missing_daily_data",
        action="store_true",
        help="Try to fill missing StockDaily bars via DataFetcherManager before marking insufficient_data.",
    )
    parser.add_argument(
        "--skip-fill-missing-daily-data",
        dest="fill_missing_daily_data",
        action="store_false",
        help="Disable missing StockDaily fill attempt.",
    )
    return parser.parse_args()


def parse_eval_windows(value: str) -> List[int]:
    windows: List[int] = []
    seen: set[int] = set()
    for raw_part in str(value or "").split(","):
        text = raw_part.strip()
        if not text:
            continue
        try:
            numeric = int(text)
        except ValueError as exc:
            raise ValueError(f"invalid window: {text}") from exc
        if numeric <= 0:
            raise ValueError(f"window must be > 0: {numeric}")
        if numeric not in seen:
            windows.append(numeric)
            seen.add(numeric)
    if not windows:
        raise ValueError("at least one eval window is required")
    return windows


def parse_score_bucket_edges(value: str) -> List[float]:
    edges: List[float] = []
    for raw_part in str(value or "").split(","):
        text = raw_part.strip()
        if not text:
            continue
        try:
            numeric = float(text)
        except ValueError as exc:
            raise ValueError(f"invalid score bucket edge: {text}") from exc
        edges.append(numeric)
    if len(edges) < 2:
        raise ValueError("at least two score bucket edges are required")
    deduped = sorted(set(edges))
    if len(deduped) < 2:
        raise ValueError("score bucket edges collapse to one value after dedupe")
    return deduped


def _safe_json_loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        payload = json.loads(str(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _snapshot_matches_profile(snapshot_row: Any, profile_name: Optional[str]) -> bool:
    if not profile_name:
        return True
    criteria_payload = _safe_json_loads(getattr(snapshot_row, "criteria_payload", None))
    snapshot_profile = str(criteria_payload.get("profile_name") or "").strip()
    return snapshot_profile == profile_name


def _is_run_summary_snapshot(snapshot_row: Any) -> bool:
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    return bool(metrics.get("is_run_summary"))


def _coerce_start_price(snapshot_row: Any, stock_repo: StockRepository) -> Optional[float]:
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    close_value = metrics.get("close")
    try:
        if close_value is not None:
            close_numeric = float(close_value)
            if close_numeric > 0:
                return close_numeric
    except (TypeError, ValueError):
        pass

    start_daily = stock_repo.get_start_daily(
        code=str(getattr(snapshot_row, "code", "") or "").strip(),
        analysis_date=getattr(snapshot_row, "signal_date", None),
    )
    if start_daily is None or start_daily.close is None:
        return None
    try:
        start_price = float(start_daily.close)
    except (TypeError, ValueError):
        return None
    return start_price if start_price > 0 else None


def _default_missing_daily_data_filler(
    *,
    code: str,
    analysis_date: date,
    eval_window_days: int,
    stock_repo: StockRepository,
) -> bool:
    code_text = str(code or "").strip()
    if not code_text:
        return False
    try:
        from data_provider.akshare_fetcher import AkshareFetcher
        from data_provider.baostock_fetcher import BaostockFetcher
        from data_provider.base import DataFetcherManager
        from data_provider.efinance_fetcher import EfinanceFetcher
        from data_provider.yfinance_fetcher import YfinanceFetcher
    except Exception as exc:
        logger.debug("missing daily-data filler unavailable: code=%s error=%s", code_text, exc)
        return False

    global _DEFAULT_FILL_MANAGER
    if _DEFAULT_FILL_MANAGER is None:
        # Avoid the default "Tushare first" chain in high-volume fill mode; keep
        # this path lighter and less likely to trigger provider throttle storms.
        _DEFAULT_FILL_MANAGER = DataFetcherManager(
            fetchers=[
                EfinanceFetcher(),
                AkshareFetcher(),
                BaostockFetcher(),
                YfinanceFetcher(),
            ]
        )

    try:
        manager = _DEFAULT_FILL_MANAGER
        fetch_days = max(30, int(eval_window_days) * 2)
        end_date = analysis_date + timedelta(days=fetch_days)
        history_df, source = manager.get_daily_data(
            stock_code=code_text,
            start_date=analysis_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            days=fetch_days,
        )
        if history_df is None or history_df.empty:
            return False
        stock_repo.save_dataframe(history_df, code=code_text, data_source=source or "daily_data_fill")
        return True
    except Exception as exc:
        logger.warning("fill missing daily data failed: code=%s date=%s error=%s", code_text, analysis_date, exc)
        return False


def _has_potential_forward_horizon(
    analysis_date: Optional[date],
    eval_window_days: int,
    *,
    trading_days_elapsed_counter: Optional[TradingDaysElapsedCounter] = None,
) -> bool:
    if analysis_date is None:
        return False
    if trading_days_elapsed_counter is not None:
        try:
            elapsed_trading_days = int(trading_days_elapsed_counter(analysis_date))
            return elapsed_trading_days >= max(1, int(eval_window_days))
        except Exception:
            # Fallback to natural-day heuristic when trading-day counter is unavailable.
            pass
    try:
        elapsed_days = (date.today() - analysis_date).days
    except Exception:
        return True
    return elapsed_days >= max(1, int(eval_window_days))


def _build_market_trading_days_elapsed_counter(stock_repo: StockRepository) -> TradingDaysElapsedCounter:
    today = date.today()
    cache: Dict[date, int] = {}

    def _counter(analysis_date: date) -> int:
        cached = cache.get(analysis_date)
        if cached is not None:
            return cached
        value = stock_repo.count_market_trading_days_between(
            start_date=analysis_date,
            end_date=today,
        )
        numeric = max(0, int(value))
        cache[analysis_date] = numeric
        return numeric

    return _counter


def _build_efficiency_summary(snapshots: Iterable[Any]) -> Dict[str, Any]:
    observed_rows = 0
    cache_hit_count = 0
    fundamental_refresh_count = 0
    quote_capital_refresh_count = 0
    cache_source_counts: Dict[str, int] = {}
    for row in snapshots:
        metrics = _safe_json_loads(getattr(row, "metrics_payload", None))
        cache_source = str(metrics.get("cache_source") or "").strip()
        capital_profile_cache_hit = metrics.get("capital_profile_cache_hit")
        capital_profile_refreshed_at = str(metrics.get("capital_profile_refreshed_at") or "").strip()
        if not any((cache_source, capital_profile_cache_hit is not None, capital_profile_refreshed_at)):
            continue
        observed_rows += 1
        if cache_source:
            cache_source_counts[cache_source] = cache_source_counts.get(cache_source, 0) + 1
        if cache_source in {"same_day_cache", "bundle_cache_overlay_refresh"}:
            cache_hit_count += 1
        if cache_source in {"fresh_bundle_fetch", "bundle_cache_rebuild"}:
            fundamental_refresh_count += 1
        if capital_profile_cache_hit is False and capital_profile_refreshed_at:
            quote_capital_refresh_count += 1
    return {
        "cache_observed_rows": observed_rows,
        "cache_hit_rows": cache_hit_count,
        "cache_hit_ratio_pct": round(cache_hit_count / observed_rows * 100.0, 2) if observed_rows else None,
        "fundamental_refresh_count": fundamental_refresh_count,
        "quote_capital_refresh_count": quote_capital_refresh_count,
        "cache_source_counts": cache_source_counts,
    }


def _extract_snapshot_score(snapshot_row: Any) -> Optional[float]:
    metrics = _safe_json_loads(getattr(snapshot_row, "metrics_payload", None))
    for key in (
        "overall_score",
        "hybrid_score",
        "earnings_strategy_score",
        "capital_profile_score",
        "breakout_score",
        "pullback_score",
    ):
        try:
            value = metrics.get(key)
            if value is None:
                continue
            numeric = float(value)
            return numeric
        except (TypeError, ValueError):
            continue
    return None


def _score_bucket_label(score: float, edges: List[float]) -> str:
    if score < edges[0]:
        return f"<{edges[0]:g}"
    for index in range(len(edges) - 1):
        left = edges[index]
        right = edges[index + 1]
        if left <= score < right:
            return f"{left:g}-{right:g}"
    return f">={edges[-1]:g}"


def _compute_trade_return_after_cost(stock_return_pct: Optional[float], *, total_trade_cost_bps: float) -> Optional[float]:
    if stock_return_pct is None:
        return None
    try:
        raw = float(stock_return_pct)
    except (TypeError, ValueError):
        return None
    return round(raw - float(total_trade_cost_bps) / 100.0, 4)


def _compute_equity_metrics(returns_pct: List[float]) -> Dict[str, Optional[float]]:
    if not returns_pct:
        return {
            "total_return_after_cost_pct": None,
            "max_drawdown_after_cost_pct": None,
            "calmar_ratio_after_cost": None,
        }
    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for value in returns_pct:
        equity *= 1.0 + float(value) / 100.0
        peak = max(peak, equity)
        if peak > 0:
            drawdown_pct = (peak - equity) / peak * 100.0
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct
    total_return_pct = (equity - 1.0) * 100.0
    calmar = (
        total_return_pct / max_drawdown_pct
        if max_drawdown_pct > 0
        else None
    )
    return {
        "total_return_after_cost_pct": round(total_return_pct, 4),
        "max_drawdown_after_cost_pct": round(max_drawdown_pct, 4),
        "calmar_ratio_after_cost": round(calmar, 4) if calmar is not None else None,
    }


def _build_score_bucket_summary(
    completed_rows: List[Dict[str, Any]],
    *,
    neutral_band_pct: float,
    score_bucket_edges: List[float],
) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in completed_rows:
        score_value = row.get("snapshot_score")
        if score_value is None:
            continue
        try:
            numeric_score = float(score_value)
        except (TypeError, ValueError):
            continue
        label = _score_bucket_label(numeric_score, score_bucket_edges)
        bucket = buckets.setdefault(
            label,
            {
                "bucket": label,
                "completed_count": 0,
                "win_count_after_cost": 0,
                "loss_count_after_cost": 0,
                "returns_raw": [],
                "returns_after_cost": [],
            },
        )
        bucket["completed_count"] += 1
        ret_raw = row.get("stock_return_pct")
        ret_net = row.get("stock_return_after_cost_pct")
        if ret_raw is not None:
            bucket["returns_raw"].append(float(ret_raw))
        if ret_net is not None:
            numeric_ret_net = float(ret_net)
            bucket["returns_after_cost"].append(numeric_ret_net)
            if numeric_ret_net > float(neutral_band_pct):
                bucket["win_count_after_cost"] += 1
            elif numeric_ret_net < -float(neutral_band_pct):
                bucket["loss_count_after_cost"] += 1

    ordered_labels = sorted(
        buckets.keys(),
        key=lambda label: (
            0 if "-" in label else (1 if label.startswith("<") else 2),
            label,
        ),
    )
    result: List[Dict[str, Any]] = []
    for label in ordered_labels:
        bucket = buckets[label]
        wins = int(bucket.get("win_count_after_cost", 0))
        losses = int(bucket.get("loss_count_after_cost", 0))
        denom = wins + losses
        returns_raw = [float(item) for item in bucket.get("returns_raw", [])]
        returns_after_cost = [float(item) for item in bucket.get("returns_after_cost", [])]
        result.append(
            {
                "bucket": label,
                "completed_count": int(bucket.get("completed_count", 0)),
                "win_rate_after_cost_pct": round(wins / denom * 100.0, 2) if denom > 0 else None,
                "avg_stock_return_pct": round(statistics.mean(returns_raw), 2) if returns_raw else None,
                "avg_stock_return_after_cost_pct": round(statistics.mean(returns_after_cost), 2)
                if returns_after_cost
                else None,
            }
        )
    return result


def evaluate_snapshot_row(
    snapshot_row: Any,
    *,
    stock_repo: StockRepository,
    eval_window_days: int,
    neutral_band_pct: float,
    total_trade_cost_bps: float,
    fill_missing_daily_data: bool = False,
    missing_daily_data_filler: Optional[MissingDailyDataFiller] = None,
    trading_days_elapsed_counter: Optional[TradingDaysElapsedCounter] = None,
) -> Dict[str, Any]:
    code = str(getattr(snapshot_row, "code", "") or "").strip()
    name = str(getattr(snapshot_row, "name", "") or "").strip()
    signal_date = getattr(snapshot_row, "signal_date", None)
    filler = missing_daily_data_filler if fill_missing_daily_data else None
    horizon_possible = _has_potential_forward_horizon(
        signal_date,
        int(eval_window_days),
        trading_days_elapsed_counter=trading_days_elapsed_counter,
    )
    start_price = _coerce_start_price(snapshot_row, stock_repo)
    if start_price is None and signal_date is not None and filler is not None and code:
        try:
            filler(
                code=code,
                analysis_date=signal_date,
                eval_window_days=int(eval_window_days),
                stock_repo=stock_repo,
            )
        except Exception as exc:
            logger.warning("missing daily-data filler callback failed: code=%s date=%s error=%s", code, signal_date, exc)
        start_price = _coerce_start_price(snapshot_row, stock_repo)

    if start_price is None or signal_date is None:
        insufficient_reason = "missing_start_price" if signal_date is not None else "missing_signal_date"
        return {
            "code": code,
            "name": name,
            "signal_date": signal_date.isoformat() if signal_date else None,
            "snapshot_score": _extract_snapshot_score(snapshot_row),
            "eval_window_days": int(eval_window_days),
            "eval_status": "insufficient_data",
            "start_price": start_price,
            "end_close": None,
            "stock_return_pct": None,
            "stock_return_after_cost_pct": None,
            "simulated_return_pct": None,
            "position_recommendation": None,
            "outcome": None,
            "direction_correct": None,
            "hit_stop_loss": None,
            "hit_take_profit": None,
            "first_hit": None,
            "first_hit_trading_days": None,
            "operation_advice": None,
            "max_runup_pct": None,
            "worst_drawdown_pct": None,
            "max_high": None,
            "min_low": None,
            "insufficient_reason": insufficient_reason,
        }

    forward_bars = stock_repo.get_forward_bars(
        code=code,
        analysis_date=signal_date,
        eval_window_days=int(eval_window_days),
    )
    if (
        len(forward_bars) < int(eval_window_days)
        and filler is not None
        and code
        and horizon_possible
    ):
        try:
            filler(
                code=code,
                analysis_date=signal_date,
                eval_window_days=int(eval_window_days),
                stock_repo=stock_repo,
            )
        except Exception as exc:
            logger.warning("missing daily-data filler callback failed: code=%s date=%s error=%s", code, signal_date, exc)
        forward_bars = stock_repo.get_forward_bars(
            code=code,
            analysis_date=signal_date,
            eval_window_days=int(eval_window_days),
        )

    evaluation = BacktestEngine.evaluate_single(
        operation_advice="buy",
        analysis_date=signal_date,
        start_price=start_price,
        forward_bars=forward_bars,
        stop_loss=None,
        take_profit=None,
        config=EvaluationConfig(
            eval_window_days=int(eval_window_days),
            neutral_band_pct=float(neutral_band_pct),
            engine_version="signal_snapshot_v1",
        ),
    )
    max_high = evaluation.get("max_high")
    min_low = evaluation.get("min_low")
    max_runup_pct = (
        round((float(max_high) - start_price) / start_price * 100, 2)
        if max_high is not None
        else None
    )
    worst_drawdown_pct = (
        round((float(min_low) - start_price) / start_price * 100, 2)
        if min_low is not None
        else None
    )
    insufficient_reason: Optional[str] = None
    if evaluation.get("eval_status") != "completed":
        if len(forward_bars) < int(eval_window_days):
            insufficient_reason = "missing_forward_bars" if horizon_possible else "insufficient_forward_horizon"
        else:
            insufficient_reason = "insufficient_data"

    return {
        "code": code,
        "name": name,
        "signal_date": signal_date.isoformat(),
        "snapshot_score": _extract_snapshot_score(snapshot_row),
        "eval_window_days": int(eval_window_days),
        "eval_status": evaluation.get("eval_status"),
        "start_price": start_price,
        "end_close": evaluation.get("end_close"),
        "stock_return_pct": evaluation.get("stock_return_pct"),
        "stock_return_after_cost_pct": _compute_trade_return_after_cost(
            evaluation.get("stock_return_pct"),
            total_trade_cost_bps=total_trade_cost_bps,
        ),
        "simulated_return_pct": evaluation.get("simulated_return_pct"),
        "position_recommendation": evaluation.get("position_recommendation"),
        "outcome": evaluation.get("outcome"),
        "direction_correct": evaluation.get("direction_correct"),
        "hit_stop_loss": evaluation.get("hit_stop_loss"),
        "hit_take_profit": evaluation.get("hit_take_profit"),
        "first_hit": evaluation.get("first_hit"),
        "first_hit_trading_days": evaluation.get("first_hit_trading_days"),
        "operation_advice": evaluation.get("operation_advice"),
        "max_runup_pct": max_runup_pct,
        "worst_drawdown_pct": worst_drawdown_pct,
        "max_high": max_high,
        "min_low": min_low,
        "insufficient_reason": insufficient_reason,
    }


def summarize_window(
    evaluations: Iterable[Dict[str, Any]],
    *,
    signal_type: str,
    eval_window_days: int,
    detail_limit: int,
    neutral_band_pct: float,
    score_bucket_edges: List[float],
) -> Dict[str, Any]:
    rows = list(evaluations)
    summary = BacktestEngine.compute_summary(
        results=[SimpleNamespace(**row) for row in rows],
        scope="signal_snapshot",
        code=signal_type,
        eval_window_days=int(eval_window_days),
        engine_version="signal_snapshot_v1",
    )
    completed = [row for row in rows if row.get("eval_status") == "completed"]
    insufficient_reason_counts: Dict[str, int] = {}
    for row in rows:
        if row.get("eval_status") == "completed":
            continue
        reason = str(row.get("insufficient_reason") or "insufficient_data")
        insufficient_reason_counts[reason] = insufficient_reason_counts.get(reason, 0) + 1
    returns = [
        float(row["stock_return_pct"])
        for row in completed
        if row.get("stock_return_pct") is not None
    ]
    runups = [
        float(row["max_runup_pct"])
        for row in completed
        if row.get("max_runup_pct") is not None
    ]
    drawdowns = [
        float(row["worst_drawdown_pct"])
        for row in completed
        if row.get("worst_drawdown_pct") is not None
    ]
    returns_after_cost = [
        float(row["stock_return_after_cost_pct"])
        for row in completed
        if row.get("stock_return_after_cost_pct") is not None
    ]
    wins_after_cost = [
        value
        for value in returns_after_cost
        if value > float(neutral_band_pct)
    ]
    losses_after_cost = [
        value
        for value in returns_after_cost
        if value < -float(neutral_band_pct)
    ]
    ordered_completed = sorted(
        completed,
        key=lambda item: (str(item.get("signal_date") or ""), str(item.get("code") or "")),
    )
    equity_metrics = _compute_equity_metrics(
        [
            float(item.get("stock_return_after_cost_pct"))
            for item in ordered_completed
            if item.get("stock_return_after_cost_pct") is not None
        ]
    )
    sorted_by_return = sorted(
        completed,
        key=lambda item: float(item.get("stock_return_pct") or float("-inf")),
        reverse=True,
    )
    return {
        **summary,
        "median_stock_return_pct": round(statistics.median(returns), 2) if returns else None,
        "avg_stock_return_after_cost_pct": round(statistics.mean(returns_after_cost), 2)
        if returns_after_cost
        else None,
        "median_stock_return_after_cost_pct": round(statistics.median(returns_after_cost), 2)
        if returns_after_cost
        else None,
        "win_rate_after_cost_pct": (
            round(len(wins_after_cost) / (len(wins_after_cost) + len(losses_after_cost)) * 100.0, 2)
            if (len(wins_after_cost) + len(losses_after_cost)) > 0
            else None
        ),
        "profit_factor_after_cost": (
            round(sum(wins_after_cost) / abs(sum(losses_after_cost)), 4)
            if wins_after_cost and losses_after_cost and abs(sum(losses_after_cost)) > 0
            else None
        ),
        "avg_max_runup_pct": round(statistics.mean(runups), 2) if runups else None,
        "avg_worst_drawdown_pct": round(statistics.mean(drawdowns), 2) if drawdowns else None,
        **equity_metrics,
        "score_bucket_summary": _build_score_bucket_summary(
            ordered_completed,
            neutral_band_pct=neutral_band_pct,
            score_bucket_edges=score_bucket_edges,
        ),
        "insufficient_reason_counts": insufficient_reason_counts,
        "best_cases": sorted_by_return[: max(detail_limit, 0)],
        "worst_cases": list(reversed(sorted_by_return[-max(detail_limit, 0) :])) if detail_limit > 0 else [],
    }


def build_markdown_report(report: Dict[str, Any]) -> str:
    filters = report.get("filters") or {}
    efficiency = report.get("efficiency_summary") or {}
    fill_stats = report.get("fill_stats") or {}
    trade_cost_model = report.get("trade_cost_model") or {}
    lines: List[str] = [
        "# Signal Snapshot Performance Report",
        "",
        f"- Signal Type: `{filters.get('signal_type') or '--'}`",
        f"- Profile: `{filters.get('profile_name') or '--'}`",
        f"- Start Date: `{filters.get('start_date') or '--'}`",
        f"- End Date: `{filters.get('end_date') or '--'}`",
        f"- Code: `{filters.get('code') or '--'}`",
        f"- Codes: `{','.join(filters.get('codes') or []) if filters.get('codes') else '--'}`",
        f"- Snapshot Rows: `{report.get('snapshot_count', 0)}`",
        f"- Neutral Band Pct: `{report.get('neutral_band_pct')}`",
        f"- Trade Cost Model (bps): `slippage={trade_cost_model.get('slippage_bps', 0.0)}, fee={trade_cost_model.get('fee_bps', 0.0)} turnover_penalty={trade_cost_model.get('turnover_penalty_bps', 0.0)} total={trade_cost_model.get('total_trade_cost_bps', 0.0)}`",
        f"- Fill Attempts: `{fill_stats.get('fill_attempted_count', 0)}` / `{fill_stats.get('fill_max_attempts', '--')}`",
        "",
        "## Window Summary",
        "",
        "| window | total | completed | insufficient | win_rate_pct | win_rate_after_cost_pct | avg_return_pct | avg_return_after_cost_pct | median_return_pct | median_return_after_cost_pct | max_drawdown_after_cost_pct | calmar_after_cost |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("window_summaries") or []:
        lines.append(
            "| {window} | {total} | {completed} | {insufficient} | {win_rate} | {win_rate_after_cost} | {avg_return} | {avg_return_after_cost} | {median_return} | {median_return_after_cost} | {max_dd_after_cost} | {calmar_after_cost} |".format(
                window=item.get("eval_window_days"),
                total=item.get("total_evaluations", 0),
                completed=item.get("completed_count", 0),
                insufficient=item.get("insufficient_count", 0),
                win_rate=item.get("win_rate_pct") if item.get("win_rate_pct") is not None else "--",
                win_rate_after_cost=item.get("win_rate_after_cost_pct")
                if item.get("win_rate_after_cost_pct") is not None
                else "--",
                avg_return=item.get("avg_stock_return_pct") if item.get("avg_stock_return_pct") is not None else "--",
                avg_return_after_cost=item.get("avg_stock_return_after_cost_pct")
                if item.get("avg_stock_return_after_cost_pct") is not None
                else "--",
                median_return=item.get("median_stock_return_pct") if item.get("median_stock_return_pct") is not None else "--",
                median_return_after_cost=item.get("median_stock_return_after_cost_pct")
                if item.get("median_stock_return_after_cost_pct") is not None
                else "--",
                max_dd_after_cost=item.get("max_drawdown_after_cost_pct")
                if item.get("max_drawdown_after_cost_pct") is not None
                else "--",
                calmar_after_cost=item.get("calmar_ratio_after_cost")
                if item.get("calmar_ratio_after_cost") is not None
                else "--",
            )
        )

    if efficiency.get("cache_observed_rows"):
        lines.extend(
            [
                "",
                "## Efficiency Summary",
                "",
                f"- Cache Observed Rows: `{efficiency.get('cache_observed_rows')}`",
                f"- Cache Hit Ratio Pct: `{efficiency.get('cache_hit_ratio_pct')}`",
                f"- Fundamental Refresh Count: `{efficiency.get('fundamental_refresh_count')}`",
                f"- Quote/Capital Refresh Count: `{efficiency.get('quote_capital_refresh_count')}`",
            ]
        )

    top_window = next(iter(report.get("window_summaries") or []), None)
    if top_window and top_window.get("best_cases"):
        lines.extend(
            [
                "",
                f"## Best Cases ({top_window.get('eval_window_days')}D)",
                "",
                "| signal_date | code | name | return_pct | max_runup_pct | worst_drawdown_pct |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        for item in top_window.get("best_cases") or []:
            lines.append(
                "| {signal_date} | {code} | {name} | {ret} | {runup} | {drawdown} |".format(
                    signal_date=item.get("signal_date") or "--",
                    code=item.get("code") or "--",
                    name=item.get("name") or "--",
                    ret=item.get("stock_return_pct") if item.get("stock_return_pct") is not None else "--",
                    runup=item.get("max_runup_pct") if item.get("max_runup_pct") is not None else "--",
                    drawdown=item.get("worst_drawdown_pct") if item.get("worst_drawdown_pct") is not None else "--",
                )
            )
    if top_window and top_window.get("score_bucket_summary"):
        lines.extend(
            [
                "",
                f"## Score Buckets ({top_window.get('eval_window_days')}D)",
                "",
                "| bucket | completed | win_rate_after_cost_pct | avg_return_pct | avg_return_after_cost_pct |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in top_window.get("score_bucket_summary") or []:
            lines.append(
                "| {bucket} | {completed} | {win_rate} | {avg_return} | {avg_return_after_cost} |".format(
                    bucket=item.get("bucket") or "--",
                    completed=item.get("completed_count", 0),
                    win_rate=item.get("win_rate_after_cost_pct")
                    if item.get("win_rate_after_cost_pct") is not None
                    else "--",
                    avg_return=item.get("avg_stock_return_pct")
                    if item.get("avg_stock_return_pct") is not None
                    else "--",
                    avg_return_after_cost=item.get("avg_stock_return_after_cost_pct")
                    if item.get("avg_stock_return_after_cost_pct") is not None
                    else "--",
                )
            )
    return "\n".join(lines) + "\n"


def build_report(
    *,
    db: DatabaseManager,
    signal_type: str,
    profile_name: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    code: Optional[str],
    codes: Optional[List[str]],
    limit: Optional[int],
    eval_windows: List[int],
    neutral_band_pct: float,
    detail_limit: int,
    slippage_bps: float = 0.0,
    fee_bps: float = 0.0,
    turnover_penalty_bps: float = 0.0,
    score_bucket_edges: Optional[List[float]] = None,
    fill_missing_daily_data: bool = False,
    missing_daily_data_filler: Optional[MissingDailyDataFiller] = None,
    fill_max_attempts: Optional[int] = DEFAULT_FILL_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    bucket_edges = score_bucket_edges or parse_score_bucket_edges(DEFAULT_SCORE_BUCKET_EDGES)
    total_trade_cost_bps = max(0.0, float(turnover_penalty_bps)) + max(0.0, 2.0 * (float(slippage_bps) + float(fee_bps)))

    snapshots = db.get_signal_snapshots(
        signal_type=signal_type,
        start_date=start_date,
        end_date=end_date,
        code=code,
        codes=codes,
        limit=None if profile_name else limit,
    )
    snapshots = [row for row in snapshots if _snapshot_matches_profile(row, profile_name)]
    snapshots = [row for row in snapshots if not _is_run_summary_snapshot(row)]
    if limit is not None and limit > 0:
        snapshots = snapshots[:limit]
    stock_repo = StockRepository(db)
    trading_days_elapsed_counter = _build_market_trading_days_elapsed_counter(stock_repo)
    fill_attempted: set[tuple[str, str]] = set()
    active_filler = missing_daily_data_filler or _default_missing_daily_data_filler
    max_eval_window = max([int(item) for item in eval_windows], default=1)
    normalized_fill_max_attempts: Optional[int]
    if fill_max_attempts is None:
        normalized_fill_max_attempts = None
    else:
        try:
            numeric_limit = int(fill_max_attempts)
        except (TypeError, ValueError):
            numeric_limit = DEFAULT_FILL_MAX_ATTEMPTS
        normalized_fill_max_attempts = None if numeric_limit < 0 else numeric_limit

    def _fill_once(*, code: str, analysis_date: date, eval_window_days: int, stock_repo: StockRepository) -> bool:
        attempt_key = (str(code or "").strip(), analysis_date.isoformat())
        if attempt_key in fill_attempted:
            return False
        if (
            normalized_fill_max_attempts is not None
            and len(fill_attempted) >= normalized_fill_max_attempts
        ):
            logger.info(
                "fill attempt budget exhausted: max_attempts=%s skip code=%s date=%s",
                normalized_fill_max_attempts,
                attempt_key[0],
                attempt_key[1],
            )
            return False
        fill_attempted.add(attempt_key)
        return bool(
            active_filler(
                code=code,
                analysis_date=analysis_date,
                eval_window_days=max_eval_window,
                stock_repo=stock_repo,
            )
        )

    efficiency_summary = _build_efficiency_summary(snapshots)
    window_summaries: List[Dict[str, Any]] = []
    for window in eval_windows:
        evaluations = [
            evaluate_snapshot_row(
                row,
                stock_repo=stock_repo,
                eval_window_days=window,
                neutral_band_pct=neutral_band_pct,
                total_trade_cost_bps=total_trade_cost_bps,
                fill_missing_daily_data=bool(fill_missing_daily_data),
                missing_daily_data_filler=_fill_once,
                trading_days_elapsed_counter=trading_days_elapsed_counter,
            )
            for row in snapshots
        ]
        window_summaries.append(
            summarize_window(
                evaluations,
                signal_type=signal_type,
                eval_window_days=window,
                detail_limit=detail_limit,
                neutral_band_pct=neutral_band_pct,
                score_bucket_edges=bucket_edges,
            )
        )

    return {
        "filters": {
            "signal_type": signal_type,
            "profile_name": profile_name,
            "start_date": start_date,
            "end_date": end_date,
            "code": code,
            "codes": codes or [],
            "limit": limit,
            "windows": eval_windows,
            "fill_missing_daily_data": bool(fill_missing_daily_data),
            "fill_max_attempts": normalized_fill_max_attempts,
        },
        "neutral_band_pct": neutral_band_pct,
        "trade_cost_model": {
            "slippage_bps": float(slippage_bps),
            "fee_bps": float(fee_bps),
            "turnover_penalty_bps": float(turnover_penalty_bps),
            "total_trade_cost_bps": float(total_trade_cost_bps),
        },
        "score_bucket_edges": bucket_edges,
        "snapshot_count": len(snapshots),
        "efficiency_summary": efficiency_summary,
        "fill_stats": {
            "fill_attempted_count": len(fill_attempted),
            "fill_max_attempts": normalized_fill_max_attempts,
        },
        "window_summaries": window_summaries,
    }


def main() -> int:
    args = parse_args()
    windows = parse_eval_windows(args.windows)
    score_bucket_edges = parse_score_bucket_edges(args.score_buckets)
    config = get_config()
    neutral_band_pct = (
        float(args.neutral_band_pct)
        if args.neutral_band_pct is not None
        else float(getattr(config, "backtest_neutral_band_pct", 2.0))
    )
    codes = [
        item.strip()
        for item in str(args.codes or "").split(",")
        if item.strip()
    ] or None

    db = DatabaseManager.get_instance()
    report = build_report(
        db=db,
        signal_type=args.signal_type,
        profile_name=args.profile,
        start_date=args.start_date,
        end_date=args.end_date,
        code=args.code,
        codes=codes,
        limit=args.limit,
        eval_windows=windows,
        neutral_band_pct=neutral_band_pct,
        detail_limit=max(args.detail_limit, 0),
        slippage_bps=max(0.0, float(args.slippage_bps)),
        fee_bps=max(0.0, float(args.fee_bps)),
        turnover_penalty_bps=max(0.0, float(args.turnover_penalty_bps)),
        score_bucket_edges=score_bucket_edges,
        fill_missing_daily_data=bool(args.fill_missing_daily_data),
        fill_max_attempts=args.fill_max_attempts,
    )

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(build_markdown_report(report), encoding="utf-8")

    print(f"snapshot_count={report['snapshot_count']}")
    for item in report["window_summaries"]:
        print(
            "window={window} completed={completed} win_rate_pct={win_rate} "
            "win_rate_after_cost_pct={win_rate_after_cost} avg_return_pct={avg_return} "
            "avg_return_after_cost_pct={avg_return_after_cost}".format(
                window=item.get("eval_window_days"),
                completed=item.get("completed_count", 0),
                win_rate=item.get("win_rate_pct") if item.get("win_rate_pct") is not None else "--",
                avg_return=item.get("avg_stock_return_pct") if item.get("avg_stock_return_pct") is not None else "--",
                win_rate_after_cost=item.get("win_rate_after_cost_pct")
                if item.get("win_rate_after_cost_pct") is not None
                else "--",
                avg_return_after_cost=item.get("avg_stock_return_after_cost_pct")
                if item.get("avg_stock_return_after_cost_pct") is not None
                else "--",
            )
        )
    print(f"json_report={output_json}")
    print(f"markdown_report={output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
