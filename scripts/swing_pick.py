#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波段选股（熊市口径）：底部抬升 + 相对强度 + 量能，输出可解释名单。

设计原则（2026-11-10）：
1. **单一口径**，不叠多种互相矛盾的策略；先做结构筛选，再补基本面/机构。
2. **熊市不追新高**：要“距高点够远 + 已离开底部 + MA20 在 MA60 上方并上行”，
   而不是百日新高/趋势领涨那类牛市基因。
3. **硬排除指数调节盘**：银行/保险/证券/多元金融/建筑工程等（国家队持仓为主，
   对短线没有交易价值），以及 ST、次新、超大盘、低流动性。
4. **每只必须给理由**：结构、相对强度、量能、行业强度、止损位、建议持有期，
   全部由本地数据算出，不写“看多”“值得关注”这类空话。
5. 基本面（营收/扣非/ROE）、机构持仓（十大流通股东/基金）、ETF 资金流当前
   本地数据不足，明确标 `pending`，不假装已纳入。

用法：
    ./.venv-linux/bin/python scripts/swing_pick.py                    # 最新交易日
    ./.venv-linux/bin/python scripts/swing_pick.py --as-of 2026-11-09
    ./.venv-linux/bin/python scripts/swing_pick.py --explain-limit 10
输出：
    data/strategy_review/swing_pick_latest.json
    data/strategy_review/swing_pick_latest.md
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DatabaseManager  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "swing_pick.json"
INDUSTRY_CSV = PROJECT_ROOT / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"
LOOKBACK_DAYS = 200


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_industry(path: Path = INDUSTRY_CSV) -> Dict[str, Dict[str, str]]:
    """code -> {name, industry}；文件缺失返回空字典（不阻断，只降级行业判定）。"""
    if not path.is_file():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            out[code] = {
                "name": str(row.get("name") or "").strip(),
                "industry": str(row.get("industry") or "").strip(),
                "market": str(row.get("market") or "").strip(),
            }
    return out


def load_bars(as_of: Optional[str]) -> pd.DataFrame:
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        latest = as_of or session.execute(text("select max(date) from stock_daily")).scalar()
        rows = session.execute(
            text(
                "select code, date, open, high, low, close, volume, amount "
                "from stock_daily where date <= :end order by code, date"
            ),
            {"end": latest},
        ).fetchall()
    frame = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume", "amount"])
    frame["date"] = frame["date"].astype(str).str[:10]
    return frame


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """按票计算结构指标，只保留有足够历史的票。"""
    grouped = frame.groupby("code", sort=False)
    stats = grouped.agg(
        bars=("date", "size"),
        last_date=("date", "last"),
        close=("close", "last"),
        high=("high", "last"),
        low=("low", "last"),
        amount=("amount", "last"),
    )
    # 尾部窗口指标
    tail = grouped.tail(LOOKBACK_DAYS)
    wide = tail.pivot(index="date", columns="code", values="close").sort_index()
    high_wide = tail.pivot(index="date", columns="code", values="high").sort_index()
    low_wide = tail.pivot(index="date", columns="code", values="low").sort_index()
    vol_wide = tail.pivot(index="date", columns="code", values="volume").sort_index()
    amt_wide = tail.pivot(index="date", columns="code", values="amount").sort_index()

    closes = wide.dropna(axis=1, how="all")
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    ret5 = closes.iloc[-1] / closes.iloc[-6] - 1 if len(closes) > 6 else pd.Series(dtype=float)
    ret20 = closes.iloc[-1] / closes.iloc[-21] - 1 if len(closes) > 21 else pd.Series(dtype=float)
    ret60 = closes.iloc[-1] / closes.iloc[-61] - 1 if len(closes) > 61 else pd.Series(dtype=float)
    high120 = high_wide.tail(120).max()
    low60 = low_wide.tail(60).min()
    vol5 = vol_wide.tail(5).mean()
    vol20 = vol_wide.tail(20).mean()
    amt20 = amt_wide.tail(20).mean()
    atr_series = (high_wide - low_wide).tail(20).mean()

    result = pd.DataFrame(
        {
            "close": closes.iloc[-1],
            "ma20": ma20.iloc[-1],
            "ma60": ma60.iloc[-1],
            "ma20_prev5": ma20.iloc[-6] if len(ma20) > 6 else float("nan"),
            "ret5": ret5,
            "ret20": ret20,
            "ret60": ret60,
            "high120": high120,
            "low60": low60,
            "low10": low_wide.tail(10).min(),
            "vol_ratio": (vol5 / vol20).replace([float("inf")], float("nan")),
            "amt20_wan": amt20 / 1e4,
            "atr_pct": (atr_series / closes.iloc[-1]).replace([float("inf")], float("nan")),
            "bars": stats["bars"],
            "last_date": stats["last_date"],
        }
    )
    return result.dropna(subset=["close", "ma20", "ma60", "ret20", "high120", "low60"])


def _screen(prepared: pd.DataFrame, industry: Dict[str, Dict[str, str]], cfg: Dict[str, Any]) -> pd.DataFrame:
    uni = cfg["universe"]
    st = cfg["structure"]
    rs = cfg["relative_strength"]
    vol_cfg = cfg["volatility"]

    df = prepared.copy()
    df["industry"] = [industry.get(c, {}).get("industry", "") for c in df.index]
    df["name"] = [industry.get(c, {}).get("name", "") for c in df.index]

    excluded_industries = set(cfg.get("exclude_industries") or [])
    reasons_drop: Dict[str, str] = {}
    keep = []
    market_median_ret20 = float(df["ret20"].median())

    name_kw = tuple(k for k in (cfg.get("exclude_name_keywords") or []) if k)
    for code, row in df.iterrows():
        name = str(row["name"])
        if not name or any(k and k in name for k in name_kw):
            reasons_drop[code] = f"名称命中排除词（{name}）"
            continue
        if row["industry"] in excluded_industries:
            reasons_drop[code] = f"行业排除（{row['industry']}）"
            continue
        if row["bars"] < uni["min_listed_days"]:
            reasons_drop[code] = f"历史不足（{int(row['bars'])} 根）"
            continue
        if row["amt20_wan"] < uni["min_avg_amount_wan"]:
            reasons_drop[code] = f"流动性不足（20日均额 {row['amt20_wan']:.0f} 万）"
            continue
        drawdown_high = row["close"] / row["high120"] - 1
        if drawdown_high > st["max_drawdown_from_high_120"]:
            reasons_drop[code] = f"离 120 日高点太近（{drawdown_high:+.1%}），属追高"
            continue
        off_low = row["close"] / row["low60"] - 1
        if off_low < st["min_off_low_60"]:
            reasons_drop[code] = f"还没离开底部（距 60 日低 {off_low:+.1%}）"
            continue
        if st["require_close_above_ma20"] and not row["close"] > row["ma20"]:
            reasons_drop[code] = "收盘在 MA20 下方"
            continue
        if st["require_ma20_above_ma60"] and not row["ma20"] > row["ma60"]:
            reasons_drop[code] = "MA20 仍在 MA60 下方（底部未抬升）"
            continue
        if not row["ma20"] > row["ma20_prev5"]:
            reasons_drop[code] = "MA20 未上行"
            continue
        if off_low > st.get("max_off_low_60", 1.0):
            reasons_drop[code] = f"距 60 日低已 {off_low:+.1%}，涨多了不是回踩位"
            continue
        if row["ret20"] > st.get("max_ret20", 1.0):
            reasons_drop[code] = f"20 日已涨 {row['ret20']:+.1%}，过热不追"
            continue
        if row["ret5"] > st["max_ret5"]:
            reasons_drop[code] = f"5 日已涨 {row['ret5']:+.1%}，过热不追"
            continue
        dist_ma20 = row["close"] / row["ma20"] - 1
        if dist_ma20 > st.get("max_dist_above_ma20", 1.0):
            reasons_drop[code] = f"离 MA20 {dist_ma20:+.1%}，等回踩再说"
            continue
        vol_low = st.get("vol_ratio_min", 0.0)
        vol_high = st.get("vol_ratio_max", 99.0)
        if not (vol_low <= row["vol_ratio"] <= vol_high):
            reasons_drop[code] = f"量能不在区间（5/20 均量 {row['vol_ratio']:.2f}）"
            continue
        excess_pp = (row["ret20"] - market_median_ret20) * 100
        if excess_pp < rs["min_excess_vs_market_pp"]:
            reasons_drop[code] = f"相对强度不够（跑赢中位 {excess_pp:+.1f}pp）"
            continue
        if not (vol_cfg["atr_pct_min"] <= row["atr_pct"] <= vol_cfg["atr_pct_max"]):
            reasons_drop[code] = f"波动不合适（ATR {row['atr_pct']:.1%}）"
            continue
        keep.append(code)

    picked = df.loc[keep].copy()
    if picked.empty:
        return picked

    # 行业强度：同一行业内 20 日收益中位数
    industry_strength = df.groupby("industry")["ret20"].median().to_dict()
    picked["market_median_ret20"] = market_median_ret20
    picked["excess20_pp"] = (picked["ret20"] - market_median_ret20) * 100
    picked["industry_ret20"] = [industry_strength.get(i, float("nan")) for i in picked["industry"]]
    picked["drawdown_high"] = picked["close"] / picked["high120"] - 1
    picked["off_low"] = picked["close"] / picked["low60"] - 1
    picked["dist_ma20"] = picked["close"] / picked["ma20"] - 1
    stop_cap = float((cfg.get("risk") or {}).get("stop_max_pct", 0.09))
    picked["stop"] = picked.apply(
        lambda r: max(float(r["low10"]), float(r["close"]) * (1 - stop_cap)), axis=1
    )

    # 打分：相对强度 30 + 回踩贴合 20 + 结构 15 + 量能 20 + 行业 15（分位数归一）
    def _pct(series: pd.Series) -> pd.Series:
        return series.rank(pct=True).fillna(0.0)

    picked["score"] = (
        _pct(picked["excess20_pp"]) * 30
        + _pct(-picked["dist_ma20"]) * 20
        + _pct(-(picked["off_low"] - 0.20).abs()) * 15
        + _pct(picked["vol_ratio"]) * 20
        + _pct(picked["industry_ret20"]) * 15
    ).round(2)
    return picked.sort_values("score", ascending=False)


def _explain(code: str, row: pd.Series, cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop = float(row["stop"])
    close = float(row["close"])
    return {
        "code": code,
        "name": row["name"],
        "industry": row["industry"],
        "close": round(close, 2),
        "score": float(row["score"]),
        "why": [
            f"距 120 日高 {row['drawdown_high']:+.1%}（不在高位，留得出空间）",
            f"已离开 60 日低 {row['off_low']:+.1%}，底部在抬但没走远",
            f"MA20 > MA60 且 MA20 上行，收盘在 MA20 上方 {row['dist_ma20']:+.1%}（回踩位附近）",
            f"20 日 {row['ret20']:+.1%}，跑赢全市场中位 {row['excess20_pp']:+.1f}pp",
            f"5 日/20 日均量 {row['vol_ratio']:.2f}，有资金接但不极端放量",
            f"行业〔{row['industry']}〕20 日中位 {row['industry_ret20']:+.1%}",
        ],
        "plan": {
            "stop": round(stop, 2),
            "stop_pct": round(stop / close - 1, 4),
            "target_ref": round(float(row["high120"]), 2),
            "holding_days": cfg["output"]["holding_days"],
            "entry_note": "次日开盘不追高于今日收盘 +2%；回踩 MA20 不破可分批",
        },
        "pending_dimensions": ["业绩(营收/扣非/ROE)", "机构持仓(十大流通股东/基金)", "ETF 资金流"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="波段选股（底部抬升 + 相对强度），输出可解释名单")
    parser.add_argument("--as-of", default=None, help="截止交易日 YYYY-MM-DD，默认库内最新")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--top", type=int, default=None, help="输出数量，默认取配置")
    parser.add_argument("--explain-limit", type=int, default=10)
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    industry = load_industry()
    frame = load_bars(args.as_of)
    if frame.empty:
        print("[swing_pick] stock_daily 无数据")
        return 1
    as_of = str(frame["date"].max())

    prepared = _prepare(frame)
    picked = _screen(prepared, industry, cfg)
    top_n = args.top or cfg["output"]["top_n"]
    if picked.empty:
        print(f"[swing_pick] as_of={as_of} 无票通过（可放宽 config 阈值）")
        return 0

    picks = [_explain(code, row, cfg) for code, row in picked.head(top_n).iterrows()]
    payload = {
        "as_of": as_of,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "rule": "bottom-up + relative-strength + volume（熊市波段）",
        "universe_size": int(len(prepared)),
        "passed": int(len(picked)),
        "excluded_industries": cfg["exclude_industries"],
        "benchmark": "全市场 20 日收益中位数",
        "data_status": {
            "price_volume": "ok（stock_daily 前复权）",
            "industry": "ok（tushare_stock_basic 快照）",
            "fundamental": "pending（本地无财报数据）",
            "institution": "pending（仅支持逐票抓取）",
            "etf_flow": "pending",
        },
        "picks": picks,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "swing_pick_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 波段选股 · {as_of}",
        "",
        f"> 口径：{payload['rule']}；基准={payload['benchmark']}；"
        f"全市场 {payload['universe_size']} 只 → 通过 {payload['passed']} 只，展示前 {len(picks)} 只。",
        f"> 已排除行业：{'、'.join(cfg['exclude_industries'])}；ST/次新/超大盘/低流动性另行剔除。",
        "> 业绩、机构持仓、ETF 资金流当前**未接入**，不缺省成结论。",
        "",
    ]
    for i, item in enumerate(picks, 1):
        lines.append(f"## {i}. `{item['code']}` {item['name']}（{item['industry']}）　score {item['score']}")
        lines.append("")
        lines.append(f"- 现价 {item['close']}　止损参考 {item['plan']['stop']}（{item['plan']['stop_pct']:+.1%}，近 10 日低）　"
                     f"上行参考 {item['plan']['target_ref']}（120 日高）　建议持有 {item['plan']['holding_days']} 个交易日")
        for why in item["why"]:
            lines.append(f"- {why}")
        lines.append(f"- 入场：{item['plan']['entry_note']}")
        lines.append(f"- 未纳入维度：{'、'.join(item['pending_dimensions'])}")
        lines.append("")
    (OUT_DIR / "swing_pick_latest.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"[swing_pick] as_of={as_of} 全市场={len(prepared)} 通过={len(picked)} 展示={len(picks)}")
    for item in picks:
        print(f"  {item['code']} {item['name']:<6} {item['industry']:<6} score={item['score']:>6} "
              f"close={item['close']:>7} stop={item['plan']['stop']:>7} ({item['plan']['stop_pct']:+.1%})")
    print(f"[swing_pick] -> {OUT_DIR / 'swing_pick_latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
