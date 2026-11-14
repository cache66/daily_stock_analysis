# -*- coding: utf-8 -*-
"""em_guard 单测：限速间隔、重试与熔断。"""

from __future__ import annotations

import time

import pytest

from src.services import em_guard


def _reset() -> None:
    em_guard._state.update({"fails": 0.0, "circuit_until": 0.0, "calls": 0, "errors": 0})
    em_guard._last_call["ts"] = 0.0


def test_throttle_enforces_min_interval(monkeypatch) -> None:
    _reset()
    timestamp = {"value": 1000.0}

    def fake_monotonic() -> float:
        return timestamp["value"]

    def fake_sleep(seconds: float) -> None:
        timestamp["value"] += seconds

    monkeypatch.setattr(em_guard.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(em_guard.time, "sleep", fake_sleep)
    monkeypatch.setattr(em_guard.random, "uniform", lambda low, high: 0.0)

    em_guard.em_call(lambda: "ok")
    started = timestamp["value"]
    em_guard.em_call(lambda: "ok")
    # 第二次调用应至少等待 MIN_INTERVAL
    assert timestamp["value"] - started >= em_guard.MIN_INTERVAL - 1e-6


def test_retry_then_success(monkeypatch) -> None:
    _reset()
    monkeypatch.setattr(em_guard.time, "sleep", lambda seconds: None)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("RemoteDisconnected")
        return "ok"

    assert em_guard.em_call(flaky) == "ok"
    assert calls["n"] == 3
    assert em_guard._state["fails"] == 0


def test_circuit_opens_after_threshold(monkeypatch) -> None:
    _reset()
    monkeypatch.setattr(em_guard.time, "sleep", lambda seconds: None)

    def boom() -> str:
        raise ConnectionError("blocked")

    for _ in range(3):
        try:
            em_guard.em_call(boom)
        except em_guard.EastmoneyUnavailable:
            pass

    # 连续失败达到阈值后熔断：即使函数正常也直接拒绝
    with pytest.raises(em_guard.EastmoneyUnavailable) as excinfo:
        em_guard.em_call(lambda: "ok")
    assert "熔断" in str(excinfo.value)
    assert em_guard.stats()["circuit_remaining_seconds"] > 0


def test_stats_shape() -> None:
    _reset()
    data = em_guard.stats()
    assert set(data) == {
        "calls",
        "errors",
        "consecutive_fails",
        "circuit_remaining_seconds",
        "min_interval",
        "max_concurrency",
    }
