"""batch_supervise（C1 心跳 / C3 收尾）的离线测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.batch_supervise import (  # noqa: E402
    all_done,
    expand_log_patterns,
    format_snapshot,
    main,
    parse_steps,
    read_log_stats,
    run_steps,
)


def test_expand_log_patterns_glob_and_dedupe(tmp_path):
    (tmp_path / "a.log").write_text("x", encoding="utf-8")
    (tmp_path / "b.log").write_text("y", encoding="utf-8")
    paths = expand_log_patterns([str(tmp_path / "*.log"), str(tmp_path / "a.log")])
    assert [p.name for p in paths] == ["a.log", "b.log"]


def test_read_log_stats_counts(tmp_path):
    log = tmp_path / "batch.log"
    log.write_text(
        "########## day1\nFAILED code=1\n########## day2\nALL-DONE\n",
        encoding="utf-8",
    )
    stats = read_log_stats(log, count_pattern="##########", done_marker="ALL-DONE", fail_marker="FAILED")
    assert stats["exists"] is True
    assert stats["count"] == 2
    assert stats["failed"] == 1
    assert stats["done"] is True


def test_read_log_stats_missing(tmp_path):
    stats = read_log_stats(tmp_path / "nope.log", done_marker="X")
    assert stats["exists"] is False
    assert stats["count"] == 0
    assert stats["done"] is False


def test_all_done_and_format_snapshot():
    stats = [
        {"name": "a", "exists": True, "count": 3, "failed": 0, "done": True, "age_seconds": 10, "size_bytes": 1},
        {"name": "b", "exists": True, "count": 1, "failed": 2, "done": True, "age_seconds": 3600, "size_bytes": 1},
    ]
    assert all_done(stats) is True
    line = format_snapshot(stats)
    assert "status=DONE done=2/2" in line
    assert "a: n=3 done=1 fail=0 age=10s" in line
    assert "b: n=1 done=1 fail=2 age=60m" in line
    stats[1]["done"] = False
    assert all_done(stats) is False
    assert "status=RUNNING done=1/2" in format_snapshot(stats)


def test_parse_steps_valid_and_invalid():
    assert parse_steps(["扫描::echo hi", " 量化 :: echo ok "]) == [("扫描", "echo hi"), ("量化", "echo ok")]
    with pytest.raises(SystemExit):
        parse_steps(["missing-separator"])


def test_run_steps_with_executor(tmp_path):
    log_path = tmp_path / "status.log"

    def executor(command: str):
        return (0 if "ok" in command else 2, f"output of {command}")

    with open(log_path, "w", encoding="utf-8") as handle:
        results = run_steps(
            [("第一步", "echo ok"), ("第二步", "echo bad")],
            status_handle=handle,
            executor=executor,
        )
    assert [item["exit_code"] for item in results] == [0, 2]
    text = log_path.read_text(encoding="utf-8")
    assert "[step:第一步] exit=0" in text
    assert "output of echo bad" in text
    assert "[step:第二步] exit=2" in text


def test_main_once_outputs_snapshot(tmp_path, capsys):
    log = tmp_path / "x.log"
    log.write_text("##########\n##########\nALL-DONE\n", encoding="utf-8")
    code = main(["--once", "--log", str(log), "--done-marker", "ALL-DONE"])
    out = capsys.readouterr().out
    assert code == 0
    assert "status=DONE done=1/1" in out
    assert "n=2" in out
