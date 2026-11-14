# -*- coding: utf-8 -*-
"""broken_followup_service 单测：事件分组、后续走势、判定标签与缓存（不联网）。"""

from __future__ import annotations

import pytest

from src.services import broken_followup_service as fu


def _stub(monkeypatch, tmp_path, *, dates, pools, flows):
    monkeypatch.setattr(fu, "CACHE_DIR", tmp_path / "followup")
    monkeypatch.setattr(fu, "FLOW_DIR", tmp_path / "flow")
    monkeypatch.setattr(fu, "_trade_dates", lambda as_of, count: list(reversed(dates)))
    monkeypatch.setattr(fu, "_flow_history", lambda code, as_of, refresh=False: flows[code])
    # 均额查询走数据库，测试里固定给 20 日均额，便于验证量比与打分
    monkeypatch.setattr(fu, "_avg_amount", lambda codes, day, window=20: {code: 1e9 for code in codes})
    # 收盘兜底（新浪）在测试里置空，避免联网
    monkeypatch.setattr(fu, "_daily_closes", lambda code, as_of, refresh=False: {})

    import src.services.market_review_service as review

    monkeypatch.setattr(review, "build_market_review", lambda day, refresh=False: {"broken": pools.get(day, [])})


def test_groups_by_day_and_sorted_by_score(monkeypatch, tmp_path) -> None:
    dates = ["2026-11-06", "2026-11-09", "2026-11-10"]
    pools = {
        "2026-11-09": [
            {"code": "600001", "name": "小单", "amount": 1e8, "break_count": 1},
            {"code": "600002", "name": "大单", "amount": 9e8, "break_count": 2},
        ],
        "2026-11-06": [{"code": "600003", "name": "旧", "amount": 6e8, "break_count": 1}],
        "2026-11-10": [],
    }
    flows = {
        "600001": {
            "2026-11-09": {"close": 10.0, "main": 1e8, "mid": -1e7, "small": -2e7},
            "2026-11-10": {"close": 10.5},
        },
        "600002": {
            "2026-11-09": {"close": 20.0, "main": -2e8, "mid": 5e7, "small": 1e8},
            "2026-11-10": {"close": 19.0},
        },
        "600003": {
            "2026-11-06": {"close": 8.0, "main": 3e8, "mid": -1e8, "small": -2e8},
            "2026-11-09": {"close": 8.4},
            "2026-11-10": {"close": 8.2},
        },
    }
    _stub(monkeypatch, tmp_path, dates=dates, pools=pools, flows=flows)

    payload = fu.build_broken_followup("2026-11-10", days=3, per_day=5, with_ticks=False)

    assert [group["break_date"] for group in payload["groups"]] == ["2026-11-09", "2026-11-06"]
    latest_day = payload["groups"][0]
    scores = [row["score"] for row in latest_day["rows"]]
    assert scores == sorted(scores, reverse=True)  # 组内按分数降序
    assert all(0 <= value <= 100 for value in scores)
    assert latest_day["big_amount"] == 1


def test_followup_returns_and_labels(monkeypatch, tmp_path) -> None:
    dates = ["2026-11-06", "2026-11-09", "2026-11-10"]
    pools = {
        "2026-11-06": [
            {"code": "600001", "name": "承接修复", "amount": 20e8, "break_count": 1},
            {"code": "600002", "name": "出货不修复", "amount": 15e8, "break_count": 2},
        ]
    }
    flows = {
        "600001": {
            "2026-11-06": {"close": 10.0, "main": 2e8, "mid": -1e8, "small": -1e8},
            "2026-11-09": {"close": 10.5, "change_pct": 5.0},
            "2026-11-10": {"close": 10.2, "change_pct": -2.9},
        },
        "600002": {
            "2026-11-06": {"close": 10.0, "main": -3e8, "mid": 2e8, "small": 1e8},
            "2026-11-09": {"close": 9.6, "change_pct": -4.0},
            "2026-11-10": {"close": 9.2, "change_pct": -4.2},
        },
    }
    _stub(monkeypatch, tmp_path, dates=dates, pools=pools, flows=flows)

    payload = fu.build_broken_followup("2026-11-10", days=3, per_day=5, with_ticks=False)
    rows = {row["code"]: row for row in payload["groups"][0]["rows"]}

    keep = rows["600001"]
    assert keep["followup"]["days"] == ["2026-11-09", "2026-11-10"]
    assert keep["followup"]["t1_ret"] == pytest.approx(0.05)
    assert keep["followup"]["latest_ret"] == pytest.approx(0.02)
    assert keep["followup"]["recovered"] is True
    assert keep["followup"]["held"] is True
    assert keep["label"] == "大单承接·已站稳"

    dump = rows["600002"]
    assert dump["followup"]["latest_ret"] == pytest.approx(-0.08)
    assert dump["followup"]["recovered"] is False
    assert dump["label"] == "大单出货·未修复"

    assert payload["summary"]["supported"] == 1
    assert payload["summary"]["dumped"] == 1
    assert payload["summary"]["recovered"] == 1


def test_today_events_are_pending(monkeypatch, tmp_path) -> None:
    dates = ["2026-11-09", "2026-11-10"]
    pools = {"2026-11-10": [{"code": "600009", "name": "今天炸", "amount": 30e8, "break_count": 3}]}
    flows = {"600009": {"2026-11-10": {"close": 12.0, "main": 5e7, "mid": -2e7, "small": -3e7}}}
    _stub(monkeypatch, tmp_path, dates=dates, pools=pools, flows=flows)

    payload = fu.build_broken_followup("2026-11-10", days=2, per_day=5, with_ticks=False)
    row = payload["groups"][0]["rows"][0]

    assert row["followup"]["days"] == []
    assert row["label"] == "大单承接·待观察"
    assert payload["summary"]["pending"] == 1
    assert payload["summary"]["recovered"] == 0


def test_cache_hit_skips_network(monkeypatch, tmp_path) -> None:
    dates = ["2026-11-09", "2026-11-10"]
    pools = {"2026-11-09": [{"code": "600001", "name": "X", "amount": 6e8, "break_count": 1}]}
    flows = {"600001": {"2026-11-09": {"close": 10.0, "main": 1e8}, "2026-11-10": {"close": 10.1}}}
    _stub(monkeypatch, tmp_path, dates=dates, pools=pools, flows=flows)

    calls = {"n": 0}

    def fetch(code, as_of, refresh=False):
        calls["n"] += 1
        return flows[code]

    monkeypatch.setattr(fu, "_flow_history", fetch)

    fu.build_broken_followup("2026-11-10", days=2, per_day=5, with_ticks=False)
    fu.build_broken_followup("2026-11-10", days=2, per_day=5, with_ticks=False)
    assert calls["n"] == 1

    fu.build_broken_followup("2026-11-10", days=2, per_day=5, with_ticks=False, refresh=True)
    assert calls["n"] == 2
