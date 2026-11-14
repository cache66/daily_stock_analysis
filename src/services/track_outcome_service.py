# -*- coding: utf-8 -*-
"""研究跟踪的结果回填（A3-a 最小闭环）。

把“信号入池的 track”按固定窗口（T+1/T+3/T+5）算出前向收益，写回事件；
T+5 可用时关闭 track。只读价格，不重算选股、不改资格。

口径：
- 入场价 = 信号日**次一交易日开盘价**（信号盘后才可得，当日收盘买不到）；
  收益 = 第 N 个交易日收盘 / 次日开盘 − 1；
- 检查点按市场交易日历取，缺某天就留空（`missing_dates`），不平移到下一根；
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
from src.services.research_sample import sample_kind as classify_sample
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 5)
OUTCOME_RULE_VERSION = "track-market-sessions-v2-next-open"
# 只测量不结束的研究标记：价格窗口完成≠研究结论，方法自己推进生命周期
MEASUREMENT_ONLY_BRANCH = "measurement_only"
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
    bars: Sequence[Any],
    *,
    sessions: Optional[Sequence[str]] = None,
    market: str = "cn",
) -> Optional[ForwardOutcome]:
    """次日开盘入场的前向结果（按市场日历检查点取价，缺失保留空，不平移）。

    口径（2026-11-10 起，`OUTCOME_RULE_VERSION` 已升级）：

    - 信号日 = bars 的第一天，只用来定位日历，**不作为入场价**；
    - 入场价 = 次一交易日**开盘价**（信号盘后才可得，当日收盘买不到）；
    - `ret{k}` = 入场日之后第 k 个交易日收盘 / 入场开盘 − 1（k=1/3/5），
      即 k 为真实持有交易日数，T+5 需要 signal + 入场 + 5 个检查点共 7 个交易日。

    bars 允许两种形态：`(date, open, close)` 或 `(date, close)`；后者按
    open=close 处理（只用于没有开盘价的旧数据/单测，会因口径退化被标 partial）。
    """
    clean: List[Tuple[str, float, float]] = []
    for row in bars:
        if len(row) >= 3:
            day, open_price, close = row[0], row[1], row[2]
        else:
            day, close = row[0], row[1]
            open_price = close
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if not close_f or not math.isfinite(close_f) or close_f <= 0:
            continue
        try:
            open_f = float(open_price) if open_price is not None else close_f
        except (TypeError, ValueError):
            open_f = close_f
        if not math.isfinite(open_f) or open_f <= 0:
            open_f = close_f
        clean.append((str(day)[:10], open_f, close_f))
    clean.sort()
    if len(clean) < 2:
        return None

    signal_date = clean[0][0]
    opens = {day: open_price for day, open_price, _ in clean}
    closes = {day: close for day, _, close in clean}
    expected = tuple(sessions) if sessions is not None else tuple(
        d.isoformat() for d in get_trading_sessions(
            market, date.fromisoformat(signal_date), date.fromisoformat(clean[-1][0]),
        )
    )
    # 需要 signal + 入场日 + 5 个检查点
    expected = expected[: max(HORIZONS) + 2]
    if not expected or expected[0] != signal_date or len(expected) < 2:
        return None

    entry_date = expected[1]
    entry_price = opens.get(entry_date)
    if not entry_price or entry_price <= 0:
        return None

    prices = closes
    present = [day for day in expected[1:] if day in prices]
    if not present:
        return None
    values: Dict[str, Optional[float]] = {}
    for horizon in HORIZONS:
        # +1：跳过信号日与入场日之间那一段，T+k 从入场日之后算起
        target = expected[horizon + 1] if len(expected) > horizon + 1 else None
        values[f"ret{horizon}"] = (
            prices[target] / entry_price - 1.0 if target and target in prices else None
        )
    return ForwardOutcome(
        entry_date=entry_date,
        entry_close=entry_price,
        last_date=present[-1],
        last_close=prices[present[-1]],
        ret1=values["ret1"],
        ret3=values["ret3"],
        ret5=values["ret5"],
        available_days=len(present),
        expected_dates=expected,
        missing_dates=tuple(day for day in expected[1:] if day not in prices),
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
        "SELECT date, open, close FROM stock_daily WHERE code = :code AND date >= :start "
        + ("AND date <= :end " if end_date else "")
        + "ORDER BY date ASC " + ("" if end_date else "LIMIT :lim")
    )
    params: Dict[str, Any] = {"code": code, "start": signal_date, "lim": max(3, int(horizon) + 2)}
    if end_date:
        params["end"] = end_date
    with db.session_scope() as session:
        rows = session.execute(text(sql), params).fetchall()
    return [
        (str(row[0])[:10],
         float(row[1]) if row[1] is not None else float(row[2]),
         float(row[2]))
        for row in rows
    ]


def evaluate_track_outcomes(
    *,
    as_of: Optional[date] = None,
    limit: int = 500,
    dry_run: bool = False,
    bars_loader=load_bars,
    hypothesis_prefix: str = "signal:",
) -> Dict[str, Any]:
    """为到期的 track 回填结果；返回统计摘要（可 dry-run）。

    ``hypothesis_prefix`` 默认只算前向样本（``signal:``）；传
    ``research:signal:`` 则回放历史信号，结果事件带 ``sample_kind=replay``，
    不与前向成绩混算。
    """
    from src.services.research_tracking_service import ResearchTrackingService

    sample_kind = "replay" if hypothesis_prefix.startswith("research:") else "forward"
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
        "not_due": 0,
        "measured_only": 0,
        "sample_kind": sample_kind,
    }
    suspicious = load_suspicious_codes()

    for track in service.repo.outcome_tracks(
        limit=limit,
        hypothesis_prefix=hypothesis_prefix,
        include_pending=sample_kind == "replay",
    ):
        if not str(track.hypothesis_key).startswith(hypothesis_prefix):
            continue
        signal_date = str(track.hypothesis_key).rsplit(":", 1)[-1]
        try:
            date.fromisoformat(signal_date)
        except ValueError:
            summary["skipped_no_signal"] += 1
            continue
        # 说明：next_review_at 只是人工复核提醒（建档时按信号日+N 个交易日生成），
        # 不是行情可用性合同。测量是否到期由“入场日（D+1）是否已过”决定，见下方
        # sessions 长度判断；否则 T+1/T+3 的中间观测会被复核日推后。

        summary["checked"] += 1

        if str(track.code)[:6] in suspicious:
            summary["skipped_suspicious"] += 1
            continue

        try:
            sessions = [d.isoformat() for d in get_trading_sessions(track.market, date.fromisoformat(signal_date), cutoff)]
        except (ValueError, RuntimeError, ImportError):
            summary["calendar_unavailable"] += 1
            continue
        # 次日开盘入场：需要 signal + 入场日 + 5 个检查点
        sessions = sessions[:max(HORIZONS) + 2]
        if not sessions or sessions[0] != signal_date:
            summary["insufficient"] += 1
            continue
        if len(sessions) < 2:
            # 入场日还没到（截止日仍是信号日）：不是缺数据，只是未到期
            summary["not_due"] += 1
            continue

        try:
            bars = bars_loader(
                track.code, signal_date, horizon=max(HORIZONS) + 1, end_date=sessions[-1]
            )
        except Exception:  # noqa: BLE001 - 单票价格失败不阻断整批
            summary["errors"] += 1
            continue

        # 最终计算也截断，防止外部/自定义 loader 忽略截止合同。
        filtered = []
        for row in bars:
            day = str(row[0])[:10]
            if day not in sessions:
                continue
            if len(row) >= 3:
                filtered.append((day, row[1], row[2]))
            else:
                filtered.append((day, row[1], row[1]))
        bars = filtered
        bars.sort()
        if not bars or str(bars[0][0])[:10] != signal_date:
            summary["insufficient"] += 1
            continue

        outcome = compute_forward_outcome(bars, sessions=sessions)
        if outcome is None:
            summary["insufficient"] += 1
            continue

        # 指纹覆盖入场价与检查点收盘：只修订开盘价也必须产出新版本。
        prices = {row[0]: row[-1] for row in bars}
        measurement = {
            "rule_version": OUTCOME_RULE_VERSION, "market": track.market,
            "entry": [outcome.entry_date, outcome.entry_close],
            "prices": [(day, prices.get(day)) for day in outcome.expected_dates],
        }
        version = hashlib.sha256(json.dumps(measurement, sort_keys=True).encode()).hexdigest()
        outcome_ref = f"outcome:{track.code}:{signal_date}:{version}"
        # 方法类研究（branch=measurement_only）只接价格测量，生命周期由方法确认/失效规则推进；
        # 价格窗口走完不等于研究结束，不能在这里关闭。
        measurement_only = str(getattr(track, "branch", "") or "") == MEASUREMENT_ONLY_BRANCH

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
            if outcome.complete and track.status == "tracking" and not measurement_only:
                if not dry_run:
                    try:
                        service.change_status(track.track_id, "closed", reason=f"{AUTO_CLOSE_REASON_PREFIX}；T+5 {_pct(outcome.ret5)}", expected_revision=track.revision)
                    except Exception as exc:  # noqa: BLE001 - 单票冲突不阻断整批
                        summary["errors"] += 1
                        logger.warning("补关闭失败 code=%s: %s", track.code, exc)
                        continue
                summary["closed"] += 1
            continue

        initial = service.repo.initial_event(track.id)
        track_kind = classify_sample(
            track.hypothesis_key, initial.observed_at if initial else None,
            json.loads(initial.payload_json or "{}") if initial else {},
        )
        payload = {**asdict(outcome), **measurement, "as_of": cutoff.isoformat(),
                   "supersedes_event_id": previous.id if previous else None,
                   "sample_kind": track_kind, "measurement_only": measurement_only}
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
                        "" if measurement_only else
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
            if measurement_only:
                summary["measured_only"] += 1
            else:
                summary["closed"] += int(track.status == "tracking")
        else:
            summary["pending"] += 1
    return summary


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"
