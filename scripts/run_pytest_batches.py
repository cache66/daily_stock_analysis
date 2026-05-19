# -*- coding: utf-8 -*-
"""Run pytest in smaller batches for local non-network regression."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


DEFAULT_PYTEST_ARGS = ["-m", "not network", "-q"]


@dataclass(frozen=True)
class TestBatch:
    start_index: int
    end_index: int
    files: List[Path]


@dataclass(frozen=True)
class BatchRunResult:
    batch: TestBatch
    batch_number: int
    total_batches: int
    display_batch_number: int
    display_total_batches: int
    returncode: int
    elapsed_seconds: float
    log_path: Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pytest test files in smaller local batches.",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Project root containing the tests/ directory.",
    )
    parser.add_argument(
        "--tests-dir",
        default="tests",
        help="Tests directory relative to project root, or an absolute path.",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="Glob pattern used to discover test files.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of test files to run per batch.",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python executable used to invoke pytest.",
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help="Optional log directory. Defaults to data/runtime/pytest_batch_runs/<timestamp>.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=20,
        help="Heartbeat interval while a batch is still running.",
    )
    parser.add_argument(
        "--start-batch",
        type=int,
        default=1,
        help="1-based batch number to start from.",
    )
    parser.add_argument(
        "--end-batch",
        type=int,
        default=0,
        help="1-based batch number to stop at. 0 means run through the final batch.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Optional pytest args appended after '--'. Defaults to '-m \"not network\" -q'.",
    )
    return parser.parse_args(argv)


def discover_test_files(tests_dir: Path, pattern: str = "test_*.py") -> List[Path]:
    return sorted(path for path in tests_dir.glob(pattern) if path.is_file())


def build_batches(files: Sequence[Path], batch_size: int) -> List[TestBatch]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batches: List[TestBatch] = []
    for start_index in range(0, len(files), batch_size):
        batch_files = list(files[start_index : start_index + batch_size])
        batches.append(
            TestBatch(
                start_index=start_index,
                end_index=start_index + len(batch_files) - 1,
                files=batch_files,
            )
        )
    return batches


def run_batch(
    *,
    batch: TestBatch,
    display_batch_number: int,
    display_total_batches: int,
    batch_number: int,
    total_batches: int,
    python_executable: str,
    pytest_args: Sequence[str],
    project_root: Path,
    log_dir: Path,
    heartbeat_seconds: int,
) -> BatchRunResult:
    command = [
        python_executable,
        "-m",
        "pytest",
        *pytest_args,
        *[str(path) for path in batch.files],
    ]
    print(
        f"[batch {display_batch_number}/{display_total_batches} | source {batch_number}/{total_batches}] "
        f"files={batch.start_index}-{batch.end_index} "
        f"count={len(batch.files)}"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"batch_{batch_number:02d}.log"
    print("  log:", log_path)
    started_at = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            elapsed = time.perf_counter() - started_at
            print(
                f"  heartbeat: batch={display_batch_number}/{display_total_batches} "
                f"source={batch_number}/{total_batches} "
                f"elapsed={_format_seconds(elapsed)}"
            )
            time.sleep(max(1, heartbeat_seconds))
        returncode = process.wait()
    return BatchRunResult(
        batch=batch,
        batch_number=batch_number,
        total_batches=total_batches,
        display_batch_number=display_batch_number,
        display_total_batches=display_total_batches,
        returncode=returncode,
        elapsed_seconds=time.perf_counter() - started_at,
        log_path=log_path,
    )


def _resolve_tests_dir(project_root: Path, tests_dir_arg: str) -> Path:
    tests_dir = Path(tests_dir_arg)
    if not tests_dir.is_absolute():
        tests_dir = project_root / tests_dir
    return tests_dir


def _normalize_pytest_args(pytest_args: Sequence[str]) -> List[str]:
    normalized = list(pytest_args)
    if normalized and normalized[0] == "--":
        normalized = normalized[1:]
    return normalized or list(DEFAULT_PYTEST_ARGS)


def _format_seconds(seconds: float) -> str:
    return f"{seconds:.2f}s"


def _resolve_log_dir(project_root: Path, requested_log_dir: str) -> Path:
    if requested_log_dir:
        log_dir = Path(requested_log_dir)
        if not log_dir.is_absolute():
            log_dir = project_root / log_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = project_root / "data" / "runtime" / "pytest_batch_runs" / timestamp
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _select_batches(
    batches: Sequence[TestBatch],
    start_batch: int,
    end_batch: int,
) -> List[tuple[int, TestBatch]]:
    if start_batch <= 0:
        raise ValueError("start_batch must be >= 1")
    effective_end = end_batch or len(batches)
    if effective_end < start_batch:
        raise ValueError("end_batch must be >= start_batch")
    selected: List[tuple[int, TestBatch]] = []
    for batch_number, batch in enumerate(batches, start=1):
        if batch_number < start_batch:
            continue
        if batch_number > effective_end:
            break
        selected.append((batch_number, batch))
    return selected


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root).resolve()
    tests_dir = _resolve_tests_dir(project_root, args.tests_dir)
    pytest_args = _normalize_pytest_args(args.pytest_args)
    log_dir = _resolve_log_dir(project_root, args.log_dir)

    files = discover_test_files(tests_dir, args.pattern)
    if not files:
        print(f"No test files matched {args.pattern} under {tests_dir}")
        return 1

    batches = build_batches(files, args.batch_size)
    selected_batches = _select_batches(batches, args.start_batch, args.end_batch)
    total_batches = len(selected_batches)
    if not selected_batches:
        print(
            f"No batches selected from total={len(batches)} with "
            f"start_batch={args.start_batch} end_batch={args.end_batch or len(batches)}."
        )
        return 1
    print(
        f"Discovered {len(files)} test files under {tests_dir}. "
        f"Running {total_batches} batch(es) with batch_size={args.batch_size}."
    )
    print(f"Logs will be written under {log_dir}.")

    results: List[BatchRunResult] = []
    for display_batch_number, (batch_number, batch) in enumerate(selected_batches, start=1):
        result = run_batch(
            batch=batch,
            display_batch_number=display_batch_number,
            display_total_batches=total_batches,
            batch_number=batch_number,
            total_batches=len(batches),
            python_executable=args.python_executable,
            pytest_args=pytest_args,
            project_root=project_root,
            log_dir=log_dir,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        results.append(result)
        print(
            f"[batch {display_batch_number}/{total_batches} | source {batch_number}/{len(batches)}] "
            f"exit={result.returncode} elapsed={_format_seconds(result.elapsed_seconds)}"
        )

    failed = [result for result in results if result.returncode != 0]
    passed = len(results) - len(failed)
    total_elapsed = sum(result.elapsed_seconds for result in results)
    print(
        "Batch summary: "
        f"total={len(results)} passed={passed} failed={len(failed)} "
        f"elapsed={_format_seconds(total_elapsed)}"
    )
    for result in failed:
        print(
            f"  failed batch {result.display_batch_number}/{result.display_total_batches} "
            f"(source {result.batch_number}/{result.total_batches}): "
            f"files={result.batch.start_index}-{result.batch.end_index} "
            f"elapsed={_format_seconds(result.elapsed_seconds)} "
            f"log={result.log_path}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
