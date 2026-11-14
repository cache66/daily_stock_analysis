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
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repositories.research_tracking_repo import ResearchTrackingRepository  # noqa: E402
from src.services.daily_board_service import build_board_for_date  # noqa: E402
from src.services.industry_profile import load_industry_map, profile_of  # noqa: E402
from src.services.research_tracking_service import ResearchTrackingService  # noqa: E402
from src.services.research_sample import sample_kind  # noqa: E402
from src.services.track_admission_service import gate_candidates  # noqa: E402
from src.services.replay_stats_service import (  # noqa: E402
    collect_samples, signal_of, _load_market_data, _annotate_samples,
)
from src.storage import DatabaseManager  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_OUT = PROJECT_ROOT / "data" / "strategy_review" / "workbench_preview.html"
DEFAULT_UI_CONFIG = PROJECT_ROOT / "config" / "workbench_ui.json"
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


def signal_checkup(db_path: Path, months: int = 8, *, as_of: Optional[str] = None) -> Dict[str, Any]:
    """与结果统计共用严格日历/基准，按截止日描述历史信号，不授资格。"""
    cutoff = as_of or date.today().isoformat()
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        rows = con.execute(
            "SELECT signal_type, signal_date, code FROM kline_signal_snapshot "
            "WHERE signal_date <= ? ORDER BY signal_date, signal_type, code", (cutoff,),
        ).fetchall()
        coverage = con.execute(
            "SELECT substr(m,1,7), count(*) FROM (SELECT code, max(date) m "
            "FROM stock_daily WHERE date <= ? GROUP BY code) GROUP BY substr(m,1,7)", (cutoff,),
        ).fetchall()
    items = [{"signal_date": str(day)[:10], "code": code, "source": source} for source, day, code in rows
             if source in SIGNAL_LABELS]
    data = _load_market_data(db_path, items, as_of=cutoff)
    sessions = data["sessions"]
    positions = {day: i for i, day in enumerate(sessions)}
    prices = {code: {bar[0]: bar[3] for bar in bars} for code, bars in data["bars"].items()}
    monthly: Dict[str, Dict[str, List[float]]] = {}
    for item in items:
        day, code = item["signal_date"], item["code"]
        index = positions.get(day)
        series = prices.get(code, {})
        base = series.get(day)
        benchmark = data["market"].get((day, 5))
        if index is None or index + 5 >= len(sessions) or not base or benchmark is None:
            continue
        later = series.get(sessions[index + 5])
        if later:
            monthly.setdefault(item["source"], {}).setdefault(day[:7], []).append((later / base - 1) * 100 - benchmark)
    all_months = sorted({month for values in monthly.values() for month in values})[-months:]
    result = []
    for source, values in monthly.items():
        per = {month: {"n": len(v), "ex": sum(v) / len(v), "win": sum(x > 0 for x in v) / len(v) * 100}
               for month, v in values.items() if month in all_months}
        result.append({"key": source, "label": SIGNAL_LABELS[source], "total": sum(len(v) for v in values.values()), "per": per})
    return {"months": all_months, "rows": result, "coverage": coverage}


def _load_bars(db, codes, board_date: str, keep: int = 120) -> Dict[str, List[Dict[str, float]]]:
    """取候选最近 120 个交易日的日线（从库里读，口径与回填一致）。"""
    codes = [str(code) for code in dict.fromkeys(codes)]
    if not codes:
        return {}
    result: Dict[str, List[Dict[str, float]]] = {}
    with db.get_session() as session:
        db_path = Path(session.get_bind().url.database)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        chunk = 400
        for start in range(0, len(codes), chunk):
            part = codes[start:start + chunk]
            rows = con.execute(
                "SELECT code, date, open, high, low, close, amount FROM stock_daily "
                "WHERE code IN (%s) AND date <= ? ORDER BY code, date" % ",".join("?" * len(part)),
                (*part, board_date),
            ).fetchall()
            for code, day, open_, high, low, close, amount in rows:
                if close is None:
                    continue
                result.setdefault(code, []).append({
                    "date": str(day)[:10], "open": open_, "high": high, "low": low,
                    "close": close, "amount": amount,
                })
    finally:
        con.close()
    return {code: bars[-keep:] for code, bars in result.items() if bars}


def _trade_plan(bars: List[Dict[str, float]]) -> Dict[str, Any]:
    """从真实日线给出参考动作/止损/目标（不是买卖建议，只是把风险量化）。"""
    if len(bars) < 25:
        return {}
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"] or b["close"]) for b in bars]
    lows = [float(b["low"] or b["close"]) for b in bars]
    last = closes[-1]
    if last <= 0:
        return {}
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    swings = [h - l for h, l in zip(highs[-14:], lows[-14:])]
    atr = sum(swings) / len(swings)
    low10 = min(lows[-10:])
    high_window = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    stop = min(low10, last - 2 * atr)
    if stop >= last * 0.995:
        stop = last - 2 * atr
    stop = max(stop, 0.01)
    target = high_window if high_window > last * 1.02 else last + 3 * atr
    rr = (target - last) / (last - stop) if last > stop else 0.0
    return {
        "last": round(last, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "atr_pct": round(atr / last * 100, 2),
        "stop": round(stop, 2),
        "stop_pct": round((stop / last - 1) * 100, 1),
        "target": round(target, 2),
        "target_pct": round((target / last - 1) * 100, 1),
        "rr": round(rr, 2),
        "above_ma20": last >= ma20,
        "entry_hint": "回踩 MA5 不破再进" if last > ma5 else "站回 MA5 再看",
    }


def _screen_metrics(bars: List[Dict[str, float]]) -> Dict[str, Any]:
    """短线可做性指标：流动性、相对位置。数据不足时返回空字典。"""
    if len(bars) < 20:
        return {}
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"] or b["close"]) for b in bars]
    amounts = [float(b["amount"] or 0) for b in bars]
    last = closes[-1]
    if last <= 0:
        return {}
    ma20 = sum(closes[-20:]) / 20
    avg_amount = sum(amounts[-20:]) / max(1, len(amounts[-20:]))
    high10 = max(highs[-10:])
    return {
        "avg_amount_yi": round(avg_amount / 1e8, 2),
        "pct_from_high10": round((last / high10 - 1) * 100, 2) if high10 else None,
        "pct_from_ma20": round((last / ma20 - 1) * 100, 2) if ma20 else None,
        "bars": len(bars),
    }


def _combine_verdict(regime: str, replay: str) -> str:
    """合并研究描述；实际候选还须通过共用准入，不授予交易资格。"""
    if regime == "block" or replay == "block":
        return "block"
    if replay == "allow":
        return "allow"
    return "caution"


def render(board_date: str, checkup: Dict[str, Any], out_path: Path) -> Path:
    db = DatabaseManager.get_instance()
    board, _ = build_board_for_date(board_date, output_path=None, db_manager=db)
    service = ResearchTrackingService(repo=ResearchTrackingRepository(db_manager=db))
    tracks = service.repo.list_tracks(limit=500)

    try:
        from src.services.signal_regime import DEFAULT_OUTPUT as REGIME_PATH, load_cached

        regime_data = load_cached(REGIME_PATH, as_of=board_date)
    except Exception:  # noqa: BLE001 - 体检结果缺失不影响页面生成
        regime_data = {"families": {}}
    try:
        from src.services.replay_stats_service import load_stats, verdict_for_signal

        replay_stats = load_stats(as_of=board_date)
    except Exception:  # noqa: BLE001 - 实测统计缺失时页面只展示体检
        replay_stats = {"signals": {}}

        def verdict_for_signal(*_args, **_kwargs):
            return {"verdict": "unknown", "reason": "统计缺失"}

    regime_families = regime_data.get("families") or {}
    industry_map = load_industry_map()
    try:
        ui_config = json.loads(DEFAULT_UI_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ui_config = {}
    verdict_text = {"block": "研究受限", "caution": "观察", "allow": "正向描述", "unknown": "证据不足"}
    verdict_cls = {"block": "neg", "caution": "mut", "allow": "pos"}
    regime_rows = "".join(
        f"<tr><td><b>{family}</b></td>"
        f"<td class='{verdict_cls.get(item.get('verdict'), 'mut')}'>{verdict_text.get(item.get('verdict'), item.get('verdict'))}</td>"
        f"<td>{(item.get('short') or {}).get('n', 0)}</td>"
        f"<td class='{_cls((item.get('short') or {}).get('excess_pct'))}'>{(item.get('short') or {}).get('excess_pct', '—')}%</td>"
        f"<td>{(item.get('short') or {}).get('win_pct', '—')}%</td>"
        f"<td class='{_cls((item.get('long') or {}).get('excess_pct'))}'>{(item.get('long') or {}).get('excess_pct', '—')}%</td>"
        f"<td class='mut'>{item.get('reason', '')}</td></tr>"
        for family, item in sorted(
            regime_families.items(),
            key=lambda kv: {"block": 0, "caution": 1, "allow": 2}.get(kv[1].get("verdict"), 9),
        )
    )
    regime_table = (
        f"<table><tr><th>信号</th><th>判定</th><th>近端条数</th><th>近端超额</th><th>近端胜率</th><th>长窗超额</th><th>依据</th></tr>"
        f"{regime_rows or '<tr><td colspan=7 class=mut>暂无体检结果</td></tr>'}</table>"
    )

    # ---- 两个口径合起来的信号结论（体检=近端能不能收，实测=到底赚不赚钱） ----
    signal_verdicts: Dict[str, Dict[str, Any]] = {}
    replay_rows = []
    families = sorted(
        set(list(regime_families) + list((replay_stats.get("signals") or {}))),
        key=lambda name: (
            {"block": 0, "caution": 1, "allow": 2, "unknown": 3}.get(
                _combine_verdict(
                    (regime_families.get(name) or {}).get("verdict") or "unknown",
                    "allow" if name in (replay_stats.get("signals") or {}) else "unknown",
                ),
                9,
            ),
            name,
        ),
    )
    for family in families:
        regime_verdict = (regime_families.get(family) or {}).get("verdict") or "unknown"
        replay_item = (replay_stats.get("signals") or {}).get(family) or {}
        replay_detail = verdict_for_signal(replay_stats, family) if replay_item else {
            "verdict": "unknown", "reason": "暂无实测样本",
        }
        combined = _combine_verdict(regime_verdict, replay_detail["verdict"])
        signal_verdicts[family] = {
            "regime": regime_verdict,
            "replay": replay_detail["verdict"],
            "combined": combined,
            "regime_reason": ((regime_families.get(family) or {}).get("reason") or ""),
            "replay_reason": replay_detail.get("reason", ""),
            "label": replay_item.get("label") or family,
        }
        def _fmt(value: Any) -> str:
            return "—" if value is None else f"{value}%"

        for kind, label in (("replay", "回放"), ("forward", "前向研究"), ("bootstrap", "事后导入/待核")):
            basis = replay_item.get(kind) or {}
            if not basis.get("n"):
                continue
            ret1 = (basis.get("ret1") or {}).get("mean")
            ret3 = (basis.get("ret3") or {}).get("mean")
            ret5 = (basis.get("ret5") or {}).get("mean")
            ex5 = (basis.get("excess5") or {}).get("mean")
            win5 = (basis.get("excess5") or {}).get("win_pct")
            mae = (basis.get("mae_pct") or {}).get("mean")
            detail = verdict_for_signal({"signals": {family: {kind: basis}}}, family)
            display_verdict = detail["verdict"]
            replay_rows.append(
                f"<tr><td><b>{signal_verdicts[family]['label']}</b></td><td>{label}</td>"
                f"<td>{basis.get('n')}</td><td>{basis.get('excess5_n', 0)} / {basis.get('excess5_batches', 0)}</td>"
                f"<td class='{_cls(ret1)}'>{_fmt(ret1)}</td>"
                f"<td class='{_cls(ret3)}'>{_fmt(ret3)}</td>"
                f"<td class='{_cls(ret5)}'>{_fmt(ret5)}</td>"
                f"<td class='{_cls(ex5)}'>{_fmt(ex5)}</td>"
                f"<td>{'—' if win5 is None else str(win5) + '%'}</td>"
                f"<td class='neg'>{'—' if mae is None else str(mae) + '%'}</td>"
                f"<td class='{verdict_cls.get(display_verdict, 'mut')}'>{verdict_text.get(display_verdict, display_verdict)}</td>"
                f"<td class='mut'>{detail.get('reason', '')}</td></tr>"
            )
    replay_table = (
        "<table><tr><th>信号</th><th>样本身份</th><th>独立样本</th><th>T+5有效/批次</th><th>T+1</th><th>T+3</th><th>T+5</th>"
        "<th>T+5 超额</th><th>跑赢率</th><th>平均最大浮亏</th><th>结论</th><th>依据</th></tr>"
        + ("".join(replay_rows) or "<tr><td colspan=12 class=mut>还没有已测量的样本</td></tr>")
        + "</table>"
    )

    with db.get_session() as session:
        db_path = Path(session.get_bind().url.database)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        news = con.execute("SELECT COUNT(*) FROM news_intel").fetchone()[0]
        intel = con.execute("SELECT COUNT(*) FROM intelligence_items").fetchone()[0]
        timeline = con.execute(
            "SELECT t.code, e.event_type, e.observed_at, COALESCE(e.summary,'') "
            "FROM research_track_events e JOIN research_tracks t ON t.id = e.track_pk "
            "ORDER BY e.id DESC LIMIT 400"
        ).fetchall()
        initial_rows = con.execute(
            "SELECT t.hypothesis_key, e.observed_at, e.payload_json FROM research_tracks t "
            "LEFT JOIN research_track_events e ON e.id=(SELECT min(c.id) "
            "FROM research_track_events c WHERE c.track_pk=t.id AND c.event_type='created')"
        ).fetchall()
        history_rows = con.execute(
            "SELECT code, signal_type, signal_date FROM kline_signal_snapshot "
            "WHERE signal_date >= date(?, '-120 day') AND signal_date <= ? ORDER BY signal_date DESC",
            (board_date, board_date),
        ).fetchall()
    finally:
        con.close()

    sample_counts: Counter = Counter()
    for key, stamp, payload in initial_rows:
        try:
            initial = json.loads(payload or "{}")
            observed = datetime.fromisoformat(str(stamp)) if stamp else None
        except (ValueError, TypeError):
            initial, observed = {}, None
        sample_counts[sample_kind(key, observed, initial if isinstance(initial, dict) else {})] += 1

    outcomes = collect_samples(db_path, as_of=board_date)
    _annotate_samples(outcomes, _load_market_data(db_path, outcomes, as_of=board_date))
    with_result = len(outcomes)
    rets = [o["ret1"] for o in outcomes if o["sample_kind"] == "forward" and o.get("ret1") is not None]
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
        f"<tr><td>{o['code']}</td><td>{o['source'].replace('screening__', '')} / {o['sample_kind']}</td><td>{o['signal_date']}</td>"
        f"<td class='{_cls(o['ret1'])}'>{_pct(o['ret1'])}</td>"
        f"<td class='{_cls(o['ret3'])}'>{_pct(o['ret3'])}</td>"
        f"<td class='{_cls(o['ret5'])}'>{_pct(o['ret5'])}</td>"
        f"<td class='mut'>{'缺 ' + ','.join(o['missing_dates'][:3]) if o['missing_dates'] else '检查点完整' if o.get('ret5') is not None else '窗口未完整'}</td></tr>"
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
            src=signal_of(track.hypothesis_key),
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
        return [{"d": row["date"], "c": round(float(row["close"]), 3)}
                for row in bars_by_code.get(code, [])[-limit:]]

    bars_by_code = _load_bars(db, [cand.code for cand in board.candidates], board_date)
    plans = {cand.code: _trade_plan(bars_by_code.get(cand.code) or []) for cand in board.candidates}
    profiles = {code: profile_of(code, industry_map) for code in {cand.code for cand in board.candidates}}
    screens = {
        cand.code: _screen_metrics(bars_by_code.get(cand.code) or [])
        for cand in board.candidates
    }

    track_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for track in tracks:
        track_by_code.setdefault(str(track.code), []).append({
            "signal_date": str(track.hypothesis_key).rsplit(":", 1)[-1],
            "source": signal_of(track.hypothesis_key),
            "status": track.status,
            "research": str(track.hypothesis_key).startswith("research:"),
            "next_review_at": str(track.next_review_at)[:10] if track.next_review_at else "",
        })
    history_by_code: Dict[str, List[Dict[str, str]]] = {}
    for code, signal_type, signal_date in history_rows:
        bucket = history_by_code.setdefault(str(code), [])
        if len(bucket) < 8:
            bucket.append({"type": str(signal_type), "date": str(signal_date)[:10]})

    candidates_payload = []
    policy_verdicts = {} if regime_data.get("error") else {
        family: str(item.get("verdict") or "unknown") for family, item in regime_families.items()
    }
    for cand in board.candidates:
        eligible, restrictions = gate_candidates(
            service, [cand], board_date, regime=board.market_regime, signal_verdicts=policy_verdicts,
        )
        candidates_payload.append({
            "admission": {"can_track": bool(eligible), "can_trade": False,
                          "scope": "research", "reasons": list(restrictions)},
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
            "history": history_by_code.get(cand.code, []),
            "plan": plans.get(cand.code) or {},
            "industry": profiles.get(cand.code, {}).get("industry", "未分类"),
            "group": profiles.get(cand.code, {}).get("group", "未分类"),
            "st": bool(profiles.get(cand.code, {}).get("st")),
            "screen": screens.get(cand.code) or {},
            "signal_verdict": signal_verdicts.get(cand.source) or {},
            "tracks": track_by_code.get(cand.code, []),
        })
    # ---- 顶部：可以随手改参数的“今天做什么”控制台（JS 实时重算） ----
    sim_html = """
<h2 style="margin:16px 0 4px">今天做什么 <span class="mut" style="font-size:12px;font-weight:400">（下面参数当场生效，改完点“存到本地”下次还保留）</span></h2>
<div id="simBar" style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;border:1px solid #e2e2e2;border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px">
  <span>排除：<span id="groupBox"></span></span>
  <label>同行业上限 <input id="capInd" type="number" min="0" max="30" style="width:54px"></label>
  <label>20日均额 ≥ <input id="minAmt" type="number" min="0" step="0.1" style="width:64px"> 亿</label>
  <label>距10日高 ≥ <input id="maxHigh" type="number" min="0" step="0.5" style="width:56px"> %（越低越靠前）</label>
  <label>距MA20 ≤ <input id="maxMa20" type="number" min="0" step="1" style="width:56px"> %</label>
  <label><input id="exSt" type="checkbox"> 排除 ST</label>
  <button id="saveSim" type="button">存到本地</button>
  <button id="resetSim" type="button">恢复默认</button>
  <button id="exportSim" type="button">导出研究候选 CSV</button>
  <span class="mut" id="simNote"></span>
</div>
<div id="decide"><div class="mut">正在计算…</div></div>
"""

    payload_json = json.dumps(
        {
            "date": board_date,
            "regime": board.market_regime,
            "candidates": candidates_payload,
            "ui": ui_config,
            "groups": sorted({p["group"] for p in profiles.values()}),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")

    html_doc = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>个人投研工作台 · 预览</title>
<style>{CSS}</style><body><div class="wrap">
<h1>个人投研工作台 · 预览</h1>
<div class="sub">数据截至 {board_date} · 生成于 {date.today().isoformat()} · 全部本机真实数据，未调模型/未发通知</div>
<div class="sub">收益口径：信号日盘后可得，<b>次一交易日开盘入场</b>；T+N = 入场后第 N 个交易日收盘 / 入场开盘 − 1；缺某天留空不平移</div>
<div class="cards">
  <div class="card"><div class="k">今日候选</div><div class="v">{len(board.candidates)}</div>
    <div class="n">{len({c.code for c in board.candidates})} 只证券</div></div>
  <div class="card"><div class="k">当前库跟踪记录</div><div class="v">{sum(sample_counts.values())}</div>
    <div class="n">前向研究 {sample_counts['forward']} / 回放 {sample_counts['replay']} / 事后导入或待核 {sample_counts['bootstrap']}；截至当日已测量 {with_result} 条</div></div>
  <div class="card"><div class="k">来源健康</div>
    <div class="v">{sum(1 for h in board.health if h.status == 'ok')}/{len(board.health)}</div>
    <div class="n">正常编排数</div></div>
  <div class="card"><div class="k">市场环境</div><div class="v">{board.market_regime}</div>
    <div class="n">运行配置，不等于牛熊判断</div></div>
</div>

{sim_html}

<h2>一、信号研究统计（回放、前向与事后导入分开）</h2>
<div class="sub" style="margin:0 0 8px">每条假设只取截至当日已收录的最新测量版本。基准价 = **次一交易日开盘价**（信号盘后才可得，信号日收盘买不到）；T+k 按入场后第 k 个交易日，超额对同窗口全市场均值。方向描述至少需 20 个 T+5 有效样本、5 批次，不授予交易资格。</div>
{replay_table}
<div style="height:10px"></div>
<div class="sub" style="margin:0 0 8px">逐月对比（按月看漂移；n 太小的月份只能当线索）：</div>
{signal_table}
<div class="warn" style="margin-top:10px"><b>读法</b>：正值=跑赢同期大盘；样本数 n 太小的月份只能当线索。
全市场覆盖完整的月份：{('、'.join(healthy) or '无')}。</div>

<h2>二、信号适配（今天该不该收这个信号）</h2>
<div class="sub" style="margin:0 0 8px">按该信号近端 T+5 超额与胜率描述研究适配；研究受限暂停新增，观察或正向描述仍须通过其他准入检查。同日既有记录可同步证据。体检缺失、过期或失败时不新增。
体检结果由 <code>scripts/build_signal_regime.py</code> 生成，准入与页面共用同一份。</div>
{regime_table}

<h2>三、今日候选（{board_date}）—— 点击左侧查看详情</h2>
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
    var s = c.screen || {};
    var seat = (s.pct_from_high10 === undefined || s.pct_from_high10 === null) ? '' : ' · 距高 ' + s.pct_from_high10 + '%';
    d.innerHTML = '<span class="code">' + c.code + '</span>' + (c.name||'') +
      '<div class="meta">' + (c.industry || '未分类') + ' · ' + c.source + seat +
      (c.flags && c.flags.length ? ' · ' + c.flags.join('、') : '') + '</div>';
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
  function planBlock(plan){
    if (!plan || !plan.last) return '<div class="mut">库内行情不足 25 根，给不出参考位</div>';
    var cls = plan.stop_pct < -12 ? 'neg' : 'mut';
    return '<table><tr><th>现价</th><th>止损</th><th>目标</th><th>盈亏比</th><th>日波动</th></tr>' +
      '<tr><td>' + plan.last + '</td>' +
      '<td class="' + cls + '">' + plan.stop + '（' + plan.stop_pct + '%）</td>' +
      '<td class="pos">' + plan.target + '（+' + plan.target_pct + '%）</td>' +
      '<td>' + plan.rr + '</td><td>' + plan.atr_pct + '%</td></tr></table>' +
      '<div class="mut" style="margin-top:4px">' + plan.entry_hint +
      '　MA5 ' + plan.ma5 + ' / MA20 ' + plan.ma20 + (plan.above_ma20 ? '（价在 MA20 上）' : '（价在 MA20 下）') + '</div>';
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
    var screenRows = (c.screen && c.screen.bars) ? (
      kv('行业', (c.industry || '未分类') + '（' + (c.group || '未分类') + '）') +
      kv('20日均额', c.screen.avg_amount_yi + ' 亿') +
      kv('距10日高', c.screen.pct_from_high10 + ' %') +
      kv('距MA20', c.screen.pct_from_ma20 + ' %')
    ) : '';
    detailEl.innerHTML =
      '<h3>' + c.code + ' ' + (c.name||'') + '</h3>' +
      '<div class="mut">' + (c.industry || '未分类') + ' · ' + c.source + ' · 批次证据 ' + (c.batch_ready ? '完整' : '不足（仅供阅读）') + '</div>' +
      '<div class="mut">研究准入：' + ((c.admission || {}).can_track ? '可新增或同步当日研究记录' : ((c.admission || {}).reasons || ['证据不足']).join('、')) + '</div>' +
      '<div style="margin-top:8px">' + flags + '</div>' +
      '<div class="kv" style="margin-top:12px">' + kv('入选理由', c.reason) + screenRows + mrows + '</div>' +
      '<div style="margin-top:12px"><b style="font-size:13px">参考位（按真实日线算）</b>' + planBlock(c.plan) + '</div>' +
      '<div style="margin-top:12px"><b style="font-size:13px">近 30 日走势</b>' + spark(c.closes) + '</div>' +
      '<div style="margin-top:12px"><b style="font-size:13px">近 120 日信号</b>' +
      ((c.history || []).length
        ? '<div class="mut" style="font-size:12px">' + (c.history || []).map(function(h){
            return h.date + ' · ' + h.type;
          }).join('　|　') + '</div>'
        : '<div class="mut">该票近期无信号记录</div>') + '</div>' +
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

// ---------------------------------------------------------------- 今天做什么：可调参数实时重算
(function(){
  var DATA = JSON.parse(document.getElementById('wb-data').textContent);
  var cands = DATA.candidates || [];
  var ui = DATA.ui || {};
  var STORE = 'wb-sim-v1';
  var defaults = {
    excluded: ui.excluded_groups || [],
    capInd: ui.max_per_industry === undefined ? 3 : ui.max_per_industry,
    minAmt: ui.min_avg_amount_yi === undefined ? 0.5 : ui.min_avg_amount_yi,
    maxHigh: ui.max_pct_from_high10 === undefined ? 3 : ui.max_pct_from_high10,
    maxMa20: ui.max_pct_from_ma20 === undefined ? 8 : ui.max_pct_from_ma20,
    exSt: ui.exclude_st !== false
  };
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch (e) { saved = {}; }
  var state = Object.assign({}, defaults, saved);

  var groups = DATA.groups || [];
  var groupBox = document.getElementById('groupBox');
  groupBox.innerHTML = groups.map(function(g){
    var checked = state.excluded.indexOf(g) >= 0 ? ' checked' : '';
    return '<label style="margin-right:8px;white-space:nowrap"><input type="checkbox" data-group="' + g + '"' + checked + '> ' + g + '</label>';
  }).join('');

  var elCapInd = document.getElementById('capInd');
  var elMinAmt = document.getElementById('minAmt');
  var elMaxHigh = document.getElementById('maxHigh');
  var elMaxMa20 = document.getElementById('maxMa20');
  var elExSt = document.getElementById('exSt');

  function syncInputs(){
    elCapInd.value = state.capInd;
    elMinAmt.value = state.minAmt;
    elMaxHigh.value = state.maxHigh;
    elMaxMa20.value = state.maxMa20;
    elExSt.checked = !!state.exSt;
    Array.prototype.slice.call(groupBox.querySelectorAll('input')).forEach(function(i){
      i.checked = state.excluded.indexOf(i.getAttribute('data-group')) >= 0;
    });
  }

  function readInputs(){
    state.capInd = parseFloat(elCapInd.value) || 0;
    state.minAmt = parseFloat(elMinAmt.value) || 0;
    state.maxHigh = parseFloat(elMaxHigh.value) || 0;
    state.maxMa20 = parseFloat(elMaxMa20.value) || 0;
    state.exSt = !!elExSt.checked;
    // 复选框没渲染出来时保留原状态，避免“排除行业”被静默清空
    if (groupBox.querySelectorAll('input').length) {
      state.excluded = Array.prototype.slice.call(groupBox.querySelectorAll('input:checked'))
        .map(function(i){ return i.getAttribute('data-group'); });
    }
  }

  function fmt(v, suffix){ return (v === null || v === undefined) ? '—' : v + (suffix || ''); }

  function filterReason(c, cfg){
    if (cfg.excluded.indexOf(c.group) >= 0) return '排除行业：' + c.group;
    if (cfg.exSt && c.st) return 'ST';
    var s = c.screen || {};
    if (!s.bars) return '本地行情不足，算不出位置';
    if (cfg.minAmt > 0 && (s.avg_amount_yi || 0) < cfg.minAmt) return '20日均额 ' + fmt(s.avg_amount_yi, '亿') + '（< ' + cfg.minAmt + '亿）';
    if (cfg.maxHigh > 0 && (s.pct_from_high10 === null || s.pct_from_high10 === undefined || s.pct_from_high10 < -cfg.maxHigh)) {
      return '距10日高 ' + fmt(s.pct_from_high10, '%') + '（要求 ≥ −' + cfg.maxHigh + '%）';
    }
    if (cfg.maxMa20 > 0 && s.pct_from_ma20 !== null && s.pct_from_ma20 !== undefined && s.pct_from_ma20 > cfg.maxMa20) {
      return '高于MA20 ' + fmt(s.pct_from_ma20, '%') + '（追高）';
    }
    return null;
  }

  // 用给定参数算一遍：返回研究候选 / 只观察 / 研究受限
  function compute(cfg){
    var allow = [], watch = [], skip = [];
    cands.forEach(function(c){
      var admission = c.admission || {};
      if (!admission.can_track) {
        skip.push([c, (admission.reasons || ['准入证据不足']).join('、')]); return;
      }
      var verdict = (c.signal_verdict || {}).combined || 'caution';
      if (verdict === 'block') { skip.push([c, '信号停用：' + ((c.signal_verdict || {}).replay_reason || '')]); return; }
      var reason = filterReason(c, cfg);
      if (reason) { watch.push([c, reason]); return; }
      if (verdict === 'allow') allow.push([c, '']); else watch.push([c, '信号谨慎（未到可用）']);
    });
    allow.sort(function(a, b){
      return ((b[0].plan || {}).rr || 0) - ((a[0].plan || {}).rr || 0);
    });
    var cap = cfg.capInd > 0 ? cfg.capInd : 1e9;
    var perInd = {}, kept = [];
    allow.forEach(function(pair){
      var ind = pair[0].industry || '未分类';
      if ((perInd[ind] || 0) >= cap) { watch.push([pair[0], '超出行业上限（' + ind + ' 已 ' + cap + ' 只）']); return; }
      perInd[ind] = (perInd[ind] || 0) + 1;
      kept.push(pair);
    });
    return { kept: kept, watch: watch, skip: skip, perInd: perInd };
  }

  function rowHtml(c, note, withPlan){
    var plan = c.plan || {};
    var s = c.screen || {};
    var v = c.signal_verdict || {};
    var head = '<b>' + c.code + ' ' + c.name + '</b> <span class="mut">' + c.industry + ' · ' + (v.label || c.source) + '</span>';
    var meta = '均额 ' + fmt(s.avg_amount_yi, '亿') + ' · 距10日高 ' + fmt(s.pct_from_high10, '%') + ' · 距MA20 ' + fmt(s.pct_from_ma20, '%') + ' · 日波动 ' + ((plan.atr_pct === undefined) ? '—' : plan.atr_pct + '%');
    var planLine = '';
    if (withPlan && plan.last){
      planLine = '<div style="margin-top:2px">现价 ' + plan.last + ' · 止损 ' + plan.stop + '（' + plan.stop_pct + '%）· 目标 ' + plan.target + '（+' + plan.target_pct + '%）· 盈亏比 ' + plan.rr + ' · ' + plan.entry_hint + '</div>';
    }
    var noteLine = note ? '<div class="mut">' + note + '</div>' : '';
    return '<div style="padding:6px 0;border-top:1px dashed #e5e5e5">' + head +
      '<br><span class="mut">' + meta + '</span>' + planLine + noteLine + '</div>';
  }

  // 空结果时提示“放开哪一项会多出几只”，方便调策略
  function hints(cfg){
    var probes = [
      ['放开行业排除', Object.assign({}, cfg, { excluded: [] })],
      ['放开成交额门槛', Object.assign({}, cfg, { minAmt: 0 })],
      ['放开距10日高', Object.assign({}, cfg, { maxHigh: 0 })],
      ['放开距MA20', Object.assign({}, cfg, { maxMa20: 0 })],
      ['放开行业上限', Object.assign({}, cfg, { capInd: 0 })]
    ];
    return probes.map(function(p){
      var got = compute(p[1]).kept.length;
      return [p[0], got];
    }).filter(function(p){ return p[1] > 0; });
  }

  var lastKept = [];

  function render(){
    readInputs();
    var result = compute(state);
    var kept = result.kept, watch = result.watch, skip = result.skip;
    lastKept = kept;
    var indHtml = Object.keys(result.perInd).sort(function(a, b){ return result.perInd[b] - result.perInd[a]; })
      .map(function(k){ return k + ' ' + result.perInd[k]; }).join('、');

    var groupCount = {};
    cands.forEach(function(c){ var g = c.group || '未分类'; groupCount[g] = (groupCount[g] || 0) + 1; });
    var comp = Object.keys(groupCount).sort(function(a, b){ return groupCount[b] - groupCount[a]; })
      .map(function(k){ return k + ' ' + groupCount[k]; }).join('、');

    var head = kept.length
      ? '<b class="pos">研究候选：' + kept.length + ' 只</b>　<span class="mut">观察 ' + watch.length + ' / 研究受限 ' + skip.length + (indHtml ? '　行业：' + indHtml : '') + '</span>'
      : '<b class="neg">按当前条件暂无研究候选</b>　<span class="mut">观察 ' + watch.length + ' / 研究受限 ' + skip.length + '</span>';
    head += '<br><span class="mut">今日候选 ' + cands.length + ' 只：' + comp + '</span>';

    var hintHtml = '';
    if (!kept.length) {
      var hs = hints(state).map(function(p){ return p[0] + ' +' + p[1] + ' 只'; });
      hintHtml = hs.length ? '<div class="sub" style="margin-top:4px">松开某一项就会多出：' + hs.join('　|　') + '</div>' : '';
    }

    document.getElementById('decide').innerHTML =
      '<div class="sub" style="margin:0 0 8px">' + head +
        '<br>“研究候选”须先通过服务端批次、行情、环境、体检和冷却检查，再按页面参数试算。历史统计仅描述样本，不授予交易资格；参考位不表示可成交计划。</div>' + hintHtml +
      '<div style="display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:12px">' +
        '<div style="border:1px solid #cfe8cf;background:#f6fbf6;border-radius:8px;padding:10px 12px"><div style="font-weight:600">① 研究候选（' + kept.length + '）</div>' +
          (kept.map(function(p){ return rowHtml(p[0], p[1], true); }).join('') || '<div class="mut" style="padding:6px 0">无</div>') + '</div>' +
        '<div style="border:1px solid #e8e0c0;background:#fdfbf3;border-radius:8px;padding:10px 12px"><div style="font-weight:600">② 只观察（' + watch.length + '）</div>' +
          (watch.slice(0, 14).map(function(p){ return rowHtml(p[0], p[1], false); }).join('') || '<div class="mut" style="padding:6px 0">无</div>') +
          (watch.length > 14 ? '<div class="mut">……共 ' + watch.length + ' 只</div>' : '') + '</div>' +
        '<div style="border:1px solid #e8c8c8;background:#fdf5f5;border-radius:8px;padding:10px 12px"><div style="font-weight:600">③ 研究受限（' + skip.length + '）</div>' +
          (skip.slice(0, 10).map(function(p){ return rowHtml(p[0], p[1], false); }).join('') || '<div class="mut" style="padding:6px 0">无</div>') +
          (skip.length > 10 ? '<div class="mut">……共 ' + skip.length + ' 只</div>' : '') + '</div>' +
      '</div>';
    document.getElementById('simNote').textContent = '候选 ' + cands.length + ' 只 · 改动即时生效';
  }

  groupBox.addEventListener('change', render);
  [elCapInd, elMinAmt, elMaxHigh, elMaxMa20, elExSt].forEach(function(el){
    el.addEventListener('change', render);
    el.addEventListener('input', render);
  });
  document.getElementById('saveSim').addEventListener('click', function(){
    readInputs();
    localStorage.setItem(STORE, JSON.stringify(state));
    document.getElementById('simNote').textContent = '已保存到本地浏览器';
  });
  document.getElementById('resetSim').addEventListener('click', function(){
    localStorage.removeItem(STORE);
    state = Object.assign({}, defaults);
    syncInputs();
    render();
    document.getElementById('simNote').textContent = '已恢复默认';
  });
  document.getElementById('exportSim').addEventListener('click', function(){
    var lines = ['code,name,industry,source,last,stop,stop_pct,target,target_pct,rr,avg_amount_yi,pct_from_high10,pct_from_ma20'];
    lastKept.forEach(function(p){
      var c = p[0], plan = c.plan || {}, s = c.screen || {};
      lines.push([c.code, c.name, c.industry, c.source, plan.last, plan.stop, plan.stop_pct,
        plan.target, plan.target_pct, plan.rr, s.avg_amount_yi, s.pct_from_high10, s.pct_from_ma20]
        .map(function(v){ return (v === undefined || v === null) ? '' : String(v).replace(/,/g, ' '); }).join(','));
    });
    // 用 String.fromCharCode(10) 拼换行，避免被 Python 模板吞成真实换行
    var blob = new Blob([lines.join(String.fromCharCode(10))], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'workbench_do_list_' + (DATA.date || '') + '.csv';
    a.click();
    document.getElementById('simNote').textContent = '已导出 ' + lastKept.length + ' 行';
  });
  syncInputs();
  render();
})();
</script>
""" + f"""

<h2>四、回填结果（独立假设最新版本；上方均值仅统计前向 T+1，截至 {board_date}）</h2>
<div class="cards">
  <div class="card"><div class="k">已回填样本</div><div class="v">{outcome_stats['n']}</div><div class="n">共 {len(outcomes)} 条有结果</div></div>
  <div class="card"><div class="k">T+1 均值</div><div class="v {_cls(outcome_stats['mean'])}">{('' if outcome_stats['mean'] is None else '%+.2f%%' % outcome_stats['mean'])}</div><div class="n">相对入场开盘</div></div>
  <div class="card"><div class="k">T+1 胜率</div><div class="v">{('' if outcome_stats['win'] is None else '%.0f%%' % outcome_stats['win'])}</div><div class="n">收盘为正占比</div></div>
  <div class="card"><div class="k">最好 / 最差</div><div class="v" style="font-size:18px">{('—' if outcome_stats['best'] is None else '%+.1f%% / %+.1f%%' % (outcome_stats['best'], outcome_stats['worst']))}</div><div class="n">单票 T+1</div></div>
</div>
{outcome_table}
<div class="warn" style="margin-top:10px">T+3/T+5 需要更多交易日，目前按日历留空；quality=partial 表示窗口未走完，不是漏数。</div>

<h2>五、跟踪样本池（点开事件列看时间线）</h2>
{track_table}
<div class="ok" style="margin-top:10px">每条样本带来源引用/内容版本/批次号/限制说明。入池前过三道闸门：
<b>环境不适配</b>（defensive 时不收趋势类）、<b>同票冷却期</b>（10 个交易日内只一条）、
<b>过热</b>（距首次信号涨幅 &gt;30% 不再入池）。</div>

<h2>六、来源健康</h2>
{health_table}

<h2>七、数据完整性</h2>
<div class="bad">各股票“最后一根 K 线”所在月份：{cov_text}<br>
本月已完成一次全市场缓存回灌（4,497 只补齐到最新交易日）；仍有约 700 只停在 9 月，多为已停牌/退市、缓存无更新，不计入统计。</div>
<ul>
<li>本地行情缓存：<code>data/cache/history/cn</code> 4,500+ 只（策略实际读取）</li>
<li>资讯：<code>news_intel</code> {news} 条 / <code>intelligence_items</code> {intel} 条 → 事件层尚未接入</li>
<li>夜间链路交易日判断：已改为本地日历优先，日历不可用时按交易日继续（原实现会把限流误判为休市并静默跳过）</li>
</ul>

<h2>八、下一步</h2>
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
    if db_path.resolve() != DEFAULT_DB.resolve():
        import os
        from src.config import Config

        os.environ["DATABASE_PATH"] = str(db_path.resolve())
        Config.reset_instance()
        DatabaseManager.reset_instance()
    checkup = signal_checkup(db_path, months=int(args.months), as_of=board_date)
    out = render(board_date, checkup, Path(args.out))
    print(f"[workbench] date={board_date} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
