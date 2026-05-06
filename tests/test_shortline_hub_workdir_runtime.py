# -*- coding: utf-8 -*-
"""Regression tests for process-mode runtime paths with external workdirs."""

import importlib
import sys
from pathlib import Path


def test_process_mode_supports_external_workdirs_with_relative_runtime_dirs(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    wt_workdir = tmp_path / "wt_external"
    fg_workdir = tmp_path / "fg_external"
    wt_workdir.mkdir()
    fg_workdir.mkdir()

    wt_script = wt_workdir / "wt_mock.py"
    fg_script = fg_workdir / "fg_mock.py"
    wt_script.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
trade_date = payload["trade_date"]
rows = [{
    "candidate_id": f"{trade_date}-300001",
    "symbol": "300001",
    "name": "workdir_case",
    "trade_date": trade_date,
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "workdir relative runtime dir case",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "board_x",
    "risk_flags": [],
}]
output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    fg_script.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "workdir process hot money",
    "big_deal_summary": "workdir process big deal",
    "chip_commentary": "workdir process chip",
    "sentiment_commentary": "workdir process sentiment",
    "risk_commentary": "workdir process risk",
    "short_term_view": "workdir process view",
    "confidence_label": "high",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-02",
                "top_n": 1,
                "output_dir": str(tmp_path / "out"),
                "run_id": "process_workdir_demo",
                "log_level": "INFO",
                "mode": "process",
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_workdir),
                "wt_runtime_dir": "data/runtime/shortline_hub/wt_external",
                "wt_timeout_seconds": 30,
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_workdir),
                "fg_runtime_dir": "data/runtime/shortline_hub/fg_external",
                "fg_timeout_seconds": 30,
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    report_text = (tmp_path / "out" / "shortline_report.md").read_text(encoding="utf-8")
    assert "workdir process hot money" in report_text
