# -*- coding: utf-8 -*-
"""Regression test for BOM-tolerant FinGenius bridge requests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_fingenius_bridge_template_accepts_utf8_bom_request_file(tmp_path: Path) -> None:
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
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8-sig",
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
