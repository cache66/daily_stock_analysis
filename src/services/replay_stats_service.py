# -*- coding: utf-8 -*-
"""信号实测统计：把已测量的样本（前向 + 回放）汇总成“这个信号到底赚不赚钱”。

口径：

- 样本来源是 `research_track_events`（`source_type=stock_daily_qfq`），payload 里
  有 `ret1/ret3/ret5`（相对建仓价）。`sample_kind=replay` 表示事后回放，
  首次登记证据不能证明前向身份时归 bootstrap，三类分开统计，不改写旧事件。
- 建仓价由评估器给出（2026-11-10 起 = **次一交易日开盘价**，`OUTCOME_RULE_VERSION=track-market-sessions-v2-next-open`）；
  收益 = (入场后第 k 个交易日收盘 − 建仓价) / 建仓价，改口径按新版本追加证据、不原地改写旧结论。
- MAE/MFE（最大不利/有利偏移）用信号日后 5 个交易日内最低 and 最高价相对建仓价，
  回答“中间要扛多少浮亏才能拿到这个收益”。
- 只输出统计，不做买卖建议；样本不足（< `MIN_SAMPLES`）时明确标注。

用法：
    from src.services.replay_stats_service import build_stats, load_stats
    stats = build_stats()
    cached = load_stats()
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional

from src.services.market_instruments import is_tradable_stock
from src.services.quote_quality import load_suspicious_codes
from src.services.research_sample import CN_TZ, sample_kind
from src.core.trading_calendar import get_trading_sessions

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "strategy_review" / "replay_stats.json"

HORIZONS = (1, 3, 5)
MAE_WINDOW = 5
MIN_SAMPLES = 20
MIN_BATCHES = 5
STATS_SCHEMA_VERSION = 2

SIGNAL_LABELS = {
    "hundred_day_high": "百日新高",
    "trend_leader_unified": "趋势龙头",
    "earnings_surprise": "业绩超预期",
    "daily_slow_rise": "缓涨",
    "long_base_release": "长期盘整突破",
    "screening__dual_low": "筛选·双低",
    "screening__momentum_quality": "筛选·趋势质量",
    "screening__shrink_pullback": "筛选·缩量回踩",
}


def signal_of(hypothesis_key: str) -> str:
    """`research:signal:src:date` / `signal:src:date` → `src`。"""
    parts = str(hypothesis_key or "").split(":")
    if len(parts) >= 4 and parts[0] == "research":
        return parts[2]
    if len(parts) >= 3:
        return parts[1]
    return ""


def signal_label(source: str) -> str:
    return SIGNAL_LABELS.get(source, source)


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def collect_samples(db_path: Path = DEFAULT_DB, *, as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """按实际收录时点选每条假设的最新测量；旧版本仅留作审计。"""
    cutoff = date.fromisoformat(as_of) if as_of else date.today()
    end = datetime.combine(cutoff + timedelta(days=1), time.min, CN_TZ).astimezone(timezone.utc)
    with _connect(db_path) as con:
        rows = con.execute(
            """
            SELECT t.code, t.hypothesis_key, e.payload_json, e.quality, e.observed_at,
                   e.track_pk, e.id,
                   (SELECT c.observed_at FROM research_track_events c
                    WHERE c.track_pk=t.id AND c.event_type='created' ORDER BY c.id LIMIT 1),
                   (SELECT c.payload_json FROM research_track_events c
                    WHERE c.track_pk=t.id AND c.event_type='created' ORDER BY c.id LIMIT 1)
            FROM research_track_events e
            JOIN research_tracks t ON t.id = e.track_pk
            WHERE e.source_type = 'stock_daily_qfq'
              AND julianday(e.observed_at) < julianday(?)
            ORDER BY e.id DESC
            """, (end.isoformat(),)
        ).fetchall()

    samples: List[Dict[str, Any]] = []
    seen = set()
    suspicious = load_suspicious_codes()
    for code, key, payload, quality, observed_at, track_pk, event_id, initial_at, initial_payload in rows:
        # 未知时点不能用于还原历史；晚到修订不应覆盖截止时点的旧版本。
        try:
            stamp = datetime.fromisoformat(str(observed_at))
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            if stamp >= end:
                continue
        except (ValueError, TypeError):
            continue
        if track_pk in seen:
            continue
        seen.add(track_pk)
        try:
            data = json.loads(payload or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        entry_date = str(data.get("entry_date") or "")
        entry_close = data.get("entry_close")
        if not entry_date or entry_date > cutoff.isoformat() or not entry_close:
            continue
        try:
            entry_close = float(entry_close)
        except (TypeError, ValueError):
            continue
        if not isfinite(entry_close) or entry_close <= 0:
            continue
        if not is_tradable_stock(code) or str(code) in suspicious:
            # 指数/基金序列不参与股票统计
            continue
        try:
            initial = json.loads(initial_payload or "{}")
            initial_stamp = datetime.fromisoformat(str(initial_at)) if initial_at else None
        except (ValueError, TypeError):
            initial, initial_stamp = {}, None
        if not isinstance(initial, dict):
            initial = {}
        item = {
            "track_pk": track_pk,
            "event_id": event_id,
            "code": str(code),
            "source": signal_of(key),
            "signal_date": entry_date,
            "sample_kind": sample_kind(str(key), initial_stamp, initial),
            "quality": str(quality or ""),
            "observed_at": str(observed_at or ""),
            "entry_close": entry_close,
            "missing_dates": data.get("missing_dates") if isinstance(data.get("missing_dates"), list) else [],
        }
        for horizon in HORIZONS:
            value = data.get(f"ret{horizon}")
            try:
                number = float(value)
                item[f"ret{horizon}"] = number if isfinite(number) else None
            except (ValueError, TypeError):
                item[f"ret{horizon}"] = None
        samples.append(item)
    return samples


def _load_market_data(
    db_path: Path, samples: Iterable[Dict[str, Any]], *, as_of: str,
) -> Dict[str, Any]:
    """一次性读入窗口内全市场行情，算出：

    - ``sessions``：窗口内交易日（升序）
    - ``bars``：code → [(date, high, low, close)]
    - ``market``：(date, k) → 同期全市场等权平均收益（%）
    """
    samples = list(samples)
    if not samples:
        return {"sessions": [], "bars": {}, "market": {}}
    dates = sorted({item["signal_date"] for item in samples})
    start = dates[0]
    end_text = as_of
    sessions = [d.isoformat() for d in get_trading_sessions("cn", date.fromisoformat(start), date.fromisoformat(as_of))]
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT code, date, high, low, close FROM stock_daily "
            "WHERE date >= ? AND date <= ? AND close IS NOT NULL AND close > 0",
            (start, end_text),
        ).fetchall()

    bars: Dict[str, List[tuple]] = defaultdict(list)
    closes_by_date: Dict[str, Dict[str, float]] = defaultdict(dict)
    suspicious = load_suspicious_codes()
    for code, day, high, low, close in rows:
        if not is_tradable_stock(str(code)) or str(code) in suspicious:
            continue
        text = str(day)[:10]
        bars[code].append((text, high, low, close))
        closes_by_date[text][code] = float(close)
    for values in bars.values():
        values.sort()

    market: Dict[tuple, Optional[float]] = {}
    for index, day in enumerate(sessions):
        base = closes_by_date.get(day, {})
        for horizon in HORIZONS:
            target_index = index + horizon
            if target_index >= len(sessions):
                market[(day, horizon)] = None
                continue
            target = closes_by_date.get(sessions[target_index], {})
            pairs = [(base[code], target[code]) for code in base.keys() & target.keys()]
            market[(day, horizon)] = (
                round(mean([t / b - 1.0 for b, t in pairs]) * 100, 4) if pairs else None
            )
    return {"sessions": sessions, "bars": bars, "market": market}


def _annotate_samples(
    samples: List[Dict[str, Any]], market_data: Dict[str, Any]
) -> None:
    """给每个样本补 MAE/MFE 与相对同批市场的超额收益（就地修改）。"""
    bars = market_data.get("bars") or {}
    market = market_data.get("market") or {}
    sessions = market_data.get("sessions") or []
    positions = {day: index for index, day in enumerate(sessions)}
    for item in samples:
        series = bars.get(item["code"]) or []
        position = positions.get(item["signal_date"])
        window = sessions[position + 1:position + MAE_WINDOW + 1] if position is not None else []
        by_date = {row[0]: row for row in series}
        after = [by_date[day] for day in window if day in by_date]
        entry = item["entry_close"]
        # MAE 只报告完整固定窗口；停牌/缺行不向后平移，未到期不冒充五日浮亏。
        if len(window) == MAE_WINDOW and len(after) == MAE_WINDOW and all(r[1] is not None and r[2] is not None for r in after):
            lows = [row[2] for row in after if row[2] is not None]
            highs = [row[1] for row in after if row[1] is not None]
            item["mae_pct"] = round((min(lows) / entry - 1.0) * 100, 2) if lows else None
            item["mfe_pct"] = round((max(highs) / entry - 1.0) * 100, 2) if highs else None
            item["days_observed"] = len(after)
        for horizon in HORIZONS:
            if position is None or position + horizon >= len(sessions):
                item[f"ret{horizon}"] = None
            value = item.get(f"ret{horizon}")
            benchmark = market.get((item["signal_date"], horizon))
            item[f"excess{horizon}"] = (
                round(value * 100 - benchmark, 2) if value is not None and benchmark is not None else None
            )


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0, "sufficient": False}

    def _stat(values: List[float]) -> Optional[Dict[str, float]]:
        if not values:
            return None
        return {
            "mean": round(mean(values), 2),
            "median": round(median(values), 2),
            "win_pct": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
            "best": round(max(values), 2),
            "worst": round(min(values), 2),
        }

    summary: Dict[str, Any] = {
        "n": len(rows),
        "batches": len({r["signal_date"] for r in rows}),
        "sufficient": False,
        "dates": [min(r["signal_date"] for r in rows), max(r["signal_date"] for r in rows)],
    }
    for horizon in HORIZONS:
        values = [r[f"ret{horizon}"] * 100 for r in rows if r.get(f"ret{horizon}") is not None]
        summary[f"ret{horizon}"] = _stat(values)
        summary[f"ret{horizon}_n"] = len(values)
        excess = [r[f"excess{horizon}"] for r in rows if r.get(f"excess{horizon}") is not None]
        summary[f"excess{horizon}"] = _stat(excess)
        valid = [r for r in rows if r.get(f"excess{horizon}") is not None]
        summary[f"excess{horizon}_n"] = len(valid)
        summary[f"ret{horizon}_batches"] = len({r["signal_date"] for r in rows if r.get(f"ret{horizon}") is not None})
        summary[f"excess{horizon}_batches"] = len({r["signal_date"] for r in valid})
        summary[f"sufficient{horizon}"] = len(valid) >= MIN_SAMPLES and summary[f"excess{horizon}_batches"] >= MIN_BATCHES
    summary["sufficient"] = summary["sufficient5"]
    for key in ("mae_pct", "mfe_pct"):
        values = [r[key] for r in rows if r.get(key) is not None]
        if values:
            summary[key] = {
                "mean": round(mean(values), 2),
                "median": round(median(values), 2),
                "worst": round(min(values), 2),
            }
    return summary


def build_stats(
    *,
    db_path: Path = DEFAULT_DB,
    output_path: Optional[Path] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """生成实测统计并写盘，返回结果字典。"""
    cutoff = date.fromisoformat(as_of) if as_of else date.today()
    samples = collect_samples(db_path, as_of=cutoff.isoformat())
    market_data = _load_market_data(db_path, samples, as_of=cutoff.isoformat())
    _annotate_samples(samples, market_data)

    by_signal: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in samples:
        by_signal[item["source"]][item["sample_kind"]].append(item)

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        "schema_version": STATS_SCHEMA_VERSION,
        "as_of": cutoff.isoformat(),
        "scope": "research",
        "price_basis": "current_local_qfq_history",
        "limitation": "按截止日收录的事件选版；本地行情无历史版本库，不宣称行情 PIT 或可成交收益。",
        "horizons": list(HORIZONS),
        "min_samples": MIN_SAMPLES,
        "min_batches": MIN_BATCHES,
        "signals": {},
    }
    for source, kinds in sorted(by_signal.items()):
        payload["signals"][source] = {
            "label": signal_label(source),
            **{kind: _summarize(rows) for kind, rows in kinds.items()},
        }

    all_rows = [item for item in samples if item["sample_kind"] == "replay"]
    payload["overall_replay"] = _summarize(all_rows)
    payload["overall_forward"] = _summarize([i for i in samples if i["sample_kind"] == "forward"])
    payload["overall_bootstrap"] = _summarize([i for i in samples if i["sample_kind"] == "bootstrap"])

    target = Path(output_path) if output_path else DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return payload


def load_stats(output_path: Optional[Path] = None, *, as_of: Optional[str] = None) -> Dict[str, Any]:
    target = Path(output_path) if output_path else DEFAULT_OUTPUT
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"signals": {}, "overall_replay": {"n": 0}, "overall_forward": {"n": 0}}
    if not isinstance(data, dict) or (as_of is not None and (
        data.get("as_of") != as_of or data.get("schema_version") != STATS_SCHEMA_VERSION
    )):
        return {"signals": {}, "error": "cache_stale"}
    return data


def verdict_for_signal(stats: Dict[str, Any], source: str) -> Dict[str, Any]:
    """仅描述研究统计方向；不产生交易资格。

    规则（T+5 有效样本 < MIN_SAMPLES 或批次 < MIN_BATCHES → 不给方向）：
    - T+5 **超额**均值 > 0 且胜率 >= 50% → 正向描述
    - T+5 超额均值 > 0 但胜率 < 50% → 靠少数大赚撑着，按谨慎处理
    - T+5 超额均值 <= 0 → 负向描述
    """
    item = (stats.get("signals") or {}).get(str(source)) or {}
    replay = item.get("replay") or {}
    forward = item.get("forward") or {}
    basis = replay if replay.get("n") else forward
    excess5 = (basis or {}).get("excess5")
    valid_n = basis.get("excess5_n", 0)
    batches = basis.get("excess5_batches", 0)
    if not excess5 or valid_n < MIN_SAMPLES or batches < MIN_BATCHES:
        return {
            "verdict": "unknown",
            "reason": f"T+5 有效样本/批次不足（n={valid_n}，批次 {batches}），仅作研究线索",
            "n": valid_n, "batches": batches, "can_trade": False,
        }
    mean5 = float(excess5["mean"])
    win5 = float(excess5["win_pct"])
    batches = int(batches)
    if mean5 <= 0:
        verdict, reason = "block", f"T+5 超额 {mean5:+.2f}%、跑赢胜率 {win5:.0f}%（n={valid_n}）"
    elif win5 < 50:
        verdict, reason = "caution", f"T+5 超额 {mean5:+.2f}% 但跑赢率仅 {win5:.0f}%，靠少数大赚撑着"
    else:
        verdict, reason = "allow", f"T+5 超额 {mean5:+.2f}%、跑赢率 {win5:.0f}%（n={valid_n}）"
    return {
        "verdict": verdict,
        "scope": "research", "can_trade": False,
        "reason": reason,
        "n": valid_n,
        "batches": batches,
        "excess5_mean": mean5,
        "win5": win5,
        "ret5_mean": ((basis.get("ret5") or {}).get("mean")),
        "mae_mean": (basis.get("mae_pct") or {}).get("mean"),
    }
