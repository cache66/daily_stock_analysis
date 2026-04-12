#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan A-shares for rule-based earnings surprise proxy events."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.fundamental_adapter import AkshareFundamentalAdapter
from src.services.kline_selector_service import KlineSelectorService
from src.storage import DatabaseManager


logger = logging.getLogger("earnings_surprise_selector")

SIGNAL_TYPE = "earnings_surprise"
DEFAULT_HISTORY_LOOKBACK_DAYS = 365
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data"
DEFAULT_POSITIVE_TEXT_KEYWORDS: Sequence[str] = (
    "预增",
    "扭亏",
    "增长",
    "大增",
    "高增",
    "向好",
    "超预期",
    "improve",
    "improved",
    "beat",
    "beats",
    "better than expected",
    "better-than-expected",
    "strong earnings",
)
DEFAULT_NEGATIVE_TEXT_KEYWORDS: Sequence[str] = (
    "预减",
    "预亏",
    "首亏",
    "续亏",
    "转亏",
    "下滑",
    "下降",
    "亏损",
    "不及预期",
    "miss",
    "missed",
    "below expectation",
    "below expectations",
    "warning",
)


@dataclass
class EarningsSurpriseCriteria:
    """Rule set for the earnings surprise proxy scan."""

    min_revenue_yoy: Optional[float] = 10.0
    min_net_profit_yoy: Optional[float] = 20.0
    min_roe: Optional[float] = None
    require_positive_text: bool = False
    require_growth_thresholds: bool = False
    max_total_market_cap: Optional[float] = None
    dedupe_by_event_key: bool = True
    positive_text_keywords: List[str] = field(
        default_factory=lambda: list(DEFAULT_POSITIVE_TEXT_KEYWORDS)
    )
    negative_text_keywords: List[str] = field(
        default_factory=lambda: list(DEFAULT_NEGATIVE_TEXT_KEYWORDS)
    )

    def __post_init__(self) -> None:
        for field_name in ("min_revenue_yoy", "min_net_profit_yoy", "min_roe", "max_total_market_cap"):
            value = getattr(self, field_name)
            if value is None:
                continue
            numeric = float(value)
            if field_name == "max_total_market_cap" and numeric <= 0:
                raise ValueError("max_total_market_cap must be > 0 when provided")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EarningsSurpriseEvaluation:
    """Evaluation result for one stock."""

    stock_code: str
    stock_name: str
    passed: bool
    total_market_cap: Optional[float] = None
    history_source: str = ""
    failure_reason: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        metrics = self.metrics or {}
        return {
            "code": self.stock_code,
            "name": self.stock_name,
            "passed": self.passed,
            "total_market_cap": self.total_market_cap,
            "total_market_cap_yi": (
                round(float(self.total_market_cap) / 1e8, 2)
                if self.total_market_cap is not None
                else None
            ),
            "close": metrics.get("close"),
            "event_date": metrics.get("event_date"),
            "forecast_announcement_date": metrics.get("forecast_announcement_date"),
            "quick_report_announcement_date": metrics.get("quick_report_announcement_date"),
            "report_date": metrics.get("report_date"),
            "revenue_yoy": metrics.get("revenue_yoy"),
            "net_profit_yoy": metrics.get("net_profit_yoy"),
            "roe": metrics.get("roe"),
            "forecast_summary": metrics.get("forecast_summary"),
            "quick_report_summary": metrics.get("quick_report_summary"),
            "positive_text_signal": metrics.get("positive_text_signal"),
            "growth_signal": metrics.get("growth_signal"),
            "signal_score": metrics.get("signal_score"),
            "event_key": metrics.get("event_key"),
            "reason_summary": metrics.get("reason_summary"),
            "latest_previous_hit_date": metrics.get("latest_previous_hit_date"),
            "previous_hit_count": metrics.get("previous_hit_count"),
            "days_since_previous_hit": metrics.get("days_since_previous_hit"),
            "history_source": self.history_source,
            "failure_reason": self.failure_reason,
        }


@dataclass
class EarningsSurpriseRunResult:
    """Aggregated scan result."""

    criteria: EarningsSurpriseCriteria
    universe_size: int
    evaluated_count: int
    selected: List[EarningsSurpriseEvaluation] = field(default_factory=list)
    failed: List[EarningsSurpriseEvaluation] = field(default_factory=list)
    skipped_market_cap_count: int = 0
    skipped_duplicate_event_count: int = 0
    universe_codes: List[str] = field(default_factory=list)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    return _safe_text(value).replace("\n", " ").replace("\r", " ").lower()


def parse_snapshot_date(value: Optional[Any]) -> date:
    text = _safe_text(value)
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid snapshot date: {text}") from exc


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描 A 股财报强势代理信号，并落库为 earnings_surprise 快照。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只股票。")
    parser.add_argument("--snapshot-date", default=None, help="信号日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument(
        "--history-lookback-days",
        type=int,
        default=DEFAULT_HISTORY_LOOKBACK_DAYS,
        help=f"历史回看窗口，默认 {DEFAULT_HISTORY_LOOKBACK_DAYS} 天。",
    )
    parser.add_argument(
        "--min-revenue-yoy",
        type=float,
        default=10.0,
        help="营收同比阈值，默认 10。",
    )
    parser.add_argument(
        "--min-net-profit-yoy",
        type=float,
        default=20.0,
        help="归母净利润同比阈值，默认 20。",
    )
    parser.add_argument(
        "--min-roe",
        type=float,
        default=None,
        help="可选 ROE 阈值，不设则忽略。",
    )
    parser.add_argument(
        "--require-positive-text",
        action="store_true",
        help="要求业绩预告/快报文本出现正向关键词。",
    )
    parser.add_argument(
        "--require-growth-thresholds",
        action="store_true",
        help="要求同比增速达到阈值。",
    )
    parser.add_argument(
        "--max-total-mv-yi",
        type=float,
        default=None,
        help="可选总市值上限，单位亿。",
    )
    parser.add_argument(
        "--disable-event-dedupe",
        action="store_true",
        help="关闭按 event_key 去重；默认同一季度/同一文本事件只记录一次。",
    )
    parser.add_argument(
        "--skip-db-persist",
        action="store_true",
        help="跳过数据库写入，仅导出文件。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="并发 worker 数，默认 1。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    return parser.parse_args()


def build_criteria_payload(
    criteria: EarningsSurpriseCriteria,
    *,
    signal_type: str,
    snapshot_date: date,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "snapshot_date": snapshot_date.isoformat(),
        "criteria": criteria.to_dict(),
    }


def build_history_payload(
    db: DatabaseManager,
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    lookback_days: int,
) -> Dict[str, Any]:
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    recent_hit_dates = [row.signal_date.isoformat() for row in history_rows if row.signal_date]
    latest_previous_hit_date = recent_hit_dates[0] if recent_hit_dates else None
    days_since_previous_hit = None
    if latest_previous_hit_date is not None:
        days_since_previous_hit = (snapshot_date - date.fromisoformat(latest_previous_hit_date)).days
    return {
        "lookback_days": lookback_days,
        "previous_hit_count": len(recent_hit_dates),
        "latest_previous_hit_date": latest_previous_hit_date,
        "days_since_previous_hit": days_since_previous_hit,
        "recent_hit_dates": recent_hit_dates,
    }


def build_event_key(
    *,
    event_date: Optional[str],
    report_date: Optional[str],
    forecast_summary: str,
    quick_report_summary: str,
    revenue_yoy: Optional[float],
    net_profit_yoy: Optional[float],
) -> str:
    event_date_text = _safe_text(event_date)
    if event_date_text:
        return f"event_date:{event_date_text}"
    report_date_text = _safe_text(report_date)
    if report_date_text:
        return f"report_date:{report_date_text}"

    summary_blob = " | ".join(
        text for text in (_safe_text(forecast_summary), _safe_text(quick_report_summary)) if text
    )
    if summary_blob:
        digest = hashlib.sha1(summary_blob.encode("utf-8")).hexdigest()[:16]
        return f"text:{digest}"

    numeric_blob = json.dumps(
        {
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(numeric_blob.encode("utf-8")).hexdigest()[:16]
    return f"numeric:{digest}"


def _keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    normalized_text = _normalize_text(text)
    hits: List[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            hits.append(_safe_text(keyword))
    return hits


def _same_event_already_recorded(
    db: Optional[DatabaseManager],
    *,
    signal_type: str,
    stock_code: str,
    snapshot_date: date,
    event_key: str,
    lookback_days: int,
) -> bool:
    if db is None or not event_key:
        return False
    history_rows = db.get_recent_signal_history(
        signal_type=signal_type,
        code=stock_code,
        days=lookback_days,
        before_date=snapshot_date,
    )
    for row in history_rows:
        try:
            metrics_payload = json.loads(row.metrics_payload or "{}")
        except Exception:
            metrics_payload = {}
        if str(metrics_payload.get("event_key") or "").strip() == event_key:
            return True
    return False


def _format_optional_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def evaluate_earnings_surprise_candidate(
    *,
    stock_code: str,
    stock_name: str,
    bundle_payload: Dict[str, Any],
    criteria: EarningsSurpriseCriteria,
    total_market_cap: Optional[float],
    latest_price: Optional[float],
    snapshot_date: date,
    signal_type: str = SIGNAL_TYPE,
    db: Optional[DatabaseManager] = None,
    history_lookback_days: int = DEFAULT_HISTORY_LOOKBACK_DAYS,
) -> EarningsSurpriseEvaluation:
    if criteria.max_total_market_cap is not None and total_market_cap is not None:
        if float(total_market_cap) > float(criteria.max_total_market_cap):
            return EarningsSurpriseEvaluation(
                stock_code=stock_code,
                stock_name=stock_name,
                passed=False,
                total_market_cap=total_market_cap,
                failure_reason="market cap exceeds configured ceiling",
            )

    growth_payload = bundle_payload.get("growth") if isinstance(bundle_payload.get("growth"), dict) else {}
    earnings_payload = bundle_payload.get("earnings") if isinstance(bundle_payload.get("earnings"), dict) else {}
    financial_report = (
        earnings_payload.get("financial_report")
        if isinstance(earnings_payload.get("financial_report"), dict)
        else {}
    )
    report_date = _safe_text(financial_report.get("report_date"))
    forecast_announcement_date = _safe_text(earnings_payload.get("forecast_announcement_date"))
    quick_report_announcement_date = _safe_text(earnings_payload.get("quick_report_announcement_date"))
    event_date = quick_report_announcement_date or forecast_announcement_date or report_date
    forecast_summary = _safe_text(earnings_payload.get("forecast_summary"))
    quick_report_summary = _safe_text(earnings_payload.get("quick_report_summary"))

    revenue_yoy = _safe_float(growth_payload.get("revenue_yoy"))
    net_profit_yoy = _safe_float(growth_payload.get("net_profit_yoy"))
    roe = _safe_float(growth_payload.get("roe"))
    if roe is None:
        roe = _safe_float(financial_report.get("roe"))

    positive_hits = _keyword_hits(
        " ".join(text for text in (forecast_summary, quick_report_summary) if text),
        criteria.positive_text_keywords,
    )
    negative_hits = _keyword_hits(
        " ".join(text for text in (forecast_summary, quick_report_summary) if text),
        criteria.negative_text_keywords,
    )
    positive_text_signal = bool(positive_hits)
    negative_text_signal = bool(negative_hits)

    growth_checks: List[bool] = []
    met_growth_items: List[str] = []
    if criteria.min_revenue_yoy is not None:
        revenue_passed = revenue_yoy is not None and revenue_yoy >= float(criteria.min_revenue_yoy)
        growth_checks.append(revenue_passed)
        if revenue_passed:
            met_growth_items.append(f"营收同比 {_format_optional_pct(revenue_yoy)}")
    if criteria.min_net_profit_yoy is not None:
        profit_passed = net_profit_yoy is not None and net_profit_yoy >= float(criteria.min_net_profit_yoy)
        growth_checks.append(profit_passed)
        if profit_passed:
            met_growth_items.append(f"净利润同比 {_format_optional_pct(net_profit_yoy)}")
    if criteria.min_roe is not None:
        roe_passed = roe is not None and roe >= float(criteria.min_roe)
        growth_checks.append(roe_passed)
        if roe_passed:
            met_growth_items.append(f"ROE {_format_optional_pct(roe)}")

    growth_signal = bool(growth_checks) and all(growth_checks)
    signal_score = 0
    if positive_text_signal:
        signal_score += 2
    if revenue_yoy is not None and criteria.min_revenue_yoy is not None and revenue_yoy >= criteria.min_revenue_yoy:
        signal_score += 1
    if (
        net_profit_yoy is not None
        and criteria.min_net_profit_yoy is not None
        and net_profit_yoy >= criteria.min_net_profit_yoy
    ):
        signal_score += 2
    if roe is not None and criteria.min_roe is not None and roe >= criteria.min_roe:
        signal_score += 1

    event_key = build_event_key(
        event_date=event_date or None,
        report_date=report_date or None,
        forecast_summary=forecast_summary,
        quick_report_summary=quick_report_summary,
        revenue_yoy=revenue_yoy,
        net_profit_yoy=net_profit_yoy,
    )

    failure_reason = ""
    passed = False
    if negative_text_signal:
        failure_reason = "negative earnings text detected"
    elif criteria.require_positive_text and not positive_text_signal:
        failure_reason = "missing positive earnings text signal"
    elif criteria.require_growth_thresholds and not growth_signal:
        failure_reason = "growth thresholds not met"
    else:
        passed = positive_text_signal or growth_signal
        if not passed:
            failure_reason = "no positive earnings text or growth threshold signal"

    duplicate_event = False
    if passed and criteria.dedupe_by_event_key:
        duplicate_event = _same_event_already_recorded(
            db,
            signal_type=signal_type,
            stock_code=stock_code,
            snapshot_date=snapshot_date,
            event_key=event_key,
            lookback_days=history_lookback_days,
        )
        if duplicate_event:
            passed = False
            failure_reason = "same event key already recorded in history"

    reason_parts: List[str] = []
    if positive_text_signal:
        reason_parts.append(f"文本命中正向业绩关键词：{'/'.join(positive_hits)}")
    if growth_signal and met_growth_items:
        reason_parts.append("增长指标命中：" + "、".join(met_growth_items))
    if event_date:
        reason_parts.append(f"事件日期：{event_date}")
    elif report_date:
        reason_parts.append(f"报告期：{report_date}")

    quant_parts: List[str] = []
    if revenue_yoy is not None:
        quant_parts.append(f"营收同比 {_format_optional_pct(revenue_yoy)}")
    if net_profit_yoy is not None:
        quant_parts.append(f"净利润同比 {_format_optional_pct(net_profit_yoy)}")
    if roe is not None:
        quant_parts.append(f"ROE {_format_optional_pct(roe)}")
    if total_market_cap is not None:
        quant_parts.append(f"总市值 {float(total_market_cap) / 1e8:.2f} 亿")

    reason_summary = "；".join(part for part in reason_parts + quant_parts if part)
    if not reason_summary and failure_reason:
        reason_summary = failure_reason

    history_source = ",".join(bundle_payload.get("source_chain") or [])
    metrics = {
        "signal_date": snapshot_date.isoformat(),
        "close": latest_price,
        "event_date": event_date or None,
        "forecast_announcement_date": forecast_announcement_date or None,
        "quick_report_announcement_date": quick_report_announcement_date or None,
        "report_date": report_date or None,
        "revenue": _safe_float(financial_report.get("revenue")),
        "net_profit_parent": _safe_float(financial_report.get("net_profit_parent")),
        "operating_cash_flow": _safe_float(financial_report.get("operating_cash_flow")),
        "revenue_yoy": revenue_yoy,
        "net_profit_yoy": net_profit_yoy,
        "roe": roe,
        "forecast_summary": forecast_summary,
        "quick_report_summary": quick_report_summary,
        "positive_keyword_hits": positive_hits,
        "negative_keyword_hits": negative_hits,
        "positive_text_signal": positive_text_signal,
        "negative_text_signal": negative_text_signal,
        "growth_signal": growth_signal,
        "signal_score": signal_score,
        "event_key": event_key,
        "duplicate_event": duplicate_event,
        "reason_summary": reason_summary,
    }
    return EarningsSurpriseEvaluation(
        stock_code=stock_code,
        stock_name=stock_name,
        passed=passed,
        total_market_cap=total_market_cap,
        history_source=history_source,
        failure_reason=failure_reason,
        metrics=metrics,
    )


def build_selected_dataframe(selected: List[EarningsSurpriseEvaluation]) -> pd.DataFrame:
    selected_df = pd.DataFrame([item.to_record() for item in selected])
    if selected_df.empty:
        return selected_df
    return selected_df.sort_values(
        by=["signal_score", "net_profit_yoy", "revenue_yoy", "total_market_cap_yi", "code"],
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)


def build_cause_payload(evaluation: EarningsSurpriseEvaluation) -> Dict[str, Any]:
    metrics = evaluation.metrics or {}
    positive_hits = metrics.get("positive_keyword_hits") or []
    negative_hits = metrics.get("negative_keyword_hits") or []
    text_logic_parts = []
    if positive_hits:
        text_logic_parts.append("正向文本关键词：" + "/".join(positive_hits))
    if negative_hits:
        text_logic_parts.append("负向文本关键词：" + "/".join(negative_hits))
    if metrics.get("forecast_summary"):
        text_logic_parts.append(f"预告摘要：{metrics['forecast_summary']}")
    if metrics.get("quick_report_summary"):
        text_logic_parts.append(f"快报摘要：{metrics['quick_report_summary']}")

    quant_logic_parts = []
    for label, key in (
        ("营收同比", "revenue_yoy"),
        ("净利润同比", "net_profit_yoy"),
        ("ROE", "roe"),
    ):
        value = metrics.get(key)
        if value is not None:
            quant_logic_parts.append(f"{label} {_format_optional_pct(float(value))}")
    if metrics.get("event_date"):
        quant_logic_parts.append(f"事件日期 {metrics['event_date']}")
    elif metrics.get("report_date"):
        quant_logic_parts.append(f"报告期 {metrics['report_date']}")

    return {
        "analysis_status": "rule_based",
        "industry": "",
        "reason_summary": metrics.get("reason_summary", ""),
        "industry_logic": "",
        "news_logic": "；".join(text_logic_parts),
        "technical_logic": "；".join(quant_logic_parts),
        "cause_tags": ["earnings"],
        "theme_label": "",
    }


def persist_selected_evaluations(
    selected: List[EarningsSurpriseEvaluation],
    *,
    signal_type: str,
    snapshot_date: date,
    criteria_payload: Dict[str, Any],
    history_lookback_days: int,
    db: DatabaseManager,
) -> pd.DataFrame:
    if not selected:
        return pd.DataFrame()

    persisted_rows: List[Dict[str, Any]] = []
    for evaluation in selected:
        history_payload = build_history_payload(
            db,
            signal_type=signal_type,
            stock_code=evaluation.stock_code,
            snapshot_date=snapshot_date,
            lookback_days=history_lookback_days,
        )
        metrics_payload = dict(evaluation.metrics or {})
        metrics_payload["history_source"] = evaluation.history_source
        metrics_payload["total_market_cap"] = evaluation.total_market_cap
        cause_payload = build_cause_payload(evaluation)
        db.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date=snapshot_date,
            code=evaluation.stock_code,
            name=evaluation.stock_name,
            criteria_payload=criteria_payload,
            metrics_payload=metrics_payload,
            cause_payload=cause_payload,
            history_payload=history_payload,
        )
        row = evaluation.to_record()
        row["latest_previous_hit_date"] = history_payload.get("latest_previous_hit_date")
        row["previous_hit_count"] = history_payload.get("previous_hit_count", 0)
        row["days_since_previous_hit"] = history_payload.get("days_since_previous_hit")
        persisted_rows.append(row)
    return pd.DataFrame(persisted_rows)


def build_markdown_report(
    run_result: EarningsSurpriseRunResult,
    selected_df: pd.DataFrame,
    generated_at: str,
    *,
    snapshot_date: date,
    signal_type: str,
) -> str:
    criteria = run_result.criteria
    lines = [
        "# 业绩超预期代理信号结果",
        "",
        f"- 生成时间: {generated_at}",
        f"- 信号日期: {snapshot_date.isoformat()}",
        f"- Signal Type: {signal_type}",
        f"- A 股样本数（排除北交所）: {run_result.universe_size}",
        f"- 实际评估数: {run_result.evaluated_count}",
        f"- 因市值过滤跳过: {run_result.skipped_market_cap_count}",
        f"- 因重复事件跳过: {run_result.skipped_duplicate_event_count}",
        f"- 命中数量: {len(run_result.selected)}",
        "",
        "## 当前规则",
        "",
        f"- 营收同比阈值: {criteria.min_revenue_yoy if criteria.min_revenue_yoy is not None else '未启用'}",
        f"- 净利润同比阈值: {criteria.min_net_profit_yoy if criteria.min_net_profit_yoy is not None else '未启用'}",
        f"- ROE 阈值: {criteria.min_roe if criteria.min_roe is not None else '未启用'}",
        f"- 需要正向文本: {'是' if criteria.require_positive_text else '否'}",
        f"- 需要增长阈值: {'是' if criteria.require_growth_thresholds else '否'}",
        f"- 按事件去重: {'是' if criteria.dedupe_by_event_key else '否'}",
        "",
    ]
    if selected_df.empty:
        lines.extend(["## 命中结果", "", "本次没有找到满足条件的股票。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## 命中结果",
            "",
            "| 代码 | 名称 | 总市值(亿) | 事件日期 | 报告期 | 营收同比 | 净利润同比 | ROE | Signal Score | 结果摘要 | 上次命中 | 历史次数 |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
        ]
    )
    for row in selected_df.itertuples(index=False):
        lines.append(
            "| {code} | {name} | {mv} | {event_date} | {report_date} | {revenue_yoy} | {profit_yoy} | {roe} | {score} | {summary} | {prev_date} | {prev_count} |".format(
                code=row.code,
                name=row.name,
                mv=f"{row.total_market_cap_yi:.2f}" if pd.notna(row.total_market_cap_yi) else "--",
                event_date=getattr(row, "event_date", None) or "--",
                report_date=row.report_date or "--",
                revenue_yoy=_format_optional_pct(row.revenue_yoy) if pd.notna(row.revenue_yoy) else "--",
                profit_yoy=_format_optional_pct(row.net_profit_yoy) if pd.notna(row.net_profit_yoy) else "--",
                roe=_format_optional_pct(row.roe) if pd.notna(row.roe) else "--",
                score=row.signal_score if pd.notna(row.signal_score) else "--",
                summary=str(row.reason_summary or "").replace("\n", " "),
                prev_date=row.latest_previous_hit_date or "--",
                prev_count=int(row.previous_hit_count or 0),
            )
        )
    lines.append("")
    return "\n".join(lines)


def export_results(
    run_result: EarningsSurpriseRunResult,
    selected_df: pd.DataFrame,
    *,
    output_dir: Path,
    snapshot_date: date,
    signal_type: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    universe_path = output_dir / "a_share_universe_no_bse.txt"
    universe_path.write_text(
        ("\n".join(run_result.universe_codes) + "\n") if run_result.universe_codes else "",
        encoding="utf-8",
    )

    csv_path = output_dir / "earnings_surprise_candidates.csv"
    txt_path = output_dir / "earnings_surprise_candidates.txt"
    md_path = output_dir / "earnings_surprise_candidates.md"

    selected_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(
        ("\n".join(selected_df["code"].tolist()) + "\n") if not selected_df.empty else "",
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(
            run_result,
            selected_df,
            generated_at,
            snapshot_date=snapshot_date,
            signal_type=signal_type,
        ),
        encoding="utf-8",
    )

    logger.info("已写出 A 股样本列表: %s", universe_path)
    logger.info("已写出业绩超预期结果 CSV: %s", csv_path)
    logger.info("已写出业绩超预期结果 TXT: %s", txt_path)
    logger.info("已写出业绩超预期结果 Markdown: %s", md_path)


def scan_market(
    *,
    criteria: EarningsSurpriseCriteria,
    snapshot_date: date,
    signal_type: str,
    history_lookback_days: int,
    db: Optional[DatabaseManager],
    limit: Optional[int],
    max_workers: int,
    universe_provider: Optional[Any] = None,
    bundle_loader: Optional[Any] = None,
) -> EarningsSurpriseRunResult:
    service = KlineSelectorService(universe_provider=universe_provider)
    universe = service.get_a_share_universe(limit=limit)
    universe_codes = universe["code"].tolist()
    adapter = AkshareFundamentalAdapter()

    def _load_bundle(code: str) -> Dict[str, Any]:
        if bundle_loader is not None:
            return bundle_loader(code)
        return adapter.get_fundamental_bundle(code)

    selected: List[EarningsSurpriseEvaluation] = []
    failed: List[EarningsSurpriseEvaluation] = []
    skipped_market_cap_count = 0
    skipped_duplicate_event_count = 0
    eligible_rows: List[Dict[str, Any]] = []

    for row in universe.itertuples(index=False):
        total_mv = _safe_float(getattr(row, "total_mv", None))
        if criteria.max_total_market_cap is not None and total_mv is not None:
            if total_mv > float(criteria.max_total_market_cap):
                skipped_market_cap_count += 1
                continue
        eligible_rows.append(
            {
                "code": str(getattr(row, "code")),
                "name": str(getattr(row, "name") or ""),
                "total_market_cap": total_mv,
                "latest_price": _safe_float(getattr(row, "latest_price", None)),
            }
        )

    if max_workers <= 0:
        raise ValueError("max_workers must be > 0")

    def _evaluate(row_payload: Dict[str, Any]) -> EarningsSurpriseEvaluation:
        bundle_payload = _load_bundle(row_payload["code"])
        return evaluate_earnings_surprise_candidate(
            stock_code=row_payload["code"],
            stock_name=row_payload["name"],
            bundle_payload=bundle_payload,
            criteria=criteria,
            total_market_cap=row_payload["total_market_cap"],
            latest_price=row_payload["latest_price"],
            snapshot_date=snapshot_date,
            signal_type=signal_type,
            db=db,
            history_lookback_days=history_lookback_days,
        )

    if max_workers == 1:
        for index, row_payload in enumerate(eligible_rows, start=1):
            evaluation = _evaluate(row_payload)
            if evaluation.passed:
                selected.append(evaluation)
            else:
                if evaluation.metrics.get("duplicate_event"):
                    skipped_duplicate_event_count += 1
                failed.append(evaluation)
            if index % 100 == 0 or index == len(eligible_rows):
                logger.info(
                    "业绩超预期扫描进度: completed=%s/%s, selected=%s, duplicate_skipped=%s",
                    index,
                    len(eligible_rows),
                    len(selected),
                    skipped_duplicate_event_count,
                )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_evaluate, row_payload): row_payload["code"]
                for row_payload in eligible_rows
            }
            completed = 0
            for future in as_completed(future_map):
                completed += 1
                evaluation = future.result()
                if evaluation.passed:
                    selected.append(evaluation)
                else:
                    if evaluation.metrics.get("duplicate_event"):
                        skipped_duplicate_event_count += 1
                    failed.append(evaluation)
                if completed % 100 == 0 or completed == len(eligible_rows):
                    logger.info(
                        "业绩超预期扫描进度: completed=%s/%s, selected=%s, duplicate_skipped=%s",
                        completed,
                        len(eligible_rows),
                        len(selected),
                        skipped_duplicate_event_count,
                    )

    return EarningsSurpriseRunResult(
        criteria=criteria,
        universe_size=len(universe),
        evaluated_count=len(eligible_rows),
        selected=selected,
        failed=failed,
        skipped_market_cap_count=skipped_market_cap_count,
        skipped_duplicate_event_count=skipped_duplicate_event_count,
        universe_codes=universe_codes,
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    snapshot_date = parse_snapshot_date(args.snapshot_date)
    signal_type = SIGNAL_TYPE

    if args.max_workers > 1:
        logger.warning("业绩超预期扫描在当前环境下更建议先用 --max-workers 1 做稳定基线。")

    criteria = EarningsSurpriseCriteria(
        min_revenue_yoy=args.min_revenue_yoy,
        min_net_profit_yoy=args.min_net_profit_yoy,
        min_roe=args.min_roe,
        require_positive_text=args.require_positive_text,
        require_growth_thresholds=args.require_growth_thresholds,
        max_total_market_cap=(args.max_total_mv_yi * 1e8) if args.max_total_mv_yi is not None else None,
        dedupe_by_event_key=not args.disable_event_dedupe,
    )
    db = None if args.skip_db_persist else DatabaseManager.get_instance()
    criteria_payload = build_criteria_payload(
        criteria,
        signal_type=signal_type,
        snapshot_date=snapshot_date,
    )

    logger.info(
        "开始运行业绩超预期代理扫描: snapshot_date=%s, limit=%s, max_workers=%s, history_lookback_days=%s, persist=%s",
        snapshot_date.isoformat(),
        args.limit or "ALL",
        args.max_workers,
        args.history_lookback_days,
        not args.skip_db_persist,
    )
    run_result = scan_market(
        criteria=criteria,
        snapshot_date=snapshot_date,
        signal_type=signal_type,
        history_lookback_days=max(1, int(args.history_lookback_days)),
        db=db,
        limit=args.limit,
        max_workers=args.max_workers,
    )

    if db is not None:
        selected_df = persist_selected_evaluations(
            run_result.selected,
            signal_type=signal_type,
            snapshot_date=snapshot_date,
            criteria_payload=criteria_payload,
            history_lookback_days=max(1, int(args.history_lookback_days)),
            db=db,
        )
    else:
        selected_df = build_selected_dataframe(run_result.selected)
        selected_df["latest_previous_hit_date"] = None
        selected_df["previous_hit_count"] = 0
        selected_df["days_since_previous_hit"] = None

    export_results(
        run_result,
        selected_df,
        output_dir=Path(args.output_dir),
        snapshot_date=snapshot_date,
        signal_type=signal_type,
    )
    logger.info(
        "业绩超预期代理扫描完成: universe=%s, evaluated=%s, selected=%s, skipped_market_cap=%s, duplicate_skipped=%s",
        run_result.universe_size,
        run_result.evaluated_count,
        len(run_result.selected),
        run_result.skipped_market_cap_count,
        run_result.skipped_duplicate_event_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
