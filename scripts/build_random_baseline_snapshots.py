#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build random-baseline pseudo snapshots mirroring per-day signal counts.

For each signal day of a source signal type with K signals, sample K distinct
traded codes on that same day from `stock_daily` and persist snapshots under
`random_baseline__<source>` with the exact-day close in `metrics_payload`.

The standard evaluator (`scripts/evaluate_signal_snapshot_performance.py`) can
then compute forward returns for this baseline with the SAME cost /
tradability / window rules, giving a "random buy" control distribution.

Sampling notes:
- universe = codes with a close bar on that date (survivorship-free within the
  covered window), excluding KCB / BSE / B-share prefixes (688/689/4/8/9).
- sampling is stratified by board: each day's random rows mirror the source
  signal mix（创业板 30xxxx vs 主板），so the control carries the same board
  composition as the signal set; quota overflow spills to the other board and
  is reported via capped_days.
- deterministic per (seed, source, day): rerunning with the same seed produces
  the same baseline rows.

用法：
    ./.venv-linux/bin/python scripts/build_random_baseline_snapshots.py \
        --source-signal-type hundred_day_high --start-date 2026-04-03 --end-date 2026-09-25
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from src.storage import DatabaseManager

logger = logging.getLogger("build_random_baseline_snapshots")

DEFAULT_SEED = 20260926
EXCLUDED_CODE_PREFIXES = ("688", "689", "4", "8", "9")


def _is_eligible_code(code: str) -> bool:
    return bool(code) and not code.startswith(EXCLUDED_CODE_PREFIXES)


def _load_day_universe(session, day: date) -> list[tuple[str, float]]:
    rows = session.execute(
        text(
            "SELECT code, close FROM stock_daily "
            "WHERE date = :day AND close IS NOT NULL AND close > 0"
        ),
        {"day": day.isoformat()},
    ).fetchall()
    return [(str(code), float(close)) for code, close in rows if _is_eligible_code(str(code))]


def _load_names(session, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    wanted = set(codes)
    try:
        rows = session.execute(text("SELECT code, name FROM stock_basic")).fetchall()
    except Exception:
        return {}
    return {str(code): str(name or "") for code, name in rows if str(code) in wanted}


def _split_universe_by_board(universe: list[tuple[str, float]]) -> tuple[list[str], list[str]]:
    """Split a day's universe into (创业板 30xxxx, 主板/其他) code lists (sorted)."""
    cyb = sorted(str(code) for code, _ in universe if str(code).startswith("30"))
    main = sorted(str(code) for code, _ in universe if not str(code).startswith("30"))
    return cyb, main


def build_random_baseline(
    *,
    db: DatabaseManager,
    source_signal_type: str,
    target_signal_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    seed: int = DEFAULT_SEED,
    dry_run: bool = False,
) -> dict:
    target = target_signal_type or f"random_baseline__{source_signal_type}"
    snapshots = db.get_signal_snapshots(
        signal_type=source_signal_type,
        start_date=start_date,
        end_date=end_date,
    )
    day_counts: Counter[str] = Counter()
    day_codes: dict[str, list[str]] = {}
    for row in snapshots:
        signal_date = getattr(row, "signal_date", None)
        if signal_date is None:
            continue
        metrics = getattr(row, "metrics_payload", None)
        if metrics:
            try:
                if json.loads(str(metrics)).get("is_run_summary"):
                    continue
            except (TypeError, ValueError):
                pass
        day_text = signal_date.isoformat()
        day_counts[day_text] += 1
        day_codes.setdefault(day_text, []).append(str(getattr(row, "code", "") or ""))
    if not day_counts:
        return {
            "source": source_signal_type,
            "target": target,
            "seed": int(seed),
            "days": 0,
            "source_rows": 0,
            "inserted": 0,
            "capped_days": [],
        }

    inserted = 0
    capped_days: list[str] = []
    with db.session_scope() as session:
        for day_text in sorted(day_counts):
            day = date.fromisoformat(day_text)
            wanted = int(day_counts[day_text])
            universe = _load_day_universe(session, day)
            if not universe:
                logger.warning("random baseline: no universe for day=%s, skipped", day_text)
                continue
            universe_cyb, universe_main = _split_universe_by_board(universe)
            day_signal_codes = day_codes.get(day_text, [])
            wanted_cyb = sum(1 for code in day_signal_codes if str(code).startswith("30"))
            quota_cyb = min(wanted_cyb, len(universe_cyb))
            quota_main = min(int(wanted) - quota_cyb, len(universe_main))
            rng = random.Random(f"{int(seed)}:{source_signal_type}:{day_text}")
            picked = rng.sample(universe_cyb, quota_cyb) + rng.sample(universe_main, quota_main)
            if len(picked) < int(wanted):
                capped_days.append(day_text)
            close_by_code = dict(universe)
            names = _load_names(session, picked)
            if not dry_run:
                # 替换语义：重新生成（种子/算法变化）时先清掉当天旧行，避免旧选票累积。
                with db.session_scope() as cleanup_session:
                    cleanup_session.execute(
                        text(
                            "DELETE FROM kline_signal_snapshot "
                            "WHERE signal_type = :t AND signal_date = :d"
                        ),
                        {"t": target, "d": day_text},
                    )
            for code in sorted(picked):
                if dry_run:
                    continue
                db.upsert_signal_snapshot(
                    signal_type=target,
                    signal_date=day_text,
                    code=code,
                    name=names.get(code, ""),
                    criteria_payload={
                        "random_baseline": {
                            "source": source_signal_type,
                            "seed": int(seed),
                        }
                    },
                    metrics_payload={
                        "close": close_by_code[code],
                        "is_random_baseline": True,
                    },
                    history_payload={},
                )
                inserted += 1

    stats = {
        "source": source_signal_type,
        "target": target,
        "seed": int(seed),
        "days": len(day_counts),
        "source_rows": sum(day_counts.values()),
        "inserted": inserted,
        "capped_days": capped_days,
    }
    logger.info(
        "random baseline built: source=%s target=%s days=%s rows=%s inserted=%s capped=%s",
        stats["source"],
        stats["target"],
        stats["days"],
        stats["source_rows"],
        stats["inserted"],
        len(capped_days),
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build random-baseline pseudo snapshots for a signal type.")
    parser.add_argument("--source-signal-type", required=True, help="Source signal type, e.g. hundred_day_high.")
    parser.add_argument("--target-signal-type", default=None, help="Target signal type, default random_baseline__<source>.")
    parser.add_argument("--start-date", default=None, help="Optional inclusive signal start date.")
    parser.add_argument("--end-date", default=None, help="Optional inclusive signal end date.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Random seed, default {DEFAULT_SEED}.")
    parser.add_argument("--dry-run", action="store_true", help="Only report counts, do not write snapshots.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()
    stats = build_random_baseline(
        db=db,
        source_signal_type=str(args.source_signal_type),
        target_signal_type=args.target_signal_type,
        start_date=args.start_date,
        end_date=args.end_date,
        seed=int(args.seed),
        dry_run=bool(args.dry_run),
    )
    print(
        "random_baseline source={source} target={target} days={days} "
        "source_rows={source_rows} inserted={inserted} capped_days={capped} dry_run={dry}".format(
            dry=bool(args.dry_run), capped=len(stats["capped_days"]), **stats
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
