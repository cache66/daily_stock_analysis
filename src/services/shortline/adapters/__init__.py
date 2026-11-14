# -*- coding: utf-8 -*-
"""方法适配层：把各生产者的输出归一成统一的候选结构。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from src.services.shortline.adapters import deep_recovery, holders, hotspot, legacy

Adapter = Callable[[str, Dict[str, Any]], List[Dict[str, Any]]]

ADAPTERS: Dict[str, Adapter] = {
    "hotspot": hotspot.discover,
    "deep_recovery": deep_recovery.discover,
    "holders": holders.discover,
    "legacy": legacy.discover,
}


def get_adapter(name: str) -> Adapter:
    if name not in ADAPTERS:
        raise KeyError(f"未实现的 adapter: {name}（可选 {sorted(ADAPTERS)}）")
    return ADAPTERS[name]
