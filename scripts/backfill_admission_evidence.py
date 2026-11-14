# -*- coding: utf-8 -*-
"""补录入池准入证据（admission_rule），**只在有外部证据时**使用。

背景：`track_from_board` 早期版本已经执行了三道入池闸门（环境不适配 / 冷却期 /
过热），但 created 事件里只写了 `batch_ref`，没有写 `admission_rule`；结果这些
真实盘后信号被 `research_sample.sample_kind` 判成 `bootstrap`（事后导入），永远
进不了前向统计。

本脚本不推断、不猜测：必须由调用方显式给出 `--evidence`（日志行号 / 命令输出 /
人工核对记录），且只处理**同时满足**以下条件的 track：

1. hypothesis_key 形如 `signal:<策略>:<日期>`；
2. created 事件 payload 里已有 `batch_ref`（批次可追溯）；
3. created 事件 payload 里没有 `admission_rule`（确实是漏记，而不是另一种口径）；
4. 该 track 当天确有闸门执行记录（由 `--evidence` 人工背书）。

默认 dry-run，只有加 `--apply` 才写库。写入的是 payload 补录，不改事件类型，
并保留 `admission_backfill` 字段记录证据来源。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.research_sample import FORWARD_ADMISSION_RULE  # noqa: E402
from src.services.research_tracking_service import ResearchTrackingService  # noqa: E402
from src.storage import DatabaseManager, utc_naive_now  # noqa: E402


def _targets(service: ResearchTrackingService, signal_date: str) -> List[Dict[str, Any]]:
    rows = service.repo.list_tracks(limit=100000)
    out: List[Dict[str, Any]] = []
    suffix = f":{signal_date}"
    for track in rows:
        key = track.hypothesis_key or ""
        if not key.startswith("signal:") or not key.endswith(suffix):
            continue
        initial = service.repo.initial_event(track.id)
        if initial is None:
            continue
        payload = json.loads(initial.payload_json or "{}")
        out.append(
            {
                "track_id": track.id,
                "key": key,
                "event_id": initial.id,
                "batch_ref": payload.get("batch_ref"),
                "admission_rule": payload.get("admission_rule"),
                "payload": payload,
            }
        )
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补录入池准入证据（需外部证据）")
    parser.add_argument("--signal-date", required=True, help="信号日 YYYY-MM-DD")
    parser.add_argument("--evidence", required=True, help="证据来源，如日志文件:行号 + 摘要")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = parser.parse_args(argv)

    DatabaseManager.reset_instance()
    service = ResearchTrackingService()
    targets = _targets(service, args.signal_date)

    skipped_missing_ref = [t["key"] for t in targets if not t["batch_ref"]]
    skipped_has_rule = [
        t["key"]
        for t in targets
        if t["admission_rule"] and t["admission_rule"] != FORWARD_ADMISSION_RULE
    ]
    pending = [
        t
        for t in targets
        if t["batch_ref"] and not t["admission_rule"]
    ]

    print(
        f"[admission_backfill] date={args.signal_date} total={len(targets)} "
        f"pending={len(pending)} already={len(targets) - len(pending) - len(skipped_missing_ref) - len(skipped_has_rule)} "
        f"skipped_no_batch_ref={len(skipped_missing_ref)} "
        f"skipped_other_rule={len(skipped_has_rule)}"
    )
    if not pending:
        return 0

    if not args.apply:
        for t in pending:
            print(f"  would patch {t['key']} (event #{t['event_id']}, batch_ref={t['batch_ref'][:12]}…)")
        print("dry-run：加 --apply 才会写库")
        return 0

    stamp = utc_naive_now().isoformat()
    patched = 0
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        from sqlalchemy import text

        for t in pending:
            payload = dict(t["payload"])
            payload["admission_rule"] = FORWARD_ADMISSION_RULE
            payload["admission_backfill"] = {
                "evidence": args.evidence,
                "at": stamp,
                "note": "闸门已执行但 created 事件漏记 admission_rule；由脚本按证据补录",
            }
            session.execute(
                text("UPDATE research_track_events SET payload_json=:p WHERE id=:i"),
                {"p": json.dumps(payload, ensure_ascii=False), "i": t["event_id"]},
            )
            patched += 1
    print(f"[admission_backfill] patched={patched} evidence={args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
