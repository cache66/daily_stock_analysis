# -*- coding: utf-8 -*-
"""研究跟踪记录的数据访问层（A2-a）。

两张表：``research_tracks``（身份 + 当前投影）与 ``research_track_events``（只追加）。
写入必须同事务更新事件与投影，并用 ``expected_revision`` 做乐观并发控制。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from src.storage import DatabaseManager, ResearchTrack, ResearchTrackEvent

# 自动关闭口径的唯一文案前缀：写入方（回填）与读取方（取样本）共用，
# 避免从中文文案字面量里再写一份 like 模式而两边漂移。
AUTO_CLOSE_REASON_PREFIX = "D+5 到期关闭"


class ResearchTrackingRepository:
    """DB access helpers for research tracks and their append-only events."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------ read
    def get_by_track_id(self, track_id: str) -> Optional[ResearchTrack]:
        with self.db.get_session() as session:
            return session.execute(
                select(ResearchTrack).where(ResearchTrack.track_id == track_id).limit(1)
            ).scalar_one_or_none()

    def get_by_identity(self, market: str, code: str, hypothesis_key: str) -> Optional[ResearchTrack]:
        with self.db.get_session() as session:
            return session.execute(
                select(ResearchTrack)
                .where(
                    ResearchTrack.market == market,
                    ResearchTrack.code == code,
                    ResearchTrack.hypothesis_key == hypothesis_key,
                )
                .limit(1)
            ).scalar_one_or_none()

    def list_tracks(
        self,
        *,
        status: Optional[str] = None,
        code: Optional[str] = None,
        limit: int = 100,
    ) -> List[ResearchTrack]:
        stmt = select(ResearchTrack)
        if status:
            stmt = stmt.where(ResearchTrack.status == status)
        if code:
            stmt = stmt.where(ResearchTrack.code == code)
        stmt = stmt.order_by(ResearchTrack.updated_at.desc(), ResearchTrack.id.desc()).limit(limit)
        with self.db.get_session() as session:
            return list(session.execute(stmt).scalars().all())

    def due_for_review(self, as_of: Optional[datetime] = None, limit: int = 100) -> List[ResearchTrack]:
        moment = as_of or datetime.now()
        stmt = (
            select(ResearchTrack)
            .where(
                ResearchTrack.status == "tracking",
                ResearchTrack.next_review_at.is_not(None),
                ResearchTrack.next_review_at <= moment,
            )
            .order_by(ResearchTrack.next_review_at.asc())
            .limit(limit)
        )
        with self.db.get_session() as session:
            return list(session.execute(stmt).scalars().all())

    def list_events(self, track_pk: int, limit: int = 200) -> List[ResearchTrackEvent]:
        stmt = (
            select(ResearchTrackEvent)
            .where(ResearchTrackEvent.track_pk == track_pk)
            .order_by(ResearchTrackEvent.id.asc())
            .limit(limit)
        )
        with self.db.get_session() as session:
            return list(session.execute(stmt).scalars().all())

    def latest_source_event(self, track_pk: int, source_type: str) -> Optional[ResearchTrackEvent]:
        """最新有效测量由追加顺序决定；不能被历史分页上限截断。"""
        with self.db.get_session() as session:
            return session.execute(
                select(ResearchTrackEvent)
                .where(ResearchTrackEvent.track_pk == track_pk, ResearchTrackEvent.source_type == source_type)
                .order_by(ResearchTrackEvent.id.desc()).limit(1)
            ).scalar_one_or_none()

    def has_event_type(self, track_pk: int, event_type: str) -> bool:
        with self.db.get_session() as session:
            return session.execute(
                select(ResearchTrackEvent.id).where(
                    ResearchTrackEvent.track_pk == track_pk, ResearchTrackEvent.event_type == event_type,
                ).limit(1)
            ).first() is not None

    def outcome_tracks(
        self, limit: int = 500, *, closed_reason_prefix: str = AUTO_CLOSE_REASON_PREFIX
    ) -> List[ResearchTrack]:
        """自动关闭样本仍可追加行情修订；不重算人工关闭/失效的假设。

        排序：先按“上次测量/检查时间”升序（最久未看的先算，避免新样本永远压住旧样本），
        再按 id 升序保证稳定；``limit`` 只截断本批，不回绕。
        """
        from sqlalchemy import or_

        with self.db.get_session() as session:
            return list(session.execute(
                select(ResearchTrack).where(
                    ResearchTrack.hypothesis_key.like("signal:%"),
                    or_(
                        ResearchTrack.status == "tracking",
                        (ResearchTrack.status == "closed") & (
                            ResearchTrack.closed_reason.like(f"{closed_reason_prefix}%")
                        ),
                    ),
                ).order_by(
                    ResearchTrack.last_checked_at.is_(None).desc(),
                    ResearchTrack.last_checked_at.asc(),
                    ResearchTrack.id.asc(),
                ).limit(limit)
            ).scalars().all())

    # ----------------------------------------------------------------- write
    def create_track(self, fields: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> ResearchTrack:
        """同事务插入 track 与首个事件。"""
        with self.db.get_session() as session:
            row = ResearchTrack(**fields)
            session.add(row)
            session.flush()
            if event:
                payload = dict(event)
                payload.setdefault("track_pk", row.id)
                session.add(ResearchTrackEvent(**payload))
            session.commit()
            session.refresh(row)
            return row

    def update_track(
        self,
        track_pk: int,
        fields: Dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
        event: Optional[Dict[str, Any]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """更新投影 + 追加事件；revision 不匹配时返回 False（不写任何东西）。"""
        with self.db.get_session() as session:
            stmt = update(ResearchTrack).where(ResearchTrack.id == track_pk)
            if expected_revision is not None:
                stmt = stmt.where(ResearchTrack.revision == int(expected_revision))
            values = dict(fields)
            values["revision"] = ResearchTrack.revision + 1
            values["updated_at"] = datetime.now()
            result = session.execute(stmt.values(**values))
            if result.rowcount == 0:
                session.rollback()
                return False
            for item in ([event] if event else []) + list(events or []):
                payload = dict(item)
                payload.setdefault("track_pk", track_pk)
                session.add(ResearchTrackEvent(**payload))
            session.commit()
            return True

    @staticmethod
    def event_payload(payload: Optional[Dict[str, Any]]) -> str:
        return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
