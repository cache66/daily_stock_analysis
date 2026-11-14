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
import hashlib
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

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

from src.services.track_admission_service import (  # noqa: E402,F401
    DEFAULT_LIMIT, REVIEW_AFTER_DAYS, RULE_TAG, TUNING_PATH,
    _read_regime, blocked_families, gate_candidates,
)
from src.services.research_sample import FORWARD_ADMISSION_RULE  # noqa: E402

logger = logging.getLogger("track_from_board")

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

    sessions = get_trading_sessions("cn", base, base + timedelta(days=30))
    if base not in sessions or len(sessions) <= REVIEW_AFTER_DAYS:
        raise SystemExit("信号日不是开市日或复核日历不可用")
    review_at = datetime.combine(sessions[REVIEW_AFTER_DAYS], datetime.min.time())
    board, _ = build_board_for_date(board_date, output_path=None)
    # 先过闸门再截断，避免被环境/冷却过滤掉的名单占满 limit 名额
    pool = select_track_candidates(board.candidates, limit=10 ** 6)

    service = ResearchTrackingService()
    regime = "bypass" if args.allow_defensive else _read_regime()
    from src.services.signal_regime import load_cached

    regime_snapshot = load_cached(as_of=board_date)
    signal_verdicts = {} if regime_snapshot.get("error") else {
        family: str(item.get("verdict") or "unknown")
        for family, item in (regime_snapshot.get("families") or {}).items()
    }
    regime_ref = hashlib.sha256(json.dumps(regime_snapshot, sort_keys=True).encode()).hexdigest()
    gate_skips: Counter = Counter()
    picks, gate_skips = gate_candidates(
        service, pool, board_date, regime=regime, apply_cooldown=not args.no_cooldown,
        signal_verdicts=signal_verdicts,
    )
    updates, new = [], []
    for cand in picks:
        existing = service.repo.get_by_identity("cn", cand.code, f"signal:{cand.source}:{board_date}")
        (updates if existing else new).append(cand)
    # 每日额度要扣掉当天已建的记录，否则同日重跑会突破上限（30 → 31 …）
    already_today = service.repo.count_signal_tracks_for_date(board_date)
    quota = max(0, int(args.limit) - already_today)
    picks = updates + new[:quota]
    gate_text = " ".join(f"{key}={value}" for key, value in gate_skips.items())
    print(
        f"[track_from_board] 闸门: 环境={regime} 可入池={len(pool)} 跳过={dict(gate_skips) or '无'} "
        f"当日已建={already_today} 剩余额度={quota}"
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
                "admission_rule": FORWARD_ADMISSION_RULE,
                "signal_regime_ref": regime_ref, "signal_regime_as_of": regime_snapshot.get("as_of"),
                "signal_regime_verdict": signal_verdicts.get(cand.source, "unknown"),
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
                next_review_at=review_at,
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
