#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成信号实测统计（前向 + 回放分开），输出 data/strategy_review/replay_stats.json。

用法：
    ./.venv-linux/bin/python scripts/build_replay_stats.py
    ./.venv-linux/bin/python scripts/build_replay_stats.py --output /tmp/stats.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.replay_stats_service import DEFAULT_DB, DEFAULT_OUTPUT, build_stats, verdict_for_signal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成信号实测统计")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--as-of", help="截止交易日 YYYY-MM-DD；按实际收录时点选择有效测量")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="本地研究数据库路径")
    args = parser.parse_args()

    stats = build_stats(output_path=Path(args.output), as_of=args.as_of, db_path=Path(args.db))
    print(f"[replay_stats] -> {args.output}")
    for source, item in stats["signals"].items():
        replay = item.get("replay") or {}
        forward = item.get("forward") or {}
        ret5 = (replay.get("ret5") or {}).get("mean")
        win5 = (replay.get("ret5") or {}).get("win_pct")
        verdict = verdict_for_signal(stats, source)
        print(
            f"  {item['label']:<10} 回放 n={replay.get('n', 0):>3} "
            f"T+5={ret5 if ret5 is not None else '--'}% 胜率={win5 if win5 is not None else '--'}% "
            f"| 前向 n={forward.get('n', 0):>2} | {verdict['verdict']}: {verdict['reason']}"
        )
    bootstrap = stats.get("overall_bootstrap") or {}
    print(f"[replay_stats] 事后导入/前向证据不足 n={bootstrap.get('n', 0)}（不计前向）")
    overall = stats.get("overall_replay") or {}
    print(
        f"[replay_stats] 回放合计 n={overall.get('n', 0)} "
        f"T+1={((overall.get('ret1') or {}).get('mean'))}% "
        f"T+3={((overall.get('ret3') or {}).get('mean'))}% "
        f"T+5={((overall.get('ret5') or {}).get('mean'))}% "
        f"MAE={((overall.get('mae_pct') or {}).get('mean'))}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
