# -*- coding: utf-8 -*-
"""Tests for the short-term watch line (graph setup ∩ catalyst)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.services.short_term_watch_service import (
    ShortTermWatchService,
    build_short_term_watch,
    load_catalyst_watchlist,
    render_short_term_watch_markdown,
    write_short_term_watch_artifacts,
)


def _item(
    code: str,
    name: str,
    *,
    matched: tuple = ("hundred_day_high",),
    passed: bool = True,
    breakout: float = 14.0,
    priority: float = 80.0,
    builtin: str = "",
    business_summary: str = "",
    today: float = 1.0,
) -> dict:
    return {
        "code": code,
        "name": name,
        "matched_strategy_ids": list(matched),
        "pure_chart_quality_passed": passed,
        "breakout_quality_score": breakout,
        "priority_score": priority,
        "cycle_catalyst_type": "storage_price_recovery" if builtin else "",
        "cycle_catalyst_label": builtin,
        "today_change_pct": today,
        "triggered_strategies": list(matched),
        "business_summary": business_summary,
    }


def test_build_short_term_watch_requires_graph_and_ranks_by_score() -> None:
    items = [
        _item("000001", "无图有催化", matched=(), builtin="存储涨价/复苏 + 业绩兑现"),
        _item("000002", "有图无催化", matched=("daily_slow_rise",), priority=60.0),
        _item(
            "000003",
            "图+内置催化",
            matched=("hundred_day_high", "daily_slow_rise"),
            builtin="存储涨价/复苏 + 业绩兑现",
            priority=90.0,
        ),
        _item(
            "000004",
            "图+清单催化",
            matched=("long_base_release",),
            business_summary="AI算力供应链",
            priority=70.0,
        ),
    ]
    watchlist = [{"label": "AI算力", "keywords": ["ai算力"], "weight": 1.5}]

    result = build_short_term_watch(items, watchlist=watchlist, top_n=10, graph_only_top=5)

    codes = [row["code"] for row in result["graph_catalyst"]]
    # 000004：图形分 82 + 30×1.5 = 127；000003：图形分 88 + 30×1.0 = 118
    assert codes == ["000004", "000003"]
    assert "清单:AI算力" in result["graph_catalyst"][0]["catalysts"]
    assert "内置:存储涨价/复苏 + 业绩兑现" in result["graph_catalyst"][1]["catalysts"]
    assert result["graph_only_total"] == 1
    assert result["graph_only"][0]["code"] == "000002"
    assert result["graph_catalyst_total"] == 2


def test_build_short_term_watch_respects_top_n_limits() -> None:
    items = [
        _item("000003", "图+催化A", builtin="催化A", priority=90.0),
        _item("000004", "图+催化B", builtin="催化B", priority=80.0),
    ]
    result = build_short_term_watch(items, watchlist=[], top_n=1)
    assert [row["code"] for row in result["graph_catalyst"]] == ["000003"]
    assert result["graph_catalyst_total"] == 2


def test_load_catalyst_watchlist_skips_expired_and_invalid_entries(tmp_path) -> None:
    watchlist_path = tmp_path / "catalyst_watchlist.json"
    watchlist_path.write_text(
        json.dumps(
            {
                "items": [
                    {"label": "有效条目", "keywords": ["abc"], "weight": 1.5},
                    {"label": "过期条目", "keywords": ["def"], "expires_on": "2026-01-01"},
                    {"label": "无关键词", "weight": 2.0},
                    {"keywords": ["ghi"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    entries = load_catalyst_watchlist(watchlist_path, today=date(2026, 9, 25))

    assert [entry["label"] for entry in entries] == ["有效条目"]
    assert entries[0]["weight"] == 1.5


def test_load_catalyst_watchlist_tolerates_missing_file(tmp_path) -> None:
    assert load_catalyst_watchlist(tmp_path / "missing.json") == []


class _FakeMatrixService:
    def get_matrix(self, *, snapshot_date=None):
        return {
            "snapshot_date": "2026-09-25",
            "source_csv_path": "data/manual_runs/demo/review/fast_review_stock_overview.csv",
            "market_regime": "defensive",
            "items": [
                _item(
                    "002463",
                    "沪电股份",
                    matched=("hundred_day_high", "long_base_release"),
                    builtin="AI算力链景气 + 业绩兑现",
                    business_summary="PCB，偏AI算力供应链",
                    priority=88.0,
                ),
                _item("002025", "航天电器", matched=("hundred_day_high",), priority=72.0),
            ],
        }


def test_service_facade_and_markdown_render(tmp_path) -> None:
    service = ShortTermWatchService(
        matrix_service=_FakeMatrixService(),
        watchlist_path=tmp_path / "missing.json",
    )

    result = service.build(snapshot_date="latest", top_n=5)

    assert result["snapshot_date"] == "2026-09-25"
    assert result["market_regime"] == "defensive"
    assert result["graph_catalyst_total"] == 1
    assert result["graph_catalyst"][0]["code"] == "002463"

    markdown = render_short_term_watch_markdown(result)
    assert "短线观察（图形 ∩ 催化）" in markdown
    assert "沪电股份" in markdown

    artifacts = write_short_term_watch_artifacts(result, output_root=tmp_path / "out")
    assert Path(artifacts["csv_path"]).exists()
    assert Path(artifacts["markdown_path"]).exists()
    csv_text = Path(artifacts["csv_path"]).read_text(encoding="utf-8-sig")
    assert "002463" in csv_text
