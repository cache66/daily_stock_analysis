# -*- coding: utf-8 -*-
"""旧五信号归集（非默认每日）：只把已有信号快照按方法视图列出来，不新增确认规则。

用途：让旧信号在方法工作区里仍然可见、可手动查看，但**不再默认入池**，
避免它们与新的短线方法混成一份候选清单。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.shortline import base


def discover(as_of: str, method: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = method.get("params") or {}
    signal_types = [str(s) for s in (params.get("signal_types") or [])]
    if not signal_types:
        return []

    from src.storage import DatabaseManager

    db = DatabaseManager.get_instance()
    qmarks = ",".join(f":t{i}" for i in range(len(signal_types)))
    sql = (
        f"SELECT signal_type, code, name, signal_date FROM kline_signal_snapshot "
        f"WHERE signal_date = :as_of AND signal_type IN ({qmarks}) "
        f"ORDER BY signal_type, code"
    )
    params_sql = {"as_of": as_of, **{f"t{i}": v for i, v in enumerate(signal_types)}}
    with db.session_scope() as session:
        from sqlalchemy import text

        try:
            rows = session.execute(text(sql), params_sql).fetchall()
        except Exception:  # noqa: BLE001 - 表不存在/字段差异时方法返回空而不是崩
            return []

    table = base.metrics(as_of)
    picks: List[Dict[str, Any]] = []
    seen = set()
    for signal_type, code, name, _signal_date in rows:
        code = str(code).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        row = table.loc[code] if code in table.index else None
        why = [
            f"来源信号：{signal_type}",
            "该信号只从默认每日下线，脚本与样本保留，可手动单跑",
            "本方法不新增确认规则，是否可用沿用原信号定义",
        ]
        if row is not None:
            candidate = base.build_candidate(code, row, as_of=as_of, why=why, method_id=method["id"])
        else:
            candidate = {
                "code": code,
                "name": str(name or ""),
                "industry": (base.industry_map(as_of).get(code) or {}).get("industry") or "",
                "method": method["id"],
                "close": None,
                "why": why + ["库内缺该票行情，价格相关字段留空"],
            }
        candidate["source_signal"] = signal_type
        picks.append(candidate)

    return picks[: int(params.get("max_picks", 20))]
