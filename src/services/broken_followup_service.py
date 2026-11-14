# -*- coding: utf-8 -*-
"""炸板后跟踪：以「炸板事件」为单位，看炸板当天资金与之后 1~N 个交易日的走势。

要回答的问题：
- 某天炸板的票，之后是修复回来，还是就废了；
- 炸板当天是「大单/超大单在买」还是「大单在卖、散户在接」——这决定后面还有没有资金维护；
- 炸板当天成交额有多大（成交额越大，越可能有资金自救）。

数据来源（公开、无需登录）：
- 炸板事件与成交额：东财炸板池（复用 `market_review_service` 的按日缓存）；
- 炸板当天与之后每日资金分档：东财个股资金流历史 `stock_individual_fund_flow`
  （含每日收盘价、涨跌幅、主力/超大单/大单/中单/小单净额），按 `as_of` 缓存；
- 当日炸板的票再叠加分笔分档买卖笔数（`tick_flow_service`，只有最近交易日）。

口径：
- 「主力」= 超大单 + 大单；「散户」= 中单 + 小单；
- 「修复」= 炸板日之后的最高收盘价重新站上炸板日收盘价；「已站稳」= 最新收盘价站上；
- 判定标签只描述事实组合，不构成买卖建议。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.broken_score import compute_break_score

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "broken_followup"
FLOW_DIR = PROJECT_ROOT / "data" / "cache" / "fund_flow_hist"
CLOSES_DIR = PROJECT_ROOT / "data" / "cache" / "daily_closes"
FOLLOWUP_SCHEMA = 6
FLOW_SCHEMA = 1
CLOSES_SCHEMA = 1


def _cache_path(as_of: str, days: int, per_day: int) -> Path:
    return CACHE_DIR / f"followup_{as_of.replace('-', '')}_{days}d_{per_day}.json"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _trade_dates(as_of: str, count: int) -> List[str]:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text("select distinct date from stock_daily where date <= :d order by date desc limit :n"),
            {"d": as_of, "n": count},
        ).fetchall()
    return [str(row[0])[:10] for row in rows]


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _avg_amount(codes: List[str], before_date: str, window: int = 20) -> Dict[str, float]:
    """事件日之前 window 个交易日的平均成交额（不含事件日，避免前视）。"""
    from sqlalchemy import text

    from src.storage import DatabaseManager

    if not codes:
        return {}
    db = DatabaseManager.get_instance()
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    params: Dict[str, Any] = {f"c{i}": code for i, code in enumerate(codes)}
    params["d"] = before_date
    with db.session_scope() as session:
        rows = session.execute(
            text(
                f"select code, date, amount from stock_daily "
                f"where code in ({placeholders}) and date < :d order by code, date desc"
            ),
            params,
        ).fetchall()
    buckets: Dict[str, List[float]] = {}
    for code, _day, amount in rows:
        values = buckets.setdefault(str(code), [])
        if len(values) < window and amount:
            values.append(float(amount))
    return {code: sum(values) / len(values) for code, values in buckets.items() if values}


def _flow_cache_candidates(code: str, as_of: str) -> List[Path]:
    """同一只票的历史资金流与 as_of 无关：先看当天目录，再退回任意既有缓存。"""
    paths = [FLOW_DIR / as_of.replace("-", "") / f"{code}.json"]
    if FLOW_DIR.is_dir():
        for path in sorted(FLOW_DIR.glob(f"*/{code}.json"), reverse=True):
            if path not in paths:
                paths.append(path)
    return paths


def _flow_history(code: str, as_of: str, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """某票最近一段每日资金分档（东财公开资金流历史），按 as_of 缓存且可跨日复用。"""
    cache_path = FLOW_DIR / as_of.replace("-", "") / f"{code}.json"
    if not refresh:
        for path in _flow_cache_candidates(code, as_of):
            cached = _load_json(path)
            if cached and cached.get("schema") == FLOW_SCHEMA:
                return cached.get("days") or {}

    market = "sh" if code.startswith(("6", "5", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    try:
        import akshare as ak

        from src.services.em_guard import em_call

        frame = em_call(ak.stock_individual_fund_flow, stock=code, market=market)
    except Exception:  # noqa: BLE001 - 单票失败只影响这一行
        return {}

    days: Dict[str, Dict[str, Any]] = {}
    for _, row in frame.iterrows():
        day = str(row.get("日期"))[:10]
        if not day:
            continue
        days[day] = {
            "close": _num(row.get("收盘价")),
            "change_pct": _num(row.get("涨跌幅")),
            "main": _num(row.get("主力净流入-净额")),
            "main_ratio": _num(row.get("主力净流入-净占比")),
            "super": _num(row.get("超大单净流入-净额")),
            "big": _num(row.get("大单净流入-净额")),
            "mid": _num(row.get("中单净流入-净额")),
            "small": _num(row.get("小单净流入-净额")),
        }
    _save_json(cache_path, {"schema": FLOW_SCHEMA, "code": code, "as_of": as_of, "days": days})
    return days


def _daily_closes(code: str, as_of: str, *, refresh: bool = False) -> Dict[str, float]:
    """东财资金流不可用时的收盘兜底（新浪前复权日线），只补收盘价。"""
    cache_path = CLOSES_DIR / as_of.replace("-", "") / f"{code}.json"
    if not refresh:
        cached = _load_json(cache_path)
        if cached and cached.get("schema") == CLOSES_SCHEMA:
            return cached.get("days") or {}

    market = "sh" if code.startswith(("6", "5", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    try:
        import akshare as ak

        frame = ak.stock_zh_a_daily(
            symbol=f"{market}{code}",
            start_date=datetime.now().replace(year=datetime.now().year - 1).strftime("%Y%m%d"),
            end_date=as_of.replace("-", ""),
            adjust="qfq",
        )
    except Exception:  # noqa: BLE001 - 兜底失败就留空
        return {}

    days: Dict[str, float] = {}
    for _, row in frame.iterrows():
        day = str(row.get("date"))[:10]
        close = _num(row.get("close"))
        if day and close:
            days[day] = close
    _save_json(cache_path, {"schema": CLOSES_SCHEMA, "code": code, "as_of": as_of, "days": days})
    return days


def _label(
    main: Optional[float],
    retail: Optional[float],
    recovered: bool,
    held: bool,
    has_followup: bool,
    source: str = "eastmoney",
) -> str:
    if main is None:
        money = "资金未知"
    elif source == "ths":
        # 同花顺只有主动买卖净额，没有分档，不能叫「大单承接/出货」
        money = "资金净买" if main > 0 else "资金净卖"
    elif main > 0 and (retail is None or retail < 0):
        money = "大单承接"
    elif main < 0 and (retail is None or retail > 0):
        money = "大单出货"
    else:
        money = "多空混合"
    if not has_followup:
        price = "待观察"  # 当天炸板：后面还没有交易日可供判断
    elif held:
        price = "已站稳"
    elif recovered:
        price = "冲高回落"
    else:
        price = "未修复"
    return f"{money}·{price}"


def build_broken_followup(
    as_of: str,
    *,
    days: int = 5,
    per_day: int = 8,
    refresh: bool = False,
    with_ticks: bool = True,
    tick_limit: int = 12,
) -> Dict[str, Any]:
    cache_path = _cache_path(as_of, days, per_day)
    if not refresh:
        cached = _load_json(cache_path)
        if cached and cached.get("schema") == FOLLOWUP_SCHEMA:
            return cached

    from src.services.market_review_service import build_market_review

    dates = sorted(_trade_dates(as_of, days))
    if len(dates) < 2:
        return {
            "schema": FOLLOWUP_SCHEMA,
            "as_of": as_of,
            "days": days,
            "status": "missing",
            "reason": "库内交易日不足，无法做炸板后跟踪",
            "groups": [],
            "summary": {},
            "limitations": [],
        }

    # 1) 每个交易日的炸板事件（按成交额取前 per_day 只）
    events: List[Dict[str, Any]] = []
    for day in dates:
        try:
            pool = build_market_review(day).get("broken") or []
        except Exception:  # noqa: BLE001 - 单日失败跳过
            pool = []
        pool = sorted(pool, key=lambda item: -(item.get("amount") or 0))[:per_day]
        for item in pool:
            events.append(
                {
                    "code": str(item.get("code") or "").zfill(6),
                    "name": item.get("name"),
                    "industry": item.get("industry"),
                    "break_date": day,
                    "break_count": item.get("break_count"),
                    "change_pct": item.get("change_pct"),
                    "price": item.get("price"),
                    "limit_price": item.get("limit_price"),
                    "amount": item.get("amount"),
                    "turnover": item.get("turnover"),
                    "limit_stat": item.get("limit_stat"),
                    "first_seal": item.get("first_seal"),
                    "amplitude": item.get("amplitude"),
                }
            )

    # 2) 拉取这些票的每日资金分档（并发 + 缓存）
    codes = sorted({item["code"] for item in events})
    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    close_fallback: Dict[str, Dict[str, float]] = {}
    fallback = {"n": 0}

    def load(code: str) -> None:
        flows[code] = _flow_history(code, as_of)
        if not flows[code]:
            fallback["n"] += 1
        # 资金流缓存可能只到上一个交易日（东财当天数据要收盘后才有），缺 as_of 就用新浪补收盘
        if as_of not in flows[code]:
            close_fallback[code] = _daily_closes(code, as_of)

    if codes:
        with ThreadPoolExecutor(max_workers=3) as pool_exec:
            list(pool_exec.map(load, codes))

    # 东财当日资金缺失时，用同花顺即时净额兜底（口径不同，单独标 source）
    ths_net: Dict[str, Dict[str, float]] = {}
    if any(as_of not in (flows.get(code) or {}) for code in codes):
        from src.services.ths_fund_flow import fetch_daily_net

        ths_net = fetch_daily_net(as_of)

    # 2.5) 事件日之前 20 日均额（按日批量查询）
    by_date: Dict[str, List[str]] = {}
    for item in events:
        by_date.setdefault(item["break_date"], []).append(item["code"])
    avg_amounts: Dict[str, Dict[str, float]] = {}
    for day, codes_of_day in by_date.items():
        avg_amounts[day] = _avg_amount(codes_of_day, day)

    # 2.6) 炸板时段成交（历史用 5 分钟线，当日用分笔）
    from src.services.break_window_service import batch_analyze

    window_map = batch_analyze(events, as_of)

    # 3) 计算炸板后的走势、打分与判定
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in events:
        history = flows.get(item["code"]) or {}
        base = history.get(item["break_date"]) or {}
        fallback_days = close_fallback.get(item["code"]) or {}
        base_close = base.get("close") or fallback_days.get(item["break_date"]) or item.get("price")
        later_days = [
            day for day in sorted(set(history) | set(fallback_days))
            if day > item["break_date"] and day <= as_of
        ]
        closes = []
        for day in later_days:
            value = (history.get(day) or {}).get("close") or fallback_days.get(day)
            if value:
                closes.append((day, value))
        t1 = closes[0][1] if len(closes) >= 1 else None
        t2 = closes[1][1] if len(closes) >= 2 else None
        latest = closes[-1][1] if closes else None
        max_close = max((close for _, close in closes), default=None)

        def ret(value: Optional[float]) -> Optional[float]:
            if value is None or not base_close:
                return None
            return round(value / base_close - 1, 4)

        recovered = bool(max_close and base_close and max_close >= base_close)
        held = bool(latest and base_close and latest >= base_close)
        main = base.get("main")
        main_ratio = base.get("main_ratio")
        fund_source = "eastmoney" if base else "none"
        if main_ratio is not None:
            main_ratio = main_ratio / 100  # 东财返回百分数，统一成小数
        if not base and item["break_date"] == as_of:
            ths = ths_net.get(item["code"]) or {}
            ths_amount = ths.get("amount") or item.get("amount")
            if ths.get("net") is not None:
                main = ths["net"]
                main_ratio = (main / ths_amount) if ths_amount else None
                fund_source = "ths"
        retail = None
        if base.get("mid") is not None or base.get("small") is not None:
            retail = (base.get("mid") or 0) + (base.get("small") or 0)

        amount = item.get("amount")
        avg20 = (avg_amounts.get(item["break_date"]) or {}).get(item["code"])
        amount_ratio = round(amount / avg20, 3) if amount and avg20 else None
        window = window_map.get(f"{item['code']}_{item['break_date']}") or {}
        break_ratio = window.get("moment_ratio") if window.get("status") == "ok" else None
        scored = compute_break_score(
            break_ratio=break_ratio,
            main_source=fund_source,
            # 今日有逐笔窗口 → 用「炸板后」分档净额；历史只有日频资金流 → 退回当日分档净额
            super_net=window.get("super_net") if window.get("super_net") is not None else base.get("super"),
            big_net=window.get("big_net") if window.get("big_net") is not None else base.get("big"),
            amount_ratio=amount_ratio,
            main_ratio=main_ratio,
            retail_net=retail,
            amount=amount,
            break_count=item.get("break_count"),
            turnover=item.get("turnover"),
            change_pct=item.get("change_pct"),
        )
        groups.setdefault(item["break_date"], []).append(
            {
                **item,
                "base_close": base_close,
                "funds": {
                    "source": fund_source,
                    "main": main,
                    "main_ratio": main_ratio,
                    "super": base.get("super"),
                    "big": base.get("big"),
                    "mid": base.get("mid"),
                    "small": base.get("small"),
                    "retail": retail,
                    "available": bool(base) or fund_source == "ths",
                },
                "followup": {
                    "days": [day for day, _ in closes],
                    "t1_close": t1,
                    "t2_close": t2,
                    "latest_close": latest,
                    "t1_ret": ret(t1),
                    "t2_ret": ret(t2),
                    "latest_ret": ret(latest),
                    "max_close": max_close,
                    "recovered": recovered,
                    "held": held,
                },
                "label": _label(main, retail, recovered, held, bool(closes), fund_source),
                "amount_ratio": amount_ratio,
                "break_window": {
                    "status": window.get("status"),
                    "method": window.get("method"),
                    "break_ratio": break_ratio,
                    "moment_ratio": break_ratio,
                    "open_ratio": window.get("open_ratio"),
                    "moment_amount": window.get("moment_amount"),
                    "open_amount": window.get("open_amount"),
                    "break_time": window.get("break_time"),
                    "super_net": window.get("super_net"),
                    "big_net": window.get("big_net"),
                    "break_amount": window.get("break_amount"),
                    "break_buy_amount": window.get("break_buy_amount"),
                    "break_sell_amount": window.get("break_sell_amount"),
                    "big_buy_amount": window.get("big_buy_amount"),
                    "big_sell_amount": window.get("big_sell_amount"),
                    "big_buy_trades": window.get("big_buy_trades"),
                    "big_sell_trades": window.get("big_sell_trades"),
                    "big_buy_share": window.get("big_buy_share"),
                    "big_net_amount": window.get("big_net_amount"),
                    "seal_time": window.get("seal_time"),
                },
                "score": scored["score"],
                "prob": scored["prob"],
                "grade": scored["grade"],
                "score_reasons": scored["reasons"],
                "score_missing": scored["missing"],
            }
        )

    # 4) 当日炸板的票补分笔分档（仅最近交易日可得）
    if with_ticks:
        from src.services.tick_flow_service import compute_tick_flow

        targets = [row for row in groups.get(as_of, [])][:tick_limit]

        def run_tick(row: Dict[str, Any]) -> None:
            row["tick"] = compute_tick_flow(row["code"], as_of)

        if targets:
            with ThreadPoolExecutor(max_workers=3) as pool_exec:
                list(pool_exec.map(run_tick, targets))

    ordered_groups = []
    labels: Dict[str, int] = {}
    grades: Dict[str, int] = {}
    for day in sorted(groups, reverse=True):
        rows = sorted(groups[day], key=lambda item: (-(item.get("score") or 0), -(item.get("amount") or 0)))
        for row in rows:
            labels[row["label"]] = labels.get(row["label"], 0) + 1
            grades[row["grade"]] = grades.get(row["grade"], 0) + 1
        ordered_groups.append(
            {
                "break_date": day,
                "count": len(rows),
                "big_amount": sum(1 for row in rows if (row.get("amount") or 0) >= 5e8),
                "rows": rows,
            }
        )

    # 试盘次数 / 板块封板家数（来自 limit_touch_event 长期累积，排除 ST 与北交所）
    from src.repositories.limit_touch_repo import LimitTouchRepository

    touch_repo = LimitTouchRepository()
    codes_all = sorted({row["code"] for group in ordered_groups for row in group["rows"]})
    touch_map = touch_repo.touch_counts(codes_all, as_of, window=10) if codes_all else {}
    industry_by_day: Dict[str, Dict[str, int]] = {}
    repeat = 0
    for group in ordered_groups:
        day = group["break_date"]
        industry_counts = industry_by_day.setdefault(day, touch_repo.industry_seal_counts(day))
        for row in group["rows"]:
            stats = touch_map.get(row["code"]) or {}
            touch = stats.get("touch") or 0
            row["touch10"] = touch
            row["seal10"] = stats.get("seal") or 0
            row["break10"] = stats.get("break") or 0
            row["seal_rate"] = round(stats["seal"] / touch, 3) if touch else None
            row["industry_limit"] = industry_counts.get(row.get("industry") or "", 0)
            if (touch or 0) >= 2:
                repeat += 1
    watchlist = touch_repo.touch_ranking(as_of, window=10, min_touch=2, limit=20)
    hot_industries = [
        {"industry": industry, "count": count}
        for industry, count in sorted(
            (industry_by_day.get(as_of) or {}).items(), key=lambda item: -item[1]
        )[:8]
        if industry
    ]

    total = sum(len(group["rows"]) for group in ordered_groups)
    payload = {
        "schema": FOLLOWUP_SCHEMA,
        "as_of": as_of,
        "days": days,
        "dates": dates,
        "per_day": per_day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if ordered_groups else "missing",
        "source": "eastmoney_zt_pool+fund_flow_history",
        "fund_flow_fallback_codes": fallback["n"],
        "summary": {
            "events": total,
            "groups": len(ordered_groups),
            "supported": sum(count for label, count in labels.items() if label.startswith("大单承接")),
            "dumped": sum(count for label, count in labels.items() if label.startswith("大单出货")),
            "recovered": sum(1 for group in ordered_groups for row in group["rows"] if row["followup"]["recovered"]),
            "pending": sum(1 for group in ordered_groups for row in group["rows"] if not row["followup"]["days"]),
            "labels": labels,
            "grades": grades,
            "repeat_touch": repeat,
            "first_touch": max(0, total - repeat),
        },
        "watchlist": watchlist,
        "hot_industries": hot_industries,
        "groups": ordered_groups,
        "limitations": [
            "「主力」= 超大单 + 大单，「散户」= 中单 + 小单，均为东财资金流按逐笔成交分档的净额",
            f"资金流取数失败的票（{fallback['n']} 只）只补新浪前复权收盘，资金列留空、判定标「资金未知」",
            "东财当日资金缺失时，当日炸板票改用同花顺「主动买卖净额」兜底（字段 funds.source=ths，"
            "口径与东财分档不同，判定写作「资金净买/净卖」）",
            "「修复」= 炸板日之后最高收盘重新站上炸板日收盘；「已站稳」= 最新收盘站上炸板日收盘，仅描述事实",
            "炸板时段 = 首封之后、价格低于涨停价的区间；历史用 5 分钟线近似（无买卖方向），当日用 3 秒分笔",
            "炸板日之前没有更早的收盘序列时，T+1/T+2 可能为空（停牌或数据缺失）",
            "逐笔买/卖笔数只有最近交易日可取，只出现在当日炸板的票上",
            "「试盘10」= 近 10 个交易日里该票封板或炸板的天数（同一天多次开板只算 1 次），"
            "来自 limit_touch_event 表；东财触板接口只保留最近约 15 个交易日，更早数据靠每日落库累积",
            "「板块涨停」= 该票所属行业在炸板日的封板家数；≥7 只属于板块过热（历史后续表现最差）",
        ],
    }
    _save_json(cache_path, payload)
    return payload
