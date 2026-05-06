# -*- coding: utf-8 -*-
"""Readability regression tests for shortline text outputs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from src.shortline_hub.adapters.fingenius_adapter import StubFinGeniusAdapter
from src.shortline_hub.adapters.wondertrader_adapter import StubWonderTraderAdapter
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator
from src.shortline_hub.report_builder import build_shortline_report_markdown


def test_shortline_report_uses_readable_chinese_labels() -> None:
    result = ShortlineHubOrchestrator(
        scanner=StubWonderTraderAdapter(),
        explainer=StubFinGeniusAdapter(),
    ).run(run_id="readable_report", trade_date="2026-05-02", top_n=1)

    report = build_shortline_report_markdown(result)

    assert "## 结果概览" in report
    assert "## 候选概览" in report
    assert "## 逐票说明" in report
    assert "候选来源" in report
    assert "价格" in report
    assert "成交额" in report
    assert "短线先看" in report
    assert "当前未见明显额外风险提示" in report
    assert "缂" not in report
    assert "闁" not in report


def test_fingenius_bridge_default_big_deal_summary_is_readable(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent
        / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    tool_dir = fingenius_root / "upstream" / "src" / "tool"
    tool_dir.mkdir(parents=True)
    (tool_dir.parent / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "base.py").write_text(
        """
class ToolResult:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "hot_money.py").write_text(
        """
from src.tool.base import ToolResult

class HotMoneyTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_latest_info": [{"stock_code": "300001", "turnover_rate": 5.1}],
            "daily_top_list": [{"stock_code": "300001", "reason": "active hot money"}],
            "stock_net_flow": [{"main_net_inflow": "1.20e8"}],
            "hot_section_data": {"industry": [{"name": "power"}]},
        })
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        """
from src.tool.base import ToolResult

class ChipAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "analysis": {
                "basic_analysis": {"profit_ratio": 72.0, "average_cost": 19.8},
                "trading_signals": {"buy_signals": ["chip improving"], "risk_warnings": []},
            }
        })
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        raise RuntimeError("big deal should be skipped by default")
""".strip(),
        encoding="utf-8",
    )

    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "sample_stock",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_process",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "test",
                    "trigger_score": 92.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "setup_tag": "breakout",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(request_path), str(output_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "未开启 BigDealAnalysisTool" in payload["big_deal_summary"]
    assert "换手" in payload["big_deal_summary"]
    assert "缂" not in payload["big_deal_summary"]
    assert "闁" not in payload["big_deal_summary"]
