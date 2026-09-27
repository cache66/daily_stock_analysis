#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次新观察清单扫描器（手工卫星线辅助：只出候选池，不产生交易信号）。

用途：按“游资式次新超跌反抽”的手工打法做第一层筛选——
上市年龄 + 流通市值区间 + 距上市高点深跌 + “跌不动”（缩量 / 收窄 / 不新低）。

口径（与《数据与验证统一约定》一致）：
- 上市日期：本地快照 `data/cache/dividend_income/stock_basic.csv`；
- 流通市值：腾讯批量行情（复用 `refresh_local_daily_basic_snapshot.fetch_quotes` 字段 44）；
- K 线：优先框架取数 `KlineSelectorService.build_fast_a_share_manager()`（本地缓存优先），
  缺失时回退 `StockRepository.get_range`（`stock_daily`，2026 年起窗口）；
- 本工具只做“候选池整理”，不落库、不接默认链路、不构成回测。

筛选条件（默认值可调）：
- 上市 120 ~ 800 天（``--min-age-days`` / ``--max-age-days``）；
- 流通市值 ≤ 30 亿（``--max-float-mv-yi``；≤20 亿在输出中标记 ★）；
- 距上市以来最高价跌幅 ≥ 45%（``--min-decline-pct``）；
- “跌不动”评分 ≥ 2（``--min-calm-score``），评分 = 3 项客观条件各 1 分：
  ①近 20 日平均振幅 < 4%；②近 5 日均量 / 60 日峰值量 < 25%；③最近 10 个交易日未创 20 日收盘新低。

产出：`data/sub_new_watch/sub_new_watch_<YYYYMMDD>.{md,csv}`。
注意：成交额为估算（等于 收盘 × 成交量，随数据源单位不同只做量级参考），默认不做硬过滤。

用法：
    ./.venv-linux/bin/python scripts/scan_sub_new_watchlist.py
    ./.venv-linux/bin/python scripts/scan_sub_new_watchlist.py --as-of 2026-09-25 --top 40
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.refresh_local_daily_basic_snapshot import (  # noqa: E402
    _to_tencent_symbol_from_code6,
    fetch_quotes,
)
from src.core.trading_calendar import get_effective_trading_date  # noqa: E402
from src.repositories.stock_repo import StockRepository  # noqa: E402
from src.services.kline_selector_service import KlineSelectorService  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("scan_sub_new_watchlist")

DEFAULT_STOCK_BASIC = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "sub_new_watch"
MIN_BARS_REQUIRED = 40


@dataclass
class WatchRecord:
    code: str
    name: str
    industry: str
    list_date: date
    days_listed: int
    close: float
    float_mv_yi: Optional[float]
    decline_pct: float
    amplitude_20: float
    vol_ratio: Optional[float]
    days_since_low: int
    above_ma10: bool
    amount_20_wan: Optional[float]
    calm_score: int
    unlock_flag: str = ""
    bars_source: str = ""


def _parse_list_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _load_stock_list(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            list_date = _parse_list_date(raw.get("list_date"))
            code = str(raw.get("symbol") or "").strip()
            if list_date is None or not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": str(raw.get("name") or "").strip(),
                    "industry": str(raw.get("industry") or "").strip(),
                    "list_date": list_date,
                }
            )
    return rows


def compute_metrics(bars: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """bars: 按时间升序的 [{date, high, low, close, volume}]，至少 15 根。"""
    if len(bars) < 15:
        raise ValueError("insufficient bars")
    latest = bars[-1]
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    vols = [float(b.get("volume") or 0.0) for b in bars]
    latest_close = closes[-1]
    high_since = max(highs)
    decline_pct = (latest_close / high_since - 1.0) * 100.0 if high_since > 0 else 0.0

    win20 = bars[-20:] if len(bars) >= 20 else bars
    amps = [
        (float(b["high"]) - float(b["low"])) / float(b["close"]) * 100.0
        for b in win20
        if b["close"]
    ]
    amplitude_20 = mean(amps) if amps else 0.0

    vol_window = [v for v in vols[-60:] if v > 0]  # 过滤缺数补零行（akshare 通道无 volume）
    vol5 = mean(vol_window[-5:]) if len(vol_window) >= 5 else 0.0
    vol_peak = max(vol_window) if vol_window else 0.0
    vol_ratio = (vol5 / vol_peak) if vol_peak > 0 and vol5 > 0 else None

    days_since_low = len(closes) - 1
    for i in range(len(closes) - 1, 9, -1):
        lo = max(0, i - 19)
        if closes[i] <= min(closes[lo : i + 1]):
            days_since_low = len(closes) - 1 - i
            break

    amount_series = [c * v for c, v in zip(closes[-20:], vols[-20:])]
    amount_20_wan = mean(amount_series) / 1e4 if amount_series else None
    ma10 = mean(closes[-10:]) if len(closes) >= 10 else mean(closes)
    above_ma10 = latest_close >= ma10

    calm_score = 0
    if amplitude_20 < 4.0:
        calm_score += 1
    if vol_ratio is not None and vol_ratio < 0.25:
        calm_score += 1
    if days_since_low >= 10:
        calm_score += 1

    return {
        "close": round(latest_close, 3),
        "decline_pct": round(decline_pct, 2),
        "amplitude_20": round(amplitude_20, 2),
        "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
        "days_since_low": days_since_low,
        "above_ma10": above_ma10,
        "amount_20_wan": round(amount_20_wan, 1) if amount_20_wan is not None else None,
        "calm_score": calm_score,
    }


def filter_and_rank(
    records: Sequence[WatchRecord],
    *,
    min_decline_pct: float = 45.0,
    max_float_mv_yi: float = 30.0,
    min_calm_score: int = 2,
    top: int = 60,
) -> List[WatchRecord]:
    selected = [r for r in records if r.decline_pct <= -abs(float(min_decline_pct))]
    selected = [
        r for r in selected if r.float_mv_yi is None or r.float_mv_yi <= float(max_float_mv_yi)
    ]
    selected = [r for r in selected if r.calm_score >= int(min_calm_score)]
    selected.sort(key=lambda r: (-r.calm_score, r.decline_pct, -(r.amount_20_wan or 0.0)))
    return selected[: int(top)] if top and int(top) > 0 else selected


def _unlock_flag(days_listed: int) -> str:
    months = days_listed / 30.44
    if 10.5 <= months <= 13.5:
        return "⚠️12月解禁窗"
    if 33.0 <= months <= 39.0:
        return "⚠️36月解禁窗"
    return ""


def _load_bars_from_manager(
    manager: Any,
    code: str,
    *,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    try:
        frame, _source = manager.get_daily_data(code, start_date=start_date, end_date=end_date)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_daily_data failed for %s: %s", code, exc)
        return []
    if frame is None or getattr(frame, "empty", True):
        return []
    out: List[Dict[str, Any]] = []
    for row in frame.to_dict("records"):
        try:
            close_value = float(row.get("close"))
            high_value = float(row.get("high"))
            low_value = float(row.get("low"))
        except (TypeError, ValueError):
            continue
        if not close_value or close_value <= 0 or high_value <= 0 or low_value <= 0:
            continue
        volume = row.get("volume")
        try:
            volume_value = float(volume) if volume is not None else 0.0
        except (TypeError, ValueError):
            volume_value = 0.0
        out.append(
            {
                "date": str(row.get("date"))[:10],
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume_value,
            }
        )
    return out


def _load_bars_from_repo(
    repo: StockRepository,
    code: str,
    *,
    start_date: date,
    end_date: date,
) -> List[Dict[str, Any]]:
    try:
        rows = repo.get_range(code, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_range failed for %s: %s", code, exc)
        return []
    out = []
    for bar in sorted(rows, key=lambda item: item.date):
        if not bar.close or bar.close <= 0:
            continue
        out.append(
            {
                "date": bar.date.isoformat() if hasattr(bar.date, "isoformat") else str(bar.date)[:10],
                "high": float(bar.high or bar.close),
                "low": float(bar.low or bar.close),
                "close": float(bar.close),
                "volume": float(bar.volume or 0.0),
            }
        )
    return out


def _default_as_of() -> date:
    try:
        value = get_effective_trading_date("cn")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
    except Exception:  # noqa: BLE001
        return date.today()


def _format_md(records: Sequence[WatchRecord], *, as_of: date, total_candidates: int) -> str:
    lines = [
        f"# 次新观察清单（{as_of.isoformat()}）",
        "",
        f"- 上市窗口候选：{total_candidates} 只；入选：{len(records)} 只。",
        "- 口径：流通市值 ≤ 阈值；距上市以来高点跌幅 ≥ 阈值；“跌不动”评分 ≥ 阈值（振幅/量比/未新低三项）。",
        "- 仅供参考：本表只做候选整理，买不买由题材与情绪决定；成交额为估算列。",
        "",
        "| 代码 | 名称 | 行业 | 上市日 | 上市天数 | 流通(亿) | 距高点% | 振幅20% | 量比 | 未新低(日) | 站上10日线 | 成交额(万) | 评分 | 备注 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for rec in records:
        star = "★" if (rec.float_mv_yi is not None and rec.float_mv_yi <= 20.0) else ""
        lines.append(
            "| {code} | {name} | {industry} | {ld} | {days} | {fmv}{star} | {decline} | {amp} | {vr} | {dsl} | {ma} | {amount} | {score} | {flag} |".format(
                code=rec.code,
                name=rec.name,
                industry=rec.industry or "--",
                ld=rec.list_date.isoformat(),
                days=rec.days_listed,
                fmv=f"{rec.float_mv_yi:.1f}" if rec.float_mv_yi is not None else "--",
                star=star,
                decline=f"{rec.decline_pct:.1f}",
                amp=f"{rec.amplitude_20:.1f}",
                vr=f"{rec.vol_ratio:.2f}" if rec.vol_ratio is not None else "--",
                dsl=rec.days_since_low,
                ma="Y" if rec.above_ma10 else "",
                amount=f"{rec.amount_20_wan:.0f}" if rec.amount_20_wan is not None else "--",
                score=rec.calm_score,
                flag=rec.unlock_flag,
            )
        )
    return "\n".join(lines) + "\n"


def _write_outputs(records: Sequence[WatchRecord], *, as_of: date, output_dir: Path, total_candidates: int) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"sub_new_watch_{as_of.strftime('%Y%m%d')}.md"
    csv_path = output_dir / f"sub_new_watch_{as_of.strftime('%Y%m%d')}.csv"
    md_path.write_text(_format_md(records, as_of=as_of, total_candidates=total_candidates), encoding="utf-8")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "code", "name", "industry", "list_date", "days_listed", "close",
                "float_mv_yi", "decline_pct", "amplitude_20", "vol_ratio",
                "days_since_low", "above_ma10", "amount_20_wan", "calm_score",
                "unlock_flag", "bars_source",
            ]
        )
        for rec in records:
            writer.writerow(
                [
                    rec.code, rec.name, rec.industry, rec.list_date.isoformat(), rec.days_listed,
                    rec.close, rec.float_mv_yi if rec.float_mv_yi is not None else "",
                    rec.decline_pct, rec.amplitude_20,
                    rec.vol_ratio if rec.vol_ratio is not None else "",
                    rec.days_since_low, int(rec.above_ma10),
                    rec.amount_20_wan if rec.amount_20_wan is not None else "",
                    rec.calm_score, rec.unlock_flag, rec.bars_source,
                ]
            )
    return md_path, csv_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan recently-listed A-share deep-pullback watchlist candidates.")
    parser.add_argument("--as-of", default=None, help="日期 YYYY-MM-DD；默认取最近有效交易日")
    parser.add_argument("--stock-basic", default=str(DEFAULT_STOCK_BASIC), help="stock_basic 快照路径")
    parser.add_argument("--min-age-days", type=int, default=120, help="上市天数下限，默认 120")
    parser.add_argument("--max-age-days", type=int, default=800, help="上市天数上限，默认 800")
    parser.add_argument("--max-float-mv-yi", type=float, default=30.0, help="流通市值上限（亿），默认 30")
    parser.add_argument("--min-decline-pct", type=float, default=45.0, help="距上市高点最小跌幅（%%），默认 45")
    parser.add_argument("--min-calm-score", type=int, default=2, help="跌不动评分下限（0~3），默认 2")
    parser.add_argument("--top", type=int, default=60, help="最多输出条数，默认 60；0=不限")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个候选（调试用），0=不限")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    as_of = date.fromisoformat(args.as_of) if args.as_of else _default_as_of()
    stocks = _load_stock_list(Path(args.stock_basic))
    candidates = [
        item
        for item in stocks
        if int(args.min_age_days) <= (as_of - item["list_date"]).days <= int(args.max_age_days)
        and not item["code"].startswith(("4", "8", "9"))  # 排除北交所（4/8/920xxx）与 B 股（900xxx）
    ]
    if args.limit and int(args.limit) > 0:
        candidates = candidates[: int(args.limit)]
    print(f"[sub-new] as_of={as_of} 上市窗口候选={len(candidates)} 只")

    float_mv_map: Dict[str, Optional[float]] = {}
    symbols = []
    symbol_to_code: Dict[str, str] = {}
    for item in candidates:
        symbol = _to_tencent_symbol_from_code6(item["code"])
        if symbol:
            symbols.append(symbol)
            symbol_to_code[symbol] = item["code"]
    if symbols:
        try:
            quotes = fetch_quotes(symbols)
            for symbol, payload in quotes.items():
                code = symbol_to_code.get(symbol) or symbol
                float_mv_map[code] = payload.get("float_mv_yi")
            print(f"[sub-new] 流通市值获取: {len(float_mv_map)}/{len(candidates)}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("腾讯行情获取失败，市值列将留空: %s", exc)

    manager = KlineSelectorService.build_fast_a_share_manager()
    repo = StockRepository(DatabaseManager.get_instance())
    records: List[WatchRecord] = []
    skipped_bars = 0
    for index, item in enumerate(candidates, start=1):
        code = item["code"]
        days_listed = (as_of - item["list_date"]).days
        start_date = max(item["list_date"], as_of - timedelta(days=550))
        bars = _load_bars_from_manager(
            manager, code, start_date=start_date.isoformat(), end_date=as_of.isoformat()
        )
        source = "manager"
        if not bars:
            bars = _load_bars_from_repo(repo, code, start_date=start_date, end_date=as_of)
            source = "stock_daily"
        if len(bars) < MIN_BARS_REQUIRED:
            skipped_bars += 1
            continue
        try:
            metrics = compute_metrics(bars)
        except Exception as exc:  # noqa: BLE001
            logger.debug("compute_metrics failed for %s: %s", code, exc)
            skipped_bars += 1
            continue
        records.append(
            WatchRecord(
                code=code,
                name=item["name"],
                industry=item["industry"],
                list_date=item["list_date"],
                days_listed=days_listed,
                close=metrics["close"],
                float_mv_yi=float_mv_map.get(code),
                decline_pct=metrics["decline_pct"],
                amplitude_20=metrics["amplitude_20"],
                vol_ratio=metrics["vol_ratio"],
                days_since_low=metrics["days_since_low"],
                above_ma10=metrics["above_ma10"],
                amount_20_wan=metrics["amount_20_wan"],
                calm_score=metrics["calm_score"],
                unlock_flag=_unlock_flag(days_listed),
                bars_source=source,
            )
        )
        if index % 50 == 0:
            print(f"[sub-new] 进度 {index}/{len(candidates)}（有效 {len(records)}）")

    selected = filter_and_rank(
        records,
        min_decline_pct=float(args.min_decline_pct),
        max_float_mv_yi=float(args.max_float_mv_yi),
        min_calm_score=int(args.min_calm_score),
        top=int(args.top),
    )
    md_path, csv_path = _write_outputs(
        selected, as_of=as_of, output_dir=Path(args.output_dir), total_candidates=len(candidates)
    )
    print(f"[sub-new] 有效样本 {len(records)} / 跳过数据不足 {skipped_bars} → 入选 {len(selected)}")
    print(f"[sub-new] md={md_path}")
    print(f"[sub-new] csv={csv_path}")
    for rec in selected[:15]:
        flag = f" {rec.unlock_flag}" if rec.unlock_flag else ""
        fmv = f"{rec.float_mv_yi:.1f}亿" if rec.float_mv_yi is not None else "--"
        print(
            f"  {rec.code} {rec.name} 上市{rec.days_listed}天 流通{fmv} "
            f"距高{rec.decline_pct:.1f}% 评分{rec.calm_score}{flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
