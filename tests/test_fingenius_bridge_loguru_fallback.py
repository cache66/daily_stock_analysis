# -*- coding: utf-8 -*-
"""Regression test for upstream loguru shim in FinGenius bridge."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_fingenius_bridge_template_can_import_upstream_without_loguru(tmp_path: Path) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    src_dir = fingenius_root / "upstream" / "src"
    tool_dir = src_dir / "tool"
    tool_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "logger.py").write_text(
        """
from loguru import logger as _logger

logger = _logger
""".strip(),
        encoding="utf-8",
    )
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
from src.logger import logger
from src.tool.base import ToolResult

class HotMoneyTool:
    async def execute(self, **kwargs):
        logger.info("hot money ok")
        return ToolResult(output={"stock_net_flow": [{"主力净流入": "9000万"}]})
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        """
from src.logger import logger
from src.tool.base import ToolResult

class ChipAnalysisTool:
    async def execute(self, **kwargs):
        logger.info("chip ok")
        return ToolResult(output={"analysis": {"trading_signals": {"buy_signals": ["筹码改善"]}}})
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
from src.logger import logger
from src.tool.base import ToolResult

class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        logger.info("big deal ok")
        return ToolResult(output={"stock_big_deal_summary": {"net_inflow_wan": 1200.0}})
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
                    "name": "特锐德",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_process",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "test",
                    "trigger_score": 92.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "电气设备",
                    "setup_tag": "放量突破",
                    "risk_flags": [],
                },
                "bridge_options": {
                    "enable_big_deal": True,
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
    assert payload["candidate_id"] == "2026-05-02-300001"
    assert "主力净流入" in payload["hot_money_summary"]
    assert "1200.00万" in payload["big_deal_summary"]
