#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按当日只读总览批量建立研究跟踪记录（A2-a 写入入口）。

测试期口径：一天最多 30 条，多来源命中优先；重复执行不产生重复 track。

用法：
    ./.venv-linux/bin/python scripts/track_from_board.py --date 2026-11-06
    ./.venv-linux/bin/python scripts/track_from_board.py --date 2026-11-06 --limit 20
    ./.venv-linux/bin/python scripts/track_from_board.py --date 2026-11-06 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.trading_calendar import get_trading_sessions  # noqa: E402
from src.services.daily_board_service import (  # noqa: E402
    build_board_for_date,
    select_track_candidates,
)
from src.services.research_tracking_service import ResearchTrackingService  # noqa: E402
from src.storage import utc_naive_now  # noqa: E402

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
    for track in service.repo.list_tracks(code=code, limit=50):
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
) -> Tuple[List[Any], Counter]:
    """在写库前做环境/冷却/过热三道闸门；返回（可入池, 跳过统计）。"""
    blocked = blocked_families(regime, tuning_path)
    kept: List[Any] = []
    skipped: Counter = Counter()
    for cand in candidates:
        family = str(getattr(cand, "source", "") or "")
        if family in blocked:
            skipped[f"环境不适配({regime})"] += 1
            continue
        history = _recent_family_tracks(service, str(cand.code), family)
        if history:
            earliest = min(day for _, day in history)
            days = _trading_days_between(earliest, date.fromisoformat(board_date))
            change = _return_since(str(cand.code), earliest.isoformat(), board_date)
            if change is not None and change > overheat_pct:
                skipped["过热"] += 1
                logger.info(
                    "跳过 %s %s：距首次信号 %s 已涨 %.1f%%",
                    cand.code, family, earliest.isoformat(), change,
                )
                continue
            if days is not None and days <= int(cooldown_days):
                skipped["冷却期"] += 1
                logger.info("跳过 %s %s：%s 已有样本（间隔 %s 个交易日）", cand.code, family, earliest.isoformat(), days)
                continue
        kept.append(cand)
    return kept, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="按当日总览批量建立研究跟踪记录")
    parser.add_argument("--date", required=True, help="信号日 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"最多建立条数（默认 {DEFAULT_LIMIT}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument(
        "--allow-defensive",
        action="store_true",
        help="即使市场环境为 defensive 也照常收取趋势类信号（默认按配置跳过）",
    )
    parser.add_argument(
        "--no-cooldown",
        action="store_true",
        help="关闭同票冷却期与过热过滤（仅用于回测对照，不用于日常）",
    )
    args = parser.parse_args()

    board_date = args.date
    try:
        base = date.fromisoformat(board_date)
    except ValueError:
        raise SystemExit(f"日期格式错误：{board_date}")

    board, _ = build_board_for_date(board_date, output_path=None)
    # 先过闸门再截断，避免被环境/冷却过滤掉的名单占满 limit 名额
    pool = select_track_candidates(board.candidates, limit=10 ** 6)

    service = ResearchTrackingService()
    regime = "bypass" if args.allow_defensive else _read_regime()
    gate_skips: Counter = Counter()
    if args.no_cooldown:
        picks = pool[: max(0, int(args.limit))]
    else:
        picks, gate_skips = gate_candidates(service, pool, board_date, regime=regime)
        picks = picks[: max(0, int(args.limit))]
    gate_text = " ".join(f"{key}={value}" for key, value in gate_skips.items())
    print(
        f"[track_from_board] 闸门: 环境={regime} 可入池={len(pool)} 跳过={dict(gate_skips) or '无'}"
    )

    created = 0
    existed = 0
    errors = 0
    for cand in picks:
        hypothesis_key = f"signal:{cand.source}:{board_date}"
        source_ref = cand.source_ref
        if args.dry_run:
            print(f"[plan] {cand.code} {cand.name} <- {cand.source} key={hypothesis_key}")
            continue
        evidence = dict(
            source_type="kline_signal_snapshot", source_ref=source_ref,
            source_version=cand.source_version, available_at=cand.available_at,
            observed_at=utc_naive_now(), quality=cand.quality, limitation=cand.limitation,
            payload={
                "batch_ref": cand.batch_ref, "import_rule": RULE_TAG,
                "source_observed_at": cand.observed_at.isoformat() if cand.observed_at else None,
                "source": cand.source, "reason": cand.reason,
                "source_snapshot": cand.source_snapshot,
            },
        )
        try:
            track, was_created = service.create_or_get(
                market="cn",
                code=cand.code,
                hypothesis_key=hypothesis_key,
                title=f"{cand.code} {cand.name} · {cand.source} · {board_date}",
                business_layer="review",
                horizon="short",
                branch=cand.source.split("__", 1)[0],
                capital_role="watch",
                hypothesis=cand.reason,
                invalidation="",
                next_review_at=datetime.combine(base + timedelta(days=REVIEW_AFTER_DAYS), datetime.min.time()),
                **evidence,
            )
            if not was_created:
                previous = service.repo.latest_source_event(track.id, "kline_signal_snapshot")
                if previous is None or previous.source_ref != source_ref:
                    evidence["payload"]["supersedes_event_id"] = previous.id if previous else None
                    track = service.append_evidence(track.track_id, summary="来源版本更新（保留原假设及历史）", expected_revision=track.revision, **evidence)
            if track.status == "pending" and not service.repo.has_event_type(track.id, "pending"):
                # 信号驱动的 track 由系统自动跟踪：pending -> tracking，到期复核才排得上
                # 重试可恢复首次创建后的中断；人工明确置 pending 的记录不自动推进。
                service.change_status(track.track_id, "tracking", reason="信号入池，自动跟踪", expected_revision=track.revision)
        except Exception as exc:  # noqa: BLE001 - 单条失败（并发/锁）不能拖住其余候选
            errors += 1
            logger.warning("建 track 失败 code=%s: %s", cand.code, exc)
            continue
        created += int(was_created)
        existed += int(not was_created)

    if args.dry_run:
        print(f"[track_from_board] plan={len(picks)} (dry-run，未写库) source={board_date}")
        return 0

    print(
        f"[track_from_board] date={board_date} 候选={len(board.candidates)} "
        f"选中={len(picks)} 新建={created} 已存在={existed} 失败={errors} rule={RULE_TAG}"
        + (f" 闸门跳过: {gate_text}" if gate_text else "")
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
