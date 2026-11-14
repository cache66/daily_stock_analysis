"""研究样本身份：回放、事后导入和有前向入池证据的信号分开。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
FORWARD_ADMISSION_RULE = "forward_admission_v1"


def sample_kind(
    hypothesis_key: str, observed_at: Optional[datetime], payload: Mapping[str, Any],
) -> str:
    """只信首次登记证据；forward 是盘后研究观察，不代表按 D0 收盘成交。"""
    if hypothesis_key.startswith("research:signal:"):
        return "replay"
    if not hypothesis_key.startswith("signal:") or observed_at is None:
        return "bootstrap"
    if payload.get("admission_rule") != FORWARD_ADMISSION_RULE or not payload.get("batch_ref"):
        return "bootstrap"
    stamp = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at
    return "forward" if stamp.astimezone(CN_TZ).date().isoformat() == hypothesis_key.rsplit(":", 1)[-1] else "bootstrap"
