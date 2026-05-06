# -*- coding: utf-8 -*-
"""Tests for shortline bridge-data compare workflow."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _build_mock_wt_script(script_path: Path) -> None:
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
trade_date = payload["trade_date"]
bridge_data_dir = Path(__file__).resolve().parent / "bridge_data"
date_file = bridge_data_dir / f"wt_candidates_{trade_date}.json"
latest_file = bridge_data_dir / "wt_candidates_latest.json"
if date_file.exists():
    rows = json.loads(date_file.read_text(encoding="utf-8"))
elif latest_file.exists():
    rows = json.loads(latest_file.read_text(encoding="utf-8"))
else:
    rows = [{
        "candidate_id": f"{trade_date}-300001",
        "symbol": "300001",
        "name": "fallback_wt",
        "trade_date": trade_date,
        "scan_source": "wondertrader_cache_scan",
        "trigger_type": "momentum_breakout",
        "trigger_reason": "mock cache fallback",
        "trigger_score": 88.0,
        "price": 23.4,
        "change_pct": 6.5,
        "volume_ratio": 2.4,
        "turnover_rate": 4.8,
        "board_name": "fallback_board",
        "setup_tag": "放量突破",
        "risk_flags": [],
    }]
output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )


def _build_mock_fg_script(script_path: Path) -> None:
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
trade_date = candidate["trade_date"]
bridge_data_dir = Path(__file__).resolve().parent / "bridge_data"
date_file = bridge_data_dir / f"fg_explanations_{trade_date}.json"
latest_file = bridge_data_dir / "fg_explanations_latest.json"
mapping = {}
if date_file.exists():
    mapping = json.loads(date_file.read_text(encoding="utf-8"))
elif latest_file.exists():
    mapping = json.loads(latest_file.read_text(encoding="utf-8"))
if candidate["candidate_id"] in mapping:
    result = mapping[candidate["candidate_id"]]
else:
    result = {
        "candidate_id": candidate["candidate_id"],
        "hot_money_summary": "fallback hot money",
        "big_deal_summary": "fallback big deal",
        "chip_commentary": "fallback chip",
        "sentiment_commentary": "fallback sentiment",
        "risk_commentary": "fallback risk",
        "short_term_view": "fallback short-term view",
        "confidence_label": "medium",
    }
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )


def test_shortline_bridge_data_compare_script_seeds_and_compares(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_bridge_data_compare")
    wt_dir = tmp_path / "WonderTrader"
    fg_dir = tmp_path / "FinGenius"
    wt_bridge_dir = wt_dir / "bridge"
    fg_bridge_dir = fg_dir / "bridge"
    wt_bridge_dir.mkdir(parents=True)
    fg_bridge_dir.mkdir(parents=True)
    wt_script = wt_bridge_dir / "wt_export_candidates.py"
    fg_script = fg_bridge_dir / "fg_explain_candidate.py"
    _build_mock_wt_script(wt_script)
    _build_mock_fg_script(fg_script)

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "bridge_data_trade_date": "2026-05-04",
                "fallback_trade_date": "2026-05-05",
                "top_n": 2,
                "output_dir": str(tmp_path / "out"),
                "run_id_prefix": "compare_demo",
                "seed_sample_data": True,
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "wt_runtime_dir": str(tmp_path / "wt_runtime"),
                "wt_timeout_seconds": 30,
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "fg_runtime_dir": str(tmp_path / "fg_runtime"),
                "fg_timeout_seconds": 30,
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads((tmp_path / "out" / "compare_summary.json").read_text(encoding="utf-8"))
    assert summary["bridge_data_seeded"] is True
    assert summary["bridge_data_run"]["trade_date"] == "2026-05-04"
    assert "wondertrader_export" in summary["bridge_data_run"]["scan_sources"]
    assert "wondertrader_cache_scan" in summary["fallback_run"]["scan_sources"]
    assert (wt_bridge_dir / "bridge_data" / "wt_candidates_2026-05-04.json").exists()
    assert (fg_bridge_dir / "bridge_data" / "fg_explanations_2026-05-04.json").exists()
    report_text = (tmp_path / "out" / "compare_report.md").read_text(encoding="utf-8")
    assert "2026-05-04" in report_text
    assert "2026-05-05" in report_text
