#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日复盘导出：最近 N 天的选股结果 + 已兑现前向收益（1/3/5 日）。

口径与 `scripts/evaluate_signal_snapshot_performance.py` 对齐：
- 入场价（默认 `--entry-mode daily`，DB 优先）= `stock_daily` 中日期 <= signal_date 的最近一根收盘
  （评估器 `--entry-mode daily` 同款；不回落快照价，避免过期尺度/高送转假亏损）
- `--entry-mode snapshot`（兼容旧口径）= 快照 `metrics_payload.close` 优先 → 精确 bar → 最近 <= signal_date 的 bar
- 出场价 = 信号日期后第 k 个交易日收盘（stock_daily 可用 bar）
- 收益率 = (出场 - 入场) / 入场 * 100，保留 2 位
- 输赢口径：|ret| <= 2%（中性带）记 neutral，其余 win/loss
- 成本口径：默认滑点 10bps/边 + 费用 3bps/边 + 换手 5bps/次（一次买卖 ≈31bps），输出 ret{k}_net 列（可用 --slippage-bps 等覆盖）

用法：
    ./.venv-linux/bin/python scripts/daily_review_export.py
    ./.venv-linux/bin/python scripts/daily_review_export.py --days 30 \
        --signals hundred_day_high,daily_slow_rise,trend_leader_unified

输出：
    data/strategy_review/daily_review_latest.csv（可用 --output 覆盖）
说明：
    - 最新一天的选股还没有前向数据，收益列为空属正常；
    - 节假日标签日（无当日 K 线）同样不参与收益计算；
    - 信号来源：默认读 `config/local_strategy_profile.json` 的 include_signals（earnings→earnings_surprise、trend_leader→trend_leader_unified）；`--signals` 可覆盖；无 profile 时回退全部并排除 random_baseline 对照。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_OUT = PROJECT_ROOT / "data" / "strategy_review" / "daily_review_latest.csv"

NEUTRAL_BAND_PCT = 2.0
WINDOWS = (1, 3, 5)
FIELDS = (
    ["signal_date", "signal_type", "code", "name", "entry_close", "cost_bps"]
    + [f"ret{k}" for k in WINDOWS]
    + [f"ret{k}_net" for k in WINDOWS]
    + [f"win{k}" for k in WINDOWS]
    + ["runup5_pct", "drawdown5_pct"]
)


def classify(ret: float | None) -> str:
    if ret is None:
        return ""
    if ret > NEUTRAL_BAND_PCT:
        return "win"
    if ret < -NEUTRAL_BAND_PCT:
        return "loss"
    return "neutral"


def _metrics_close(metrics_payload) -> float | None:
    """优先取快照 metrics_payload.close（与评估器 _coerce_start_price 一致）。"""
    if not metrics_payload:
        return None
    try:
        value = json.loads(str(metrics_payload)).get("close")
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _latest_close_on_or_before(seq: list, sd: str) -> float | None:
    """stock_daily 中日期 <= sd 的最近一根收盘（评估器 get_start_daily 同款语义）。"""
    candidates = [b for b in seq if b[0] <= sd and b[1]]
    if not candidates:
        return None
    value = float(candidates[-1][1])
    return value if value > 0 else None


def _resolve_entry(metrics_payload, seq: list, sd: str, *, entry_mode: str) -> float | None:
    """入场价口径：daily=只用 stock_daily（DB 优先，评估器同款）；snapshot=快照价优先（旧口径）。"""
    if entry_mode == "snapshot":
        entry = _metrics_close(metrics_payload)
        if entry is None:
            entry = _latest_close_on_or_before(seq, sd)
        return entry
    return _latest_close_on_or_before(seq, sd)


SIGNAL_ALIASES = {
    "earnings": "earnings_surprise",
    "trend_leader": "trend_leader_unified",
}


def _profile_signals() -> list[str]:
    """默认信号来源：profile 的 include_signals（映射别名、去掉 exclude）。"""
    try:
        profile_path = PROJECT_ROOT / "config" / "local_strategy_profile.json"
        defaults = (json.loads(profile_path.read_text(encoding="utf-8")) or {}).get("defaults") or {}
        tokens = defaults.get("include_signals") or []
        excluded = {str(item).strip() for item in (defaults.get("exclude_signals") or [])}
        result: list[str] = []
        for token in tokens:
            name = str(token).strip()
            if not name or name in excluded:
                continue
            mapped = SIGNAL_ALIASES.get(name, name)
            if mapped not in result:
                result.append(mapped)
        return result
    except Exception:
        return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="最近 N 天选股结果 + 已兑现前向收益导出")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="sqlite 数据库路径")
    parser.add_argument("--days", type=int, default=21, help="回看自然日数（相对库内最新信号日）")
    parser.add_argument("--signals", default="", help="逗号分隔信号类型过滤；默认全部")
    parser.add_argument(
        "--entry-mode",
        choices=["daily", "snapshot"],
        default="daily",
        help="入场价口径：daily=DB 优先（默认，评估器同款）；snapshot=快照价优先（旧口径）",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUT), help="输出 CSV 路径")
    parser.add_argument("--quiet", action="store_true", help="只写文件，不打印摘要")
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="单边滑点（bps），默认 10")
    parser.add_argument("--fee-bps", type=float, default=3.0, help="单边费用（bps），默认 3")
    parser.add_argument("--turnover-penalty-bps", type=float, default=5.0, help="一次买卖换手惩罚（bps），默认 5")
    args = parser.parse_args(argv)

    total_trade_cost_bps = max(0.0, float(args.turnover_penalty_bps)) + max(
        0.0, 2.0 * (float(args.slippage_bps) + float(args.fee_bps))
    )

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.execute("SELECT MAX(signal_date) FROM kline_signal_snapshot")
    latest = cur.fetchone()[0]
    if not latest:
        print("[daily-review] 快照库为空")
        con.close()
        return 1
    window_start = (date.fromisoformat(str(latest)) - timedelta(days=max(1, args.days))).isoformat()
    signals = [s.strip() for s in str(args.signals or "").split(",") if s.strip()]
    used_profile = False
    if not signals:
        signals = _profile_signals()
        used_profile = bool(signals)

    sql = (
        "SELECT signal_type, signal_date, code, COALESCE(name, ''), metrics_payload "
        "FROM kline_signal_snapshot WHERE signal_date >= ?"
    )
    params: list = [window_start]
    if signals:
        sql += " AND signal_type IN (%s)" % ",".join("?" * len(signals))
        params += signals
    else:
        sql += " AND signal_type NOT LIKE 'random_baseline%'"
    sql += " ORDER BY signal_date DESC, signal_type, code"
    cur.execute(sql, params)
    snapshots = cur.fetchall()
    if not snapshots:
        print(f"[daily-review] {window_start} 以来无快照")
        con.close()
        return 1

    codes = sorted({row[2] for row in snapshots})
    qmarks = ",".join("?" * len(codes))
    # 入场价回退需要信号日之前的 bar（评估器用“最近一条 <= signal_date”），比回看窗口多取 90 天。
    bars_start = (date.fromisoformat(window_start) - timedelta(days=90)).isoformat()
    cur.execute(
        f"SELECT code, date, close, high, low FROM stock_daily "
        f"WHERE code IN ({qmarks}) AND date >= ? ORDER BY code, date",
        codes + [bars_start],
    )
    bars: dict[str, list] = defaultdict(list)
    for code, d, close, high, low in cur.fetchall():
        bars[code].append((str(d), close, high, low))
    con.close()

    rows = []
    for signal_type, signal_date, code, name, metrics_payload in snapshots:
        sd = str(signal_date)
        seq = bars.get(code) or []
        record = {
            "signal_date": sd,
            "signal_type": signal_type,
            "code": code,
            "name": name,
            "cost_bps": round(total_trade_cost_bps, 2),
        }
        entry = _resolve_entry(metrics_payload, seq, sd, entry_mode=str(args.entry_mode))
        fwd = [b for b in seq if b[0] > sd]
        if not entry:
            record.update({k: "" for k in FIELDS if k not in record})
            rows.append(record)
            continue
        record["entry_close"] = round(entry, 3)
        for k in WINDOWS:
            if len(fwd) >= k and entry:
                ret = round((float(fwd[k - 1][1]) - entry) / entry * 100.0, 2)
                record[f"ret{k}"] = ret
                record[f"ret{k}_net"] = round(ret - total_trade_cost_bps / 100.0, 2)
                record[f"win{k}"] = classify(ret)
            else:
                record[f"ret{k}"] = ""
                record[f"ret{k}_net"] = ""
                record[f"win{k}"] = ""
        n5 = min(5, len(fwd))
        if n5 and entry:
            hi = max(float(b[2]) for b in fwd[:n5])
            lo = min(float(b[3]) for b in fwd[:n5])
            record["runup5_pct"] = round((hi - entry) / entry * 100.0, 2)
            record["drawdown5_pct"] = round((lo - entry) / entry * 100.0, 2)
        else:
            record["runup5_pct"] = ""
            record["drawdown5_pct"] = ""
        rows.append(record)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})

    if args.quiet:
        return 0

    print(f"[daily-review] 区间: {window_start} ~ {latest}（共 {len(rows)} 条选股）")
    print(f"[daily-review] 明细: {out_path}")
    print(f"[daily-review] 成本口径: 一次买卖 {round(total_trade_cost_bps, 2)} bps（净列 = 成本后）")
    print(
        "[daily-review] 入场口径: "
        + ("daily（DB 优先，评估器同款）" if str(args.entry_mode) == "daily" else "snapshot（快照价优先，旧口径）")
    )
    if used_profile:
        print(f"[daily-review] 信号来源: profile include_signals -> {', '.join(signals)}")
    else:
        print("[daily-review] 信号来源: 全部（已排除 random_baseline 对照）")
    by_signal: dict[str, list] = defaultdict(list)
    for row in rows:
        by_signal[row["signal_type"]].append(row)
    for sig in sorted(by_signal):
        subset = by_signal[sig]
        dates = sorted({r["signal_date"] for r in subset}, reverse=True)
        print(f"\n== {sig}（{len(subset)} 条，信号日 {dates[0]} ~ {dates[-1]}）")
        for k in WINDOWS:
            wins = sum(1 for r in subset if r.get(f"win{k}") == "win")
            losses = sum(1 for r in subset if r.get(f"win{k}") == "loss")
            done = [r[f"ret{k}"] for r in subset if r.get(f"ret{k}") != ""]
            total_cls = wins + losses
            rate = f"{wins / total_cls * 100:.1f}%" if total_cls else "--"
            avg = f"{sum(done) / len(done):+.2f}%" if done else "--"
            done_net = [r[f"ret{k}_net"] for r in subset if r.get(f"ret{k}_net") not in ("", None)]
            avg_net = f"{sum(done_net) / len(done_net):+.2f}%" if done_net else "--"
            print(f"   T+{k}: 已兑现 {len(done):>4} 条 | 胜率(不含中性) {rate:>6} | 均值 {avg}（净 {avg_net}）")
        latest_date = dates[0]
        day_rows = [r for r in subset if r["signal_date"] == latest_date and r.get("ret1") not in ("", None)]
        if day_rows:
            day_rows.sort(key=lambda r: r["ret1"], reverse=True)
            top = "，".join(f"{r['name']}({r['ret1']:+.2f}%)" for r in day_rows[:3])
            bottom = "，".join(f"{r['name']}({r['ret1']:+.2f}%)" for r in reversed(day_rows[-3:]))
            print(f"   {latest_date} T+1 最佳: {top}")
            print(f"   {latest_date} T+1 最差: {bottom}")
        else:
            print(f"   {latest_date} 暂无已兑现 T+1 数据（最新信号日）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
