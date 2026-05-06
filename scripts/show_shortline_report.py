#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print a shortline report with stable UTF-8 stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview a shortline report with UTF-8 output.")
    parser.add_argument("report_path")
    parser.add_argument("--lines", type=int, default=40)
    return parser.parse_args()


def _configure_utf8_stdout() -> None:
    if not sys.stdout.isatty():
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def main() -> int:
    args = parse_args()
    _configure_utf8_stdout()
    report_path = Path(str(args.report_path))
    if not report_path.exists():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 1

    lines = report_path.read_text(encoding="utf-8").splitlines()
    limit = max(0, int(args.lines))
    for line in lines[:limit]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
