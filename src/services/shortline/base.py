# -*- coding: utf-8 -*-
"""短线方法共用的行情/行业指标与候选构造（只在服务层计算，页面不重算）。"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.services.shortline import registry


def _f(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@lru_cache(maxsize=4)
def load_bars(as_of: str, lookback_days: int = 200) -> pd.DataFrame:
    """长表：code/date/open/high/low/close/volume/amount（按日期升序）。"""
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        rows = session.execute(
            text(
                "SELECT code, date, open, high, low, close, volume, amount "
                "FROM stock_daily WHERE date <= :as_of AND date >= date(:as_of, '-400 day') "
                "ORDER BY code, date"
            ),
            {"as_of": as_of},
        ).fetchall()
    frame = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume", "amount"])
    if frame.empty:
        return frame
    frame["date"] = frame["date"].astype(str).str[:10]
    return frame.groupby("code", sort=False).tail(lookback_days).reset_index(drop=True)


@lru_cache(maxsize=4)
def metrics(as_of: str) -> pd.DataFrame:
    """每只票的结构指标（index=code）；纯本地日线，无外部依赖。"""
    frame = load_bars(as_of)
    if frame.empty:
        return pd.DataFrame()
    grouped = frame.groupby("code", sort=False)
    tail = frame.tail(0)  # 占位，保持类型
    del tail
    wide_close = frame.pivot(index="date", columns="code", values="close").sort_index()
    wide_high = frame.pivot(index="date", columns="code", values="high").sort_index()
    wide_low = frame.pivot(index="date", columns="code", values="low").sort_index()
    wide_vol = frame.pivot(index="date", columns="code", values="volume").sort_index()
    wide_amt = frame.pivot(index="date", columns="code", values="amount").sort_index()

    close = wide_close.ffill()
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    last = close.iloc[-1]
    # ret1 必须用未 ffill 的原始收盘：停牌/缺当日 bar 的票今日涨幅应为空，而不是 0
    prev_traded = close.ffill().iloc[-2] if len(close) > 1 else pd.Series(dtype=float)
    ret1 = wide_close.iloc[-1] / prev_traded - 1 if len(close) > 1 else pd.Series(dtype=float)
    ret5 = last / close.iloc[-6] - 1 if len(close) > 6 else pd.Series(dtype=float)
    ret20 = last / close.iloc[-21] - 1 if len(close) > 21 else pd.Series(dtype=float)
    ret60 = last / close.iloc[-61] - 1 if len(close) > 61 else pd.Series(dtype=float)
    high120 = wide_high.tail(120).max()
    low60 = wide_low.tail(60).min()
    low10 = wide_low.tail(10).min()
    atr = (wide_high - wide_low).tail(20).mean()
    vol5 = wide_vol.tail(5).mean()
    vol20 = wide_vol.tail(20).mean()
    amt20 = wide_amt.tail(20).mean()

    out = pd.DataFrame(
        {
            "close": last,
            "ma20": ma20,
            "ma60": ma60,
            "ma20_prev5": close.rolling(20).mean().iloc[-6] if len(close) > 6 else float("nan"),
            "ret1": ret1,
            "ret5": ret5,
            "ret20": ret20,
            "ret60": ret60,
            "high120": high120,
            "low60": low60,
            "low10": low10,
            "atr_pct": (atr / last),
            "vol_ratio": (vol5 / vol20).replace([float("inf")], float("nan")),
            "amt20_wan": amt20 / 1e4,
            "bars": grouped.size(),
        }
    )
    out["drawdown_high120"] = out["close"] / out["high120"] - 1
    out["dist_ma20"] = out["close"] / out["ma20"] - 1
    out["market_median_ret20"] = float(out["ret20"].median())
    out["excess20_pp"] = (out["ret20"] - out["market_median_ret20"]) * 100
    return out.dropna(subset=["close", "ma20", "ma60", "ret20", "high120", "low60"])


@lru_cache(maxsize=4)
def industry_map(as_of: str) -> Dict[str, Dict[str, str]]:
    """code -> {name, industry}；来自 tushare_stock_basic 快照（行业分类不是行情，允许跨日复用）。"""
    from src.services.profile_card_service import _basic_map

    return _basic_map()


@lru_cache(maxsize=4)
def industry_strength(as_of: str) -> Dict[str, Dict[str, float]]:
    """行业 -> {median_ret20, pct, peers}；pct 为行业在全市场行业中的分位。"""
    table = metrics(as_of)
    if table.empty:
        return {}
    basic = industry_map(as_of)
    buckets: Dict[str, List[float]] = {}
    for code, row in table.iterrows():
        industry = (basic.get(code) or {}).get("industry")
        if not industry:
            continue
        buckets.setdefault(industry, []).append(float(row["ret20"]))
    medians = {name: sorted(vals)[len(vals) // 2] for name, vals in buckets.items() if vals}
    ordered = sorted(medians.values())
    out: Dict[str, Dict[str, float]] = {}
    for name, median in medians.items():
        rank = sum(1 for v in ordered if v < median)
        out[name] = {
            "median_ret20": median,
            "pct": round(rank / len(ordered) * 100, 1) if ordered else 0.0,
            "peers": len(buckets[name]),
        }
    return out


def universe(as_of: str, method: Dict[str, Any]) -> pd.DataFrame:
    """应用方法级过滤（行业排除 + 流动性 + ST/退市）后的候选池。"""
    table = metrics(as_of)
    if table.empty:
        return table
    cfg = registry.defaults()
    excluded = set(registry.excluded_industries(method))
    min_amt = float((method.get("params") or {}).get("min_avg_amount_wan")
                    or cfg.get("min_avg_amount_wan") or 0)
    basic = industry_map(as_of)

    keep = []
    for code, row in table.iterrows():
        info = basic.get(code) or {}
        name = info.get("name") or ""
        if not name or "ST" in name or "退" in name:
            continue
        if info.get("industry") in excluded:
            continue
        if _f(row["amt20_wan"]) is None or float(row["amt20_wan"]) < min_amt:
            continue
        keep.append(code)
    return table.loc[keep] if keep else table.iloc[0:0]


def stop_price(row: pd.Series, *, max_pct: float = 0.09) -> float:
    """止损 = max(近 10 日低, 收盘×(1-max_pct))；封顶避免过深止损。"""
    close = float(row["close"])
    low10 = _f(row["low10"]) or close
    return round(max(low10, close * (1 - max_pct)), 2)


def build_candidate(code: str, row: pd.Series, *, as_of: str, why: List[str],
                    method_id: str) -> Dict[str, Any]:
    info = industry_map(as_of).get(code) or {}
    stop = stop_price(row)
    close = float(row["close"])
    return {
        "code": code,
        "name": info.get("name") or "",
        "industry": info.get("industry") or "",
        "method": method_id,
        "close": round(close, 2),
        "chg_today": round(float(row["ret1"]), 4) if _f(row["ret1"]) is not None else None,
        "ret5": round(float(row["ret5"]), 4) if _f(row["ret5"]) is not None else None,
        "ret20": round(float(row["ret20"]), 4),
        "excess20_pp": round(float(row["excess20_pp"]), 2),
        "dist_ma20": round(float(row["dist_ma20"]), 4),
        "drawdown_high120": round(float(row["drawdown_high120"]), 4),
        "vol_ratio": round(float(row["vol_ratio"]), 2) if _f(row["vol_ratio"]) is not None else None,
        "amt20_wan": round(float(row["amt20_wan"]), 0),
        "stop": stop,
        "stop_pct": round(stop / close - 1, 4),
        "why": why,
    }


def fmt_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def thresholds(method: Dict[str, Any]) -> Dict[str, float]:
    return {k: float(v) for k, v in (method.get("params") or {}).items() if isinstance(v, (int, float))}


def summarize_status(table: pd.DataFrame, codes: Tuple[str, ...]) -> Dict[str, int]:
    """给一组代码按当前指标判定 confirming / invalid（供跟踪状态使用）。"""
    result = {"confirming": 0, "invalid": 0, "unknown": 0}
    for code in codes:
        if code not in table.index:
            result["unknown"] += 1
            continue
        row = table.loc[code]
        if float(row["close"]) >= float(row["ma20"]) and float(row["excess20_pp"]) >= 0:
            result["confirming"] += 1
        else:
            result["invalid"] += 1
    return result
