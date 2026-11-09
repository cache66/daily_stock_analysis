#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基金前十大持仓抓取（东财 F10 主源 + 移动端兜底），替代已失效的 akshare 持仓接口。

背景（2026-10-02 修复）：
- 东财 F10 接口 `FundArchivesDatas.aspx` 已启用防盗链：请求必须携带本站 Referer，
  否则返回 404 页面；akshare `fund_portfolio_hold_em` 未带 Referer，会把 404 页面
  当作 JS 解析并报 “Can not decode value starting with character ';'”。
- 本脚本直连该接口并补齐请求头（UA + Referer），解析内嵌 `apidata` 对象；
  另备东财移动端 JSON 接口（fundmobapi）兜底（仅最新一期，带行业/变动字段）。

用法：
    ./.venv-linux/bin/python scripts/fetch_fund_holdings.py 018730 019189 013465 000199
    ./.venv-linux/bin/python scripts/fetch_fund_holdings.py 018730 --year 2025 --quarters 4
    ./.venv-linux/bin/python scripts/fetch_fund_holdings.py 018730 --industry \
        --csv data/strategy_review/fund_holdings_20261002.csv

说明：
- 默认查“今年”最新一期；如今年无数据（新基金/跨年年初）自动回退到历史最近有数据年份。
- `--quarters N` 一次展示同一年的最近 N 期，用于“风格漂移”对照。
- 数据只读、不落库、不产生信号；仅服务本地基金研究/体检。

产物：默认只打印；指定 `--csv` 时导出明细（utf-8-sig）。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

F10_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
MOBILE_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
REQUEST_TIMEOUT = 15
FUND_SLEEP_SECONDS = 0.6


class HoldingsError(RuntimeError):
    """持仓数据抓取或解析失败。"""


def make_session() -> requests.Session:
    """带重试的 requests Session。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _num(text) -> float | None:
    """把 '5.78%' / '6,973.51' / '--' 这类单元格解析为 float。"""
    if text is None:
        return None
    cleaned = str(text).replace(",", "").replace("%", "").replace("*", "").strip()
    if cleaned in {"", "--", "-", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_apidata(text: str) -> tuple[str, list[int], int | None]:
    """从 F10 的 JS 响应中提取 (content, arryear, curyear)。

    响应形如 ``var apidata={ content:"<div…>",arryear:[2026,2025],curyear:2026};``，
    其中 content 内的 HTML 只用单引号，不会出现未转义双引号。
    """
    match = re.search(r'content:"(.*?)",arryear:\[([^\]]*)\],curyear:(\d*)\}', text, re.S)
    if not match:
        raise HoldingsError(f"响应不是预期的 apidata 结构：{text[:120]!r}")
    content = match.group(1)
    years = [int(item) for item in match.group(2).split(",") if item.strip().isdigit()]
    curyear = int(match.group(3)) if match.group(3) else None
    return content, years, curyear


def _parse_table(table) -> list[dict]:
    """解析单个季度持仓表；返回按出现顺序（权重降序）的持仓行。"""
    trs = table.find_all("tr")
    if not trs:
        return []
    header = [cell.get_text(strip=True) for cell in trs[0].find_all(["th", "td"])]

    def col_index(*keywords: str) -> int | None:
        for idx, name in enumerate(header):
            if any(keyword in name for keyword in keywords):
                return idx
        return None

    i_code = col_index("股票代码")
    i_name = col_index("股票名称")
    i_weight = col_index("占净值")
    i_shares = col_index("持股数")
    i_value = col_index("持仓市值")
    if i_code is None or i_name is None or i_weight is None:
        return []

    rows: list[dict] = []
    for tr in trs[1:]:
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["th", "td"])]
        if len(cells) <= max(i_code, i_name, i_weight):
            continue
        code = cells[i_code].strip()
        if not re.fullmatch(r"\d{4,6}", code):
            continue  # 跳过“合计”等非股票行
        rows.append(
            {
                "stock_code": code,
                "stock_name": cells[i_name].replace("*", "").strip(),
                "weight_pct": _num(cells[i_weight]),
                "shares_wan": _num(cells[i_shares]) if i_shares is not None and i_shares < len(cells) else None,
                "value_wan": _num(cells[i_value]) if i_value is not None and i_value < len(cells) else None,
                "starred": any("*" in cell for cell in cells),
            }
        )
    return rows


def parse_holdings_html(content: str) -> list[dict]:
    """解析 F10 content HTML，展平为带季度信息的持仓行。"""
    if not content.strip():
        return []
    soup = BeautifulSoup(content, "lxml")
    heads = soup.find_all("h4")
    tables = soup.find_all("table")
    if len(heads) != len(tables):
        raise HoldingsError(f"h4 与 table 数量不匹配：h4={len(heads)} table={len(tables)}")
    name_match = re.search(r"title='([^']+)'", content)
    fund_name = name_match.group(1) if name_match else ""

    rows: list[dict] = []
    for head, table in zip(heads, tables):
        head_text = head.get_text(" ", strip=True)
        quarter_match = re.search(r"(\d{4})年(\d)季度", head_text)
        end_match = re.search(r"截止至[：:]\s*(\d{4}-\d{2}-\d{2})", head_text)
        quarter = f"{quarter_match.group(1)}年{quarter_match.group(2)}季度" if quarter_match else head_text[:40]
        end_date = end_match.group(1) if end_match else ""
        for idx, record in enumerate(_parse_table(table)):
            record.update(
                fund_name=fund_name,
                quarter=quarter,
                end_date=end_date,
                seq=idx + 1,
                in_top10=idx < 10,
            )
            rows.append(record)
    return rows


def fetch_f10_year(code: str, year: int, session: requests.Session) -> tuple[list[dict], list[int]]:
    """抓取指定年份的 F10 持仓；返回 (行, 该基金可用年份列表)。

    注意：东财已加防盗链，必须携带本站 Referer，否则返回 404。
    """
    response = session.get(
        F10_URL,
        params={
            "type": "jjcc",
            "code": code,
            "topline": "10",
            "year": str(year),
            "month": "",
            "rt": f"{time.time():.6f}",
        },
        headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    content, years, _ = extract_apidata(response.text)
    return parse_holdings_html(content), years


def fetch_f10_latest(code: str, session: requests.Session, year: int | None = None) -> tuple[list[dict], int, bool]:
    """抓取最近一期持仓；今年无数据时自动回退历史最近年份。

    返回 (行, 实际年份, 是否发生回退)。
    """
    target = year or date.today().year
    rows, years = fetch_f10_year(code, target, session)
    if rows:
        return rows, target, False
    for candidate in sorted((item for item in years if item != target), reverse=True):
        rows, more_years = fetch_f10_year(code, candidate, session)
        if rows:
            return rows, candidate, True
        years = more_years or years
    return [], target, False


def fetch_mobile_holdings(code: str, session: requests.Session) -> list[dict]:
    """东财移动端 JSON 兜底源（最新一期，带行业与变动类型字段）。"""
    response = session.get(
        MOBILE_URL,
        params={
            "FCODE": code,
            "deviceid": "Wap",
            "plat": "Wap",
            "product": "EFund",
            "version": "2.0.0",
            "_": str(int(time.time() * 1000)),
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    stocks = ((payload.get("Datas") or {}).get("fundStocks")) or []
    rows: list[dict] = []
    for idx, item in enumerate(stocks):
        rows.append(
            {
                "stock_code": str(item.get("GPDM") or "").strip(),
                "stock_name": str(item.get("GPJC") or "").strip(),
                "weight_pct": _num(item.get("JZBL")),
                "shares_wan": None,
                "value_wan": None,
                "starred": False,
                "industry": item.get("INDEXNAME") or "",
                "change_type": item.get("PCTNVCHGTYPE") or "",
                "seq": idx + 1,
                "in_top10": idx < 10,
                "quarter": "最新一期",
                "end_date": "",
                "fund_name": "",
            }
        )
    return rows


def attach_industry(rows: list[dict], session: requests.Session, code: str) -> bool:
    """用移动端接口给 F10 行补充行业/变动列；失败时返回 False（不中断主流程）。"""
    try:
        mobile_rows = fetch_mobile_holdings(code, session)
    except Exception:  # noqa: BLE001 - 兜底源失败不影响主流程
        return False
    mobile_map = {row["stock_code"]: row for row in mobile_rows}
    for row in rows:
        extra = mobile_map.get(row["stock_code"])
        if extra:
            row["industry"] = extra.get("industry", "")
            row["change_type"] = extra.get("change_type", "")
        else:
            row.setdefault("industry", "")
            row.setdefault("change_type", "")
    return True


def _display_frame(rows: list[dict]) -> pd.DataFrame:
    has_industry = any(row.get("industry") for row in rows)
    has_change = any(row.get("change_type") for row in rows)
    items = []
    for row in rows:
        item = {
            "序号": row["seq"],
            "股票代码": row["stock_code"],
            "股票名称": row["stock_name"],
            "占净值%": f"{row['weight_pct']:.2f}" if row["weight_pct"] is not None else "-",
            "持股数(万股)": f"{row['shares_wan']:.2f}" if row["shares_wan"] is not None else "-",
            "持仓市值(万元)": f"{row['value_wan']:,.2f}" if row["value_wan"] is not None else "-",
        }
        if has_industry:
            item["行业"] = row.get("industry") or "-"
        if has_change:
            item["变动"] = row.get("change_type") or "-"
        items.append(item)
    return pd.DataFrame(items)


def print_fund(code: str, rows: list[dict], source_label: str, quarters: int) -> None:
    """打印某只基金的持仓信息。"""
    fund_name = next((row.get("fund_name") for row in rows if row.get("fund_name")), "")
    print(f"\n=== {code} {fund_name} ===")
    blocks: list[tuple[str, str]] = []
    for row in rows:
        key = (row["quarter"], row["end_date"])
        if key not in blocks:
            blocks.append(key)
    limit = max(quarters, 1)
    for quarter, end_date in blocks[:limit]:
        block = [row for row in rows if (row["quarter"], row["end_date"]) == (quarter, end_date)]
        top = [row for row in block if row["in_top10"]]
        extra = [row for row in block if not row["in_top10"]]
        label = f"{quarter} · 截止 {end_date}" if end_date else quarter
        print(f"  [{label}] 来源：{source_label}")
        print(_display_frame(top).to_string(index=False))
        total = sum(row["weight_pct"] for row in top if row["weight_pct"] is not None)
        print(f"  前十大合计占净值：{total:.2f}%")
        if extra:
            names = "、".join(row["stock_name"] for row in extra)
            print(f"  （另有 {len(extra)} 行带 * 标记（非前十大重仓），已忽略：{names}）")
    if len(blocks) > limit:
        print(f"  （该年共 {len(blocks)} 期，已按 --quarters={limit} 截断）")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基金前十大持仓抓取（东财 F10 + 移动端兜底）")
    parser.add_argument("codes", nargs="+", help="基金代码，如 018730 019189")
    parser.add_argument("--year", type=int, default=None, help="指定年份（默认今年，无数据自动回退）")
    parser.add_argument("--quarters", type=int, default=1, help="展示同一年内最近 N 期（默认 1）")
    parser.add_argument("--industry", action="store_true", help="用移动端接口补充行业/变动列（失败则忽略）")
    parser.add_argument("--csv", type=Path, default=None, help="导出明细 CSV 路径（可选）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = make_session()
    failed: list[str] = []
    export_rows: list[dict] = []
    for index, code in enumerate(args.codes):
        if index:
            time.sleep(FUND_SLEEP_SECONDS)
        code = code.strip()
        print(f"\n>>> 抓取 {code} …")
        try:
            rows, year_used, fell_back = fetch_f10_latest(code, session, args.year)
            source = "F10"
            fallback_note = ""
            if fell_back:
                fallback_note = f"（{args.year or date.today().year} 无数据，回退）"
            if not rows:
                rows = fetch_mobile_holdings(code, session)
                source = "移动端"
                year_used = "—"
            if not rows:
                raise HoldingsError("F10 与移动端均未返回数据")
            if args.industry and source == "F10":
                if not attach_industry(rows, session, code):
                    print("  （提示：行业补充失败，仅展示持仓）")
            source_label = f"F10 · {year_used}{fallback_note}" if source == "F10" else "移动端"
            print_fund(code, rows, source_label, args.quarters)
            for row in rows:
                export_rows.append(
                    {
                        "fund_code": code,
                        "fund_name": row.get("fund_name", ""),
                        "year": year_used,
                        "quarter": row["quarter"],
                        "end_date": row["end_date"],
                        "seq": row["seq"],
                        "in_top10": row["in_top10"],
                        "stock_code": row["stock_code"],
                        "stock_name": row["stock_name"],
                        "weight_pct": row["weight_pct"],
                        "shares_wan": row["shares_wan"],
                        "value_wan": row["value_wan"],
                        "starred": row["starred"],
                        "industry": row.get("industry", ""),
                        "source": source,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 单只失败不影响其它基金
            failed.append(code)
            print(f"⚠️ {code} 抓取失败：{exc}")
    if args.csv and export_rows:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(export_rows).to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\nsaved: {args.csv}")
    if failed:
        print(f"\n⚠️ 共 {len(failed)} 只失败：{'、'.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
