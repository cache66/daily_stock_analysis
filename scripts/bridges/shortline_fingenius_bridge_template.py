#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template bridge script for FinGenius -> shortline_hub JSON protocol."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import sys
import time
import types
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "candidate_id",
    "hot_money_summary",
    "big_deal_summary",
    "chip_commentary",
    "sentiment_commentary",
    "risk_commentary",
    "short_term_view",
    "confidence_label",
]

GENERIC_BOARD_NAMES = {
    "",
    "spot_cache",
    "unknown_board",
    "placeholder_board",
}

BRIDGE_PROTOCOL_VERSION = "shortline_fg_v1"
SOURCE_BRIDGE_DATA = "bridge_data"
SOURCE_UPSTREAM_TOOLS = "upstream_tools"
SOURCE_HEURISTIC_FALLBACK = "heuristic_fallback"


def _load_request(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8-sig"))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bridge_data_dir(script_dir: Path) -> Path:
    return script_dir / "bridge_data"


def _explanation_file_candidates(script_dir: Path, trade_date: str) -> list[Path]:
    bridge_data_dir = _bridge_data_dir(script_dir)
    return [
        bridge_data_dir / f"fg_explanations_{trade_date}.json",
        bridge_data_dir / "fg_explanations_latest.json",
    ]


def _normalize_explanation(raw: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": str(raw.get("candidate_id") or candidate_id).strip(),
        "hot_money_summary": str(raw.get("hot_money_summary") or "").strip(),
        "big_deal_summary": str(raw.get("big_deal_summary") or "").strip(),
        "chip_commentary": str(raw.get("chip_commentary") or "").strip(),
        "sentiment_commentary": str(raw.get("sentiment_commentary") or "").strip(),
        "risk_commentary": str(raw.get("risk_commentary") or "").strip(),
        "short_term_view": str(raw.get("short_term_view") or "").strip(),
        "confidence_label": str(raw.get("confidence_label") or "medium").strip() or "medium",
    }


def _load_local_explanation(script_dir: Path, candidate: dict[str, Any]) -> dict[str, Any] | None:
    trade_date = str(candidate.get("trade_date") or "").strip()
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    symbol = str(candidate.get("symbol") or "").strip()

    for file_path in _explanation_file_candidates(script_dir, trade_date):
        if not file_path.exists():
            continue
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            raw = payload.get(candidate_id) or payload.get(symbol)
            if isinstance(raw, dict):
                return _normalize_explanation(raw, candidate_id=candidate_id)
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                if str(item.get("candidate_id") or "").strip() == candidate_id:
                    return _normalize_explanation(item, candidate_id=candidate_id)
                if symbol and str(item.get("symbol") or "").strip() == symbol:
                    return _normalize_explanation(item, candidate_id=candidate_id)
    return None


def _normalize_risk_flags(candidate: dict[str, Any]) -> list[str]:
    risk_flags = candidate.get("risk_flags") or []
    if isinstance(risk_flags, str):
        risk_flags = [item.strip() for item in risk_flags.split(",") if item.strip()]
    return [str(item).strip() for item in risk_flags if str(item).strip()]


def _is_meaningful_board_name(board_name: str) -> bool:
    return board_name.strip() not in GENERIC_BOARD_NAMES


def _normalize_setup_tag(candidate: dict[str, Any]) -> str:
    return str(candidate.get("setup_tag") or "").strip()


def _normalize_bridge_options(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("bridge_options") or {}
    return value if isinstance(value, dict) else {}


def _is_big_deal_enabled(bridge_options: dict[str, Any]) -> bool:
    return bool(bridge_options.get("enable_big_deal"))


def _pick_first_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        return value
    return None


def _pick_matching_record(rows: Any, stock_code: str) -> dict[str, Any] | None:
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            code = str(
                item.get("股票代码")
                or item.get("代码")
                or item.get("鑲＄エ浠ｇ爜")
                or item.get("浠ｇ爜")
                or item.get("stock_code")
                or item.get("symbol")
                or ""
            ).strip()
            if stock_code and code == stock_code:
                return item
        return _pick_first_record(rows)
    if isinstance(rows, dict):
        return rows
    return None


def _extract_value(container: Any, *keys: str) -> Any:
    if not isinstance(container, dict):
        return None
    for key in keys:
        if key in container and container[key] not in (None, ""):
            return container[key]
    return None


def _format_amount_wan(value: Any) -> str:
    amount = _to_float(value, 0.0)
    if amount <= 0:
        return ""
    return f"{amount:.2f}万"


def _format_percent(value: Any) -> str:
    number = _to_float(value, 0.0)
    return f"{number:.2f}%"


def _normalize_stock_code(symbol: str) -> str:
    value = str(symbol or "").strip()
    digits = re.findall(r"\d+", value)
    if digits:
        for chunk in digits:
            if len(chunk) >= 6:
                return chunk[-6:]
        return digits[-1]
    return value.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")


def _append_unique(parts: list[str], text: str) -> None:
    cleaned = str(text or "").strip()
    if cleaned and cleaned not in parts:
        parts.append(cleaned)


def _coerce_tool_result(result: Any) -> tuple[Any, str]:
    if result is None:
        return None, "empty tool result"
    error = str(getattr(result, "error", "") or "").strip()
    output = getattr(result, "output", None)
    if output is None and isinstance(result, dict):
        output = result.get("output")
        error = error or str(result.get("error") or "").strip()
    return output, error


def _build_metadata_payload(
    *,
    payload: dict[str, Any],
    explanation_source: str,
    started_at: float,
    used_upstream_tools: list[str] | None = None,
    tool_errors: list[str] | None = None,
    upstream_tool_elapsed_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    normalized_tools = [
        str(item).strip() for item in (used_upstream_tools or []) if str(item).strip()
    ]
    normalized_errors = [
        str(item).strip() for item in (tool_errors or []) if str(item).strip()
    ]
    merged = dict(payload)
    merged["protocol_version"] = BRIDGE_PROTOCOL_VERSION
    merged["explanation_source"] = str(explanation_source).strip()
    merged["used_upstream_tools"] = normalized_tools
    merged["tool_error_count"] = len(normalized_errors)
    merged["tool_errors"] = normalized_errors
    merged["upstream_tool_elapsed_ms"] = {
        str(key).strip(): max(0, int(value or 0))
        for key, value in (upstream_tool_elapsed_ms or {}).items()
        if str(key).strip()
    }
    merged["explain_elapsed_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    return merged


def _ensure_loguru_compat() -> None:
    try:
        importlib.import_module("loguru")
        return
    except ModuleNotFoundError:
        pass

    class _CompatLogger:
        def __init__(self) -> None:
            self._logger = logging.getLogger("fingenius_loguru_compat")
            if not self._logger.handlers:
                self._logger.addHandler(logging.NullHandler())
            self._logger.setLevel(logging.INFO)

        def remove(self, *args: Any, **kwargs: Any) -> None:
            return None

        def add(self, *args: Any, **kwargs: Any) -> int:
            return 1

        def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.info(str(message))

        def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.debug(str(message))

        def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.warning(str(message))

        def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.error(str(message))

        def critical(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.critical(str(message))

        def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._logger.exception(str(message))

    compat_module = types.ModuleType("loguru")
    compat_module.logger = _CompatLogger()
    sys.modules["loguru"] = compat_module


def _load_real_tool_classes(script_dir: Path) -> tuple[type[Any], type[Any], type[Any]] | None:
    upstream_root = script_dir.parent / "upstream"
    if not upstream_root.exists():
        return None

    upstream_root_str = str(upstream_root)
    if upstream_root_str not in sys.path:
        sys.path.insert(0, upstream_root_str)

    _ensure_loguru_compat()
    importlib.invalidate_caches()
    hot_module = importlib.import_module("src.tool.hot_money")
    chip_module = importlib.import_module("src.tool.chip_analysis")
    big_module = importlib.import_module("src.tool.big_deal_analysis")
    return (
        getattr(hot_module, "HotMoneyTool"),
        getattr(chip_module, "ChipAnalysisTool"),
        getattr(big_module, "BigDealAnalysisTool"),
    )


def _run_tool(tool: Any, **kwargs: Any) -> tuple[Any, str]:
    result = asyncio.run(tool.execute(**kwargs))
    return _coerce_tool_result(result)


def _build_real_hot_money_summary(
    candidate: dict[str, Any],
    hot_output: dict[str, Any],
    *,
    board_label: str,
    stock_code: str,
) -> str:
    stock_label = str(candidate.get("name") or candidate.get("symbol") or "该股").strip()
    latest_info = _pick_matching_record(hot_output.get("stock_latest_info"), stock_code)
    top_record = _pick_matching_record(hot_output.get("daily_top_list"), stock_code)
    flow_record = _pick_first_record(hot_output.get("stock_net_flow"))
    section_payload = hot_output.get("hot_section_data")

    parts: list[str] = []
    net_flow_text = _extract_value(
        flow_record,
        "主力净流入",
        "主力净流入-净额",
        "今日主力净流入-净额",
        "main_net_inflow",
        "涓诲姏鍑€娴佸叆",
        "涓诲姏鍑€娴佸叆-鍑€棰",
        "浠婃棩涓诲姏鍑€娴佸叆-鍑€棰",
    )
    if net_flow_text:
        _append_unique(parts, f"主力净流入 {net_flow_text}")

    turnover = _extract_value(
        latest_info,
        "换手率",
        "换手",
        "turnover_rate",
        "鎹㈡墜鐜",
    )
    if turnover not in (None, ""):
        _append_unique(parts, f"换手 {_format_percent(turnover)}")
    elif candidate.get("turnover_rate") not in (None, ""):
        _append_unique(parts, f"换手 {_format_percent(candidate.get('turnover_rate'))}")

    top_reason = _extract_value(
        top_record,
        "上榜原因",
        "解读",
        "原因",
        "reason",
        "涓婃鍘熷洜",
        "瑙ｈ",
        "鍘熷洜",
    )
    if top_reason:
        _append_unique(parts, f"上榜线索 {top_reason}")

    if isinstance(section_payload, dict):
        for key in ("industry", "concept", "hot", "regional"):
            record = _pick_first_record(section_payload.get(key))
            section_name = _extract_value(record, "板块名称", "名称", "name", "鏉垮潡鍚嶇О", "鍚嶇О")
            section_move = _extract_value(record, "涨跌幅", "涨幅", "娑ㄨ穼骞", "娑ㄥ箙")
            if section_name:
                if section_move not in (None, ""):
                    _append_unique(parts, f"热点板块 {section_name} {_format_percent(section_move)}")
                else:
                    _append_unique(parts, f"热点板块 {section_name}")
                break

    if parts:
        return f"{stock_label} 的 FinGenius 资金侧显示：{'；'.join(parts)}。当前更贴近 {board_label} 方向。"

    return (
        f"{stock_label} 已走到 FinGenius 真实资金工具链，但热钱字段暂不完整，"
        f"当前先按 {board_label} 方向的增量承接来理解。"
    )


def _build_real_big_deal_summary(
    candidate: dict[str, Any],
    big_output: dict[str, Any],
    *,
    board_label: str,
) -> str:
    stock_label = str(candidate.get("name") or candidate.get("symbol") or "该股").strip()
    summary = big_output.get("stock_big_deal_summary") or {}
    rank_record = _pick_first_record(big_output.get("individual_rank_stock"))

    net_inflow = _extract_value(summary, "net_inflow_wan", "净流入", "主力净流入", "鍑€娴佸叆", "涓诲姏鍑€娴佸叆")
    trade_count = _extract_value(summary, "trade_count", "成交笔数", "鎴愪氦绗旀暟")
    rank_flow = _extract_value(
        rank_record,
        "今日主力净流入-净额",
        "主力净流入",
        "净流入",
        "main_net_inflow",
        "浠婃棩涓诲姏鍑€娴佸叆-鍑€棰",
        "涓诲姏鍑€娴佸叆",
        "鍑€娴佸叆",
    )

    parts: list[str] = []
    if net_inflow not in (None, ""):
        _append_unique(parts, f"大单净流入 {_format_amount_wan(net_inflow)}")
    if trade_count not in (None, "") and str(trade_count).strip():
        _append_unique(parts, f"样本笔数 {int(_to_float(trade_count, 0.0))}")
    if rank_flow:
        _append_unique(parts, f"排行口径主力净流入 {rank_flow}")

    if parts:
        return f"{stock_label} 的大单行为显示：{'；'.join(parts)}。短线资金确实在围绕 {board_label} 博弈。"

    return f"{stock_label} 已调用 BigDealAnalysisTool，但当前拿到的大单明细还不够完整。"


def _build_proxy_big_deal_summary(
    candidate: dict[str, Any],
    hot_output: dict[str, Any],
    *,
    board_label: str,
) -> str:
    stock_label = str(candidate.get("name") or candidate.get("symbol") or "该股").strip()
    flow_record = _pick_first_record(hot_output.get("stock_net_flow"))
    flow_text = _extract_value(
        flow_record,
        "主力净流入",
        "主力净流入-净额",
        "今日主力净流入-净额",
        "main_net_inflow",
        "涓诲姏鍑€娴佸叆",
        "涓诲姏鍑€娴佸叆-鍑€棰",
        "浠婃棩涓诲姏鍑€娴佸叆-鍑€棰",
    )
    turnover_rate = _to_float(candidate.get("turnover_rate"), 0.0)
    volume_ratio = _to_float(candidate.get("volume_ratio"), 0.0)

    parts: list[str] = []
    if flow_text:
        _append_unique(parts, f"代理主力净流入 {flow_text}")
    if turnover_rate > 0:
        _append_unique(parts, f"换手 {_format_percent(turnover_rate)}")
    if volume_ratio > 0:
        _append_unique(parts, f"量比 {volume_ratio:.2f}")

    if parts:
        return (
            f"{stock_label} 未开启 BigDealAnalysisTool，先用 HotMoney/行情做代理大单强度："
            f"{'；'.join(parts)}。当前资金仍围绕 {board_label} 博弈。"
        )
    return f"{stock_label} 未开启 BigDealAnalysisTool，当前先按 {board_label} 的承接强度做代理大单解释。"


def _build_real_chip_commentary(candidate: dict[str, Any], chip_output: dict[str, Any]) -> tuple[str, list[str]]:
    stock_label = str(candidate.get("name") or candidate.get("symbol") or "该股").strip()
    analysis = chip_output.get("analysis") or {}
    basic = analysis.get("basic_analysis") or {}
    main_cost = analysis.get("main_cost_analysis") or {}
    concentration = analysis.get("concentration_analysis") or {}
    trading_signals = analysis.get("trading_signals") or {}

    parts: list[str] = []
    profit_ratio = _extract_value(basic, "profit_ratio")
    average_cost = _extract_value(basic, "average_cost")
    if average_cost not in (None, ""):
        _append_unique(parts, f"平均成本线 {float(average_cost):.2f}")
    if profit_ratio not in (None, ""):
        _append_unique(parts, f"获利盘 {_format_percent(profit_ratio)}")

    main_text = _extract_value(main_cost, "analysis")
    control_level = _extract_value(main_cost, "control_level")
    if main_text:
        _append_unique(parts, str(main_text))
    elif control_level:
        _append_unique(parts, f"主力控盘 {control_level}")

    concentration_text = _extract_value(concentration, "analysis")
    if concentration_text:
        _append_unique(parts, str(concentration_text))
    else:
        concentration_level = _extract_value(concentration, "concentration_level")
        if concentration_level:
            _append_unique(parts, f"筹码状态 {concentration_level}")

    buy_signals = trading_signals.get("buy_signals") or []
    risk_warnings = trading_signals.get("risk_warnings") or []
    if isinstance(buy_signals, list) and buy_signals:
        _append_unique(parts, f"偏多信号 {' / '.join(str(item) for item in buy_signals[:2])}")
    if isinstance(risk_warnings, list) and risk_warnings:
        _append_unique(parts, f"风险提示 {' / '.join(str(item) for item in risk_warnings[:2])}")

    if parts:
        commentary = f"{stock_label} 的筹码分析显示：{'；'.join(parts)}。"
    else:
        commentary = f"{stock_label} 已调用筹码工具，但当前结构信息较弱，先按交易型筹码理解。"
    return commentary, [str(item).strip() for item in risk_warnings if str(item).strip()]


def _build_real_sentiment_commentary(
    candidate: dict[str, Any],
    hot_output: dict[str, Any],
    *,
    board_name: str,
    board_label: str,
) -> str:
    trigger_type = str(candidate.get("trigger_type") or "当前触发结构").strip()
    section_payload = hot_output.get("hot_section_data")
    section_name = ""
    if isinstance(section_payload, dict):
        for key in ("industry", "concept", "hot", "regional"):
            record = _pick_first_record(section_payload.get(key))
            section_name = str(
                _extract_value(record, "板块名称", "名称", "name", "鏉垮潡鍚嶇О", "鍚嶇О") or ""
            ).strip()
            if section_name:
                break

    if board_name and section_name and board_name != section_name:
        return (
            f"{board_name} 仍是这只票最直接的情绪锚点，同时 FinGenius 热点层还捕捉到 {section_name} 的联动，"
            f"{trigger_type} 更像板块扩散中的强势跟进。"
        )
    if board_name:
        return f"{board_name} 是这只票当前最主要的情绪锚点，{trigger_type} 容易吸引短线资金反复博弈。"
    if section_name:
        return f"当前更像 {section_name} 情绪扩散中的个股强化，{trigger_type} 具备一定板块映射。"
    return f"当前仍以个股强势结构为主，{board_label} 是否持续扩散决定情绪延续度。"


def _build_real_risk_commentary(
    candidate: dict[str, Any],
    *,
    board_label: str,
    extra_warnings: list[str],
) -> str:
    stock_label = str(candidate.get("name") or candidate.get("symbol") or "该股").strip()
    risk_flags = _normalize_risk_flags(candidate)
    all_risks = risk_flags + [item for item in extra_warnings if item not in risk_flags]
    if all_risks:
        return f"{stock_label} 当前主要风险在 {' / '.join(all_risks[:3])}，若 {board_label} 转弱，短线波动会明显放大。"

    change_pct = _to_float(candidate.get("change_pct"), 0.0)
    if change_pct >= 9.5:
        return f"{stock_label} 已处于强势加速区，若次日无法继续扩散，容易出现冲高回落。"

    return "当前未见明显额外风险提示，重点观察板块温度和次日承接。"


def _build_real_short_term_view(
    candidate: dict[str, Any],
    *,
    board_name: str,
    setup_tag: str,
    trigger_type: str,
    buy_signals: list[str],
) -> str:
    structure_label = setup_tag or trigger_type or "当前强势结构"
    board_text = board_name or "当前强势方向"
    if isinstance(buy_signals, list) and buy_signals:
        signal_text = " / ".join(str(item) for item in buy_signals[:2])
        return f"短线先看 {board_text} 能否继续扩散，再看 {structure_label} 是否持续得到承接；当前偏多信号有 {signal_text}。"
    return f"短线先看 {board_text} 能否继续扩散，再看 {structure_label} 是否持续得到承接。"


def _ensure_required_fields(payload: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in payload or payload[field] == ""]
    if missing:
        raise ValueError(f"explanation payload missing required fields: {missing}")


def _build_real_tool_explanation(
    script_dir: Path,
    candidate: dict[str, Any],
    *,
    bridge_options: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        tool_classes = _load_real_tool_classes(script_dir)
    except Exception:
        return None
    if tool_classes is None:
        return None

    HotMoneyTool, ChipAnalysisTool, BigDealAnalysisTool = tool_classes
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    trade_date = str(candidate.get("trade_date") or "").strip()
    stock_code = _normalize_stock_code(str(candidate.get("symbol") or ""))
    if not stock_code:
        return None

    succeeded_tools: list[str] = []
    tool_errors: list[str] = []
    tool_elapsed_ms: dict[str, int] = {}
    big_deal_enabled = _is_big_deal_enabled(bridge_options)

    hot_started_at = time.perf_counter()
    try:
        hot_output, hot_error = _run_tool(
            HotMoneyTool(),
            stock_code=stock_code,
            date=trade_date,
            max_retry=1,
            sleep_seconds=0,
        )
    except Exception as exc:
        hot_output, hot_error = None, str(exc)
    tool_elapsed_ms["HotMoneyTool"] = max(0, int((time.perf_counter() - hot_started_at) * 1000))
    if isinstance(hot_output, dict) and hot_output:
        succeeded_tools.append("HotMoneyTool")
    if hot_error:
        tool_errors.append(f"HotMoneyTool: {hot_error}")

    chip_started_at = time.perf_counter()
    try:
        chip_output, chip_error = _run_tool(
            ChipAnalysisTool(),
            stock_code=stock_code,
            analysis_days=5,
        )
    except Exception as exc:
        chip_output, chip_error = None, str(exc)
    tool_elapsed_ms["ChipAnalysisTool"] = max(0, int((time.perf_counter() - chip_started_at) * 1000))
    if isinstance(chip_output, dict) and chip_output:
        succeeded_tools.append("ChipAnalysisTool")
    if chip_error:
        tool_errors.append(f"ChipAnalysisTool: {chip_error}")

    big_output = None
    big_error = ""
    if big_deal_enabled:
        big_started_at = time.perf_counter()
        try:
            big_output, big_error = _run_tool(
                BigDealAnalysisTool(),
                stock_code=stock_code,
                top_n=10,
                rank_symbol="即时",
                max_retry=1,
                sleep_seconds=0,
            )
        except Exception as exc:
            big_output, big_error = None, str(exc)
        tool_elapsed_ms["BigDealAnalysisTool"] = max(
            0,
            int((time.perf_counter() - big_started_at) * 1000),
        )
        if isinstance(big_output, dict) and big_output:
            succeeded_tools.append("BigDealAnalysisTool")
        if big_error:
            tool_errors.append(f"BigDealAnalysisTool: {big_error}")

    succeeded = [
        item
        for item in (hot_output, chip_output, big_output)
        if isinstance(item, dict) and item
    ]
    if not succeeded:
        candidate["_tool_errors"] = tool_errors
        candidate["_upstream_tool_elapsed_ms"] = tool_elapsed_ms
        return None

    board_name = str(candidate.get("board_name") or "").strip()
    board_label = board_name if _is_meaningful_board_name(board_name) else "当前强势方向"
    setup_tag = _normalize_setup_tag(candidate)
    trigger_type = str(candidate.get("trigger_type") or "").strip()

    hot_money_summary = _build_real_hot_money_summary(
        candidate,
        hot_output if isinstance(hot_output, dict) else {},
        board_label=board_label,
        stock_code=stock_code,
    )
    if big_deal_enabled:
        big_deal_summary = _build_real_big_deal_summary(
            candidate,
            big_output if isinstance(big_output, dict) else {},
            board_label=board_label,
        )
    else:
        big_deal_summary = _build_proxy_big_deal_summary(
            candidate,
            hot_output if isinstance(hot_output, dict) else {},
            board_label=board_label,
        )
    chip_commentary, chip_risk_warnings = _build_real_chip_commentary(
        candidate,
        chip_output if isinstance(chip_output, dict) else {},
    )
    sentiment_commentary = _build_real_sentiment_commentary(
        candidate,
        hot_output if isinstance(hot_output, dict) else {},
        board_name=board_name,
        board_label=board_label,
    )

    extra_warnings = chip_risk_warnings[:]
    if tool_errors and len(succeeded) < (3 if big_deal_enabled else 2):
        extra_warnings.append("partial_tool_output")
    risk_commentary = _build_real_risk_commentary(
        candidate,
        board_label=board_label,
        extra_warnings=extra_warnings,
    )

    trading_signals = {}
    if isinstance(chip_output, dict):
        trading_signals = (chip_output.get("analysis") or {}).get("trading_signals") or {}
    short_term_view = _build_real_short_term_view(
        candidate,
        board_name=board_name,
        setup_tag=setup_tag,
        trigger_type=trigger_type,
        buy_signals=trading_signals.get("buy_signals") or [],
    )

    confidence_label = "high"
    if len(succeeded) == 1 and _to_float(candidate.get("trigger_score"), 0.0) < 85:
        confidence_label = "medium"

    payload = {
        "candidate_id": candidate_id,
        "hot_money_summary": hot_money_summary,
        "big_deal_summary": big_deal_summary,
        "chip_commentary": chip_commentary,
        "sentiment_commentary": sentiment_commentary,
        "risk_commentary": risk_commentary,
        "short_term_view": short_term_view,
        "confidence_label": confidence_label,
    }
    payload["_used_upstream_tools"] = succeeded_tools
    payload["_tool_errors"] = tool_errors
    payload["_upstream_tool_elapsed_ms"] = tool_elapsed_ms
    _ensure_required_fields(payload)
    return payload


def _build_heuristic_explanation(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    symbol = str(candidate.get("symbol") or "").strip()
    name = str(candidate.get("name") or "").strip()
    board_name = str(candidate.get("board_name") or "").strip()
    trigger_type = str(candidate.get("trigger_type") or "").strip()
    setup_tag = _normalize_setup_tag(candidate)
    trigger_score = _to_float(candidate.get("trigger_score"), 0.0)
    change_pct = _to_float(candidate.get("change_pct"), 0.0)
    volume_ratio = _to_float(candidate.get("volume_ratio"), 0.0)
    turnover_rate = _to_float(candidate.get("turnover_rate"), 0.0)
    risk_flags = _normalize_risk_flags(candidate)

    stock_label = name or symbol or "该股"
    has_board_name = _is_meaningful_board_name(board_name)
    board_label = board_name if has_board_name else "当前强势方向"
    structure_label = setup_tag or trigger_type or "当前强势结构"

    if turnover_rate >= 18.0 or change_pct >= 9.5:
        hot_money_summary = (
            f"{stock_label} 当日涨幅 {change_pct:.2f}%，换手 {turnover_rate:.2f}%，"
            f"属于 {board_label} 方向里的高活跃强势样本，短线资金博弈较集中。"
        )
    elif volume_ratio >= 1.8:
        hot_money_summary = (
            f"{stock_label} 量比 {volume_ratio:.2f} 偏强，说明有增量资金跟随，"
            f"更像 {board_label} 方向内的主动强化。"
        )
    else:
        hot_money_summary = (
            f"{stock_label} 当前更像 {board_label} 情绪扩散下的跟涨样本，"
            "短线资金强度中等，后续还要看板块是否继续发酵。"
        )
    if setup_tag:
        hot_money_summary = f"{hot_money_summary.rstrip('。')}，当前形态偏 {setup_tag}。"

    big_deal_summary = (
        f"未开启 BigDealAnalysisTool，当前先按 {structure_label} 结构理解，"
        f"重点观察 {board_label} 是否继续出现主动承接；换手 {turnover_rate:.2f}%，量比 {volume_ratio:.2f}。"
    )

    if turnover_rate >= 12.0 and volume_ratio < 1.0:
        chip_commentary = (
            f"{stock_label} 更像高换手博弈，筹码还未完全沉淀，"
            f"要看 {board_label} 的持续性是否足以完成重新收敛。"
        )
    elif volume_ratio >= 1.8:
        chip_commentary = (
            f"{stock_label} 量价配合较好，筹码有从分散走向活跃集中的迹象，"
            f"若 {board_label} 延续，短线结构还会更顺。"
        )
    else:
        chip_commentary = (
            f"{stock_label} 当前筹码结构偏交易型，"
            f"后续能否走强主要取决于 {board_label} 是否继续给出承接。"
        )

    if has_board_name:
        sentiment_commentary = (
            f"{board_name} 是这只票当前最主要的情绪锚点，"
            f"{structure_label} 更容易吸引短线资金围绕该方向反复博弈。"
        )
    else:
        sentiment_commentary = (
            f"{stock_label} 当前更多依赖个股强势结构本身，"
            f"{structure_label} 能否延续要看次日承接是否继续增强。"
        )

    if risk_flags:
        risk_commentary = (
            f"{stock_label} 当前主要风险在 {', '.join(risk_flags)}，"
            f"若 {board_label} 走弱或次日承接转差，容易出现冲高回落。"
        )
    elif change_pct >= 9.5:
        risk_commentary = (
            f"{stock_label} 已处于强势加速区，"
            f"若 {board_label} 次日不能继续扩散，短线波动会明显放大。"
        )
    else:
        risk_commentary = "当前未见明显额外风险提示，重点观察板块温度和次日承接。"

    if has_board_name:
        short_term_view = (
            f"短线先看 {board_name} 能否继续扩散，再看 {structure_label} 是否持续得到承接；"
            f"当前触发分 {trigger_score:.1f}。"
        )
    else:
        short_term_view = (
            f"短线先看 {structure_label} 能否在下一个交易日继续得到承接；"
            f"当前触发分 {trigger_score:.1f}。"
        )

    return {
        "candidate_id": candidate_id,
        "hot_money_summary": hot_money_summary,
        "big_deal_summary": big_deal_summary,
        "chip_commentary": chip_commentary,
        "sentiment_commentary": sentiment_commentary,
        "risk_commentary": risk_commentary,
        "short_term_view": short_term_view,
        "confidence_label": "high" if trigger_score >= 85 else "medium",
    }


def _build_explanation(
    script_dir: Path,
    candidate: dict[str, Any],
    *,
    bridge_options: dict[str, Any],
) -> dict[str, Any]:
    started_at = time.perf_counter()
    local_payload = _load_local_explanation(script_dir, candidate)
    if local_payload is not None:
        _ensure_required_fields(local_payload)
        return _build_metadata_payload(
            payload=local_payload,
            explanation_source=SOURCE_BRIDGE_DATA,
            started_at=started_at,
            upstream_tool_elapsed_ms={},
        )

    real_payload = _build_real_tool_explanation(
        script_dir,
        candidate,
        bridge_options=bridge_options,
    )
    if real_payload is not None:
        _ensure_required_fields(real_payload)
        return _build_metadata_payload(
            payload={
                key: value
                for key, value in real_payload.items()
                if key not in {"_used_upstream_tools", "_tool_errors", "_upstream_tool_elapsed_ms"}
            },
            explanation_source=SOURCE_UPSTREAM_TOOLS,
            started_at=started_at,
            used_upstream_tools=list(real_payload.get("_used_upstream_tools") or []),
            tool_errors=list(real_payload.get("_tool_errors") or []),
            upstream_tool_elapsed_ms=dict(real_payload.get("_upstream_tool_elapsed_ms") or {}),
        )

    payload = _build_heuristic_explanation(candidate)
    _ensure_required_fields(payload)
    return _build_metadata_payload(
        payload=payload,
        explanation_source=SOURCE_HEURISTIC_FALLBACK,
        started_at=started_at,
        tool_errors=list(candidate.get("_tool_errors") or []),
        upstream_tool_elapsed_ms=dict(candidate.get("_upstream_tool_elapsed_ms") or {}),
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: python shortline_fingenius_bridge_template.py <request_json> <output_json>",
            file=sys.stderr,
        )
        return 1

    request_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = _load_request(request_path)
    candidate = payload.get("candidate") or {}
    if not candidate:
        raise ValueError("candidate payload is required")
    bridge_options = _normalize_bridge_options(payload)

    script_dir = Path(__file__).resolve().parent
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            _build_explanation(
                script_dir,
                candidate,
                bridge_options=bridge_options,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
