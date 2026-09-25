# -*- coding: utf-8 -*-
"""Tests for scripts/sync_investment_data.py (offline only, no network)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "sync_investment_data.py"
    spec = importlib.util.spec_from_file_location("sync_investment_data", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


sync_module = _load_module()


def test_parse_manifest_and_find_sha256() -> None:
    payload = {
        "tag": "2026-09-24",
        "sha256": "a" * 64,
        "files": {"qlib_bin.tar.gz": "b" * 64},
    }
    summary = sync_module.parse_manifest(payload)
    assert summary["tag"] == "2026-09-24"
    assert "sha256" in summary["keys"]
    assert sync_module.find_sha256(payload) == "a" * 64

    with pytest.raises(ValueError):
        sync_module.parse_manifest(["not", "a", "dict"])

    assert sync_module.find_sha256({"note": "no checksum"}) is None


def test_sha256_of_file(tmp_path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"hello investment_data")
    expected = hashlib.sha256(b"hello investment_data").hexdigest()
    assert sync_module.sha256_of_file(sample) == expected


def test_strip_member_guard() -> None:
    assert sync_module._strip_member("qlib_bin/features/sh600000.parquet", 1) == "features/sh600000.parquet"
    assert sync_module._strip_member("qlib_bin", 1) is None
    with pytest.raises(ValueError):
        sync_module._strip_member("../evil.txt", 1)
    with pytest.raises(ValueError):
        sync_module._strip_member("/etc/passwd", 1)


def test_extract_archive_strips_and_skips_unsafe(tmp_path) -> None:
    archive_path = tmp_path / "qlib_bin.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        data = b"feature-bytes"
        info = tarfile.TarInfo("qlib_bin/features/sh600000.parquet")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        dir_info = tarfile.TarInfo("qlib_bin/features")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)

    target_dir = tmp_path / "target"
    extracted = sync_module.extract_archive(archive_path, target_dir)

    assert extracted == 1
    assert (target_dir / "features" / "sh600000.parquet").read_bytes() == b"feature-bytes"


def test_manifest_json_roundtrip(tmp_path) -> None:
    manifest = tmp_path / "qlib_bin.manifest.json"
    manifest.write_text(json.dumps({"tag": "2026-09-24"}), encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert sync_module.parse_manifest(payload)["tag"] == "2026-09-24"
