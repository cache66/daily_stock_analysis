"""screening 快照桥接的离线测试（不触库、不联网）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.collect_screening_snapshots import (  # noqa: E402
    build_snapshot_rows,
    collect_for_strategy,
)


def _candidate(code: str, **extra):
    base = {
        "code": code,
        "name": f"股票{code}",
        "score": 80.0,
        "screen_score": 75.0,
        "reason": "测试原因",
        "industry": "银行",
        "risk_level": "低",
    }
    base.update(extra)
    return base


def test_build_snapshot_rows_normalizes_dedupes_and_caps():
    candidates = [
        _candidate("SH601318"),
        _candidate("601398"),
        _candidate(""),  # 空代码跳过
        _candidate("601318"),  # 归一后与首条重复
        _candidate("000001"),
    ]
    rows = build_snapshot_rows(
        "dual_low",
        candidates,
        market="cn",
        top_n=3,
        engine_meta={"ranking_mode": "factor", "run_id": "r1", "degradation": ["fallback"]},
    )
    assert [row["code"] for row in rows] == ["601318", "601398", "000001"]
    payload = rows[0]["metrics_payload"]
    assert payload["strategy"] == "dual_low"
    assert payload["ranking_mode"] == "factor"
    assert payload["degradation"] == ["fallback"]
    assert rows[0]["criteria_payload"]["selection_seed"] == ""
    assert rows[0]["name"] == "股票SH601318"


def test_build_snapshot_rows_skips_non_dict_and_non_a_share_codes():
    rows = build_snapshot_rows(
        "dual_low",
        ["junk", {"code": "AAPL"}, {"code": "600036"}],
        market="cn",
        top_n=5,
    )
    assert [row["code"] for row in rows] == ["600036"]


class _FakeService:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def screen(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeDb:
    def __init__(self):
        self.calls = []

    def replace_signal_snapshots_for_date(self, **kwargs):
        self.calls.append(kwargs)
        return len(kwargs.get("snapshots") or [])


def test_collect_for_strategy_persists_and_writes_audit(tmp_path):
    response = {
        "candidates": [_candidate("601318"), _candidate("601398")],
        "candidate_count": 2,
        "ranking_mode": "factor",
        "run_id": "run-1",
        "snapshot_source": "tushare",
        "snapshot_count": 5000,
        "after_filter_count": 300,
        "degradation": ["LLM ranking failed: fell back to screen_score"],
        "warnings": [],
    }
    service = _FakeService(response)
    db = _FakeDb()
    summary = collect_for_strategy(
        service,
        db,
        "dual_low",
        market="cn",
        top_n=20,
        as_of=date(2026, 9, 25),
        output_root=tmp_path,
    )
    # 严格 Top-N：空 seed 且 max_results=top_n
    assert service.calls[0]["selection_seed"] == ""
    assert service.calls[0]["max_results"] == 20
    assert db.calls[0]["signal_type"] == "screening__dual_low"
    assert db.calls[0]["signal_date"] == date(2026, 9, 25)
    assert len(db.calls[0]["snapshots"]) == 2
    assert summary["candidates"] == 2
    assert summary["written"] == 2
    assert summary["ranking_mode"] == "factor"

    audit_path = tmp_path / "2026-09-25" / "dual_low.json"
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["signal_type"] == "screening__dual_low"
    assert audit["candidates"][0]["code"] == "601318"
    assert audit["ranking_mode"] == "factor"


def test_collect_for_strategy_propagates_errors(tmp_path):
    class _Boom:
        def screen(self, **kwargs):
            raise RuntimeError("engine down")

    with pytest.raises(RuntimeError):
        collect_for_strategy(
            _Boom(),
            _FakeDb(),
            "dual_low",
            market="cn",
            top_n=5,
            as_of=date(2026, 9, 25),
            output_root=tmp_path,
        )
