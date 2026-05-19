# -*- coding: utf-8 -*-
"""Audit fast-review CSV artifacts for long business-sentence label regressions."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


DEFAULT_FIELDS = (
    "preferred_industry_label",
    "business_summary",
    "peer_group_label",
)
DEFAULT_PATTERN = "fast_review_strategy_focus.csv"
SUSPICIOUS_TOKENS = ("研发、生产", "研发、生产与销售", "研发、生产及销售", "生产销售", "业务")


@dataclass(frozen=True)
class LabelFinding:
    csv_path: Path
    field_name: str
    code: str
    name: str
    value: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit fast-review CSV artifacts for long-text label regressions.",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent / "data" / "manual_runs"),
        help="Root directory scanned recursively for fast-review focus CSV files.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help="Filename pattern matched recursively under the root directory.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=12,
        help="Minimum suspicious text length before a match is reported.",
    )
    parser.add_argument(
        "--field",
        action="append",
        dest="fields",
        default=[],
        help="Optional field name to audit. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of findings printed to stdout.",
    )
    return parser.parse_args(argv)


def discover_focus_csvs(root: Path, pattern: str = DEFAULT_PATTERN) -> List[Path]:
    return sorted(path for path in root.rglob(pattern) if path.is_file())


def is_suspicious_value(value: str, *, min_length: int = 16) -> bool:
    text = str(value or "").strip()
    if not text or len(text) < min_length:
        return False
    return any(token in text for token in SUSPICIOUS_TOKENS)


def audit_csv(
    csv_path: Path,
    *,
    fields: Sequence[str] = DEFAULT_FIELDS,
    min_length: int = 12,
) -> List[LabelFinding]:
    findings: List[LabelFinding] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("code", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            for field_name in fields:
                value = str(row.get(field_name, "") or "").strip()
                if not is_suspicious_value(value, min_length=min_length):
                    continue
                findings.append(
                    LabelFinding(
                        csv_path=csv_path,
                        field_name=field_name,
                        code=code,
                        name=name,
                        value=value,
                    )
                )
    return findings


def audit_paths(
    paths: Iterable[Path],
    *,
    fields: Sequence[str] = DEFAULT_FIELDS,
    min_length: int = 12,
) -> List[LabelFinding]:
    findings: List[LabelFinding] = []
    for path in paths:
        findings.extend(audit_csv(path, fields=fields, min_length=min_length))
    return findings


def _normalize_fields(fields: Sequence[str]) -> List[str]:
    normalized = [str(field or "").strip() for field in fields if str(field or "").strip()]
    return normalized or list(DEFAULT_FIELDS)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    fields = _normalize_fields(args.fields)
    csv_paths = discover_focus_csvs(root, args.pattern)
    findings = audit_paths(csv_paths, fields=fields, min_length=args.min_length)

    print(
        f"Scanned fast-review focus CSVs: files={len(csv_paths)} findings={len(findings)} root={root}"
    )
    for finding in findings[: max(0, args.limit)]:
        print(
            f"[{finding.field_name}] {finding.code} {finding.name} | "
            f"{finding.csv_path.as_posix()} | {finding.value}"
        )
    if len(findings) > args.limit:
        print(f"... truncated {len(findings) - args.limit} additional finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
