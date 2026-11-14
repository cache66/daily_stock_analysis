# -*- coding: utf-8 -*-
"""建 track 前的三道闸门：环境不适配 / 同票冷却期 / 涨幅过热。

背景（2026-11-09）：百日新高 6 月起 T+5 超额转负，但 defensive 环境下仍照收；
同一只票（冰山冷热）10 个交易日内连触 7 次，前 5 次赚钱、后 3 次 T+5 −13%~−17%。
本文件只测闸门本身，选股/批次准入仍由既有回归文件覆盖。
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from scripts import track_from_board as importer
from src.config import Config
from src.repositories import research_tracking_repo as repo_module
from src.services.daily_board_service import Candidate
from src.services import signal_regime
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    service = ResearchTrackingService()
    signal_regime.DEFAULT_OUTPUT.write_text(json.dumps({
        "as_of": "2026-11-06", "schema_version": signal_regime.CACHE_SCHEMA_VERSION,
        "families": {family: {"verdict": "caution"} for family in signal_regime.TRACKED_FAMILIES},
    }), encoding="utf-8")
    try:
        yield db, service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _cand(code: str, source: str) -> Candidate:
    return Candidate(source=source, code=code, name=code, reason="fixture", batch_ready=True)


def _tune(tmp_path, families) -> object:
    path = tmp_path / "tuning.json"
    path.write_text(json.dumps({"defensive_demote_single": families}, ensure_ascii=False), encoding="utf-8")
    return path


def _history(service: ResearchTrackingService, code: str, source: str, signal_date: str):
    track, _ = service.create_or_get(
        market="cn", code=code, hypothesis_key=f"signal:{source}:{signal_date}",
        next_review_at=datetime(2026, 11, 20),
    )
    return service.change_status(track.track_id, "tracking", reason="fixture")


def _bars(db, code: str, rows):
    db.save_daily_data(
        pd.DataFrame([
            dict(date=day, open=p, high=p, low=p, close=p, volume=10000, amount=p * 10000)
            for day, p in rows
        ]), code, "baostock_qfq_rebuild",
    )


# ------------------------------------------------------------------ 环境闸门
def test_defensive_blocks_trend_family_only(tmp_path):
    families = importer.blocked_families("defensive", _tune(tmp_path, ["hundred_day_high"]))
    assert families == ("hundred_day_high",)
    assert importer.blocked_families("risk_on", _tune(tmp_path, ["hundred_day_high"])) == ()
    assert importer.blocked_families("balanced") == ()


def test_gate_skips_trend_signals_in_defensive_regime(workspace, tmp_path, monkeypatch):
    _, service = workspace
    monkeypatch.setattr(importer, "TUNING_PATH", _tune(tmp_path, ["hundred_day_high"]))
    monkeypatch.setattr(importer, "_read_regime", lambda: "defensive")

    kept, skipped = importer.gate_candidates(
        service,
        [_cand("600100", "hundred_day_high"), _cand("600101", "screening__dual_low")],
        "2026-11-06",
        regime="defensive",
        tuning_path=_tune(tmp_path, ["hundred_day_high"]),
    )

    assert [c.code for c in kept] == ["600101"]
    assert skipped["环境不适配(defensive)"] == 1


def test_gate_allows_trend_signals_when_regime_is_healthy(workspace, tmp_path):
    _, service = workspace
    kept, skipped = importer.gate_candidates(
        service,
        [_cand("600100", "hundred_day_high")],
        "2026-11-06",
        regime="risk_on",
        tuning_path=_tune(tmp_path, ["hundred_day_high"]),
    )
    assert [c.code for c in kept] == ["600100"]
    assert not skipped


# ------------------------------------------------------------------ 冷却期
def test_cooldown_skips_same_code_within_window(workspace):
    db, service = workspace
    _history(service, "600100", "hundred_day_high", "2026-11-04")
    _bars(db, "600100", [("2026-11-04", 10.0), ("2026-11-06", 10.5)])

    kept, skipped = importer.gate_candidates(
        service, [_cand("600100", "hundred_day_high")], "2026-11-06", regime="risk_on",
    )

    assert kept == []
    assert skipped["冷却期"] == 1


def test_cooldown_expires_after_window(workspace):
    db, service = workspace
    _history(service, "600100", "hundred_day_high", "2026-10-01")
    _bars(db, "600100", [("2026-10-01", 10.0), ("2026-11-06", 11.0)])

    kept, skipped = importer.gate_candidates(
        service, [_cand("600100", "hundred_day_high")], "2026-11-06", regime="risk_on",
    )

    assert [c.code for c in kept] == ["600100"]
    assert not skipped


def test_cooldown_is_per_source(workspace):
    db, service = workspace
    _history(service, "600100", "hundred_day_high", "2026-11-05")
    _bars(db, "600100", [("2026-11-05", 10.0), ("2026-11-06", 10.2)])

    kept, _ = importer.gate_candidates(
        service, [_cand("600100", "screening__dual_low")], "2026-11-06", regime="risk_on",
    )

    assert [c.code for c in kept] == ["600100"]


# ------------------------------------------------------------------ 过热
def test_overheat_blocks_chasing_after_large_run(workspace):
    db, service = workspace
    _history(service, "000530", "hundred_day_high", "2026-10-14")
    _bars(db, "000530", [("2026-10-14", 6.30), ("2026-11-06", 8.70)])  # +38%

    kept, skipped = importer.gate_candidates(
        service, [_cand("000530", "hundred_day_high")], "2026-11-06", regime="risk_on",
    )

    assert kept == []
    assert skipped["过热"] == 1


def test_overheat_threshold_respects_custom_value(workspace):
    db, service = workspace
    _history(service, "000530", "hundred_day_high", "2026-10-14")
    _bars(db, "000530", [("2026-10-14", 6.30), ("2026-11-06", 8.70)])

    kept, _ = importer.gate_candidates(
        service, [_cand("000530", "hundred_day_high")], "2026-11-06",
        regime="risk_on", overheat_pct=60.0,
    )

    assert [c.code for c in kept] == ["000530"]


def test_missing_price_does_not_block_admission(workspace):
    _, service = workspace
    _history(service, "600100", "hundred_day_high", "2026-10-01")

    kept, _ = importer.gate_candidates(
        service, [_cand("600100", "hundred_day_high")], "2026-11-06", regime="risk_on",
    )

    # 没有价格就不做“过热”判断，但冷却期已过，应当放行
    assert [c.code for c in kept] == ["600100"]


def test_auto_close_prefix_still_shared():
    assert repo_module.AUTO_CLOSE_REASON_PREFIX == "D+5 到期关闭"
