# -*- coding: utf-8 -*-
"""Regression checks for shortline review bundle helper script."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _first_code_line(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return ""


def test_shortline_review_bundle_script_uses_fixed_single_machine_defaults() -> None:
    script_path = PROJECT_ROOT / "scripts" / "run-shortline-review-bundle.ps1"
    helper_path = PROJECT_ROOT / "scripts" / "shortline-wrapper-common.ps1"

    assert script_path.exists()
    assert helper_path.exists()

    content = script_path.read_text(encoding="utf-8")
    helper_content = helper_path.read_text(encoding="utf-8")
    assert _first_code_line(content).startswith("param(")
    assert "run_shortline_review_bundle.py" in content
    assert "D:\\bb\\WonderTrader\\bridge\\wt_export_candidates.py" in content
    assert "D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe" in content
    assert ". (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')" in content
    assert "function Resolve-RepoPythonExecutable" in helper_content
    assert "py -3.10 -c" in helper_content
    assert "$resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable" in content
    assert "if (![string]::IsNullOrWhiteSpace($RunId))" in content
    assert "--enable-explain-cache" in content
    assert "--explain-cache-path" in content
    assert '[string]$TrackingHistoryPath = \'\'' in content
    assert 'data\\runtime\\shortline_hub\\tracking\\shortline_tracking_history.json' in content
    assert "--tracking-history-path" in content
    assert "--wt-source-mode', 'prefer_real_engine'" in content
    assert "data\\runtime\\shortline_hub\\explain_cache" in content


def test_shortline_replay_validation_script_reuses_shared_cache_and_tracking() -> None:
    script_path = PROJECT_ROOT / "scripts" / "run-shortline-replay-validation.ps1"
    helper_path = PROJECT_ROOT / "scripts" / "shortline-wrapper-common.ps1"

    assert script_path.exists()
    assert helper_path.exists()

    content = script_path.read_text(encoding="utf-8")
    helper_content = helper_path.read_text(encoding="utf-8")
    assert _first_code_line(content).startswith("param(")
    assert ". (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')" in content
    assert "function Resolve-RepoPythonExecutable" in helper_content
    assert "run-shortline-review-bundle.ps1" in content
    assert "[switch]$DryRun" in content
    assert "if ($DryRun)" in content
    assert "BaselineTradeDate" in content
    assert "--ExplainCachePath" not in content
    assert "shortline_replay_validation_" in content
    assert "validation_replay_" in content
    assert "_warm" in content
    assert "TrackingHistoryPath" in content
    assert "ExplainCachePath" in content
    assert "SkipShortlinePersistSnapshot" in content
    assert "SkipFastReviewPersistSnapshots" in content
