# -*- coding: utf-8 -*-
"""研究跟踪的结果回填（A3-a 最小闭环）。

把“信号入池的 track”按固定窗口（T+1/T+3/T+5）算出前向收益，写回事件；
T+5 可用时关闭 track。只读价格，不重算选股、不改资格。

口径：
- 入场价 = 信号日（D0）收盘；收益 = 第 N 个交易日收盘 / D0 收盘 − 1；
- 只用本地 `stock_daily`（前复权），缺数据就记 `insufficient_data`，不推测；
- 同一测量版本重跑幂等；行情修订追加新证据，完整结果与自动关闭同事务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.trading_calendar import get_trading_sessions
from src.repositories.research_tracking_repo import AUTO_CLOSE_REASON_PREFIX
from src.services.quote_quality import load_suspicious_codes as _shared_load_suspicious_codes
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 5)
OUTCOME_RULE_VERSION = "track-market-sessions-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUSPICIOUS_CODES_PATH = (
    PROJECT_ROOT / "data" / "run_logs" / "rebuild_stock_daily_qfq_suspicious_codes.txt"
)


def load_suspicious_codes(path: Optional[Path] = None) -> set[str]:
    """行情跳变待核清单（不能单凭阈值证明错误），复核前限制回填。

    实现收敛到 `src.services.quote_quality`，本函数仅保留稳定导入名（测试注入点）。
    """
    return _shared_load_suspicious_codes(path)


@dataclass(frozen=True)
class ForwardOutcome:
    entry_date: str
    entry_close: float
    last_date: str
    last_close: float
    ret1: Optional[float] = None
    ret3: Optional[float] = None
    ret5: Optional[float] = None
    available_days: int = 0
    expected_dates: Tuple[str, ...] = ()
    missing_dates: Tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(getattr(self, f"ret{h}") is not None for h in HORIZONS) and not self.missing_dates


def compute_forward_outcome(
    bars: Sequence[Tuple[str, float]], *, sessions: Optional[Sequence[str]] = None, market: str = "cn",
) -> Optional[ForwardOutcome]:
    """按市场日历检查点取价，缺失保留空，不平移到下一条行情。"""
    clean = [(str(day)[:10], float(close)) for day, close in bars if close and math.isfinite(float(close)) and float(close) > 0]
    clean.sort()
    if len(clean) < 2:
        return None
    entry_date, entry_close = clean[0]
    if entry_close <= 0:
        return None
    expected = tuple(sessions) if sessions is not None else tuple(
        d.isoformat() for d in get_trading_sessions(market, date.fromisoformat(entry_date), date.fromisoformat(clean[-1][0]))
    )
    expected = expected[:max(HORIZONS) + 1]
    if not expected or expected[0] != entry_date:
        return None
    prices = dict(clean)
    present = [day for day in expected if day in prices]
    if len(present) < 2:
        return None
    values: Dict[str, Optional[float]] = {}
    for horizon in HORIZONS:
        if len(expected) > horizon and expected[horizon] in prices:
            values[f"ret{horizon}"] = prices[expected[horizon]] / entry_close - 1.0
        else:
            values[f"ret{horizon}"] = None
    return ForwardOutcome(
        entry_date=entry_date,
        entry_close=entry_close,
        last_date=present[-1],
        last_close=prices[present[-1]],
        ret1=values["ret1"],
        ret3=values["ret3"],
        ret5=values["ret5"],
        available_days=len(present) - 1,
        expected_dates=expected,
        missing_dates=tuple(day for day in expected if day not in prices),
    )


def load_bars(
    code: str,
    signal_date: str,
    *,
    horizon: int = 5,
    end_date: Optional[str] = None,
    db_manager: Any = None,
) -> List[Tuple[str, float]]:
    """取信号日到截止日的日线；由调用方按日历限定窗口，不按行数平移。

    ``end_date`` 是硬截断：回填某日时必须传，否则会读到截止日之后的行情（前视）。
    """
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = db_manager or DatabaseManager.get_instance()
    sql = (
        "SELECT date, close FROM stock_daily WHERE code = :code AND date >= :start "
        + ("AND date <= :end " if end_date else "")
        + "ORDER BY date ASC " + ("" if end_date else "LIMIT :lim")
    )
    params: Dict[str, Any] = {"code": code, "start": signal_date, "lim": max(2, int(horizon) + 1)}
    if end_date:
        params["end"] = end_date
    with db.session_scope() as session:
        rows = session.execute(text(sql), params).fetchall()
    return [(str(row[0])[:10], float(row[1])) for row in rows]


def evaluate_track_outcomes(
    *,
    as_of: Optional[date] = None,
    limit: int = 500,
    dry_run: bool = False,
    bars_loader=load_bars,
) -> Dict[str, Any]:
    """为到期的 signal track 回填结果；返回统计摘要（可 dry-run）。"""
    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    cutoff = as_of or date.today()
    summary: Dict[str, Any] = {
        "checked": 0,
        "written": 0,
        "closed": 0,
        "pending": 0,
        "insufficient": 0,
        "skipped_no_signal": 0,
        "skipped_suspicious": 0,
        "errors": 0,
        "calendar_unavailable": 0,
        "newer_measurement": 0,
    }
    suspicious = load_suspicious_codes()

    for track in service.repo.outcome_tracks(limit=limit):
        if not str(track.hypothesis_key).startswith("signal:"):
            continue
        signal_date = str(track.hypothesis_key).rsplit(":", 1)[-1]
        try:
            date.fromisoformat(signal_date)
        except ValueError:
            summary["skipped_no_signal"] += 1
            continue
        if track.next_review_at and track.next_review_at.date() > cutoff:
            continue

        summary["checked"] += 1

        if str(track.code)[:6] in suspicious:
            summary["skipped_suspicious"] += 1
            continue

        try:
            sessions = [d.isoformat() for d in get_trading_sessions(track.market, date.fromisoformat(signal_date), cutoff)]
        except (ValueError, RuntimeError, ImportError):
            summary["calendar_unavailable"] += 1
            continue
        sessions = sessions[:max(HORIZONS) + 1]
        if not sessions or sessions[0] != signal_date:
            summary["insufficient"] += 1
            continue

        try:
            bars = bars_loader(
                track.code, signal_date, horizon=max(HORIZONS), end_date=sessions[-1]
            )
        except Exception:  # noqa: BLE001 - 单票价格失败不阻断整批
            summary["errors"] += 1
            continue

        # 最终计算也截断，防止外部/自定义 loader 忽略截止合同。
        bars = [(str(day)[:10], close) for day, close in bars if str(day)[:10] in sessions]
        bars.sort()
        if not bars or str(bars[0][0])[:10] != signal_date:
            summary["insufficient"] += 1
            continue

        outcome = compute_forward_outcome(bars, sessions=sessions)
        if outcome is None:
            summary["insufficient"] += 1
            continue

        prices = dict(bars)
        measurement = {
            "rule_version": OUTCOME_RULE_VERSION, "market": track.market,
            "prices": [(day, prices.get(day)) for day in outcome.expected_dates],
        }
        version = hashlib.sha256(json.dumps(measurement, sort_keys=True).encode()).hexdigest()
        outcome_ref = f"outcome:{track.code}:{signal_date}:{version}"
        previous = service.repo.latest_source_event(track.id, "stock_daily_qfq")
        if previous:
            previous_payload = json.loads(previous.payload_json or "{}")
            previous_cutoff = previous_payload.get("as_of") or previous_payload.get("last_date") or ""
            if previous_cutoff > cutoff.isoformat():
                # 历史重放不能把较晚的测量降为“最新有效结果”。
                summary["newer_measurement"] += 1
                continue
        if previous and previous.source_ref == outcome_ref:
            # 兼容旧版“结果已提交、关闭未提交”的中断记录。
            if outcome.complete and track.status == "tracking":
                if not dry_run:
                    try:
                        service.change_status(track.track_id, "closed", reason=f"{AUTO_CLOSE_REASON_PREFIX}；T+5 {_pct(outcome.ret5)}", expected_revision=track.revision)
                    except Exception as exc:  # noqa: BLE001 - 单票冲突不阻断整批
                        summary["errors"] += 1
                        logger.warning("补关闭失败 code=%s: %s", track.code, exc)
                        continue
                summary["closed"] += 1
            continue

        payload = {**asdict(outcome), **measurement, "as_of": cutoff.isoformat(), "supersedes_event_id": previous.id if previous else None}
        if not dry_run:
            try:
                service.append_evidence(
                    track.track_id,
                    source_type="stock_daily_qfq",
                    source_ref=outcome_ref,
                    source_version=OUTCOME_RULE_VERSION,
                    summary=(f"测量更新，替代 #{previous.id}；" if previous else "") +
                    f"T+1 {_pct(outcome.ret1)} / T+3 {_pct(outcome.ret3)} / T+5 {_pct(outcome.ret5)}",
                    observed_at=utc_naive_now(),
                    quality="complete" if outcome.complete else "partial",
                    limitation="行情版本公开可得时点未核实；" + (
                        "检查点完整" if outcome.complete else f"窗口未完整；缺失日 {','.join(outcome.missing_dates) or '后续交易日未到'}"
                    ),
                    payload=payload,
                    expected_revision=track.revision,
                    close_reason=(
                        f"{AUTO_CLOSE_REASON_PREFIX}；T+5 {_pct(outcome.ret5)}" if outcome.complete else
                        f"{AUTO_CLOSE_REASON_PREFIX}；最新测量受限，查看修订证据" if track.status == "closed" else ""
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - 并发/锁冲突只影响本票，计错误继续
                summary["errors"] += 1
                logger.warning("结果写回失败 code=%s track=%s: %s", track.code, track.track_id, exc)
                continue
        summary["written"] += 1
        if dry_run:
            continue
        if outcome.complete:
            summary["closed"] += int(track.status == "tracking")
        else:
            summary["pending"] += 1
    return summary


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"
