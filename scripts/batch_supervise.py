#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批处理守护（C1 心跳 + C3 收尾）：对长批次日志做周期心跳，完成后可自动跑验收步骤。

定位（批处理基建，2026-09-27）：
- 心跳（C1）：按固定间隔对一组日志输出/落盘状态行（进度计数、完成/失败标记、文件年龄），
  长批次不必“守着终端”或反复手敲 grep；
- 收尾（C3）：当全部日志出现完成标记后，按序执行 `--on-done "名称::命令"` 步骤
  （如异常扫描、污染量化、leaderboard 刷新），步骤输出与退出码汇总进同一状态文件。

口径与约定：
- `--log` 支持字面路径与 glob（含 `*?[` 时由脚本展开，只取文件）；
- 进度计数默认统计 `##########`（PIT 分批的“每天”标记）；完成/失败标记为子串匹配；
- `--once` 只输出一次快照立即退出（随时“看一眼”）；watch 模式默认最长等待 12h（退出码 2 超时）；
- on-done 步骤用 `bash -lc` 在仓库根目录执行，任一步骤失败则最终退出码 3；无步骤时完成即 0；
- 状态行同时落 `--status-file`（追加）或 stdout（缺省）。

用法：
    # C1：单次快照
    ./.venv-linux/bin/python scripts/batch_supervise.py --once \
        --log "data/run_logs/sens_*.log" --done-marker "PIT SENSITIVITY DONE"

    # C1：持续心跳（每 5 分钟一行；全部日志命中完成标记后退出）
    nohup ./.venv-linux/bin/python scripts/batch_supervise.py \
        --log "data/run_logs/sens_*.log" --done-marker "PIT SENSITIVITY DONE" \
        --interval 300 --status-file data/run_logs/sensitivity_heartbeat.log &

    # C3：qfq 重建完成后的自动收尾（示例）
    nohup ./.venv-linux/bin/python scripts/batch_supervise.py \
        --log data/run_logs/qfq_rebuild.log --done-marker "[done]" \
        --on-done "异常扫描::./.venv-linux/bin/python scripts/check_price_anomalies.py --start-date 2026-01-01 --compare-baseline data/verification/qfq_fingerprint_2026_before_rebuild_20260926.csv" \
        --on-done "污染量化::./.venv-linux/bin/python scripts/quantify_pit_contamination_impact.py --fingerprint-csv data/verification/qfq_fingerprint_2026_before_rebuild_20260926.csv" \
        --status-file data/run_logs/qfq_rebuild_wrapup.log &
"""
from __future__ import annotations

import argparse
import glob
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("batch_supervise")

DEFAULT_COUNT_PATTERN = "##########"
DEFAULT_FAIL_MARKER = "FAILED"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_MAX_MINUTES = 720


def expand_log_patterns(patterns: Sequence[str]) -> List[Path]:
    """展开 glob/字面路径（去重、排序）；glob 只取文件，字面路径保留以便状态行标记 missing。"""
    paths: List[Path] = []
    seen = set()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            candidates = [match for match in sorted(glob.glob(pattern)) if Path(match).is_file()]
        else:
            candidates = [pattern]
        for candidate in candidates:
            path = Path(candidate)
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def read_log_stats(
    path: Path,
    *,
    count_pattern: str = DEFAULT_COUNT_PATTERN,
    done_marker: str = "",
    fail_marker: str = DEFAULT_FAIL_MARKER,
) -> Dict[str, object]:
    """统计单个日志：进度计数 / 完成标记 / 失败计数 / 文件年龄。"""
    stat: Dict[str, object] = {
        "name": Path(path).name,
        "path": str(path),
        "exists": False,
        "count": 0,
        "failed": 0,
        "done": False,
        "age_seconds": None,
        "size_bytes": 0,
    }
    try:
        file_stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return stat
    stat["exists"] = True
    stat["size_bytes"] = int(file_stat.st_size)
    stat["age_seconds"] = max(0, int(time.time() - file_stat.st_mtime))
    stat["count"] = int(text.count(count_pattern)) if count_pattern else 0
    stat["failed"] = int(text.count(fail_marker)) if fail_marker else 0
    stat["done"] = bool(done_marker) and (done_marker in text)
    return stat


def _age_text(seconds: Optional[object]) -> str:
    if seconds is None:
        return "?"
    value = int(seconds)
    if value < 600:
        return f"{value}s"
    return f"{round(value / 60)}m"


def format_snapshot(stats: Sequence[Dict[str, object]], *, now: Optional[datetime] = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    total = len(stats)
    done_count = sum(1 for item in stats if item.get("done"))
    parts = []
    for item in stats:
        if not item.get("exists"):
            parts.append(f"{item['name']}: missing")
            continue
        parts.append(
            "{name}: n={count} done={done} fail={failed} age={age}".format(
                name=item["name"],
                count=item["count"],
                done=int(bool(item.get("done"))),
                failed=item["failed"],
                age=_age_text(item.get("age_seconds")),
            )
        )
    status = "DONE" if total and done_count == total else "RUNNING"
    return f"[{timestamp}] status={status} done={done_count}/{total} | " + " | ".join(parts)


def all_done(stats: Sequence[Dict[str, object]]) -> bool:
    return bool(stats) and all(bool(item.get("done")) for item in stats)


def parse_steps(specs: Sequence[str]) -> List[Tuple[str, str]]:
    steps: List[Tuple[str, str]] = []
    for spec in specs:
        if "::" not in spec:
            raise SystemExit(f"--on-done 格式应为 名称::命令，收到: {spec}")
        name, command = spec.split("::", 1)
        if not name.strip() or not command.strip():
            raise SystemExit(f"--on-done 无效: {spec}")
        steps.append((name.strip(), command.strip()))
    return steps


def _default_executor(command: str) -> Tuple[int, str]:
    process = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    output = (process.stdout or "") + (process.stderr or "")
    return int(process.returncode), output


def run_steps(
    steps: Sequence[Tuple[str, str]],
    *,
    status_handle=None,
    executor: Optional[Callable[[str], Tuple[int, str]]] = None,
) -> List[Dict[str, object]]:
    execute = executor or _default_executor
    results: List[Dict[str, object]] = []

    def emit(line: str) -> None:
        if status_handle is not None:
            status_handle.write(line + "\n")
            status_handle.flush()
        else:
            print(line)

    for name, command in steps:
        started = time.time()
        emit(f"[step:{name}] start: {command}")
        exit_code, output = execute(command)
        elapsed = round(time.time() - started, 1)
        if output:
            emit(output.rstrip())
        emit(f"[step:{name}] exit={int(exit_code)} elapsed={elapsed}s")
        results.append({"name": name, "exit_code": int(exit_code), "elapsed_seconds": elapsed})
    return results


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批处理守护：心跳（C1）+ 完成后自动收尾（C3）")
    parser.add_argument("--log", action="append", required=True, help="日志路径或 glob（可重复）")
    parser.add_argument("--count-pattern", default=DEFAULT_COUNT_PATTERN, help=f"进度计数子串，默认 {DEFAULT_COUNT_PATTERN!r}")
    parser.add_argument("--done-marker", default="", help="完成标记子串（全部日志命中即完成；watch 模式建议提供）")
    parser.add_argument("--fail-marker", default=DEFAULT_FAIL_MARKER, help=f"失败标记子串，默认 {DEFAULT_FAIL_MARKER!r}")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help=f"心跳间隔秒数，默认 {DEFAULT_INTERVAL_SECONDS}")
    parser.add_argument("--max-minutes", type=int, default=DEFAULT_MAX_MINUTES, help=f"watch 最长等待分钟数（0=不限），默认 {DEFAULT_MAX_MINUTES}")
    parser.add_argument("--status-file", default="", help="状态/心跳落盘文件（追加）；缺省只打印 stdout")
    parser.add_argument("--once", action="store_true", help="只输出一次快照后退出")
    parser.add_argument("--on-done", action="append", default=[], help="完成后执行的步骤，格式 名称::命令；可重复")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    paths = expand_log_patterns(args.log)
    if not paths:
        print("[batch-supervise] 未匹配到任何日志", file=sys.stderr)
        return 1
    steps = parse_steps(args.on_done)

    handle = None
    if args.status_file:
        status_path = Path(args.status_file)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(status_path, "a", encoding="utf-8")

    def emit(line: str) -> None:
        if handle is not None:
            handle.write(line + "\n")
            handle.flush()
        else:
            print(line)

    def snapshot() -> List[Dict[str, object]]:
        stats = [
            read_log_stats(
                path,
                count_pattern=str(args.count_pattern),
                done_marker=str(args.done_marker),
                fail_marker=str(args.fail_marker),
            )
            for path in paths
        ]
        emit(format_snapshot(stats))
        return stats

    try:
        if args.once:
            snapshot()
            return 0

        started = time.time()
        max_seconds = max(0, int(args.max_minutes)) * 60
        while True:
            stats = snapshot()
            if all_done(stats):
                emit(f"[batch-supervise] 全部完成（{len(stats)} 个日志）")
                break
            if max_seconds and time.time() - started > max_seconds:
                emit(f"[batch-supervise] 等待超时（max-minutes={args.max_minutes}）")
                return 2
            time.sleep(max(1, int(args.interval)))

        if steps:
            results = run_steps(steps, status_handle=handle)
            failed = [item for item in results if item["exit_code"] != 0]
            if failed:
                emit("[batch-supervise] 收尾步骤失败: " + ", ".join(str(item["name"]) for item in failed))
                return 3
            emit("[batch-supervise] 收尾步骤全部成功")
        return 0
    finally:
        if handle is not None:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
