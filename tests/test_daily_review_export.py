"""daily_review_export 的离线测试（临时 sqlite，不触网）。"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import daily_review_export  # noqa: E402


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "mini.db"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE kline_signal_snapshot "
        "(signal_type TEXT, signal_date TEXT, code TEXT, name TEXT, metrics_payload TEXT)"
    )
    con.execute("CREATE TABLE stock_daily (code TEXT, date TEXT, close REAL, high REAL, low REAL)")
    con.executemany(
        "INSERT INTO kline_signal_snapshot VALUES (?,?,?,?,?)",
        [
            ("hundred_day_high", "2026-09-20", "600001", "测试甲", '{"close": 999.0}'),
            ("hundred_day_high", "2026-09-20", "600002", "测试乙", '{"close": 4.2}'),
            ("hundred_day_high", "2026-09-21", "600003", "测试丙", '{"close": 7.5}'),
        ],
    )
    con.executemany(
        "INSERT INTO stock_daily VALUES (?,?,?,?,?)",
        [
            ("600001", "2026-09-19", 10.0, 10.2, 9.8),
            ("600001", "2026-09-20", 10.5, 10.8, 10.2),
            ("600001", "2026-09-21", 11.0, 11.5, 10.9),
            ("600001", "2026-09-22", 12.0, 12.2, 11.6),
            ("600001", "2026-09-23", 13.0, 13.5, 12.8),
            ("600001", "2026-09-24", 14.0, 14.2, 13.9),
            ("600002", "2026-09-18", 5.0, 5.1, 4.9),
            ("600002", "2026-09-21", 6.0, 6.2, 5.9),
            ("600002", "2026-09-22", 6.6, 6.7, 6.5),
            ("600002", "2026-09-23", 7.2, 7.3, 7.1),
            ("600002", "2026-09-24", 8.0, 8.1, 7.9),
        ],
    )
    con.commit()
    con.close()
    return db_path


def _read_rows(out_path: Path) -> dict:
    with open(out_path, encoding="utf-8-sig") as handle:
        return {row["code"]: row for row in csv.DictReader(handle)}


def _run_main(db_path: Path, out_path: Path, *extra: str) -> int:
    return daily_review_export.main(
        ["--db", str(db_path), "--days", "30", "--signals", "hundred_day_high", "--output", str(out_path), *extra]
    )


def test_daily_mode_prefers_stock_daily(tmp_path):
    db_path = _make_db(tmp_path)
    out_path = tmp_path / "review.csv"
    assert _run_main(db_path, out_path, "--quiet") == 0
    rows = _read_rows(out_path)
    # 600001：忽略快照的 999.0，取 signal_date 当日 DB 收盘
    assert float(rows["600001"]["entry_close"]) == 10.5
    assert float(rows["600001"]["ret1"]) == 4.76
    assert float(rows["600001"]["ret1_net"]) == 4.45
    assert rows["600001"]["win1"] == "win"
    assert float(rows["600001"]["ret3"]) == 23.81
    assert rows["600001"]["ret5"] == ""
    # 600002：signal_date 无当日 bar → 取最近一根（09-18），同样忽略快照 4.2
    assert float(rows["600002"]["entry_close"]) == 5.0
    assert float(rows["600002"]["ret1"]) == 20.0
    assert float(rows["600002"]["ret3"]) == 44.0
    # 600003：DB 无任何 bar → 入场留空（评估器 missing_start_price 同义）
    assert rows["600003"]["entry_close"] == ""
    assert rows["600003"]["ret1"] == ""


def test_snapshot_mode_keeps_legacy_priority(tmp_path):
    db_path = _make_db(tmp_path)
    out_path = tmp_path / "review_snapshot.csv"
    assert _run_main(db_path, out_path, "--quiet", "--entry-mode", "snapshot") == 0
    rows = _read_rows(out_path)
    assert float(rows["600001"]["entry_close"]) == 999.0
    assert float(rows["600002"]["entry_close"]) == 4.2
    assert float(rows["600003"]["entry_close"]) == 7.5


def test_summary_prints_entry_mode(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out_path = tmp_path / "review2.csv"
    assert _run_main(db_path, out_path) == 0
    out = capsys.readouterr().out
    assert "入场口径: daily" in out


def test_latest_close_on_or_before_helper():
    assert daily_review_export._latest_close_on_or_before([], "2026-09-20") is None
    seq = [("2026-09-18", 5.0, 1, 1), ("2026-09-21", 6.0, 1, 1)]
    assert daily_review_export._latest_close_on_or_before(seq, "2026-09-20") == 5.0
    assert daily_review_export._latest_close_on_or_before(seq, "2026-09-21") == 6.0
    assert daily_review_export._latest_close_on_or_before(seq, "2026-09-17") is None
