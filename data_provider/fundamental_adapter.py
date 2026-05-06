# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import BoundedSemaphore, RLock, Thread
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_SUPPORTED_FUNDAMENTAL_BLOCKS = (
    "financial",
    "forecast",
    "quick_report",
    "dividend",
    "institution",
    "top10",
)
_DEFAULT_DF_CANDIDATE_TIMEOUT_SECONDS = 8.0
_DEFAULT_DF_CANDIDATE_TIMEOUT_WORKERS = 8
_DEFAULT_MARKET_EXPECTATION_CACHE_MAX_AGE_DAYS = 1
_DEFAULT_MARKET_EXPECTATION_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "market_expectation"

_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}

_FINANCIAL_SERIES_KEYWORD_MAP: Dict[str, List[str]] = {
    "revenue_yoy": ["营业收入同比", "营收同比", "收入同比", "同比增长"],
    "net_profit_yoy": ["净利润同比", "净利同比", "归母净利润同比"],
    "roe": ["净资产收益率", "ROE", "净资产收益"],
    "gross_margin": ["毛利率"],
    "revenue": ["营业总收入", "营业收入", "营收"],
    "net_profit_parent": ["归母净利润", "母公司股东净利润", "净利润"],
    "operating_cash_flow": ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"],
    "accounts_receivable": ["应收账款", "应收款项", "应收票据及应收账款"],
    "inventory": ["存货"],
    "contract_liabilities": ["合同负债", "预收款项"],
    "selling_expense_rate": ["销售费用率", "营业费用率"],
    "management_expense_rate": ["管理费用率"],
    "r_and_d_expense_rate": ["研发费用率", "研发投入占比"],
    "net_margin": ["销售净利率", "净利率", "净利润率"],
    "operating_margin": ["营业利润率"],
}


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except Exception:
        return None


def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s


def _pick_by_keywords(row: pd.Series, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)


def _filter_rows_by_code(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))]
    if not code_cols:
        return df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()


def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[:max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }


def _find_first_matching_column(df: pd.DataFrame, keywords: List[str]) -> Optional[str]:
    for col in df.columns:
        col_s = str(col)
        if any(keyword in col_s for keyword in keywords):
            return str(col)
    return None


def _sort_rows_by_report_date(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    report_date_col = _find_first_matching_column(df, _DIVIDEND_KEYWORD_MAP["report_date"])
    if report_date_col is None:
        return df

    work_df = df.copy()
    try:
        parsed_dates = pd.to_datetime(work_df[report_date_col], errors="coerce")
    except Exception:
        return df

    if parsed_dates.isna().all():
        return df

    work_df["_normalized_report_date"] = parsed_dates
    work_df = work_df.sort_values("_normalized_report_date", ascending=False, na_position="last")
    return work_df.drop(columns=["_normalized_report_date"], errors="ignore")


def _extract_relevant_rows(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    filtered = _filter_rows_by_code(df, stock_code)
    if not filtered.empty:
        return _sort_rows_by_report_date(filtered)

    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    if code_cols:
        return pd.DataFrame()

    return _sort_rows_by_report_date(df)


def _build_financial_series_item(row: pd.Series) -> Dict[str, Any]:
    report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))

    payload: Dict[str, Any] = {"report_date": report_date}
    for field, keywords in _FINANCIAL_SERIES_KEYWORD_MAP.items():
        payload[field] = _safe_float(_pick_by_keywords(row, keywords))
    return {key: value for key, value in payload.items() if value is not None}


def _period_column_to_report_date(value: Any) -> Optional[str]:
    text = _safe_str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return _normalize_report_date(text)


def _field_from_wide_financial_metric(metric_name: Any) -> Optional[str]:
    text = _safe_str(metric_name)
    if not text:
        return None
    if any(token in text for token in ("\u8425\u4e1a\u603b\u6536\u5165\u589e\u957f\u7387", "\u8425\u4e1a\u6536\u5165\u589e\u957f\u7387", "\u8425\u6536\u589e\u957f\u7387")):
        return "revenue_yoy"
    if any(token in text for token in ("\u5f52\u5c5e\u6bcd\u516c\u53f8\u51c0\u5229\u6da6\u589e\u957f\u7387", "\u5f52\u6bcd\u51c0\u5229\u6da6\u589e\u957f\u7387")):
        return "net_profit_yoy"
    if "\u51c0\u8d44\u4ea7\u6536\u76ca\u7387" in text or "ROE" in text.upper():
        return "roe"
    if "\u6bdb\u5229\u7387" in text:
        return "gross_margin"
    if text in ("\u8425\u4e1a\u603b\u6536\u5165", "\u8425\u4e1a\u6536\u5165"):
        return "revenue"
    if text in ("\u5f52\u6bcd\u51c0\u5229\u6da6", "\u5f52\u5c5e\u6bcd\u516c\u53f8\u51c0\u5229\u6da6"):
        return "net_profit_parent"
    if "\u7ecf\u8425\u73b0\u91d1\u6d41" in text or "\u7ecf\u8425\u6d3b\u52a8\u51c0\u73b0\u91d1" in text:
        return "operating_cash_flow"
    return None


def _build_wide_financial_report_series(fin_df: pd.DataFrame, max_periods: int = 12) -> List[Dict[str, Any]]:
    if fin_df is None or fin_df.empty:
        return []
    metric_col = next((col for col in fin_df.columns if _safe_str(col) == "\u6307\u6807"), None)
    if metric_col is None:
        return []
    period_columns = [
        col
        for col in fin_df.columns
        if _period_column_to_report_date(col) is not None
    ]
    if not period_columns:
        return []

    items_by_period: Dict[str, Dict[str, Any]] = {}
    for _, row in fin_df.iterrows():
        field_name = _field_from_wide_financial_metric(row.get(metric_col))
        if not field_name:
            continue
        for period_col in period_columns:
            report_date = _period_column_to_report_date(period_col)
            if not report_date:
                continue
            value = _safe_float(row.get(period_col))
            if value is None:
                continue
            item = items_by_period.setdefault(report_date, {"report_date": report_date})
            if field_name == "roe" and item.get("roe") is not None:
                continue
            item[field_name] = value

    series = [
        item
        for _, item in sorted(items_by_period.items(), key=lambda pair: pair[0], reverse=True)
        if any(key != "report_date" for key in item)
    ]
    return series[: max(1, int(max_periods))]


def _build_financial_report_series(
    fin_df: pd.DataFrame,
    stock_code: str,
    max_periods: int = 12,
) -> List[Dict[str, Any]]:
    work_df = _extract_relevant_rows(fin_df, stock_code)
    if work_df.empty:
        return []

    wide_series = _build_wide_financial_report_series(work_df, max_periods=max_periods)
    if wide_series:
        return wide_series

    series: List[Dict[str, Any]] = []
    seen_report_dates = set()
    max_items = max(1, int(max_periods))

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        item = _build_financial_series_item(row)
        if not item:
            continue

        report_date = item.get("report_date")
        if report_date:
            if report_date in seen_report_dates:
                continue
            seen_report_dates.add(report_date)

        series.append(item)
        if len(series) >= max_items:
            break

    return series


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    work_df = _extract_relevant_rows(df, stock_code)
    if work_df.empty:
        return None
    return work_df.iloc[0]


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def __init__(self) -> None:
        self._df_candidates_cache_lock = RLock()
        self._df_candidates_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._df_candidate_timeout_seconds = _DEFAULT_DF_CANDIDATE_TIMEOUT_SECONDS
        self._df_candidate_timeout_slots = BoundedSemaphore(_DEFAULT_DF_CANDIDATE_TIMEOUT_WORKERS)
        self._tushare_sector_rankings_cache_lock = RLock()
        self._tushare_sector_rankings_cache: Dict[str, Any] = {
            "cache_date": "",
            "attempted": False,
            "top": [],
            "bottom": [],
            "source": None,
            "errors": [],
        }
        self._market_expectation_cache_dir: Path = _DEFAULT_MARKET_EXPECTATION_CACHE_DIR
        self._market_expectation_cache_max_age_days: int = _DEFAULT_MARKET_EXPECTATION_CACHE_MAX_AGE_DAYS

    @staticmethod
    def _build_candidate_cache_key(
        func_name: str,
        kwargs: Dict[str, Any],
    ) -> Tuple[str, str]:
        try:
            serialized_kwargs = json.dumps(kwargs or {}, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            fallback_items = sorted((str(key), repr(value)) for key, value in (kwargs or {}).items())
            serialized_kwargs = repr(fallback_items)
        return str(func_name), serialized_kwargs

    def _call_df_candidate_with_timeout(
        self,
        fn: Any,
        *,
        func_name: str,
        kwargs: Dict[str, Any],
    ) -> Any:
        timeout_seconds = max(
            0.0,
            float(
                getattr(
                    self,
                    "_df_candidate_timeout_seconds",
                    _DEFAULT_DF_CANDIDATE_TIMEOUT_SECONDS,
                )
                or 0.0
            ),
        )
        if timeout_seconds <= 0:
            return fn(**kwargs)

        slots = getattr(self, "_df_candidate_timeout_slots", None)
        if slots is None:
            self._df_candidate_timeout_slots = BoundedSemaphore(_DEFAULT_DF_CANDIDATE_TIMEOUT_WORKERS)
            slots = self._df_candidate_timeout_slots

        if not slots.acquire(blocking=False):
            raise TimeoutError(f"{func_name} timeout worker pool exhausted")

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_holder["value"] = fn(**kwargs)
            except BaseException as exc:
                error_holder["value"] = exc
            finally:
                try:
                    slots.release()
                except ValueError:
                    pass

        worker = Thread(target=runner, daemon=True, name=f"fundamental-df-{func_name}")
        worker.start()
        worker.join(timeout_seconds)
        if worker.is_alive():
            raise TimeoutError(f"{func_name} timeout after {timeout_seconds:.2f}s")
        if "value" in error_holder:
            raise error_holder["value"]
        return result_holder.get("value")

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            cache_key = self._build_candidate_cache_key(func_name, kwargs)
            with self._df_candidates_cache_lock:
                cached = dict(self._df_candidates_cache.get(cache_key) or {})

            if not cached:
                try:
                    df = self._call_df_candidate_with_timeout(
                        fn,
                        func_name=func_name,
                        kwargs=kwargs,
                    )
                    if isinstance(df, pd.Series):
                        df = df.to_frame().T
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        cached = {"df": df.copy(deep=True), "source": func_name, "error": None}
                    else:
                        cached = {"df": None, "source": None, "error": None}
                except Exception as exc:
                    cached = {"df": None, "source": None, "error": f"{func_name}:{type(exc).__name__}"}
                with self._df_candidates_cache_lock:
                    self._df_candidates_cache[cache_key] = cached

            cached_error = str(cached.get("error") or "").strip()
            if cached_error:
                errors.append(cached_error)
                continue

            cached_df = cached.get("df")
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                source_name = str(cached.get("source") or func_name)
                return cached_df.copy(deep=False), source_name, errors
        return None, None, errors

    def _market_expectation_cache_file(
        self,
        *,
        stock_code: str,
        prefer_year: Optional[int],
    ) -> Path:
        normalized_code = _normalize_code(stock_code) or _safe_str(stock_code)
        year_token = str(int(prefer_year)) if prefer_year is not None else "auto"
        return Path(self._market_expectation_cache_dir) / f"{normalized_code}_{year_token}.json"

    def _load_market_expectation_cache(
        self,
        *,
        stock_code: str,
        prefer_year: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        cache_file = self._market_expectation_cache_file(stock_code=stock_code, prefer_year=prefer_year)
        try:
            if not cache_file.exists():
                return None
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        fetched_at_text = _safe_str(payload.get("fetched_at"))
        fetched_at = _safe_datetime(fetched_at_text)
        max_age_days = max(0, int(getattr(self, "_market_expectation_cache_max_age_days", 0) or 0))
        if fetched_at is None or max_age_days <= 0:
            return None
        if datetime.now() - fetched_at > timedelta(days=max_age_days):
            return None
        cached_result = payload.get("result")
        if not isinstance(cached_result, dict):
            return None
        return dict(cached_result)

    def _write_market_expectation_cache(
        self,
        *,
        stock_code: str,
        prefer_year: Optional[int],
        result: Dict[str, Any],
    ) -> None:
        cache_file = self._market_expectation_cache_file(stock_code=stock_code, prefer_year=prefer_year)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": datetime.now().replace(microsecond=0).isoformat(),
                "result": dict(result or {}),
            }
            cache_file.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            return

    def _load_tushare_sector_rankings(
        self,
        top_n: int = 5,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str], List[str]]:
        cache_date = datetime.now().strftime("%Y-%m-%d")
        with self._tushare_sector_rankings_cache_lock:
            cache = dict(self._tushare_sector_rankings_cache)
        if cache.get("attempted") and cache.get("cache_date") == cache_date:
            return (
                {
                    "top": list(cache.get("top") or [])[:top_n],
                    "bottom": list(cache.get("bottom") or [])[:top_n],
                },
                cache.get("source"),
                list(cache.get("errors") or []),
            )

        errors: List[str] = []
        rankings = {"top": [], "bottom": []}
        source: Optional[str] = None

        try:
            import tushare as ts
            from src.config import get_config
        except Exception as exc:
            errors.append(f"tushare_import:{type(exc).__name__}")
            with self._tushare_sector_rankings_cache_lock:
                self._tushare_sector_rankings_cache = {
                    "cache_date": cache_date,
                    "attempted": True,
                    "top": [],
                    "bottom": [],
                    "source": None,
                    "errors": errors,
                }
            return rankings, source, errors

        token = str(getattr(get_config(), "tushare_token", "") or "").strip()
        if not token:
            errors.append("tushare_token_missing")
            with self._tushare_sector_rankings_cache_lock:
                self._tushare_sector_rankings_cache = {
                    "cache_date": cache_date,
                    "attempted": True,
                    "top": [],
                    "bottom": [],
                    "source": None,
                    "errors": errors,
                }
            return rankings, source, errors

        ts.set_token(token)
        pro = ts.pro_api()

        trade_dates = [
            (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(0, 7)
        ]
        api_candidates = ("moneyflow_ind_ths", "moneyflow_ind_dc")

        for api_name in api_candidates:
            api_func = getattr(pro, api_name, None)
            if api_func is None:
                errors.append(f"{api_name}:missing")
                continue
            for trade_date in trade_dates:
                try:
                    raw_df = api_func(trade_date=trade_date)
                except Exception as exc:
                    errors.append(f"{api_name}:{type(exc).__name__}")
                    continue
                parsed = self._parse_sector_rankings_from_df(raw_df, top_n=top_n)
                if parsed["top"] or parsed["bottom"]:
                    rankings = parsed
                    source = f"{api_name}:{trade_date}"
                    with self._tushare_sector_rankings_cache_lock:
                        self._tushare_sector_rankings_cache = {
                            "cache_date": cache_date,
                            "attempted": True,
                            "top": list(rankings["top"]),
                            "bottom": list(rankings["bottom"]),
                            "source": source,
                            "errors": errors,
                        }
                    return rankings, source, errors

        with self._tushare_sector_rankings_cache_lock:
            self._tushare_sector_rankings_cache = {
                "cache_date": cache_date,
                "attempted": True,
                "top": [],
                "bottom": [],
                "source": None,
                "errors": errors,
            }
        return rankings, source, errors

    @staticmethod
    def _parse_sector_rankings_from_df(
        df: Any,
        *,
        top_n: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return {"top": [], "bottom": []}

        name_keywords = ("industry", "name", "行业", "板块", "概念")
        flow_keywords = ("net_amount", "net_inflow", "净流入", "净额")
        name_col = next((c for c in df.columns if any(k in str(c) for k in name_keywords)), None)
        flow_col = next((c for c in df.columns if any(k in str(c) for k in flow_keywords)), None)
        if not name_col or not flow_col:
            return {"top": [], "bottom": []}

        work_df = df[[name_col, flow_col]].copy()
        work_df[name_col] = work_df[name_col].astype(str).str.strip()
        work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
        work_df = work_df.dropna(subset=[flow_col])
        work_df = work_df[work_df[name_col] != ""]
        if work_df.empty:
            return {"top": [], "bottom": []}

        top_df = work_df.nlargest(top_n, flow_col)
        bottom_df = work_df.nsmallest(top_n, flow_col)
        return {
            "top": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()],
            "bottom": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in bottom_df.iterrows()],
        }

    def get_fundamental_bundle(
        self,
        stock_code: str,
        *,
        enabled_blocks: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        enabled_block_set = {
            str(block or "").strip()
            for block in (enabled_blocks or _SUPPORTED_FUNDAMENTAL_BLOCKS)
            if str(block or "").strip()
        }
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # Financial indicators
        if "financial" in enabled_block_set:
            fin_df, fin_source, fin_errors = self._call_df_candidates([
                ("stock_financial_abstract", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {}),
            ])
            result["errors"].extend(fin_errors)
            if fin_df is not None:
                financial_series = _build_financial_report_series(fin_df, stock_code, max_periods=12)
                if financial_series:
                    latest_item = dict(financial_series[0])
                    growth_payload = {
                        "revenue_yoy": latest_item.get("revenue_yoy"),
                        "net_profit_yoy": latest_item.get("net_profit_yoy"),
                        "roe": latest_item.get("roe"),
                        "gross_margin": latest_item.get("gross_margin"),
                    }
                    growth_payload = {key: value for key, value in growth_payload.items() if value is not None}
                    growth_quarterly_series = [
                        {
                            key: item[key]
                            for key in ("report_date", "revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
                            if key in item
                        }
                        for item in financial_series
                    ]
                    growth_quarterly_series = [
                        item
                        for item in growth_quarterly_series
                        if any(metric in item for metric in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin"))
                    ]
                    if growth_quarterly_series:
                        growth_payload["quarterly_series"] = growth_quarterly_series
                    result["growth"] = growth_payload

                    financial_report_payload = {
                        key: latest_item[key]
                        for key in ("report_date", "revenue", "net_profit_parent", "operating_cash_flow", "roe")
                        if key in latest_item
                    }
                    if financial_report_payload:
                        result["earnings"]["financial_report"] = financial_report_payload
                    result["earnings"]["financial_report_series"] = financial_series
                    result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast
        if "forecast" in enabled_block_set:
            forecast_df, forecast_source, forecast_errors = self._call_df_candidates([
                ("stock_yjyg_em", {"symbol": stock_code}),
                ("stock_yjyg_em", {}),
                ("stock_yjbb_em", {"symbol": stock_code}),
                ("stock_yjbb_em", {}),
            ])
            result["errors"].extend(forecast_errors)
            if forecast_df is not None:
                row = _extract_latest_row(forecast_df, stock_code)
                if row is not None:
                    result["earnings"]["forecast_summary"] = _safe_str(
                        _pick_by_keywords(row, ["棰勫憡", "涓氱哗鍙樺姩", "鍐呭", "鎽樿", "鍏憡"])
                    )[:200]
                    forecast_announcement_date = _normalize_report_date(
                        _pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"])
                    )
                    if forecast_announcement_date:
                        result["earnings"]["forecast_announcement_date"] = forecast_announcement_date
                    result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report
        if "quick_report" in enabled_block_set:
            quick_df, quick_source, quick_errors = self._call_df_candidates([
                ("stock_yjkb_em", {"symbol": stock_code}),
                ("stock_yjkb_em", {}),
            ])
            result["errors"].extend(quick_errors)
            if quick_df is not None:
                row = _extract_latest_row(quick_df, stock_code)
                if row is not None:
                    result["earnings"]["quick_report_summary"] = _safe_str(
                        _pick_by_keywords(row, ["蹇姤", "鎽樿", "鍏憡", "璇存槑"])
                    )[:200]
                    quick_announcement_date = _normalize_report_date(
                        _pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"])
                    )
                    if quick_announcement_date:
                        result["earnings"]["quick_report_announcement_date"] = quick_announcement_date
                    result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        if "dividend" in enabled_block_set:
            dividend_df, dividend_source, dividend_errors = self._call_df_candidates([
                ("stock_fhps_detail_em", {"symbol": stock_code}),
                ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "鍒嗙孩", "date": ""}),
                ("stock_dividend_cninfo", {"symbol": stock_code}),
            ])
            result["errors"].extend(dividend_errors)
            if dividend_df is not None:
                dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
                if dividend_payload:
                    result["earnings"]["dividend"] = dividend_payload
                    result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        if "institution" in enabled_block_set:
            inst_df, inst_source, inst_errors = self._call_df_candidates([
                ("stock_institute_hold", {}),
                ("stock_institute_recommend", {}),
            ])
            result["errors"].extend(inst_errors)
            if inst_df is not None:
                row = _extract_latest_row(inst_df, stock_code)
                if row is not None:
                    inst_change = _safe_float(_pick_by_keywords(row, ["澧炲噺", "鍙樺寲", "鍙樺姩", "鎸佽偂鍙樺寲"]))
                    result["institution"]["institution_holding_change"] = inst_change
                    result["source_chain"].append(f"institution:{inst_source}")

        if "top10" in enabled_block_set:
            top10_df, top10_source, top10_errors = self._call_df_candidates([
                ("stock_gdfx_top_10_em", {"symbol": stock_code}),
                ("stock_gdfx_top_10_em", {}),
                ("stock_zh_a_gdhs_detail_em", {"symbol": stock_code}),
                ("stock_zh_a_gdhs_detail_em", {}),
            ])
            result["errors"].extend(top10_errors)
            if top10_df is not None:
                row = _extract_latest_row(top10_df, stock_code)
                if row is not None:
                    holder_change = _safe_float(_pick_by_keywords(row, ["澧炲噺", "鍙樺寲", "鎸佽偂鍙樺寲", "鍙樺姩"]))
                    result["institution"]["top10_holder_change"] = holder_change
                    result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_market_expectation_snapshot(
        self,
        stock_code: str,
        *,
        prefer_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return a lightweight sell-side expectation snapshot for review reference."""
        result: Dict[str, Any] = {
            "status": "not_supported",
            "source": None,
            "forecast_year": None,
            "institution_count": None,
            "eps_min": None,
            "eps_mean": None,
            "eps_max": None,
            "industry_avg_eps": None,
            "summary": None,
            "errors": [],
            "cache_hit": False,
            "cache_source": None,
        }

        cached_result = self._load_market_expectation_cache(
            stock_code=stock_code,
            prefer_year=prefer_year,
        )
        if isinstance(cached_result, dict):
            cached_result["cache_hit"] = True
            cached_result["cache_source"] = "disk"
            merged = dict(result)
            merged.update(cached_result)
            return merged

        forecast_df, forecast_source, forecast_errors = self._call_df_candidates(
            [
                (
                    "stock_profit_forecast_ths",
                    {"symbol": stock_code, "indicator": "预测年报每股收益"},
                ),
            ]
        )
        result["errors"].extend(forecast_errors)
        if forecast_df is None or forecast_df.empty:
            return result

        year_col = next((col for col in forecast_df.columns if "年度" in str(col)), None)
        inst_col = next((col for col in forecast_df.columns if "预测机构数" in str(col)), None)
        min_col = next((col for col in forecast_df.columns if "最小值" in str(col)), None)
        mean_col = next((col for col in forecast_df.columns if "均值" in str(col)), None)
        max_col = next((col for col in forecast_df.columns if "最大值" in str(col)), None)
        industry_avg_col = next((col for col in forecast_df.columns if "行业平均" in str(col)), None)
        if year_col is None or mean_col is None:
            return result

        selected_row = None
        if prefer_year is not None:
            target_year = str(int(prefer_year))
            for _, row in forecast_df.iterrows():
                if target_year == _safe_str(row.get(year_col)):
                    selected_row = row
                    break
        if selected_row is None:
            selected_row = forecast_df.iloc[0]

        forecast_year = _safe_str(selected_row.get(year_col)) or None
        institution_count = _safe_float(selected_row.get(inst_col)) if inst_col else None
        eps_min = _safe_float(selected_row.get(min_col)) if min_col else None
        eps_mean = _safe_float(selected_row.get(mean_col)) if mean_col else None
        eps_max = _safe_float(selected_row.get(max_col)) if max_col else None
        industry_avg_eps = _safe_float(selected_row.get(industry_avg_col)) if industry_avg_col else None

        if eps_mean is None:
            return result

        summary_parts = []
        if forecast_year:
            summary_parts.append(f"{forecast_year}年EPS一致预期 {eps_mean:.2f} 元")
        else:
            summary_parts.append(f"EPS一致预期 {eps_mean:.2f} 元")
        if institution_count is not None:
            summary_parts.append(f"{int(round(institution_count))}家机构")
        if eps_min is not None and eps_max is not None:
            summary_parts.append(f"区间 {eps_min:.2f}-{eps_max:.2f}")
        if industry_avg_eps is not None:
            summary_parts.append(f"行业均值 {industry_avg_eps:.2f}")

        result.update(
            {
                "status": "available",
                "source": forecast_source or "stock_profit_forecast_ths",
                "forecast_year": forecast_year,
                "institution_count": int(round(institution_count)) if institution_count is not None else None,
                "eps_min": eps_min,
                "eps_mean": eps_mean,
                "eps_max": eps_max,
                "industry_avg_eps": industry_avg_eps,
                "summary": "（".join(summary_parts[:1])
                + (f"（{'；'.join(summary_parts[1:])}）" if len(summary_parts) > 1 else ""),
            }
        )
        self._write_market_expectation_cache(
            stock_code=stock_code,
            prefer_year=prefer_year,
            result=result,
        )
        return result

    def get_capital_flow(
        self,
        stock_code: str,
        top_n: int = 5,
        *,
        include_sector_rankings: bool = True,
    ) -> Dict[str, Any]:
        """
        Return stock + sector capital flow.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        stock_df, stock_source, stock_errors = self._call_df_candidates([
            ("stock_individual_fund_flow", {"stock": stock_code}),
            ("stock_individual_fund_flow", {"symbol": stock_code}),
            ("stock_individual_fund_flow", {}),
            ("stock_main_fund_flow", {"symbol": stock_code}),
            ("stock_main_fund_flow", {}),
        ])
        result["errors"].extend(stock_errors)
        if stock_df is not None:
            row = _extract_latest_row(stock_df, stock_code)
            if row is not None:
                net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
                inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
                inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
                result["stock_flow"] = {
                    "main_net_inflow": net_inflow,
                    "inflow_5d": inflow_5d,
                    "inflow_10d": inflow_10d,
                }
                result["source_chain"].append(f"capital_stock:{stock_source}")

        if not include_sector_rankings:
            has_content = bool(result["stock_flow"])
            result["status"] = "partial" if has_content else "not_supported"
            return result

        sector_df, sector_source, sector_errors = self._call_df_candidates([
            ("stock_sector_fund_flow_rank", {}),
            ("stock_sector_fund_flow_summary", {}),
            ])
        result["errors"].extend(sector_errors)
        if sector_df is not None:
            name_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None)
            flow_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("净流入", "主力", "flow", "净额"))), None)
            if name_col and flow_col:
                work_df = sector_df[[name_col, flow_col]].copy()
                work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
                work_df = work_df.dropna(subset=[flow_col])
                top_df = work_df.nlargest(top_n, flow_col)
                bottom_df = work_df.nsmallest(top_n, flow_col)
                result["sector_rankings"] = {
                    "top": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()],
                    "bottom": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in bottom_df.iterrows()],
                }
                result["source_chain"].append(f"capital_sector:{sector_source}")

        if not result["sector_rankings"]["top"] and not result["sector_rankings"]["bottom"]:
            tushare_rankings, tushare_source, tushare_errors = self._load_tushare_sector_rankings(top_n=top_n)
            result["errors"].extend(tushare_errors)
            if tushare_rankings["top"] or tushare_rankings["bottom"]:
                result["sector_rankings"] = tushare_rankings
                if tushare_source:
                    result["source_chain"].append(f"capital_sector:{tushare_source}")

        has_content = bool(result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        df, source, errors = self._call_df_candidates([
            ("stock_lhb_stock_statistic_em", {}),
            ("stock_lhb_detail_em", {}),
            ("stock_lhb_jgmmtj_em", {}),
        ])
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next((c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None)
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = max(recent_dates).date().isoformat() if recent_dates else (
            max(parsed_dates).date().isoformat() if parsed_dates else None
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result
