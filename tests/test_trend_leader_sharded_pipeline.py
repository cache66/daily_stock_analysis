# -*- coding: utf-8 -*-
"""Tests for sharded trend leader pipeline."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import scripts.run_trend_leader_sharded_pipeline as sharded_pipeline


def test_build_shard_command_forces_skip_db_and_shard_args(tmp_path: Path) -> None:
    args = SimpleNamespace(
        signal_type="trend_leader_unified",
        shard_count=4,
        max_workers=3,
        fallback_top_n=20,
        history_lookback_days=365,
        checkpoint_every=50,
        progress_every=25,
        log_level="INFO",
        limit=200,
        no_resume=False,
        disable_prefetch_realtime_quotes=False,
        disable_second_stage_news_search=True,
        disable_second_stage_business_profile=True,
        enrich_top_n=0,
        exclude_st=True,
        exclude_kcb=True,
        exclude_cyb=True,
        universe_codes_file=None,
    )
    command = sharded_pipeline.build_shard_command(
        args,
        snapshot_date=date(2026, 4, 21),
        shard_index=2,
        shard_output_dir=tmp_path / "shard_02",
        checkpoint_path=tmp_path / "cp_02.json",
    )
    assert "--skip-db-persist" in command
    assert "--shard-count" in command and command[command.index("--shard-count") + 1] == "4"
    assert "--shard-index" in command and command[command.index("--shard-index") + 1] == "2"
    assert "--max-workers" in command and command[command.index("--max-workers") + 1] == "3"
    assert "--resume" in command


def test_merge_shard_csvs_deduplicates_by_code(tmp_path: Path) -> None:
    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    pd.DataFrame(
        [
            {"code": "600001", "name": "A1", "overall_score": 80, "leader_gate_score": 70, "trend_score": 60, "capital_score": 50},
            {"code": "600002", "name": "B", "overall_score": 75, "leader_gate_score": 70, "trend_score": 60, "capital_score": 50},
        ]
    ).to_csv(csv_a, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"code": "600001", "name": "A2", "overall_score": 90, "leader_gate_score": 70, "trend_score": 60, "capital_score": 50},
            {"code": "600003", "name": "C", "overall_score": 65, "leader_gate_score": 60, "trend_score": 50, "capital_score": 40},
        ]
    ).to_csv(csv_b, index=False, encoding="utf-8-sig")

    merged_csv = tmp_path / "merged.csv"
    merged_txt = tmp_path / "merged.txt"
    loaded_rows, merged_rows = sharded_pipeline.merge_shard_csvs(
        shard_csv_paths=[csv_a, csv_b],
        merged_csv_path=merged_csv,
        merged_txt_path=merged_txt,
    )

    assert loaded_rows == 4
    assert merged_rows == 3
    merged_df = pd.read_csv(merged_csv, encoding="utf-8-sig")
    assert str(merged_df.iloc[0]["code"]) == "600001"
    assert merged_df.iloc[0]["name"] == "A2"
