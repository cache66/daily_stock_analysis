#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two-stage sharded runner for trend leader scan: download/scan -> merge -> optional persist."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DatabaseManager

logger = logging.getLogger("trend_leader_sharded_pipeline")
RUN_SUMMARY_CODE = "TL_SUMMARY"


@dataclass
class ShardRunResult:
    shard_index: int
    output_dir: Path
    csv_path: Path
    duration_sec: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trend leader sharded pipeline. Phase-1 runs shard scans only (skip DB persist), "
            "phase-2 merges shard CSVs, then optionally writes merged snapshots."
        )
    )
    parser.add_argument("--snapshot-date", default=None, help="Snapshot date, format YYYY-MM-DD, default today.")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "download", "merge", "persist"],
        help="Pipeline stage: all/download/merge/persist.",
    )
    parser.add_argument("--signal-type", default="trend_leader_unified", help="Signal type for merged persistence.")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "trend_leader_sharded"), help="Output root.")
    parser.add_argument("--shard-count", type=int, default=4, help="Total shard count.")
    parser.add_argument("--parallel-shards", type=int, default=4, help="Concurrent shard processes in download stage.")
    parser.add_argument("--limit", type=int, default=None, help="Optional scan limit before sharding.")
    parser.add_argument("--max-workers", type=int, default=4, help="Per-shard worker count.")
    parser.add_argument("--fallback-top-n", type=int, default=20, help="Fallback top-N for trend scan.")
    parser.add_argument("--history-lookback-days", type=int, default=365, help="History lookback for merged persistence.")
    parser.add_argument("--checkpoint-every", type=int, default=50, help="Checkpoint interval in each shard.")
    parser.add_argument("--progress-every", type=int, default=25, help="Progress interval in each shard.")
    parser.add_argument("--no-resume", action="store_true", help="Disable shard resume.")
    parser.add_argument("--disable-prefetch-realtime-quotes", action="store_true", help="Disable quote prefetch.")
    parser.add_argument("--disable-second-stage-news-search", action="store_true", help="Disable post-select news enrichment.")
    parser.add_argument("--disable-second-stage-business-profile", action="store_true", help="Disable post-select business enrichment.")
    parser.add_argument("--enrich-top-n", type=int, default=0, help="Post-select enrichment top-N in shard runs.")
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--exclude-kcb", action="store_true")
    parser.add_argument("--exclude-cyb", action="store_true")
    parser.add_argument("--universe-codes-file", default=None)
    parser.set_defaults(persist_snapshots=False)
    parser.add_argument("--persist-snapshots", dest="persist_snapshots", action="store_true", help="Persist merged rows into DB.")
    parser.add_argument("--skip-persist-snapshots", dest="persist_snapshots", action="store_false", help="Do not persist merged rows.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_snapshot_date(value: Optional[Any]) -> date:
    if value is None or str(value).strip() == "":
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def run_command(command: Sequence[str]) -> str:
    output_lines: List[str] = []
    process = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    return_code = process.wait()
    output = "".join(output_lines)
    if return_code != 0:
        raise RuntimeError(f"command failed ({return_code}): {' '.join(command)}\n{output}")
    return output


def build_shard_command(
    args: argparse.Namespace,
    *,
    snapshot_date: date,
    shard_index: int,
    shard_output_dir: Path,
    checkpoint_path: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "select_trend_leader_candidates.py"),
        "--snapshot-date",
        snapshot_date.isoformat(),
        "--signal-type",
        str(args.signal_type),
        "--shard-count",
        str(max(1, int(args.shard_count))),
        "--shard-index",
        str(max(0, int(shard_index))),
        "--max-workers",
        str(max(1, int(args.max_workers))),
        "--fallback-top-n",
        str(max(0, int(args.fallback_top_n))),
        "--history-lookback-days",
        str(max(1, int(args.history_lookback_days))),
        "--checkpoint-path",
        str(checkpoint_path),
        "--checkpoint-every",
        str(max(1, int(args.checkpoint_every))),
        "--progress-every",
        str(max(0, int(args.progress_every))),
        "--output-dir",
        str(shard_output_dir),
        "--skip-db-persist",
        "--log-level",
        str(args.log_level),
    ]
    if args.limit is not None and int(args.limit) > 0:
        command.extend(["--limit", str(int(args.limit))])
    if not bool(args.no_resume):
        command.append("--resume")
    if bool(args.disable_prefetch_realtime_quotes):
        command.append("--disable-prefetch-realtime-quotes")
    if bool(args.disable_second_stage_news_search):
        command.append("--disable-second-stage-news-search")
    if bool(args.disable_second_stage_business_profile):
        command.append("--disable-second-stage-business-profile")
    if int(args.enrich_top_n) >= 0:
        command.extend(["--enrich-top-n", str(max(0, int(args.enrich_top_n)))])
    if bool(args.exclude_st):
        command.append("--exclude-st")
    if bool(args.exclude_kcb):
        command.append("--exclude-kcb")
    if bool(args.exclude_cyb):
        command.append("--exclude-cyb")
    if args.universe_codes_file:
        command.extend(["--universe-codes-file", str(args.universe_codes_file)])
    return command


def run_download_stage(args: argparse.Namespace, *, snapshot_date: date, day_dir: Path) -> List[ShardRunResult]:
    shard_root = day_dir / "shards"
    checkpoint_root = day_dir / "checkpoints"
    shard_root.mkdir(parents=True, exist_ok=True)
    checkpoint_root.mkdir(parents=True, exist_ok=True)

    shard_count = max(1, int(args.shard_count))
    parallel_shards = min(max(1, int(args.parallel_shards)), shard_count)
    logger.info("download stage start: shard_count=%s parallel_shards=%s", shard_count, parallel_shards)

    def _run_one(shard_index: int) -> ShardRunResult:
        started = datetime.now()
        shard_dir = shard_root / f"shard_{shard_index:02d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_root / f"shard_{shard_index:02d}.json"
        command = build_shard_command(
            args,
            snapshot_date=snapshot_date,
            shard_index=shard_index,
            shard_output_dir=shard_dir,
            checkpoint_path=checkpoint_path,
        )
        run_command(command)
        csv_path = shard_dir / "trend_leader_unified_candidates.csv"
        return ShardRunResult(
            shard_index=shard_index,
            output_dir=shard_dir,
            csv_path=csv_path,
            duration_sec=round((datetime.now() - started).total_seconds(), 3),
        )

    results: List[ShardRunResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_shards) as executor:
        future_map = {executor.submit(_run_one, i): i for i in range(shard_count)}
        for future in concurrent.futures.as_completed(future_map):
            shard_idx = future_map[future]
            result = future.result()
            logger.info(
                "download stage shard done: shard=%s duration=%.2fs csv=%s",
                shard_idx,
                result.duration_sec,
                result.csv_path,
            )
            results.append(result)
    return sorted(results, key=lambda item: item.shard_index)


def merge_shard_csvs(*, shard_csv_paths: Sequence[Path], merged_csv_path: Path, merged_txt_path: Path) -> Tuple[int, int]:
    frames: List[pd.DataFrame] = []
    loaded_count = 0
    for csv_path in shard_csv_paths:
        if not csv_path.exists():
            logger.warning("shard csv missing, skip: %s", csv_path)
            continue
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        loaded_count += len(df)
        frames.append(df)

    if not frames:
        merged_df = pd.DataFrame(columns=["code", "name", "overall_score"])
    else:
        merged_df = pd.concat(frames, ignore_index=True, sort=False)
        for field in ("overall_score", "leader_gate_score", "trend_score", "capital_score"):
            if field not in merged_df.columns:
                merged_df[field] = 0.0
            merged_df[field] = pd.to_numeric(merged_df[field], errors="coerce").fillna(0.0)
        if "code" not in merged_df.columns:
            merged_df["code"] = ""
        merged_df["code"] = merged_df["code"].astype(str).str.strip()
        merged_df = merged_df[merged_df["code"] != ""].copy()
        merged_df = merged_df.sort_values(
            by=["overall_score", "leader_gate_score", "trend_score", "capital_score", "code"],
            ascending=[False, False, False, False, True],
        )
        merged_df = merged_df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

    merged_csv_path.parent.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(merged_csv_path, index=False, encoding="utf-8-sig")
    codes = merged_df["code"].astype(str).tolist() if "code" in merged_df.columns else []
    merged_txt_path.write_text(("\n".join(codes) + "\n") if codes else "", encoding="utf-8")
    return loaded_count, len(merged_df)


def _build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=code,
        days=max(1, int(lookback_days)),
        before_date=snapshot_date,
    )
    hit_dates = [row.signal_date for row in rows if row.signal_date is not None]
    latest_hit = hit_dates[0] if hit_dates else None
    days_since = (snapshot_date - latest_hit).days if latest_hit is not None else None
    return {
        "lookback_days": max(1, int(lookback_days)),
        "previous_hit_count": len(hit_dates),
        "latest_previous_hit_date": latest_hit.isoformat() if latest_hit is not None else None,
        "days_since_previous_hit": days_since,
        "recent_hit_dates": [item.isoformat() for item in hit_dates[:5]],
    }


def persist_merged_csv(
    *,
    merged_csv_path: Path,
    signal_type: str,
    snapshot_date: date,
    history_lookback_days: int,
    shard_count: int,
    max_workers: int,
    fallback_top_n: int,
    limit: Optional[int],
) -> int:
    db = DatabaseManager.get_instance()
    rows: List[Dict[str, Any]] = []
    if merged_csv_path.exists():
        df = pd.read_csv(merged_csv_path, encoding="utf-8-sig")
        rows = df.to_dict(orient="records")

    criteria_payload = {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "scope": "a_share_unified_pool",
        "strategy": "trend_leader_unified",
        "profiles": ["breakout", "pullback", "hybrid"],
        "source_mode": "sharded_pipeline_merge",
        "shard_count": int(max(1, shard_count)),
        "max_workers": int(max(1, max_workers)),
        "fallback_top_n": int(max(0, fallback_top_n)),
        "limit": int(limit) if limit is not None else None,
    }

    snapshot_rows: List[Dict[str, Any]] = []
    for item in rows:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        name = str(item.get("name") or "").strip() or code
        metrics_payload = {str(k): v for k, v in item.items()}
        summary = str(item.get("strategy_summary") or "").strip() or "trend leader merged shard result"
        snapshot_rows.append(
            {
                "code": code,
                "name": name,
                "criteria_payload": dict(criteria_payload),
                "metrics_payload": metrics_payload,
                "cause_payload": {
                    "reason_summary": summary,
                    "industry_logic": "",
                    "news_logic": "",
                    "technical_logic": "",
                    "theme_label": "trend_leader_unified",
                },
                "history_payload": _build_history_payload(
                    db,
                    signal_type=signal_type,
                    code=code,
                    snapshot_date=snapshot_date,
                    lookback_days=history_lookback_days,
                ),
            }
        )

    if not snapshot_rows:
        snapshot_rows.append(
            {
                "code": RUN_SUMMARY_CODE,
                "name": "趋势龙头运行摘要",
                "criteria_payload": dict(criteria_payload),
                "metrics_payload": {
                    "is_run_summary": True,
                    "run_status": "completed_no_hits",
                    "selected_count": 0,
                    "history_lookback_days": int(max(1, history_lookback_days)),
                    "executed_at": datetime.now().isoformat(timespec="seconds"),
                    "strategy_summary": "当日分片扫描已完成，未命中趋势龙头候选（0）",
                },
                "cause_payload": {
                    "reason_summary": "当日分片扫描已完成，未命中趋势龙头候选（0）",
                    "industry_logic": "",
                    "news_logic": "",
                    "technical_logic": "",
                    "theme_label": "trend_leader_unified",
                },
                "history_payload": {
                    "lookback_days": int(max(1, history_lookback_days)),
                    "previous_hit_count": 0,
                    "latest_previous_hit_date": None,
                    "days_since_previous_hit": None,
                    "recent_hit_dates": [],
                },
            }
        )

    return db.replace_signal_snapshots_for_date(
        signal_type=signal_type,
        signal_date=snapshot_date,
        snapshots=snapshot_rows,
    )


def _read_shard_csv_paths(day_dir: Path, shard_count: int) -> List[Path]:
    return [
        day_dir / "shards" / f"shard_{i:02d}" / "trend_leader_unified_candidates.csv"
        for i in range(max(1, int(shard_count)))
    ]


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)

    day_dir = Path(args.output_dir) / snapshot_date.isoformat()
    merge_dir = day_dir / "merged"
    report_dir = day_dir / "report"
    merge_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    merged_csv_path = merge_dir / "trend_leader_unified_candidates_merged.csv"
    merged_txt_path = merge_dir / "trend_leader_unified_candidates_merged.txt"

    logger.info(
        (
            "sharded pipeline start: stage=%s snapshot_date=%s shard_count=%s parallel_shards=%s "
            "max_workers=%s persist=%s"
        ),
        args.stage,
        snapshot_date.isoformat(),
        max(1, int(args.shard_count)),
        max(1, int(args.parallel_shards)),
        max(1, int(args.max_workers)),
        bool(args.persist_snapshots),
    )

    shard_results: List[ShardRunResult] = []
    if args.stage in {"all", "download"}:
        shard_results = run_download_stage(args, snapshot_date=snapshot_date, day_dir=day_dir)
        if args.stage == "download":
            print(f"snapshot_date={snapshot_date.isoformat()}")
            print("stage=download")
            print(f"shard_count={max(1, int(args.shard_count))}")
            for item in shard_results:
                print(f"shard_{item.shard_index}_csv={item.csv_path}")
            return 0

    if args.stage in {"all", "merge", "persist"}:
        shard_csv_paths = _read_shard_csv_paths(day_dir, max(1, int(args.shard_count)))
        loaded_rows, merged_rows = merge_shard_csvs(
            shard_csv_paths=shard_csv_paths,
            merged_csv_path=merged_csv_path,
            merged_txt_path=merged_txt_path,
        )
        logger.info("merge stage done: loaded_rows=%s merged_rows=%s", loaded_rows, merged_rows)
    else:
        loaded_rows, merged_rows = 0, 0

    persisted_rows = 0
    if args.stage in {"all", "persist"} and bool(args.persist_snapshots):
        persisted_rows = persist_merged_csv(
            merged_csv_path=merged_csv_path,
            signal_type=str(args.signal_type),
            snapshot_date=snapshot_date,
            history_lookback_days=max(1, int(args.history_lookback_days)),
            shard_count=max(1, int(args.shard_count)),
            max_workers=max(1, int(args.max_workers)),
            fallback_top_n=max(0, int(args.fallback_top_n)),
            limit=args.limit,
        )
        logger.info("persist stage done: persisted_rows=%s", persisted_rows)

    summary_lines = [
        f"# Trend Leader Sharded Pipeline ({snapshot_date.isoformat()})",
        "",
        f"- stage: `{args.stage}`",
        f"- signal_type: `{args.signal_type}`",
        f"- shard_count: `{max(1, int(args.shard_count))}`",
        f"- parallel_shards: `{max(1, int(args.parallel_shards))}`",
        f"- max_workers_per_shard: `{max(1, int(args.max_workers))}`",
        f"- loaded_rows: `{loaded_rows}`",
        f"- merged_rows: `{merged_rows}`",
        f"- persisted_rows: `{persisted_rows}`",
        f"- merged_csv: `{merged_csv_path}`",
        f"- merged_txt: `{merged_txt_path}`",
        "",
        "说明：本流水线默认将“分片下载/扫描”和“合并/入库”分阶段执行，不与其它策略同步耦合。",
    ]
    summary_md = report_dir / "trend_leader_sharded_pipeline.md"
    summary_md.write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"snapshot_date={snapshot_date.isoformat()}")
    print(f"stage={args.stage}")
    print(f"merged_rows={merged_rows}")
    print(f"persisted_rows={persisted_rows}")
    print(f"merged_csv={merged_csv_path}")
    print(f"merged_txt={merged_txt_path}")
    print(f"summary_md={summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

