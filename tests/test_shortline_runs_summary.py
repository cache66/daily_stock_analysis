# -*- coding: utf-8 -*-
"""Tests for shortline run summary aggregation CLI."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace


def _write_run_dir(
    root: Path,
    *,
    run_id: str,
    trade_date: str,
    candidate_count: int,
    total_explain_elapsed_ms: int,
    setup_tag: str,
    used_tools: list[str],
    explain_cache_enabled: bool = True,
    explain_cache_mode: str = "light",
    explain_cache_hit_count: int = 0,
    explain_cache_miss_count: int = 0,
    explain_parallel_workers: int = 0,
    tool_error_count: int = 0,
    tracking_repeat_symbol_count: int = 0,
    tracking_longest_streak_days: int = 0,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "trade_date": trade_date,
                "top_n": candidate_count,
                "candidate_count": candidate_count,
                "explanation_count": candidate_count,
                "combined_count": candidate_count,
                "scan_source_counts": {"wondertrader_real_engine": candidate_count},
                "setup_tag_counts": {setup_tag: candidate_count},
                "risk_flag_counts": {},
                "explanation_source_counts": {"upstream_tools": candidate_count},
                "upstream_tool_hit_counts": {tool: candidate_count for tool in used_tools},
                "tool_error_count": tool_error_count,
                "total_explain_elapsed_ms": total_explain_elapsed_ms,
                "avg_explain_elapsed_ms": int(total_explain_elapsed_ms / max(candidate_count, 1)),
                "orchestrator_explain_elapsed_ms": total_explain_elapsed_ms + 1000,
                "explain_cache_enabled": explain_cache_enabled,
                "explain_cache_mode": explain_cache_mode,
                "explain_cache_hit_count": explain_cache_hit_count,
                "explain_cache_miss_count": explain_cache_miss_count,
                "explain_parallel_workers": explain_parallel_workers,
                "tracking_repeat_symbol_count": tracking_repeat_symbol_count,
                "tracking_longest_streak_days": tracking_longest_streak_days,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "shortline_combined_results.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "300632",
                    "name": "光莆股份",
                    "board_name": "半导体",
                    "setup_tag": setup_tag,
                    "used_upstream_tools": used_tools,
                    "risk_flags": [],
                    "explanation_source": "upstream_tools",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def test_shortline_runs_summary_cli_writes_markdown_and_json(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("scripts.summarize_shortline_runs")
    runs_root = tmp_path / "manual_runs"
    output_dir = tmp_path / "summary_out"
    _write_run_dir(
        runs_root,
        run_id="shortline_daily_demo_20260503",
        trade_date="2026-05-03",
        candidate_count=5,
        total_explain_elapsed_ms=141016,
        setup_tag="涨停后强势延续",
        used_tools=["HotMoneyTool", "ChipAnalysisTool"],
        explain_cache_enabled=True,
        explain_cache_mode="light",
        explain_cache_hit_count=4,
        explain_cache_miss_count=1,
        explain_parallel_workers=1,
    )
    _write_run_dir(
        runs_root,
        run_id="shortline_fullcheck_demo_20260503",
        trade_date="2026-05-03",
        candidate_count=1,
        total_explain_elapsed_ms=80911,
        setup_tag="涨停后分歧承接",
        used_tools=["HotMoneyTool", "ChipAnalysisTool", "BigDealAnalysisTool"],
        explain_cache_enabled=True,
        explain_cache_mode="light",
        explain_cache_hit_count=0,
        explain_cache_miss_count=3,
        explain_parallel_workers=1,
        tool_error_count=2,
    )
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            runs_root=str(runs_root),
            output_dir=str(output_dir),
            limit=10,
            quality_profile="standard",
            log_level="INFO",
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary_json = json.loads((output_dir / "shortline_runs_summary.json").read_text(encoding="utf-8"))
    summary_md = (output_dir / "shortline_runs_summary.md").read_text(encoding="utf-8")
    assert summary_json["run_count"] == 2
    assert summary_json["trade_date_counts"] == {"2026-05-03": 2}
    assert summary_json["upstream_tool_counts"]["HotMoneyTool"] == 2
    assert summary_json["upstream_tool_counts"]["BigDealAnalysisTool"] == 1
    assert summary_json["latest_run_id"] == "shortline_fullcheck_demo_20260503"
    assert summary_json["cache_enabled_run_count"] == 2
    assert summary_json["total_explain_cache_hits"] == 4
    assert summary_json["total_explain_cache_misses"] == 4
    assert summary_json["max_explain_parallel_workers"] == 1
    assert summary_json["anomaly_counts"] == {
        "cache_hit_ratio_low": 1,
        "parallel_workers_single": 1,
        "tool_errors_present": 1,
    }
    assert summary_json["latest_run_anomalies"] == [
        "cache_hit_ratio_low",
        "parallel_workers_single",
        "tool_errors_present",
    ]
    assert summary_json["quality_verdict_counts"] == {"fail": 1, "pass": 1}
    assert summary_json["latest_run_quality"]["verdict"] == "fail"
    assert summary_json["latest_run_quality"]["failed_checks"] == ["tool_errors_absent"]
    assert summary_json["latest_run_quality"]["signals"]["tracking_history_ready"] is False
    assert summary_json["runs"][0]["explain_cache_hit_count"] == 0
    assert summary_json["runs"][0]["anomalies"] == [
        "cache_hit_ratio_low",
        "parallel_workers_single",
        "tool_errors_present",
    ]
    assert summary_json["runs"][0]["quality_summary"]["verdict"] == "fail"
    assert summary_json["runs"][0]["quality_summary"]["signals"]["tracking_history_ready"] is False
    assert summary_json["runs"][1]["quality_summary"]["verdict"] == "pass"
    assert "tracking_continuity_weak" not in summary_json["runs"][1]["quality_summary"]["warning_checks"]
    assert summary_json["runs"][1]["quality_summary"]["signals"]["tracking_history_ready"] is False
    assert summary_json["runs"][1]["explain_cache_hit_count"] == 4
    assert "shortline_daily_demo_20260503" in summary_md
    assert "shortline_fullcheck_demo_20260503" in summary_md
    assert "BigDealAnalysisTool" in summary_md
    assert "total_explain_cache_hits: 4" in summary_md
    assert "total_explain_cache_misses: 4" in summary_md
    assert "cache_hits/cache_misses" in summary_md
    assert "anomaly_counts: {'cache_hit_ratio_low': 1, 'parallel_workers_single': 1, 'tool_errors_present': 1}" in summary_md
    assert "cache_hit_ratio_low, parallel_workers_single, tool_errors_present" in summary_md
    assert "quality_verdict_counts: {'fail': 1, 'pass': 1}" in summary_md
    assert "latest_run_quality_verdict: fail" in summary_md


def test_evaluate_run_quality_supports_off_and_strict_profiles() -> None:
    module = importlib.import_module("scripts.summarize_shortline_runs")
    entry = {
        "candidate_count": 3,
        "explanation_count": 3,
        "tool_error_count": 0,
        "scan_source_counts": {"wondertrader_real_engine": 3},
        "explain_cache_enabled": True,
        "explain_cache_hit_count": 0,
        "explain_cache_miss_count": 3,
        "explain_parallel_workers": 1,
        "tracking_repeat_symbol_count": 0,
    }

    off_quality = module.evaluate_run_quality(entry, profile="off")
    relaxed_strict_quality = module.evaluate_run_quality(
        entry,
        profile="strict",
        tracking_history_ready=False,
    )
    strict_quality = module.evaluate_run_quality(
        entry,
        profile="strict",
        tracking_history_ready=True,
    )

    assert off_quality["verdict"] == "off"
    assert off_quality["failed_checks"] == []
    assert relaxed_strict_quality["verdict"] == "fail"
    assert relaxed_strict_quality["warning_checks"] == [
        "cache_health_low",
        "parallelism_underused",
    ]
    assert strict_quality["verdict"] == "fail"
    assert strict_quality["failed_checks"] == []
    assert strict_quality["warning_checks"] == [
        "tracking_continuity_weak",
        "cache_health_low",
        "parallelism_underused",
    ]


def test_shortline_runs_summary_only_warns_tracking_when_prior_trade_date_exists(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.summarize_shortline_runs")
    runs_root = tmp_path / "manual_runs"
    output_dir = tmp_path / "summary_out"
    _write_run_dir(
        runs_root,
        run_id="shortline_day1",
        trade_date="2026-05-02",
        candidate_count=3,
        total_explain_elapsed_ms=100,
        setup_tag="tag_a",
        used_tools=["HotMoneyTool"],
        tracking_repeat_symbol_count=0,
    )
    _write_run_dir(
        runs_root,
        run_id="shortline_day2",
        trade_date="2026-05-03",
        candidate_count=3,
        total_explain_elapsed_ms=110,
        setup_tag="tag_b",
        used_tools=["HotMoneyTool"],
        tracking_repeat_symbol_count=0,
    )
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            runs_root=str(runs_root),
            output_dir=str(output_dir),
            limit=10,
            quality_profile="standard",
            log_level="INFO",
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary_json = json.loads((output_dir / "shortline_runs_summary.json").read_text(encoding="utf-8"))
    assert summary_json["latest_run_id"] == "shortline_day2"
    assert summary_json["latest_run_quality"]["verdict"] == "degraded"
    assert summary_json["latest_run_quality"]["warning_checks"] == ["tracking_continuity_weak"]
    assert summary_json["latest_run_quality"]["signals"]["tracking_history_ready"] is True
    assert summary_json["runs"][0]["quality_summary"]["warning_checks"] == ["tracking_continuity_weak"]
    assert summary_json["runs"][0]["quality_summary"]["signals"]["tracking_history_ready"] is True
    assert summary_json["runs"][1]["quality_summary"]["warning_checks"] == []
    assert summary_json["runs"][1]["quality_summary"]["signals"]["tracking_history_ready"] is False


def test_shortline_runs_summary_includes_bundle_shortline_child_run(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.summarize_shortline_runs")
    runs_root = tmp_path / "manual_runs"
    output_dir = tmp_path / "summary_out"

    _write_run_dir(
        runs_root,
        run_id="shortline_daily_demo_20260503",
        trade_date="2026-05-03",
        candidate_count=5,
        total_explain_elapsed_ms=141016,
        setup_tag="tag_a",
        used_tools=["HotMoneyTool", "ChipAnalysisTool"],
        explain_cache_enabled=True,
        explain_cache_mode="light",
        explain_cache_hit_count=0,
        explain_cache_miss_count=5,
        explain_parallel_workers=4,
    )

    bundle_shortline_dir = runs_root / "shortline_review_bundle_real_verify_20260504" / "shortline_run"
    bundle_shortline_dir.mkdir(parents=True)
    (bundle_shortline_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "run_id": "shortline_bundle_20260504",
                "trade_date": "2026-05-04",
                "top_n": 5,
                "candidate_count": 5,
                "explanation_count": 5,
                "combined_count": 5,
                "scan_source_counts": {"wondertrader_real_engine": 5},
                "setup_tag_counts": {"tag_b": 5},
                "risk_flag_counts": {},
                "explanation_source_counts": {"upstream_tools": 5},
                "upstream_tool_hit_counts": {
                    "HotMoneyTool": 5,
                    "ChipAnalysisTool": 5,
                },
                "tool_error_count": 0,
                "total_explain_elapsed_ms": 230645,
                "avg_explain_elapsed_ms": 46129,
                "orchestrator_explain_elapsed_ms": 2,
                "explain_cache_enabled": True,
                "explain_cache_mode": "light",
                "explain_cache_hit_count": 5,
                "explain_cache_miss_count": 0,
                "explain_parallel_workers": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_shortline_dir / "shortline_combined_results.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "300083",
                    "name": "demo_symbol",
                    "board_name": "demo_board",
                    "setup_tag": "tag_b",
                    "used_upstream_tools": ["HotMoneyTool", "ChipAnalysisTool"],
                    "risk_flags": [],
                    "explanation_source": "upstream_tools",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            runs_root=str(runs_root),
            output_dir=str(output_dir),
            limit=10,
            log_level="INFO",
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary_json = json.loads((output_dir / "shortline_runs_summary.json").read_text(encoding="utf-8"))
    assert summary_json["run_count"] == 2
    assert summary_json["latest_run_id"] == "shortline_bundle_20260504"
    assert summary_json["runs"][0]["run_id"] == "shortline_bundle_20260504"
    assert summary_json["runs"][0]["explain_cache_hit_count"] == 5
    assert summary_json["runs"][0]["explain_cache_miss_count"] == 0


def test_shortline_runs_summary_deduplicates_same_run_id_by_latest_mtime(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.summarize_shortline_runs")
    runs_root = tmp_path / "manual_runs"
    output_dir = tmp_path / "summary_out"

    first_dir = runs_root / "shortline_review_bundle_a" / "shortline_run"
    second_dir = runs_root / "shortline_review_bundle_b" / "shortline_run"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)

    first_payload = {
        "run_id": "shortline_bundle_20260504",
        "trade_date": "2026-05-04",
        "top_n": 5,
        "candidate_count": 5,
        "explanation_count": 5,
        "combined_count": 5,
        "scan_source_counts": {"wondertrader_real_engine": 5},
        "setup_tag_counts": {"tag_old": 5},
        "risk_flag_counts": {},
        "explanation_source_counts": {"upstream_tools": 5},
        "upstream_tool_hit_counts": {"HotMoneyTool": 5},
        "tool_error_count": 0,
        "total_explain_elapsed_ms": 250000,
        "avg_explain_elapsed_ms": 50000,
        "orchestrator_explain_elapsed_ms": 3,
        "explain_cache_enabled": True,
        "explain_cache_mode": "light",
        "explain_cache_hit_count": 1,
        "explain_cache_miss_count": 4,
        "explain_parallel_workers": 4,
    }
    second_payload = dict(first_payload)
    second_payload["setup_tag_counts"] = {"tag_new": 5}
    second_payload["explain_cache_hit_count"] = 5
    second_payload["explain_cache_miss_count"] = 0

    for target_dir, payload, symbol in (
        (first_dir, first_payload, "300001"),
        (second_dir, second_payload, "300002"),
    ):
        (target_dir / "run_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "shortline_combined_results.json").write_text(
            json.dumps(
                [
                    {
                        "symbol": symbol,
                        "name": "demo_symbol",
                        "board_name": "demo_board",
                        "setup_tag": list(payload["setup_tag_counts"].keys())[0],
                        "used_upstream_tools": ["HotMoneyTool"],
                        "risk_flags": [],
                        "explanation_source": "upstream_tools",
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    newer_summary_path = second_dir / "run_summary.json"
    older_summary_path = first_dir / "run_summary.json"
    newer_mtime = newer_summary_path.stat().st_mtime
    older_mtime = older_summary_path.stat().st_mtime
    if newer_mtime <= older_mtime:
        import os
        os.utime(newer_summary_path, (older_mtime + 5, older_mtime + 5))

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            runs_root=str(runs_root),
            output_dir=str(output_dir),
            limit=10,
            log_level="INFO",
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    summary_json = json.loads((output_dir / "shortline_runs_summary.json").read_text(encoding="utf-8"))
    assert summary_json["run_count"] == 1
    assert summary_json["latest_run_id"] == "shortline_bundle_20260504"
    assert summary_json["runs"][0]["top_symbols"] == ["300002"]
    assert summary_json["runs"][0]["explain_cache_hit_count"] == 5
