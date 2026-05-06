# -*- coding: utf-8 -*-
"""Common helpers for process-based shortline adapters."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProcessAdapterConfig:
    python_executable: str
    script_path: Path
    runtime_dir: Path
    workdir: Path | None = None
    timeout_seconds: int = 120


def write_json_payload(file_path: Path, payload: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_payload(file_path: Path) -> Any:
    return json.loads(file_path.read_text(encoding="utf-8"))


def build_runtime_paths(runtime_dir: Path, prefix: str) -> tuple[Path, Path]:
    runtime_dir = runtime_dir.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    unique_id = uuid.uuid4().hex
    return (
        runtime_dir / f"{prefix}_request_{unique_id}.json",
        runtime_dir / f"{prefix}_output_{unique_id}.json",
    )


def run_external_python_process(
    *,
    config: ProcessAdapterConfig,
    request_path: Path,
    output_path: Path,
) -> None:
    command = [
        str(config.python_executable),
        str(config.script_path),
        str(request_path),
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(config.workdir) if config.workdir else None,
        capture_output=True,
        text=True,
        timeout=max(1, int(config.timeout_seconds)),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "external process failed: returncode={code}, stdout={stdout}, stderr={stderr}".format(
                code=completed.returncode,
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
            )
        )
    if not output_path.exists():
        raise RuntimeError(f"external process finished but output file was not created: {output_path}")
