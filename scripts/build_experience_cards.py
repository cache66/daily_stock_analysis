#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成经验卡 Markdown（A1-b）。

用法：
    ./.venv-linux/bin/python scripts/build_experience_cards.py
    ./.venv-linux/bin/python scripts/build_experience_cards.py --output /tmp/cards.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.experience_cards_service import (  # noqa: E402
    DEFAULT_OUTPUT,
    DEFAULT_REGISTRY,
    build_cards_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成经验卡 Markdown")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    result = build_cards_file(Path(args.registry), Path(args.output))
    print(f"[experience_cards] cards={result['count']} -> {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
