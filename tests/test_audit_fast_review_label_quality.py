# -*- coding: utf-8 -*-
"""Tests for the fast-review label-quality audit helper."""

from __future__ import annotations

import importlib
from pathlib import Path


def _load_script_module():
    return importlib.import_module("scripts.audit_fast_review_label_quality")


def _write_focus_csv(path: Path, rows: list[dict[str, str]]) -> None:
    header = [
        "code",
        "name",
        "preferred_industry_label",
        "business_summary",
        "peer_group_label",
        "display_reason_summary",
        "reason_summary",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    for row in rows:
        values = []
        for column in header:
            value = str(row.get(column, "") or "")
            if "," in value:
                value = f"\"{value}\""
            values.append(value)
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_discover_focus_csvs_returns_sorted_matches(tmp_path: Path) -> None:
    module = _load_script_module()
    a = tmp_path / "a" / "review" / "fast_review_strategy_focus.csv"
    b = tmp_path / "b" / "review" / "fast_review_strategy_focus.csv"
    _write_focus_csv(a, [])
    _write_focus_csv(b, [])

    paths = module.discover_focus_csvs(tmp_path)

    assert [path.as_posix() for path in paths] == [a.as_posix(), b.as_posix()]


def test_audit_csv_reports_long_business_sentence_fields(tmp_path: Path) -> None:
    module = _load_script_module()
    csv_path = tmp_path / "run" / "review" / "fast_review_strategy_focus.csv"
    _write_focus_csv(
        csv_path,
        [
            {
                "code": "002565",
                "name": "顺灏股份",
                "preferred_industry_label": "特种环保纸的研发、生产及销售",
                "business_summary": "",
                "peer_group_label": "特种环保纸",
                "display_reason_summary": "",
                "reason_summary": "",
            },
            {
                "code": "002384",
                "name": "东山精密",
                "preferred_industry_label": "PCB/光模块/电子材料",
                "business_summary": "PCB/光模块/电子材料，偏AI上游材料链",
                "peer_group_label": "PCB",
                "display_reason_summary": "",
                "reason_summary": "",
            },
        ],
    )

    findings = module.audit_csv(csv_path)

    assert len(findings) == 1
    assert findings[0].code == "002565"
    assert findings[0].field_name == "preferred_industry_label"
    assert findings[0].value == "特种环保纸的研发、生产及销售"


def test_main_returns_nonzero_when_findings_exist(tmp_path: Path, capsys) -> None:
    module = _load_script_module()
    csv_path = tmp_path / "run" / "review" / "fast_review_strategy_focus.csv"
    _write_focus_csv(
        csv_path,
        [
            {
                "code": "600330",
                "name": "天通股份",
                "preferred_industry_label": "软磁材料及磁心的研发、生产与销售",
            }
        ],
    )

    rc = module.main(["--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "findings=1" in captured.out
    assert "天通股份" in captured.out


def test_main_returns_zero_when_no_findings_exist(tmp_path: Path, capsys) -> None:
    module = _load_script_module()
    csv_path = tmp_path / "run" / "review" / "fast_review_strategy_focus.csv"
    _write_focus_csv(
        csv_path,
        [
            {
                "code": "300476",
                "name": "胜宏科技",
                "preferred_industry_label": "PCB",
                "business_summary": "PCB，偏AI算力供应链",
                "peer_group_label": "PCB",
                "display_reason_summary": "当前更像是 PCB 方向走强；业务更偏 PCB。",
                "reason_summary": "当前更像是 PCB 方向走强；业务更偏 PCB。",
            }
        ],
    )

    rc = module.main(["--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "findings=0" in captured.out
