#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线图形筛股：近 N 日「阳线多 + 走势向上 + 有成交额」（只读 stock_daily）。

用途：给手工做 T / 趋势跟随提供候选池，不触网、不写库。

默认规则（近 20 个交易日）：
- 上涨日占比（close > 前收）>= 60%，阳线占比（close > open）>= 55%；
- 区间涨幅 >= 5%（close 对 20 根前收盘）；
- 收盘 > MA20 且 MA20 五日斜率 > 0（重心上移）；
- 距 20 日最高价回撤 <= 8%；
- 20 日均成交额（close x volume 估算）>= 1 亿；
- 池：主板/创业板（60/00/30 前缀，可配），排除 ST/退市，上市满 30 个交易日。

用法：
    ./.venv-linux/bin/python scripts/screen_trend_candles.py
    ./.venv-linux/bin/python scripts/screen_trend_candles.py --days 30 --min-ret 8 --top 80
    ./.venv-linux/bin/python scripts/screen_trend_candles.py --boards 60,00,30,68
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_sub_new_segment import DEFAULT_BASIC, DEFAULT_DB  # noqa: E402

Bar = Tuple[str, float, float, float, float, float]  # date, open, high, low, close, volume


def evaluate_bars(
    bars: Sequence[Bar],
    *,
    days: int = 20,
    min_up_ratio: float = 0.60,
    min_red_ratio: float = 0.55,
    min_ret: float = 0.05,
    max_pullback: float = 0.08,
) -> Optional[Dict[str, float]]:
    """对单票最近 days 根 K 线做规则评估；不满足则返回 None。

    注意：MA20 及斜率用完整 bars 计算（需要 >= 25 根），不能用截断窗口。
    """
    if len(bars) < max(days + 1, 25):
        return None
    closes_all = [b[4] for b in bars]
    window = list(bars[-(days + 1) :])  # 多取 1 根用于首日涨跌比较
    base_close = window[0][4]
    last_close = closes_all[-1]

    up_days = sum(1 for i in range(1, len(window)) if window[i][4] > window[i - 1][4])
    up_ratio = up_days / days
    red_days = sum(1 for b in window[1:] if b[4] > b[1])
    red_ratio = red_days / days

    ret = last_close / base_close - 1.0
    high_max = max(b[2] for b in window[1:])
    dist_high = last_close / high_max - 1.0

    ma20 = sum(closes_all[-20:]) / 20.0
    ma_prev = sum(closes_all[-25:-5]) / 20.0
    slope = ma20 / ma_prev - 1.0 if ma_prev else 0.0

    amt20 = sum(b[4] * b[5] for b in window[1:]) / days / 1e8

    if (
        up_ratio < min_up_ratio
        or red_ratio < min_red_ratio
        or ret < min_ret
        or last_close <= ma20
        or slope <= 0
        or dist_high < -max_pullback
    ):
        return None
    return {
        "up_ratio": up_ratio,
        "red_ratio": red_ratio,
        "ret": ret,
        "dist_high": dist_high,
        "ma20_bias": last_close / ma20 - 1.0,
        "ma20_slope": slope,
        "amt20": amt20,
        "close": last_close,
        "high20": high_max,
    }


def _load_st_codes(basic_path: Path) -> set:
    out = set()
    with open(basic_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = str(row.get("name") or "")
            if "ST" in name.upper() or "退" in name:
                out.add(str(row.get("symbol") or "").strip())
    return out


def _load_names(basic_path: Path) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with open(basic_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("symbol") or "").strip()
            if code:
                out[code] = {
                    "name": str(row.get("name") or "").strip(),
                    "industry": str(row.get("industry") or "").strip(),
                    "list_date": str(row.get("list_date") or "").strip(),
                }
    return out


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线图形筛股：阳线多 + 走势向上 + 有量")
    parser.add_argument("--days", type=int, default=20, help="回看交易日数，默认 20")
    parser.add_argument("--min-up-ratio", type=float, default=0.60, help="上涨日占比下限，默认 0.60")
    parser.add_argument("--min-red-ratio", type=float, default=0.55, help="阳线占比下限，默认 0.55")
    parser.add_argument("--min-ret", type=float, default=5.0, help="区间涨幅下限（%%），默认 5")
    parser.add_argument("--min-amt-yi", type=float, default=1.0, help="20 日均额下限（亿），默认 1.0")
    parser.add_argument("--max-pullback", type=float, default=8.0, help="距 20 日高点最大回撤（%%），默认 8")
    parser.add_argument("--boards", default="60,00,30", help="允许的代码前缀，逗号分隔，默认 60,00,30")
    parser.add_argument("--exclude-st", action="store_true", default=True, help="排除 ST/退市（默认开）")
    parser.add_argument("--include-st", action="store_true", help="保留 ST/退市")
    parser.add_argument("--top", type=int, default=60, help="打印前 N 只，默认 60")
    parser.add_argument("--asof", default=None, help="截止日期 YYYY-MM-DD，默认库内最新")
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "data" / "screens"))
    parser.add_argument("--tag", default="")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    prefixes = tuple(p.strip() for p in str(args.boards).split(",") if p.strip())

    with sqlite3.connect(DEFAULT_DB) as con:
        if args.asof:
            asof = args.asof
        else:
            asof = con.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
        start = (date.fromisoformat(asof) - timedelta(days=150)).isoformat()
        rows = con.execute(
            "SELECT code, date, open, high, low, close, volume FROM stock_daily "
            "WHERE date >= ? AND date <= ? ORDER BY code, date",
            (start, asof),
        ).fetchall()

    grouped: Dict[str, List[Bar]] = defaultdict(list)
    for code, d, o, h, l, c, v in rows:
        if o is None or h is None or l is None or c is None:
            continue
        grouped[code].append((d, float(o), float(h), float(l), float(c), float(v or 0.0)))

    # 接缝过滤：剔除日跳变 >30% 的脏序列（统一模块 scripts/history_quality）
    from history_quality import detect_seam_codes

    dirty = detect_seam_codes(
        {code: (None, [bar[4] for bar in bars]) for code, bars in grouped.items()}
    )
    if dirty:
        for code in dirty:
            grouped.pop(code, None)
        print(f"[筛股] 接缝剔除 {len(dirty)} 只（|日跳变|>30%）")

    st_codes = _load_st_codes(Path(DEFAULT_BASIC)) if not args.include_st else set()
    names = _load_names(Path(DEFAULT_BASIC))

    hits: List[Dict[str, object]] = []
    for code, bars in grouped.items():
        if not code.startswith(prefixes):
            continue
        if code in st_codes:
            continue
        if bars[-1][0] != asof:
            continue
        if len(bars) < args.days + 25:
            continue
        metrics = evaluate_bars(
            bars,
            days=args.days,
            min_up_ratio=args.min_up_ratio,
            min_red_ratio=args.min_red_ratio,
            min_ret=args.min_ret / 100.0,
            max_pullback=args.max_pullback / 100.0,
        )
        if metrics is None or metrics["amt20"] < args.min_amt_yi:
            continue
        meta = names.get(code, {})
        bars_len = len(bars)
        hits.append(
            {
                "code": code,
                "name": meta.get("name", ""),
                "industry": meta.get("industry", ""),
                "close": round(metrics["close"], 2),
                "ret_pct": round(metrics["ret"] * 100, 1),
                "up_ratio": round(metrics["up_ratio"] * 100, 0),
                "red_ratio": round(metrics["red_ratio"] * 100, 0),
                "dist_high_pct": round(metrics["dist_high"] * 100, 1),
                "ma20_bias_pct": round(metrics["ma20_bias"] * 100, 1),
                "ma20_slope_pct": round(metrics["ma20_slope"] * 100, 2),
                "amt20_yi": round(metrics["amt20"], 2),
                "bars": bars_len,
                "list_date": meta.get("list_date", ""),
            }
        )

    hits.sort(key=lambda r: (r["ret_pct"], r["amt20_yi"]), reverse=True)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tag = args.tag or f"{asof}_{args.days}d"
    csv_path = outdir / f"trend_candles_{tag}.csv"
    if hits:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(hits[0].keys()))
            writer.writeheader()
            writer.writerows(hits)

    print(f"== 图形筛股（{asof}，回看 {args.days} 日，命中 {len(hits)} 只，前缀 {args.boards}）")
    print(f"   规则: 上涨占比>={args.min_up_ratio:.0%} 阳线占比>={args.min_red_ratio:.0%} 涨幅>={args.min_ret}% "
          f"距高点<=-{args.max_pullback}% 均额>={args.min_amt_yi}亿")
    if hits:
        print(f"   存档: {csv_path}")
    print()
    header = f"{'代码':<7}{'名称':<9}{'板':<3}{'收盘':>8}{'阶段涨':>8}{'阳线':>5}{'涨日':>5}{'距高':>7}{'MA20离':>8}{'均额亿':>8}"
    print(header)
    for r in hits[: args.top]:
        board = "沪" if r["code"].startswith("60") else ("深" if r["code"].startswith("00") else "创" if r["code"].startswith("30") else "科")
        print(
            f"{r['code']:<7}{str(r['name'])[:8]:<9}{board:<3}{r['close']:>8}{r['ret_pct']:>7}%"
            f"{int(r['up_ratio']):>4}%{int(r['red_ratio']):>4}%{r['dist_high_pct']:>6}%{r['ma20_bias_pct']:>7}%{r['amt20_yi']:>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
