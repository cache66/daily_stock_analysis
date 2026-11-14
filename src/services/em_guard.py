# -*- coding: utf-8 -*-
"""东财接口的礼貌调用封装：限速 + 重试 + 熔断。

背景（2026-11-11 实测）：东财 `push2`/`push2his` 在短时间内被并发打了数百次后开始
`RemoteDisconnected`；隔一段时间**单次 curl 又能 200**，说明是频控而不是永久封禁。
所以这里做三件事，而不是去绕反爬：

1. **全局限速**：同一进程里所有东财请求串行排队，最小间隔默认 0.35s（≈3 次/秒上限）；
2. **重试退避**：失败重试 2 次，间隔 0.6s / 1.5s；
3. **熔断**：连续失败 5 次后暂停 5 分钟，期间直接抛 `EastmoneyUnavailable`，让上层走兜底，
   避免把失败放大成雪崩。

调用方只需把 `ak.xxx(...)` 换成 `em_call(ak.xxx, **kwargs)`。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

MIN_INTERVAL = 0.35
MAX_CONCURRENCY = 2
RETRY_BACKOFF = (0.6, 1.5)
FAIL_THRESHOLD = 5
CIRCUIT_SECONDS = 300

_lock = threading.Lock()
_semaphore = threading.Semaphore(MAX_CONCURRENCY)
_last_call = {"ts": 0.0}
_state: Dict[str, float] = {"fails": 0, "circuit_until": 0.0, "calls": 0, "errors": 0}


class EastmoneyUnavailable(RuntimeError):
    """熔断中或重试耗尽。"""


def _throttle() -> None:
    with _lock:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _last_call["ts"])
        if wait > 0:
            # 加一点抖动，避免所有进程同一时刻醒来
            time.sleep(wait + random.uniform(0, 0.05))
        _last_call["ts"] = time.monotonic()
        _state["calls"] += 1


def em_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """限速 + 重试 + 熔断地调用一个东财接口。"""
    if time.monotonic() < _state["circuit_until"]:
        raise EastmoneyUnavailable("东财熔断中，跳过本次请求")

    last_error: Exception | None = None
    for attempt in range(len(RETRY_BACKOFF) + 1):
        with _semaphore:
            _throttle()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 统一按失败处理
                last_error = exc
                _state["errors"] += 1
                _state["fails"] += 1
                if _state["fails"] >= FAIL_THRESHOLD:
                    _state["circuit_until"] = time.monotonic() + CIRCUIT_SECONDS
                    if _state["fails"] == FAIL_THRESHOLD:  # 只在刚跨阈值时提示一次
                        logger.warning(
                            "东财连续失败 %s 次，熔断 %ss（%s）",
                            FAIL_THRESHOLD,
                            CIRCUIT_SECONDS,
                            str(exc)[:120],
                        )
                else:
                    logger.debug("东财请求失败（第 %s 次）：%s", attempt + 1, str(exc)[:120])
            else:
                _state["fails"] = 0
                return result
        if attempt < len(RETRY_BACKOFF):
            time.sleep(RETRY_BACKOFF[attempt])
    raise EastmoneyUnavailable(f"东财请求重试耗尽: {last_error}")


def stats() -> Dict[str, float]:
    """当前限速/熔断状态，便于诊断与前端提示。"""
    remaining = max(0.0, _state["circuit_until"] - time.monotonic())
    return {
        "calls": _state["calls"],
        "errors": _state["errors"],
        "consecutive_fails": _state["fails"],
        "circuit_remaining_seconds": round(remaining, 1),
        "min_interval": MIN_INTERVAL,
        "max_concurrency": MAX_CONCURRENCY,
    }
