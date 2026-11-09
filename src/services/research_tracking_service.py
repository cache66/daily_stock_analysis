# -*- coding: utf-8 -*-
"""研究跟踪服务（A2-a）。

职责：按稳定身份创建/复用 track、追加证据事件、状态流转、乐观并发冲突处理。
不做的事情：不重算资格、不写 DecisionSignal、不调模型、不建第二套建议池。

合同（见 docs/个人策略文档/个人投研工作台重构方案.md §3 / §7）：
1. 身份 = ``market + code + hypothesis_key``；重复创建返回既有记录，不产生重复样本。
2. 事件只追加；错误通过新增事件纠正，不覆盖历史。
3. 所有变更都带 ``expected_revision``，冲突抛 ``TrackRevisionConflict``。
4. 同股可以并存长期/短期/反弹多条 track，互不覆盖、不合成总分。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.repositories.research_tracking_repo import ResearchTrackingRepository
from src.storage import ResearchTrack, to_utc_naive_datetime, utc_naive_now

TRACK_STATUSES = ("pending", "tracking", "invalid", "closed")
CAPITAL_ROLES = ("guard", "attack", "watch", "shared")
BUSINESS_LAYERS = ("hotspot", "review", "longterm")
HORIZONS = ("short", "long")


class TrackRevisionConflict(RuntimeError):
    """乐观并发冲突：调用方持有的是旧 revision，需重新读取后再改。"""


class ResearchTrackingService:
    """研究假设的创建、证据追加与状态流转。"""

    def __init__(self, repo: Optional[ResearchTrackingRepository] = None):
        self.repo = repo or ResearchTrackingRepository()

    # ------------------------------------------------------------------ 创建
    def create_or_get(
        self,
        *,
        market: str,
        code: str,
        hypothesis_key: str,
        title: str = "",
        business_layer: str = "review",
        horizon: str = "short",
        branch: str = "",
        capital_role: str = "watch",
        hypothesis: str = "",
        invalidation: str = "",
        next_review_at: Optional[datetime] = None,
        source_type: str = "",
        source_ref: str = "",
        source_version: str = "",
        available_at: Optional[datetime] = None,
        observed_at: Optional[datetime] = None,
        quality: str = "",
        limitation: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ResearchTrack, bool]:
        """按稳定身份幂等创建；返回 (track, created)。"""
        if not code or not hypothesis_key:
            raise ValueError("code 与 hypothesis_key 必须非空")
        if business_layer not in BUSINESS_LAYERS:
            raise ValueError(f"business_layer 必须是 {BUSINESS_LAYERS}")
        if horizon not in HORIZONS:
            raise ValueError(f"horizon 必须是 {HORIZONS}")
        if capital_role not in CAPITAL_ROLES:
            raise ValueError(f"capital_role 必须是 {CAPITAL_ROLES}")

        existing = self.repo.get_by_identity(market, code, hypothesis_key)
        if existing is not None:
            return existing, False

        fields: Dict[str, Any] = {
            "track_id": str(uuid.uuid4()),
            "market": market,
            "code": code,
            "hypothesis_key": hypothesis_key,
            "title": title or f"{code} {hypothesis_key}",
            "business_layer": business_layer,
            "horizon": horizon,
            "branch": branch,
            "capital_role": capital_role,
            "hypothesis": hypothesis,
            "invalidation": invalidation,
            "status": "pending",
            "revision": 1,
            "next_review_at": next_review_at,
        }
        event = {
            "event_type": "created",
            "source_type": source_type or None,
            "source_ref": source_ref or None,
            "source_version": source_version or None,
            "available_at": to_utc_naive_datetime(available_at) if available_at else None,
            "observed_at": to_utc_naive_datetime(observed_at) if observed_at else utc_naive_now(),
            "quality": quality or None,
            "limitation": limitation or None,
            "summary": "首次登记",
            "payload_json": self.repo.event_payload({**(payload or {}), "hypothesis": hypothesis, "invalidation": invalidation}),
        }
        try:
            return self.repo.create_track(fields, event=event), True
        except Exception:
            # 并发首次导入：唯一约束冲突时回读既有记录，避免两个 track
            again = self.repo.get_by_identity(market, code, hypothesis_key)
            if again is not None:
                return again, False
            raise

    # ------------------------------------------------------------------ 证据
    def append_evidence(
        self,
        track_id: str,
        *,
        source_type: str,
        source_ref: str,
        summary: str = "",
        source_version: str = "",
        available_at: Optional[datetime] = None,
        observed_at: Optional[datetime] = None,
        quality: str = "",
        limitation: str = "",
        payload: Optional[Dict[str, Any]] = None,
        expected_revision: Optional[int] = None,
        touch: bool = True,
        close_reason: str = "",
    ) -> ResearchTrack:
        track = self._require(track_id)
        fields: Dict[str, Any] = {"last_checked_at": observed_at or datetime.now()} if touch else {}
        if close_reason:
            fields.update(status="closed", closed_reason=close_reason[:200], next_review_at=None)
        ok = self.repo.update_track(
            track.id,
            fields,
            expected_revision=expected_revision if expected_revision is not None else track.revision,
            event={
                "event_type": "evidence",
                "source_type": source_type or None,
                "source_ref": source_ref or None,
                "source_version": source_version or None,
                "available_at": to_utc_naive_datetime(available_at) if available_at else None,
                "observed_at": to_utc_naive_datetime(observed_at) if observed_at else utc_naive_now(),
                "quality": quality or None,
                "limitation": limitation or None,
                "summary": summary or None,
                "payload_json": self.repo.event_payload(payload),
            },
            events=[{
                "event_type": "closed", "observed_at": to_utc_naive_datetime(observed_at) if observed_at else utc_naive_now(),
                "summary": close_reason,
                "payload_json": self.repo.event_payload({"status": "closed", "reason": close_reason}),
            }] if close_reason else None,
        )
        if not ok:
            raise TrackRevisionConflict(f"track {track_id} 已被更新，请重新读取后再写")
        return self._require(track_id)

    # ------------------------------------------------------------------ 状态
    def change_status(
        self,
        track_id: str,
        status: str,
        *,
        reason: str = "",
        next_review_at: Optional[datetime] = None,
        expected_revision: Optional[int] = None,
    ) -> ResearchTrack:
        if status not in TRACK_STATUSES:
            raise ValueError(f"status 必须是 {TRACK_STATUSES}")
        if status in {"invalid", "closed"} and not reason:
            raise ValueError("置为 invalid/closed 必须给出原因")

        track = self._require(track_id)
        fields: Dict[str, Any] = {"status": status}
        if next_review_at is not None:
            fields["next_review_at"] = next_review_at
        if status in {"invalid", "closed"}:
            fields["closed_reason"] = reason[:200]
            fields["next_review_at"] = None

        ok = self.repo.update_track(
            track.id,
            fields,
            expected_revision=expected_revision if expected_revision is not None else track.revision,
            event={
                "event_type": status,
                "observed_at": utc_naive_now(),
                "summary": reason or None,
                "payload_json": self.repo.event_payload({"status": status, "reason": reason}),
            },
        )
        if not ok:
            raise TrackRevisionConflict(f"track {track_id} 已被更新，请重新读取后再写")
        return self._require(track_id)

    # ------------------------------------------------------------------ 查询
    def list_open(self, limit: int = 100) -> List[ResearchTrack]:
        return [t for t in self.repo.list_tracks(limit=limit) if t.status in {"pending", "tracking"}]

    def due_for_review(self, as_of: Optional[datetime] = None, limit: int = 100) -> List[ResearchTrack]:
        return self.repo.due_for_review(as_of=as_of, limit=limit)

    def history(self, track_id: str) -> List[Dict[str, Any]]:
        track = self._require(track_id)
        return [
            {
                "event_type": row.event_type,
                "source_ref": row.source_ref,
                "source_version": row.source_version,
                "available_at": row.available_at,
                "observed_at": row.observed_at,
                "quality": row.quality,
                "limitation": row.limitation,
                "summary": row.summary,
                "created_at": row.created_at,
            }
            for row in self.repo.list_events(track.id)
        ]

    # ------------------------------------------------------------------ 内部
    def _require(self, track_id: str) -> ResearchTrack:
        track = self.repo.get_by_track_id(track_id)
        if track is None:
            raise LookupError(f"track 不存在：{track_id}")
        return track
