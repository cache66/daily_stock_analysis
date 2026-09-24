# -*- coding: utf-8 -*-
"""本地行情快照刷新工具（无 Tushare 配额时构造 daily_basic 近似快照）。

背景：Tushare ``daily_basic`` 账号限频（1 次/分钟，高压下 1 次/小时甚至更久），
股息线在配额受限时拿不到全市场股息率快照。本脚本用免费行情源构造本地快照，
让 ``select_dividend_income_candidates.py --cache-only`` 可离线运行：

- 最新交易日：腾讯全市场行情（收盘价 / PE(TTM) / PB / 总市值）；
- 近 N 个交易日：对"最新日股息率 >= 阈值"的股票拉腾讯日 K 线，重建各日收盘价；
- 股息率（代理 dv_ttm）= 过去 365 天已实施现金分红合计（AKShare ``stock_fhps_em``
  缓存，按除权除息日归集） / 当日收盘价。

产出写入 ``data/cache/dividend_income/daily_basic/daily_basic_YYYYMMDD.csv``，
附带 ``snapshot_source=tencent_proxy`` 列用于区分数据来源；不覆盖已有 Tushare 快照
（若目标文件已存在则跳过，可用 ``--overwrite`` 强制刷新）。

注意事项：
- 历史日快照只覆盖"最新日股息率 >= 阈值"的股票，历史日候选池以该子集为准；
- 代理股息率口径与 Tushare ``dv_ttm`` 接近但非完全一致（按已除息口径归集）；
- 本工具仅建议在 Tushare 配额不可用时使用。

用法示例：
    python scripts/refresh_local_daily_basic_snapshot.py                 # 最近 5 个交易日
    python scripts/refresh_local_daily_basic_snapshot.py --days 3 --yield-cutoff 3.0
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "dividend_income"
PROXY_SOURCE_TAG = "tencent_proxy"
QUOTE_URL = "http://qt.gtimg.cn/q="
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def _to_tencent_symbol(ts_code: str) -> str | None:
    code, _, suffix = str(ts_code).upper().partition(".")
    if suffix == "SH":
        return f"sh{code}"
    if suffix == "SZ":
        return f"sz{code}"
    return None


def _to_tencent_symbol_from_code6(code6: str) -> str:
    return ("sh" if code6.startswith(("5", "6", "9")) else "sz") + code6


def try_refresh_stock_basic(cache_dir: Path) -> str:
    """尽力用 Tushare 刷新 stock_basic（含 act_ent_type）；配额受限时返回失败信息。"""
    try:
        import tushare as ts

        from src.config import setup_env

        setup_env()
        token = ""
        for key in ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "TUSHARE_API_TOKEN"):
            token = str(os.getenv(key) or "").strip()
            if token:
                break
        if not token:
            return "no_token"
        pro = ts.pro_api(token)
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry,market,list_date,exchange,act_ent_type",
        )
        if df is not None and not df.empty:
            (cache_dir / "stock_basic.csv").parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_dir / "stock_basic.csv", index=False)
            return f"ok rows={len(df)}"
        return "empty"
    except Exception as exc:  # noqa: BLE001
        return f"failed: {str(exc)[:120]}"


def fetch_quotes(symbols: list[str], *, batch_size: int = 60, workers: int = 6) -> dict[str, dict]:
    """腾讯批量行情：code6 -> {close, pe_ttm, pb, mv_yi}。"""
    batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]
    out: dict[str, dict] = {}

    def fetch_one(batch: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for _ in range(2):
            try:
                resp = SESSION.get(QUOTE_URL + ",".join(batch), timeout=15)
                resp.encoding = "gbk"
                for line in resp.text.strip().split(";"):
                    line = line.strip()
                    if "=" not in line or '"' not in line:
                        continue
                    fields = line.split('="', 1)[1].rstrip('"').split("~")
                    if len(fields) < 47 or not fields[2]:
                        continue
                    try:
                        close = float(fields[3]) if fields[3] else None
                        pe = float(fields[39]) if fields[39] else None
                        pb = float(fields[46]) if fields[46] else None
                        mv_yi = float(fields[45]) if fields[45] else None
                    except ValueError:
                        continue
                    if close is None or close <= 0:
                        continue
                    result[fields[2]] = {"close": close, "pe_ttm": pe, "pb": pb, "mv_yi": mv_yi}
                return result
            except Exception:  # noqa: BLE001
                continue
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(fetch_one, batch) for batch in batches]):
            out.update(future.result())
    return out


def fetch_kline(symbol: str, *, days: int = 45) -> dict[str, float]:
    """腾讯日 K 线：日期 -> 收盘价（未复权；近端窗口内多数股票与复权价一致）。"""
    url = f"{KLINE_URL}?param={symbol},day,,,{days},"
    try:
        resp = SESSION.get(url, timeout=15)
        data = resp.json().get("data", {}).get(symbol, {})
        rows = data.get("day") or data.get("qfqday") or []
        return {str(row[0]): float(row[2]) for row in rows if len(row) >= 3 and row[2]}
    except Exception:  # noqa: BLE001
        return {}


def load_dividends(fhps_dir: Path) -> dict[str, list[tuple[date, float]]]:
    """已实施现金分红（按除权除息日归集）：code6 -> [(ex_date, cash_per_share)]。"""
    frames = [pd.read_csv(path, dtype=str) for path in sorted(fhps_dir.glob("fhps_*.csv"))]
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    df = df[df["方案进度"].astype(str).str.contains("实施", na=False)]
    cash = pd.to_numeric(df.get("现金分红-现金分红比例"), errors="coerce")
    df = df.assign(_cash=cash).dropna(subset=["_cash"])
    df = df[df["_cash"] > 0]
    out: dict[str, list[tuple[date, float]]] = {}
    for _, row in df.iterrows():
        code6 = str(row.get("代码", "")).strip().zfill(6)
        ex_text = str(row.get("除权除息日", "") or "").strip()
        if not ex_text or ex_text.lower() == "nan":
            continue
        try:
            ex_date = datetime.strptime(ex_text[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        out.setdefault(code6, []).append((ex_date, float(row["_cash"]) / 10.0))
    return out


def ttm_cash(rows: list[tuple[date, float]] | None, day: date) -> float:
    if not rows:
        return 0.0
    low = day - timedelta(days=365)
    return sum(cash for ex, cash in rows if low < ex <= day)


def _recent_trade_days(count: int) -> list[date]:
    series = fetch_kline("sh600519", days=max(60, count * 6))
    days = sorted(series.keys())[-count:]
    if len(days) < count:
        # K 线不可用（如被风控限流）：退化为工作日日历（含节假日时仅影响历史日补数）
        fallback: list[date] = []
        cursor = date.today()
        while len(fallback) < count:
            if cursor.weekday() < 5:
                fallback.append(cursor)
            cursor -= timedelta(days=1)
        return sorted(fallback)
    return [datetime.strptime(text, "%Y-%m-%d").date() for text in days]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地行情快照刷新（腾讯行情 + AKShare 分红缓存）")
    parser.add_argument("--days", type=int, default=5, help="生成的最近交易日数量，默认 5。")
    parser.add_argument(
        "--yield-cutoff",
        type=float,
        default=2.0,
        help="历史日重建 K 线的股息率门槛（%%），默认 2.0（覆盖 4%% 门槛附近的漂移）。",
    )
    parser.add_argument("--kline-days", type=int, default=45, help="每只股票拉取的 K 线天数，默认 45。")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="股息线缓存根目录。")
    parser.add_argument("--refresh-stock-basic", action="store_true", help="同时尝试用 Tushare 刷新 stock_basic（消耗一次配额）。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的目标快照文件。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    daily_dir = cache_dir / "daily_basic"
    fhps_dir = cache_dir / "akshare_fhps"
    daily_dir.mkdir(parents=True, exist_ok=True)

    if args.refresh_stock_basic:
        print("[0/5] 刷新 stock_basic:", try_refresh_stock_basic(cache_dir))

    basic_path = cache_dir / "stock_basic.csv"
    if not basic_path.exists():
        print(f"【错误】缺少股票基础信息缓存：{basic_path}（请先正常运行一次股息线）")
        return 1
    basic = pd.read_csv(basic_path, dtype=str)
    ts_by_code6 = {
        str(row["symbol"]).strip().zfill(6): str(row["ts_code"]).strip()
        for _, row in basic.iterrows()
        if str(row.get("symbol", "")).strip()
    }
    symbols = sorted({sym for ts_code in basic["ts_code"] if (sym := _to_tencent_symbol(ts_code))})

    target_days = _recent_trade_days(args.days)
    print(f"[1/5] 目标交易日：{[d.isoformat() for d in target_days]}")

    print(f"[2/5] 抓取全市场行情 {len(symbols)} 只 ...")
    quotes = fetch_quotes(symbols)
    print(f"      有效报价 {len(quotes)} 只")

    dividends = load_dividends(fhps_dir)
    print(f"[3/5] 分红缓存覆盖 {len(dividends)} 只股票")

    today = target_days[-1]
    near_codes = []
    for code6, quote in quotes.items():
        ttm = ttm_cash(dividends.get(code6), today)
        if quote["close"] > 0 and ttm / quote["close"] * 100.0 >= args.yield_cutoff:
            near_codes.append(code6)
    print(f"      今日股息率 >= {args.yield_cutoff}% 的股票 {len(near_codes)} 只（历史日重建范围）")

    closes: dict[str, dict[str, float]] = {}
    historical_days = [d for d in target_days if d != today]
    if historical_days:
        print(f"[4/5] 拉取近端 K 线（{len(near_codes)} 只）...")
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch_kline, _to_tencent_symbol_from_code6(code6), days=args.kline_days): code6 for code6 in near_codes}
            done = 0
            for future in as_completed(futures):
                code6 = futures[future]
                series = future.result()
                if series:
                    closes[code6] = series
                done += 1
                if done % 500 == 0:
                    print(f"      kline 进度 {done}/{len(futures)}")
        print(f"      有效 K 线 {len(closes)} 只")
    else:
        print("[4/5] 仅最新交易日，跳过 K 线拉取")

    shares_map: dict[str, float] = {}
    frames = [pd.read_csv(path, dtype=str) for path in sorted(fhps_dir.glob("fhps_*.csv"))]
    if frames:
        all_fhps = pd.concat(frames, ignore_index=True)
        for _, row in all_fhps.iterrows():
            code6 = str(row.get("代码", "")).strip().zfill(6)
            if code6 not in shares_map:
                shares = pd.to_numeric(row.get("总股本"), errors="coerce")
                if pd.notna(shares) and shares > 0:
                    shares_map[code6] = float(shares)

    print("[5/5] 写入快照 ...")
    written = 0
    for day in target_days:
        out_path = daily_dir / f"daily_basic_{day:%Y%m%d}.csv"
        if out_path.exists() and not args.overwrite:
            print(f"  {day.isoformat()}: 已存在，跳过（--overwrite 可强制刷新）-> {out_path.name}")
            continue
        rows = []
        day_key = day.isoformat()
        for code6, quote in quotes.items():
            close = quote["close"] if day == today else closes.get(code6, {}).get(day_key)
            if close is None or close <= 0:
                continue
            ttm = ttm_cash(dividends.get(code6), day)
            mv_wan = None
            if day == today and quote.get("mv_yi"):
                mv_wan = quote["mv_yi"] * 10000.0
            elif code6 in shares_map:
                mv_wan = close * shares_map[code6] / 10000.0
            fallback_code = code6 + (".SH" if code6.startswith(("5", "6", "9")) else ".SZ")
            rows.append(
                {
                    "ts_code": ts_by_code6.get(code6) or fallback_code,
                    "close": round(close, 3),
                    "dv_ratio": round(ttm / close * 100.0, 4),
                    "dv_ttm": round(ttm / close * 100.0, 4),
                    "pe_ttm": (quote["pe_ttm"] if day == today else None),
                    "pb": (quote["pb"] if day == today else None),
                    "total_mv": (round(mv_wan, 2) if mv_wan else None),
                    "snapshot_source": PROXY_SOURCE_TAG,
                }
            )
        if not rows:
            print(f"  {day.isoformat()}: 无可用行情/收盘价，跳过")
            continue
        pd.DataFrame(rows).to_csv(out_path, index=False)
        print(f"  {day.isoformat()}: {len(rows)} 行 -> {out_path.name}")
        written += 1
    print(f"完成：写入/跳过 {written}/{len(target_days)} 个快照（cache dir: {cache_dir}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
