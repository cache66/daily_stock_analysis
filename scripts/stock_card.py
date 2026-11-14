#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印固定信息卡（CLI）：把长短线共用的五个维度量化输出。

用法：
    ./.venv-linux/bin/python scripts/stock_card.py 600519
    ./.venv-linux/bin/python scripts/stock_card.py 600519,000001 --as-of 2026-11-09
    ./.venv-linux/bin/python scripts/stock_card.py 600519 --json

缺数显示为 `pending/limited/missing` 并附原因，不推断、不用其它周期替代。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.profile_card_service import build_profile_card, status_of, summarize_missing  # noqa: E402

STATUS_LABEL = {
    "ok": "可用",
    "pending": "待补",
    "limited": "受限",
    "missing": "缺失",
}


def _fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def render(card: Dict[str, Any]) -> str:
    sections = card["sections"]
    basic = sections["basic"].get("data") or {}
    price = (sections.get("price") or {}).get("data") or {}
    valuation = sections["valuation"].get("data") or {}
    earnings = sections["earnings"].get("data") or {}
    holders = sections["holders"].get("data") or {}
    institution = sections["institution"].get("data") or {}
    sector = sections["sector"].get("data") or {}

    lines: List[str] = []
    lines.append("=" * 78)
    head = f"{card['code']} {basic.get('name') or '—'}　{basic.get('industry') or '未分类'}"
    if basic.get("board"):
        head += f"（{basic['board']}）"
    lines.append(f"{head}　as_of={card['as_of']}")
    lines.append("-" * 78)
    lines.append(
        f"价格　收盘 {_fmt(price.get('close'))}　20日 {_fmt((price.get('ret20') or 0) * 100, '%')}　"
        f"距120日高 {_fmt((sector.get('dist_high120') or 0) * 100, '%')}　最近交易日 {price.get('last_date') or '—'}"
    )
    lines.append(
        f"规模　总市值 {_fmt(valuation.get('market_cap'), ' 亿')}　流通 {_fmt(valuation.get('float_cap'), ' 亿')}　"
        f"PE(TTM) {_fmt(valuation.get('pe_ttm'))}　PB {_fmt(valuation.get('pb'))}　换手 {_fmt(valuation.get('turnover'), '%')}"
    )
    lines.append(
        f"业绩　报告期 {sections['earnings'].get('report_period') or '—'}（公告 {sections['earnings'].get('ann_date') or '—'}）"
        f"　营收同比 {_fmt(earnings.get('revenue_yoy'), '%')}　扣非同比 {_fmt(earnings.get('profit_yoy'), '%')}"
        f"　ROE {_fmt(earnings.get('roe'), '%')}　毛利率 {_fmt(earnings.get('gross_margin'), '%')}"
    )
    lines.append(
        f"户数　{_fmt(holders.get('holders'), ' 户', 0)}　环比 {_fmt(holders.get('chg_pct'), '%')}　"
        f"连续下降 {holders.get('drop_periods', '—')} 期　历史分位 {_fmt(holders.get('holders_percentile'), '%', 1)}"
        f"　截止 {sections['holders'].get('holder_asof') or '—'}（公告 {sections['holders'].get('ann_date') or '—'}）"
    )
    lines.append(
        f"机构　基金 {institution.get('fund_count', '—')} 家　私募 {institution.get('private_fund_count', '—')} 家　"
        f"社保 {institution.get('social_security_count', '—')} 家　QFII {institution.get('qfii_count', '—')} 家　"
        f"合计占比 {_fmt(institution.get('inst_ratio'), '%')}　环比 {_fmt(institution.get('inst_chg_pct'), '%')}"
        f"（{sections['institution'].get('report_period') or '—'}）"
    )
    lines.append(
        f"板块　{sector.get('industry') or '未分类'}　行业20日中位 {_fmt((sector.get('industry_ret20_median') or 0) * 100, '%')}"
        f"　全市场分位 {_fmt(sector.get('industry_pct'), '%', 1)}　同业样本 {sector.get('peer_count', '—')} 只"
    )

    gaps = summarize_missing(card)
    if gaps:
        lines.append("-" * 78)
        lines.append("缺口：")
        for item in gaps:
            lines.append(f"  · {item}")
    for note in card.get("limitations") or []:
        lines.append(f"  注：{note}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="打印固定信息卡（基本信息/估值/业绩/股东户数/机构/板块）")
    parser.add_argument("codes", help="逗号分隔的股票代码")
    parser.add_argument("--as-of", default=None, help="截止交易日 YYYY-MM-DD，默认库内最新")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args(argv)

    codes = [c.strip() for c in str(args.codes).split(",") if c.strip()]
    if not codes:
        print("未提供股票代码")
        return 2

    cards = []
    for code in codes:
        try:
            card = build_profile_card(code, as_of=args.as_of)
        except Exception as exc:  # noqa: BLE001 - CLI 单票失败不阻断其余
            print(f"[stock_card] {code} 失败: {exc}", file=sys.stderr)
            continue
        cards.append(card)
        if not args.json:
            print(render(card))
    if args.json:
        print(json.dumps(cards if len(cards) > 1 else cards[0], ensure_ascii=False, indent=2))
    if not cards:
        return 1
    statuses = [status_of(s) for card in cards for s in card["sections"].values()]
    if all(s in ("pending", "missing") for s in statuses):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
