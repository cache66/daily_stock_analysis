# -*- coding: utf-8 -*-
"""工作台只读 API（P2）：每日复盘首页、固定信息卡、方法区、K 线。

设计约束（见 `docs/个人策略文档/工作台实施规划-20261110.md`）：
- 只读；不提供写入接口，跟踪仍由 CLI/后处理脚本创建。
- 计算全部在服务层（`profile_card_service` / `shortline`），本层只装配与裁剪。
- 受限数据显式返回状态与原因，不用 0 或空数组掩盖。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
METHODS_DIR = PROJECT_ROOT / "data" / "strategy_review" / "methods"
HOME_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "workbench"
HOME_MEMO_TTL = 300  # 进程内缓存秒数：同一 as_of 的首页在 5 分钟内不重算
HOME_DISK_TTL = 1800  # 磁盘缓存秒数：重启后仍能直接命中上次结果
_HOME_MEMO: Dict[str, Any] = {}


def _home_disk_path(review_date: str) -> Path:
    return HOME_CACHE_DIR / f"home_{review_date.replace('-', '')}.json"


def _home_cache_get(key: str, review_date: str, max_age: int) -> Optional[Dict[str, Any]]:
    memo = _HOME_MEMO.get(key)
    if memo and time.monotonic() - memo[1] <= HOME_MEMO_TTL:
        return {**memo[0], "cached": True, "cache_ttl_seconds": HOME_MEMO_TTL}
    path = _home_disk_path(review_date)
    if not path.is_file():
        return None
    if time.time() - path.stat().st_mtime > max_age:
        return None
    payload = _load_json(path)
    if not payload:
        return None
    _HOME_MEMO[key] = (payload, time.monotonic())
    return {**payload, "cached": True, "cache_ttl_seconds": max_age}


def _home_cache_put(key: str, review_date: str, payload: Dict[str, Any]) -> None:
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    _HOME_MEMO[key] = (payload, time.monotonic())
    try:
        HOME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _home_disk_path(review_date).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:  # noqa: BLE001 - 写缓存失败不影响返回
        logger.warning("工作台首页缓存写入失败: %s", exc)


def _latest_trade_date(as_of: Optional[str] = None) -> str:
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        if as_of:
            value = session.execute(
                text("select max(date) from stock_daily where date <= :d"), {"d": as_of}
            ).scalar()
        else:
            value = session.execute(text("select max(date) from stock_daily")).scalar()
    if not value:
        raise HTTPException(status_code=404, detail="stock_daily 无数据")
    return str(value)[:10]


def _data_coverage(date_str: str) -> Dict[str, Any]:
    """某交易日的日线覆盖：当日 bar 数 / 近 30 日最大 bar 数。"""
    from src.storage import DatabaseManager
    from sqlalchemy import text

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "select date, count(*) as n from stock_daily "
                "where date <= :d group by date order by date desc limit 30"
            ),
            {"d": date_str},
        ).fetchall()
    counts = {str(row[0])[:10]: int(row[1]) for row in rows}
    if not counts:
        return {"date": None, "bars": 0, "peak": 0, "ratio": 0.0}
    peak = max(counts.values())
    today = counts.get(date_str[:10], 0)
    return {
        "date": date_str[:10],
        "bars": today,
        "peak": peak,
        "ratio": round(today / peak, 4) if peak else 0.0,
    }


def _complete_trade_date(as_of: str, *, min_ratio: float = 0.6) -> str:
    """返回最近一个日线基本齐全的交易日。

    当日盘中/同步未完成时（例如收盘后只写进来几百只），横截面指标与「今日涨幅」
    会大面积失真；此时回退到上一根齐全的日线，并把实际日期返回给前端展示。
    """
    coverage = _data_coverage(as_of)
    if not coverage["peak"] or coverage["ratio"] >= min_ratio:
        return as_of
    from src.storage import DatabaseManager
    from sqlalchemy import text

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text(
                "select date, count(*) as n from stock_daily where date < :d "
                "group by date order by date desc limit 30"
            ),
            {"d": as_of},
        ).fetchall()
    peak = max((int(r[1]) for r in rows), default=0)
    for row in rows:
        if peak and int(row[1]) >= peak * min_ratio:
            return str(row[0])[:10]
    return as_of


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _methods_index(as_of: str) -> Dict[str, Any]:
    for candidate in (METHODS_DIR / f"index_{as_of}.json", METHODS_DIR / "_index_latest.json"):
        data = _load_json(candidate)
        if data:
            return data
    return {
        "as_of": as_of,
        "methods": [],
        "intersections": [],
        "limitations": ["尚无方法产物：运行 scripts/run_shortline_methods.py"],
    }


def _todos(as_of: str, limit: int = 20) -> List[Dict[str, Any]]:
    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    items = []
    for track in service.repo.list_tracks(limit=200):
        if track.status not in ("tracking", "pending"):
            continue
        due = track.next_review_at.date().isoformat() if track.next_review_at else None
        if due and due > as_of:
            continue
        items.append(
            {
                "type": "review_due",
                "track_id": track.track_id,
                "code": track.code,
                "hypothesis_key": track.hypothesis_key,
                "due": due,
                "status": track.status,
            }
        )
        if len(items) >= limit:
            break
    return items


@router.get("/home")
def workbench_home(
    date_str: Optional[str] = Query(default=None, alias="date"),
    refresh: bool = Query(default=False),
) -> Dict[str, Any]:
    """每日复盘首页：市场与板块、短线方法进度、长线变化、待办。

    结果进程内缓存 5 分钟、磁盘缓存 30 分钟；`refresh=true` 强制重算。
    """
    return _build_home(date_str, refresh=refresh)


def _build_home(date_str: Optional[str] = None, *, refresh: bool = False) -> Dict[str, Any]:
    review_date = _latest_trade_date(date_str)
    as_of = _complete_trade_date(review_date)
    key = f"{as_of}|{review_date}"
    if not refresh:
        cached = _home_cache_get(key, review_date, HOME_DISK_TTL)
        if cached:
            return cached

    index = _methods_index(as_of)
    market = _market_snapshot(as_of)
    coverage = _data_coverage(review_date)
    staleness: List[str] = []
    if as_of != review_date:
        staleness.append(
            f"{review_date} 日线只同步到 {coverage['bars']}/{coverage['peak']} 只，"
            f"横截面与涨幅按 {as_of} 收盘计算；复盘（涨停/炸板）仍用 {review_date}。"
        )
    payload = {
        "as_of": as_of,
        "review_as_of": review_date,
        "data_coverage": coverage,
        "market": market,
        "sections": {
            "market_review": _market_review(review_date),
            "broken_followup": _broken_followup(review_date),
            "shortline": index.get("methods") or [],
            "longterm_changes": _longterm_changes(as_of),
            "todos": _todos(as_of),
        },
        "intersections": index.get("intersections") or [],
        "limitations": (index.get("limitations") or []) + staleness,
    }
    _home_cache_put(key, review_date, payload)
    return {**payload, "cached": False, "cache_ttl_seconds": 0}


def _market_snapshot(as_of: str) -> Dict[str, Any]:
    """市场环境 + 全市场中位数，供首页顶栏使用（不做买卖判断）。"""
    from src.services.shortline import base

    table = base.metrics(as_of)
    if table.empty:
        return {"status": "pending", "reason": "无行情"}
    median_ret20 = float(table["market_median_ret20"].iloc[0])
    above_ma20 = float((table["close"] >= table["ma20"]).mean())
    return {
        "status": "ok",
        "as_of": as_of,
        "universe": int(len(table)),
        "median_ret20": round(median_ret20, 4),
        "above_ma20_ratio": round(above_ma20, 4),
        "evidence": f"全市场 20 日收益中位 {median_ret20 * 100:+.1f}%，站上 MA20 占比 {above_ma20 * 100:.0f}%",
    }


def _longterm_watchlist() -> List[str]:
    """长线关注池：config/workbench_longterm.json 的 watch_codes（可空）。"""
    path = PROJECT_ROOT / "config" / "workbench_longterm.json"
    if not path.is_file():
        return []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(code).strip() for code in (config.get("watch_codes") or []) if str(code).strip()]


def _longterm_changes(as_of: str, limit: int = 20) -> Dict[str, Any]:
    """长线区：跟踪中的长线假设 + 关注池，逐票补业绩/机构/户数证据（只取已披露期）。"""
    from src.services.profile_card_service import build_profile_card
    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    track_map: Dict[str, Dict[str, Any]] = {}
    for track in service.repo.list_tracks(limit=300):
        if track.status not in ("tracking", "pending") or track.horizon != "long":
            continue
        track_map[track.code] = {
            "code": track.code,
            "hypothesis_key": track.hypothesis_key,
            "hypothesis": track.hypothesis or "",
            "last_checked_at": track.last_checked_at.isoformat() if track.last_checked_at else None,
            "next_review_at": track.next_review_at.isoformat() if track.next_review_at else None,
        }

    codes: List[str] = list(track_map)
    for code in _longterm_watchlist():
        if code not in codes:
            codes.append(code)
    if not codes:
        return {
            "status": "limited",
            "reason": "尚无长线跟踪记录；可在 config/workbench_longterm.json 配置 watch_codes 先看证据",
            "rows": [],
        }

    rows = []
    for code in codes[:limit]:
        base_row = track_map.get(code, {"code": code, "hypothesis_key": "", "hypothesis": ""})
        try:
            card = build_profile_card(code, as_of=as_of)
            sections = card.get("sections") or {}
        except Exception as exc:  # noqa: BLE001 - 单票失败不阻断长线区
            rows.append({**base_row, "evidence_status": "error", "evidence_reason": str(exc)[:120]})
            continue
        earnings = sections.get("earnings") or {}
        institution = sections.get("institution") or {}
        holders = sections.get("holders") or {}
        basis = sections.get("basic") or {}
        rows.append(
            {
                **base_row,
                "name": (basis.get("data") or {}).get("name"),
                "industry": (basis.get("data") or {}).get("industry"),
                "evidence_status": "ok",
                "earnings": {
                    "status": earnings.get("status"),
                    "report_period": earnings.get("report_period"),
                    "ann_date": earnings.get("ann_date"),
                    "revenue_yoy": (earnings.get("data") or {}).get("revenue_yoy"),
                    "profit_yoy": (earnings.get("data") or {}).get("profit_yoy"),
                    "roe": (earnings.get("data") or {}).get("roe"),
                    "reason": earnings.get("reason"),
                },
                "institution": {
                    "status": institution.get("status"),
                    "report_period": institution.get("report_period"),
                    "fund_count": (institution.get("data") or {}).get("fund_count"),
                    "inst_ratio": (institution.get("data") or {}).get("inst_ratio"),
                    "inst_chg_pct": (institution.get("data") or {}).get("inst_chg_pct"),
                    "reason": institution.get("reason"),
                },
                "holders": {
                    "status": holders.get("status"),
                    "holder_asof": holders.get("holder_asof"),
                    "drop_periods": (holders.get("data") or {}).get("drop_periods"),
                    "reason": holders.get("reason"),
                },
            }
        )
    return {
        "status": "ok",
        "count": len(rows),
        "rows": rows,
        "limitation": "业绩按已公告报告期、机构按已披露十大流通股东；权重变化不等于主动加减仓",
    }


def _broken_followup(
    as_of: str,
    *,
    days: int = 5,
    per_day: int = 8,
    refresh: bool = False,
) -> Dict[str, Any]:
    from src.services.broken_followup_service import build_broken_followup

    try:
        return build_broken_followup(as_of, days=days, per_day=per_day, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - 炸板跟踪失败不影响复盘
        logger.warning("炸板后跟踪生成失败 as_of=%s: %s", as_of, exc)
        return {
            "as_of": as_of,
            "days": days,
            "status": "missing",
            "reason": str(exc)[:200],
            "summary": {},
            "groups": [],
            "limitations": [],
        }


@router.get("/review")
def workbench_review(
    date_str: Optional[str] = Query(default=None, alias="date"),
    refresh: bool = Query(default=False),
) -> Dict[str, Any]:
    """每日复盘：涨停/炸板/跌停池、连板梯队、板块涨停分布、情绪指标。"""
    as_of = _latest_trade_date(date_str)
    return _market_review(as_of, refresh=refresh)


@router.get("/broken-followup")
def workbench_broken_followup(
    date_str: Optional[str] = Query(default=None, alias="date"),
    days: int = Query(default=5, ge=2, le=20),
    per_day: int = Query(default=8, ge=3, le=30),
    refresh: bool = Query(default=False),
) -> Dict[str, Any]:
    """炸板后跟踪：按炸板日分组，看炸板当天资金分档与之后 T+1/T+2/至今走势。"""
    as_of = _latest_trade_date(date_str)
    return _broken_followup(as_of, days=days, per_day=per_day, refresh=refresh)


def _market_review(as_of: str, *, refresh: bool = False) -> Dict[str, Any]:
    from src.services.market_review_service import build_market_review

    try:
        return build_market_review(as_of, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - 复盘失败不拖垮首页
        logger.warning("市场复盘生成失败 as_of=%s: %s", as_of, exc)
        return {"as_of": as_of, "status": "missing", "reason": str(exc)[:200], "summary": {}, "ladder": [],
                "limit_up": [], "broken": [], "industry_limit_up": [], "industry_broken": [], "sectors": [],
                "limitations": []}


@router.get("/methods")
def workbench_methods(date_str: Optional[str] = Query(default=None, alias="date")) -> Dict[str, Any]:
    as_of = _complete_trade_date(_latest_trade_date(date_str))
    return _methods_index(as_of)


@router.get("/method/{method_id}")
def workbench_method_detail(
    method_id: str,
    date_str: Optional[str] = Query(default=None, alias="date"),
) -> Dict[str, Any]:
    as_of = _complete_trade_date(_latest_trade_date(date_str))
    for candidate in (METHODS_DIR / method_id / f"{as_of}.json", METHODS_DIR / method_id / "_latest.json"):
        data = _load_json(candidate)
        if data:
            return data
    raise HTTPException(status_code=404, detail=f"方法 {method_id} 暂无 {as_of} 产物")


@router.get("/profile/{code}")
def workbench_profile(
    code: str,
    as_of: Optional[str] = Query(default=None, alias="as_of"),
) -> Dict[str, Any]:
    """固定信息卡 + 该股全部跟踪假设（长短线并列，不合并）。"""
    from src.services.profile_card_service import build_profile_card

    try:
        card = build_profile_card(code, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    tracks = []
    for track in service.repo.list_tracks(limit=1000):
        if track.code != str(code):
            continue
        events = service.repo.list_events(track.id)
        tracks.append(
            {
                "track_id": track.track_id,
                "hypothesis_key": track.hypothesis_key,
                "title": track.title,
                "status": track.status,
                "horizon": track.horizon,
                "business_layer": track.business_layer,
                "hypothesis": track.hypothesis or "",
                "invalidation": track.invalidation or "",
                "next_review_at": track.next_review_at.isoformat() if track.next_review_at else None,
                "events": [
                    {
                        "event_type": event.event_type,
                        "source_type": event.source_type,
                        "summary": event.summary,
                        "observed_at": event.observed_at.isoformat() if event.observed_at else None,
                    }
                    for event in events[-10:]
                ],
            }
        )
    card["tracks"] = tracks
    return card


@router.get("/kline/{code}")
def workbench_kline(
    code: str,
    period: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    limit: int = Query(default=120, ge=10, le=600),
    as_of: Optional[str] = Query(default=None, alias="as_of"),
) -> Dict[str, Any]:
    """日/周/月 OHLC + 成交量。返回真实 OHLC，不接受“收盘折线冒充 K 线”。"""
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    sql = "select date, open, high, low, close, volume from stock_daily where code = :code"
    params: Dict[str, Any] = {"code": str(code), "limit": limit}
    if as_of:
        sql += " and date <= :as_of"
        params["as_of"] = as_of
    sql += " order by date desc limit :limit"
    with db.session_scope() as session:
        from sqlalchemy import text

        rows = session.execute(text(sql), params).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"无 {code} 行情")

    bars = [
        {
            "date": str(r[0])[:10],
            "open": float(r[1]) if r[1] is not None else None,
            "high": float(r[2]) if r[2] is not None else None,
            "low": float(r[3]) if r[3] is not None else None,
            "close": float(r[4]) if r[4] is not None else None,
            "volume": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ][::-1]

    if period != "daily":
        bars = _resample(bars, period)
    return {
        "code": str(code),
        "period": period,
        "as_of": bars[-1]["date"] if bars else None,
        "adjusted": "qfq",
        "bars": bars,
        "limitation": "前复权口径；周/月线由日线聚合（开=首日开，收=末日收，高低为区间极值）",
    }


def _resample(bars: List[Dict[str, Any]], period: str) -> List[Dict[str, Any]]:
    def bucket(day: str) -> str:
        parsed = date.fromisoformat(day)
        if period == "monthly":
            return f"{parsed.year}-{parsed.month:02d}"
        iso_week = parsed.isocalendar()
        return f"{iso_week.year}-W{iso_week.week:02d}"

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for bar in bars:
        grouped.setdefault(bucket(bar["date"]), []).append(bar)
    out = []
    for key in sorted(grouped):
        chunk = grouped[key]
        out.append(
            {
                "date": key,
                "open": chunk[0]["open"],
                "high": max(b["high"] for b in chunk if b["high"] is not None),
                "low": min(b["low"] for b in chunk if b["low"] is not None),
                "close": chunk[-1]["close"],
                "volume": sum(b["volume"] or 0 for b in chunk),
            }
        )
    return out
