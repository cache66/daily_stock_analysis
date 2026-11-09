# -*- coding: utf-8 -*-
"""A2-a 研究跟踪服务合同测试。

覆盖：身份幂等、同股多假设并存、事件只追加、乐观并发冲突、
状态机约束、到期复核查询。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from src.config import Config
from src.repositories.research_tracking_repo import ResearchTrackingRepository
from src.services.research_tracking_service import (
    ResearchTrackingService,
    TrackRevisionConflict,
)
from src.storage import DatabaseManager


@pytest.fixture()
def service(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "research_tracking.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield ResearchTrackingService(ResearchTrackingRepository(db))
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _create(service, **overrides):
    kwargs = dict(
        market="cn",
        code="600519",
        hypothesis_key="long:dividend_recovery",
        title="贵州茅台 现金回报修复",
        business_layer="longterm",
        horizon="long",
        branch="config",
        capital_role="guard",
        hypothesis="分红率回升且现金流覆盖分红",
        invalidation="连续两期经营现金流为负",
    )
    kwargs.update(overrides)
    return service.create_or_get(**kwargs)


def test_create_is_idempotent_by_identity(service) -> None:
    first, created_first = _create(service)
    second, created_second = _create(service)

    assert created_first is True
    assert created_second is False
    assert first.track_id == second.track_id
    assert len(service.repo.list_tracks(limit=10)) == 1


def test_same_stock_keeps_multiple_hypotheses(service) -> None:
    long_track, _ = _create(service)
    short_track, created = _create(
        service,
        hypothesis_key="short:event:us_policy",
        business_layer="review",
        horizon="short",
        branch="event",
        capital_role="watch",
    )

    assert created is True
    assert long_track.id != short_track.id
    assert long_track.horizon == "long" and short_track.horizon == "short"
    # 长期 track 不被短期登记覆盖
    reloaded = service.repo.get_by_track_id(long_track.track_id)
    assert reloaded is not None and reloaded.hypothesis_key == "long:dividend_recovery"


def test_append_evidence_bumps_revision_and_records_versions(service) -> None:
    track, _ = _create(service)
    updated = service.append_evidence(
        track.track_id,
        source_type="report",
        source_ref="report:12",
        source_version="v3",
        summary="季报现金流改善",
        limitation="单季，不足以确认趋势",
    )

    assert updated.revision == track.revision + 1
    history = service.history(track.track_id)
    assert [item["event_type"] for item in history] == ["created", "evidence"]
    assert history[1]["source_ref"] == "report:12"
    assert history[1]["limitation"] == "单季，不足以确认趋势"


def test_stale_revision_raises_conflict_without_writing(service) -> None:
    track, _ = _create(service)
    service.append_evidence(track.track_id, source_type="news", source_ref="n:1")

    with pytest.raises(TrackRevisionConflict):
        service.append_evidence(
            track.track_id,
            source_type="news",
            source_ref="n:2",
            expected_revision=track.revision,
        )

    assert len(service.history(track.track_id)) == 2  # created + 第一次证据


def test_close_requires_reason_and_clears_review(service) -> None:
    next_review = datetime.now() + timedelta(days=7)
    track, _ = _create(service, next_review_at=next_review)

    with pytest.raises(ValueError):
        service.change_status(track.track_id, "closed")

    closed = service.change_status(track.track_id, "closed", reason="逻辑被证伪")
    assert closed.status == "closed"
    assert closed.closed_reason == "逻辑被证伪"
    assert closed.next_review_at is None
    assert service.history(track.track_id)[-1]["event_type"] == "closed"


def test_due_for_review_returns_only_tracking_and_due(service) -> None:
    past = datetime.now() - timedelta(days=1)
    future = datetime.now() + timedelta(days=5)
    due_track, _ = _create(service, hypothesis_key="k1", next_review_at=past)
    not_due, _ = _create(service, hypothesis_key="k2", next_review_at=future)
    pending_track, _ = _create(service, hypothesis_key="k3", next_review_at=past)

    service.change_status(due_track.track_id, "tracking")
    service.change_status(not_due.track_id, "tracking")

    due = service.due_for_review()
    codes = {track.hypothesis_key for track in due}
    assert codes == {"k1"}
    assert pending_track.status == "pending"


def test_invalid_classification_is_rejected(service) -> None:
    with pytest.raises(ValueError):
        _create(service, business_layer="unknown")

    with pytest.raises(ValueError):
        _create(service, capital_role="unknown")


def test_bootstrap_loop_is_idempotent(service) -> None:
    """重复跑同一日的建 track 循环不产生重复记录（模拟 scripts/track_from_board.py）。"""
    items = [("600519", "hundred_day_high"), ("000001", "screening__dual_low")]

    def run_once():
        created = 0
        for code, source in items:
            _, was_created = service.create_or_get(
                market="cn",
                code=code,
                hypothesis_key=f"signal:{source}:2026-11-06",
                business_layer="review",
                horizon="short",
                branch=source.split("__", 1)[0],
                capital_role="watch",
                hypothesis="测试建卡",
                source_type="kline_signal_snapshot",
                source_ref=f"{source}:2026-11-06:{code}",
            )
            created += int(was_created)
        return created

    assert run_once() == 2
    assert run_once() == 0
    assert len(service.repo.list_tracks(limit=10)) == 2
