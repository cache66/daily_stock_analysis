#!/usr/bin/env python3
"""Screening Top-N 快照桥接（路线图 T1.2；前向积累）。

背景：screening 引擎依赖实时全市场快照，**无法历史回测**，只能在启用后前向积累。
本脚本把每日 screening 的**严格 Top-N** 落库为 `kline_signal_snapshot`
（signal_type = `screening__<strategy>`），供统一评估器
（`scripts/evaluate_signal_snapshot_performance.py`）按既有口径评估。

口径（先写死，2026-09-27）：
- 严格 Top-N：`selection_seed` 固定为空 → 引擎按原始排序取 Top-N，不做 near-score 轮换；
- signal_date 默认 `get_effective_trading_date("cn")`（非交易日/收盘前回落到上一交易日），
  可用 --as-of 显式覆盖；
- 幂等：同 (signal_type, signal_date) 再次运行走“替换式重生成”（storage 层原子替换）；
- metrics_payload 记录 rank/score/screen_score/reason/industry 与引擎审计信息
  （ranking_mode / run_id / snapshot_source / degradation），便于日后复核；
- 审计产物：`data/screening_snapshots/<as_of>/<strategy>.json`（裁剪版响应摘要）。

调度建议（按需写入 crontab；先确认 .env 或命令内已设 SCREENING_ENABLED=true）：
    10 16 * * 1-5 cd <repo> && SCREENING_ENABLED=true ./.venv-linux/bin/python \
        scripts/collect_screening_snapshots.py >> data/run_logs/screening_snapshots.log 2>&1

用法：
    SCREENING_ENABLED=true ./.venv-linux/bin/python scripts/collect_screening_snapshots.py \
        --strategies momentum_quality,shrink_pullback,dual_low --top-n 20
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.base import normalize_stock_code  # noqa: E402

logger = logging.getLogger("collect_screening_snapshots")

SIGNAL_TYPE_PREFIX = "screening__"
DEFAULT_STRATEGIES = "momentum_quality,shrink_pullback,dual_low"
DEFAULT_TOP_N = 20
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "screening_snapshots"
_CODE_PATTERN = re.compile(r"^\d{6}$")

_CANDIDATE_AUDIT_FIELDS = (
    "rank",
    "code",
    "name",
    "score",
    "screen_score",
    "reason",
    "industry",
    "risk_level",
)


def _parse_strategies(raw: str) -> List[str]:
    strategies = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    if not strategies:
        raise SystemExit("--strategies 为空")
    return strategies


def build_snapshot_rows(
    strategy: str,
    candidates: Sequence[Dict[str, Any]],
    *,
    market: str,
    top_n: int,
    engine_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """把 screening 候选裁剪成 snapshot 行（代码归一、去重、截断 Top-N）。"""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    meta = dict(engine_meta or {})
    limit = max(int(top_n), 0)
    for index, candidate in enumerate(candidates or [], start=1):
        if len(rows) >= limit:
            break
        if not isinstance(candidate, dict):
            continue
        raw_code = str(candidate.get("code") or "").strip()
        code = normalize_stock_code(raw_code) if raw_code else ""
        if not _CODE_PATTERN.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        metrics_payload = {
            "engine": "screening",
            "strategy": str(strategy),
            "market": str(market),
            "rank": candidate.get("rank") or index,
            "score": candidate.get("score"),
            "screen_score": candidate.get("screen_score"),
            "reason": candidate.get("reason"),
            "risk_level": candidate.get("risk_level"),
            "industry": candidate.get("industry"),
            "llm_theme": candidate.get("llm_theme"),
            "llm_tags": list(candidate.get("llm_tags") or []),
            "ranking_mode": meta.get("ranking_mode"),
            "run_id": meta.get("run_id"),
            "snapshot_source": meta.get("snapshot_source"),
            "snapshot_count": meta.get("snapshot_count"),
            "after_filter_count": meta.get("after_filter_count"),
            "degradation": list(meta.get("degradation") or []),
        }
        rows.append(
            {
                "code": code,
                "name": candidate.get("name"),
                "criteria_payload": {
                    "strategy": str(strategy),
                    "market": str(market),
                    "top_n": int(top_n),
                    "selection_seed": "",
                },
                "metrics_payload": metrics_payload,
            }
        )
    return rows


def _load_config():
    from src.config import get_config

    return get_config()


def _load_db():
    from src.storage import DatabaseManager

    return DatabaseManager.get_instance()


def _build_service(config, db_manager):
    from src.services.screening_service import ScreeningService

    return ScreeningService(config=config, db_manager=db_manager)


def collect_for_strategy(
    service,
    db_manager,
    strategy: str,
    *,
    market: str,
    top_n: int,
    as_of: date,
    output_root: Path,
) -> Dict[str, Any]:
    """跑单策略 screening → 落库替换 → 写审计 JSON，返回摘要。"""
    response = service.screen(
        strategy=strategy,
        market=market,
        max_results=int(top_n),
        selection_seed="",
    )
    candidates = [item for item in (response.get("candidates") or []) if isinstance(item, dict)]
    engine_meta = {
        "ranking_mode": response.get("ranking_mode"),
        "run_id": response.get("run_id"),
        "snapshot_source": response.get("snapshot_source"),
        "snapshot_count": response.get("snapshot_count"),
        "after_filter_count": response.get("after_filter_count"),
        "degradation": list(response.get("degradation") or []),
    }
    rows = build_snapshot_rows(
        strategy,
        candidates,
        market=market,
        top_n=top_n,
        engine_meta=engine_meta,
    )
    signal_type = f"{SIGNAL_TYPE_PREFIX}{strategy}"
    written = db_manager.replace_signal_snapshots_for_date(
        signal_type=signal_type,
        signal_date=as_of,
        snapshots=rows,
    )

    audit_dir = Path(output_root) / as_of.isoformat()
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{strategy}.json"
    audit_payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy,
        "market": market,
        "as_of": as_of.isoformat(),
        "top_n": int(top_n),
        "signal_type": signal_type,
        "candidate_count": response.get("candidate_count"),
        "ranking_mode": response.get("ranking_mode"),
        "run_id": response.get("run_id"),
        "snapshot_source": response.get("snapshot_source"),
        "degradation": list(response.get("degradation") or []),
        "warnings": list(response.get("warnings") or []),
        "candidates": [
            {key: item.get(key) for key in _CANDIDATE_AUDIT_FIELDS} for item in candidates
        ],
    }
    audit_path.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "strategy": strategy,
        "signal_type": signal_type,
        "signal_date": as_of.isoformat(),
        "candidates": len(rows),
        "written": written,
        "ranking_mode": response.get("ranking_mode"),
        "audit_path": str(audit_path),
        "degraded": bool(response.get("degradation")),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screening Top-N → kline_signal_snapshot 桥接（T1.2）")
    parser.add_argument(
        "--strategies",
        default=DEFAULT_STRATEGIES,
        help=f"逗号分隔策略 id，默认 {DEFAULT_STRATEGIES}",
    )
    parser.add_argument("--market", default="cn", help="市场，默认 cn")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help=f"每策略 Top-N，默认 {DEFAULT_TOP_N}")
    parser.add_argument(
        "--as-of",
        default="",
        help="信号日 YYYY-MM-DD；默认取有效交易日（get_effective_trading_date，按市场）",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"审计 JSON 根目录，默认 {DEFAULT_OUTPUT_ROOT}",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    strategies = _parse_strategies(args.strategies)
    if args.as_of:
        as_of = date.fromisoformat(str(args.as_of))
    else:
        from src.core.trading_calendar import get_effective_trading_date

        as_of = get_effective_trading_date(args.market)

    config = _load_config()
    if not bool(getattr(config, "screening_enabled", False)):
        print(
            "[screening-snapshots] SCREENING_ENABLED 未启用："
            "请在 .env 或进程环境变量设置 SCREENING_ENABLED=true 后重试。"
        )
        return 2

    db_manager = _load_db()
    service = _build_service(config, db_manager)
    output_root = Path(args.output_root)

    results: List[Dict[str, Any]] = []
    failures: List[tuple] = []
    for strategy in strategies:
        logger.info("screening 采集开始: strategy=%s as_of=%s top_n=%s", strategy, as_of, args.top_n)
        try:
            summary = collect_for_strategy(
                service,
                db_manager,
                strategy,
                market=args.market,
                top_n=int(args.top_n),
                as_of=as_of,
                output_root=output_root,
            )
        except Exception as exc:  # noqa: BLE001 - 单策略失败不阻断其余策略
            logger.error("screening 采集失败: strategy=%s err=%s", strategy, exc)
            failures.append((strategy, str(exc)))
            continue
        results.append(summary)
        logger.info(
            "screening 采集完成: strategy=%s candidates=%s written=%s ranking_mode=%s",
            strategy,
            summary["candidates"],
            summary["written"],
            summary["ranking_mode"],
        )

    print(f"[screening-snapshots] as_of={as_of.isoformat()} 成功={len(results)} 失败={len(failures)}")
    for item in results:
        print(
            "  {strategy}: signal_type={signal_type} candidates={candidates} written={written} "
            "ranking_mode={ranking_mode} audit={audit_path}".format(**item)
        )
    for strategy, error in failures:
        print(f"  {strategy}: FAILED - {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
