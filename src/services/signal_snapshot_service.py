# -*- coding: utf-8 -*-
"""
Signal snapshot query service.

Provides query/read models for persisted K-line signal snapshots such as
``hundred_day_high``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager
from src.utils.data_processing import parse_json_field


class SignalSnapshotService:
    """Read/query service for K-line signal snapshots."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()

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
        items = [self._row_to_list_item(row) for row in rows]
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
                projection_rows = get_projection_rows(include_history_payload=True)
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
                projection_rows = get_projection_rows(include_history_payload=True)
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
        items = [self._row_to_history_item(row) for row in rows]
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
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _row_to_list_item(self, row: Any) -> Dict[str, Any]:
        metrics = self._to_dict(getattr(row, "metrics_payload", None))
        cause = self._to_dict(getattr(row, "cause_payload", None))
        history = self._to_dict(getattr(row, "history_payload", None))

        return {
            "code": getattr(row, "code", ""),
            "name": getattr(row, "name", ""),
            "signal_date": row.signal_date.isoformat() if getattr(row, "signal_date", None) else None,
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
        }

    def _row_to_history_item(self, row: Any) -> Dict[str, Any]:
        base = self._row_to_list_item(row)
        metrics = self._to_dict(getattr(row, "metrics_payload", None))
        base.update(
            {
                "new_high_window": metrics.get("new_high_window"),
                "history_source": metrics.get("history_source"),
                "total_market_cap": self._to_float(metrics.get("total_market_cap")),
            }
        )
        return base

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
        return {
            "code": row.get("code"),
            "name": row.get("name"),
            "signal_date": row.get("signal_date").isoformat() if row.get("signal_date") else None,
            "is_consecutive_signal": self._is_consecutive_item(history),
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
                },
            )
            bucket["total_count"] += 1
            if item.get("is_consecutive_signal"):
                bucket["continuous_count"] += 1
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
