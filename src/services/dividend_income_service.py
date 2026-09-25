# -*- coding: utf-8 -*-
"""股息线（防守线）选股服务：高股息 + 可持续分红筛选 MVP。

设计口径（见 docs/个人策略文档/个人策略精简方案.md §3.1）：

- 数据源：
  - Tushare ``stock_basic``：全量股票基础信息（缓存 7 天）；
  - Tushare ``daily_basic``：按 trade_date 快照一次拉全市场，取 ``dv_ttm`` 股息率（TTM，%）；
    优先复用本地已缓存快照，低配额账号（1 次/分钟~1 次/小时级）尽量零调用；
  - AKShare ``stock_fhps_em``（主）：按报告期批量拉全市场分红送配（免费、无配额），
    提供分红比例（每 10 股派息）与每股收益，缓存 30 天；
  - AKShare ``stock_financial_abstract_ths``（业绩面）：按年度拉取 ROE、每股经营现金流等指标
    （免费，缓存 90 天），用于盈利质量块与分红现金流覆盖校验（``--no-fundamentals`` 可关）；
  - Tushare ``dividend``（兜底）：仅当某只候选股在 AKShare 批量数据中缺失时逐票补拉；
  - EPS 无 ``fina_indicator`` 权限时，用 AKShare 每股收益或 ``close / pe_ttm`` 反推。
- 默认样本口径（沿用个人策略既有约定）：排除 ST（含 PT）、排除科创板、排除北交所、保留创业板；
- 默认入池：连续分红 ≥5 个年度、最新分红年度不早于前 2 个年度、股息率（TTM）≥ 4%；
- 价格走势校验（默认开启）：复用个人策略同一行情链路（DataFetcherManager，本地磁盘
  缓存 data/cache/history 优先、缺口自动增量补齐并落盘），计算近一年/近半年原始涨跌，
  并以 ``原始涨跌 + TTM 股息率`` 近似含息净回报，对持续下跌的候选打标签并扣分，
  避免"高息陷阱"——股价跌幅吞掉股息；可用 ``--no-price-trend`` 关闭。
- 输出：``data/dividend_income/<snapshot>/dividend_income_candidates.csv`` / ``.md`` / ``summary.json``。

本模块只做“池子与指标展示”，不构成任何买卖建议。
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "dividend_income"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "dividend_income"

DIVIDEND_HISTORY_YEARS_WINDOW = 12
DIV_PROC_IMPLEMENTED_MARKER = "实施"
RATE_LIMIT_MARKERS = ("频率超限", "rate limit", "too many requests", "次/分", "每分钟")
STOCK_BASIC_FIELDS = "ts_code,symbol,name,industry,market,list_date,exchange,act_ent_type"
DAILY_BASIC_FIELDS = "ts_code,close,dv_ratio,dv_ttm,pe_ttm,pb,total_mv"
DIVIDEND_FIELDS = "ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax,record_date,ex_date,pay_date,imp_ann_date"
FINA_FIELDS = "ts_code,end_date,eps,roe,ocfps"

# AKShare 分红送配（stock_fhps_em）：按报告期批量拉全市场；缓存 30 天。
AKSHARE_FHPS_REFRESH_DAYS = 30
AKSHARE_FHPS_ANNUAL_YEARS = 11
AKSHARE_FHPS_INTERIM_YEARS = 2
AKSHARE_FHPS_CASH10_COLUMN = "现金分红-现金分红比例"
AKSHARE_FHPS_EPS_COLUMN = "每股收益"
AKSHARE_FHPS_PROGRESS_COLUMN = "方案进度"
AKSHARE_FHPS_EX_DATE_COLUMN = "除权除息日"

# 周期属性提示（仅打标签，不做硬过滤）：资源、航运、化工等景气顶部可能虚高股息率。
CYCLICAL_INDUSTRY_HINTS = (
    "煤炭",
    "石油",
    "有色",
    "钢铁",
    "化学",
    "化工",
    "航运",
    "港口",
    "水运",
    "水泥",
    "房地产",
    "建材",
)

# 综合分权重（v2，基础满分 92 + 调整项）：
# 股息率 35 + 连续性 20 + 盈利质量 25（EPS 基础 ≤10 + ROE ≤8 + 现金流覆盖 ≤7）+ 低波动 12；
# 央国企偏好（+3）、分红成长（±2/-3）、价格走势（≤0）为独立调整项。
YIELD_SCORE_CAP = 35.0
CONTINUITY_SCORE_CAP = 20.0
QUALITY_SCORE_CAP = 25.0
QUALITY_EPS_SCORE_CAP = 10.0
QUALITY_ROE_SCORE_CAP = 8.0
QUALITY_CF_SCORE_CAP = 7.0
QUALITY_ROE_NEUTRAL_SCORE = 4.0
QUALITY_CF_NEUTRAL_SCORE = 3.0
LOW_VOL_SCORE_CAP = 12.0
LOW_VOL_NEUTRAL_SCORE = 6.0
LOW_VOL_EXCELLENT_PCT = 20.0
LOW_VOL_GOOD_PCT = 28.0
LOW_VOL_FAIR_PCT = 35.0
LOW_VOL_DEEP_DRAWDOWN_PCT = -30.0
SOE_SCORE_BONUS = 3.0
DIVIDEND_GROWTH_BONUS_ADJ = 2.0
DIVIDEND_SHRINK_PENALTY_ADJ = -3.0
DIVIDEND_GROWTH_YEARS = 5
FUNDAMENTAL_REFRESH_DAYS = 90
OWNER_TYPE_SOE = ("央企", "地方国企")

# v3 条件（2026-09-24）：趋势闸门（均线）+ 扣非质量 + ROE 趋势 + PE 极端软扣分。
MA_LONG_BARS = 200
MA_MEDIUM_BARS = 60
MA_SLOW_BARS = 120
TREND_ABOVE_MA200_BONUS_ADJ = 4.0
TREND_BREAKDOWN_PENALTY_ADJ = -6.0
TREND_BREAKDOWN_DISTANCE_PCT = -5.0
NP_DEDUCT_LOW_THRESHOLD = 0.7
NP_DEDUCT_LOW_ADJ = -3.0
ROE_DECLINE_RATIO = 0.7
ROE_DECLINE_ADJ = -2.0
PE_EXTREME_HIGH = 30.0
PE_EXTREME_ADJ = -2.0
FUNDAMENTAL_DECLINE_PCT = -10.0

# 价格走势校验（复用个人策略本地行情缓存；原始涨跌 + TTM 股息率 ≈ 含息净回报）：
# 防止"高息陷阱"（股价跌幅吞掉股息）。净回报 < 0 扣 3 分；≤ -15% 扣 8 分；
# ≤ -30% 扣 12 分；近半年仍跌 ≥10% 再扣 2 分（总扣分下限 -14）。
PRICE_TREND_REQUEST_DAYS = 260
PRICE_TREND_YEAR_BARS = 250
PRICE_TREND_HALF_YEAR_BARS = 120
PRICE_TREND_MIN_BARS = 251
PRICE_TREND_FALL_MILD_ADJ = -3.0
PRICE_TREND_FALL_MEDIUM_ADJ = -8.0
PRICE_TREND_FALL_SEVERE_ADJ = -12.0
PRICE_TREND_FALL_6M_EXTRA_ADJ = -2.0
PRICE_TREND_ADJ_FLOOR = -14.0


@dataclass(frozen=True)
class DividendIncomeOptions:
    """一次股息线筛选的运行参数。"""

    snapshot_date: str
    min_yield_pct: float = 4.0
    min_dividend_years: int = 5
    min_listed_years: int = 5
    prefilter_limit: int = 300
    refresh_days: int = 30
    rate_limit_per_minute: int = 45
    cache_only: bool = False
    wait_minutes: float = 0.0
    prefer_soe: bool = True
    price_trend: bool = True
    fundamentals: bool = True
    output_dir: Path = DEFAULT_OUTPUT_DIR
    cache_dir: Path = DEFAULT_CACHE_DIR


class TushareDividendProvider:
    """最小 Tushare 客户端包装：进程级节流 + 按接口记忆频率上限冷却。

    低积分账号常见 single-interface 配额为 1 次/分钟；命中后本接口冷却 62 秒，
    后续调用自动等待冷却结束，保证长任务（逐票分红明细）可以无人值守跑完。
    """

    def __init__(self, token: str, *, rate_limit_per_minute: int = 45) -> None:
        import tushare as ts

        ts.set_token(token)
        self._api = ts.pro_api()
        self._min_interval = 60.0 / max(1.0, float(rate_limit_per_minute))
        self._last_call_ts = 0.0
        self._interface_cooldown: Dict[str, float] = {}
        self.rate_limited = False

    def query(self, interface: str, **kwargs: Any) -> pd.DataFrame:
        now = time.monotonic()
        cooldown_until = self._interface_cooldown.get(interface, 0.0)
        if now < cooldown_until:
            time.sleep(cooldown_until - now)
        wait = self._min_interval - (time.monotonic() - self._last_call_ts)
        if wait > 0:
            time.sleep(min(wait, 5.0))
        self._last_call_ts = time.monotonic()
        try:
            return self._api.query(interface, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一由调用方决定降级策略
            if _is_rate_limit_error(exc):
                self._interface_cooldown[interface] = time.monotonic() + 62.0
            raise


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def _is_permission_error(exc: Exception) -> bool:
    text = str(exc or "")
    return "没有接口" in text or "访问权限" in text


def _cache_is_fresh(path: Path, *, refresh_days: int) -> bool:
    if not path.exists():
        return False
    if refresh_days <= 0:
        return True
    age = time.time() - path.stat().st_mtime
    return age <= refresh_days * 86400


def _read_csv_safe(path: Path) -> pd.DataFrame:
    """读取 CSV 缓存；空文件/损坏文件按“无数据”处理，避免整轮失败。"""
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:  # noqa: BLE001 - 空文件视为无数据
        return pd.DataFrame()
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame()
    return df.fillna("")


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def _normalize_ts_code(code: Any) -> str:
    return str(code or "").strip().upper()


def _parse_cn_number(value: Any) -> Optional[float]:
    """解析同花顺口径数字：支持 '36.02%'、'862.28亿'、'1,234.5万'、'--' 等。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-", "None", "nan"}:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier, text = 1e8, text[:-1]
    elif text.endswith("万"):
        multiplier, text = 1e4, text[:-1]
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return None


def _normalize_owner_type(raw: Any) -> str:
    """归一化实控人企业性质：央企 / 地方国企 / 民营 / 外资 / 其他 / 未知。"""
    text = str(raw or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "未知"
    if "中央" in text:
        return "央企"
    if "地方" in text:
        return "地方国企"
    if "民营" in text:
        return "民营"
    if "外资" in text or "境外" in text or "外商" in text:
        return "外资"
    if "国有" in text:
        return "国企"
    return "其他"


def _code_prefix(ts_code: str) -> str:
    return _normalize_ts_code(ts_code).split(".")[0]


class DividendIncomeService:
    """股息线筛选主流程。"""

    def __init__(
        self,
        *,
        provider: Any = None,
        akshare_fetcher: Any = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        rate_limit_per_minute: int = 45,
        retry_wait_seconds: float = 63.0,
        wait_step_seconds: Optional[float] = None,
        price_trend_fetcher: Any = None,
        price_trend_interval_seconds: float = 0.25,
    ) -> None:
        self._provider = provider
        self._akshare_fetcher = akshare_fetcher
        self._price_trend_fetcher = price_trend_fetcher
        self._price_trend_interval = max(float(price_trend_interval_seconds), 0.0)
        self._cache_dir = Path(cache_dir)
        self._output_dir = Path(output_dir)
        self._rate_limit_per_minute = int(rate_limit_per_minute)
        self._retry_wait_seconds = float(retry_wait_seconds)
        self._wait_step_seconds = wait_step_seconds
        self._warnings: List[str] = []
        self._fundamental_failures = 0
        self._unavailable_interfaces: set = set()
        self._rate_limit_strikes: Dict[str, int] = {}
        self._gave_up_interfaces: set = set()

    # ------------------------------------------------------------------ 基础

    def _query(self, interface: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        if (
            self._provider is None
            or interface in self._unavailable_interfaces
            or interface in self._gave_up_interfaces
        ):
            return None
        last_error: Optional[Exception] = None
        for attempt in range(2):  # 首次 + 冷却后重试一次
            try:
                result = self._provider.query(interface, **kwargs)
                self._rate_limit_strikes[interface] = 0
                return result
            except Exception as exc:  # noqa: BLE001 - 需要按接口降级而不是整体失败
                last_error = exc
                if _is_permission_error(exc):
                    self._unavailable_interfaces.add(interface)
                    logger.warning("[股息线] %s 无访问权限，本轮跳过该接口：%s", interface, str(exc)[:120])
                    self._warnings.append(f"{interface}_permission_denied")
                    return None
                if not _is_rate_limit_error(exc):
                    break
                self._mark_rate_limited(interface)
                if self._note_rate_limit_strike(interface):
                    return None
                if attempt == 0:
                    time.sleep(self._retry_wait_seconds)
        logger.warning("[股息线] %s 查询失败：%s", interface, str(last_error)[:160])
        self._warnings.append(f"{interface}_failed")
        return None

    def _note_rate_limit_strike(self, interface: str) -> bool:
        """记录一次频率限制；连续 2 次后本轮放弃该接口，避免长循环空转。"""
        strikes = self._rate_limit_strikes.get(interface, 0) + 1
        self._rate_limit_strikes[interface] = strikes
        if strikes >= 2:
            self._gave_up_interfaces.add(interface)
            logger.warning("[股息线] %s 连续触发频率限制，本轮放弃该接口（改用缓存/降级路径）", interface)
            self._warnings.append(f"{interface}_rate_limit_give_up")
            return True
        return False

    def _mark_rate_limited(self, interface: str) -> None:
        logger.warning("[股息线] %s 触发频率限制，等待 %.0f 秒后重试一次", interface, self._retry_wait_seconds)
        self._warnings.append(f"{interface}_rate_limited")
        if self._provider is not None:
            setattr(self._provider, "rate_limited", True)

    # ------------------------------------------------------------------ 数据加载

    def _load_stock_basic(self, *, cache_only: bool) -> pd.DataFrame:
        cache_path = self._cache_dir / "stock_basic.csv"
        if _cache_is_fresh(cache_path, refresh_days=7):
            cached = _read_csv_safe(cache_path)
            if cache_only or "act_ent_type" in cached.columns:
                return cached
            # 旧缓存缺少实控人类型（央国企偏好需要）：尽力刷新一次，失败则沿用旧缓存。
            refreshed = self._query("stock_basic", exchange="", list_status="L", fields=STOCK_BASIC_FIELDS)
            if refreshed is not None and not refreshed.empty:
                if "act_ent_type" not in refreshed.columns:
                    refreshed["act_ent_type"] = ""
                refreshed.to_csv(cache_path, index=False)
                return refreshed.fillna("")
            self._warnings.append("stock_basic_refresh_failed")
            return cached
        if cache_only:
            return _read_csv_safe(cache_path)
        df = self._query("stock_basic", exchange="", list_status="L", fields=STOCK_BASIC_FIELDS)
        if df is None or df.empty:
            return pd.DataFrame()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df.fillna("")

    def _resolve_daily_basic_snapshot(self, snapshot: date, *, cache_only: bool) -> Tuple[Optional[date], pd.DataFrame]:
        """按 trade_date 探测最近的可用快照日；成功日期写入 state 缓存，重复运行不再逐日探测。"""
        daily_dir = self._cache_dir / "daily_basic"
        daily_dir.mkdir(parents=True, exist_ok=True)
        state_path = self._cache_dir / "daily_basic_resolved.json"
        state: Dict[str, str] = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                state = {}

        def _read_snapshot(day: date) -> Optional[pd.DataFrame]:
            path = daily_dir / f"daily_basic_{day:%Y%m%d}.csv"
            if not path.exists():
                return None
            df = _read_csv_safe(path)
            return df if not df.empty else None

        cached_day_text = state.get(snapshot.isoformat())
        if cached_day_text:
            try:
                cached_day = datetime.strptime(cached_day_text, "%Y-%m-%d").date()
            except ValueError:
                cached_day = None
            if cached_day is not None:
                cached_df = _read_snapshot(cached_day)
                if cached_df is not None:
                    return cached_day, cached_df

        def _remember(day: date) -> None:
            state[snapshot.isoformat()] = day.isoformat()
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        # 优先扫描本地已有快照（最多回溯 30 天）：低配额账号应尽量零调用复用缓存。
        for offset in range(0, 31):
            probe = snapshot - timedelta(days=offset)
            cached_df = _read_snapshot(probe)
            if cached_df is not None:
                _remember(probe)
                logger.info("[股息线] 使用本地快照 daily_basic=%s（未调用接口）", probe.isoformat())
                return probe, cached_df

        if cache_only:
            return None, pd.DataFrame()

        # 无本地快照才做在线探测：跳过周末、最多尝试 3 个日期；配额受限时快速放弃。
        attempted = 0
        for offset in range(0, 15):
            probe = snapshot - timedelta(days=offset)
            if probe.weekday() >= 5:
                continue
            if attempted >= 3:
                break
            attempted += 1
            df = self._query("daily_basic", trade_date=f"{probe:%Y%m%d}", fields=DAILY_BASIC_FIELDS)
            if df is None or df.empty:
                if "daily_basic" in self._gave_up_interfaces:
                    break
                continue
            df.to_csv(daily_dir / f"daily_basic_{probe:%Y%m%d}.csv", index=False)
            _remember(probe)
            return probe, df
        return None, pd.DataFrame()

    def _load_dividend_history(self, ts_code: str, *, cache_only: bool, refresh_days: int) -> Optional[pd.DataFrame]:
        cache_path = self._cache_dir / "dividend" / f"{_code_prefix(ts_code)}.csv"
        if _cache_is_fresh(cache_path, refresh_days=refresh_days):
            return _read_csv_safe(cache_path)
        if cache_only:
            return _read_csv_safe(cache_path) if cache_path.exists() else None
        df = self._query("dividend", ts_code=ts_code, fields=DIVIDEND_FIELDS)
        if df is None:
            return _read_csv_safe(cache_path) if cache_path.exists() else None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df.fillna("")

    def _load_fina_indicator(self, ts_code: str, *, cache_only: bool, refresh_days: int) -> Optional[pd.DataFrame]:
        cache_path = self._cache_dir / "fina_indicator" / f"{_code_prefix(ts_code)}.csv"
        if _cache_is_fresh(cache_path, refresh_days=refresh_days):
            return _read_csv_safe(cache_path)
        if cache_only:
            return _read_csv_safe(cache_path) if cache_path.exists() else None
        df = self._query("fina_indicator", ts_code=ts_code, fields=FINA_FIELDS)
        if df is None:
            return _read_csv_safe(cache_path) if cache_path.exists() else None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df.fillna("")

    def _load_fundamental_quality(self, ts_code: str, *, cache_only: bool) -> Optional[Dict[str, Any]]:
        """逐票业绩面指标（同花顺年度摘要：ROE / 每股经营现金流），缓存 90 天。"""
        cache_dir = self._cache_dir / "fundamentals"
        cache_path = cache_dir / f"{_code_prefix(ts_code)}.csv"
        frame: Optional[pd.DataFrame] = None
        if _cache_is_fresh(cache_path, refresh_days=FUNDAMENTAL_REFRESH_DAYS):
            frame = _read_csv_safe(cache_path)
        elif (
            cache_only
            or self._akshare_fetcher is None
            or not hasattr(self._akshare_fetcher, "stock_financial_abstract_ths")
        ):
            frame = _read_csv_safe(cache_path) if cache_path.exists() else None
        else:
            try:
                raw = self._akshare_fetcher.stock_financial_abstract_ths(
                    symbol=_code_prefix(ts_code), indicator="按年度"
                )
                if raw is not None and not raw.empty:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    raw.to_csv(cache_path, index=False)
                    frame = raw
            except Exception as exc:  # noqa: BLE001 - 单票失败不拖垮整体
                self._fundamental_failures += 1
                logger.debug("[股息线] %s 业绩面数据拉取失败：%s", ts_code, str(exc)[:100])
            if frame is None and cache_path.exists():
                frame = _read_csv_safe(cache_path)
        return self.compute_fundamental_metrics(frame)

    # ------------------------------------------------------------------ AKShare 批量分红

    def _fhps_report_dates(self, snapshot: date) -> List[str]:
        """需要拉取的报告期：近 N 年年度 + 近两年中期/三季度（覆盖中期分红）。"""
        dates: List[str] = []
        for year in range(snapshot.year - AKSHARE_FHPS_ANNUAL_YEARS, snapshot.year):
            dates.append(f"{year}1231")
        for year in range(snapshot.year - AKSHARE_FHPS_INTERIM_YEARS, snapshot.year):
            dates.append(f"{year}0630")
            dates.append(f"{year}0930")
        return dates

    def _load_fhps_frames(self, snapshot: date, *, cache_only: bool) -> Dict[str, pd.DataFrame]:
        """按报告期批量加载 AKShare 分红送配数据，返回 code(6 位) -> 该股票全部行。"""
        cache_dir = self._cache_dir / "akshare_fhps"
        cache_dir.mkdir(parents=True, exist_ok=True)
        frames: List[pd.DataFrame] = []
        for report_date in self._fhps_report_dates(snapshot):
            cache_path = cache_dir / f"fhps_{report_date}.csv"
            if _cache_is_fresh(cache_path, refresh_days=AKSHARE_FHPS_REFRESH_DAYS):
                df = _read_csv_safe(cache_path)
            elif cache_only or self._akshare_fetcher is None:
                df = _read_csv_safe(cache_path)
            else:
                try:
                    raw = self._akshare_fetcher.stock_fhps_em(date=report_date)
                except Exception as exc:  # noqa: BLE001 - 单期失败不拖垮整体
                    logger.warning("[股息线] AKShare 分红送配 %s 拉取失败：%s", report_date, str(exc)[:120])
                    self._warnings.append(f"akshare_fhps_failed:{report_date}")
                    continue
                df = raw.copy() if raw is not None else pd.DataFrame()
                if not df.empty:
                    df["报告期"] = report_date
                    df.to_csv(cache_path, index=False)
            if df is not None and not df.empty:
                if "报告期" not in df.columns:
                    df["报告期"] = report_date
                frames.append(df)
        if not frames:
            return {}
        merged = pd.concat(frames, ignore_index=True, sort=False)
        return self._split_fhps_by_code(merged)

    @staticmethod
    def _split_fhps_by_code(merged: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        if "代码" not in merged.columns:
            return {}
        df = merged.copy()
        df["__code"] = df["代码"].astype(str).str.strip().str.zfill(6)
        grouped: Dict[str, pd.DataFrame] = {}
        for code, frame in df.groupby("__code"):
            grouped[str(code)] = frame.drop(columns=["__code"]).reset_index(drop=True)
        return grouped

    @staticmethod
    def _build_fhps_inputs(code: str, rows: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[int, float]]:
        """把 AKShare 行转换为分红明细帧（每股口径）与年度 EPS 映射（年报优先）。"""
        cash_series = rows.get(AKSHARE_FHPS_CASH10_COLUMN)
        eps_series = rows.get(AKSHARE_FHPS_EPS_COLUMN)
        progress_series = rows.get(AKSHARE_FHPS_PROGRESS_COLUMN)
        ex_date_series = rows.get(AKSHARE_FHPS_EX_DATE_COLUMN)
        report_series = rows.get("报告期")
        cash_values = cash_series if cash_series is not None else pd.Series([None] * len(rows))
        eps_values = eps_series if eps_series is not None else pd.Series([None] * len(rows))
        progress_values = progress_series if progress_series is not None else pd.Series([""] * len(rows))
        ex_date_values = ex_date_series if ex_date_series is not None else pd.Series([""] * len(rows))
        report_values = report_series if report_series is not None else pd.Series([""] * len(rows))

        dividend_rows: List[Dict[str, Any]] = []
        eps_by_year: Dict[int, float] = {}
        for idx in range(len(rows)):
            report_text = str(report_values.iloc[idx] or "").strip()
            if len(report_text) != 8 or not report_text.isdigit():
                continue
            year = int(report_text[:4])
            cash10 = _to_float(cash_values.iloc[idx])
            eps = _to_float(eps_values.iloc[idx])
            progress = str(progress_values.iloc[idx] or "").strip()
            if eps is not None:
                if report_text.endswith("1231"):
                    eps_by_year[year] = eps
                else:
                    eps_by_year.setdefault(year, eps)
            if cash10 is not None and cash10 > 0:
                dividend_rows.append(
                    {
                        "ts_code": code,
                        "end_date": report_text,
                        "div_proc": progress,
                        "cash_div": cash10 / 10.0,
                        "cash_div_tax": cash10 / 10.0,
                        "ex_date": str(ex_date_values.iloc[idx] or "").strip(),
                    }
                )
        return pd.DataFrame(dividend_rows), eps_by_year

    # ------------------------------------------------------------------ 纯计算

    @staticmethod
    def filter_universe(merged: pd.DataFrame) -> pd.DataFrame:
        """沿用个人策略默认样本口径：排除 ST/PT、科创板、北交所。"""
        if merged.empty:
            return merged
        df = merged.copy()
        name = df["name"].astype(str).str.upper()
        code = df["ts_code"].astype(str).str.upper()
        exchange = df.get("exchange", pd.Series([""] * len(df))).astype(str).str.upper()
        market = df.get("market", pd.Series([""] * len(df))).astype(str)
        mask = (
            ~name.str.contains("ST", na=False)
            & ~name.str.contains("PT", na=False)
            & ~code.str.startswith(("688", "689"))
            & ~code.str.endswith(".BJ")
            & ~exchange.isin(["BSE"])
            & ~market.str.contains("北交", na=False)
        )
        return df[mask].reset_index(drop=True)

    @staticmethod
    def compute_dividend_metrics(
        dividend_df: Optional[pd.DataFrame],
        *,
        current_year: int,
        min_dividend_years: int,
    ) -> Dict[str, Any]:
        """从分红历史计算连续年数、最新年度、每股分红等字段。"""
        result: Dict[str, Any] = {
            "consecutive_dividend_years": 0,
            "dividend_years_recent": 0,
            "latest_dividend_year": None,
            "latest_year_cash_div": None,
            "dividend_history_stale": True,
            "dividend_history_lagging": True,
            "dividend_growth_5y_pct": None,
            "dividend_decline_years": None,
        }
        if dividend_df is None or dividend_df.empty:
            result["flags"] = ["dividend_history_missing"]
            return result

        df = dividend_df.copy()
        for column in ("end_date", "div_proc", "cash_div", "cash_div_tax"):
            if column not in df.columns:
                df[column] = ""
        proc = df["div_proc"].astype(str).str.strip()
        implemented = proc.str.contains(DIV_PROC_IMPLEMENTED_MARKER, na=False) & ~proc.str.contains("取消", na=False)

        end_text = df["end_date"].astype(str).str.strip()
        year_series = end_text.str.slice(0, 4)
        year_values = pd.to_numeric(year_series, errors="coerce")

        cash_tax = pd.to_numeric(df["cash_div_tax"], errors="coerce")
        cash = pd.to_numeric(df["cash_div"], errors="coerce")
        cash_per_share = cash_tax.where(cash_tax > 0, cash)

        window = df[
            implemented
            & year_values.between(current_year - DIVIDEND_HISTORY_YEARS_WINDOW, current_year)
            & (cash_per_share.fillna(0) > 0)
        ].copy()
        if window.empty:
            result["flags"] = ["no_recent_dividend"]
            return result

        window["year"] = year_values[window.index].astype(int)
        window["cash"] = cash_per_share[window.index].astype(float)
        yearly = window.groupby("year")["cash"].sum().sort_index()
        years = sorted(int(year) for year in yearly.index.tolist())

        latest_year = years[-1]
        streak = 1
        for year in range(latest_year - 1, latest_year - min_dividend_years * 3, -1):
            if year in set(years):
                streak += 1
            else:
                break

        # 分红成长性（DGI 思路）：近 N 年每股分红的年化增速与下降年数。
        recent_values = [float(value) for value in yearly.tail(DIVIDEND_GROWTH_YEARS).tolist()]
        growth_pct = None
        if len(recent_values) >= 3 and recent_values[0] > 0:
            growth_pct = round(
                ((recent_values[-1] / recent_values[0]) ** (1.0 / (len(recent_values) - 1)) - 1.0) * 100.0,
                2,
            )
        decline_years = sum(1 for prev, cur in zip(recent_values, recent_values[1:]) if cur < prev - 1e-12)

        result.update(
            {
                "consecutive_dividend_years": streak,
                "dividend_years_recent": len(years),
                "latest_dividend_year": latest_year,
                "latest_year_cash_div": round(float(yearly.loc[latest_year]), 4),
                "latest_n_years": years[-min_dividend_years:],
                "dividend_history_stale": latest_year < current_year - 2,
                "dividend_history_lagging": latest_year < current_year - 1,
                "dividend_growth_5y_pct": growth_pct,
                "dividend_decline_years": decline_years,
            }
        )
        return result

    @staticmethod
    def compute_quality_metrics(
        fina_df: Optional[pd.DataFrame],
        latest_year: Optional[int],
        *,
        eps_proxy: Optional[float] = None,
        annual_eps_map: Optional[Dict[int, float]] = None,
    ) -> Dict[str, Any]:
        """EPS/ROE/每股现金流：优先 AKShare 年度 EPS，其次 fina_indicator，最后 pe_ttm 反推。"""
        result: Dict[str, Any] = {
            "eps_annual_latest": None,
            "eps_positive_last3": None,
            "roe_annual_latest": None,
            "ocfps_annual_latest": None,
            "eps_source": "missing",
        }
        if annual_eps_map:
            years_sorted = sorted(int(year) for year in annual_eps_map.keys())
            latest_key = None
            if latest_year is not None and int(latest_year) in annual_eps_map:
                latest_key = int(latest_year)
            elif years_sorted:
                latest_key = years_sorted[-1]
            if latest_key is not None:
                result["eps_annual_latest"] = round(float(annual_eps_map[latest_key]), 4)
                last3 = years_sorted[-3:]
                result["eps_positive_last3"] = int(sum(1 for year in last3 if annual_eps_map.get(year, -1) > 0))
                result["eps_source"] = "akshare_fhps"
                return result
        df = fina_df.copy() if fina_df is not None else pd.DataFrame()
        if df.empty:
            if eps_proxy is not None and eps_proxy > 0:
                result["eps_annual_latest"] = round(float(eps_proxy), 4)
                result["eps_source"] = "pe_ttm_proxy"
            return result
        for column in ("end_date", "eps", "roe", "ocfps"):
            if column not in df.columns:
                df[column] = ""
        end_text = df["end_date"].astype(str).str.strip()
        annual = df[end_text.str.endswith("1231")].copy()
        if annual.empty:
            if eps_proxy is not None and eps_proxy > 0:
                result["eps_annual_latest"] = round(float(eps_proxy), 4)
                result["eps_source"] = "pe_ttm_proxy"
            return result
        annual["year"] = pd.to_numeric(end_text[annual.index].str.slice(0, 4), errors="coerce")
        annual["eps"] = pd.to_numeric(annual["eps"], errors="coerce")
        annual["roe"] = pd.to_numeric(annual["roe"], errors="coerce")
        annual["ocfps"] = pd.to_numeric(annual["ocfps"], errors="coerce")
        annual = annual.dropna(subset=["year"]).sort_values("year")
        annual = annual.drop_duplicates("year", keep="last")

        latest_eps = None
        if latest_year is not None:
            matched = annual[annual["year"] == float(latest_year)]
            if not matched.empty:
                latest_eps = _to_float(matched.iloc[-1]["eps"])
                result["roe_annual_latest"] = _to_float(matched.iloc[-1]["roe"])
                result["ocfps_annual_latest"] = _to_float(matched.iloc[-1]["ocfps"])
        if latest_eps is None and not annual.empty:
            latest_eps = _to_float(annual.iloc[-1]["eps"])
        result["eps_annual_latest"] = latest_eps
        result["eps_source"] = "fina_indicator"
        recent = annual.tail(3)
        result["eps_positive_last3"] = int((recent["eps"].fillna(-1) > 0).sum())
        return result

    @staticmethod
    def compute_price_trend_metrics(
        frame: Optional[pd.DataFrame], snapshot: date
    ) -> Optional[Dict[str, Any]]:
        """由日线收盘价计算近一年/近半年原始涨跌（%）。数据不足时返回 None。"""
        if frame is None or pd.DataFrame(frame).empty:
            return None
        df = pd.DataFrame(frame)
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(df["date"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
            }
        ).dropna()
        df = df[df["date"] <= pd.Timestamp(snapshot)].sort_values("date")
        bars = len(df)
        if bars < PRICE_TREND_MIN_BARS:
            return None
        close_now = float(df["close"].iloc[-1])
        if close_now <= 0:
            return None

        def _return_pct(offset: int) -> Optional[float]:
            ref = float(df["close"].iloc[-1 - offset])
            if ref <= 0:
                return None
            return round((close_now / ref - 1.0) * 100.0, 2)

        year_return = _return_pct(PRICE_TREND_YEAR_BARS)
        if year_return is None:
            return None

        window = df["close"].astype(float).tail(PRICE_TREND_YEAR_BARS + 1).reset_index(drop=True)
        returns = window.pct_change().dropna()
        volatility = None
        if len(returns) >= PRICE_TREND_HALF_YEAR_BARS:
            volatility = round(float(returns.std(ddof=1)) * math.sqrt(250.0) * 100.0, 2)
        drawdown = None
        if len(window) >= 2:
            cummax = window.cummax()
            drawdown = round(float((window / cummax - 1.0).min()) * 100.0, 2)

        # 趋势闸门（v3）：收盘价相对均线的位置（红利低波/趋势过滤思路）。
        last_close = float(window.iloc[-1])
        ma200_ratio = None
        if len(window) >= MA_LONG_BARS:
            ma200 = float(window.tail(MA_LONG_BARS).mean())
            if ma200 > 0:
                ma200_ratio = round((last_close / ma200 - 1.0) * 100.0, 2)
        ma60_ratio = None
        if len(window) >= MA_MEDIUM_BARS:
            ma60 = float(window.tail(MA_MEDIUM_BARS).mean())
            if ma60 > 0:
                ma60_ratio = round((last_close / ma60 - 1.0) * 100.0, 2)
        ma60_above_ma120 = None
        if len(window) >= MA_SLOW_BARS:
            ma120 = float(window.tail(MA_SLOW_BARS).mean())
            if ma120 > 0:
                ma60_above_ma120 = 1 if float(window.tail(MA_MEDIUM_BARS).mean()) > ma120 else 0
        return {
            "year_return_pct": year_return,
            "half_year_return_pct": _return_pct(PRICE_TREND_HALF_YEAR_BARS),
            "volatility_1y_pct": volatility,
            "max_drawdown_1y_pct": drawdown,
            "ma200_ratio_pct": ma200_ratio,
            "ma60_ratio_pct": ma60_ratio,
            "ma60_above_ma120": ma60_above_ma120,
            "bars": bars,
        }

    @staticmethod
    def compute_fundamental_metrics(frame: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
        """由 AKShare 年度财务摘要（同花顺）提取 ROE 与每股经营现金流等业绩面指标。"""
        if frame is None or pd.DataFrame(frame).empty:
            return None
        df = pd.DataFrame(frame)
        if "报告期" not in df.columns:
            return None
        roe_by_year: Dict[int, float] = {}
        ocfps_by_year: Dict[int, float] = {}
        revenue_by_year: Dict[int, float] = {}
        profit_by_year: Dict[int, float] = {}
        deduct_by_year: Dict[int, float] = {}
        for _, record in df.iterrows():
            period = str(record.get("报告期") or "").strip()
            if len(period) != 4 or not period.isdigit():
                continue
            year = int(period)
            roe = _parse_cn_number(record.get("净资产收益率"))
            if roe is not None:
                roe_by_year[year] = roe
            ocfps = _parse_cn_number(record.get("每股经营现金流"))
            if ocfps is not None:
                ocfps_by_year[year] = ocfps
            revenue = _parse_cn_number(record.get("营业总收入"))
            if revenue is not None:
                revenue_by_year[year] = revenue
            profit = _parse_cn_number(record.get("净利润"))
            if profit is not None:
                profit_by_year[year] = profit
            deduct = _parse_cn_number(record.get("扣非净利润"))
            if deduct is not None:
                deduct_by_year[year] = deduct
        if not roe_by_year and not ocfps_by_year:
            return None
        roe_values = [roe_by_year[year] for year in sorted(roe_by_year)][-5:]
        latest_ocfps = ocfps_by_year[max(ocfps_by_year)] if ocfps_by_year else None

        np_deduct_ratio = None
        if profit_by_year and deduct_by_year:
            latest_profit_year = max(profit_by_year)
            latest_profit = profit_by_year[latest_profit_year]
            latest_deduct = deduct_by_year.get(latest_profit_year)
            if latest_deduct is not None and latest_profit > 0:
                np_deduct_ratio = round(latest_deduct / latest_profit, 3)

        def _cagr(values_by_year: Dict[int, float]) -> Optional[float]:
            years_sorted = sorted(values_by_year)
            if len(years_sorted) < 2:
                return None
            first, last = years_sorted[0], years_sorted[-1]
            span = last - first
            if span < 2:
                return None
            value0, value1 = values_by_year[first], values_by_year[last]
            if value0 <= 0 or value1 <= 0:
                return None
            return round(((value1 / value0) ** (1.0 / span) - 1.0) * 100.0, 2)

        recent_revenue = {year: revenue_by_year[year] for year in sorted(revenue_by_year)[-4:]}
        recent_profit = {year: profit_by_year[year] for year in sorted(profit_by_year)[-4:]}
        return {
            "roe_5y_mean": round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
            "roe_5y_min": round(min(roe_values), 2) if roe_values else None,
            "roe_latest": round(roe_values[-1], 2) if roe_values else None,
            "ocfps_latest": round(latest_ocfps, 4) if latest_ocfps is not None else None,
            "np_deduct_ratio": np_deduct_ratio,
            "revenue_cagr_3y_pct": _cagr(recent_revenue),
            "profit_cagr_3y_pct": _cagr(recent_profit),
            "fundamental_years": max(len(roe_values), len(ocfps_by_year)),
        }

    @classmethod
    def build_candidate_row(
        cls,
        base_row: Dict[str, Any],
        dividend_metrics: Dict[str, Any],
        quality_metrics: Dict[str, Any],
        *,
        min_yield_pct: float,
        min_dividend_years: int,
        prefer_soe: bool = True,
        price_metrics: Optional[Dict[str, Any]] = None,
        fundamentals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        yield_pct = _to_float(base_row.get("dv_ttm")) or 0.0
        flags: List[str] = list(dividend_metrics.get("flags") or [])
        owner_type = _normalize_owner_type(base_row.get("act_ent_type"))
        soe_bonus = SOE_SCORE_BONUS if (prefer_soe and owner_type in OWNER_TYPE_SOE) else 0.0

        consecutive = int(dividend_metrics.get("consecutive_dividend_years") or 0)
        stale = bool(dividend_metrics.get("dividend_history_stale", True))
        lagging = bool(dividend_metrics.get("dividend_history_lagging", True))
        if lagging and not stale:
            flags.append("dividend_history_lagging")
        if stale:
            flags.append("dividend_history_stale")

        payout = None
        latest_cash = _to_float(dividend_metrics.get("latest_year_cash_div"))
        latest_eps = quality_metrics.get("eps_annual_latest")
        eps_source = str(quality_metrics.get("eps_source") or "missing")
        if latest_cash is not None and latest_eps is not None and latest_eps > 0:
            payout = round(latest_cash / latest_eps * 100.0, 2)
            if payout > 100:
                flags.append("payout_over_100")
            elif payout > 80:
                flags.append("payout_high")
        else:
            flags.append("payout_unknown")
        if eps_source == "pe_ttm_proxy":
            flags.append("eps_proxy")

        eps_positive_raw = quality_metrics.get("eps_positive_last3")
        eps_positive = int(eps_positive_raw) if eps_positive_raw is not None else None
        if latest_eps is not None and latest_eps <= 0:
            flags.append("eps_nonpositive")
        elif eps_source in ("fina_indicator", "akshare_fhps") and eps_positive is not None and eps_positive < 3:
            flags.append("eps_unstable")

        if yield_pct > 15.0:
            flags.append("yield_outlier")
        industry = str(base_row.get("industry") or "")
        if any(hint in industry for hint in CYCLICAL_INDUSTRY_HINTS):
            flags.append("cyclical_industry")

        year_return = _to_float((price_metrics or {}).get("year_return_pct"))
        half_return = _to_float((price_metrics or {}).get("half_year_return_pct"))
        total_return = None if year_return is None else round(year_return + yield_pct, 2)
        price_trend_adj = 0.0
        if total_return is not None:
            if total_return < 0:
                flags.append("total_return_1y_negative")
                price_trend_adj = PRICE_TREND_FALL_MILD_ADJ
            if total_return <= -15.0:
                flags.append("price_fall_1y")
                price_trend_adj = PRICE_TREND_FALL_MEDIUM_ADJ
            if total_return <= -30.0:
                flags.append("price_fall_severe_1y")
                price_trend_adj = PRICE_TREND_FALL_SEVERE_ADJ
            if half_return is not None and half_return <= -10.0:
                flags.append("price_fall_6m")
                price_trend_adj = max(price_trend_adj + PRICE_TREND_FALL_6M_EXTRA_ADJ, PRICE_TREND_ADJ_FLOOR)

        growth_5y = _to_float(dividend_metrics.get("dividend_growth_5y_pct"))
        decline_years = int(dividend_metrics.get("dividend_decline_years") or 0)
        dividend_growth_adj = 0.0
        if growth_5y is not None:
            if decline_years >= 2 or growth_5y <= -10.0:
                flags.append("dividend_shrinking")
                dividend_growth_adj = DIVIDEND_SHRINK_PENALTY_ADJ
            elif growth_5y >= 5.0 and decline_years == 0:
                flags.append("dividend_growing")
                dividend_growth_adj = DIVIDEND_GROWTH_BONUS_ADJ

        roe_mean = _to_float((fundamentals or {}).get("roe_5y_mean"))
        roe_min = _to_float((fundamentals or {}).get("roe_5y_min"))
        ocfps_latest = _to_float((fundamentals or {}).get("ocfps_latest"))
        if ocfps_latest is None:
            ocfps_latest = _to_float(quality_metrics.get("ocfps_annual_latest"))
        cf_coverage = None
        if ocfps_latest is not None and latest_cash is not None and latest_cash > 0:
            cf_coverage = round(ocfps_latest / latest_cash, 2)
        if roe_mean is not None and roe_mean < 8.0:
            flags.append("roe_weak")
        if roe_min is not None and roe_min < 0:
            flags.append("roe_negative_year")
        if ocfps_latest is not None and ocfps_latest <= 0:
            flags.append("ocfps_negative")
        elif cf_coverage is not None and cf_coverage < 1.0:
            flags.append("cf_below_dividend")

        volatility = _to_float((price_metrics or {}).get("volatility_1y_pct"))
        max_drawdown = _to_float((price_metrics or {}).get("max_drawdown_1y_pct"))
        low_vol_score = LOW_VOL_NEUTRAL_SCORE
        if volatility is not None:
            if volatility <= LOW_VOL_EXCELLENT_PCT:
                low_vol_score = LOW_VOL_SCORE_CAP
            elif volatility <= LOW_VOL_GOOD_PCT:
                low_vol_score = LOW_VOL_SCORE_CAP * 2.0 / 3.0
            elif volatility <= LOW_VOL_FAIR_PCT:
                low_vol_score = LOW_VOL_SCORE_CAP / 3.0
            else:
                low_vol_score = 0.0
        if max_drawdown is not None and max_drawdown <= LOW_VOL_DEEP_DRAWDOWN_PCT:
            low_vol_score = max(0.0, low_vol_score - 3.0)
            flags.append("deep_drawdown_1y")

        ma200_ratio = _to_float((price_metrics or {}).get("ma200_ratio_pct"))
        ma60_ratio = _to_float((price_metrics or {}).get("ma60_ratio_pct"))
        ma60_above_ma120 = _to_float((price_metrics or {}).get("ma60_above_ma120"))
        trend_adj = 0.0
        if ma200_ratio is not None:
            if ma200_ratio > 0:
                flags.append("trend_above_ma200")
                trend_adj = TREND_ABOVE_MA200_BONUS_ADJ
            elif ma200_ratio <= TREND_BREAKDOWN_DISTANCE_PCT:
                flags.append("trend_breakdown")
                trend_adj = TREND_BREAKDOWN_PENALTY_ADJ

        roe_latest = _to_float((fundamentals or {}).get("roe_latest"))
        np_deduct_ratio = _to_float((fundamentals or {}).get("np_deduct_ratio"))
        revenue_cagr = _to_float((fundamentals or {}).get("revenue_cagr_3y_pct"))
        profit_cagr = _to_float((fundamentals or {}).get("profit_cagr_3y_pct"))
        quality_adj = 0.0
        if np_deduct_ratio is not None and np_deduct_ratio < NP_DEDUCT_LOW_THRESHOLD:
            flags.append("np_deduct_low")
            quality_adj += NP_DEDUCT_LOW_ADJ
        if roe_latest is not None and roe_mean is not None and roe_mean > 0 and roe_latest < roe_mean * ROE_DECLINE_RATIO:
            flags.append("roe_declining")
            quality_adj += ROE_DECLINE_ADJ
        if revenue_cagr is not None and revenue_cagr <= FUNDAMENTAL_DECLINE_PCT:
            flags.append("revenue_declining")
        if profit_cagr is not None and profit_cagr <= FUNDAMENTAL_DECLINE_PCT:
            flags.append("profit_declining")
        pe_value = _to_float(base_row.get("pe_ttm"))
        if pe_value is not None and (pe_value <= 0 or pe_value > PE_EXTREME_HIGH):
            flags.append("pe_extreme")
            quality_adj += PE_EXTREME_ADJ

        if eps_source in ("fina_indicator", "akshare_fhps") and eps_positive is not None and eps_positive >= 3:
            eps_base_score = QUALITY_EPS_SCORE_CAP
        elif eps_source in ("fina_indicator", "akshare_fhps") and latest_eps is not None and latest_eps > 0:
            eps_base_score = QUALITY_EPS_SCORE_CAP * 2.0 / 3.0
        elif eps_source == "pe_ttm_proxy" and latest_eps is not None and latest_eps > 0:
            eps_base_score = QUALITY_EPS_SCORE_CAP / 2.0
        else:
            eps_base_score = 0.0

        if roe_mean is None:
            roe_score = QUALITY_ROE_NEUTRAL_SCORE
        elif roe_min is not None and roe_min < 0:
            roe_score = 0.0
        elif roe_mean >= 15.0:
            roe_score = QUALITY_ROE_SCORE_CAP
        elif roe_mean >= 10.0:
            roe_score = QUALITY_ROE_SCORE_CAP * 5.0 / 8.0
        elif roe_mean >= 8.0:
            roe_score = QUALITY_ROE_SCORE_CAP * 2.0 / 8.0
        else:
            roe_score = 0.0

        if cf_coverage is None:
            cf_score = QUALITY_CF_NEUTRAL_SCORE
        elif cf_coverage >= 1.5:
            cf_score = QUALITY_CF_SCORE_CAP
        elif cf_coverage >= 1.0:
            cf_score = QUALITY_CF_SCORE_CAP * 5.0 / 7.0
        elif cf_coverage >= 0.5:
            cf_score = QUALITY_CF_SCORE_CAP * 2.0 / 7.0
        else:
            cf_score = 0.0
        quality_score = eps_base_score + roe_score + cf_score

        yield_score = max(0.0, min(YIELD_SCORE_CAP, (yield_pct - (min_yield_pct - 1.0)) * 7.0))
        continuity_score = min(CONTINUITY_SCORE_CAP, 4.0 * consecutive)
        score = round(
            yield_score + continuity_score + quality_score + low_vol_score + soe_bonus
            + price_trend_adj + dividend_growth_adj + trend_adj + quality_adj,
            1,
        )

        qualified = consecutive >= min_dividend_years and not stale

        return {
            "ts_code": _normalize_ts_code(base_row.get("ts_code")),
            "name": base_row.get("name"),
            "industry": industry,
            "market": base_row.get("market"),
            "close": base_row.get("close"),
            "dv_ttm_pct": round(yield_pct, 2),
            "year_return_pct": year_return,
            "total_return_1y_pct": total_return,
            "half_year_return_pct": half_return,
            "volatility_1y_pct": volatility,
            "max_drawdown_1y_pct": max_drawdown,
            "ma200_ratio_pct": ma200_ratio,
            "ma60_ratio_pct": ma60_ratio,
            "ma60_above_ma120": ma60_above_ma120,
            "trend_adj": round(trend_adj, 1),
            "price_trend_adj": round(price_trend_adj, 1),
            "dividend_growth_5y_pct": growth_5y,
            "dividend_decline_years": dividend_metrics.get("dividend_decline_years"),
            "dividend_growth_adj": round(dividend_growth_adj, 1),
            "quality_adj": round(quality_adj, 1),
            "pe_ttm": base_row.get("pe_ttm"),
            "pb": base_row.get("pb"),
            "total_mv_yi": base_row.get("total_mv_yi"),
            "list_date": base_row.get("list_date"),
            "owner_type": owner_type,
            "soe_bonus": round(soe_bonus, 1),
            "consecutive_dividend_years": consecutive,
            "dividend_years_recent": int(dividend_metrics.get("dividend_years_recent") or 0),
            "latest_dividend_year": dividend_metrics.get("latest_dividend_year"),
            "latest_year_cash_div": latest_cash,
            "eps_annual_latest": latest_eps,
            "eps_source": eps_source,
            "roe_5y_mean": roe_mean,
            "roe_5y_min": roe_min,
            "roe_latest": roe_latest,
            "ocfps_latest": ocfps_latest,
            "cf_coverage": cf_coverage,
            "np_deduct_ratio": np_deduct_ratio,
            "revenue_cagr_3y_pct": revenue_cagr,
            "profit_cagr_3y_pct": profit_cagr,
            "payout_ratio_pct": payout,
            "eps_positive_last3": eps_positive,
            "dividend_score": score,
            "qualified": bool(qualified),
            "flags": ",".join(flags),
        }

    # ------------------------------------------------------------------ 主流程

    def _wait_for_daily_basic(
        self, snapshot: date, options: DividendIncomeOptions
    ) -> Tuple[Optional[date], pd.DataFrame]:
        """配额恢复窗口内轮询重试：退避等待直到 wait_minutes 用尽或快照成功。"""
        deadline = time.time() + float(options.wait_minutes) * 60.0
        if self._wait_step_seconds is not None:
            delay = max(float(self._wait_step_seconds), 0.0)
        else:
            delay = max(self._retry_wait_seconds, 60.0)
        while time.time() < deadline:
            remaining = deadline - time.time()
            sleep_seconds = max(0.0, min(delay, remaining))
            if sleep_seconds > 0:
                logger.info(
                    "[股息线] daily_basic 配额受限，等待 %.0f 秒后重试（剩余窗口 %.1f 分钟）",
                    sleep_seconds,
                    remaining / 60.0,
                )
                time.sleep(sleep_seconds)
            self._gave_up_interfaces.discard("daily_basic")
            self._rate_limit_strikes["daily_basic"] = 0
            trade_date, daily_basic = self._resolve_daily_basic_snapshot(snapshot, cache_only=False)
            if not daily_basic.empty:
                return trade_date, daily_basic
            delay = min(delay * 2.0, 900.0)
        return None, pd.DataFrame()

    def run(self, options: DividendIncomeOptions) -> Dict[str, Any]:
        snapshot = self._parse_snapshot(options.snapshot_date)
        self._cache_dir = Path(options.cache_dir)
        self._output_dir = Path(options.output_dir)
        self._warnings = []
        self._fundamental_failures = 0

        logger.info(
            "[股息线] snapshot=%s min_yield=%.1f%% min_years=%d cache_only=%s",
            snapshot.isoformat(),
            options.min_yield_pct,
            options.min_dividend_years,
            options.cache_only,
        )

        stock_basic = self._load_stock_basic(cache_only=options.cache_only)
        if stock_basic.empty:
            return self._finish(
                status="failed",
                snapshot=snapshot,
                options=options,
                rows=[],
                trade_date=None,
                message="stock_basic 数据不可用（无缓存且无法拉取）",
            )

        trade_date, daily_basic = self._resolve_daily_basic_snapshot(snapshot, cache_only=options.cache_only)
        if daily_basic.empty and not options.cache_only and options.wait_minutes > 0:
            trade_date, daily_basic = self._wait_for_daily_basic(snapshot, options)
        if daily_basic.empty:
            return self._finish(
                status="failed",
                snapshot=snapshot,
                options=options,
                rows=[],
                trade_date=None,
                message="daily_basic 快照不可用（无缓存且无法拉取；可调大 --wait-minutes 等配额恢复后重试）",
            )

        merged = self._merge_universe(stock_basic, daily_basic)
        merged = self.filter_universe(merged)
        candidates = self._prefilter_candidates(merged, options)
        logger.info("[股息线] 基础样本 %d，初筛后候选 %d（将拉取明细）", len(merged), len(candidates))

        price_trends: Dict[str, Dict[str, Any]] = {}
        if options.price_trend:
            price_trends = self._load_price_trends(candidates, snapshot, options)
        else:
            logger.info("[股息线] 价格走势校验已关闭（--no-price-trend）")

        fhps_by_code = self._load_fhps_frames(snapshot, cache_only=options.cache_only)
        if fhps_by_code:
            logger.info("[股息线] AKShare 分红送配批量数据覆盖 %d 只股票", len(fhps_by_code))
        elif not options.cache_only:
            logger.warning("[股息线] AKShare 批量分红数据不可用，逐票转 Tushare dividend 兜底（注意配额）")

        rows: List[Dict[str, Any]] = []
        for index, candidate in enumerate(candidates, start=1):
            ts_code = _normalize_ts_code(candidate.get("ts_code"))
            code = _code_prefix(ts_code)
            dividend_df: Optional[pd.DataFrame] = None
            annual_eps_map: Dict[int, float] = {}
            fhps_rows = fhps_by_code.get(code)
            if fhps_rows is not None and not fhps_rows.empty:
                dividend_df, annual_eps_map = self._build_fhps_inputs(code, fhps_rows)
            if dividend_df is None or dividend_df.empty:
                # 批量数据已加载时，缺失即视为无有效分红记录，不再逐票消耗 Tushare 配额。
                dividend_df = (
                    pd.DataFrame()
                    if fhps_by_code
                    else self._load_dividend_history(
                        ts_code, cache_only=options.cache_only, refresh_days=options.refresh_days
                    )
                )
            dividend_metrics = self.compute_dividend_metrics(
                dividend_df, current_year=snapshot.year, min_dividend_years=options.min_dividend_years
            )
            fina_df = None
            if not annual_eps_map:
                fina_df = self._load_fina_indicator(
                    ts_code, cache_only=options.cache_only, refresh_days=options.refresh_days
                )
            eps_proxy = None
            close_value = _to_float(candidate.get("close"))
            pe_value = _to_float(candidate.get("pe_ttm"))
            if close_value is not None and pe_value is not None and pe_value > 0:
                eps_proxy = close_value / pe_value
            quality_metrics = self.compute_quality_metrics(
                fina_df,
                dividend_metrics.get("latest_dividend_year"),
                eps_proxy=eps_proxy,
                annual_eps_map=annual_eps_map,
            )
            fundamentals = None
            if options.fundamentals:
                fundamentals = self._load_fundamental_quality(ts_code, cache_only=options.cache_only)
            row = self.build_candidate_row(
                candidate,
                dividend_metrics,
                quality_metrics,
                min_yield_pct=options.min_yield_pct,
                min_dividend_years=options.min_dividend_years,
                prefer_soe=options.prefer_soe,
                price_metrics=price_trends.get(ts_code),
                fundamentals=fundamentals,
            )
            rows.append(row)
            if index % 25 == 0:
                logger.info("[股息线] 明细进度 %d/%d", index, len(candidates))

        if self._fundamental_failures:
            self._warnings.append(f"fundamentals_fetch_failed:{self._fundamental_failures}")

        rows.sort(key=lambda item: (item["qualified"], item["dividend_score"], item["dv_ttm_pct"]), reverse=True)

        output_dir = self._output_dir / snapshot.isoformat()
        csv_path = output_dir / "dividend_income_candidates.csv"
        md_path = output_dir / "dividend_income_candidates.md"
        summary_path = output_dir / "summary.json"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        md_path.write_text(
            self._render_markdown(rows, options=options, trade_date=trade_date, snapshot=snapshot),
            encoding="utf-8",
        )

        qualified_rows = [row for row in rows if row["qualified"]]
        summary = self._finish(
            status="succeeded",
            snapshot=snapshot,
            options=options,
            rows=rows,
            trade_date=trade_date,
            message="ok",
            extra={
                "qualified_count": len(qualified_rows),
                "artifact_dir": str(output_dir),
                "top_qualified": [row["ts_code"] for row in qualified_rows[:20]],
            },
        )
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        logger.info(
            "[股息线] 完成：候选 %d，合格 %d，产物目录 %s",
            len(rows),
            len(qualified_rows),
            output_dir,
        )
        return summary

    # ------------------------------------------------------------------ 内部工具

    @staticmethod
    def _parse_snapshot(snapshot_date: str) -> date:
        text = str(snapshot_date or "").strip()
        if text:
            return datetime.strptime(text, "%Y-%m-%d").date()
        return date.today()

    @staticmethod
    def _merge_universe(stock_basic: pd.DataFrame, daily_basic: pd.DataFrame) -> pd.DataFrame:
        basic = stock_basic.copy()
        daily = daily_basic.copy()
        for column in ("close", "dv_ttm", "pe_ttm", "pb", "total_mv"):
            if column in daily.columns:
                daily[column] = pd.to_numeric(daily[column], errors="coerce")
        if "total_mv" in daily.columns:
            daily["total_mv_yi"] = (daily["total_mv"] / 10000.0).round(2)
        keep = [column for column in ("ts_code", "close", "dv_ttm", "pe_ttm", "pb", "total_mv_yi") if column in daily.columns]
        merged = basic.merge(daily[keep], on="ts_code", how="inner")
        merged["list_date"] = merged["list_date"].astype(str).str.strip()
        return merged

    @staticmethod
    def _prefilter_candidates(merged: pd.DataFrame, options: DividendIncomeOptions) -> List[Dict[str, Any]]:
        if merged.empty or "dv_ttm" not in merged.columns:
            return []
        df = merged.copy()
        df["dv_ttm"] = pd.to_numeric(df["dv_ttm"], errors="coerce")
        df = df[df["dv_ttm"].notna() & (df["dv_ttm"] >= float(options.min_yield_pct))]

        cutoff = datetime.strptime(options.snapshot_date, "%Y-%m-%d").date() - timedelta(days=int(options.min_listed_years * 365.25) - 90)
        list_dates = pd.to_datetime(df["list_date"].astype(str), format="%Y%m%d", errors="coerce")
        df = df[list_dates.isna() | (list_dates <= pd.Timestamp(cutoff))]

        df = df.sort_values("dv_ttm", ascending=False)
        if options.prefilter_limit and options.prefilter_limit > 0:
            df = df.head(int(options.prefilter_limit))
        return df.to_dict("records")

    def _load_price_trends(
        self, candidates: Sequence[Dict[str, Any]], snapshot: date, options: DividendIncomeOptions
    ) -> Dict[str, Dict[str, Any]]:
        """加载/补齐候选股近一年走势（复用本地行情缓存，原始价格口径）。按快照日缓存，失败不留"已知"标记。"""
        codes: List[str] = []
        for candidate in candidates:
            code = _normalize_ts_code(candidate.get("ts_code"))
            if code and code not in codes:
                codes.append(code)
        if not codes:
            return {}

        cache_dir = self._cache_dir / "price_trends"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"price_trends_{snapshot:%Y%m%d}.csv"
        cached = _read_csv_safe(cache_path)
        if not cached.empty and "ma200_ratio_pct" not in cached.columns:
            # 旧版指标缓存缺趋势/低波字段：整体重算（本地行情缓存命中，代价很小）。
            cached = pd.DataFrame()

        metrics: Dict[str, Dict[str, Any]] = {}
        known: set = set()
        cached_records: List[Dict[str, Any]] = []
        if not cached.empty and "ts_code" in cached.columns:
            for record in cached.to_dict("records"):
                code = _normalize_ts_code(record.get("ts_code"))
                if not code:
                    continue
                cached_records.append(record)
                year_return = _to_float(record.get("year_return_pct"))
                if year_return is None:
                    continue
                known.add(code)
                metrics[code] = {
                    "year_return_pct": year_return,
                    "half_year_return_pct": _to_float(record.get("half_year_return_pct")),
                    "volatility_1y_pct": _to_float(record.get("volatility_1y_pct")),
                    "max_drawdown_1y_pct": _to_float(record.get("max_drawdown_1y_pct")),
                    "ma200_ratio_pct": _to_float(record.get("ma200_ratio_pct")),
                    "ma60_ratio_pct": _to_float(record.get("ma60_ratio_pct")),
                    "ma60_above_ma120": _to_float(record.get("ma60_above_ma120")),
                    "bars": _to_float(record.get("bars")),
                }

        missing = [code for code in codes if code not in known]
        if not missing:
            logger.info("[股息线] 价格走势：候选 %d 只全部命中缓存", len(codes))
            return {code: metrics[code] for code in codes if code in metrics}
        if options.cache_only or self._price_trend_fetcher is None:
            logger.info(
                "[股息线] 价格走势：%d/%d 只命中缓存，其余跳过（cache_only=%s，fetcher=%s）",
                len(codes) - len(missing),
                len(codes),
                options.cache_only,
                "有" if self._price_trend_fetcher is not None else "无",
            )
            return {code: metrics[code] for code in codes if code in metrics}

        end_date = snapshot.isoformat()
        fetched = failed = 0
        appended: List[Dict[str, Any]] = []
        for index, code in enumerate(missing, start=1):
            result: Optional[Dict[str, Any]] = None
            source = ""
            try:
                frame = self._price_trend_fetcher.get_daily_data(
                    code, end_date=end_date, days=PRICE_TREND_REQUEST_DAYS
                )
                if isinstance(frame, tuple):
                    frame, source = frame[0], str(frame[1] or "")
                result = self.compute_price_trend_metrics(frame, snapshot)
            except Exception as exc:  # noqa: BLE001 - 单票失败不影响整体
                logger.debug("[股息线] %s 价格走势拉取失败：%s", code, str(exc)[:100])
            if result is None:
                failed += 1
                appended.append(
                    {
                        "ts_code": code,
                        "year_return_pct": "",
                        "half_year_return_pct": "",
                        "volatility_1y_pct": "",
                        "max_drawdown_1y_pct": "",
                        "ma200_ratio_pct": "",
                        "ma60_ratio_pct": "",
                        "ma60_above_ma120": "",
                        "bars": "",
                        "source": source,
                    }
                )
            else:
                fetched += 1
                metrics[code] = result
                appended.append(
                    {
                        "ts_code": code,
                        "year_return_pct": result["year_return_pct"],
                        "half_year_return_pct": result.get("half_year_return_pct"),
                        "volatility_1y_pct": result.get("volatility_1y_pct"),
                        "max_drawdown_1y_pct": result.get("max_drawdown_1y_pct"),
                        "ma200_ratio_pct": result.get("ma200_ratio_pct"),
                        "ma60_ratio_pct": result.get("ma60_ratio_pct"),
                        "ma60_above_ma120": result.get("ma60_above_ma120"),
                        "bars": result["bars"],
                        "source": source,
                    }
                )
            if index % 25 == 0:
                logger.info("[股息线] 价格走势进度 %d/%d（成功 %d，失败 %d）", index, len(missing), fetched, failed)
            if self._price_trend_interval > 0 and index < len(missing):
                time.sleep(self._price_trend_interval)

        if appended:
            merged = pd.concat([pd.DataFrame(cached_records), pd.DataFrame(appended)], ignore_index=True, sort=False)
            merged = merged.drop_duplicates(subset=["ts_code"], keep="last")
            merged.to_csv(cache_path, index=False)
        if failed:
            self._warnings.append(f"price_trend_fetch_failed:{failed}")
            logger.warning("[股息线] 价格走势：新增 %d 只，失败/数据不足 %d 只（下轮可补拉）", fetched, failed)
        else:
            logger.info("[股息线] 价格走势：新增 %d 只，全部成功", fetched)
        return {code: metrics[code] for code in codes if code in metrics}

    def _finish(
        self,
        *,
        status: str,
        snapshot: date,
        options: DividendIncomeOptions,
        rows: Sequence[Dict[str, Any]],
        trade_date: Optional[date],
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "status": status,
            "message": message,
            "snapshot_date": snapshot.isoformat(),
            "trade_date": trade_date.isoformat() if trade_date else None,
            "candidate_count": len(rows),
            "qualified_count": sum(1 for row in rows if row.get("qualified")),
            "params": {
                "min_yield_pct": options.min_yield_pct,
                "min_dividend_years": options.min_dividend_years,
                "min_listed_years": options.min_listed_years,
                "prefilter_limit": options.prefilter_limit,
                "cache_only": options.cache_only,
                "price_trend": options.price_trend,
                "fundamentals": options.fundamentals,
            },
            "warnings": list(dict.fromkeys(self._warnings)),
        }
        if extra:
            summary.update(extra)
        return summary

    @staticmethod
    def _render_markdown(
        rows: Sequence[Dict[str, Any]],
        *,
        options: DividendIncomeOptions,
        trade_date: Optional[date],
        snapshot: date,
    ) -> str:
        qualified = [row for row in rows if row.get("qualified")]
        qualified_top = qualified[:30]
        lines: List[str] = []
        lines.append("# 股息线候选（防守线 MVP）")
        lines.append("")
        lines.append(f"- 快照日期：{snapshot.isoformat()}；行情快照交易日：{trade_date.isoformat() if trade_date else '未知'}")
        lines.append(
            f"- 参数：股息率（TTM）≥ {options.min_yield_pct:.1f}%，连续分红 ≥ {options.min_dividend_years} 年，"
            f"上市 ≥ {options.min_listed_years} 年；仅做池子与指标展示，不构成买卖建议。"
        )
        lines.append(f"- 结果：候选 {len(rows)} 只，其中合格 {len(qualified)} 只。")
        lines.append("")
        lines.append("## 合格池（Top 30）")
        lines.append("")
        if qualified_top:
            lines.append("| 代码 | 名称 | 类型 | 行业 | 股息率% | 近1年% | 含息1年% | 波动% | 距200日线% | 股息5年增速% | 连续年数 | 派现率% | 综合分 | 标注 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for row in qualified_top:
                year_return = row.get("year_return_pct")
                total_return = row.get("total_return_1y_pct")
                volatility = row.get("volatility_1y_pct")
                growth = row.get("dividend_growth_5y_pct")
                ma200_ratio = row.get("ma200_ratio_pct")
                lines.append(
                    f"| {row['ts_code']} | {row['name']} | {row.get('owner_type') or '—'} | {row['industry']} | {row['dv_ttm_pct']} | "
                    f"{year_return if year_return is not None else '—'} | "
                    f"{total_return if total_return is not None else '—'} | "
                    f"{volatility if volatility is not None else '—'} | "
                    f"{ma200_ratio if ma200_ratio is not None else '—'} | "
                    f"{growth if growth is not None else '—'} | "
                    f"{row['consecutive_dividend_years']} | "
                    f"{row['payout_ratio_pct'] if row['payout_ratio_pct'] is not None else '—'} | {row['dividend_score']} | "
                    f"{row['flags'] or '—'} |"
                )
        else:
            lines.append("（本期无合格样本）")
        lines.append("")
        lines.append("## 方法与边界")
        lines.append("")
        lines.append("- 数据：Tushare `daily_basic`（股息率快照）+ AKShare `stock_fhps_em`（分红送配按报告期批量，含每股收益）；Tushare `dividend` 仅作兜底。")
        lines.append("- EPS 来源优先级：AKShare 每股收益（`eps_source=akshare_fhps`）> fina_indicator > `close / pe_ttm` 反推（`eps_source=pe_ttm_proxy`）；后两档派现率仅供粗看。")
        lines.append("- 合格口径：连续分红年数 ≥ 门槛，且最新分红年度不早于当前年度前 2 年（避免用历史高息的名义）。")
        lines.append("- 央国企偏好：`owner_type` 为央企/地方国企的候选加 3 分软偏好（`soe_bonus`），可用 `--no-prefer-soe` 关闭。")
        lines.append("- 评分体系 v2（基础满分 92）：股息率 ≤35 + 连续性 ≤20（4 分/年）+ 盈利质量 ≤25（EPS 基础 ≤10 + ROE ≤8 + 现金流覆盖 ≤7）+ 低波动 ≤12；调整项：央国企 +3、分红成长 -3/+2、价格走势 ≤0。")
        lines.append("- 低波动（红利低波思路）：`volatility_1y_pct` 近一年年化波动率、`max_drawdown_1y_pct` 最大回撤；深度回撤（≤-30%）额外扣 3 分并标 `deep_drawdown_1y`。")
        lines.append("- 业绩面（AKShare 同花顺年度摘要，缓存 90 天，`--no-fundamentals` 关闭）：`roe_5y_mean`/`roe_5y_min`、现金流覆盖 `cf_coverage`（每股经营现金流/每股分红）；标注 `roe_weak`/`roe_negative_year`/`ocfps_negative`/`cf_below_dividend`。")
        lines.append("- 分红成长（DGI 思路）：`dividend_growth_5y_pct` 近 5 年每股分红年化增速、`dividend_decline_years` 下降年数；增速 ≥5% 且无下降记 `dividend_growing`（+2），下降年数 ≥2 或增速 ≤-10% 记 `dividend_shrinking`（-3）。")
        lines.append("- 趋势闸门（v3）：`ma200_ratio_pct`（收盘距 200 日线）+ `ma60_ratio_pct`；站上 200 日线记 `trend_above_ma200`（+4），低于 200 日线超 5% 记 `trend_breakdown`（-6）。")
        lines.append("- 质量强化（v3，同花顺年度摘要）：`np_deduct_ratio` 扣非/净利 <0.7 记 `np_deduct_low`（-3）；最新年 ROE 低于 5 年均值 70% 记 `roe_declining`（-2）；PE≤0 或 >30 记 `pe_extreme`（-2）；营收/净利 3 年 CAGR ≤-10% 分别记 `revenue_declining`/`profit_declining`（仅标注）。")
        lines.append("- 价格走势校验（默认开启）：复用个人策略同一行情链路（DataFetcherManager，本地 `data/cache/history` 磁盘缓存优先、缺口自动增量补齐）；")
        lines.append("  `year_return_pct`（近1年）/ `half_year_return_pct`（近半年）为原始涨跌；`total_return_1y_pct ≈ 原始涨跌 + 股息率（TTM）` 近似含息净回报（1 年窗口内的一次性/送转分红、除权与实际税务可能有偏差）；")
        lines.append("  含息净回报 < 0 记 `total_return_1y_negative`（-3 分）、≤ -15% 记 `price_fall_1y`（-8 分）、≤ -30% 记 `price_fall_severe_1y`（-12 分）、近半年原始跌幅 ≥10% 记 `price_fall_6m`（再 -2 分）；可用 `--no-price-trend` 关闭。")
        lines.append("- 标注说明：`payout_over_100` 派现率超 100%；`yield_outlier` 股息率异常高（>15%，注意一次性分红或价格异动）；")
        lines.append("  `dividend_history_stale` 分红历史过期；`cyclical_industry` 周期行业（景气顶部高息需自行复核）。")
        lines.append("- 复权/除权、红利税与再平衡时点需自行把握；本表只做筛选，不做交易建议。")
        lines.append("")
        return "\n".join(lines)
