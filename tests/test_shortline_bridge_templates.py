# -*- coding: utf-8 -*-
"""Tests for shortline bridge template scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import shutil


def test_wondertrader_bridge_template_writes_candidate_list(tmp_path: Path) -> None:
    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 2}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bridges/shortline_wondertrader_bridge_template.py",
            str(request_path),
            str(output_path),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0]["scan_source"] in {
        "wondertrader_template",
        "wondertrader_cache_scan",
        "wondertrader_real_engine",
    }
    assert "candidate_id" in rows[0]


def test_fingenius_bridge_template_writes_explanation_payload(tmp_path: Path) -> None:
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
                    "trigger_reason": "模拟扫描",
                    "trigger_score": 87.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "电气设备",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bridges/shortline_fingenius_bridge_template.py",
            str(request_path),
            str(output_path),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["candidate_id"] == "2026-05-02-300001"
    assert payload["hot_money_summary"] != ""
    assert payload["confidence_label"] in {"medium", "high"}


def test_wondertrader_bridge_template_prefers_local_export_file(tmp_path: Path) -> None:
    script_dir = tmp_path / "bridge"
    bridge_data_dir = script_dir / "bridge_data"
    script_dir.mkdir()
    bridge_data_dir.mkdir()
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 1}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bridge_data_dir / "wt_candidates_2026-05-02.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "2026-05-02-688001",
                    "symbol": "688001",
                    "name": "real_case",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_export",
                    "trigger_type": "manual_export",
                    "trigger_reason": "loaded from local export file",
                    "trigger_score": 93.0,
                    "price": 45.6,
                    "change_pct": 8.9,
                    "volume_ratio": 3.1,
                    "turnover_rate": 7.2,
                    "board_name": "chip",
                    "risk_flags": [],
                }
            ],
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "688001"
    assert rows[0]["scan_source"] == "wondertrader_export"


def test_wondertrader_bridge_template_uses_repo_spot_cache_when_no_bridge_data(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_cache_dir = (
        tmp_path / "daily_stock_analysis" / "data" / "cache" / "reference"
    )
    repo_cache_dir.mkdir(parents=True)
    (repo_cache_dir / "kline_selector_spot_universe.csv").write_text(
        "\n".join(
            [
                "code,name,total_mv,list_date,listed_days,latest_price,pct_change,turnover_rate,volume_ratio,change_pct_60d,amount",
                "000001,平安银行,,1991-04-03,12810,12.0,1.5,0.8,1.0,5.0,100000000",
                "000007,全新好,,1992-04-13,12434,9.8,10.0,3.35,2.2,18.0,280000000",
                "300001,特锐德,,2009-10-30,6000,21.5,7.6,5.1,2.0,25.0,360000000",
            ]
        ),
        encoding="utf-8",
    )
    (repo_cache_dir / "tushare_stock_basic_list.csv").write_text(
        "\n".join(
            [
                "code,name,industry",
                "000001,平安银行,银行",
                "000007,全新好,其他商业",
                "300001,特锐德,电气设备",
            ]
        ),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 2}, ensure_ascii=False, indent=2),
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0]["scan_source"] == "wondertrader_cache_scan"
    assert rows[0]["symbol"] == "000007"
    assert rows[0]["board_name"] == "其他商业"
    assert rows[0]["setup_tag"] == "涨停强势延续"
    assert rows[1]["symbol"] == "300001"
    assert rows[1]["setup_tag"] == "放量突破"
    assert "spot cache" in rows[0]["trigger_reason"]


def test_wondertrader_bridge_template_prefers_real_engine_helper_when_available(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_root = tmp_path / "daily_stock_analysis"
    helper_dir = repo_root / "src" / "shortline_hub"
    helper_dir.mkdir(parents=True)
    (repo_root / "data" / "cache" / "reference").mkdir(parents=True)
    (helper_dir / "__init__.py").write_text("", encoding="utf-8")
    (helper_dir / "wondertrader_real_engine.py").write_text(
        """
def export_candidates_via_real_wondertrader(**kwargs):
    trade_date = kwargs["trade_date"]
    return [{
        "candidate_id": f"{trade_date}-600519",
        "symbol": "600519",
        "name": "real_engine_case",
        "trade_date": trade_date,
        "scan_source": "wondertrader_real_engine",
        "trigger_type": "momentum_breakout",
        "trigger_reason": "loaded from real engine helper",
        "trigger_score": 96.0,
        "price": 1666.0,
        "change_pct": 5.6,
        "change_pct_60d": 18.2,
        "amount": 520000000.0,
        "volume_ratio": 1.9,
        "turnover_rate": 2.3,
        "board_name": "liquor",
        "setup_tag": "real_engine_tag",
        "risk_flags": [],
    }]
""".strip(),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 1}, ensure_ascii=False, indent=2),
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["scan_source"] == "wondertrader_real_engine"
    assert rows[0]["trigger_reason"] == "loaded from real engine helper"
    assert rows[0]["change_pct_60d"] == 18.2
    assert rows[0]["amount"] == 520000000.0


def test_wondertrader_bridge_template_prefers_real_engine_over_bridge_data_by_default(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    bridge_data_dir = script_dir / "bridge_data"
    script_dir.mkdir(parents=True)
    bridge_data_dir.mkdir()
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_root = tmp_path / "daily_stock_analysis"
    helper_dir = repo_root / "src" / "shortline_hub"
    helper_dir.mkdir(parents=True)
    (repo_root / "data" / "cache" / "reference").mkdir(parents=True)
    (helper_dir / "__init__.py").write_text("", encoding="utf-8")
    (helper_dir / "wondertrader_real_engine.py").write_text(
        """
def export_candidates_via_real_wondertrader(**kwargs):
    trade_date = kwargs["trade_date"]
    return [{
        "candidate_id": f"{trade_date}-600519",
        "symbol": "600519",
        "name": "real_engine_case",
        "trade_date": trade_date,
        "scan_source": "wondertrader_real_engine",
        "trigger_type": "momentum_breakout",
        "trigger_reason": "loaded from real engine helper",
        "trigger_score": 96.0,
        "price": 1666.0,
        "change_pct": 5.6,
        "change_pct_60d": 18.2,
        "amount": 520000000.0,
        "volume_ratio": 1.9,
        "turnover_rate": 2.3,
        "board_name": "liquor",
        "setup_tag": "real_engine_tag",
        "risk_flags": [],
    }]
""".strip(),
        encoding="utf-8",
    )
    (bridge_data_dir / "wt_candidates_2026-05-02.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "bridge_data_case",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_export",
                    "trigger_type": "manual_export",
                    "trigger_reason": "loaded from bridge_data",
                    "trigger_score": 87.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "change_pct_60d": 12.0,
                    "amount": 210000000.0,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "setup_tag": "bridge_data_tag",
                    "risk_flags": [],
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 1}, ensure_ascii=False, indent=2),
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["scan_source"] == "wondertrader_real_engine"


def test_wondertrader_bridge_template_can_prefer_bridge_data_explicitly(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    bridge_data_dir = script_dir / "bridge_data"
    script_dir.mkdir(parents=True)
    bridge_data_dir.mkdir()
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_root = tmp_path / "daily_stock_analysis"
    helper_dir = repo_root / "src" / "shortline_hub"
    helper_dir.mkdir(parents=True)
    (repo_root / "data" / "cache" / "reference").mkdir(parents=True)
    (helper_dir / "__init__.py").write_text("", encoding="utf-8")
    (helper_dir / "wondertrader_real_engine.py").write_text(
        """
def export_candidates_via_real_wondertrader(**kwargs):
    trade_date = kwargs["trade_date"]
    return [{
        "candidate_id": f"{trade_date}-600519",
        "symbol": "600519",
        "name": "real_engine_case",
        "trade_date": trade_date,
        "scan_source": "wondertrader_real_engine",
        "trigger_type": "momentum_breakout",
        "trigger_reason": "loaded from real engine helper",
        "trigger_score": 96.0,
        "price": 1666.0,
        "change_pct": 5.6,
        "change_pct_60d": 18.2,
        "amount": 520000000.0,
        "volume_ratio": 1.9,
        "turnover_rate": 2.3,
        "board_name": "liquor",
        "setup_tag": "real_engine_tag",
        "risk_flags": [],
    }]
""".strip(),
        encoding="utf-8",
    )
    (bridge_data_dir / "wt_candidates_2026-05-02.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "bridge_data_case",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_export",
                    "trigger_type": "manual_export",
                    "trigger_reason": "loaded from bridge_data",
                    "trigger_score": 87.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "change_pct_60d": 12.0,
                    "amount": 210000000.0,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "setup_tag": "bridge_data_tag",
                    "risk_flags": [],
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps(
            {
                "trade_date": "2026-05-02",
                "top_n": 1,
                "wt_source_mode": "prefer_bridge_data",
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "300001"
    assert rows[0]["scan_source"] == "wondertrader_export"


def test_fingenius_bridge_template_prefers_local_explanation_file(tmp_path: Path) -> None:
    script_dir = tmp_path / "bridge"
    bridge_data_dir = script_dir / "bridge_data"
    script_dir.mkdir()
    bridge_data_dir.mkdir()
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "candidate_x",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_process",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "test",
                    "trigger_score": 87.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bridge_data_dir / "fg_explanations_2026-05-02.json").write_text(
        json.dumps(
            {
                "2026-05-02-300001": {
                    "candidate_id": "2026-05-02-300001",
                    "hot_money_summary": "loaded real hot money",
                    "big_deal_summary": "loaded real big deal",
                    "chip_commentary": "loaded real chip",
                    "sentiment_commentary": "loaded real sentiment",
                    "risk_commentary": "loaded real risk",
                    "short_term_view": "loaded real view",
                    "confidence_label": "high",
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
    assert payload["hot_money_summary"] == "loaded real hot money"
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "bridge_data"
    assert payload["used_upstream_tools"] == []
    assert payload["tool_error_count"] == 0
    assert payload["tool_errors"] == []
    assert payload["explain_elapsed_ms"] >= 0
    assert payload["upstream_tool_elapsed_ms"] == {}


def test_fingenius_bridge_template_uses_real_upstream_tools_when_available(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
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
            "stock_latest_info": [{"股票代码": "300001", "最新价": 21.5, "涨跌幅": 7.6, "换手率": 5.1}],
            "daily_top_list": [{"股票代码": "300001", "上榜原因": "游资席位活跃"}],
            "stock_net_flow": [{"主力净流入": "1.20亿"}],
            "hot_section_data": {"industry": [{"板块名称": "电气设备", "涨跌幅": 3.2}]},
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
                "main_cost_analysis": {"control_level": "中度控盘", "analysis": "主力成本抬升"},
                "concentration_analysis": {"concentration_level": "中度集中", "analysis": "筹码集中度改善"},
                "trading_signals": {"buy_signals": ["筹码集中改善"], "risk_warnings": ["短线追高波动"]},
            }
        })
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
from src.tool.base import ToolResult

class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_big_deal_summary": {"net_inflow_wan": 3200.0, "trade_count": 18},
            "individual_rank_stock": [{"股票代码": "300001", "今日主力净流入-净额": "1.20亿"}],
        })
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
                },
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
    assert "3200.00万" in payload["big_deal_summary"]
    assert "筹码" in payload["chip_commentary"]
    assert "电气设备" in payload["sentiment_commentary"]
    assert payload["confidence_label"] == "high"
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "upstream_tools"
    assert payload["used_upstream_tools"] == [
        "HotMoneyTool",
        "ChipAnalysisTool",
        "BigDealAnalysisTool",
    ]
    assert payload["tool_error_count"] == 0
    assert payload["tool_errors"] == []
    assert payload["explain_elapsed_ms"] >= 0
    assert set(payload["upstream_tool_elapsed_ms"]) == {
        "HotMoneyTool",
        "ChipAnalysisTool",
        "BigDealAnalysisTool",
    }
    assert payload["upstream_tool_elapsed_ms"]["HotMoneyTool"] >= 0
    assert payload["upstream_tool_elapsed_ms"]["ChipAnalysisTool"] >= 0
    assert payload["upstream_tool_elapsed_ms"]["BigDealAnalysisTool"] >= 0


def test_fingenius_bridge_template_skips_big_deal_by_default(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
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
    assert payload["explanation_source"] == "upstream_tools"
    assert payload["used_upstream_tools"] == ["HotMoneyTool", "ChipAnalysisTool"]
    assert payload["tool_error_count"] == 0
    assert payload["tool_errors"] == []
    assert set(payload["upstream_tool_elapsed_ms"]) == {
        "HotMoneyTool",
        "ChipAnalysisTool",
    }
    assert payload["big_deal_summary"] != ""


def test_fingenius_bridge_template_runs_big_deal_when_enabled(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
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
        })
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        """
from src.tool.base import ToolResult

class ChipAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={"analysis": {"trading_signals": {"buy_signals": [], "risk_warnings": []}}})
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
from src.tool.base import ToolResult

class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_big_deal_summary": {"net_inflow_wan": 3200.0, "trade_count": 18},
            "individual_rank_stock": [{"stock_code": "300001", "main_net_inflow": "1.20e8"}],
        })
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
                },
                "bridge_options": {
                    "enable_big_deal": True,
                },
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
    assert "BigDealAnalysisTool" in payload["used_upstream_tools"]
    assert "BigDealAnalysisTool" in payload["upstream_tool_elapsed_ms"]


def test_fingenius_bridge_template_tracks_partial_upstream_tool_failures(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
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
        })
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        """
class ChipAnalysisTool:
    async def execute(self, **kwargs):
        raise RuntimeError("chip tool failed")
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
from src.tool.base import ToolResult

class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_big_deal_summary": {"net_inflow_wan": 3200.0, "trade_count": 18},
            "individual_rank_stock": [{"stock_code": "300001", "main_net_inflow": "1.20e8"}],
        })
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
    assert payload["explanation_source"] == "upstream_tools"
    assert payload["used_upstream_tools"] == ["HotMoneyTool", "BigDealAnalysisTool"]
    assert payload["tool_error_count"] == 1
    assert payload["tool_errors"] == ["ChipAnalysisTool: chip tool failed"]
    assert set(payload["upstream_tool_elapsed_ms"]) == {
        "HotMoneyTool",
        "ChipAnalysisTool",
        "BigDealAnalysisTool",
    }
    assert payload["upstream_tool_elapsed_ms"]["ChipAnalysisTool"] >= 0


def test_fingenius_bridge_template_falls_back_when_real_upstream_tools_fail(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    tool_dir = fingenius_root / "upstream" / "src" / "tool"
    tool_dir.mkdir(parents=True)
    (tool_dir.parent / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "hot_money.py").write_text(
        """
class HotMoneyTool:
    async def execute(self, **kwargs):
        raise RuntimeError("hot money failed")
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        """
class ChipAnalysisTool:
    async def execute(self, **kwargs):
        raise RuntimeError("chip failed")
""".strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        """
class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        raise RuntimeError("big deal failed")
""".strip(),
        encoding="utf-8",
    )

    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-03-300750",
                    "symbol": "300750",
                    "name": "宁德时代",
                    "trade_date": "2026-05-03",
                    "scan_source": "wondertrader_cache_scan",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "loaded from repo spot cache",
                    "trigger_score": 88.4,
                    "price": 198.0,
                    "change_pct": 8.31,
                    "volume_ratio": 2.36,
                    "turnover_rate": 6.42,
                    "board_name": "锂电池",
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
    assert payload["candidate_id"] == "2026-05-03-300750"
    assert "锂电池" in payload["sentiment_commentary"]
    assert "放量突破" in payload["short_term_view"] or "放量突破" in payload["hot_money_summary"]
    assert payload["confidence_label"] == "high"
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "heuristic_fallback"
    assert payload["used_upstream_tools"] == []
    assert payload["tool_error_count"] == 3
    assert payload["tool_errors"] == [
        "HotMoneyTool: hot money failed",
        "ChipAnalysisTool: chip failed",
        "BigDealAnalysisTool: big deal failed",
    ]
    assert payload["explain_elapsed_ms"] >= 0
    assert set(payload["upstream_tool_elapsed_ms"]) == {
        "HotMoneyTool",
        "ChipAnalysisTool",
        "BigDealAnalysisTool",
    }


def test_wondertrader_bridge_template_labels_active_turnover_push_from_repo_spot_cache(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_cache_dir = tmp_path / "daily_stock_analysis" / "data" / "cache" / "reference"
    repo_cache_dir.mkdir(parents=True)
    (repo_cache_dir / "kline_selector_spot_universe.csv").write_text(
        "\n".join(
            [
                "code,name,total_mv,list_date,listed_days,latest_price,pct_change,turnover_rate,volume_ratio,change_pct_60d,amount",
                "600001,閭兏閽㈤搧,,2000-01-01,8000,8.8,5.2,6.5,1.4,12.0,180000000",
            ]
        ),
        encoding="utf-8",
    )
    (repo_cache_dir / "tushare_stock_basic_list.csv").write_text(
        "\n".join(
            [
                "code,name,industry",
                "600001,閭兏閽㈤搧,閽㈤搧",
            ]
        ),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 1}, ensure_ascii=False, indent=2),
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["trigger_type"] == "active_turnover_push"
    assert rows[0]["setup_tag"] == "活跃换手拉升"


def test_wondertrader_bridge_template_labels_high_turnover_explosive_push_from_repo_spot_cache(
    tmp_path: Path,
) -> None:
    wondertrader_root = tmp_path / "WonderTrader"
    script_dir = wondertrader_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_wondertrader_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_wondertrader_bridge_template.py",
        script_path,
    )

    repo_cache_dir = tmp_path / "daily_stock_analysis" / "data" / "cache" / "reference"
    repo_cache_dir.mkdir(parents=True)
    (repo_cache_dir / "kline_selector_spot_universe.csv").write_text(
        "\n".join(
            [
                "code,name,total_mv,list_date,listed_days,latest_price,pct_change,turnover_rate,volume_ratio,change_pct_60d,amount",
                "600002,齐鲁石化,,2000-01-01,8000,12.8,6.8,15.2,1.6,18.0,380000000",
            ]
        ),
        encoding="utf-8",
    )
    (repo_cache_dir / "tushare_stock_basic_list.csv").write_text(
        "\n".join(
            [
                "code,name,industry",
                "600002,齐鲁石化,石油化工",
            ]
        ),
        encoding="utf-8",
    )

    request_path = tmp_path / "wt_request.json"
    output_path = tmp_path / "wt_output.json"
    request_path.write_text(
        json.dumps({"trade_date": "2026-05-02", "top_n": 1}, ensure_ascii=False, indent=2),
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
    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["trigger_type"] == "active_turnover_push"
    assert rows[0]["setup_tag"] == "高换手爆量博弈"


def test_fingenius_bridge_template_builds_heuristic_explanation_when_no_local_file(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-03-300083",
                    "symbol": "300083",
                    "name": "创世纪",
                    "trade_date": "2026-05-03",
                    "scan_source": "wondertrader_cache_scan",
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "loaded from repo spot cache",
                    "trigger_score": 91.2,
                    "price": 0.0,
                    "change_pct": 20.02,
                    "volume_ratio": 0.0,
                    "turnover_rate": 26.82,
                    "board_name": "机器人",
                    "risk_flags": ["limit_up_extension", "high_turnover"],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bridges/shortline_fingenius_bridge_template.py",
            str(request_path),
            str(output_path),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["candidate_id"] == "2026-05-03-300083"
    assert "换手" in payload["hot_money_summary"] or "量比" in payload["hot_money_summary"]
    assert "机器人" in payload["sentiment_commentary"]
    assert "limit_up_extension" in payload["risk_commentary"]
    assert payload["confidence_label"] == "high"
    assert "placeholder" not in json.dumps(payload, ensure_ascii=False).lower()


def test_fingenius_bridge_template_uses_board_name_in_short_term_view(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-03-300750",
                    "symbol": "300750",
                    "name": "瀹佸痉鏃朵唬",
                    "trade_date": "2026-05-03",
                    "scan_source": "wondertrader_cache_scan",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "loaded from repo spot cache",
                    "trigger_score": 88.4,
                    "price": 198.0,
                    "change_pct": 8.31,
                    "volume_ratio": 2.36,
                    "turnover_rate": 6.42,
                    "board_name": "鐢垫睜",
                    "setup_tag": "放量突破",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bridges/shortline_fingenius_bridge_template.py",
            str(request_path),
            str(output_path),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["candidate_id"] == "2026-05-03-300750"
    assert "鐢垫睜" in payload["sentiment_commentary"]
    assert "鐢垫睜" in payload["short_term_view"]
    assert "放量突破" in payload["short_term_view"] or "放量突破" in payload["hot_money_summary"]
