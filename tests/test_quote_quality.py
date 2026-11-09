# -*- coding: utf-8 -*-
"""行情质量闸门（跳变分级 / 可疑清单）与批处理隔离的回归测试。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from scripts import track_from_board as importer  # noqa: F401 - 保持入口可导入
from scripts.rebuild_stock_daily_qfq import normalize_and_validate_frame
from src.config import Config
from src.repositories import research_tracking_repo as repo_module
from src.services import daily_board_service as boards
from src.services import quote_quality
from src.services import track_outcome_service as outcomes
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    service = ResearchTrackingService()
    monkeypatch.setattr(outcomes, "load_suspicious_codes", lambda: set())
    monkeypatch.setattr(boards, "load_suspicious_codes", lambda: set())
    try:
        yield db, service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _frame(days, closes):
    return pd.DataFrame(
        dict(
            date=days,
            close=[float(value) for value in closes],
            volume=[10000.0] * len(days),
            amount=[float(value) * 10000.0 for value in closes],
        )
    )


def _plain_days(count: int = 6, start: date = date(2026, 1, 5)) -> list[str]:
    """足够长的正常序列（前 5 根落在上市宽限窗口之外）。"""
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


# ------------------------------------------------------------------ 跳变分级
def test_adjacent_extreme_jump_is_rejected_and_recorded():
    days = _plain_days() + ["2026-01-13"]
    frame = _frame(days, [10.0] * 6 + [15.5])  # +55%，主板、隔 1 个自然日

    with pytest.raises(ValueError, match="极端跳变"):
        normalize_and_validate_frame(frame, "600100")

    assert "600100" in quote_quality.load_suspicious_codes()
    events = [
        json.loads(line)
        for line in quote_quality.SUSPICIOUS_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["reason"] == "extreme_jump" and events[-1]["added"] == ["600100"]


def test_extreme_jump_without_adjacent_session_is_only_flagged():
    days = _plain_days() + ["2026-03-02"]  # 距前值 ~50 天：可能是停牌/复牌，不能定罪
    frame = _frame(days, [10.0] * 6 + [19.0])  # +90%

    result = normalize_and_validate_frame(frame, "600100")

    assert list(result["close"])[-1] == 19.0
    assert "600100" in quote_quality.load_suspicious_codes()


def test_new_listing_grace_window_is_not_rejected():
    frame = _frame(["2026-09-17", "2026-09-18", "2026-09-21"], [20.8, 57.77, 37.72])

    result = normalize_and_validate_frame(frame, "601091")

    assert list(result["close"]) == [20.8, 57.77, 37.72]
    assert "601091" in quote_quality.load_suspicious_codes()  # 仍登记待复核


def test_extreme_jump_can_be_explicitly_allowed():
    days = _plain_days() + ["2026-01-13"]
    frame = _frame(days, [10.0] * 6 + [15.5])

    result = normalize_and_validate_frame(frame, "600100", allow_extreme_jumps=True)

    assert list(result["close"])[-1] == 15.5
    assert "600100" in quote_quality.load_suspicious_codes()


def test_dry_run_does_not_write_the_list():
    days = _plain_days() + ["2026-01-13"]
    frame = _frame(days, [10.0] * 6 + [15.5])

    with pytest.raises(ValueError, match="极端跳变"):
        normalize_and_validate_frame(frame, "600100", persist_suspicious=False)

    assert quote_quality.load_suspicious_codes() == set()


def test_bse_legal_30pct_is_neither_rejected_nor_flagged():
    frame = _frame(["2026-11-05", "2026-11-06"], [10.0, 13.0])

    normalize_and_validate_frame(frame, "920001")

    assert quote_quality.load_suspicious_codes() == set()


# ------------------------------------------------------------------ 清单读写
def test_record_merges_and_replace_reports_added_and_removed(tmp_path):
    target = tmp_path / "codes.txt"
    events = tmp_path / "events.jsonl"

    assert quote_quality.record_suspicious_codes(
        ["600100", "600100", "sh600101"], reason="review_jump", source="t", path=target, events_path=events
    ) == ["600100", "600101"]
    assert quote_quality.record_suspicious_codes(
        ["600100"], reason="review_jump", source="t", path=target, events_path=events
    ) == []
    assert quote_quality.load_suspicious_codes(target) == {"600100", "600101"}

    change = quote_quality.replace_suspicious_codes(
        ["600101"], reason="post_rebuild_scan", source="t", path=target, events_path=events
    )
    assert change == {"added": [], "removed": ["600100"]}
    assert quote_quality.load_suspicious_codes(target) == {"600101"}
    assert not target.with_suffix(target.suffix + ".tmp").exists()


# ------------------------------------------------------------------ 准入与取样
def test_select_track_candidates_excludes_quote_anomaly():
    candidates = [
        boards.Candidate(source="hdh", code="600001", name="A", reason="r", batch_ready=True),
        boards.Candidate(
            source="hdh", code="600002", name="B", reason="r", flags=("行情异常",), batch_ready=True
        ),
    ]

    assert [cand.code for cand in boards.select_track_candidates(candidates, limit=10)] == ["600001"]


def test_auto_close_reason_prefix_is_shared():
    assert outcomes.AUTO_CLOSE_REASON_PREFIX == repo_module.AUTO_CLOSE_REASON_PREFIX


def _track(service, code: str, signal_date: str = "2026-11-06"):
    track, _ = service.create_or_get(
        market="cn",
        code=code,
        hypothesis_key=f"signal:hundred_day_high:{signal_date}",
        next_review_at=datetime(2026, 11, 9),
    )
    return service.change_status(track.track_id, "tracking", reason="fixture")


def test_outcome_tracks_rotates_least_recently_checked_first(workspace):
    db, service = workspace
    tracks = {code: _track(service, code) for code in ("600100", "600101", "600102")}
    with db.session_scope() as session:
        session.execute(
            text("UPDATE research_tracks SET last_checked_at=NULL WHERE id=:id"), {"id": tracks["600100"].id}
        )
        session.execute(
            text("UPDATE research_tracks SET last_checked_at=:at WHERE id=:id"),
            {"at": datetime(2026, 11, 1, 10), "id": tracks["600101"].id},
        )
        session.execute(
            text("UPDATE research_tracks SET last_checked_at=:at WHERE id=:id"),
            {"at": datetime(2026, 11, 8, 10), "id": tracks["600102"].id},
        )

    picked = [track.code for track in service.repo.outcome_tracks(limit=2)]

    assert picked == ["600100", "600101"]


def test_single_track_conflict_does_not_abort_the_batch(workspace, monkeypatch):
    db, service = workspace
    bars = _frame(["2026-11-06", "2026-11-09"], [10.0, 11.0])
    for code in ("600100", "600101"):
        db.save_daily_data(bars, code, "baostock_qfq_rebuild")
        _track(service, code)

    original = ResearchTrackingService.append_evidence

    def sometimes_conflict(self, track_id, **kwargs):
        track = self.repo.get_by_track_id(track_id)
        if track and track.code == "600100":
            raise RuntimeError("revision conflict")
        return original(self, track_id, **kwargs)

    monkeypatch.setattr(ResearchTrackingService, "append_evidence", sometimes_conflict)

    summary = outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 9))

    assert summary["errors"] == 1 and summary["written"] == 1
    conflict = service.repo.get_by_identity("cn", "600100", "signal:hundred_day_high:2026-11-06")
    healthy = service.repo.get_by_identity("cn", "600101", "signal:hundred_day_high:2026-11-06")
    assert [e for e in service.repo.list_events(conflict.id) if e.source_type == "stock_daily_qfq"] == []
    assert [e for e in service.repo.list_events(healthy.id) if e.source_type == "stock_daily_qfq"]
