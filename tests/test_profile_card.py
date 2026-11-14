# -*- coding: utf-8 -*-
"""固定信息卡（P0）合同测试：状态机、时点隔离、缺数不伪造。"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pandas as pd
import pytest

from src.services import profile_card_service as pcs
from src.storage import DatabaseManager


def _write_csv(path: Path, rows, header) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把卡片依赖的所有数据源指向临时目录，并准备一个只含一只票的库。"""
    basic = _write_csv(
        tmp_path / "basic.csv",
        [["600100.SH", "600100", "测试股份", "元件", "上海", "主板"],
         ["600200.SH", "600200", "同行业乙", "元件", "上海", "主板"]],
        ["ts_code", "code", "name", "industry", "area", "market"],
    )
    monkeypatch.setattr(pcs, "BASIC_CSV", basic)
    monkeypatch.setattr(pcs, "GDFX_DIR", tmp_path / "gdfx")
    monkeypatch.setattr(pcs, "PROFILE_CACHE_DIR", tmp_path / "profile")
    monkeypatch.setattr(pcs, "DIVIDEND_BASIC_DIR", tmp_path / "dividend_basic")
    pcs._basic_map.cache_clear()
    pcs._market_metrics.cache_clear()

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "card.db"))
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    days = [f"2026-08-{d:02d}" for d in range(1, 31)] + [f"2026-09-{d:02d}" for d in range(1, 26)]
    rows = []
    for idx, day in enumerate(days):
        close = 10 + idx * 0.1
        rows.append(dict(date=day, open=close, high=close * 1.02, low=close * 0.98,
                         close=close, volume=10000, amount=close * 10000))
    db.save_daily_data(pd.DataFrame(rows), "600100", "baostock_qfq_rebuild")
    rows2 = []
    for idx, day in enumerate(days):
        close = 20 - idx * 0.05
        rows2.append(dict(date=day, open=close, high=close * 1.02, low=close * 0.98,
                          close=close, volume=10000, amount=close * 10000))
    db.save_daily_data(pd.DataFrame(rows2), "600200", "baostock_qfq_rebuild")
    yield tmp_path
    DatabaseManager.reset_instance()


def _seed_holders(base: Path, *, ann_date: str = "2026-08-20") -> None:
    gdfx = base / "gdfx"
    # 7 期，户数持续下降 → 分位与连续下降期数都应可算
    counts = [90000, 85000, 80000, 76000, 72000, 70000, 68000]
    ends = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
            "2025-12-31", "2026-03-31", "2026-06-30"]
    for end, count in zip(ends, counts):
        stamp = end.replace("-", "")
        _write_csv(
            gdfx / f"gdhs_{stamp}.csv",
            [["600100", "测试股份", 10.0, 1.0, count, count + 4000, -4000, -4.5, 5.0,
              end, "2026-03-31", 1200000, 1200, 1e10, 1e9, ann_date]],
            ["代码", "名称", "最新价", "涨跌幅", "股东户数-本次", "股东户数-上次",
             "股东户数-增减", "股东户数-增减比例", "区间涨跌幅", "股东户数统计截止日-本次",
             "股东户数统计截止日-上次", "户均持股市值", "户均持股数量", "总市值", "总股本", "公告日期"],
        )
        _write_csv(
            gdfx / f"free_analyse_{stamp}.csv",
            [["1", "某基金", "证券投资基金", "600100", "测试股份", end, 1_000_000, 0, 0, "不变", 1e7, ann_date],
             ["2", "某社保", "全国社保基金", "600100", "测试股份", end, 500_000, 0, 0, "不变", 5e6, ann_date]],
            ["序号", "股东名称", "股东类型", "股票代码", "股票简称", "报告期", "期末持股-数量",
             "期末持股-数量变化", "期末持股-数量变化比例", "期末持股-持股变动", "期末持股-流通市值", "公告日"],
        )


def _seed_valuation(base: Path, *, trade_date: str = "20260925") -> None:
    _write_csv(
        base / "profile" / f"daily_basic_{trade_date}.csv",
        [["600100.SH", trade_date, 13.4, 2.1, 2.2, 30.0, 25.0, 3.1, 5.0, 1.2, 1e9, 8e8, 1.3e10, 1.0e10]],
        ["ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f", "pe", "pe_ttm",
         "pb", "ps_ttm", "dv_ttm", "total_share", "float_share", "total_mv", "circ_mv"],
    )


def _seed_earnings(base: Path, *, end_date: str, ann_date: str = "", yoy: float = 21.5) -> None:
    _write_csv(
        base / "profile" / "fina_indicator_600100.csv",
        [["600100.SH", ann_date, end_date, yoy, 18.0, 12.5, 33.0, 1.8, 45.0]],
        ["ts_code", "ann_date", "end_date", "or_yoy", "netprofit_yoy", "roe",
         "grossprofit_margin", "ocfps", "debt_to_assets"],
    )


def test_card_dimensions_ok(sandbox):
    _seed_holders(sandbox)
    _seed_valuation(sandbox)
    _seed_earnings(sandbox, end_date="20260630", ann_date="20260820")

    card = pcs.build_profile_card("600100", as_of="2026-09-25")
    sections = card["sections"]

    assert sections["basic"]["status"] == "limited"  # 上市日期/概念板块来源未接入
    assert "list_date" in (sections["basic"].get("missing_fields") or [])
    assert sections["basic"]["data"]["industry"] == "元件"
    assert sections["valuation"]["status"] == "ok"
    assert sections["valuation"]["data"]["market_cap"] == pytest.approx(1_300_000.0, rel=1e-6)
    assert sections["valuation"]["data"]["turnover"] == pytest.approx(2.1)
    assert sections["earnings"]["status"] == "ok"
    assert sections["earnings"]["report_period"] == "2026-06-30"
    assert sections["earnings"]["data"]["revenue_yoy"] == pytest.approx(21.5)

    holders = sections["holders"]
    assert holders["status"] == "ok"
    assert holders["data"]["holders"] == 68000
    assert holders["data"]["drop_periods"] == 6
    assert holders["data"]["holders_percentile"] is not None

    inst = sections["institution"]
    assert inst["status"] == "ok"
    assert inst["data"]["fund_count"] == 1
    assert inst["data"]["social_security_count"] == 1
    assert inst["data"]["inst_ratio"] == pytest.approx(0.15, rel=1e-6)
    assert sections["price"]["data"]["ret20"] is not None


def test_missing_sources_are_pending_not_fabricated(sandbox):
    card = pcs.build_profile_card("600100", as_of="2026-09-25")
    sections = card["sections"]
    assert sections["valuation"]["status"] == "pending"
    assert sections["earnings"]["status"] == "pending"
    assert sections["holders"]["status"] == "pending"
    assert sections["institution"]["status"] == "pending"
    for name in ("valuation", "earnings", "holders", "institution"):
        assert "data" not in sections[name] or sections[name]["data"] == {}
        assert sections[name].get("reason")


def test_holder_disclosure_after_as_of_is_excluded(sandbox):
    # 公告日 2026-09-30 晚于截止日 2026-09-25 → 当时不可知，不能进卡片
    _seed_holders(sandbox, ann_date="2026-09-30")
    card = pcs.build_profile_card("600100", as_of="2026-09-25")
    assert card["sections"]["holders"]["status"] == "pending"
    assert card["sections"]["institution"]["status"] == "pending"


def test_earnings_without_ann_date_uses_conservative_lag(sandbox):
    # 报告期 2026-06-30 + 90 天 = 2026-09-28 > 2026-09-25 → 视为尚未可知
    _seed_earnings(sandbox, end_date="20260630", ann_date="")
    late = pcs.build_profile_card("600100", as_of="2026-09-25")
    assert late["sections"]["earnings"]["status"] == "pending"

    early = pcs.build_profile_card("600100", as_of="2026-09-30")
    assert early["sections"]["earnings"]["status"] == "ok"
    assert early["sections"]["earnings"]["available_rule"].startswith("report_period+90d")


def test_industry_peer_sample_marks_limited(sandbox):
    card = pcs.build_profile_card("600100", as_of="2026-09-25")
    sector = card["sections"]["sector"]
    assert sector["status"] == "limited"  # 同业仅 2 只
    assert sector["data"]["peer_count"] == 2


def test_unknown_code_is_missing_and_cli_exit_code(sandbox, capsys):
    card = pcs.build_profile_card("999999", as_of="2026-09-25")
    assert card["sections"]["basic"]["status"] == "missing"

    from scripts import stock_card

    rc = stock_card.main(["999999", "--as-of", "2026-09-25"])
    assert rc == 1
    assert "999999" in capsys.readouterr().out
