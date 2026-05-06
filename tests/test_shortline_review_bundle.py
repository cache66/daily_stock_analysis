# -*- coding: utf-8 -*-
"""Tests for shortline review bundle orchestration."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def test_run_command_does_not_replay_full_child_output_to_parent_logger(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    stdout_path = tmp_path / "logs" / "child.stdout.log"
    stderr_path = tmp_path / "logs" / "child.stderr.log"
    info_mock = Mock()
    warning_mock = Mock()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="line one from child\nline two from child\n",
            stderr="child info on stderr\n",
            returncode=0,
        ),
    )
    monkeypatch.setattr(module.logger, "info", info_mock)
    monkeypatch.setattr(module.logger, "warning", warning_mock)

    return_code = module._run_command(
        command=["python", "child.py"],
        workdir=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert return_code == 0
    assert stdout_path.read_text(encoding="utf-8") == "line one from child\nline two from child\n"
    assert stderr_path.read_text(encoding="utf-8") == "child info on stderr\n"
    assert warning_mock.call_count == 0
    assert info_mock.call_count >= 1
    info_text = " ".join(str(call.args[0]) for call in info_mock.call_args_list if call.args)
    assert "line one from child" not in info_text
    assert "child info on stderr" not in info_text


def test_shortline_review_bundle_main_runs_daily_summary_and_fast_review(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "shortline_review_bundle_latest"
    calls: list[list[str]] = []

    def _mock_run_command(*, command, workdir, stdout_path=None, stderr_path=None):
        calls.append(list(command))
        if any("run_shortline_hub.py" in item for item in command):
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "shortline_bundle_20260503",
                        "trade_date": "2026-05-03",
                        "candidate_count": 3,
                        "explanation_count": 3,
                        "review_tier_counts": {"watchlist": 2, "top_pick": 1},
                        "scan_source_counts": {"wondertrader_real_engine": 3},
                        "upstream_tool_hit_counts": {"HotMoneyTool": 3},
                        "total_explain_elapsed_ms": 48,
                        "orchestrator_explain_elapsed_ms": 31,
                        "explain_cache_enabled": True,
                        "explain_cache_mode": "light",
                        "explain_cache_hit_count": 2,
                        "explain_cache_miss_count": 1,
                        "explain_parallel_workers": 1,
                        "tracking_repeat_symbol_count": 2,
                        "tracking_longest_streak_days": 3,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (shortline_dir / "shortline_report.md").write_text(
                "# shortline report\n",
                encoding="utf-8",
            )
        elif any("summarize_shortline_runs.py" in item for item in command):
            summary_dir = output_dir / "shortline_runs_summary"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "shortline_runs_summary.json").write_text(
                json.dumps(
                    {
                        "run_count": 4,
                        "latest_run_id": "shortline_bundle_20260503",
                        "trade_date_counts": {"2026-05-03": 4},
                        "upstream_tool_counts": {"HotMoneyTool": 4},
                        "cache_enabled_run_count": 4,
                        "total_explain_cache_hits": 9,
                        "total_explain_cache_misses": 6,
                        "max_explain_parallel_workers": 2,
                        "anomaly_counts": {"cache_hit_ratio_low": 1, "tool_errors_present": 1},
                        "latest_run_anomalies": ["cache_hit_ratio_low"],
                        "latest_run_quality": {
                            "profile": "standard",
                            "verdict": "degraded",
                            "failed_checks": [],
                            "warning_checks": ["cache_health_low"],
                            "passed_checks": ["tool_errors_absent"],
                            "recommendations": ["Warm or inspect explain cache because misses exceeded hits."],
                        },
                        "quality_verdict_counts": {"degraded": 3, "pass": 1},
                        "runs": [
                            {
                                "candidate_count": 3,
                                "total_explain_elapsed_ms": 48,
                                "top_symbols": ["300001", "300002"],
                                "used_upstream_tools": ["HotMoneyTool"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (summary_dir / "shortline_runs_summary.md").write_text(
                "# runs summary\n",
                encoding="utf-8",
            )
        elif any("run_fast_review_bundle.py" in item for item in command):
            review_dir = output_dir / "fast_review"
            day_review_dir = review_dir / "2026-05-03" / "review"
            day_review_dir.mkdir(parents=True, exist_ok=True)
            (day_review_dir / "fast_review_summary.md").write_text(
                "# fast review summary\n",
                encoding="utf-8",
            )
            (review_dir / "fast_review_summary_latest.md").write_text(
                "# fast review summary latest\n",
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=3,
            output_dir=str(output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            persist_snapshot=True,
            enable_explain_cache=True,
            explain_cache_path=str(tmp_path / "runtime" / "explain_cache" / "bundle_cache.json"),
            explain_cache_mode="light",
            tracking_history_path=str(tmp_path / "runtime" / "tracking" / "bundle_tracking_history.json"),
            fast_review_persist_snapshots=True,
            dry_run=False,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=5,
            runs_summary_limit=12,
            quality_profile="standard",
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    assert exit_code == 0
    assert len(calls) == 3
    assert any("run_shortline_hub.py" in item for item in calls[0])
    assert "--persist-snapshot" in calls[0]
    assert "--enable-explain-cache" in calls[0]
    assert "--explain-cache-path" in calls[0]
    assert any("summarize_shortline_runs.py" in item for item in calls[1])
    assert "--quality-profile" in calls[1]
    assert calls[1][calls[1].index("--quality-profile") + 1] == "standard"
    assert any("run_fast_review_bundle.py" in item for item in calls[2])
    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    bundle_report = (output_dir / "bundle_report.md").read_text(encoding="utf-8")
    assert manifest["trade_date"] == "2026-05-03"
    assert manifest["execution_status"] == "success"
    assert manifest["status"] == "success"
    assert manifest["overall_status"] == "success"
    assert manifest["shortline_run"]["run_id"] == "shortline_bundle_20260503"
    assert manifest["runs_summary"]["run_count"] == 4
    assert manifest["steps"]["shortline"]["status"] == "success"
    assert manifest["steps"]["shortline"]["elapsed_ms"] >= 0
    assert manifest["steps"]["shortline"]["stdout_log_path"].endswith("logs/shortline.stdout.log")
    assert manifest["shortline_run"]["explain_cache_hit_count"] == 2
    assert manifest["shortline_run"]["explain_cache_miss_count"] == 1
    assert manifest["shortline_run"]["explain_parallel_workers"] == 1
    assert manifest["shortline_run"]["tracking_repeat_symbol_count"] == 2
    assert manifest["shortline_run"]["tracking_longest_streak_days"] == 3
    assert manifest["runs_summary"]["total_explain_cache_hits"] == 9
    assert manifest["runs_summary"]["total_explain_cache_misses"] == 6
    assert manifest["runs_summary"]["max_explain_parallel_workers"] == 2
    assert manifest["runs_summary"]["anomaly_counts"] == {
        "cache_hit_ratio_low": 1,
        "tool_errors_present": 1,
    }
    assert manifest["runs_summary"]["latest_run_anomalies"] == ["cache_hit_ratio_low"]
    assert manifest["quality_summary"]["verdict"] == "degraded"
    assert manifest["quality_summary"]["warning_checks"] == ["cache_health_low"]
    assert manifest["quality_summary"]["signals"]["tracking_history_ready"] is False
    assert manifest["quality_summary"]["signals"]["source_real_engine_expected_passed"] is True
    assert manifest["quality_failed_due_to"] == []
    assert "shortline_run/shortline_report.md" in bundle_report.replace("\\", "/")
    assert "fast_review_summary_latest.md" in bundle_report
    assert "cache: enabled=True, mode=light, hits=2, misses=1" in bundle_report
    assert "parallel_workers: 1" in bundle_report
    assert "tracking: repeat_symbol_count=2, longest_streak_days=3" in bundle_report
    assert "cache_totals: hits=9, misses=6, cache_enabled_runs=4" in bundle_report
    assert "max_parallel_workers: 2" in bundle_report
    assert "anomaly_counts: cache_hit_ratio_low=1, tool_errors_present=1" in bundle_report
    assert "latest_run_anomalies: cache_hit_ratio_low" in bundle_report
    assert "quality_verdict: degraded" in bundle_report
    assert "execution_status: success" in bundle_report
    assert "overall_status: success" in bundle_report
    assert "quality_failed_due_to: -" in bundle_report
    assert "tracking_history_ready: False" in bundle_report
    assert "source_real_engine_expected_passed: True" in bundle_report
    assert "warning_checks: cache_health_low" in bundle_report


def test_shortline_review_bundle_promotes_top_level_status_when_quality_fails(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "shortline_review_bundle_quality_fail"
    manual_runs_root = tmp_path / "manual_runs_root"

    def _mock_run_command(*, command, workdir, stdout_path=None, stderr_path=None):
        if any("run_shortline_hub.py" in item for item in command):
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "shortline_bundle_quality_fail",
                        "trade_date": "2026-05-03",
                        "candidate_count": 3,
                        "explanation_count": 3,
                        "review_tier_counts": {"watchlist": 3},
                        "scan_source_counts": {"wondertrader_bridge_data": 3},
                        "upstream_tool_hit_counts": {"HotMoneyTool": 3},
                        "total_explain_elapsed_ms": 48,
                        "orchestrator_explain_elapsed_ms": 31,
                        "explain_cache_enabled": False,
                        "explain_cache_mode": "",
                        "explain_cache_hit_count": 0,
                        "explain_cache_miss_count": 0,
                        "explain_parallel_workers": 0,
                        "tracking_repeat_symbol_count": 2,
                        "tracking_longest_streak_days": 3,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (shortline_dir / "shortline_report.md").write_text("# shortline report\n", encoding="utf-8")
        elif any("summarize_shortline_runs.py" in item for item in command):
            summary_dir = output_dir / "shortline_runs_summary"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "shortline_runs_summary.json").write_text(
                json.dumps(
                    {
                        "run_count": 1,
                        "latest_run_id": "shortline_bundle_quality_fail",
                        "trade_date_counts": {"2026-05-03": 1},
                        "upstream_tool_counts": {"HotMoneyTool": 1},
                        "cache_enabled_run_count": 0,
                        "total_explain_cache_hits": 0,
                        "total_explain_cache_misses": 0,
                        "max_explain_parallel_workers": 0,
                        "anomaly_counts": {},
                        "latest_run_anomalies": [],
                        "latest_run_quality": {
                            "profile": "standard",
                            "verdict": "pass",
                            "failed_checks": [],
                            "warning_checks": [],
                            "passed_checks": ["tool_errors_absent"],
                            "recommendations": [],
                        },
                        "runs": [{"candidate_count": 3, "total_explain_elapsed_ms": 48, "top_symbols": ["300001"]}],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (summary_dir / "shortline_runs_summary.md").write_text("# runs summary\n", encoding="utf-8")
        elif any("run_fast_review_bundle.py" in item for item in command):
            review_dir = output_dir / "fast_review"
            day_review_dir = review_dir / "2026-05-03" / "review"
            day_review_dir.mkdir(parents=True, exist_ok=True)
            (day_review_dir / "fast_review_summary.md").write_text("# fast review summary\n", encoding="utf-8")
            (review_dir / "fast_review_summary_latest.md").write_text(
                "# fast review summary latest\n",
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(module, "DEFAULT_MANUAL_RUNS_ROOT", manual_runs_root)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=3,
            output_dir=str(output_dir),
            run_id="bundle_qf",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            persist_snapshot=True,
            enable_explain_cache=False,
            explain_cache_path=str(tmp_path / "runtime" / "explain_cache" / "bundle_cache.json"),
            explain_cache_mode="light",
            tracking_history_path=str(tmp_path / "runtime" / "tracking" / "bundle_tracking_history.json"),
            fast_review_persist_snapshots=True,
            dry_run=False,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=5,
            runs_summary_limit=12,
            quality_profile="standard",
            log_level="INFO",
            runs_root=str(tmp_path / "manual_runs"),
            wt_source_mode="prefer_real_engine",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    bundle_report = (output_dir / "bundle_report.md").read_text(encoding="utf-8")
    pointer_path = (
        manual_runs_root
        / "shortline_review_bundle_index"
        / "2026-05-03"
        / "bundle_qf"
        / "bundle_pointer.json"
    )
    pointer_payload = json.loads(pointer_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert manifest["execution_status"] == "success"
    assert manifest["status"] == "success"
    assert manifest["overall_status"] == "quality_failed"
    assert manifest["quality_summary"]["verdict"] == "fail"
    assert manifest["quality_summary"]["failed_checks"] == ["source_real_engine_expected"]
    assert manifest["quality_summary"]["signals"]["source_real_engine_expected_passed"] is False
    assert manifest["quality_failed_due_to"] == ["source_real_engine_expected"]
    assert pointer_payload["execution_status"] == "success"
    assert pointer_payload["status"] == "success"
    assert pointer_payload["overall_status"] == "quality_failed"
    assert "status: success" in bundle_report
    assert "execution_status: success" in bundle_report
    assert "overall_status: quality_failed" in bundle_report
    assert "quality_failed_due_to: source_real_engine_expected" in bundle_report
    assert "source_real_engine_expected_passed: False" in bundle_report


def test_shortline_review_bundle_commands_use_absolute_output_paths(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    args = SimpleNamespace(
        trade_date="2026-05-03",
        top_n=1,
        repo_python_executable="python",
        wt_python_executable="python",
        fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
        wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
        fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
        wt_workdir="D:\\bb\\WonderTrader",
        fg_workdir="D:\\bb\\FinGenius",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        runs_root=str(tmp_path / "manual_runs"),
        runs_summary_limit=12,
        fast_review_include_signals="hundred_day_high",
        fast_review_limit=3,
        quality_profile="strict",
        persist_snapshot=True,
        enable_explain_cache=True,
        explain_cache_path=str(tmp_path / "runtime" / "explain_cache" / "bundle_cache.json"),
        explain_cache_mode="light",
        tracking_history_path=str(tmp_path / "runtime" / "tracking" / "bundle_tracking_history.json"),
        fast_review_persist_snapshots=True,
        log_level="INFO",
    )
    bundle_output_dir = tmp_path / "bundle_latest"

    shortline_command = module.build_shortline_command(
        args=args,
        run_id="shortline_bundle_20260503",
        output_dir=bundle_output_dir / "shortline_run",
    )
    runs_summary_command = module.build_runs_summary_command(
        args=args,
        output_dir=bundle_output_dir / "shortline_runs_summary",
    )
    fast_review_command = module.build_fast_review_command(
        args=args,
        output_dir=bundle_output_dir / "fast_review",
    )

    assert shortline_command[shortline_command.index("--output-dir") + 1] == str(
        (bundle_output_dir / "shortline_run").resolve()
    )
    assert shortline_command[shortline_command.index("--explain-cache-path") + 1] == str(
        (Path(args.explain_cache_path)).resolve()
    )
    assert shortline_command[shortline_command.index("--tracking-history-path") + 1] == str(
        (Path(args.tracking_history_path)).resolve()
    )
    assert runs_summary_command[runs_summary_command.index("--output-dir") + 1] == str(
        (bundle_output_dir / "shortline_runs_summary").resolve()
    )
    assert runs_summary_command[runs_summary_command.index("--quality-profile") + 1] == "strict"
    assert fast_review_command[fast_review_command.index("--output-dir") + 1] == str(
        (bundle_output_dir / "fast_review").resolve()
    )


def test_evaluate_bundle_quality_flags_non_real_engine_source() -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")

    quality = module.evaluate_bundle_quality(
        dry_run=False,
        quality_profile="standard",
        wt_source_mode="prefer_real_engine",
        current_trade_date="2026-05-03",
        shortline_summary={
            "candidate_count": 3,
            "explanation_count": 3,
            "scan_source_counts": {"wondertrader_bridge_data": 3},
            "explain_cache_enabled": False,
            "explain_cache_hit_count": 0,
            "explain_cache_miss_count": 0,
            "explain_parallel_workers": 0,
            "tracking_repeat_symbol_count": 1,
            "report_path": "shortline_run/shortline_report.md",
            "summary_json_path": "shortline_run/run_summary.json",
        },
        runs_summary_payload={"latest_run_quality": {"verdict": "pass", "warning_checks": [], "failed_checks": []}},
    )

    assert quality["verdict"] == "fail"
    assert quality["failed_checks"] == ["source_real_engine_expected"]


def test_evaluate_bundle_quality_skips_tracking_warning_when_history_not_ready() -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")

    quality = module.evaluate_bundle_quality(
        dry_run=False,
        quality_profile="standard",
        wt_source_mode="prefer_real_engine",
        current_trade_date="2026-05-03",
        shortline_summary={
            "candidate_count": 3,
            "explanation_count": 3,
            "scan_source_counts": {"wondertrader_real_engine": 3},
            "explain_cache_enabled": False,
            "explain_cache_hit_count": 0,
            "explain_cache_miss_count": 0,
            "explain_parallel_workers": 0,
            "tracking_repeat_symbol_count": 0,
            "report_path": "shortline_run/shortline_report.md",
            "summary_json_path": "shortline_run/run_summary.json",
        },
        runs_summary_payload={
            "trade_date_counts": {"2026-05-03": 1},
            "latest_run_quality": {"verdict": "pass", "warning_checks": [], "failed_checks": []},
        },
    )

    assert quality["verdict"] == "pass"
    assert "tracking_continuity_weak" not in quality["warning_checks"]


def test_shortline_review_bundle_commands_default_to_current_interpreter_when_repo_python_not_provided(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    args = SimpleNamespace(
        trade_date="2026-05-03",
        top_n=1,
        fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
        wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
        fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
        wt_workdir="D:\\bb\\WonderTrader",
        fg_workdir="D:\\bb\\FinGenius",
        wt_runtime_dir=str(tmp_path / "wt_runtime"),
        fg_runtime_dir=str(tmp_path / "fg_runtime"),
        runs_root=str(tmp_path / "manual_runs"),
        runs_summary_limit=12,
        fast_review_include_signals="hundred_day_high",
        fast_review_limit=3,
        persist_snapshot=False,
        enable_explain_cache=False,
        explain_cache_path=str(tmp_path / "runtime" / "explain_cache" / "bundle_cache.json"),
        explain_cache_mode="light",
        tracking_history_path="",
        fast_review_persist_snapshots=False,
        log_level="INFO",
    )

    shortline_command = module.build_shortline_command(
        args=args,
        run_id="bundle_r_py",
        output_dir=tmp_path / "shortline_run",
    )
    runs_summary_command = module.build_runs_summary_command(
        args=args,
        output_dir=tmp_path / "shortline_runs_summary",
    )
    fast_review_command = module.build_fast_review_command(
        args=args,
        output_dir=tmp_path / "fast_review",
    )

    assert shortline_command[0] == sys.executable
    assert runs_summary_command[0] == sys.executable
    assert fast_review_command[0] == sys.executable
    assert shortline_command[shortline_command.index("--wt-python-executable") + 1] == sys.executable
    assert "--enable-explain-cache" not in shortline_command


def test_build_fast_review_command_can_skip_snapshot_persist_independently(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    args = SimpleNamespace(
        trade_date="2026-05-03",
        repo_python_executable="python",
        fast_review_include_signals="hundred_day_high",
        fast_review_limit=3,
        fast_review_persist_snapshots=False,
        log_level="INFO",
    )

    fast_review_command = module.build_fast_review_command(
        args=args,
        output_dir=tmp_path / "fast_review",
    )

    assert "--skip-persist-snapshots" in fast_review_command
    assert "--persist-snapshots" not in fast_review_command


def test_shortline_review_bundle_main_normalizes_relative_output_dir_for_child_commands(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    commands: list[list[str]] = []
    relative_output_dir = Path("data/manual_runs/shortline_review_bundle_relative_case")

    def _mock_run_command(*, command, workdir, stdout_path=None, stderr_path=None):
        commands.append(list(command))
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(relative_output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            persist_snapshot=False,
            fast_review_persist_snapshots=True,
            dry_run=False,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)
    monkeypatch.setattr(module, "_load_json", lambda _path: {})

    exit_code = module.main()

    expected_shortline_dir = str((module.PROJECT_ROOT / relative_output_dir / "shortline_run").resolve())
    expected_summary_dir = str((module.PROJECT_ROOT / relative_output_dir / "shortline_runs_summary").resolve())
    expected_fast_review_dir = str((module.PROJECT_ROOT / relative_output_dir / "fast_review").resolve())

    assert exit_code == 0
    assert commands[0][commands[0].index("--output-dir") + 1] == expected_shortline_dir
    assert commands[1][commands[1].index("--output-dir") + 1] == expected_summary_dir
    assert commands[2][commands[2].index("--output-dir") + 1] == expected_fast_review_dir


def test_shortline_review_bundle_main_uses_project_root_as_child_workdir(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    workdirs: list[Path] = []

    def _mock_run_command(*, command, workdir, stdout_path=None, stderr_path=None):
        workdirs.append(Path(workdir))
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(tmp_path / "bundle"),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            persist_snapshot=False,
            fast_review_persist_snapshots=True,
            dry_run=False,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)
    monkeypatch.setattr(module, "_load_json", lambda _path: {})

    exit_code = module.main()

    assert exit_code == 0
    assert workdirs == [module.PROJECT_ROOT, module.PROJECT_ROOT, module.PROJECT_ROOT]


def test_shortline_review_bundle_main_dry_run_writes_manifest_without_executing_steps(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "bundle"
    calls: list[list[str]] = []

    def _mock_run_command(*, command, workdir, stdout_path=None, stderr_path=None):
        calls.append(list(command))
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            persist_snapshot=True,
            fast_review_persist_snapshots=False,
            dry_run=True,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert calls == []
    assert manifest["dry_run"] is True
    assert manifest["steps"]["shortline"]["status"] == "dry_run"
    assert manifest["steps"]["runs_summary"]["status"] == "dry_run"
    assert manifest["steps"]["fast_review"]["status"] == "dry_run"
    assert "--skip-persist-snapshots" in manifest["steps"]["fast_review"]["command"]


def test_shortline_review_bundle_main_records_step_telemetry_and_logs(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "bundle"

    def _mock_run_command(*, command, workdir, stdout_path, stderr_path):
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("ok\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        if any("run_shortline_hub.py" in item for item in command):
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps({"candidate_count": 2}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif any("summarize_shortline_runs.py" in item for item in command):
            summary_dir = output_dir / "shortline_runs_summary"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "shortline_runs_summary.json").write_text(
                json.dumps({"run_count": 6, "latest_run_id": "r1"}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif any("run_fast_review_bundle.py" in item for item in command):
            review_dir = output_dir / "fast_review"
            (review_dir / "2026-05-03" / "review").mkdir(parents=True, exist_ok=True)
            (review_dir / "fast_review_summary_latest.md").write_text("# latest\n", encoding="utf-8")
            (review_dir / "2026-05-03" / "review" / "fast_review_summary.md").write_text(
                "# review\n",
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            persist_snapshot=True,
            fast_review_persist_snapshots=True,
            dry_run=False,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    shortline_step = manifest["steps"]["shortline"]
    fast_review_step = manifest["steps"]["fast_review"]

    assert exit_code == 0
    assert shortline_step["status"] == "success"
    assert shortline_step["elapsed_ms"] >= 0
    assert shortline_step["stdout_log_path"].endswith("logs/shortline.stdout.log")
    assert shortline_step["stderr_log_path"].endswith("logs/shortline.stderr.log")
    assert Path(output_dir / "logs" / "shortline.stdout.log").read_text(encoding="utf-8") == "ok\n"
    assert fast_review_step["workdir"].replace("\\", "/").endswith("daily_stock_analysis")


def test_shortline_review_bundle_main_collects_fast_review_runtime_summary(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "bundle"

    def _mock_run_command(*, command, workdir, stdout_path, stderr_path):
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.write_text("", encoding="utf-8")
        if any("run_shortline_hub.py" in item for item in command):
            stdout_path.write_text("", encoding="utf-8")
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps({"candidate_count": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif any("summarize_shortline_runs.py" in item for item in command):
            stdout_path.write_text("", encoding="utf-8")
            summary_dir = output_dir / "shortline_runs_summary"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "shortline_runs_summary.json").write_text(
                json.dumps({"run_count": 3, "latest_run_id": "r1"}, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            stdout_path.write_text(
                "\n".join(
                    [
                        "snapshot_date=2026-05-03",
                        "include_signals=hundred_day_high,trend_leader",
                        "persist_snapshots=False",
                        "signal_hundred_day_high_elapsed_sec=12.34",
                        "signal_trend_leader_elapsed_sec=5.67",
                        "skipped_signals_count=1",
                        "skipped_signal=trend_leader|trend_leader_unified|no_rows|no candidate rows loaded from csv",
                        "summary_md=D:\\tmp\\fast_review_summary.md",
                        "latest_md=D:\\tmp\\fast_review_summary_latest.md",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stderr_path.write_text(
                "2026-05-03 15:00:00,000 [INFO] fast_review_bundle: fast review signal done: key=hundred_day_high count=4 csv=D:\\tmp\\hundred.csv elapsed=12.34s\n"
                "2026-05-03 15:00:02,000 [INFO] fast_review_bundle: fast review signal done: key=trend_leader count=2 csv=D:\\tmp\\trend.csv elapsed=5.67s\n",
                encoding="utf-8",
            )
            review_dir = output_dir / "fast_review"
            (review_dir / "2026-05-03" / "review").mkdir(parents=True, exist_ok=True)
            (review_dir / "fast_review_summary_latest.md").write_text("# latest\n", encoding="utf-8")
            (review_dir / "2026-05-03" / "review" / "fast_review_summary.md").write_text(
                "# review\n",
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            persist_snapshot=False,
            fast_review_persist_snapshots=False,
            dry_run=False,
            fast_review_include_signals="hundred_day_high,trend_leader",
            fast_review_limit=3,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    fast_review = manifest["fast_review"]
    assert exit_code == 0
    assert fast_review["runtime_summary"]["persist_snapshots"] is False
    assert fast_review["runtime_summary"]["include_signals"] == "hundred_day_high,trend_leader"
    assert fast_review["runtime_summary"]["signal_elapsed_sec"]["hundred_day_high"] == 12.34
    assert fast_review["runtime_summary"]["signal_elapsed_sec"]["trend_leader"] == 5.67
    assert fast_review["runtime_summary"]["signals"]["hundred_day_high"]["count"] == 4
    assert fast_review["runtime_summary"]["signals"]["trend_leader"]["count"] == 2
    assert fast_review["runtime_summary"]["signals"]["trend_leader"]["csv_path"] == "D:\\tmp\\trend.csv"
    assert fast_review["runtime_summary"]["total_signal_elapsed_sec"] == 18.01
    assert fast_review["runtime_summary"]["total_signal_count"] == 6
    assert fast_review["runtime_summary"]["skipped_reason_counts"]["no_rows"] == 1
    assert fast_review["runtime_summary"]["output_paths"]["summary_md"] == "D:\\tmp\\fast_review_summary.md"
    assert fast_review["runtime_summary"]["skipped_signals_count"] == 1
    assert fast_review["runtime_summary"]["skipped_signals"][0]["signal_key"] == "trend_leader"


def test_shortline_review_bundle_main_writes_artifact_governance_pointers(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "custom_bundle_output"
    manual_runs_root = tmp_path / "manual_runs_root"

    def _mock_run_command(*, command, workdir, stdout_path, stderr_path):
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        if any("run_shortline_hub.py" in item for item in command):
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "candidate_count": 1,
                        "explanation_count": 1,
                        "run_id": "bundle_r1",
                        "scan_source_counts": {"wondertrader_real_engine": 1},
                        "tracking_repeat_symbol_count": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        elif any("summarize_shortline_runs.py" in item for item in command):
            summary_dir = output_dir / "shortline_runs_summary"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "shortline_runs_summary.json").write_text(
                json.dumps({"run_count": 2, "latest_run_id": "bundle_r1"}, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            review_dir = output_dir / "fast_review"
            (review_dir / "2026-05-03" / "review").mkdir(parents=True, exist_ok=True)
            (review_dir / "fast_review_summary_latest.md").write_text("# latest\n", encoding="utf-8")
            (review_dir / "2026-05-03" / "review" / "fast_review_summary.md").write_text(
                "# review\n",
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(module, "DEFAULT_MANUAL_RUNS_ROOT", manual_runs_root)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(output_dir),
            run_id="bundle_r1",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            persist_snapshot=False,
            fast_review_persist_snapshots=False,
            dry_run=False,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    governance = manifest["artifact_governance"]
    pointer_path = manual_runs_root / "shortline_review_bundle_index" / "2026-05-03" / "bundle_r1" / "bundle_pointer.json"
    latest_path = manual_runs_root / "shortline_review_bundle_index" / "latest.json"

    assert exit_code == 0
    assert governance["output_class"] == "custom"
    assert governance["pointer_json_path"].replace("\\", "/").endswith(
        "shortline_review_bundle_index/2026-05-03/bundle_r1/bundle_pointer.json"
    )
    assert pointer_path.exists()
    assert latest_path.exists()
    pointer_payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    assert pointer_payload["output_dir"] == str(output_dir.resolve())
    assert pointer_payload["status"] == "success"
    assert pointer_payload["execution_status"] == "success"
    assert pointer_payload["overall_status"] == "success"
    assert latest_payload["run_id"] == "bundle_r1"


def test_shortline_review_bundle_main_writes_failure_manifest_and_report(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    output_dir = tmp_path / "bundle"

    def _mock_run_command(*, command, workdir, stdout_path, stderr_path):
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        if any("run_shortline_hub.py" in item for item in command):
            stdout_path.write_text("shortline ok\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            shortline_dir = output_dir / "shortline_run"
            shortline_dir.mkdir(parents=True, exist_ok=True)
            (shortline_dir / "run_summary.json").write_text(
                json.dumps({"candidate_count": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            return 0

        stdout_path.write_text("summary failed soon\nlast line\n", encoding="utf-8")
        stderr_path.write_text("Traceback: boom\n", encoding="utf-8")
        raise RuntimeError("bundle step failed: command=python scripts/summarize_shortline_runs.py returncode=2")

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            trade_date="2026-05-03",
            top_n=1,
            output_dir=str(output_dir),
            run_id="",
            repo_python_executable="python",
            wt_python_executable="python",
            fg_python_executable="D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe",
            wt_script_path="D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py",
            fg_script_path="D:\\bb\\FinGenius\\bridge\\fg_explain_candidate.py",
            wt_workdir="D:\\bb\\WonderTrader",
            fg_workdir="D:\\bb\\FinGenius",
            wt_runtime_dir=str(tmp_path / "wt_runtime"),
            fg_runtime_dir=str(tmp_path / "fg_runtime"),
            runs_root=str(tmp_path / "manual_runs"),
            runs_summary_limit=12,
            persist_snapshot=False,
            fast_review_persist_snapshots=False,
            dry_run=False,
            fast_review_include_signals="hundred_day_high",
            fast_review_limit=3,
            log_level="INFO",
        ),
    )
    monkeypatch.setattr(module, "_run_command", _mock_run_command)

    exit_code = module.main()

    manifest = json.loads((output_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    report = (output_dir / "bundle_report.md").read_text(encoding="utf-8")
    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["overall_status"] == "failed"
    assert manifest["failed_step"] == "runs_summary"
    assert manifest["failure"]["step"] == "runs_summary"
    assert "summarize_shortline_runs.py" in manifest["failure"]["error_message"]
    assert manifest["failure"]["stdout_log_path"].endswith("logs/runs_summary.stdout.log")
    assert manifest["failure"]["stderr_log_path"].endswith("logs/runs_summary.stderr.log")
    assert manifest["failure"]["pending_steps"] == ["fast_review"]
    assert manifest["steps"]["shortline"]["status"] == "success"
    assert manifest["steps"]["runs_summary"]["status"] == "failed"
    assert manifest["steps"]["runs_summary"]["diagnostics"]["stderr_excerpt"] == "Traceback: boom"
    assert manifest["steps"]["fast_review"]["status"] == "pending"
    assert manifest["artifact_governance"]["pointer_json_path"]
    assert "Failed Step" in report
    assert "- stdout_log:" in report
    assert "- stderr_log:" in report
    assert "- stdout_excerpt: summary failed soon" in report
    assert "- stderr_excerpt: Traceback: boom" in report
    assert "- pending_steps: fast_review" in report


def test_shortline_review_bundle_report_shows_compact_fast_review_runtime_summary() -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    manifest = {
        "trade_date": "2026-05-03",
        "run_id": "bundle_r1",
        "generated_at": "2026-05-04T07:00:00",
        "dry_run": False,
        "status": "success",
        "artifact_governance": {
            "output_class": "smoke",
            "pointer_json_path": "data/manual_runs/shortline_review_bundle_index/2026-05-03/bundle_r1/bundle_pointer.json",
            "latest_json_path": "data/manual_runs/shortline_review_bundle_index/latest.json",
        },
        "shortline_run": {"output_dir": "a", "report_path": "b", "candidate_count": 1},
        "runs_summary": {"output_dir": "c", "summary_md_path": "d", "run_count": 2, "latest_run_id": "bundle_r1"},
        "fast_review": {
            "output_dir": "e",
            "latest_md_path": "f",
            "summary_md_path": "g",
            "runtime_summary": {
                "include_signals": "hundred_day_high,trend_leader",
                "persist_snapshots": False,
                "total_signal_count": 6,
                "skipped_signals_count": 2,
                "total_signal_elapsed_sec": 18.01,
                "skipped_reason_counts": {"no_rows": 2},
                "signal_elapsed_sec": {"hundred_day_high": 12.34, "trend_leader": 5.67},
            },
        },
        "steps": {
            "shortline": {"status": "success", "elapsed_ms": 100, "stdout_log_path": "x", "stderr_log_path": "y"},
            "runs_summary": {"status": "success", "elapsed_ms": 50, "stdout_log_path": "x2", "stderr_log_path": "y2"},
            "fast_review": {"status": "success", "elapsed_ms": 200, "stdout_log_path": "x3", "stderr_log_path": "y3"},
        },
    }

    report = module.build_bundle_report(manifest=manifest)

    assert "- total_signal_count: 6" in report
    assert "- total_signal_elapsed_sec: 18.01" in report
    assert "- skipped_reason_counts: no_rows=2" in report


def test_shortline_review_bundle_report_shows_shortline_and_runs_compact_summaries() -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    manifest = {
        "trade_date": "2026-05-03",
        "run_id": "bundle_r2",
        "generated_at": "2026-05-04T08:10:00",
        "dry_run": False,
        "status": "success",
        "artifact_governance": {
            "output_class": "smoke",
            "pointer_json_path": "data/manual_runs/shortline_review_bundle_index/2026-05-03/bundle_r2/bundle_pointer.json",
            "latest_json_path": "data/manual_runs/shortline_review_bundle_index/latest.json",
        },
        "shortline_run": {
            "output_dir": "shortline_dir",
            "report_path": "shortline_report.md",
            "candidate_count": 3,
            "explanation_count": 3,
            "top_pick_count": 1,
            "watchlist_count": 1,
            "high_risk_mover_count": 1,
            "scan_source_counts": {"wondertrader_real_engine": 3},
            "review_tier_counts": {"top_pick": 1, "watchlist": 1, "high_risk_mover": 1},
            "upstream_tool_hit_counts": {"ChipAnalysisTool": 3, "HotMoneyTool": 3},
            "total_explain_elapsed_ms": 120000,
            "orchestrator_explain_elapsed_ms": 121500,
            "tracking_repeat_symbol_count": 2,
            "tracking_longest_streak_days": 4,
        },
        "runs_summary": {
            "output_dir": "runs_summary_dir",
            "summary_md_path": "shortline_runs_summary.md",
            "run_count": 12,
            "latest_run_id": "latest_r1",
            "trade_date_counts": {"2026-05-03": 12},
            "upstream_tool_counts": {"ChipAnalysisTool": 11, "HotMoneyTool": 11},
            "latest_run_candidate_count": 3,
            "latest_run_total_explain_elapsed_ms": 170737,
            "latest_run_top_symbols": ["300083", "688256", "688400"],
            "latest_run_used_upstream_tools": ["ChipAnalysisTool", "HotMoneyTool"],
        },
        "fast_review": {
            "output_dir": "fast_review_dir",
            "latest_md_path": "fast_review_latest.md",
            "summary_md_path": "fast_review_summary.md",
        },
        "steps": {
            "shortline": {"status": "success", "elapsed_ms": 100, "stdout_log_path": "x", "stderr_log_path": "y"},
            "runs_summary": {"status": "success", "elapsed_ms": 50, "stdout_log_path": "x2", "stderr_log_path": "y2"},
            "fast_review": {"status": "success", "elapsed_ms": 200, "stdout_log_path": "x3", "stderr_log_path": "y3"},
        },
    }

    report = module.build_bundle_report(manifest=manifest)

    assert "## Shortline Runtime" in report
    assert "- explanation_count: 3" in report
    assert "- tier_counts: top_pick=1, watchlist=1, high_risk_mover=1" in report
    assert "- upstream_tools: ChipAnalysisTool=3, HotMoneyTool=3" in report
    assert "- explain_elapsed_ms: total=120000, orchestrator=121500" in report
    assert "- tracking: repeat_symbol_count=2, longest_streak_days=4" in report
    assert "## Runs Summary Runtime" in report
    assert "- trade_date_counts: 2026-05-03=12" in report
    assert "- upstream_tool_counts: ChipAnalysisTool=11, HotMoneyTool=11" in report
    assert "- latest_run_snapshot: candidate_count=3, total_explain_elapsed_ms=170737" in report
    assert "- latest_run_top_symbols: 300083, 688256, 688400" in report


def test_shortline_review_bundle_report_orders_runtime_sections_before_detail_sections() -> None:
    module = importlib.import_module("scripts.run_shortline_review_bundle")
    manifest = {
        "trade_date": "2026-05-03",
        "run_id": "bundle_r3",
        "generated_at": "2026-05-04T08:30:00",
        "dry_run": False,
        "status": "failed",
        "artifact_governance": {
            "output_class": "smoke",
            "pointer_json_path": "pointer.json",
            "latest_json_path": "latest.json",
        },
        "shortline_run": {
            "output_dir": "shortline_dir",
            "report_path": "shortline_report.md",
            "candidate_count": 1,
            "explanation_count": 1,
            "review_tier_counts": {"high_risk_mover": 1},
            "scan_source_counts": {"wondertrader_real_engine": 1},
            "upstream_tool_hit_counts": {"ChipAnalysisTool": 1},
            "total_explain_elapsed_ms": 20000,
            "orchestrator_explain_elapsed_ms": 21000,
        },
        "runs_summary": {
            "output_dir": "runs_dir",
            "summary_md_path": "runs.md",
            "run_count": 9,
            "latest_run_id": "latest_r3",
            "trade_date_counts": {"2026-05-03": 9},
            "upstream_tool_counts": {"ChipAnalysisTool": 8},
            "latest_run_candidate_count": 3,
            "latest_run_total_explain_elapsed_ms": 123456,
            "latest_run_top_symbols": ["300083"],
            "latest_run_used_upstream_tools": ["ChipAnalysisTool"],
        },
        "fast_review": {
            "output_dir": "fast_dir",
            "latest_md_path": "fast_latest.md",
            "summary_md_path": "fast_summary.md",
            "runtime_summary": {
                "include_signals": "hundred_day_high",
                "persist_snapshots": False,
                "total_signal_count": 0,
                "total_signal_elapsed_sec": 88.8,
                "skipped_signals_count": 1,
            },
        },
        "steps": {
            "shortline": {"status": "success", "elapsed_ms": 100, "stdout_log_path": "s1.out", "stderr_log_path": "s1.err"},
            "runs_summary": {"status": "failed", "elapsed_ms": 50, "stdout_log_path": "s2.out", "stderr_log_path": "s2.err"},
            "fast_review": {"status": "pending", "elapsed_ms": 0, "stdout_log_path": "s3.out", "stderr_log_path": "s3.err"},
        },
        "failure": {
            "step": "runs_summary",
            "error_message": "boom",
            "stdout_excerpt": "stdout boom",
            "stderr_excerpt": "stderr boom",
        },
    }

    report = module.build_bundle_report(manifest=manifest)

    shortline_runtime_index = report.index("## Shortline Runtime")
    runs_runtime_index = report.index("## Runs Summary Runtime")
    fast_runtime_index = report.index("## Fast Review Runtime")
    failed_step_index = report.index("## Failed Step")
    shortline_detail_index = report.index("## Shortline\n")
    runs_detail_index = report.index("## Runs Summary\n")
    fast_detail_index = report.index("## Fast Review\n")
    artifact_index = report.index("## Artifact Governance")
    steps_index = report.index("## Steps")

    assert shortline_runtime_index < runs_runtime_index < fast_runtime_index < failed_step_index
    assert failed_step_index < shortline_detail_index < runs_detail_index < fast_detail_index
    assert fast_detail_index < artifact_index < steps_index
