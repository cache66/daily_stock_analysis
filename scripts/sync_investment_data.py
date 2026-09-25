#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync the chenditc/investment_data daily dataset (P0 research data foundation).

The dataset ships as a GitHub Release asset pair:
  - qlib_bin.tar.gz          (qlib binary feature data)
  - qlib_bin.manifest.json   (release metadata)

This script only downloads / verifies / extracts; it never touches the
trading pipeline and never overwrites existing data unless ``--force``.

Examples:
    python scripts/sync_investment_data.py --check
    python scripts/sync_investment_data.py --download
    python scripts/sync_investment_data.py --extract-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TARGET_DIR = PROJECT_ROOT / "data" / "external" / "investment_data" / "qlib_bin"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "external" / "investment_data" / "downloads"

RELEASE_ASSET_BASE = "https://github.com/chenditc/investment_data/releases/latest/download"
ARCHIVE_NAME = "qlib_bin.tar.gz"
MANIFEST_NAME = "qlib_bin.manifest.json"


def parse_manifest(payload: Any) -> Dict[str, Any]:
    """Validate a manifest payload and return a summary dict."""

    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    tag = payload.get("tag") or payload.get("release_tag") or payload.get("expected_tag")
    return {"tag": str(tag).strip() if tag else None, "keys": sorted(str(key) for key in payload.keys())}


def find_sha256(payload: Dict[str, Any]) -> Optional[str]:
    """Find a plausible sha256 for the archive inside the manifest."""

    for key, value in payload.items():
        key_text = str(key).lower()
        if "sha256" not in key_text:
            continue
        if isinstance(value, str):
            candidate = value.strip().lower()
            if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
                return candidate
    return None


def sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, dest: Path, *, timeout: int = 120) -> Path:
    """Download ``url`` to ``dest`` atomically (write .part then rename)."""

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as response, open(part_path, "wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    part_path.replace(dest)
    return dest


def http_content_length(url: str, *, timeout: int = 60) -> Optional[int]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:  # noqa: BLE001 - 探测失败不作为错误
        return None


def _strip_member(name: str, components: int) -> Optional[str]:
    """Return the stripped relative member path, rejecting unsafe entries."""

    parts = Path(name).parts
    if name.startswith("/") or any(part == ".." for part in parts):
        raise ValueError(f"unsafe archive member: {name}")
    if len(parts) <= components:
        return None
    return Path(*parts[components:]).as_posix()


def extract_archive(archive: Path, target_dir: Path, *, strip_components: int = 1) -> int:
    """Extract a tarball with path-traversal guard; returns extracted entries."""

    archive = Path(archive)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            stripped = _strip_member(member.name, strip_components)
            if not stripped:
                continue
            if member.isdir():
                (target_dir / stripped).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            dest_path = target_dir / stripped
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as handle:
                handle.write(fileobj.read())
            extracted += 1
    return extracted


def _human_size(size: Optional[int]) -> str:
    if not size:
        return "unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def run_check(archive_dir: Path) -> int:
    manifest_url = f"{RELEASE_ASSET_BASE}/{MANIFEST_NAME}"
    archive_url = f"{RELEASE_ASSET_BASE}/{ARCHIVE_NAME}"
    print(f"清单: {manifest_url}")
    try:
        with urllib.request.urlopen(manifest_url, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 网络不可达时给出可操作提示
        print(f"网络不可达或超时: {exc}", file=sys.stderr)
        print("可稍后重试，或手动下载以下文件后使用 --extract-only：", file=sys.stderr)
        print(f"  {manifest_url}", file=sys.stderr)
        print(f"  {archive_url}", file=sys.stderr)
        return 1
    summary = parse_manifest(payload)
    print(f"清单键: {', '.join(summary['keys'])}")
    if summary["tag"]:
        print(f"版本: {summary['tag']}")
    sha256 = find_sha256(payload)
    if sha256:
        print(f"清单 sha256: {sha256}")
    else:
        print("清单 sha256: 未提供（下载后以实际 sha256 为准）")
    size = http_content_length(archive_url)
    print(f"归档大小: {_human_size(size)}（{archive_url}）")
    print(f"计划落盘: {archive_dir}")
    return 0


def run_download(archive_dir: Path, target_dir: Path, *, force: bool, no_extract: bool) -> int:
    manifest_path = archive_dir / MANIFEST_NAME
    archive_path = archive_dir / ARCHIVE_NAME

    if archive_path.exists() and not force:
        print(f"已存在归档（跳过下载，--force 可覆盖）: {archive_path}")
    else:
        manifest_url = f"{RELEASE_ASSET_BASE}/{MANIFEST_NAME}"
        archive_url = f"{RELEASE_ASSET_BASE}/{ARCHIVE_NAME}"
        print(f"下载清单: {manifest_url}")
        try:
            download_file(manifest_url, manifest_path)
            print(f"下载归档: {archive_url}")
            download_file(archive_url, archive_path)
        except Exception as exc:  # noqa: BLE001 - 网络不可达时给出可操作提示
            print(f"下载失败: {exc}", file=sys.stderr)
            print("可稍后重试，或手动下载以下文件到", archive_dir, "后使用 --extract-only：", file=sys.stderr)
            print(f"  {manifest_url}", file=sys.stderr)
            print(f"  {archive_url}", file=sys.stderr)
            return 1

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = parse_manifest(payload)
    actual_sha256 = sha256_of_file(archive_path)
    print(f"归档: {archive_path}（{_human_size(archive_path.stat().st_size)}）")
    print(f"sha256: {actual_sha256}")

    expected = find_sha256(payload)
    if expected:
        if expected == actual_sha256:
            print("sha256 校验: 一致")
        else:
            print(f"sha256 校验: 不一致（清单 {expected}），请勿使用该归档", file=sys.stderr)
            return 2
    if summary["tag"]:
        print(f"版本: {summary['tag']}")

    if no_extract:
        print("按 --no-extract 跳过解压")
        return 0

    if target_dir.exists() and any(target_dir.iterdir()) and not force:
        print(f"目标已存在数据（跳过解压，--force 可覆盖）: {target_dir}")
        return 0
    extracted = extract_archive(archive_path, target_dir)
    print(f"解压完成: {target_dir}（{extracted} 个文件）")
    return 0


def run_extract_only(archive_dir: Path, target_dir: Path, *, force: bool) -> int:
    archive_path = archive_dir / ARCHIVE_NAME
    if not archive_path.exists():
        print(f"归档不存在: {archive_path}", file=sys.stderr)
        return 1
    if target_dir.exists() and any(target_dir.iterdir()) and not force:
        print(f"目标已存在数据（跳过解压，--force 可覆盖）: {target_dir}")
        return 0
    extracted = extract_archive(archive_path, target_dir)
    print(f"解压完成: {target_dir}（{extracted} 个文件）")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="investment_data 日更数据下载/校验/解压（P0 数据底座）。")
    parser.add_argument("--check", action="store_true", help="只看 release 清单与归档大小（需要网络）。")
    parser.add_argument("--download", action="store_true", help="下载 + 校验 + 解压。")
    parser.add_argument("--extract-only", action="store_true", help="只解压已下载的归档。")
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR), help="解压目标目录。")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR), help="归档下载目录。")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有归档/目标数据。")
    parser.add_argument("--no-extract", action="store_true", help="只下载校验，不解压。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_dir = Path(args.archive_dir)
    target_dir = Path(args.target_dir)

    if args.check:
        return run_check(archive_dir)
    if args.extract_only:
        return run_extract_only(archive_dir, target_dir, force=args.force)
    if args.download:
        return run_download(archive_dir, target_dir, force=args.force, no_extract=args.no_extract)

    print("请指定 --check / --download / --extract-only 之一（--help 查看说明）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
