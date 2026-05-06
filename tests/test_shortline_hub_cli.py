# -*- coding: utf-8 -*-
"""Tests for shortline hub CLI."""

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.config import Config
from src.storage import DatabaseManager


def test_shortline_hub_cli_writes_json_and_markdown(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-02",
                "top_n": 2,
                "output_dir": str(tmp_path),
                "run_id": "demo_run",
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    assert (tmp_path / "shortline_candidates.json").exists()
    assert (tmp_path / "shortline_explanations.json").exists()
    assert (tmp_path / "shortline_combined_results.json").exists()
    assert (tmp_path / "shortline_report.md").exists()
    assert (tmp_path / "run_summary.json").exists()


def test_shortline_hub_cli_process_mode_uses_external_scripts(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    wt_script = tmp_path / "wt_mock.py"
    fg_script = tmp_path / "fg_mock.py"
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
    "name": "sample",
    "trade_date": trade_date,
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "mock scan",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "power",
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
bridge_options = payload.get("bridge_options") or {}
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "process hot",
    "big_deal_summary": json.dumps(bridge_options, ensure_ascii=False, sort_keys=True),
    "chip_commentary": "process chip",
    "sentiment_commentary": "process sentiment",
    "risk_commentary": "process risk",
    "short_term_view": "process short view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool"],
    "tool_error_count": 0,
    "tool_errors": [],
    "explain_elapsed_ms": 18,
    "upstream_tool_elapsed_ms": {"HotMoneyTool": 18},
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
                "run_id": "process_demo",
                "log_level": "INFO",
                "mode": "process",
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": "",
                "wt_runtime_dir": str(tmp_path / "wt_runtime"),
                "wt_timeout_seconds": 30,
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": "",
                "fg_runtime_dir": str(tmp_path / "fg_runtime"),
                "fg_timeout_seconds": 30,
                "fg_enable_big_deal": True,
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    assert (tmp_path / "out" / "shortline_candidates.json").exists()
    assert (tmp_path / "out" / "shortline_explanations.json").exists()
    assert (tmp_path / "out" / "shortline_combined_results.json").exists()
    report_text = (tmp_path / "out" / "shortline_report.md").read_text(encoding="utf-8")
    summary_payload = json.loads((tmp_path / "out" / "run_summary.json").read_text(encoding="utf-8"))
    combined_payload = json.loads(
        (tmp_path / "out" / "shortline_combined_results.json").read_text(encoding="utf-8")
    )

    assert "process hot" in report_text
    assert summary_payload["explanation_source_counts"] == {"upstream_tools": 1}
    assert summary_payload["upstream_tool_hit_counts"] == {"HotMoneyTool": 1}
    assert summary_payload["upstream_tool_elapsed_totals_ms"] == {"HotMoneyTool": 18}
    assert summary_payload["orchestrator_explain_elapsed_ms"] >= 0
    assert combined_payload[0]["explanation_source"] == "upstream_tools"
    assert combined_payload[0]["used_upstream_tools"] == ["HotMoneyTool"]
    assert combined_payload[0]["explain_elapsed_ms"] == 18
    assert combined_payload[0]["upstream_tool_elapsed_ms"] == {"HotMoneyTool": 18}
    assert combined_payload[0]["big_deal_summary"] == '{"enable_big_deal": true}'
    assert "FinGenius Explain Summary" in report_text
    assert "upstream_tools=1" in report_text
    assert "HotMoneyTool=18ms" in report_text


def test_shortline_hub_cli_process_mode_passes_wt_source_mode(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    wt_script = tmp_path / "wt_mock.py"
    fg_script = tmp_path / "fg_mock.py"
    wt_script.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
trade_date = payload["trade_date"]
wt_source_mode = payload.get("wt_source_mode") or ""
symbol = "300111" if wt_source_mode == "prefer_bridge_data" else "300222"
rows = [{
    "candidate_id": f"{trade_date}-{symbol}",
    "symbol": symbol,
    "name": wt_source_mode or "default_mode",
    "trade_date": trade_date,
    "scan_source": f"wt_mode::{wt_source_mode or 'missing'}",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "mock scan",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "power",
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
    "hot_money_summary": "process hot",
    "big_deal_summary": "process big",
    "chip_commentary": "process chip",
    "sentiment_commentary": "process sentiment",
    "risk_commentary": "process risk",
    "short_term_view": "process short view",
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
                "run_id": "process_wt_mode_demo",
                "log_level": "INFO",
                "mode": "process",
                "wt_python_executable": sys.executable,
                "wt_script_path": str(wt_script),
                "wt_workdir": "",
                "wt_runtime_dir": str(tmp_path / "wt_runtime"),
                "wt_timeout_seconds": 30,
                "wt_source_mode": "prefer_bridge_data",
                "fg_python_executable": sys.executable,
                "fg_script_path": str(fg_script),
                "fg_workdir": "",
                "fg_runtime_dir": str(tmp_path / "fg_runtime"),
                "fg_timeout_seconds": 30,
                "fg_enable_big_deal": False,
                "manual_watchlist": False,
                "symbols": "",
                "symbols_file": "",
                "persist_snapshot": False,
                "enable_explain_cache": False,
                "explain_cache_path": str(tmp_path / "cache.json"),
                "explain_cache_mode": "light",
                "tracking_history_path": str(tmp_path / "tracking.json"),
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    candidates_payload = json.loads(
        (tmp_path / "out" / "shortline_candidates.json").read_text(encoding="utf-8")
    )
    assert candidates_payload[0]["symbol"] == "300111"
    assert candidates_payload[0]["scan_source"] == "wt_mode::prefer_bridge_data"


def test_shortline_hub_cli_process_mode_can_enable_explain_cache(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    wt_script = tmp_path / "wt_mock.py"
    fg_script = tmp_path / "fg_mock.py"
    explain_cache_path = tmp_path / "cache" / "explain_cache.json"
    fg_counter_path = tmp_path / "fg_counter.txt"

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
    "name": "sample",
    "trade_date": trade_date,
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "mock scan",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "power",
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
counter_path = Path(r"__COUNTER_PATH__")
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
counter = int(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 0
counter += 1
counter_path.write_text(str(counter), encoding="utf-8")
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": f"process hot #{{counter}}",
    "big_deal_summary": "cache demo",
    "chip_commentary": "process chip",
    "sentiment_commentary": "process sentiment",
    "risk_commentary": "process risk",
    "short_term_view": "process short view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool"],
    "tool_error_count": 0,
    "tool_errors": [],
    "explain_elapsed_ms": 18,
    "upstream_tool_elapsed_ms": {"HotMoneyTool": 18},
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip().replace("__COUNTER_PATH__", str(fg_counter_path)),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        trade_date="2026-05-02",
        top_n=1,
        output_dir=str(tmp_path / "out1"),
        run_id="process_cache_demo",
        log_level="INFO",
        mode="process",
        wt_python_executable=sys.executable,
        wt_script_path=str(wt_script),
        wt_workdir="",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        wt_timeout_seconds=30,
        fg_python_executable=sys.executable,
        fg_script_path=str(fg_script),
        fg_workdir="",
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        fg_timeout_seconds=30,
        fg_enable_big_deal=False,
        manual_watchlist=False,
        symbols="",
        symbols_file="",
        persist_snapshot=False,
        enable_explain_cache=True,
        explain_cache_path=str(explain_cache_path),
        explain_cache_mode="light",
    )

    monkeypatch.setattr(module, "parse_args", lambda: args)
    original_build_orchestrator = module._build_orchestrator

    def _build_orchestrator_with_counter(patched_args):
        orchestrator = original_build_orchestrator(patched_args)
        return orchestrator

    monkeypatch.setattr(module, "_build_orchestrator", _build_orchestrator_with_counter)

    first_exit_code = module.main()
    first_summary = json.loads((tmp_path / "out1" / "run_summary.json").read_text(encoding="utf-8"))

    args.output_dir = str(tmp_path / "out2")
    second_exit_code = module.main()
    second_summary = json.loads((tmp_path / "out2" / "run_summary.json").read_text(encoding="utf-8"))

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert first_summary["explain_cache_enabled"] is True
    assert first_summary["explain_cache_mode"] == "light"
    assert first_summary["explain_cache_hit_count"] == 0
    assert first_summary["explain_cache_miss_count"] == 1
    assert second_summary["explain_cache_enabled"] is True
    assert second_summary["explain_cache_hit_count"] == 1
    assert second_summary["explain_cache_miss_count"] == 0
    assert fg_counter_path.read_text(encoding="utf-8") == "1"
    assert explain_cache_path.exists()


def test_shortline_hub_cli_big_deal_mode_does_not_reuse_light_cache(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    wt_script = tmp_path / "wt_mock.py"
    fg_script = tmp_path / "fg_mock.py"
    explain_cache_path = tmp_path / "cache" / "explain_cache.json"
    fg_counter_path = tmp_path / "fg_counter.txt"

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
    "name": "sample",
    "trade_date": trade_date,
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "mock scan",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "power",
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
counter_path = Path(r"__COUNTER_PATH__")
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
bridge_options = payload.get("bridge_options") or {}
counter = int(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 0
counter += 1
counter_path.write_text(str(counter), encoding="utf-8")
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": f"process hot #{counter}",
    "big_deal_summary": json.dumps(bridge_options, ensure_ascii=False, sort_keys=True),
    "chip_commentary": "process chip",
    "sentiment_commentary": "process sentiment",
    "risk_commentary": "process risk",
    "short_term_view": "process short view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool"],
    "tool_error_count": 0,
    "tool_errors": [],
    "explain_elapsed_ms": 18,
    "upstream_tool_elapsed_ms": {"HotMoneyTool": 18},
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip().replace("__COUNTER_PATH__", str(fg_counter_path)),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        trade_date="2026-05-02",
        top_n=1,
        output_dir=str(tmp_path / "out1"),
        run_id="process_light_demo",
        log_level="INFO",
        mode="process",
        wt_python_executable=sys.executable,
        wt_script_path=str(wt_script),
        wt_workdir="",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        wt_timeout_seconds=30,
        fg_python_executable=sys.executable,
        fg_script_path=str(fg_script),
        fg_workdir="",
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        fg_timeout_seconds=30,
        fg_enable_big_deal=False,
        manual_watchlist=False,
        symbols="",
        symbols_file="",
        persist_snapshot=False,
        enable_explain_cache=True,
        explain_cache_path=str(explain_cache_path),
        explain_cache_mode="auto",
    )
    monkeypatch.setattr(module, "parse_args", lambda: args)

    first_exit_code = module.main()
    first_summary = json.loads((tmp_path / "out1" / "run_summary.json").read_text(encoding="utf-8"))

    args.output_dir = str(tmp_path / "out2")
    args.run_id = "process_full_demo"
    args.fg_enable_big_deal = True
    second_exit_code = module.main()
    second_summary = json.loads((tmp_path / "out2" / "run_summary.json").read_text(encoding="utf-8"))

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert first_summary["explain_cache_mode"] == "light"
    assert second_summary["explain_cache_mode"] == "full"
    assert second_summary["explain_cache_hit_count"] == 0
    assert second_summary["explain_cache_miss_count"] == 1
    assert fg_counter_path.read_text(encoding="utf-8") == "2"


def test_shortline_hub_cli_supports_manual_watchlist_mode(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-03",
                "top_n": 2,
                "output_dir": str(tmp_path / "out"),
                "run_id": "manual_watchlist_demo",
                "log_level": "INFO",
                "mode": "stub",
                "wt_python_executable": sys.executable,
                "wt_script_path": "",
                "wt_workdir": "",
                "wt_runtime_dir": str(tmp_path / "wt_runtime"),
                "wt_timeout_seconds": 30,
                "fg_python_executable": sys.executable,
                "fg_script_path": "",
                "fg_workdir": "",
                "fg_runtime_dir": str(tmp_path / "fg_runtime"),
                "fg_timeout_seconds": 30,
                "fg_enable_big_deal": False,
                "manual_watchlist": True,
                "symbols": "300083,688256",
                "symbols_file": "",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    candidates_payload = json.loads(
        (tmp_path / "out" / "shortline_candidates.json").read_text(encoding="utf-8")
    )
    report_text = (tmp_path / "out" / "shortline_report.md").read_text(encoding="utf-8")

    assert [item["symbol"] for item in candidates_payload] == ["300083", "688256"]
    assert all(item["scan_source"] == "manual_watchlist" for item in candidates_payload)
    assert "300083" in report_text


def test_shortline_hub_cli_persists_and_reuses_tracking_history(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    tracking_history_path = tmp_path / "runtime" / "shortline_tracking_history.json"

    first_args = SimpleNamespace(
        trade_date="2026-05-02",
        top_n=1,
        output_dir=str(tmp_path / "out1"),
        run_id="tracking_day1",
        log_level="INFO",
        mode="stub",
        wt_python_executable=sys.executable,
        wt_script_path="",
        wt_workdir="",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        wt_timeout_seconds=30,
        fg_python_executable=sys.executable,
        fg_script_path="",
        fg_workdir="",
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        fg_timeout_seconds=30,
        fg_enable_big_deal=False,
        enable_explain_cache=False,
        explain_cache_path=str(tmp_path / "cache.json"),
        explain_cache_mode="auto",
        manual_watchlist=True,
        symbols="300083",
        symbols_file="",
        persist_snapshot=False,
        tracking_history_path=str(tracking_history_path),
    )
    monkeypatch.setattr(module, "parse_args", lambda: first_args)
    assert module.main() == 0

    second_args = SimpleNamespace(
        trade_date="2026-05-03",
        top_n=1,
        output_dir=str(tmp_path / "out2"),
        run_id="tracking_day2",
        log_level="INFO",
        mode="stub",
        wt_python_executable=sys.executable,
        wt_script_path="",
        wt_workdir="",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        wt_timeout_seconds=30,
        fg_python_executable=sys.executable,
        fg_script_path="",
        fg_workdir="",
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        fg_timeout_seconds=30,
        fg_enable_big_deal=False,
        enable_explain_cache=False,
        explain_cache_path=str(tmp_path / "cache.json"),
        explain_cache_mode="auto",
        manual_watchlist=True,
        symbols="300083",
        symbols_file="",
        persist_snapshot=False,
        tracking_history_path=str(tracking_history_path),
    )
    monkeypatch.setattr(module, "parse_args", lambda: second_args)
    assert module.main() == 0

    combined_payload = json.loads(
        (tmp_path / "out2" / "shortline_combined_results.json").read_text(encoding="utf-8")
    )
    report_text = (tmp_path / "out2" / "shortline_report.md").read_text(encoding="utf-8")

    assert combined_payload[0]["tracking_appear_streak_days"] == 2
    assert combined_payload[0]["tracking_last_seen_dates"] == ["2026-05-02", "2026-05-03"]
    assert (tmp_path / "out2" / "shortline_tracking_history.json").exists()
    assert (tmp_path / "out2" / "shortline_tracking_history.csv").exists()
    assert tracking_history_path.exists()
    assert "连续出现 2 天" in report_text


def test_shortline_hub_parse_args_supports_explain_cache_flags(monkeypatch) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_shortline_hub.py",
            "--trade-date",
            "2026-05-03",
            "--enable-explain-cache",
            "--explain-cache-path",
            "data/runtime/shortline_hub/cache/demo.json",
            "--explain-cache-mode",
            "light",
        ],
    )

    args = module.parse_args()

    assert args.enable_explain_cache is True
    assert args.explain_cache_path == "data/runtime/shortline_hub/cache/demo.json"
    assert args.explain_cache_mode == "light"


def test_shortline_hub_build_orchestrator_injects_db_manager(monkeypatch) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    sentinel_db = object()
    monkeypatch.setattr(module.DatabaseManager, "get_instance", lambda: sentinel_db)

    args = SimpleNamespace(
        trade_date="2026-05-04",
        top_n=1,
        output_dir="unused",
        run_id="db_injection_demo",
        log_level="INFO",
        mode="stub",
        wt_python_executable=sys.executable,
        wt_script_path="",
        wt_workdir="",
        wt_runtime_dir="unused",
        wt_timeout_seconds=30,
        wt_source_mode="prefer_real_engine",
        fg_python_executable=sys.executable,
        fg_script_path="",
        fg_workdir="",
        fg_runtime_dir="unused",
        fg_timeout_seconds=30,
        fg_enable_big_deal=False,
        enable_explain_cache=False,
        explain_cache_path="unused",
        explain_cache_mode="light",
        manual_watchlist=False,
        symbols="",
        symbols_file="",
        persist_snapshot=False,
        tracking_history_path="unused",
    )

    orchestrator = module._build_orchestrator(args)

    assert orchestrator.db_manager is sentinel_db


def test_shortline_hub_cli_can_persist_snapshot(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "shortline_cli.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()

    try:
        monkeypatch.setattr(
            module,
            "parse_args",
            lambda: type(
                "Args",
                (),
                {
                    "trade_date": "2026-05-03",
                    "top_n": 2,
                    "output_dir": str(tmp_path / "out"),
                    "run_id": "persist_snapshot_demo",
                    "log_level": "INFO",
                    "mode": "stub",
                    "wt_python_executable": sys.executable,
                    "wt_script_path": "",
                    "wt_workdir": "",
                    "wt_runtime_dir": str(tmp_path / "wt_runtime"),
                    "wt_timeout_seconds": 30,
                    "fg_python_executable": sys.executable,
                    "fg_script_path": "",
                    "fg_workdir": "",
                    "fg_runtime_dir": str(tmp_path / "fg_runtime"),
                    "fg_timeout_seconds": 30,
                    "fg_enable_big_deal": False,
                    "manual_watchlist": False,
                    "symbols": "",
                    "symbols_file": "",
                    "persist_snapshot": True,
                },
            )(),
        )

        exit_code = module.main()

        db = DatabaseManager.get_instance()
        total_rows = 0
        for signal_type in (
            "shortline_top_pick",
            "shortline_watchlist",
            "shortline_high_risk_mover",
        ):
            total_rows += len(
                db.get_signal_snapshots(
                    signal_type=signal_type,
                    signal_date="2026-05-03",
                )
            )
        assert exit_code == 0
        assert total_rows >= 1
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()
