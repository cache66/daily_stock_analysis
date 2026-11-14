# -*- coding: utf-8 -*-
"""同花顺即时资金流（备用源）：全市场一次调用，给「当日主动买卖净额」。

用途：东财 `stock_individual_fund_flow` 被频控/不可用时，至少让当日炸板票的
「资金净额」不是空的。口径与东财**不同**：
- 同花顺给的是「流入资金 − 流出资金」（主动买卖净额），**没有**超大单/大单/中单/小单分档；
- 只有最近交易日的快照，没有历史逐日序列；
- 因此只用于当日事件，字段名为 `net`，不得与东财 `main`（超大+大单净额）混用作同一口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "ths_fund_flow"
SCHEMA = 1


def _parse_amount(text: Any) -> Optional[float]:
    """同花顺返回 '1.15亿' / '-6855.04万' / '0' 这类字符串 → 元。"""
    if text is None:
        return None
    value = str(text).strip().replace(",", "")
    if not value or value in {"-", "--"}:
        return None
    multiplier = 1.0
    if value.endswith("亿"):
        multiplier, value = 1e8, value[:-1]
    elif value.endswith("万"):
        multiplier, value = 1e4, value[:-1]
    try:
        return float(value) * multiplier
    except ValueError:
        return None


def _cache_path(as_of: str) -> Path:
    return CACHE_DIR / f"{as_of.replace('-', '')}.json"


def fetch_daily_net(as_of: str, *, refresh: bool = False) -> Dict[str, Dict[str, float]]:
    """返回 {6 位代码: {net, inflow, outflow, amount, turnover}}；失败返回空 dict。"""
    path = _cache_path(as_of)
    if not refresh and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if payload and payload.get("schema") == SCHEMA:
            return payload.get("stocks") or {}

    try:
        import akshare as ak

        frame = ak.stock_fund_flow_individual(symbol="即时")
    except Exception:  # noqa: BLE001 - 备用源失败就返回空，调用方继续降级
        return {}

    if frame is None or len(frame) == 0:
        return {}

    stocks: Dict[str, Dict[str, float]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("股票代码") or "").strip().split(".")[0].zfill(6)
        if not code.isdigit() or len(code) != 6:
            continue
        stocks[code] = {
            "net": _parse_amount(row.get("净额")),
            "inflow": _parse_amount(row.get("流入资金")),
            "outflow": _parse_amount(row.get("流出资金")),
            "amount": _parse_amount(row.get("成交额")),
            "turnover": _parse_amount(str(row.get("换手率") or "").replace("%", "")),
        }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": SCHEMA, "as_of": as_of, "stocks": stocks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return stocks
