# -*- coding: utf-8 -*-
"""Research tracking read-only API tests（A2-b）。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from src.config import Config
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager


@pytest.fixture()
def client(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "research_tracking_api.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        app = create_app()
        with TestClient(app) as test_client:
            yield test_client
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _seed(status: str = "tracking", next_review_at: datetime | None = None) -> str:
    service = ResearchTrackingService()
    track, _ = service.create_or_get(
        market="cn",
        code="600519",
        hypothesis_key="api:test:600519",
        title="贵州茅台 测试假设",
        business_layer="review",
        horizon="short",
        branch="event",
        capital_role="watch",
        hypothesis="测试用假设",
        next_review_at=next_review_at or datetime.now() - timedelta(days=1),
        source_type="test",
        source_ref="test:1",
    )
    if status != track.status:
        service.change_status(track.track_id, status, reason="测试推进")
    return track.track_id


def test_list_tracks_empty(client) -> None:
    response = client.get("/api/v1/research-tracks")
    assert response.status_code == 200
    assert response.json() == {"total": 0, "items": []}


def test_list_and_filter_tracks(client) -> None:
    _seed()
    response = client.get("/api/v1/research-tracks", params={"code": "600519"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 1
    assert payload["items"][0]["hypothesis_key"] == "api:test:600519"
    assert payload["items"][0]["status"] == "tracking"

    miss = client.get("/api/v1/research-tracks", params={"code": "000001"})
    assert miss.json()["total"] == 0


def test_due_endpoint_respects_as_of(client) -> None:
    _seed(next_review_at=datetime(2026, 11, 9, 0, 0))

    before = client.get("/api/v1/research-tracks/due", params={"as_of": "2026-11-08"})
    assert before.json()["total"] == 0

    on_day = client.get("/api/v1/research-tracks/due", params={"as_of": "2026-11-09"})
    assert on_day.json()["total"] == 1


def test_detail_returns_events_and_404_for_unknown(client) -> None:
    track_id = _seed()

    detail = client.get(f"/api/v1/research-tracks/{track_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["track_id"] == track_id
    assert [event["event_type"] for event in body["events"]] == ["created", "tracking"]

    missing = client.get("/api/v1/research-tracks/not-a-track")
    assert missing.status_code == 404
