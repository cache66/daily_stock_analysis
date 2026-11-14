# -*- coding: utf-8 -*-
"""炸板窗口分析：炸板瞬间成交、开板后累计成交、以及分档买卖结构。

## 口径（2026-11-11 修正）

旧版把「首封之后、价格低于涨停价」的**全部成交**都算成"炸板成交"，对于
「封板很久后打开、之后再没回封」的票会把整个下午的成交都算进去（实测慈文传媒
被算成 56%，而真正炸板瞬间 5 分钟只有约 5%），严重失真。现在区分三个指标：

- `moment_amount` / `moment_ratio`：**炸板瞬间**——首次跌破涨停价后 5 分钟内的成交额及其占全天比例，
  这才是"炸板时有多少钱在换手"；
- `open_amount` / `open_ratio`：开板后**至收盘**的累计成交（旧口径，仅作参考，不参与打分）；
- `super_net` / `big_net`：分档买卖净额（超大单 ≥100 万、大单 20~100 万），用来判断
  「是大单在接，还是大单在卖、散户在接」。

## 取数

1. 东财 3 秒分笔（`stock_intraday_em`）——最近交易日，含买卖方向；
2. 东财不可用时退回**腾讯分笔**（`stock_zh_a_tick_tx_js`），同样有成交金额与买卖性质；
3. 历史交易日用 5 分钟线（`stock_zh_a_hist_min_em`）：`moment` = 首次「最高≥涨停价且收盘<涨停价」那根
   5 分钟 bar 的成交额，`open` = 之后仍低于涨停价的 bar 之和；无买卖方向。

注意：分笔是 3 秒快照聚合，单笔金额会被放大，"小单"统计不可靠；因此只信 `super_net`/`big_net` 的方向。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "break_window"
SCHEMA = 2
SUPER_THRESHOLD = 1_000_000
BIG_THRESHOLD = 200_000
MOMENT_MINUTES = 5


def _cache_path(code: str, date: str) -> Path:
    return CACHE_DIR / f"{code}_{date.replace('-', '')}.json"


def _legacy_cache_path(as_of: str, code: str, date: str) -> Path:
    return CACHE_DIR / as_of.replace("-", "") / f"{code}_{date.replace('-', '')}.json"


def _load(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("schema") == SCHEMA else None


def _save(path: Path, payload: Dict[str, Any]) -> None:
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
    except Exception:  # noqa: BLE001
        return None


def seal_time_str(value: Any) -> str:
    """东财首封时间 94025 / 131354 → HH:MM:SS。"""
    if not value:
        return ""
    try:
        text = str(int(float(value))).zfill(6)
    except (TypeError, ValueError):
        return ""
    return f"{text[:2]}:{text[2:4]}:{text[4:6]}"


# ---------------------------------------------------------------- 取数


def _fetch_tick_em(code: str):
    import akshare as ak

    from src.services.em_guard import em_call

    return em_call(ak.stock_intraday_em, symbol=code)


def _fetch_tick_tx(code: str):
    import akshare as ak

    market = "sh" if code.startswith(("6", "5", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    return ak.stock_zh_a_tick_tx_js(symbol=f"{market}{code}")


def _fetch_min5(code: str, date: str, as_of: str):
    import akshare as ak

    from src.services.em_guard import em_call

    return em_call(
        ak.stock_zh_a_hist_min_em,
        symbol=code,
        start_date=f"{date} 09:30:00",
        end_date=f"{as_of} 15:00:00",
        period="5",
        adjust="",
    )


def _tick_rows(frame: Any, source: str) -> List[Tuple[str, float, float, str]]:
    """统一成 [(time, price, amount, side)]。"""
    rows: List[Tuple[str, float, float, str]] = []
    if frame is None or len(frame) == 0:
        return rows
    if source == "em":
        for _, row in frame.iterrows():
            try:
                price = float(row.get("成交价"))
                amount = price * float(row.get("手数")) * 100
            except (TypeError, ValueError):
                continue
            if amount > 0:
                rows.append((str(row.get("时间")), price, amount, str(row.get("买卖盘性质") or "")))
    else:
        for _, row in frame.iterrows():
            try:
                price = float(row.get("成交价格"))
                amount = float(row.get("成交金额"))
            except (TypeError, ValueError):
                continue
            if amount > 0:
                rows.append((str(row.get("成交时间")), price, amount, str(row.get("性质") or "")))
    rows.sort(key=lambda item: item[0])
    return rows


def _analyze_ticks(rows: List[Tuple[str, float, float, str]], limit_price: float, seal_t: str) -> Dict[str, Any]:
    total = sum(amount for _, _, amount, _ in rows)
    if not rows or not total:
        return {"status": "missing", "reason": "分笔为空"}

    touched_time = next((moment for moment, price, _, _ in rows if price >= limit_price - 1e-6), "")
    break_time = ""
    start_index = 0
    for index, (moment, price, _, _) in enumerate(rows):
        if seal_t and moment < seal_t:
            continue
        if price < limit_price - 1e-6:
            break_time = moment
            start_index = index
            break
    if not break_time:
        return {
            "status": "ok",
            "total_amount": total,
            "limit_up_broken": False,
            "touched_time": touched_time,
            "break_time": "",
            "moment_amount": 0.0,
            "moment_ratio": 0.0,
            "open_amount": 0.0,
            "open_ratio": 0.0,
            "super_net": None,
            "big_net": None,
        }

    try:
        start_dt = datetime.strptime(break_time, "%H:%M:%S")
        end_moment = (start_dt + timedelta(minutes=MOMENT_MINUTES)).strftime("%H:%M:%S")
    except ValueError:
        end_moment = break_time

    moment_amount = 0.0
    open_amount = 0.0
    buckets = {
        "super": {"buy": 0.0, "sell": 0.0},
        "big": {"buy": 0.0, "sell": 0.0},
    }
    for moment, price, amount, side in rows[start_index:]:
        if price >= limit_price - 1e-6:
            continue  # 又回封了，不计入开板成交
        open_amount += amount
        if moment <= end_moment:
            moment_amount += amount
        bucket = "super" if amount >= SUPER_THRESHOLD else ("big" if amount >= BIG_THRESHOLD else None)
        if bucket and side in ("买盘", "卖盘"):
            buckets[bucket]["buy" if side == "买盘" else "sell"] += amount

    return {
        "status": "ok",
        "total_amount": total,
        "limit_up_broken": True,
        "touched_time": touched_time,
        "break_time": break_time,
        "moment_minutes": MOMENT_MINUTES,
        "moment_amount": moment_amount,
        "moment_ratio": round(moment_amount / total, 4) if total else None,
        "open_amount": open_amount,
        "open_ratio": round(open_amount / total, 4) if total else None,
        "super_net": buckets["super"]["buy"] - buckets["super"]["sell"],
        "big_net": buckets["big"]["buy"] - buckets["big"]["sell"],
    }


def _analyze_min5(frame: Any, date: str, limit_price: float, seal_t: str) -> Dict[str, Any]:
    if frame is None or len(frame) == 0:
        return {"status": "missing", "reason": "5 分钟线为空"}
    day_rows = frame[frame["时间"].astype(str).str[:10] == date]
    if len(day_rows) == 0:
        return {"status": "missing", "reason": f"5 分钟线不含 {date}"}

    total = float(day_rows["成交额"].sum() or 0)
    break_index: Optional[int] = None
    records = list(day_rows.itertuples())
    for index, row in enumerate(records):
        moment = str(getattr(row, "时间"))[11:]
        if seal_t and moment < seal_t:
            continue
        high = float(getattr(row, "最高") or 0)
        close = float(getattr(row, "收盘") or 0)
        if high >= limit_price - 1e-6 and close < limit_price - 1e-6:
            break_index = index
            break
    if break_index is None:
        return {
            "status": "ok",
            "total_amount": total,
            "limit_up_broken": False,
            "moment_amount": 0.0,
            "moment_ratio": 0.0,
            "open_amount": 0.0,
            "open_ratio": 0.0,
            "super_net": None,
            "big_net": None,
        }
    moment_amount = float(getattr(records[break_index], "成交额") or 0)
    open_amount = 0.0
    for row in records[break_index:]:
        close = float(getattr(row, "收盘") or 0)
        if close < limit_price - 1e-6:
            open_amount += float(getattr(row, "成交额") or 0)
    return {
        "status": "ok",
        "total_amount": total,
        "limit_up_broken": True,
        "break_time": str(getattr(records[break_index], "时间"))[11:],
        "moment_minutes": 5,
        "moment_amount": moment_amount,
        "moment_ratio": round(moment_amount / total, 4) if total else None,
        "open_amount": open_amount,
        "open_ratio": round(open_amount / total, 4) if total else None,
        "super_net": None,
        "big_net": None,
    }


# ---------------------------------------------------------------- 对外


def analyze_break_window(
    code: str,
    date: str,
    limit_price: Optional[float],
    first_seal: Any,
    *,
    as_of: Optional[str] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """炸板瞬间成交 + 开板累计 + 分档净额。"""
    code = str(code).zfill(6)
    as_of = as_of or _latest_trade_date() or date
    if not limit_price:
        return {"status": "missing", "reason": "缺涨停价"}
    path = _cache_path(code, date)
    if not refresh:
        cached = _load(path) or _load(_legacy_cache_path(as_of, code, date))
        if cached:
            return cached

    seal_t = seal_time_str(first_seal)
    latest = _latest_trade_date()
    payload: Dict[str, Any]
    method = "none"

    if latest and date == latest:
        rows: List[Tuple[str, float, float, str]] = []
        source = ""
        for source_name, fetcher in (("em", _fetch_tick_em), ("tx", _fetch_tick_tx)):
            try:
                rows = _tick_rows(fetcher(code), source_name)
            except Exception:  # noqa: BLE001 - 依次降级
                rows = []
            if rows:
                source = source_name
                break
        if rows:
            payload = _analyze_ticks(rows, float(limit_price), seal_t)
            method = f"tick_{source}"
        else:
            payload = {"status": "missing", "reason": "东财/腾讯分笔均不可用"}
    else:
        try:
            payload = _analyze_min5(_fetch_min5(code, date, as_of), date, float(limit_price), seal_t)
            method = "min5"
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "missing", "reason": str(exc)[:200]}

    payload.update(
        {
            "schema": SCHEMA,
            "code": code,
            "date": date,
            "as_of": as_of,
            "method": method,
            "seal_time": seal_t,
            "seal_known": bool(seal_t),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if payload.get("status") == "ok":
        payload["limitations"] = [
            "炸板瞬间 = 首次跌破涨停价后 5 分钟内成交；开板累计 = 开板至收盘（旧口径，仅参考）"
            + ("；首封时间缺失，按 09:30 处理" if not seal_t else ""),
            "分笔为 3 秒快照聚合，单笔金额被放大，小单统计不可靠；只用超大单/大单净额判方向",
            "历史交易日用 5 分钟线，只有金额、没有买卖方向",
        ]
        _save(path, payload)
    return payload


def batch_analyze(events: List[Dict[str, Any]], as_of: str, *, max_workers: int = 3) -> Dict[str, Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    results: Dict[str, Dict[str, Any]] = {}

    def run(item: Dict[str, Any]) -> None:
        key = f"{item.get('code')}_{item.get('break_date')}"
        results[key] = analyze_break_window(
            item.get("code"),
            item.get("break_date"),
            item.get("limit_price"),
            item.get("first_seal"),
            as_of=as_of,
        )

    if events:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(run, events))
    return results
