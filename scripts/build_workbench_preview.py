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
import json
import sqlite3
import sys
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
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1f2328;line-height:1.6}
.wrap{max-width:1400px;margin:0 auto;padding:20px 18px 50px}
h1{font-size:22px;margin:0 0 4px} .sub{color:#6a737d;font-size:13px;margin-bottom:16px}
h2{font-size:16px;margin:24px 0 10px;padding-left:9px;border-left:4px solid #2f6feb}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:12px 14px}
.card .k{font-size:12px;color:#6a737d} .card .v{font-size:24px;font-weight:650;margin-top:2px}
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
/* ---- 交互区 ---- */
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}
.chip{border:1px solid #d8dde3;background:#fff;border-radius:14px;padding:3px 11px;font-size:12px;cursor:pointer;color:#4a5158}
.chip.on{background:#2f6feb;border-color:#2f6feb;color:#fff}
input.search{border:1px solid #d8dde3;border-radius:8px;padding:5px 10px;font-size:13px;min-width:180px}
.split{display:grid;grid-template-columns:minmax(320px,420px) 1fr;gap:14px;align-items:start}
.list{background:#fff;border:1px solid #e3e6ea;border-radius:10px;max-height:620px;overflow:auto}
.li{padding:9px 12px;border-bottom:1px solid #f0f2f4;cursor:pointer;font-size:13px}
.li:hover{background:#f7f9fc}
.li.on{background:#eaf1ff;border-left:3px solid #2f6feb;padding-left:9px}
.li .code{font-weight:650;margin-right:6px}
.li .meta{color:#8b949e;font-size:11px}
.detail{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px 18px;min-height:320px}
.detail h3{margin:0 0 4px;font-size:17px}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:13px;margin:10px 0}
.kv .k{color:#6a737d}
.ev{font-size:12px;color:#4a5158;background:#fafbfc;border:1px solid #eef0f3;border-radius:8px;padding:10px;margin-top:8px;word-break:break-all}
.flag{display:inline-block;background:#fff2f0;color:#c0392b;border:1px solid #ffccc7;border-radius:10px;padding:0 7px;font-size:11px;margin-right:4px}
.empty{color:#8b949e;font-size:13px;padding:26px 0;text-align:center}
.spark{display:block;margin-top:6px}
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

    con = sqlite3.connect(str(DEFAULT_DB))
    try:
        with_result = con.execute(
            "SELECT COUNT(DISTINCT track_pk) FROM research_track_events WHERE source_type='stock_daily_qfq'"
        ).fetchone()[0]
        news = con.execute("SELECT COUNT(*) FROM news_intel").fetchone()[0]
        intel = con.execute("SELECT COUNT(*) FROM intelligence_items").fetchone()[0]
        outcome_rows = con.execute(
            "SELECT t.code, t.hypothesis_key, e.payload_json, e.quality "
            "FROM research_track_events e JOIN research_tracks t ON t.id = e.track_pk "
            "WHERE e.source_type='stock_daily_qfq' ORDER BY e.id DESC"
        ).fetchall()
        timeline = con.execute(
            "SELECT t.code, e.event_type, e.observed_at, COALESCE(e.summary,'') "
            "FROM research_track_events e JOIN research_tracks t ON t.id = e.track_pk "
            "ORDER BY e.id DESC LIMIT 400"
        ).fetchall()
    finally:
        con.close()

    seen = set()
    outcomes: List[Dict[str, Any]] = []
    for code, key, payload, quality in outcome_rows:
        if code in seen:
            continue
        seen.add(code)
        data = json.loads(payload or "{}")
        outcomes.append({
            "code": code,
            "signal_date": str(key).rsplit(":", 1)[-1],
            "source": str(key).split(":")[1] if ":" in str(key) else "",
            "entry_date": data.get("entry_date"),
            "ret1": data.get("ret1"),
            "ret3": data.get("ret3"),
            "ret5": data.get("ret5"),
            "quality": quality,
            "missing": data.get("missing_dates") or [],
        })
    rets = [o["ret1"] for o in outcomes if o.get("ret1") is not None]
    outcome_stats = {
        "n": len(rets),
        "mean": (sum(rets) / len(rets) * 100) if rets else None,
        "win": (sum(1 for x in rets if x > 0) / len(rets) * 100) if rets else None,
        "best": max(rets) * 100 if rets else None,
        "worst": min(rets) * 100 if rets else None,
    }

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

    health_rows = "".join(
        f"<tr><td>{h.label}</td><td class='{_cls(1 if h.status == 'ok' else -1)}'>{h.status}</td>"
        f"<td>{h.last_done_local or '—'}</td><td>{'—' if h.exit_code is None else h.exit_code}</td>"
        f"<td class='mut'>{h.detail}</td></tr>"
        for h in board.health
    )
    health_table = f"<table><tr><th>编排</th><th>状态</th><th>最近完成</th><th>exit</th><th>说明</th></tr>{health_rows}</table>"

    cov_text = "、".join(f"{ym}: {count} 只" for ym, count in checkup["coverage"] if ym >= "2026-08")
    healthy = [ym for ym, count in checkup["coverage"] if count > 3000]

    def _pct(value):
        if value is None:
            return "—"
        return f"{value * 100:+.2f}%"

    outcome_rows_html = "".join(
        f"<tr><td>{o['code']}</td><td>{o['source'].replace('screening__', '')}</td><td>{o['signal_date']}</td>"
        f"<td class='{_cls(o['ret1'])}'>{_pct(o['ret1'])}</td>"
        f"<td class='{_cls(o['ret3'])}'>{_pct(o['ret3'])}</td>"
        f"<td class='{_cls(o['ret5'])}'>{_pct(o['ret5'])}</td>"
        f"<td class='mut'>{'缺 ' + ','.join(o['missing'][:3]) if o['missing'] else '检查点完整'}</td></tr>"
        for o in sorted(outcomes, key=lambda item: (item["ret1"] is None, -(item["ret1"] or 0)))
    )
    outcome_table = (
        "<table><tr><th>代码</th><th>来源</th><th>信号日</th><th>T+1</th><th>T+3</th><th>T+5</th><th>缺失检查点</th></tr>"
        + (outcome_rows_html or "<tr><td colspan='7' class='mut'>暂无回填结果</td></tr>")
        + "</table>"
    )
    timeline_by_code: Dict[str, List[str]] = {}
    for code, event_type, observed_at, summary in timeline:
        label = f"{event_type}" + (f" · {str(observed_at)[:16]}" if observed_at else "") + (f" · {summary[:60]}" if summary else "")
        timeline_by_code.setdefault(code, []).append(label)
    track_detail_rows = "".join(
        "<tr><td>{code}</td><td>{sig}</td><td>{src}</td><td>{status}</td><td>{due}</td><td><details><summary>{n} 条事件</summary>"
        "<div class='ev' style='white-space:normal'>{events}</div></details></td></tr>".format(
            code=track.code,
            sig=str(track.hypothesis_key).rsplit(":", 1)[-1],
            src=str(track.hypothesis_key).split(":")[1] if ":" in str(track.hypothesis_key) else "",
            status=track.status,
            due=str(track.next_review_at)[:10] if track.next_review_at else "—",
            n=len(timeline_by_code.get(str(track.code), [])),
            events="<br>".join(timeline_by_code.get(str(track.code), [])[:8]) or "（无事件）",
        )
        for track in tracks[:40]
    )
    track_table = (
        "<table><tr><th>代码</th><th>信号日</th><th>来源</th><th>状态</th><th>到期</th><th>事件</th></tr>"
        + track_detail_rows
        + "</table>"
    )

    # ---- 交互数据：候选详情（含近 30 日收盘序列，用于画迷你走势） ----
    def _closes(code: str, limit: int = 30):
        path = CACHE_DIR / f"{code}.csv"
        if not path.is_file():
            return []
        try:
            frame = pd.read_csv(path).tail(limit)
        except (OSError, pd.errors.ParserError):
            return []
        return [
            {"d": str(row.date)[:10], "c": round(float(row.close), 3)}
            for row in frame.itertuples() if pd.notna(row.close)
        ]

    track_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for track in tracks:
        track_by_code.setdefault(str(track.code), []).append({
            "signal_date": str(track.hypothesis_key).rsplit(":", 1)[-1],
            "source": str(track.hypothesis_key).split(":")[1] if ":" in track.hypothesis_key else "",
            "status": track.status,
            "next_review_at": str(track.next_review_at)[:10] if track.next_review_at else "",
        })

    candidates_payload = []
    for cand in board.candidates:
        candidates_payload.append({
            "code": cand.code,
            "name": cand.name,
            "source": cand.source,
            "reason": cand.reason,
            "flags": list(cand.flags),
            "batch_ready": bool(cand.batch_ready),
            "quality": cand.quality,
            "limitation": cand.limitation,
            "batch_ref": cand.batch_ref,
            "source_ref": cand.source_ref,
            "source_version": cand.source_version,
            "observed_at": str(cand.observed_at or ""),
            "metrics": cand.source_snapshot.get("metrics", {}) if cand.source_snapshot else {},
            "closes": _closes(cand.code),
            "tracks": track_by_code.get(cand.code, []),
        })
    payload_json = json.dumps(
        {"date": board_date, "regime": board.market_regime, "candidates": candidates_payload},
        ensure_ascii=False,
    ).replace("</", "<\\/")

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

<h2>二、今日候选（{board_date}）—— 点击左侧查看详情</h2>
<div class="toolbar">
  <input class="search" id="q" placeholder="搜代码 / 名称 / 理由" autocomplete="off">
  <span id="chips"></span>
  <span class="mut" id="count"></span>
</div>
<div class="split">
  <div class="list" id="list"></div>
  <div class="detail" id="detail"><div class="empty">从左侧选一只看详情（打分依据 / 来源版本 / 近 30 日走势 / 关联跟踪）</div></div>
</div>
<div class="warn" style="margin-top:10px">小提醒：当前候选里 <b>dual_low（双低）</b> 天然偏向银行——它要求 PE 0~15 且 PB 0~2，银行最符合条件的行业。
所以“怎么都是银行”不是 bug，是这条策略的定义；要分散就得靠其他来源或加行业上限。</div>
<script type="application/json" id="wb-data">{payload_json}</script>
""" + """
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('wb-data').textContent);
  var cands = DATA.candidates || [];
  var state = { source: '*', q: '', sel: null };

  var chipsEl = document.getElementById('chips');
  var listEl = document.getElementById('list');
  var detailEl = document.getElementById('detail');
  var countEl = document.getElementById('count');

  var bySource = {};
  cands.forEach(function(c){ bySource[c.source] = (bySource[c.source]||0)+1; });
  var sources = Object.keys(bySource).sort(function(a,b){ return bySource[b]-bySource[a]; });

  function chip(label, key, n){
    var b = document.createElement('button');
    b.className = 'chip' + (state.source === key ? ' on' : '');
    b.textContent = label + (n ? ' ' + n : '');
    b.onclick = function(){ state.source = key; renderChips(); renderList(); };
    return b;
  }
  function renderChips(){
    chipsEl.innerHTML = '';
    chipsEl.appendChild(chip('全部', '*', cands.length));
    sources.forEach(function(s){ chipsEl.appendChild(chip(s, s, bySource[s])); });
  }
  function filtered(){
    var q = state.q.trim().toLowerCase();
    return cands.filter(function(c){
      if (state.source !== '*' && c.source !== state.source) return false;
      if (!q) return true;
      return (c.code + ' ' + c.name + ' ' + c.reason + ' ' + c.source).toLowerCase().indexOf(q) >= 0;
    });
  }
  function row(c){
    var d = document.createElement('div');
    d.className = 'li' + (state.sel === c.code + '|' + c.source ? ' on' : '');
    d.innerHTML = '<span class="code">' + c.code + '</span>' + (c.name||'') +
      '<div class="meta">' + c.source + (c.flags && c.flags.length ? ' · ' + c.flags.join('、') : '') + '</div>';
    d.onclick = function(){ state.sel = c.code + '|' + c.source; renderList(); renderDetail(c); };
    return d;
  }
  function renderList(){
    var rows = filtered();
    listEl.innerHTML = '';
    if (!rows.length) { listEl.innerHTML = '<div class="empty">没有匹配的候选</div>'; }
    rows.forEach(function(c){ listEl.appendChild(row(c)); });
    countEl.textContent = '显示 ' + rows.length + ' / ' + cands.length;
  }
  function spark(closes){
    if (!closes || closes.length < 2) return '<div class="mut">（无本地行情序列）</div>';
    var w = 380, h = 90, pad = 4;
    var xs = closes.map(function(p){ return p.c; });
    var min = Math.min.apply(null, xs), max = Math.max.apply(null, xs);
    var span = (max - min) || 1;
    var pts = xs.map(function(v,i){
      var x = pad + i * (w - pad*2) / (xs.length - 1);
      var y = h - pad - (v - min) / span * (h - pad*2);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var up = xs[xs.length-1] >= xs[0];
    var color = up ? '#0a7d33' : '#c0392b';
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">' +
      '<polyline fill="none" stroke="' + color + '" stroke-width="1.6" points="' + pts + '"/></svg>' +
      '<div class="mut" style="font-size:11px">' + closes[0].d + ' → ' + closes[closes.length-1].d +
      '（' + (((xs[xs.length-1]/xs[0])-1)*100).toFixed(1) + '%）</div>';
  }
  function kv(k, v){ return '<div class="k">' + k + '</div><div>' + (v === undefined || v === null || v === '' ? '—' : v) + '</div>'; }
  function renderDetail(c){
    var m = c.metrics || {};
    var pick = ['close','window_high','new_high_window','rank','score','reason','industry','run_id','history_source','risk_level'];
    var mrows = pick.filter(function(k){ return m[k] !== undefined && m[k] !== null; })
      .map(function(k){ return kv(k, String(m[k]).slice(0,120)); }).join('');
    var flags = (c.flags||[]).map(function(f){ return '<span class="flag">' + f + '</span>'; }).join('') || '<span class="mut">无</span>';
    var tracks = (c.tracks||[]).length
      ? (c.tracks||[]).map(function(t){
          return '<li>' + t.signal_date + ' · ' + t.source + ' · <b>' + t.status + '</b>' +
            (t.next_review_at ? ' · 到期 ' + t.next_review_at : '') + '</li>';
        }).join('')
      : '<li class="mut">该股暂无跟踪记录（未入池或已被闸门跳过）</li>';
    detailEl.innerHTML =
      '<h3>' + c.code + ' ' + (c.name||'') + '</h3>' +
      '<div class="mut">' + c.source + ' · 批次证据 ' + (c.batch_ready ? '完整（可自动入池）' : '不足（仅供阅读）') + '</div>' +
      '<div style="margin-top:8px">' + flags + '</div>' +
      '<div class="kv" style="margin-top:12px">' + kv('入选理由', c.reason) + mrows + '</div>' +
      '<div style="margin-top:12px"><b style="font-size:13px">近 30 日走势</b>' + spark(c.closes) + '</div>' +
      '<div style="margin-top:12px"><b style="font-size:13px">关联跟踪</b><ul>' + tracks + '</ul></div>' +
      '<div class="ev"><div><b>来源引用</b>：' + (c.source_ref || '—') + '</div>' +
      '<div><b>内容版本</b>：' + (c.source_version || '—') + '</div>' +
      '<div><b>批次号</b>：' + (c.batch_ref || '—') + '</div>' +
      '<div><b>收录时点(UTC)</b>：' + (c.observed_at || '—') + '</div>' +
      '<div><b>质量</b>：' + (c.quality || '—') + '</div>' +
      '<div><b>限制</b>：' + (c.limitation || '—') + '</div></div>';
  }
  document.getElementById('q').addEventListener('input', function(e){
    state.q = e.target.value; renderList();
  });
  renderChips(); renderList();
  if (cands.length) { state.sel = cands[0].code + '|' + cands[0].source; renderList(); renderDetail(cands[0]); }
})();
</script>
""" + f"""

<h2>三、回填结果（真实 T+1，截至 {board_date}）</h2>
<div class="cards">
  <div class="card"><div class="k">已回填样本</div><div class="v">{outcome_stats['n']}</div><div class="n">共 {len(outcomes)} 条有结果</div></div>
  <div class="card"><div class="k">T+1 均值</div><div class="v {_cls(outcome_stats['mean'])}">{('' if outcome_stats['mean'] is None else '%+.2f%%' % outcome_stats['mean'])}</div><div class="n">相对 D0 收盘</div></div>
  <div class="card"><div class="k">T+1 胜率</div><div class="v">{('' if outcome_stats['win'] is None else '%.0f%%' % outcome_stats['win'])}</div><div class="n">收盘为正占比</div></div>
  <div class="card"><div class="k">最好 / 最差</div><div class="v" style="font-size:18px">{('—' if outcome_stats['best'] is None else '%+.1f%% / %+.1f%%' % (outcome_stats['best'], outcome_stats['worst']))}</div><div class="n">单票 T+1</div></div>
</div>
{outcome_table}
<div class="warn" style="margin-top:10px">T+3/T+5 需要更多交易日，目前按日历留空；quality=partial 表示窗口未走完，不是漏数。</div>

<h2>四、跟踪样本池（点开事件列看时间线）</h2>
{track_table}
<div class="ok" style="margin-top:10px">每条样本带来源引用/内容版本/批次号/限制说明。入池前过三道闸门：
<b>环境不适配</b>（defensive 时不收趋势类）、<b>同票冷却期</b>（10 个交易日内只一条）、
<b>过热</b>（距首次信号涨幅 &gt;30% 不再入池）。</div>

<h2>五、来源健康</h2>
{health_table}

<h2>六、数据完整性</h2>
<div class="bad">各股票“最后一根 K 线”所在月份：{cov_text}<br>
本月已完成一次全市场缓存回灌（4,497 只补齐到最新交易日）；仍有约 700 只停在 9 月，多为已停牌/退市、缓存无更新，不计入统计。</div>
<ul>
<li>本地行情缓存：<code>data/cache/history/cn</code> 4,500+ 只（策略实际读取）</li>
<li>资讯：<code>news_intel</code> {news} 条 / <code>intelligence_items</code> {intel} 条 → 事件层尚未接入</li>
<li>夜间链路交易日判断：已改为本地日历优先，日历不可用时按交易日继续（原实现会把限流误判为休市并静默跳过）</li>
</ul>

<h2>七、下一步</h2>
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
