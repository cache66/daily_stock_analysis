#!/usr/bin/env python3
"""吃股息策略 · 点时（point-in-time）月度回测。

口径说明（v1，全部本地数据，避免在线依赖）：
- 股票池：本地行情缓存 ``data/cache/history/cn/*.csv`` 覆盖的全部 A 股（主/中小/创业板）；
- 节奏：每月末最后交易日打分，等权持有 1 个月，滚动换仓；
- 点时信息：
  - 股息率 = 过去 12 个月"已实施且已除权"的每股现金分红之和 / 月末收盘价；
  - 业绩/分红历史只使用除权日（或报告期）不晚于 T 的数据；
  - 低波/趋势指标只用 T 之前的行情（不足 251 根 K 线时为中性）；
- 对照：全部合格股等权（"池子"）、中证红利指数 000922（价格指数，未含分红再投）。

已知偏差（输出中会提示）：
- 幸存者偏差：股票池取自 2026-07 本地缓存，退市股缺失；
- 行情缓存为分批增量合并，可能存在复权口径接缝（月末收益率个别异常）；
- 业绩面（ROE/扣非/现金流）仅约 500 只有缓存，缺缓存的按中性处理。
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.dividend_income_service import (  # noqa: E402
    DIV_PROC_IMPLEMENTED_MARKER,
    DividendIncomeService,
    _to_float,
)

HISTORY_DIR = REPO_ROOT / "data" / "cache" / "history" / "cn"
DEEP_HISTORY_DIR = REPO_ROOT / "data" / "cache" / "backtest_history" / "cn"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "dividend_income"
OUTPUT_DIR = REPO_ROOT / "data" / "runtime"
BENCHMARK_CACHE = CACHE_DIR / "benchmark_000922.csv"

DEEP_FETCH_LOOKBACK_DAYS = 800

DEFAULT_MIN_YIELD = 4.0
DEFAULT_MIN_DIV_YEARS = 5
DEFAULT_TOP_N = 20
DEFAULT_START = "2024-06-30"
DEFAULT_END = "2026-06-30"


# ---------------------------------------------------------------------- 数据加载


def load_price_series(
    limit: Optional[int] = None, *, prefer_deep: bool = False
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """读取本地行情缓存：code6 -> (dates[datetime64], closes[float])。

    prefer_deep=True 时优先使用回测专用深历史（`data/cache/backtest_history/cn`），
    避免共享缓存只有 260 根 K 线导致趋势/波动因子在历史月份不可用。
    """
    series: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    paths = sorted(HISTORY_DIR.glob("*.csv"))
    if limit:
        paths = paths[:limit]
    for path in paths:
        try:
            df = pd.read_csv(path, usecols=["date", "close"])
        except Exception:  # noqa: BLE001 - 单个坏文件跳过
            continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date")
        if len(df) < 60:
            continue
        series[path.stem.zfill(6)] = (
            df["date"].to_numpy(dtype="datetime64[ns]"),
            df["close"].to_numpy(dtype=float),
        )
    if prefer_deep and DEEP_HISTORY_DIR.exists():
        deep_count = 0
        for path in DEEP_HISTORY_DIR.glob("*.csv"):
            code = path.stem.zfill(6)
            try:
                df = pd.read_csv(path, usecols=["date", "close"])
            except Exception:  # noqa: BLE001
                continue
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.dropna(subset=["date", "close"]).sort_values("date")
            if len(df) < 60:
                continue
            series[code] = (
                df["date"].to_numpy(dtype="datetime64[ns]"),
                df["close"].to_numpy(dtype=float),
            )
            deep_count += 1
        print(f"[回测] 深历史覆盖 {deep_count} 只")
    return series


def _deep_ready(code: str, start: date) -> bool:
    path = DEEP_HISTORY_DIR / f"{code}.csv"
    if not path.exists():
        return False
    try:
        df = pd.read_csv(path, usecols=["date"])
    except Exception:  # noqa: BLE001
        return False
    if df.empty:
        return False
    first = pd.to_datetime(df["date"]).min().date()
    return first <= start + timedelta(days=45)


def ensure_deep_history(codes: List[str], start: date, end: date) -> int:
    """用 Baostock 补齐回测专用深历史（不复权口径，与共享缓存约定一致）。"""
    try:
        import baostock as bs  # type: ignore
    except Exception:  # noqa: BLE001
        print("[回测] 未安装 baostock，跳过深历史抓取。")
        return 0
    pending = [code for code in codes if not _deep_ready(code, start)]
    if not pending:
        return 0
    DEEP_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    lg = bs.login()
    if getattr(lg, "error_code", "1") != "0":
        print("[回测] Baostock 登录失败，跳过深历史抓取。")
        return 0
    fetched = 0
    try:
        for index, code in enumerate(pending, start=1):
            prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
            try:
                rs = bs.query_history_k_data_plus(
                    f"{prefix}.{code}",
                    "date,close",
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    frequency="d",
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    pd.DataFrame(rows, columns=["date", "close"]).to_csv(
                        DEEP_HISTORY_DIR / f"{code}.csv", index=False
                    )
                    fetched += 1
            except Exception:  # noqa: BLE001 - 单票失败跳过
                continue
            if index % 50 == 0:
                print(f"[回测] 深历史抓取 {index}/{len(pending)}（成功 {fetched}）...")
            time.sleep(0.05)
    finally:
        bs.logout()
    return fetched


def collect_pool_codes(
    prices: Dict[str, Tuple[np.ndarray, np.ndarray]],
    streams: Dict[str, Dict[str, Any]],
    stock_basic: Dict[str, Dict[str, str]],
    rebalance_dates: List[np.datetime64],
    min_yield: float,
) -> Set[str]:
    """轻量扫一遍：收集所有月份中曾达到股息率门槛的股票代码（用于深历史抓取）。"""
    codes: Set[str] = set()
    for t in rebalance_dates[:-1]:
        for code, (dates, closes) in prices.items():
            basic = stock_basic.get(code)
            if basic is None:
                continue
            name = basic["name"].upper()
            if (
                "ST" in name
                or "PT" in name
                or basic["ts_code"].startswith(("688", "689"))
                or basic["ts_code"].endswith(".BJ")
            ):
                continue
            stream = streams.get(code)
            if stream is None:
                continue
            idx = int(np.searchsorted(dates, t, side="right")) - 1
            if idx < 0:
                continue
            close_t = float(closes[idx])
            if close_t <= 0:
                continue
            if ttm_cash(stream, t) / close_t * 100.0 >= min_yield:
                codes.add(code)
    return codes


def load_stock_basic(service: DividendIncomeService) -> Dict[str, Dict[str, str]]:
    df = service._load_stock_basic(cache_only=True)  # noqa: SLF001 - 复用同一缓存
    info: Dict[str, Dict[str, str]] = {}
    if df is None or df.empty:
        return info
    for _, row in df.iterrows():
        code = str(row.get("symbol") or "").strip().zfill(6)
        if not code or not code.isdigit():
            continue
        info[code] = {
            "ts_code": str(row.get("ts_code") or f"{code}.SZ"),
            "name": str(row.get("name") or ""),
            "industry": str(row.get("industry") or ""),
            "market": str(row.get("market") or ""),
            "act_ent_type": str(row.get("act_ent_type") or ""),
        }
    return info


def load_dividend_streams(
    service: DividendIncomeService, rows_by_code: Dict[str, pd.DataFrame]
) -> Dict[str, Dict[str, Any]]:
    """把 fhps 行拆成两套输入：完整 PIT 帧（供打分）与除权现金流（供收益计算）。"""
    streams: Dict[str, Dict[str, Any]] = {}
    for code, rows in rows_by_code.items():
        dividend_df, eps_map = service._build_fhps_inputs(code, rows)  # noqa: SLF001
        ex_dates: List[np.datetime64] = []
        cash: List[float] = []
        if not dividend_df.empty:
            proc = dividend_df["div_proc"].astype(str)
            keep = proc.str.contains(DIV_PROC_IMPLEMENTED_MARKER, na=False) & ~proc.str.contains("取消", na=False)
            ex_parsed = pd.to_datetime(dividend_df["ex_date"], errors="coerce")
            valid = keep & ex_parsed.notna()
            payout = pd.to_numeric(dividend_df["cash_div_tax"], errors="coerce").where(
                pd.to_numeric(dividend_df["cash_div_tax"], errors="coerce") > 0,
                pd.to_numeric(dividend_df["cash_div"], errors="coerce"),
            )
            for ex_value, cash_value in zip(ex_parsed[valid], payout[valid]):
                if pd.notna(cash_value) and float(cash_value) > 0:
                    ex_dates.append(np.datetime64(ex_value.to_pydatetime(), "ns"))
                    cash.append(float(cash_value))
            order = np.argsort(np.array(ex_dates)) if ex_dates else np.array([], dtype=int)
            ex_arr = np.array(ex_dates, dtype="datetime64[ns]")[order] if ex_dates else np.array([], dtype="datetime64[ns]")
            cash_arr = np.array(cash, dtype=float)[order] if cash else np.array([], dtype=float)
            cum = np.concatenate([[0.0], np.cumsum(cash_arr)])
        else:
            ex_arr = np.array([], dtype="datetime64[ns]")
            cash_arr = np.array([], dtype=float)
            cum = np.array([0.0])
        streams[code] = {
            "dividend_df": dividend_df,
            "eps_map": eps_map,
            "ex_dates": ex_arr,
            "cash": cash_arr,
            "cum": cum,
        }
    return streams


def load_fundamental_frames() -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for path in (CACHE_DIR / "fundamentals").glob("*.csv"):
        try:
            df = pd.read_csv(path)
        except Exception:  # noqa: BLE001
            continue
        if not df.empty:
            frames[path.stem.zfill(6)] = df
    return frames


def load_benchmark(start: date, end: date, *, offline: bool) -> Optional[pd.Series]:
    """中证红利 000922 月末收盘序列；多源依次尝试，缓存到 CSV。"""
    frame: Optional[pd.DataFrame] = None
    if BENCHMARK_CACHE.exists():
        frame = pd.read_csv(BENCHMARK_CACHE, parse_dates=["date"])
    if frame is None and not offline:
        frame = _fetch_benchmark_online(start, end)
        if frame is not None and not frame.empty:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            frame.to_csv(BENCHMARK_CACHE, index=False)
    if frame is None or frame.empty:
        return None
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    series = pd.Series(frame["close"].to_numpy(float), index=pd.to_datetime(frame["date"]))
    return series


def _fetch_benchmark_online(start: date, end: date) -> Optional[pd.DataFrame]:
    start_text = start.strftime("%Y-%m-%d")
    end_text = end.strftime("%Y-%m-%d")
    # 源 1：Baostock（免费稳定）
    try:
        import baostock as bs  # type: ignore

        lg = bs.login()
        if getattr(lg, "error_code", "1") == "0":
            try:
                rs = bs.query_history_k_data_plus(
                    "sh.000922", "date,close", start_date=start_text, end_date=end_text, frequency="d"
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    return pd.DataFrame(rows, columns=["date", "close"])
            finally:
                bs.logout()
    except Exception:  # noqa: BLE001
        pass
    # 源 2：AKShare 新浪接口
    try:
        import akshare as ak  # type: ignore

        raw = ak.stock_zh_index_daily(symbol="sh000922")
        if raw is not None and not raw.empty:
            df = raw[["date", "close"]].copy()
            mask = (pd.to_datetime(df["date"]) >= start_text) & (pd.to_datetime(df["date"]) <= end_text)
            return df[mask]
    except Exception:  # noqa: BLE001
        pass
    # 源 3：腾讯单次请求（避免批量直连）
    try:
        import requests  # type: ignore

        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        resp = requests.get(
            url,
            params={"param": "sh000922,day,,,640,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        payload = resp.json()
        data = (payload.get("data") or {}).get("sh000922") or {}
        klines = data.get("day") or data.get("qfqday") or []
        rows = [(item[0], item[2]) for item in klines]
        if rows:
            df = pd.DataFrame(rows, columns=["date", "close"])
            mask = (pd.to_datetime(df["date"]) >= start_text) & (pd.to_datetime(df["date"]) <= end_text)
            return df[mask]
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------- 回测主体


def month_end_calendar(master_dates: np.ndarray, start: date, end: date) -> List[np.datetime64]:
    unique = np.unique(master_dates)
    months = pd.period_range(start=start, end=end, freq="M")
    picked: List[np.datetime64] = []
    for month in months:
        month_end = month.end_time.normalize()
        idx = np.searchsorted(unique, np.datetime64(month_end, "ns"), side="right") - 1
        if idx < 0:
            continue
        value = unique[idx]
        if value not in picked:
            picked.append(value)
    return picked


def ttm_cash(stream: Dict[str, Any], at: np.datetime64) -> float:
    ex_dates = stream["ex_dates"]
    if len(ex_dates) == 0:
        return 0.0
    right = np.searchsorted(ex_dates, at, side="right")
    left = np.searchsorted(ex_dates, at - np.timedelta64(365, "D"), side="right")
    return float(stream["cum"][right] - stream["cum"][left])


def cash_in_window(stream: Dict[str, Any], start_at: np.datetime64, end_at: np.datetime64) -> float:
    ex_dates = stream["ex_dates"]
    if len(ex_dates) == 0:
        return 0.0
    right = np.searchsorted(ex_dates, end_at, side="right")
    left = np.searchsorted(ex_dates, start_at, side="right")
    return float(stream["cum"][right] - stream["cum"][left])


def pit_fundamentals(frame: Optional[pd.DataFrame], cutoff_year: int) -> Optional[Dict[str, Any]]:
    if frame is None or frame.empty:
        return None
    period = frame.get("报告期", pd.Series([""] * len(frame))).astype(str).str.strip()
    mask = period.str.fullmatch(r"\d{4}") & (pd.to_numeric(period, errors="coerce") <= cutoff_year)
    sliced = frame[mask]
    if sliced.empty:
        return None
    return DividendIncomeService.compute_fundamental_metrics(sliced)


def price_metrics_at(
    code: str,
    series: Dict[str, Tuple[np.ndarray, np.ndarray]],
    at: np.datetime64,
) -> Optional[Dict[str, Any]]:
    dates, closes = series[code]
    idx = int(np.searchsorted(dates, at, side="right"))
    if idx < 60:
        return None
    lo = max(0, idx - 300)
    frame = pd.DataFrame({"date": pd.to_datetime(dates[lo:idx]), "close": closes[lo:idx]})
    snapshot = pd.Timestamp(at).date()
    return DividendIncomeService.compute_price_trend_metrics(frame, snapshot)


def run_backtest(args: argparse.Namespace) -> int:
    start = pd.Timestamp(args.start).date()
    end = pd.Timestamp(args.end).date()

    print(f"[回测] 加载行情缓存 {HISTORY_DIR} ...")
    prices = load_price_series(limit=args.limit)
    if not prices:
        print("没有可用行情缓存，退出。")
        return 1
    print(f"[回测] 股票数 {len(prices)}")

    service = DividendIncomeService(cache_dir=CACHE_DIR, output_dir=OUTPUT_DIR)
    stock_basic = load_stock_basic(service)
    print(f"[回测] stock_basic {len(stock_basic)}")

    print("[回测] 加载分红送配缓存 ...")
    fhps_frames = service._load_fhps_frames(date(2026, 9, 24), cache_only=True)  # noqa: SLF001
    streams = load_dividend_streams(service, fhps_frames)
    print(f"[回测] 分红历史覆盖 {len(streams)}")
    fundamentals = load_fundamental_frames()
    print(f"[回测] 业绩面缓存 {len(fundamentals)}")

    all_dates = np.concatenate([dates for dates, _ in prices.values()])
    rebalance_dates = month_end_calendar(all_dates, start, end)
    if len(rebalance_dates) < 3:
        print("调仓日期太少，退出。")
        return 1
    print(f"[回测] 调仓 {len(rebalance_dates)} 期：{pd.Timestamp(rebalance_dates[0]).date()} -> {pd.Timestamp(rebalance_dates[-1]).date()}")

    if args.deep_fetch:
        pool_codes = collect_pool_codes(prices, streams, stock_basic, rebalance_dates, args.min_yield)
        print(f"[回测] 池子代码并集 {len(pool_codes)} 只，检查回测专用深历史 ...")
        fetched = ensure_deep_history(
            sorted(pool_codes), start - timedelta(days=DEEP_FETCH_LOOKBACK_DAYS), end
        )
        print(f"[回测] 本轮新抓取 {fetched} 只")
        prices = load_price_series(limit=args.limit, prefer_deep=True)

    benchmark = load_benchmark(start, end, offline=args.no_benchmark)
    if benchmark is None:
        print("[回测] 警告：基准指数未取得，仅与池子对照。")
    else:
        print(f"[回测] 基准中证红利 {len(benchmark)} 个交易日")

    fund_cache: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
    monthly_rows: List[Dict[str, Any]] = []
    holdings_log: List[Dict[str, Any]] = []

    for pos in range(len(rebalance_dates) - 1):
        t = rebalance_dates[pos]
        t_next = rebalance_dates[pos + 1]
        t_ts = pd.Timestamp(t)
        cutoff_year = t_ts.year - 1

        scored: List[Dict[str, Any]] = []
        pool_returns: List[float] = []
        trend_up_returns: List[float] = []
        trend_down_returns: List[float] = []
        trend_unknown_returns: List[float] = []

        for code, (dates, closes) in prices.items():
            basic = stock_basic.get(code)
            if basic is None:
                continue
            name = basic["name"].upper()
            if "ST" in name or "PT" in name or basic["ts_code"].startswith(("688", "689")) or basic["ts_code"].endswith(".BJ"):
                continue
            stream = streams.get(code)
            if stream is None:
                continue
            idx = int(np.searchsorted(dates, t, side="right")) - 1
            if idx < 0:
                continue
            close_t = float(closes[idx])
            if close_t <= 0:
                continue
            yield_pct = ttm_cash(stream, t) / close_t * 100.0
            if yield_pct < args.min_yield:
                continue

            idx_next = int(np.searchsorted(dates, t_next, side="right")) - 1
            if idx_next <= idx:
                continue
            close_next = float(closes[idx_next])
            if close_next <= 0:
                continue
            div_cash = cash_in_window(stream, t, t_next)
            forward_return = (close_next / close_t - 1.0) + div_cash / close_t * 100.0 / 100.0

            dividend_df = stream["dividend_df"]
            if dividend_df.empty:
                continue
            ex_parsed = pd.to_datetime(dividend_df["ex_date"], errors="coerce")
            pit_dividend_df = dividend_df[ex_parsed.notna() & (ex_parsed <= t_ts)]
            dividend_metrics = DividendIncomeService.compute_dividend_metrics(
                pit_dividend_df, current_year=t_ts.year, min_dividend_years=args.min_years
            )
            eps_pit = {year: value for year, value in stream["eps_map"].items() if year <= cutoff_year}
            quality_metrics = DividendIncomeService.compute_quality_metrics(
                None, dividend_metrics.get("latest_dividend_year"), annual_eps_map=eps_pit
            )
            latest_eps = quality_metrics.get("eps_annual_latest")
            pe_proxy = round(close_t / latest_eps, 2) if latest_eps and latest_eps > 0 else None

            fund_key = (code, cutoff_year)
            if fund_key not in fund_cache:
                fund_cache[fund_key] = pit_fundamentals(fundamentals.get(code), cutoff_year)
            price_metrics = price_metrics_at(code, prices, t)

            base_row = {
                "ts_code": basic["ts_code"],
                "name": basic["name"],
                "industry": basic["industry"],
                "market": basic["market"],
                "close": close_t,
                "dv_ttm": round(yield_pct, 3),
                "pe_ttm": pe_proxy,
                "act_ent_type": basic["act_ent_type"],
            }
            row = DividendIncomeService.build_candidate_row(
                base_row,
                dividend_metrics,
                quality_metrics,
                min_yield_pct=args.min_yield,
                min_dividend_years=args.min_years,
                prefer_soe=True,
                price_metrics=price_metrics,
                fundamentals=fund_cache[fund_key],
            )
            if not row.get("qualified", True):
                continue
            row["__forward_return_pct"] = round(forward_return * 100.0, 3)
            row["__yield_pct"] = round(yield_pct, 2)
            scored.append(row)
            pool_returns.append(forward_return * 100.0)
            ma200 = row.get("ma200_ratio_pct")
            if ma200 is None:
                trend_unknown_returns.append(forward_return * 100.0)
            elif ma200 > 0:
                trend_up_returns.append(forward_return * 100.0)
            elif ma200 <= -5.0:
                trend_down_returns.append(forward_return * 100.0)

        if not scored:
            print(f"[回测] {t_ts.date()} 无合格样本，跳过。")
            continue

        scored.sort(key=lambda item: item.get("dividend_score") or 0.0, reverse=True)
        top = scored[: args.top]
        top_returns = [item["__forward_return_pct"] for item in top]
        top_features = {
            "avg_yield": round(float(np.mean([item["__yield_pct"] for item in top])), 2),
            "avg_vol": round(
                float(np.mean([item.get("volatility_1y_pct") or 0.0 for item in top])), 2
            ),
            "ma200_above_share": round(
                float(np.mean([1.0 if (item.get("ma200_ratio_pct") or 0.0) > 0 else 0.0 for item in top])), 2
            ),
            "soe_share": round(float(np.mean([1.0 if item.get("soe_bonus") else 0.0 for item in top])), 2),
        }
        for rank, item in enumerate(top, start=1):
            holdings_log.append(
                {
                    "date": t_ts.date().isoformat(),
                    "rank": rank,
                    "ts_code": item.get("ts_code"),
                    "name": item.get("name"),
                    "score": item.get("dividend_score"),
                    "yield_pct": item["__yield_pct"],
                    "forward_return_pct": item["__forward_return_pct"],
                }
            )

        record: Dict[str, Any] = {
            "date": t_ts.date().isoformat(),
            "next_date": pd.Timestamp(t_next).date().isoformat(),
            "pool_size": len(scored),
            "top_ret_pct": round(float(np.mean(top_returns)), 3),
            "pool_ret_pct": round(float(np.mean(pool_returns)), 3),
            "trend_up_ret_pct": round(float(np.mean(trend_up_returns)), 3) if trend_up_returns else None,
            "trend_down_ret_pct": round(float(np.mean(trend_down_returns)), 3) if trend_down_returns else None,
            "trend_unknown_ret_pct": round(float(np.mean(trend_unknown_returns)), 3) if trend_unknown_returns else None,
            **top_features,
        }
        # 诊断：评分排序 IC、评分/股息率分位组合收益、趋势指标覆盖率
        scores_arr = np.array([item.get("dividend_score") or 0.0 for item in scored], dtype=float)
        yields_arr = np.array([item["__yield_pct"] for item in scored], dtype=float)
        forwards_arr = np.array([item["__forward_return_pct"] for item in scored], dtype=float)
        record["trend_coverage"] = round(
            float(np.mean([1.0 if item.get("ma200_ratio_pct") is not None else 0.0 for item in scored])), 2
        )
        if len(scored) >= 10:
            ic_value = pd.Series(scores_arr).rank().corr(pd.Series(forwards_arr).rank())
            record["score_ic"] = round(float(ic_value), 3) if pd.notna(ic_value) else None
        ma200_arr = np.array(
            [item["ma200_ratio_pct"] if item.get("ma200_ratio_pct") is not None else np.nan for item in scored],
            dtype=float,
        )
        vol_arr = np.array(
            [item["volatility_1y_pct"] if item.get("volatility_1y_pct") is not None else np.nan for item in scored],
            dtype=float,
        )
        for prefix, values in (("ma200", ma200_arr), ("vol", vol_arr)):
            mask = ~np.isnan(values)
            if int(mask.sum()) >= 25:
                factor_ic = pd.Series(values[mask]).rank().corr(pd.Series(forwards_arr[mask]).rank())
                record[f"{prefix}_ic"] = round(float(factor_ic), 3) if pd.notna(factor_ic) else None
        for prefix, values in (("score", scores_arr), ("yield", yields_arr)):
            if len(values) >= 25:
                buckets = pd.qcut(pd.Series(values).rank(method="first"), 5, labels=False)
                means = pd.Series(forwards_arr).groupby(buckets.to_numpy()).mean()
                high = float(means.get(4, np.nan))
                low = float(means.get(0, np.nan))
                record[f"{prefix}_q5_pct"] = round(high, 3)
                record[f"{prefix}_q1_pct"] = round(low, 3)
                record[f"{prefix}_q5_minus_q1"] = round(high - low, 3)
        if benchmark is not None:
            window = benchmark[benchmark.index <= pd.Timestamp(t_next)]
            before = window[window.index <= t_ts]
            if not before.empty and not window.empty:
                bench_ret = (float(window.iloc[-1]) / float(before.iloc[-1]) - 1.0) * 100.0
                record["bench_ret_pct"] = round(bench_ret, 3)
        monthly_rows.append(record)
        print(
            f"[回测] {record['date']} 池子 {record['pool_size']} 只 | Top{args.top} {record['top_ret_pct']:+.2f}%"
            f" | 池子 {record['pool_ret_pct']:+.2f}%"
            + (f" | 基准 {record.get('bench_ret_pct', float('nan')):+.2f}%" if "bench_ret_pct" in record else "")
        )

    if not monthly_rows:
        print("无有效回测区间。")
        return 1

    frame = pd.DataFrame(monthly_rows)

    def _stats(source: pd.DataFrame, column: str) -> Dict[str, float]:
        values = source[column].dropna().astype(float)
        if values.empty:
            return {}
        equity = (1.0 + values / 100.0).cumprod()
        months = len(values)
        cumulative = float(equity.iloc[-1] - 1.0) * 100.0
        annualized = (float(equity.iloc[-1]) ** (12.0 / months) - 1.0) * 100.0 if months else 0.0
        drawdown = float((equity / equity.cummax() - 1.0).min()) * 100.0
        return {
            "months": months,
            "cumulative_pct": round(cumulative, 2),
            "annualized_pct": round(annualized, 2),
            "max_drawdown_pct": round(drawdown, 2),
            "win_rate": round(float((values > 0).mean()) * 100.0, 1),
            "avg_monthly_pct": round(float(values.mean()), 3),
            "best_month_pct": round(float(values.max()), 2),
            "worst_month_pct": round(float(values.min()), 2),
        }

    summary = {
        "top": _stats(frame, "top_ret_pct"),
        "pool": _stats(frame, "pool_ret_pct"),
    }
    if "bench_ret_pct" in frame.columns and frame["bench_ret_pct"].notna().any():
        summary["benchmark"] = _stats(frame, "bench_ret_pct")
        diff = (frame["top_ret_pct"].astype(float) - frame["bench_ret_pct"].astype(float)).dropna()
        summary["top_vs_bench"] = {
            "win_rate": round(float((diff > 0).mean()) * 100.0, 1),
            "avg_excess_monthly_pct": round(float(diff.mean()), 3),
        }
    if "score_ic" in frame.columns and frame["score_ic"].notna().any():
        summary["ranking"] = {
            "score_ic_mean": round(float(frame["score_ic"].dropna().mean()), 3),
            "score_q5_minus_q1_avg": round(float(frame["score_q5_minus_q1"].dropna().mean()), 3),
            "yield_q5_minus_q1_avg": round(float(frame["yield_q5_minus_q1"].dropna().mean()), 3),
            "ma200_ic_mean": round(float(frame["ma200_ic"].dropna().mean()), 3)
            if "ma200_ic" in frame.columns and frame["ma200_ic"].notna().any()
            else None,
            "vol_ic_mean": round(float(frame["vol_ic"].dropna().mean()), 3)
            if "vol_ic" in frame.columns and frame["vol_ic"].notna().any()
            else None,
        }
    if "trend_coverage" in frame.columns:
        active = frame[frame["trend_coverage"] >= 0.5]
        inactive = frame[frame["trend_coverage"] < 0.5]
        if not active.empty:
            summary["trend_active_period"] = {
                "months": int(len(active)),
                "top": _stats(active, "top_ret_pct"),
                "pool": _stats(active, "pool_ret_pct"),
            }
        if not inactive.empty:
            summary["trend_inactive_period"] = {
                "months": int(len(inactive)),
                "top": _stats(inactive, "top_ret_pct"),
                "pool": _stats(inactive, "pool_ret_pct"),
            }
    trend_up = frame["trend_up_ret_pct"].dropna().astype(float)
    trend_down = frame["trend_down_ret_pct"].dropna().astype(float)
    if not trend_up.empty and not trend_down.empty:
        summary["trend_split"] = {
            "months": int(len(trend_up)),
            "above_ma200_avg_pct": round(float(trend_up.mean()), 3),
            "below_ma200_avg_pct": round(float(trend_down.mean()), 3),
            "above_minus_below_pct": round(float(trend_up.mean() - trend_down.mean()), 3),
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    monthly_path = OUTPUT_DIR / f"backtest_dividend_income_monthly_{tag}.csv"
    holdings_path = OUTPUT_DIR / f"backtest_dividend_income_holdings_{tag}.csv"
    frame.to_csv(monthly_path, index=False)
    pd.DataFrame(holdings_log).to_csv(holdings_path, index=False)

    print("\n================ 回测汇总 ================")
    for key, label in (("top", f"策略 Top{args.top}"), ("pool", "池子等权"), ("benchmark", "中证红利 000922（价格）")):
        stats = summary.get(key)
        if not stats:
            continue
        print(
            f"{label}: 累计 {stats['cumulative_pct']:+.2f}% | 年化 {stats['annualized_pct']:+.2f}%"
            f" | 月胜率 {stats['win_rate']}% | 最大回撤 {stats['max_drawdown_pct']:.2f}%"
            f" | 月均 {stats['avg_monthly_pct']:+.2f}%"
        )
    excess = summary.get("top_vs_bench")
    if excess:
        print(f"Top{args.top} 月胜基准 {excess['win_rate']}%，月均超额 {excess['avg_excess_monthly_pct']:+.2f}%")
    split = summary.get("trend_split")
    if split:
        print(
            f"趋势分组（{split['months']} 个月）：站上 200 日线月均 {split['above_ma200_avg_pct']:+.2f}% vs"
            f" 跌破 5% 以上 {split['below_ma200_avg_pct']:+.2f}% | 差 {split['above_minus_below_pct']:+.2f}%"
        )
    rank = summary.get("ranking")
    if rank:
        extra = ""
        if rank.get("ma200_ic_mean") is not None:
            extra += f" | 距200日线 IC {rank['ma200_ic_mean']:+.3f}"
        if rank.get("vol_ic_mean") is not None:
            extra += f" | 波动率 IC {rank['vol_ic_mean']:+.3f}"
        print(
            f"评分月均 RankIC {rank['score_ic_mean']:+.3f} | 评分 Q5-Q1 月均 {rank['score_q5_minus_q1_avg']:+.2f}%"
            f" | 股息率 Q5-Q1 月均 {rank['yield_q5_minus_q1_avg']:+.2f}%{extra}"
        )
    for key, label in (("trend_active_period", "趋势因子启用期"), ("trend_inactive_period", "趋势因子未启用期")):
        block = summary.get(key)
        if not block:
            continue
        print(
            f"{label}（{block['months']} 个月）：Top{args.top} 月均 {block['top']['avg_monthly_pct']:+.2f}%"
            f" vs 池子 {block['pool']['avg_monthly_pct']:+.2f}% | 累计 Top {block['top']['cumulative_pct']:+.2f}%"
            f" vs 池子 {block['pool']['cumulative_pct']:+.2f}%"
        )
    print(f"月度明细：{monthly_path}")
    print(f"持仓明细：{holdings_path}")
    print("\n注意：幸存者偏差（池子取自 2026-07 缓存）、行情缓存接缝、业绩面仅覆盖部分标的；基准为价格指数未含分红。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="吃股息策略点时回测")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始月末（含）")
    parser.add_argument("--end", default=DEFAULT_END, help="回测截止月末（含）")
    parser.add_argument("--min-yield", type=float, default=DEFAULT_MIN_YIELD, help="股息率门槛（%）")
    parser.add_argument("--min-years", type=int, default=DEFAULT_MIN_DIV_YEARS, help="连续分红年数门槛")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="持仓只数")
    parser.add_argument("--limit", type=int, default=0, help="仅用前 N 只股票调试")
    parser.add_argument("--no-benchmark", action="store_true", help="不联网拉基准指数（只用已缓存/跳过）")
    parser.add_argument(
        "--deep-fetch",
        dest="deep_fetch",
        action="store_true",
        default=True,
        help="为池子股票抓取回测专用深历史（Baostock，默认开启）",
    )
    parser.add_argument("--no-deep-fetch", dest="deep_fetch", action="store_false", help="跳过深历史抓取")
    args = parser.parse_args()
    return run_backtest(args)


if __name__ == "__main__":
    raise SystemExit(main())
