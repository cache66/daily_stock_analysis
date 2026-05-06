# -*- coding: utf-8 -*-
"""Tests for the board universe template helper script."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
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
    return importlib.import_module("scripts.generate_board_universe_template")


def _build_args(**overrides):
    payload = {
        "boards": "锂矿,猪肉",
        "board_type": "concept",
        "output_file": "",
        "log_level": "INFO",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_parse_args_supports_boards_and_output_file(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_board_universe_template.py",
            "--boards",
            "锂矿,猪肉",
            "--board-type",
            "concept",
            "--output-file",
            str(tmp_path / "board_universe.csv"),
        ],
    )

    args = module.parse_args()

    assert args.boards == "锂矿,猪肉"
    assert args.board_type == "concept"
    assert Path(args.output_file) == (tmp_path / "board_universe.csv")


def test_main_writes_remote_board_rows(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()

    class _RemoteService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            return pd.DataFrame(
                [
                    {"code": "002460", "name": "赣锋锂业", "board_name": board_name, "board_type": board_type},
                    {"code": "002466", "name": "天齐锂业", "board_name": board_name, "board_type": board_type},
                ]
            )

    monkeypatch.setattr(module, "BoardCycleScanService", _RemoteService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(output_file=str(tmp_path / "board_universe.csv"), boards="锂矿"),
    )

    assert module.main() == 0

    output_df = pd.read_csv(tmp_path / "board_universe.csv", dtype=str).fillna("")
    assert len(output_df) == 2
    assert set(output_df["name"]) == {"赣锋锂业", "天齐锂业"}
    assert set(output_df["template_status"]) == {"remote_fetched"}


def test_main_writes_placeholder_row_when_fetch_fails(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()

    class _MixedService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            if board_name == "锂矿":
                return pd.DataFrame(
                    [{"code": "002460", "name": "赣锋锂业", "board_name": board_name, "board_type": board_type}]
                )
            raise RuntimeError("upstream failed")

    monkeypatch.setattr(module, "BoardCycleScanService", _MixedService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(output_file=str(tmp_path / "board_universe.csv")),
    )

    assert module.main() == 0

    output_df = pd.read_csv(tmp_path / "board_universe.csv", dtype=str).fillna("")
    pork_rows = output_df[output_df["board_name"] == "猪肉"]
    assert len(pork_rows) == 1
    assert pork_rows.iloc[0]["template_status"] == "needs_manual_fill"
    assert pork_rows.iloc[0]["template_note"]
    assert pork_rows.iloc[0]["code"] == ""
    assert pork_rows.iloc[0]["name"] == ""
