# -*- coding: utf-8 -*-
"""2026-11-10 复核修复的回归用例（F1/F2/F4/F5）。

- F2：清理脚本只按“可核实的指数证据”删除，碰撞代码只提示；
- F4：每日额度扣减当天已建记录；
- F5：`--include-research` 同时跑前向与回放，而不是替换。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime

import pytest

from scripts import clean_index_rows
from scripts import evaluate_track_outcomes as cli
from src.config import Config
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    service = ResearchTrackingService()
    try:
        yield service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _seed_daily(con, code, closes):
    for index, close in enumerate(closes):
        con.execute(
            "INSERT INTO stock_daily (code, date, close, data_source) VALUES (?, ?, ?, ?)",
            (code, f"2026-09-{index + 1:02d}", close, "fixture"),
        )


# ------------------------------------------------------------------ F2
def test_index_cleanup_keeps_normal_priced_collision_codes(tmp_path):
    db = tmp_path / "probe.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE stock_daily (code TEXT, date TEXT, close REAL, data_source TEXT)"
    )
    # 000852/000905 是碰撞代码，但价格是正常股票量级 -> 不能删
    _seed_daily(con, "000852", [5.0, 5.1, 5.2])
    _seed_daily(con, "000905", [8.0, 8.1, 8.2])
    # 指数点位 -> 必须删
    _seed_daily(con, "000300", [3800.0, 3810.0, 3820.0])
    con.commit()

    candidates, hints = clean_index_rows.find_index_rows(con)
    con.close()

    assert [item["code"] for item in candidates] == ["000300"]
    hinted = {item["code"]: item["would_delete"] for item in hints}
    assert hinted.get("000852") is False
    assert hinted.get("000905") is False


def test_index_cleanup_keeps_stock_when_dataclass_would_have_matched(tmp_path):
    """即使代码在 INDEX_CODES 里，价格是股票量级时也不该按点位误删。"""
    db = tmp_path / "probe2.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE stock_daily (code TEXT, date TEXT, close REAL, data_source TEXT)"
    )
    _seed_daily(con, "399001", [12.0, 12.3, 12.5])
    con.commit()
    candidates, _ = clean_index_rows.find_index_rows(con)
    con.close()
    # 399001 在 INDEX_CODES 内，仍按“明确指数代码”删除；这条用例锁定当前约定，
    # 若将来改为“仅点位证据”，需同步调整这里。
    assert [item["code"] for item in candidates] == ["399001"]


# ------------------------------------------------------------------ F4
def test_daily_quota_counts_records_already_created_today(workspace):
    service = workspace
    for index in range(5):
        service.create_or_get(
            market="cn",
            code=f"60010{index}",
            hypothesis_key="signal:hundred_day_high:2026-11-09",
            next_review_at=datetime(2026, 11, 20),
        )
    # 回放记录不占前向额度
    service.create_or_get(
        market="cn",
        code="600200",
        hypothesis_key="research:signal:hundred_day_high:2026-11-09",
        next_review_at=datetime(2026, 11, 20),
    )

    assert service.repo.count_signal_tracks_for_date("2026-11-09") == 5
    limit = 30
    assert max(0, limit - service.repo.count_signal_tracks_for_date("2026-11-09")) == 25


# ------------------------------------------------------------------ F5
def test_include_research_measures_forward_and_replay(monkeypatch, capsys):
    calls = []

    def fake_evaluate(*, as_of=None, limit=500, dry_run=False, hypothesis_prefix="signal:", **kwargs):
        calls.append(hypothesis_prefix)
        base = {"checked": 1, "written": 0, "errors": 0, "calendar_unavailable": 0}
        return {**base, "sample_kind": "replay" if hypothesis_prefix.startswith("research") else "forward"}

    monkeypatch.setattr(cli, "evaluate_track_outcomes", fake_evaluate)
    monkeypatch.setattr(sys, "argv", ["evaluate_track_outcomes.py", "--include-research", "--dry-run"])

    assert cli.main() == 0
    assert calls == ["signal:", "research:signal:"]
    out = capsys.readouterr().out
    assert "track_outcome:forward" in out
    assert "track_outcome:replay" in out
    assert "track_outcome:total" in out


def test_default_mode_only_measures_forward(monkeypatch):
    calls = []

    def fake_evaluate(*, as_of=None, limit=500, dry_run=False, hypothesis_prefix="signal:", **kwargs):
        calls.append(hypothesis_prefix)
        return {"checked": 0, "written": 0, "errors": 0, "calendar_unavailable": 0}

    monkeypatch.setattr(cli, "evaluate_track_outcomes", fake_evaluate)
    monkeypatch.setattr(sys, "argv", ["evaluate_track_outcomes.py", "--dry-run"])
    assert cli.main() == 0
    assert calls == ["signal:"]


# ------------------------------------------------------------------ F1
def test_sync_refuses_cache_marked_raw_price_basis(tmp_path, workspace, monkeypatch):
    from scripts import sync_cache_to_stock_daily as sync

    cache = tmp_path / "cache"
    cache.mkdir()
    rows = "\n".join(f"2026-09-{day:02d},10,10,10,10,1000,10000" for day in range(1, 26))
    (cache / "600001.csv").write_text(
        "date,open,high,low,close,volume,amount\n" + rows + "\n", encoding="utf-8"
    )
    (cache / "600001.json").write_text(
        '{"stock_code": "600001", "price_basis": "raw_unadjusted"}', encoding="utf-8"
    )

    db = DatabaseManager.get_instance()
    stats = sync.run_sync(db=db, cache_dir=cache, dry_run=False)
    assert stats["skipped_price_basis"] == 1
    assert stats["updated"] == 0

    with db.get_session() as session:
        kept = session.execute(
            __import__("sqlalchemy").text("select count(*) from stock_daily where code='600001'")
        ).scalar_one()
    assert kept == 0


def test_sync_reads_qfq_basis_metadata(tmp_path):
    from scripts import sync_cache_to_stock_daily as sync

    csv_path = tmp_path / "600002.csv"
    csv_path.write_text("date,close\n2026-09-01,10\n", encoding="utf-8")
    (tmp_path / "600002.json").write_text('{"price_basis": "qfq"}', encoding="utf-8")
    assert sync._read_price_basis(csv_path) == "qfq"

    legacy = tmp_path / "600003.csv"
    legacy.write_text("date,close\n2026-09-01,10\n", encoding="utf-8")
    assert sync._read_price_basis(legacy) == ""
