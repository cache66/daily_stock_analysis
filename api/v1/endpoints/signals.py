# -*- coding: utf-8 -*-
"""Signal snapshot query endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.signals import (
    SignalSnapshotHistoryResponse,
    SignalSnapshotListItem,
    SignalSnapshotListResponse,
    SignalSnapshotCompareItem,
    SignalSnapshotStreakItem,
    SignalSnapshotHistoryItem,
    SignalContinuitySummary,
    SignalDrawdownSummary,
)
from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/kline-snapshots",
    response_model=SignalSnapshotListResponse,
    responses={
        200: {"description": "K 线信号快照列表"},
        400: {"description": "请求参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="按日期查询 K 线信号快照",
    description="按信号类型与日期查询当天命中的 K 线信号快照。",
)
def get_kline_signal_snapshots(
    signal_type: str = Query(..., description="信号类型，如 hundred_day_high"),
    signal_date: Optional[str] = Query(None, description="信号日期 (YYYY-MM-DD)"),
    signal_date_from: Optional[str] = Query(None, description="信号起始日期 (YYYY-MM-DD)"),
    signal_date_to: Optional[str] = Query(None, description="信号结束日期 (YYYY-MM-DD)"),
    code: Optional[str] = Query(None, description="可选股票代码过滤"),
    codes: Optional[str] = Query(None, description="可选多股票代码过滤，逗号分隔"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页数量"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> SignalSnapshotListResponse:
    try:
        service = SignalSnapshotService(db_manager)
        parsed_codes = [item.strip() for item in str(codes or "").split(",") if item.strip()]
        data = service.get_snapshot_list(
            signal_type=signal_type,
            signal_date=signal_date,
            signal_date_from=signal_date_from,
            signal_date_to=signal_date_to,
            code=code,
            codes=parsed_codes or None,
            page=page,
            page_size=page_size,
        )
        return SignalSnapshotListResponse(
            signal_type=data["signal_type"],
            signal_date=data.get("signal_date"),
            signal_date_from=data.get("signal_date_from"),
            signal_date_to=data.get("signal_date_to"),
            total=data["total"],
            page=data["page"],
            page_size=data["page_size"],
            compare_summary=[SignalSnapshotCompareItem(**item) for item in data.get("compare_summary", [])],
            streak_leaderboard=[SignalSnapshotStreakItem(**item) for item in data.get("streak_leaderboard", [])],
            items=[SignalSnapshotListItem(**item) for item in data["items"]],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("查询 K 线信号快照列表失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"查询 K 线信号快照列表失败: {str(exc)}"},
        )


@router.get(
    "/kline-snapshots/{signal_type}/{code}",
    response_model=SignalSnapshotHistoryResponse,
    responses={
        200: {"description": "股票 K 线信号历史"},
        400: {"description": "请求参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="按股票查询 K 线信号历史",
    description="查询某只股票最近一段时间的同口径 K 线信号历史，并附带连续命中和回撤摘要。",
)
def get_kline_signal_history(
    signal_type: str,
    code: str,
    days: int = Query(180, ge=1, le=1000, description="查询窗口天数"),
    limit: int = Query(100, ge=1, le=500, description="返回数量限制"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> SignalSnapshotHistoryResponse:
    try:
        service = SignalSnapshotService(db_manager)
        data = service.get_signal_history(
            signal_type=signal_type,
            code=code,
            days=days,
            limit=limit,
        )
        return SignalSnapshotHistoryResponse(
            signal_type=data["signal_type"],
            code=data["code"],
            days=data["days"],
            total=data["total"],
            continuity=SignalContinuitySummary(**data["continuity"]),
            drawdown=SignalDrawdownSummary(**data["drawdown"]),
            items=[SignalSnapshotHistoryItem(**item) for item in data["items"]],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("查询 K 线信号历史失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"查询 K 线信号历史失败: {str(exc)}"},
        )
