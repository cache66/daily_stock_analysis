#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史行情数据质量工具：接缝（复权混段 / 份额折算 / 错误价格段）检测与过滤。

背景：历史缓存或面板数据可能混入"接缝"——分段复权口径不一致、份额折算未处理或
错误价格段，表现为单日跳变异常（默认 >30%），会造成假涨跌、假股息率、假月收益
（实例：600519 缓存段 close=10.50 假价 → 假股息率 ~285%、假月收益 +13242%）。

本模块提供统一的检测函数，供回测 / 筛选 / 研究链路复用；``detect_seam_codes`` 的
实现由 ``experiment_dividend_rerank.py`` 收敛而来。

用法::

    from scripts.history_quality import detect_seam_codes

    dirty = detect_seam_codes(prices)  # prices: {code: (dates, closes)} 或 {code: closes}
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Set

import numpy as np

DEFAULT_SEAM_JUMP = 0.30


def _to_float_list(closes: Sequence[float]) -> List[float]:
    values: List[float] = []
    for value in closes:
        try:
            if value is None:
                continue
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def find_seam_indexes(
    closes: Sequence[float], *, jump: float = DEFAULT_SEAM_JUMP
) -> List[int]:
    """返回 |日收益|>jump 的下标（i 表示 closes[i] 相对 closes[i-1] 的跳变）。"""
    values = _to_float_list(closes)
    if len(values) < 2:
        return []
    array = np.asarray(values, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = array[1:] / array[:-1] - 1.0
    return [
        index + 1
        for index, value in enumerate(rets)
        if np.isfinite(value) and abs(float(value)) > float(jump)
    ]


def has_seam(closes: Sequence[float], *, jump: float = DEFAULT_SEAM_JUMP) -> bool:
    """序列中是否存在超阈跳变（即存在接缝）。"""
    return bool(find_seam_indexes(closes, jump=jump))


def detect_seam_codes(
    prices: Dict[str, tuple], *, jump: float = DEFAULT_SEAM_JUMP
) -> Set[str]:
    """在 ``{code: (dates, closes)}``（亦兼容 ``{code: closes}``）面板中找出接缝票。"""
    dirty: Set[str] = set()
    for code, payload in prices.items():
        if (
            isinstance(payload, tuple)
            and len(payload) >= 2
            and isinstance(payload[1], (list, tuple))
        ):
            closes = payload[1]
        else:
            closes = payload
        values = _to_float_list(closes) if hasattr(closes, "__iter__") else []
        if len(values) < 3:
            continue
        array = np.asarray(values, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            rets = array[1:] / array[:-1] - 1.0
        rets = rets[np.isfinite(rets)]
        if rets.size and float(np.max(np.abs(rets))) > float(jump):
            dirty.add(code)
    return dirty
