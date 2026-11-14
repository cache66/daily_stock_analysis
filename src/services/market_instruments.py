# -*- coding: utf-8 -*-
"""交易标的分类：哪些代码是“可以当股票做统计”的。

背景（2026-11-09）：`stock_daily` 里混进了指数与基金序列——

- `000300`（沪深300）、`399001`、`399006`、`000688` 是指数点位（几千点），
  在大盘分析链路里按裸码写进了股票表，canonical_id 还是 `sz000300` 这种股票形式；
- `000852`/`000905` 更麻烦：库里是**指数**（中证1000/中证500），而本地行情缓存
  里同名代码是**股票**（石化机械/厦门港务），两者价格量级差三个数量级；
- `563230` 是 ETF，`CSI930955` 是 CSI 指数代码，只各有几行。

混在股票池里的后果：横截面均值/分位、涨跌分布、选股候选都可能把“指数点位”
当成一只票。本模块只做分类，不改写数据；清理由 `scripts/clean_index_rows.py`。

口径说明：裸码判断不了歧义（000001 既是上证指数也是平安银行），所以这里只列
**明确无对应股票**的指数代码，加上按代码段识别的基金。
"""

from __future__ import annotations

import re
from typing import Iterable

# 明确无同码 A 股、只作为指数存在的代码
# 注意：000688 是深市股票（国城矿业），科创50 指数是 000688.SH，不能用裸码判定，
# 因此这里只列出确认没有同码股票的指数代码。
INDEX_CODES = frozenset({"000300", "399001", "399006", "399300", "399905"})

# 基金/ETF 代码段（沪市 5xxxxx、深市 15xxxx/16xxxx）
FUND_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")

# A 股股票代码段
STOCK_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-3][0-9]{3}|30[01][0-9]{3}|[48][0-9]{5}|9[0-9]{5})$")

# 明确是“同名不同物”的污染代码：库里是指数、缓存里是股票，需要按股票重建
COLLIDED_INDEX_CODES = frozenset({"000852", "000905"})


def is_index_code(code: str) -> bool:
    text = str(code or "").strip().upper()
    return text in INDEX_CODES or text.startswith("CSI")


def is_fund_code(code: str) -> bool:
    text = str(code or "").strip()
    return text[:2] in FUND_PREFIXES


def is_tradable_stock(code: str) -> bool:
    """可以作为股票参与横截面统计/选股的代码。"""
    text = str(code or "").strip()
    if not text or is_index_code(text) or is_fund_code(text):
        return False
    return bool(STOCK_RE.match(text))


def filter_tradable(codes: Iterable[str]) -> list:
    return [code for code in codes if is_tradable_stock(code)]


def classify(codes: Iterable[str]) -> dict:
    """按类别计数，便于日志核对。"""
    summary = {"stock": 0, "index": 0, "fund": 0, "other": 0}
    for code in codes:
        text = str(code or "").strip()
        if is_index_code(text):
            summary["index"] += 1
        elif is_fund_code(text):
            summary["fund"] += 1
        elif STOCK_RE.match(text):
            summary["stock"] += 1
        else:
            summary["other"] += 1
    return summary
