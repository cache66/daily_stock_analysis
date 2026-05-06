#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template bridge script for WonderTrader -> shortline_hub JSON protocol."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "candidate_id",
    "symbol",
    "name",
    "trade_date",
    "scan_source",
    "trigger_type",
    "trigger_reason",
    "trigger_score",
    "price",
    "change_pct",
    "change_pct_60d",
    "amount",
    "volume_ratio",
    "turnover_rate",
    "board_name",
    "risk_flags",
]


def _load_request(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8"))


def _resolve_wt_source_mode(payload: dict[str, Any]) -> str:
    configured = str(payload.get("wt_source_mode") or "").strip().lower()
    if configured in {"prefer_real_engine", "prefer_bridge_data"}:
        return configured
    return "prefer_real_engine"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_candidate(raw: dict[str, Any], *, trade_date: str) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or "").strip()
    candidate_id = str(raw.get("candidate_id") or "").strip() or f"{trade_date}-{symbol or 'unknown'}"
    risk_flags = raw.get("risk_flags") or []
    if isinstance(risk_flags, str):
        risk_flags = [item.strip() for item in risk_flags.split(",") if item.strip()]

    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "name": str(raw.get("name") or symbol or "unknown").strip(),
        "trade_date": str(raw.get("trade_date") or trade_date).strip(),
        "scan_source": str(raw.get("scan_source") or "wondertrader_process").strip(),
        "trigger_type": str(raw.get("trigger_type") or "manual_export").strip(),
        "trigger_reason": str(
            raw.get("trigger_reason") or "loaded from local WonderTrader bridge export"
        ).strip(),
        "trigger_score": _to_float(raw.get("trigger_score"), 0.0),
        "price": _to_float(raw.get("price"), 0.0),
        "change_pct": _to_float(raw.get("change_pct"), 0.0),
        "change_pct_60d": _to_float(raw.get("change_pct_60d"), 0.0),
        "amount": _to_float(raw.get("amount"), 0.0),
        "volume_ratio": _to_float(raw.get("volume_ratio"), 0.0),
        "turnover_rate": _to_float(raw.get("turnover_rate"), 0.0),
        "board_name": str(raw.get("board_name") or "unknown_board").strip(),
        "setup_tag": str(raw.get("setup_tag") or "").strip(),
        "risk_flags": [str(item).strip() for item in risk_flags if str(item).strip()],
    }


def _bridge_data_dir(script_dir: Path) -> Path:
    return script_dir / "bridge_data"


def _discover_repo_root(script_dir: Path) -> Path | None:
    env_value = str(
        os.getenv("SHORTLINE_HUB_REPO_ROOT")
        or os.getenv("DAILY_STOCK_ANALYSIS_ROOT")
        or ""
    ).strip()
    candidates: list[Path] = []
    if env_value:
        candidates.append(Path(env_value))
    candidates.extend(
        [
            script_dir.parent / "daily_stock_analysis",
            script_dir.parent.parent / "daily_stock_analysis",
            script_dir.parent.parent.parent / "daily_stock_analysis",
        ]
    )
    for candidate in candidates:
        if (candidate / "data" / "cache" / "reference").exists():
            return candidate
    return None


def _repo_spot_cache_path(script_dir: Path) -> Path | None:
    repo_root = _discover_repo_root(script_dir)
    if repo_root is None:
        return None
    cache_path = repo_root / "data" / "cache" / "reference" / "kline_selector_spot_universe.csv"
    return cache_path if cache_path.exists() else None


def _repo_stock_basic_path(script_dir: Path) -> Path | None:
    repo_root = _discover_repo_root(script_dir)
    if repo_root is None:
        return None
    stock_basic_path = repo_root / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"
    return stock_basic_path if stock_basic_path.exists() else None


def _load_repo_industry_map(script_dir: Path) -> dict[str, str]:
    stock_basic_path = _repo_stock_basic_path(script_dir)
    if stock_basic_path is None:
        return {}

    with stock_basic_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    industry_map: dict[str, str] = {}
    for raw in rows:
        symbol = str(raw.get("code") or raw.get("symbol") or "").strip()
        industry = str(raw.get("industry") or raw.get("??") or "").strip()
        if len(symbol) == 6 and symbol.isdigit() and industry:
            industry_map[symbol] = industry
    return industry_map


def _resolve_cache_board_name(
    raw: dict[str, Any], *, symbol: str, industry_map: dict[str, str]
) -> str:
    board_name = str(raw.get("industry") or raw.get("board_name") or "").strip()
    if board_name:
        return board_name
    board_name = str(industry_map.get(symbol) or "").strip()
    return board_name or "spot_cache"


def _candidate_file_candidates(script_dir: Path, trade_date: str) -> list[Path]:
    bridge_data_dir = _bridge_data_dir(script_dir)
    return [
        bridge_data_dir / f"wt_candidates_{trade_date}.json",
        bridge_data_dir / "wt_candidates_latest.json",
        bridge_data_dir / f"wt_candidates_{trade_date}.csv",
        bridge_data_dir / "wt_candidates_latest.csv",
    ]


def _load_local_candidates(script_dir: Path, trade_date: str) -> list[dict[str, Any]] | None:
    for file_path in _candidate_file_candidates(script_dir, trade_date):
        if not file_path.exists():
            continue
        if file_path.suffix.lower() == ".json":
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise TypeError(f"candidate export file must contain a list: {file_path}")
            return [_normalize_candidate(item, trade_date=trade_date) for item in payload]

        if file_path.suffix.lower() == ".csv":
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            return [_normalize_candidate(item, trade_date=trade_date) for item in rows]

    return None


def _load_real_engine_candidates(script_dir: Path, trade_date: str, top_n: int) -> list[dict[str, Any]] | None:
    repo_root = _discover_repo_root(script_dir)
    if repo_root is None:
        return None

    import_candidates = [
        ("src.shortline_hub.wondertrader_real_engine", str(repo_root)),
        ("shortline_hub.wondertrader_real_engine", str(repo_root / "src")),
    ]
    for module_name, module_path in import_candidates:
        try:
            if module_path not in sys.path:
                sys.path.insert(0, module_path)
            module = __import__(module_name, fromlist=["export_candidates_via_real_wondertrader"])
            export_fn = getattr(module, "export_candidates_via_real_wondertrader", None)
            if export_fn is None:
                continue
            rows = export_fn(repo_root=repo_root, trade_date=trade_date, top_n=top_n)
            if not rows:
                return []
            return [_normalize_candidate(item, trade_date=trade_date) for item in rows]
        except Exception as exc:
            print(
                f"[shortline_wt_bridge] real engine helper failed via {module_name}: {exc}",
                file=sys.stderr,
            )
    return None


def _score_spot_row(row: dict[str, Any]) -> float:
    pct_change = _to_float(row.get("pct_change"), 0.0)
    turnover_rate = _to_float(row.get("turnover_rate"), 0.0)
    volume_ratio = _to_float(row.get("volume_ratio"), 0.0)
    change_pct_60d = _to_float(row.get("change_pct_60d"), 0.0)
    amount = _to_float(row.get("amount"), 0.0)
    liquidity_bonus = min(max(amount, 0.0) / 100000000.0, 8.0)
    return (
        pct_change * 6.0
        + min(max(turnover_rate, 0.0), 20.0) * 2.0
        + min(max(volume_ratio, 0.0), 5.0) * 8.0
        + min(max(change_pct_60d, 0.0), 60.0) * 0.2
        + liquidity_bonus
    )


def _build_trigger_type(*, pct_change: float, turnover_rate: float, volume_ratio: float) -> str:
    if pct_change >= 9.5:
        return "limit_up_momentum"
    if pct_change >= 7.0 and volume_ratio >= 1.8:
        return "momentum_breakout"
    if turnover_rate >= 4.0:
        return "active_turnover_push"
    return "strong_relative_strength"


def _build_setup_tag(
    *, trigger_type: str, pct_change: float, turnover_rate: float, volume_ratio: float
) -> str:
    if trigger_type == "limit_up_momentum":
        if turnover_rate >= 18.0:
            return "涨停后高换手分歧"
        if turnover_rate >= 10.0:
            return "涨停后分歧承接"
        return "涨停强势延续"
    if trigger_type == "momentum_breakout":
        if volume_ratio >= 2.4 and pct_change >= 8.0:
            return "强势放量抢筹"
        return "放量突破" if volume_ratio >= 2.0 and pct_change >= 6.0 else "强势突破跟进"
    if trigger_type == "active_turnover_push":
        if turnover_rate >= 15.0:
            return "高换手爆量博弈"
        if turnover_rate >= 10.0:
            return "高换手博弈"
        return "活跃换手拉升" if pct_change >= 5.0 else "活跃换手推进"
    if pct_change >= 6.0 and volume_ratio >= 1.3:
        return "板块核心跟涨"
    if pct_change >= 4.0:
        return "板块跟涨"
    return "相对强势整理"


def _build_risk_flags(*, listed_days: int, pct_change: float, turnover_rate: float) -> list[str]:
    flags: list[str] = []
    if listed_days and listed_days < 365:
        flags.append("recent_listing")
    if pct_change >= 9.5:
        flags.append("limit_up_extension")
    if turnover_rate >= 10.0:
        flags.append("high_turnover")
    return flags


def _load_repo_spot_cache_candidates(script_dir: Path, trade_date: str) -> list[dict[str, Any]] | None:
    cache_path = _repo_spot_cache_path(script_dir)
    if cache_path is None:
        return None

    with cache_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    industry_map = _load_repo_industry_map(script_dir)
    selected: list[tuple[float, dict[str, Any]]] = []
    for raw in rows:
        symbol = str(raw.get("code") or raw.get("symbol") or "").strip()
        name = str(raw.get("name") or symbol).strip()
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        if "ST" in name.upper():
            continue

        listed_days = int(_to_float(raw.get("listed_days"), 0.0))
        pct_change = _to_float(raw.get("pct_change"), 0.0)
        turnover_rate = _to_float(raw.get("turnover_rate"), 0.0)
        volume_ratio = _to_float(raw.get("volume_ratio"), 0.0)
        latest_price = _to_float(raw.get("latest_price"), 0.0)
        change_pct_60d = _to_float(raw.get("change_pct_60d"), 0.0)
        amount = _to_float(raw.get("amount"), 0.0)

        if listed_days and listed_days < 120:
            continue
        if pct_change < 2.0:
            continue
        if turnover_rate < 1.0 and volume_ratio < 1.2:
            continue

        score = _score_spot_row(raw)
        trigger_type = _build_trigger_type(
            pct_change=pct_change,
            turnover_rate=turnover_rate,
            volume_ratio=volume_ratio,
        )
        selected.append(
            (
                score,
                {
                    "candidate_id": f"{trade_date}-{symbol}",
                    "symbol": symbol,
                    "name": name,
                    "trade_date": trade_date,
                    "scan_source": "wondertrader_cache_scan",
                    "trigger_type": trigger_type,
                    "trigger_reason": (
                        "loaded from repo spot cache: "
                        f"pct_change={pct_change:.2f}, volume_ratio={volume_ratio:.2f}, "
                        f"turnover_rate={turnover_rate:.2f}, change_pct_60d={change_pct_60d:.2f}"
                    ),
                    "trigger_score": round(score, 2),
                    "price": latest_price,
                    "change_pct": pct_change,
                    "change_pct_60d": change_pct_60d,
                    "amount": amount,
                    "volume_ratio": volume_ratio,
                    "turnover_rate": turnover_rate,
                    "board_name": _resolve_cache_board_name(
                        raw,
                        symbol=symbol,
                        industry_map=industry_map,
                    ),
                    "setup_tag": _build_setup_tag(
                        trigger_type=trigger_type,
                        pct_change=pct_change,
                        turnover_rate=turnover_rate,
                        volume_ratio=volume_ratio,
                    ),
                    "risk_flags": _build_risk_flags(
                        listed_days=listed_days,
                        pct_change=pct_change,
                        turnover_rate=turnover_rate,
                    ),
                    "_score": score,
                    "_amount": amount,
                },
            )
        )

    if not selected:
        return None

    selected.sort(
        key=lambda item: (
            item[0],
            _to_float(item[1].get("change_pct"), 0.0),
            _to_float(item[1].get("volume_ratio"), 0.0),
            _to_float(item[1].get("turnover_rate"), 0.0),
            _to_float(item[1].get("_amount"), 0.0),
        ),
        reverse=True,
    )
    normalized: list[dict[str, Any]] = []
    for _, row in selected:
        row.pop("_score", None)
        row.pop("_amount", None)
        normalized.append(row)
    return normalized


def _load_repo_spot_cache_candidates_v2(
    script_dir: Path, trade_date: str
) -> list[dict[str, Any]] | None:
    return _load_repo_spot_cache_candidates(script_dir, trade_date)


def _build_placeholder_candidates(*, trade_date: str, top_n: int) -> list[dict[str, Any]]:
    candidates = [
        {
            "candidate_id": f"{trade_date}-300001",
            "symbol": "300001",
            "name": "placeholder_alpha",
            "trade_date": trade_date,
            "scan_source": "wondertrader_template",
            "trigger_type": "momentum_breakout",
            "trigger_reason": "template bridge fallback output",
            "trigger_score": 87.0,
            "price": 21.50,
            "change_pct": 7.60,
            "volume_ratio": 2.00,
            "turnover_rate": 5.10,
            "board_name": "placeholder_board",
            "setup_tag": "放量突破",
            "risk_flags": [],
        },
        {
            "candidate_id": f"{trade_date}-600111",
            "symbol": "600111",
            "name": "placeholder_beta",
            "trade_date": trade_date,
            "scan_source": "wondertrader_template",
            "trigger_type": "theme_reacceleration",
            "trigger_reason": "template bridge fallback output",
            "trigger_score": 84.0,
            "price": 19.82,
            "change_pct": 5.43,
            "volume_ratio": 1.76,
            "turnover_rate": 4.62,
            "board_name": "placeholder_board",
            "setup_tag": "活跃换手推进",
            "risk_flags": ["high_volatility"],
        },
        {
            "candidate_id": f"{trade_date}-002261",
            "symbol": "002261",
            "name": "placeholder_gamma",
            "trade_date": trade_date,
            "scan_source": "wondertrader_template",
            "trigger_type": "capital_flow_follow",
            "trigger_reason": "template bridge fallback output",
            "trigger_score": 81.0,
            "price": 31.26,
            "change_pct": 4.88,
            "volume_ratio": 1.52,
            "turnover_rate": 6.73,
            "board_name": "placeholder_board",
            "setup_tag": "相对强势整理",
            "risk_flags": [],
        },
    ]
    return candidates[: max(0, int(top_n))]


def _ensure_required_fields(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"candidate row missing required fields: {missing}")


def _build_candidates(
    *,
    script_dir: Path,
    trade_date: str,
    top_n: int,
    wt_source_mode: str,
) -> list[dict[str, Any]]:
    source_mode = str(wt_source_mode or "prefer_real_engine").strip().lower()
    ordered_loaders = (
        [
            lambda: _load_local_candidates(script_dir, trade_date),
            lambda: _load_real_engine_candidates(script_dir, trade_date, top_n),
        ]
        if source_mode == "prefer_bridge_data"
        else [
            lambda: _load_real_engine_candidates(script_dir, trade_date, top_n),
            lambda: _load_local_candidates(script_dir, trade_date),
        ]
    )
    for loader in ordered_loaders:
        rows = loader()
        if rows is None:
            continue
        rows = rows[: max(0, int(top_n))]
        _ensure_required_fields(rows)
        return rows

    cache_rows = _load_repo_spot_cache_candidates_v2(script_dir, trade_date)
    if cache_rows is not None:
        rows = cache_rows[: max(0, int(top_n))]
        _ensure_required_fields(rows)
        return rows

    rows = _build_placeholder_candidates(trade_date=trade_date, top_n=top_n)
    _ensure_required_fields(rows)
    return rows


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: python shortline_wondertrader_bridge_template.py <request_json> <output_json>",
            file=sys.stderr,
        )
        return 1

    request_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = _load_request(request_path)
    trade_date = str(payload.get("trade_date") or "").strip()
    top_n = int(payload.get("top_n") or 0)
    wt_source_mode = _resolve_wt_source_mode(payload)
    if not trade_date:
        raise ValueError("trade_date is required")

    script_dir = Path(__file__).resolve().parent
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            _build_candidates(
                script_dir=script_dir,
                trade_date=trade_date,
                top_n=top_n,
                wt_source_mode=wt_source_mode,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
