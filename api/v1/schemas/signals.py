# -*- coding: utf-8 -*-
"""Schemas for K-line signal snapshot query APIs."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SignalSnapshotListItem(BaseModel):
    code: str = Field(..., description="Stock code")
    name: Optional[str] = Field(None, description="Stock name")
    signal_date: Optional[str] = Field(None, description="Signal date")
    event_date: Optional[str] = Field(None, description="Event date")
    board_name: Optional[str] = Field(None, description="Board name")
    board_rank: Optional[int] = Field(None, description="Board rank")
    board_candidate_count: Optional[int] = Field(None, description="Board candidate count")
    source_signal_type: Optional[str] = Field(None, description="Source signal type")
    source_signal_date: Optional[str] = Field(None, description="Source signal date")
    subtheme_key: Optional[str] = Field(None, description="Commodity subtheme key")
    chain_role: Optional[str] = Field(None, description="Commodity chain role")
    pass_through_direction: Optional[str] = Field(None, description="Commodity pass-through direction")
    earnings_validation_status: Optional[str] = Field(None, description="Earnings validation status")
    earnings_release_probability: Optional[str] = Field(None, description="Earnings release probability")
    earnings_quality_signal: Optional[bool] = Field(None, description="Whether earnings quality passed the quality signal threshold")
    earnings_strategy_score: Optional[float] = Field(None, description="Hybrid earnings-strategy score")
    earnings_strategy_label: Optional[str] = Field(None, description="Hybrid earnings-strategy label")
    earnings_strategy_gate_status: Optional[str] = Field(None, description="Hybrid earnings-strategy gate status")
    earnings_growth_continuity_score: Optional[float] = Field(None, description="Growth continuity factor score")
    earnings_profit_quality_score: Optional[float] = Field(None, description="Profit-quality factor score")
    earnings_profitability_score: Optional[float] = Field(None, description="Profitability factor score")
    earnings_disclosure_signal_score: Optional[float] = Field(None, description="Disclosure-signal factor score")
    earnings_cycle_score: Optional[float] = Field(None, description="Cycle factor score")
    earnings_event_freshness_score: Optional[float] = Field(None, description="Event freshness factor score")
    earnings_risk_penalty: Optional[float] = Field(None, description="Risk penalty deducted from hybrid score")
    earnings_quality_verdict: Optional[str] = Field(None, description="Earnings quality verdict")
    earnings_quality_score: Optional[float] = Field(None, description="Earnings quality score")
    earnings_quality_cycle_phase: Optional[str] = Field(None, description="Earnings quality cycle phase")
    earnings_quality_quarterly_trend: Optional[str] = Field(None, description="Earnings quality quarterly trend")
    earnings_quality_dual_positive_streak: Optional[int] = Field(None, description="Earnings quality dual-positive streak")
    directness: Optional[str] = Field(None, description="Beneficiary directness")
    matched_example_bucket: Optional[str] = Field(None, description="Matched example bucket")
    matched_example_name: Optional[str] = Field(None, description="Matched example name")
    recognizability_score: Optional[float] = Field(None, description="Recognizability score")
    sustained_growth_score: Optional[float] = Field(None, description="Sustained growth score")
    liquidity_score: Optional[float] = Field(None, description="Liquidity score")
    valuation_score: Optional[float] = Field(None, description="Valuation score")
    dividend_score: Optional[float] = Field(None, description="Dividend score")
    logic_consensus_score: Optional[float] = Field(None, description="Logic consensus score")
    capital_consensus_score: Optional[float] = Field(None, description="Capital consensus score")
    cache_source: Optional[str] = Field(None, description="Same-day cache source")
    bundle_refreshed_at: Optional[str] = Field(None, description="Same-day bundle refresh timestamp")
    capital_profile_refreshed_at: Optional[str] = Field(None, description="Capital-profile refresh timestamp")
    capital_profile_cache_hit: Optional[bool] = Field(None, description="Whether capital profile came from same-day cache")
    leader_probability: Optional[str] = Field(None, description="Dragon-head leader probability")
    leader_type: Optional[str] = Field(None, description="Dragon-head leader type")
    selection_mode: Optional[str] = Field(None, description="Trend-leader selection mode such as strict/fallback")
    is_breakout_candidate: Optional[bool] = Field(None, description="Whether breakout structure is satisfied")
    is_pullback_candidate: Optional[bool] = Field(None, description="Whether pullback structure is satisfied")
    near_new_high: Optional[bool] = Field(None, description="Whether price is near 100-day high")
    signal_tags: Optional[List[str]] = Field(None, description="Trend-leader tags for fast reading")
    primary_profile: Optional[str] = Field(None, description="Primary profile such as breakout/pullback")
    breakout_score: Optional[float] = Field(None, description="Breakout profile score")
    pullback_score: Optional[float] = Field(None, description="Pullback profile score")
    hybrid_score: Optional[float] = Field(None, description="Hybrid profile score")
    overall_score: Optional[float] = Field(None, description="Unified strategy overall score")
    trend_label: Optional[str] = Field(None, description="Unified strategy trend label")
    risk_flags: Optional[List[str]] = Field(None, description="Unified strategy risk flags")
    strategy_summary: Optional[str] = Field(None, description="Unified strategy summary")
    profile_name: Optional[str] = Field(None, description="Profile name")
    profile_label: Optional[str] = Field(None, description="Profile label")
    sector_leadership_score: Optional[float] = Field(None, description="Sector leadership score")
    relative_strength_score: Optional[float] = Field(None, description="Relative strength score")
    catalyst_score: Optional[float] = Field(None, description="Catalyst score")
    monthly_positive_ratio: Optional[float] = Field(None, description="Monthly positive ratio")
    monthly_higher_low_ratio: Optional[float] = Field(None, description="Monthly higher-low ratio")
    monthly_total_return_pct: Optional[float] = Field(None, description="Monthly total return percentage")
    monthly_max_single_gain_pct: Optional[float] = Field(None, description="Monthly max single gain percentage")
    monthly_worst_drawdown_pct: Optional[float] = Field(None, description="Monthly worst drawdown percentage")
    monthly_ma_short: Optional[float] = Field(None, description="Short monthly moving average")
    monthly_ma_long: Optional[float] = Field(None, description="Long monthly moving average")
    monthly_latest_month: Optional[str] = Field(None, description="Latest monthly bar label")
    industry: Optional[str] = Field(None, description="Industry")
    reason_summary: Optional[str] = Field(None, description="Reason summary")
    industry_logic: Optional[str] = Field(None, description="Industry logic")
    news_logic: Optional[str] = Field(None, description="News logic")
    technical_logic: Optional[str] = Field(None, description="Technical logic")
    theme_label: Optional[str] = Field(None, description="Theme label")
    latest_previous_hit_date: Optional[str] = Field(None, description="Latest previous hit date")
    previous_hit_count: int = Field(0, description="Previous hit count")
    days_since_previous_hit: Optional[int] = Field(None, description="Days since previous hit")
    is_consecutive_signal: bool = Field(False, description="Whether this is consecutive")
    close: Optional[float] = Field(None, description="Close price")
    latest_high: Optional[float] = Field(None, description="Latest high")
    window_high: Optional[float] = Field(None, description="Window high")
    total_market_cap: Optional[float] = Field(None, description="Total market cap")
    total_market_cap_yi: Optional[float] = Field(None, description="Total market cap in 100m CNY")
    year_start_date: Optional[str] = Field(None, description="Year-start date")
    year_start_close: Optional[float] = Field(None, description="Year-start close")
    ytd_return_pct: Optional[float] = Field(None, description="YTD return percentage")


class SignalSnapshotCompareItem(BaseModel):
    class SignalRefItem(BaseModel):
        code: str = Field(..., description="Stock code")
        name: Optional[str] = Field(None, description="Stock name")

    signal_date: str = Field(..., description="Signal date")
    total_count: int = Field(..., description="Total count")
    continuous_count: int = Field(..., description="Continuous count")
    added_count: int = Field(0, description="Added count")
    dropped_count: int = Field(0, description="Dropped count")
    top_codes: List[str] = Field(default_factory=list, description="Top codes")
    added_codes: List[str] = Field(default_factory=list, description="Added codes")
    dropped_codes: List[str] = Field(default_factory=list, description="Dropped codes")
    added_items: List[SignalRefItem] = Field(default_factory=list, description="Added items")
    dropped_items: List[SignalRefItem] = Field(default_factory=list, description="Dropped items")
    avg_ytd_return_pct: Optional[float] = Field(None, description="Average YTD return percentage")
    median_ytd_return_pct: Optional[float] = Field(None, description="Median YTD return percentage")


class SignalSnapshotStreakItem(BaseModel):
    code: str = Field(..., description="Stock code")
    name: Optional[str] = Field(None, description="Stock name")
    industry: Optional[str] = Field(None, description="Industry")
    current_streak_count: int = Field(..., description="Current streak count")
    longest_streak_count: int = Field(..., description="Longest streak count")
    current_streak_start_date: Optional[str] = Field(None, description="Current streak start date")
    current_streak_end_date: Optional[str] = Field(None, description="Current streak end date")
    latest_signal_date: Optional[str] = Field(None, description="Latest signal date")
    latest_high: Optional[float] = Field(None, description="Latest high")
    close: Optional[float] = Field(None, description="Close price")
    theme_label: Optional[str] = Field(None, description="Theme label")


class SignalSnapshotListResponse(BaseModel):
    signal_type: str = Field(..., description="Signal type")
    signal_date: Optional[str] = Field(None, description="Signal date")
    signal_date_from: Optional[str] = Field(None, description="Signal start date")
    signal_date_to: Optional[str] = Field(None, description="Signal end date")
    total: int = Field(..., description="Total count")
    page: int = Field(..., description="Page number")
    page_size: int = Field(..., description="Page size")
    compare_summary: List[SignalSnapshotCompareItem] = Field(default_factory=list, description="Compare summary")
    streak_leaderboard: List[SignalSnapshotStreakItem] = Field(default_factory=list, description="Streak leaderboard")
    items: List[SignalSnapshotListItem] = Field(default_factory=list, description="Items")


class SignalSnapshotCountItem(BaseModel):
    signal_type: str = Field(..., description="Signal type")
    total: int = Field(..., description="Total count")
    display_label: Optional[str] = Field(None, description="Display label")
    group: Optional[str] = Field(None, description="Signal group")


class SignalSnapshotCountsResponse(BaseModel):
    signal_date: Optional[str] = Field(None, description="Signal date")
    signal_date_from: Optional[str] = Field(None, description="Signal start date")
    signal_date_to: Optional[str] = Field(None, description="Signal end date")
    items: List[SignalSnapshotCountItem] = Field(default_factory=list, description="Items")


class SignalSnapshotHistoryItem(SignalSnapshotListItem):
    new_high_window: Optional[int] = Field(None, description="New-high window")
    history_source: Optional[str] = Field(None, description="History source")
    total_market_cap: Optional[float] = Field(None, description="Total market cap")
    total_market_cap_yi: Optional[float] = Field(None, description="Total market cap in 100m CNY")


class SignalContinuitySummary(BaseModel):
    is_current_streak: bool = Field(..., description="Whether a current streak is active")
    current_streak_count: int = Field(..., description="Current streak count")
    current_streak_start_date: Optional[str] = Field(None, description="Current streak start date")
    current_streak_end_date: Optional[str] = Field(None, description="Current streak end date")
    longest_streak_count: int = Field(..., description="Longest streak count")
    longest_streak_start_date: Optional[str] = Field(None, description="Longest streak start date")
    longest_streak_end_date: Optional[str] = Field(None, description="Longest streak end date")


class SignalDrawdownSummary(BaseModel):
    anchor_close: Optional[float] = Field(None, description="Anchor close")
    max_signal_high: Optional[float] = Field(None, description="Max signal high")
    max_signal_high_date: Optional[str] = Field(None, description="Max signal high date")
    distance_from_max_signal_high_pct: Optional[float] = Field(None, description="Distance from max signal high percentage")
    latest_signal_high: Optional[float] = Field(None, description="Latest signal high")
    latest_signal_date: Optional[str] = Field(None, description="Latest signal date")
    distance_from_latest_signal_high_pct: Optional[float] = Field(None, description="Distance from latest signal high percentage")


class SignalSnapshotHistoryResponse(BaseModel):
    signal_type: str = Field(..., description="Signal type")
    code: str = Field(..., description="Stock code")
    days: int = Field(..., description="Days")
    total: int = Field(..., description="Total count")
    continuity: SignalContinuitySummary
    drawdown: SignalDrawdownSummary
    items: List[SignalSnapshotHistoryItem] = Field(default_factory=list, description="Items")
