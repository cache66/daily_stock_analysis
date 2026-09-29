#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 做 T 适配仪表盘：波动（近60日）+ 趋势（MA20/MA60）+ 位置（距120日高）+ T+0 标记。

数据源：腾讯前复权日线（``web.ifzq.gtimg.cn``；qfq 口径，已规避新浪接口在
份额折算日的假暴跌毛刺）。大盘开关状态取自沪深300（sh000300）同口径。

三闸门判定（用于标注"可T / 反弹观察 / 过渡 / 波动不足 / 禁碰"）：
1. 波动闸：近 60 日日均振幅 ≥ ``--min-range``（默认 1.5%；≥2% 才比较好做T）；
2. 趋势闸：收盘 > MA20 且 MA20 五日斜率 ≥ 0（"顺趋势或修复中"）；
3. 确认闸：在趋势闸基础上再看是否站上 MA60（跌破 MA60 = 下跌趋势未修复）。

用法：
    ./.venv-linux/bin/python scripts/etf_t_dashboard.py
    ./.venv-linux/bin/python scripts/etf_t_dashboard.py --days 60 --min-range 1.5
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REVIEW_DIR = PROJECT_ROOT / "data" / "strategy_review"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# (代码, 名称, 是否T+0, 分组)
WATCHLIST: List[Tuple[str, str, bool, str]] = [
    ("sh588200", "科创芯片ETF·嘉实", False, "高波动"),
    ("sh512480", "半导体ETF·国联安", False, "高波动"),
    ("sz159995", "芯片ETF·华夏", False, "高波动"),
    ("sh512760", "芯片ETF·国泰", False, "高波动"),
    ("sh588000", "科创50ETF·华夏", False, "高波动"),
    ("sz159949", "创业板50ETF·华安", False, "高波动"),
    ("sz159915", "创业板ETF·易方达", False, "高波动"),
    ("sh512400", "有色金属ETF·南方", False, "中波动"),
    ("sz159869", "游戏ETF·华夏", False, "中波动"),
    ("sh512660", "军工ETF·国泰", False, "中波动"),
    ("sh512170", "医疗ETF·华宝", False, "中波动"),
    ("sh512010", "医药ETF·易方达", False, "中波动"),
    ("sh515790", "光伏ETF·华泰柏瑞", False, "中波动"),
    ("sh515030", "新能源车ETF·华夏", False, "中波动"),
    ("sh512690", "酒ETF·鹏华", False, "中波动"),
    ("sh513330", "恒生互联网ETF·华夏", True, "T+0"),
    ("sh513050", "中概互联网ETF·易方达", True, "T+0"),
    ("sh513130", "恒生科技ETF·华泰柏瑞", True, "T+0"),
    ("sh513180", "恒生科技ETF·华夏", True, "T+0"),
    ("sh513100", "纳指ETF·国泰", True, "T+0"),
    ("sh518880", "黄金ETF·华安", True, "T+0"),
    ("sh512880", "证券ETF·国泰", False, "券商对照"),
    ("sh512000", "券商ETF·华宝", False, "券商对照"),
    ("sh512800", "银行ETF·华宝", False, "防守对照"),
    ("sh512890", "红利低波ETF·华泰柏瑞", False, "防守对照"),
    ("sh510880", "红利ETF·华泰柏瑞", False, "防守对照"),
]

SEAM_RET_THRESHOLD_PCT = 15.0  # 单日 |涨跌| 超过视为折算毛刺（腾讯 qfq 下应≈0）


def fetch_tencent_kline(
    code: str,
    *,
    count: int = 400,
    session: Optional[requests.Session] = None,
    end_date: Optional[date] = None,
) -> List[List[Any]]:
    """拉取腾讯前复权日线；返回原始行 [date, open, close, high, low, volume, ...]。"""
    end = end_date or date.today()
    start = end - timedelta(days=count)
    url = (
        f"{TENCENT_KLINE_URL}?param={code},day,"
        f"{start.isoformat()},{end.isoformat()},{int(count)},qfq"
    )
    http = session or requests.Session()
    payload = http.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).json()
    block = (payload.get("data") or {}).get(code) or {}
    rows = block.get("qfqday") or block.get("day") or []
    return list(rows)


def _parse_rows(rows: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        try:
            parsed.append(
                {
                    "date": str(row[0]),
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item["date"])
    return parsed


def compute_metrics(
    rows: Sequence[Sequence[Any]],
    *,
    volatility_days: int = 60,
    high_days: int = 120,
) -> Optional[Dict[str, Any]]:
    """计算波动/趋势/位置指标；样本不足返回 None。"""
    bars = _parse_rows(rows)
    if len(bars) < max(volatility_days + 1, 61):
        return None
    closes = [bar["close"] for bar in bars]
    close = closes[-1]

    # 波动（近 volatility_days 个交易日；剔除可能的折算毛刺日）
    window = bars[-volatility_days - 1 :]
    rets: List[float] = []
    ranges: List[float] = []
    outliers: List[str] = []
    for idx in range(1, len(window)):
        prev_close = window[idx - 1]["close"]
        bar = window[idx]
        ret = (bar["close"] / prev_close - 1.0) * 100.0
        rng = (bar["high"] - bar["low"]) / prev_close * 100.0
        if abs(ret) > SEAM_RET_THRESHOLD_PCT:
            outliers.append(bar["date"])
            continue
        rets.append(ret)
        ranges.append(rng)
    avg_range = statistics.mean(ranges) if ranges else None
    avg_abs = statistics.mean(abs(value) for value in rets) if rets else None
    ge1 = (sum(1 for value in ranges if value >= 1.0) / len(ranges) * 100.0) if ranges else None
    ge2 = (sum(1 for value in ranges if value >= 2.0) / len(ranges) * 100.0) if ranges else None
    ann_vol = statistics.pstdev(rets) * (244 ** 0.5) if len(rets) > 1 else None

    # 趋势 / 位置
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    ma20_prev = sum(closes[-25:-5]) / 20.0
    slope20_5d = (ma20 / ma20_prev - 1.0) * 100.0
    high_window = [bar["high"] for bar in bars[-high_days:]]
    high_ref = max(high_window)
    ret60 = (close / closes[-61] - 1.0) * 100.0
    return {
        "close": round(close, 4),
        "avg_range_pct": round(avg_range, 2) if avg_range is not None else None,
        "avg_abs_ret_pct": round(avg_abs, 2) if avg_abs is not None else None,
        "ge1_share_pct": round(ge1, 0) if ge1 is not None else None,
        "ge2_share_pct": round(ge2, 0) if ge2 is not None else None,
        "ann_vol_pct": round(ann_vol, 0) if ann_vol is not None else None,
        "vs_ma20_pct": round((close / ma20 - 1.0) * 100.0, 1),
        "vs_ma60_pct": round((close / ma60 - 1.0) * 100.0, 1),
        "slope20_5d_pct": round(slope20_5d, 2),
        "vs_high120_pct": round((close / high_ref - 1.0) * 100.0, 1),
        "ret60_pct": round(ret60, 1),
        "bars": len(bars),
        "outlier_dates": outliers,
    }


def classify(metrics: Dict[str, Any], *, min_range_pct: float = 1.5) -> str:
    """三闸门标签：可T / 反弹观察 / 过渡 / 波动不足 / 禁碰。"""
    avg_range = metrics.get("avg_range_pct")
    vs_ma20 = metrics.get("vs_ma20_pct")
    slope = metrics.get("slope20_5d_pct")
    vs_ma60 = metrics.get("vs_ma60_pct")
    if avg_range is None or vs_ma20 is None or slope is None:
        return "数据不足"
    if avg_range < float(min_range_pct):
        return "波动不足"
    above_ma20 = vs_ma20 > 0
    slope_up = slope >= 0
    above_ma60 = (vs_ma60 or 0) > 0
    if above_ma20 and slope_up and above_ma60:
        return "可T"
    if above_ma20 and slope_up:
        return "反弹观察"
    if (not above_ma20) and (not slope_up):
        return "禁碰"
    return "过渡"


TAG_ORDER = {"可T": 0, "反弹观察": 1, "过渡": 2, "波动不足": 3, "禁碰": 4, "数据不足": 5, "获取失败": 6}


def build_rows(
    *,
    days: int,
    min_range_pct: float,
    session: Optional[requests.Session] = None,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    http = session or requests.Session()
    http.headers.update({"User-Agent": "Mozilla/5.0"})
    rows: List[Dict[str, Any]] = []
    for code, name, t0, group in WATCHLIST:
        record: Dict[str, Any] = {"code": code, "name": name, "t0": t0, "group": group}
        try:
            raw = fetch_tencent_kline(code, count=400, session=http)
            metrics = compute_metrics(raw, volatility_days=days)
            if metrics is None:
                record.update({"tag": "数据不足", "metrics": None})
            else:
                record.update({"tag": classify(metrics, min_range_pct=min_range_pct), "metrics": metrics})
        except Exception as exc:  # noqa: BLE001 - 单票失败不阻塞
            record.update({"tag": "获取失败", "metrics": None, "error": str(exc)[:120]})
        rows.append(record)
        time.sleep(0.15)

    market: Optional[Dict[str, Any]] = None
    try:
        raw_index = fetch_tencent_kline("sh000300", count=120, session=http)
        bars = _parse_rows(raw_index)
        if len(bars) >= 21:
            closes = [bar["close"] for bar in bars]
            ma20 = sum(closes[-20:]) / 20.0
            market = {
                "close": round(closes[-1], 2),
                "ma20": round(ma20, 2),
                "switch": "开" if closes[-1] >= ma20 else "关",
                "vs_ma20_pct": round((closes[-1] / ma20 - 1.0) * 100.0, 2),
            }
    except Exception:  # noqa: BLE001 - 大盘数据失败不阻塞
        market = None

    rows.sort(
        key=lambda item: (
            TAG_ORDER.get(str(item.get("tag")), 9),
            -((item.get("metrics") or {}).get("avg_range_pct") or 0.0),
        )
    )
    return rows, market


def build_markdown(
    rows: Sequence[Dict[str, Any]],
    market: Optional[Dict[str, Any]],
    *,
    generated_at: str,
    days: int,
    min_range_pct: float,
) -> str:
    out: List[str] = []
    out.append("# ETF 做 T 仪表盘")
    out.append("")
    out.append(
        f"- 生成时间: `{generated_at}`；波动窗口: 近 `{days}` 个交易日；"
        f"波动闸: 日均振幅 ≥ `{min_range_pct:g}%`"
    )
    out.append(
        "- 数据源：腾讯前复权（qfq）；单日 |涨跌|>15% 视为折算毛刺已剔除（正常应为 0 条）"
    )
    out.append(
        "- 三闸门：①波动（日均振幅）②趋势（>MA20 且 MA20 五日斜率 ≥0）"
        "③确认（站上 MA60）；标签：可T / 反弹观察 / 过渡 / 波动不足 / 禁碰"
    )
    if market:
        out.append(
            f"- **大盘开关（沪深300）**：{market['switch']}"
            f"（收盘 {market['close']} vs MA20 {market['ma20']}，{market['vs_ma20_pct']:+.2f}%）"
        )
    else:
        out.append("- **大盘开关（沪深300）**：数据获取失败")
    out.append("")
    out.append(
        "| 标签 | 代码 | 名称 | 分组 | T+0 | 日均振幅% | ≥2%天% | 年化波动% | "
        "距MA20% | 距MA60% | MA20斜率5日 | 距120日高% | 60日收益% |"
    )
    out.append(
        "| --- | --- | --- | --- | :-: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for row in rows:
        metrics = row.get("metrics") or {}
        out.append(
            "| {tag} | {code} | {name} | {group} | {t0} | {rng} | {ge2} | {vol} | "
            "{d20} | {d60} | {slope} | {dhigh} | {ret60} |".format(
                tag=row.get("tag"),
                code=row.get("code"),
                name=row.get("name"),
                group=row.get("group"),
                t0="✅" if row.get("t0") else "-",
                rng=metrics.get("avg_range_pct", "-"),
                ge2=metrics.get("ge2_share_pct", "-"),
                vol=metrics.get("ann_vol_pct", "-"),
                d20=metrics.get("vs_ma20_pct", "-"),
                d60=metrics.get("vs_ma60_pct", "-"),
                slope=metrics.get("slope20_5d_pct", "-"),
                dhigh=metrics.get("vs_high120_pct", "-"),
                ret60=metrics.get("ret60_pct", "-"),
            )
        )
    out.append("")
    out.append(
        "---\n*提醒：高波动=高风险；我们此前研究结论：做T必须在「趋势未坏+回踩够深」时做，"
        "单边下跌里抢反弹是主要亏损来源。本表仅为纪律工具，不构成投资建议。*"
    )
    out.append("")
    return "\n".join(out)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETF 做 T 适配仪表盘（腾讯前复权口径）。")
    parser.add_argument("--days", type=int, default=60, help="波动窗口（默认 60 个交易日）")
    parser.add_argument("--min-range", type=float, default=1.5, help="波动闸（日均振幅%，默认 1.5）")
    parser.add_argument("--output-dir", default=str(REVIEW_DIR), help="输出目录")
    parser.add_argument("--out-name", default=None, help="输出文件名主干（默认按日期）")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    rows, market = build_rows(days=int(args.days), min_range_pct=float(args.min_range))

    stem = str(args.out_name) if args.out_name else f"etf_t_dashboard_{date.today().isoformat()}"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"

    md_path.write_text(
        build_markdown(
            rows,
            market,
            generated_at=generated_at,
            days=int(args.days),
            min_range_pct=float(args.min_range),
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "days": int(args.days),
                "min_range_pct": float(args.min_range),
                "market": market,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    if market:
        print(f"大盘开关: {market['switch']}（300 {market['vs_ma20_pct']:+.2f}% vs MA20）")
    for row in rows:
        metrics = row.get("metrics") or {}
        print(
            f"  [{row.get('tag')}] {row['code']} {row['name']}: "
            f"振幅 {metrics.get('avg_range_pct', '-')}% | 距MA20 {metrics.get('vs_ma20_pct', '-')}% | "
            f"距MA60 {metrics.get('vs_ma60_pct', '-')}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
