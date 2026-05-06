# -*- coding: utf-8 -*-
"""Same-day cache for shortline explanations."""

from __future__ import annotations

import json
from pathlib import Path

from src.shortline_hub.schemas import ShortlineExplanation


class ShortlineExplainCache:
    """Simple JSON-backed cache keyed by trade_date + symbol + mode."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _build_key(*, trade_date: str, symbol: str, mode: str) -> str:
        return f"{str(trade_date).strip()}::{str(symbol).strip()}::{str(mode).strip()}"

    def _load_payload(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get(
        self,
        *,
        trade_date: str,
        symbol: str,
        mode: str,
    ) -> ShortlineExplanation | None:
        payload = self._load_payload()
        key = self._build_key(trade_date=trade_date, symbol=symbol, mode=mode)
        row = payload.get(key)
        return ShortlineExplanation(**row) if row else None

    def put(
        self,
        *,
        trade_date: str,
        symbol: str,
        mode: str,
        explanation: ShortlineExplanation,
    ) -> None:
        payload = self._load_payload()
        key = self._build_key(trade_date=trade_date, symbol=symbol, mode=mode)
        payload[key] = explanation.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
