"""patch_history_cache_by_date 的离线测试（合成缓存与行情帧，不触网）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.patch_history_cache_by_date import (  # noqa: E402
    _iter_day_chunks,
    _row_from_frame,
    patch_from_day_frames,
)


def _seed_cache(cache_root: Path, code: str, rows: int = 2, updated_at: float = 1000.0) -> None:
    market_dir = cache_root / "cn"
    market_dir.mkdir(parents=True, exist_ok=True)
    dates = [f"2026-09-2{idx + 2}" for idx in range(rows)]
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [10.0] * rows,
            "high": [10.5] * rows,
            "low": [9.5] * rows,
            "close": [10.2] * rows,
            "volume": [1_000_000.0] * rows,
            "amount": [10_000_000.0] * rows,
            "pct_chg": [0.5] * rows,
        }
    )
    frame.to_csv(market_dir / f"{code}.csv", index=False, encoding="utf-8")
    (market_dir / f"{code}.json").write_text(
        json.dumps(
            {"stock_code": code, "market": "cn", "source": "TushareFetcher", "updated_at": updated_at, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _day_frame(day: str, entries) -> pd.DataFrame:
    rows = []
    for code, extra in entries:
        row = {
            "ts_code": f"{code}.SH" if code.startswith("6") else f"{code}.SZ",
            "trade_date": day,
            "open": 10.0,
            "high": 10.6,
            "low": 9.8,
            "close": 10.3,
            "pre_close": 10.2,
            "change": 0.1,
            "pct_chg": 0.9804,
            "vol": 1234.0,  # 手
            "amount": 5678.9,  # 千元
        }
        row.update(extra)
        rows.append(row)
    return pd.DataFrame(rows)


def test_patch_appends_and_updates_metadata(tmp_path: Path) -> None:
    cache_root = tmp_path / "history"
    _seed_cache(cache_root, "000001")
    _seed_cache(cache_root, "600519")
    frames = {"20260928": _day_frame("20260928", [("000001", {}), ("600519", {})])}

    stats = patch_from_day_frames(frames, cache_root=cache_root)

    assert stats["updated_files"] == 2
    assert stats["rows_added"] == 2
    csv = pd.read_csv(cache_root / "cn" / "000001.csv", dtype={"date": str})
    assert csv["date"].tolist()[-1] == "2026-09-28"
    assert csv["volume"].tolist()[-1] == 1234.0 * 100  # 手 → 股
    assert abs(csv["amount"].tolist()[-1] - 5678.9 * 1000) < 1e-6  # 千元 → 元
    # 列序与缓存口径一致
    assert list(csv.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    meta = json.loads((cache_root / "cn" / "000001.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 3
    assert meta["updated_at"] > 1000.0


def test_patch_is_idempotent(tmp_path: Path) -> None:
    cache_root = tmp_path / "history"
    _seed_cache(cache_root, "000001")
    frames = {"20260928": _day_frame("20260928", [("000001", {})])}

    first = patch_from_day_frames(frames, cache_root=cache_root)
    second = patch_from_day_frames(frames, cache_root=cache_root)

    assert first["updated_files"] == 1
    assert second["updated_files"] == 0
    assert second["skipped_uptodate"] == 1


def test_missing_cache_file_skipped(tmp_path: Path) -> None:
    cache_root = tmp_path / "history"
    _seed_cache(cache_root, "000001")
    frames = {"20260928": _day_frame("20260928", [("000001", {}), ("300750", {})])}

    stats = patch_from_day_frames(frames, cache_root=cache_root)

    assert stats["updated_files"] == 1
    assert stats["skipped_no_file"] == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    cache_root = tmp_path / "history"
    _seed_cache(cache_root, "000001")
    before = (cache_root / "cn" / "000001.csv").read_text(encoding="utf-8")
    frames = {"20260928": _day_frame("20260928", [("000001", {})])}

    stats = patch_from_day_frames(frames, cache_root=cache_root, dry_run=True)

    assert stats["updated_files"] == 1
    assert stats["rows_added"] == 1
    assert (cache_root / "cn" / "000001.csv").read_text(encoding="utf-8") == before


def test_iter_day_chunks() -> None:
    chunks = _iter_day_chunks(date(2026, 1, 1), date(2026, 1, 10), 4)
    assert chunks == [
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"],
        ["2026-01-09", "2026-01-10"],
    ]


def test_row_parser_guards() -> None:
    assert _row_from_frame("20260928", {"ts_code": "BADCODE"}) is None
    assert _row_from_frame("20260928", {"ts_code": "000001.HK"}) is None
    parsed = _row_from_frame(
        "20260928",
        {
            "ts_code": "000001.SZ",
            "open": 10.0,
            "high": 10.5,
            "low": 9.9,
            "close": 10.2,
            "vol": 100.0,
            "amount": 50.0,
            "pct_chg": 0.123456,
        },
    )
    assert parsed is not None
    assert parsed["code"] == "000001"
    assert parsed["row"]["date"] == "2026-09-28"
    assert parsed["row"]["pct_chg"] == 0.1235
    assert _row_from_frame(
        "20260928",
        {"ts_code": "000001.SZ", "open": None, "high": 1, "low": 1, "close": 1, "vol": 1, "amount": 1, "pct_chg": 0},
    ) is None
