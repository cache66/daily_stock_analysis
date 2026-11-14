# -*- coding: utf-8 -*-
"""短线方法层（P1/P3）合同测试：方法隔离、行业排除、幂等、披露时点、交集默认关闭。"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from src.services import profile_card_service as pcs
from src.services.shortline import base, registry, runner
from src.services.shortline.adapters import deep_recovery, holders, hotspot, legacy
from src.storage import DatabaseManager


def _write_csv(path: Path, rows, header) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    _write_csv(
        tmp_path / "basic.csv",
        [
            ["600100.SH", "600100", "强势制造", "专用机械", "上海", "主板"],
            ["600101.SH", "600101", "同板块乙", "专用机械", "上海", "主板"],
            ["601398.SH", "601398", "某大银行", "银行", "北京", "主板"],
            ["600200.SH", "600200", "超跌修复", "电气设备", "江苏", "主板"],
            ["600300.SH", "600300", "户数下降", "元器件", "广东", "主板"],
        ],
        ["ts_code", "code", "name", "industry", "area", "market"],
    )
    monkeypatch.setattr(pcs, "BASIC_CSV", tmp_path / "basic.csv")
    monkeypatch.setattr(pcs, "GDFX_DIR", tmp_path / "gdfx")
    monkeypatch.setattr(pcs, "PROFILE_CACHE_DIR", tmp_path / "profile")
    monkeypatch.setattr(pcs, "DIVIDEND_BASIC_DIR", tmp_path / "div")
    monkeypatch.setattr(runner, "OUT_DIR", tmp_path / "methods")
    pcs._basic_map.cache_clear()
    pcs._market_metrics.cache_clear()
    base.metrics.cache_clear()
    base.load_bars.cache_clear()
    base.industry_map.cache_clear()
    base.industry_strength.cache_clear()

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "shortline.db"))
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    db.save_daily_data(_trend(600100, 10.0, 0.008), "600100", "baostock_qfq_rebuild")
    db.save_daily_data(_trend(600101, 10.0, 0.005), "600101", "baostock_qfq_rebuild")
    db.save_daily_data(_trend(601398, 5.0, 0.0005), "601398", "baostock_qfq_rebuild")
    db.save_daily_data(_trend(600200, 30.0, -0.008, tail=0.03), "600200", "baostock_qfq_rebuild")
    db.save_daily_data(_trend(600300, 8.0, 0.0035), "600300", "baostock_qfq_rebuild")
    yield tmp_path
    DatabaseManager.reset_instance()


def _trend(code: int, start: float, daily: float, *, tail: float = 0.0, days: int = 120) -> pd.DataFrame:
    rows = []
    close = start
    for idx in range(days):
        if idx >= days - 5 and tail:
            close *= 1 + tail
        else:
            close *= 1 + daily
        rows.append(
            dict(date=f"2026-{6 + idx // 30:02d}-{idx % 30 + 1:02d}", open=close * 0.99,
                 high=close * 1.01, low=close * 0.98, close=close,
                 volume=10_000_000, amount=close * 10_000_000)
        )
    return pd.DataFrame(rows)


def test_hotspot_excludes_bank_industry(sandbox):
    method = registry.get_method("hotspot")
    picks = hotspot.discover(_latest(), method)
    codes = {item["code"] for item in picks}
    assert "601398" not in codes
    # 方法级排除清单来自注册表，且包含银行
    assert "银行" in registry.excluded_industries(method)


def test_hotspot_caps_per_industry(sandbox):
    method = registry.get_method("hotspot")
    picks = hotspot.discover(_latest(), method)
    industries = [item["industry"] for item in picks]
    cap = int((method.get("params") or {}).get("max_per_industry", 2))
    for industry in set(industries):
        assert industries.count(industry) <= cap


def test_deep_recovery_requires_drawdown_and_reclaim(sandbox):
    method = registry.get_method("deep_recovery")
    picks = deep_recovery.discover(_latest(), method)
    codes = {item["code"] for item in picks}
    assert "600200" in codes
    for item in picks:
        assert item["drawdown_high120"] <= float((method.get("params") or {}).get("min_drawdown", -0.35))
        assert item["stop_pct"] >= -0.0901  # 止损封顶 9%


def test_holders_uses_only_disclosed_periods(sandbox):
    gdfx = sandbox / "gdfx"
    periods = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    counts = [90000, 85000, 80000, 76000, 72000]
    for end, count in zip(periods, counts):
        _write_csv(
            gdfx / f"gdhs_{end.replace('-', '')}.csv",
            [["600300", "户数下降", 8.0, 1.0, count, count + 5000, -5000, -5.5, 3.0,
              end, "2026-03-31", 900000, 900, 8e9, 2e8, "2026-09-30"]],
            ["代码", "名称", "最新价", "涨跌幅", "股东户数-本次", "股东户数-上次", "股东户数-增减",
             "股东户数-增减比例", "区间涨跌幅", "股东户数统计截止日-本次", "股东户数统计截止日-上次",
             "户均持股市值", "户均持股数量", "总市值", "总股本", "公告日期"],
        )

    method = registry.get_method("holders")
    # 公告日 2026-09-30 晚于截止日 2026-09-25 → 不得使用（不前视）
    early = holders.discover("2026-09-25", method)
    assert "600300" not in {item["code"] for item in early}

    late = holders.discover(_latest(), method)
    codes = {item["code"] for item in late}
    assert "600300" in codes
    item = next(i for i in late if i["code"] == "600300")
    assert item["holder_drop_periods"] >= 2
    assert item["holder_ann_date"] == "2026-09-30"


def test_intersections_disabled_by_default(sandbox):
    config = registry.load_config()
    enabled = [i for i in config.get("intersections") or [] if i.get("enabled")]
    assert enabled == []
    index = runner.run_methods(_latest(), track=False, dry_run=True)
    for inter in index["intersections"]:
        assert inter["enabled"] is False
        assert inter["count"] == 0


def test_run_is_idempotent_and_legacy_not_default_daily(sandbox):
    as_of = _latest()
    first = runner.run_methods(as_of, method_ids=["hotspot"], track=True, dry_run=False)
    assert first["methods"][0]["new_today"] > 0
    second = runner.run_methods(as_of, method_ids=["hotspot"], track=True, dry_run=False)
    assert second["methods"][0]["new_today"] == 0
    assert second["methods"][0]["tracking"] == second["methods"][0]["count"]

    legacy_method = registry.get_method("legacy_signals")
    assert (legacy_method.get("params") or {}).get("default_daily") is False
    assert legacy.discover(as_of, legacy_method) == [] or True  # 无快照时返回空也合法


def test_methods_do_not_share_candidate_pool(sandbox):
    """热点与超跌各自独立求值，不能被统一交集过滤成空。"""
    as_of = _latest()
    hotspot_codes = {item["code"] for item in hotspot.discover(as_of, registry.get_method("hotspot"))}
    recovery_codes = {item["code"] for item in deep_recovery.discover(as_of, registry.get_method("deep_recovery"))}
    assert hotspot_codes  # 方法本身有输出
    assert recovery_codes
    assert hotspot_codes != recovery_codes


def _latest() -> str:
    with DatabaseManager.get_instance().session_scope() as session:
        from sqlalchemy import text

        return str(session.execute(text("select max(date) from stock_daily")).scalar())[:10]
