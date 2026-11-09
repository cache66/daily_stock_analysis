# -*- coding: utf-8 -*-
"""行情质量线索：跳变分级阈值 + 可疑代码清单（单一真源）。

背景（2026-11-06 复审）：跳变校验降级为“仅告警不拒写”后，污染序列仍能进入
`stock_daily`，而当时只有总览和结果回填读可疑清单，重建脚本自身也不留痕。
本模块把三件事收敛到一处，供重建脚本、总览、结果回填共用：

1. **复核阈值**：按板块与生效日期给出（主板 11.5%、创业板/科创板 21.5%、北交所 31.5%）。
2. **极端跳变**：复核阈值的 ``EXTREME_MULTIPLIER`` 倍，且前一交易日紧邻、不在上市
   初期窗口内时才判为污染（可解释的阶段跳变不能据此拒写）。
3. **可疑清单**：追加即落盘（原子替换）+ 审计事件流水，写入方不再等到整批结束。

判读口径：清单是“需复核”线索，不是“已证明错误”；命中后限制用途（不用于跟踪
回填、不在总览当有效信号），复核通过再由下一次重建的 ``replace_suspicious_codes``
清除。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from data_provider.base import is_bse_code, normalize_stock_code

logger = logging.getLogger("quote_quality")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_LOG_ROOT = PROJECT_ROOT / "data" / "run_logs"
SUSPICIOUS_CODES_PATH = RUN_LOG_ROOT / "rebuild_stock_daily_qfq_suspicious_codes.txt"
SUSPICIOUS_EVENTS_PATH = RUN_LOG_ROOT / "rebuild_stock_daily_qfq_suspicious_events.jsonl"

# 复核阈值（不是错误证明，只是“值得人工看一眼”的下限）
MAIN_BOARD_REVIEW_PCT = 11.5
GROWTH_BOARD_REVIEW_PCT = 21.5
BSE_REVIEW_PCT = 31.5
# 极端跳变 = 复核阈值 × 2；主板 23%、创业板/科创板 43%、北交所 63%
EXTREME_MULTIPLIER = 2.0
# 前一交易日紧邻判定：<=4 个自然日覆盖周末，再长就可能是停牌/缺行情，不能只凭跳变定罪
ADJACENT_GAP_DAYS = 4
# 上市初期窗口（新股前 5 个交易日不设涨跌幅），按返回序列前若干根判定
LISTING_GRACE_BARS = 6

LEVEL_REVIEW = "review"
LEVEL_EXTREME = "extreme"

_DIGITS = re.compile(r"\D+")


def normalize_code(code: Any) -> str:
    """归一化为 6 位数字代码；非 A 股形态原样返回（不参与阈值判断）。"""
    text = str(code or "").strip()
    if not text:
        return ""
    try:
        normalized = normalize_stock_code(text)
    except Exception:  # noqa: BLE001 - 归一化失败不影响清单读写
        normalized = text
    digits = _DIGITS.sub("", str(normalized))
    return digits if len(digits) == 6 else str(normalized)


def price_jump_review_threshold(code: str, trading_date: str) -> Optional[float]:
    """常规交易的复核线索阈值；无涨跌幅限制的历史阶段返回 None（不判）。"""
    code = normalize_code(code)
    day = str(trading_date or "")[:10]
    if is_bse_code(code):
        return BSE_REVIEW_PCT if day >= "2021-11-15" else None
    if code.startswith("688"):
        return GROWTH_BOARD_REVIEW_PCT if day >= "2019-07-22" else None
    if code.startswith(("300", "301")):
        return GROWTH_BOARD_REVIEW_PCT if day >= "2020-08-24" else MAIN_BOARD_REVIEW_PCT
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return MAIN_BOARD_REVIEW_PCT
    return None


def price_jump_extreme_threshold(code: str, trading_date: str) -> Optional[float]:
    """极端跳变阈值（复核阈值 × 倍数）；None 表示该阶段不判。"""
    review = price_jump_review_threshold(code, trading_date)
    return None if review is None else round(review * EXTREME_MULTIPLIER, 2)


@dataclass(frozen=True)
class JumpHit:
    """单条跳变线索。"""

    code: str
    date: str
    prev_date: str
    gap_days: Optional[int]
    change_pct: float
    threshold_pct: float
    level: str

    def describe(self) -> str:
        gap = "未知" if self.gap_days is None else f"{self.gap_days}天"
        return (
            f"{self.date} {self.change_pct:+.1f}%"
            f"（阈值 {self.threshold_pct:.1f}%，前值日 {self.prev_date}，间隔 {gap}）"
        )


def _gap_days(prev_date: str, current_date: str) -> Optional[int]:
    try:
        return (datetime.fromisoformat(current_date).date() - datetime.fromisoformat(prev_date).date()).days
    except (TypeError, ValueError):
        return None


def classify_price_jumps(
    dates: Sequence[Any], closes: Sequence[Any], code: str
) -> Dict[str, List[JumpHit]]:
    """按板块/日期阈值给跳变分级。

    返回 ``{"review": [...], "extreme": [...]}``：
    - ``review``：超复核阈值，登记待复核（不拒写）；
    - ``extreme``：超极端阈值且前一交易日紧邻、不在上市初期窗口内——常规交易阶段
      无法解释，属污染候选（调用方可据此拒写）。
    """
    hits: Dict[str, List[JumpHit]] = {LEVEL_REVIEW: [], LEVEL_EXTREME: []}
    pairs: List[tuple[str, float]] = []
    for day, close in zip(dates, closes):
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        pairs.append((str(day)[:10], value))

    previous: Optional[tuple[str, float]] = None
    for position, (day, close) in enumerate(pairs, start=1):
        if previous is None:
            previous = (day, close)
            continue
        prev_date, prev_close = previous
        change = (close - prev_close) * 100.0 / prev_close
        threshold = price_jump_review_threshold(code, day)
        if threshold is not None and abs(change) > threshold:
            gap = _gap_days(prev_date, day)
            extreme_limit = round(threshold * EXTREME_MULTIPLIER, 2)
            # 上市初期或行情缺失（停牌/断档/日期不可解析）都不能只靠跳变定罪
            unexplained_stage = position < LISTING_GRACE_BARS or gap is None or gap > ADJACENT_GAP_DAYS
            level = LEVEL_EXTREME if abs(change) >= extreme_limit and not unexplained_stage else LEVEL_REVIEW
            hits[level].append(
                JumpHit(
                    code=normalize_code(code),
                    date=day,
                    prev_date=prev_date,
                    gap_days=gap,
                    change_pct=round(change, 2),
                    threshold_pct=float(threshold),
                    level=level,
                )
            )
        previous = (day, close)
    return hits


# ---------------------------------------------------------------- 可疑清单读写
def load_suspicious_codes(path: Optional[Path] = None) -> Set[str]:
    """读取可疑代码清单（去重后的 6 位代码）；不存在或不可读时返回空集合。"""
    target = Path(path) if path else SUSPICIOUS_CODES_PATH
    if not target.is_file():
        return set()
    try:
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return set()
    codes: Set[str] = set()
    for line in lines:
        code = normalize_code(line)
        if len(code) == 6 and code.isdigit():
            codes.add(code)
    return codes


def _write_codes(target: Path, codes: Iterable[str]) -> None:
    """原子替换清单文件（tmp + os.replace），中途被 kill 也不会留下半截文件。"""
    ordered = sorted({code for code in codes if code})
    target.parent.mkdir(parents=True, exist_ok=True)
    if not ordered:
        if target.exists():
            target.unlink()
        return
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("\n".join(ordered) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _append_event(payload: Dict[str, Any], *, target: Optional[Path] = None) -> None:
    """追加审计流水；失败只告警，不影响主流程（清单本体已落盘）。"""
    path = Path(target) if target else SUSPICIOUS_EVENTS_PATH
    record = {"at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), **payload}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("可疑清单审计流水写入失败: %s", exc)


def record_suspicious_codes(
    codes: Iterable[Any],
    *,
    reason: str,
    source: str,
    as_of: Optional[str] = None,
    path: Optional[Path] = None,
    events_path: Optional[Path] = None,
) -> List[str]:
    """把新发现的待复核代码并入清单（立即落盘）；返回本次新增的代码。"""
    target = Path(path) if path else SUSPICIOUS_CODES_PATH
    incoming = {normalize_code(code) for code in codes}
    incoming = {code for code in incoming if len(code) == 6 and code.isdigit()}
    if not incoming:
        return []
    existing = load_suspicious_codes(target)
    added = sorted(incoming - existing)
    if not added:
        return []
    _write_codes(target, existing | incoming)
    _append_event(
        {
            "action": "merge",
            "reason": reason,
            "source": source,
            "as_of": as_of,
            "added": added,
            "total": len(existing | incoming),
        },
        target=events_path,
    )
    logger.warning("可疑行情登记 %s 只（%s）：%s", len(added), reason, ",".join(added[:10]))
    return added


def replace_suspicious_codes(
    codes: Iterable[Any],
    *,
    reason: str,
    source: str,
    as_of: Optional[str] = None,
    path: Optional[Path] = None,
    events_path: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """用权威扫描结果整体替换清单；返回 ``{"added", "removed"}``。"""
    target = Path(path) if path else SUSPICIOUS_CODES_PATH
    incoming = {normalize_code(code) for code in codes}
    incoming = {code for code in incoming if len(code) == 6 and code.isdigit()}
    existing = load_suspicious_codes(target)
    added = sorted(incoming - existing)
    removed = sorted(existing - incoming)
    _write_codes(target, incoming)
    _append_event(
        {
            "action": "replace",
            "reason": reason,
            "source": source,
            "as_of": as_of,
            "added": added,
            "removed": removed,
            "total": len(incoming),
        },
        target=events_path,
    )
    return {"added": added, "removed": removed}
