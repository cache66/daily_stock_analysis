"""daily_action_plan / strategy_scoreboard 的离线测试（不触网、临时 sqlite）。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.daily_action_plan import (  # noqa: E402
    build_markdown,
    dedupe_pool,
    discipline_label,
    load_recent_signal_pool,
    load_tuning,
    read_bias,
    read_index_regime,
    split_defensive_rows,
    update_profile_regime,
)
from scripts.strategy_scoreboard import fmt_stat  # noqa: E402


def _make_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "create table stock_daily ("
        "id integer primary key, code text, date text, close real, "
        "ma5 real, ma10 real, ma20 real)"
    )
    con.executemany(
        "insert into stock_daily (code, date, close, ma5, ma10, ma20) values (?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def _index_rows(closes: list[float]) -> list[tuple]:
    rows = []
    for i, c in enumerate(closes):
        month = i // 28 + 1
        day = i % 28 + 1
        rows.append(("000300", f"2026-{month:02d}-{day:02d}", c, None, None, None))
    return rows


def test_discipline_thresholds() -> None:
    label, _ = discipline_label(0.5, 1)
    assert label.startswith("✅")
    label, _ = discipline_label(3.0, 1)
    assert label.startswith("⚠️")
    label, _ = discipline_label(6.0, 1)
    assert label.startswith("⛔")
    # 双信号放宽到 7%
    label, _ = discipline_label(6.0, 2)
    assert label.startswith("⚠️")
    label, _ = discipline_label(8.0, 2)
    assert label.startswith("⛔")


def test_regime_offensive_defensive_balanced(tmp_path: Path) -> None:
    # offensive: 收盘 > MA20 > MA60（持续上行）
    db = tmp_path / "up.db"
    _make_db(db, _index_rows([100 + i * 0.5 for i in range(90)]))
    info = read_index_regime(db)
    assert info["ok"] and info["regime"] == "offensive"

    # defensive: 收盘 < MA20 < MA60（持续下行）
    db = tmp_path / "down.db"
    _make_db(db, _index_rows([200 - i * 0.5 for i in range(90)]))
    info = read_index_regime(db)
    assert info["ok"] and info["regime"] == "defensive"

    # balanced: 横盘
    db = tmp_path / "flat.db"
    _make_db(db, _index_rows([150.0] * 90))
    info = read_index_regime(db)
    assert info["ok"] and info["regime"] == "balanced"

    # 数据不足
    db = tmp_path / "short.db"
    _make_db(db, _index_rows([100.0] * 10))
    info = read_index_regime(db)
    assert not info["ok"]


def test_read_bias_direct_and_fallback(tmp_path: Path) -> None:
    db = tmp_path / "bias.db"
    _make_db(
        db,
        [
            ("603657", "2026-10-01", 39.5, 37.19, 36.45, 32.14),
            ("603657", "2026-09-30", 39.0, 36.0, 35.0, 31.0),
        ],
    )
    info = read_bias(db, "603657")
    assert info["ok"]
    assert abs(info["bias5"] - (39.5 - 37.19) / 37.19 * 100) < 1e-6

    # ma5 缺失 → 用最近 5 根收盘现算
    db2 = tmp_path / "bias2.db"
    rows = [("301560", f"2026-10-0{i}", 100.0 + i, None, None, None) for i in (1, 2, 3, 4, 5)]
    _make_db(db2, rows)
    info = read_bias(db2, "301560")
    assert info["ok"]
    closes = [101.0, 102.0, 103.0, 104.0, 105.0]
    ma5 = sum(closes) / 5
    assert abs(info["bias5"] - (105.0 - ma5) / ma5 * 100) < 1e-6


def test_update_profile_regime(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"market_regime": "defensive"}), encoding="utf-8")
    assert update_profile_regime(profile, "defensive") is None  # 未变化
    assert update_profile_regime(profile, "balanced") == "defensive"
    assert json.loads(profile.read_text(encoding="utf-8"))["market_regime"] == "balanced"


def test_fmt_stat() -> None:
    assert fmt_stat([]) == "—"
    assert fmt_stat([1.0, -0.5]) == "+0.25% / 胜率 50%（n=2）"


def test_discipline_custom_thresholds() -> None:
    label, _ = discipline_label(6.0, 1, warn=5.0, strong=8.0)
    assert label.startswith("⛔")
    label, _ = discipline_label(6.0, 2, warn=5.0, strong=8.0)
    assert label.startswith("⚠️")
    label, _ = discipline_label(1.5, 1, best=2.0)
    assert label.startswith("✅")


def test_load_tuning_merge(tmp_path: Path) -> None:
    cfg = tmp_path / "tuning.json"
    cfg.write_text('{"bias_warn": 6.0, "ai_review": {"top": 50}}', encoding="utf-8")
    tuning = load_tuning(cfg)
    assert tuning["bias_warn"] == 6.0
    assert tuning["ai_review"]["top"] == 50
    assert tuning["bias_best"] == 2.0          # 未覆盖项保留默认
    assert tuning["ai_review"]["workers"] == 4
    assert load_tuning(tmp_path / "missing.json")["ai_review"]["top"] == 80


def test_dedupe_pool_merges_sources() -> None:
    pool_a = [
        {"code": "000001", "name": "A", "sources": ["快复盘"], "signals": ["hundred_day_high"], "priority": 70.0, "order": 0},
        {"code": "000002", "name": "B", "sources": ["快复盘"], "signals": [], "priority": 50.0, "order": 1},
    ]
    pool_b = [
        {"code": "000001", "name": "A", "sources": ["近期信号"], "signals": ["trend_leader_unified"], "priority": 0.0, "order": 0},
        {"code": "000003", "name": "C", "sources": ["近期信号"], "signals": ["earnings"], "priority": 0.0, "order": 1},
    ]
    merged = dedupe_pool(pool_a, pool_b)
    assert [m["code"] for m in merged] == ["000001", "000002", "000003"]
    first = merged[0]
    assert first["sources"] == ["快复盘", "近期信号"]
    assert "trend_leader_unified" in first["signals"]
    assert first["priority"] == 70.0


def test_load_recent_signal_pool(tmp_path: Path) -> None:
    csv_path = tmp_path / "recent.csv"
    csv_path.write_text(
        "signal_date,signal_type,code,name\n"
        "2026-10-02,hundred_day_high,000001,A\n"
        "2026-10-01,hundred_day_high,000001,A\n"
        "2026-10-01,trend_leader_unified,000002,B\n"
        "2026-09-20,hundred_day_high,000009,OLD\n",
        encoding="utf-8",
    )
    pool = load_recent_signal_pool(csv_path, days=2)
    codes = [p["code"] for p in pool]
    assert codes == ["000001", "000002"]           # 只取最近 2 个交易日；按最新日期+次数排序
    assert "000009" not in codes                    # 超出窗口的剔除
    first = pool[0]
    assert first["signals"] == ["hundred_day_high"]
    assert first["sources"] == ["近期信号"]


def test_recent_signal_pool_excludes_fallback(tmp_path: Path) -> None:
    csv_path = tmp_path / "recent_fb.csv"
    csv_path.write_text(
        "signal_date,signal_type,code,name,selection_mode\n"
        "2026-10-02,trend_leader_unified,000010,兜底票,fallback\n"
        "2026-10-02,hundred_day_high,000011,正常票,strict\n"
        "2026-10-01,hundred_day_high,000012,旧列票,\n",
        encoding="utf-8",
    )
    pool = load_recent_signal_pool(csv_path, days=30)
    codes = [p["code"] for p in pool]
    assert "000010" not in codes                # 兜底样本剔除
    assert {"000011", "000012"} <= set(codes)   # strict / 旧数据正常保留


def test_build_markdown_tolerates_failed_review_items() -> None:
    regime_info = {"ok": False, "reason": "test"}
    ai_results = [
        {"code": "000001", "name": "失败票", "ok": False, "reason": "返回为空", "sources": []},
        {"code": "000002", "name": "正常票", "ok": True, "content": "①关注价值：测试内容", "sources": []},
    ]
    md = build_markdown(
        "2026-10-02",
        regime_info,
        "test",
        [],
        [],
        ai_results,
        None,
        ai_meta={"pool_size": 2, "raw_count": 2, "skill": "bull_trend", "workers": 4},
        ai_inline=3,
    )
    assert "#### 正常票（000002）" in md            # 内联只收成功项
    assert "#### 失败票（000001）" not in md
    assert "⚠️ 返回为空" in md                      # 摘要表保留失败标记


def test_split_defensive_rows() -> None:
    rows = [
        {"code": "1", "name": "A", "signals": "hundred_day_high"},
        {"code": "2", "name": "B", "signals": "hundred_day_high;trend_leader_unified"},
        {"code": "3", "name": "C", "signals": "trend_leader_unified"},
        {"code": "4", "name": "D", "signals": ""},
    ]
    main, watch = split_defensive_rows(rows, "defensive", ["hundred_day_high"])
    assert [r["code"] for r in watch] == ["1"]          # 仅 hdh 单信号 → 观察区
    assert [r["code"] for r in main] == ["2", "3", "4"]  # 双确认/其它信号保留
    main2, watch2 = split_defensive_rows(rows, "balanced", ["hundred_day_high"])
    assert watch2 == [] and len(main2) == 4              # 非防守市不拆
    main3, watch3 = split_defensive_rows(rows, "defensive", [])
    assert watch3 == [] and len(main3) == 4              # 未配置降权信号不拆
