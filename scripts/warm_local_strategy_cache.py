#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warm local K-line and earnings caches for daily strategy runs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_earnings_surprise_candidates import (
    build_recent_earnings_event_catalog,
    resolve_current_report_period,
)
from src.core.trading_calendar import get_effective_trading_date
from src.services.kline_selector_service import KlineSelectorService, resolve_local_strategy_universe_filters


logger = logging.getLogger("warm_local_strategy_cache")

DEFAULT_EVENT_CATALOG_MAX_AGE_MINUTES = 15


def _column_values(df: pd.DataFrame, column: str) -> list[Any]:
    if df is None or df.empty or column not in df.columns:
        return []
    return df[column].tolist()


def _parse_snapshot_date(value: str) -> date:
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="预热本地策略缓存（K 线历史 + 业绩上下文），默认低并发顺序运行。",
    )
    parser.add_argument(
        "--snapshot-date",
        default=date.today().isoformat(),
        help="交易日日期，格式 YYYY-MM-DD，默认今天。",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=0,
        help="预热前 N 只样本的 K 线缓存；<=0 表示覆盖全部过滤后样本，默认全部。",
    )
    parser.add_argument(
        "--earnings-top-n",
        type=int,
        default=300,
        help="额外预热前 N 只样本的业绩缓存，默认 300。",
    )
    parser.add_argument(
        "--kline-days",
        type=int,
        default=160,
        help="每只股票预热多少个交易日的历史 K 线，默认 160。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅拉取前 N 只股票池样本，便于试跑。",
    )
    parser.add_argument(
        "--allowed-prefixes",
        default="",
        help="可选股票代码前缀白名单；默认不额外限缩，由 ST/科创/北交所过滤控制。",
    )
    parser.set_defaults(exclude_st=True)
    parser.add_argument(
        "--exclude-st",
        dest="exclude_st",
        action="store_true",
        help="排除 ST 股票（默认开启）。",
    )
    parser.add_argument(
        "--include-st",
        dest="exclude_st",
        action="store_false",
        help="允许 ST 股票进入样本池。",
    )
    parser.add_argument(
        "--exclude-cyb",
        action="store_true",
        help="额外排除创业板（300/301）。默认不排除。",
    )
    parser.add_argument(
        "--report-period-hint",
        default="",
        help="业绩缓存复用的报告期提示，如 20260331 或 2026Q1；默认按 snapshot-date 自动推断。",
    )
    parser.add_argument(
        "--earnings-budget-seconds",
        type=float,
        default=1.2,
        help="单只股票业绩上下文预算秒数，默认 1.2。",
    )
    parser.add_argument(
        "--event-catalog-max-age-minutes",
        type=int,
        default=DEFAULT_EVENT_CATALOG_MAX_AGE_MINUTES,
        help="当 snapshot-date 是当前有效交易日时，同日业绩事件目录缓存最大允许年龄（分钟），默认 15。",
    )
    parser.add_argument(
        "--force-refresh-event-catalog",
        action="store_true",
        help="强制刷新当前报告期业绩事件目录，再开始业绩缓存预热。",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data" / "runtime" / "cache_prewarm"),
        help="输出根目录，默认 data/runtime/cache_prewarm。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile_rank(series: pd.Series, *, neutral_for_missing: float = 0.5) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    ranked = numeric.rank(method="average", pct=True)
    return ranked.fillna(float(neutral_for_missing))


def _normalize_prefixes(raw_prefixes: str) -> tuple[str, ...]:
    prefixes = [
        str(item or "").strip()
        for item in str(raw_prefixes or "").split(",")
        if str(item or "").strip()
    ]
    return tuple(dict.fromkeys(prefixes))


def _build_ranked_universe(
    universe: pd.DataFrame,
    *,
    allowed_prefixes: tuple[str, ...],
) -> pd.DataFrame:
    ranked = universe.copy()
    ranked["code"] = ranked.get("code", pd.Series(dtype="object")).astype(str).str.strip()
    ranked = ranked[ranked["code"].str.len() == 6].copy()
    if allowed_prefixes:
        ranked = ranked[ranked["code"].str.startswith(allowed_prefixes)].copy()
    ranked = ranked.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

    ranked["turnover_rate"] = pd.to_numeric(ranked.get("turnover_rate"), errors="coerce")
    ranked["total_mv"] = pd.to_numeric(ranked.get("total_mv"), errors="coerce")
    ranked["pct_change"] = pd.to_numeric(ranked.get("pct_change"), errors="coerce")
    ranked["change_pct_60d"] = pd.to_numeric(ranked.get("change_pct_60d"), errors="coerce")
    ranked["turnover_amount_proxy"] = (ranked["total_mv"] * ranked["turnover_rate"] / 100.0).fillna(0.0)

    ranked["turnover_proxy_pct_rank"] = _percentile_rank(ranked["turnover_amount_proxy"])
    ranked["change_60d_pct_rank"] = _percentile_rank(ranked["change_pct_60d"])
    ranked["today_change_pct_rank"] = _percentile_rank(ranked["pct_change"])
    ranked["warm_score"] = (
        ranked["turnover_proxy_pct_rank"] * 55.0
        + ranked["change_60d_pct_rank"] * 35.0
        + ranked["today_change_pct_rank"] * 10.0
    ).round(4)

    ranked = ranked.sort_values(
        by=["warm_score", "turnover_amount_proxy", "change_pct_60d", "pct_change", "code"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    ranked["warm_rank"] = range(1, len(ranked) + 1)
    return ranked


def _select_target_rows(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    normalized_top_n = int(top_n or 0)
    if normalized_top_n <= 0:
        return frame.reset_index(drop=True)
    return frame.head(normalized_top_n).reset_index(drop=True)


def _warm_kline_cache(
    manager: Any,
    ranked_universe: pd.DataFrame,
    *,
    snapshot_date: date,
    top_n: int,
    kline_days: int,
) -> pd.DataFrame:
    records = []
    top_rows = _select_target_rows(ranked_universe, top_n)
    snapshot_date_text = snapshot_date.isoformat()
    total = len(top_rows)
    for idx, row in enumerate(top_rows.itertuples(index=False), start=1):
        code = str(getattr(row, "code", "") or "").strip()
        name = str(getattr(row, "name", "") or "").strip()
        try:
            history, history_source = manager.get_daily_data(
                code,
                end_date=snapshot_date_text,
                days=max(30, int(kline_days)),
                force_refresh=False,
            )
            latest_date = None
            if history is not None and not history.empty and "date" in history.columns:
                latest_date = pd.to_datetime(history["date"], errors="coerce").max()
            records.append(
                {
                    "code": code,
                    "name": name,
                    "warm_rank": getattr(row, "warm_rank", idx),
                    "history_rows": int(len(history)) if history is not None else 0,
                    "history_source": str(history_source or ""),
                    "latest_history_date": latest_date.strftime("%Y-%m-%d")
                    if isinstance(latest_date, pd.Timestamp) and not pd.isna(latest_date)
                    else "",
                    "status": "ok",
                }
            )
        except Exception as exc:
            records.append(
                {
                    "code": code,
                    "name": name,
                    "warm_rank": getattr(row, "warm_rank", idx),
                    "history_rows": 0,
                    "history_source": "",
                    "latest_history_date": "",
                    "status": "failed",
                    "error": str(exc),
                }
            )
        if idx == total or idx % 100 == 0:
            logger.info("K 线缓存预热进度: %s/%s", idx, total)
    return pd.DataFrame(records)


def _warm_earnings_cache(
    manager: Any,
    ranked_universe: pd.DataFrame,
    *,
    report_period_hint: str,
    recent_event_catalog: Optional[Dict[str, Dict[str, Any]]],
    top_n: int,
    budget_seconds: float,
) -> pd.DataFrame:
    records = []
    top_rows = _select_target_rows(ranked_universe, top_n)
    total = len(top_rows)
    for idx, row in enumerate(top_rows.itertuples(index=False), start=1):
        code = str(getattr(row, "code", "") or "").strip()
        name = str(getattr(row, "name", "") or "").strip()
        recent_event_payload = (
            recent_event_catalog.get(code)
            if isinstance(recent_event_catalog, dict)
            else None
        )
        announcement_date_hint = ""
        if isinstance(recent_event_payload, dict):
            for field_name in (
                "report_announcement_date",
                "quick_report_announcement_date",
                "forecast_announcement_date",
            ):
                text = str(recent_event_payload.get(field_name) or "").strip()
                if text and text > announcement_date_hint:
                    announcement_date_hint = text
        try:
            context = manager.get_earnings_fundamental_context(
                code,
                budget_seconds=max(0.1, float(budget_seconds)),
                enabled_blocks=("financial", "forecast", "quick_report"),
                report_period_hint=report_period_hint,
                announcement_date_hint=announcement_date_hint or None,
            )
            earnings_block = context.get("earnings") if isinstance(context, dict) else {}
            earnings_data = earnings_block.get("data", {}) if isinstance(earnings_block, dict) else {}
            financial_report = earnings_data.get("financial_report", {}) if isinstance(earnings_data, dict) else {}
            records.append(
                {
                    "code": code,
                    "name": name,
                    "warm_rank": getattr(row, "warm_rank", idx),
                    "status": str((context or {}).get("status") or ""),
                    "cache_hit": bool((context or {}).get("cache_hit")),
                    "cache_source": str((context or {}).get("cache_source") or ""),
                    "report_date": str(financial_report.get("report_date") or ""),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "code": code,
                    "name": name,
                    "warm_rank": getattr(row, "warm_rank", idx),
                    "status": "failed",
                    "cache_hit": False,
                    "cache_source": "",
                    "report_date": "",
                    "error": str(exc),
                }
            )
        if idx == total or idx % 50 == 0:
            logger.info("业绩缓存预热进度: %s/%s", idx, total)
    return pd.DataFrame(records)


def _build_summary(
    ranked_universe: pd.DataFrame,
    kline_warm_df: pd.DataFrame,
    earnings_warm_df: pd.DataFrame,
    *,
    snapshot_date: date,
    report_period_hint: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_date": snapshot_date.isoformat(),
        "report_period_hint": report_period_hint,
        "top_n": int(args.top_n),
        "earnings_top_n": int(args.earnings_top_n),
        "effective_kline_target_count": int(len(kline_warm_df)),
        "effective_earnings_target_count": int(len(earnings_warm_df)),
        "kline_days": int(args.kline_days),
        "event_catalog_max_age_minutes": int(args.event_catalog_max_age_minutes),
        "exclude_st": bool(args.exclude_st),
        "exclude_cyb": bool(args.exclude_cyb),
        "allowed_prefixes": list(_normalize_prefixes(args.allowed_prefixes)),
        "filtered_universe_size": int(len(ranked_universe)),
        "kline_status_counts": dict(Counter(_column_values(kline_warm_df, "status"))),
        "kline_source_counts": dict(Counter(_column_values(kline_warm_df, "history_source"))),
        "earnings_status_counts": dict(Counter(_column_values(earnings_warm_df, "status"))),
        "earnings_cache_source_counts": dict(Counter(_column_values(earnings_warm_df, "cache_source"))),
    }


def _resolve_event_catalog_refresh_policy(
    *,
    snapshot_date: date,
    force_refresh: bool,
    max_age_minutes: int,
) -> Dict[str, Any]:
    effective_trading_date = get_effective_trading_date("cn")
    normalized_max_age_minutes = max(0, int(max_age_minutes or 0))
    same_day_cache_age_seconds: Optional[int] = None
    should_force_refresh = bool(force_refresh)
    if snapshot_date == effective_trading_date and normalized_max_age_minutes > 0:
        same_day_cache_age_seconds = normalized_max_age_minutes * 60
    elif snapshot_date == effective_trading_date and normalized_max_age_minutes <= 0:
        should_force_refresh = True
    return {
        "effective_trading_date": effective_trading_date.isoformat(),
        "force_refresh": should_force_refresh,
        "max_same_day_cache_age_seconds": same_day_cache_age_seconds,
    }


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    snapshot_date = _parse_snapshot_date(args.snapshot_date)
    report_period_hint = str(args.report_period_hint or "").strip() or resolve_current_report_period(snapshot_date)
    output_dir = Path(args.output_root) / snapshot_date.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    manager = KlineSelectorService.build_fast_a_share_manager()
    service = KlineSelectorService(manager=manager)

    try:
        logger.info("加载 A 股股票池并准备预热样本: snapshot_date=%s", snapshot_date.isoformat())
        universe = service.get_spot_enriched_a_share_universe(
            limit=args.limit,
            as_of_date=snapshot_date,
        )
        prepared = service.prepare_scan_universe(
            universe=universe,
            prefilter=None,
            **resolve_local_strategy_universe_filters(
                exclude_st=bool(args.exclude_st),
                exclude_cyb=bool(args.exclude_cyb),
            ),
            as_of_date=snapshot_date,
        )
        ranked_universe = _build_ranked_universe(
            prepared.prepared_universe,
            allowed_prefixes=_normalize_prefixes(args.allowed_prefixes),
        )
        event_catalog_refresh_policy = _resolve_event_catalog_refresh_policy(
            snapshot_date=snapshot_date,
            force_refresh=bool(args.force_refresh_event_catalog),
            max_age_minutes=int(args.event_catalog_max_age_minutes),
        )
        recent_event_catalog = build_recent_earnings_event_catalog(
            snapshot_date=snapshot_date,
            force_refresh=bool(event_catalog_refresh_policy["force_refresh"]),
            period_list_override=[report_period_hint],
            max_same_day_cache_age_seconds=event_catalog_refresh_policy["max_same_day_cache_age_seconds"],
        )

        kline_warm_df = _warm_kline_cache(
            manager,
            ranked_universe,
            snapshot_date=snapshot_date,
            top_n=args.top_n,
            kline_days=args.kline_days,
        )
        earnings_warm_df = _warm_earnings_cache(
            manager,
            ranked_universe,
            report_period_hint=report_period_hint,
            recent_event_catalog=recent_event_catalog,
            top_n=(
                int(args.earnings_top_n)
                if int(args.top_n) <= 0
                else min(int(args.earnings_top_n), int(args.top_n))
            ),
            budget_seconds=args.earnings_budget_seconds,
        )

        ranked_output = ranked_universe.copy()
        ranked_output.to_csv(output_dir / "ranked_universe.csv", index=False, encoding="utf-8-sig")
        kline_warm_df.to_csv(output_dir / "kline_cache_warm_results.csv", index=False, encoding="utf-8-sig")
        earnings_warm_df.to_csv(output_dir / "earnings_cache_warm_results.csv", index=False, encoding="utf-8-sig")
        (output_dir / "ranked_codes.txt").write_text(
            "\n".join(ranked_output["code"].astype(str).tolist()) + "\n" if not ranked_output.empty else "",
            encoding="utf-8",
        )

        summary = _build_summary(
            ranked_universe,
            kline_warm_df,
            earnings_warm_df,
            snapshot_date=snapshot_date,
            report_period_hint=report_period_hint,
            args=args,
        )
        summary["event_catalog_refresh_policy"] = event_catalog_refresh_policy
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        logger.info(
            "本地缓存预热完成: universe=%s kline=%s earnings=%s output=%s",
            len(ranked_universe),
            len(kline_warm_df),
            len(earnings_warm_df),
            output_dir,
        )
        return 0
    finally:
        if hasattr(manager, "close"):
            try:
                manager.close()
            except Exception:
                logger.debug("closing warm-cache manager failed", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
