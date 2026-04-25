# -*- coding: utf-8 -*-
"""Tests for industry turning-point daily runner."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import scripts.run_industry_turning_point_daily as turning_bundle


def test_normalize_board_targets_accepts_json_text() -> None:
    targets = turning_bundle.normalize_board_targets(
        '[{"board_name":"CPO","board_type":"concept","commodity_hint":"optical_fiber"}]'
    )
    assert len(targets) == 1
    assert targets[0]["board_name"] == "CPO"
    assert targets[0]["board_type"] == "concept"


def test_build_recognizability_command_contains_snapshot_date(tmp_path: Path) -> None:
    args = SimpleNamespace(
        source_signal_type="trend_leader_unified",
        recognizability_top_n=5,
        recognizability_skip_db_persist=False,
        recognizability_signal_type_prefix="board_recognizability",
        history_lookback_days=365,
        log_level="INFO",
    )
    command = turning_bundle.build_recognizability_command(
        args=args,
        snapshot_date=date(2026, 4, 21),
        output_dir=tmp_path,
    )
    assert "collect_board_recognizability_rankings.py" in command[1]
    assert command[command.index("--snapshot-date") + 1] == "2026-04-21"
    assert command[command.index("--top-n") + 1] == "5"
    assert command[command.index("--source-signal-type") + 1] == "trend_leader_unified"


def test_build_board_theme_command_contains_target_fields(tmp_path: Path) -> None:
    args = SimpleNamespace(
        board_theme_limit=200,
        board_theme_max_workers=2,
        board_theme_top_per_subtheme=1,
        board_theme_minimum_subtheme_core_probability="medium",
        log_level="INFO",
    )
    command = turning_bundle.build_board_theme_command(
        args=args,
        target={"board_name": "CPO", "board_type": "concept", "commodity_hint": "optical_fiber"},
        snapshot_date=date(2026, 4, 21),
        output_dir=tmp_path,
    )
    assert "collect_board_theme_core_snapshots.py" in command[1]
    assert command[command.index("--board-name") + 1] == "CPO"
    assert command[command.index("--board-type") + 1] == "concept"
    assert command[command.index("--snapshot-date") + 1] == "2026-04-21"
