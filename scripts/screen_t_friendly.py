#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""温和阳线型扫描（"好看的图" / T 友好，离线只读 data/cache/history 与 stock_basic）。

口径（2026-10-13 与用户确认的方向，"看图"逻辑）：
- 窗口 42 个交易日（约 2 个月），基于日线 OHLC；
- 阳线占比 = close > open 的天数占比（默认 >= --min-bull 55%，即"阳线多"）；
- 波动温和：平均日振幅 ((高-低)/昨收) <= --max-amp（默认 5.5%），
  且窗口内最大单日涨跌 <= --max-mx（默认 9.5%，避开大涨大跌/打板票）；
- 回撤受控：窗口内最大回撤 <= --max-dd（默认 15%，不是"一跌 20-30%"的单边下跌票）；
- 有来回摆动（适合做 T）：收盘价 4.5% 级别 ZigZag 摆动次数 >= --min-swings（默认 4）；
- 趋势质量层（2026-10-13 晚根据“质量不行”反馈新增，可用 --no-trend-filter 关闭）：
  净涨 >= --min-net（默认 0%，剔除“华安证券式”慢跌震荡）、近 10 日 >= --min-net10（默认 -3%）、
  距 60 日高 >= --min-dist-high60（默认 -10%）、收盘站上 MA20 与 MA60；
- 最大单段回调（波段高→低）作为"一跌多深"的直观指标输出；
- 流动性：20 日均额 >= --min-amt（亿，默认 1）；剔除 ST/退市；接缝保护 |单日| > 25%。

用法：
    ./.venv-linux/bin/python scripts/screen_t_friendly.py
    ./.venv-linux/bin/python scripts/screen_t_friendly.py --min-bull 56 --no-charts
输出：
    data/strategy_review/t_friendly_<YYYYMMDD>.md / .csv
    --charts（默认开）额外输出 data/strategy_review/t_friendly_charts_<YYYYMMDD>/
      （每只一张蜡烛图 + INDEX.md + overview.png 总览图）
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
BASIC_PATH = PROJECT_ROOT / "data" / "cache" / "dividend_income" / "stock_basic.csv"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
PREFIXES = ("60", "00", "30")

_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)
_BLOCKS = "▁▂▃▄▅▆▇█"


def swing_stats(closes: Sequence[float], thr: float = 0.045) -> Tuple[int, float]:
    """收盘价 ZigZag 摆动：返回 (摆动次数, 最大单段回调%)。

    摆动次数 = 已完成波段数（方向切换次数 - 1）；最大单段回调 = 单个下跌波段
    从波段高点到低点的百分比深度（含末端未完成下跌段）。
    """
    if len(closes) < 2:
        return 0, 0.0
    piv: list = []  # [("H"/"L", price)]
    last = float(closes[0])
    direction = 0  # 0 未定, 1 上升, -1 下降
    for x in [float(v) for v in closes[1:]]:
        if direction == 0:
            if x >= last * (1 + thr):
                piv.append(("L", last))
                direction = 1
                last = x
            elif x <= last * (1 - thr):
                piv.append(("H", last))
                direction = -1
                last = x
        elif direction == 1:
            if x > last:
                last = x
            elif x <= last * (1 - thr):
                piv.append(("H", last))
                direction = -1
                last = x
        else:
            if x < last:
                last = x
            elif x >= last * (1 + thr):
                piv.append(("L", last))
                direction = 1
                last = x
    swings = max(0, len(piv) - 1)
    worst = 0.0
    for i in range(len(piv) - 1):
        if piv[i][0] == "H" and piv[i + 1][0] == "L":
            worst = max(worst, (1 - piv[i + 1][1] / piv[i][1]) * 100)
    if piv and piv[-1][0] == "H" and direction == -1:  # 末端未完成下跌段
        worst = max(worst, (1 - last / piv[-1][1]) * 100)
    return swings, worst


def window_metrics(w: pd.DataFrame) -> dict:
    """42 交易日窗口指标；w 需含 open/high/low/close 数值列（首行仅作昨收基准）。"""
    o = w["open"].to_numpy(dtype=float)
    h = w["high"].to_numpy(dtype=float)
    low = w["low"].to_numpy(dtype=float)
    c = w["close"].to_numpy(dtype=float)
    prev, cur = c[:-1], c[1:]
    ret = cur / prev - 1
    bull_ratio = float((cur > o[1:]).mean() * 100)
    up_ratio = float((ret > 0).mean() * 100)
    amp = float(((h[1:] - low[1:]) / prev * 100).mean())
    mx = float((np.abs(ret) * 100).max())
    dd = float(((c / np.maximum.accumulate(c) - 1) * 100).min())
    net = float((c[-1] / c[0] - 1) * 100)
    sw, worst = swing_stats(c.tolist())
    return {
        "bull_ratio": bull_ratio,
        "up_ratio": up_ratio,
        "swings": sw,
        "worst_leg_pct": worst,
        "avg_amp_pct": amp,
        "max_move_pct": mx,
        "max_dd_pct": dd,
        "net_pct": net,
    }


def passes_filters(
    m: dict,
    *,
    min_bull: float,
    max_amp: float,
    max_mx: float,
    max_dd: float,
    min_net: float,
    max_net: float,
    min_swings: int,
    min_net10: float,
    min_dist_high60: float,
    require_trend: bool,
) -> bool:
    return (
        m["bull_ratio"] >= min_bull
        and m["avg_amp_pct"] <= max_amp
        and m["max_move_pct"] <= max_mx
        and m["max_dd_pct"] >= -max_dd
        and min_net <= m["net_pct"] <= max_net
        and m["swings"] >= min_swings
        and m["net10_pct"] >= min_net10
        and m["dist_high60_pct"] >= min_dist_high60
        and (not require_trend or (m["above_ma20"] and m["above_ma60"]))
    )


def sparkline(closes: Sequence[float]) -> str:
    lo, hi = min(closes), max(closes)
    if hi <= lo:
        return _BLOCKS[0] * len(closes)
    return "".join(_BLOCKS[min(7, int((float(c) - lo) / (hi - lo) * 8))] for c in closes)


def _load_basic() -> Tuple[dict, dict]:
    if not BASIC_PATH.exists():
        return {}, {}
    b = pd.read_csv(BASIC_PATH, dtype={"symbol": str})
    names = dict(zip(b["symbol"], b["name"].astype(str)))
    inds = dict(zip(b["symbol"], b["industry"].astype(str))) if "industry" in b.columns else {}
    return names, inds


def make_charts(df: pd.DataFrame, tag: str) -> Optional[Path]:
    """为入选名单输出蜡烛图（含 MA5/10/20）+ 总览图 + INDEX.md。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager as fm
    except Exception as e:  # pragma: no cover - 环境无 matplotlib 时降级
        print(f"[warn] matplotlib 不可用，跳过画图：{e}")
        return None

    for fp in _FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
            except Exception:
                pass
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "WenQuanYi Zen Hei",
        "AR PL UMing CN",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    out_dir = OUT_DIR / f"t_friendly_charts_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_lines = [f"# T 友好图（温和阳线型）· {tag}", "", f"共 {len(df)} 只。每只图为最近 45 根日 K + MA5/10/20。", ""]

    for _, r in df.iterrows():
        code, name = r["code"], r["name"]
        p = CACHE_DIR / f"{code}.csv"
        d = pd.read_csv(p, usecols=["date", "open", "high", "low", "close"])
        for col in ("open", "high", "low", "close"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
        for n in (5, 10, 20):
            d[f"ma{n}"] = d["close"].rolling(n).mean()
        w = d.tail(45).reset_index(drop=True)
        x = np.arange(len(w))

        fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
        for i in range(len(w)):
            o, h, low, c = (float(v) for v in (w["open"][i], w["high"][i], w["low"][i], w["close"][i]))
            color = "#d62728" if c >= o else "#2ca02c"
            ax.vlines(i, low, h, color=color, linewidth=0.8)
            body = abs(c - o)
            if body <= 0:
                body = max(1e-4, c * 0.0005)
            ax.bar(i, body, bottom=min(o, c), width=0.62, color=color, edgecolor=color, linewidth=0.3)
        for n, clr in ((5, "#1f77b4"), (10, "#ff7f0e"), (20, "#9467bd")):
            ax.plot(x, w[f"ma{n}"].values, lw=1.0, color=clr, label=f"MA{n}")
        ax.legend(loc="upper left", fontsize=7)
        ax.set_title(
            f"{code} {name} · 阳线 {r['bull_ratio']:.0f}% · 摆动 {int(r['swings'])} 次 · "
            f"最大回撤 {r['max_dd_pct']:.1f}% · 单段最深 -{r['worst_leg_pct']:.1f}%"
        )
        step = max(1, len(w) // 6)
        ax.set_xticks(list(range(0, len(w), step)))
        ax.set_xticklabels([str(w["date"][i])[5:] for i in range(0, len(w), step)], fontsize=7)
        ax.grid(alpha=0.25, linewidth=0.5)
        fig.tight_layout()
        fn = f"{code}_{name}.png"
        fig.savefig(out_dir / fn, bbox_inches="tight")
        plt.close(fig)
        idx_lines.append(f"## {code} {name}")
        idx_lines.append(
            f"- 阳线 {r['bull_ratio']:.1f}% · 摆动 {int(r['swings'])} 次 · 最大回撤 {r['max_dd_pct']:.1f}% · "
            f"单段最深 -{r['worst_leg_pct']:.1f}% · 振幅 {r['avg_amp_pct']:.1f}% · 42日 {r['net_pct']:+.1f}% · 均额 {r['amt20_yi']} 亿"
        )
        idx_lines.append("")
        idx_lines.append(f"![{code}]({fn})")
        idx_lines.append("")

    # 总览图
    n = len(df)
    cols = 4
    rows = max(1, (n + cols - 1) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(17, 2.4 * rows + 0.8), dpi=110)
    axes = np.atleast_1d(axes).ravel()
    for i, (_, r) in enumerate(df.iterrows()):
        ax = axes[i]
        d = pd.read_csv(CACHE_DIR / f"{r['code']}.csv", usecols=["close"])
        c = pd.to_numeric(d["close"], errors="coerce").dropna().tail(45).to_numpy()
        color = "#c00000" if c[-1] >= c[0] else "#006400"
        ax.plot(np.arange(len(c)), c, lw=1.2, color=color)
        ax.scatter([len(c) - 1], [c[-1]], s=12, color=color, zorder=3)
        ax.set_title(f"{r['code']} {r['name']}", fontsize=8)
        ax.set_xticks([])
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.2, linewidth=0.4)
    for j in range(n, len(axes)):
        axes[j].axis("off")
    fig.suptitle(f"T 友好图总览 · {tag} · {n} 只（红=2月上涨，绿=2月下跌）", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_dir / "overview.png", bbox_inches="tight")
    plt.close(fig)

    idx_lines.insert(2, "![总览](overview.png)")
    idx_lines.insert(3, "")
    (out_dir / "INDEX.md").write_text("\n".join(idx_lines), encoding="utf-8")
    return out_dir


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="温和阳线型（T 友好图）扫描（离线）")
    parser.add_argument("--days", type=int, default=42, help="窗口交易日数，默认 42（约 2 个月）")
    parser.add_argument("--min-bull", type=float, default=55.0, help="阳线占比下限（%%），默认 55")
    parser.add_argument("--max-amp", type=float, default=5.5, help="平均日振幅上限（%%），默认 5.5")
    parser.add_argument("--max-mx", type=float, default=9.5, help="最大单日涨跌上限（%%），默认 9.5")
    parser.add_argument("--max-dd", type=float, default=15.0, help="窗口最大回撤上限（%%），默认 15")
    parser.add_argument("--min-net", type=float, default=0.0, help="窗口净涨幅下限（%%），默认 0（质量层）")
    parser.add_argument("--max-net", type=float, default=60.0, help="窗口净涨幅上限（%%），默认 60")
    parser.add_argument("--min-swings", type=int, default=4, help="4.5%% 级别摆动次数下限，默认 4")
    parser.add_argument("--min-net10", type=float, default=-3.0, help="近 10 日净涨幅下限（%%），默认 -3")
    parser.add_argument("--min-dist-high60", type=float, default=-10.0, help="距 60 日高点下限（%%），默认 -10")
    parser.add_argument("--no-trend-filter", action="store_true", help="关闭 MA20/MA60 趋势质量过滤")
    parser.add_argument("--min-amt", type=float, default=1.0, help="20 日均额下限（亿），默认 1")
    parser.add_argument("--date", default=date.today().isoformat(), help="输出文件名日期")
    parser.add_argument("--no-charts", action="store_true", help="跳过画图（默认生成图表）")
    args = parser.parse_args(argv)

    nm, ind = _load_basic()
    amt_min = args.min_amt * 1e8
    need = args.days + 1
    rows = []
    for p in sorted(glob.glob(str(CACHE_DIR / "*.csv"))):
        code = os.path.basename(p)[:6]
        if not code.startswith(PREFIXES):
            continue
        name = str(nm.get(code, ""))
        if not name or "ST" in name.upper() or "退" in name:
            continue
        try:
            d = pd.read_csv(p, usecols=["date", "open", "high", "low", "close", "amount"])
        except Exception:
            continue
        for col in ("open", "high", "low", "close", "amount"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
        if len(d) < 70:
            continue
        w = d.tail(need).reset_index(drop=True)
        if (w["close"].pct_change().abs() * 100 > 25).any():
            continue
        amt20 = float(d["amount"].tail(20).mean()) if d["amount"].notna().any() else 0.0
        if amt20 < amt_min or amt20 != amt20:
            continue
        m = window_metrics(w)
        cw = w["close"]
        c60 = d["close"].tail(60)
        close = float(cw.iloc[-1])
        m.update(
            {
                "net10_pct": float((close / cw.iloc[-11] - 1) * 100),
                "dist_high60_pct": float((close / c60.max() - 1) * 100),
                "above_ma20": bool(close >= cw.tail(20).mean()),
                "above_ma60": bool(close >= c60.mean()),
            }
        )
        if not passes_filters(
            m,
            min_bull=args.min_bull,
            max_amp=args.max_amp,
            max_mx=args.max_mx,
            max_dd=args.max_dd,
            min_net=args.min_net,
            max_net=args.max_net,
            min_swings=args.min_swings,
            min_net10=args.min_net10,
            min_dist_high60=args.min_dist_high60,
            require_trend=not args.no_trend_filter,
        ):
            continue
        m.update(
            {
                "code": code,
                "name": name,
                "industry": ind.get(code, ""),
                "close": round(close, 2),
                "net10_pct": round(m["net10_pct"], 1),
                "dist_high60_pct": round(m["dist_high60_pct"], 1),
                "amt20_yi": round(amt20 / 1e8, 2),
                "spark": sparkline(w["close"].tolist()),
            }
        )
        rows.append(m)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["bull_ratio", "swings"], ascending=[False, False]).reset_index(drop=True)

    tag = args.date.replace("-", "")
    out_md = OUT_DIR / f"t_friendly_{tag}.md"
    out_csv = OUT_DIR / f"t_friendly_{tag}.csv"
    if not df.empty:
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    lines = [f"# 温和阳线型扫描（{args.date}，近 {args.days} 个交易日）", ""]
    lines.append(
        "- 规则：阳线占比 ≥%.0f%%；均振幅 ≤%.1f%% 且最大单日 ≤%.1f%%；"
        "最大回撤 ≤%.0f%%（非单边下跌）；4.5%% 摆动 ≥%d 次；净涨 %.0f%%~%.0f%%；20 日均额 ≥%.1f 亿。"
        % (args.min_bull, args.max_amp, args.max_mx, args.max_dd, args.min_swings, args.min_net, args.max_net, args.min_amt)
    )
    if not args.no_trend_filter:
        lines.append(
            "- 趋势质量层：净涨 ≥%.1f%%、近 10 日 ≥%.1f%%、距 60 日高 ≥%.1f%%、收盘站上 MA20 与 MA60。"
            % (args.min_net, args.min_net10, args.min_dist_high60)
        )
    if df.empty:
        lines.append("- 结果：0 只。")
    else:
        lines.append(f"- 结果：**{len(df)} 只**（按阳线占比排序）。")
        lines.append("")
        lines.append("| # | 代码 | 名称 | 行业 | 收盘 | 阳线% | 上涨日% | 摆动 | 单段最深% | 振幅% | 回撤% | 42日% | 近10日% | 距60高% | MA20/60 | 均额(亿) | 走势 |")
        lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for i, r in df.iterrows():
            ma_mark = ("✓" if r["above_ma20"] else "✗") + ("✓" if r["above_ma60"] else "✗")
            lines.append(
                f"| {i+1} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['bull_ratio']:.1f} | {r['up_ratio']:.1f} | "
                f"{int(r['swings'])} | -{r['worst_leg_pct']:.1f} | {r['avg_amp_pct']:.1f} | {r['max_dd_pct']:.1f} | {r['net_pct']:+.1f} | "
                f"{r['net10_pct']:+.1f} | {r['dist_high60_pct']:+.1f} | {ma_mark} | {r['amt20_yi']} | `{r['spark']}` |"
            )
    lines.append("")
    lines.append("*离线结构筛选（样本内描述性口径），不构成投资建议。*")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"候选 {len(df)} 只 -> {out_md}")
    if not df.empty:
        print(
            df.head(30)[
                ["code", "name", "bull_ratio", "swings", "worst_leg_pct", "avg_amp_pct", "max_dd_pct", "net_pct", "amt20_yi"]
            ].to_string(index=False)
        )
        if not args.no_charts:
            chart_dir = make_charts(df, tag)
            if chart_dir:
                print(f"图表目录 -> {chart_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
