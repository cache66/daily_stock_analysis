# -*- coding: utf-8 -*-
"""股息线（防守线）选股服务：高股息 + 可持续分红筛选 MVP。

设计口径（见 docs/个人策略文档/个人策略精简方案.md §3.1）：

- 数据源：
  - Tushare ``stock_basic``：全量股票基础信息（缓存 7 天）；
  - Tushare ``daily_basic``：按 trade_date 快照一次拉全市场，取 ``dv_ttm`` 股息率（TTM，%）；
    优先复用本地已缓存快照，低配额账号（1 次/分钟~1 次/小时级）尽量零调用；
  - AKShare ``stock_fhps_em``（主）：按报告期批量拉全市场分红送配（免费、无配额），
    提供分红比例（每 10 股派息）与每股收益，缓存 30 天；
  - Tushare ``dividend``（兜底）：仅当某只候选股在 AKShare 批量数据中缺失时逐票补拉；
  - EPS 无 ``fina_indicator`` 权限时，用 AKShare 每股收益或 ``close / pe_ttm`` 反推。
- 默认样本口径（沿用个人策略既有约定）：排除 ST（含 PT）、排除科创板、排除北交所、保留创业板；
- 默认入池：连续分红 ≥5 个年度、最新分红年度不早于前 2 个年度、股息率（TTM）≥ 4%；
- 输出：``data/dividend_income/<snapshot>/dividend_income_candidates.csv`` / ``.md`` / ``summary.json``。

本模块只做“池子与指标展示”，不构成任何买卖建议。
"""

from __future__ import annotations

import json
import logging
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
STOCK_BASIC_FIELDS = "ts_code,symbol,name,industry,market,list_date,exchange"
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

# 综合分权重（满分 90）：股息率 45 + 连续性 30 + 盈利质量 15。
YIELD_SCORE_CAP = 45.0
CONTINUITY_SCORE_CAP = 30.0
QUALITY_SCORE_CAP = 15.0


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
    ) -> None:
        self._provider = provider
        self._akshare_fetcher = akshare_fetcher
        self._cache_dir = Path(cache_dir)
        self._output_dir = Path(output_dir)
        self._rate_limit_per_minute = int(rate_limit_per_minute)
        self._retry_wait_seconds = float(retry_wait_seconds)
        self._wait_step_seconds = wait_step_seconds
        self._warnings: List[str] = []
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
            return _read_csv_safe(cache_path)
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
        report_series = rows.get("报告期")
        cash_values = cash_series if cash_series is not None else pd.Series([None] * len(rows))
        eps_values = eps_series if eps_series is not None else pd.Series([None] * len(rows))
        progress_values = progress_series if progress_series is not None else pd.Series([""] * len(rows))
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

        result.update(
            {
                "consecutive_dividend_years": streak,
                "dividend_years_recent": len(years),
                "latest_dividend_year": latest_year,
                "latest_year_cash_div": round(float(yearly.loc[latest_year]), 4),
                "latest_n_years": years[-min_dividend_years:],
                "dividend_history_stale": latest_year < current_year - 2,
                "dividend_history_lagging": latest_year < current_year - 1,
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

    @classmethod
    def build_candidate_row(
        cls,
        base_row: Dict[str, Any],
        dividend_metrics: Dict[str, Any],
        quality_metrics: Dict[str, Any],
        *,
        min_yield_pct: float,
        min_dividend_years: int,
    ) -> Dict[str, Any]:
        yield_pct = _to_float(base_row.get("dv_ttm")) or 0.0
        flags: List[str] = list(dividend_metrics.get("flags") or [])

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

        yield_score = max(0.0, min(YIELD_SCORE_CAP, (yield_pct - (min_yield_pct - 1.0)) * 9.0))
        continuity_score = min(CONTINUITY_SCORE_CAP, 6.0 * consecutive)
        if (
            eps_source in ("fina_indicator", "akshare_fhps")
            and eps_positive is not None
            and eps_positive >= 3
            and (quality_metrics.get("ocfps_annual_latest") or 0) > 0
        ):
            quality_score = QUALITY_SCORE_CAP
        elif eps_source in ("fina_indicator", "akshare_fhps") and latest_eps is not None and latest_eps > 0:
            quality_score = QUALITY_SCORE_CAP * 2.0 / 3.0
        elif eps_source == "pe_ttm_proxy" and latest_eps is not None and latest_eps > 0:
            quality_score = QUALITY_SCORE_CAP / 2.0
        else:
            quality_score = 0.0
        score = round(yield_score + continuity_score + quality_score, 1)

        qualified = consecutive >= min_dividend_years and not stale

        return {
            "ts_code": _normalize_ts_code(base_row.get("ts_code")),
            "name": base_row.get("name"),
            "industry": industry,
            "market": base_row.get("market"),
            "close": base_row.get("close"),
            "dv_ttm_pct": round(yield_pct, 2),
            "pe_ttm": base_row.get("pe_ttm"),
            "pb": base_row.get("pb"),
            "total_mv_yi": base_row.get("total_mv_yi"),
            "list_date": base_row.get("list_date"),
            "consecutive_dividend_years": consecutive,
            "dividend_years_recent": int(dividend_metrics.get("dividend_years_recent") or 0),
            "latest_dividend_year": dividend_metrics.get("latest_dividend_year"),
            "latest_year_cash_div": latest_cash,
            "eps_annual_latest": latest_eps,
            "eps_source": eps_source,
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
            row = self.build_candidate_row(
                candidate,
                dividend_metrics,
                quality_metrics,
                min_yield_pct=options.min_yield_pct,
                min_dividend_years=options.min_dividend_years,
            )
            rows.append(row)
            if index % 25 == 0:
                logger.info("[股息线] 明细进度 %d/%d", index, len(candidates))

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
            lines.append("| 代码 | 名称 | 行业 | 股息率% | 连续年数 | 最新年度 | 每股分红 | 派现率% | 综合分 | 标注 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for row in qualified_top:
                lines.append(
                    f"| {row['ts_code']} | {row['name']} | {row['industry']} | {row['dv_ttm_pct']} | "
                    f"{row['consecutive_dividend_years']} | {row['latest_dividend_year']} | {row['latest_year_cash_div']} | "
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
        lines.append("- 标注说明：`payout_over_100` 派现率超 100%；`yield_outlier` 股息率异常高（>15%，注意一次性分红或价格异动）；")
        lines.append("  `dividend_history_stale` 分红历史过期；`cyclical_industry` 周期行业（景气顶部高息需自行复核）。")
        lines.append("- 复权/除权、红利税与再平衡时点需自行把握；本表只做筛选，不做交易建议。")
        lines.append("")
        return "\n".join(lines)
