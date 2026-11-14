#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国家队（社保 / 汇金 / 证金 / 国新）十大流通股东持仓追踪。

数据源：东财「十大流通股东持股分析」，按报告期返回全市场记录（每季约 5~6 万行）。
口径注意：只有进入前十大流通股东的持仓可见；「消失」= 减到第 11 名以后或清仓，
两者无法区分，因此报告里的措辞统一为「退出前十大」。ETF 持仓不进十大股东，
不在本脚本覆盖范围内（宽基 ETF 份额见 `scripts/etf_flow_history.py` +
`scripts/analyze_etf_national_team.py`）。

用法：
    # 自动识别最新可用季度，并与上一季度对比
    ./.venv-linux/bin/python scripts/track_national_team_holdings.py

    # 指定季度 + 基线
    ./.venv-linux/bin/python scripts/track_national_team_holdings.py \\
        --quarter 20260930 --baseline 20250630

    # 连最近 6 个季度一起出趋势表
    ./.venv-linux/bin/python scripts/track_national_team_holdings.py --history 6

    # 只用本地缓存（不联网；缓存缺失的季度会跳过）
    ./.venv-linux/bin/python scripts/track_national_team_holdings.py --history 8 --cache-only

产物：
    data/cache/national_team/gdfx_<报告期>.csv        原始快照缓存
    data/strategy_review/national_team_<报告期>/       summary.json / report.md / diff_*.csv
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "national_team"
OUT_ROOT = PROJECT_ROOT / "data" / "strategy_review"

# (展示名, 股东名称关键字) —— 顺序即报告顺序
ENTITIES: Sequence[Tuple[str, str]] = (
    ("社保基金", "全国社保基金"),
    ("汇金投资", "中央汇金投资有限"),
    ("汇金资管", "中央汇金资产"),
    ("证金公司", "中国证券金融"),
    ("国新投资", "国新投资"),
    ("梧桐树", "梧桐树投资"),
)

# 这些主体的持股数长期不变，市值变化只反映股价，报告里需要单独注明
FIXED_POSITION_ENTITIES = frozenset({"汇金投资"})

IDX_NAME = "股东名称"
IDX_CODE = "股票代码"
IDX_NAME_SHORT = "股票简称"
IDX_MV = "期末持股-流通市值"
IDX_QTY = "期末持股-数量"
IDX_CHG = "期末持股-持股变动"

# 报告期按「已披露」常识往前推：Q1 报 4 月底、Q2 报 8 月底、Q3 报 10 月底、Q4 报次年 4 月底
_QUARTER_MONTHS = ("0331", "0630", "0930", "1231")


def _quarter_candidates(as_of: pd.Timestamp, lookback: int = 6) -> List[str]:
    """从 as_of 往前推 lookback 个已过披露期的报告期（新 → 旧）。"""
    out: List[str] = []
    year = as_of.year
    while len(out) < lookback:
        for mm in reversed(_QUARTER_MONTHS):
            period_end = pd.Timestamp(f"{year}-{mm[:2]}-{mm[2:]}")
            # 披露滞后：Q1/Q3 约 1 个月，Q2 约 2 个月，Q4 约 4 个月
            lag_days = {"0331": 35, "0630": 65, "0930": 35, "1231": 120}[mm]
            if period_end + pd.Timedelta(days=lag_days) <= as_of:
                out.append(f"{year}{mm}")
            if len(out) >= lookback:
                break
        year -= 1
        if year < 2018:
            break
    return out


def _cache_path(quarter: str) -> Path:
    return CACHE_DIR / f"gdfx_{quarter}.csv"


def fetch_quarter(quarter: str, force: bool = False, tries: int = 4) -> Optional[pd.DataFrame]:
    """拉取单季快照，命中缓存直接返回。失败返回 None（不抛异常，便于批量跑）。"""
    path = _cache_path(quarter)
    if path.exists() and not force:
        df = pd.read_csv(path, dtype={IDX_CODE: str})
        if len(df):
            print(f"[cache] {quarter}: {len(df)} 行")
            return df

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import akshare as ak

    for i in range(tries):
        try:
            df = ak.stock_gdfx_free_holding_analyse_em(date=quarter)
            if df is None or not len(df):
                print(f"[warn] {quarter}: 返回空数据（该季可能未披露）")
                return None
            df.to_csv(path, index=False)
            print(f"[fetch] {quarter}: {len(df)} 行 -> {path.relative_to(PROJECT_ROOT)}")
            return df
        except Exception as exc:  # noqa: BLE001 - 三方接口异常类型不稳定
            wait = 6 * (i + 1)
            print(f"[retry] {quarter} 第 {i + 1} 次失败（{type(exc).__name__}），{wait}s 后重试")
            if i + 1 < tries:
                time.sleep(wait)
    print(f"[error] {quarter}: 拉取失败，跳过")
    return None


def _to_mv(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df[IDX_MV], errors="coerce") / 1e8


def entity_slice(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    mask = df[IDX_NAME].astype(str).str.contains(keyword, na=False)
    sub = df.loc[mask].copy()
    if len(sub):
        sub["_mv_yi"] = _to_mv(sub)
        sub["_qty"] = pd.to_numeric(sub[IDX_QTY], errors="coerce")
    return sub


def summarize(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """各主体：市值合计（亿）、涉及个股数、增/减/不变 家数。"""
    out: Dict[str, Dict[str, Any]] = {}
    for label, keyword in ENTITIES:
        sub = entity_slice(df, keyword)
        if not len(sub):
            out[label] = {"mv_yi": 0.0, "codes": 0, "records": 0, "inc": 0, "dec": 0, "flat": 0}
            continue
        chg = sub[IDX_CHG].astype(str)
        out[label] = {
            "mv_yi": round(float(sub["_mv_yi"].sum()), 1),
            "codes": int(sub[IDX_CODE].nunique()),
            "records": int(len(sub)),
            "inc": int((chg == "增加").sum()),
            "dec": int((chg == "减少").sum()),
            "flat": int((chg == "不变").sum()),
        }
    return out


def _by_code(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    sub = entity_slice(df, keyword)
    if not len(sub):
        return pd.DataFrame(columns=[IDX_CODE, "name", "mv_yi"])
    g = sub.groupby(IDX_CODE, as_index=False).agg(
        name=(IDX_NAME_SHORT, "first"), mv_yi=("_mv_yi", "sum")
    )
    return g


def diff_entity(prev: pd.DataFrame, cur: pd.DataFrame, keyword: str) -> Dict[str, Any]:
    """两期对比：退出前十大 / 新进 的个股清单。"""
    a, b = _by_code(prev, keyword), _by_code(cur, keyword)
    prev_codes, cur_codes = set(a[IDX_CODE]), set(b[IDX_CODE])
    gone = a[a[IDX_CODE].isin(prev_codes - cur_codes)].sort_values("mv_yi", ascending=False)
    new = b[b[IDX_CODE].isin(cur_codes - prev_codes)].sort_values("mv_yi", ascending=False)
    return {
        "prev_codes": len(prev_codes),
        "cur_codes": len(cur_codes),
        "gone": gone,
        "new": new,
        "gone_mv_yi": round(float(gone["mv_yi"].sum()), 1),
        "new_mv_yi": round(float(new["mv_yi"].sum()), 1),
    }


def holding_changes(prev: pd.DataFrame, cur: pd.DataFrame, keyword: str) -> pd.DataFrame:
    """两期都在的个股，按市值变化排序（含股价因素的近似值，仅供方向参考）。"""
    a, b = _by_code(prev, keyword), _by_code(cur, keyword)
    j = a.merge(b, on=IDX_CODE, how="inner", suffixes=("_prev", "_cur"))
    if not len(j):
        return j
    j["delta_mv_yi"] = (j["mv_yi_cur"] - j["mv_yi_prev"]).round(2)
    return j.sort_values("delta_mv_yi", ascending=False)


def _fmt_series(series: pd.Series, topn: int) -> str:
    items = [f"{r['name']}{r['mv_yi']:.0f}亿" for _, r in series.head(topn).iterrows()]
    return "、".join(items) if items else "—"


def render_report(
    quarter: str,
    baseline: Optional[str],
    cur_sum: Dict[str, Dict[str, Any]],
    base_sum: Optional[Dict[str, Dict[str, Any]]],
    diffs: Dict[str, Dict[str, Any]],
    changes: Dict[str, pd.DataFrame],
    history_rows: List[Tuple[str, Dict[str, Dict[str, Any]]]],
    cur_rows: int,
) -> str:
    lines: List[str] = []
    lines.append(f"# 国家队持仓追踪 {quarter}")
    lines.append("")
    lines.append(f"- 报告期：**{quarter[:4]}-{quarter[4:6]}-{quarter[6:]}**")
    lines.append(f"- 样本：全市场十大流通股东 {cur_rows} 条记录")
    lines.append("- 口径：仅统计进入前十大流通股东的持仓；「退出前十大」不等于清仓；ETF 持仓不在此表")
    if baseline:
        lines.append(f"- 对比基线：{baseline[:4]}-{baseline[4:6]}-{baseline[6:]}")
    lines.append("")

    lines.append("## 一、本期持仓")
    lines.append("")
    lines.append("| 主体 | 市值(亿) | 个股数 | 记录数 | 增持 | 减持 | 不变 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, _ in ENTITIES:
        s = cur_sum[label]
        if not s["codes"] and not s["mv_yi"]:
            continue  # 本期无持仓的主体直接跳过（如梧桐树）
        lines.append(
            f"| {label} | {s['mv_yi']:,.1f} | {s['codes']} | {s['records']} | "
            f"{s['inc']} | {s['dec']} | {s['flat']} |"
        )
    total = sum(cur_sum[label]["mv_yi"] for label, _ in ENTITIES)
    lines.append(f"| **合计** | **{total:,.1f}** | — | — | — | — | — |")
    lines.append("")

    if baseline and base_sum:
        lines.append(f"## 二、与 {baseline} 对比")
        lines.append("")
        lines.append("| 主体 | 基线市值(亿) | 本期市值(亿) | 变化 | 只数变化 | 退出前十大 | 新进 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for label, _ in ENTITIES:
            if label not in diffs:
                continue
            d = diffs[label]
            b, c = base_sum[label], cur_sum[label]
            if not b["codes"] and not c["codes"]:
                continue
            delta = c["mv_yi"] - b["mv_yi"]
            lines.append(
                f"| {label} | {b['mv_yi']:,.1f} | {c['mv_yi']:,.1f} | {delta:+,.1f} | "
                f"{b['codes']} → {c['codes']} | {len(d['gone'])}只/{d['gone_mv_yi']:.0f}亿 | "
                f"{len(d['new'])}只/{d['new_mv_yi']:.0f}亿 |"
            )
        lines.append("")
        lines.append("> 市值变化同时受股价与持仓变动影响；判断方向请看「只数变化」和「新进只数」。")
        lines.append("")

        lines.append("## 三、退出前十大明细（按市值）")
        lines.append("")
        for label, _ in ENTITIES:
            d = diffs.get(label)
            if not d or not len(d["gone"]):
                continue
            lines.append(f"**{label}**（{len(d['gone'])} 只 / {d['gone_mv_yi']:.0f} 亿）")
            lines.append("")
            lines.append(_fmt_series(d["gone"], 15))
            lines.append("")
        if not any(len(rec.get("gone", [])) for rec in diffs.values()):
            lines.append("（无）")
            lines.append("")

        lines.append("## 四、新进明细（按市值）")
        lines.append("")
        any_new = False
        for label, _ in ENTITIES:
            d = diffs.get(label)
            if not d or not len(d["new"]):
                continue
            any_new = True
            lines.append(f"**{label}**（{len(d['new'])} 只 / {d['new_mv_yi']:.0f} 亿）")
            lines.append("")
            lines.append(_fmt_series(d["new"], 15))
            lines.append("")
        if not any_new:
            lines.append("（无新进 —— 单边卖出特征）")
            lines.append("")

        lines.append("## 五、持续持有中的加减仓（前 10 / 后 10）")
        lines.append("")
        for label, _ in ENTITIES:
            j = changes.get(label)
            if j is None or not len(j):
                continue
            lines.append(f"**{label}**")
            lines.append("")
            if label in FIXED_POSITION_ENTITIES:
                lines.append("> 该主体持股数常年不变，本表市值变化**全部来自股价波动**，不代表加减仓。")
                lines.append("")
            lines.append("| 加仓(市值增幅) | 变化(亿) | 减持(市值降幅) | 变化(亿) |")
            lines.append("| --- | ---: | --- | ---: |")
            up = j[j["delta_mv_yi"] > 0].head(10).reset_index(drop=True)
            down = j[j["delta_mv_yi"] < 0].tail(10).sort_values("delta_mv_yi").reset_index(drop=True)
            for i in range(max(len(up), len(down), 1)):
                u = f"{up.loc[i, 'name_cur']}" if i < len(up) else ""
                uv = f"{up.loc[i, 'delta_mv_yi']:+.1f}" if i < len(up) else ""
                d = f"{down.loc[i, 'name_cur']}" if i < len(down) else ""
                dv = f"{down.loc[i, 'delta_mv_yi']:+.1f}" if i < len(down) else ""
                lines.append(f"| {u} | {uv} | {d} | {dv} |")
            lines.append("")

    if len(history_rows) > 1:
        lines.append("## 六、季度趋势")
        lines.append("")
        head = "| 报告期 | " + " | ".join(f"{lbl}市值/只数" for lbl, _ in ENTITIES) + " |"
        lines.append(head)
        lines.append("| --- | " + " | ".join("---" for _ in ENTITIES) + " |")
        for q, summ in history_rows:
            cells = [f"{summ[lbl]['mv_yi']:,.0f}亿/{summ[lbl]['codes']}只" for lbl, _ in ENTITIES]
            lines.append(f"| {q[:4]}-{q[4:6]}-{q[6:]} | " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## 七、判读要点")
    lines.append("")
    lines.append("1. **看只数，不看市值**：市值受股价影响，只数减少才是真退出。")
    lines.append("2. **「新进 0 只」是单边卖出信号**：有出无进说明不是调仓，是撤离。")
    lines.append("3. **汇金投资（四大行）常年不变**：它的市值波动等于银行股波动，不代表操作。")
    lines.append("4. **社保是唯一会做主动配置的主体**：它的加减仓方向可当参考，但量级远小于证金。")
    lines.append("5. **ETF 是盲区**：汇金可能通过宽基 ETF 承接，需配合 ETF 份额数据一起看。")
    lines.append("")
    return "\n".join(lines)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="国家队十大流通股东持仓追踪")
    ap.add_argument("--quarter", default=None, help="报告期 yyyymmdd，默认自动识别最新可用季度")
    ap.add_argument("--baseline", default=None, help="对比基线报告期 yyyymmdd，默认取上一季度")
    ap.add_argument("--history", type=int, default=0, help="额外输出最近 N 个季度的趋势表")
    ap.add_argument("--cache-only", action="store_true", help="只用本地缓存，不联网")
    ap.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    ap.add_argument("--out-dir", default=None, help="产物目录，默认 data/strategy_review/national_team_<季度>")
    return ap.parse_args(argv)


def _resolve_quarter(args: argparse.Namespace) -> Optional[str]:
    if args.quarter:
        return args.quarter.replace("-", "")
    for q in _quarter_candidates(pd.Timestamp.now()):
        if _cache_path(q).exists():
            return q
        if args.cache_only:
            continue
        df = fetch_quarter(q)
        if df is not None and len(df):
            return q
    return None


def _prev_quarter(quarter: str) -> str:
    y, md = int(quarter[:4]), quarter[4:]
    idx = _QUARTER_MONTHS.index(md)
    if idx == 0:
        return f"{y - 1}1231"
    return f"{y}{_QUARTER_MONTHS[idx - 1]}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    quarter = _resolve_quarter(args)
    if not quarter:
        print("[error] 没有可用季度数据（可加 --quarter 指定，或先联网拉取）", file=sys.stderr)
        return 2

    cur = fetch_quarter(quarter, force=args.force)
    if cur is None or not len(cur):
        print(f"[error] {quarter} 无数据", file=sys.stderr)
        return 2

    baseline = args.baseline or _prev_quarter(quarter)
    if str(baseline).lower() in ("none", "0", "off"):
        baseline = None
    base_df = None
    if baseline and baseline != quarter:
        base_df = fetch_quarter(baseline)

    cur_sum = summarize(cur)
    base_sum = summarize(base_df) if base_df is not None and len(base_df) else None

    diffs: Dict[str, Dict[str, Any]] = {}
    changes: Dict[str, pd.DataFrame] = {}
    if base_df is not None and len(base_df):
        for label, keyword in ENTITIES:
            diffs[label] = diff_entity(base_df, cur, keyword)
            changes[label] = holding_changes(base_df, cur, keyword)
    else:
        baseline = None if base_sum is None else baseline

    history_rows: List[Tuple[str, Dict[str, Dict[str, Any]]]] = []
    if args.history and args.history > 1:
        quarters = [quarter]
        q = quarter
        for _ in range(args.history - 1):
            q = _prev_quarter(q)
            quarters.append(q)
        for q in reversed(quarters):
            df = cur if q == quarter else fetch_quarter(q)
            if df is not None and len(df):
                history_rows.append((q, summarize(df)))

    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / f"national_team_{quarter}"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = render_report(
        quarter=quarter,
        baseline=baseline if base_df is not None and len(base_df) else None,
        cur_sum=cur_sum,
        base_sum=base_sum,
        diffs=diffs,
        changes=changes,
        history_rows=history_rows,
        cur_rows=len(cur),
    )
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    payload = {
        "quarter": quarter,
        "baseline": baseline if base_df is not None and len(base_df) else None,
        "rows": len(cur),
        "summary": cur_sum,
        "baseline_summary": base_sum,
        "diffs": {
            label: {
                "prev_codes": d["prev_codes"],
                "cur_codes": d["cur_codes"],
                "gone": d["gone"].to_dict("records"),
                "new": d["new"].to_dict("records"),
                "gone_mv_yi": d["gone_mv_yi"],
                "new_mv_yi": d["new_mv_yi"],
            }
            for label, d in diffs.items()
        },
        "history": [{"quarter": q, "summary": s} for q, s in history_rows],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    for label, d in diffs.items():
        for kind in ("gone", "new"):
            if len(d[kind]):
                d[kind].to_csv(out_dir / f"diff_{label}_{kind}.csv", index=False)

    print(f"[done] 报告 -> {out_dir / 'report.md'}")
    for label, _ in ENTITIES:
        s = cur_sum[label]
        if s["codes"]:
            print(f"  {label}: {s['mv_yi']:,.0f}亿 / {s['codes']}只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
