# -*- coding: utf-8 -*-
"""Signal snapshot query endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.signals import (
    FastReviewFocusItem,
    FastReviewFocusResponse,
    FastReviewStockOverviewItem,
    FastReviewStockOverviewResponse,
    PersonalStrategyDefinition,
    PersonalStrategyMatrixItem,
    PersonalStrategyMatrixResponse,
    SignalContinuitySummary,
    SignalDrawdownSummary,
    SignalSnapshotCompareItem,
    SignalSnapshotCountItem,
    SignalSnapshotCountsResponse,
    SignalSnapshotHistoryItem,
    SignalSnapshotHistoryResponse,
    SignalSnapshotListItem,
    SignalSnapshotListResponse,
    SignalSnapshotStreakItem,
)
from src.services.fast_review_focus_service import FastReviewFocusService
from src.services.personal_strategy_matrix_service import PersonalStrategyMatrixService
from src.services.signal_snapshot_service import SignalSnapshotService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/fast-review-focus",
    response_model=FastReviewFocusResponse,
    responses={
        200: {"description": "Fast-review focus rows"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get fast-review focus rows",
    description="Return the latest fast-review focus artifact for the given snapshot date.",
)
def get_fast_review_focus(
    snapshot_date: str = Query(..., description="Snapshot date (YYYY-MM-DD)"),
) -> FastReviewFocusResponse:
    try:
        service = FastReviewFocusService()
        data = service.get_focus(snapshot_date=snapshot_date)
        return FastReviewFocusResponse(
            snapshot_date=data["snapshot_date"],
            total=data["total"],
            source_run_dir=data["source_run_dir"],
            source_csv_path=data["source_csv_path"],
            ab_summary=data.get("ab_summary", {}),
            stage_summary=data.get("stage_summary", {}),
            driver_summary=data.get("driver_summary", {}),
            items=[FastReviewFocusItem(**item) for item in data.get("items", [])],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Query fast-review focus failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query fast-review focus failed: {str(exc)}"},
        )


@router.get(
    "/fast-review-stock-overview",
    response_model=FastReviewStockOverviewResponse,
    responses={
        200: {"description": "Fast-review stock-centered overview rows"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get fast-review stock overview rows",
    description=(
        "Return the latest stock-centered fast-review artifact for the given snapshot date. "
        "Optionally filter to one stock code to inspect which strategies it triggered."
    ),
)
def get_fast_review_stock_overview(
    snapshot_date: str = Query(..., description="Snapshot date (YYYY-MM-DD)"),
    code: Optional[str] = Query(None, description="Optional stock code filter"),
) -> FastReviewStockOverviewResponse:
    try:
        service = FastReviewFocusService()
        data = service.get_stock_overview(snapshot_date=snapshot_date, code=code)
        return FastReviewStockOverviewResponse(
            snapshot_date=data["snapshot_date"],
            total=data["total"],
            source_run_dir=data["source_run_dir"],
            source_csv_path=data["source_csv_path"],
            lane_summary=data.get("lane_summary", {}),
            signal_summary=data.get("signal_summary", {}),
            items=[FastReviewStockOverviewItem(**item) for item in data.get("items", [])],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Query fast-review stock overview failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query fast-review stock overview failed: {str(exc)}"},
        )


@router.get(
    "/personal-strategy-matrix",
    response_model=PersonalStrategyMatrixResponse,
    responses={
        200: {"description": "Personal strategy matrix rows"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get stock-centered personal strategy matrix",
    description=(
        "Return the personal strategy registry and stock-centered strategy hits for a snapshot date. "
        "This endpoint reads generated fast-review artifacts and does not run strategy scans."
    ),
)
def get_personal_strategy_matrix(
    snapshot_date: str = Query(..., description="Snapshot date (YYYY-MM-DD)"),
    code: Optional[str] = Query(None, description="Optional stock code filter"),
    strategy_ids: Optional[str] = Query(None, description="Optional comma-separated personal strategy ids"),
) -> PersonalStrategyMatrixResponse:
    try:
        selected_strategy_ids = [
            item.strip()
            for item in str(strategy_ids or "").split(",")
            if item.strip()
        ]
        service = PersonalStrategyMatrixService()
        data = service.get_matrix(
            snapshot_date=snapshot_date,
            code=code,
            strategy_ids=selected_strategy_ids,
        )
        return PersonalStrategyMatrixResponse(
            snapshot_date=data["snapshot_date"],
            total=data["total"],
            source_run_dir=data["source_run_dir"],
            source_csv_path=data["source_csv_path"],
            lane_summary=data.get("lane_summary", {}),
            signal_summary=data.get("signal_summary", {}),
            strategy_summary=data.get("strategy_summary", {}),
            strategies=[PersonalStrategyDefinition(**item) for item in data.get("strategies", [])],
            items=[PersonalStrategyMatrixItem(**item) for item in data.get("items", [])],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Query personal strategy matrix failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query personal strategy matrix failed: {str(exc)}"},
        )


@router.get(
    "/kline-snapshot-counts",
    response_model=SignalSnapshotCountsResponse,
    responses={
        200: {"description": "K-line signal counts"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get signal snapshot counts",
    description="Return counts for multiple signal types under the same date or date-range filter.",
)
def get_kline_signal_snapshot_counts(
    signal_date: Optional[str] = Query(None, description="Signal date (YYYY-MM-DD)"),
    signal_date_from: Optional[str] = Query(None, description="Signal start date (YYYY-MM-DD)"),
    signal_date_to: Optional[str] = Query(None, description="Signal end date (YYYY-MM-DD)"),
    code: Optional[str] = Query(None, description="Optional stock code filter"),
    codes: Optional[str] = Query(None, description="Optional comma-separated stock codes filter"),
    signal_types: Optional[str] = Query(None, description="Optional comma-separated signal types"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> SignalSnapshotCountsResponse:
    try:
        service = SignalSnapshotService(db_manager)
        parsed_codes = [item.strip() for item in str(codes or "").split(",") if item.strip()]
        parsed_signal_types = [item.strip() for item in str(signal_types or "").split(",") if item.strip()]
        data = service.get_snapshot_counts(
            signal_date=signal_date,
            signal_date_from=signal_date_from,
            signal_date_to=signal_date_to,
            code=code,
            codes=parsed_codes or None,
            signal_types=parsed_signal_types or None,
        )
        return SignalSnapshotCountsResponse(
            signal_date=data.get("signal_date"),
            signal_date_from=data.get("signal_date_from"),
            signal_date_to=data.get("signal_date_to"),
            items=[SignalSnapshotCountItem(**item) for item in data.get("items", [])],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Query K-line signal snapshot counts failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query K-line signal snapshot counts failed: {str(exc)}"},
        )


@router.get(
    "/kline-snapshots",
    response_model=SignalSnapshotListResponse,
    responses={
        200: {"description": "K-line signal snapshot list"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Query K-line signal snapshots by date",
    description="Query the daily hit list for one K-line signal type by exact date or date range.",
)
def get_kline_signal_snapshots(
    signal_type: str = Query(..., description="Signal type such as hundred_day_high"),
    signal_date: Optional[str] = Query(None, description="Signal date (YYYY-MM-DD)"),
    signal_date_from: Optional[str] = Query(None, description="Signal start date (YYYY-MM-DD)"),
    signal_date_to: Optional[str] = Query(None, description="Signal end date (YYYY-MM-DD)"),
    code: Optional[str] = Query(None, description="Optional stock code filter"),
    codes: Optional[str] = Query(None, description="Optional comma-separated stock codes filter"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Page size"),
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
        logger.error("Query K-line signal snapshot list failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query K-line signal snapshot list failed: {str(exc)}"},
        )


@router.get(
    "/kline-snapshots/{signal_type}/{code}",
    response_model=SignalSnapshotHistoryResponse,
    responses={
        200: {"description": "K-line signal history for one stock"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Query K-line signal history by stock",
    description="Query one stock's recent hit history for the given signal type, including continuity and drawdown summaries.",
)
def get_kline_signal_history(
    signal_type: str,
    code: str,
    days: int = Query(180, ge=1, le=1000, description="History window in days"),
    limit: int = Query(100, ge=1, le=500, description="Max result rows"),
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
        logger.error("Query K-line signal history failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"Query K-line signal history failed: {str(exc)}"},
        )
