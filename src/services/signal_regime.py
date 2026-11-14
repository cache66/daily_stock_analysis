# -*- coding: utf-8 -*-
"""按信号近端超额决定“今天还收不收这个信号”（替代单一 defensive 开关）。

背景（2026-11-09）：准入原来只看 `config/local_strategy_profile.json` 的
`market_regime`，defensive 就把所有趋势类信号一起停掉。问题是它不区分
“大盘弱”和“某个信号自己失效”——实测百日新高 04/05 月 T+5 超额 +3.9%/+3.3%，
06 月起转负（07 −7.3%、10 −1.6%），而筛选类（双低/趋势质量）在 09/10 月反而是
+3.9%/+2.6%、+4.0%/+2.9%。同一个市场环境下，不同信号该不该收是相反答案。

口径：
- 超额 = 该信号 T+5 收益 − 同日全市场等权均值（涨跌基准一致，剔除大盘漂移）；
- 短窗 20 条、长窗 60 条（按信号日及代码排序，只算截止日已成熟的 T+5）；
- verdict：allow / caution / block
  - block：短窗超额 ≤ −2% 且短窗胜率 < 45%，或短窗样本 ≥ 20 且长窗超额 ≤ −3%；
  - caution：短窗或长窗超额为负，但没到 block 门槛；
  - allow：两窗都不为负。
样本不足（短窗 < 5）时给 caution 并说明，不硬判。

结果缓存到 `data/strategy_review/signal_regime.json`，准入与预览页共用同一份。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.market_instruments import is_tradable_stock
from src.services.quote_quality import load_suspicious_codes

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "strategy_review" / "signal_regime.json"

SHORT_WINDOW = 20
LONG_WINDOW = 60
MIN_SHORT_SAMPLES = 5
HORIZON = 5
CACHE_SCHEMA_VERSION = 2

# 只有这些信号参与准入判断（其余是诊断/基线，不进样本池）
TRACKED_FAMILIES = (
    "hundred_day_high",
    "trend_leader_unified",
    "earnings_surprise",
    "daily_slow_rise",
    "screening__dual_low",
    "screening__momentum_quality",
    "screening__shrink_pullback",
)

BLOCK_SHORT_EXCESS = -2.0
BLOCK_SHORT_WIN = 45.0
BLOCK_LONG_EXCESS = -3.0


def _load_prices(db_path: Path, start: str, end: str) -> Dict[str, Dict[str, float]]:
    """{code: {date: close}}，只取需要的列，避免整表搬到内存。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT code, date, close FROM stock_daily WHERE date BETWEEN ? AND ? AND close > 0",
            (start, end),
        ).fetchall()
    finally:
        con.close()
    prices: Dict[str, Dict[str, float]] = {}
    suspicious = load_suspicious_codes()
    for code, day, close in rows:
        if not is_tradable_stock(str(code)) or str(code) in suspicious:
            continue
        prices.setdefault(str(code), {})[str(day)[:10]] = float(close)
    return prices


def _market_mean_by_date(prices: Dict[str, Dict[str, float]], sessions: List[str]) -> Dict[str, float]:
    means: Dict[str, float] = {}
    index = {session: position for position, session in enumerate(sessions)}
    for day, position in index.items():
        target = position + HORIZON
        if target >= len(sessions):
            continue
        target_day = sessions[target]
        returns = []
        for series in prices.values():
            base = series.get(day)
            later = series.get(target_day)
            if base and later and base > 0:
                returns.append(later / base - 1.0)
        if returns:
            means[day] = sum(returns) / len(returns)
    return means


def _trading_sessions(start: date, end: date) -> List[str]:
    from src.core.trading_calendar import get_trading_sessions

    return [d.isoformat() for d in get_trading_sessions("cn", start, end)]


def compute_signal_regime(
    *,
    as_of: Optional[date] = None,
    db_path: Path = DEFAULT_DB,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """算各信号近端超额；返回 {as_of, families:{...}, generated_at}。"""
    cutoff = as_of or date.today()
    # 多取一些日历天，保证长窗有足够交易日；同时留出 T+5 的尾部
    start = cutoff - timedelta(days=int(LONG_WINDOW * 1.6) + HORIZON * 2 + 20)
    try:
        sessions = _trading_sessions(start, cutoff)
    except (ValueError, RuntimeError, ImportError) as exc:
        logger.warning("交易日历不可用，信号体检跳过: %s", exc)
        return _publish({"as_of": cutoff.isoformat(), "families": {}, "error": "calendar_unavailable"}, output_path)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" * len(TRACKED_FAMILIES))
        snapshot_rows = con.execute(
            f"SELECT signal_type, signal_date, code FROM kline_signal_snapshot "
            f"WHERE signal_date BETWEEN ? AND ? AND signal_type IN ({placeholders}) "
            "ORDER BY signal_date, signal_type, code",
            (start.isoformat(), cutoff.isoformat(), *TRACKED_FAMILIES),
        ).fetchall()
    finally:
        con.close()
    if not snapshot_rows:
        return _publish({"as_of": cutoff.isoformat(), "families": {}, "error": "no_signals"}, output_path)

    prices = _load_prices(db_path, start.isoformat(), cutoff.isoformat())
    market = _market_mean_by_date(prices, sessions)
    index = {session: position for position, session in enumerate(sessions)}

    per_family: Dict[str, List[Dict[str, Any]]] = {}
    for family, signal_date, code in snapshot_rows:
        day = str(signal_date)[:10]
        position = index.get(day)
        if day > cutoff.isoformat() or position is None or position + HORIZON >= len(sessions):
            continue
        target_day = sessions[position + HORIZON]
        if target_day > cutoff.isoformat():
            continue
        series = prices.get(str(code)) or {}
        base = series.get(day)
        later = series.get(target_day)
        if not base or not later or base <= 0:
            continue
        base_mean = market.get(day)
        if base_mean is None:
            continue
        ret = later / base - 1.0
        per_family.setdefault(str(family), []).append({
            "date": day,
            "ret": ret,
            "excess": ret - base_mean,
        })

    families: Dict[str, Any] = {}
    for family in TRACKED_FAMILIES:
        items = sorted(per_family.get(family, []), key=lambda item: item["date"])
        if not items:
            families[family] = {
                "verdict": "caution",
                "reason": "近端无可用样本（缺 T+5 结果）",
                "short": {"n": 0},
                "long": {"n": 0},
            }
            continue
        short = items[-SHORT_WINDOW:]
        long = items[-LONG_WINDOW:]
        short_ex = sum(item["excess"] for item in short) / len(short) * 100
        long_ex = sum(item["excess"] for item in long) / len(long) * 100
        short_win = sum(1 for item in short if item["ret"] > 0) / len(short) * 100
        verdict, reason = _verdict(len(short), short_ex, short_win, long_ex)
        families[family] = {
            "verdict": verdict,
            "reason": reason,
            "short": {"n": len(short), "excess_pct": round(short_ex, 2), "win_pct": round(short_win, 1),
                      "from": short[0]["date"], "to": short[-1]["date"]},
            "long": {"n": len(long), "excess_pct": round(long_ex, 2)},
        }

    result = {
        "as_of": cutoff.isoformat(),
        "window": {"short": SHORT_WINDOW, "long": LONG_WINDOW, "horizon": HORIZON},
        "families": families,
    }
    return _publish(result, output_path)


def _publish(result: Dict[str, Any], output_path: Optional[Path]) -> Dict[str, Any]:
    result.update(schema_version=CACHE_SCHEMA_VERSION, scope="research",
                  generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if output_path is not None:
        import os
        import tempfile

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return result


def _verdict(sample: int, short_excess: float, short_win: float, long_excess: float):
    if sample < MIN_SHORT_SAMPLES:
        return "caution", f"近端样本仅 {sample} 条（<{MIN_SHORT_SAMPLES}），样本不足不下结论"
    if short_excess <= BLOCK_SHORT_EXCESS and short_win < BLOCK_SHORT_WIN:
        return "block", f"近{sample}条 T+5 超额 {short_excess:+.1f}%、胜率 {short_win:.0f}%"
    if sample >= 20 and long_excess <= BLOCK_LONG_EXCESS:
        return "block", f"长窗超额 {long_excess:+.1f}%（近端 {short_excess:+.1f}%）"
    if short_excess < 0 or long_excess < 0:
        return "caution", f"近端超额 {short_excess:+.1f}% / 长窗 {long_excess:+.1f}%，未到停用线"
    return "allow", f"近端超额 {short_excess:+.1f}% / 长窗 {long_excess:+.1f}%"


def _resolve_path(output_path: Optional[Path] = None) -> Path:
    # 惰性解析，便于测试或临时运行覆盖 DEFAULT_OUTPUT
    return Path(output_path) if output_path is not None else DEFAULT_OUTPUT


def load_cached(output_path: Optional[Path] = None, *, as_of: Optional[str] = None) -> Dict[str, Any]:
    try:
        data = json.loads(_resolve_path(output_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"families": {}, "error": "cache_missing"}
    if not isinstance(data, dict):
        return {"families": {}, "error": "cache_invalid"}
    if as_of is not None and (data.get("as_of") != as_of or data.get("schema_version") != CACHE_SCHEMA_VERSION):
        return {"families": {}, "error": "cache_stale", "as_of": data.get("as_of")}
    return data


def verdict_for(family: str, output_path: Optional[Path] = None, *, as_of: Optional[str] = None) -> str:
    """读取研究描述；用于当日准入时必须传 as_of，unknown 不新增记录。"""
    data = load_cached(output_path, as_of=as_of)
    if data.get("error"):
        return "unknown"
    item = (data.get("families") or {}).get(str(family)) or {}
    return str(item.get("verdict") or "unknown")
