# -*- coding: utf-8 -*-
"""Tests for the local pytest batch runner helper."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace


def _load_script_module():
    return importlib.import_module("scripts.run_pytest_batches")


def test_discover_test_files_returns_sorted_matches(tmp_path: Path) -> None:
    module = _load_script_module()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_b_case.py").write_text("", encoding="utf-8")
    (tests_dir / "test_a_case.py").write_text("", encoding="utf-8")
    (tests_dir / "helper.py").write_text("", encoding="utf-8")

    files = module.discover_test_files(tests_dir)

    assert [path.name for path in files] == ["test_a_case.py", "test_b_case.py"]


def test_build_batches_splits_files_with_source_ranges(tmp_path: Path) -> None:
    module = _load_script_module()
    files = [tmp_path / f"test_{index:02d}.py" for index in range(5)]

    batches = module.build_batches(files, batch_size=2)

    assert len(batches) == 3
    assert batches[0].start_index == 0
    assert batches[0].end_index == 1
    assert [path.name for path in batches[0].files] == ["test_00.py", "test_01.py"]
    assert batches[2].start_index == 4
    assert batches[2].end_index == 4
    assert [path.name for path in batches[2].files] == ["test_04.py"]


def test_main_runs_batches_and_reports_failures(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_script_module()
    files = [tmp_path / f"test_{index:02d}.py" for index in range(5)]
    recorded_commands = []

    monkeypatch.setattr(module, "discover_test_files", lambda root, pattern="test_*.py": files)

    def _fake_run_batch(
        *,
        batch,
        display_batch_number,
        display_total_batches,
        batch_number,
        total_batches,
        python_executable,
        pytest_args,
        project_root,
        log_dir,
        heartbeat_seconds,
    ):
        recorded_commands.append(
            {
                "batch_number": batch_number,
                "total_batches": total_batches,
                "display_batch_number": display_batch_number,
                "display_total_batches": display_total_batches,
                "python_executable": python_executable,
                "pytest_args": list(pytest_args),
                "project_root": project_root,
                "log_dir": log_dir,
                "heartbeat_seconds": heartbeat_seconds,
                "files": [path.name for path in batch.files],
            }
        )
        return SimpleNamespace(
            batch=batch,
            batch_number=batch_number,
            total_batches=total_batches,
            display_batch_number=display_batch_number,
            display_total_batches=display_total_batches,
            returncode=1 if batch_number == 2 else 0,
            elapsed_seconds=1.25 * batch_number,
            log_path=log_dir / f"batch_{batch_number:02d}.log",
        )

    monkeypatch.setattr(module, "run_batch", _fake_run_batch)

    rc = module.main(
        [
            "--project-root",
            str(tmp_path),
            "--batch-size",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert len(recorded_commands) == 3
    assert recorded_commands[0]["pytest_args"] == ["-m", "not network", "-q"]
    assert recorded_commands[1]["files"] == ["test_02.py", "test_03.py"]
    assert "Batch summary: total=3 passed=2 failed=1" in captured.out
    assert "failed batch 2" in captured.out


def test_main_supports_running_selected_batch_range(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_script_module()
    files = [tmp_path / f"test_{index:02d}.py" for index in range(6)]
    seen_batch_numbers = []

    monkeypatch.setattr(module, "discover_test_files", lambda root, pattern="test_*.py": files)

    def _fake_run_batch(
        *,
        batch,
        display_batch_number,
        display_total_batches,
        batch_number,
        total_batches,
        python_executable,
        pytest_args,
        project_root,
        log_dir,
        heartbeat_seconds,
    ):
        seen_batch_numbers.append(batch_number)
        return SimpleNamespace(
            batch=batch,
            batch_number=batch_number,
            total_batches=total_batches,
            display_batch_number=display_batch_number,
            display_total_batches=display_total_batches,
            returncode=0,
            elapsed_seconds=1.0,
            log_path=log_dir / f"batch_{batch_number:02d}.log",
        )

    monkeypatch.setattr(module, "run_batch", _fake_run_batch)

    rc = module.main(
        [
            "--project-root",
            str(tmp_path),
            "--batch-size",
            "2",
            "--start-batch",
            "2",
            "--end-batch",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert seen_batch_numbers == [2]
    assert "Running 1 batch(es)" in captured.out
    assert "source 2/3" in captured.out


def test_run_batch_writes_combined_output_to_log_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    batch = module.TestBatch(
        start_index=0,
        end_index=1,
        files=[tmp_path / "test_00.py", tmp_path / "test_01.py"],
    )

    class _FakeProcess:
        def __init__(self, command, cwd, stdout, stderr, text):
            assert command[:3] == ["python", "-m", "pytest"]
            assert cwd == tmp_path
            assert stderr == module.subprocess.STDOUT
            assert text is True
            stdout.write("ok\n")
            stdout.flush()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", _FakeProcess)

    result = module.run_batch(
        batch=batch,
        display_batch_number=1,
        display_total_batches=3,
        batch_number=1,
        total_batches=3,
        python_executable="python",
        pytest_args=["-q"],
        project_root=tmp_path,
        log_dir=tmp_path / "logs",
        heartbeat_seconds=30,
    )

    assert result.returncode == 0
    assert result.log_path == (tmp_path / "logs" / "batch_01.log")
    assert result.log_path.read_text(encoding="utf-8") == "ok\n"


def test_run_batch_emits_heartbeat_for_long_running_batches(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_script_module()
    batch = module.TestBatch(
        start_index=2,
        end_index=3,
        files=[tmp_path / "test_02.py", tmp_path / "test_03.py"],
    )

    class _FakeProcess:
        def __init__(self, command, cwd, stdout, stderr, text):
            stdout.write("running\n")
            stdout.flush()
            self.returncode = 0
            self._poll_count = 0

        def poll(self):
            self._poll_count += 1
            if self._poll_count == 1:
                return None
            return self.returncode

        def wait(self):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", _FakeProcess)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    result = module.run_batch(
        batch=batch,
        display_batch_number=2,
        display_total_batches=3,
        batch_number=2,
        total_batches=3,
        python_executable="python",
        pytest_args=["-q"],
        project_root=tmp_path,
        log_dir=tmp_path / "logs",
        heartbeat_seconds=5,
    )

    captured = capsys.readouterr()
    assert result.returncode == 0
    assert "heartbeat" in captured.out
