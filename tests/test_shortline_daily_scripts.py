# -*- coding: utf-8 -*-
"""Regression checks for shortline daily helper scripts."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _first_code_line(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return ""


def test_shortline_daily_script_uses_py311_fingenius_runtime() -> None:
    script_path = PROJECT_ROOT / "scripts" / "run-shortline-daily.ps1"
    helper_path = PROJECT_ROOT / "scripts" / "shortline-wrapper-common.ps1"

    assert script_path.exists()
    assert helper_path.exists()

    content = script_path.read_text(encoding="utf-8")
    helper_content = helper_path.read_text(encoding="utf-8")
    assert _first_code_line(content).startswith("param(")
    assert "run_shortline_hub.py" in content
    assert "D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe" in content
    assert ". (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')" in content
    assert "function Resolve-RepoPythonExecutable" in helper_content
    assert "py -3.10 -c" in helper_content
    assert "$resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable" in content
    assert "[switch]$DryRun" in content
    assert "if ($DryRun)" in content
    assert "--fg-enable-big-deal" not in content
    assert "--enable-explain-cache" in content
    assert "--explain-cache-path" in content
    assert "--wt-source-mode', 'prefer_real_engine'" in content
    assert "data\\runtime\\shortline_hub\\explain_cache" in content
    assert "select_trend_leader_candidates.py" in content
    assert "select_earnings_surprise_candidates.py" in content
    assert "collect_commodity_beneficiary_snapshots.py" in content
    assert content.index("& $resolvedRepoPythonExecutable $trendLeaderScript @trendArgs") < content.index("& $resolvedRepoPythonExecutable $hubScript @hubArgs")
    assert content.index("& $resolvedRepoPythonExecutable $earningsScript @earningsArgs") < content.index("& $resolvedRepoPythonExecutable $hubScript @hubArgs")
    assert content.index("& $resolvedRepoPythonExecutable $commodityScript @commodityArgs") < content.index("& $resolvedRepoPythonExecutable $hubScript @hubArgs")
    assert "--snapshot-date', $TradeDate" in content
    assert "--commodities', 'optical_fiber,memory,hard_disk'" in content


def test_shortline_fullcheck_script_enables_big_deal() -> None:
    script_path = PROJECT_ROOT / "scripts" / "run-shortline-fullcheck.ps1"
    helper_path = PROJECT_ROOT / "scripts" / "shortline-wrapper-common.ps1"

    assert script_path.exists()
    assert helper_path.exists()

    content = script_path.read_text(encoding="utf-8")
    helper_content = helper_path.read_text(encoding="utf-8")
    assert _first_code_line(content).startswith("param(")
    assert "run_shortline_hub.py" in content
    assert "D:\\bb\\FinGenius\\.venv311\\Scripts\\python.exe" in content
    assert ". (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')" in content
    assert "function Resolve-RepoPythonExecutable" in helper_content
    assert "py -3.10 -c" in helper_content
    assert "$resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable" in content
    assert "[switch]$DryRun" in content
    assert "if ($DryRun)" in content
    assert "--fg-enable-big-deal" in content
    assert "--enable-explain-cache" in content
    assert "--explain-cache-path" in content
    assert "--wt-source-mode', 'prefer_real_engine'" in content
    assert "--explain-cache-mode', 'full'" in content
    assert "data\\runtime\\shortline_hub\\explain_cache" in content
