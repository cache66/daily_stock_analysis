# -*- coding: utf-8 -*-
"""
===================================
数据源基类与管理器
===================================

设计模式：策略模式 (Strategy Pattern)
- BaseFetcher: 抽象基类，定义统一接口
- DataFetcherManager: 策略管理器，实现自动切换

防封禁策略：
1. 每个 Fetcher 内置流控逻辑
2. 失败自动切换到下一个数据源
3. 指数退避重试机制
"""

import json
import logging
import math
import random
import time
from threading import BoundedSemaphore, RLock, Thread
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, List, Tuple, Dict, Any

import pandas as pd
import numpy as np
from src.data.stock_index_loader import get_index_stock_name
from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
from .fundamental_adapter import AkshareFundamentalAdapter

# 配置日志
logger = logging.getLogger(__name__)

DEFAULT_BELONG_BOARDS_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SECTOR_RANKINGS_CACHE_TTL_SECONDS = 120
DEFAULT_BOARD_CONSTITUENTS_CACHE_TTL_SECONDS = 30 * 60
DEFAULT_DAILY_DATA_REQUEST_CALENDAR_SPAN_MULTIPLIER = 2.0
_EARNINGS_QUALITY_POSITIVE_KEYWORDS = (
    "预增",
    "增长",
    "大增",
    "扭亏",
    "超预期",
    "向好",
    "改善",
    "回升",
)
_EARNINGS_QUALITY_NEGATIVE_KEYWORDS = (
    "预减",
    "下滑",
    "亏损",
    "首亏",
    "续亏",
    "承压",
    "恶化",
    "下降",
)


# === 标准化列名定义 ===
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
DAILY_HISTORY_CACHE_COLUMNS = list(STANDARD_COLUMNS)


def _clean_daily_history_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common daily-history columns before indicator calculation."""
    df = df.copy()

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])

    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['close', 'volume'])
    df = df.sort_values('date', ascending=True).reset_index(drop=True)
    return df


def _calculate_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the shared set of lightweight indicators for daily-history rows."""
    df = df.copy()

    df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
    df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
    df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()

    avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
    df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
    df['volume_ratio'] = df['volume_ratio'].fillna(1.0)

    for col in ['ma5', 'ma10', 'ma20', 'volume_ratio']:
        if col in df.columns:
            df[col] = df[col].round(2)

    return df


def _finalize_daily_history_data(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean, indicator-ready daily-history dataframe."""
    if df is None or df.empty:
        return pd.DataFrame(columns=DAILY_HISTORY_CACHE_COLUMNS)
    return _calculate_daily_indicators(_clean_daily_history_data(df))


def unwrap_exception(exc: Exception) -> Exception:
    """
    Follow chained exceptions and return the deepest non-cyclic cause.
    """
    current = exc
    visited = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            break
        current = next_exc

    return current


def summarize_exception(exc: Exception) -> Tuple[str, str]:
    """
    Build a stable summary for logs while preserving the application-layer message.
    """
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())


def normalize_stock_code(stock_code: str) -> str:
    """
    Normalize stock code by stripping exchange prefixes/suffixes.

    Accepted formats and their normalized results:
    - '600519'      -> '600519'   (already clean)
    - 'SH600519'    -> '600519'   (strip SH prefix)
    - 'SZ000001'    -> '000001'   (strip SZ prefix)
    - 'BJ920748'    -> '920748'   (strip BJ prefix, BSE)
    - 'sh600519'    -> '600519'   (case-insensitive)
    - '600519.SH'   -> '600519'   (strip .SH suffix)
    - '000001.SZ'   -> '000001'   (strip .SZ suffix)
    - '920748.BJ'   -> '920748'   (strip .BJ suffix, BSE)
    - 'HK00700'     -> 'HK00700'  (keep HK prefix for HK stocks)
    - '1810.HK'     -> 'HK01810'  (normalize HK suffix to canonical prefix form)
    - 'AAPL'        -> 'AAPL'     (keep US stock ticker as-is)

    This function is applied at the DataProviderManager layer so that
    all individual fetchers receive a clean 6-digit code (for A-shares/ETFs).
    """
    code = stock_code.strip()
    upper = code.upper()

    # Normalize HK prefix to a canonical 5-digit form (e.g. hk1810 -> HK01810)
    if upper.startswith('HK') and not upper.startswith('HK.'):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"

    # Strip SH/SZ prefix (e.g. SH600519 -> 600519)
    if upper.startswith(('SH', 'SZ')) and not upper.startswith('SH.') and not upper.startswith('SZ.'):
        candidate = code[2:]
        # Only strip if the remainder looks like a valid numeric code
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    # Strip BJ prefix (e.g. BJ920748 -> 920748)
    if upper.startswith('BJ') and not upper.startswith('BJ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    # Strip .SH/.SZ/.BJ suffix (e.g. 600519.SH -> 600519, 920748.BJ -> 920748)
    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix.upper() == 'HK' and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if suffix.upper() in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit():
            return base

    return code


ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


def _is_us_market(code: str) -> bool:
    """判断是否为美股/美股指数代码（不含中文前后缀）。"""
    from .us_index_mapping import is_us_stock_code, is_us_index_code

    normalized = (code or "").strip().upper()
    return is_us_index_code(normalized) or is_us_stock_code(normalized)


def _is_hk_market(code: str) -> bool:
    """
    判定是否为港股代码。

    支持 `HK00700` 及纯 5 位数字形式（A 股 ETF/股票常见为 6 位）。
    """
    normalized = (code or "").strip().upper()
    if normalized.endswith(".HK"):
        base = normalized[:-3]
        return base.isdigit() and 1 <= len(base) <= 5
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


def _is_etf_code(code: str) -> bool:
    """判定 A 股 ETF 基金代码（保守规则）。"""
    normalized = normalize_stock_code(code)
    return (
        normalized.isdigit()
        and len(normalized) == 6
        and normalized.startswith(ETF_PREFIXES)
    )


def _market_tag(code: str) -> str:
    """返回市场标签: cn/us/hk."""
    if _is_us_market(code):
        return "us"
    if _is_hk_market(code):
        return "hk"
    return "cn"


def is_bse_code(code: str) -> bool:
    """
    Check if the code is a Beijing Stock Exchange (BSE) A-share code.

    BSE rules (2026):
    - New format (2024+): 92xxxx main trading codes
    - Historical ranges: 43xxxx, 83xxxx, 87xxxx, 88xxxx
    - Special instruments: 81xxxx convertible bonds, 82xxxx preferred shares
    - Subscription codes: 889xxx
    Note: 900xxx are Shanghai B-shares and must return False.
    """
    c = (code or "").strip().split(".")[0]
    if len(c) != 6 or not c.isdigit():
        return False

    if c.startswith("900"):
        return False

    return c.startswith(("92", "43", "81", "82", "83", "87", "88"))

def is_st_stock(name: str) -> bool:
    """
    Check if the stock is an ST or *ST stock based on its name.

    ST stocks have special trading rules and typically a ±5% limit.
    """
    n = (name or "").upper()
    return 'ST' in n

def is_kc_cy_stock(code: str) -> bool:
    """
    Check if the stock is a STAR Market (科创板) or ChiNext (创业板) stock based on its code.

    - STAR Market: Codes starting with 688
    - ChiNext: Codes starting with 300
    Both have a ±20% limit.
    """
    c = (code or "").strip().split(".")[0]
    return c.startswith("688") or c.startswith("30")


def canonical_stock_code(code: str) -> str:
    """
    Return the canonical (uppercase) form of a stock code.

    This is a display/storage layer concern, distinct from normalize_stock_code
    which strips exchange prefixes. Apply at system input boundaries to ensure
    consistent case across BOT, WEB UI, API, and CLI paths (Issue #355).

    Examples:
        'aapl'    -> 'AAPL'
        'AAPL'    -> 'AAPL'
        '600519'  -> '600519'  (digits are unchanged)
        'hk00700' -> 'HK00700'
    """
    return (code or "").strip().upper()


class DataFetchError(Exception):
    """数据获取异常基类"""
    pass


class RateLimitError(DataFetchError):
    """API 速率限制异常"""
    pass


class DataSourceUnavailableError(DataFetchError):
    """数据源不可用异常"""
    pass


class BaseFetcher(ABC):
    """
    数据源抽象基类
    
    职责：
    1. 定义统一的数据获取接口
    2. 提供数据标准化方法
    3. 实现通用的技术指标计算
    
    子类实现：
    - _fetch_raw_data(): 从具体数据源获取原始数据
    - _normalize_data(): 将原始数据转换为标准格式
    """
    
    name: str = "BaseFetcher"
    priority: int = 99  # 优先级数字越小越优先
    
    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        从数据源获取原始数据（子类必须实现）
        
        Args:
            stock_code: 股票代码，如 '600519', '000001'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            
        Returns:
            原始数据 DataFrame（列名因数据源而异）
        """
        pass
    
    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化数据列名（子类必须实现）

        将不同数据源的列名统一为：
        ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        """
        pass

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """
        获取主要指数实时行情

        Args:
            region: 市场区域，cn=A股 us=美股

        Returns:
            List[Dict]: 指数列表，每个元素为字典，包含:
                - code: 指数代码
                - name: 指数名称
                - current: 当前点位
                - change: 涨跌点数
                - change_pct: 涨跌幅(%)
                - volume: 成交量
                - amount: 成交额
        """
        return None

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """
        获取市场涨跌统计

        Returns:
            Dict: 包含:
                - up_count: 上涨家数
                - down_count: 下跌家数
                - flat_count: 平盘家数
                - limit_up_count: 涨停家数
                - limit_down_count: 跌停家数
                - total_amount: 两市成交额
        """
        return None

    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """
        获取板块涨跌榜

        Args:
            n: 返回前n个

        Returns:
            Tuple: (领涨板块列表, 领跌板块列表)
        """
        return None

    def get_daily_data(
        self,
        stock_code: str, 
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> pd.DataFrame:
        """
        获取日线数据（统一入口）
        
        流程：
        1. 计算日期范围
        2. 调用子类获取原始数据
        3. 标准化列名
        4. 计算技术指标
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期（可选）
            end_date: 结束日期（可选，默认今天）
            days: 获取天数（当 start_date 未指定时使用）
            
        Returns:
            标准化的 DataFrame，包含技术指标
        """
        # 计算日期范围
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        if start_date is None:
            # 默认获取最近 30 个交易日（按日历日估算，多取一些）
            from datetime import timedelta
            start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)
            start_date = start_dt.strftime('%Y-%m-%d')

        request_start = time.time()
        logger.info(f"[{self.name}] 开始获取 {stock_code} 日线数据: 范围={start_date} ~ {end_date}")
        
        try:
            # Step 1: 获取原始数据
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
            
            if raw_df is None or raw_df.empty:
                raise DataFetchError(f"[{self.name}] (empty_result) returned empty daily data")
            
            # Step 2: 标准化列名
            df = self._normalize_data(raw_df, stock_code)
            
            # Step 3: 数据清洗
            df = self._clean_data(df)
            
            # Step 4: 计算技术指标
            df = self._calculate_indicators(df)

            elapsed = time.time() - request_start
            logger.info(
                f"[{self.name}] {stock_code} 获取成功: 范围={start_date} ~ {end_date}, "
                f"rows={len(df)}, elapsed={elapsed:.2f}s"
            )
            return df
            
        except Exception as e:
            elapsed = time.time() - request_start
            error_type, error_reason = summarize_exception(e)
            logger.error(
                f"[{self.name}] {stock_code} 获取失败: 范围={start_date} ~ {end_date}, "
                f"error_type={error_type}, elapsed={elapsed:.2f}s, reason={error_reason}"
            )
            raise DataFetchError(f"[{self.name}] {stock_code}: {error_reason}") from e
    
    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        数据清洗
        
        处理：
        1. 确保日期列格式正确
        2. 数值类型转换
        3. 去除空值行
        4. 按日期排序
        """
        df = df.copy()
        
        # 确保日期列为 datetime 类型
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        
        # 数值列类型转换
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 去除关键列为空的行
        df = df.dropna(subset=['close', 'volume'])
        
        # 按日期升序排序
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        
        return df
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标
        
        计算指标：
        - MA5, MA10, MA20: 移动平均线
        - Volume_Ratio: 量比（今日成交量 / 5日平均成交量）
        """
        df = df.copy()
        
        # 移动平均线
        df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
        
        # 量比：当日成交量 / 5日平均成交量
        # 注意：此处的 volume_ratio 是“日线成交量 / 前5日均量(shift 1)”的相对倍数，
        # 与部分交易软件口径的“分时量比（同一时刻对比）”不同，含义更接近“放量倍数”。
        # 该行为目前保留（按需求不改逻辑）。
        avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
        
        # 保留2位小数
        for col in ['ma5', 'ma10', 'ma20', 'volume_ratio']:
            if col in df.columns:
                df[col] = df[col].round(2)
        
        return df
    
    @staticmethod
    def random_sleep(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        """
        智能随机休眠（Jitter）
        
        防封禁策略：模拟人类行为的随机延迟
        在请求之间加入不规则的等待时间
        """
        sleep_time = random.uniform(min_seconds, max_seconds)
        logger.debug(f"随机休眠 {sleep_time:.2f} 秒...")
        time.sleep(sleep_time)


class DataFetcherManager:
    """
    数据源策略管理器
    
    职责：
    1. 管理多个数据源（按优先级排序）
    2. 自动故障切换（Failover）
    3. 提供统一的数据获取接口
    
    切换策略：
    - 优先使用高优先级数据源
    - 失败后自动切换到下一个
    - 所有数据源都失败时抛出异常
    """
    
    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None):
        """
        初始化管理器
        
        Args:
            fetchers: 数据源列表（可选，默认按优先级自动创建）
        """
        self._fetchers: List[BaseFetcher] = []
        self._fetchers_lock = RLock()
        self._fetcher_call_locks: Dict[int, RLock] = {}
        self._fetcher_call_locks_lock = RLock()
        self._stock_name_cache: Dict[str, str] = {}
        self._stock_name_cache_lock = RLock()
        self._belong_boards_cache: Dict[str, Dict[str, Any]] = {}
        self._belong_boards_cache_lock = RLock()
        self._belong_boards_cache_ttl_seconds = DEFAULT_BELONG_BOARDS_CACHE_TTL_SECONDS
        self._sector_rankings_cache: Dict[int, Dict[str, Any]] = {}
        self._sector_rankings_cache_lock = RLock()
        self._sector_rankings_cache_ttl_seconds = DEFAULT_SECTOR_RANKINGS_CACHE_TTL_SECONDS
        self._board_constituents_cache: Dict[str, Dict[str, Any]] = {}
        self._board_constituents_cache_lock = RLock()
        self._board_constituents_cache_ttl_seconds = DEFAULT_BOARD_CONSTITUENTS_CACHE_TTL_SECONDS
        self._history_cache_locks: Dict[str, RLock] = {}
        self._history_cache_locks_lock = RLock()
        
        if fetchers:
            # 按优先级排序
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
        else:
            # 默认数据源将在首次使用时延迟加载
            self._init_default_fetchers()
        self._fundamental_adapter = AkshareFundamentalAdapter()
        self._tickflow_fetcher = None
        self._tickflow_api_key: Optional[str] = None
        self._tickflow_lock = RLock()
        self._fundamental_cache: Dict[str, Dict[str, Any]] = {}
        self._fundamental_cache_lock = RLock()
        self._fundamental_timeout_worker_limit = 8
        self._fundamental_timeout_slots = BoundedSemaphore(self._fundamental_timeout_worker_limit)
        self._daily_data_fetch_timeout_seconds = 0.0
        self._daily_data_timeout_worker_limit = 8
        self._daily_data_timeout_slots = BoundedSemaphore(self._daily_data_timeout_worker_limit)
        self._daily_data_request_calendar_span_multiplier = DEFAULT_DAILY_DATA_REQUEST_CALENDAR_SPAN_MULTIPLIER
        self._daily_data_include_derived_indicators = True
        self._prefer_cached_history_when_covered = False

    def _ensure_concurrency_guards(self) -> None:
        """Lazily initialize thread-safety primitives for test scaffolds using __new__."""
        if not hasattr(self, "_fetchers_lock") or self._fetchers_lock is None:
            self._fetchers_lock = RLock()
        if not hasattr(self, "_fetcher_call_locks") or self._fetcher_call_locks is None:
            self._fetcher_call_locks = {}
        if not hasattr(self, "_fetcher_call_locks_lock") or self._fetcher_call_locks_lock is None:
            self._fetcher_call_locks_lock = RLock()
        if not hasattr(self, "_stock_name_cache") or self._stock_name_cache is None:
            self._stock_name_cache = {}
        if not hasattr(self, "_stock_name_cache_lock") or self._stock_name_cache_lock is None:
            self._stock_name_cache_lock = RLock()
        if not hasattr(self, "_belong_boards_cache") or self._belong_boards_cache is None:
            self._belong_boards_cache = {}
        if not hasattr(self, "_belong_boards_cache_lock") or self._belong_boards_cache_lock is None:
            self._belong_boards_cache_lock = RLock()
        if not hasattr(self, "_belong_boards_cache_ttl_seconds") or self._belong_boards_cache_ttl_seconds is None:
            self._belong_boards_cache_ttl_seconds = DEFAULT_BELONG_BOARDS_CACHE_TTL_SECONDS
        if not hasattr(self, "_sector_rankings_cache") or self._sector_rankings_cache is None:
            self._sector_rankings_cache = {}
        if not hasattr(self, "_sector_rankings_cache_lock") or self._sector_rankings_cache_lock is None:
            self._sector_rankings_cache_lock = RLock()
        if not hasattr(self, "_sector_rankings_cache_ttl_seconds") or self._sector_rankings_cache_ttl_seconds is None:
            self._sector_rankings_cache_ttl_seconds = DEFAULT_SECTOR_RANKINGS_CACHE_TTL_SECONDS
        if not hasattr(self, "_board_constituents_cache") or self._board_constituents_cache is None:
            self._board_constituents_cache = {}
        if not hasattr(self, "_board_constituents_cache_lock") or self._board_constituents_cache_lock is None:
            self._board_constituents_cache_lock = RLock()
        if not hasattr(self, "_board_constituents_cache_ttl_seconds") or self._board_constituents_cache_ttl_seconds is None:
            self._board_constituents_cache_ttl_seconds = DEFAULT_BOARD_CONSTITUENTS_CACHE_TTL_SECONDS
        if not hasattr(self, "_history_cache_locks") or self._history_cache_locks is None:
            self._history_cache_locks = {}
        if not hasattr(self, "_history_cache_locks_lock") or self._history_cache_locks_lock is None:
            self._history_cache_locks_lock = RLock()
        if not hasattr(self, "_fundamental_timeout_worker_limit") or self._fundamental_timeout_worker_limit is None:
            self._fundamental_timeout_worker_limit = 8
        if not hasattr(self, "_fundamental_timeout_slots") or self._fundamental_timeout_slots is None:
            self._fundamental_timeout_slots = BoundedSemaphore(self._fundamental_timeout_worker_limit)
        if not hasattr(self, "_daily_data_fetch_timeout_seconds") or self._daily_data_fetch_timeout_seconds is None:
            self._daily_data_fetch_timeout_seconds = 0.0
        if not hasattr(self, "_daily_data_timeout_worker_limit") or self._daily_data_timeout_worker_limit is None:
            self._daily_data_timeout_worker_limit = 8
        if not hasattr(self, "_daily_data_timeout_slots") or self._daily_data_timeout_slots is None:
            self._daily_data_timeout_slots = BoundedSemaphore(self._daily_data_timeout_worker_limit)
        if (
            not hasattr(self, "_daily_data_request_calendar_span_multiplier")
            or self._daily_data_request_calendar_span_multiplier is None
        ):
            self._daily_data_request_calendar_span_multiplier = (
                DEFAULT_DAILY_DATA_REQUEST_CALENDAR_SPAN_MULTIPLIER
            )
        if (
            not hasattr(self, "_daily_data_include_derived_indicators")
            or self._daily_data_include_derived_indicators is None
        ):
            self._daily_data_include_derived_indicators = True
        if not hasattr(self, "_prefer_cached_history_when_covered") or self._prefer_cached_history_when_covered is None:
            self._prefer_cached_history_when_covered = False

    def _get_fetchers_snapshot(self) -> List[BaseFetcher]:
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            return list(getattr(self, "_fetchers", []))

    def _get_fetcher_call_lock(self, fetcher: BaseFetcher) -> RLock:
        self._ensure_concurrency_guards()
        fetcher_id = id(fetcher)
        with self._fetcher_call_locks_lock:
            lock = self._fetcher_call_locks.get(fetcher_id)
            if lock is None:
                lock = RLock()
                self._fetcher_call_locks[fetcher_id] = lock
            return lock

    def _call_fetcher_method(self, fetcher: BaseFetcher, method_name: str, *args, **kwargs):
        """Serialize shared fetcher state access through manager-owned per-instance locks."""
        method = getattr(fetcher, method_name)
        with self._get_fetcher_call_lock(fetcher):
            return method(*args, **kwargs)

    def _call_fetcher_daily_data(
        self,
        fetcher: BaseFetcher,
        stock_code: str,
        start_date: Optional[str],
        end_date: Optional[str],
        days: int,
    ):
        timeout_seconds = max(0.0, float(getattr(self, "_daily_data_fetch_timeout_seconds", 0.0) or 0.0))
        if timeout_seconds <= 0:
            return self._call_fetcher_method(
                fetcher,
                "get_daily_data",
                stock_code=stock_code,
                start_date=start_date,
                end_date=end_date,
                days=days,
            )

        task_name = f"{fetcher.name}.get_daily_data({stock_code})"
        result, err, _ = self._run_with_timeout_with_slots(
            lambda: self._call_fetcher_method(
                fetcher,
                "get_daily_data",
                stock_code=stock_code,
                start_date=start_date,
                end_date=end_date,
                days=days,
            ),
            timeout_seconds=timeout_seconds,
            task_name=task_name,
            slots=self._daily_data_timeout_slots,
            worker_name_prefix="daily-data",
        )
        if err is not None:
            raise DataFetchError(err)
        return result

    def _get_history_cache_lock(self, cache_key: str) -> RLock:
        self._ensure_concurrency_guards()
        with self._history_cache_locks_lock:
            lock = self._history_cache_locks.get(cache_key)
            if lock is None:
                lock = RLock()
                self._history_cache_locks[cache_key] = lock
            return lock

    def _resolve_daily_data_request_range(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        days: int,
    ) -> Tuple[str, str]:
        resolved_end = end_date or datetime.now().strftime('%Y-%m-%d')
        if start_date:
            return start_date, resolved_end

        multiplier = max(
            1.0,
            float(
                getattr(
                    self,
                    "_daily_data_request_calendar_span_multiplier",
                    DEFAULT_DAILY_DATA_REQUEST_CALENDAR_SPAN_MULTIPLIER,
                )
                or DEFAULT_DAILY_DATA_REQUEST_CALENDAR_SPAN_MULTIPLIER
            ),
        )
        calendar_days = max(1, int(math.ceil(max(1, int(days)) * multiplier)))
        start_dt = datetime.strptime(resolved_end, '%Y-%m-%d') - timedelta(days=calendar_days)
        return start_dt.strftime('%Y-%m-%d'), resolved_end

    @staticmethod
    def _storage_history_columns(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=DAILY_HISTORY_CACHE_COLUMNS)

        prepared = df.copy()
        if 'date' in prepared.columns:
            prepared['date'] = pd.to_datetime(prepared['date'])

        keep_columns = [col for col in DAILY_HISTORY_CACHE_COLUMNS if col in prepared.columns]
        prepared = prepared[keep_columns].copy()
        prepared = prepared.dropna(subset=['close', 'volume'])
        prepared = prepared.sort_values('date', ascending=True).reset_index(drop=True)
        return prepared

    def _finalize_history_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=DAILY_HISTORY_CACHE_COLUMNS)

        if 'date' in df.columns:
            df = df.copy()
            df['date'] = pd.to_datetime(df['date'])
        if not bool(getattr(self, "_daily_data_include_derived_indicators", True)):
            return _clean_daily_history_data(df)
        return _finalize_daily_history_data(df)

    @staticmethod
    def _slice_history_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        if df is None or df.empty or 'date' not in df.columns:
            return pd.DataFrame()

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        sliced = df.loc[(df['date'] >= start_ts) & (df['date'] <= end_ts)].copy()
        return sliced.reset_index(drop=True)

    def _merge_history_frames(self, *frames: pd.DataFrame) -> pd.DataFrame:
        prepared_frames = []
        for frame in frames:
            if frame is None or frame.empty:
                continue
            prepared = DataFetcherManager._storage_history_columns(frame)
            if not prepared.empty:
                prepared_frames.append(prepared)

        if not prepared_frames:
            return pd.DataFrame(columns=DAILY_HISTORY_CACHE_COLUMNS)

        merged = pd.concat(prepared_frames, ignore_index=True, sort=False)
        merged['date'] = pd.to_datetime(merged['date'])
        merged = merged.sort_values('date', ascending=True)
        merged = merged.drop_duplicates(subset=['date'], keep='last').reset_index(drop=True)
        return self._finalize_history_frame(merged)

    def _get_history_cache_config(self) -> Dict[str, Any]:
        from src.config import get_config

        config = get_config()
        return {
            "enabled": bool(getattr(config, "history_disk_cache_enabled", True)),
            "cache_dir": Path(getattr(config, "history_disk_cache_dir", "./data/cache/history")),
            "ttl_seconds": max(0, int(getattr(config, "history_disk_cache_ttl_seconds", 21600))),
            "overlap_days": max(0, int(getattr(config, "history_disk_cache_overlap_days", 5))),
        }

    @staticmethod
    def _history_cache_key(stock_code: str) -> str:
        return f"{_market_tag(stock_code)}:{canonical_stock_code(stock_code)}"

    @staticmethod
    def _history_cache_filename(stock_code: str) -> str:
        code = canonical_stock_code(stock_code)
        for src, dest in (("/", "_"), ("\\", "_"), (":", "_"), ("*", "_"), ("?", "_"), ('"', "_"), ("<", "_"), (">", "_"), ("|", "_")):
            code = code.replace(src, dest)
        return code

    def _get_history_cache_paths(self, stock_code: str) -> Tuple[Path, Path]:
        settings = self._get_history_cache_config()
        cache_root = settings["cache_dir"] / _market_tag(stock_code)
        filename = self._history_cache_filename(stock_code)
        return cache_root / f"{filename}.csv", cache_root / f"{filename}.json"

    @staticmethod
    def _history_cache_is_fresh(metadata: Dict[str, Any], request_end_date: str, ttl_seconds: int) -> bool:
        try:
            request_end = datetime.strptime(request_end_date, '%Y-%m-%d').date()
        except ValueError:
            return False

        if request_end < datetime.now().date():
            return True
        if ttl_seconds <= 0:
            return False

        updated_at = float(metadata.get("updated_at") or 0)
        if updated_at <= 0:
            return False
        return (time.time() - updated_at) <= ttl_seconds

    def _read_history_cache(self, stock_code: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        csv_path, metadata_path = self._get_history_cache_paths(stock_code)
        if not csv_path.exists():
            return pd.DataFrame(), {}

        try:
            cached_df = pd.read_csv(csv_path)
        except Exception as exc:
            logger.warning("[history cache] failed to read %s: %s", csv_path, exc)
            return pd.DataFrame(), {}

        metadata: Dict[str, Any] = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[history cache] failed to read metadata %s: %s", metadata_path, exc)

        if not metadata:
            metadata = {"updated_at": csv_path.stat().st_mtime}

        return self._finalize_history_frame(cached_df), metadata

    def _write_history_cache(self, stock_code: str, df: pd.DataFrame, source: str) -> None:
        csv_path, metadata_path = self._get_history_cache_paths(stock_code)
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        storage_df = self._storage_history_columns(df)
        storage_df.to_csv(csv_path, index=False, encoding="utf-8")

        metadata = {
            "stock_code": canonical_stock_code(stock_code),
            "market": _market_tag(stock_code),
            "source": source,
            "updated_at": time.time(),
            "rows": int(len(storage_df)),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_cached_stock_name(self, stock_code: str) -> Optional[str]:
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            return self._stock_name_cache.get(stock_code)

    def _cache_stock_name(self, stock_code: str, name: Optional[str]) -> Optional[str]:
        if name is None:
            return None
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            self._stock_name_cache[stock_code] = name
        return name

    def _get_cached_belong_boards(self, stock_code: str) -> Optional[List[Dict[str, Any]]]:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_belong_boards_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0:
            return None
        now_ts = time.time()
        with self._belong_boards_cache_lock:
            item = self._belong_boards_cache.get(stock_code)
            if not item:
                return None
            if now_ts - float(item.get("ts", 0)) > ttl_seconds:
                self._belong_boards_cache.pop(stock_code, None)
                return None
            boards = item.get("boards") or []
            return [dict(board) for board in boards if isinstance(board, dict)]

    def _cache_belong_boards(self, stock_code: str, boards: List[Dict[str, Any]]) -> None:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_belong_boards_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0 or not boards:
            return
        with self._belong_boards_cache_lock:
            self._belong_boards_cache[stock_code] = {
                "ts": time.time(),
                "boards": [dict(board) for board in boards if isinstance(board, dict)],
            }

    def _get_cached_sector_rankings(
        self,
        n: int,
    ) -> Optional[Tuple[List[Dict], List[Dict], List[Dict[str, Any]], str]]:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_sector_rankings_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0:
            return None
        now_ts = time.time()
        with self._sector_rankings_cache_lock:
            item = self._sector_rankings_cache.get(int(n))
            if not item:
                return None
            if now_ts - float(item.get("ts", 0)) > ttl_seconds:
                self._sector_rankings_cache.pop(int(n), None)
                return None
            top = [dict(row) for row in (item.get("top") or []) if isinstance(row, dict)]
            bottom = [dict(row) for row in (item.get("bottom") or []) if isinstance(row, dict)]
            source_chain = [
                dict(row) for row in (item.get("source_chain") or []) if isinstance(row, dict)
            ]
            return top, bottom, source_chain, str(item.get("last_error") or "")

    def _cache_sector_rankings(
        self,
        n: int,
        *,
        top: List[Dict],
        bottom: List[Dict],
        source_chain: List[Dict[str, Any]],
        last_error: str,
    ) -> None:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_sector_rankings_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0 or (not top and not bottom):
            return
        with self._sector_rankings_cache_lock:
            self._sector_rankings_cache[int(n)] = {
                "ts": time.time(),
                "top": [dict(row) for row in top if isinstance(row, dict)],
                "bottom": [dict(row) for row in bottom if isinstance(row, dict)],
                "source_chain": [dict(row) for row in source_chain if isinstance(row, dict)],
                "last_error": str(last_error or ""),
            }

    def _get_cached_board_constituents(self, cache_key: str) -> Optional[pd.DataFrame]:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_board_constituents_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0:
            return None
        now_ts = time.time()
        with self._board_constituents_cache_lock:
            item = self._board_constituents_cache.get(cache_key)
            if not item:
                return None
            if now_ts - float(item.get("ts", 0)) > ttl_seconds:
                self._board_constituents_cache.pop(cache_key, None)
                return None
            df = item.get("df")
            if isinstance(df, pd.DataFrame):
                return df.copy()
            return None

    def _cache_board_constituents(self, cache_key: str, df: pd.DataFrame) -> None:
        self._ensure_concurrency_guards()
        ttl_seconds = max(0, int(getattr(self, "_board_constituents_cache_ttl_seconds", 0)))
        if ttl_seconds <= 0 or df is None or df.empty:
            return
        with self._board_constituents_cache_lock:
            self._board_constituents_cache[cache_key] = {
                "ts": time.time(),
                "df": df.copy(),
            }

    def _get_tickflow_fetcher(self):
        """Lazily create a TickFlow fetcher for market-review-only calls."""
        from src.config import get_config

        config = get_config()
        api_key = (getattr(config, "tickflow_api_key", None) or "").strip()

        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            current_key = getattr(self, "_tickflow_api_key", None)

            if not api_key:
                if current_fetcher is not None and hasattr(current_fetcher, "close"):
                    try:
                        current_fetcher.close()
                    except Exception as exc:
                        logger.debug("[TickFlowFetcher] 关闭旧实例失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

            if current_fetcher is not None and current_key == api_key:
                return current_fetcher

            if current_fetcher is not None and hasattr(current_fetcher, "close"):
                try:
                    current_fetcher.close()
                except Exception as exc:
                    logger.debug("[TickFlowFetcher] 切换实例时关闭失败: %s", exc)

            try:
                from .tickflow_fetcher import TickFlowFetcher

                fetcher = TickFlowFetcher(api_key=api_key)
                self._tickflow_fetcher = fetcher
                self._tickflow_api_key = api_key
                return fetcher
            except Exception as exc:
                logger.warning("[TickFlowFetcher] 初始化失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

    def close(self) -> None:
        """Best-effort release of manager-owned resources."""
        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            self._tickflow_fetcher = None
            self._tickflow_api_key = None

        if current_fetcher is not None and hasattr(current_fetcher, "close"):
            try:
                current_fetcher.close()
            except Exception as exc:
                logger.debug("[TickFlowFetcher] 关闭管理器资源失败: %s", exc)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Best-effort cleanup during interpreter shutdown.
            pass

    def _get_fundamental_cache_key(self, stock_code: str, budget_seconds: Optional[float] = None) -> str:
        """生成基本面缓存 key（包含预算分桶以避免低预算结果污染高预算请求）。"""
        normalized_code = normalize_stock_code(stock_code)
        if budget_seconds is None:
            return f"{normalized_code}|budget=default"
        try:
            budget = max(0.0, float(budget_seconds))
        except (TypeError, ValueError):
            budget = 0.0
        # 100ms bucket to balance cache reuse and scenario isolation.
        budget_bucket = int(round(budget * 10))
        return f"{normalized_code}|budget={budget_bucket}"

    def _prune_fundamental_cache(self, ttl_seconds: int, max_entries: int) -> None:
        """Prune expired and overflow fundamental cache items."""
        with self._fundamental_cache_lock:
            if not self._fundamental_cache:
                return

            now_ts = time.time()
            if ttl_seconds > 0:
                cache_items = list(self._fundamental_cache.items())
                expired_keys = [
                    key
                    for key, value in cache_items
                    if now_ts - float(value.get("ts", 0)) > ttl_seconds
                ]
                for key in expired_keys:
                    self._fundamental_cache.pop(key, None)

            if max_entries > 0 and len(self._fundamental_cache) > max_entries:
                overflow = len(self._fundamental_cache) - max_entries
                sorted_items = sorted(
                    list(self._fundamental_cache.items()),
                    key=lambda item: float(item[1].get("ts", 0)),
                )
                for key, _ in sorted_items[:overflow]:
                    self._fundamental_cache.pop(key, None)

    @staticmethod
    def _try_scalar_isna(value: Any, context: str) -> Optional[bool]:
        """Return scalar ``pd.isna`` result, or ``None`` when callers should use fallback logic."""
        if isinstance(value, (dict, list, tuple, set, pd.DataFrame, pd.Series, pd.Index)):
            return None

        if isinstance(value, np.ndarray):
            if value.ndim != 0:
                return None
            value = value.item()

        try:
            isna_result = pd.isna(value)
        except (TypeError, ValueError) as exc:
            if hasattr(value, "__array__"):
                logger.debug(
                    "[%s] pd.isna failed for array-like object; re-raise: value_type=%s error_type=%s",
                    context,
                    type(value).__name__,
                    type(exc).__name__,
                )
                raise
            logger.debug(
                "[%s] pd.isna fallback: value_type=%s error_type=%s",
                context,
                type(value).__name__,
                type(exc).__name__,
            )
            return None

        if isinstance(isna_result, (bool, np.bool_)):
            return bool(isna_result)

        if isinstance(isna_result, np.ndarray):
            if isna_result.ndim == 0:
                return bool(isna_result.item())
            logger.debug(
                "[%s] pd.isna returned non-scalar result: value_type=%s result_type=%s",
                context,
                type(value).__name__,
                type(isna_result).__name__,
            )
            return None

        logger.debug(
            "[%s] pd.isna returned unexpected result type: value_type=%s result_type=%s",
            context,
            type(value).__name__,
            type(isna_result).__name__,
        )
        return None

    @staticmethod
    def _is_missing_board_value(value: Any) -> bool:
        """Return True when a board field value should be treated as missing."""
        if value is None:
            return True
        is_missing = DataFetcherManager._try_scalar_isna(value, "board_value")
        if is_missing is True:
            return True
        text = str(value).strip()
        return text == "" or text.lower() in {"nan", "none", "null", "na", "n/a"}

    @staticmethod
    def _normalize_belong_boards(raw_data: Any) -> List[Dict[str, Any]]:
        """Normalize belong-board results from heterogeneous providers."""
        if DataFetcherManager._is_missing_board_value(raw_data):
            return []

        normalized: List[Dict[str, Any]] = []
        dedupe = set()

        if isinstance(raw_data, pd.DataFrame):
            if raw_data.empty:
                return []
            name_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块名称", "板块", "所属板块", "板块名", "name", "industry"}
                ),
                None,
            )
            code_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块代码", "代码", "code"}
                ),
                None,
            )
            type_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块类型", "类别", "type"}
                ),
                None,
            )
            if name_col is None:
                return []
            for _, row in raw_data.iterrows():
                board_name_raw = row.get(name_col, "")
                if DataFetcherManager._is_missing_board_value(board_name_raw):
                    continue
                board_name = str(board_name_raw).strip()
                if board_name in dedupe:
                    continue
                dedupe.add(board_name)
                item = {"name": board_name}
                if code_col is not None:
                    board_code_raw = row.get(code_col, "")
                    if not DataFetcherManager._is_missing_board_value(board_code_raw):
                        item["code"] = str(board_code_raw).strip()
                if type_col is not None:
                    board_type_raw = row.get(type_col, "")
                    if not DataFetcherManager._is_missing_board_value(board_type_raw):
                        item["type"] = str(board_type_raw).strip()
                normalized.append(item)
            return normalized

        if isinstance(raw_data, dict):
            raw_data = [raw_data]

        if isinstance(raw_data, (list, tuple, set)):
            for item in raw_data:
                if isinstance(item, dict):
                    board_name_raw = (
                        item.get("name")
                        or item.get("board_name")
                        or item.get("板块名称")
                        or item.get("板块")
                        or item.get("所属板块")
                        or item.get("板块名")
                        or item.get("industry")
                        or item.get("行业")
                    )
                    if DataFetcherManager._is_missing_board_value(board_name_raw):
                        continue
                    board_name = str(board_name_raw).strip()
                    if board_name in dedupe:
                        continue
                    dedupe.add(board_name)
                    normalized_item: Dict[str, Any] = {"name": board_name}
                    code_raw = (
                        item.get("code")
                        or item.get("板块代码")
                        or item.get("代码")
                    )
                    if not DataFetcherManager._is_missing_board_value(code_raw):
                        normalized_item["code"] = str(code_raw).strip()
                    type_raw = (
                        item.get("type")
                        or item.get("板块类型")
                        or item.get("类别")
                    )
                    if not DataFetcherManager._is_missing_board_value(type_raw):
                        normalized_item["type"] = str(type_raw).strip()
                    normalized.append(normalized_item)
                    continue
                if DataFetcherManager._is_missing_board_value(item):
                    continue
                board_name = str(item).strip()
                if board_name in dedupe:
                    continue
                dedupe.add(board_name)
                normalized.append({"name": board_name})
            return normalized

        if not DataFetcherManager._is_missing_board_value(raw_data):
            board_name = str(raw_data).strip()
            return [{"name": board_name}]
        return []
    
    def _init_default_fetchers(self) -> None:
        """
        初始化默认数据源列表

        优先级动态调整逻辑：
        - 如果配置了 TUSHARE_TOKEN：Tushare 优先级提升为 0（最高）
        - 否则按默认优先级：
          0. EfinanceFetcher (Priority 0) - 最高优先级
          1. AkshareFetcher (Priority 1)
          2. PytdxFetcher (Priority 2) - 通达信
          2. TushareFetcher (Priority 2)
          3. BaostockFetcher (Priority 3)
          4. YfinanceFetcher (Priority 4)
          5. LongbridgeFetcher (Priority 5) - 长桥（美股/港股兜底）
        """
        from .efinance_fetcher import EfinanceFetcher
        from .akshare_fetcher import AkshareFetcher
        from .tushare_fetcher import TushareFetcher
        from .pytdx_fetcher import PytdxFetcher
        from .baostock_fetcher import BaostockFetcher
        from .yfinance_fetcher import YfinanceFetcher
        from .longbridge_fetcher import LongbridgeFetcher
        # 创建所有数据源实例（优先级在各 Fetcher 的 __init__ 中确定）
        efinance = EfinanceFetcher()
        akshare = AkshareFetcher()
        tushare = TushareFetcher()  # 会根据 Token 配置自动调整优先级
        pytdx = PytdxFetcher()      # 通达信数据源（可配 PYTDX_HOST/PYTDX_PORT）
        baostock = BaostockFetcher()
        yfinance = YfinanceFetcher()
        longbridge = LongbridgeFetcher()  # 长桥（美股/港股兜底，懒加载）

        # 初始化数据源列表
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            self._fetchers = [
                efinance,
                akshare,
                tushare,
                pytdx,
                baostock,
                yfinance,
                longbridge,
            ]

            # 按优先级排序（Tushare 如果配置了 Token 且初始化成功，优先级为 0）
            self._fetchers.sort(key=lambda f: f.priority)

        # 构建优先级说明
        priority_info = ", ".join([f"{f.name}(P{f.priority})" for f in self._get_fetchers_snapshot()])
        logger.info(f"已初始化 {len(self._fetchers)} 个数据源（按优先级）: {priority_info}")
    
    def add_fetcher(self, fetcher: BaseFetcher) -> None:
        """添加数据源并重新排序"""
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            self._fetchers.append(fetcher)
            self._fetchers.sort(key=lambda f: f.priority)
    
    def get_daily_data(
        self, 
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Tuple[pd.DataFrame, str]:
        """
        获取日线数据（自动切换数据源）
        
        故障切换策略：
        1. 美股指数/美股股票直接路由到 YfinanceFetcher
        2. 其他代码从最高优先级数据源开始尝试
        3. 捕获异常后自动切换到下一个
        4. 记录每个数据源的失败原因
        5. 所有数据源失败后抛出详细异常
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            days: 获取天数
            
        Returns:
            Tuple[DataFrame, str]: (数据, 成功的数据源名称)
            
        Raises:
            DataFetchError: 所有数据源都失败时抛出
        """
        from .us_index_mapping import is_us_index_code, is_us_stock_code

        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        fetchers = self._get_fetchers_snapshot()
        errors = []
        total_fetchers = len(fetchers)
        request_start = time.time()

        def _record_empty_result(fetcher: BaseFetcher, attempt: int) -> None:
            error_msg = f"[{fetcher.name}] (empty_result) returned empty daily data"
            logger.warning(
                f"[manager empty_result {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                "returned empty daily data"
            )
            errors.append(error_msg)

        # 快速路径：美股/港股使用专用数据源路由
        #   - 配置长桥凭据后: Longbridge 为首选, YFinance/AkShare 兜底
        #   - 未配置长桥:     YFinance 为首选（美股）, 通用 fetcher 循环（港股）
        #   - 美股指数:       始终 YFinance 为首选（Longbridge 不提供指数K线）
        is_us_index = is_us_index_code(stock_code)
        is_us = is_us_index or is_us_stock_code(stock_code)
        is_hk = (not is_us) and _is_hk_market(stock_code)

        # 美股（含美股指数）使用 Longbridge/YFinance 特殊路由；港股走下方通用数据源循环
        if is_us:
            prefer_lb = self._longbridge_preferred() and not is_us_index
            source_order = (
                ["LongbridgeFetcher", "YfinanceFetcher"]
                if prefer_lb
                else ["YfinanceFetcher", "LongbridgeFetcher"]
            )
            market_label = "美股指数" if is_us_index else "美股"

            for src_name in source_order:
                for attempt, fetcher in enumerate(fetchers, start=1):
                    if fetcher.name != src_name:
                        continue
                    try:
                        role = "首选" if src_name == source_order[0] else "兜底"
                        logger.info(
                            f"[数据源尝试 {attempt}/{total_fetchers}] [{fetcher.name}] "
                            f"{market_label} {stock_code} {role}路由..."
                        )
                        df = self._call_fetcher_daily_data(
                            fetcher,
                            stock_code,
                            start_date,
                            end_date,
                            days,
                        )
                        if df is not None and not df.empty:
                            elapsed = time.time() - request_start
                            logger.info(
                                f"[数据源完成] {stock_code} 使用 [{fetcher.name}] 获取成功: "
                                f"rows={len(df)}, elapsed={elapsed:.2f}s"
                            )
                            return df, fetcher.name
                        _record_empty_result(fetcher, attempt)
                    except Exception as e:
                        error_type, error_reason = summarize_exception(e)
                        error_msg = f"[{fetcher.name}] ({error_type}) {error_reason}"
                        logger.warning(
                            f"[数据源失败 {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                            f"error_type={error_type}, reason={error_reason}"
                        )
                        errors.append(error_msg)
                    break

            error_summary = f"{market_label} {stock_code} 获取失败:\n" + "\n".join(errors)
            elapsed = time.time() - request_start
            logger.error(f"[数据源终止] {stock_code} 获取失败: elapsed={elapsed:.2f}s\n{error_summary}")
            raise DataFetchError(error_summary)

        for attempt, fetcher in enumerate(fetchers, start=1):
            try:
                logger.info(f"[数据源尝试 {attempt}/{total_fetchers}] [{fetcher.name}] 获取 {stock_code}...")
                df = self._call_fetcher_daily_data(
                    fetcher,
                    stock_code,
                    start_date,
                    end_date,
                    days,
                )
                
                if df is not None and not df.empty:
                    elapsed = time.time() - request_start
                    logger.info(
                        f"[数据源完成] {stock_code} 使用 [{fetcher.name}] 获取成功: "
                        f"rows={len(df)}, elapsed={elapsed:.2f}s"
                    )
                    return df, fetcher.name
                _record_empty_result(fetcher, attempt)

            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                error_msg = f"[{fetcher.name}] ({error_type}) {error_reason}"
                logger.warning(
                    f"[数据源失败 {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                    f"error_type={error_type}, reason={error_reason}"
                )
                errors.append(error_msg)
                if attempt < total_fetchers:
                    next_fetcher = fetchers[attempt]
                    logger.info(f"[数据源切换] {stock_code}: [{fetcher.name}] -> [{next_fetcher.name}]")
                # 继续尝试下一个数据源
                continue
        
        # 所有数据源都失败
        error_summary = f"所有数据源获取 {stock_code} 失败:\n" + "\n".join(errors)
        elapsed = time.time() - request_start
        logger.error(f"[数据源终止] {stock_code} 获取失败: elapsed={elapsed:.2f}s\n{error_summary}")
        raise DataFetchError(error_summary)
    
    _fetch_daily_data_from_sources = get_daily_data

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
        force_refresh: bool = False,
    ) -> Tuple[pd.DataFrame, str]:
        """Fetch daily history with manager-level disk cache and incremental top-up."""
        stock_code = normalize_stock_code(stock_code)
        auto_range_request = start_date is None
        resolved_start_date, resolved_end_date = self._resolve_daily_data_request_range(
            start_date=start_date,
            end_date=end_date,
            days=days,
        )
        cache_settings = self._get_history_cache_config()

        if not cache_settings["enabled"]:
            return self._fetch_daily_data_from_sources(
                stock_code,
                start_date=resolved_start_date,
                end_date=resolved_end_date,
                days=days,
            )

        cache_key = self._history_cache_key(stock_code)
        with self._get_history_cache_lock(cache_key):
            cached_df, metadata = self._read_history_cache(stock_code)
            cached_source = str(metadata.get("source") or "").strip()
            cache_fresh = self._history_cache_is_fresh(
                metadata,
                resolved_end_date,
                cache_settings["ttl_seconds"],
            )

            if not force_refresh and not cached_df.empty:
                cached_start_date = cached_df['date'].min().strftime('%Y-%m-%d')
                cached_end_date = cached_df['date'].max().strftime('%Y-%m-%d')
                cache_covers_request = (
                    cached_start_date <= resolved_start_date <= resolved_end_date <= cached_end_date
                )

                if cache_covers_request and cache_fresh:
                    return (
                        self._slice_history_range(cached_df, resolved_start_date, resolved_end_date),
                        f"disk_cache:{cached_source}" if cached_source else "disk_cache",
                    )
                if cache_covers_request and getattr(self, "_prefer_cached_history_when_covered", False):
                    return (
                        self._slice_history_range(cached_df, resolved_start_date, resolved_end_date),
                        (
                            f"disk_cache_stale_covered:{cached_source}"
                            if cached_source else
                            "disk_cache_stale_covered"
                        ),
                    )

                # For days-based requests (no explicit start_date), tolerate a tiny tail lag to avoid
                # unnecessary retries on weekends/holidays or short-lived endpoint instability.
                if auto_range_request:
                    resolved_end_ts = pd.Timestamp(resolved_end_date)
                    cached_end_ts = pd.Timestamp(cached_end_date)
                    end_gap_days = int((resolved_end_ts - cached_end_ts).days)
                    allow_end_lag_days = 3
                    if end_gap_days <= allow_end_lag_days:
                        sliced_cached = self._slice_history_range(cached_df, resolved_start_date, resolved_end_date)
                        required_rows = max(1, int(days))
                        if len(sliced_cached) >= required_rows:
                            cache_mode = (
                                "disk_cache_best_effort"
                                if end_gap_days <= 0
                                else "disk_cache_best_effort_stale"
                            )
                            return (
                                sliced_cached,
                                f"{cache_mode}:{cached_source}" if cached_source else cache_mode,
                            )

                merged_df = cached_df
                active_source = cached_source

                if resolved_start_date < cached_start_date:
                    head_end = (pd.Timestamp(cached_start_date) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                    head_df, head_source = self._fetch_daily_data_from_sources(
                        stock_code,
                        start_date=resolved_start_date,
                        end_date=head_end,
                        days=days,
                    )
                    merged_df = self._merge_history_frames(head_df, merged_df)
                    active_source = head_source or active_source

                should_refresh_tail = (resolved_end_date > cached_end_date) or (not cache_fresh)
                if should_refresh_tail:
                    overlap_days = cache_settings["overlap_days"]
                    tail_start = max(
                        pd.Timestamp(resolved_start_date),
                        pd.Timestamp(cached_end_date) - pd.Timedelta(days=overlap_days),
                    ).strftime('%Y-%m-%d')
                    try:
                        tail_df, tail_source = self._fetch_daily_data_from_sources(
                            stock_code,
                            start_date=tail_start,
                            end_date=resolved_end_date,
                            days=days,
                        )
                    except Exception:
                        if cache_covers_request and resolved_end_date <= cached_end_date:
                            logger.warning(
                                "[history cache] stale refresh failed for %s, falling back to cached history",
                                stock_code,
                                exc_info=True,
                            )
                            return (
                                self._slice_history_range(cached_df, resolved_start_date, resolved_end_date),
                                f"disk_cache_stale:{cached_source}" if cached_source else "disk_cache_stale",
                            )
                        raise

                    merged_df = self._merge_history_frames(merged_df, tail_df)
                    active_source = tail_source or active_source

                if not merged_df.empty:
                    self._write_history_cache(stock_code, merged_df, active_source or cached_source or "unknown")
                    sliced = self._slice_history_range(merged_df, resolved_start_date, resolved_end_date)
                    if not sliced.empty:
                        return sliced, active_source or cached_source or "unknown"

            network_df, network_source = self._fetch_daily_data_from_sources(
                stock_code,
                start_date=resolved_start_date,
                end_date=resolved_end_date,
                days=days,
            )
            finalized_df = self._finalize_history_frame(network_df)
            self._write_history_cache(stock_code, finalized_df, network_source)
            return self._slice_history_range(finalized_df, resolved_start_date, resolved_end_date), network_source

    @property
    def available_fetchers(self) -> List[str]:
        """返回可用数据源名称列表"""
        return [f.name for f in self._get_fetchers_snapshot()]
    
    def prefetch_realtime_quotes(self, stock_codes: List[str]) -> int:
        """
        批量预取实时行情数据（在分析开始前调用）
        
        策略：
        1. 检查优先级中是否包含全量拉取数据源（efinance/akshare_em）
        2. 如果不包含，跳过预取（新浪/腾讯是单股票查询，无需预取）
        3. 如果自选股数量 >= 5 且使用全量数据源，则预取填充缓存
        
        这样做的好处：
        - 使用新浪/腾讯时：每只股票独立查询，无全量拉取问题
        - 使用 efinance/东财时：预取一次，后续缓存命中
        
        Args:
            stock_codes: 待分析的股票代码列表
            
        Returns:
            预取的股票数量（0 表示跳过预取）
        """
        # Normalize all codes
        stock_codes = [normalize_stock_code(c) for c in stock_codes]

        from src.config import get_config

        config = get_config()

        # Issue #455: PREFETCH_REALTIME_QUOTES=false 可禁用预取，避免全市场拉取
        if not getattr(config, "prefetch_realtime_quotes", True):
            logger.debug("[预取] PREFETCH_REALTIME_QUOTES=false，跳过批量预取")
            return 0

        # 如果实时行情被禁用，跳过预取
        if not config.enable_realtime_quote:
            logger.debug("[预取] 实时行情功能已禁用，跳过预取")
            return 0
        
        # 检查优先级中是否包含全量拉取数据源
        # 注意：新增全量接口（如 tushare_realtime）时需同步更新此列表
        # 全量接口特征：一次 API 调用拉取全市场 5000+ 股票数据
        priority = config.realtime_source_priority.lower()
        bulk_sources = ['efinance', 'akshare_em', 'tushare']  # 全量接口列表
        
        # 如果优先级中前两个都不是全量数据源，跳过预取
        # 因为新浪/腾讯是单股票查询，不需要预取
        priority_list = [s.strip() for s in priority.split(',')]
        first_bulk_source_index = None
        for i, source in enumerate(priority_list):
            if source in bulk_sources:
                first_bulk_source_index = i
                break
        
        # 如果没有全量数据源，或者全量数据源排在第 3 位之后，跳过预取
        if first_bulk_source_index is None or first_bulk_source_index >= 2:
            logger.info(f"[预取] 当前优先级使用轻量级数据源(sina/tencent)，无需预取")
            return 0
        
        # 如果股票数量少于 5 个，不进行批量预取（逐个查询更高效）
        if len(stock_codes) < 5:
            logger.info(f"[预取] 股票数量 {len(stock_codes)} < 5，跳过批量预取")
            return 0
        
        logger.info(f"[预取] 开始批量预取实时行情，共 {len(stock_codes)} 只股票...")
        
        # 尝试通过 efinance 或 akshare 预取
        # 只需要调用一次 get_realtime_quote，缓存机制会自动拉取全市场数据
        try:
            # 用第一只股票触发全量拉取
            first_code = stock_codes[0]
            quote = self.get_realtime_quote(first_code)
            
            if quote:
                logger.info(f"[预取] 批量预取完成，缓存已填充")
                return len(stock_codes)
            else:
                logger.warning(f"[预取] 批量预取失败，将使用逐个查询模式")
                return 0
                
        except Exception as e:
            logger.error(f"[预取] 批量预取异常: {e}")
            return 0
    
    def get_realtime_quote(self, stock_code: str, *, log_final_failure: bool = True):
        """
        获取实时行情数据（自动故障切换）
        
        故障切换策略（按配置的优先级）：
        1. 美股：使用 YfinanceFetcher.get_realtime_quote()
        2. EfinanceFetcher.get_realtime_quote()
        3. AkshareFetcher.get_realtime_quote(source="em")  - 东财
        4. AkshareFetcher.get_realtime_quote(source="sina") - 新浪
        5. AkshareFetcher.get_realtime_quote(source="tencent") - 腾讯
        6. 返回 None（降级兜底）
        
        Args:
            stock_code: 股票代码
            log_final_failure: Whether to emit the final "all sources failed"
                summary log when no realtime quote is available.
            
        Returns:
            UnifiedRealtimeQuote 对象，所有数据源都失败则返回 None
        """
        raw_stock_code = (stock_code or "").strip()
        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        from .akshare_fetcher import _is_us_code
        from .us_index_mapping import is_us_index_code
        from src.config import get_config

        config = get_config()

        # 如果实时行情功能被禁用，直接返回 None
        if not config.enable_realtime_quote:
            logger.debug(f"[实时行情] 功能已禁用，跳过 {stock_code}")
            return None

        # ----------------------------------------------------------
        # 美股 (指数 + 个股) / 港股 — 专用双源路由
        #   配置长桥后: Longbridge 首选, YFinance/AkShare 补充
        #   未配置长桥: YFinance/AkShare 首选, Longbridge 补充
        #   美股指数:   始终 YFinance 首选（Longbridge 不提供指数行情）
        # ----------------------------------------------------------
        is_us_index = is_us_index_code(stock_code)
        is_us = is_us_index or _is_us_code(stock_code)
        is_hk = (not is_us) and _is_hk_market(stock_code)

        if is_us or is_hk:
            prefer_lb = self._longbridge_preferred() and not is_us_index
            if is_us:
                primary_src = "LongbridgeFetcher" if prefer_lb else "YfinanceFetcher"
                secondary_src = "YfinanceFetcher" if prefer_lb else "LongbridgeFetcher"
                market_label = "美股指数" if is_us_index else "美股"
                primary_kw: dict = {}
                secondary_kw: dict = {}
            else:
                primary_src = "LongbridgeFetcher" if prefer_lb else "AkshareFetcher"
                secondary_src = "AkshareFetcher" if prefer_lb else "LongbridgeFetcher"
                market_label = "港股"
                primary_kw = {"source": "hk"} if primary_src == "AkshareFetcher" else {}
                secondary_kw = {"source": "hk"} if secondary_src == "AkshareFetcher" else {}

            primary_quote = self._try_fetcher_quote(stock_code, primary_src, **primary_kw)
            if primary_quote is not None:
                logger.info(f"[实时行情] {market_label} {stock_code} 成功获取 (来源: {primary_src})")
            primary_quote = self._supplement_quote(
                stock_code, primary_quote, secondary_src, **secondary_kw,
            )
            if primary_quote is not None:
                return primary_quote
            if log_final_failure:
                logger.info(f"[实时行情] {market_label} {stock_code} 无可用数据源")
            return None
        
        # 获取配置的数据源优先级
        source_priority = config.realtime_source_priority.split(',')
        
        errors = []
        # primary_quote holds the first successful result; we may supplement
        # missing fields (volume_ratio, turnover_rate, etc.) from later sources.
        primary_quote = None
        
        for source in source_priority:
            source = source.strip().lower()
            
            try:
                quote = None
                
                if source == "efinance":
                    # 尝试 EfinanceFetcher
                    for fetcher in self._get_fetchers_snapshot():
                        if fetcher.name == "EfinanceFetcher":
                            if hasattr(fetcher, 'get_realtime_quote'):
                                quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)
                            break
                
                elif source == "akshare_em":
                    # 尝试 AkshareFetcher 东财数据源
                    for fetcher in self._get_fetchers_snapshot():
                        if fetcher.name == "AkshareFetcher":
                            if hasattr(fetcher, 'get_realtime_quote'):
                                quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="em")
                            break
                
                elif source == "akshare_sina":
                    # 尝试 AkshareFetcher 新浪数据源
                    for fetcher in self._get_fetchers_snapshot():
                        if fetcher.name == "AkshareFetcher":
                            if hasattr(fetcher, 'get_realtime_quote'):
                                quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="sina")
                            break
                
                elif source in ("tencent", "akshare_qq"):
                    # 尝试 AkshareFetcher 腾讯数据源
                    for fetcher in self._get_fetchers_snapshot():
                        if fetcher.name == "AkshareFetcher":
                            if hasattr(fetcher, 'get_realtime_quote'):
                                quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="tencent")
                            break
                
                elif source == "tushare":
                    # 尝试 TushareFetcher（需要 Tushare Pro 积分）
                    for fetcher in self._get_fetchers_snapshot():
                        if fetcher.name == "TushareFetcher":
                            if hasattr(fetcher, 'get_realtime_quote'):
                                quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', raw_stock_code or stock_code)
                            break
                
                if quote is not None and quote.has_basic_data():
                    if primary_quote is None:
                        # First successful source becomes primary
                        primary_quote = quote
                        logger.info(f"[实时行情] {stock_code} 成功获取 (来源: {source})")
                        # If all key supplementary fields are present, return early
                        if not self._quote_needs_supplement(primary_quote):
                            return primary_quote
                        # Otherwise, continue to try later sources for missing fields
                        logger.debug(f"[实时行情] {stock_code} 部分字段缺失，尝试从后续数据源补充")
                        supplement_attempts = 0
                    else:
                        # Supplement missing fields from this source (limit attempts)
                        supplement_attempts += 1
                        if supplement_attempts > 1:
                            logger.debug(f"[实时行情] {stock_code} 补充尝试已达上限，停止继续")
                            break
                        merged = self._merge_quote_fields(primary_quote, quote)
                        if merged:
                            logger.info(f"[实时行情] {stock_code} 从 {source} 补充了缺失字段: {merged}")
                        # Stop supplementing once all key fields are filled
                        if not self._quote_needs_supplement(primary_quote):
                            break
                    
            except Exception as e:
                error_msg = f"[{source}] 失败: {str(e)}"
                logger.info(f"[实时行情] {stock_code} {error_msg}，继续尝试下一个数据源")
                errors.append(error_msg)
                continue
        
        # Return primary even if some fields are still missing
        if primary_quote is not None:
            return primary_quote

        # 所有数据源都失败，返回 None（降级兜底）
        if log_final_failure:
            if errors:
                logger.info(f"[实时行情] {stock_code} 所有数据源均失败: {'; '.join(errors)}")
            else:
                logger.info(f"[实时行情] {stock_code} 无可用数据源")

        return None

    # Fields worth supplementing from secondary sources when the primary
    # source returns None for them. Ordered by importance.
    _SUPPLEMENT_FIELDS = [
        'volume_ratio', 'turnover_rate',
        'pe_ratio', 'pb_ratio', 'total_mv', 'circ_mv',
        'amplitude',
    ]

    @classmethod
    def _quote_needs_supplement(cls, quote) -> bool:
        """Check if any key supplementary field is still None."""
        for f in cls._SUPPLEMENT_FIELDS:
            if getattr(quote, f, None) is None:
                return True
        return False

    @classmethod
    def _merge_quote_fields(cls, primary, secondary) -> list:
        """
        Copy non-None fields from *secondary* into *primary* where
        *primary* has None. Returns list of field names that were filled.
        """
        filled = []
        for f in cls._SUPPLEMENT_FIELDS:
            if getattr(primary, f, None) is None:
                val = getattr(secondary, f, None)
                if val is not None:
                    setattr(primary, f, val)
                    filled.append(f)
        return filled

    def _longbridge_preferred(self) -> bool:
        """Return True when Longbridge keys are configured and available.

        When True, non-A-share routing (US & HK) uses Longbridge as the
        primary data source with Yfinance/AkShare as fallback.
        """
        for f in self._get_fetchers_snapshot():
            if f.name == "LongbridgeFetcher":
                return hasattr(f, '_is_available') and f._is_available()
        return False

    def _try_fetcher_quote(self, stock_code: str, fetcher_name: str, **kw):
        """Try to get a realtime quote from a named fetcher; returns quote or None."""
        for f in self._get_fetchers_snapshot():
            if f.name != fetcher_name:
                continue
            if not hasattr(f, 'get_realtime_quote'):
                return None
            try:
                q = self._call_fetcher_method(f, 'get_realtime_quote', stock_code, **kw)
                if q is not None and q.has_basic_data():
                    return q
            except Exception as e:
                logger.debug(f"[实时行情] {stock_code} {fetcher_name} 获取失败: {e}")
            return None
        return None

    def _supplement_quote(self, stock_code: str, primary_quote, fetcher_name: str, **kw):
        """Supplement *primary_quote* with data from *fetcher_name*.

        If *primary_quote* is None, try *fetcher_name* as the sole source.
        Returns the (potentially enriched) quote, or None.
        """
        if primary_quote is not None:
            if not self._quote_needs_supplement(primary_quote):
                return primary_quote
            try:
                secondary = self._try_fetcher_quote(stock_code, fetcher_name, **kw)
                if secondary is not None:
                    filled = self._merge_quote_fields(primary_quote, secondary)
                    if filled:
                        logger.info(f"[实时行情] {stock_code} 从 {fetcher_name} 补充了: {filled}")
            except Exception as e:
                logger.debug(f"[实时行情] {stock_code} {fetcher_name} 补充失败: {e}")
            return primary_quote

        q = self._try_fetcher_quote(stock_code, fetcher_name, **kw)
        if q is not None:
            logger.info(f"[实时行情] {stock_code} 从 {fetcher_name} 获取成功 (独立数据源)")
        return q

    def _supplement_from_longbridge(self, stock_code: str, primary_quote):
        """Shortcut kept for backward-compat with A-share general loop."""
        return self._supplement_quote(stock_code, primary_quote, "LongbridgeFetcher")

    def get_chip_distribution(self, stock_code: str):
        """
        获取筹码分布数据（带熔断和多数据源降级）

        策略：
        1. 检查配置开关
        2. 检查熔断器状态
        3. 依次尝试多个数据源：数据源优先级与获取daily的数据优先级一致
        4. 所有数据源失败则返回 None（降级兜底）

        Args:
            stock_code: 股票代码

        Returns:
            ChipDistribution 对象，失败则返回 None
        """
        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        from .realtime_types import get_chip_circuit_breaker
        from src.config import get_config

        config = get_config()

        # 如果筹码分布功能被禁用，直接返回 None
        if not config.enable_chip_distribution:
            logger.debug(f"[筹码分布] 功能已禁用，跳过 {stock_code}")
            return None

        circuit_breaker = get_chip_circuit_breaker()

        # 直接遍历管理器已经按 priority 排好序的数据源列表
        for fetcher in self._get_fetchers_snapshot():
            # 只处理实现了筹码分布逻辑的数据源
            if not hasattr(fetcher, 'get_chip_distribution'):
                continue
            
            fetcher_name = fetcher.name
            # 动态生成熔断器的 key，例如 "TushareFetcher" -> "tushare_chip"
            source_key = f"{fetcher_name.replace('Fetcher', '').lower()}_chip"

            # 检查熔断器状态
            if not circuit_breaker.is_available(source_key):
                logger.debug(f"[熔断] {fetcher_name} 筹码接口处于熔断状态，尝试下一个")
                continue

            try:
                chip = self._call_fetcher_method(fetcher, 'get_chip_distribution', stock_code)
                if chip is not None:
                    circuit_breaker.record_success(source_key)
                    logger.info(f"[筹码分布] {stock_code} 成功获取 (来源: {fetcher_name})")
                    return chip
                else:
                    # 空结果：释放 HALF_OPEN 探测名额，避免卡死
                    circuit_breaker.record_inconclusive(source_key)
            except Exception as e:
                logger.warning(f"[筹码分布] {fetcher_name} 获取 {stock_code} 失败: {e}")
                circuit_breaker.record_failure(source_key, str(e))
                continue

        logger.warning(f"[筹码分布] {stock_code} 所有数据源均失败")
        return None

    def get_stock_name(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        """
        获取股票中文名称（自动切换数据源）
        
        尝试从多个数据源获取股票名称：
        1. 先从内存缓存中获取（如果有）
        2. 再尝试本地维护映射与 stocks.index.json 索引
        3. 然后按需查询实时行情
        4. 依次尝试各个数据源的 get_stock_name 方法
        
        Args:
            stock_code: 股票代码
            allow_realtime: Whether to query realtime quote first. Set False when
                caller only wants lightweight prefetch without triggering heavy
                realtime source calls.
            
        Returns:
            股票中文名称，所有数据源都失败则返回 None
        """
        raw_stock_code = (stock_code or "").strip()
        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)
        static_name = STOCK_NAME_MAP.get(stock_code)

        # 1. 先检查缓存
        cached_name = self._get_cached_stock_name(stock_code)
        if cached_name is not None:
            return cached_name
        
        if is_meaningful_stock_name(static_name, stock_code):
            return self._cache_stock_name(stock_code, static_name) or static_name

        index_name = get_index_stock_name(stock_code)
        if is_meaningful_stock_name(index_name, stock_code):
            return self._cache_stock_name(stock_code, index_name) or index_name

        # 2. 尝试从实时行情中获取（最快，可按需禁用）
        if allow_realtime:
            quote = self.get_realtime_quote(raw_stock_code or stock_code, log_final_failure=False)
            if quote and hasattr(quote, 'name') and is_meaningful_stock_name(getattr(quote, 'name', ''), stock_code):
                name = quote.name
                self._cache_stock_name(stock_code, name)
                logger.info(f"[股票名称] 从实时行情获取: {stock_code} -> {name}")
                return name

        # 3. 依次尝试各个数据源
        from .akshare_fetcher import _is_us_code
        is_us = _is_us_code(stock_code)
        _US_CAPABLE_FETCHERS = {"YfinanceFetcher", "LongbridgeFetcher"}
        for fetcher in self._get_fetchers_snapshot():
            if not hasattr(fetcher, 'get_stock_name'):
                continue
            if is_us and fetcher.name not in _US_CAPABLE_FETCHERS:
                continue
            try:
                name = self._call_fetcher_method(fetcher, 'get_stock_name', stock_code)
                if is_meaningful_stock_name(name, stock_code):
                    self._cache_stock_name(stock_code, name)
                    logger.info(f"[股票名称] 从 {fetcher.name} 获取: {stock_code} -> {name}")
                    return name
            except Exception as e:
                logger.debug(f"[股票名称] {fetcher.name} 获取失败: {e}")
                continue

        # 4. 所有数据源都失败
        logger.warning(f"[股票名称] 所有数据源都无法获取 {stock_code} 的名称")
        return ""

    def get_belong_boards(self, stock_code: str) -> List[Dict[str, Any]]:
        """
        Get stock membership boards through capability probing.

        Keep this at manager layer to avoid changing BaseFetcher abstraction.
        """
        stock_code = normalize_stock_code(stock_code)
        if _market_tag(stock_code) != "cn":
            return []
        cached_boards = self._get_cached_belong_boards(stock_code)
        if cached_boards is not None:
            return cached_boards
        for fetcher in self._fetchers:
            if not hasattr(fetcher, "get_belong_board"):
                continue
            try:
                raw_data = fetcher.get_belong_board(stock_code)
                boards = self._normalize_belong_boards(raw_data)
                if boards:
                    self._cache_belong_boards(stock_code, boards)
                    logger.info(f"[{fetcher.name}] 获取所属板块成功: {stock_code}, count={len(boards)}")
                    return boards
            except Exception as e:
                logger.debug(f"[{fetcher.name}] 获取所属板块失败: {e}")
                continue
        return []

    @staticmethod
    def _normalize_board_constituents(
        raw_data: Any,
        *,
        board_name: str,
        board_type: str,
    ) -> pd.DataFrame:
        if raw_data is None:
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])
        if not isinstance(raw_data, pd.DataFrame):
            try:
                raw_data = pd.DataFrame(raw_data)
            except Exception:
                return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])
        if raw_data.empty:
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

        code_col = None
        for candidate in ("代码", "股票代码", "证券代码", "成分代码", "code"):
            if candidate in raw_data.columns:
                code_col = candidate
                break
        name_col = None
        for candidate in ("名称", "股票名称", "证券简称", "成分名称", "name"):
            if candidate in raw_data.columns:
                name_col = candidate
                break
        if code_col is None or name_col is None:
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

        records: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for _, row in raw_data.iterrows():
            code = normalize_stock_code(str(row.get(code_col, "") or "").strip())
            name = str(row.get(name_col, "") or "").strip()
            upper_name = name.upper()
            if (
                not (isinstance(code, str) and code.isdigit() and len(code) == 6)
                or is_bse_code(code)
                or code.startswith("900")
                or any(keyword in upper_name for keyword in ("ETF", "LOF", "基金", "指数", "联接"))
            ):
                continue
            if code in seen:
                continue
            seen.add(code)
            records.append(
                {
                    "code": code,
                    "name": name,
                    "board_name": board_name,
                    "board_type": board_type,
                }
            )
        return pd.DataFrame(records, columns=["code", "name", "board_name", "board_type"])

    def get_board_constituents(
        self,
        board_name: str,
        *,
        board_type: str = "auto",
    ) -> pd.DataFrame:
        """Get normalized A-share constituents for a concept/industry board."""
        normalized_name = str(board_name or "").strip()
        normalized_type = str(board_type or "auto").strip().lower() or "auto"
        if not normalized_name:
            return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

        cache_key = f"{normalized_type}:{normalized_name}"
        cached_df = self._get_cached_board_constituents(cache_key)
        if cached_df is not None:
            return cached_df

        for fetcher in self._get_fetchers_snapshot():
            if not hasattr(fetcher, "get_board_constituents"):
                continue
            try:
                raw_data = self._call_fetcher_method(
                    fetcher,
                    "get_board_constituents",
                    normalized_name,
                    board_type=normalized_type,
                )
                normalized_df = self._normalize_board_constituents(
                    raw_data,
                    board_name=normalized_name,
                    board_type=normalized_type,
                )
                if normalized_df is not None and not normalized_df.empty:
                    self._cache_board_constituents(cache_key, normalized_df)
                    logger.info(
                        "[%s] 获取板块成分股成功: %s (%s), count=%s",
                        fetcher.name,
                        normalized_name,
                        normalized_type,
                        len(normalized_df),
                    )
                    return normalized_df
            except Exception as exc:
                logger.debug(
                    "[%s] 获取板块成分股失败: board=%s, board_type=%s, error=%s",
                    getattr(fetcher, "name", type(fetcher).__name__),
                    normalized_name,
                    normalized_type,
                    exc,
                )
                continue
        return pd.DataFrame(columns=["code", "name", "board_name", "board_type"])

    def prefetch_stock_names(self, stock_codes: List[str], use_bulk: bool = False) -> None:
        """
        Pre-fetch stock names into cache before parallel analysis (Issue #455).

        When use_bulk=False, only calls get_stock_name per code (no get_stock_list),
        avoiding full-market fetch. Sequential execution to avoid rate limits.

        Args:
            stock_codes: Stock codes to prefetch.
            use_bulk: If True, may use get_stock_list (full fetch). Default False.
        """
        if not stock_codes:
            return
        stock_codes = [normalize_stock_code(c) for c in stock_codes]
        if use_bulk:
            self.batch_get_stock_names(stock_codes)
            return
        for code in stock_codes:
            # Skip realtime lookup to avoid triggering expensive full-market quote
            # requests during the prefetch phase.
            self.get_stock_name(code, allow_realtime=False)

    def batch_get_stock_names(self, stock_codes: List[str]) -> Dict[str, str]:
        """
        批量获取股票中文名称
        
        先尝试从支持批量查询的数据源获取股票列表，
        然后再逐个查询缺失的股票名称。
        
        Args:
            stock_codes: 股票代码列表
            
        Returns:
            {股票代码: 股票名称} 字典
        """
        result = {}
        missing_codes = set(stock_codes)
        
        # 1. 先检查缓存
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            for code in stock_codes:
                cached_name = self._stock_name_cache.get(code)
                if cached_name is not None:
                    result[code] = cached_name
                    missing_codes.discard(code)
        
        if not missing_codes:
            return result
        
        # 2. 尝试批量获取股票列表
        for fetcher in self._get_fetchers_snapshot():
            if hasattr(fetcher, 'get_stock_list') and missing_codes:
                try:
                    stock_list = self._call_fetcher_method(fetcher, 'get_stock_list')
                    if stock_list is not None and not stock_list.empty:
                        cache_updates: Dict[str, str] = {}
                        for _, row in stock_list.iterrows():
                            code = row.get('code')
                            name = row.get('name')
                            if code and name:
                                cache_updates[code] = name
                                if code in missing_codes:
                                    result[code] = name
                                    missing_codes.discard(code)

                        if cache_updates:
                            with self._stock_name_cache_lock:
                                self._stock_name_cache.update(cache_updates)
                        
                        if not missing_codes:
                            break
                        
                        logger.info(f"[股票名称] 从 {fetcher.name} 批量获取完成，剩余 {len(missing_codes)} 个待查")
                except Exception as e:
                    logger.debug(f"[股票名称] {fetcher.name} 批量获取失败: {e}")
                    continue
        
        # 3. 逐个获取剩余的
        for code in list(missing_codes):
            name = self.get_stock_name(code)
            if name:
                result[code] = name
                missing_codes.discard(code)
        
        logger.info(f"[股票名称] 批量获取完成，成功 {len(result)}/{len(stock_codes)}")
        return result

    def get_main_indices(self, region: str = "cn") -> List[Dict[str, Any]]:
        """获取主要指数实时行情（自动切换数据源）"""
        if region == "cn":
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                try:
                    data = tickflow_fetcher.get_main_indices(region=region)
                    if data:
                        logger.info("[TickFlowFetcher] 获取指数行情成功")
                        return data
                except Exception as e:
                    logger.warning(f"[TickFlowFetcher] 获取指数行情失败: {e}")

        for fetcher in self._fetchers:
            try:
                data = fetcher.get_main_indices(region=region)
                if data:
                    logger.info(f"[{fetcher.name}] 获取指数行情成功")
                    return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] 获取指数行情失败: {e}")
                continue
        return []

    def get_market_stats(self) -> Dict[str, Any]:
        """获取市场涨跌统计（自动切换数据源）"""
        tickflow_fetcher = self._get_tickflow_fetcher()
        if tickflow_fetcher is not None:
            try:
                data = tickflow_fetcher.get_market_stats()
                if data:
                    logger.info("[TickFlowFetcher] 获取市场统计成功")
                    return data
            except Exception as e:
                logger.warning(f"[TickFlowFetcher] 获取市场统计失败: {e}")

        for fetcher in self._fetchers:
            try:
                data = fetcher.get_market_stats()
                if data:
                    logger.info(f"[{fetcher.name}] 获取市场统计成功")
                    return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] 获取市场统计失败: {e}")
                continue
        return {}

    def _run_with_timeout_with_slots(
        self,
        task: Callable[[], Any],
        timeout_seconds: float,
        task_name: str,
        *,
        slots: BoundedSemaphore,
        worker_name_prefix: str,
    ) -> Tuple[Optional[Any], Optional[str], int]:
        """Execute a task in a short-lived thread with timeout and bounded workers."""
        start = time.time()
        timeout_value = max(0.0, timeout_seconds)
        if timeout_value <= 0:
            return None, f"{task_name} timeout", 0
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}

        if not slots.acquire(blocking=False):
            return None, f"{task_name} timeout worker pool exhausted", int(timeout_value * 1000)

        def runner() -> None:
            try:
                result_holder["value"] = task()
            except Exception as exc:
                error_holder["value"] = exc
            finally:
                try:
                    slots.release()
                except ValueError:
                    pass

        worker = Thread(target=runner, daemon=True, name=f"{worker_name_prefix}-{task_name}")
        try:
            worker.start()
        except Exception as exc:
            try:
                slots.release()
            except ValueError:
                pass
            return None, str(exc), int((time.time() - start) * 1000)
        worker.join(timeout=timeout_value)
        if worker.is_alive():
            return None, f"{task_name} timeout", int(timeout_value * 1000)
        if "value" in error_holder:
            return None, str(error_holder["value"]), int((time.time() - start) * 1000)
        return result_holder.get("value"), None, int((time.time() - start) * 1000)

    def _run_with_timeout(
        self,
        task: Callable[[], Any],
        timeout_seconds: float,
        task_name: str,
    ) -> Tuple[Optional[Any], Optional[str], int]:
        """
        Execute a task in a short-lived thread and enforce a timeout.

        Returns:
            (result, error, duration_ms)
        """
        return self._run_with_timeout_with_slots(
            task,
            timeout_seconds,
            task_name,
            slots=self._fundamental_timeout_slots,
            worker_name_prefix="fundamental",
        )

    def _run_with_retry(
        self,
        task: Callable[[], Any],
        timeout_seconds: float,
        task_name: str,
    ) -> Tuple[Optional[Any], Optional[str], int]:
        """
        Execute a task with bounded budget and best-effort retries.

        Returns:
            (result, error, total_duration_ms)
        """
        config = self._get_fundamental_config()
        attempts = max(1, int(config.fundamental_retry_max))
        remaining_seconds = max(0.0, float(timeout_seconds))
        total_cost_ms = 0
        last_error: Optional[str] = None

        for _ in range(attempts):
            if remaining_seconds <= 0:
                break
            result, err, cost_ms = self._run_with_timeout(task, remaining_seconds, task_name)
            total_cost_ms += cost_ms
            remaining_seconds = max(0.0, remaining_seconds - cost_ms / 1000)
            if err is None:
                return result, None, total_cost_ms
            last_error = err
            if remaining_seconds <= 0:
                break

        return None, last_error, total_cost_ms

    def _get_fundamental_config(self):
        from src.config import get_config
        return get_config()

    @staticmethod
    def _normalize_source_chain(
        entries: Any,
        provider: str,
        result: str,
        duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """Normalize free-form source chain entries to structured dict list."""
        if entries is None:
            return [{"provider": provider, "result": result, "duration_ms": duration_ms}]

        normalized: List[Dict[str, Any]] = []
        if not isinstance(entries, (list, tuple)):
            entries = [entries]

        for item in entries:
            if isinstance(item, dict):
                normalized.append({
                    "provider": str(item.get("provider") or provider),
                    "result": str(item.get("result") or result),
                    "duration_ms": int(item.get("duration_ms", duration_ms)),
                })
                continue

            if item is None:
                continue

            provider_name = str(item)
            normalized.append({
                "provider": provider_name,
                "result": result,
                "duration_ms": duration_ms,
            })

        if not normalized:
            return [{"provider": provider, "result": result, "duration_ms": duration_ms}]

        return normalized

    @staticmethod
    def _block_status(payload: Dict[str, Any], available: bool = True) -> str:
        if not available:
            return "not_supported"
        if not payload:
            return "partial"
        return "ok"

    @staticmethod
    def _build_fundamental_block(
        status: str,
        payload: Optional[Dict[str, Any]] = None,
        source_chain: Optional[List[Dict[str, Any]]] = None,
        errors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "coverage": {"status": status},
            "source_chain": source_chain or [],
            "errors": errors or [],
            "data": payload or {},
        }

    @staticmethod
    def _has_meaningful_payload(payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, str):
            normalized = payload.strip().lower()
            return normalized not in ("", "-", "nan", "none", "null", "n/a", "na")
        if isinstance(payload, dict):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload.values())
        if isinstance(payload, pd.DataFrame):
            if payload.empty:
                return False
            return any(
                DataFetcherManager._has_meaningful_payload(v)
                for v in payload.to_numpy().flat
            )
        if isinstance(payload, (pd.Series, pd.Index)):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload.tolist())
        if isinstance(payload, np.ndarray):
            if payload.ndim == 0:
                payload = payload.item()
            else:
                return any(
                    DataFetcherManager._has_meaningful_payload(v)
                    for v in payload.flat
                )
        if isinstance(payload, (list, tuple, set)):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload)
        if DataFetcherManager._try_scalar_isna(payload, "fundamental_payload") is True:
            return False
        return True

    @staticmethod
    def _infer_block_status(payload: Any, fallback_status: str) -> str:
        if DataFetcherManager._has_meaningful_payload(payload):
            return "ok"
        if fallback_status in ("failed", "partial", "not_supported"):
            return fallback_status
        return "partial"

    @classmethod
    def _has_meaningful_earnings_quality_payload(cls, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        verdict = str(payload.get("verdict", "")).strip().lower()
        if verdict and verdict != "unavailable":
            return True
        # "unavailable" with no score means no earnings-quality evidence;
        # ignore placeholder metrics from the default payload.
        if cls._safe_float(payload.get("score_total")) is not None:
            return True
        for key in ("positive_signals", "risk_flags"):
            if cls._has_meaningful_payload(payload.get(key)):
                return True
        return False

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                normalized = value.strip().replace(",", "").replace("%", "")
                if not normalized:
                    return None
                return float(normalized)
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_text_value(value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split()).strip()

    @classmethod
    def _normalize_earnings_quality_series(
        cls,
        growth_data: Dict[str, Any],
        earnings_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        candidate_series = earnings_data.get("financial_report_series")
        if not isinstance(candidate_series, list):
            candidate_series = growth_data.get("quarterly_series")
        if not isinstance(candidate_series, list):
            return []

        normalized_series: List[Dict[str, Any]] = []
        seen_report_dates = set()

        for item in candidate_series:
            if not isinstance(item, dict):
                continue
            report_date = cls._normalize_text_value(item.get("report_date")) or None
            normalized_item = {
                "report_date": report_date,
                "revenue_yoy": cls._safe_float(item.get("revenue_yoy")),
                "net_profit_yoy": cls._safe_float(item.get("net_profit_yoy")),
                "roe": cls._safe_float(item.get("roe")),
                "gross_margin": cls._safe_float(item.get("gross_margin")),
                "revenue": cls._safe_float(item.get("revenue")),
                "net_profit_parent": cls._safe_float(item.get("net_profit_parent")),
                "operating_cash_flow": cls._safe_float(item.get("operating_cash_flow")),
                "accounts_receivable": cls._safe_float(item.get("accounts_receivable")),
                "inventory": cls._safe_float(item.get("inventory")),
                "contract_liabilities": cls._safe_float(item.get("contract_liabilities")),
                "selling_expense_rate": cls._safe_float(item.get("selling_expense_rate")),
                "management_expense_rate": cls._safe_float(item.get("management_expense_rate")),
                "r_and_d_expense_rate": cls._safe_float(item.get("r_and_d_expense_rate")),
                "net_margin": cls._safe_float(item.get("net_margin")),
                "operating_margin": cls._safe_float(item.get("operating_margin")),
            }
            if not any(value is not None for key, value in normalized_item.items() if key != "report_date"):
                continue
            if report_date:
                if report_date in seen_report_dates:
                    continue
                seen_report_dates.add(report_date)
            normalized_series.append({key: value for key, value in normalized_item.items() if value is not None})

        normalized_series.sort(key=lambda item: item.get("report_date") or "", reverse=True)
        return normalized_series

    @staticmethod
    def _parse_report_date(value: Any) -> Optional[datetime]:
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        try:
            parsed = pd.to_datetime(text, errors="coerce")
        except Exception:
            return None
        if pd.isna(parsed):
            return None
        try:
            return parsed.to_pydatetime()
        except Exception:
            return None

    @staticmethod
    def _report_quarter_from_datetime(value: Optional[datetime]) -> Optional[int]:
        if value is None:
            return None
        return ((int(value.month) - 1) // 3) + 1

    @staticmethod
    def _calc_pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
        if current is None or previous is None or abs(previous) <= 1e-9:
            return None
        return round((current - previous) / abs(previous) * 100.0, 4)

    @classmethod
    def _infer_numeric_trend(cls, values: List[float], lower_is_better: bool = False) -> str:
        if len(values) < 2:
            return "insufficient_history"

        work_values = [(-value if lower_is_better else value) for value in values]
        has_up = any(work_values[idx] > work_values[idx + 1] for idx in range(len(work_values) - 1))
        has_down = any(work_values[idx] < work_values[idx + 1] for idx in range(len(work_values) - 1))
        if has_up and not has_down:
            return "improving"
        if has_down and not has_up:
            return "deteriorating"
        if not has_up and not has_down:
            return "flat"
        return "mixed"

    @classmethod
    def _build_derived_financial_series(cls, quarterly_series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not quarterly_series:
            return []

        ascending_series: List[Dict[str, Any]] = []
        for item in quarterly_series:
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            normalized_item["_report_dt"] = cls._parse_report_date(item.get("report_date"))
            ascending_series.append(normalized_item)

        ascending_series.sort(key=lambda item: item.get("_report_dt") or datetime.min)
        by_year_quarter: Dict[Tuple[int, int], Dict[str, Any]] = {}

        for item in ascending_series:
            report_dt = item.get("_report_dt")
            fiscal_quarter = cls._report_quarter_from_datetime(report_dt)
            fiscal_year = report_dt.year if report_dt is not None else None
            item["fiscal_year"] = fiscal_year
            item["fiscal_quarter"] = fiscal_quarter
            if fiscal_year is not None and fiscal_quarter is not None:
                by_year_quarter[(fiscal_year, fiscal_quarter)] = item

            for source_key, derived_key in (
                ("revenue", "single_quarter_revenue"),
                ("net_profit_parent", "single_quarter_net_profit_parent"),
                ("operating_cash_flow", "single_quarter_operating_cash_flow"),
            ):
                current_value = cls._safe_float(item.get(source_key))
                single_value = None
                if current_value is not None and fiscal_quarter is not None:
                    if fiscal_quarter == 1:
                        single_value = current_value
                    else:
                        previous_item = by_year_quarter.get((fiscal_year, fiscal_quarter - 1))
                        previous_value = (
                            cls._safe_float(previous_item.get(source_key))
                            if isinstance(previous_item, dict)
                            else None
                        )
                        if previous_value is not None:
                            single_value = current_value - previous_value
                if single_value is not None:
                    item[derived_key] = round(single_value, 4)

            if fiscal_year is not None and fiscal_quarter is not None:
                previous_year_item = by_year_quarter.get((fiscal_year - 1, fiscal_quarter))
                for metric_key, yoy_key in (
                    ("single_quarter_revenue", "single_quarter_revenue_yoy"),
                    ("single_quarter_net_profit_parent", "single_quarter_net_profit_parent_yoy"),
                    ("single_quarter_operating_cash_flow", "single_quarter_operating_cash_flow_yoy"),
                ):
                    current_value = cls._safe_float(item.get(metric_key))
                    previous_value = (
                        cls._safe_float(previous_year_item.get(metric_key))
                        if isinstance(previous_year_item, dict)
                        else None
                    )
                    yoy_value = cls._calc_pct_change(current_value, previous_value)
                    if yoy_value is not None:
                        item[yoy_key] = yoy_value

            revenue = cls._safe_float(item.get("revenue"))
            accounts_receivable = cls._safe_float(item.get("accounts_receivable"))
            inventory = cls._safe_float(item.get("inventory"))
            contract_liabilities = cls._safe_float(item.get("contract_liabilities"))
            if revenue is not None and abs(revenue) > 1e-9:
                if accounts_receivable is not None:
                    item["accounts_receivable_to_revenue"] = round(accounts_receivable / revenue, 4)
                if inventory is not None:
                    item["inventory_to_revenue"] = round(inventory / revenue, 4)
                if contract_liabilities is not None:
                    item["contract_liabilities_to_revenue"] = round(contract_liabilities / revenue, 4)

            expense_rates = [
                cls._safe_float(item.get("selling_expense_rate")),
                cls._safe_float(item.get("management_expense_rate")),
                cls._safe_float(item.get("r_and_d_expense_rate")),
            ]
            expense_rates = [value for value in expense_rates if value is not None]
            if expense_rates:
                item["total_core_expense_rate"] = round(sum(expense_rates), 4)

        descending_series = sorted(
            ascending_series,
            key=lambda item: item.get("_report_dt") or datetime.min,
            reverse=True,
        )
        cleaned_series: List[Dict[str, Any]] = []
        for item in descending_series:
            cleaned_series.append({key: value for key, value in item.items() if key != "_report_dt"})
        return cleaned_series

    @classmethod
    def _build_ttm_snapshot(cls, derived_series: List[Dict[str, Any]]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        for metric_key, prefix in (
            ("single_quarter_revenue", "revenue"),
            ("single_quarter_net_profit_parent", "net_profit_parent"),
            ("single_quarter_operating_cash_flow", "operating_cash_flow"),
        ):
            values = [cls._safe_float(item.get(metric_key)) for item in derived_series[:8] if isinstance(item, dict)]
            latest_ttm = None
            previous_ttm = None
            if len(values) >= 4 and all(value is not None for value in values[:4]):
                latest_ttm = round(sum(float(value) for value in values[:4] if value is not None), 4)
                snapshot[f"{prefix}_ttm"] = latest_ttm
            if len(values) >= 8 and all(value is not None for value in values[4:8]):
                previous_ttm = round(sum(float(value) for value in values[4:8] if value is not None), 4)
                snapshot[f"{prefix}_ttm_previous"] = previous_ttm
            yoy_pct = cls._calc_pct_change(latest_ttm, previous_ttm)
            if yoy_pct is not None:
                snapshot[f"{prefix}_ttm_yoy"] = yoy_pct
        return snapshot

    @classmethod
    def _build_qoq_snapshot(cls, derived_series: List[Dict[str, Any]]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        latest_item = derived_series[0] if derived_series else {}
        previous_item = derived_series[1] if len(derived_series) > 1 else {}
        if not isinstance(latest_item, dict):
            latest_item = {}
        if not isinstance(previous_item, dict):
            previous_item = {}

        for metric_key, prefix in (
            ("single_quarter_revenue", "revenue"),
            ("single_quarter_net_profit_parent", "net_profit_parent"),
            ("single_quarter_operating_cash_flow", "operating_cash_flow"),
        ):
            latest_value = cls._safe_float(latest_item.get(metric_key))
            previous_value = cls._safe_float(previous_item.get(metric_key))
            if latest_value is not None:
                snapshot[f"latest_{prefix}"] = latest_value
            qoq_pct = cls._calc_pct_change(latest_value, previous_value)
            if qoq_pct is not None:
                snapshot[f"{prefix}_qoq"] = qoq_pct
        return snapshot

    @classmethod
    def _build_single_quarter_growth_snapshot(cls, derived_series: List[Dict[str, Any]]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        latest_item = derived_series[0] if derived_series else {}
        if not isinstance(latest_item, dict):
            latest_item = {}

        for metric_key, prefix in (
            ("single_quarter_revenue", "revenue"),
            ("single_quarter_net_profit_parent", "net_profit_parent"),
            ("single_quarter_operating_cash_flow", "operating_cash_flow"),
        ):
            latest_value = cls._safe_float(latest_item.get(metric_key))
            latest_yoy = cls._safe_float(latest_item.get(f"{metric_key}_yoy"))
            if latest_value is not None:
                snapshot[f"latest_{prefix}"] = latest_value
            if latest_yoy is not None:
                snapshot[f"latest_{prefix}_yoy"] = latest_yoy

            yoy_trend = cls._infer_series_trend(derived_series, f"{metric_key}_yoy")
            if yoy_trend != "insufficient_history":
                snapshot[f"{prefix}_yoy_trend"] = yoy_trend
        return snapshot

    @classmethod
    def _infer_series_trend(
        cls,
        derived_series: List[Dict[str, Any]],
        key: str,
        *,
        lower_is_better: bool = False,
        max_points: int = 3,
    ) -> str:
        values: List[float] = []
        for item in derived_series:
            if not isinstance(item, dict):
                continue
            value = cls._safe_float(item.get(key))
            if value is None:
                continue
            values.append(value)
            if len(values) >= max(2, max_points):
                break
        return cls._infer_numeric_trend(values, lower_is_better=lower_is_better)

    @classmethod
    def _build_margin_and_working_capital_snapshot(cls, derived_series: List[Dict[str, Any]]) -> Dict[str, Any]:
        latest_item = derived_series[0] if derived_series else {}
        if not isinstance(latest_item, dict):
            latest_item = {}
        return {
            "latest_accounts_receivable_to_revenue": cls._safe_float(latest_item.get("accounts_receivable_to_revenue")),
            "latest_inventory_to_revenue": cls._safe_float(latest_item.get("inventory_to_revenue")),
            "latest_contract_liabilities_to_revenue": cls._safe_float(latest_item.get("contract_liabilities_to_revenue")),
            "latest_total_core_expense_rate": cls._safe_float(latest_item.get("total_core_expense_rate")),
            "latest_net_margin": cls._safe_float(latest_item.get("net_margin")),
            "latest_operating_margin": cls._safe_float(latest_item.get("operating_margin")),
            "accounts_receivable_to_revenue_trend": cls._infer_series_trend(
                derived_series, "accounts_receivable_to_revenue", lower_is_better=True
            ),
            "inventory_to_revenue_trend": cls._infer_series_trend(
                derived_series, "inventory_to_revenue", lower_is_better=True
            ),
            "total_core_expense_rate_trend": cls._infer_series_trend(
                derived_series, "total_core_expense_rate", lower_is_better=True
            ),
            "net_margin_trend": cls._infer_series_trend(derived_series, "net_margin"),
            "gross_margin_trend": cls._infer_series_trend(derived_series, "gross_margin"),
        }

    @classmethod
    def _count_positive_streak(cls, quarterly_series: List[Dict[str, Any]], key: str) -> int:
        streak = 0
        for item in quarterly_series:
            value = cls._safe_float(item.get(key)) if isinstance(item, dict) else None
            if value is None or value <= 0:
                break
            streak += 1
        return streak

    @classmethod
    def _count_dual_positive_streak(cls, quarterly_series: List[Dict[str, Any]]) -> int:
        streak = 0
        for item in quarterly_series:
            if not isinstance(item, dict):
                break
            revenue_yoy = cls._safe_float(item.get("revenue_yoy"))
            net_profit_yoy = cls._safe_float(item.get("net_profit_yoy"))
            if revenue_yoy is None or net_profit_yoy is None or revenue_yoy <= 0 or net_profit_yoy <= 0:
                break
            streak += 1
        return streak

    @classmethod
    def _build_quarterly_earnings_evidence(
        cls,
        quarterly_series: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        derived_series = cls._build_derived_financial_series(quarterly_series)
        latest_quarters: List[Dict[str, Any]] = []
        revenue_values: List[float] = []
        profit_values: List[float] = []

        for item in derived_series[:4]:
            if not isinstance(item, dict):
                continue
            quarter_snapshot: Dict[str, Any] = {}
            report_date = cls._normalize_text_value(item.get("report_date")) or None
            if report_date:
                quarter_snapshot["report_date"] = report_date

            revenue_yoy = cls._safe_float(item.get("revenue_yoy"))
            if revenue_yoy is not None:
                quarter_snapshot["revenue_yoy"] = revenue_yoy
                revenue_values.append(revenue_yoy)

            net_profit_yoy = cls._safe_float(item.get("net_profit_yoy"))
            if net_profit_yoy is not None:
                quarter_snapshot["net_profit_yoy"] = net_profit_yoy
                profit_values.append(net_profit_yoy)

            if quarter_snapshot:
                latest_quarters.append(quarter_snapshot)

        observation_count = len(derived_series)
        revenue_positive_streak = cls._count_positive_streak(derived_series, "revenue_yoy")
        profit_positive_streak = cls._count_positive_streak(derived_series, "net_profit_yoy")
        dual_positive_streak = cls._count_dual_positive_streak(derived_series)
        revenue_positive_quarters = sum(
            1
            for item in derived_series
            if isinstance(item, dict) and (cls._safe_float(item.get("revenue_yoy")) or 0.0) > 0
        )
        profit_positive_quarters = sum(
            1
            for item in derived_series
            if isinstance(item, dict) and (cls._safe_float(item.get("net_profit_yoy")) or 0.0) > 0
        )

        revenue_trend = cls._infer_numeric_trend(revenue_values[:3])
        profit_trend = cls._infer_numeric_trend(profit_values[:3])
        if observation_count < 2:
            latest_trend = "insufficient_history"
        elif dual_positive_streak >= min(3, observation_count):
            if "improving" in (revenue_trend, profit_trend):
                latest_trend = "improving"
            else:
                latest_trend = "stable_positive"
        elif revenue_trend == "deteriorating" and profit_trend == "deteriorating":
            latest_trend = "deteriorating"
        elif dual_positive_streak >= 2:
            latest_trend = "stable_positive"
        elif "improving" in (revenue_trend, profit_trend):
            latest_trend = "recovering"
        elif "deteriorating" in (revenue_trend, profit_trend):
            latest_trend = "deteriorating"
        else:
            latest_trend = "mixed"

        ttm_snapshot = cls._build_ttm_snapshot(derived_series)
        qoq_snapshot = cls._build_qoq_snapshot(derived_series)
        single_quarter_growth_snapshot = cls._build_single_quarter_growth_snapshot(derived_series)
        margin_quality_snapshot = cls._build_margin_and_working_capital_snapshot(derived_series)
        return {
            "observation_count": observation_count,
            "observed_report_dates": [
                item.get("report_date")
                for item in derived_series[:4]
                if isinstance(item, dict) and item.get("report_date")
            ],
            "revenue_positive_quarters": revenue_positive_quarters,
            "profit_positive_quarters": profit_positive_quarters,
            "revenue_positive_streak": revenue_positive_streak,
            "profit_positive_streak": profit_positive_streak,
            "dual_positive_streak": dual_positive_streak,
            "revenue_trend": revenue_trend,
            "profit_trend": profit_trend,
            "latest_trend": latest_trend,
            "latest_quarters": latest_quarters,
            "latest_single_quarters": [
                {
                    key: item[key]
                    for key in (
                        "report_date",
                        "single_quarter_revenue",
                        "single_quarter_net_profit_parent",
                        "single_quarter_operating_cash_flow",
                        "single_quarter_revenue_yoy",
                        "single_quarter_net_profit_parent_yoy",
                        "single_quarter_operating_cash_flow_yoy",
                    )
                    if key in item
                }
                for item in derived_series[:4]
                if isinstance(item, dict)
            ],
            "ttm_snapshot": ttm_snapshot,
            "qoq_snapshot": qoq_snapshot,
            "single_quarter_growth_snapshot": single_quarter_growth_snapshot,
            "margin_quality_snapshot": margin_quality_snapshot,
        }

    @classmethod
    def _build_growth_cycle_analysis(cls, quarterly_evidence: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(quarterly_evidence, dict):
            return {
                "phase": "unavailable",
                "confidence": "low",
                "score": 0,
                "signals": [],
                "drivers": {},
                "limitations": ["quarterly_evidence_unavailable"],
            }

        ttm_snapshot = quarterly_evidence.get("ttm_snapshot")
        if not isinstance(ttm_snapshot, dict):
            ttm_snapshot = {}
        single_quarter_snapshot = quarterly_evidence.get("single_quarter_growth_snapshot")
        if not isinstance(single_quarter_snapshot, dict):
            single_quarter_snapshot = {}
        margin_snapshot = quarterly_evidence.get("margin_quality_snapshot")
        if not isinstance(margin_snapshot, dict):
            margin_snapshot = {}

        observation_count = int(quarterly_evidence.get("observation_count") or 0)
        latest_trend = str(quarterly_evidence.get("latest_trend") or "").strip().lower() or None
        dual_positive_streak = int(quarterly_evidence.get("dual_positive_streak") or 0)
        revenue_ttm_yoy = cls._safe_float(ttm_snapshot.get("revenue_ttm_yoy"))
        net_profit_ttm_yoy = cls._safe_float(ttm_snapshot.get("net_profit_parent_ttm_yoy"))
        operating_cash_flow_ttm_yoy = cls._safe_float(ttm_snapshot.get("operating_cash_flow_ttm_yoy"))
        latest_single_quarter_revenue_yoy = cls._safe_float(single_quarter_snapshot.get("latest_revenue_yoy"))
        latest_single_quarter_net_profit_yoy = cls._safe_float(single_quarter_snapshot.get("latest_net_profit_parent_yoy"))
        latest_single_quarter_operating_cash_flow_yoy = cls._safe_float(
            single_quarter_snapshot.get("latest_operating_cash_flow_yoy")
        )
        gross_margin_trend = str(margin_snapshot.get("gross_margin_trend") or "").strip().lower() or None
        net_margin_trend = str(margin_snapshot.get("net_margin_trend") or "").strip().lower() or None

        limitations: List[str] = []
        if observation_count < 2:
            limitations.append("quarterly_series_history_short_for_cycle_analysis")
        if revenue_ttm_yoy is None and net_profit_ttm_yoy is None:
            limitations.append("ttm_growth_unavailable_for_cycle_analysis")
        if latest_single_quarter_revenue_yoy is None and latest_single_quarter_net_profit_yoy is None:
            limitations.append("single_quarter_yoy_unavailable_for_cycle_analysis")

        score = 0
        phase_signals: List[str] = []

        if revenue_ttm_yoy is not None:
            if revenue_ttm_yoy >= 20:
                score += 2
                phase_signals.append("revenue_ttm_yoy_strong")
            elif revenue_ttm_yoy > 0:
                score += 1
                phase_signals.append("revenue_ttm_yoy_positive")
            else:
                score -= 2
                phase_signals.append("revenue_ttm_yoy_non_positive")

        if net_profit_ttm_yoy is not None:
            if net_profit_ttm_yoy >= 25:
                score += 3
                phase_signals.append("profit_ttm_yoy_strong")
            elif net_profit_ttm_yoy > 0:
                score += 1
                phase_signals.append("profit_ttm_yoy_positive")
            else:
                score -= 3
                phase_signals.append("profit_ttm_yoy_non_positive")

        if operating_cash_flow_ttm_yoy is not None:
            if operating_cash_flow_ttm_yoy >= 15:
                score += 1
                phase_signals.append("cashflow_ttm_yoy_positive")
            elif operating_cash_flow_ttm_yoy < 0:
                score -= 1
                phase_signals.append("cashflow_ttm_yoy_non_positive")

        if latest_single_quarter_revenue_yoy is not None:
            if latest_single_quarter_revenue_yoy >= 15:
                score += 2
                phase_signals.append("latest_single_quarter_revenue_yoy_strong")
            elif latest_single_quarter_revenue_yoy > 0:
                score += 1
                phase_signals.append("latest_single_quarter_revenue_yoy_positive")
            else:
                score -= 2
                phase_signals.append("latest_single_quarter_revenue_yoy_non_positive")

        if latest_single_quarter_net_profit_yoy is not None:
            if latest_single_quarter_net_profit_yoy >= 20:
                score += 3
                phase_signals.append("latest_single_quarter_profit_yoy_strong")
            elif latest_single_quarter_net_profit_yoy > 0:
                score += 1
                phase_signals.append("latest_single_quarter_profit_yoy_positive")
            else:
                score -= 3
                phase_signals.append("latest_single_quarter_profit_yoy_non_positive")

        if latest_single_quarter_operating_cash_flow_yoy is not None:
            if latest_single_quarter_operating_cash_flow_yoy >= 10:
                score += 1
                phase_signals.append("latest_single_quarter_cashflow_yoy_positive")
            elif latest_single_quarter_operating_cash_flow_yoy < 0:
                score -= 1
                phase_signals.append("latest_single_quarter_cashflow_yoy_non_positive")

        if latest_trend == "improving":
            score += 2
            phase_signals.append("quarterly_growth_trend_improving")
        elif latest_trend == "stable_positive":
            score += 1
            phase_signals.append("quarterly_growth_trend_stable_positive")
        elif latest_trend == "recovering":
            score += 1
            phase_signals.append("quarterly_growth_trend_recovering")
        elif latest_trend == "deteriorating":
            score -= 2
            phase_signals.append("quarterly_growth_trend_deteriorating")

        if dual_positive_streak >= 4:
            score += 1
            phase_signals.append("dual_growth_streak_4q")
        elif dual_positive_streak >= 2:
            score += 1
            phase_signals.append("dual_growth_streak_active")
        elif observation_count >= 2 and dual_positive_streak == 0:
            score -= 1
            phase_signals.append("dual_growth_streak_missing")

        if gross_margin_trend == "improving":
            score += 1
            phase_signals.append("gross_margin_trend_improving")
        elif gross_margin_trend == "deteriorating":
            score -= 1
            phase_signals.append("gross_margin_trend_deteriorating")

        if net_margin_trend == "improving":
            score += 1
            phase_signals.append("net_margin_trend_improving")
        elif net_margin_trend == "deteriorating":
            score -= 1
            phase_signals.append("net_margin_trend_deteriorating")

        if observation_count < 2:
            phase = "unavailable"
        elif (
            score <= -5
            and latest_trend in {"deteriorating", "mixed", "flat", None}
            and (
                (net_profit_ttm_yoy is not None and net_profit_ttm_yoy < 0)
                or (
                    latest_single_quarter_net_profit_yoy is not None
                    and latest_single_quarter_net_profit_yoy < 0
                )
            )
        ):
            phase = "downcycle"
        elif (
            score >= 7
            and latest_trend == "improving"
            and (
                (latest_single_quarter_net_profit_yoy is not None and latest_single_quarter_net_profit_yoy >= 20)
                or (net_profit_ttm_yoy is not None and net_profit_ttm_yoy >= 25)
            )
        ):
            phase = "reaccelerating"
        elif (
            score >= 5
            and dual_positive_streak >= 3
            and latest_trend in {"improving", "stable_positive"}
        ):
            phase = "expanding"
        elif (
            score >= 2
            and latest_trend in {"recovering", "mixed"}
            and (
                (latest_single_quarter_revenue_yoy is not None and latest_single_quarter_revenue_yoy > 0)
                or (
                    latest_single_quarter_net_profit_yoy is not None
                    and latest_single_quarter_net_profit_yoy > 0
                )
            )
        ):
            phase = "recovering"
        elif score >= 3 and latest_trend == "stable_positive":
            phase = "mature"
        else:
            phase = "mixed"

        evidence_count = sum(
            value is not None
            for value in (
                revenue_ttm_yoy,
                net_profit_ttm_yoy,
                latest_single_quarter_revenue_yoy,
                latest_single_quarter_net_profit_yoy,
            )
        )
        if observation_count >= 4 and evidence_count >= 2 and abs(score) >= 5:
            confidence = "high"
        elif observation_count >= 3 and evidence_count >= 1 and abs(score) >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        if phase not in {"unavailable", "mixed"}:
            phase_signals.append(f"cycle_phase_{phase}")

        drivers = {
            "latest_trend": latest_trend,
            "dual_positive_streak": dual_positive_streak if observation_count > 0 else None,
            "revenue_ttm_yoy": revenue_ttm_yoy,
            "net_profit_ttm_yoy": net_profit_ttm_yoy,
            "operating_cash_flow_ttm_yoy": operating_cash_flow_ttm_yoy,
            "latest_single_quarter_revenue_yoy": latest_single_quarter_revenue_yoy,
            "latest_single_quarter_net_profit_yoy": latest_single_quarter_net_profit_yoy,
            "latest_single_quarter_operating_cash_flow_yoy": latest_single_quarter_operating_cash_flow_yoy,
            "gross_margin_trend": gross_margin_trend,
            "net_margin_trend": net_margin_trend,
        }
        drivers = {key: value for key, value in drivers.items() if value is not None}

        return {
            "phase": phase,
            "confidence": confidence,
            "score": score,
            "signals": sorted(set(phase_signals)),
            "drivers": drivers,
            "limitations": sorted(set(limitations)),
        }

    @staticmethod
    def _pick_earnings_quality_verdict(score_total: Optional[float], has_evidence: bool) -> str:
        if not has_evidence or score_total is None:
            return "unavailable"
        if score_total >= 80:
            return "strong"
        if score_total >= 65:
            return "good"
        if score_total >= 45:
            return "mixed"
        return "weak"

    @classmethod
    def _build_earnings_quality_payload(
        cls,
        growth_payload: Any,
        earnings_payload: Any,
    ) -> Dict[str, Any]:
        growth_data = dict(growth_payload) if isinstance(growth_payload, dict) else {}
        earnings_data = dict(earnings_payload) if isinstance(earnings_payload, dict) else {}
        financial_report = earnings_data.get("financial_report")
        if not isinstance(financial_report, dict):
            financial_report = {}

        quarterly_series = cls._normalize_earnings_quality_series(growth_data, earnings_data)
        latest_quarter = quarterly_series[0] if quarterly_series else {}

        revenue_yoy = cls._safe_float(growth_data.get("revenue_yoy"))
        net_profit_yoy = cls._safe_float(growth_data.get("net_profit_yoy"))
        roe = cls._safe_float(growth_data.get("roe"))
        gross_margin = cls._safe_float(growth_data.get("gross_margin"))
        operating_cash_flow = cls._safe_float(financial_report.get("operating_cash_flow"))
        net_profit_parent = cls._safe_float(financial_report.get("net_profit_parent"))
        report_date = financial_report.get("report_date")
        if revenue_yoy is None:
            revenue_yoy = cls._safe_float(latest_quarter.get("revenue_yoy"))
        if net_profit_yoy is None:
            net_profit_yoy = cls._safe_float(latest_quarter.get("net_profit_yoy"))
        if roe is None:
            roe = cls._safe_float(latest_quarter.get("roe"))
        if gross_margin is None:
            gross_margin = cls._safe_float(latest_quarter.get("gross_margin"))
        if operating_cash_flow is None:
            operating_cash_flow = cls._safe_float(latest_quarter.get("operating_cash_flow"))
        if net_profit_parent is None:
            net_profit_parent = cls._safe_float(latest_quarter.get("net_profit_parent"))
        if not cls._normalize_text_value(report_date):
            report_date = latest_quarter.get("report_date")

        growth_continuity_score = 0
        quarterly_continuity_score = 0
        profit_quality_score = 0
        profitability_score = 0
        disclosure_signal_score = 0
        positive_signals: List[str] = []
        risk_flags: List[str] = []
        limitations = [
            "score_blends_quarterly_series_latest_snapshot_and_disclosure_text",
        ]

        if revenue_yoy is not None:
            if revenue_yoy >= 25:
                growth_continuity_score += 14
                positive_signals.append("revenue_yoy_strong")
            elif revenue_yoy >= 10:
                growth_continuity_score += 10
                positive_signals.append("revenue_yoy_positive")
            elif revenue_yoy > 0:
                growth_continuity_score += 6
                positive_signals.append("revenue_yoy_slightly_positive")
            else:
                risk_flags.append("revenue_yoy_non_positive")

        if net_profit_yoy is not None:
            if net_profit_yoy >= 40:
                growth_continuity_score += 16
                positive_signals.append("net_profit_yoy_very_strong")
            elif net_profit_yoy >= 20:
                growth_continuity_score += 12
                positive_signals.append("net_profit_yoy_strong")
            elif net_profit_yoy > 0:
                growth_continuity_score += 7
                positive_signals.append("net_profit_yoy_positive")
            else:
                risk_flags.append("net_profit_yoy_non_positive")

        if revenue_yoy is not None and net_profit_yoy is not None:
            if revenue_yoy > 0 and net_profit_yoy > revenue_yoy:
                growth_continuity_score += 3
                positive_signals.append("profit_growth_outpaces_revenue_growth")
            elif revenue_yoy > 0 and net_profit_yoy < 0:
                risk_flags.append("profit_growth_diverges_from_revenue_growth")

        quarterly_evidence = cls._build_quarterly_earnings_evidence(quarterly_series)
        ttm_snapshot = quarterly_evidence.get("ttm_snapshot") if isinstance(quarterly_evidence, dict) else {}
        if not isinstance(ttm_snapshot, dict):
            ttm_snapshot = {}
        qoq_snapshot = quarterly_evidence.get("qoq_snapshot") if isinstance(quarterly_evidence, dict) else {}
        if not isinstance(qoq_snapshot, dict):
            qoq_snapshot = {}
        margin_quality_snapshot = (
            quarterly_evidence.get("margin_quality_snapshot") if isinstance(quarterly_evidence, dict) else {}
        )
        if not isinstance(margin_quality_snapshot, dict):
            margin_quality_snapshot = {}
        single_quarter_growth_snapshot = (
            quarterly_evidence.get("single_quarter_growth_snapshot") if isinstance(quarterly_evidence, dict) else {}
        )
        if not isinstance(single_quarter_growth_snapshot, dict):
            single_quarter_growth_snapshot = {}
        quarterly_observation_count = int(quarterly_evidence.get("observation_count") or 0)
        if quarterly_observation_count <= 0:
            limitations.append("quarterly_series_unavailable_for_continuity_check")
        else:
            if quarterly_observation_count < 3:
                limitations.append("quarterly_series_history_short")

            dual_positive_streak = int(quarterly_evidence.get("dual_positive_streak") or 0)
            revenue_positive_streak = int(quarterly_evidence.get("revenue_positive_streak") or 0)
            profit_positive_streak = int(quarterly_evidence.get("profit_positive_streak") or 0)
            latest_trend = str(quarterly_evidence.get("latest_trend") or "").strip().lower()

            if dual_positive_streak >= 4:
                quarterly_continuity_score += 10
                positive_signals.append("quarterly_dual_growth_streak_4q")
            elif dual_positive_streak >= 3:
                quarterly_continuity_score += 7
                positive_signals.append("quarterly_dual_growth_streak_3q")
            elif dual_positive_streak >= 2:
                quarterly_continuity_score += 4
                positive_signals.append("quarterly_dual_growth_streak_2q")
            elif quarterly_observation_count >= 2:
                risk_flags.append("quarterly_dual_growth_streak_missing")

            if revenue_positive_streak >= 4:
                quarterly_continuity_score += 2
                positive_signals.append("revenue_yoy_positive_streak_4q")
            elif quarterly_observation_count >= 2 and revenue_positive_streak == 0:
                risk_flags.append("recent_revenue_growth_not_consistently_positive")

            if profit_positive_streak >= 4:
                quarterly_continuity_score += 2
                positive_signals.append("profit_yoy_positive_streak_4q")
            elif quarterly_observation_count >= 2 and profit_positive_streak == 0:
                risk_flags.append("recent_profit_growth_not_consistently_positive")

            if latest_trend == "improving":
                quarterly_continuity_score += 4
                positive_signals.append("quarterly_growth_trend_improving")
            elif latest_trend == "stable_positive":
                quarterly_continuity_score += 2
                positive_signals.append("quarterly_growth_trend_stable_positive")
            elif latest_trend == "recovering":
                quarterly_continuity_score += 1
                positive_signals.append("quarterly_growth_trend_recovering")
            elif latest_trend == "deteriorating":
                risk_flags.append("quarterly_growth_trend_deteriorating")

        quarterly_continuity_score = max(0, min(15, quarterly_continuity_score))
        growth_continuity_score += quarterly_continuity_score

        cycle_analysis = cls._build_growth_cycle_analysis(quarterly_evidence)
        cycle_phase = str(cycle_analysis.get("phase") or "").strip().lower()
        cycle_limitations = cycle_analysis.get("limitations")
        if isinstance(cycle_limitations, list):
            limitations.extend(str(item) for item in cycle_limitations if str(item).strip())
        if cycle_phase in {"reaccelerating", "expanding", "recovering", "mature"}:
            positive_signals.append(f"cycle_phase_{cycle_phase}")
        elif cycle_phase == "downcycle":
            risk_flags.append("cycle_phase_downcycle")

        cashflow_to_profit_ratio = None
        if operating_cash_flow is not None and net_profit_parent is not None and abs(net_profit_parent) > 1e-9:
            cashflow_to_profit_ratio = round(operating_cash_flow / net_profit_parent, 4)
            if operating_cash_flow > 0 and cashflow_to_profit_ratio >= 1.2:
                profit_quality_score += 18
                positive_signals.append("cashflow_covers_profit_well")
            elif operating_cash_flow > 0 and cashflow_to_profit_ratio >= 0.8:
                profit_quality_score += 12
                positive_signals.append("cashflow_matches_profit")
            elif operating_cash_flow > 0:
                profit_quality_score += 6
                risk_flags.append("cashflow_conversion_soft")
            else:
                risk_flags.append("operating_cash_flow_non_positive")
        else:
            if operating_cash_flow is not None and operating_cash_flow <= 0:
                risk_flags.append("operating_cash_flow_non_positive")
            limitations.append("cashflow_to_profit_ratio_unavailable")

        if roe is not None:
            if roe >= 18:
                profitability_score += 12
                positive_signals.append("roe_excellent")
            elif roe >= 12:
                profitability_score += 9
                positive_signals.append("roe_good")
            elif roe >= 8:
                profitability_score += 5
                positive_signals.append("roe_acceptable")
            else:
                risk_flags.append("roe_weak")

        if gross_margin is not None:
            if gross_margin >= 35:
                profitability_score += 8
                positive_signals.append("gross_margin_strong")
            elif gross_margin >= 20:
                profitability_score += 5
                positive_signals.append("gross_margin_healthy")
            elif gross_margin >= 10:
                profitability_score += 2
            else:
                risk_flags.append("gross_margin_thin")
        else:
            limitations.append("gross_margin_unavailable")

        disclosure_texts = [
            cls._normalize_text_value(earnings_data.get("forecast_summary")),
            cls._normalize_text_value(earnings_data.get("quick_report_summary")),
        ]
        disclosure_text = " ".join(text for text in disclosure_texts if text).strip()
        if disclosure_text:
            matched_positive = False
            matched_negative = False
            for keyword in _EARNINGS_QUALITY_POSITIVE_KEYWORDS:
                if keyword in disclosure_text:
                    matched_positive = True
                    positive_signals.append(f"disclosure_positive:{keyword}")
                    disclosure_signal_score += 10
                    break
            for keyword in _EARNINGS_QUALITY_NEGATIVE_KEYWORDS:
                if keyword in disclosure_text:
                    matched_negative = True
                    risk_flags.append(f"disclosure_negative:{keyword}")
                    disclosure_signal_score -= 8
                    break
            if matched_positive and matched_negative:
                limitations.append("disclosure_text_contains_mixed_signals")
        else:
            limitations.append("earnings_disclosure_text_unavailable")

        disclosure_signal_score = max(0, min(20, disclosure_signal_score))
        growth_continuity_score = max(0, min(35, growth_continuity_score))
        profit_quality_score = max(0, min(25, profit_quality_score))
        profitability_score = max(0, min(20, profitability_score))

        score_total = growth_continuity_score + profit_quality_score + profitability_score + disclosure_signal_score
        has_evidence = any(
            value is not None
            for value in (
                revenue_yoy,
                net_profit_yoy,
                roe,
                gross_margin,
                operating_cash_flow,
                net_profit_parent,
            )
        ) or bool(disclosure_text)
        verdict = cls._pick_earnings_quality_verdict(score_total, has_evidence)

        metrics = {
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
            "roe": roe,
            "gross_margin": gross_margin,
            "operating_cash_flow": operating_cash_flow,
            "net_profit_parent": net_profit_parent,
            "cashflow_to_profit_ratio": cashflow_to_profit_ratio,
            "report_date": report_date,
            "quarterly_observation_count": quarterly_observation_count if quarterly_observation_count > 0 else None,
            "dual_positive_streak": quarterly_evidence.get("dual_positive_streak") if quarterly_observation_count > 0 else None,
            "latest_quarterly_trend": quarterly_evidence.get("latest_trend") if quarterly_observation_count > 0 else None,
            "revenue_ttm_yoy": cls._safe_float(ttm_snapshot.get("revenue_ttm_yoy")),
            "net_profit_ttm_yoy": cls._safe_float(ttm_snapshot.get("net_profit_parent_ttm_yoy")),
            "operating_cash_flow_ttm_yoy": cls._safe_float(ttm_snapshot.get("operating_cash_flow_ttm_yoy")),
            "revenue_qoq": cls._safe_float(qoq_snapshot.get("revenue_qoq")),
            "net_profit_qoq": cls._safe_float(qoq_snapshot.get("net_profit_parent_qoq")),
            "operating_cash_flow_qoq": cls._safe_float(qoq_snapshot.get("operating_cash_flow_qoq")),
            "latest_single_quarter_revenue_yoy": cls._safe_float(single_quarter_growth_snapshot.get("latest_revenue_yoy")),
            "latest_single_quarter_net_profit_yoy": cls._safe_float(
                single_quarter_growth_snapshot.get("latest_net_profit_parent_yoy")
            ),
            "latest_single_quarter_operating_cash_flow_yoy": cls._safe_float(
                single_quarter_growth_snapshot.get("latest_operating_cash_flow_yoy")
            ),
            "gross_margin_trend": margin_quality_snapshot.get("gross_margin_trend"),
            "net_margin_trend": margin_quality_snapshot.get("net_margin_trend"),
            "cycle_phase": cycle_analysis.get("phase"),
            "cycle_confidence": cycle_analysis.get("confidence"),
            "cycle_score": cycle_analysis.get("score"),
        }
        metrics = {key: value for key, value in metrics.items() if value is not None}

        payload = {
            "score_total": score_total,
            "verdict": verdict,
            "growth_continuity_score": growth_continuity_score,
            "quarterly_continuity_score": quarterly_continuity_score,
            "profit_quality_score": profit_quality_score,
            "profitability_score": profitability_score,
            "disclosure_signal_score": disclosure_signal_score,
            "metrics": metrics,
            "quarterly_evidence": quarterly_evidence,
            "cycle_analysis": cycle_analysis,
            "positive_signals": sorted(set(positive_signals)),
            "risk_flags": sorted(set(risk_flags)),
            "limitations": sorted(set(limitations)),
        }
        if not has_evidence:
            payload["score_total"] = None
            payload["growth_continuity_score"] = 0
            payload["quarterly_continuity_score"] = 0
            payload["profit_quality_score"] = 0
            payload["profitability_score"] = 0
            payload["disclosure_signal_score"] = 0
        return payload

    @staticmethod
    def _should_cache_fundamental_context(context: Any) -> bool:
        if not isinstance(context, dict):
            return False
        status = str(context.get("status", "")).strip().lower()
        if status == "ok":
            return True
        if status == "failed":
            return False
        for block in (
            "valuation",
            "growth",
            "earnings",
            "earnings_quality",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
            payload = context.get(block, {})
            if isinstance(payload, dict):
                data = payload.get("data")
                if block == "earnings_quality":
                    if DataFetcherManager._has_meaningful_earnings_quality_payload(data):
                        return True
                    continue
                if DataFetcherManager._has_meaningful_payload(data):
                    return True
        return False

    def _build_market_not_supported(self, market: str, reason: str) -> Dict[str, Any]:
        blocks = {
            "valuation": self._build_fundamental_block(
                "partial" if market == "etf" else "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "growth": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "earnings": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "earnings_quality": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "institution": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "capital_flow": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "dragon_tiger": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "boards": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
        }
        return {
            "market": market,
            "status": "partial" if market == "etf" else "not_supported",
            "coverage": {
                block: blocks[block]["status"] for block in blocks
            },
            "source_chain": [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def build_failed_fundamental_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        """Build a consistent failed-context payload for caller-side fallback."""
        market = _market_tag(stock_code)
        block_names = (
            "valuation",
            "growth",
            "earnings",
            "earnings_quality",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        )
        blocks = {
            block: self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                [reason],
            )
            for block in block_names
        }
        return {
            "market": market,
            "status": "failed",
            "coverage": {block: "failed" for block in block_names},
            "source_chain": [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def get_earnings_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fetch only the earnings-related fundamental blocks needed by fast scans."""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        market = _market_tag(stock_code)
        if not config.enable_fundamental_pipeline:
            return self._build_market_not_supported(
                market=market,
                reason="fundamental pipeline disabled",
            )
        if market in {"us", "hk"}:
            return self._build_market_not_supported(
                market=market,
                reason="market not supported",
            )
        if _is_etf_code(stock_code):
            return self._build_market_not_supported(
                market=market,
                reason="etf not fully supported",
            )

        stage_timeout = float(
            budget_seconds if budget_seconds is not None else config.fundamental_stage_timeout_seconds
        )
        stage_timeout = max(0.0, stage_timeout)
        fetch_timeout = max(0.0, float(config.fundamental_fetch_timeout_seconds))
        bundle_timeout = min(fetch_timeout, stage_timeout)

        if bundle_timeout <= 0:
            bundle_status = "failed"
            bundle_payload: Dict[str, Any] = {}
            bundle_errors = ["fundamental stage timeout"]
            bundle_ms = 0
        else:
            bundle_payload, bundle_err_msg, bundle_ms = self._run_with_retry(
                lambda: self._fundamental_adapter.get_fundamental_bundle(stock_code),
                bundle_timeout,
                "fundamental_bundle",
            )
            if not isinstance(bundle_payload, dict):
                bundle_status = "failed"
                bundle_payload = {}
                bundle_errors = ["fundamental_bundle failed"]
                if bundle_err_msg:
                    bundle_errors.append(bundle_err_msg)
            else:
                bundle_status = str(bundle_payload.get("status", "not_supported"))
                bundle_errors = [bundle_err_msg] if bundle_err_msg else []

        bundle_chain = self._normalize_source_chain(
            bundle_payload.get("source_chain", []) if isinstance(bundle_payload, dict) else None,
            "fundamental_bundle",
            bundle_status,
            bundle_ms,
        )
        growth_payload = bundle_payload.get("growth", {}) if isinstance(bundle_payload, dict) else {}
        earnings_payload = bundle_payload.get("earnings", {}) if isinstance(bundle_payload, dict) else {}
        if not isinstance(growth_payload, dict):
            growth_payload = {}
        else:
            growth_payload = dict(growth_payload)
        if not isinstance(earnings_payload, dict):
            earnings_payload = {}
        else:
            earnings_payload = dict(earnings_payload)

        adapter_errors = list(bundle_payload.get("errors", [])) if isinstance(bundle_payload, dict) else []
        adapter_errors.extend(bundle_errors)
        earnings_quality_payload = self._build_earnings_quality_payload(growth_payload, earnings_payload)

        growth_status = self._infer_block_status(growth_payload, bundle_status)
        earnings_status = self._infer_block_status(earnings_payload, bundle_status)
        if self._has_meaningful_earnings_quality_payload(earnings_quality_payload):
            earnings_quality_status = "ok"
        elif bundle_status in ("failed", "partial", "not_supported"):
            earnings_quality_status = bundle_status
        else:
            earnings_quality_status = "partial"

        statuses = [growth_status, earnings_status, earnings_quality_status]
        if any(status == "ok" for status in statuses):
            overall_status = "ok"
        elif any(status == "partial" for status in statuses):
            overall_status = "partial"
        else:
            overall_status = bundle_status or "failed"

        return {
            "market": market,
            "status": overall_status,
            "coverage": {
                "growth": growth_status,
                "earnings": earnings_status,
                "earnings_quality": earnings_quality_status,
            },
            "source_chain": bundle_chain,
            "errors": adapter_errors,
            "growth": self._build_fundamental_block(
                growth_status,
                growth_payload,
                bundle_chain,
                list(adapter_errors),
            ),
            "earnings": self._build_fundamental_block(
                earnings_status,
                earnings_payload,
                bundle_chain,
                list(adapter_errors),
            ),
            "earnings_quality": self._build_fundamental_block(
                earnings_quality_status,
                earnings_quality_payload,
                bundle_chain,
                list(adapter_errors),
            ),
        }

    def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Aggregate fundamental blocks with fail-open semantics.
        """
        from src.config import get_config

        config = get_config()
        if not config.enable_fundamental_pipeline:
            return self._build_market_not_supported(
                market=_market_tag(stock_code),
                reason="fundamental pipeline disabled",
            )

        stock_code = normalize_stock_code(stock_code)
        market = _market_tag(stock_code)
        is_etf = _is_etf_code(stock_code)
        if market in {"us", "hk"}:
            return self._build_market_not_supported(
                market=market,
                reason="market not supported",
            )

        stage_timeout = float(
            budget_seconds if budget_seconds is not None else config.fundamental_stage_timeout_seconds
        )
        stage_timeout = max(0.0, stage_timeout)
        fetch_timeout = float(config.fundamental_fetch_timeout_seconds)
        fetch_timeout = max(0.0, fetch_timeout)

        cache_ttl = int(config.fundamental_cache_ttl_seconds)
        cache_max_entries = max(0, int(getattr(config, "fundamental_cache_max_entries", 256)))
        cache_key = self._get_fundamental_cache_key(stock_code, stage_timeout)
        if cache_ttl > 0:
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
            with self._fundamental_cache_lock:
                cache_item = self._fundamental_cache.get(cache_key)
                if cache_item:
                    age = time.time() - float(cache_item.get("ts", 0))
                    if age <= cache_ttl:
                        return cache_item.get("context", {})

        remaining_seconds = stage_timeout
        result_ctx: Dict[str, Any] = {
            "market": market,
            "valuation": {},
            "growth": {},
            "earnings": {},
            "earnings_quality": {},
            "institution": {},
            "capital_flow": {},
            "dragon_tiger": {},
            "boards": {},
            "coverage": {},
            "source_chain": [],
            "errors": [],
        }

        start_ts = time.time()

        def _consume_budget(consumed_ms: int) -> None:
            nonlocal remaining_seconds
            remaining_seconds = max(0.0, remaining_seconds - consumed_ms / 1000.0)

        valuation_timeout = min(fetch_timeout, remaining_seconds)
        if valuation_timeout > 0:
            quote_payload, valuation_err, valuation_ms = self._run_with_retry(
                lambda: self.get_realtime_quote(stock_code),
                valuation_timeout,
                "fundamental_valuation",
            )
            _consume_budget(valuation_ms)
        else:
            quote_payload, valuation_err, valuation_ms = None, "fundamental stage timeout", 0

        valuation_payload = {
            "pe_ratio": getattr(quote_payload, "pe_ratio", None) if quote_payload else None,
            "pb_ratio": getattr(quote_payload, "pb_ratio", None) if quote_payload else None,
            "total_mv": getattr(quote_payload, "total_mv", None) if quote_payload else None,
            "circ_mv": getattr(quote_payload, "circ_mv", None) if quote_payload else None,
        }
        valuation_status = self._infer_block_status(
            valuation_payload,
            "partial" if quote_payload is not None else "not_supported",
        )
        if valuation_status == "partial" and valuation_err and not self._has_meaningful_payload(valuation_payload):
            valuation_status = "failed"
        result_ctx["valuation"] = self._build_fundamental_block(
            valuation_status,
            valuation_payload,
            self._normalize_source_chain(
                [{"provider": "realtime_quote", "result": valuation_status, "duration_ms": valuation_ms}],
                "realtime_quote",
                valuation_status,
                valuation_ms,
            ),
            [valuation_err] if valuation_err else [],
        )

        # growth / earnings / institution (one AkShare call)
        if remaining_seconds <= 0:
            bundle_status = "failed"
            bundle_payload: Dict[str, Any] = {}
            bundle_errors = ["fundamental stage timeout"]
            bundle_ms = 0
        else:
            bundle_timeout = min(fetch_timeout, remaining_seconds)
            bundle_payload, bundle_err_msg, bundle_ms = self._run_with_retry(
                lambda: self._fundamental_adapter.get_fundamental_bundle(stock_code),
                bundle_timeout,
                "fundamental_bundle",
            )
            _consume_budget(bundle_ms)
            if not isinstance(bundle_payload, dict):
                bundle_status = "failed"
                bundle_payload = {}
                bundle_errors = ["fundamental_bundle failed"]
                if bundle_err_msg:
                    bundle_errors.append(bundle_err_msg)
            else:
                bundle_status = str(bundle_payload.get("status", "not_supported"))
                bundle_errors = [bundle_err_msg] if bundle_err_msg else []

        bundle_chain = self._normalize_source_chain(
            bundle_payload.get("source_chain", []),
            "fundamental_bundle",
            bundle_status,
            bundle_ms,
        ) if isinstance(bundle_payload, dict) else self._normalize_source_chain(
            None,
            "fundamental_bundle",
            bundle_status,
            bundle_ms,
        )
        growth_payload = bundle_payload.get("growth", {}) if isinstance(bundle_payload, dict) else {}
        earnings_payload = bundle_payload.get("earnings", {}) if isinstance(bundle_payload, dict) else {}
        institution_payload = bundle_payload.get("institution", {}) if isinstance(bundle_payload, dict) else {}
        if not isinstance(growth_payload, dict):
            growth_payload = {}
        else:
            growth_payload = dict(growth_payload)
        if not isinstance(earnings_payload, dict):
            earnings_payload = {}
        else:
            earnings_payload = dict(earnings_payload)
        if not isinstance(institution_payload, dict):
            institution_payload = {}
        else:
            institution_payload = dict(institution_payload)

        # Derive TTM dividend yield from already-fetched quote price; avoid extra quote calls.
        earnings_extra_errors: List[str] = []
        dividend_payload = earnings_payload.get("dividend")
        if isinstance(dividend_payload, dict):
            dividend_payload = dict(dividend_payload)
            ttm_cash_raw = dividend_payload.get("ttm_cash_dividend_per_share")
            ttm_cash = None
            if ttm_cash_raw is not None:
                try:
                    ttm_cash = float(ttm_cash_raw)
                except (TypeError, ValueError):
                    earnings_extra_errors.append("invalid_ttm_cash_dividend_per_share")
            if isinstance(quote_payload, dict):
                latest_price_raw = quote_payload.get("price")
            else:
                latest_price_raw = getattr(quote_payload, "price", None) if quote_payload else None
            latest_price = None
            if latest_price_raw is not None:
                try:
                    latest_price = float(latest_price_raw)
                except (TypeError, ValueError):
                    latest_price = None
            ttm_yield = None
            if ttm_cash is not None:
                if latest_price is not None and latest_price > 0:
                    ttm_yield = round(ttm_cash / latest_price * 100.0, 4)
                else:
                    earnings_extra_errors.append("invalid_price_for_ttm_dividend_yield")

            dividend_payload["ttm_dividend_yield_pct"] = ttm_yield
            if ttm_yield is not None:
                dividend_payload["yield_formula"] = "ttm_cash_dividend_per_share / latest_price * 100"
            earnings_payload["dividend"] = dividend_payload

        adapter_errors = list(bundle_payload.get("errors", [])) if isinstance(bundle_payload, dict) else []
        adapter_errors.extend(bundle_errors)
        growth_errors = list(adapter_errors)
        earnings_errors = list(adapter_errors)
        earnings_errors.extend(earnings_extra_errors)
        earnings_quality_payload = self._build_earnings_quality_payload(growth_payload, earnings_payload)
        earnings_quality_errors = list(adapter_errors)
        institution_errors = list(adapter_errors)

        growth_status = self._infer_block_status(growth_payload, bundle_status)
        earnings_status = self._infer_block_status(earnings_payload, bundle_status)
        if self._has_meaningful_earnings_quality_payload(earnings_quality_payload):
            earnings_quality_status = "ok"
        elif bundle_status in ("failed", "partial", "not_supported"):
            earnings_quality_status = bundle_status
        else:
            earnings_quality_status = "partial"
        institution_status = self._infer_block_status(institution_payload, bundle_status)

        result_ctx["growth"] = self._build_fundamental_block(
            growth_status,
            growth_payload,
            bundle_chain,
            growth_errors,
        )
        result_ctx["earnings"] = self._build_fundamental_block(
            earnings_status,
            earnings_payload,
            bundle_chain,
            earnings_errors,
        )
        result_ctx["earnings_quality"] = self._build_fundamental_block(
            earnings_quality_status,
            earnings_quality_payload,
            bundle_chain,
            earnings_quality_errors,
        )
        result_ctx["institution"] = self._build_fundamental_block(
            institution_status,
            institution_payload,
            bundle_chain,
            institution_errors,
        )

        # capital flow
        if is_etf:
            result_ctx["capital_flow"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["dragon_tiger"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["boards"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["status"] = "partial"
        else:
            capital_flow_budget = min(fetch_timeout, remaining_seconds)
            capital_flow_start = time.time()
            result_ctx["capital_flow"] = self.get_capital_flow_context(
                stock_code,
                budget_seconds=capital_flow_budget,
            )
            _consume_budget(int((time.time() - capital_flow_start) * 1000))

            dragon_tiger_budget = min(fetch_timeout, remaining_seconds)
            dragon_tiger_start = time.time()
            result_ctx["dragon_tiger"] = self.get_dragon_tiger_context(
                stock_code,
                budget_seconds=dragon_tiger_budget,
            )
            _consume_budget(int((time.time() - dragon_tiger_start) * 1000))

            result_ctx["boards"] = self.get_board_context(
                stock_code,
                budget_seconds=min(fetch_timeout, remaining_seconds),
            )

        block_statuses = {
            "valuation": result_ctx["valuation"].get("status", "not_supported"),
            "growth": result_ctx["growth"].get("status", "not_supported"),
            "earnings": result_ctx["earnings"].get("status", "not_supported"),
            "earnings_quality": result_ctx["earnings_quality"].get("status", "not_supported"),
            "institution": result_ctx["institution"].get("status", "not_supported"),
            "capital_flow": result_ctx["capital_flow"].get("status", "not_supported"),
            "dragon_tiger": result_ctx["dragon_tiger"].get("status", "not_supported"),
            "boards": result_ctx["boards"].get("status", "not_supported"),
        }
        result_ctx["coverage"] = block_statuses
        for block in (
            "valuation",
            "growth",
            "earnings",
            "earnings_quality",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
            result_ctx["errors"].extend(result_ctx[block].get("errors", []))
            result_ctx["source_chain"].extend(result_ctx[block].get("source_chain", []))

        if is_etf:
            # Keep ETF downgrade semantics for overall status even when valuation is available.
            result_ctx["status"] = (
                "not_supported" if all(value == "not_supported" for value in block_statuses.values()) else "partial"
            )
        elif all(value == "not_supported" for value in block_statuses.values()):
            result_ctx["status"] = "not_supported"
        elif "failed" in block_statuses.values() or "partial" in block_statuses.values():
            result_ctx["status"] = "partial"
        else:
            result_ctx["status"] = "ok"

        result_ctx["elapsed_ms"] = int((time.time() - start_ts) * 1000)
        if cache_ttl > 0 and self._should_cache_fundamental_context(result_ctx):
            with self._fundamental_cache_lock:
                self._fundamental_cache[cache_key] = {
                    "ts": time.time(),
                    "context": result_ctx,
                }
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
        return result_ctx

    def get_capital_flow_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """资金流向块（fail-open）。"""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )
        payload, err, cost_ms = self._run_with_retry(
            lambda: self._fundamental_adapter.get_capital_flow(stock_code),
            timeout,
            "capital_flow",
        )
        if not isinstance(payload, dict):
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": cost_ms}],
                [err or "capital_flow failed"],
            )

        stock_flow = payload.get("stock_flow") or {}
        sector_rankings = payload.get("sector_rankings") or {}
        has_stock_flow = False
        if isinstance(stock_flow, dict):
            has_stock_flow = any(v is not None for v in stock_flow.values())
        has_sector_rankings = bool(sector_rankings.get("top")) or bool(sector_rankings.get("bottom"))
        adapter_status = str(payload.get("status", "not_supported"))
        if has_stock_flow or has_sector_rankings:
            capital_flow_status = "ok"
        elif adapter_status == "not_supported":
            capital_flow_status = "not_supported"
        else:
            capital_flow_status = "partial"

        return self._build_fundamental_block(
            capital_flow_status,
            {
                "stock_flow": payload.get("stock_flow", {}),
                "sector_rankings": payload.get("sector_rankings", {}),
            },
            self._normalize_source_chain(
                payload.get("source_chain", []),
                "capital_flow",
                capital_flow_status,
                cost_ms,
            ),
            list(payload.get("errors", [])) + ([err] if err else []),
        )

    def get_dragon_tiger_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """龙虎榜块（fail-open）。"""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )
        payload, err, cost_ms = self._run_with_retry(
            lambda: self._fundamental_adapter.get_dragon_tiger_flag(stock_code),
            timeout,
            "dragon_tiger",
        )
        if not isinstance(payload, dict):
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": cost_ms}],
                [err or "dragon_tiger failed"],
            )
        return self._build_fundamental_block(
            (payload.get("status") if isinstance(payload.get("status"), str) else "partial"),
            {
                "is_on_list": bool(payload.get("is_on_list", False)),
                "recent_count": int(payload.get("recent_count", 0)),
                "latest_date": payload.get("latest_date"),
            },
            self._normalize_source_chain(
                payload.get("source_chain", []),
                "dragon_tiger",
                str(payload.get("status", "ok")),
                cost_ms,
            ),
            list(payload.get("errors", [])) + ([err] if err else []),
        )

    def get_board_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """板块榜单块（fail-open）。"""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )

        def task() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], str]:
            return self._get_sector_rankings_with_meta(5)

        rankings, err, cost_ms = self._run_with_retry(task, timeout, "boards")
        if isinstance(rankings, tuple) and len(rankings) == 4:
            top, bottom, chain, chain_error = rankings
            if chain_error and not err:
                err = chain_error
            if not top and not bottom:
                return self._build_fundamental_block(
                    "failed",
                    {},
                    chain if chain else [{"provider": "sector_rankings", "result": "failed", "duration_ms": cost_ms}],
                    [err or "boards empty from all sources"],
                )
            board_status = "ok" if top and bottom else "partial"
            return self._build_fundamental_block(
                board_status,
                {"top": top or [], "bottom": bottom or []},
                chain if chain else self._normalize_source_chain(
                    ["sector_rankings"],
                    "boards",
                    board_status,
                    cost_ms,
                ),
                [err] if err else [],
            )

        return self._build_fundamental_block(
            "failed",
            {},
            [{"provider": "sector_rankings", "result": "failed", "duration_ms": cost_ms}],
            [err or "boards failed"],
        )

    def _get_sector_rankings_with_meta(
            self,
            n: int = 5,
        ) -> Tuple[List[Dict], List[Dict], List[Dict[str, Any]], str]:
            """Get sector rankings with ordered fallback chain metadata."""
            cached_rankings = self._get_cached_sector_rankings(n)
            if cached_rankings is not None:
                return cached_rankings
            source_chain: List[Dict[str, Any]] = []
            last_error = ""

            # 直接遍历管理器已经按 priority 排好序的数据源列表
            for fetcher in self._fetchers:
                if not hasattr(fetcher, 'get_sector_rankings'):
                    continue

                start = time.time()
                try:
                    data = fetcher.get_sector_rankings(n)
                    duration_ms = int((time.time() - start) * 1000)
                    if data and data[0] is not None and data[1] is not None:
                        source_chain.append(
                            {
                                "provider": fetcher.name,
                                "result": "ok",
                                "duration_ms": duration_ms,
                            }
                        )
                        self._cache_sector_rankings(
                            n,
                            top=data[0],
                            bottom=data[1],
                            source_chain=source_chain,
                            last_error="",
                        )
                        logger.info(f"[{fetcher.name}] 获取板块排行成功")
                        return data[0], data[1], source_chain, ""

                    last_error = f"{fetcher.name}返回空结果"
                    source_chain.append(
                        {
                            "provider": fetcher.name,
                            "result": "empty",
                            "duration_ms": duration_ms,
                            "error": last_error,
                        }
                    )
                except Exception as e:
                    error_type, error_reason = summarize_exception(e)
                    last_error = f"{fetcher.name} ({error_type}) {error_reason}"
                    duration_ms = int((time.time() - start) * 1000)
                    source_chain.append(
                        {
                            "provider": fetcher.name,
                            "result": "failed",
                            "duration_ms": duration_ms,
                            "error": error_reason,
                        }
                    )
                    logger.warning(f"[{fetcher.name}] 获取板块排行失败: {error_reason}")

            return [], [], source_chain, last_error

    def get_sector_rankings(self, n: int = 5) -> Tuple[List[Dict], List[Dict]]:
        """获取板块涨跌榜（自动切换数据源）"""
        # 按需求固定回退顺序：Akshare(EM) -> Akshare(Sina) -> Tushare -> Efinance
        top, bottom, _, last_error = self._get_sector_rankings_with_meta(n)
        if top or bottom:
            return top, bottom
        logger.warning(f"[板块排行] 所有数据源均失败，最终错误: {last_error}")
        return [], []
