"""fetch_fund_holdings 的离线测试（内置样本，不触网）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_fund_holdings import (  # noqa: E402
    HoldingsError,
    _num,
    extract_apidata,
    parse_holdings_html,
)

SAMPLE = (
    'var apidata={ content:"'
    "<div class='box'><h4 class='t'><label class='left'>"
    "<a title='某某基金A' href='http://fund.eastmoney.com/000000.html'>某某基金A</a>"
    "&nbsp;&nbsp;2026年2季度股票投资明细</label>"
    "<label class='right'>来源：天天基金&nbsp;&nbsp;截止至： 2026-06-30</label></h4>"
    "<table class='w782 comm tzxq'><thead><tr><th>序号</th><th>股票代码</th><th>股票名称</th>"
    "<th>最新价</th><th>涨跌幅</th><th>相关资讯</th><th>占净值比例</th>"
    "<th>持股数（万股）</th><th>持仓市值（万元）</th></tr></thead><tbody>"
    "<tr><td>1</td><td>605111</td><td>新洁能</td><td></td><td></td><td>变动详情</td>"
    "<td>5.78%</td><td>70.34</td><td>6,973.51</td></tr>"
    "<tr><td>2</td><td>00700</td><td>腾讯控股*</td><td></td><td></td><td>变动详情</td>"
    "<td>2.50%</td><td>10.00</td><td>1,000.00</td></tr>"
    "</tbody></table></div>"
    '",arryear:[2026,2025],curyear:2026};'
)


def test_extract_apidata() -> None:
    content, years, curyear = extract_apidata(SAMPLE)
    assert "某某基金A" in content
    assert years == [2026, 2025]
    assert curyear == 2026


def test_extract_apidata_empty_content() -> None:
    content, years, _ = extract_apidata('var apidata={ content:"",arryear:[2026,2025,2024],curyear:2026};')
    assert content == ""
    assert years == [2026, 2025, 2024]


def test_extract_apidata_rejects_non_payload() -> None:
    # 模拟防盗链缺失时的 404 页面（CSS 花括号曾让 akshare 误判为 JSON）
    with pytest.raises(HoldingsError):
        extract_apidata("<html><style>body{margin:0;}</style></html>")


def test_parse_holdings_html() -> None:
    content, _, _ = extract_apidata(SAMPLE)
    rows = parse_holdings_html(content)
    assert len(rows) == 2
    first, second = rows
    assert first["fund_name"] == "某某基金A"
    assert first["quarter"] == "2026年2季度"
    assert first["end_date"] == "2026-06-30"
    assert first["stock_code"] == "605111"
    assert first["weight_pct"] == pytest.approx(5.78)
    assert first["shares_wan"] == pytest.approx(70.34)
    assert first["value_wan"] == pytest.approx(6973.51)
    assert first["starred"] is False
    assert first["in_top10"] is True
    assert second["stock_name"] == "腾讯控股"
    assert second["starred"] is True
    assert second["in_top10"] is True


def test_parse_holdings_html_empty() -> None:
    assert parse_holdings_html("") == []


def test_num_parsing() -> None:
    assert _num("5.78%") == pytest.approx(5.78)
    assert _num("6,973.51") == pytest.approx(6973.51)
    assert _num("--") is None
    assert _num("") is None
