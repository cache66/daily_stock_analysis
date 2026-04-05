# -*- coding: utf-8 -*-
"""
===================================
服务层模块初始化
===================================

职责：
1. 声明可导出的服务类（延迟导入，避免启动时拉入 LLM 等重依赖）

使用方式：
    直接从子模块导入，例如:
    from src.services.history_service import HistoryService
"""


def __getattr__(name: str):
    """延迟导入：仅在通过 src.services.X 访问时才加载对应子模块。"""
    _lazy_map = {
        "AnalysisService": "src.services.analysis_service",
        "BacktestService": "src.services.backtest_service",
        "HistoryService": "src.services.history_service",
        "KlineSelectorCriteria": "src.services.kline_selector_service",
        "KlineSelectorPrefilter": "src.services.kline_selector_service",
        "KlineSelectorService": "src.services.kline_selector_service",
        "LimitUpReviewService": "src.services.limit_up_review_service",
        "NextDaySetupSignalRule": "src.services.next_day_setup_service",
        "SignalCauseAnalysisService": "src.services.signal_cause_analysis_service",
        "SignalSnapshotService": "src.services.signal_snapshot_service",
        "StockService": "src.services.stock_service",
        "TaskService": "src.services.task_service",
        "get_task_service": "src.services.task_service",
    }
    if name in _lazy_map:
        import importlib
        module = importlib.import_module(_lazy_map[name])
        return getattr(module, name)
    raise AttributeError(f"module 'src.services' has no attribute {name!r}")


__all__ = [
    "AnalysisService",
    "BacktestService",
    "HistoryService",
    "KlineSelectorCriteria",
    "KlineSelectorPrefilter",
    "KlineSelectorService",
    "LimitUpReviewService",
    "NextDaySetupSignalRule",
    "SignalCauseAnalysisService",
    "SignalSnapshotService",
    "StockService",
    "TaskService",
    "get_task_service",
]
