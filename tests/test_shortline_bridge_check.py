# -*- coding: utf-8 -*-
"""Tests for shortline bridge setup checks."""

from __future__ import annotations

import importlib
import json
import os
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
rows = [{
    "candidate_id": f"{trade_date}-300001",
    "symbol": "300001",
    "name": "mock_wt",
    "trade_date": trade_date,
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "mock wt signal",
    "trigger_score": 88.0,
    "price": 23.4,
    "change_pct": 6.5,
    "volume_ratio": 2.4,
    "turnover_rate": 4.8,
    "board_name": "mock_board",
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
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "mock hot money",
    "big_deal_summary": "mock big deal",
    "chip_commentary": "mock chip",
    "sentiment_commentary": "mock sentiment",
    "risk_commentary": "mock risk",
    "short_term_view": "mock short term view",
    "confidence_label": "high",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )


def test_shortline_bridge_check_inspection_writes_summary(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
    wt_dir = tmp_path / "WonderTrader"
    fg_dir = tmp_path / "FinGenius"
    wt_bridge_dir = wt_dir / "bridge"
    fg_bridge_dir = fg_dir / "bridge"
    wt_bridge_dir.mkdir(parents=True)
    fg_bridge_dir.mkdir(parents=True)
    wt_script = wt_bridge_dir / "wt_export_candidates.py"
    fg_script = fg_bridge_dir / "fg_explain_candidate.py"
    wt_script.write_text("print('wt ok')", encoding="utf-8")
    fg_script.write_text("print('fg ok')", encoding="utf-8")
    (wt_bridge_dir / "bridge_data").mkdir()
    (fg_bridge_dir / "bridge_data").mkdir()
    (wt_bridge_dir / "bridge_data" / "wt_candidates_2026-05-02.json").write_text(
        "[]",
        encoding="utf-8",
    )
    (fg_bridge_dir / "bridge_data" / "fg_explanations_2026-05-02.json").write_text(
        "{}",
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
                "output_dir": str(tmp_path / "out"),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": False,
                "run_orchestrator_smoke": False,
                "top_n": 2,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "out" / "bridge_setup_summary.json").read_text(encoding="utf-8")
    )
    assert summary["trade_date"] == "2026-05-02"
    assert summary["checks"]["wondertrader"]["script_exists"] is True
    assert summary["checks"]["wondertrader"]["data_file_found"] is True
    assert summary["checks"]["fingenius"]["script_exists"] is True
    assert summary["checks"]["fingenius"]["data_file_found"] is True


def test_shortline_bridge_check_script_smoke_runs_bridge_scripts(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
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
                "trade_date": "2026-05-02",
                "output_dir": str(tmp_path / "out"),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": True,
                "run_orchestrator_smoke": False,
                "top_n": 2,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "out" / "bridge_setup_summary.json").read_text(encoding="utf-8")
    )
    assert summary["smoke"]["wondertrader_script"]["status"] == "passed"
    assert summary["smoke"]["fingenius_script"]["status"] == "passed"
    assert (tmp_path / "out" / "smoke" / "wondertrader_output.json").exists()
    assert (tmp_path / "out" / "smoke" / "fingenius_output.json").exists()


def test_shortline_bridge_check_orchestrator_smoke_runs_end_to_end(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
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
                "trade_date": "2026-05-02",
                "output_dir": str(tmp_path / "out"),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": False,
                "run_orchestrator_smoke": True,
                "top_n": 1,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "out" / "bridge_setup_summary.json").read_text(encoding="utf-8")
    )
    assert summary["smoke"]["orchestrator"]["status"] == "passed"
    report_text = (
        tmp_path / "out" / "orchestrator_smoke" / "shortline_report.md"
    ).read_text(encoding="utf-8")
    assert "mock hot money" in report_text


def test_shortline_bridge_check_script_smoke_supports_relative_output_dir_with_external_workdir(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
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

    relative_output_dir = Path("data") / "manual_runs" / "bridge_check_relative_case"
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-02",
                "output_dir": str(relative_output_dir),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": True,
                "run_orchestrator_smoke": False,
                "top_n": 2,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (Path.cwd() / relative_output_dir / "bridge_setup_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["smoke"]["wondertrader_script"]["status"] == "passed"
    assert summary["smoke"]["fingenius_script"]["status"] == "passed"


def test_shortline_bridge_check_marks_stale_bridge_data_warning(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
    wt_dir = tmp_path / "WonderTrader"
    fg_dir = tmp_path / "FinGenius"
    wt_bridge_dir = wt_dir / "bridge"
    fg_bridge_dir = fg_dir / "bridge"
    wt_bridge_dir.mkdir(parents=True)
    fg_bridge_dir.mkdir(parents=True)
    wt_script = wt_bridge_dir / "wt_export_candidates.py"
    fg_script = fg_bridge_dir / "fg_explain_candidate.py"
    wt_script.write_text("print('wt ok')", encoding="utf-8")
    fg_script.write_text("print('fg ok')", encoding="utf-8")
    wt_data_dir = wt_bridge_dir / "bridge_data"
    fg_data_dir = fg_bridge_dir / "bridge_data"
    wt_data_dir.mkdir()
    fg_data_dir.mkdir()
    wt_data_path = wt_data_dir / "wt_candidates_2026-05-02.json"
    fg_data_path = fg_data_dir / "fg_explanations_2026-05-02.json"
    wt_data_path.write_text("[]", encoding="utf-8")
    fg_data_path.write_text("{}", encoding="utf-8")
    old_timestamp = 1_700_000_000
    os.utime(wt_data_path, (old_timestamp, old_timestamp))
    os.utime(fg_data_path, (old_timestamp, old_timestamp))

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-02",
                "output_dir": str(tmp_path / "out"),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": False,
                "run_orchestrator_smoke": False,
                "top_n": 2,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "out" / "bridge_setup_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "warning"
    assert summary["checks"]["wondertrader"]["data_freshness_status"] == "stale"
    assert summary["checks"]["fingenius"]["data_freshness_status"] == "stale"


def test_shortline_bridge_check_treats_missing_bridge_data_as_passed_when_runtime_smoke_is_green(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.check_shortline_bridge_setup")
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
                "trade_date": "2026-05-02",
                "output_dir": str(tmp_path / "out"),
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": str(wt_dir),
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": str(fg_dir),
                "run_script_smoke": True,
                "run_orchestrator_smoke": True,
                "top_n": 1,
                "run_id": "bridge_check_demo",
                "orchestrator_python_executable": sys.executable,
                "orchestrator_script_path": str(Path("scripts") / "run_shortline_hub.py"),
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "out" / "bridge_setup_summary.json").read_text(encoding="utf-8")
    )
    assert summary["checks"]["wondertrader"]["data_file_found"] is False
    assert summary["checks"]["fingenius"]["data_file_found"] is False
    assert summary["smoke"]["wondertrader_script"]["status"] == "passed"
    assert summary["smoke"]["fingenius_script"]["status"] == "passed"
    assert summary["smoke"]["orchestrator"]["status"] == "passed"
    assert summary["overall_status"] == "passed"
