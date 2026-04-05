# -*- coding: utf-8 -*-
"""Schemas for K-line signal snapshot query APIs."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SignalSnapshotListItem(BaseModel):
    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    signal_date: Optional[str] = Field(None, description="信号日期")
    industry: Optional[str] = Field(None, description="行业")
    reason_summary: Optional[str] = Field(None, description="归因摘要")
    industry_logic: Optional[str] = Field(None, description="行业逻辑")
    news_logic: Optional[str] = Field(None, description="消息逻辑")
    technical_logic: Optional[str] = Field(None, description="技术逻辑")
    theme_label: Optional[str] = Field(None, description="主题级海外映射")
    latest_previous_hit_date: Optional[str] = Field(None, description="最近一次历史命中日期")
    previous_hit_count: int = Field(0, description="历史命中次数")
    days_since_previous_hit: Optional[int] = Field(None, description="距离上次命中天数")
    is_consecutive_signal: bool = Field(False, description="是否属于连续新高")
    close: Optional[float] = Field(None, description="信号日收盘价")
    latest_high: Optional[float] = Field(None, description="信号日最新 high")
    window_high: Optional[float] = Field(None, description="窗口 high")


class SignalSnapshotCompareItem(BaseModel):
    class SignalRefItem(BaseModel):
        code: str = Field(..., description="股票代码")
        name: Optional[str] = Field(None, description="股票名称")

    signal_date: str = Field(..., description="日期")
    total_count: int = Field(..., description="当日命中数")
    continuous_count: int = Field(..., description="当日连续新高数")
    added_count: int = Field(0, description="相对前一交易日新增数量")
    dropped_count: int = Field(0, description="相对前一交易日掉队数量")
    top_codes: List[str] = Field(default_factory=list, description="当日代表股票代码")
    added_codes: List[str] = Field(default_factory=list, description="新增股票代码")
    dropped_codes: List[str] = Field(default_factory=list, description="掉队股票代码")
    added_items: List[SignalRefItem] = Field(default_factory=list, description="新增股票列表")
    dropped_items: List[SignalRefItem] = Field(default_factory=list, description="掉队股票列表")


class SignalSnapshotStreakItem(BaseModel):
    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    industry: Optional[str] = Field(None, description="行业")
    current_streak_count: int = Field(..., description="当前连续命中次数")
    longest_streak_count: int = Field(..., description="历史最长连续命中次数")
    current_streak_start_date: Optional[str] = Field(None, description="当前连续命中起点")
    current_streak_end_date: Optional[str] = Field(None, description="当前连续命中终点")
    latest_signal_date: Optional[str] = Field(None, description="最近一次命中日期")
    latest_high: Optional[float] = Field(None, description="最近一次命中 high")
    close: Optional[float] = Field(None, description="最近一次命中 close")
    theme_label: Optional[str] = Field(None, description="主题级海外映射")


class SignalSnapshotListResponse(BaseModel):
    signal_type: str = Field(..., description="信号类型")
    signal_date: Optional[str] = Field(None, description="查询日期")
    signal_date_from: Optional[str] = Field(None, description="查询起始日期")
    signal_date_to: Optional[str] = Field(None, description="查询结束日期")
    total: int = Field(..., description="结果总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    compare_summary: List[SignalSnapshotCompareItem] = Field(default_factory=list, description="多日对比摘要")
    streak_leaderboard: List[SignalSnapshotStreakItem] = Field(default_factory=list, description="连续新高 streak 排行")
    items: List[SignalSnapshotListItem] = Field(default_factory=list, description="查询结果")


class SignalSnapshotHistoryItem(SignalSnapshotListItem):
    new_high_window: Optional[int] = Field(None, description="新高窗口")
    history_source: Optional[str] = Field(None, description="历史行情来源")
    total_market_cap: Optional[float] = Field(None, description="总市值")


class SignalContinuitySummary(BaseModel):
    is_current_streak: bool = Field(..., description="当前是否处于连续命中状态")
    current_streak_count: int = Field(..., description="当前连续命中次数")
    current_streak_start_date: Optional[str] = Field(None, description="当前连续命中起点")
    current_streak_end_date: Optional[str] = Field(None, description="当前连续命中终点")
    longest_streak_count: int = Field(..., description="历史最长连续命中次数")
    longest_streak_start_date: Optional[str] = Field(None, description="历史最长连续命中起点")
    longest_streak_end_date: Optional[str] = Field(None, description="历史最长连续命中终点")


class SignalDrawdownSummary(BaseModel):
    anchor_close: Optional[float] = Field(None, description="最新一条信号的 close")
    max_signal_high: Optional[float] = Field(None, description="查询窗口内最大信号 high")
    max_signal_high_date: Optional[str] = Field(None, description="最大信号 high 对应日期")
    distance_from_max_signal_high_pct: Optional[float] = Field(None, description="相对最大信号 high 的距离百分比")
    latest_signal_high: Optional[float] = Field(None, description="最新信号 high")
    latest_signal_date: Optional[str] = Field(None, description="最新信号日期")
    distance_from_latest_signal_high_pct: Optional[float] = Field(None, description="相对最新信号 high 的距离百分比")


class SignalSnapshotHistoryResponse(BaseModel):
    signal_type: str = Field(..., description="信号类型")
    code: str = Field(..., description="股票代码")
    days: int = Field(..., description="查询窗口天数")
    total: int = Field(..., description="结果总数")
    continuity: SignalContinuitySummary
    drawdown: SignalDrawdownSummary
    items: List[SignalSnapshotHistoryItem] = Field(default_factory=list, description="历史命中记录")
