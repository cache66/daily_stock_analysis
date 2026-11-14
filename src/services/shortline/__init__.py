# -*- coding: utf-8 -*-
"""短线方法层：注册表 + 适配层 + 执行器（方法化，替代"信号源堆候选"）。"""

from src.services.shortline.registry import (  # noqa: F401
    MethodError,
    defaults,
    excluded_industries,
    get_method,
    list_intersections,
    list_methods,
    load_config,
)
from src.services.shortline.runner import render_markdown, run_methods  # noqa: F401

__all__ = [
    "MethodError",
    "run_methods",
    "render_markdown",
    "list_methods",
    "get_method",
    "list_intersections",
    "excluded_industries",
    "defaults",
    "load_config",
]
