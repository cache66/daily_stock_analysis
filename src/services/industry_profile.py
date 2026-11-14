# -*- coding: utf-8 -*-
"""个股画像：行业、分组、是否权重/国家队票。

数据来源是本地已缓存的 Tushare `stock_basic` 快照
（`data/cache/reference/tushare_stock_basic_list.csv`，含 code/name/industry/market），
不联网、不新增依赖。

用途：工作台只看“能不能做短线”，所以要能一眼看出某只票是不是
银行/保险/基建这类国家队重仓、低波动的品种，并按行业做敞口上限。
分组只是粗分类，允许用户在页面上改；这里只提供默认口径。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STOCK_BASIC_CSV = PROJECT_ROOT / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"

# 行业 → 分组。行业名称取自 Tushare stock_basic.industry（共 110 类）。
GROUPS: Dict[str, frozenset] = {
    "银行保险券商": frozenset({"银行", "保险", "证券", "多元金融"}),
    "基建地产": frozenset({
        "建筑工程", "装修装饰", "路桥", "水泥", "全国地产", "区域地产", "园区开发", "房产服务",
    }),
    "资源与公用": frozenset({
        "石油开采", "石油加工", "石油贸易", "煤炭开采", "焦炭加工",
        "火力发电", "水力发电", "新型电力", "供气供热", "水务",
        "普钢", "特种钢", "钢加工",
    }),
    "交通运输": frozenset({"港口", "公路", "铁路", "水运", "机场", "空运", "公共交通"}),
}

# 页面默认排除的分组：用户口径里“国家队持仓、对短线没价值”的那批
DEFAULT_EXCLUDED_GROUPS = ("银行保险券商", "基建地产")


def group_of(industry: str) -> str:
    text = str(industry or "").strip()
    for name, members in GROUPS.items():
        if text in members:
            return name
    return "其他"


def is_st_name(name: str) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "退" in text


def load_industry_map(path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """code → {name, industry, group, market}；文件缺失时返回空字典（页面降级为未分类）。"""
    target = Path(path) if path else DEFAULT_STOCK_BASIC_CSV
    if not target.is_file():
        return {}
    result: Dict[str, Dict[str, str]] = {}
    try:
        with target.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").strip()
                if not code:
                    continue
                industry = str(row.get("industry") or "").strip()
                result[code] = {
                    "name": str(row.get("name") or "").strip(),
                    "industry": industry,
                    "group": group_of(industry),
                    "market": str(row.get("market") or "").strip(),
                }
    except (OSError, csv.Error):
        return {}
    return result


def profile_of(code: str, mapping: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    data = (mapping or {}).get(str(code)) or {}
    industry = str(data.get("industry") or "")
    return {
        "industry": industry or "未分类",
        "group": data.get("group") or ("未分类" if not industry else group_of(industry)),
        "st": is_st_name(data.get("name", "")),
    }
