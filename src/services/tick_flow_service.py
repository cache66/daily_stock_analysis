# -*- coding: utf-8 -*-
"""当日逐笔（3 秒快照）买卖盘统计。

数据来源：东方财富公开分笔成交明细（akshare `stock_intraday_em`），字段为
`时间 / 成交价 / 手数 / 买卖盘性质`，按 3 秒快照聚合。

口径与边界（**必须随结果展示**）：
- 这是公开分笔快照，不是 Level-2：没有十档、没有逐笔委托队列；
- 分档按「单个快照成交额」划分（超大 ≥100 万、大 20~100 万、中 4~20 万、小 <4 万），
  与东财资金流按**逐笔成交**分档的口径不同，两套数字不可直接相减或互相校验；
- 只覆盖**最近一个交易日**（分笔接口不提供历史某日全量），历史日期返回 `history_unavailable`；
- 中性盘（集合竞价/无法判定方向）单独计数，不计入买盘/卖盘。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "tick_flow"

TICK_SCHEMA = 2

BUCKETS: Tuple[Tuple[str, float, str], ...] = (
    ("super", 1_000_000, "超大单(≥100万)"),
    ("big", 200_000, "大单(20~100万)"),
    ("mid", 40_000, "中单(4~20万)"),
    ("small", 0, "小单(<4万)"),
)


def _bucket_of(amount: float) -> str:
    for key, threshold, _ in BUCKETS:
        if amount >= threshold:
            return key
    return "small"


def _cache_path(as_of: str, code: str) -> Path:
    return CACHE_DIR / as_of.replace("-", "") / f"{code}.json"


def _load(as_of: str, code: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(as_of, code)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # 口径/字段变更后旧缓存必须失效，避免半旧结构混进页面
    if payload.get("schema") != TICK_SCHEMA:
        return None
    return payload


def _save(as_of: str, code: str, payload: Dict[str, Any]) -> None:
    path = _cache_path(as_of, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_trade_date() -> Optional[str]:
    try:
        from sqlalchemy import text

        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            value = session.execute(text("select max(date) from stock_daily")).scalar()
        return str(value)[:10] if value else None
    except Exception:  # noqa: BLE001 - 取不到日期按历史日期处理
        return None


def _fetch_frame(code: str) -> Any:
    """拉取东财公开分笔（测试里可 monkeypatch 掉，避免联网）。"""
    import akshare as ak

    from src.services.em_guard import em_call

    return em_call(ak.stock_intraday_em, symbol=code)


def compute_tick_flow(code: str, as_of: str, *, refresh: bool = False) -> Dict[str, Any]:
    """单只票当日分档买卖盘统计（快照口径）。"""
    code = str(code).zfill(6)
    latest = _latest_trade_date()
    if latest and latest != as_of[:10]:
        return {
            "status": "history_unavailable",
            "reason": f"分笔只提供最近交易日（{latest}），{as_of} 无法回溯",
        }

    if not refresh:
        cached = _load(as_of, code)
        if cached:
            return cached

    try:
        frame = _fetch_frame(code)
    except Exception as exc:  # noqa: BLE001 - 单票失败不影响其他票
        return {"status": "missing", "reason": str(exc)[:200]}

    if frame is None or len(frame) == 0:
        return {"status": "missing", "reason": "分笔为空"}

    buckets: Dict[str, Dict[str, float]] = {
        key: {"buy_trades": 0, "sell_trades": 0, "neutral_trades": 0, "buy_amount": 0.0, "sell_amount": 0.0}
        for key, _, _ in BUCKETS
    }
    total_trades = 0
    total_amount = 0.0
    for _, row in frame.iterrows():
        try:
            amount = float(row.get("成交价")) * float(row.get("手数")) * 100
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        total_trades += 1
        total_amount += amount
        bucket = buckets[_bucket_of(amount)]
        side = str(row.get("买卖盘性质") or "").strip()
        if side == "买盘":
            bucket["buy_trades"] += 1
            bucket["buy_amount"] += amount
        elif side == "卖盘":
            bucket["sell_trades"] += 1
            bucket["sell_amount"] += amount
        else:
            bucket["neutral_trades"] += 1

    for key, _, _ in BUCKETS:
        item = buckets[key]
        item["net_amount"] = round(item["buy_amount"] - item["sell_amount"], 2)
        item["buy_amount"] = round(item["buy_amount"], 2)
        item["sell_amount"] = round(item["sell_amount"], 2)

    main_net = buckets["super"]["net_amount"] + buckets["big"]["net_amount"]
    payload = {
        "schema": TICK_SCHEMA,
        "status": "ok",
        "code": code,
        "as_of": as_of,
        "source": "eastmoney_intraday_3s",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": {
            "trades": total_trades,
            "amount": round(total_amount, 2),
            "buy_trades": buckets["super"]["buy_trades"] + buckets["big"]["buy_trades"] + buckets["mid"]["buy_trades"] + buckets["small"]["buy_trades"],
            "sell_trades": buckets["super"]["sell_trades"] + buckets["big"]["sell_trades"] + buckets["mid"]["sell_trades"] + buckets["small"]["sell_trades"],
            "neutral_trades": buckets["super"]["neutral_trades"] + buckets["big"]["neutral_trades"] + buckets["mid"]["neutral_trades"] + buckets["small"]["neutral_trades"],
            "buy_amount": round(
                sum(buckets[key]["buy_amount"] for key, _, _ in BUCKETS), 2
            ),
            "sell_amount": round(
                sum(buckets[key]["sell_amount"] for key, _, _ in BUCKETS), 2
            ),
        },
        "buckets": {key: buckets[key] for key, _, _ in BUCKETS},
        "main_net": round(main_net, 2),
        "limitations": [
            "分笔为东财公开 3 秒快照，不是 L2；没有十档与逐笔委托队列",
            "分档按单个快照成交额，与东财资金流按逐笔成交分档的口径不同，两套数字不可直接相减",
            "只覆盖最近一个交易日，历史炸板日无法回溯该口径",
        ],
    }
    _save(as_of, code, payload)
    return payload


def labels() -> List[Dict[str, str]]:
    return [{"key": key, "label": label} for key, _, label in BUCKETS]
