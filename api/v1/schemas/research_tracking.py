# -*- coding: utf-8 -*-
"""Research tracking API schemas（A2-b，只读）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class TrackItem(BaseModel):
    track_id: str
    market: str
    code: str
    hypothesis_key: str
    title: str
    business_layer: str
    horizon: str
    branch: str
    capital_role: str
    status: str
    revision: int
    next_review_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TrackEventItem(BaseModel):
    event_type: str
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    source_version: Optional[str] = None
    available_at: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    quality: Optional[str] = None
    limitation: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[datetime] = None


class TrackListResponse(BaseModel):
    total: int
    items: List[TrackItem]


class TrackDetailResponse(TrackItem):
    events: List[TrackEventItem] = []
