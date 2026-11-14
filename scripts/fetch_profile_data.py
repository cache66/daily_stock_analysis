#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按需补齐固定信息卡缺失的数据（daily_basic / fina_indicator）。

只抓你点名的票或整市场单日快照，不做全历史全市场刷新（节省 Tushare 配额）。

用法：
    # 估值/规模：一次抓某个交易日的全市场快照（1 次接口调用）
    ./.venv-linux/bin/python scripts/fetch_profile_data.py --valuation --date 2026-11-09

    # 财务：按代码抓（每票 1 次调用）
    ./.venv-linux/bin/python scripts/fetch_profile_data.py --earnings --codes 600519,000001

    # 只看会调用什么，不实际请求
    ./.venv-linux/bin/python scripts/fetch_profile_data.py --valuation --date 2026-11-09 --dry-run

缓存位置：`data/cache/profile/`
    daily_basic_YYYYMMDD.csv        全市场估值/规模/换手
    fina_indicator_<code>.csv       单票财务指标

权限不足/限流时打印原因并返回非零，不写入半成品。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "profile"

DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,pe,pe_ttm,pb,"
    "ps_ttm,dv_ttm,total_share,float_share,total_mv,circ_mv"
)
FINA_FIELDS = (
    "ts_code,ann_date,end_date,or_yoy,netprofit_yoy,roe,grossprofit_margin,"
    "ocfps,debt_to_assets"
)


def _read_env_token() -> Optional[str]:
    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TUSHARE_PRO_TOKEN")
    if token:
        return token.strip()
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    except OSError:
        return None
    return None


def _provider():
    token = _read_env_token()
    if not token:
        raise RuntimeError("未找到 TUSHARE_TOKEN（环境变量或 .env）")
    import tushare as ts

    ts.set_token(token)
    return ts.pro_api()


def _write_csv(df: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"[fetch_profile] 写入 {path}（{len(df)} 行）")


def fetch_valuation(api: Any, trade_date: str, *, dry_run: bool) -> int:
    stamp = trade_date.replace("-", "")
    target = CACHE_DIR / f"daily_basic_{stamp}.csv"
    if target.is_file() and target.stat().st_size > 0:
        print(f"[fetch_profile] 已存在 {target.name}，跳过")
        return 0
    if dry_run:
        print(f"[dry-run] 将调用 daily_basic(trade_date={stamp}, fields={DAILY_BASIC_FIELDS})")
        return 0
    # 非交易日回退：最多往前找 5 个自然日
    for offset in range(0, 6):
        day = _shift_date(stamp, -offset)
        df = api.daily_basic(trade_date=day, fields=DAILY_BASIC_FIELDS)
        if df is not None and not df.empty:
            _write_csv(df, CACHE_DIR / f"daily_basic_{day}.csv")
            if day != stamp:
                print(f"[fetch_profile] {stamp} 无数据，已回退到 {day}")
            return 0
        time.sleep(1.0)
    print(f"[fetch_profile] daily_basic 无数据（{stamp} 及前 5 日），可能限流或权限不足")
    return 1


def fetch_earnings(api: Any, codes: List[str], *, start: str, dry_run: bool) -> int:
    """先走 Tushare；权限不足时自动降级 AKShare（口径见 _fetch_earnings_akshare）。"""
    failures = 0
    for code in codes:
        ts_code = _to_ts_code(code)
        target = CACHE_DIR / f"fina_indicator_{code}.csv"
        if dry_run:
            print(f"[dry-run] 将调用 fina_indicator(ts_code={ts_code}, start_date={start.replace('-', '')})")
            print(f"[dry-run] 失败时降级 ak.stock_financial_analysis_indicator(symbol={code})")
            continue
        df = None
        try:
            df = api.fina_indicator(ts_code=ts_code, start_date=start.replace("-", ""), fields=FINA_FIELDS)
        except Exception as exc:  # noqa: BLE001 - 权限/限流逐票隔离
            print(f"[fetch_profile] {code} Tushare 财务不可用（{str(exc)[:60]}），改走 AKShare")
        if df is not None and not df.empty:
            _write_csv(df, target)
            time.sleep(0.6)  # 保守限流
            continue
        if _fetch_earnings_akshare(code, start_year=start[:4], target=target):
            time.sleep(0.8)
            continue
        print(f"[fetch_profile] {code} 财务两路都失败")
        failures += 1
    return 1 if failures else 0


# AKShare 指标列 -> Tushare fina_indicator 字段
AKSHARE_EARNINGS_MAP = {
    "主营业务收入增长率(%)": "or_yoy",
    "净利润增长率(%)": "netprofit_yoy",
    "净资产收益率(%)": "roe",
    "销售毛利率(%)": "grossprofit_margin",
    "每股经营性现金流(元)": "ocfps",
    "资产负债率(%)": "debt_to_assets",
}


def _fetch_earnings_akshare(code: str, *, start_year: str, target: Path) -> bool:
    """AKShare 财务指标兜底。

    注意：AKShare 不返回公告日，落库时 `ann_date` 留空；服务层按
    「报告期 + 90 天」作为保守可获取判定（明确标注为假设，不当作事实展示）。
    """
    try:
        import akshare as ak

        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=str(start_year))
    except Exception as exc:  # noqa: BLE001 - 网络/接口异常降级为失败
        print(f"[fetch_profile] {code} AKShare 财务失败: {str(exc)[:80]}")
        return False
    if df is None or len(df) == 0 or "日期" not in df.columns:
        return False
    out = df[[c for c in AKSHARE_EARNINGS_MAP if c in df.columns]].copy()
    if out.empty:
        return False
    out = out.rename(columns=AKSHARE_EARNINGS_MAP)
    out.insert(0, "ts_code", _to_ts_code(code))
    out.insert(1, "ann_date", "")
    out.insert(2, "end_date", df["日期"].astype(str).str.replace("-", "", regex=False))
    out["data_source"] = "akshare_financial_indicator"
    _write_csv(out, target)
    return True


def _shift_date(stamp: str, days: int) -> str:
    from datetime import date, timedelta

    base = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    return (base + timedelta(days=days)).strftime("%Y%m%d")


def _to_ts_code(code: str) -> str:
    code = code.strip()
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按需补齐固定信息卡的估值/财务数据")
    parser.add_argument("--valuation", action="store_true", help="抓 daily_basic 全市场快照")
    parser.add_argument("--earnings", action="store_true", help="抓 fina_indicator（按代码）")
    parser.add_argument("--date", default=None, help="估值快照交易日 YYYY-MM-DD，默认今天")
    parser.add_argument("--codes", default="", help="逗号分隔代码（财务必填）")
    parser.add_argument("--start", default="2024-01-01", help="财务起始日，默认 2024-01-01")
    parser.add_argument("--dry-run", action="store_true", help="只打印将调用的接口")
    args = parser.parse_args(argv)

    if not args.valuation and not args.earnings:
        parser.error("至少指定 --valuation 或 --earnings")

    codes = [c.strip() for c in str(args.codes).split(",") if c.strip()]
    if args.earnings and not codes:
        parser.error("--earnings 需要 --codes")

    rc = 0
    if args.dry_run:
        api: Any = None
    else:
        try:
            api = _provider()
        except Exception as exc:  # noqa: BLE001 - 缺 token/缺依赖给出明确原因
            print(f"[fetch_profile] 无法初始化 Tushare: {exc}")
            return 2

    if args.valuation:
        day = args.date or _today()
        rc |= fetch_valuation(api, day, dry_run=args.dry_run)
    if args.earnings:
        rc |= fetch_earnings(api, codes, start=args.start, dry_run=args.dry_run)
    return rc


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
