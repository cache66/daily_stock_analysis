# -*- coding: utf-8 -*-
"""
Signal snapshot query service.

Provides query/read models for persisted K-line signal snapshots such as
``hundred_day_high``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.repositories.stock_repo import StockRepository
from src.services.kline_selector_service import KlineSelectorService
from src.storage import DatabaseManager
from src.utils.data_processing import parse_json_field


class SignalSnapshotService:
    """Read/query service for K-line signal snapshots."""

    BOARD_RECOGNIZABILITY_SIGNAL_PREFIX = "board_recognizability__"
    MONTHLY_SLOW_RISE_SIGNAL_PREFIX = "monthly_slow_rise_profile__"
    COMPOSITE_SIGNAL_TYPES: Dict[str, Dict[str, str]] = {
        "hundred_day_high_with_earnings": {
            "primary": "hundred_day_high",
            "secondary": "earnings_surprise",
        },
    }
    DEFAULT_SIGNAL_TYPES: List[str] = [
        "trend_leader_unified",
        "hundred_day_high",
        "earnings_surprise",
        "monthly_slow_rise",
        "daily_slow_rise",
        "long_base_release",
        "earnings_observation_registry",
        "earnings_observation_active",
        "hundred_day_high_with_earnings",
        "dragon_head_candidate",
        "commodity_beneficiary__optical_fiber",
        "commodity_beneficiary__memory",
        "commodity_beneficiary__hard_disk",
        "shortline_hub",
        "shortline_top_pick",
        "shortline_watchlist",
        "shortline_high_risk_mover",
    ]

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.stock_repo = StockRepository(self.db)
        self._history_manager = None

    def get_snapshot_list(
        self,
        *,
        signal_type: str,
        signal_date: Optional[Any] = None,
        signal_date_from: Optional[Any] = None,
        signal_date_to: Optional[Any] = None,
        code: Optional[str] = None,
        codes: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        normalized_signal_type = str(signal_type or "").strip()
        if normalized_signal_type in self.COMPOSITE_SIGNAL_TYPES:
            return self._get_composite_snapshot_list(
                signal_type=normalized_signal_type,
                signal_date=signal_date,
                signal_date_from=signal_date_from,
                signal_date_to=signal_date_to,
                code=code,
                codes=codes,
                page=page,
                page_size=page_size,
            )
        normalized_date = self._coerce_date(signal_date)
        normalized_from = self._coerce_date(signal_date_from)
        normalized_to = self._coerce_date(signal_date_to)
        if normalized_date is None and normalized_from is None and normalized_to is None:
            raise ValueError("signal_date or signal_date_from/signal_date_to is required")
        if normalized_date is not None and (normalized_from is not None or normalized_to is not None):
            raise ValueError("signal_date cannot be used together with signal_date_from/signal_date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("signal_date_from cannot be later than signal_date_to")

        page = max(1, int(page))
        page_size = max(1, int(page_size))
        offset = (page - 1) * page_size
        total = self.db.count_signal_snapshots(
            signal_type=signal_type,
            signal_date=normalized_date,
            start_date=normalized_from,
            end_date=normalized_to,
            code=code,
            codes=codes,
        )

        rows = self.db.get_signal_snapshots(
            signal_type=signal_type,
            signal_date=normalized_date,
            start_date=normalized_from,
            end_date=normalized_to,
            code=code,
            codes=codes,
            limit=page_size,
            offset=offset,
        )
        ytd_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        items = [self._row_to_list_item(row, ytd_cache=ytd_cache) for row in rows]
        compare_summary: List[Dict[str, Any]] = []
        streak_leaderboard: List[Dict[str, Any]] = []
        projection_rows_cache: Dict[tuple[bool, bool, bool], List[Any]] = {}

        def get_projection_rows(
            *,
            include_history_payload: bool = False,
            include_metrics_payload: bool = False,
            include_cause_payload: bool = False,
        ) -> List[Any]:
            cache_key = (
                include_history_payload,
                include_metrics_payload,
                include_cause_payload,
            )
            if cache_key not in projection_rows_cache:
                projection_rows_cache[cache_key] = self.db.get_signal_snapshot_projection(
                    signal_type=signal_type,
                    signal_date=normalized_date,
                    start_date=normalized_from,
                    end_date=normalized_to,
                    code=code,
                    codes=codes,
                    include_history_payload=include_history_payload,
                    include_metrics_payload=include_metrics_payload,
                    include_cause_payload=include_cause_payload,
                )
            return projection_rows_cache[cache_key]

        if normalized_from is not None or normalized_to is not None:
            if not code and not codes:
                daily_summaries = self.db.get_signal_daily_summaries(
                    signal_type=signal_type,
                    start_date=normalized_from,
                    end_date=normalized_to,
                )
                compare_summary = self._build_compare_summary_from_daily_summaries(daily_summaries)
            else:
                projection_rows = get_projection_rows(include_history_payload=True, include_metrics_payload=True)
                compare_items = [self._projection_row_to_compare_item(row) for row in projection_rows]
                compare_summary = self._build_compare_summary(compare_items)

            streak_rows = self.db.get_signal_streak_snapshots(
                signal_type=signal_type,
                start_date=normalized_from,
                end_date=normalized_to,
                code=code,
                codes=codes,
            )
            streak_leaderboard = self._build_streak_leaderboard_from_streak_rows(streak_rows)
            if not compare_summary and total > 0:
                projection_rows = get_projection_rows(include_history_payload=True, include_metrics_payload=True)
                compare_items = [self._projection_row_to_compare_item(row) for row in projection_rows]
                compare_summary = self._build_compare_summary(compare_items)
            if not streak_leaderboard and total > 0:
                projection_rows = get_projection_rows(
                    include_metrics_payload=True,
                    include_cause_payload=True,
                )
                streak_leaderboard = self._build_streak_leaderboard_from_projection_rows(
                    projection_rows,
                    signal_type=signal_type,
                    signal_date=normalized_date,
                    signal_date_from=normalized_from,
                    signal_date_to=normalized_to,
                    code=code,
                    codes=codes,
                )
        return {
            "signal_type": str(signal_type or "").strip(),
            "signal_date": normalized_date.isoformat() if normalized_date else None,
            "signal_date_from": normalized_from.isoformat() if normalized_from else None,
            "signal_date_to": normalized_to.isoformat() if normalized_to else None,
            "total": total,
            "page": page,
            "page_size": page_size,
            "compare_summary": compare_summary,
            "streak_leaderboard": streak_leaderboard,
            "items": items,
        }

    def get_signal_history(
        self,
        *,
        signal_type: str,
        code: str,
        days: int = 180,
        limit: int = 100,
    ) -> Dict[str, Any]:
        normalized_signal_type = str(signal_type or "").strip()
        if normalized_signal_type in self.COMPOSITE_SIGNAL_TYPES:
            return self._get_composite_signal_history(
                signal_type=normalized_signal_type,
                code=code,
                days=days,
                limit=limit,
            )
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("code is required")

        days = max(1, int(days))
        rows = self.db.get_signal_snapshots(
            signal_type=signal_type,
            code=normalized_code,
            days=days,
            limit=limit,
        )
        ytd_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        items = [self._row_to_history_item(row, ytd_cache=ytd_cache) for row in rows]
        continuity = self._build_continuity_summary(items)
        drawdown = self._build_drawdown_summary(items)
        return {
            "signal_type": str(signal_type or "").strip(),
            "code": normalized_code,
            "days": days,
            "total": len(items),
            "continuity": continuity,
            "drawdown": drawdown,
            "items": items,
        }

    def get_snapshot_counts(
        self,
        *,
        signal_date: Optional[Any] = None,
        signal_date_from: Optional[Any] = None,
        signal_date_to: Optional[Any] = None,
        code: Optional[str] = None,
        codes: Optional[List[str]] = None,
        signal_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        normalized_date = self._coerce_date(signal_date)
        normalized_from = self._coerce_date(signal_date_from)
        normalized_to = self._coerce_date(signal_date_to)
        if normalized_date is None and normalized_from is None and normalized_to is None:
            raise ValueError("signal_date or signal_date_from/signal_date_to is required")
        if normalized_date is not None and (normalized_from is not None or normalized_to is not None):
            raise ValueError("signal_date cannot be used together with signal_date_from/signal_date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("signal_date_from cannot be later than signal_date_to")

        requested_types = signal_types or list(self.DEFAULT_SIGNAL_TYPES)
        if signal_types is None:
            requested_types.extend(
                self.db.list_signal_snapshot_signal_types(
                    signal_date=normalized_date,
                    start_date=normalized_from,
                    end_date=normalized_to,
                    code=code,
                    codes=codes,
                    prefix=self.BOARD_RECOGNIZABILITY_SIGNAL_PREFIX,
                )
            )
            requested_types.extend(
                self.db.list_signal_snapshot_signal_types(
                    signal_date=normalized_date,
                    start_date=normalized_from,
                    end_date=normalized_to,
                    code=code,
                    codes=codes,
                    prefix=self.MONTHLY_SLOW_RISE_SIGNAL_PREFIX,
                )
            )
        items: List[Dict[str, Any]] = []
        seen_signal_types = set()
        for signal_type in requested_types:
            normalized_signal_type = str(signal_type or "").strip()
            if not normalized_signal_type or normalized_signal_type in seen_signal_types:
                continue
            seen_signal_types.add(normalized_signal_type)
            if normalized_signal_type in self.COMPOSITE_SIGNAL_TYPES:
                total = len(
                    self._build_composite_projection_rows(
                        signal_type=normalized_signal_type,
                        signal_date=normalized_date,
                        signal_date_from=normalized_from,
                        signal_date_to=normalized_to,
                        code=code,
                        codes=codes,
                    )
                )
            else:
                total = self.db.count_signal_snapshots(
                    signal_type=normalized_signal_type,
                    signal_date=normalized_date,
                    start_date=normalized_from,
                    end_date=normalized_to,
                    code=code,
                    codes=codes,
                )
            count_item = {
                "signal_type": normalized_signal_type,
                "total": int(total or 0),
            }
            count_item.update(
                self._build_signal_type_count_metadata(
                    signal_type=normalized_signal_type,
                    signal_date=normalized_date,
                    signal_date_from=normalized_from,
                    signal_date_to=normalized_to,
                    code=code,
                    codes=codes,
                )
            )
            items.append(count_item)
        return {
            "signal_date": normalized_date.isoformat() if normalized_date else None,
            "signal_date_from": normalized_from.isoformat() if normalized_from else None,
            "signal_date_to": normalized_to.isoformat() if normalized_to else None,
            "items": items,
        }

    def _get_composite_snapshot_list(
        self,
        *,
        signal_type: str,
        signal_date: Optional[Any],
        signal_date_from: Optional[Any],
        signal_date_to: Optional[Any],
        code: Optional[str],
        codes: Optional[List[str]],
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        normalized_date = self._coerce_date(signal_date)
        normalized_from = self._coerce_date(signal_date_from)
        normalized_to = self._coerce_date(signal_date_to)
        if normalized_date is None and normalized_from is None and normalized_to is None:
            raise ValueError("signal_date or signal_date_from/signal_date_to is required")
        if normalized_date is not None and (normalized_from is not None or normalized_to is not None):
            raise ValueError("signal_date cannot be used together with signal_date_from/signal_date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("signal_date_from cannot be later than signal_date_to")

        page = max(1, int(page))
        page_size = max(1, int(page_size))
        offset = (page - 1) * page_size

        composite_rows = self._build_composite_projection_rows(
            signal_type=signal_type,
            signal_date=normalized_date,
            signal_date_from=normalized_from,
            signal_date_to=normalized_to,
            code=code,
            codes=codes,
        )
        total = len(composite_rows)
        paged_rows = composite_rows[offset: offset + page_size]
        ytd_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        items = [self._row_to_list_item(SimpleNamespace(**row), ytd_cache=ytd_cache) for row in paged_rows]
        compare_summary: List[Dict[str, Any]] = []
        streak_leaderboard: List[Dict[str, Any]] = []
        if normalized_from is not None or normalized_to is not None:
            compare_items = [self._projection_row_to_compare_item(row) for row in composite_rows]
            compare_summary = self._build_compare_summary(compare_items)
            streak_leaderboard = self._build_streak_leaderboard([
                self._row_to_list_item(SimpleNamespace(**row), ytd_cache=ytd_cache) for row in composite_rows
            ])
        return {
            "signal_type": signal_type,
            "signal_date": normalized_date.isoformat() if normalized_date else None,
            "signal_date_from": normalized_from.isoformat() if normalized_from else None,
            "signal_date_to": normalized_to.isoformat() if normalized_to else None,
            "total": total,
            "page": page,
            "page_size": page_size,
            "compare_summary": compare_summary,
            "streak_leaderboard": streak_leaderboard,
            "items": items,
        }

    def _get_composite_signal_history(
        self,
        *,
        signal_type: str,
        code: str,
        days: int,
        limit: int,
    ) -> Dict[str, Any]:
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("code is required")

        days = max(1, int(days))
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        composite_rows = self._build_composite_projection_rows(
            signal_type=signal_type,
            signal_date=None,
            signal_date_from=start_date,
            signal_date_to=end_date,
            code=normalized_code,
            codes=None,
        )
        composite_rows = composite_rows[: max(1, int(limit))]
        ytd_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        items = [self._row_to_history_item(SimpleNamespace(**row), ytd_cache=ytd_cache) for row in composite_rows]
        continuity = self._build_continuity_summary(items)
        drawdown = self._build_drawdown_summary(items)
        return {
            "signal_type": signal_type,
            "code": normalized_code,
            "days": days,
            "total": len(items),
            "continuity": continuity,
            "drawdown": drawdown,
            "items": items,
        }

    def _build_composite_projection_rows(
        self,
        *,
        signal_type: str,
        signal_date: Optional[date],
        signal_date_from: Optional[date],
        signal_date_to: Optional[date],
        code: Optional[str],
        codes: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        config = self.COMPOSITE_SIGNAL_TYPES.get(signal_type) or {}
        primary_type = config.get("primary")
        secondary_type = config.get("secondary")
        if not primary_type or not secondary_type:
            return []

        primary_rows = self.db.get_signal_snapshot_projection(
            signal_type=primary_type,
            signal_date=signal_date,
            start_date=signal_date_from,
            end_date=signal_date_to,
            code=code,
            codes=codes,
            include_history_payload=True,
            include_metrics_payload=True,
            include_cause_payload=True,
        )
        secondary_rows = self.db.get_signal_snapshot_projection(
            signal_type=secondary_type,
            signal_date=signal_date,
            start_date=signal_date_from,
            end_date=signal_date_to,
            code=code,
            codes=codes,
            include_history_payload=True,
            include_metrics_payload=True,
            include_cause_payload=True,
        )

        secondary_by_key = {
            (
                self._coerce_date(row.get("signal_date")).isoformat() if self._coerce_date(row.get("signal_date")) else "",
                str(row.get("code") or "").strip(),
            ): row
            for row in secondary_rows
            if str(row.get("code") or "").strip() and self._coerce_date(row.get("signal_date")) is not None
        }

        grouped_rows: Dict[str, List[Dict[str, Any]]] = {}
        merged_rows: List[Dict[str, Any]] = []
        for primary_row in primary_rows:
            signal_day = self._coerce_date(primary_row.get("signal_date"))
            code_value = str(primary_row.get("code") or "").strip()
            if signal_day is None or not code_value:
                continue
            match = secondary_by_key.get((signal_day.isoformat(), code_value))
            if match is None:
                continue
            primary_metrics = self._to_dict(primary_row.get("metrics_payload"))
            primary_cause = self._to_dict(primary_row.get("cause_payload"))
            secondary_metrics = self._to_dict(match.get("metrics_payload"))
            secondary_cause = self._to_dict(match.get("cause_payload"))

            merged_metrics = dict(primary_metrics)
            merged_metrics["event_date"] = (
                secondary_metrics.get("event_date")
                or secondary_metrics.get("quick_report_announcement_date")
                or secondary_metrics.get("forecast_announcement_date")
                or secondary_metrics.get("report_date")
            )
            merged_metrics["forecast_summary"] = secondary_metrics.get("forecast_summary")
            merged_metrics["quick_report_summary"] = secondary_metrics.get("quick_report_summary")
            merged_metrics["earnings_signal_score"] = secondary_metrics.get("signal_score")
            merged_cause = dict(primary_cause)
            if not str(merged_cause.get("reason_summary", "") or "").strip():
                merged_cause["reason_summary"] = str(secondary_cause.get("reason_summary", "") or "").strip()
            merged_cause["earnings_reason_summary"] = str(secondary_cause.get("reason_summary", "") or "").strip()
            merged_cause["earnings_news_logic"] = str(secondary_cause.get("news_logic", "") or "").strip()
            merged_cause["earnings_technical_logic"] = str(secondary_cause.get("technical_logic", "") or "").strip()

            merged_row = {
                "signal_type": signal_type,
                "code": code_value,
                "name": primary_row.get("name") or match.get("name"),
                "signal_date": signal_day,
                "metrics_payload": merged_metrics,
                "cause_payload": merged_cause,
                "history_payload": {},
            }
            grouped_rows.setdefault(code_value, []).append(merged_row)
            merged_rows.append(merged_row)

        for code_value, rows in grouped_rows.items():
            rows.sort(key=lambda item: self._coerce_date(item.get("signal_date")) or date.min, reverse=True)
            previous_dates: List[date] = []
            for row in rows:
                signal_day = self._coerce_date(row.get("signal_date"))
                recent_hit_dates = [item.isoformat() for item in previous_dates]
                latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
                days_since_previous_hit = None
                if signal_day is not None and latest_previous_hit_date is not None:
                    days_since_previous_hit = (signal_day - date.fromisoformat(latest_previous_hit_date)).days
                row["history_payload"] = {
                    "previous_hit_count": len(previous_dates),
                    "latest_previous_hit_date": latest_previous_hit_date,
                    "days_since_previous_hit": days_since_previous_hit,
                    "recent_hit_dates": recent_hit_dates,
                }
                if signal_day is not None:
                    previous_dates.append(signal_day)
        merged_rows.sort(
            key=lambda item: (
                -(self._coerce_date(item.get("signal_date")).toordinal() if self._coerce_date(item.get("signal_date")) else 0),
                str(item.get("code") or ""),
            )
        )
        return merged_rows

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _to_dict(payload: Any) -> Dict[str, Any]:
        parsed = parse_json_field(payload)
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _is_board_recognizability_signal_type(signal_type: str) -> bool:
        return str(signal_type or "").strip().startswith(
            SignalSnapshotService.BOARD_RECOGNIZABILITY_SIGNAL_PREFIX
        )

    @staticmethod
    def _is_monthly_slow_rise_signal_type(signal_type: str) -> bool:
        normalized = str(signal_type or "").strip()
        return (
            normalized == "monthly_slow_rise"
            or normalized.startswith(SignalSnapshotService.MONTHLY_SLOW_RISE_SIGNAL_PREFIX)
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_optional_bool(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if not text:
            return None
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
        return None

    @staticmethod
    def _build_trend_leader_signal_tags(metrics: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        near_new_high = SignalSnapshotService._to_optional_bool(metrics.get("near_new_high"))
        breakout = SignalSnapshotService._to_optional_bool(metrics.get("is_breakout_candidate"))
        pullback = SignalSnapshotService._to_optional_bool(metrics.get("is_pullback_candidate"))
        selection_mode = str(metrics.get("selection_mode", "") or "").strip().lower()

        if near_new_high:
            tags.append("百日新高")
        if breakout:
            tags.append("突破形态")
        if pullback:
            tags.append("回踩形态")
        if selection_mode == "strict":
            tags.append("严格命中")
        elif selection_mode == "fallback":
            tags.append("兜底观察")

        deduped: List[str] = []
        for tag in tags:
            if tag and tag not in deduped:
                deduped.append(tag)
        return deduped

    def _get_history_manager(self):
        if self._history_manager is None:
            self._history_manager = KlineSelectorService.build_fast_a_share_manager()
        return self._history_manager

    def _build_ytd_fields(
        self,
        *,
        signal_type: str,
        code: str,
        name: Optional[str],
        signal_date: Optional[date],
        metrics: Dict[str, Any],
        cache: Optional[Dict[tuple[str, str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not code or signal_date is None:
            return {
                "year_start_date": None,
                "year_start_close": None,
                "ytd_return_pct": None,
            }
        if str(code).strip().upper() == "TL_SUMMARY":
            return {
                "year_start_date": None,
                "year_start_close": None,
                "ytd_return_pct": None,
            }

        cache_key = (code, signal_date.isoformat())
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        signal_close = self._to_float(metrics.get("close"))
        try:
            start_daily = self.stock_repo.get_first_daily_of_year(
                code=code,
                year=signal_date.year,
                end_date=signal_date,
            )
        except Exception:
            start_daily = None
        start_close = self._to_float(getattr(start_daily, "close", None))
        year_start_date = start_daily.date.isoformat() if getattr(start_daily, "date", None) else None

        if signal_close is None:
            try:
                latest_daily = self.stock_repo.get_latest_daily_on_or_before(
                    code=code,
                    target_date=signal_date,
                )
            except Exception:
                latest_daily = None
            signal_close = self._to_float(getattr(latest_daily, "close", None))

        if start_close is None or signal_close is None:
            try:
                history_df, _history_source = self._get_history_manager().get_daily_data(
                    code,
                    start_date=date(int(signal_date.year), 1, 1).isoformat(),
                    end_date=signal_date.isoformat(),
                )
            except Exception:
                history_df = None
            history = KlineSelectorService._prepare_history(history_df)
            if not history.empty:
                if start_close is None:
                    start_close = self._to_float(history.iloc[0].get("close"))
                    first_date = history.iloc[0].get("date")
                    if hasattr(first_date, "date"):
                        year_start_date = first_date.date().isoformat()
                if signal_close is None:
                    signal_close = self._to_float(history.iloc[-1].get("close"))

        payload = {
            "year_start_date": year_start_date,
            "year_start_close": start_close,
            "ytd_return_pct": (
                round((signal_close - start_close) / start_close * 100, 2)
                if start_close is not None and start_close > 0 and signal_close is not None
                else None
            ),
        }
        existing_payload = {
            "year_start_date": metrics.get("year_start_date"),
            "year_start_close": self._to_float(metrics.get("year_start_close")),
            "ytd_return_pct": self._to_float(metrics.get("ytd_return_pct")),
        }
        if payload != existing_payload:
            patched_metrics = dict(metrics)
            patched_metrics.update(payload)
            try:
                self.db.upsert_signal_snapshot(
                    signal_type=signal_type,
                    signal_date=signal_date,
                    code=code,
                    name=name,
                    metrics_payload=patched_metrics,
                )
            except Exception:
                pass
        if cache is not None:
            cache[cache_key] = payload
        return payload

    def _row_to_list_item(
        self,
        row: Any,
        *,
        ytd_cache: Optional[Dict[tuple[str, str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        metrics = self._to_dict(getattr(row, "metrics_payload", None))
        cause = self._to_dict(getattr(row, "cause_payload", None))
        history = self._to_dict(getattr(row, "history_payload", None))
        criteria = self._to_dict(getattr(row, "criteria_payload", None))
        signal_date = getattr(row, "signal_date", None)
        ytd_fields = self._build_ytd_fields(
            signal_type=getattr(row, "signal_type", "") or "",
            code=getattr(row, "code", ""),
            name=getattr(row, "name", None),
            signal_date=signal_date,
            metrics=metrics,
            cache=ytd_cache,
        )

        return {
            "code": getattr(row, "code", ""),
            "name": getattr(row, "name", ""),
            "signal_date": signal_date.isoformat() if signal_date else None,
            "event_date": str(metrics.get("event_date", "") or "").strip() or None,
            "board_name": (
                str(metrics.get("board_name", "") or "").strip()
                or str(criteria.get("board_name", "") or "").strip()
                or str(cause.get("industry", "") or "").strip()
                or None
            ),
            "board_rank": self._to_int(metrics.get("board_rank")),
            "board_candidate_count": self._to_int(metrics.get("board_candidate_count")),
            "source_signal_type": str(metrics.get("source_signal_type", "") or "").strip() or None,
            "source_signal_date": str(metrics.get("source_signal_date", "") or "").strip() or None,
            "subtheme_key": str(metrics.get("subtheme_key", "") or "").strip() or None,
            "chain_role": str(metrics.get("chain_role", "") or "").strip() or None,
            "pass_through_direction": str(metrics.get("pass_through_direction", "") or "").strip() or None,
            "earnings_validation_status": str(metrics.get("earnings_validation_status", "") or "").strip() or None,
            "earnings_release_probability": str(metrics.get("earnings_release_probability", "") or "").strip() or None,
            "earnings_quality_signal": bool(metrics.get("earnings_quality_signal")) if metrics.get("earnings_quality_signal") is not None else None,
            "earnings_strategy_score": self._to_float(metrics.get("earnings_strategy_score")),
            "earnings_strategy_label": str(metrics.get("earnings_strategy_label", "") or "").strip() or None,
            "earnings_strategy_gate_status": str(metrics.get("earnings_strategy_gate_status", "") or "").strip() or None,
            "earnings_growth_continuity_score": self._to_float(metrics.get("earnings_growth_continuity_score")),
            "earnings_profit_quality_score": self._to_float(metrics.get("earnings_profit_quality_score")),
            "earnings_profitability_score": self._to_float(metrics.get("earnings_profitability_score")),
            "earnings_disclosure_signal_score": self._to_float(metrics.get("earnings_disclosure_signal_score")),
            "earnings_cycle_score": self._to_float(metrics.get("earnings_cycle_score")),
            "earnings_event_freshness_score": self._to_float(metrics.get("earnings_event_freshness_score")),
            "earnings_risk_penalty": self._to_float(metrics.get("earnings_risk_penalty")),
            "earnings_quality_verdict": str(metrics.get("earnings_quality_verdict", "") or "").strip() or None,
            "earnings_quality_score": self._to_float(metrics.get("earnings_quality_score")),
            "earnings_quality_cycle_phase": str(metrics.get("earnings_quality_cycle_phase", "") or "").strip() or None,
            "earnings_quality_quarterly_trend": str(metrics.get("earnings_quality_quarterly_trend", "") or "").strip() or None,
            "earnings_quality_dual_positive_streak": self._to_int(metrics.get("earnings_quality_dual_positive_streak")),
            "directness": str(metrics.get("directness", "") or "").strip() or None,
            "matched_example_bucket": str(metrics.get("matched_example_bucket", "") or "").strip() or None,
            "matched_example_name": str(metrics.get("matched_example_name", "") or "").strip() or None,
            "recognizability_score": self._to_float(metrics.get("recognizability_score")),
            "sustained_growth_score": self._to_float(metrics.get("sustained_growth_score")),
            "liquidity_score": self._to_float(metrics.get("liquidity_score")),
            "valuation_score": self._to_float(metrics.get("valuation_score")),
            "dividend_score": self._to_float(metrics.get("dividend_score")),
            "logic_consensus_score": self._to_float(metrics.get("logic_consensus_score")),
            "capital_consensus_score": self._to_float(metrics.get("capital_consensus_score")),
            "cache_source": str(metrics.get("cache_source", "") or "").strip() or None,
            "bundle_refreshed_at": str(metrics.get("bundle_refreshed_at", "") or "").strip() or None,
            "capital_profile_refreshed_at": str(metrics.get("capital_profile_refreshed_at", "") or "").strip() or None,
            "capital_profile_cache_hit": (
                bool(metrics.get("capital_profile_cache_hit"))
                if metrics.get("capital_profile_cache_hit") is not None
                else None
            ),
            "leader_probability": str(metrics.get("leader_probability", "") or "").strip() or None,
            "leader_type": str(metrics.get("leader_type", "") or "").strip() or None,
            "selection_mode": str(metrics.get("selection_mode", "") or "").strip() or None,
            "is_breakout_candidate": self._to_optional_bool(metrics.get("is_breakout_candidate")),
            "is_pullback_candidate": self._to_optional_bool(metrics.get("is_pullback_candidate")),
            "near_new_high": self._to_optional_bool(metrics.get("near_new_high")),
            "signal_tags": self._build_trend_leader_signal_tags(metrics),
            "primary_profile": str(metrics.get("primary_profile", "") or "").strip() or None,
            "breakout_score": self._to_float(metrics.get("breakout_score")),
            "pullback_score": self._to_float(metrics.get("pullback_score")),
            "hybrid_score": self._to_float(metrics.get("hybrid_score")),
            "overall_score": self._to_float(metrics.get("overall_score")),
            "trend_label": str(metrics.get("trend_label", "") or "").strip() or None,
            "risk_flags": [
                str(item).strip()
                for item in (metrics.get("risk_flags") if isinstance(metrics.get("risk_flags"), list) else [])
                if str(item).strip()
            ],
            "strategy_summary": (
                str(metrics.get("strategy_summary", "") or "").strip()
                or str(cause.get("reason_summary", "") or "").strip()
                or None
            ),
            "review_tier": str(metrics.get("review_tier", "") or "").strip() or None,
            "composite_score": self._to_float(metrics.get("composite_score")),
            "shortline_category": str(metrics.get("shortline_category", "") or "").strip() or None,
            "confidence_label": str(metrics.get("confidence_label", "") or "").strip() or None,
            "short_term_view": str(metrics.get("short_term_view", "") or "").strip() or None,
            "tracking_appear_streak_days": self._to_int(metrics.get("tracking_appear_streak_days")),
            "tracking_tier_transition": str(metrics.get("tracking_tier_transition", "") or "").strip() or None,
            "profile_name": (
                str(metrics.get("profile_name", "") or "").strip()
                or str(criteria.get("profile_name", "") or "").strip()
                or None
            ),
            "profile_label": (
                str(metrics.get("profile_label", "") or "").strip()
                or str(criteria.get("profile_label", "") or "").strip()
                or None
            ),
            "sector_leadership_score": self._to_float(metrics.get("sector_leadership_score")),
            "relative_strength_score": self._to_float(metrics.get("relative_strength_score")),
            "catalyst_score": self._to_float(metrics.get("catalyst_score")),
            "monthly_positive_ratio": self._to_float(metrics.get("monthly_positive_ratio")),
            "monthly_higher_low_ratio": self._to_float(metrics.get("monthly_higher_low_ratio")),
            "monthly_total_return_pct": self._to_float(metrics.get("monthly_total_return_pct")),
            "monthly_max_single_gain_pct": self._to_float(metrics.get("monthly_max_single_gain_pct")),
            "monthly_worst_drawdown_pct": self._to_float(metrics.get("monthly_worst_drawdown_pct")),
            "monthly_ma_short": self._to_float(metrics.get("monthly_ma_short")),
            "monthly_ma_long": self._to_float(metrics.get("monthly_ma_long")),
            "monthly_latest_month": str(metrics.get("monthly_latest_month", "") or "").strip() or None,
            "industry": str(cause.get("industry", "") or "").strip(),
            "reason_summary": str(cause.get("reason_summary", "") or "").strip(),
            "industry_logic": str(cause.get("industry_logic", "") or "").strip(),
            "news_logic": str(cause.get("news_logic", "") or "").strip(),
            "technical_logic": str(cause.get("technical_logic", "") or "").strip(),
            "theme_label": str(cause.get("theme_label", "") or "").strip(),
            "latest_previous_hit_date": history.get("latest_previous_hit_date"),
            "previous_hit_count": int(history.get("previous_hit_count", 0) or 0),
            "days_since_previous_hit": history.get("days_since_previous_hit"),
            "is_consecutive_signal": self._is_consecutive_item(history),
            "close": self._to_float(metrics.get("close")),
            "latest_high": self._to_float(metrics.get("latest_high")),
            "window_high": self._to_float(metrics.get("window_high")),
            "total_market_cap": self._to_float(metrics.get("total_market_cap")),
            "total_market_cap_yi": self._to_float(metrics.get("total_market_cap_yi")),
            "year_start_date": ytd_fields.get("year_start_date"),
            "year_start_close": ytd_fields.get("year_start_close"),
            "ytd_return_pct": ytd_fields.get("ytd_return_pct"),
        }

    def _row_to_history_item(
        self,
        row: Any,
        *,
        ytd_cache: Optional[Dict[tuple[str, str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        base = self._row_to_list_item(row, ytd_cache=ytd_cache)
        metrics = self._to_dict(getattr(row, "metrics_payload", None))
        base.update(
            {
                "new_high_window": metrics.get("new_high_window"),
                "history_source": metrics.get("history_source"),
                "total_market_cap": self._to_float(metrics.get("total_market_cap")),
                "total_market_cap_yi": self._to_float(metrics.get("total_market_cap_yi")),
            }
        )
        return base

    def _build_signal_type_count_metadata(
        self,
        *,
        signal_type: str,
        signal_date: Optional[date],
        signal_date_from: Optional[date],
        signal_date_to: Optional[date],
        code: Optional[str],
        codes: Optional[List[str]],
    ) -> Dict[str, Any]:
        if signal_type == "trend_leader_unified":
            return {
                "group": "strategy",
                "display_label": "强趋势龙头总榜",
            }
        if signal_type == "hundred_day_high":
            return {
                "group": "strategy",
                "display_label": "百日新高",
            }
        if signal_type == "earnings_surprise":
            return {
                "group": "strategy",
                "display_label": "业绩超预期",
            }
        if signal_type == "monthly_slow_rise":
            return {
                "group": "strategy",
                "display_label": "月线慢牛",
            }
        if signal_type == "daily_slow_rise":
            return {
                "group": "strategy",
                "display_label": "日线慢涨",
            }
        if signal_type == "long_base_release":
            return {
                "group": "strategy",
                "display_label": "长横盘释放",
            }
        if signal_type == "hundred_day_high_with_earnings":
            return {
                "group": "strategy",
                "display_label": "新高且业绩",
            }
        if signal_type == "earnings_observation_registry":
            return {
                "group": "strategy",
                "display_label": "业绩观察池",
            }
        if signal_type == "earnings_observation_active":
            return {
                "group": "strategy",
                "display_label": "业绩观察活跃",
            }
        if signal_type == "shortline_hub":
            return {
                "group": "shortline",
                "display_label": "shortline_hub",
            }
        if signal_type == "shortline_top_pick":
            return {
                "group": "shortline",
                "display_label": "shortline_top_pick",
            }
        if signal_type == "shortline_watchlist":
            return {
                "group": "shortline",
                "display_label": "shortline_watchlist",
            }
        if signal_type == "shortline_high_risk_mover":
            return {
                "group": "shortline",
                "display_label": "shortline_high_risk_mover",
            }

        if not self._is_board_recognizability_signal_type(signal_type) and not self._is_monthly_slow_rise_signal_type(signal_type):
            return {}

        rows = self.db.get_signal_snapshots(
            signal_type=signal_type,
            signal_date=signal_date,
            start_date=signal_date_from,
            end_date=signal_date_to,
            code=code,
            codes=codes,
            limit=1,
        )
        if not rows:
            return {
                "group": "board_recognizability" if self._is_board_recognizability_signal_type(signal_type) else "monthly_slow_rise"
            }

        row = rows[0]
        metrics = self._to_dict(getattr(row, "metrics_payload", None))
        criteria = self._to_dict(getattr(row, "criteria_payload", None))
        cause = self._to_dict(getattr(row, "cause_payload", None))
        if self._is_monthly_slow_rise_signal_type(signal_type):
            profile_label = (
                str(metrics.get("profile_label", "") or "").strip()
                or str(criteria.get("profile_label", "") or "").strip()
                or str(metrics.get("profile_name", "") or "").strip()
                or str(criteria.get("profile_name", "") or "").strip()
            )
            metadata: Dict[str, Any] = {"group": "monthly_slow_rise"}
            if profile_label:
                metadata["display_label"] = profile_label
            return metadata

        board_name = (
            str(metrics.get("board_name", "") or "").strip()
            or str(criteria.get("board_name", "") or "").strip()
            or str(cause.get("industry", "") or "").strip()
        )
        metadata: Dict[str, Any] = {"group": "board_recognizability"}
        if board_name:
            metadata["display_label"] = f"{board_name}辨识度"
        return metadata

    def _build_continuity_summary(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {
                "is_current_streak": False,
                "current_streak_count": 0,
                "current_streak_start_date": None,
                "current_streak_end_date": None,
                "longest_streak_count": 0,
                "longest_streak_start_date": None,
                "longest_streak_end_date": None,
            }

        signal_dates = [
            self._coerce_date(item.get("signal_date"))
            for item in items
            if self._coerce_date(item.get("signal_date")) is not None
        ]
        if not signal_dates:
            return {
                "is_current_streak": False,
                "current_streak_count": 0,
                "current_streak_start_date": None,
                "current_streak_end_date": None,
                "longest_streak_count": 0,
                "longest_streak_start_date": None,
                "longest_streak_end_date": None,
            }

        current_streak = [signal_dates[0]]
        for next_date in signal_dates[1:]:
            if self._is_consecutive_signal(current_streak[-1], next_date):
                current_streak.append(next_date)
            else:
                break

        streaks: List[List[date]] = []
        active: List[date] = [signal_dates[0]]
        for next_date in signal_dates[1:]:
            if self._is_consecutive_signal(active[-1], next_date):
                active.append(next_date)
            else:
                streaks.append(active)
                active = [next_date]
        streaks.append(active)
        longest = max(streaks, key=len)

        return {
            "is_current_streak": len(current_streak) > 1,
            "current_streak_count": len(current_streak),
            "current_streak_start_date": current_streak[-1].isoformat(),
            "current_streak_end_date": current_streak[0].isoformat(),
            "longest_streak_count": len(longest),
            "longest_streak_start_date": longest[-1].isoformat(),
            "longest_streak_end_date": longest[0].isoformat(),
        }

    @staticmethod
    def _is_consecutive_signal(later_date: date, earlier_date: date) -> bool:
        delta_days = (later_date - earlier_date).days
        return 0 < delta_days <= 4

    @staticmethod
    def _is_consecutive_item(history_payload: Dict[str, Any]) -> bool:
        previous_hits = int(history_payload.get("previous_hit_count", 0) or 0)
        days_since_previous_hit = history_payload.get("days_since_previous_hit")
        return previous_hits > 0 and isinstance(days_since_previous_hit, int) and 0 < days_since_previous_hit <= 4

    def _projection_row_to_compare_item(self, row: Dict[str, Any]) -> Dict[str, Any]:
        history = self._to_dict(row.get("history_payload"))
        metrics = self._to_dict(row.get("metrics_payload"))
        return {
            "code": row.get("code"),
            "name": row.get("name"),
            "signal_date": row.get("signal_date").isoformat() if row.get("signal_date") else None,
            "is_consecutive_signal": self._is_consecutive_item(history),
            "ytd_return_pct": self._to_float(metrics.get("ytd_return_pct")),
        }

    def _build_drawdown_summary(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {
                "anchor_close": None,
                "max_signal_high": None,
                "max_signal_high_date": None,
                "distance_from_max_signal_high_pct": None,
                "latest_signal_high": None,
                "latest_signal_date": None,
                "distance_from_latest_signal_high_pct": None,
            }

        latest_item = items[0]
        anchor_close = self._to_float(latest_item.get("close"))
        latest_signal_high = self._to_float(latest_item.get("latest_high"))
        latest_signal_date = latest_item.get("signal_date")

        max_item = max(
            items,
            key=lambda item: self._to_float(item.get("latest_high")) or float("-inf"),
        )
        max_signal_high = self._to_float(max_item.get("latest_high"))
        max_signal_high_date = max_item.get("signal_date")

        return {
            "anchor_close": anchor_close,
            "max_signal_high": max_signal_high,
            "max_signal_high_date": max_signal_high_date,
            "distance_from_max_signal_high_pct": self._pct_from_peak(anchor_close, max_signal_high),
            "latest_signal_high": latest_signal_high,
            "latest_signal_date": latest_signal_date,
            "distance_from_latest_signal_high_pct": self._pct_from_peak(anchor_close, latest_signal_high),
        }

    def _build_compare_summary(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for item in items:
            signal_date = item.get("signal_date")
            if not signal_date:
                continue
            bucket = buckets.setdefault(
                signal_date,
                {
                    "signal_date": signal_date,
                    "total_count": 0,
                    "continuous_count": 0,
                    "top_codes": [],
                    "codes": set(),
                    "code_to_name": {},
                    "ytd_returns": [],
                },
            )
            bucket["total_count"] += 1
            if item.get("is_consecutive_signal"):
                bucket["continuous_count"] += 1
            ytd_value = self._to_float(item.get("ytd_return_pct"))
            if ytd_value is not None:
                bucket["ytd_returns"].append(ytd_value)
            if len(bucket["top_codes"]) < 5:
                bucket["top_codes"].append(item.get("code"))
            code = str(item.get("code") or "").strip()
            if code:
                bucket["codes"].add(code)
                if code not in bucket["code_to_name"]:
                    bucket["code_to_name"][code] = item.get("name")

        ordered_keys = sorted(buckets.keys(), reverse=True)
        result: List[Dict[str, Any]] = []
        ordered_code_sets = {
            key: set(buckets[key].pop("codes", set()))
            for key in ordered_keys
        }
        for index, key in enumerate(ordered_keys):
            bucket = buckets[key]
            current_codes = ordered_code_sets[key]
            current_code_to_name = bucket.pop("code_to_name", {})
            ytd_returns = sorted(bucket.pop("ytd_returns", []))
            older_codes = ordered_code_sets.get(ordered_keys[index + 1]) if index + 1 < len(ordered_keys) else None
            older_code_to_name = buckets[ordered_keys[index + 1]].get("code_to_name", {}) if index + 1 < len(ordered_keys) else {}
            if older_codes is None:
                added_codes: List[str] = []
                dropped_codes: List[str] = []
                added_items: List[Dict[str, Any]] = []
                dropped_items: List[Dict[str, Any]] = []
            else:
                added_codes = sorted(current_codes - older_codes)
                dropped_codes = sorted(older_codes - current_codes)
                added_items = [
                    {"code": code, "name": current_code_to_name.get(code)}
                    for code in added_codes[:5]
                ]
                dropped_items = [
                    {"code": code, "name": older_code_to_name.get(code)}
                    for code in dropped_codes[:5]
                ]
            bucket["added_count"] = len(added_codes)
            bucket["dropped_count"] = len(dropped_codes)
            bucket["added_codes"] = added_codes[:5]
            bucket["dropped_codes"] = dropped_codes[:5]
            bucket["added_items"] = added_items
            bucket["dropped_items"] = dropped_items
            bucket["avg_ytd_return_pct"] = round(sum(ytd_returns) / len(ytd_returns), 2) if ytd_returns else None
            if ytd_returns:
                mid = len(ytd_returns) // 2
                if len(ytd_returns) % 2 == 1:
                    bucket["median_ytd_return_pct"] = round(ytd_returns[mid], 2)
                else:
                    bucket["median_ytd_return_pct"] = round((ytd_returns[mid - 1] + ytd_returns[mid]) / 2, 2)
            else:
                bucket["median_ytd_return_pct"] = None
            result.append(bucket)
        return result

    def _build_compare_summary_from_daily_summaries(self, rows: List[Any]) -> List[Dict[str, Any]]:
        if not rows:
            return []

        ordered_rows = sorted(
            rows,
            key=lambda row: self._coerce_date(getattr(row, "signal_date", None)) or date.min,
            reverse=True,
        )
        result: List[Dict[str, Any]] = []
        for index, row in enumerate(ordered_rows):
            current_codes_list = self._coerce_list_json(getattr(row, "codes_json", None))
            current_codes = set(current_codes_list)
            current_name_map = self._coerce_dict_json(getattr(row, "code_to_name_json", None))
            older_codes_list = self._coerce_list_json(getattr(ordered_rows[index + 1], "codes_json", None)) if index + 1 < len(ordered_rows) else []
            older_name_map = self._coerce_dict_json(getattr(ordered_rows[index + 1], "code_to_name_json", None)) if index + 1 < len(ordered_rows) else {}
            older_codes = set(older_codes_list)
            added_codes = sorted(current_codes - older_codes) if index + 1 < len(ordered_rows) else []
            dropped_codes = sorted(older_codes - current_codes) if index + 1 < len(ordered_rows) else []
            result.append(
                {
                    "signal_date": getattr(row, "signal_date", None).isoformat() if getattr(row, "signal_date", None) else None,
                    "total_count": int(getattr(row, "total_count", 0) or 0),
                    "continuous_count": int(getattr(row, "continuous_count", 0) or 0),
                    "top_codes": self._coerce_list_json(getattr(row, "top_codes_json", None))[:5],
                    "added_count": len(added_codes),
                    "dropped_count": len(dropped_codes),
                    "added_codes": added_codes[:5],
                    "dropped_codes": dropped_codes[:5],
                    "added_items": [{"code": code, "name": current_name_map.get(code)} for code in added_codes[:5]],
                    "dropped_items": [{"code": code, "name": older_name_map.get(code)} for code in dropped_codes[:5]],
                    "avg_ytd_return_pct": self._to_float(getattr(row, "avg_ytd_return_pct", None)),
                    "median_ytd_return_pct": self._to_float(getattr(row, "median_ytd_return_pct", None)),
                }
            )
        return result

    def _build_streak_leaderboard(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            grouped.setdefault(code, []).append(item)

        leaderboard: List[Dict[str, Any]] = []
        for code, code_items in grouped.items():
            dated_items = [
                (self._coerce_date(item.get("signal_date")), item)
                for item in code_items
                if self._coerce_date(item.get("signal_date")) is not None
            ]
            if not dated_items:
                continue
            dated_items.sort(key=lambda row: row[0], reverse=True)
            dates = [entry[0] for entry in dated_items]
            current_streak = [dates[0]]
            for next_date in dates[1:]:
                if self._is_consecutive_signal(current_streak[-1], next_date):
                    current_streak.append(next_date)
                else:
                    break

            streaks: List[List[date]] = []
            active: List[date] = [dates[0]]
            for next_date in dates[1:]:
                if self._is_consecutive_signal(active[-1], next_date):
                    active.append(next_date)
                else:
                    streaks.append(active)
                    active = [next_date]
            streaks.append(active)
            longest = max(streaks, key=len)
            latest_item = dated_items[0][1]
            leaderboard.append(
                {
                    "code": code,
                    "name": latest_item.get("name"),
                    "industry": latest_item.get("industry"),
                    "current_streak_count": len(current_streak),
                    "longest_streak_count": len(longest),
                    "current_streak_start_date": current_streak[-1].isoformat(),
                    "current_streak_end_date": current_streak[0].isoformat(),
                    "latest_signal_date": latest_item.get("signal_date"),
                    "latest_high": latest_item.get("latest_high"),
                    "close": latest_item.get("close"),
                    "theme_label": latest_item.get("theme_label"),
                }
            )

        leaderboard.sort(
            key=lambda item: (
                -(item.get("current_streak_count") or 0),
                -(item.get("longest_streak_count") or 0),
                item.get("latest_signal_date") or "",
                item.get("code") or "",
            ),
        )
        return leaderboard[:10]

    def _build_streak_leaderboard_from_projection_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        signal_type: str,
        signal_date: Optional[date],
        signal_date_from: Optional[date],
        signal_date_to: Optional[date],
        code: Optional[str],
        codes: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        grouped_dates: Dict[str, List[date]] = {}
        grouped_names: Dict[str, str] = {}
        for row in rows:
            code_value = str(row.get("code") or "").strip()
            signal_day = self._coerce_date(row.get("signal_date"))
            if not code_value or signal_day is None:
                continue
            grouped_dates.setdefault(code_value, []).append(signal_day)
            if code_value not in grouped_names:
                grouped_names[code_value] = str(row.get("name") or "").strip()

        if not grouped_dates:
            return []

        latest_rows = self.db.get_signal_snapshot_projection(
            signal_type=signal_type,
            signal_date=signal_date,
            start_date=signal_date_from,
            end_date=signal_date_to,
            code=code,
            codes=codes,
            include_metrics_payload=True,
            include_cause_payload=True,
            latest_per_code=True,
        )
        latest_by_code = {
            str(row.get("code") or "").strip(): row
            for row in latest_rows
            if str(row.get("code") or "").strip()
        }

        leaderboard: List[Dict[str, Any]] = []
        for code_value, signal_dates in grouped_dates.items():
            ordered_dates = sorted(signal_dates, reverse=True)
            current_streak = [ordered_dates[0]]
            for next_date in ordered_dates[1:]:
                if self._is_consecutive_signal(current_streak[-1], next_date):
                    current_streak.append(next_date)
                else:
                    break

            streaks: List[List[date]] = []
            active: List[date] = [ordered_dates[0]]
            for next_date in ordered_dates[1:]:
                if self._is_consecutive_signal(active[-1], next_date):
                    active.append(next_date)
                else:
                    streaks.append(active)
                    active = [next_date]
            streaks.append(active)
            longest = max(streaks, key=len)

            latest_row = latest_by_code.get(code_value, {})
            metrics = self._to_dict(latest_row.get("metrics_payload"))
            cause = self._to_dict(latest_row.get("cause_payload"))
            latest_signal_day = self._coerce_date(latest_row.get("signal_date")) or ordered_dates[0]
            leaderboard.append(
                {
                    "code": code_value,
                    "name": str(latest_row.get("name") or grouped_names.get(code_value) or "").strip() or None,
                    "industry": str(cause.get("industry", "") or "").strip() or None,
                    "current_streak_count": len(current_streak),
                    "longest_streak_count": len(longest),
                    "current_streak_start_date": current_streak[-1].isoformat(),
                    "current_streak_end_date": current_streak[0].isoformat(),
                    "latest_signal_date": latest_signal_day.isoformat(),
                    "latest_high": self._to_float(metrics.get("latest_high")),
                    "close": self._to_float(metrics.get("close")),
                    "theme_label": str(cause.get("theme_label", "") or "").strip() or None,
                }
            )

        leaderboard.sort(
            key=lambda item: (
                -(item.get("current_streak_count") or 0),
                -(item.get("longest_streak_count") or 0),
                item.get("latest_signal_date") or "",
                item.get("code") or "",
            ),
        )
        return leaderboard[:10]

    def _build_streak_leaderboard_from_streak_rows(self, rows: List[Any]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Any]] = {}
        for row in rows:
            code_value = str(getattr(row, "code", "") or "").strip()
            if not code_value:
                continue
            grouped.setdefault(code_value, []).append(row)

        leaderboard: List[Dict[str, Any]] = []
        for code_value, code_rows in grouped.items():
            ordered_rows = sorted(
                code_rows,
                key=lambda row: self._coerce_date(getattr(row, "signal_date", None)) or date.min,
                reverse=True,
            )
            ordered_dates = [
                self._coerce_date(getattr(row, "signal_date", None))
                for row in ordered_rows
                if self._coerce_date(getattr(row, "signal_date", None)) is not None
            ]
            if not ordered_dates:
                continue

            current_streak = [ordered_dates[0]]
            for next_date in ordered_dates[1:]:
                if self._is_consecutive_signal(current_streak[-1], next_date):
                    current_streak.append(next_date)
                else:
                    break

            streaks: List[List[date]] = []
            active: List[date] = [ordered_dates[0]]
            for next_date in ordered_dates[1:]:
                if self._is_consecutive_signal(active[-1], next_date):
                    active.append(next_date)
                else:
                    streaks.append(active)
                    active = [next_date]
            streaks.append(active)
            longest = max(streaks, key=len)
            latest_row = ordered_rows[0]
            leaderboard.append(
                {
                    "code": code_value,
                    "name": getattr(latest_row, "name", None),
                    "industry": getattr(latest_row, "industry", None),
                    "current_streak_count": len(current_streak),
                    "longest_streak_count": len(longest),
                    "current_streak_start_date": current_streak[-1].isoformat(),
                    "current_streak_end_date": current_streak[0].isoformat(),
                    "latest_signal_date": getattr(latest_row, "signal_date", None).isoformat() if getattr(latest_row, "signal_date", None) else None,
                    "latest_high": self._to_float(getattr(latest_row, "latest_high", None)),
                    "close": self._to_float(getattr(latest_row, "close", None)),
                    "theme_label": getattr(latest_row, "theme_label", None),
                }
            )

        leaderboard.sort(
            key=lambda item: (
                -(item.get("current_streak_count") or 0),
                -(item.get("longest_streak_count") or 0),
                item.get("latest_signal_date") or "",
                item.get("code") or "",
            ),
        )
        return leaderboard[:10]

    def _coerce_list_json(self, payload: Any) -> List[str]:
        parsed = parse_json_field(payload)
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def _coerce_dict_json(self, payload: Any) -> Dict[str, Any]:
        parsed = parse_json_field(payload)
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _pct_from_peak(anchor_close: Optional[float], peak: Optional[float]) -> Optional[float]:
        if anchor_close is None or peak is None or peak <= 0:
            return None
        return round((anchor_close - peak) / peak * 100, 2)
