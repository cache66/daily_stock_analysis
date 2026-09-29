#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超跌企稳扫描：年内高点回撤 ≥ 阈值 + 最近走好（站上MA20/5日强/10日正）+ 流动性。

口径：
- 数据：data/cache/history/cn（本地缓存，raw）；接缝保护（|单日|>25% 跳变剔除，防除权失真）。
- 默认：年内回撤 ≥35%、近5日 >+2%、近10日 >0、站上 MA20、20日均额 ≥1 亿、排除 ST。
- 输出：CSV 全量 + MD 分档（A档 距60日低 ≤25% 刚企稳 / B档 25~50% / C档 >50% 已抢跑）。

用法：
    ./.venv-linux/bin/python scripts/screen_deep_recovery.py
    ./.venv-linux/bin/python scripts/screen_deep_recovery.py --min-drawdown 40 --min-amt 2
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.history_quality import find_seam_indexes  # noqa: E402

REVIEW_DIR = PROJECT_ROOT / "data" / "strategy_review"
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"


def evaluate_recovery(
    dates: Sequence[str],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    amounts: Sequence[float],
    *,
    min_drawdown_pct: float = 35.0,
    min_amt20_yi: float = 1.0,
    min_ret5_pct: float = 2.0,
    min_ret10_pct: float = 0.0,
    seam_jump: float = 0.25,
) -> Optional[Dict[str, Any]]:
    """单票超跌企稳评估（纯函数）；不满足返回 None。"""
    if len(dates) < 80 or len(closes) < 80:
        return None
    closes_l = [float(x) for x in closes]
    if find_seam_indexes(closes_l, jump=seam_jump):
        return None
    ytd_start = next((i for i, d in enumerate(dates) if str(d) >= "2026-01-01"), None)
    if ytd_start is None or len(dates) - ytd_start < 40:
        return None
    high_ytd = max(float(x) for x in highs[ytd_start:])
    hi_i = ytd_start + [float(x) for x in highs[ytd_start:]].index(high_ytd)
    close = closes_l[-1]
    dd = close / high_ytd - 1.0
    if dd > -abs(min_drawdown_pct) / 100.0:
        return None
    ret5 = close / closes_l[-6] - 1.0
    ret10 = close / closes_l[-11] - 1.0
    ret20 = close / closes_l[-21] - 1.0
    ma20 = sum(closes_l[-20:]) / 20.0
    ma60 = sum(closes_l[-60:]) / 60.0
    ma20_prev = sum(closes_l[-25:-5]) / 20.0
    slope5 = (ma20 / ma20_prev - 1.0) * 100.0
    low60 = min(float(x) for x in lows[-60:])
    amt20 = float(np.mean([float(x) for x in amounts[-20:]])) / 1e8
    if amt20 < float(min_amt20_yi):
        return None
    recent_ok = (
        close > ma20
        and ret5 * 100.0 > float(min_ret5_pct)
        and ret10 * 100.0 > float(min_ret10_pct)
    )
    return {
        "close": round(close, 2),
        "high_ytd": round(high_ytd, 2),
        "high_date": str(dates[hi_i]),
        "dd_ytd_pct": round(dd * 100.0, 1),
        "from_low60_pct": round((close / low60 - 1.0) * 100.0, 1),
        "ret5_pct": round(ret5 * 100.0, 1),
        "ret10_pct": round(ret10 * 100.0, 1),
        "ret20_pct": round(ret20 * 100.0, 1),
        "vs_ma20_pct": round((close / ma20 - 1.0) * 100.0, 1),
        "vs_ma60_pct": round((close / ma60 - 1.0) * 100.0, 1),
        "ma20_slope5d": round(slope5, 2),
        "amt20_yi": round(amt20, 2),
        "recent_ok": bool(recent_ok),
    }


def _load_name_maps() -> tuple[Dict[str, str], Dict[str, str]]:
    name_map: Dict[str, str] = {}
    ind_map: Dict[str, str] = {}
    if BASIC_PATH.exists():
        basic = pd.read_csv(BASIC_PATH, dtype=str)
        for _, row in basic.iterrows():
            sym = str(row.get("symbol") or row.get("ts_code") or "").split(".")[0]
            name_map[sym] = str(row.get("name") or "")
            ind_map[sym] = str(row.get("industry") or "")
    return name_map, ind_map


def scan_all(
    *,
    min_drawdown_pct: float,
    min_amt20_yi: float,
    min_ret5_pct: float,
) -> pd.DataFrame:
    name_map, ind_map = _load_name_maps()
    rows: List[Dict[str, Any]] = []
    for path in glob.glob(str(CACHE_DIR / "*.csv")):
        code = path.rsplit("/", 1)[-1][:6]
        name = name_map.get(code, "")
        if "ST" in name.upper():
            continue
        try:
            frame = pd.read_csv(path, usecols=["date", "high", "low", "close", "amount"])
        except Exception:  # noqa: BLE001 - 单票坏文件跳过
            continue
        result = evaluate_recovery(
            frame["date"].astype(str).tolist(),
            frame["high"].tolist(),
            frame["low"].tolist(),
            frame["close"].tolist(),
            frame["amount"].tolist(),
            min_drawdown_pct=min_drawdown_pct,
            min_amt20_yi=min_amt20_yi,
            min_ret5_pct=min_ret5_pct,
        )
        if result is None or not result["recent_ok"]:
            continue
        result.update({"code": code, "name": name, "industry": ind_map.get(code, "")})
        rows.append(result)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values("ret5_pct", ascending=False).reset_index(drop=True)
    return frame


def build_markdown(frame: pd.DataFrame, *, generated_at: str, min_drawdown_pct: float) -> str:
    def _fmt(df: pd.DataFrame) -> str:
        headers = ["代码", "名称", "行业", "收盘", "年内高点", "高点日", "回撤%",
                   "距60日低%", "5日%", "10日%", "vsMA20%", "vsMA60%", "额20亿"]
        lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        for _, row in df.iterrows():
            lines.append("| " + " | ".join([
                str(row["code"]), str(row["name"]), str(row["industry"]), str(row["close"]),
                str(row["high_ytd"]), str(row["high_date"]), str(row["dd_ytd_pct"]),
                str(row["from_low60_pct"]), str(row["ret5_pct"]), str(row["ret10_pct"]),
                str(row["vs_ma20_pct"]), str(row["vs_ma60_pct"]), str(row["amt20_yi"]),
            ]) + " |")
        return "\n".join(lines)

    out: List[str] = []
    out.append("# 超跌企稳扫描")
    out.append("")
    out.append(
        f"- 生成时间: `{generated_at}`；口径：年内回撤 ≥{min_drawdown_pct:g}% + 站上MA20 + "
        "5日>+2% + 10日>0 + 20日均额≥1亿；剔除 ST 与接缝失真"
    )
    out.append(f"- 命中：**{len(frame)}** 只（数据：本地缓存，raw 口径）")
    out.append("")
    if frame.empty:
        out.append("（无命中）")
        return "\n".join(out) + "\n"
    early = frame[frame["from_low60_pct"] <= 25].head(15)
    mid = frame[(frame["from_low60_pct"] > 25) & (frame["from_low60_pct"] <= 50)].sort_values("amt20_yi", ascending=False).head(12)
    ext = frame[frame["from_low60_pct"] > 50].head(8)
    out.append("## A 档：早期企稳（距60日低 ≤25%，按10日涨幅）")
    out.append("")
    out.append(_fmt(early) if not early.empty else "（无）")
    out.append("")
    out.append("## B 档：已反弹（25~50%，按流动性）")
    out.append("")
    out.append(_fmt(mid) if not mid.empty else "（无）")
    out.append("")
    out.append("## C 档：抢跑区（>50%，谨慎追高）")
    out.append("")
    out.append(_fmt(ext) if not ext.empty else "（无）")
    out.append("")
    out.append("---\n*反弹候选≠反转确认；大盘开关=关时仅作观察/轻仓参考，不构成投资建议。*")
    out.append("")
    return "\n".join(out)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="超跌企稳扫描（年内深跌 + 近期走好）。")
    parser.add_argument("--min-drawdown", type=float, default=35.0, help="年内回撤门槛（%%，默认 35）")
    parser.add_argument("--min-amt", type=float, default=1.0, help="20日均额门槛（亿，默认 1）")
    parser.add_argument("--min-ret5", type=float, default=2.0, help="近5日涨幅门槛（%%，默认 2）")
    parser.add_argument("--output-dir", default=str(REVIEW_DIR), help="输出目录")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    frame = scan_all(
        min_drawdown_pct=float(args.min_drawdown),
        min_amt20_yi=float(args.min_amt),
        min_ret5_pct=float(args.min_ret5),
    )
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = date.today().isoformat()
    csv_path = out_dir / f"deep_fall_recovery_{tag}.csv"
    md_path = out_dir / f"deep_fall_recovery_{tag}.md"
    frame.to_csv(csv_path, index=False)
    md_path.write_text(
        build_markdown(frame, generated_at=generated_at, min_drawdown_pct=float(args.min_drawdown)),
        encoding="utf-8",
    )
    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")
    print(f"命中 {len(frame)} 只；A档 {int((frame['from_low60_pct'] <= 25).sum()) if not frame.empty else 0} / "
          f"B档 {int(((frame['from_low60_pct'] > 25) & (frame['from_low60_pct'] <= 50)).sum()) if not frame.empty else 0} / "
          f"C档 {int((frame['from_low60_pct'] > 50).sum()) if not frame.empty else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
