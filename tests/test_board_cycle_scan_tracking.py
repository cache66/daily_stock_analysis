# -*- coding: utf-8 -*-
"""Tracking/diff tests for the board cycle scan CLI script."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _load_script_module():
    return importlib.import_module("scripts.select_board_cycle_candidates")


def _build_board_result(module, **overrides):
    payload = {
        "board_name": "lithium",
        "board_type": "concept",
        "constituent_count": 8,
        "qualified_stock_count": 3,
        "leader_count": 1,
        "earnings_supported_count": 1,
        "breadth_score": 3.0,
        "leadership_score": 3.0,
        "earnings_support_score": 2.0,
        "structure_score": 2.0,
        "board_cycle_score": 10.0,
        "board_cycle_label": "watch",
        "leader_ratio": 0.125,
        "earnings_supported_ratio": 0.125,
        "board_reason_summary": "baseline",
        "top_leaders": ["Alpha Lithium"],
        "top_earnings_supported": ["Alpha Lithium"],
    }
    payload.update(overrides)
    return module.BoardCycleBoardResult(**payload)


def test_write_outputs_marks_first_run_in_change_summary(tmp_path: Path) -> None:
    module = _load_script_module()
    board_results = [_build_board_result(module)]

    paths = module.write_outputs(
        board_results=board_results,
        stock_results=[],
        output_dir=tmp_path,
        run_context={"snapshot_date": "2026-05-02"},
    )

    assert paths["board_change_summary_csv"].exists()
    assert paths["board_change_summary_md"].exists()
    assert paths["board_tracking_history_csv"].exists()

    change_summary = pd.read_csv(paths["board_change_summary_csv"])
    assert bool(change_summary.loc[0, "is_first_run"]) is True
    assert bool(change_summary.loc[0, "board_updated"]) is False
    assert str(change_summary.loc[0, "update_reasons"]) == "first_run"

    history = pd.read_csv(paths["board_tracking_history_csv"])
    assert len(history) == 1
    assert str(history.loc[0, "board_name"]) == "lithium"


def test_write_outputs_compares_with_previous_run_and_appends_history(tmp_path: Path) -> None:
    module = _load_script_module()
    previous_output_dir = tmp_path / "run_001"
    current_output_dir = tmp_path / "run_002"

    module.write_outputs(
        board_results=[_build_board_result(module)],
        stock_results=[],
        output_dir=previous_output_dir,
        run_context={"snapshot_date": "2026-05-01"},
    )

    current_board = _build_board_result(
        module,
        board_cycle_score=13.5,
        board_cycle_label="strengthening",
        leader_count=2,
        earnings_supported_count=2,
        leader_ratio=0.25,
        earnings_supported_ratio=0.25,
        top_leaders=["Alpha Lithium", "Beta Lithium"],
        top_earnings_supported=["Alpha Lithium", "Gamma Lithium"],
        board_reason_summary="improved",
    )

    paths = module.write_outputs(
        board_results=[current_board],
        stock_results=[],
        output_dir=current_output_dir,
        run_context={"snapshot_date": "2026-05-02"},
    )

    change_summary = pd.read_csv(paths["board_change_summary_csv"])
    assert bool(change_summary.loc[0, "is_first_run"]) is False
    assert bool(change_summary.loc[0, "board_updated"]) is True
    assert float(change_summary.loc[0, "board_cycle_score_delta"]) == 3.5
    assert str(change_summary.loc[0, "previous_board_cycle_label"]) == "watch"
    assert str(change_summary.loc[0, "current_board_cycle_label"]) == "strengthening"
    assert bool(change_summary.loc[0, "top_leaders_changed"]) is True
    assert "label_changed" in str(change_summary.loc[0, "update_reasons"])
    assert "score_changed" in str(change_summary.loc[0, "update_reasons"])
    assert "leaders_changed" in str(change_summary.loc[0, "update_reasons"])
    assert "earnings_count_changed" in str(change_summary.loc[0, "update_reasons"])

    history = pd.read_csv(paths["board_tracking_history_csv"])
    assert len(history) == 2
    assert list(history["snapshot_date"]) == ["2026-05-01", "2026-05-02"]


def test_write_outputs_keeps_board_stable_when_only_small_score_change_exists(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    previous_output_dir = tmp_path / "run_001"
    current_output_dir = tmp_path / "run_002"

    module.write_outputs(
        board_results=[_build_board_result(module)],
        stock_results=[],
        output_dir=previous_output_dir,
        run_context={"snapshot_date": "2026-05-01"},
    )

    paths = module.write_outputs(
        board_results=[
            _build_board_result(
                module,
                board_cycle_score=11.0,
                board_reason_summary="minor improvement",
            )
        ],
        stock_results=[],
        output_dir=current_output_dir,
        run_context={"snapshot_date": "2026-05-02"},
    )

    change_summary = pd.read_csv(paths["board_change_summary_csv"])
    assert bool(change_summary.loc[0, "is_first_run"]) is False
    assert bool(change_summary.loc[0, "board_updated"]) is False
    assert float(change_summary.loc[0, "board_cycle_score_delta"]) == 1.0
    assert str(change_summary.loc[0, "update_reasons"]) == "stable"
