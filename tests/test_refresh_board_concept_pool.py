# -*- coding: utf-8 -*-
"""Tests for local board concept pool refresh cache."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import TushareFetcher

import scripts.refresh_board_concept_pool as module


class _FakeFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"board_name": "锂矿", "board_code": "BK001", "board_source": "ths"},
                {"board_name": "猪肉", "board_code": "BK002", "board_source": "ths"},
            ]
        )


class _ExplodingFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        raise AssertionError("should not refresh when cache is still fresh")


class _FailingFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        raise RuntimeError("remote failed")


def _write_meta(meta_path: Path, payload: dict) -> None:
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_refresh_writes_csv_and_meta_on_first_success(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_FakeFetcher(),
    )

    assert result["status"] == "refreshed"
    assert (output_dir / "board_concept_pool.csv").exists()
    assert (output_dir / "board_concept_pool_meta.json").exists()

    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv", dtype=str)
    assert exported_df["board_name"].tolist() == ["锂矿", "猪肉"]

    meta = json.loads((output_dir / "board_concept_pool_meta.json").read_text(encoding="utf-8"))
    assert meta["refresh_status"] == "success"
    assert meta["expire_after_days"] == 3
    assert meta["board_count"] == 2


def test_refresh_skips_when_cache_is_fresh(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"board_name": "锂矿", "board_code": "BK001", "board_source": "ths"}]).to_csv(
        output_dir / "board_concept_pool.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_meta(
        output_dir / "board_concept_pool_meta.json",
        {
            "source": "ths",
            "last_updated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "expire_after_days": 3,
            "refresh_status": "success",
            "board_count": 1,
        },
    )

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_ExplodingFetcher(),
    )

    assert result["status"] == "skipped"
    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv", dtype=str)
    assert exported_df["board_name"].tolist() == ["锂矿"]


def test_refresh_keeps_stale_cache_when_remote_refresh_fails(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"board_name": "猪肉", "board_code": "BK002", "board_source": "ths"}]).to_csv(
        output_dir / "board_concept_pool.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_meta(
        output_dir / "board_concept_pool_meta.json",
        {
            "source": "ths",
            "last_updated_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            "expire_after_days": 3,
            "refresh_status": "success",
            "board_count": 1,
        },
    )

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_FailingFetcher(),
    )

    assert result["status"] == "stale_cache_retained"
    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv", dtype=str)
    assert exported_df["board_name"].tolist() == ["猪肉"]

    failed_meta = json.loads((output_dir / "board_concept_pool_meta.json").read_text(encoding="utf-8"))
    assert failed_meta["refresh_status"] == "failed"
    assert failed_meta["board_count"] == 1
    assert "remote failed" in failed_meta["notes"]


def test_get_board_concept_pool_normalizes_ths_rows() -> None:
    with patch.object(TushareFetcher, "_init_api", return_value=None):
        fetcher = TushareFetcher()
    fetcher._api = MagicMock()
    fetcher.priority = 2

    sample = pd.DataFrame(
        [
            {"industry": "锂矿", "ts_code": "BK001", "exchange": "A"},
            {"industry": "猪肉", "ts_code": "BK002", "exchange": "A"},
        ]
    )

    with patch.object(fetcher, "get_trade_time", return_value="20260502"), patch.object(
        fetcher,
        "_call_api_with_rate_limit",
        return_value=sample.copy(),
    ):
        result = fetcher.get_board_concept_pool(source="ths")

    assert list(result.columns) == ["board_name", "board_code", "board_source"]
    assert result["board_name"].tolist() == ["锂矿", "猪肉"]
    assert result["board_source"].tolist() == ["ths", "ths"]
