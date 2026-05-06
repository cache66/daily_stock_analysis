# -*- coding: utf-8 -*-
"""Tests for UTF-8 shortline report preview helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_show_shortline_report_prints_readable_utf8_lines(tmp_path: Path) -> None:
    report_path = tmp_path / "shortline_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Shortline Hub Report",
                "",
                "## 结果概览",
                "",
                "- 候选数量: 3",
                "- 板块分布: 半导体=1, 机床制造=1, 专用机械=1",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "show_shortline_report.py"),
            str(report_path),
            "--lines",
            "6",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "## 结果概览" in completed.stdout
    assert "板块分布" in completed.stdout
    assert "缁" not in completed.stdout
