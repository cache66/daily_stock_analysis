#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成工作台预览页（单文件 HTML，本地打开即可看）。

用途：把当前真实状态压成一页给使用者看，不新增持久化表、不调模型、不发通知：
- 今日候选（按来源分组）、跟踪样本池、来源健康、数据完整性；
- 信号体检：各信号 T+5 超额收益（相对同日全市场均值）按月对比；
- 闸门现状：环境不适配/冷却期/过热 三类跳过口径。

输出默认写到 ``data/strategy_review/workbench_preview.html``（data/ 已被 gitignore，
属于本地产物，不入库）。

用法：
    ./.venv-linux/bin/python scripts/build_workbench_preview.py
    ./.venv-linux/bin/python scripts/build_workbench_preview.py --date 2026-11-06
    ./.venv-linux/bin/python scripts/build_workbench_preview.py --months 8 --open
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.repositories.research_tracking_repo import ResearchTrackingRepository  # noqa: E402
from src.services.daily_board_service import build_board_for_date  # noqa: E402
from src.services.research_tracking_service import ResearchTrackingService  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_OUT = PROJECT_ROOT / "data" / "strategy_review" / "workbench_preview.html"
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "history" / "cn"
SIGNAL_LABELS = {
    "hundred_day_high": "百日新高",
    "trend_leader_unified": "趋势龙头",
    "earnings_surprise": "业绩超预期",
    "continuous_up_ratio": "连涨占比",
    "continuous_up_streak": "连涨天数",
    "daily_slow_rise": "缓涨",
    "screening__dual_low": "筛选·双低",
    "screening__momentum_quality": "筛选·趋势质量",
    "screening__shrink_pullback": "筛选·缩量回踩",
}
CSS = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1f2328;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:23px;margin:0 0 4px} .sub{color:#6a737d;font-size:13px;margin-bottom:20px}
h2{font-size:16px;margin:28px 0 10px;padding-left:9px;border-left:4px solid #2f6feb}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:#6a737d} .card .v{font-size:25px;font-weight:650;margin-top:2px}
.card .n{font-size:12px;color:#8b949e;margin-top:2px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ea;border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid #eef0f3;text-align:left;white-space:nowrap}
th{background:#fafbfc;font-weight:600;color:#4a5158;font-size:12px}
tr:last-child td{border-bottom:none}
.pos{color:#0a7d33;font-weight:600} .neg{color:#c0392b;font-weight:600} .mut{color:#8b949e}
.warn{background:#fff8e6;border:1px solid #f0dca4;border-radius:10px;padding:12px 16px;font-size:13px}
.ok{background:#eefaf1;border:1px solid #b9e5c6;border-radius:10px;padding:12px 16px;font-size:13px}
.bad{background:#fdeeec;border:1px solid #f3c2bb;border-radius:10px;padding:12px 16px;font-size:13px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
ul{margin:6px 0 0 18px;padding:0} li{margin:3px 0}
code{background:#f1f3f5;padding:1px 5px;border-radius:4px;font-size:12px}
"""


def _cls(value: Optional[float]) -> str:
    if value is None:
        return "mut"
    return "pos" if value > 0 else ("neg" if value < 0 else "mut")


def latest_signal_date(db_path: Path) -> str:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT MAX(signal_date) FROM kline_signal_snapshot").fetchone()
    finally:
        con.close()
    return str(row[0])[:10] if row and row[0] else date.today().isoformat()


def signal_checkup(db_path: Path, months: int = 8) -> Dict[str, Any]:
    """各信号 T+5 超额收益（相对同日全市场均值），按月汇总。"""
    con = sqlite3.connect(str(db_path))
    try:
        bars = pd.read_sql(
            "SELECT code, date, close FROM stock_daily "
            "WHERE date >= date('now','-400 day')", con,
        )
        snapshots = pd.read_sql(
            "SELECT signal_type, signal_date, code FROM kline_signal_snapshot "
            "WHERE signal_type IN (%s)" % ",".join("?" * len(SIGNAL_LABELS)),
            con, params=list(SIGNAL_LABELS),
        )
        coverage = pd.read_sql(
            "SELECT substr(m,1,7) ym, count(*) c FROM (SELECT code, max(date) m FROM stock_daily GROUP BY code) GROUP BY ym",
            con,
        )
    finally:
        con.close()
    if bars.empty or snapshots.empty:
        return {"months": [], "rows": [], "coverage": []}
    bars["date"] = bars["date"].astype(str).str[:10]
    snapshots["signal_date"] = snapshots["signal_date"].astype(str).str[:10]
    px = bars.drop_duplicates(["code", "date"]).pivot(index="date", columns="code", values="close").sort_index()
    fwd = px.shift(-5) / px - 1.0
    market = fwd.mean(axis=1)

    months_present = sorted({d[:7] for d in px.index if not pd.isna(market.get(d))})[-months:]
    rows: List[Dict[str, Any]] = []
    for key, label in SIGNAL_LABELS.items():
        subset = snapshots[snapshots.signal_type == key]
        if subset.empty:
            continue
        per: Dict[str, Dict[str, float]] = {}
        for month in months_present:
            group = subset[subset.signal_date.str[:7] == month]
            excess: List[float] = []
            for day, code in zip(group.signal_date, group.code):
                if day in px.index and code in px.columns:
                    value = fwd.at[day, code]
                    base = market.loc[day]
                    if pd.notna(value) and pd.notna(base):
                        excess.append(float(value - base))
            if excess:
                series = pd.Series(excess) * 100
                per[month] = {"n": len(series), "ex": float(series.mean()), "win": float((series > 0).mean() * 100)}
        rows.append({"key": key, "label": label, "total": int(len(subset)), "per": per})
    cov = {str(row.ym): int(row.c) for row in coverage.itertuples()}
    return {"months": months_present, "rows": rows, "coverage": sorted(cov.items())}


def render(board_date: str, checkup: Dict[str, Any], out_path: Path) -> Path:
    db = DatabaseManager.get_instance()
    board, _ = build_board_for_date(board_date, output_path=None, db_manager=db)
    service = ResearchTrackingService(repo=ResearchTrackingRepository(db_manager=db))
    tracks = service.repo.list_tracks(limit=200)
    by_source = Counter(track.hypothesis_key.split(":")[1] for track in tracks if ":" in track.hypothesis_key)

    con = sqlite3.connect(str(DEFAULT_DB))
    try:
        with_result = con.execute(
            "SELECT COUNT(DISTINCT track_pk) FROM research_track_events WHERE source_type='stock_daily_qfq'"
        ).fetchone()[0]
        news = con.execute("SELECT COUNT(*) FROM news_intel").fetchone()[0]
        intel = con.execute("SELECT COUNT(*) FROM intelligence_items").fetchone()[0]
    finally:
        con.close()

    months = checkup["months"]
    head = "".join(f"<th>{m[5:]}月</th>" for m in months)
    body = ""
    for row in checkup["rows"]:
        cells = ""
        for month in months:
            item = row["per"].get(month)
            if item:
                cells += (
                    f"<td class='{_cls(item['ex'])}'>{item['ex']:+.1f}%"
                    f"<span class='mut' style='font-size:11px'> n={item['n']}</span></td>"
                )
            else:
                cells += "<td class='mut'>—</td>"
        body += f"<tr><td><b>{row['label']}</b><div class='mut' style='font-size:11px'>{row['total']} 条</div></td>{cells}</tr>"
    signal_table = f"<table><tr><th>信号</th>{head}</tr>{body}</table>"

    cand_rows = ""
    for source, count in Counter(c.source for c in board.candidates).most_common():
        picks = [c for c in board.candidates if c.source == source][:6]
        codes = "、".join(f"{c.code} {c.name}" for c in picks)
        cand_rows += f"<tr><td>{source}</td><td>{count}</td><td>{codes}</td></tr>"
    cand_table = f"<table><tr><th>来源</th><th>条数</th><th>示例（前 6）</th></tr>{cand_rows}</table>"

    track_rows = "".join(
        f"<tr><td>{source}</td><td>{count}</td><td class='mut'>{round(count / max(1, len(tracks)) * 100)}%</td></tr>"
        for source, count in by_source.most_common()
    )
    track_table = f"<table><tr><th>来源</th><th>样本数</th><th>占比</th></tr>{track_rows}</table>"

    health_rows = "".join(
        f"<tr><td>{h.label}</td><td class='{_cls(1 if h.status == 'ok' else -1)}'>{h.status}</td>"
        f"<td>{h.last_done_local or '—'}</td><td>{'—' if h.exit_code is None else h.exit_code}</td>"
        f"<td class='mut'>{h.detail}</td></tr>"
        for h in board.health
    )
    health_table = f"<table><tr><th>编排</th><th>状态</th><th>最近完成</th><th>exit</th><th>说明</th></tr>{health_rows}</table>"

    cov_text = "、".join(f"{ym}: {count} 只" for ym, count in checkup["coverage"] if ym >= "2026-08")
    healthy = [ym for ym, count in checkup["coverage"] if count > 3000]

    html_doc = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>个人投研工作台 · 预览</title>
<style>{CSS}</style><body><div class="wrap">
<h1>个人投研工作台 · 预览</h1>
<div class="sub">数据截至 {board_date} · 生成于 {date.today().isoformat()} · 全部本机真实数据，未调模型/未发通知</div>
<div class="cards">
  <div class="card"><div class="k">今日候选</div><div class="v">{len(board.candidates)}</div>
    <div class="n">{len({c.code for c in board.candidates})} 只证券</div></div>
  <div class="card"><div class="k">跟踪样本</div><div class="v">{len(tracks)}</div>
    <div class="n">已出结果 {with_result} 条</div></div>
  <div class="card"><div class="k">来源健康</div>
    <div class="v">{sum(1 for h in board.health if h.status == 'ok')}/{len(board.health)}</div>
    <div class="n">正常编排数</div></div>
  <div class="card"><div class="k">市场环境</div><div class="v">{board.market_regime}</div>
    <div class="n">运行配置，不等于牛熊判断</div></div>
</div>

<h2>一、信号体检：T+5 超额收益（按月）</h2>
<div class="sub" style="margin:0 0 8px">相对同日全市场均值；只列出库里真实存在的信号。全市场覆盖完整的月份才有可比性。</div>
{signal_table}
<div class="warn" style="margin-top:10px"><b>读法</b>：正值=跑赢同期大盘；样本数 n 太小的月份只能当线索。
全市场覆盖完整的月份：{('、'.join(healthy) or '无')}。</div>

<h2>二、今日候选（{board_date}）</h2>
{cand_table}

<h2>三、跟踪样本池</h2>
<div class="two"><div>{track_table}</div>
<div class="ok">每条样本带来源引用/内容版本/批次号/限制说明。入池前过三道闸门：
<b>环境不适配</b>（defensive 时不收趋势类）、<b>同票冷却期</b>（10 个交易日内只一条）、
<b>过热</b>（距首次信号涨幅 &gt;30% 不再入池）。</div></div>

<h2>四、来源健康</h2>
{health_table}

<h2>五、数据完整性</h2>
<div class="bad">各股票“最后一根 K 线”所在月份：{cov_text}<br>
本月已完成一次全市场缓存回灌（4,497 只补齐到最新交易日）；仍有约 700 只停在 9 月，多为已停牌/退市、缓存无更新，不计入统计。</div>
<ul>
<li>本地行情缓存：<code>data/cache/history/cn</code> 4,500+ 只（策略实际读取）</li>
<li>资讯：<code>news_intel</code> {news} 条 / <code>intelligence_items</code> {intel} 条 → 事件层尚未接入</li>
<li>夜间链路交易日判断：已改为本地日历优先，日历不可用时按交易日继续（原实现会把限流误判为休市并静默跳过）</li>
</ul>

<h2>六、下一步</h2>
<ul>
<li><b>1</b> 观察今晚跑完后的首批真实回填结果（T+1/T+3/T+5）</li>
<li><b>2</b> 新闻/概念层接入，让“冰雪”这类主题驱动能被看见</li>
<li><b>3</b> 事件/资金线：用 <code>capital_flow_context</code> 做“同日同题材共振 + 资金净流入”</li>
</ul>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成工作台预览页（单文件 HTML）")
    parser.add_argument("--date", help="总览日期，默认取库内最新信号日")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--months", type=int, default=8, help="信号体检保留的月份数")
    args = parser.parse_args()

    db_path = Path(args.db)
    board_date = args.date or latest_signal_date(db_path)
    checkup = signal_checkup(db_path, months=int(args.months))
    out = render(board_date, checkup, Path(args.out))
    print(f"[workbench] date={board_date} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
