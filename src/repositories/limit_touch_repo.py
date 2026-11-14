# -*- coding: utf-8 -*-
"""触板事件数据访问层（涨停封板 / 炸板）。

用于长期累积「近 N 个交易日试盘次数」等资金态度指标。
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, func, select

from src.storage import DatabaseManager, LimitTouchEvent

logger = logging.getLogger(__name__)


def _as_date(value: Any) -> date_type:
    """字符串 / date 统一成 date（SQLite Date 列只接受 date 对象）。"""
    if isinstance(value, date_type):
        return value
    return date_type.fromisoformat(str(value)[:10])


def is_excluded(code: str, name: Optional[str] = None) -> bool:
    """过滤北交所（4/8/920 开头）与 ST / 退市股。"""
    text = str(code or "").zfill(6)
    if text.startswith(("4", "8", "920")):
        return True
    label = str(name or "").upper()
    return "ST" in label or "退" in label


class LimitTouchRepository:
    """limit_touch_event 表读写。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def replace_day(self, trade_date: str, rows: Iterable[Dict[str, Any]]) -> int:
        """按日整体替换（幂等）。返回写入行数。"""
        day = _as_date(trade_date)
        payload = []
        for row in rows:
            if is_excluded(row.get("code"), row.get("name")):
                continue
            payload.append(
                {
                    "code": str(row["code"]).zfill(6),
                    "name": (row.get("name") or "")[:32] or None,
                    "trade_date": day,
                    "kind": row.get("kind") or "break",
                    "industry": (row.get("industry") or None),
                    "change_pct": row.get("change_pct"),
                    "close_price": row.get("close_price"),
                    "limit_price": row.get("limit_price"),
                    "dist_limit": row.get("dist_limit"),
                    "break_count": row.get("break_count"),
                    "first_seal": row.get("first_seal"),
                    "boards": row.get("boards"),
                    "limit_stat": row.get("limit_stat"),
                    "amount": row.get("amount"),
                    "turnover": row.get("turnover"),
                    "amplitude": row.get("amplitude"),
                    "seal_amount": row.get("seal_amount"),
                    "float_mv": row.get("float_mv"),
                    "source": row.get("source"),
                }
            )
        if not payload:
            return 0
        with self.db.session_scope() as session:
            session.execute(
                delete(LimitTouchEvent).where(LimitTouchEvent.trade_date == day)
            )
            session.bulk_insert_mappings(LimitTouchEvent, payload)
        return len(payload)

    def touch_counts(
        self,
        codes: Iterable[str],
        as_of: str,
        window: int = 10,
    ) -> Dict[str, Dict[str, int]]:
        """近 window 个交易日（不含 as_of 当日）的触板统计。

        返回 {code: {"touch": 天数, "seal": 封板天数, "break": 炸板天数}}。
        """
        code_list = sorted({str(c).zfill(6) for c in codes})
        if not code_list:
            return {}
        cutoff = _as_date(as_of)
        with self.db.session_scope() as session:
            days = session.execute(
                select(LimitTouchEvent.trade_date)
                .where(LimitTouchEvent.trade_date < cutoff)
                .distinct()
                .order_by(LimitTouchEvent.trade_date.desc())
                .limit(window)
            ).scalars().all()
            if not days:
                return {}
            window_days = [d for d in days]
            rows = session.execute(
                select(
                    LimitTouchEvent.code,
                    LimitTouchEvent.trade_date,
                    LimitTouchEvent.kind,
                ).where(
                    LimitTouchEvent.code.in_(code_list),
                    LimitTouchEvent.trade_date.in_(window_days),
                )
            ).all()
        result: Dict[str, Dict[str, int]] = {}
        for code, _day, kind in rows:
            bucket = result.setdefault(code, {"touch": 0, "seal": 0, "break": 0})
            bucket["touch"] += 1
            if kind == "seal":
                bucket["seal"] += 1
            else:
                bucket["break"] += 1
        return result

    def industry_seal_counts(self, trade_date: str) -> Dict[str, int]:
        """某日各行业封板家数（判断「板块过热 = 兑现」用）。"""
        day = _as_date(trade_date)
        with self.db.session_scope() as session:
            rows = session.execute(
                select(LimitTouchEvent.industry, func.count())
                .where(
                    LimitTouchEvent.trade_date == day,
                    LimitTouchEvent.kind == "seal",
                )
                .group_by(LimitTouchEvent.industry)
            ).all()
        return {str(industry): int(count) for industry, count in rows if industry}

    def touch_ranking(
        self,
        as_of: str,
        window: int = 10,
        min_touch: int = 2,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """近 window 个交易日触板次数 ≥ min_touch 的票（含最近一次状态）。"""
        cutoff = _as_date(as_of)
        with self.db.session_scope() as session:
            days = [
                d
                for d in session.execute(
                    select(LimitTouchEvent.trade_date)
                    .where(LimitTouchEvent.trade_date < cutoff)
                    .distinct()
                    .order_by(LimitTouchEvent.trade_date.desc())
                    .limit(window)
                ).scalars().all()
            ]
            if not days:
                return []
            rows = session.execute(
                select(
                    LimitTouchEvent.code,
                    LimitTouchEvent.name,
                    LimitTouchEvent.industry,
                    LimitTouchEvent.trade_date,
                    LimitTouchEvent.kind,
                )
                .where(LimitTouchEvent.trade_date.in_(days))
                .order_by(LimitTouchEvent.trade_date)
            ).all()
        grouped: Dict[str, Dict[str, Any]] = {}
        for code, name, industry, trade_day, kind in rows:
            item = grouped.setdefault(
                str(code),
                {
                    "code": str(code),
                    "name": name,
                    "industry": industry,
                    "touch": 0,
                    "seal": 0,
                    "break": 0,
                    "last_date": None,
                    "last_kind": None,
                },
            )
            item["touch"] += 1
            if kind == "seal":
                item["seal"] += 1
            else:
                item["break"] += 1
            day_text = str(trade_day)[:10]
            if item["last_date"] is None or day_text >= item["last_date"]:
                item["last_date"] = day_text
                item["last_kind"] = kind
                item["industry"] = industry or item["industry"]
        result = [v for v in grouped.values() if v["touch"] >= min_touch]
        for item in result:
            item["seal_rate"] = round(item["seal"] / item["touch"], 3) if item["touch"] else None
        result.sort(key=lambda x: (-x["touch"], -(x["seal"] or 0), x["code"]))
        return result[:limit]

    def counts_by_day(self, as_of: str) -> Dict[str, int]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(LimitTouchEvent.trade_date, func.count())
                .group_by(LimitTouchEvent.trade_date)
                .order_by(LimitTouchEvent.trade_date.desc())
                .limit(30)
            ).all()
        return {str(day)[:10]: int(count) for day, count in rows}

    def latest_date(self) -> Optional[str]:
        with self.db.session_scope() as session:
            value = session.execute(
                select(func.max(LimitTouchEvent.trade_date))
            ).scalar()
        return str(value)[:10] if value else None


def touch_counts(codes: Iterable[str], as_of: str, window: int = 10) -> Dict[str, Dict[str, int]]:
    """模块级快捷入口。"""
    return LimitTouchRepository().touch_counts(codes, as_of, window)


__all__ = ["LimitTouchRepository", "is_excluded", "touch_counts", "date_type"]
