# -*- coding: utf-8 -*-
"""Fetch quarterly shareholder data for the sub-new pool (idempotent, retryable).

Data sources (akshare, all East Money / exchange disclosure based):
- ``stock_gdfx_free_holding_analyse_em(date=YYYYMMDD)``: market-wide top-10 float-holder
  detail per quarter (股东名称/股东类型/期末持股/变动/公告日).
- ``stock_institute_hold(symbol=YYYYQ)``: THS institutional aggregate (best effort).
- ``stock_zh_a_gdhs(symbol=YYYYMMDD)``: market-wide shareholder-count table (best effort);
  falls back to per-stock ``stock_zh_a_gdhs_detail_em`` for the watch pool.

Outputs under ``data/cache/gdfx/``:
- ``free_analyse_<quarter>.csv``
- ``institute_hold_<quarter>.csv`` (best effort)
- ``gdhs_<date>.csv`` (best effort) / ``gdhs_detail_pool.csv`` (per-stock fallback)

Usage: ``./.venv-linux/bin/python scripts/fetch_sub_new_holders.py``
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "gdfx"
OUT.mkdir(parents=True, exist_ok=True)

QUARTERS = ["20241231", "20250331", "20250630", "20250930", "20251231", "20260331", "20260630"]
INSTITUTE_QUARTERS = ["20252", "20261", "20262"]
GDHS_DATES = ["20250930", "20251231", "20260331", "20260630"]
POOL_CSV = ROOT / "data" / "sub_new_watch" / "sub_new_watch_20260925.csv"


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch_with_retry(label: str, fn, *, retries: int = 3, sleep: float = 5.0):
    for attempt in range(1, retries + 1):
        try:
            t0 = time.time()
            df = fn()
            elapsed = time.time() - t0
            log(f"[OK] {label}: shape={getattr(df, 'shape', None)} elapsed={elapsed:.1f}s")
            return df
        except Exception as exc:  # noqa: BLE001 - keep robust for unattended run
            log(f"[RETRY {attempt}/{retries}] {label}: {type(exc).__name__}: {exc}")
            time.sleep(sleep)
    log(f"[FAIL] {label}")
    return None


def fetch_quarterly_float_holders() -> None:
    for quarter in QUARTERS:
        path = OUT / f"free_analyse_{quarter}.csv"
        if path.exists() and path.stat().st_size > 0:
            log(f"[SKIP] {path.name} exists")
            continue
        df = fetch_with_retry(
            f"free_analyse {quarter}",
            lambda quarter=quarter: ak.stock_gdfx_free_holding_analyse_em(date=quarter),
        )
        if df is not None and not df.empty:
            df.to_csv(path, index=False)
            log(f"[SAVE] {path}")


def fetch_institute_hold() -> None:
    for quarter in INSTITUTE_QUARTERS:
        path = OUT / f"institute_hold_{quarter}.csv"
        if path.exists():
            continue
        df = fetch_with_retry(
            f"institute_hold {quarter}",
            lambda quarter=quarter: ak.stock_institute_hold(symbol=quarter),
            retries=1,
            sleep=1.0,
        )
        if df is not None and not df.empty:
            df.to_csv(path, index=False)
            log(f"[SAVE] {path}")


def fetch_gdhs() -> None:
    for date_str in GDHS_DATES:
        path = OUT / f"gdhs_{date_str}.csv"
        if path.exists():
            continue
        df = fetch_with_retry(
            f"gdhs {date_str}",
            lambda date_str=date_str: ak.stock_zh_a_gdhs(symbol=date_str),
            retries=2,
        )
        if df is not None and not df.empty:
            df.to_csv(path, index=False)
            log(f"[SAVE] {path}")


def fetch_gdhs_detail_pool() -> None:
    combined_path = OUT / "gdhs_detail_pool.csv"
    if combined_path.exists() and combined_path.stat().st_size > 0:
        log(f"[SKIP] {combined_path.name} exists")
        return
    if not POOL_CSV.exists():
        log(f"[WARN] pool csv missing: {POOL_CSV}")
        return

    import pandas as pd

    pool = pd.read_csv(POOL_CSV, dtype={"code": str})
    codes = [str(code).zfill(6) for code in pool["code"].dropna()]
    frames = []
    for idx, code in enumerate(codes, 1):
        try:
            df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
            if df is not None and not df.empty:
                df = df.copy()
                df["code"] = code
                frames.append(df)
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] gdhs {code}: {type(exc).__name__}: {exc}")
        if idx % 25 == 0:
            log(f"[..] gdhs pool {idx}/{len(codes)}")
        time.sleep(0.2)

    if frames:
        pd.concat(frames, ignore_index=True).to_csv(combined_path, index=False)
        log(f"[SAVE] {combined_path} rows={sum(len(f) for f in frames)}")
    else:
        log("[WARN] gdhs pool: no data collected")


def main() -> int:
    log(f"== fetch sub-new holders start | out={OUT}")
    fetch_quarterly_float_holders()
    fetch_institute_hold()
    fetch_gdhs()
    fetch_gdhs_detail_pool()
    log("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
