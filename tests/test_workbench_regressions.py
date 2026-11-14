"""工作台复审反例：真实临时库、最终导入/回填入口，外部输入固定。"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

from scripts import track_from_board as importer
from src.config import Config
from src.services import daily_board_service as boards
from src.services import track_outcome_service as outcomes
from src.services import signal_regime
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager, KlineSignalSnapshot


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    service = ResearchTrackingService()
    monkeypatch.setattr(outcomes, "load_suspicious_codes", lambda: set())
    monkeypatch.setattr(boards, "load_suspicious_codes", lambda: set())
    monkeypatch.setattr(boards, "read_rebuild_failures", lambda: (set(), 0, None))
    try:
        yield db, service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _save(db, bars, code="600100"):
    """写日线；2 元组按“次日开盘=前一日收盘”补齐开盘价（口子无跳空的基准）。

    真实口径是**次日开盘入场**，所以 fixtures 必须带 open；2 元组仍可用，
    只是开盘价按前一根收盘推。
    """
    rows = []
    prev_close = None
    for row in bars:
        if len(row) >= 3:
            day, open_price, close = row[0], row[1], row[2]
        else:
            day, close = row[0], row[1]
            open_price = prev_close if prev_close is not None else close
        prev_close = close
        rows.append(dict(
            date=day, open=open_price, high=max(open_price, close),
            low=min(open_price, close), close=close, volume=10000, amount=close * 10000,
        ))
    db.save_daily_data(pd.DataFrame(rows), code, "baostock_qfq_rebuild")


def _track(service, code="600100"):
    t, _ = service.create_or_get(
        market="cn", code=code, hypothesis_key="signal:hundred_day_high:2026-11-06",
        next_review_at=datetime(2026, 11, 9),
    )
    return service.change_status(t.track_id, "tracking", reason="fixture")


def _events(service, track):
    return [e for e in service.repo.list_events(track.id) if e.source_type == "stock_daily_qfq"]


# 入场 = 11-09 开盘 10.0；T+1/T+3/T+5 = 11-10 / 11-12 / 11-16 收盘
BARS_END = date(2026, 11, 16)


def _bars():
    return list(zip(
        ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12",
         "2026-11-13", "2026-11-16"],
        [10., 10.2, 10.3, 10.5, 10.6, 10.8, 11.0],
    ))


def test_missing_internal_session_never_shifts_checkpoints(workspace):
    """内部缺一根（11-10）不平移检查点：入场仍是 11-09 开盘，检查点按日历取。"""
    db, service = workspace
    _save(db, [
        ("2026-11-06", 10.0, 10.0),
        ("2026-11-09", 10.0, 10.2),
        ("2026-11-11", 10.2, 10.5),
        ("2026-11-12", 10.5, 10.6),
        ("2026-11-13", 10.6, 10.8),
        ("2026-11-16", 10.8, 11.0),
    ])
    t = _track(service)
    outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 16))
    payload = json.loads(_events(service, t)[-1].payload_json)
    assert payload["entry_date"] == "2026-11-09"
    assert payload["entry_close"] == pytest.approx(10.)
    # T+1 就是缺的那根（11-10）：留空，不拿 11-11 顶替
    assert payload["ret1"] is None
    assert payload["ret3"] == pytest.approx(0.06)     # 11-12 收盘 10.6 / 10.0 - 1
    assert payload["ret5"] == pytest.approx(0.10)     # 11-16 收盘 11.0 / 10.0 - 1
    assert payload["missing_dates"] == ["2026-11-10"]
    assert service.repo.get_by_track_id(t.track_id).status == "tracking"


def test_as_of_limits_default_database_loader(workspace):
    db, service = workspace
    _save(db, _bars())
    t = _track(service)
    outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 9))
    event = _events(service, t)[-1]
    assert json.loads(event.payload_json)["ret5"] is None
    assert json.loads(event.payload_json)["last_date"] == "2026-11-09"
    assert service.repo.get_by_track_id(t.track_id).status == "tracking"


def test_measurement_only_track_not_auto_closed(workspace):
    """method 研究（branch=measurement_only）价格窗口走完只写测量，不结束研究。"""
    db, service = workspace
    _save(db, _bars())
    track, _ = service.create_or_get(
        market="cn", code="600100",
        hypothesis_key="signal:hotspot:2026-11-06",
        branch="measurement_only",
        next_review_at=datetime(2026, 11, 20),
    )
    service.change_status(track.track_id, "tracking", reason="fixture")
    result = outcomes.evaluate_track_outcomes(as_of=BARS_END)
    assert result["measured_only"] == 1
    assert result["closed"] == 0
    assert service.repo.get_by_track_id(track.track_id).status == "tracking"
    payload = json.loads(_events(service, track)[-1].payload_json)
    assert payload["measurement_only"] is True
    assert payload["ret5"] is not None


def test_signal_day_cutoff_is_not_due_not_insufficient(workspace):
    """截止日仍是信号日时，入场日还没到：记 not_due，不伪造缺数。"""
    db, service = workspace
    _save(db, _bars())
    t = _track(service)
    result = outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 6))
    assert result["checked"] == 1
    assert result["not_due"] == 1
    assert result["insufficient"] == 0 and result["written"] == 0
    assert not _events(service, t)


def test_review_reminder_never_delays_measurement(workspace):
    """next_review_at 只是人工复核提醒，不能把 T+1/T+3 观测一起推后。"""
    db, service = workspace
    _save(db, _bars())
    t, _ = service.create_or_get(
        market="cn", code="600100",
        hypothesis_key="signal:hundred_day_high:2026-11-06",
        next_review_at=datetime(2027, 1, 4),
    )
    service.change_status(t.track_id, "tracking", reason="fixture")
    result = outcomes.evaluate_track_outcomes(as_of=BARS_END)
    assert result["checked"] == 1 and result["written"] == 1
    assert service.repo.get_by_track_id(t.track_id).status == "closed"


@pytest.mark.parametrize("complete", [False, True])
def test_price_correction_appends_version_for_open_and_auto_closed(workspace, complete):
    db, service = workspace
    _save(db, _bars() if complete else _bars()[:2])
    t = _track(service)
    cutoff = BARS_END if complete else date(2026, 11, 9)
    outcomes.evaluate_track_outcomes(as_of=cutoff)
    first = _events(service, t)[0]
    first_payload = json.loads(first.payload_json)
    # 修订 11-09：它既是入场日（开盘价 12.0），也是 ret1 的检查点（收盘 12.0）
    _save(db, [("2026-11-09", 12., 12.)])
    outcomes.evaluate_track_outcomes(as_of=cutoff)
    events = _events(service, t)
    assert len(events) == 2
    assert events[0].payload_json == first.payload_json
    correction = json.loads(events[-1].payload_json)
    assert correction["entry_date"] == "2026-11-09"
    assert correction["entry_close"] == pytest.approx(12.)
    if complete:
        # 入场 10.0 -> 12.0，检查点收盘不变（11-10=10.3 / 11-12=10.6 / 11-16=11.0）
        assert first_payload["entry_close"] == pytest.approx(10.)
        assert correction["ret1"] == pytest.approx(10.3 / 12 - 1)
        assert correction["ret3"] == pytest.approx(10.6 / 12 - 1)
        assert correction["ret5"] == pytest.approx(11.0 / 12 - 1)
    else:
        # 只有信号日 + 入场日，检查点未到：仍是 partial，但版本已更新
        assert first_payload["entry_close"] == pytest.approx(10.)
        assert correction["ret1"] is None
        assert correction["complete"] is False if "complete" in correction else True
    assert correction["supersedes_event_id"] == first.id
    assert events[-1].source_ref != first.source_ref
    assert outcomes.evaluate_track_outcomes(as_of=cutoff)["written"] == 0


def test_outcome_and_close_rollback_together(workspace, monkeypatch):
    db, service = workspace
    _save(db, _bars())
    t = _track(service)
    from sqlalchemy import event
    from src.storage import ResearchTrackEvent

    def fail_close(mapper, connection, target):
        if target.event_type == "closed":
            raise RuntimeError("interrupted during close")

    event.listen(ResearchTrackEvent, "before_insert", fail_close)
    try:
        first = outcomes.evaluate_track_outcomes(as_of=BARS_END)
    finally:
        event.remove(ResearchTrackEvent, "before_insert", fail_close)
    # 单票写入失败只计错误、不中断整批；证据与关闭仍必须同事务
    assert first["errors"] == 1 and first["written"] == 0
    assert not _events(service, t)
    assert service.repo.get_by_track_id(t.track_id).status == "tracking"
    result = outcomes.evaluate_track_outcomes(as_of=BARS_END)
    assert result["written"] == result["closed"] == 1


def test_retry_recovers_old_separate_commit_interruption(workspace):
    db, service = workspace
    _save(db, _bars())
    t = _track(service)
    outcomes.evaluate_track_outcomes(as_of=BARS_END)
    # 模拟旧版已提交 outcome、未提交 close 的持久化状态。
    with db.session_scope() as session:
        session.execute(text("UPDATE research_tracks SET status='tracking', closed_reason=NULL WHERE id=:id"), {"id": t.id})
    result = outcomes.evaluate_track_outcomes(as_of=BARS_END)
    assert result["written"] == 0 and result["closed"] == 1
    assert len(_events(service, t)) == 1
    assert service.repo.get_by_track_id(t.track_id).status == "closed"


def test_calendar_unavailable_never_guesses_sessions(workspace, monkeypatch):
    db, service = workspace
    _save(db, _bars())
    t = _track(service)

    def unavailable(*args):
        raise ValueError("calendar out of range")

    monkeypatch.setattr(outcomes, "get_trading_sessions", unavailable)
    result = outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 13))
    assert result["calendar_unavailable"] == 1
    assert not _events(service, t)
    from scripts import evaluate_track_outcomes as entry
    monkeypatch.setattr(sys, "argv", ["evaluate_track_outcomes.py", "--as-of", "2026-11-13"])
    assert entry.main() == 1


def test_post_done_never_hides_main_failure(tmp_path):
    log = tmp_path / "matrix.log"
    log.write_text(
        "[matrix_daily] start snapshot=2026-11-06 19:05:00\n"
        "[matrix_daily] done snapshot=2026-11-06 exit=1 2026-11-06 19:06:00\n"
        "[matrix_daily] post done (board/track/outcome/cards) 2026-11-06 19:07:00\n",
    )
    health = boards.read_task_health("matrix", "matrix", log, "2026-11-06", True)
    assert health.status == "failed" and health.exit_code == 1


@pytest.fixture()
def screening(workspace, tmp_path, monkeypatch):
    db, service = workspace
    log = tmp_path / "screening.log"
    log.write_text("[screening] start 2026-11-06 18:10:00\n[screening] done exit=0 2026-11-06 18:18:12\n")
    audits = [dict(strategy="dual_low", run_id="batch-18h", as_of="2026-11-06",
                   generated_at="2026-11-06 18:18:00", top_n=20, candidates=[{"code": "600100"}])]
    with db.get_session() as session:
        session.add(KlineSignalSnapshot(
            signal_type="screening__dual_low", signal_date=date(2026, 11, 6), code="600100", name="fixture",
            metrics_payload=json.dumps({"run_id": "batch-18h", "reason": "original"}),
            created_at=datetime(2026, 11, 6, 18, 17, 50), updated_at=datetime(2026, 11, 6, 18, 17, 50),
        ))
        session.commit()
    _save(db, _bars()[:1])
    monkeypatch.setattr(boards, "build_health", lambda day: [boards.read_task_health("screening", "screening", log, day, True)])
    monkeypatch.setattr(boards, "load_screening_audits", lambda day: audits)
    monkeypatch.setattr(importer, "build_board_for_date", lambda day, **kw: boards.build_board_for_date(
        day, db_manager=db, output_path=None, market_regime="unknown",
    ))
    signal_regime.DEFAULT_OUTPUT.write_text(json.dumps({
        "as_of": "2026-11-06", "schema_version": signal_regime.CACHE_SCHEMA_VERSION,
        "families": {family: {"verdict": "caution"} for family in signal_regime.TRACKED_FAMILIES},
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["track_from_board.py", "--date", "2026-11-06"])
    monkeypatch.setattr(importer, "_read_regime", lambda: "unknown")
    return db, service, log, audits


@pytest.mark.parametrize("problem", ["running", "missing_audit", "wrong_run", "partial_rows", "old_snapshot"])
def test_unready_batch_is_read_only_through_actual_importer(screening, problem):
    db, service, log, audits = screening
    if problem == "running":
        log.write_text("[screening] start 2026-11-06 18:10:00\n")
    elif problem == "missing_audit":
        audits.clear()
    elif problem == "wrong_run":
        audits[0]["run_id"] = "other-run"
    elif problem == "partial_rows":
        audits[0]["candidates"].append({"code": "600101"})
    else:
        with db.session_scope() as session:
            session.execute(text("UPDATE kline_signal_snapshot SET updated_at='2026-11-06 17:00:00'"))
    board, _ = importer.build_board_for_date("2026-11-06")
    assert len(board.candidates) == 1
    assert "批次未就绪" in board.candidates[0].flags
    assert importer.main() == 0
    assert service.repo.list_tracks() == []


def test_import_preserves_provenance_and_appends_same_day_revision(screening):
    db, service, log, audits = screening
    importer.main()
    track = service.repo.list_tracks()[0]
    first = service.repo.latest_source_event(track.id, "kline_signal_snapshot")
    # 筛选批次有审计生成时间，能证明“该时点已落库可读” -> 不再留空
    expected_available = (
        datetime(2026, 11, 6, 18, 18)
        .replace(tzinfo=boards.LOCAL_TZ)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    assert first.available_at == expected_available
    assert "available_at 为空" not in (first.limitation or "")
    assert "batch-18h" in first.source_ref
    assert first.source_version != importer.RULE_TAG
    assert json.loads(first.payload_json)["batch_ref"]
    assert json.loads(first.payload_json)["source_snapshot"]["metrics"]["reason"] == "original"
    assert track.invalidation == ""
    initial_count = len(service.repo.list_events(track.id))
    importer.main()
    assert len(service.repo.list_events(track.id)) == initial_count
    with db.session_scope() as session:
        session.execute(text("UPDATE kline_signal_snapshot SET metrics_payload=:v"), {
            "v": json.dumps({"run_id": "batch-18h-new", "reason": "revised"}),
        })
    audits[0]["run_id"] = "batch-18h-new"
    log.write_text("[screening] start 2026-11-06 18:15:00\n[screening] done exit=0 2026-11-06 18:19:00\n")
    importer.main()
    latest = service.repo.latest_source_event(track.id, "kline_signal_snapshot")
    assert latest.id != first.id
    assert latest.source_ref != first.source_ref
    assert json.loads(latest.payload_json)["supersedes_event_id"] == first.id
    assert json.loads(first.payload_json)["source_snapshot"]["metrics"]["reason"] == "original"
    assert json.loads(latest.payload_json)["source_snapshot"]["metrics"]["reason"] == "revised"
    assert service.repo.get_by_track_id(track.track_id).hypothesis == "original"


@pytest.mark.parametrize("restriction", ["environment", "signal_checkup"])
def test_no_cooldown_does_not_bypass_environment_or_checkup(screening, tmp_path, monkeypatch, restriction):
    _, service, _, _ = screening
    from src.services import track_admission_service as admission

    monkeypatch.setattr(sys, "argv", ["track_from_board.py", "--date", "2026-11-06", "--no-cooldown"])
    if restriction == "environment":
        tuning = tmp_path / "tuning.json"
        tuning.write_text(json.dumps({"defensive_demote_single": ["screening__dual_low"]}))
        monkeypatch.setattr(admission, "TUNING_PATH", tuning)
        monkeypatch.setattr(importer, "_read_regime", lambda: "defensive")
    else:
        data = json.loads(signal_regime.DEFAULT_OUTPUT.read_text())
        data["families"]["screening__dual_low"]["verdict"] = "block"
        signal_regime.DEFAULT_OUTPUT.write_text(json.dumps(data))
    assert importer.main() == 0
    assert not service.repo.list_tracks()


def test_corrected_legacy_source_keeps_old_event_and_adds_honest_metadata(screening):
    _, service, _, _ = screening
    old, _ = service.create_or_get(
        market="cn", code="600100", hypothesis_key="signal:screening__dual_low:2026-11-06",
        source_type="kline_signal_snapshot", source_ref="screening__dual_low:2026-11-06:600100",
        source_version="a1a_track_bootstrap_v0", available_at=datetime(2026, 11, 6, 15),
    )
    importer.main()
    events = [e for e in service.repo.list_events(old.id) if e.source_type == "kline_signal_snapshot"]
    assert len(events) == 2
    assert events[0].available_at == datetime(2026, 11, 6, 15)
    assert events[1].available_at == (
        datetime(2026, 11, 6, 18, 18)
        .replace(tzinfo=boards.LOCAL_TZ)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    assert json.loads(events[1].payload_json)["supersedes_event_id"] == events[0].id


def test_reading_across_batch_change_cannot_import(screening, monkeypatch):
    _, service, _, _ = screening
    from dataclasses import replace

    original = boards.build_health
    count = 0

    def changing(day):
        nonlocal count
        count += 1
        health = original(day)
        return [replace(h, batch_ref=f"batch-{count}") for h in health]

    monkeypatch.setattr(boards, "build_health", changing)
    importer.main()
    assert not service.repo.list_tracks()


def test_new_source_failure_does_not_block_other_completed_source(screening, monkeypatch):
    db, service, log, _ = screening
    log.write_text("[screening] start 2026-11-06 18:10:00\n")
    original = boards.build_health
    ready = boards.SourceHealth("matrix", "matrix", "ok", "done", "2026-11-06 19:30:00", 0,
                               "2026-11-06 19:00:00", "matrix-batch")
    monkeypatch.setattr(boards, "build_health", lambda day: [*original(day), ready])
    with db.get_session() as session:
        session.add(KlineSignalSnapshot(
            signal_type="hundred_day_high", signal_date=date(2026, 11, 6), code="600101",
            metrics_payload='{"close":10}', updated_at=datetime(2026, 11, 6, 19, 20),
        ))
        session.commit()
    _save(db, _bars()[:1], code="600101")
    importer.main()
    assert [t.code for t in service.repo.list_tracks()] == ["600101"]


def test_bse_legal_jump_is_written_and_not_quarantined(workspace):
    from scripts.rebuild_stock_daily_qfq import normalize_and_validate_frame, scan_abnormal_jumps

    db, _ = workspace
    frame = pd.DataFrame(dict(date=["2026-11-05", "2026-11-06"], close=[10., 13.],
                              volume=[10000., 10000.], amount=[100000., 130000.]))
    normalized = normalize_and_validate_frame(frame, "920001")
    db.save_daily_data(normalized, "920001", "baostock_qfq_rebuild")
    result = scan_abnormal_jumps(db, start_date="2026-11-05", end_date="2026-11-06", include_rows=True)
    assert result["abnormal_count"] == 0


def test_unlimited_stage_without_metadata_is_not_proven_invalid(caplog):
    from scripts.rebuild_stock_daily_qfq import normalize_and_validate_frame

    frame = pd.DataFrame(dict(date=["2026-11-05", "2026-11-06"], close=[10., 15.],
                              volume=[10000., 10000.], amount=[100000., 150000.]))
    result = normalize_and_validate_frame(frame, "600100")
    assert list(result["close"]) == [10., 15.]
    assert "特殊交易阶段未核实" in caplog.text


def test_sessions_skip_known_holidays_without_weekday_guess():
    from src.core.trading_calendar import get_trading_sessions

    sessions = get_trading_sessions("cn", date(2025, 1, 27), date(2025, 2, 7))
    assert sessions == [date(2025, 1, 27), date(2025, 2, 5), date(2025, 2, 6), date(2025, 2, 7)]


def test_historical_replay_does_not_replace_newer_measurement(workspace):
    db, service = workspace
    _save(db, _bars())
    t = _track(service)
    outcomes.evaluate_track_outcomes(as_of=BARS_END)
    result = outcomes.evaluate_track_outcomes(as_of=date(2026, 11, 9))
    assert result["newer_measurement"] == 1 and result["written"] == 0
    assert len(_events(service, t)) == 1
    assert service.repo.get_by_track_id(t.track_id).status == "closed"


def test_long_task_log_keeps_start_and_ignores_child_completions(tmp_path):
    log = tmp_path / "matrix.log"
    log.write_text(
        "[matrix_daily] start snapshot=2026-11-06 19:05:00\n" + "x" * 30000 + "\n"
        "[matrix_daily] done snapshot=2026-11-06 exit=0 2026-11-06 19:30:00\n"
        "[child] start 2026-11-06 19:31:00\n",
    )
    health = boards.read_task_health("matrix", "matrix", log, "2026-11-06", True)
    assert health.status == "ok"
    assert health.last_start_local == "2026-11-06 19:05:00"


@pytest.mark.parametrize("top_n", ["bad", None, -1, True])
def test_bad_optional_audit_is_limited_not_whole_board_failure(screening, top_n):
    _, service, _, audits = screening
    audits[0]["top_n"] = top_n
    assert importer.main() == 0
    assert service.repo.list_tracks() == []


def test_import_retry_recovers_created_but_not_tracking(screening, monkeypatch):
    _, service, _, _ = screening
    original = ResearchTrackingService.change_status

    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted after creation")

    monkeypatch.setattr(ResearchTrackingService, "change_status", interrupted)
    # 单票失败不再拖垮整批：计错误并返回非零，但已建好的 track 保持 pending
    assert importer.main() == 1
    assert service.repo.list_tracks()[0].status == "pending"
    monkeypatch.setattr(ResearchTrackingService, "change_status", original)
    assert importer.main() == 0
    assert len(service.repo.list_tracks()) == 1
    assert service.repo.list_tracks()[0].status == "tracking"


def test_import_retry_preserves_manual_pending_status(screening):
    _, service, _, _ = screening
    importer.main()
    t = service.repo.list_tracks()[0]
    service.change_status(t.track_id, "pending", reason="人工待核")
    importer.main()
    assert service.repo.list_tracks()[0].status == "pending"


@pytest.mark.parametrize("main_exit,post_exit", [(1, 0), (0, 1)])
def test_real_matrix_wrapper_keeps_exit_contract_and_final_board_order(tmp_path, main_exit, post_exit):
    if not shutil.which("bash") or not shutil.which("flock"):
        pytest.skip("runtime wrapper requires bash/flock")
    source = Path(__file__).resolve().parents[1] / "data/runtime/matrix_daily.sh"
    if not source.is_file():
        pytest.skip("local runtime wrapper is not installed in this checkout")
    script = tmp_path / "matrix.sh"
    script.write_text("\n".join(
        f"cd {shlex.quote(str(tmp_path))} || exit 1" if line.startswith("cd ") else line
        for line in source.read_text().splitlines()
    ) + "\n")
    tracked_script = Path(__file__).resolve().parents[1] / "scripts/run_workbench_postprocess.sh"
    (tmp_path / "scripts").mkdir()
    shutil.copy2(tracked_script, tmp_path / "scripts/run_workbench_postprocess.sh")
    (tmp_path / "data/run_logs").mkdir(parents=True)
    bin_dir = tmp_path / ".venv-linux/bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "python"
    stub.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$1" >> "$WORKBENCH_TEST_CALLS"\n'
        'case "$1" in\n'
        '  -c) echo open;;\n'
        f"  scripts/run_personal_strategy_matrix.py) exit {main_exit};;\n"
        f"  scripts/evaluate_track_outcomes.py) exit {post_exit};;\n"
        "esac\nexit 0\n"
    )
    stub.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/bash\necho 000\n")
    curl.chmod(0o755)
    calls = tmp_path / "calls.txt"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=10,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
             "WORKBENCH_TEST_CALLS": str(calls)},
    )
    assert result.returncode == 1
    log = tmp_path / "data/run_logs/matrix_daily.log"
    health = boards.read_task_health("matrix", "matrix", log, date.today().isoformat(), True)
    assert health.status == ("failed" if main_exit else "ok")
    assert f"workbench complete exit={post_exit}" in log.read_text()
    ordered = calls.read_text().splitlines()
    assert ordered.index("scripts/build_today_board.py") > ordered.index("scripts/evaluate_track_outcomes.py")


def test_service_normalizes_new_evidence_timestamps_without_rewriting_history(workspace):
    _, service = workspace
    local = datetime(2026, 11, 6, 18, 18, tzinfo=timezone(timedelta(hours=8)))
    t, _ = service.create_or_get(market="cn", code="600100", hypothesis_key="timestamp",
                                available_at=local, observed_at=local)
    event = service.repo.list_events(t.id)[0]
    assert event.available_at == event.observed_at == datetime(2026, 11, 6, 10, 18)
    service.append_evidence(t.track_id, source_type="test", source_ref="time", observed_at=local)
    assert service.repo.list_events(t.id)[1].observed_at == datetime(2026, 11, 6, 10, 18)
