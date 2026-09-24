# -*- coding: utf-8 -*-
"""Long-term position and lightweight valuation helpers for review overlays."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional

import pandas as pd


CYCLICAL_CATALYST_TYPES = {
    "resource_price_cycle",
    "shipping_cycle",
    "lithium_battery_materials_recovery",
}


@dataclass(frozen=True)
class LongTermPositionMetrics:
    status: str
    label: str
    reason: str
    latest_trade_date: Optional[str] = None
    latest_close: Optional[float] = None
    price_position_1y_pct: Optional[float] = None
    price_position_2y_pct: Optional[float] = None
    return_120d_pct: Optional[float] = None
    return_1y_pct: Optional[float] = None
    return_2y_pct: Optional[float] = None
    max_drawdown_1y_pct: Optional[float] = None
    ma120: Optional[float] = None
    ma250: Optional[float] = None
    above_ma120: Optional[bool] = None
    above_ma250: Optional[bool] = None
    monthly_positive_ratio_12m: Optional[float] = None
    monthly_above_ma6: Optional[bool] = None
    monthly_above_ma12: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "long_term_position_status": self.status,
            "long_term_position_label": self.label,
            "long_term_position_reason": self.reason,
            "latest_trade_date": self.latest_trade_date,
            "latest_close": self.latest_close,
            "price_position_1y_pct": self.price_position_1y_pct,
            "price_position_2y_pct": self.price_position_2y_pct,
            "return_120d_pct": self.return_120d_pct,
            "return_1y_pct": self.return_1y_pct,
            "return_2y_pct": self.return_2y_pct,
            "max_drawdown_1y_pct": self.max_drawdown_1y_pct,
            "ma120": self.ma120,
            "ma250": self.ma250,
            "above_ma120": self.above_ma120,
            "above_ma250": self.above_ma250,
            "monthly_positive_ratio_12m": self.monthly_positive_ratio_12m,
            "monthly_above_ma6": self.monthly_above_ma6,
            "monthly_above_ma12": self.monthly_above_ma12,
        }


def compute_long_term_position(
    history_df: pd.DataFrame,
    *,
    as_of_date: Optional[Any] = None,
    min_rows: int = 120,
) -> LongTermPositionMetrics:
    """Classify a stock's 1-2 year chart position from daily history."""
    prepared = _prepare_history(history_df, as_of_date=as_of_date)
    if prepared.empty:
        return LongTermPositionMetrics(
            status="history_missing",
            label="历史不足",
            reason="没有可用日线缓存，无法判断月线/年线位置。",
        )
    if len(prepared) < min_rows:
        return LongTermPositionMetrics(
            status="insufficient_history",
            label="历史不足",
            reason=f"可用日线只有 {len(prepared)} 根，低于 {min_rows} 根。",
            latest_trade_date=_format_latest_date(prepared),
            latest_close=_round_float(prepared["close"].iloc[-1]),
        )

    close = prepared["close"].astype(float)
    latest_close = float(close.iloc[-1])
    latest_trade_date = _format_latest_date(prepared)
    close_120 = close.tail(min(120, len(close)))
    close_1y = close.tail(min(252, len(close)))
    close_2y = close.tail(min(504, len(close)))

    position_1y = _percentile_rank(close_1y, latest_close)
    position_2y = _percentile_rank(close_2y, latest_close)
    return_120d = _return_pct(close_120.iloc[0], latest_close) if len(close_120) >= 2 else None
    return_1y = _return_pct(close_1y.iloc[0], latest_close) if len(close_1y) >= 2 else None
    return_2y = _return_pct(close_2y.iloc[0], latest_close) if len(close_2y) >= 2 else None
    max_drawdown_1y = _max_drawdown_pct(close_1y)

    ma120 = _rolling_mean(close, 120)
    ma250 = _rolling_mean(close, 250)
    above_ma120 = latest_close >= ma120 if ma120 is not None else None
    above_ma250 = latest_close >= ma250 if ma250 is not None else None

    monthly = _monthly_close(prepared)
    monthly_positive_ratio_12m = _monthly_positive_ratio(monthly, 12)
    monthly_above_ma6 = _monthly_above_ma(monthly, 6)
    monthly_above_ma12 = _monthly_above_ma(monthly, 12)

    status, label = _classify_position(
        position_2y=position_2y,
        return_1y=return_1y,
        return_120d=return_120d,
        above_ma120=above_ma120,
        above_ma250=above_ma250,
        monthly_above_ma6=monthly_above_ma6,
        monthly_above_ma12=monthly_above_ma12,
    )
    reason = _build_position_reason(
        position_2y=position_2y,
        return_1y=return_1y,
        return_2y=return_2y,
        max_drawdown_1y=max_drawdown_1y,
        above_ma120=above_ma120,
        above_ma250=above_ma250,
        monthly_positive_ratio_12m=monthly_positive_ratio_12m,
        monthly_above_ma6=monthly_above_ma6,
        monthly_above_ma12=monthly_above_ma12,
    )

    return LongTermPositionMetrics(
        status=status,
        label=label,
        reason=reason,
        latest_trade_date=latest_trade_date,
        latest_close=_round_float(latest_close),
        price_position_1y_pct=_round_float(position_1y),
        price_position_2y_pct=_round_float(position_2y),
        return_120d_pct=_round_float(return_120d),
        return_1y_pct=_round_float(return_1y),
        return_2y_pct=_round_float(return_2y),
        max_drawdown_1y_pct=_round_float(max_drawdown_1y),
        ma120=_round_float(ma120),
        ma250=_round_float(ma250),
        above_ma120=above_ma120,
        above_ma250=above_ma250,
        monthly_positive_ratio_12m=_round_float(monthly_positive_ratio_12m),
        monthly_above_ma6=monthly_above_ma6,
        monthly_above_ma12=monthly_above_ma12,
    )


def classify_lightweight_valuation(
    *,
    pe_ratio: Optional[Any] = None,
    pb_ratio: Optional[Any] = None,
    total_market_cap_yi: Optional[Any] = None,
    cycle_catalyst_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a conservative valuation label from sparse review fields."""
    pe = _safe_float(pe_ratio)
    pb = _safe_float(pb_ratio)
    market_cap = _safe_float(total_market_cap_yi)
    catalyst_type = str(cycle_catalyst_type or "").strip()
    is_cyclical = catalyst_type in CYCLICAL_CATALYST_TYPES

    parts = []
    if market_cap is not None:
        parts.append(f"总市值 {market_cap:.2f} 亿")
    if pe is not None and pe > 0:
        parts.append(f"PE {pe:.1f}")
    elif pe is not None and pe <= 0:
        parts.append("PE 为负/无意义")
    if pb is not None and pb > 0:
        parts.append(f"PB {pb:.2f}")

    if is_cyclical and pb is None:
        return {
            "valuation_status": "needs_pb_for_cycle",
            "valuation_label": "周期股需补PB/周期位置",
            "valuation_reason": "资源、航运、锂电材料等周期股只看 PE 容易误判，需要 PB、商品/运价周期和历史分位一起看。"
            + ("；" + " / ".join(parts) if parts else ""),
            "pe_ratio": pe,
            "pb_ratio": pb,
            "total_market_cap_yi": market_cap,
        }

    if pe is None and pb is None:
        return {
            "valuation_status": "data_missing",
            "valuation_label": "估值数据不足",
            "valuation_reason": "缺少 PE/PB，不能判断估值高低。" + (f"；{parts[0]}" if parts else ""),
            "pe_ratio": pe,
            "pb_ratio": pb,
            "total_market_cap_yi": market_cap,
        }

    if pb is not None and pb > 0:
        status, label = _classify_pb(pb, is_cyclical=is_cyclical)
    elif pe is not None and pe > 0:
        status, label = _classify_pe(pe)
    else:
        status, label = "data_not_meaningful", "估值口径无意义"

    return {
        "valuation_status": status,
        "valuation_label": label,
        "valuation_reason": " / ".join(parts) if parts else "估值字段有限，仅作粗读。",
        "pe_ratio": pe,
        "pb_ratio": pb,
        "total_market_cap_yi": market_cap,
    }


def _prepare_history(history_df: pd.DataFrame, *, as_of_date: Optional[Any]) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame()
    if "date" not in history_df.columns or "close" not in history_df.columns:
        return pd.DataFrame()
    df = history_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df = df[df["close"] > 0]
    if as_of_date is not None:
        cutoff = _coerce_timestamp(as_of_date)
        if cutoff is not None:
            df = df[df["date"] <= cutoff]
    return df.reset_index(drop=True)


def _coerce_timestamp(value: Any) -> Optional[pd.Timestamp]:
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, datetime):
        return pd.Timestamp(value.date())
    if isinstance(value, date):
        return pd.Timestamp(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return pd.Timestamp(text)
    except Exception:
        return None


def _format_latest_date(df: pd.DataFrame) -> Optional[str]:
    if df.empty:
        return None
    value = df["date"].iloc[-1]
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return None


def _percentile_rank(series: pd.Series, value: float) -> Optional[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((clean <= value).mean() * 100.0)


def _return_pct(start: Any, end: Any) -> Optional[float]:
    start_float = _safe_float(start)
    end_float = _safe_float(end)
    if start_float is None or end_float is None or start_float <= 0:
        return None
    return (end_float / start_float - 1.0) * 100.0


def _max_drawdown_pct(series: pd.Series) -> Optional[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    rolling_peak = clean.cummax()
    drawdowns = clean / rolling_peak - 1.0
    return abs(float(drawdowns.min() * 100.0))


def _rolling_mean(series: pd.Series, window: int) -> Optional[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < window:
        return None
    return float(clean.tail(window).mean())


def _monthly_close(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    try:
        monthly = df.set_index("date")["close"].resample("ME").last().dropna()
    except Exception:
        monthly = df.set_index("date")["close"].resample("M").last().dropna()
    return monthly


def _monthly_positive_ratio(monthly: pd.Series, lookback: int) -> Optional[float]:
    if len(monthly) < 3:
        return None
    returns = monthly.pct_change().dropna().tail(lookback)
    if returns.empty:
        return None
    return float((returns > 0).mean() * 100.0)


def _monthly_above_ma(monthly: pd.Series, window: int) -> Optional[bool]:
    if len(monthly) < window:
        return None
    ma = float(monthly.tail(window).mean())
    return float(monthly.iloc[-1]) >= ma


def _classify_position(
    *,
    position_2y: Optional[float],
    return_1y: Optional[float],
    return_120d: Optional[float],
    above_ma120: Optional[bool],
    above_ma250: Optional[bool],
    monthly_above_ma6: Optional[bool],
    monthly_above_ma12: Optional[bool],
) -> tuple[str, str]:
    pos = position_2y if position_2y is not None else 50.0
    ret1 = return_1y if return_1y is not None else 0.0
    ret120 = return_120d if return_120d is not None else 0.0
    above_long = bool(above_ma250) if above_ma250 is not None else bool(above_ma120)
    monthly_ok = bool(monthly_above_ma6) and (monthly_above_ma12 is not False)

    if pos >= 85 and ret1 >= 30 and above_long and monthly_ok:
        return "high_position_uptrend", "高位强趋势"
    if pos >= 60 and ret1 >= 10 and above_long:
        return "long_term_uptrend", "长期上行"
    if pos <= 35 and ret120 >= 8 and above_ma120:
        return "low_position_recovery", "低位修复"
    if pos <= 35 and ret1 <= 0 and not above_long:
        return "long_term_weak", "长期走弱"
    return "mid_position_mixed", "中位震荡/待确认"


def _build_position_reason(
    *,
    position_2y: Optional[float],
    return_1y: Optional[float],
    return_2y: Optional[float],
    max_drawdown_1y: Optional[float],
    above_ma120: Optional[bool],
    above_ma250: Optional[bool],
    monthly_positive_ratio_12m: Optional[float],
    monthly_above_ma6: Optional[bool],
    monthly_above_ma12: Optional[bool],
) -> str:
    parts = []
    if position_2y is not None:
        parts.append(f"2年价格分位 {position_2y:.1f}%")
    if return_1y is not None:
        parts.append(f"1年涨幅 {return_1y:.1f}%")
    if return_2y is not None:
        parts.append(f"2年涨幅 {return_2y:.1f}%")
    if max_drawdown_1y is not None:
        parts.append(f"1年最大回撤 {max_drawdown_1y:.1f}%")
    if above_ma120 is not None:
        parts.append("站上MA120" if above_ma120 else "未站上MA120")
    if above_ma250 is not None:
        parts.append("站上MA250" if above_ma250 else "未站上MA250")
    if monthly_positive_ratio_12m is not None:
        parts.append(f"近12个月上涨月占比 {monthly_positive_ratio_12m:.1f}%")
    if monthly_above_ma6 is not None:
        parts.append("月线在6月均线上" if monthly_above_ma6 else "月线低于6月均线")
    if monthly_above_ma12 is not None:
        parts.append("月线在12月均线上" if monthly_above_ma12 else "月线低于12月均线")
    return "；".join(parts) if parts else "长期位置数据不足。"


def _classify_pb(pb: float, *, is_cyclical: bool) -> tuple[str, str]:
    if is_cyclical:
        if pb <= 1.5:
            return "pb_low", "PB偏低"
        if pb <= 2.8:
            return "pb_neutral", "PB中性"
        return "pb_high", "PB偏高"
    if pb <= 2.0:
        return "pb_low", "PB偏低"
    if pb <= 5.0:
        return "pb_neutral", "PB中性"
    return "pb_high", "PB偏高"


def _classify_pe(pe: float) -> tuple[str, str]:
    if pe <= 15:
        return "pe_low", "PE偏低"
    if pe <= 40:
        return "pe_neutral", "PE中性"
    if pe <= 80:
        return "pe_elevated", "PE偏高"
    return "pe_high", "PE很高"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "--"}:
        return None
    try:
        result = float(text)
    except Exception:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _round_float(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except Exception:
        return None
    return round(float(value), digits)
