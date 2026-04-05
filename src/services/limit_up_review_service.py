# -*- coding: utf-8 -*-
"""Limit-up review service for CN market recap."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from data_provider.base import is_bse_code, is_kc_cy_stock, is_st_stock, normalize_stock_code

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY_LIMIT = 20
DEFAULT_HISTORY_LOOKBACK_DAYS = 250


def _to_sina_symbol(stock_code: str) -> str:
    base = normalize_stock_code(stock_code)
    if is_bse_code(base):
        return f"bj{base}"
    if base.startswith(("6", "5", "90")):
        return f"sh{base}"
    return f"sz{base}"


def _limit_up_ratio(stock_code: str, stock_name: str) -> float:
    normalized = normalize_stock_code(stock_code)
    if is_bse_code(normalized):
        return 0.30
    if is_kc_cy_stock(normalized):
        return 0.20
    if is_st_stock(stock_name):
        return 0.05
    return 0.10


class LimitUpReviewService:
    """Build structured CN limit-up review rows and markdown output."""

    def __init__(
        self,
        display_limit: int = DEFAULT_DISPLAY_LIMIT,
        history_lookback_days: int = DEFAULT_HISTORY_LOOKBACK_DAYS,
    ) -> None:
        self.display_limit = max(1, int(display_limit))
        self.history_lookback_days = max(1, int(history_lookback_days))

    def get_review_rows(self, date: Optional[str] = None) -> List[Dict[str, Any]]:
        import akshare as ak

        trade_date = (date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        limit_up_df = ak.stock_zt_pool_em(date=trade_date)
        if limit_up_df is None or limit_up_df.empty:
            return []

        reason_map = self._fetch_reason_map(ak, trade_date)
        review_df = self._normalize_review_df(limit_up_df, reason_map)
        review_df = review_df.sort_values(
            by=["连板数", "封板资金", "代码"],
            ascending=[False, False, True],
        ).head(self.display_limit)

        rows: List[Dict[str, Any]] = []
        for _, row in review_df.iterrows():
            stock_code = str(row["代码"])
            stock_name = str(row["名称"])
            rows.append(
                {
                    "code": stock_code,
                    "name": stock_name,
                    "board_count": int(row["连板数"]),
                    "sealed_amount_yi": float(row["封板资金(亿)"]) if pd.notna(row["封板资金(亿)"]) else 0.0,
                    "limit_up_stats": str(row["涨停统计"] or "-"),
                    "reason": str(row["涨停原因"] or "未进强势股池"),
                    "historical_limit_up_count": self._count_recent_limit_up_days(
                        ak=ak,
                        stock_code=stock_code,
                        stock_name=stock_name,
                    ),
                    "industry": str(row["所属行业"] or "-"),
                }
            )
        return rows

    def build_markdown_block(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return ""

        lines = [
            "### 八、今日涨停股复盘表",
            "",
            f"> 按连板数、封板资金排序展示前 {len(rows)} 只涨停股；历史涨停次数按近 {self.history_lookback_days} 个交易日统计。",
            "",
            "| 代码 | 名称 | 连板 | 封板资金(亿) | 涨停统计 | 涨停原因 | 历史涨停次数 | 所属行业 |",
            "|------|------|-----:|------------:|----------|----------|---------------:|----------|",
        ]
        for row in rows:
            history_count = row.get("historical_limit_up_count")
            history_text = str(history_count) if history_count is not None else "N/A"
            lines.append(
                "| {code} | {name} | {board_count} | {sealed_amount_yi:.2f} | {limit_up_stats} | {reason} | {history_text} | {industry} |".format(
                    code=row.get("code", "-"),
                    name=row.get("name", "-"),
                    board_count=row.get("board_count", 0),
                    sealed_amount_yi=float(row.get("sealed_amount_yi", 0.0) or 0.0),
                    limit_up_stats=row.get("limit_up_stats", "-"),
                    reason=row.get("reason", "-"),
                    history_text=history_text,
                    industry=row.get("industry", "-"),
                )
            )
        return "\n".join(lines)

    def _fetch_reason_map(self, ak: Any, trade_date: str) -> Dict[str, str]:
        try:
            strong_df = ak.stock_zt_pool_strong_em(date=trade_date)
        except Exception as exc:
            logger.warning("[LimitUpReviewService] 获取涨停原因失败，继续输出基础涨停表: %s", exc)
            return {}

        if strong_df is None or strong_df.empty or "代码" not in strong_df.columns:
            return {}

        strong_df = strong_df.copy()
        strong_df["代码"] = strong_df["代码"].astype(str).str.zfill(6)
        strong_df = strong_df.drop_duplicates(subset=["代码"], keep="first")
        if "入选理由" not in strong_df.columns:
            return {}
        return strong_df.set_index("代码")["入选理由"].fillna("").astype(str).to_dict()

    @staticmethod
    def _normalize_review_df(limit_up_df: pd.DataFrame, reason_map: Dict[str, str]) -> pd.DataFrame:
        review_df = limit_up_df.copy()
        review_df["代码"] = review_df["代码"].astype(str).str.zfill(6)
        review_df["连板数"] = pd.to_numeric(review_df.get("连板数"), errors="coerce").fillna(0).astype(int)
        review_df["封板资金"] = pd.to_numeric(review_df.get("封板资金"), errors="coerce").fillna(0.0)
        review_df["涨停原因"] = review_df["代码"].map(reason_map).fillna("未进强势股池")
        review_df["涨停统计"] = (
            review_df["涨停统计"].fillna("").astype(str)
            if "涨停统计" in review_df.columns
            else ""
        )
        review_df["所属行业"] = (
            review_df["所属行业"].fillna("").astype(str)
            if "所属行业" in review_df.columns
            else ""
        )
        review_df["封板资金(亿)"] = review_df["封板资金"] / 1e8
        return review_df

    def _count_recent_limit_up_days(self, ak: Any, stock_code: str, stock_name: str) -> Optional[int]:
        symbol = _to_sina_symbol(stock_code)
        try:
            history_df = ak.stock_zh_a_daily(symbol=symbol, adjust="")
        except Exception as first_exc:
            logger.debug(
                "[LimitUpReviewService] stock_zh_a_daily(%s) 失败，回退 EM 历史接口: %s",
                symbol,
                first_exc,
            )
            try:
                start_date = (
                    datetime.now() - timedelta(days=max(self.history_lookback_days * 2, 520))
                ).strftime("%Y%m%d")
                end_date = datetime.now().strftime("%Y%m%d")
                history_df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
            except Exception as second_exc:
                logger.warning(
                    "[LimitUpReviewService] 统计 %s 历史涨停次数失败: %s",
                    stock_code,
                    second_exc,
                )
                return None

        if history_df is None or history_df.empty:
            return None

        close_col = "close" if "close" in history_df.columns else ("收盘" if "收盘" in history_df.columns else None)
        if close_col is None:
            return None

        df = history_df.copy()
        df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
        df = df.dropna(subset=[close_col]).tail(self.history_lookback_days + 1)
        if len(df) <= 1:
            return 0

        ratio = _limit_up_ratio(stock_code, stock_name)
        prev_close = df[close_col].shift(1)
        theoretical = prev_close * (1 + ratio)
        limit_up_price = (theoretical * 100 + 0.5).floordiv(1) / 100.0
        tolerance = (theoretical - limit_up_price).abs().fillna(0.0).clip(lower=1e-6)
        is_limit_up = (
            prev_close.notna()
            & (df[close_col] > 0)
            & ((df[close_col] - limit_up_price).abs() <= tolerance)
        )
        return int(is_limit_up.tail(self.history_lookback_days).sum())
