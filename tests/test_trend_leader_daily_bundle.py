# -*- coding: utf-8 -*-
"""Tests for one-command trend leader daily bundle runner."""

from datetime import date
from pathlib import Path
import tempfile

import scripts.run_trend_leader_daily_bundle as daily_bundle
from scripts.run_trend_leader_daily_bundle import _build_bundle_markdown, _count_candidates, _tail_output


def test_count_candidates_reads_utf8_sig_csv() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path = Path(temp_dir) / "candidates.csv"
        csv_path.write_text(
            "code,name,overall_score\n600001,A,88.0\n600002,B,77.0\n",
            encoding="utf-8-sig",
        )
        assert _count_candidates(csv_path) == 2


def test_build_bundle_markdown_contains_window_table() -> None:
    content = _build_bundle_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_type="trend_leader_unified",
        selected_count=2,
        scan_csv=Path("scan.csv"),
        scan_txt=Path("scan.txt"),
        eval_json=Path("report.json"),
        eval_md=Path("report.md"),
        eval_report={
            "trade_cost_model": {
                "slippage_bps": 0.0,
                "fee_bps": 0.0,
                "turnover_penalty_bps": 0.0,
                "total_trade_cost_bps": 0.0,
            },
            "window_summaries": [
                {
                    "eval_window_days": 1,
                    "completed_count": 2,
                    "win_rate_after_cost_pct": 50.0,
                    "avg_stock_return_after_cost_pct": 1.2,
                    "max_drawdown_after_cost_pct": 3.4,
                }
            ],
        },
        select_stdout="select-ok",
        eval_stdout="eval-ok",
    )

    assert "趋势龙头一键日报（2026-04-20）" in content
    assert "| window | completed | win_rate_after_cost_pct |" in content
    assert "select-ok" in content
    assert "eval-ok" in content


def test_tail_output_keeps_last_lines_with_omission_header() -> None:
    text = "\n".join([f"line-{idx}" for idx in range(1, 8)])
    tailed = _tail_output(text, max_lines=3)
    assert "省略前 4 行" in tailed
    assert "line-5" in tailed
    assert "line-7" in tailed
    assert "line-1" not in tailed


def test_main_forwards_post_select_enrichment_flags(monkeypatch, tmp_path: Path) -> None:
    commands = []
    whitelist_file = tmp_path / "universe_codes.txt"
    whitelist_file.write_text("600001\n688001\n300001\n", encoding="utf-8")

    def _mock_run_command(command):
        commands.append(list(command))
        return "ok"

    monkeypatch.setattr(daily_bundle, "_run_command", _mock_run_command)
    monkeypatch.setattr(daily_bundle, "_count_candidates", lambda _path: 0)
    monkeypatch.setattr(daily_bundle, "_load_json", lambda _path: {})
    monkeypatch.setattr(
        daily_bundle.sys,
        "argv",
        [
            "run_trend_leader_daily_bundle.py",
            "--snapshot-date",
            "2026-04-20",
            "--output-dir",
            str(tmp_path),
            "--max-workers",
            "4",
            "--shard-count",
            "3",
            "--shard-index",
            "1",
            "--enrich-top-n",
            "5",
            "--progress-every",
            "7",
            "--disable-second-stage-news-search",
            "--disable-second-stage-business-profile",
            "--exclude-st",
            "--exclude-kcb",
            "--exclude-cyb",
            "--universe-codes-file",
            str(whitelist_file),
        ],
    )

    rc = daily_bundle.main()

    assert rc == 0
    assert len(commands) == 2
    select_cmd = commands[0]
    assert "--enrich-top-n" in select_cmd
    assert select_cmd[select_cmd.index("--enrich-top-n") + 1] == "5"
    assert "--progress-every" in select_cmd
    assert select_cmd[select_cmd.index("--progress-every") + 1] == "7"
    assert "--max-workers" in select_cmd
    assert select_cmd[select_cmd.index("--max-workers") + 1] == "4"
    assert "--shard-count" in select_cmd
    assert select_cmd[select_cmd.index("--shard-count") + 1] == "3"
    assert "--shard-index" in select_cmd
    assert select_cmd[select_cmd.index("--shard-index") + 1] == "1"
    assert "--disable-second-stage-news-search" in select_cmd
    assert "--disable-second-stage-business-profile" in select_cmd
    assert "--exclude-st" in select_cmd
    assert "--exclude-kcb" in select_cmd
    assert "--exclude-cyb" in select_cmd
    assert "--universe-codes-file" in select_cmd
    assert select_cmd[select_cmd.index("--universe-codes-file") + 1] == str(whitelist_file)
