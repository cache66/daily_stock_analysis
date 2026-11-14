# -*- coding: utf-8 -*-
"""每日市场复盘：涨停/炸板/跌停池 + 板块涨停分布 + 情绪指标。

数据来源：东方财富涨停池（akshare `stock_zt_pool_*_em`），按交易日缓存到
`data/cache/market_review/<date>.json`；抓取失败时用本地日线近似，并明确标注
「日线近似」而不是假装是实时封板数据。

口径说明（必须随数据一起展示）：
- 涨停/炸板/跌停以交易所与东方财富口径为准；本地近似只看收盘与最高价；
- 「封板率」= 涨停家数 ÷ (涨停 + 炸板)，不区分首板/连板；
- 连板梯队按 `连板数` 分桶；`连板数=1` 为首板；
- 板块涨停分布按涨停池的「所属行业」，与本地行业分类可能不一致，展示两处来源。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "market_review"


def _stamp(day: str) -> str:
    return day.replace("-", "")


def _cache_path(as_of: str) -> Path:
    return CACHE_DIR / f"review_{_stamp(as_of)}.json"


def _load_cache(as_of: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(as_of)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(as_of: str, payload: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(as_of).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_records(df: Any, fields: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if df is None or len(df) == 0:
        return rows
    for _, row in df.iterrows():
        item: Dict[str, Any] = {}
        for target, source in fields.items():
            value = row.get(source)
            if value is None:
                item[target] = None
                continue
            if target == "code":
                try:
                    item[target] = str(int(float(value))).zfill(6)
                except (TypeError, ValueError):
                    item[target] = str(value).strip()
                continue
            try:
                import math

                number = float(value)
                item[target] = None if not math.isfinite(number) else number
            except (TypeError, ValueError):
                item[target] = str(value).strip()
        rows.append(item)
    return rows


LIMIT_FIELDS = {
    "code": "代码",
    "name": "名称",
    "change_pct": "涨跌幅",
    "price": "最新价",
    "amount": "成交额",
    "seal_amount": "封板资金",
    "first_seal": "首次封板时间",
    "break_count": "炸板次数",
    "limit_stat": "涨停统计",
    "boards": "连板数",
    "industry": "所属行业",
}
BROKEN_FIELDS = {
    "code": "代码",
    "name": "名称",
    "change_pct": "涨跌幅",
    "price": "最新价",
    "limit_price": "涨停价",
    "amount": "成交额",
    "turnover": "换手率",
    "float_mv": "流通市值",
    "break_count": "炸板次数",
    "first_seal": "首次封板时间",
    "limit_stat": "涨停统计",
    "amplitude": "振幅",
    "industry": "所属行业",
}


def _fetch_pools(as_of: str) -> Dict[str, List[Dict[str, Any]]]:
    import akshare as ak

    stamp = _stamp(as_of)
    return {
        "limit_up": _to_records(ak.stock_zt_pool_em(date=stamp), LIMIT_FIELDS),
        "broken": _to_records(ak.stock_zt_pool_zbgc_em(date=stamp), BROKEN_FIELDS),
        "limit_down": _to_records(ak.stock_zt_pool_dtgc_em(date=stamp), {"code": "代码", "name": "名称"}),
    }


def _local_approx(as_of: str) -> Dict[str, Any]:
    """本地日线近似：只用于取数失败时的兜底，口径比交易所数据粗。"""
    from src.services.shortline import base

    bars = base.load_bars(as_of)
    if bars is None or bars.empty:
        return {"status": "missing", "reason": "无本地行情"}
    approx: List[Dict[str, Any]] = []
    broken: List[Dict[str, Any]] = []
    for code, group in bars.groupby("code"):
        if len(group) < 2:
            continue
        last, prev = group.iloc[-1], group.iloc[-2]
        try:
            change = float(last["close"]) / float(prev["close"]) - 1
            limit_pct = 0.195 if str(code).startswith(("30", "68")) else 0.095
            limit_price = round(float(prev["close"]) * (1 + limit_pct), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if change >= limit_pct:
            approx.append({"code": str(code), "change_pct": round(change * 100, 2)})
        elif float(last["high"]) >= limit_price > float(last["close"]):
            broken.append({"code": str(code), "change_pct": round(change * 100, 2)})
    return {
        "status": "limited",
        "reason": "东方财富涨停池不可用，改用本地日线近似（只看收盘/最高价，无封单与连板）",
        "limit_up": approx,
        "broken": broken,
        "limit_down": [],
    }


def _board_ladder(limit_up: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for item in limit_up:
        try:
            boards = int(float(item.get("boards") or 1))
        except (TypeError, ValueError):
            boards = 1
        buckets.setdefault(boards, []).append(item)
    ladder = []
    for boards in sorted(buckets, reverse=True):
        members = buckets[boards]
        ladder.append(
            {
                "boards": boards,
                "label": "首板" if boards <= 1 else f"{boards}连板",
                "count": len(members),
                "names": [f"{m.get('name')}({m.get('code')})" for m in members[:12]],
            }
        )
    return ladder


def _industry_distribution(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for item in items:
        industry = str(item.get("industry") or "").strip() or "未分类"
        counts[industry] = counts.get(industry, 0) + 1
    total = sum(counts.values()) or 1
    return [
        {"industry": name, "count": count, "share": round(count / total, 4)}
        for name, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    ]


def _latest_trade_date() -> Optional[str]:
    try:
        from src.storage import DatabaseManager
        from sqlalchemy import text

        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            value = session.execute(text("select max(date) from stock_daily")).scalar()
        return str(value)[:10] if value else None
    except Exception:  # noqa: BLE001 - 取不到最新交易日就当作历史日期处理
        return None


def _board_quotes(as_of: str) -> Dict[str, Dict[str, Any]]:
    """东财行业板块当日行情（涨跌幅/领涨股）。

    只对最新交易日有效：接口返回的是「最近一个交易日」快照，历史回放时若强行套用
    会把今天的板块涨幅贴到历史日期上，因此非最新交易日直接返回空并记录限制。
    """
    try:
        latest = _latest_trade_date()
        if not latest or str(latest)[:10] != str(as_of)[:10]:
            return {}
        import akshare as ak

        df = ak.stock_board_industry_name_em()
        quotes: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            name = str(row.get("板块名称") or "").strip()
            if not name:
                continue
            quotes[name] = {
                "change_pct": round(float(row.get("涨跌幅") or 0), 2),
                "up": int(row.get("上涨家数") or 0),
                "down": int(row.get("下跌家数") or 0),
                "leader": str(row.get("领涨股票") or "").strip(),
                "leader_change": round(float(row.get("领涨股票-涨跌幅") or 0), 2),
            }
        return quotes
    except Exception:  # noqa: BLE001 - 板块行情失败不影响涨停池展示
        return {}


def _match_board(industry: str, quotes: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """涨停池行业名是截断版（如「汽车零部」），用最长前缀匹配回东财板块全名。"""
    best_name, best_len = None, 0
    for name in quotes:
        if len(name) < 2:
            continue
        if (name.startswith(industry) or industry.startswith(name)) and len(name) > best_len:
            best_name, best_len = name, len(name)
    return quotes.get(best_name) if best_name else None


def _sector_moves(
    as_of: str,
    limit_up: List[Dict[str, Any]],
    broken: List[Dict[str, Any]],
    quotes: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """板块层面：涨停家数 + 炸板家数 + 东财板块涨幅/领涨股。"""
    counts: Dict[str, Dict[str, int]] = {}
    for item in limit_up:
        industry = str(item.get("industry") or "").strip() or "未分类"
        counts.setdefault(industry, {"limit_up": 0, "broken": 0})["limit_up"] += 1
    for item in broken:
        industry = str(item.get("industry") or "").strip() or "未分类"
        counts.setdefault(industry, {"limit_up": 0, "broken": 0})["broken"] += 1

    quotes = quotes or {}
    rows = []
    for industry, stat in counts.items():
        board = _match_board(industry, quotes) or {}
        rows.append(
            {
                "industry": industry,
                "limit_up": stat["limit_up"],
                "broken": stat["broken"],
                "board_change": board.get("change_pct"),
                "up": board.get("up"),
                "down": board.get("down"),
                "leader": board.get("leader"),
                "leader_change": board.get("leader_change"),
            }
        )
    rows.sort(key=lambda item: (item["limit_up"], item["broken"]), reverse=True)
    return rows[:10]


def build_market_review(as_of: str, *, refresh: bool = False) -> Dict[str, Any]:
    cached = None if refresh else _load_cache(as_of)
    if cached:
        return cached

    try:
        pools = _fetch_pools(as_of)
        source = "eastmoney_zt_pool"
        status = "ok"
        limitations: List[str] = []
        if not pools["limit_up"] and not pools["broken"]:
            raise RuntimeError("东财涨停池为空")
    except Exception as exc:  # noqa: BLE001 - 取数失败降级为本地近似
        approx = _local_approx(as_of)
        pools = {
            "limit_up": approx.get("limit_up") or [],
            "broken": approx.get("broken") or [],
            "limit_down": approx.get("limit_down") or [],
        }
        source = "local_daily_approx"
        status = "limited"
        limitations = [str(approx.get("reason") or exc)]

    limit_up = pools["limit_up"]
    broken = pools["broken"]
    limit_down = pools["limit_down"]
    quotes = _board_quotes(as_of)
    if source == "eastmoney_zt_pool" and not quotes:
        limitations.append("板块涨幅/领涨股仅最新交易日可取，历史日期不回填（避免拿今天的板块行情贴到历史）")
    seal_total = sum(float(item.get("seal_amount") or 0) for item in limit_up) / 1e8
    ladder = _board_ladder(limit_up)
    highest = ladder[0]["boards"] if ladder else 0
    seal_rate = round(len(limit_up) / (len(limit_up) + len(broken)), 4) if (limit_up or broken) else None

    payload = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "source": source,
        "summary": {
            "limit_up": len(limit_up),
            "broken": len(broken),
            "limit_down": len(limit_down),
            "seal_rate": seal_rate,
            "highest_boards": highest,
            "seal_amount_total_yi": round(seal_total, 2) if source == "eastmoney_zt_pool" else None,
        },
        "ladder": ladder,
        "limit_up": sorted(limit_up, key=lambda item: -(float(item.get("boards") or 1)))[:40],
        "broken": sorted(broken, key=lambda item: -(float(item.get("break_count") or 0)))[:40],
        "industry_limit_up": _industry_distribution(limit_up, "industry"),
        "industry_broken": _industry_distribution(broken, "industry"),
        "sectors": _sector_moves(as_of, limit_up, broken, quotes),
        "limitations": limitations
        + [
            "封板率 = 涨停 ÷ (涨停 + 炸板)，不区分首板/连板",
            "板块涨停分布用涨停池「所属行业」，与本地行业分类可能不同",
        ],
    }
    if source == "eastmoney_zt_pool":
        _save_cache(as_of, payload)
    return payload
