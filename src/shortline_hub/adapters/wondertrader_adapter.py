# -*- coding: utf-8 -*-
"""WonderTrader-style candidate adapter."""

from __future__ import annotations

from src.shortline_hub.adapters.process_utils import (
    ProcessAdapterConfig,
    build_runtime_paths,
    read_json_payload,
    run_external_python_process,
    write_json_payload,
)
from src.shortline_hub.schemas import ShortlineCandidate


class StubWonderTraderAdapter:
    """Deterministic stub that simulates a WonderTrader candidate feed."""

    def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
        base_candidates = [
            ShortlineCandidate(
                candidate_id=f"{trade_date}-000001",
                symbol="000001",
                name="平安银行",
                trade_date=trade_date,
                scan_source="wondertrader_stub",
                trigger_type="breakout_retest",
                trigger_reason="强势突破后量价配合，板块承接仍在",
                trigger_score=82.0,
                price=12.36,
                change_pct=4.82,
                volume_ratio=1.73,
                turnover_rate=3.41,
                board_name="金融",
                setup_tag="回调再突破",
                risk_flags=["earnings_pending"],
            ),
            ShortlineCandidate(
                candidate_id=f"{trade_date}-600000",
                symbol="600000",
                name="浦发银行",
                trade_date=trade_date,
                scan_source="wondertrader_stub",
                trigger_type="capital_flow_follow",
                trigger_reason="大单承接增强，低位放量跟随",
                trigger_score=78.0,
                price=10.88,
                change_pct=3.26,
                volume_ratio=1.42,
                turnover_rate=2.67,
                board_name="银行",
                setup_tag="相对强势整理",
                risk_flags=[],
            ),
            ShortlineCandidate(
                candidate_id=f"{trade_date}-300750",
                symbol="300750",
                name="宁德时代",
                trade_date=trade_date,
                scan_source="wondertrader_stub",
                trigger_type="leader_reacceleration",
                trigger_reason="龙头再加速，趋势与板块共振",
                trigger_score=88.0,
                price=219.50,
                change_pct=5.12,
                volume_ratio=2.15,
                turnover_rate=4.02,
                board_name="新能源",
                setup_tag="放量突破",
                risk_flags=["high_volatility"],
            ),
        ]
        return base_candidates[: max(0, int(top_n))]


class WonderTraderProcessAdapter:
    """Call an external WonderTrader-compatible script via local process."""

    def __init__(
        self,
        *,
        config: ProcessAdapterConfig,
        source_mode: str = "prefer_real_engine",
    ) -> None:
        self.config = config
        self.source_mode = str(source_mode or "prefer_real_engine").strip() or "prefer_real_engine"

    def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
        request_path, output_path = build_runtime_paths(self.config.runtime_dir, "wondertrader")
        write_json_payload(
            request_path,
            {
                "trade_date": str(trade_date),
                "top_n": max(0, int(top_n)),
                "wt_source_mode": self.source_mode,
            },
        )
        run_external_python_process(
            config=self.config,
            request_path=request_path,
            output_path=output_path,
        )
        payload = read_json_payload(output_path)
        return [
            ShortlineCandidate(
                candidate_id=str(item["candidate_id"]),
                symbol=str(item["symbol"]),
                name=str(item["name"]),
                trade_date=str(item["trade_date"]),
                scan_source=str(item["scan_source"]),
                trigger_type=str(item["trigger_type"]),
                trigger_reason=str(item["trigger_reason"]),
                trigger_score=float(item["trigger_score"]),
                price=float(item["price"]),
                change_pct=float(item["change_pct"]),
                change_pct_60d=float(item.get("change_pct_60d") or 0.0),
                amount=float(item.get("amount") or 0.0),
                volume_ratio=float(item["volume_ratio"]),
                turnover_rate=float(item["turnover_rate"]),
                board_name=str(item["board_name"]),
                setup_tag=str(item.get("setup_tag") or ""),
                risk_flags=[str(flag) for flag in item.get("risk_flags", [])],
            )
            for item in payload
        ]
