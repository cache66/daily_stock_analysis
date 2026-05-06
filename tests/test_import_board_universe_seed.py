# -*- coding: utf-8 -*-
"""Tests for importing the official local board universe seed."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _load_script_module():
    return importlib.import_module("scripts.import_board_universe_seed")


def test_parse_args_supports_input_and_output_dir(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()
    input_file = tmp_path / "source.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_board_universe_seed.py",
            "--input-file",
            str(input_file),
            "--output-dir",
            str(tmp_path / "seed"),
            "--source-label",
            "recognizability_manual",
            "--expire-after-days",
            "3",
        ],
    )

    args = module.parse_args()

    assert Path(args.input_file) == input_file
    assert Path(args.output_dir) == (tmp_path / "seed")
    assert args.source_label == "recognizability_manual"
    assert args.expire_after_days == 3


def test_import_board_universe_seed_writes_csv_meta_and_summary(tmp_path: Path) -> None:
    module = _load_script_module()
    input_file = tmp_path / "source.csv"
    input_file.write_text(
        "board_name,board_type,code,name,logic_keywords,leader_candidates,belong_boards\n"
        "锂矿,concept,002460,赣锋锂业,锂;锂矿,赣锋锂业;天齐锂业,锂矿;新能源\n"
        "锂矿,concept,002460,赣锋锂业,锂;锂矿,赣锋锂业;天齐锂业,锂矿;新能源\n"
        "猪肉,concept,002714,牧原股份,猪肉;养殖,牧原股份;温氏股份,猪肉\n",
        encoding="utf-8",
    )

    result = module.import_board_universe_seed(
        input_file=input_file,
        output_dir=tmp_path / "seed",
        source_label="recognizability_manual",
        expire_after_days=3,
    )

    assert result["row_count"] == 2
    assert result["board_count"] == 2
    assert result["paths"]["csv"].exists()
    assert result["paths"]["meta"].exists()
    assert result["paths"]["summary"].exists()

    exported_df = pd.read_csv(result["paths"]["csv"], dtype=str).fillna("")
    assert exported_df["board_name"].tolist() == ["锂矿", "猪肉"]
    assert exported_df.loc[0, "logic_keywords"] == "锂;锂矿"

    meta = json.loads(result["paths"]["meta"].read_text(encoding="utf-8"))
    assert meta["source_label"] == "recognizability_manual"
    assert meta["expire_after_days"] == 3
    assert meta["row_count"] == 2
    assert meta["board_count"] == 2

    summary = result["paths"]["summary"].read_text(encoding="utf-8")
    assert "status=imported" in summary
    assert "row_count=2" in summary


def test_import_board_universe_seed_requires_board_name_code_and_name(tmp_path: Path) -> None:
    module = _load_script_module()
    input_file = tmp_path / "source.csv"
    input_file.write_text("board_name,code\n锂矿,002460\n", encoding="utf-8")

    try:
        module.import_board_universe_seed(
            input_file=input_file,
            output_dir=tmp_path / "seed",
            source_label="manual",
            expire_after_days=3,
        )
    except ValueError as exc:
        assert "board_name/code/name" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing required columns")
