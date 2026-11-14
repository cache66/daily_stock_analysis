# -*- coding: utf-8 -*-
"""固定信息卡：一只票的基本信息、规模估值、业绩、股东户数、机构持股、板块强度。

设计合同（见 `docs/个人策略文档/工作台实施规划-20261110.md` §4）：

- 六个维度各自返回 `status ∈ ok | pending | limited | missing`，并带 `as_of`/来源/原因；
  缺数**不省略、不推断、不用其它周期替代**。
- 只用本地已有数据：`tushare_stock_basic_list.csv`、`stock_daily`、`gdhs_*.csv`
  （股东户数）、`free_analyse_*.csv`（十大流通股东）、可选的 `daily_basic` /
  `fina_indicator` 缓存（由 `scripts/fetch_profile_data.py` 补齐）。
- 时点隔离：所有「披露类」数据按 `公告日(ann_date) <= as_of` 过滤，避免前视；
  行情按 `date <= as_of`。
- 本模块只读，不写数据库；页面/CLI/API 消费同一份结果。
"""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASIC_CSV = PROJECT_ROOT / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"
GDFX_DIR = PROJECT_ROOT / "data" / "cache" / "gdfx"
PROFILE_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "profile"
DIVIDEND_BASIC_DIR = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "daily_basic"

STATUS_OK = "ok"
STATUS_PENDING = "pending"
STATUS_LIMITED = "limited"
STATUS_MISSING = "missing"

# 机构类型归并（原始值来自东方财富「股东类型」）
FUND_TYPES = ("证券投资基金", "基金资产管理计划")
SOCIAL_SECURITY_TYPES = ("全国社保基金",)
QFII_TYPES = ("QFII",)
INSURANCE_TYPES = ("保险产品",)
BROKER_TYPES = ("证券公司",)
PRIVATE_FUND_TYPES = ("私募基金",)

HOLDER_MIN_PERIODS_FOR_PERCENTILE = 6


def _section(
    status: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    source: str = "",
    as_of: Optional[str] = None,
    reason: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": status}
    if source:
        out["source"] = source
    if as_of:
        out["as_of"] = as_of
    if reason:
        out["reason"] = reason
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


def _f(value: Any) -> Optional[float]:
    """安全转 float；NaN/inf/空值一律 None（页面显示为缺口而不是 0）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------- 基本信息


@lru_cache(maxsize=1)
def _basic_map() -> Dict[str, Dict[str, str]]:
    if not BASIC_CSV.is_file():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with BASIC_CSV.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            out[code] = {
                "name": str(row.get("name") or "").strip(),
                "industry": str(row.get("industry") or "").strip(),
                "market": str(row.get("market") or "").strip(),
                "area": str(row.get("area") or "").strip(),
            }
    return out


def _basic_section(code: str) -> Dict[str, Any]:
    row = _basic_map().get(code)
    if not row:
        return _section(STATUS_MISSING, source="stock_basic", reason="stock_basic 无此代码")
    try:
        from src.services.industry_profile import group_of

        board = group_of(row["industry"]) if row["industry"] else ""
    except Exception:  # noqa: BLE001 - 行业分组缺失不阻断基本信息
        board = ""
    # stock_basic 快照只含 ts_code/code/name/industry/area/market：
    # 上市日期与概念板块不在本表，明确列为缺字段，不用行业标签冒充概念板块。
    missing_fields = ["list_date", "concepts"]
    return _section(
        STATUS_LIMITED,
        source="stock_basic",
        data={
            "code": code,
            "name": row["name"],
            "market": row["market"],
            "industry": row["industry"] or None,
            "board": board or None,
            "area": row["area"] or None,
            "list_date": None,
            "concepts": None,
        },
        missing_fields=missing_fields,
        reason="stock_basic 快照不含上市日期/概念板块；需补数据源（不是 pending，是来源未接入）",
    )


# --------------------------------------------------------------------------- 行情


def _load_bars(code: str, *, as_of: Optional[str], limit: int = 160) -> List[Tuple[str, float, float]]:
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        sql = "SELECT date, close, high FROM stock_daily WHERE code = :code"
        params: Dict[str, Any] = {"code": code, "limit": limit}
        if as_of:
            sql += " AND date <= :as_of"
            params["as_of"] = as_of
        sql += " ORDER BY date DESC LIMIT :limit"
        rows = session.execute(text(sql), params).fetchall()
    bars = [(str(r[0])[:10], _f(r[1]), _f(r[2])) for r in rows]
    return [(d, c, h) for d, c, h in bars if c is not None and h is not None][::-1]


@lru_cache(maxsize=8)
def _market_metrics(as_of: str) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """全市场 code -> (20 日收益, 距 120 日高)；单次窗口查询 + 按 as_of 缓存。"""
    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    sql = """
        WITH ranked AS (
            SELECT code, close, high,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM stock_daily
            WHERE date <= :as_of AND date >= date(:as_of, '-260 day')
        )
        SELECT code,
               MAX(CASE WHEN rn = 1 THEN close END) AS last_close,
               MAX(CASE WHEN rn = 21 THEN close END) AS close_21,
               MAX(CASE WHEN rn <= 120 THEN high END) AS high120
        FROM ranked
        WHERE rn <= 120
        GROUP BY code
    """
    with db.session_scope() as session:
        from sqlalchemy import text

        rows = session.execute(text(sql), {"as_of": as_of}).fetchall()
    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for code, last_close, close_21, high120 in rows:
        c, c21, h = _f(last_close), _f(close_21), _f(high120)
        ret20 = (c / c21 - 1) if (c and c21) else None
        dist = (c / h - 1) if (c and h) else None
        out[str(code)] = (ret20, dist)
    return out


# --------------------------------------------------------------------------- 估值


def _daily_basic_files() -> List[Tuple[str, Path]]:
    """返回 [(交易日戳, 路径)]，跨目录按日期统一升序（同一天优先 profile 目录）。"""
    found: Dict[str, Path] = {}
    for directory in (DIVIDEND_BASIC_DIR, PROFILE_CACHE_DIR):
        if not directory.is_dir():
            continue
        for path in directory.glob("daily_basic_*.csv"):
            stamp = path.stem.replace("daily_basic_", "")
            if len(stamp) == 8 and stamp.isdigit():
                found[stamp] = path  # 后来的目录（profile）覆盖同日旧路径
    return sorted(found.items())


def _valuation_section(code: str, *, as_of: Optional[str]) -> Dict[str, Any]:
    target = (as_of or "9999-12-31").replace("-", "")
    candidates = [(stamp, path) for stamp, path in _daily_basic_files() if stamp <= target]
    if not candidates:
        return _section(
            STATUS_PENDING,
            source="daily_basic",
            reason="本地无 daily_basic 快照；运行 scripts/fetch_profile_data.py --valuation",
        )
    # 同一交易日可能同时存在 profile 与股息线缓存，取最新文件里命中该代码的一条
    for day, path in reversed(candidates):
        try:
            with path.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    raw_code = str(row.get("ts_code") or row.get("code") or "")
                    if not raw_code.startswith(code):
                        continue
                    data = {
                        "market_cap": _round((_f(row.get("total_mv")) or 0) / 1e4, 2) or None,
                        "float_cap": _round((_f(row.get("circ_mv")) or 0) / 1e4, 2) or None,
                        "pe_ttm": _round(_f(row.get("pe_ttm")), 3),
                        "pb": _round(_f(row.get("pb")), 3),
                        "turnover": _round(_f(row.get("turnover_rate") or row.get("turnover_rate_f")), 3),
                        "close": _round(_f(row.get("close")), 3),
                    }
                    missing_fields = [k for k in ("float_cap", "turnover") if data.get(k) is None]
                    status = STATUS_OK if not missing_fields else STATUS_LIMITED
                    return _section(
                        status,
                        source=f"daily_basic:{path.name}",
                        as_of=f"{day[:4]}-{day[4:6]}-{day[6:]}",
                        data=data,
                        reason="" if status == STATUS_OK else f"快照缺字段 {'/'.join(missing_fields)}（用 fetch_profile_data.py 重抓）",
                    )
        except OSError:
            continue
    return _section(STATUS_PENDING, source="daily_basic", reason="快照存在但无此代码")


# --------------------------------------------------------------------------- 业绩


def _earnings_section(code: str, *, as_of: Optional[str]) -> Dict[str, Any]:
    path = PROFILE_CACHE_DIR / f"fina_indicator_{code}.csv"
    if not path.is_file():
        return _section(
            STATUS_PENDING,
            source="fina_indicator",
            reason="本地无该票财务缓存；运行 scripts/fetch_profile_data.py --earnings --codes <code>",
        )
    rows: List[Dict[str, str]] = []
    assumed_lag_used = False
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ann = str(row.get("ann_date") or "")
            if ann:
                if as_of and ann > as_of.replace("-", ""):
                    continue  # 公告日晚于截止日 → 当时不可知，不能前视
            elif as_of:
                # AKShare 无公告日：用「报告期 + 90 天」保守判定，并在返回里标注这是假设
                end = str(row.get("end_date") or "")
                if not _available_after_lag(end, 90, as_of):
                    assumed_lag_used = True
                    continue
                assumed_lag_used = True
            rows.append(row)
    if not rows:
        return _section(STATUS_PENDING, source="fina_indicator", reason="截止日前无已公告报告期")
    latest = max(rows, key=lambda r: str(r.get("end_date") or ""))
    data = {
        "revenue_yoy": _round(_f(latest.get("or_yoy")), 2),
        "profit_yoy": _round(_f(latest.get("netprofit_yoy")), 2),
        "roe": _round(_f(latest.get("roe")), 2),
        "gross_margin": _round(_f(latest.get("grossprofit_margin")), 2),
        "ocf_ps": _round(_f(latest.get("ocfps")), 3),
    }
    if all(v is None for v in data.values()):
        return _section(STATUS_LIMITED, source="fina_indicator", reason="字段全部缺失（接口权限或口径不符）")
    return _section(
        STATUS_OK,
        source=str(latest.get("data_source") or "fina_indicator"),
        data=data,
        report_period=_fmt_date(latest.get("end_date")),
        ann_date=_fmt_date(latest.get("ann_date")),
        available_rule="report_period+90d（公告日未知，保守估计）" if assumed_lag_used else "ann_date",
    )


def _available_after_lag(end_date: str, lag_days: int, as_of: str) -> bool:
    """报告期 + lag_days 是否已早于/等于 as_of（用于公告日缺失时的保守过滤）。"""
    raw = str(end_date or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return False
    from datetime import date, timedelta

    try:
        available = date(int(raw[:4]), int(raw[4:6]), int(raw[6:])) + timedelta(days=lag_days)
    except ValueError:
        return False
    return available.isoformat() <= as_of


def _fmt_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or None


# --------------------------------------------------------------------------- 股东户数


def _gdhs_files() -> List[Tuple[str, Path]]:
    """返回 [(统计截止日, 文件路径)]，按日期升序。"""
    out: List[Tuple[str, Path]] = []
    if not GDFX_DIR.is_dir():
        return out
    for path in sorted(GDFX_DIR.glob("gdhs_*.csv")):
        stamp = path.stem.replace("gdhs_", "")
        if len(stamp) == 8 and stamp.isdigit():
            out.append((f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", path))
    return out


def _read_gdhs_row(path: Path, code: str) -> Optional[Dict[str, str]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("代码") or "").strip() == code:
                    return row
    except OSError:
        return None
    return None


def _holders_section(code: str, *, as_of: Optional[str]) -> Dict[str, Any]:
    periods: List[Tuple[str, Dict[str, str]]] = []
    for stat_date, path in _gdhs_files():
        if as_of:
            # 先按统计截止日粗筛，再用公告日精筛（公告前不可知）
            if stat_date > as_of:
                continue
        row = _read_gdhs_row(path, code)
        if not row:
            continue
        ann = _fmt_date(row.get("公告日期"))
        if as_of and ann and ann > as_of:
            continue
        periods.append((stat_date, row))
    if not periods:
        return _section(STATUS_PENDING, source="gdhs", reason="本地无该票户数披露（或公告日晚于截止日）")

    latest_date, latest = periods[-1]
    holders = _f(latest.get("股东户数-本次"))
    prev_holders = _f(latest.get("股东户数-上次"))
    if prev_holders is None and len(periods) >= 2:
        prev_holders = _f(periods[-2][1].get("股东户数-本次"))
    chg_pct = _f(latest.get("股东户数-增减比例"))

    drop_periods = 0
    series = [(d, _f(r.get("股东户数-本次"))) for d, r in periods]
    clean = [(d, v) for d, v in series if v is not None]
    for idx in range(len(clean) - 1, 0, -1):
        if clean[idx][1] < clean[idx - 1][1]:
            drop_periods += 1
        else:
            break

    percentile = None
    status = STATUS_OK
    reasons = []
    if len(clean) >= HOLDER_MIN_PERIODS_FOR_PERCENTILE and holders is not None:
        smaller = sum(1 for _, v in clean if v < holders)
        percentile = round(smaller / len(clean) * 100, 1)
    else:
        percentile = None
        status = STATUS_LIMITED
        reasons.append(f"历史仅 {len(clean)} 期，不足以计算分位（需 ≥{HOLDER_MIN_PERIODS_FOR_PERCENTILE} 期）")

    data = {
        "holders": None if holders is None else int(holders),
        "prev_holders": None if prev_holders is None else int(prev_holders),
        "chg_pct": _round(chg_pct, 2),
        "holders_percentile": percentile,
        "drop_periods": drop_periods,
        "avg_holder_value": _round(_f(latest.get("户均持股市值")), 2),
        "total_share": _round(_f(latest.get("总股本")), 0),
    }
    return _section(
        status,
        source=f"gdhs:{latest_date}",
        data=data,
        holder_asof=latest_date,
        ann_date=_fmt_date(latest.get("公告日期")),
        reason="；".join(reasons),
    )


# --------------------------------------------------------------------------- 机构持股


def _free_analyse_files() -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    if not GDFX_DIR.is_dir():
        return out
    for path in sorted(GDFX_DIR.glob("free_analyse_*.csv")):
        stamp = path.stem.replace("free_analyse_", "")
        if len(stamp) == 8 and stamp.isdigit():
            out.append((f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", path))
    return out


def _inst_rows(path: Path, code: str, *, as_of: Optional[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("股票代码") or "").strip() != code:
                    continue
                ann = _fmt_date(row.get("公告日"))
                if as_of and ann and ann > as_of:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _institution_section(code: str, *, as_of: Optional[str], total_share: Optional[float]) -> Dict[str, Any]:
    periods = _free_analyse_files()
    if not periods:
        return _section(STATUS_PENDING, source="free_analyse", reason="本地无十大流通股东数据")
    usable = []
    for report_period, path in periods:
        if as_of and report_period > as_of:
            continue
        rows = _inst_rows(path, code, as_of=as_of)
        if rows:
            usable.append((report_period, rows))
    if not usable:
        return _section(STATUS_PENDING, source="free_analyse", reason="该票无已披露报告期（或公告日晚于截止日）")

    report_period, rows = usable[-1]
    counts = {
        "fund_count": sum(1 for r in rows if str(r.get("股东类型")) in FUND_TYPES),
        "private_fund_count": sum(1 for r in rows if str(r.get("股东类型")) in PRIVATE_FUND_TYPES),
        "social_security_count": sum(1 for r in rows if str(r.get("股东类型")) in SOCIAL_SECURITY_TYPES),
        "qfii_count": sum(1 for r in rows if str(r.get("股东类型")) in QFII_TYPES),
        "insurance_count": sum(1 for r in rows if str(r.get("股东类型")) in INSURANCE_TYPES),
        "broker_count": sum(1 for r in rows if str(r.get("股东类型")) in BROKER_TYPES),
    }
    inst_types = set(FUND_TYPES) | set(SOCIAL_SECURITY_TYPES) | set(QFII_TYPES) | set(INSURANCE_TYPES) | set(BROKER_TYPES)
    inst_shares = sum(_f(r.get("期末持股-数量")) or 0.0 for r in rows if str(r.get("股东类型")) in inst_types)
    ratio = (inst_shares / total_share * 100.0) if total_share else None

    status = STATUS_OK
    reasons = []
    chg = None
    if len(usable) >= 2:
        prev_share = sum(
            _f(r.get("期末持股-数量")) or 0.0
            for r in usable[-2][1]
            if str(r.get("股东类型")) in inst_types
        )
        if prev_share:
            chg = round((inst_shares - prev_share) / prev_share * 100.0, 2)
    if ratio is None:
        status = STATUS_LIMITED
        reasons.append("缺总股本，无法计算机构持股占比")
    if len(usable) < 2:
        reasons.append("仅一个报告期，无环比")
    data = {
        **counts,
        "inst_share": _round(inst_shares, 0),
        "inst_ratio": _round(ratio, 2),
        "inst_chg_pct": chg,
        "holder_rows": len(rows),
    }
    return _section(
        status,
        source=f"free_analyse:{report_period}",
        data=data,
        report_period=report_period,
        ann_date=_fmt_date(rows[0].get("公告日")),
        reason="；".join(reasons),
    )


# --------------------------------------------------------------------------- 板块强度


def _sector_section(code: str, *, industry: Optional[str], as_of: Optional[str], dist_high120: Optional[float]) -> Dict[str, Any]:
    if not industry:
        return _section(STATUS_MISSING, source="industry", reason="行业未分类")
    if not as_of:
        return _section(STATUS_PENDING, source="stock_daily", reason="缺 as_of")
    metrics = _market_metrics(as_of)
    basic = _basic_map()
    by_industry: Dict[str, List[float]] = {}
    for other_code, (ret20, _dist) in metrics.items():
        info = basic.get(other_code)
        if not info or not info["industry"] or ret20 is None:
            continue
        by_industry.setdefault(info["industry"], []).append(ret20)
    peers = by_industry.get(industry) or []
    if not peers:
        return _section(STATUS_MISSING, source="stock_daily", reason="该行业无可用行情样本")
    peers_sorted = sorted(peers)
    median = peers_sorted[len(peers_sorted) // 2]
    better = sum(1 for v in peers_sorted if v < median)
    pct = round(better / len(peers_sorted) * 100, 1)
    status = STATUS_OK if len(peers) >= 20 else STATUS_LIMITED
    return _section(
        status,
        source="stock_daily+industry",
        as_of=as_of,
        data={
            "industry": industry,
            "industry_ret20_median": _round(median, 4),
            "industry_pct": pct,
            "peer_count": len(peers),
            "dist_high120": _round(dist_high120, 4),
        },
        reason="" if status == STATUS_OK else f"行业样本仅 {len(peers)} 只",
    )


# --------------------------------------------------------------------------- 入口


def build_profile_card(code: str, *, as_of: Optional[str] = None) -> Dict[str, Any]:
    """构建固定信息卡；`as_of` 缺省取库内最新交易日。"""
    code = str(code or "").strip()
    if not code:
        raise ValueError("code 不能为空")

    bars = _load_bars(code, as_of=as_of)
    if not bars:
        return {
            "code": code,
            "as_of": as_of,
            "sections": {
                "basic": _basic_section(code),
                "valuation": _section(STATUS_MISSING, source="stock_daily", reason="库内无该票行情"),
                "earnings": _earnings_section(code, as_of=as_of),
                "holders": _holders_section(code, as_of=as_of),
                "institution": _institution_section(code, as_of=as_of, total_share=None),
                "sector": _section(STATUS_MISSING, source="stock_daily", reason="库内无该票行情"),
            },
            "limitations": ["库内无该票行情，价格相关维度不可用"],
        }

    effective_as_of = as_of or bars[-1][0]
    basic = _basic_section(code)
    holders = _holders_section(code, as_of=effective_as_of)
    total_share = ((holders.get("data") or {}).get("total_share")) if holders.get("data") else None

    market = _market_metrics(effective_as_of)
    ret20, dist_high120 = market.get(code, (None, None))

    sections = {
        "basic": basic,
        "valuation": _valuation_section(code, as_of=effective_as_of),
        "earnings": _earnings_section(code, as_of=effective_as_of),
        "holders": holders,
        "institution": _institution_section(code, as_of=effective_as_of, total_share=total_share),
        "sector": _sector_section(
            code,
            industry=(basic.get("data") or {}).get("industry") if basic.get("data") else None,
            as_of=effective_as_of,
            dist_high120=dist_high120,
        ),
    }
    limitations: List[str] = []
    if status_of(sections["holders"]) == STATUS_LIMITED:
        limitations.append("股东户数历史期数不足，分位不可用")
    if sections["institution"]["status"] != STATUS_OK:
        limitations.append("机构持股只用已披露报告期；权重变化不等于主动加减仓")
    if ret20 is not None:
        sections["price"] = {
            "status": STATUS_OK,
            "source": "stock_daily",
            "as_of": bars[-1][0],
            "data": {
                "close": _round(bars[-1][1], 3),
                "ret20": _round(ret20, 4),
                "last_date": bars[-1][0],
            },
        }
    return {
        "code": code,
        "as_of": effective_as_of,
        "sections": sections,
        "limitations": limitations,
    }


def status_of(section: Dict[str, Any]) -> str:
    return str(section.get("status") or STATUS_MISSING)


def summarize_missing(card: Dict[str, Any]) -> Sequence[str]:
    """返回所有非 ok 维度的一句话摘要，供 CLI/页面提示。"""
    out = []
    for name, section in (card.get("sections") or {}).items():
        if not isinstance(section, dict) or "status" not in section:
            continue
        status = status_of(section)
        if status != STATUS_OK:
            out.append(f"{name}: {status} {section.get('reason') or ''}".strip())
    return out
