#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""炸板事件量化研究：用历史炸板事件检验「资金 + 成交量」对后续走势的区分度。

只做统计，不改线上数据：
- 事件：最近 N 个交易日的东财炸板池（全部，不截断）；
- 特征（炸板日，不使用未来数据）：
  - 资金：主力净额占比、超大单/大单净额占比、小单净额占比、散户净额；
  - 量：成交额、换手率、成交额 / 前 20 日均额；
  - 盘口：炸板次数、首次封板时间、振幅、收盘距涨停价。
- 结果（炸板日收盘为基准）：T+1 收益、T+1~T+3 最高收益、3 日内是否收复炸板日收盘。

输出：`data/strategy_review/broken_events/research_<as_of>.{csv,json}` + 控制台分桶表。

用法：
    ./.venv-linux/bin/python scripts/research_broken_events.py --days 25
    ./.venv-linux/bin/python scripts/research_broken_events.py --days 40 --as-of 2026-11-10
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "broken_events"


def _trade_dates(as_of: str, count: int) -> List[str]:
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(
            text("select distinct date from stock_daily where date <= :d order by date desc limit :n"),
            {"d": as_of, "n": count},
        ).fetchall()
    return [str(row[0])[:10] for row in rows]


def _avg_amount(codes: List[str], upto: str, window: int = 20) -> Dict[str, float]:
    """事件日之前 window 个交易日的平均成交额（不含事件日，避免用未来数据）。"""
    from sqlalchemy import text

    from src.storage import DatabaseManager

    if not codes:
        return {}
    db = DatabaseManager.get_instance()
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    params: Dict[str, Any] = {f"c{i}": code for i, code in enumerate(codes)}
    params["d"] = upto
    with db.session_scope() as session:
        rows = session.execute(
            text(
                f"select code, date, amount from stock_daily "
                f"where code in ({placeholders}) and date < :d order by code, date desc"
            ),
            params,
        ).fetchall()
    buckets: Dict[str, List[float]] = {}
    for code, _day, amount in rows:
        values = buckets.setdefault(str(code), [])
        if len(values) < window and amount:
            values.append(float(amount))
    return {code: sum(values) / len(values) for code, values in buckets.items() if values}


def _pct_rank(value: Optional[float], series: List[float]) -> Optional[float]:
    if value is None or not series:
        return None
    below = sum(1 for item in series if item <= value)
    return round(below / len(series), 4)


def main() -> int:
    parser = argparse.ArgumentParser(description="炸板事件量化研究")
    parser.add_argument("--as-of", help="截止交易日，默认 stock_daily 最新交易日")
    parser.add_argument("--days", type=int, default=25, help="回看的交易日数")
    args = parser.parse_args()

    from src.services.break_window_service import analyze_break_window
    from src.services.broken_followup_service import _flow_history
    from src.services.broken_score import compute_break_score
    from src.services.market_review_service import build_market_review

    as_of = args.as_of
    if not as_of:
        dates_all = _trade_dates("2026-12-31", 1)
        as_of = dates_all[0]
    dates = sorted(_trade_dates(as_of, args.days))
    print(f"[research] as_of={as_of} 交易日={len(dates)} ({dates[0]}~{dates[-1]})")

    events: List[Dict[str, Any]] = []
    for day in dates:
        try:
            pool = build_market_review(day).get("broken") or []
        except Exception as exc:  # noqa: BLE001 - 单日失败跳过
            print(f"  {day} 取数失败: {exc}")
            continue
        for item in pool:
            events.append(
                {
                    "code": str(item.get("code") or "").zfill(6),
                    "name": item.get("name"),
                    "industry": item.get("industry"),
                    "break_date": day,
                    "break_count": item.get("break_count"),
                    "change_pct": item.get("change_pct"),
                    "amount": item.get("amount"),
                    "turnover": item.get("turnover"),
                    "amplitude": item.get("amplitude"),
                    "first_seal": item.get("first_seal"),
                    "price": item.get("price"),
                    "limit_price": item.get("limit_price"),
                }
            )
        print(f"  {day}: 炸板 {len(pool)}")

    codes = sorted({item["code"] for item in events})
    print(f"[research] 事件 {len(events)} 个，涉及 {len(codes)} 只票，开始拉资金流…")

    flows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    done = {"n": 0}

    def load(code: str) -> None:
        flows[code] = _flow_history(code, as_of)
        done["n"] += 1
        if done["n"] % 50 == 0:
            print(f"    资金流进度 {done['n']}/{len(codes)}")

    with ThreadPoolExecutor(max_workers=8) as pool_exec:
        list(pool_exec.map(load, codes))

    rows: List[Dict[str, Any]] = []
    avg_by_date: Dict[str, Dict[str, float]] = {}
    by_date: Dict[str, List[str]] = {}
    for item in events:
        by_date.setdefault(item["break_date"], []).append(item["code"])
    for day, codes_of_day in by_date.items():
        avg_by_date[day] = _avg_amount(codes_of_day, day)
    for item in events:
        history = flows.get(item["code"]) or {}
        base = history.get(item["break_date"]) or {}
        base_close = base.get("close") or item.get("price")
        if not base_close:
            continue
        later = [day for day in sorted(history) if day > item["break_date"] and day <= as_of]
        closes = [(day, history[day].get("close")) for day in later]
        closes = [(day, close) for day, close in closes if close]
        if not closes:
            continue  # 还没有后续交易日，无法评估
        t1 = closes[0][1]
        window = min(3, len(closes))
        max3 = max(close for _, close in closes[:window])
        avg20 = (avg_by_date.get(item["break_date"]) or {}).get(item["code"])
        amount = item.get("amount")
        total_amount = amount
        rows.append(
            {
                **item,
                "base_close": base_close,
                "main_ratio": (base.get("main_ratio") / 100) if base.get("main_ratio") is not None else None,
                "super": base.get("super"),
                "big": base.get("big"),
                "mid": base.get("mid"),
                "small": base.get("small"),
                "retail": (base.get("mid") or 0) + (base.get("small") or 0) if base else None,
                "amount_ratio": round(amount / avg20, 3) if amount and avg20 else None,
                "dist_limit": round(base_close / item["limit_price"] - 1, 4) if item.get("limit_price") else None,
                "t1_ret": round(t1 / base_close - 1, 4),
                "max3_ret": round(max3 / base_close - 1, 4),
                "recovered3": bool(max3 >= base_close),
                "total_amount": total_amount,
            }
        )
        window = analyze_break_window(
            item["code"], item["break_date"], item.get("limit_price"), item.get("first_seal"), as_of=as_of
        )
        rows[-1]["break_ratio"] = window.get("break_ratio")
        rows[-1]["break_method"] = window.get("method")
        rows[-1]["break_big_buy_share"] = window.get("big_buy_share")
        rows[-1]["break_big_net"] = window.get("big_net_amount")
        scored = compute_break_score(
            amount_ratio=rows[-1]["amount_ratio"],
            main_ratio=rows[-1]["main_ratio"],
            retail_net=rows[-1]["retail"],
            amount=rows[-1]["amount"],
            break_count=rows[-1]["break_count"],
            turnover=rows[-1]["turnover"],
            change_pct=rows[-1]["change_pct"],
        )
        rows[-1].update({"score": scored["score"], "grade": scored["grade"]})

    if not rows:
        print("[research] 没有可用样本（可能近期无炸板或资金流缺失）")
        return 1

    # ---------------- 统计 ----------------
    def bucket_stats(label: str, buckets: List[tuple], key: str) -> List[Dict[str, Any]]:
        out = []
        for name, low, high in buckets:
            subset = [
                row
                for row in rows
                if row.get(key) is not None and (low is None or row[key] >= low) and (high is None or row[key] < high)
            ]
            if not subset:
                continue
            wins = sum(1 for row in subset if row["t1_ret"] > 0)
            out.append(
                {
                    "bucket": name,
                    "n": len(subset),
                    "t1_win": round(wins / len(subset), 3),
                    "t1_mean": round(sum(row["t1_ret"] for row in subset) / len(subset), 4),
                    "max3_mean": round(sum(row["max3_ret"] for row in subset) / len(subset), 4),
                    "recovered3": round(sum(1 for row in subset if row["recovered3"]) / len(subset), 3),
                }
            )
        print(f"\n[{label}]")
        for item in out:
            print(
                f"  {item['bucket']:<12} n={item['n']:>4}  T+1胜率={item['t1_win']*100:5.1f}%  "
                f"T+1均值={item['t1_mean']*100:+5.1f}%  3日最高均值={item['max3_mean']*100:+5.1f}%  "
                f"3日收复率={item['recovered3']*100:5.1f}%"
            )
        return out

    print(f"\n[总体] n={len(rows)}  T+1胜率={sum(1 for r in rows if r['t1_ret'] > 0)/len(rows)*100:.1f}%  "
          f"T+1均值={sum(r['t1_ret'] for r in rows)/len(rows)*100:+.1f}%")

    result = {
        "as_of": as_of,
        "days": args.days,
        "events": len(rows),
        "overall": {
            "n": len(rows),
            "t1_win": round(sum(1 for r in rows if r["t1_ret"] > 0) / len(rows), 3),
            "t1_mean": round(sum(r["t1_ret"] for r in rows) / len(rows), 4),
            "max3_mean": round(sum(r["max3_ret"] for r in rows) / len(rows), 4),
            "recovered3": round(sum(1 for r in rows if r["recovered3"]) / len(rows), 3),
        },
        "buckets": {},
    }

    result["buckets"]["main_ratio"] = bucket_stats(
        "主力净额占成交额比例",
        [("<-5%", None, -0.05), ("-5%~0", -0.05, 0), ("0~5%", 0, 0.05), ("5%~10%", 0.05, 0.10), (">10%", 0.10, None)],
        "main_ratio",
    )
    result["buckets"]["amount_ratio"] = bucket_stats(
        "成交额 / 前20日均额",
        [("<1.5x", None, 1.5), ("1.5~3x", 1.5, 3), ("3~5x", 3, 5), (">5x", 5, None)],
        "amount_ratio",
    )
    result["buckets"]["turnover"] = bucket_stats(
        "换手率(%)",
        [("<5%", None, 5), ("5~10%", 5, 10), ("10~20%", 10, 20), (">20%", 20, None)],
        "turnover",
    )
    result["buckets"]["break_count"] = bucket_stats(
        "炸板次数",
        [("1次", None, 1.5), ("2~3次", 1.5, 3.5), (">=4次", 3.5, None)],
        "break_count",
    )
    result["buckets"]["small_net"] = bucket_stats(
        "小单净额(万元)",
        [("<-2000", None, -2e7), ("-2000~0", -2e7, 0), ("0~2000", 0, 2e7), (">2000", 2e7, None)],
        "small",
    )

    result["buckets"]["break_ratio"] = bucket_stats(
        "炸板时段成交额占当日比例",
        [("<20%", None, 0.2), ("20~40%", 0.2, 0.4), ("40~60%", 0.4, 0.6), (">=60%", 0.6, None)],
        "break_ratio",
    )

    # 组合：主力净买 + 放量
    combos = {
        "主力净买>5% 且 放量>3x": lambda r: (r.get("main_ratio") or 0) > 0.05 and (r.get("amount_ratio") or 0) > 3,
        "主力净买>5% 且 量能一般(1.5~3x)": lambda r: (r.get("main_ratio") or 0) > 0.05 and 1.5 <= (r.get("amount_ratio") or 0) <= 3,
        "主力净卖 且 小单净买": lambda r: (r.get("main_ratio") or 0) < 0 and (r.get("small") or 0) > 0,
        "主力净买 且 小单净买(散户也买)": lambda r: (r.get("main_ratio") or 0) > 0 and (r.get("small") or 0) > 0,
        "炸板>=2次 且 主力净卖": lambda r: (r.get("break_count") or 0) >= 2 and (r.get("main_ratio") or 0) < 0,
    }
    combo_stats = []
    print("\n[组合条件]")
    for name, fn in combos.items():
        subset = [row for row in rows if fn(row)]
        if not subset:
            print(f"  {name:<34} n=0")
            continue
        wins = sum(1 for row in subset if row["t1_ret"] > 0)
        item = {
            "name": name,
            "n": len(subset),
            "t1_win": round(wins / len(subset), 3),
            "t1_mean": round(sum(row["t1_ret"] for row in subset) / len(subset), 4),
            "max3_mean": round(sum(row["max3_ret"] for row in subset) / len(subset), 4),
            "recovered3": round(sum(1 for row in subset if row["recovered3"]) / len(subset), 3),
        }
        combo_stats.append(item)
        print(
            f"  {name:<34} n={item['n']:>4}  T+1胜率={item['t1_win']*100:5.1f}%  "
            f"T+1均值={item['t1_mean']*100:+5.1f}%  3日最高={item['max3_mean']*100:+5.1f}%  "
            f"3日收复={item['recovered3']*100:5.1f}%"
        )
    combos_ratio = {
        "炸板占比>=60% 且 主力净买": lambda r: (r.get("break_ratio") or 0) >= 0.6 and (r.get("main_ratio") or 0) > 0,
        "炸板占比>=60% 且 缩量<1.5x": lambda r: (r.get("break_ratio") or 0) >= 0.6 and (r.get("amount_ratio") or 9) < 1.5,
        "炸板占比<20%（几乎没开板）": lambda r: (r.get("break_ratio") or 1) < 0.2,
    }
    print("\n[炸板占比组合]")
    ratio_combos = []
    for name, fn in combos_ratio.items():
        subset = [row for row in rows if row.get("break_ratio") is not None and fn(row)]
        if not subset:
            print(f"  {name:<28} n=0")
            continue
        wins = sum(1 for row in subset if row["t1_ret"] > 0)
        item = {
            "name": name,
            "n": len(subset),
            "t1_win": round(wins / len(subset), 3),
            "t1_mean": round(sum(row["t1_ret"] for row in subset) / len(subset), 4),
            "max3_mean": round(sum(row["max3_ret"] for row in subset) / len(subset), 4),
            "recovered3": round(sum(1 for row in subset if row["recovered3"]) / len(subset), 3),
        }
        ratio_combos.append(item)
        print(
            f"  {name:<28} n={item['n']:>4}  T+1胜率={item['t1_win']*100:5.1f}%  "
            f"T+1均值={item['t1_mean']*100:+5.1f}%  3日收复={item['recovered3']*100:5.1f}%"
        )
    result["ratio_combos"] = ratio_combos

    result["combos"] = combo_stats

    # 分数分桶验证
    print("\n[分数分桶]")
    score_buckets = []
    for name, low, high in (
        ("<45 不跟", None, 45),
        ("45~59", 45, 60),
        ("60~69", 60, 70),
        ("70~79", 70, 80),
        (">=80", 80, None),
    ):
        subset = [
            row
            for row in rows
            if row.get("score") is not None and (low is None or row["score"] >= low) and (high is None or row["score"] < high)
        ]
        if not subset:
            print(f"  {name:<8} n=0")
            continue
        wins = sum(1 for row in subset if row["t1_ret"] > 0)
        item = {
            "bucket": name,
            "n": len(subset),
            "t1_win": round(wins / len(subset), 3),
            "t1_mean": round(sum(row["t1_ret"] for row in subset) / len(subset), 4),
            "max3_mean": round(sum(row["max3_ret"] for row in subset) / len(subset), 4),
            "recovered3": round(sum(1 for row in subset if row["recovered3"]) / len(subset), 3),
        }
        score_buckets.append(item)
        print(
            f"  {item['bucket']:<8} n={item['n']:>4}  T+1胜率={item['t1_win']*100:5.1f}%  "
            f"T+1均值={item['t1_mean']*100:+5.1f}%  3日最高={item['max3_mean']*100:+5.1f}%  "
            f"3日收复={item['recovered3']*100:5.1f}%"
        )
    result["score_buckets"] = score_buckets
    result["rows"] = rows

    # ---------------- 逻辑回归拟合（numpy 手写，无 sklearn 依赖） ----------------
    try:
        import numpy as np

        def feature_vector(row: Dict[str, Any]) -> Optional[List[float]]:
            values = [
                row.get("amount_ratio"),
                row.get("main_ratio"),
                (row.get("retail") / row["amount"]) if row.get("retail") is not None and row.get("amount") else None,
                row.get("break_count"),
                row.get("turnover"),
                row.get("change_pct"),
                row.get("break_ratio"),
            ]
            if any(value is None for value in values):
                return None
            ratio = max(0.2, min(20.0, float(values[0])))
            return [
                float(np.log(ratio)),
                float(values[1]) * 100,  # 主力净占比 → 百分点
                float(values[2]) * 100,  # 散户净额占成交额 → 百分点
                float(values[3]),
                float(values[4]),
                float(values[5]),
                float(np.log(max(0.05, min(1.0, float(values[6]))))),
            ]

        miss = {
            "amount_ratio": sum(1 for row in rows if row.get("amount_ratio") is None),
            "main_ratio": sum(1 for row in rows if row.get("main_ratio") is None),
            "retail": sum(1 for row in rows if row.get("retail") is None),
            "break_count": sum(1 for row in rows if row.get("break_count") is None),
            "turnover": sum(1 for row in rows if row.get("turnover") is None),
            "change_pct": sum(1 for row in rows if row.get("change_pct") is None),
        }
        print(f"  缺失统计: {miss}")

        names = ["log量比", "主力净占比", "散户净占比", "炸板次数", "换手率", "当日涨跌幅", "log炸板占比"]
        pairs = [(feature_vector(row), 1.0 if row["t1_ret"] > 0 else 0.0) for row in rows]
        pairs = [pair for pair in pairs if pair[0] is not None]
        print(f"\n[回归] 可用样本 {len(pairs)} / {len(rows)}")
        if len(pairs) >= 60:
            X = np.array([pair[0] for pair in pairs], dtype=float)
            y = np.array([pair[1] for pair in pairs], dtype=float)
            mu = X.mean(axis=0)
            sd = X.std(axis=0)
            sd[sd == 0] = 1.0
            Z = (X - mu) / sd
            Z = np.hstack([np.ones((len(Z), 1)), Z])
            weights = np.zeros(Z.shape[1])
            lr = 0.3
            for _ in range(4000):
                pred = 1 / (1 + np.exp(-Z @ weights))
                grad = Z.T @ (pred - y) / len(y)
                weights -= lr * grad
            prob = 1 / (1 + np.exp(-Z @ weights))

            # AUC
            order = np.argsort(prob)
            ranks = np.empty_like(order, dtype=float)
            ranks[order] = np.arange(1, len(prob) + 1)
            pos = y.sum()
            neg = len(y) - pos
            auc = (ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg) if pos and neg else float("nan")

            print(f"  AUC={auc:.3f}  基准胜率={y.mean()*100:.1f}%")
            print("  系数(标准化): " + ", ".join(f"{names[i]}={weights[i+1]:+.3f}" for i in range(len(names))))
            print(f"  截距={weights[0]:+.3f}")

            # 十分位
            decile_rows = sorted(zip(prob, rows), key=lambda item: item[0])
            n = len(decile_rows)
            print("  十分位（按预测概率升序，越高越好）:")
            deciles = []
            for i in range(10):
                chunk = [row for _, row in decile_rows[i * n // 10 : (i + 1) * n // 10]]
                if not chunk:
                    continue
                win = sum(1 for row in chunk if row["t1_ret"] > 0) / len(chunk)
                mean_ret = sum(row["t1_ret"] for row in chunk) / len(chunk)
                rec = sum(1 for row in chunk if row["recovered3"]) / len(chunk)
                deciles.append({"decile": i + 1, "n": len(chunk), "win": round(win, 3),
                                "t1_mean": round(mean_ret, 4), "recovered3": round(rec, 3)})
                print(f"    D{i+1:<2} n={len(chunk):>3} 胜率={win*100:5.1f}% T+1均值={mean_ret*100:+5.1f}% 3日收复={rec*100:5.1f}%")

            # 多特征集对比：看哪组特征真正有区分度
            print("  特征集对比:")
            subsets = {
                "仅量比": [0],
                "量比+炸板次数": [0, 3],
                "量比+炸板次数+散户净占比": [0, 2, 3],
                "量比+主力净占比+散户净占比": [0, 1, 2],
                "量比+炸板占比": [0, 6],
                "主力净占比+炸板占比": [1, 6],
                "全量": list(range(len(names))),
            }
            comparisons = []
            for label, idx in subsets.items():
                Zs = np.hstack([np.ones((len(Z), 1)), Z[:, [i + 1 for i in idx]]])
                ws = np.zeros(Zs.shape[1])
                for _ in range(4000):
                    pr = 1 / (1 + np.exp(-Zs @ ws))
                    ws -= 0.3 * (Zs.T @ (pr - y) / len(y))
                ps = 1 / (1 + np.exp(-Zs @ ws))
                order_s = np.argsort(ps)
                ranks_s = np.empty_like(order_s, dtype=float)
                ranks_s[order_s] = np.arange(1, len(ps) + 1)
                auc_s = (ranks_s[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg) if pos and neg else float("nan")
                top = [row for _, row in sorted(zip(ps, rows), key=lambda item: item[0])[-len(ps) // 10 :]]
                top_win = sum(1 for row in top if row["t1_ret"] > 0) / len(top) if top else 0
                comparisons.append({"features": label, "auc": round(float(auc_s), 4), "top_decile_win": round(top_win, 3)})
                print(f"    {label:<28} AUC={auc_s:.3f}  最高十分位胜率={top_win*100:5.1f}%")
            result["comparisons"] = comparisons

            result["model"] = {
                "features": names,
                "coef_std": [float(value) for value in weights[1:]],
                "intercept": float(weights[0]),
                "mean": [float(value) for value in mu],
                "std": [float(value) for value in sd],
                "auc": float(auc),
                "base_win": float(y.mean()),
                "n": int(len(y)),
                "deciles": deciles,
            }
    except Exception as exc:  # noqa: BLE001 - 拟合失败不影响分桶统计
        print(f"  回归拟合失败: {exc}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = as_of.replace("-", "")
    (OUT_DIR / f"research_{stem}.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        import pandas as pd

        pd.DataFrame(rows).to_csv(OUT_DIR / f"research_{stem}.csv", index=False, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001 - CSV 只是辅助产物
        print(f"CSV 写入失败: {exc}")
    print(f"\n[research] 结果已写入 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
