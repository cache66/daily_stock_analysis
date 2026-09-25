# -*- coding: utf-8 -*-
"""Tests for the catalyst/theme digest service."""

from __future__ import annotations

from pathlib import Path

from src.services.catalyst_digest_service import (
    CatalystDigestService,
    append_history,
    build_catalyst_digest,
    collect_item_tags,
    load_history,
    render_catalyst_digest_markdown,
    split_digest_tags,
)


def _item(
    code: str,
    name: str,
    *,
    matched: tuple = ("hundred_day_high",),
    cycle_label: str = "",
    theme_label: str = "",
    industry: str = "",
    cause_tags: str = "",
    priority: float = 80.0,
) -> dict:
    return {
        "code": code,
        "name": name,
        "matched_strategy_ids": list(matched),
        "priority_score": priority,
        "cycle_catalyst_label": cycle_label,
        "theme_label": theme_label,
        "preferred_industry_label": industry,
        "cause_tags_zh": cause_tags,
    }


def test_split_digest_tags_and_collect() -> None:
    assert split_digest_tags("业绩/涨价/供需") == ["业绩", "涨价", "供需"]
    assert split_digest_tags("AI算力、存储") == ["AI算力", "存储"]
    assert split_digest_tags("景气 + 业绩兑现") == ["景气", "业绩兑现"]
    assert split_digest_tags("") == []

    tags = collect_item_tags(
        _item("000001", "测试", cycle_label="AI算力链景气 + 业绩兑现", cause_tags="业绩/涨价")
    )
    assert ("AI算力链景气", "催化") in tags
    assert ("业绩兑现", "催化") in tags
    assert ("业绩", "标签") in tags
    assert ("涨价", "标签") in tags


def test_build_catalyst_digest_aggregates_and_sorts() -> None:
    items = [
        _item("002463", "沪电股份", matched=("hundred_day_high",), cycle_label="AI算力链", priority=90.0),
        _item("300476", "胜宏科技", matched=("daily_slow_rise",), theme_label="AI算力链", priority=70.0),
        _item("000504", "南华生物", matched=(), theme_label="AI算力链", priority=60.0),
        _item("002025", "航天电器", matched=("long_base_release",), industry="军工", priority=50.0),
    ]

    result = build_catalyst_digest(items, history=[], top_n=10)

    assert result["total_candidates"] == 4
    assert result["graph_candidates"] == 3
    themes = {row["theme"]: row for row in result["themes"]}
    assert themes["AI算力链"]["count"] == 3
    assert themes["AI算力链"]["graph_count"] == 2
    assert themes["AI算力链"]["kinds"] == ["催化", "题材"]
    assert themes["AI算力链"]["streak"] == 1
    assert themes["AI算力链"]["is_new"] is True
    assert themes["AI算力链"]["delta"] is None
    # 图形优先的主题排最前
    assert result["themes"][0]["theme"] == "AI算力链"
    assert themes["AI算力链"]["stocks"][0]["code"] == "002463"


def test_build_catalyst_digest_computes_streak_and_delta() -> None:
    history = [
        {"date": "2026-09-23", "themes": {"AI算力链": 2}},
        {"date": "2026-09-24", "themes": {"AI算力链": 1, "军工": 1}},
    ]
    items = [
        _item("002463", "沪电股份", cycle_label="AI算力链", priority=90.0),
        _item("002025", "航天电器", industry="机器人", priority=50.0),
    ]

    result = build_catalyst_digest(items, history=history)

    themes = {row["theme"]: row for row in result["themes"]}
    assert themes["AI算力链"]["streak"] == 3
    assert themes["AI算力链"]["delta"] == 0
    assert themes["AI算力链"]["is_new"] is False
    assert "军工" not in themes  # 今日未出现，不进入聚合结果
    assert themes["机器人"]["streak"] == 1
    assert themes["机器人"]["delta"] == 1
    assert themes["机器人"]["is_new"] is True
    assert result["history_days"] == 2


def test_build_catalyst_digest_dedupes_same_tag_from_multiple_fields() -> None:
    items = [
        _item(
            "601899",
            "紫金矿业",
            matched=(),
            cycle_label="资源涨价",
            industry="资源涨价",
            priority=80.0,
        )
    ]

    result = build_catalyst_digest(items, history=[])

    row = result["themes"][0]
    assert row["theme"] == "资源涨价"
    assert row["count"] == 1
    assert row["kinds"] == ["催化", "行业"]
    assert len(row["stocks"]) == 1


def test_history_append_is_idempotent(tmp_path) -> None:
    history_path = tmp_path / "history.jsonl"
    append_history(history_path, "2026-09-24", {"AI算力链": 2})
    append_history(history_path, "2026-09-25", {"AI算力链": 3})
    append_history(history_path, "2026-09-25", {"AI算力链": 5, "军工": 1})

    entries = load_history(history_path)

    assert [entry["date"] for entry in entries] == ["2026-09-24", "2026-09-25"]
    assert entries[-1]["themes"] == {"AI算力链": 5, "军工": 1}


class _FakeMatrixService:
    def get_matrix(self, *, snapshot_date=None):
        return {
            "snapshot_date": "2026-09-25",
            "source_csv_path": "data/manual_runs/demo/review/fast_review_stock_overview.csv",
            "market_regime": "defensive",
            "items": [
                _item("002463", "沪电股份", cycle_label="AI算力链景气 + 业绩兑现", priority=88.0),
                _item("000570", "苏常柴A", matched=("daily_slow_rise",), priority=60.0),
            ],
        }


def test_service_facade_persists_and_renders(tmp_path) -> None:
    service = CatalystDigestService(
        matrix_service=_FakeMatrixService(),
        history_path=tmp_path / "history.jsonl",
        output_root=tmp_path / "out",
    )

    result = service.build(snapshot_date="latest", top_n=5)

    assert result["snapshot_date"] == "2026-09-25"
    assert result["all_theme_counts"] == {"AI算力链景气": 1, "业绩兑现": 1}

    artifacts = service.persist(result)

    assert Path(artifacts["markdown_path"]).exists()
    assert Path(artifacts["json_path"]).exists()
    assert load_history(service.history_path)[-1]["themes"] == {"AI算力链景气": 1, "业绩兑现": 1}

    markdown = render_catalyst_digest_markdown(result)
    assert "题材 / 催化聚合" in markdown
    assert "沪电股份" in markdown
