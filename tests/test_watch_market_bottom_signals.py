"""watch_market_bottom_signals 的离线测试（构造 PE 序列 / 临时 sqlite，不触网）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.watch_market_bottom_signals import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    GREEN,
    RED,
    YELLOW,
    check_turnover,
    check_valuation,
    render_markdown,
    summarize_lights,
)


def _pe_df(pe_values: list[float]) -> pd.DataFrame:
    n = len(pe_values)
    return pd.DataFrame(
        {
            "date": [f"2020-{1 + i % 12:02d}-01" for i in range(n)],
            "pe": pe_values,
            "close": [100 + i for i in range(n)],
        }
    )


def test_valuation_green_when_cheap() -> None:
    df = _pe_df([20.0] * 10 + [10.5])
    s = check_valuation(df, DEFAULT_THRESHOLDS)
    assert s["light"] == GREEN
    assert s["status"] == "green"
    assert s["pe"] == 10.5
    assert s["percentile"] <= 10  # 便宜 → 低分位


def test_valuation_red_when_expensive() -> None:
    df = _pe_df([8.0] * 10 + [12.6])
    s = check_valuation(df, DEFAULT_THRESHOLDS)
    assert s["light"] == RED
    assert s["percentile"] >= 90


def test_valuation_yellow_band() -> None:
    df = _pe_df([8.0] * 10 + [11.5])
    s = check_valuation(df, DEFAULT_THRESHOLDS)
    assert s["light"] == YELLOW


def test_valuation_handles_missing_data() -> None:
    s = check_valuation(None, DEFAULT_THRESHOLDS)
    assert s["light"] == YELLOW
    assert s["status"] == "unknown"


def _make_db(tmp_path: Path, daily_amount_yi: float, days: int = 30) -> Path:
    db = tmp_path / "t.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stock_daily (code text, date text, amount real, pct_chg real)")
        for i in range(days):
            d = f"2026-10-{1 + i % 28:02d}" if i < 28 else f"2026-11-{i - 27:02d}"
            conn.execute(
                "insert into stock_daily values (?,?,?,?)",
                ("600000", d, daily_amount_yi * 1e8, 1.0),
            )
        conn.commit()
    return db


def test_turnover_green_when_dry(tmp_path: Path) -> None:
    db = _make_db(tmp_path, 11000)
    s = check_turnover(db, DEFAULT_THRESHOLDS)
    assert s["light"] == GREEN
    assert 10000 < s["amt20_yi"] < 12000


def test_turnover_red_when_heavy(tmp_path: Path) -> None:
    db = _make_db(tmp_path, 20000)
    s = check_turnover(db, DEFAULT_THRESHOLDS)
    assert s["light"] == RED
    assert s["amt20_yi"] > 15000


def test_turnover_missing_db(tmp_path: Path) -> None:
    s = check_turnover(tmp_path / "nope.db", DEFAULT_THRESHOLDS)
    assert s["light"] == YELLOW
    assert s["status"] == "unknown"


def test_summarize_lights_action_mapping() -> None:
    assert summarize_lights([{"light": GREEN}] * 3)["action"] == "可以加仓"
    assert summarize_lights([{"light": GREEN}, {"light": GREEN}, {"light": RED}])["action"] == "小额试探"
    assert summarize_lights([{"light": GREEN}, {"light": RED}, {"light": RED}])["action"] == "继续等待"
    assert summarize_lights([{"light": RED}] * 3)["action"] == "继续等待"
    # 绿数统计要正确
    assert summarize_lights([{"light": GREEN}, {"light": YELLOW}, {"light": GREEN}])["green_count"] == 2


def test_render_markdown_contains_sections(tmp_path: Path) -> None:
    db = _make_db(tmp_path, 20000)
    signals = [
        check_valuation(_pe_df([20.0] * 10 + [12.6]), DEFAULT_THRESHOLDS),
        check_turnover(db, DEFAULT_THRESHOLDS),
    ]
    verdict = summarize_lights(signals)
    md = render_markdown("2026-11-14", signals, verdict)
    assert "市场底部信号 · 2026-11-14" in md
    assert "三灯明细" in md
    assert verdict["action"] in md
    # 阈值必须显式出现在报告里，避免口径漂移
    assert "≤11.0" in md or "11.0" in md
