#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MA20 择时开关实验：按基准指数 MA20 状态缩放组合层日收益。

背景：``portfolio_*_filled*.json``（组合层净值报告，窗口 2026-04-20 ~ 2026-09-24）
显示策略线在 7-8 月回撤巨大。本实验回答：如果叠加一个最朴素的指数择时开关
（前一日收盘跌破 MA20 → 次日空仓/半仓），净值曲线会变成什么样。

规则（无未来函数；含「缓冲带」与「切换成本」两个工程化要素）：
- 用报告内 ``benchmark.series`` 重建指数收盘点位（与报告同口径）；
- 策略第 i 日收益的敞口，取决于截至第 i-1 日收盘的开关状态；
- 状态机（hysteresis）：ON→OFF 需收盘跌破 MA×(1-band)；OFF→ON 需收盘收回 MA×(1+band)；
  band=0 时退化为单阈值（收盘 < MA 即关）；
- 每次状态切换按 ``|Δ敞口|×switch_cost_bps`` 扣一次摩擦成本（默认 15.5bps ≈ 单边），
  直接从切换日收益中扣减；
- 均线需要 MA 窗口个历史点，窗口前 ``ma_window - 1`` 个交易日视为满敞口（warm-up）。

口径说明（第二版）：
- 每个敞口变体拆两档：①「无缓冲·无成本」= 第一版口径（对照）；②「缓冲·成本」= 工程化口径；
- 成本为近似：按敞口变化比例线性计费，未计冲击成本与滑点价差；
- OFF 日的策略交易被整体跳过（不是重新选股）；输出保留每日开关状态与扣费，便于复核。

用法：
    ./.venv-linux/bin/python scripts/experiment_ma20_switch.py \
        data/strategy_review/portfolio_hdh_filled_all.json \
        data/strategy_review/portfolio_hdh_filled_top5.json \
        data/strategy_review/portfolio_trend_filled.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_signal_portfolio import _monthly_compounded  # noqa: E402
from scripts.evaluate_signal_snapshot_performance import (  # noqa: E402
    _compute_equity_metrics,
)

DEFAULT_MA_WINDOW = 20
DEFAULT_EXPOSURES = (0.0, 0.5)
DEFAULT_BAND_PCT = 0.5
DEFAULT_SWITCH_COST_BPS = 15.5
REVIEW_DIR = PROJECT_ROOT / "data" / "strategy_review"


def _reconstruct_closes(series: Sequence[Dict[str, Any]]) -> List[float]:
    """把日收益序列重建成收盘点位；``closes[k]`` = 第 k-1 日收盘（closes[0]=1.0 为起始）。"""
    closes = [1.0]
    for item in series:
        closes.append(closes[-1] * (1.0 + float(item["ret_pct"]) / 100.0))
    return closes


def build_switch_states(
    series: Sequence[Dict[str, Any]],
    closes: Sequence[float],
    *,
    ma_window: int = DEFAULT_MA_WINDOW,
    band_pct: float = 0.0,
) -> List[Dict[str, Any]]:
    """生成每日开关状态（带缓冲带的状态机）；第 i 日状态只用截至第 i-1 日收盘的数据。

    - band_pct=0：退化为单阈值（收盘 < MA 即关、收盘 > MA 即开）；
    - band_pct>0：ON→OFF 需跌破 MA×(1-band)，OFF→ON 需收回 MA×(1+band)，抑制抖动。
    """
    if int(ma_window) < 2:
        raise ValueError("ma_window 至少为 2")
    band = max(0.0, float(band_pct)) / 100.0
    current_on = True
    states: List[Dict[str, Any]] = []
    for i, item in enumerate(series):
        if i < int(ma_window) - 1:
            states.append({"date": str(item["date"]), "off": False, "warmup": True})
            continue
        ref_close = float(closes[i])  # 第 i-1 日收盘
        window = closes[i - (int(ma_window) - 1) : i + 1]
        ref_ma = sum(window) / float(ma_window)
        lower = ref_ma * (1.0 - band)
        upper = ref_ma * (1.0 + band)
        if current_on:
            if ref_close < lower:
                current_on = False
        elif ref_close > upper:
            current_on = True
        states.append(
            {
                "date": str(item["date"]),
                "off": not current_on,
                "warmup": False,
                "ref_close": round(ref_close, 6),
                "ref_ma": round(ref_ma, 6),
                "band_lower": round(lower, 6),
                "band_upper": round(upper, 6),
            }
        )
    return states


def _scale_series(
    series: Sequence[Dict[str, Any]],
    states: Sequence[Dict[str, Any]],
    *,
    exposure_off: float,
    switch_cost_bps: float = 0.0,
) -> List[Dict[str, Any]]:
    """按敞口缩放日收益；状态切换日按 ``|Δ敞口|×成本bps`` 扣一次切换成本。"""
    scaled: List[Dict[str, Any]] = []
    prev_exposure = 1.0
    for item, state in zip(series, states):
        factor = float(exposure_off) if state["off"] else 1.0
        cost_pct = abs(factor - prev_exposure) * float(switch_cost_bps) / 100.0
        scaled.append(
            {
                "date": str(item["date"]),
                "ret_pct": round(float(item["ret_pct"]) * factor - cost_pct, 4),
                "positions": item.get("positions"),
            }
        )
        prev_exposure = factor
    return scaled


def _count_flips(states: Sequence[Dict[str, Any]]) -> int:
    """统计评估期内的状态翻转次数（warm-up 视为 ON）。"""
    flips = 0
    prev_off = False
    for state in states:
        if state.get("warmup"):
            prev_off = False
            continue
        cur_off = bool(state["off"])
        if cur_off != prev_off:
            flips += 1
        prev_off = cur_off
    return flips


def _switch_cost_sum(
    series: Sequence[Dict[str, Any]],
    states: Sequence[Dict[str, Any]],
    exposure_off: float,
    switch_cost_bps: float,
) -> float:
    """累计切换成本（百分比，未复利）。"""
    total = 0.0
    prev_exposure = 1.0
    for _item, state in zip(series, states):
        factor = float(exposure_off) if state["off"] else 1.0
        total += abs(factor - prev_exposure) * float(switch_cost_bps) / 100.0
        prev_exposure = factor
    return total


def _variant_metrics(scaled: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not scaled:
        return {
            "total_return_after_cost_pct": None,
            "max_drawdown_after_cost_pct": None,
            "calmar_ratio_after_cost": None,
            "trading_days": 0,
            "daily_win_rate_pct": None,
        }
    rets = [float(item["ret_pct"]) for item in scaled]
    metrics: Dict[str, Any] = dict(_compute_equity_metrics(rets))
    metrics["trading_days"] = len(rets)
    metrics["daily_win_rate_pct"] = round(
        sum(1 for value in rets if value > 0) / len(rets) * 100.0, 2
    )
    return metrics


def _off_stats(
    series: Sequence[Dict[str, Any]], states: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    avoided = 0.0
    missed = 0.0
    by_month: Dict[str, int] = {}
    off_days = 0
    for item, state in zip(series, states):
        if not state["off"]:
            continue
        off_days += 1
        value = float(item["ret_pct"])
        if value < 0:
            avoided += value
        elif value > 0:
            missed += value
        month = str(item["date"])[:7]
        by_month[month] = by_month.get(month, 0) + 1
    return {
        "off_days": off_days,
        "avoided_loss_pct_sum": round(avoided, 4),
        "missed_gain_pct_sum": round(missed, 4),
        "off_days_by_month": by_month,
    }


def _variant_label(exposure: float, *, band_pct: float, switch_cost_bps: float) -> str:
    if float(exposure) == 0.0:
        base = "空仓开关"
    elif float(exposure) == 0.5:
        base = "半仓开关"
    else:
        base = f"开关（OFF×{float(exposure):g}）"
    if float(band_pct) <= 0 and float(switch_cost_bps) <= 0:
        return f"{base}（无缓冲·无成本）"
    return f"{base}（缓冲{float(band_pct):g}%·成本{float(switch_cost_bps):g}bps）"


def analyze_input(
    input_path: Path,
    *,
    ma_window: int,
    exposures: Sequence[float],
    band_pct: float = DEFAULT_BAND_PCT,
    switch_cost_bps: float = DEFAULT_SWITCH_COST_BPS,
) -> Dict[str, Any]:
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    line = payload.get("line") or {}
    series = line.get("series") or []
    bench = payload.get("benchmark") or {}
    bench_series = bench.get("series") or []
    if not series or not bench_series:
        raise SystemExit(f"{input_path}: 缺少 line.series / benchmark.series")
    if len(series) != len(bench_series):
        raise SystemExit(f"{input_path}: line/benchmark 序列长度不一致")
    dates = [str(item["date"]) for item in series]
    if dates != [str(item["date"]) for item in bench_series]:
        raise SystemExit(f"{input_path}: line/benchmark 日期不一致")

    filters = payload.get("filters") or {}
    name = str(filters.get("signal_type") or Path(input_path).stem)
    start = str(filters.get("start_date") or dates[0])
    end = str(filters.get("end_date") or dates[-1])

    closes = _reconstruct_closes(bench_series)
    states_legacy = build_switch_states(series, closes, ma_window=ma_window, band_pct=0.0)
    states_main = build_switch_states(
        series, closes, ma_window=ma_window, band_pct=float(band_pct)
    )
    off_stats = _off_stats(series, states_main)
    off_stats["flips"] = _count_flips(states_main)
    off_stats["flips_legacy"] = _count_flips(states_legacy)

    variants: Dict[str, Any] = {
        "base": {
            "label": "原线（无开关）",
            "metrics": line.get("metrics"),
            "monthly": line.get("monthly"),
        }
    }
    for exposure in exposures:
        scaled_legacy = _scale_series(
            series, states_legacy, exposure_off=float(exposure), switch_cost_bps=0.0
        )
        variants[f"off_{float(exposure):g}_raw"] = {
            "label": _variant_label(float(exposure), band_pct=0.0, switch_cost_bps=0.0),
            "exposure_off": float(exposure),
            "band_pct": 0.0,
            "switch_cost_bps": 0.0,
            "flips": _count_flips(states_legacy),
            "switch_cost_sum_pct": 0.0,
            "metrics": _variant_metrics(scaled_legacy),
            "monthly": _monthly_compounded(scaled_legacy),
            "series": scaled_legacy,
        }
        if float(band_pct) > 0 or float(switch_cost_bps) > 0:
            scaled_main = _scale_series(
                series,
                states_main,
                exposure_off=float(exposure),
                switch_cost_bps=float(switch_cost_bps),
            )
            variants[f"off_{float(exposure):g}_eng"] = {
                "label": _variant_label(
                    float(exposure),
                    band_pct=float(band_pct),
                    switch_cost_bps=float(switch_cost_bps),
                ),
                "exposure_off": float(exposure),
                "band_pct": float(band_pct),
                "switch_cost_bps": float(switch_cost_bps),
                "flips": _count_flips(states_main),
                "switch_cost_sum_pct": round(
                    _switch_cost_sum(
                        series, states_main, float(exposure), float(switch_cost_bps)
                    ),
                    4,
                ),
                "metrics": _variant_metrics(scaled_main),
                "monthly": _monthly_compounded(scaled_main),
                "series": scaled_main,
            }

    return {
        "source": str(input_path),
        "line": name,
        "start": start,
        "end": end,
        "band_pct": float(band_pct),
        "switch_cost_bps": float(switch_cost_bps),
        "warmup_days": sum(1 for state in states_main if state["warmup"]),
        "off_stats": off_stats,
        "states": states_main,
        "states_legacy": states_legacy,
        "variants": variants,
        "benchmark": {
            "code": bench.get("code"),
            "metrics": bench.get("metrics"),
            "monthly": bench.get("monthly"),
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _monthly_map(monthly: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in monthly or []:
        result[str(item.get("month"))] = item.get("ret_pct")
    return result


def build_markdown(
    results: Sequence[Dict[str, Any]],
    *,
    ma_window: int,
    band_pct: float,
    switch_cost_bps: float,
    generated_at: str,
) -> str:
    out: List[str] = []
    out.append(
        f"# MA20 择时开关实验（基准前一日收盘 vs MA{int(ma_window)} → 次日敞口缩放）"
    )
    out.append("")
    out.append(f"- 生成时间: `{generated_at}`；MA 窗口: `{int(ma_window)}`；"
               f"缓冲带: `{float(band_pct):g}%`；切换成本: `{float(switch_cost_bps):g}bps/边`")
    out.append(
        "- 规则（无未来函数）：缓冲带状态机 —— ON→OFF 需跌破 MA×(1-band)、OFF→ON 需收回 "
        "MA×(1+band)；切换日按 |Δ敞口|×成本bps 扣费；每个敞口含「无缓冲·无成本」对照。"
    )
    out.append(
        "- 口径限制（第二版）：成本按敞口变化线性近似（未计冲击成本）；OFF 日跳过当日选股；"
        "仅供判断开关是否值得进一步工程化。"
    )
    out.append(
        "- 读表提醒：空仓变体把 OFF 日收益清零，会机械性拉低其“日胜率”（零点不计胜），"
        "该列仅供参考；重点看总收益 / 最大回撤 / Calmar。"
    )
    out.append("")
    for result in results:
        stats = result["off_stats"]
        out.append(f"## {result['line']}（`{Path(result['source']).name}`）")
        out.append("")
        out.append(
            f"- 窗口: `{result['start']} ~ {result['end']}`；"
            f"OFF 天数: {stats['off_days']}；翻转: {stats['flips']}"
            f"（无缓冲对照 {stats['flips_legacy']}）；warm-up 天数: {result['warmup_days']}"
        )
        out.append(
            f"- OFF 日原始收益合计: 躲掉下跌 {stats['avoided_loss_pct_sum']:.2f}% / "
            f"错过上涨 +{stats['missed_gain_pct_sum']:.2f}%"
        )
        by_month = "、".join(
            f"{month}: {count}"
            for month, count in sorted(stats["off_days_by_month"].items())
        )
        out.append(f"- OFF 天数按月: {by_month or '（无）'}")
        out.append("")
        out.append("| 口径 | 总收益% | 最大回撤% | Calmar | 日胜率% | 交易日 | 翻转 | 切换成本% |")
        out.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        rows: List[Tuple[str, Dict[str, Any], Any, Any]] = []
        for key in ["base", *[k for k in result["variants"] if k != "base"]]:
            block = result["variants"][key]
            rows.append(
                (
                    str(block["label"]),
                    block.get("metrics") or {},
                    block.get("flips"),
                    block.get("switch_cost_sum_pct"),
                )
            )
        bench = result["benchmark"]
        rows.append(
            (f"基准买持（{bench.get('code') or '-'}）", bench.get("metrics") or {}, None, None)
        )
        for label, metrics, flips, cost_sum in rows:
            out.append(
                "| "
                + label
                + " | "
                + " | ".join(
                    [
                        _fmt(metrics.get("total_return_after_cost_pct")),
                        _fmt(metrics.get("max_drawdown_after_cost_pct")),
                        _fmt(metrics.get("calmar_ratio_after_cost")),
                        _fmt(metrics.get("daily_win_rate_pct")),
                        _fmt(metrics.get("trading_days")),
                        _fmt(flips),
                        _fmt(cost_sum),
                    ]
                )
                + " |"
            )
        out.append("")

        monthly_maps = [
            (str(block["label"]), _monthly_map(block.get("monthly")))
            for block in result["variants"].values()
        ]
        bench_monthly = _monthly_map(result["benchmark"].get("monthly"))
        months = sorted(
            {month for _, mapping in monthly_maps for month in mapping}
            | set(bench_monthly)
        )
        out.append("分月收益（复利）：")
        out.append("")
        out.append(
            "| 月份 | "
            + " | ".join([label + "%" for label, _ in monthly_maps] + ["基准%"])
            + " |"
        )
        out.append(
            "| --- | " + " | ".join(["---:"] * (len(monthly_maps) + 1)) + " |"
        )
        for month in months:
            cells = [
                _fmt(mapping.get(month)) for _, mapping in monthly_maps
            ] + [_fmt(bench_monthly.get(month))]
            out.append("| " + month + " | " + " | ".join(cells) + " |")
        out.append("")
    out.append(
        "---\n*说明：基准序列取自组合层报告（buy&hold 同窗口）；开关状态逐日记录在 JSON 内，"
        "可对照原始报告核查。*"
    )
    out.append("")
    return "\n".join(out)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MA20 择时开关实验：按基准指数 MA20 状态缩放组合层日收益。"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="组合层 filled JSON（如 data/strategy_review/portfolio_hdh_filled_all.json）",
    )
    parser.add_argument(
        "--ma-window", type=int, default=DEFAULT_MA_WINDOW, help="均线窗口（默认 20）"
    )
    parser.add_argument(
        "--band-pct",
        type=float,
        default=DEFAULT_BAND_PCT,
        help=f"缓冲带百分比（默认 {DEFAULT_BAND_PCT:g}；0=单阈值）",
    )
    parser.add_argument(
        "--switch-cost-bps",
        type=float,
        default=DEFAULT_SWITCH_COST_BPS,
        help=f"切换成本 bps/边（默认 {DEFAULT_SWITCH_COST_BPS:g}；0=不计成本）",
    )
    parser.add_argument(
        "--exposures",
        default=",".join(f"{value:g}" for value in DEFAULT_EXPOSURES),
        help="OFF 日敞口变体列表，逗号分隔（默认 0,0.5）",
    )
    parser.add_argument(
        "--output-dir", default=str(REVIEW_DIR), help="输出目录（默认 data/strategy_review）"
    )
    parser.add_argument("--out-name", default=None, help="输出文件名主干（默认按窗口自动）")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    exposures: List[float] = []
    for chunk in str(args.exposures).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = float(chunk)
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"敞口必须在 [0,1] 内：{chunk}")
        exposures.append(value)
    if not exposures:
        raise SystemExit("--exposures 不能为空")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = [
        analyze_input(
            Path(path),
            ma_window=int(args.ma_window),
            exposures=exposures,
            band_pct=float(args.band_pct),
            switch_cost_bps=float(args.switch_cost_bps),
        )
        for path in args.inputs
    ]

    first = results[0]
    window_key = f"{first['start']}_{first['end']}"
    stem = str(args.out_name) if args.out_name else f"ma20_switch_{window_key}"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"

    md_path.write_text(
        build_markdown(
            results,
            ma_window=int(args.ma_window),
            band_pct=float(args.band_pct),
            switch_cost_bps=float(args.switch_cost_bps),
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "ma_window": int(args.ma_window),
                "band_pct": float(args.band_pct),
                "switch_cost_bps": float(args.switch_cost_bps),
                "exposures": exposures,
                "rule": "缓冲带状态机（ON→OFF 需 < MA×(1-band)、OFF→ON 需 > MA×(1+band)）"
                " + |Δ敞口|×成本 扣费；无未来函数",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    for result in results:
        base = result["variants"]["base"].get("metrics") or {}
        pieces = [
            f"{result['line']}: 原线 {_fmt(base.get('total_return_after_cost_pct'))}%"
        ]
        for key, block in result["variants"].items():
            if key == "base":
                continue
            metrics = block.get("metrics") or {}
            pieces.append(
                f"{block['label'].split('（')[0]} {_fmt(metrics.get('total_return_after_cost_pct'))}%"
            )
        bench = result["benchmark"].get("metrics") or {}
        pieces.append(f"基准 {_fmt(bench.get('total_return_after_cost_pct'))}%")
        print("  " + " | ".join(pieces))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
