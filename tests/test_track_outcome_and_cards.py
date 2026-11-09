# -*- coding: utf-8 -*-
"""A3-a 结果回填 + A1-b 经验卡 的合同测试。"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import pytest

from src.config import Config
from src.services.experience_cards_service import (
    ExperienceCardError,
    load_cards,
    render_cards,
)
from src.services.research_tracking_service import ResearchTrackingService
from src.services.track_outcome_service import compute_forward_outcome, evaluate_track_outcomes
from src.storage import DatabaseManager


# ---------------------------------------------------------------- 前向收益


def test_compute_forward_outcome_complete() -> None:
    bars = [("2026-11-06", 10.0), ("2026-11-09", 10.2), ("2026-11-10", 9.9),
            ("2026-11-11", 10.5), ("2026-11-12", 10.4), ("2026-11-13", 11.0)]
    outcome = compute_forward_outcome(bars)

    assert outcome is not None and outcome.complete
    assert outcome.entry_date == "2026-11-06"
    assert round(outcome.ret1, 6) == 0.02
    assert round(outcome.ret5, 6) == 0.1
    assert outcome.available_days == 5


def test_compute_forward_outcome_partial_and_insufficient() -> None:
    partial = compute_forward_outcome([("2026-11-06", 10.0), ("2026-11-09", 10.5)])
    assert partial is not None and not partial.complete
    assert partial.ret1 is not None and partial.ret3 is None

    assert compute_forward_outcome([("2026-11-06", 10.0)]) is None
    assert compute_forward_outcome([]) is None


# ---------------------------------------------------------------- 回填流程


@pytest.fixture()
def service(tmp_path):
    old = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "track_outcome.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        yield ResearchTrackingService()
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old


def _make_track(service, key: str = "signal:hundred_day_high:2026-11-06"):
    track, _ = service.create_or_get(
        market="cn",
        code="600519",
        hypothesis_key=key,
        business_layer="review",
        horizon="short",
        capital_role="watch",
        hypothesis="测试",
        next_review_at=datetime(2026, 11, 9),
    )
    service.change_status(track.track_id, "tracking", reason="测试推进")
    return track


def test_evaluate_closes_track_and_is_idempotent(service) -> None:
    _make_track(service)
    bars = [("2026-11-06", 10.0), ("2026-11-09", 10.2), ("2026-11-10", 9.9),
            ("2026-11-11", 10.5), ("2026-11-12", 10.4), ("2026-11-13", 11.0)]

    first = evaluate_track_outcomes(as_of=date(2026, 11, 13), bars_loader=lambda *a, **k: bars)
    assert first["written"] == 1 and first["closed"] == 1

    track = service.repo.list_tracks(limit=5)[0]
    assert track.status == "closed"
    events = [event.event_type for event in service.repo.list_events(track.id)]
    assert events == ["created", "tracking", "evidence", "closed"]

    second = evaluate_track_outcomes(as_of=date(2026, 11, 13), bars_loader=lambda *a, **k: bars)
    assert second["written"] == 0 and second["closed"] == 0


def test_evaluate_marks_insufficient_instead_of_guessing(service) -> None:
    _make_track(service)

    summary = evaluate_track_outcomes(bars_loader=lambda *a, **k: [("2026-11-06", 10.0)])
    assert summary["insufficient"] == 1
    assert service.repo.list_tracks(limit=5)[0].status == "tracking"


# ---------------------------------------------------------------- 经验卡


def _write_registry(path, cards):
    path.write_text(json.dumps({"version": "test", "cards": cards}, ensure_ascii=False), encoding="utf-8")


def _card(**overrides):
    base = {
        "id": "c1", "date": "2026-11-06", "type": "机制", "title": "测试卡",
        "facts": "事实", "conclusion": "结论", "boundary": "边界", "source": "docs/x.md",
    }
    base.update(overrides)
    return base


def test_cards_load_and_render(tmp_path) -> None:
    path = tmp_path / "cards.json"
    _write_registry(path, [_card()])

    registry = load_cards(path)
    markdown = render_cards(registry)
    assert "测试卡" in markdown and "docs/x.md" in markdown
    assert "共 1 张" in markdown


def test_cards_require_source_and_unique_id(tmp_path) -> None:
    path = tmp_path / "cards.json"

    _write_registry(path, [_card(source="")])
    with pytest.raises(ExperienceCardError):
        load_cards(path)

    _write_registry(path, [_card(), _card()])
    with pytest.raises(ExperienceCardError):
        load_cards(path)

    _write_registry(path, [_card(type="未知")])
    registry = load_cards(path)
    assert registry["cards"][0]["type"] == "未知"


# ---------------------------------------------------------------- 前视与 D0


def test_loader_receives_as_of_cutoff(service) -> None:
    """回填必须把 as_of 传给价格查询，否则会读到截止日之后的行情。"""
    _make_track(service)
    seen = {}

    def loader(code, signal_date, **kwargs):
        seen.update(kwargs)
        return [("2026-11-06", 10.0), ("2026-11-09", 10.2), ("2026-11-10", 9.9),
                ("2026-11-11", 10.5), ("2026-11-12", 10.4), ("2026-11-13", 11.0)]

    evaluate_track_outcomes(as_of=date(2026, 11, 9), bars_loader=loader)
    assert seen.get("end_date") == "2026-11-09"


def test_missing_d0_is_insufficient_not_shifted(service) -> None:
    """信号日缺行情时不能拿下一根 K 线当基准。"""
    _make_track(service)

    summary = evaluate_track_outcomes(
        bars_loader=lambda *a, **k: [("2026-11-09", 10.0), ("2026-11-10", 10.2)]
    )
    assert summary["insufficient"] == 1
    assert summary["written"] == 0


def test_partial_rerun_does_not_duplicate_events(service) -> None:
    _make_track(service)
    bars = [("2026-11-06", 10.0), ("2026-11-09", 10.2), ("2026-11-10", 9.9)]

    first = evaluate_track_outcomes(bars_loader=lambda *a, **k: bars)
    second = evaluate_track_outcomes(bars_loader=lambda *a, **k: bars)

    assert first["written"] == 1 and second["written"] == 0
    track = service.repo.list_tracks(limit=5)[0]
    evidence = [e for e in service.repo.list_events(track.id) if e.event_type == "evidence"]
    assert len(evidence) == 1
