# -*- coding: utf-8 -*-
"""Tests for the board cycle scan CLI script."""

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
    return importlib.import_module("scripts.select_board_cycle_candidates")


def _build_args(**overrides):
    payload = {
        "boards": "锂矿",
        "board_type": "concept",
        "snapshot_date": "2026-05-02",
        "top_per_board": 3,
        "limit_per_board": None,
        "max_workers": 1,
        "board_universe_file": None,
        "board_universe_cache_dir": None,
        "use_local_cache": False,
        "output_dir": "",
        "log_level": "INFO",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_parse_args_supports_board_list_and_output_dir(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "select_board_cycle_candidates.py",
            "--boards",
            "锂矿,白酒",
            "--board-type",
            "concept",
            "--board-universe-file",
            str(tmp_path / "board_universe.csv"),
            "--board-universe-cache-dir",
            str(tmp_path / "cache"),
            "--use-local-cache",
            "--output-dir",
            str(tmp_path),
        ],
    )

    args = module.parse_args()

    assert args.boards == "锂矿,白酒"
    assert args.board_type == "concept"
    assert Path(args.output_dir) == tmp_path
    assert Path(args.board_universe_file) == (tmp_path / "board_universe.csv")
    assert Path(args.board_universe_cache_dir) == (tmp_path / "cache")
    assert args.use_local_cache is True


def test_parse_board_names_strips_and_deduplicates() -> None:
    module = _load_script_module()

    board_names = module.parse_board_names(" 锂矿,白酒,锂矿 ,  ")

    assert board_names == ["锂矿", "白酒"]


def test_write_outputs_emits_summary_and_candidates_files(tmp_path: Path) -> None:
    module = _load_script_module()
    board_results = [
        module.BoardCycleBoardResult(
            board_name="锂矿",
            board_type="concept",
            constituent_count=5,
            qualified_stock_count=2,
            leader_count=1,
            earnings_supported_count=1,
            breadth_score=4.0,
            leadership_score=3.0,
            earnings_support_score=2.0,
            structure_score=4.0,
            board_cycle_score=13.0,
            board_cycle_label="strengthening",
            leader_ratio=0.2,
            earnings_supported_ratio=0.2,
            board_reason_summary="前排龙头明确，业绩支撑占比尚可。",
            top_leaders=["赣锋锂业"],
            top_earnings_supported=["天齐锂业"],
        )
    ]
    stock_results = [
        module.BoardStockEvaluation(
            stock_code="002460",
            stock_name="赣锋锂业",
            board_name="锂矿",
            board_type="concept",
            stock_role="leader",
            board_stock_score=9.0,
            logic_match_score=3.0,
            board_leader_score=4.0,
            earnings_support_score=2.0,
            earnings_supported=True,
            board_rank=1,
            selection_reason="rank#1 | role=leader | total=9.00",
            primary_board="锂矿",
            primary_board_reason="logic_match=3.00",
            related_boards=["固态电池"],
        )
    ]

    paths = module.write_outputs(
        board_results=board_results,
        stock_results=stock_results,
        output_dir=tmp_path,
        run_context={"snapshot_date": "2026-05-02", "max_workers": 1},
    )

    assert paths["board_summary_csv"].exists()
    assert paths["board_summary_md"].exists()
    assert paths["board_stock_candidates_csv"].exists()
    assert paths["board_stock_candidates_md"].exists()
    assert paths["run_summary_txt"].exists()

    board_summary = pd.read_csv(paths["board_summary_csv"])
    stock_summary = pd.read_csv(paths["board_stock_candidates_csv"], dtype={"stock_code": str})
    assert list(board_summary["board_name"]) == ["锂矿"]
    assert float(board_summary.loc[0, "leader_ratio"]) == 0.2
    assert str(board_summary.loc[0, "board_reason_summary"]) == "前排龙头明确，业绩支撑占比尚可。"
    assert list(stock_summary["stock_code"]) == ["002460"]
    assert int(stock_summary.loc[0, "board_rank"]) == 1
    assert str(stock_summary.loc[0, "selection_reason"]) == "rank#1 | role=leader | total=9.00"
    run_summary = paths["run_summary_txt"].read_text(encoding="utf-8")
    assert "snapshot_date=2026-05-02" in run_summary
    assert "max_workers=1" in run_summary


def test_main_runs_service_flow_and_writes_outputs(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()

    board_eval = module.BoardStockEvaluation(
        stock_code="002460",
        stock_name="赣锋锂业",
        board_name="锂矿",
        board_type="concept",
        stock_role="leader",
        board_stock_score=9.0,
        logic_match_score=3.0,
        board_leader_score=5.0,
        earnings_support_score=1.0,
        earnings_supported=True,
    )
    board_result = module.BoardCycleBoardResult(
        board_name="锂矿",
        board_type="concept",
        constituent_count=1,
        qualified_stock_count=1,
        leader_count=1,
        earnings_supported_count=1,
        breadth_score=1.0,
        leadership_score=3.0,
        earnings_support_score=2.0,
        structure_score=3.0,
        board_cycle_score=9.0,
        board_cycle_label="strengthening",
        leader_ratio=1.0,
        earnings_supported_ratio=1.0,
        board_reason_summary="龙头集中，业绩支撑明确。",
        top_leaders=["赣锋锂业"],
        top_earnings_supported=["赣锋锂业"],
    )

    class _FakeService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            return pd.DataFrame(
                [
                    {
                        "code": "002460",
                        "name": "赣锋锂业",
                        "board_name": board_name,
                        "board_type": board_type,
                    }
                ]
            )

        def scan_board_rows(self, *, board_name: str, board_type: str, stock_rows):
            return board_result, [board_eval]

        def resolve_primary_boards_for_results(self, evaluations):
            evaluations[0].primary_board = "锂矿"
            evaluations[0].primary_board_reason = "logic_match=3.00"
            evaluations[0].related_boards = []
            return evaluations

        def limit_stock_results_per_board(self, evaluations, *, top_per_board: int):
            return list(evaluations)[:top_per_board]

    monkeypatch.setattr(module, "BoardCycleScanService", _FakeService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(output_dir=str(tmp_path)),
    )

    exit_code = module.main()

    assert exit_code == 0
    assert (tmp_path / "board_summary.csv").exists()
    assert (tmp_path / "board_summary.md").exists()
    assert (tmp_path / "board_stock_candidates.csv").exists()
    assert (tmp_path / "board_stock_candidates.md").exists()
    assert (tmp_path / "run_summary.txt").exists()
    run_summary = (tmp_path / "run_summary.txt").read_text(encoding="utf-8")
    assert "snapshot_date=2026-05-02" in run_summary
    assert "max_workers=1" in run_summary


def test_main_returns_one_when_service_raises(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()

    class _FailingService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(module, "BoardCycleScanService", _FailingService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(output_dir=str(tmp_path), max_workers=2),
    )

    assert module.main() == 1


def test_main_writes_warning_when_board_constituents_are_empty(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_script_module()

    class _EmptyBoardService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

        def scan_board_rows(self, *, board_name: str, board_type: str, stock_rows):
            return (
                module.BoardCycleBoardResult(
                    board_name=board_name,
                    board_type=board_type,
                    constituent_count=0,
                    qualified_stock_count=0,
                    leader_count=0,
                    earnings_supported_count=0,
                    breadth_score=0.0,
                    leadership_score=0.0,
                    earnings_support_score=0.0,
                    structure_score=0.0,
                    board_cycle_score=0.0,
                    board_cycle_label="idle",
                    board_reason_summary="板块成分股暂未取到。",
                    top_leaders=[],
                    top_earnings_supported=[],
                ),
                [],
            )

        def resolve_primary_boards_for_results(self, evaluations):
            return list(evaluations)

        def limit_stock_results_per_board(self, evaluations, *, top_per_board: int):
            return list(evaluations)

    monkeypatch.setattr(module, "BoardCycleScanService", _EmptyBoardService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(
            boards="锂矿,猪肉",
            board_type="auto",
            limit_per_board=20,
            output_dir=str(tmp_path),
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    run_summary = (tmp_path / "run_summary.txt").read_text(encoding="utf-8")
    board_summary_md = (tmp_path / "board_summary.md").read_text(encoding="utf-8")
    assert "warning=" in run_summary
    assert "锂矿" in run_summary
    assert "猪肉" in run_summary
    assert "Note:" in board_summary_md


def test_main_uses_board_universe_file_when_provided(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()
    universe_path = tmp_path / "board_universe.csv"
    universe_path.write_text(
        "board_name,board_type,code,name,logic_keywords,leader_candidates\n"
        "锂矿,concept,002460,赣锋锂业,锂;锂矿,赣锋锂业;天齐锂业\n"
        "猪肉,concept,002714,牧原股份,猪肉;养殖,牧原股份;温氏股份\n",
        encoding="utf-8",
    )

    class _FileOnlyService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            raise AssertionError("remote fetch should not be called when file is provided")

        def scan_board_rows(self, *, board_name: str, board_type: str, stock_rows):
            row = stock_rows[0]
            return (
                module.BoardCycleBoardResult(
                    board_name=board_name,
                    board_type=board_type,
                    constituent_count=len(stock_rows),
                    qualified_stock_count=1,
                    leader_count=1,
                    earnings_supported_count=1,
                    breadth_score=1.0,
                    leadership_score=3.0,
                    earnings_support_score=2.0,
                    structure_score=2.0,
                    board_cycle_score=8.0,
                    board_cycle_label="strengthening",
                    leader_ratio=1.0,
                    earnings_supported_ratio=1.0,
                    board_reason_summary=f"{board_name} local",
                    top_leaders=[row["name"]],
                    top_earnings_supported=[row["name"]],
                ),
                [
                    module.BoardStockEvaluation(
                        stock_code=row["code"],
                        stock_name=row["name"],
                        board_name=board_name,
                        board_type=board_type,
                        stock_role="leader",
                        board_stock_score=8.0,
                        logic_match_score=2.0,
                        board_leader_score=4.0,
                        earnings_support_score=2.0,
                        earnings_supported=True,
                    )
                ],
            )

        def resolve_primary_boards_for_results(self, evaluations):
            for item in evaluations:
                item.primary_board = item.board_name
                item.primary_board_reason = "local"
            return list(evaluations)

        def limit_stock_results_per_board(self, evaluations, *, top_per_board: int):
            return list(evaluations)

    monkeypatch.setattr(module, "BoardCycleScanService", _FileOnlyService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(
            boards="锂矿,猪肉",
            board_type="concept",
            board_universe_file=str(universe_path),
            output_dir=str(tmp_path / "out"),
        ),
    )

    assert module.main() == 0
    board_summary = pd.read_csv(tmp_path / "out" / "board_summary.csv")
    assert set(board_summary["board_name"]) == {"锂矿", "猪肉"}


def test_main_uses_default_seed_file_when_explicit_file_missing(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_script_module()
    seed_dir = tmp_path / "official_seed"
    seed_dir.mkdir()
    seed_file = seed_dir / "board_universe.csv"
    seed_meta = seed_dir / "board_universe_meta.json"
    seed_file.write_text(
        "board_name,board_type,code,name,logic_keywords,leader_candidates\n"
        "閿傜熆,concept,002460,璧ｉ攱閿備笟,閿?閿傜熆,璧ｉ攱閿備笟;澶╅綈閿備笟\n",
        encoding="utf-8",
    )
    seed_meta.write_text(
        '{"source_label":"manual","expire_after_days":3,"imported_at":"2026-05-02T09:00:00+08:00"}',
        encoding="utf-8",
    )

    class _DefaultSeedService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            raise AssertionError("remote fetch should not be called when official seed exists")

        def scan_board_rows(self, *, board_name: str, board_type: str, stock_rows):
            row = stock_rows[0]
            return (
                module.BoardCycleBoardResult(
                    board_name=board_name,
                    board_type=board_type,
                    constituent_count=len(stock_rows),
                    qualified_stock_count=1,
                    leader_count=1,
                    earnings_supported_count=1,
                    breadth_score=1.0,
                    leadership_score=3.0,
                    earnings_support_score=2.0,
                    structure_score=2.0,
                    board_cycle_score=8.0,
                    board_cycle_label="strengthening",
                    leader_ratio=1.0,
                    earnings_supported_ratio=1.0,
                    board_reason_summary=f"{board_name} local",
                    top_leaders=[row["name"]],
                    top_earnings_supported=[row["name"]],
                ),
                [
                    module.BoardStockEvaluation(
                        stock_code=row["code"],
                        stock_name=row["name"],
                        board_name=board_name,
                        board_type=board_type,
                        stock_role="leader",
                        board_stock_score=8.0,
                        logic_match_score=2.0,
                        board_leader_score=4.0,
                        earnings_support_score=2.0,
                        earnings_supported=True,
                    )
                ],
            )

        def resolve_primary_boards_for_results(self, evaluations):
            for item in evaluations:
                item.primary_board = item.board_name
                item.primary_board_reason = "official_seed"
            return list(evaluations)

        def limit_stock_results_per_board(self, evaluations, *, top_per_board: int):
            return list(evaluations)

    monkeypatch.setattr(module, "BoardCycleScanService", _DefaultSeedService)
    monkeypatch.setattr(module, "DEFAULT_BOARD_UNIVERSE_SEED_FILE", seed_file)
    monkeypatch.setattr(module, "DEFAULT_BOARD_UNIVERSE_SEED_META_FILE", seed_meta)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(
            boards="閿傜熆",
            board_type="concept",
            output_dir=str(tmp_path / "out"),
        ),
    )

    assert module.main() == 0
    run_summary = (tmp_path / "out" / "run_summary.txt").read_text(encoding="utf-8")
    assert str(seed_file) in run_summary


def test_main_falls_back_to_cached_board_universe(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "锂矿.csv").write_text(
        "board_name,board_type,code,name\n锂矿,concept,002460,赣锋锂业\n",
        encoding="utf-8",
    )

    class _CacheFallbackService:
        def fetch_board_universe(self, *, board_name: str, board_type: str):
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

        def scan_board_rows(self, *, board_name: str, board_type: str, stock_rows):
            return (
                module.BoardCycleBoardResult(
                    board_name=board_name,
                    board_type=board_type,
                    constituent_count=len(stock_rows),
                    qualified_stock_count=1 if stock_rows else 0,
                    leader_count=1 if stock_rows else 0,
                    earnings_supported_count=1 if stock_rows else 0,
                    breadth_score=1.0 if stock_rows else 0.0,
                    leadership_score=3.0 if stock_rows else 0.0,
                    earnings_support_score=2.0 if stock_rows else 0.0,
                    structure_score=2.0 if stock_rows else 0.0,
                    board_cycle_score=8.0 if stock_rows else 0.0,
                    board_cycle_label="strengthening" if stock_rows else "idle",
                    leader_ratio=1.0 if stock_rows else 0.0,
                    earnings_supported_ratio=1.0 if stock_rows else 0.0,
                    board_reason_summary="cached",
                    top_leaders=["赣锋锂业"] if stock_rows else [],
                    top_earnings_supported=["赣锋锂业"] if stock_rows else [],
                ),
                [],
            )

        def resolve_primary_boards_for_results(self, evaluations):
            return list(evaluations)

        def limit_stock_results_per_board(self, evaluations, *, top_per_board: int):
            return list(evaluations)

    monkeypatch.setattr(module, "BoardCycleScanService", _CacheFallbackService)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: _build_args(
            boards="锂矿",
            board_type="concept",
            board_universe_cache_dir=str(cache_dir),
            use_local_cache=True,
            output_dir=str(tmp_path / "out"),
        ),
    )

    assert module.main() == 0
    run_summary = (tmp_path / "out" / "run_summary.txt").read_text(encoding="utf-8")
    assert "cached board universe" in run_summary


def test_cached_board_universe_roundtrip_preserves_list_fields(tmp_path: Path) -> None:
    module = _load_script_module()
    cache_dir = tmp_path / "cache"
    records = [
        {
            "board_name": "锂矿",
            "board_type": "concept",
            "code": "002460",
            "name": "赣锋锂业",
            "logic_keywords": ["锂", "锂矿"],
            "leader_candidates": ["赣锋锂业", "天齐锂业"],
            "belong_boards": ["锂矿", "新能源车"],
        }
    ]

    module._write_cached_board_universe(cache_dir, board_name="锂矿", records=records)
    loaded = module._load_cached_board_universe(
        cache_dir,
        board_name="锂矿",
        board_type="concept",
    )

    assert loaded[0]["logic_keywords"] == ["锂", "锂矿"]
    assert loaded[0]["leader_candidates"] == ["赣锋锂业", "天齐锂业"]
    assert loaded[0]["belong_boards"] == ["锂矿", "新能源车"]
