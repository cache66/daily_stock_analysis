# -*- coding: utf-8 -*-
"""信号体检（signal_regime）：判定阈值、缺缓存不误杀、回放样本不计入前向成绩。

背景（2026-11-09）：单个 defensive 开关只能一刀切；这里按“该信号近端 T+5 超额 +
胜率”逐信号判 allow/caution/block，准入与经验卡共用同一份结果。
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest

from scripts import track_from_board as importer
from src.config import Config
from src.services import signal_regime
from src.services.daily_board_service import Candidate
from src.services.research_tracking_service import ResearchTrackingService
from src.services import track_outcome_service as outcomes
from src.storage import DatabaseManager


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    service = ResearchTrackingService()
    signal_regime.DEFAULT_OUTPUT.write_text(json.dumps({
        "as_of": "2026-11-06", "schema_version": signal_regime.CACHE_SCHEMA_VERSION,
        "families": {family: {"verdict": "caution"} for family in signal_regime.TRACKED_FAMILIES},
    }), encoding="utf-8")
    monkeypatch.setattr(outcomes, "load_suspicious_codes", lambda: set())
    try:
        yield service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _cand(code: str, source: str) -> Candidate:
    return Candidate(source=source, code=code, name=code, reason="fixture", batch_ready=True)


# --------------------------------------------------------------- 判定阈值
@pytest.mark.parametrize(
    "sample,short_excess,short_win,long_excess,expected",
    [
        (20, -4.0, 35.0, -1.6, "block"),   # 近端超额为负 + 胜率过低
        (4, -4.0, 20.0, -5.0, "caution"),  # 样本不足不判停用
        (30, 0.5, 55.0, -3.5, "block"),    # 长窗很差也会停用
        (20, -1.0, 60.0, -0.5, "caution"),  # 轻微负超额只提示
        (20, 1.2, 60.0, 2.0, "allow"),     # 近端长窗都正
    ],
)
def test_verdict_thresholds(sample, short_excess, short_win, long_excess, expected):
    verdict, reason = signal_regime._verdict(sample, short_excess, short_win, long_excess)
    assert verdict == expected
    assert reason


def test_missing_cache_is_unknown_not_block(tmp_path):
    assert signal_regime.verdict_for("hundred_day_high", tmp_path / "missing.json") == "unknown"
    assert signal_regime.load_cached(tmp_path / "missing.json")["error"] == "cache_missing"


def test_cached_verdict_is_read(tmp_path):
    path = tmp_path / "regime.json"
    path.write_text(json.dumps({"families": {"hundred_day_high": {"verdict": "block"}}}), encoding="utf-8")
    assert signal_regime.verdict_for("hundred_day_high", path) == "block"
    assert signal_regime.verdict_for("screening__dual_low", path) == "unknown"


# --------------------------------------------------------------- 准入衔接
def test_gate_drops_blocked_family_and_keeps_caution(workspace, tmp_path):
    verdicts = {"hundred_day_high": "block", "screening__dual_low": "caution"}
    kept, skipped = importer.gate_candidates(
        workspace,
        [_cand("600100", "hundred_day_high"), _cand("600101", "screening__dual_low")],
        "2026-11-06",
        regime="risk_on",
        tuning_path=tmp_path / "absent.json",
        signal_verdicts=verdicts,
    )
    assert [c.code for c in kept] == ["600101"]
    assert skipped["信号近端失效"] == 1
    assert "冷却期" not in skipped


def test_gate_uses_cached_verdict_when_not_passed(workspace, monkeypatch, tmp_path):
    path = tmp_path / "regime.json"
    path.write_text(json.dumps({"as_of": "2026-11-06", "schema_version": signal_regime.CACHE_SCHEMA_VERSION,
                               "families": {"hundred_day_high": {"verdict": "block"}}}), encoding="utf-8")
    monkeypatch.setattr(signal_regime, "DEFAULT_OUTPUT", path)
    kept, skipped = importer.gate_candidates(
        workspace, [_cand("600100", "hundred_day_high")], "2026-11-06",
        regime="risk_on", tuning_path=tmp_path / "absent.json",
    )
    assert not kept and skipped["信号近端失效"] == 1


# --------------------------------------------------------------- 回放样本隔离
def test_research_replay_tracks_do_not_enter_forward_outcomes(workspace, monkeypatch):
    service = workspace
    service.create_or_get(
        market="cn", code="600100", hypothesis_key="research:signal:hundred_day_high:2026-11-04",
        available_at=datetime(2026, 11, 4, 7), next_review_at=datetime(2026, 11, 20),
    )
    db = DatabaseManager.get_instance()
    db.save_daily_data(
        pd.DataFrame([
            dict(date="2026-11-04", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e4, amount=1e5),
            dict(date="2026-11-05", open=11.0, high=11.0, low=11.0, close=11.0, volume=1e4, amount=1e5),
        ]), "600100", "baostock_qfq_rebuild",
    )
    monkeypatch.setattr(outcomes, "get_trading_sessions", lambda *a, **k: ["2026-11-04", "2026-11-05"])

    assert service.repo.outcome_tracks(limit=50, closed_reason_prefix="") == []
    result = outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 9))
    assert result["checked"] == 0
    events = service.repo.list_events(service.repo.list_tracks()[0].id)
    assert [e.source_type for e in events if e.source_type == "stock_daily_qfq"] == []
