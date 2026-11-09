#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 A1-a 只读总览（today_board.md）。

只读已有产物，不建表、不接 API、不调用模型、不启动任何生产者脚本。

用法：
    ./.venv-linux/bin/python scripts/build_today_board.py --date 2026-11-06
    ./.venv-linux/bin/python scripts/build_today_board.py            # 取库内最新信号日
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.daily_board_service import (  # noqa: E402
    DEFAULT_OUTPUT_PATH,
    build_board_for_date,
)


def _latest_signal_date() -> str:
    from src.storage import DatabaseManager, KlineSignalSnapshot

    db = DatabaseManager.get_instance()
    session = db.get_session()
    try:
        row = (
            session.query(KlineSignalSnapshot.signal_date)
            .order_by(KlineSignalSnapshot.signal_date.desc())
            .first()
        )
    finally:
        session.close()
    if not row or not row[0]:
        raise SystemExit("库内没有信号快照，请显式传 --date")
    value = row[0]
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 A1-a 只读总览 today_board.md")
    parser.add_argument("--date", help="信号日 YYYY-MM-DD；缺省取库内最新信号日")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_PATH), help="输出路径")
    parser.add_argument("--no-write", action="store_true", help="只渲染到标准输出，不落盘")
    parser.add_argument("--no-timestamp", action="store_true", help="不写生成时间（输出确定，便于比较）")
    args = parser.parse_args()

    board_date = args.date or _latest_signal_date()
    try:
        date.fromisoformat(board_date)
    except ValueError:
        raise SystemExit(f"日期格式错误：{board_date}")

    output_path = None if args.no_write else Path(args.out)
    generated_at = None if args.no_timestamp else datetime.now()

    board, markdown = build_board_for_date(
        board_date,
        output_path=output_path,
        generated_at=generated_at,
    )

    degraded = sum(1 for item in board.health if item.status != "ok")
    print(
        f"[today_board] date={board.board_date} 候选={len(board.candidates)} "
        f"来源={len({c.source for c in board.candidates})} "
        f"诊断={len(board.diagnostics)} 来源异常={degraded} "
        f"重建失败={board.rebuild_failure_count}"
    )
    if output_path is not None:
        print(f"[today_board] -> {output_path}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
