# -*- coding: utf-8 -*-
"""同花顺备用资金流与兜底判定的单测（不联网）。"""

from __future__ import annotations

import pytest

from src.services import broken_followup_service as fu
from src.services import ths_fund_flow


def test_parse_amount_units() -> None:
    assert ths_fund_flow._parse_amount("1.15亿") == pytest.approx(1.15e8)
    assert ths_fund_flow._parse_amount("-6855.04万") == pytest.approx(-6855.04e4)
    assert ths_fund_flow._parse_amount("0") == 0.0
    assert ths_fund_flow._parse_amount("--") is None
    assert ths_fund_flow._parse_amount(None) is None


def test_label_uses_neutral_wording_for_ths() -> None:
    assert fu._label(1e7, None, False, False, False, "ths") == "资金净买·待观察"
    assert fu._label(-1e7, None, False, False, False, "ths") == "资金净卖·待观察"
    # 东财有分档时仍用「大单承接/出货」；held 决定是否已站稳
    assert fu._label(1e7, -1e6, True, True, True, "eastmoney") == "大单承接·已站稳"
    assert fu._label(-1e7, 1e6, False, False, True, "eastmoney") == "大单出货·未修复"
    assert fu._label(None, None, False, False, False, "none") == "资金未知·待观察"


def test_ths_fallback_fills_today_funds(monkeypatch, tmp_path) -> None:
    """东财资金流为空、当日炸板时，用同花顺净额兜底并标 source=ths。"""
    dates = ["2026-11-10", "2026-11-11"]
    pools = {"2026-11-11": [{"code": "600001", "name": "甲", "amount": 10e8, "break_count": 1}]}
    monkeypatch.setattr(fu, "CACHE_DIR", tmp_path / "followup")
    monkeypatch.setattr(fu, "FLOW_DIR", tmp_path / "flow")
    monkeypatch.setattr(fu, "CLOSES_DIR", tmp_path / "closes")
    monkeypatch.setattr(fu, "_trade_dates", lambda as_of, count: list(reversed(dates)))
    monkeypatch.setattr(fu, "_flow_history", lambda code, as_of, refresh=False: {})
    monkeypatch.setattr(fu, "_daily_closes", lambda code, as_of, refresh=False: {"2026-11-11": 11.0})
    monkeypatch.setattr(fu, "_avg_amount", lambda codes, day, window=20: {code: 8e8 for code in codes})

    import src.services.market_review_service as review

    monkeypatch.setattr(review, "build_market_review", lambda day, refresh=False: {"broken": pools.get(day, [])})

    import src.services.break_window_service as bw

    monkeypatch.setattr(bw, "batch_analyze", lambda events, as_of: {})

    import src.services.ths_fund_flow as ths

    monkeypatch.setattr(ths, "fetch_daily_net", lambda as_of, refresh=False: {"600001": {"net": 2e7, "amount": 10e8}})

    payload = fu.build_broken_followup("2026-11-11", days=2, per_day=5, with_ticks=False)
    row = payload["groups"][0]["rows"][0]

    assert row["funds"]["source"] == "ths"
    assert row["funds"]["main"] == pytest.approx(2e7)
    assert row["funds"]["main_ratio"] == pytest.approx(0.02)
    assert row["label"] == "资金净买·待观察"
