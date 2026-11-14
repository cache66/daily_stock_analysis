# -*- coding: utf-8 -*-
"""短线方法注册表：方法定义、行业排除、方法交集规则的唯一读取入口。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config" / "shortline_methods.json"


class MethodError(RuntimeError):
    """方法定义不合法（缺字段/重复 ID/交集引用未知方法）。"""


@lru_cache(maxsize=4)
def _load(path: str) -> Dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise MethodError(f"方法注册表不存在: {target}")
    with target.open(encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def _validate(data: Dict[str, Any]) -> None:
    methods = data.get("methods") or []
    if not methods:
        raise MethodError("方法注册表为空")
    ids = set()
    for item in methods:
        for field in ("id", "name", "adapter", "question", "confirm_rule", "invalid_rule"):
            if not item.get(field):
                raise MethodError(f"方法缺字段 {field}: {item.get('id')}")
        if item["id"] in ids:
            raise MethodError(f"方法 ID 重复: {item['id']}")
        ids.add(item["id"])
    for inter in data.get("intersections") or []:
        unknown = [m for m in (inter.get("methods") or []) if m not in ids]
        if unknown:
            raise MethodError(f"交集 {inter.get('id')} 引用未知方法: {unknown}")


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    return _load(str(path or CONFIG_PATH))


def defaults() -> Dict[str, Any]:
    return dict(load_config().get("defaults") or {})


def list_methods(*, enabled_only: bool = False) -> List[Dict[str, Any]]:
    items = list(load_config().get("methods") or [])
    if enabled_only:
        items = [m for m in items if m.get("enabled")]
    return items


def get_method(method_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    for item in load_config(path).get("methods") or []:
        if item["id"] == method_id:
            return item
    raise MethodError(f"未知方法: {method_id}")


def list_intersections(*, enabled_only: bool = False) -> List[Dict[str, Any]]:
    items = list(load_config().get("intersections") or [])
    if enabled_only:
        items = [i for i in items if i.get("enabled")]
    return items


def excluded_industries(method: Dict[str, Any]) -> List[str]:
    """方法级行业排除：方法里写 False 表示该方法不做行业过滤（如长线研究）。"""
    if method.get("exclude_industries") is False:
        return []
    base = list((load_config().get("defaults") or {}).get("exclude_industries") or [])
    extra = method.get("exclude_industries") if isinstance(method.get("exclude_industries"), list) else []
    return sorted({*base, *extra})
