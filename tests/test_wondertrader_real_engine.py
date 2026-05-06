# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import sys
import types
from pathlib import Path

import pytest

import src.shortline_hub.wondertrader_real_engine as real_engine
from src.shortline_hub.wondertrader_real_engine import (
    compute_shortline_signal_from_history_rows,
    repair_history_rows_from_amount,
    infer_a_share_exchange,
    select_prefilter_snapshots,
    to_wt_std_code,
)


def test_infer_a_share_exchange_and_wt_code() -> None:
    assert infer_a_share_exchange("600519") == "SSE"
    assert infer_a_share_exchange("688981") == "SSE"
    assert infer_a_share_exchange("000001") == "SZSE"
    assert infer_a_share_exchange("300750") == "SZSE"
    assert to_wt_std_code("600519") == "SSE.STK.600519"
    assert to_wt_std_code("000001") == "SZSE.STK.000001"


def test_select_prefilter_snapshots_prefers_liquid_momentum_rows(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache" / "reference"
    cache_dir.mkdir(parents=True)
    csv_path = cache_dir / "kline_selector_spot_universe.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "code",
                "name",
                "latest_price",
                "pct_change",
                "turnover_rate",
                "volume_ratio",
                "change_pct_60d",
                "amount",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "code": "000001",
                "name": "平安银行",
                "latest_price": "11.46",
                "pct_change": "0.70",
                "turnover_rate": "0.59",
                "volume_ratio": "0.80",
                "change_pct_60d": "2.0",
                "amount": "1674259",
            }
        )
        writer.writerow(
            {
                "code": "300750",
                "name": "宁德时代",
                "latest_price": "219.50",
                "pct_change": "5.12",
                "turnover_rate": "4.02",
                "volume_ratio": "2.15",
                "change_pct_60d": "26.0",
                "amount": "8800000000",
            }
        )
        writer.writerow(
            {
                "code": "600111",
                "name": "北方稀土",
                "latest_price": "19.82",
                "pct_change": "7.43",
                "turnover_rate": "6.50",
                "volume_ratio": "2.60",
                "change_pct_60d": "18.0",
                "amount": "5300000000",
            }
        )

    selected = select_prefilter_snapshots(tmp_path, prefilter_limit=2)

    assert [item["code"] for item in selected] == ["600111", "300750"]


def test_compute_shortline_signal_from_history_rows_returns_signal_for_breakout() -> None:
    rows: list[dict[str, float | str]] = []
    close = 10.0
    for day in range(1, 31):
        close += 0.08
        rows.append(
            {
                "date": f"2026-04-{day:02d}",
                "open": round(close - 0.1, 2),
                "high": round(close + 0.05, 2),
                "low": round(close - 0.2, 2),
                "close": round(close, 2),
                "volume": 1000.0,
            }
        )

    rows[-1]["close"] = 13.2
    rows[-1]["high"] = 13.35
    rows[-1]["low"] = 12.8
    rows[-1]["volume"] = 3200.0

    signal = compute_shortline_signal_from_history_rows(rows)

    assert signal is not None
    assert signal["trigger_type"] == "momentum_breakout"
    assert signal["asof_date"] == "2026-04-30"
    assert signal["trigger_score"] > 0
    assert signal["volume_ratio"] > 1.0


def test_compute_shortline_signal_from_history_rows_returns_dual_thrust_breakout() -> None:
    rows: list[dict[str, float | str]] = []
    base_close = 10.0
    for day in range(1, 31):
        close = base_close + day * 0.03
        rows.append(
            {
                "date": f"2026-04-{day:02d}",
                "open": round(close - 0.05, 2),
                "high": round(close + 0.08, 2),
                "low": round(close - 0.10, 2),
                "close": round(close, 2),
                "volume": 1000.0,
            }
        )

    rows[-1]["open"] = 10.60
    rows[-1]["high"] = 11.05
    rows[-1]["low"] = 10.55
    rows[-1]["close"] = 10.95
    rows[-1]["volume"] = 2600.0
    rows[-2]["open"] = 10.45
    rows[-2]["high"] = 10.55
    rows[-2]["low"] = 10.40
    rows[-2]["close"] = 10.50
    rows[-5]["high"] = 11.20

    signal = compute_shortline_signal_from_history_rows(rows)

    assert signal is not None
    assert signal["trigger_type"] == "dual_thrust_breakout"
    assert "dual thrust" in signal["trigger_reason"]


def test_compute_shortline_signal_from_history_rows_returns_none_when_not_enough_history() -> None:
    rows = [
        {
            "date": "2026-04-01",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 1000.0,
        }
        for _ in range(10)
    ]

    assert compute_shortline_signal_from_history_rows(rows) is None


def test_compute_shortline_signal_from_history_rows_returns_none_without_trigger() -> None:
    rows: list[dict[str, float | str]] = []
    for day in range(1, 31):
        rows.append(
            {
                "date": f"2026-04-{day:02d}",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 1000.0,
            }
        )

    assert compute_shortline_signal_from_history_rows(rows) is None


def test_export_candidates_via_real_wondertrader_uses_cta_engine_and_price_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)

    engine_records: dict[str, list | int] = {
        "engine_types": [],
        "set_cta_strategy_calls": 0,
    }

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

        def name(self) -> str:
            return self._name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    fake_signal_rows = [
        {
            "tag": "SZSE.STK.000001",
            "data": {
                "asof_date": "2026-05-02",
                "close": 12.34,
                "pct_change": 6.5,
                "volume_ratio": 1.8,
                "trigger_type": "momentum_breakout",
                "trigger_reason": "sel breakout",
                "trigger_score": 88.0,
            },
        },
        {
            "tag": "SSE.STK.600111",
            "data": {
                "asof_date": "2026-05-02",
                "close": 23.45,
                "pct_change": 9.9,
                "volume_ratio": 2.5,
                "trigger_type": "limit_up_momentum",
                "trigger_reason": "sel limit up",
                "trigger_score": 96.0,
            },
        },
    ]

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            engine_records["engine_types"].append(engine_type)
            self.writer = None
            self.strategy = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            engine_records["set_cta_strategy_calls"] += 1
            self.strategy = strategy

        def run_backtest(self, bNeedDump=False) -> None:
            current_code = "000001" if "000001" in getattr(self.strategy, "bar_code", "") else "600111"
            row = next(item for item in fake_signal_rows if item["tag"].endswith(current_code))
            self.writer.write_indicator("fake_cta", row["tag"], 0, row["data"])

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "000001",
                "name": "pingan",
                "latest_price": 0.0,
                "pct_change": 6.5,
                "turnover_rate": 3.2,
                "volume_ratio": 1.8,
                "industry": "bank",
            },
            {
                "code": "600111",
                "name": "north_rare",
                "latest_price": 21.0,
                "pct_change": 9.9,
                "turnover_rate": 11.2,
                "volume_ratio": 2.5,
                "industry": "rare_earth",
            },
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {
            "000001": {"name": "pingan", "industry": "bank"},
            "600111": {"name": "north_rare", "industry": "rare_earth"},
        },
    )
    monkeypatch.setattr(
        real_engine,
        "load_history_rows",
        lambda repo_root, code: [
            {
                "date": f"2026-04-{day:02d}",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
                "amount": 1000000.0,
            }
            for day in range(1, 31)
        ],
    )

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-05-02",
        top_n=2,
    )

    assert engine_records["engine_types"] == [FakeEngineType.ET_CTA, FakeEngineType.ET_CTA]
    assert engine_records["set_cta_strategy_calls"] == 2
    assert len(rows) == 2
    assert rows[0]["scan_source"] == real_engine.DEFAULT_REAL_SCAN_SOURCE
    assert rows[0]["price"] == 21.0
    assert rows[1]["symbol"] == "000001"
    assert rows[1]["price"] == 12.34


def test_export_candidates_via_real_wondertrader_marks_missing_volume_and_backfills_history_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

        def name(self) -> str:
            return self._name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None
            self.strategy = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            self.strategy = strategy

        def run_backtest(self, bNeedDump=False) -> None:
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": "2026-05-02",
                    "close": 24.47,
                    "pct_change": 20.0,
                    "volume_ratio": 0.0,
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "limit up",
                    "trigger_score": 99.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": "",
                "pct_change": "",
                "turnover_rate": "",
                "volume_ratio": "",
                "change_pct_60d": "",
                "amount": "",
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )

    history_rows = []
    for day in range(1, 62):
        close = 10.0 + day * 0.1
        history_rows.append(
            {
                "date": f"2026-03-{day:02d}" if day <= 31 else f"2026-04-{day - 31:02d}",
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 0.0,
                "amount": 1000000.0 + day * 1000.0,
            }
        )
    history_rows[-1]["date"] = "2026-05-02"
    history_rows[-1]["close"] = 24.47
    history_rows[-1]["amount"] = 368278000.0

    monkeypatch.setattr(real_engine, "load_history_rows", lambda repo_root, code: history_rows)

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-05-02",
        top_n=1,
    )

    assert len(rows) == 1
    assert rows[0]["price"] == 24.47
    assert rows[0]["amount"] == 368278000.0
    assert rows[0]["change_pct_60d"] == pytest.approx(((24.47 / 10.1) - 1.0) * 100.0, rel=1e-6)
    assert "missing_volume_history" not in rows[0]["risk_flags"]
    assert "volume_reconstructed_from_amount" in rows[0]["risk_flags"]


def test_export_candidates_via_real_wondertrader_skips_history_asof_flag_on_weekend_trade_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            return None

        def run_backtest(self, bNeedDump=False) -> None:
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": "2026-04-30",
                    "close": 24.47,
                    "pct_change": 20.0,
                    "volume_ratio": 1.5,
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "limit up",
                    "trigger_score": 99.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": 24.47,
                "pct_change": 20.0,
                "turnover_rate": 16.0,
                "volume_ratio": 1.5,
                "change_pct_60d": 30.0,
                "amount": 368278000.0,
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )
    monkeypatch.setattr(
        real_engine,
        "load_history_rows",
        lambda repo_root, code: [
            {
                "date": "2026-04-30",
                "open": 24.0,
                "high": 24.6,
                "low": 23.8,
                "close": 24.47,
                "volume": 1000.0,
                "amount": 368278000.0,
            }
        ]
        * 30,
    )

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-05-03",
        top_n=1,
    )

    assert len(rows) == 1
    assert "history_asof_2026-04-30" not in rows[0]["risk_flags"]


def test_export_candidates_via_real_wondertrader_skips_history_asof_flag_on_cached_holiday_trade_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)
    trade_cal_path = repo_root / "data" / "cache" / "reference" / "tushare_trade_cal_sse.csv"
    trade_cal_path.parent.mkdir(parents=True, exist_ok=True)
    trade_cal_path.write_text(
        "exchange,cal_date,is_open\nSSE,20260504,0\n",
        encoding="utf-8",
    )

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            return None

        def run_backtest(self, bNeedDump=False) -> None:
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": "2026-04-30",
                    "close": 24.47,
                    "pct_change": 20.0,
                    "volume_ratio": 1.5,
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "limit up",
                    "trigger_score": 99.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": 24.47,
                "pct_change": 20.0,
                "turnover_rate": 16.0,
                "volume_ratio": 1.5,
                "change_pct_60d": 30.0,
                "amount": 368278000.0,
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )
    monkeypatch.setattr(
        real_engine,
        "load_history_rows",
        lambda repo_root, code: [
            {
                "date": "2026-04-30",
                "open": 24.0,
                "high": 24.6,
                "low": 23.8,
                "close": 24.47,
                "volume": 1000.0,
                "amount": 368278000.0,
            }
        ]
        * 30,
    )

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-05-04",
        top_n=1,
    )

    assert len(rows) == 1
    assert "history_asof_2026-04-30" not in rows[0]["risk_flags"]


def test_export_candidates_via_real_wondertrader_skips_history_asof_flag_when_local_calendar_is_misleading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)
    trade_cal_path = repo_root / "data" / "cache" / "reference" / "tushare_trade_cal_sse.csv"
    trade_cal_path.parent.mkdir(parents=True, exist_ok=True)
    trade_cal_path.write_text(
        "exchange,cal_date,is_open\nSSE,20260430,1\nSSE,20260501,1\nSSE,20260502,0\n",
        encoding="utf-8",
    )

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            return None

        def run_backtest(self, bNeedDump=False) -> None:
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": "2026-04-30",
                    "close": 24.47,
                    "pct_change": 20.0,
                    "volume_ratio": 1.5,
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "limit up",
                    "trigger_score": 99.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    class FakeAkshareModule:
        @staticmethod
        def tool_trade_date_hist_sina():
            import pandas as pd

            return pd.DataFrame(
                {
                    "trade_date": [
                        "2026-04-29",
                        "2026-04-30",
                        "2026-05-06",
                    ]
                }
            )

    monkeypatch.setitem(sys.modules, "akshare", FakeAkshareModule())
    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": 24.47,
                "pct_change": 20.0,
                "turnover_rate": 16.0,
                "volume_ratio": 1.5,
                "change_pct_60d": 30.0,
                "amount": 368278000.0,
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )
    monkeypatch.setattr(
        real_engine,
        "load_history_rows",
        lambda repo_root, code: [
            {
                "date": "2026-04-30",
                "open": 24.0,
                "high": 24.6,
                "low": 23.8,
                "close": 24.47,
                "volume": 1000.0,
                "amount": 368278000.0,
            }
        ]
        * 30,
    )

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-05-04",
        top_n=1,
    )

    assert len(rows) == 1
    assert "history_asof_2026-04-30" not in rows[0]["risk_flags"]


def test_export_candidates_via_real_wondertrader_truncates_history_to_trade_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None
            self.strategy = None
            self.loader = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            self.loader = loader

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            self.strategy = strategy

        def run_backtest(self, bNeedDump=False) -> None:
            captured: dict[str, list[FakeBarStruct]] = {"bars": []}

            def _feeder(buffer, count: int) -> None:
                captured["bars"] = list(buffer[:count])

            assert self.loader is not None
            assert self.strategy is not None
            loaded = self.loader.load_final_his_bars(self.strategy.bar_code, "d1", _feeder)
            assert loaded is True
            assert captured["bars"]
            last_bar = captured["bars"][-1]
            asof_date = str(int(last_bar.date))
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": f"{asof_date[:4]}-{asof_date[4:6]}-{asof_date[6:8]}",
                    "close": float(last_bar.close),
                    "pct_change": 5.6,
                    "volume_ratio": 1.9,
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "replay respects trade_date cutoff",
                    "trigger_score": 92.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": "",
                "pct_change": "",
                "turnover_rate": "8.6",
                "volume_ratio": "",
                "change_pct_60d": "",
                "amount": "",
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )

    history_rows: list[dict[str, float | str]] = []
    for day in range(1, 31):
        close = 10.0 + day * 0.1
        history_rows.append(
            {
                "date": f"2026-04-{day:02d}",
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0 + day,
                "amount": 400000000.0 + day * 1000000.0,
            }
        )
    history_rows[28]["close"] = 12.9
    history_rows[28]["amount"] = 429000000.0
    history_rows[29]["close"] = 14.8
    history_rows[29]["amount"] = 530000000.0

    monkeypatch.setattr(real_engine, "load_history_rows", lambda repo_root, code: history_rows)

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-04-29",
        top_n=1,
    )

    assert len(rows) == 1
    assert rows[0]["price"] == 12.9
    assert rows[0]["amount"] == 429000000.0
    assert "history_asof_2026-04-29" not in rows[0]["risk_flags"]
    assert "history_asof_2026-04-30" not in rows[0]["risk_flags"]


def test_export_candidates_via_real_wondertrader_downgrades_recent_reconstructed_volume_when_amount_ratio_matches_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "daily_stock_analysis"
    wtpy_root = tmp_path / "WonderTrader" / "wtpy" / "demos" / "cta_stk_bt"
    wtpy_root.mkdir(parents=True)

    class FakeEngineType:
        ET_CTA = "ET_CTA"

    class FakeBaseCtaStrategy:
        def __init__(self, name: str) -> None:
            self._name = name

    class FakeCtaContext:
        pass

    class FakeBaseIndexWriter:
        def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
            return None

    class FakeBaseExtDataLoader:
        pass

    class FakeBarStruct:
        def __init__(self) -> None:
            self.date = 0
            self.time = 0
            self.open = 0.0
            self.high = 0.0
            self.low = 0.0
            self.close = 0.0
            self.vol = 0.0
            self.money = 0.0
            self.hold = 0

    class FakeBarStructFactory:
        def __mul__(self, count: int):
            def _factory():
                return [FakeBarStruct() for _ in range(count)]

            return _factory

    class FakeWtBtEngine:
        def __init__(self, engine_type) -> None:
            self.writer = None

        def set_writer(self, writer) -> None:
            self.writer = writer

        def set_extended_data_loader(self, loader=None, bAutoTrans=False) -> None:
            return None

        def init(self, *args, **kwargs) -> None:
            return None

        def configBacktest(self, *args, **kwargs) -> None:
            return None

        def configBTStorage(self, *args, **kwargs) -> None:
            return None

        def commitBTConfig(self) -> None:
            return None

        def set_cta_strategy(self, strategy) -> None:
            return None

        def run_backtest(self, bNeedDump=False) -> None:
            self.writer.write_indicator(
                "fake_cta",
                "SZSE.STK.300632",
                0,
                {
                    "asof_date": "2026-04-30",
                    "close": 24.47,
                    "pct_change": 20.01,
                    "volume_ratio": 1.86,
                    "trigger_type": "limit_up_momentum",
                    "trigger_reason": "limit up",
                    "trigger_score": 99.0,
                },
            )

        def release_backtest(self) -> None:
            return None

    fake_wtpy = types.ModuleType("wtpy")
    fake_wtpy.BaseCtaStrategy = FakeBaseCtaStrategy
    fake_wtpy.CtaContext = FakeCtaContext
    fake_wtpy.EngineType = FakeEngineType
    fake_wtpy.WtBtEngine = FakeWtBtEngine
    monkeypatch.setitem(sys.modules, "wtpy", fake_wtpy)

    fake_ext_module_defs = types.ModuleType("wtpy.ExtModuleDefs")
    fake_ext_module_defs.BaseExtDataLoader = FakeBaseExtDataLoader
    monkeypatch.setitem(sys.modules, "wtpy.ExtModuleDefs", fake_ext_module_defs)

    fake_ext_tool_defs = types.ModuleType("wtpy.ExtToolDefs")
    fake_ext_tool_defs.BaseIndexWriter = FakeBaseIndexWriter
    monkeypatch.setitem(sys.modules, "wtpy.ExtToolDefs", fake_ext_tool_defs)

    fake_core_defs = types.ModuleType("wtpy.WtCoreDefs")
    fake_core_defs.WTSBarStruct = FakeBarStructFactory()
    monkeypatch.setitem(sys.modules, "wtpy.WtCoreDefs", fake_core_defs)

    monkeypatch.setattr(real_engine, "_import_wtpy", lambda wtpy_root: None)
    monkeypatch.setattr(real_engine.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        real_engine,
        "select_prefilter_snapshots",
        lambda repo_root, prefilter_limit=real_engine.DEFAULT_PREFILTER_LIMIT: [
            {
                "code": "300632",
                "name": "test_stock",
                "latest_price": 24.47,
                "pct_change": 20.01,
                "turnover_rate": 16.65,
                "volume_ratio": 1.86,
                "change_pct_60d": 63.79,
                "amount": 368278000.0,
                "industry": "optics",
            }
        ],
    )
    monkeypatch.setattr(
        real_engine,
        "load_stock_profile_map",
        lambda repo_root: {"300632": {"name": "test_stock", "industry": "optics"}},
    )

    history_rows = []
    for day in range(1, 63):
        close = 10.0 + day * 0.1
        history_rows.append(
            {
                "date": f"2026-03-{day:02d}" if day <= 31 else f"2026-04-{day - 31:02d}",
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 1000000.0 + day * 1000.0,
                "amount": (1000000.0 + day * 1000.0) * close,
            }
        )
    for row in history_rows[-9:]:
        row["volume"] = 0.0
        row["amount"] = row["amount"] / 1000.0
    history_rows[-1]["date"] = "2026-04-30"
    history_rows[-1]["close"] = 24.47
    history_rows[-5]["amount"] = 246686.0
    history_rows[-4]["amount"] = 287670.0
    history_rows[-3]["amount"] = 196069.0
    history_rows[-2]["amount"] = 88508.0
    history_rows[-1]["amount"] = 368278.0

    monkeypatch.setattr(real_engine, "load_history_rows", lambda repo_root, code: history_rows)

    rows = real_engine.export_candidates_via_real_wondertrader(
        repo_root=repo_root,
        trade_date="2026-04-30",
        top_n=1,
    )

    assert len(rows) == 1
    assert "volume_reconstructed_from_amount" not in rows[0]["risk_flags"]
    assert "volume_ratio_validated_by_amount_history" in rows[0]["risk_flags"]


def test_repair_history_rows_from_amount_restores_scaled_amount_and_volume() -> None:
    rows = [
        {
            "date": "2026-04-17",
            "open": 18.2,
            "high": 18.8,
            "low": 18.0,
            "close": 18.66,
            "volume": 37671291.0,
            "amount": 690748023.0,
        },
        {
            "date": "2026-04-20",
            "open": 16.2,
            "high": 16.8,
            "low": 16.0,
            "close": 16.52,
            "volume": 0.0,
            "amount": 526436.0,
        },
        {
            "date": "2026-04-21",
            "open": 17.2,
            "high": 17.7,
            "low": 17.0,
            "close": 17.52,
            "volume": 0.0,
            "amount": 444378.0,
        },
    ]

    repaired = repair_history_rows_from_amount(rows)
    calibration_ratio = rows[0]["amount"] / (rows[0]["volume"] * rows[0]["close"])

    assert repaired[0]["volume"] == 37671291.0
    assert repaired[1]["amount"] == pytest.approx(526436000.0, rel=1e-6)
    assert repaired[1]["volume"] == pytest.approx(
        repaired[1]["amount"] / (repaired[1]["close"] * calibration_ratio),
        rel=1e-6,
    )
    assert repaired[1]["_volume_reconstructed_from_amount"] is True
    assert repaired[1]["_amount_scale_repaired"] is True


def test_repair_history_rows_from_amount_is_idempotent_for_repair_flags() -> None:
    rows = [
        {
            "date": "2026-04-17",
            "open": 18.2,
            "high": 18.8,
            "low": 18.0,
            "close": 18.66,
            "volume": 37671291.0,
            "amount": 690748023.0,
        },
        {
            "date": "2026-04-20",
            "open": 16.2,
            "high": 16.8,
            "low": 16.0,
            "close": 16.52,
            "volume": 0.0,
            "amount": 526436.0,
        },
    ]

    repaired_once = repair_history_rows_from_amount(rows)
    repaired_twice = repair_history_rows_from_amount(repaired_once)

    assert repaired_once[1]["_volume_reconstructed_from_amount"] is True
    assert repaired_twice[1]["_volume_reconstructed_from_amount"] is True
    assert repaired_twice[1]["_amount_scale_repaired"] is True
