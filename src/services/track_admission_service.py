"""工作台共用研究准入；不授予交易资格。"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.trading_calendar import get_trading_sessions
from src.services.market_instruments import is_tradable_stock
from src.services.research_tracking_service import ResearchTrackingService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIMIT = 30
REVIEW_AFTER_DAYS = 3
RULE_TAG = "a2a_completed_batch_v1"
# 环境不适配时不收的趋势类来源；名单沿用 config/daily_plan_tuning.json 的
# defensive_demote_single（与作战计划同一份配置，避免两处维护两份口径）。
DEFAULT_DEMOTE_ON_DEFENSIVE: Tuple[str, ...] = ("hundred_day_high",)
DEFENSIVE_REGIMES = frozenset({"defensive", "risk_off"})
# 同一只票在同一来源上多少交易日内只允许一条样本（避免反复计样本）
COOLDOWN_TRADING_DAYS = 10
# 距该批次首个信号涨幅超过此值视为过热，不再入池（实测：同一票连触 7 次时，后 3 次 T+5 为 −13%~−17%）
OVERHEAT_RETURN_PCT = 30.0
TUNING_PATH = PROJECT_ROOT / "config" / "daily_plan_tuning.json"

logger = logging.getLogger("track_from_board")


def _read_regime() -> str:
    """运行配置里的市场环境（与总览同源）。"""
    profile = PROJECT_ROOT / "config" / "local_strategy_profile.json"
    try:
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return str(data.get("market_regime") or "unknown")


def blocked_families(regime: str, tuning_path: Optional[Path] = None) -> Tuple[str, ...]:
    """该环境不适合自动跟踪的来源；非防守环境返回空。"""
    if str(regime) not in DEFENSIVE_REGIMES:
        return ()
    path = Path(tuning_path) if tuning_path else TUNING_PATH
    try:
        tuning = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_DEMOTE_ON_DEFENSIVE
    families = tuning.get("defensive_demote_single")
    if not isinstance(families, list) or not families:
        return DEFAULT_DEMOTE_ON_DEFENSIVE
    return tuple(str(item) for item in families if str(item).strip())


def _signal_date_of(track: Any, family: str) -> Optional[date]:
    """从 hypothesis_key (signal:<family>:<YYYY-MM-DD>) 取出信号日。"""
    key = str(getattr(track, "hypothesis_key", "") or "")
    prefix = f"signal:{family}:"
    if not key.startswith(prefix):
        return None
    try:
        return date.fromisoformat(key[len(prefix):][:10])
    except ValueError:
        return None


def _recent_family_tracks(service: ResearchTrackingService, code: str, family: str) -> List[Tuple[Any, date]]:
    """该票在同一来源上已有的样本（含信号日）。"""
    out: List[Tuple[Any, date]] = []
    for track in service.repo.signal_history(code, family):
        day = _signal_date_of(track, family)
        if day is not None:
            out.append((track, day))
    return out


def _trading_days_between(start: date, end: date) -> Optional[int]:
    """两个日期之间的交易日数（end 不计入）；日历不可用时返回 None。"""
    if end <= start:
        return 0
    try:
        sessions = get_trading_sessions("cn", start, end)
    except (ValueError, RuntimeError, ImportError):
        return None
    return max(0, len(sessions) - 1)


def _return_since(code: str, start: str, end: str) -> Optional[float]:
    """从 start 收盘到 end 收盘的涨幅；任一端缺价返回 None（不推测）。"""
    from sqlalchemy import text

    from src.storage import DatabaseManager

    try:
        with DatabaseManager.get_instance().session_scope() as session:
            rows = session.execute(
                text(
                    "SELECT date, close FROM stock_daily "
                    "WHERE code=:code AND date IN (:a, :b)"
                ),
                {"code": code, "a": start, "b": end},
            ).fetchall()
    except Exception:  # noqa: BLE001 - 取价失败只影响过热判断，不应阻断入池
        return None
    prices = {str(row[0])[:10]: float(row[1]) for row in rows if row[1]}
    if start not in prices or end not in prices or prices[start] <= 0:
        return None
    return (prices[end] / prices[start] - 1.0) * 100.0


def gate_candidates(
    service: ResearchTrackingService,
    candidates: Sequence[Any],
    board_date: str,
    *,
    regime: str,
    cooldown_days: int = COOLDOWN_TRADING_DAYS,
    overheat_pct: float = OVERHEAT_RETURN_PCT,
    tuning_path: Optional[Path] = None,
    signal_verdicts: Optional[Dict[str, str]] = None,
    apply_cooldown: bool = True,
) -> Tuple[List[Any], Counter]:
    """在写库前做四道闸门：环境不适配 / 信号近端失效 / 冷却期 / 过热。

    ``signal_verdicts`` 来自 `src/services/signal_regime.py`（allow/caution/block）：
    block 不建新记录，caution/allow 可作研究跟踪。传 None 时读目标日缓存；
    缺失、过期或失败时仅阅读；已有身份仍可追加完整批次的修订。
    """
    if signal_verdicts is None:
        from src.services.signal_regime import load_cached

        cached = load_cached(as_of=board_date)
        signal_verdicts = {} if cached.get("error") else {
            family: str(item.get("verdict") or "unknown")
            for family, item in (cached.get("families") or {}).items()
        }
    kept: List[Any] = []
    skipped: Counter = Counter()
    for cand in candidates:
        family = str(cand.source)
        reason = candidate_data_limitation(cand)
        if reason:
            skipped[reason] += 1
            continue
        # 更新既有身份的证据不等于新入池；不得被当天冷却/新环境覆盖掉。
        if service.repo.get_by_identity("cn", str(cand.code), f"signal:{family}:{board_date}"):
            kept.append(cand)
            continue
        if family in blocked_families(regime, tuning_path):
            skipped[f"环境不适配({regime})"] += 1
            continue
        verdict = signal_verdicts.get(family, "unknown")
        if verdict == "block":
            skipped["信号近端失效"] += 1
            continue
        if verdict not in {"allow", "caution"}:
            skipped["信号体检未就绪"] += 1
            continue
        history = [(track, day) for track, day in _recent_family_tracks(service, str(cand.code), family)
                   if day < date.fromisoformat(board_date)]
        if apply_cooldown and history:
            latest = max(day for _, day in history)
            days = _trading_days_between(latest, date.fromisoformat(board_date))
            if days is None:
                skipped["冷却日历不可用"] += 1
                continue
            if days <= int(cooldown_days):
                skipped["冷却期"] += 1
                continue
            # 一次冷却结束即是新的观察批次，过热不永久锚定几年前的首次信号。
            change = _return_since(str(cand.code), latest.isoformat(), board_date)
            if change is not None and change > overheat_pct:
                skipped["过热"] += 1
                continue
        kept.append(cand)
    return kept, skipped



def candidate_data_limitation(cand: Any) -> str:
    """总览、导入和页面共享的数据底线；未知不会显示成健康。"""
    if not is_tradable_stock(str(cand.code)):
        return "非股票对象"
    if not cand.batch_ready:
        return "批次未就绪"
    for flag in cand.flags:
        if flag in {"无本地行情", "行情异常", "行情重建失败", "行情状态未知", "缓存快照", "缓存兜底"} or flag.startswith("行情截至"):
            return flag
    return ""
