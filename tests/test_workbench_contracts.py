"""工作台时点、样本身份、默认准入和实际页面消费的跨层反例。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from scripts import build_signal_regime as regime_cli
from src.config import Config
from src.services import replay_stats_service as stats, signal_regime, track_admission_service as admission
from src.services.daily_board_service import Candidate
from src.services.research_sample import FORWARD_ADMISSION_RULE
from src.services.research_tracking_service import ResearchTrackingService
from src.storage import DatabaseManager, KlineSignalSnapshot

SESSIONS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    db_path = tmp_path / "contracts.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    service = ResearchTrackingService()
    try:
        yield db_path, db, service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def bars(db, code="600100", days=SESSIONS):
    db.save_daily_data(pd.DataFrame([
        {"date": day, "open": 10, "high": 12, "low": 9, "close": 10 + i,
         "volume": 10000, "amount": 100000}
        for i, day in enumerate(days)
    ]), code, "baostock_qfq_rebuild")


def track(service, key="research:signal:hundred_day_high:2026-11-02", **kwargs):
    row, _ = service.create_or_get(
        market="cn", code="600100", hypothesis_key=key,
        observed_at=datetime(2026, 11, 2, 12), **kwargs,
    )
    return row


def measure(service, row, observed_at, ret1, ret5=None):
    service.append_evidence(
        row.track_id, source_type="stock_daily_qfq", source_ref=observed_at.isoformat(),
        observed_at=observed_at, quality="complete" if ret5 is not None else "partial",
        payload={"entry_date": "2026-11-02", "entry_close": 10, "ret1": ret1, "ret5": ret5},
    )


def test_latest_measurement_is_one_sample_and_historical_cutoff_keeps_old_version(workspace, tmp_path):
    path, db, service = workspace
    bars(db)
    row = track(service)
    measure(service, row, datetime(2026, 11, 3, 12), .1)
    measure(service, row, datetime(2026, 11, 9, 12), .2, .5)
    early = stats.build_stats(db_path=path, as_of="2026-11-03", output_path=tmp_path / "early.json")
    late = stats.build_stats(db_path=path, as_of="2026-11-09", output_path=tmp_path / "late.json")
    assert early["overall_replay"]["n"] == late["overall_replay"]["n"] == 1
    assert early["overall_replay"]["ret1"]["mean"] == 10
    assert late["overall_replay"]["ret1"]["mean"] == 20
    assert early["overall_replay"]["ret5_n"] == 0
    assert len([e for e in service.repo.list_events(row.id) if e.source_type == "stock_daily_qfq"]) == 2


@pytest.mark.parametrize("kind,key,initial", [
    ("bootstrap", "signal:hundred_day_high:2026-11-02", {}),
    ("bootstrap", "signal:hundred_day_high:2026-11-01", {"admission_rule": FORWARD_ADMISSION_RULE, "batch_ref": "b"}),
    ("forward", "signal:hundred_day_high:2026-11-02", {"admission_rule": FORWARD_ADMISSION_RULE, "batch_ref": "b"}),
    ("replay", "research:signal:hundred_day_high:2026-11-02", {}),
])
def test_sample_kind_uses_initial_evidence_not_measurement_label(workspace, kind, key, initial):
    path, _, service = workspace
    row = track(service, key, payload=initial)
    measure(service, row, datetime(2026, 11, 9, 12), .1)
    result = stats.collect_samples(path, as_of="2026-11-09")
    assert len(result) == 1 and result[0]["sample_kind"] == kind


def test_china_midnight_cutoff_excludes_later_collected_events(workspace):
    path, _, service = workspace
    row = track(service)
    measure(service, row, datetime(2026, 11, 2, 16), .1)  # 北京时间次日零点
    assert not stats.collect_samples(path, as_of="2026-11-02")


def test_mae_and_benchmark_keep_calendar_gap_instead_of_using_next_bar(workspace, tmp_path):
    path, db, service = workspace
    bars(db, days=[SESSIONS[0], *SESSIONS[2:], "2026-11-10"])
    row = track(service)
    measure(service, row, datetime(2026, 11, 10, 12), .1, .5)
    result = stats.build_stats(db_path=path, as_of="2026-11-10", output_path=tmp_path / "stats.json")
    summary = result["overall_replay"]
    assert summary["excess1"] is None
    assert "mae_pct" not in summary
    assert summary["sufficient"] is False


def test_partial_counts_do_not_claim_t5_sufficiency():
    rows = [{"signal_date": "2026-11-02", "ret1": .1} for _ in range(30)]
    result = stats._summarize(rows)
    assert result["n"] == 30 and result["ret5_n"] == 0 and not result["sufficient"]


def test_signal_regime_cannot_see_future_prices_or_signals(workspace, tmp_path):
    path, db, _ = workspace
    for i in range(5):
        code = f"60010{i}"
        bars(db, code)
        with db.get_session() as session:
            session.add(KlineSignalSnapshot(signal_type="hundred_day_high", signal_date=date(2026, 11, 2), code=code))
            session.commit()
    early = signal_regime.compute_signal_regime(as_of=date(2026, 11, 2), db_path=path)
    late = signal_regime.compute_signal_regime(as_of=date(2026, 11, 9), db_path=path)
    assert early["families"]["hundred_day_high"]["short"]["n"] == 0
    assert late["families"]["hundred_day_high"]["short"]["n"] == 5


def test_failed_regime_cli_replaces_previous_success_with_explicit_failure(workspace, tmp_path, monkeypatch):
    path, _, _ = workspace
    out = tmp_path / "regime.json"
    signal_regime._publish({"as_of": "2026-11-09", "families": {"hundred_day_high": {"verdict": "allow"}}}, out)
    monkeypatch.setattr("sys.argv", ["build_signal_regime.py", "--db", str(path), "--as-of", "2026-11-09", "--out", str(out)])
    assert regime_cli.main() == 1
    result = signal_regime.load_cached(out, as_of="2026-11-09")
    assert result["error"] == "no_signals" and result["families"] == {}
    assert not list(tmp_path.glob("*.tmp"))


def test_latest_signal_controls_cooldown_even_with_much_older_record(workspace):
    _, _, service = workspace
    track(service, "signal:hundred_day_high:2026-09-01")
    track(service, "signal:hundred_day_high:2026-11-06")
    cand = Candidate("hundred_day_high", "600100", "fixture", "fixture", batch_ready=True)
    kept, skipped = admission.gate_candidates(service, [cand], "2026-11-09", regime="risk_on",
                                               signal_verdicts={"hundred_day_high": "caution"})
    assert not kept and skipped["冷却期"] == 1


@pytest.mark.parametrize("problem", ["missing", "stale", "old_schema", "failed"])
def test_invalid_regime_cache_cannot_admit_new_record(workspace, problem):
    _, _, service = workspace
    data = {"as_of": "2026-11-09", "schema_version": signal_regime.CACHE_SCHEMA_VERSION,
            "families": {"hundred_day_high": {"verdict": "allow"}}}
    if problem == "stale":
        data["as_of"] = "2026-11-06"
    if problem == "old_schema":
        data["schema_version"] = 1
    if problem == "failed":
        data["error"] = "calendar_unavailable"
    if problem != "missing":
        signal_regime.DEFAULT_OUTPUT.write_text(json.dumps(data), encoding="utf-8")
    cand = Candidate("hundred_day_high", "600100", "fixture", "fixture", batch_ready=True)
    kept, skipped = admission.gate_candidates(service, [cand], "2026-11-09", regime="risk_on")
    assert not kept and skipped["信号体检未就绪"] == 1


@pytest.mark.parametrize("flag", ["行情异常", "行情截至2026-11-05", "行情状态未知", "行情重建失败"])
def test_shared_admission_rejects_bad_data_even_when_signal_is_positive(workspace, flag):
    _, _, service = workspace
    cand = Candidate("hundred_day_high", "600100", "fixture", "fixture", (flag,), batch_ready=True)
    kept, skipped = admission.gate_candidates(service, [cand], "2026-11-09", regime="risk_on",
                                               signal_verdicts={"hundred_day_high": "allow"})
    assert not kept and skipped[flag] == 1


def test_actual_page_javascript_cannot_override_server_admission():
    if not shutil.which("node"):
        pytest.skip("node is required for the preview's actual JavaScript consumer")
    path = Path(__file__).resolve().parents[1] / "scripts/build_workbench_preview.py"
    source = path.read_text()
    logic = source[source.index("  function filterReason("):source.index("  function rowHtml(")]
    cands = [{"code": "600100", "industry": "半导体", "group": "其他", "st": False,
              "admission": {"can_track": False, "reasons": ["行情异常"]},
              "signal_verdict": {"combined": "allow"}, "screen": {"bars": 60}}]
    script = f"var cands={json.dumps(cands)};{logic};process.stdout.write(JSON.stringify(compute({{}})));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=10)
    payload = json.loads(result.stdout)
    assert not payload["kept"] and payload["skip"][0][1] == "行情异常"
