"""rebuild_stock_daily_qfq 失败重试模式的离线测试（不触网/不写库）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import rebuild_stock_daily_qfq as rebuild  # noqa: E402


def test_failed_codes_read_write_roundtrip(tmp_path):
    path = tmp_path / "failed.txt"
    assert rebuild._read_failed_codes(path) == []
    rebuild._write_failed_codes(path, ["600001", "000002", "600001"])
    assert path.read_text(encoding="utf-8") == "000002\n600001\n"
    assert rebuild._read_failed_codes(path) == ["000002", "600001"]
    rebuild._write_failed_codes(path, [])
    assert not path.exists()


def test_retry_failed_codes_shrinks_until_zero(tmp_path, monkeypatch):
    path = tmp_path / "failed.txt"
    path.write_text("600001\n600002\n600003\n", encoding="utf-8")
    calls = []
    failures_by_attempt = [["600002", "600003"], ["600003"], []]

    def fake_run_rebuild(codes, **kwargs):
        calls.append(list(codes))
        attempt = len(calls) - 1
        failures = [code for code in codes if code in failures_by_attempt[attempt]]
        return {
            "total": len(codes),
            "updated": len(codes) - len(failures),
            "skipped": 0,
            "failed": len(failures),
            "failed_codes": failures,
        }

    monkeypatch.setattr(rebuild, "run_rebuild", fake_run_rebuild)
    result = rebuild.retry_failed_codes(
        db=None,
        failed_path=path,
        start_date="2026-01-01",
        end_date="2026-09-26",
        source="akshare",
        max_attempts=3,
    )
    assert result["remaining"] == []
    assert [item["remaining"] for item in result["attempts"]] == [2, 1, 0]
    assert calls == [["600001", "600002", "600003"], ["600002", "600003"], ["600003"]]
    assert not path.exists()


def test_retry_failed_codes_respects_max_attempts(tmp_path, monkeypatch):
    path = tmp_path / "failed.txt"
    path.write_text("600001\n", encoding="utf-8")

    def fake_run_rebuild(codes, **kwargs):
        return {
            "total": len(codes),
            "updated": 0,
            "skipped": 0,
            "failed": len(codes),
            "failed_codes": list(codes),
        }

    monkeypatch.setattr(rebuild, "run_rebuild", fake_run_rebuild)
    result = rebuild.retry_failed_codes(
        db=None,
        failed_path=path,
        start_date="2026-01-01",
        end_date="2026-09-26",
        source="baostock",
        max_attempts=2,
    )
    assert result["remaining"] == ["600001"]
    assert len(result["attempts"]) == 2
    assert rebuild._read_failed_codes(path) == ["600001"]


def test_retry_failed_codes_noop_when_empty(tmp_path):
    result = rebuild.retry_failed_codes(
        db=None,
        failed_path=tmp_path / "none.txt",
        start_date="2026-01-01",
        end_date="2026-09-26",
    )
    assert result["attempts"] == []
    assert result["remaining"] == []
