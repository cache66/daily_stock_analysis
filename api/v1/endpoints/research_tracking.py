# -*- coding: utf-8 -*-
"""Research tracking API endpoints（A2-b，只读）。

只读：不提供创建/修订接口（当前由 `scripts/track_from_board.py` 与人工流程写入），
避免在页面接入前引入第二套写入契约。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.research_tracking import (
    TrackDetailResponse,
    TrackEventItem,
    TrackItem,
    TrackListResponse,
)
from src.repositories.research_tracking_repo import ResearchTrackingRepository
from src.services.research_tracking_service import ResearchTrackingService

logger = logging.getLogger(__name__)

router = APIRouter()


def _service() -> ResearchTrackingService:
    return ResearchTrackingService(ResearchTrackingRepository())


def _to_item(row) -> TrackItem:
    return TrackItem(
        track_id=row.track_id,
        market=row.market,
        code=row.code,
        hypothesis_key=row.hypothesis_key,
        title=row.title or "",
        business_layer=row.business_layer,
        horizon=row.horizon,
        branch=row.branch or "",
        capital_role=row.capital_role,
        status=row.status,
        revision=int(row.revision or 0),
        next_review_at=row.next_review_at,
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=TrackListResponse)
def list_tracks(
    status: Optional[str] = Query(None, description="pending/tracking/invalid/closed"),
    code: Optional[str] = Query(None, description="六位证券代码"),
    limit: int = Query(50, ge=1, le=500),
) -> TrackListResponse:
    rows = _service().repo.list_tracks(status=status, code=code, limit=limit)
    items = [_to_item(row) for row in rows]
    return TrackListResponse(total=len(items), items=items)


@router.get("/due", response_model=TrackListResponse)
def list_due_tracks(
    as_of: Optional[date] = Query(None, description="按截止日查；缺省今日"),
    limit: int = Query(100, ge=1, le=500),
) -> TrackListResponse:
    moment = datetime.combine(as_of, datetime.max.time()) if as_of else datetime.now()
    rows = _service().due_for_review(as_of=moment, limit=limit)
    items = [_to_item(row) for row in rows]
    return TrackListResponse(total=len(items), items=items)


@router.get("/{track_id}", response_model=TrackDetailResponse)
def get_track(track_id: str) -> TrackDetailResponse:
    service = _service()
    row = service.repo.get_by_track_id(track_id)
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    events = [
        TrackEventItem(
            event_type=event.event_type,
            source_type=event.source_type,
            source_ref=event.source_ref,
            source_version=event.source_version,
            available_at=event.available_at,
            observed_at=event.observed_at,
            quality=event.quality,
            limitation=event.limitation,
            summary=event.summary,
            created_at=event.created_at,
        )
        for event in service.repo.list_events(row.id)
    ]
    detail = TrackDetailResponse(**_to_item(row).model_dump(), events=events)
    return detail
