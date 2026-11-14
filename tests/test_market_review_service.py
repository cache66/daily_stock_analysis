# -*- coding: utf-8 -*-
"""market_review_service 的离线单测：结构、口径与降级标记。"""

from __future__ import annotations

import pytest

from src.services import market_review_service as review


def test_board_ladder_buckets_and_labels() -> None:
    limit_up = [
        {"code": "000001", "name": "A", "boards": 4},
        {"code": "000002", "name": "B", "boards": 2},
        {"code": "000003", "name": "C", "boards": 2},
        {"code": "000004", "name": "D", "boards": None},
    ]
    ladder = review._board_ladder(limit_up)
    assert [item["boards"] for item in ladder] == [4, 2, 1]
    assert ladder[0]["label"] == "4连板"
    assert ladder[-1]["label"] == "首板"
    assert ladder[1]["count"] == 2


def test_industry_distribution_shares_sum_to_one() -> None:
    items = [{"industry": "银行"}, {"industry": "银行"}, {"industry": ""}]
    rows = review._industry_distribution(items, "industry")
    assert sum(row["count"] for row in rows) == 3
    assert rows[0]["industry"] == "银行"
    assert rows[0]["count"] == 2


def test_match_board_uses_longest_prefix() -> None:
    quotes = {"汽车零部件": {"change_pct": 2.5}, "汽车": {"change_pct": 1.0}}
    matched = review._match_board("汽车零部", quotes)
    assert matched is not None
    assert matched["change_pct"] == 2.5
    assert review._match_board("不存在行业", quotes) is None


def test_build_market_review_falls_back_to_local_approx(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(review, "CACHE_DIR", tmp_path)

    def boom(as_of: str):
        raise RuntimeError("网络不可用")

    monkeypatch.setattr(review, "_fetch_pools", boom)
    monkeypatch.setattr(
        review,
        "_local_approx",
        lambda as_of: {
            "status": "limited",
            "reason": "东方财富涨停池不可用，改用本地日线近似",
            "limit_up": [{"code": "000001", "change_pct": 9.98}],
            "broken": [{"code": "000002", "change_pct": 3.1}],
            "limit_down": [],
        },
    )
    monkeypatch.setattr(review, "_board_quotes", lambda as_of: {})

    payload = review.build_market_review("2026-01-05")

    assert payload["status"] == "limited"
    assert payload["source"] == "local_daily_approx"
    assert payload["summary"]["limit_up"] == 1
    assert payload["summary"]["broken"] == 1
    assert payload["summary"]["seal_amount_total_yi"] is None  # 近似口径不编造封单
    assert payload["summary"]["seal_rate"] == pytest.approx(0.5)
    assert any("日线近似" in item for item in payload["limitations"])
    # 近似口径不落盘：避免把估算当成真实复盘缓存
    assert not list(tmp_path.glob("*.json"))


def test_build_market_review_uses_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(review, "CACHE_DIR", tmp_path)
    calls = {"n": 0}

    def fake_fetch(as_of: str):
        calls["n"] += 1
        return {
            "limit_up": [{"code": "000001", "name": "A", "boards": 1, "seal_amount": 3e8}],
            "broken": [],
            "limit_down": [],
        }

    monkeypatch.setattr(review, "_fetch_pools", fake_fetch)
    monkeypatch.setattr(review, "_board_quotes", lambda as_of: {})

    first = review.build_market_review("2026-01-06")
    second = review.build_market_review("2026-01-06")

    assert calls["n"] == 1
    assert first["summary"]["limit_up"] == second["summary"]["limit_up"] == 1
    assert first["summary"]["seal_amount_total_yi"] == pytest.approx(3.0)
    assert (tmp_path / "review_20260106.json").is_file()

    review.build_market_review("2026-01-06", refresh=True)
    assert calls["n"] == 2
