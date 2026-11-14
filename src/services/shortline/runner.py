# -*- coding: utf-8 -*-
"""短线方法执行器：发现 → 建跟踪 → 计算当前状态 → 落盘（JSON/Markdown）。

只读行情与披露数据，写两处产物：
- `data/strategy_review/methods/<method>/<date>.json`（当日发现 + 状态）
- `data/strategy_review/methods/index_<date>.json` / `latest.md`（方法区总览）

跟踪复用 `research_tracks`（不新建表），hypothesis_key 形如
`signal:<method>:<date>`，这样 `evaluate_track_outcomes.py` 的次日开盘口径
可以直接回填结果。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.shortline import base, registry
from src.services.shortline.adapters import get_adapter

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = PROJECT_ROOT / "data" / "strategy_review" / "methods"


def _hypothesis_key(method_id: str, as_of: str) -> str:
    return f"signal:{method_id}:{as_of}"


def _open_tracks(method_id: str) -> List[Any]:
    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    rows = service.repo.outcome_tracks(limit=5000, hypothesis_prefix=f"signal:{method_id}:", include_pending=True)
    return [t for t in rows if t.status in ("tracking", "pending")]


def _track_status(table: Any, tracks: List[Any]) -> Dict[str, List[str]]:
    confirming: List[str] = []
    invalid: List[str] = []
    unknown: List[str] = []
    for track in tracks:
        code = str(track.code)
        if code not in table.index:
            unknown.append(code)
            continue
        row = table.loc[code]
        if float(row["close"]) >= float(row["ma20"]) and float(row["excess20_pp"]) >= 0:
            confirming.append(code)
        else:
            invalid.append(code)
    return {"confirming": confirming, "invalid": invalid, "unknown": unknown}


def _create_tracks(method: Dict[str, Any], as_of: str, candidates: List[Dict[str, Any]]) -> List[str]:
    from src.services.research_tracking_service import ResearchTrackingService

    service = ResearchTrackingService()
    review_at = datetime.combine(date.fromisoformat(as_of) + timedelta(days=7), datetime.min.time())
    created: List[str] = []
    for item in candidates:
        track, was_created = service.create_or_get(
            market="cn",
            code=item["code"],
            hypothesis_key=_hypothesis_key(method["id"], as_of),
            title=f"{item['code']} {method['name']}",
            business_layer="review",
            horizon="short",
            branch="measurement_only",  # 价格测量完成后不自动结束研究
            capital_role="watch",
            hypothesis=method.get("question") or "",
            invalidation=method.get("invalid_rule") or "",
            next_review_at=review_at,
            source_type=f"shortline:{method['id']}",
            source_ref=f"{method['id']}:{as_of}:{item['code']}",
            source_version=str(registry.load_config().get("version") or ""),
            quality="complete" if item.get("close") else "partial",
            limitation="；".join(method.get("limitations") or []) or None,
            payload={
                "method_id": method["id"],
                "method_name": method["name"],
                "as_of": as_of,
                "close": item.get("close"),
                "stop": item.get("stop"),
                "excess20_pp": item.get("excess20_pp"),
                "why": item.get("why"),
                "confirm_rule": method.get("confirm_rule"),
                "invalid_rule": method.get("invalid_rule"),
            },
        )
        if was_created:
            try:
                service.change_status(track.track_id, "tracking", reason=f"{method['name']} 发现", expected_revision=track.revision)
            except Exception:  # noqa: BLE001 - 状态推进失败不影响发现结果
                pass
            created.append(item["code"])
    return created


def run_methods(
    as_of: str,
    *,
    method_ids: Optional[List[str]] = None,
    track: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    methods = registry.list_methods(enabled_only=True)
    if method_ids:
        wanted = set(method_ids)
        methods = [m for m in registry.list_methods() if m["id"] in wanted]
    if not methods:
        raise registry.MethodError("没有可执行的方法")

    table = base.metrics(as_of)
    index: Dict[str, Any] = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "registry_version": registry.load_config().get("version"),
        "methods": [],
        "intersections": [],
        "limitations": [
            "热点方法未接入资讯，不解释涨的原因",
            "股东户数为披露低频数据，两次披露之间不产生新样本",
        ],
    }

    discovered_by_method: Dict[str, List[str]] = {}
    for method in methods:
        adapter = get_adapter(method["adapter"])
        candidates = adapter(as_of, method)
        discovered_by_method[method["id"]] = [c["code"] for c in candidates]

        created: List[str] = []
        if track and candidates and not dry_run:
            created = _create_tracks(method, as_of, candidates)

        tracks = _open_tracks(method["id"])
        status = _track_status(table, tracks)

        payload = {
            "as_of": as_of,
            "method": {k: method.get(k) for k in
                       ("id", "name", "question", "horizon", "confirm_rule", "invalid_rule",
                        "producer", "adapter", "params")},
            "excluded_industries": registry.excluded_industries(method),
            "count": len(candidates),
            "new_today": len(created),
            "tracking": len(tracks),
            "confirming": status["confirming"],
            "invalid": status["invalid"],
            "unknown": status["unknown"],
            "candidates": candidates,
        }
        if not dry_run:
            out = OUT_DIR / method["id"]
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{as_of}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            (out / "_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        index["methods"].append(
            {
                "id": method["id"],
                "name": method["name"],
                "enabled": bool(method.get("enabled")),
                "question": method.get("question"),
                "horizon": method.get("horizon"),
                "confirm_rule": method.get("confirm_rule"),
                "invalid_rule": method.get("invalid_rule"),
                "default_daily": bool((method.get("params") or {}).get("default_daily", True)),
                "count": len(candidates),
                "new_today": len(created),
                "tracking": len(tracks),
                "confirming": len(status["confirming"]),
                "invalid": len(status["invalid"]),
                "codes": discovered_by_method[method["id"]],
            }
        )

    for inter in registry.list_intersections():
        if not inter.get("enabled"):
            index["intersections"].append({**{k: inter.get(k) for k in ("id", "name", "methods", "rule")},
                                           "enabled": False, "count": 0, "codes": []})
            continue
        buckets = [set(discovered_by_method.get(m, [])) for m in inter.get("methods") or []]
        codes = sorted(set.intersection(*buckets)) if buckets else []
        index["intersections"].append({**{k: inter.get(k) for k in ("id", "name", "methods", "rule")},
                                       "enabled": True, "count": len(codes), "codes": codes})

    if not dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"index_{as_of}.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / "_index_latest.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / "latest.md").write_text(render_markdown(index), encoding="utf-8")
    return index


def render_markdown(index: Dict[str, Any]) -> str:
    lines = [
        f"# 短线方法工作区 · {index['as_of']}",
        "",
        f"> 注册表版本 `{index.get('registry_version')}`；每种方法只回答一个问题，"
        f"不做统一交集（交集需单独登记并启用）。",
        "",
        "| 方法 | 今日发现 | 新建跟踪 | 跟踪中 | 相对确认 | 已失效 | 默认每日 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in index["methods"]:
        lines.append(
            f"| {item['name']} | {item['count']} | {item['new_today']} | {item['tracking']} | "
            f"{item['confirming']} | {item['invalid']} | {'是' if item['default_daily'] else '否（只归集）'} |"
        )
    for item in index["methods"]:
        if not item["codes"]:
            continue
        lines += ["", f"## {item['name']}", "", f"- 研究问题：{item['question']}",
                  f"- 确认条件：{item['confirm_rule']}", f"- 失效条件：{item['invalid_rule']}",
                  f"- 今日代码：{', '.join(item['codes'])}"]
    if index.get("intersections"):
        lines += ["", "## 方法交集", ""]
        for inter in index["intersections"]:
            state = "已启用" if inter.get("enabled") else "未启用（需单独登记验证）"
            lines.append(f"- {inter['name']}（{state}）：{inter['rule']}")
    if index.get("limitations"):
        lines += ["", "## 数据限制", ""]
        lines += [f"- {item}" for item in index["limitations"]]
    return "\n".join(lines) + "\n"
