#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红利 T 提示器：扫 ① 趋势红利（可选带 ②），输出回踩触发档位 + 均线健康度。

规则（2026-09-29 讨论稿）：
- 池子：三级清单 ① 趋势红利（biz 稳增/平稳 & t_good & trend_ok）；② 组仅作参考。
- 触发档：距 10 日高点回撤 ≥3% 为第 1 档、≥5% 第 2 档、≥7% 第 3 档（分批买入参考）。
- 护栏：收盘跌破 MA200 或 MA200 60 日斜率 ≤0 → 闸门失效，停止 T；
        跌破 MA60 → 提示"新单减半/等站回"。
- 卖出参考：反弹 +2~3% 止盈 或 最长持 10 个交易日（S1 口径，31bps 成本已含）。

数据：data/cache/history/cn（本地缓存）；名单：最新体检表 ① 组（每月体检后自动更新）。
用法：
    ./.venv-linux/bin/python scripts/dividend_t_watch.py
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.make_dividend_three_tiers import build_tiers, load_checkup  # noqa: E402

REVIEW_DIR = PROJECT_ROOT / "data" / "strategy_review"
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"

LEVEL_ORDER = {"🟢 第3档": 0, "🟢 第2档": 1, "🟢 第1档": 2, "🟡": 3, "⚪": 4, "⛔": 5}


def evaluate_t_state(
    closes: Sequence[float], highs: Sequence[float]
) -> Optional[Dict[str, Any]]:
    """计算单票 T 状态（纯函数）。样本不足 220 根返回 None。"""
    if len(closes) < 220 or len(highs) < 220:
        return None
    close = float(closes[-1])
    dd10 = (close / max(highs[-10:]) - 1.0) * 100.0
    ret5 = (close / float(closes[-6]) - 1.0) * 100.0
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    ma200 = sum(closes[-200:]) / 200.0
    ma200_prev = sum(closes[-260:-60]) / 200.0
    slope200 = (ma200 / ma200_prev - 1.0) * 100.0
    gate_ok = close >= ma200 and slope200 > 0
    if not gate_ok:
        level = "⛔ 停T（闸门失效）"
    elif dd10 <= -7.0:
        level = "🟢 第3档（-7%深档）"
    elif dd10 <= -5.0:
        level = "🟢 第2档（-5%档）"
    elif dd10 <= -3.0:
        level = "🟢 第1档（-3%档）"
    elif dd10 <= -2.0:
        level = f"🟡 接近（还差 {abs(dd10 + 3.0):.1f}%）"
    else:
        level = "⚪ 高位（无触发）"
    return {
        "close": round(close, 2),
        "dd10_pct": round(dd10, 1),
        "ret5_pct": round(ret5, 1),
        "vs_ma20_pct": round((close / ma20 - 1.0) * 100.0, 1),
        "vs_ma60_pct": round((close / ma60 - 1.0) * 100.0, 1),
        "vs_ma200_pct": round((close / ma200 - 1.0) * 100.0, 1),
        "ma200_slope60": round(slope200, 2),
        "below_ma60": bool(close < ma60),
        "level": level,
    }


def find_latest_checkup() -> Path:
    paths = sorted(glob.glob(str(REVIEW_DIR / "dividend_top100_checkup_*_top100.csv")))
    if not paths:
        raise SystemExit("未找到体检 CSV：data/strategy_review/dividend_top100_checkup_*_top100.csv")
    return Path(paths[-1])


def build_watch_rows(*, include_watch: bool = False, checkup_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    df = load_checkup(checkup_path or find_latest_checkup())
    tiers = build_tiers(df)
    pool = tiers["trend"]
    if include_watch:
        pool = pd.concat([tiers["trend"], tiers["t_watch"]], ignore_index=True)
    rows: List[Dict[str, Any]] = []
    for item in pool.itertuples():
        code = str(getattr(item, "code") or "").zfill(6)
        name = str(getattr(item, "name") or "")
        industry = str(getattr(item, "industry") or "")
        path = CACHE_DIR / f"{code}.csv"
        record: Dict[str, Any] = {"code": code, "name": name, "industry": industry}
        if not path.exists():
            record.update({"level": "⛔ 数据缺失", "state": None})
            rows.append(record)
            continue
        frame = pd.read_csv(path, usecols=["date", "high", "close"])
        closes = [float(x) for x in frame["close"].tolist()]
        highs = [float(x) for x in frame["high"].tolist()]
        state = evaluate_t_state(closes, highs)
        record.update({"state": state, "last_date": str(frame["date"].iloc[-1])})
        record["level"] = (state or {}).get("level", "⛔ 数据不足")
        rows.append(record)
    rows.sort(key=lambda item: LEVEL_ORDER.get(str(item["level"])[:2], 9))
    return rows


def build_markdown(rows: Sequence[Dict[str, Any]], *, generated_at: str) -> str:
    out: List[str] = []
    out.append("# 红利 T 提示器（① 趋势红利）")
    out.append("")
    out.append(f"- 生成时间: `{generated_at}`；数据: 本地缓存（最新交易日收盘）")
    out.append(
        "- 触发档：距 10 日高点回撤 ≥3% / ≥5% / ≥7% 为第 1/2/3 档（分批买入参考）；"
        "卖出参考 +2~3% 止盈或最长 10 个交易日"
    )
    out.append(
        "- 护栏：破 MA200 或 MA200 斜率转负 → 停 T；破 MA60 → 新单减半/等站回。"
        "T 只用机动仓，熊市不加总仓。"
    )
    triggered = sum(1 for r in rows if str(r["level"]).startswith("🟢"))
    near = sum(1 for r in rows if str(r["level"]).startswith("🟡"))
    blocked = sum(1 for r in rows if str(r["level"]).startswith("⛔"))
    out.append(f"- 汇总：触发 **{triggered}** 只 / 接近 {near} 只 / 停T或异常 {blocked} 只")
    out.append("")
    out.append("| 状态 | 代码 | 名称 | 行业 | 收盘 | 距10日高% | 5日% | vsMA20% | vsMA60% | vsMA200% | MA200斜率 | 备注 |")
    out.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in rows:
        state = row.get("state") or {}
        note = "破MA60（新单减半）" if state.get("below_ma60") else ""
        if row.get("level") == "⛔ 数据缺失":
            note = "无缓存文件"
        out.append(
            "| {level} | {code} | {name} | {industry} | {close} | {dd} | {r5} | {d20} | {d60} | {d200} | {slope} | {note} |".format(
                level=row.get("level"),
                code=row.get("code"),
                name=row.get("name"),
                industry=row.get("industry"),
                close=state.get("close", "-"),
                dd=state.get("dd10_pct", "-"),
                r5=state.get("ret5_pct", "-"),
                d20=state.get("vs_ma20_pct", "-"),
                d60=state.get("vs_ma60_pct", "-"),
                d200=state.get("vs_ma200_pct", "-"),
                slope=state.get("ma200_slope60", "-"),
                note=note,
            )
        )
    out.append("")
    out.append("---\n*样本内规则参考，不构成投资建议；下月体检后名单自动更新。*")
    out.append("")
    return "\n".join(out)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="红利 T 提示器（① 趋势红利回踩触发扫描）。")
    parser.add_argument("--checkup", default="", help="指定体检 CSV（默认取最新）")
    parser.add_argument("--include-watch", action="store_true", help="附带 ② 吃息+T 观察组")
    parser.add_argument("--output-dir", default=str(REVIEW_DIR), help="输出目录")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = build_watch_rows(
        include_watch=bool(args.include_watch),
        checkup_path=Path(args.checkup) if args.checkup else None,
    )
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"dividend_t_watch_{date.today().isoformat()}.md"
    md_path.write_text(build_markdown(rows, generated_at=generated_at), encoding="utf-8")
    print(f"wrote {md_path}")
    for row in rows:
        state = row.get("state") or {}
        if str(row["level"]).startswith(("🟢", "🟡", "⛔")):
            print(
                f"  {row['level']} {row['code']} {row['name']}: "
                f"距10日高 {state.get('dd10_pct', '-')}% | vsMA60 {state.get('vs_ma60_pct', '-')}%"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
