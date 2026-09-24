# -*- coding: utf-8 -*-
"""股息线服务单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.services.dividend_income_service import (
    DividendIncomeOptions,
    DividendIncomeService,
)


class FakeProvider:
    """按接口返回固定数据的假 Tushare provider，并记录调用次数。"""

    def __init__(
        self,
        *,
        stock_basic: pd.DataFrame,
        daily_basic_by_date: dict,
        dividend_by_code: dict,
        fina_by_code: dict,
        fina_permission_denied: bool = False,
    ) -> None:
        self.stock_basic = stock_basic
        self.daily_basic_by_date = daily_basic_by_date
        self.dividend_by_code = dividend_by_code
        self.fina_by_code = fina_by_code
        self.fina_permission_denied = fina_permission_denied
        self.calls = []

    def query(self, interface: str, **kwargs) -> pd.DataFrame:
        self.calls.append((interface, dict(kwargs)))
        if interface == "stock_basic":
            return self.stock_basic.copy()
        if interface == "daily_basic":
            df = self.daily_basic_by_date.get(kwargs.get("trade_date"))
            return df.copy() if df is not None else pd.DataFrame()
        if interface == "dividend":
            df = self.dividend_by_code.get(kwargs.get("ts_code"))
            return df.copy() if df is not None else pd.DataFrame()
        if interface == "fina_indicator":
            if self.fina_permission_denied:
                raise RuntimeError("抱歉，您没有接口(fina_indicator)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。")
            df = self.fina_by_code.get(kwargs.get("ts_code"))
            return df.copy() if df is not None else pd.DataFrame()
        return pd.DataFrame()

    def count(self, interface: str) -> int:
        return sum(1 for name, _ in self.calls if name == interface)


def _stock_basic() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts_code": "600001.SH", "symbol": "600001", "name": "稳定红利A", "industry": "银行", "market": "主板", "list_date": "20000101", "exchange": "SSE"},
            {"ts_code": "600002.SH", "symbol": "600002", "name": "缺口B", "industry": "电力", "market": "主板", "list_date": "20050101", "exchange": "SSE"},
            {"ts_code": "600003.SH", "symbol": "600003", "name": "过期C", "industry": "煤炭", "market": "主板", "list_date": "20010101", "exchange": "SSE"},
            {"ts_code": "600005.SH", "symbol": "600005", "name": "高派现E", "industry": "高速公路", "market": "主板", "list_date": "20100101", "exchange": "SSE"},
            {"ts_code": "688004.SH", "symbol": "688004", "name": "科创D", "industry": "半导体", "market": "科创板", "list_date": "20190101", "exchange": "SSE"},
            {"ts_code": "600006.SH", "symbol": "600006", "name": "ST老壳", "industry": "综合", "market": "主板", "list_date": "20000101", "exchange": "SSE"},
            {"ts_code": "600007.SH", "symbol": "600007", "name": "低息G", "industry": "食品", "market": "主板", "list_date": "20110101", "exchange": "SSE"},
            {"ts_code": "830001.BJ", "symbol": "830001", "name": "北交H", "industry": "机械", "market": "北交所", "list_date": "20160101", "exchange": "BSE"},
        ]
    )


def _daily_basic() -> pd.DataFrame:
    rows = [
        ("600001.SH", 5.5),
        ("600002.SH", 6.0),
        ("600003.SH", 7.0),
        ("600005.SH", 6.5),
        ("688004.SH", 9.0),
        ("600006.SH", 8.0),
        ("600007.SH", 2.0),
        ("830001.BJ", 10.0),
    ]
    return pd.DataFrame(
        [
            {
                "ts_code": code,
                "close": 10.0,
                "dv_ratio": yield_pct,
                "dv_ttm": yield_pct,
                "pe_ttm": 8.0,
                "pb": 0.9,
                "total_mv": 5_000_000.0,
            }
            for code, yield_pct in rows
        ]
    )


def _div_rows(code: str, years, cash: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": code,
                "end_date": f"{year}1231",
                "ann_date": f"{year + 1}0410",
                "div_proc": "实施",
                "cash_div": str(cash),
                "cash_div_tax": str(cash),
                "record_date": "",
                "ex_date": "",
                "pay_date": "",
                "imp_ann_date": "",
            }
            for year in years
        ]
    )


def _fina_rows(code: str, years, eps: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": code,
                "end_date": f"{year}1231",
                "eps": str(eps),
                "roe": "10.0",
                "ocfps": "1.2",
            }
            for year in years
        ]
    )


def _build_provider() -> FakeProvider:
    return FakeProvider(
        stock_basic=_stock_basic(),
        daily_basic_by_date={"20260923": _daily_basic()},
        dividend_by_code={
            "600001.SH": _div_rows("600001.SH", range(2020, 2026), cash=0.55),
            "600002.SH": _div_rows("600002.SH", [2021, 2023, 2024, 2025], cash=0.6),
            "600003.SH": _div_rows("600003.SH", range(2019, 2023), cash=0.7),
            "600005.SH": _div_rows("600005.SH", range(2020, 2026), cash=1.0),
        },
        fina_by_code={
            "600001.SH": _fina_rows("600001.SH", range(2020, 2026), eps=1.0),
            "600002.SH": _fina_rows("600002.SH", range(2020, 2026), eps=1.0),
            "600003.SH": _fina_rows("600003.SH", range(2019, 2023), eps=1.0),
            "600005.SH": _fina_rows("600005.SH", range(2020, 2026), eps=0.2),
        },
    )


class FakeAkshare:
    """按报告期返回分红送配表（列名对齐 akshare stock_fhps_em）。"""

    def __init__(self, frames: dict) -> None:
        self.frames = frames
        self.calls = []

    def stock_fhps_em(self, date: str) -> pd.DataFrame:
        self.calls.append(date)
        df = self.frames.get(date)
        return df.copy() if df is not None else pd.DataFrame()


def _fhps_row(code: str, name: str, cash10: float, eps: float) -> dict:
    return {
        "代码": code,
        "名称": name,
        "现金分红-现金分红比例": cash10,
        "现金分红-股息率": 0.05,
        "每股收益": eps,
        "方案进度": "实施分配",
        "除权除息日": "",
    }


def _build_fhps_frames() -> dict:
    def frame(rows):
        return pd.DataFrame([_fhps_row(*row) for row in rows])

    return {
        "20191231": frame([("600003", "过期C", 7.0, 1.0)]),
        "20201231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600003", "过期C", 7.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
        "20211231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600002", "缺口B", 6.0, 1.0), ("600003", "过期C", 7.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
        "20221231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600003", "过期C", 7.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
        "20231231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600002", "缺口B", 6.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
        "20241231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600002", "缺口B", 6.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
        "20251231": frame([("600001", "稳定红利A", 5.5, 1.0), ("600002", "缺口B", 6.0, 1.0), ("600005", "高派现E", 10.0, 0.2)]),
    }


class RateLimitedDailyBasicProvider(FakeProvider):
    """daily_basic 始终频率超限，用于验证快速放弃逻辑。"""

    def query(self, interface: str, **kwargs) -> pd.DataFrame:
        if interface == "daily_basic":
            self.calls.append((interface, dict(kwargs)))
            raise RuntimeError("抱歉，您访问接口(daily_basic)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。")
        return super().query(interface, **kwargs)


class FlakyDailyBasicProvider(FakeProvider):
    """daily_basic 前若干次调用频率超限，之后恢复正常（模拟小时级配额恢复）。"""

    def __init__(self, *, fail_times: int = 4, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self.daily_basic_attempts = 0

    def query(self, interface: str, **kwargs) -> pd.DataFrame:
        if interface == "daily_basic":
            self.daily_basic_attempts += 1
            if self.daily_basic_attempts <= self.fail_times:
                self.calls.append((interface, dict(kwargs)))
                raise RuntimeError("抱歉，您访问接口(daily_basic)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。")
        return super().query(interface, **kwargs)


def _options(tmp_path: Path, **overrides) -> DividendIncomeOptions:
    base = dict(
        snapshot_date="2026-09-24",
        min_yield_pct=4.0,
        min_dividend_years=5,
        min_listed_years=5,
        prefilter_limit=300,
        refresh_days=30,
        cache_only=False,
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
    )
    base.update(overrides)
    return DividendIncomeOptions(**base)


def test_full_run_outputs(tmp_path: Path) -> None:
    provider = _build_provider()
    service = DividendIncomeService(provider=provider, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")

    summary = service.run(_options(tmp_path))

    assert summary["status"] == "succeeded"
    assert summary["trade_date"] == "2026-09-23"
    assert summary["candidate_count"] == 4  # 688/ST/低息/北交 已被过滤
    assert summary["qualified_count"] == 2

    out_dir = tmp_path / "out" / "2026-09-24"
    csv_path = out_dir / "dividend_income_candidates.csv"
    md_path = out_dir / "dividend_income_candidates.md"
    summary_path = out_dir / "summary.json"
    assert csv_path.exists() and md_path.exists() and summary_path.exists()

    df = pd.read_csv(csv_path)
    assert len(df) == 4
    assert df.iloc[0]["ts_code"] == "600005.SH"  # 高息 + 高派现分数最高
    qualified = set(df[df["qualified"] == True]["ts_code"])  # noqa: E712
    assert qualified == {"600001.SH", "600005.SH"}

    rows = {row["ts_code"]: row for row in df.to_dict("records")}
    assert "payout_over_100" in rows["600005.SH"]["flags"]
    assert "dividend_history_stale" in rows["600003.SH"]["flags"]
    assert rows["600002.SH"]["qualified"] == False  # noqa: E712 - 有缺口

    md_text = md_path.read_text(encoding="utf-8")
    assert "合格池" in md_text and "不构成买卖建议" in md_text

    stored = json.loads(summary_path.read_text(encoding="utf-8"))
    assert stored["qualified_count"] == 2


def test_cache_reuse_and_trade_date_probing(tmp_path: Path) -> None:
    provider = _build_provider()
    service = DividendIncomeService(provider=provider, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")
    service.run(_options(tmp_path))

    # 09-24 无数据 + 09-23 命中 = 2 次探测；明细 4 票 × 2 接口
    assert provider.count("daily_basic") == 2
    assert provider.count("dividend") == 4
    assert provider.count("fina_indicator") == 4

    second_provider = _build_provider()
    second_service = DividendIncomeService(
        provider=second_provider, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out"
    )
    second_service.run(_options(tmp_path))

    assert second_provider.count("daily_basic") == 0  # state + 快照缓存命中
    assert second_provider.count("dividend") == 0  # 明细缓存在有效期内
    assert second_provider.count("fina_indicator") == 0


def test_continuity_computation() -> None:
    service = DividendIncomeService(provider=None)

    contiguous = service.compute_dividend_metrics(
        _div_rows("x", range(2021, 2026)), current_year=2026, min_dividend_years=5
    )
    assert contiguous["consecutive_dividend_years"] == 5
    assert contiguous["latest_dividend_year"] == 2025
    assert contiguous["dividend_history_stale"] is False

    gap = service.compute_dividend_metrics(
        _div_rows("x", [2020, 2022, 2023, 2024, 2025]), current_year=2026, min_dividend_years=5
    )
    assert gap["consecutive_dividend_years"] == 4

    stale = service.compute_dividend_metrics(
        _div_rows("x", [2019, 2020, 2021]), current_year=2026, min_dividend_years=5
    )
    assert stale["dividend_history_stale"] is True

    missing = service.compute_dividend_metrics(pd.DataFrame(), current_year=2026, min_dividend_years=5)
    assert missing["consecutive_dividend_years"] == 0
    assert "dividend_history_missing" in missing["flags"]


def test_universe_filter() -> None:
    filtered = DividendIncomeService.filter_universe(_stock_basic())
    codes = set(filtered["ts_code"])
    assert "688004.SH" not in codes  # 科创板
    assert "830001.BJ" not in codes  # 北交所
    assert "600006.SH" not in codes  # ST
    assert {"600001.SH", "600002.SH", "600003.SH", "600005.SH", "600007.SH"} <= codes


def test_fina_permission_denied_falls_back_to_pe_proxy(tmp_path: Path) -> None:
    provider = _build_provider()
    provider.fina_permission_denied = True
    service = DividendIncomeService(provider=provider, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")

    summary = service.run(_options(tmp_path))

    assert summary["status"] == "succeeded"
    assert provider.count("fina_indicator") == 1  # 首次被拒后不再逐票重试
    df = pd.read_csv(tmp_path / "out" / "2026-09-24" / "dividend_income_candidates.csv")
    assert set(df["eps_source"]) == {"pe_ttm_proxy"}
    row = df[df["ts_code"] == "600001.SH"].iloc[0]
    # 600001: close 10 / pe_ttm 8 = 1.25 EPS → 派现率 0.55 / 1.25 = 44%
    assert row["payout_ratio_pct"] == 44.0
    assert "eps_proxy" in row["flags"]


def test_payout_unknown_and_scoring() -> None:
    row = DividendIncomeService.build_candidate_row(
        {
            "ts_code": "600008.SH",
            "name": "无财务X",
            "industry": "水务",
            "market": "主板",
            "close": 5.0,
            "dv_ttm": 5.0,
            "pe_ttm": "",
            "pb": "",
            "total_mv_yi": 100.0,
            "list_date": "20000101",
        },
        {
            "consecutive_dividend_years": 6,
            "dividend_history_stale": False,
            "dividend_history_lagging": False,
            "latest_dividend_year": 2025,
            "latest_year_cash_div": 0.5,
            "dividend_years_recent": 6,
        },
        {"eps_annual_latest": None, "eps_positive_last3": 0, "ocfps_annual_latest": None},
        min_yield_pct=4.0,
        min_dividend_years=5,
    )
    assert row["qualified"] is True
    assert "payout_unknown" in row["flags"]
    # 45 分上限内的股息率得分 (5 - 3) * 9 = 18 + 连续性 30 + 质量 0
    assert row["dividend_score"] == 48.0


def test_akshare_bulk_path(tmp_path: Path) -> None:
    provider = _build_provider()
    akshare = FakeAkshare(_build_fhps_frames())
    service = DividendIncomeService(
        provider=provider, akshare_fetcher=akshare, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out"
    )

    summary = service.run(_options(tmp_path))

    assert summary["status"] == "succeeded"
    assert provider.count("dividend") == 0  # 分红明细全部命中 AKShare 批量数据
    assert provider.count("fina_indicator") == 0  # EPS 来自批量表
    assert len(akshare.calls) == 15  # 11 个年度 + 4 个中期报告期

    df = pd.read_csv(tmp_path / "out" / "2026-09-24" / "dividend_income_candidates.csv")
    assert set(df["eps_source"]) == {"akshare_fhps"}
    assert df.iloc[0]["ts_code"] == "600005.SH"  # 股息率 6.5% + 连续 6 年，得分最高
    rows = {row["ts_code"]: row for row in df.to_dict("records")}
    assert "payout_over_100" in rows["600005.SH"]["flags"]  # 每股派 1.0 元 / EPS 0.2
    assert bool(rows["600003.SH"]["qualified"]) is False  # 最新分红年度停留在 2022，过期
    assert summary["qualified_count"] == 2


def test_daily_basic_give_up_fails_fast(tmp_path: Path) -> None:
    provider = RateLimitedDailyBasicProvider(
        stock_basic=_stock_basic(), daily_basic_by_date={}, dividend_by_code={}, fina_by_code={}
    )
    service = DividendIncomeService(
        provider=provider, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out", retry_wait_seconds=0
    )

    summary = service.run(_options(tmp_path))

    assert summary["status"] == "failed"
    assert "daily_basic_rate_limit_give_up" in summary["warnings"]
    assert provider.count("daily_basic") == 2  # 首次 + 重试一次后放弃，不再长循环


def test_snapshot_cache_scan_without_api(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    daily_dir = cache_dir / "daily_basic"
    daily_dir.mkdir(parents=True)
    _daily_basic().to_csv(daily_dir / "daily_basic_20260923.csv", index=False)

    provider = _build_provider()
    provider.daily_basic_by_date = {}
    service = DividendIncomeService(
        provider=provider, cache_dir=cache_dir, output_dir=tmp_path / "out", retry_wait_seconds=0
    )

    summary = service.run(_options(tmp_path))

    assert summary["status"] == "succeeded"
    assert summary["trade_date"] == "2026-09-23"
    assert provider.count("daily_basic") == 0  # 本地快照扫描直接命中，零调用
    assert summary["qualified_count"] == 2


def test_wait_minutes_recovers_from_quota(tmp_path: Path) -> None:
    base = _build_provider()
    provider = FlakyDailyBasicProvider(
        fail_times=4,
        stock_basic=base.stock_basic,
        daily_basic_by_date=base.daily_basic_by_date,
        dividend_by_code=base.dividend_by_code,
        fina_by_code=base.fina_by_code,
    )
    service = DividendIncomeService(
        provider=provider,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        retry_wait_seconds=0,
        wait_step_seconds=0.05,
    )

    summary = service.run(_options(tmp_path, wait_minutes=1))

    assert summary["status"] == "succeeded"
    assert summary["trade_date"] == "2026-09-23"
    assert provider.daily_basic_attempts >= 5  # 经历多轮退避重试后拿到快照
