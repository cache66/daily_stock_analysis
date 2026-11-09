#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Baostock 前复权数据重建 stock_daily 的评估窗口（口径归一化专项）。

背景（2026-09-26 发现）：`stock_daily` 实为多来源合并写入，同一股票序列内混有
"不复权 / 前复权"两种口径（例如 603155 的 2026-06-11=25.12 vs 06-12=15.36858225），
产生 ±30~60% 的假跳变（4 月以来 1800+ 跌 + 1900+ 涨异常），会污染回测结论。

本脚本：对指定代码（默认=窗口内出现过的信号代码）用 Baostock 前复权（adjustflag=2）
重新抓取 [start, end] 全窗口日线，经 `save_daily_data` 覆盖式写回，并对重建前后
的超板单日收益做扫描对比。

用法：
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --dry-run
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --codes 603155,000688
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --start-date 2026-01-01
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --retry-failed
    ./.venv-linux/bin/python scripts/rebuild_stock_daily_qfq.py --retry-failed --source akshare --max-attempts 5

注意：Baostock 为单会话服务，多进程并发（--workers>1）会互相踢登录，
日志表现为“用户未登录”批量失败，必须保持串行（默认 workers=1）；
若 Baostock 被封禁（“黑名单用户”），只能用 `--source akshare`（adjust=qfq）。

**不要用 tushare 重建本表**：TushareFetcher 走的是 `daily`（不复权），写进前复权库
会制造假跳变（2026-11-08 实证：1,758 行被覆写成不复权口径，已回滚重建）。

口径闸门（2026-11-08 新增）：写入前用 `amount/close` 估算股数并校验 `volume`，
比例异常或缺 volume 的批次计为失败，不再覆盖已有日线；akshare 的“手”会自动 ×100。

跳变闸门（2026-11-09 修订，见 `src/services/quote_quality.py`）：
- 超复核阈值（主板 11.5%、创业板/科创板 21.5%、北交所 31.5%）→ 告警 + **立即**登记
  可疑清单，不再等整批扫描结束才留痕；
- 超极端阈值（阈值 ×2）且前一交易日紧邻、不在上市初期窗口 → 视为口径混写，拒写该票
  （`--allow-extreme-jumps` 可显式放行）；停牌/缺行情/新股阶段仍只告警，不据跳变定罪。
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from data_provider.baostock_fetcher import BaostockFetcher
from src.services.quote_quality import (  # noqa: F401 - 常量按旧名再导出，供外部引用
    BSE_REVIEW_PCT,
    GROWTH_BOARD_REVIEW_PCT,
    LEVEL_EXTREME,
    LEVEL_REVIEW,
    MAIN_BOARD_REVIEW_PCT,
    SUSPICIOUS_CODES_PATH,
    classify_price_jumps,
    load_suspicious_codes,
    price_jump_extreme_threshold,
    price_jump_review_threshold,
    record_suspicious_codes,
    replace_suspicious_codes,
)
from src.storage import DatabaseManager

logger = logging.getLogger("rebuild_stock_daily_qfq")

SOURCE_LABEL = "baostock_qfq_rebuild"
DEFAULT_START = "2026-01-01"
# 失败率阈值：超过则视为重建未完成（非零退出），并要求检查失败清单后重试。
MAX_FAIL_RATE = 0.05
DEFAULT_FAILED_CODES_PATH = PROJECT_ROOT / "data" / "run_logs" / "rebuild_stock_daily_qfq_failed_codes.txt"
DEFAULT_SUSPICIOUS_CODES_PATH = SUSPICIOUS_CODES_PATH


def load_universe_codes(db: DatabaseManager, *, start_date: str, end_date: str, limit: int = 0) -> List[str]:
    """默认评估相关代码：窗口内出现过的信号代码（含 random_baseline）。

    仅保留 6 位纯数字 A 股代码（港股/美股等非 A 股无法用本脚本重建，
    且会产生无意义的失败计数；--codes 显式传入时不做此过滤）。
    """
    from sqlalchemy import text

    with db.session_scope() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT code FROM kline_signal_snapshot "
                "WHERE signal_date >= :start AND signal_date <= :end ORDER BY code"
            ),
            {"start": start_date, "end": end_date},
        ).fetchall()
    codes = [str(row[0]).strip() for row in rows if str(row[0]).strip()]
    ashare_codes = [code for code in codes if code.isdigit() and len(code) == 6]
    skipped = len(codes) - len(ashare_codes)
    if skipped:
        logger.info("universe filter: %s non-A-share codes skipped (e.g. HK/US tickers)", skipped)
    codes = ashare_codes
    if limit and limit > 0:
        codes = codes[:limit]
    return codes


def scan_abnormal_jumps(
    db: DatabaseManager,
    *,
    start_date: str,
    end_date: str,
    threshold_pct: float = 21.5,
    sample: int = 5,
    include_rows: bool = False,
) -> Dict[str, Any]:
    """扫描需复核跳变；按板块包络排除合法北交所 30%，不是错误证明。"""
    from sqlalchemy import text

    query = text(
        """
        SELECT code, date AS d, prev, close, (close - prev) * 100.0 / prev AS ret
        FROM (
          SELECT code, date, close,
                 LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev
          FROM stock_daily WHERE date >= :start AND date <= :end
        )
        WHERE prev IS NOT NULL AND prev > 0
          AND ABS((close - prev) * 100.0 / prev) > :threshold
        ORDER BY ABS((close - prev) * 100.0 / prev) DESC
        """
    )
    with db.session_scope() as session:
        rows = list(
            session.execute(
                query,
                {"start": start_date, "end": end_date, "threshold": float(threshold_pct)},
            ).fetchall()
        )
    rows = [
        row for row in rows
        if (limit := price_jump_review_threshold(str(row[0]), str(row[1])[:10])) is not None
        and abs(float(row[4])) > max(float(threshold_pct), limit)
    ]
    result: Dict[str, Any] = {
        "abnormal_count": len(rows),
        "codes_affected": len({str(row[0]) for row in rows}),
        "samples": [
            {"code": str(row[0]), "date": str(row[1]), "prev": float(row[2]), "close": float(row[3]), "ret_pct": round(float(row[4]), 1)}
            for row in rows[: max(0, int(sample))]
        ],
    }
    if include_rows:
        result["rows"] = [
            {
                "code": str(row[0]),
                "date": str(row[1]),
                "prev_close": float(row[2]),
                "close": float(row[3]),
                "ret_pct": round(float(row[4]), 2),
            }
            for row in rows
        ]
    return result


_WORKER: Dict[str, Any] = {}


def _worker_init(source: str = "baostock") -> None:
    if str(source) == "akshare":
        from data_provider.akshare_fetcher import AkshareFetcher

        # 2026-11-08 实测：tencent 返回 volume 全 0（被口径闸门拒绝），
        # sina / em 为 adjust=qfq 且带成交量（单位为手，由闸门换算成股）。
        _WORKER["fetcher"] = AkshareFetcher(
            stock_history_source_priority=("sina", "em", "tencent")
        )
    else:
        _WORKER["fetcher"] = BaostockFetcher()
    _WORKER["label"] = f"{source}_qfq_rebuild" if str(source) != "baostock" else SOURCE_LABEL
    _WORKER["db"] = DatabaseManager.get_instance()


# volume 估算容差：amount/close 得到股数，与之比较
_VOLUME_RATIO_MIN = 0.5
_VOLUME_RATIO_MAX = 2.0
# 手 -> 股 的换算区间（volume 远小于估算股数时视为手）
_VOLUME_LOT_RATIO = 0.2


def normalize_and_validate_frame(
    frame: pd.DataFrame,
    code: str,
    *,
    allow_extreme_jumps: bool = False,
    persist_suspicious: bool = True,
    source: str = SOURCE_LABEL,
) -> pd.DataFrame:
    """校验并归一化成交量语义（股）；跳变分级处理。

    规则：
    1. 必须有 volume；全零视为缺失（腾讯源会回填空列）→ 报错。
    2. 用 ``amount / close`` 估算股数；若 volume 明显偏小（<0.2 倍）按“手”处理 ×100。
    3. 归一化后比例仍不在 [0.5, 2.0] 内 → 报错，宁可不写。
    4. 跳变：超复核阈值→告警 + 立即登记可疑清单；超极端阈值且前一交易日紧邻、
       不在上市初期窗口 → 拒写（除非 ``allow_extreme_jumps``）。停牌、缺行情、
       新股阶段仍只告警，不据跳变定罪。
    """
    if frame is None or frame.empty:
        raise ValueError("empty frame")
    columns = {str(col).lower() for col in frame.columns}
    if "volume" not in columns:
        raise ValueError("missing volume column")

    volume = pd.to_numeric(frame["volume"], errors="coerce")
    close = pd.to_numeric(frame.get("close"), errors="coerce") if "close" in columns else None
    amount = pd.to_numeric(frame.get("amount"), errors="coerce") if "amount" in columns else None
    if volume is None or volume.fillna(0).abs().sum() <= 0:
        raise ValueError("volume all zero")

    if close is not None:
        dates = frame["date"] if "date" in frame else pd.Series([""] * len(frame), index=frame.index)
        jumps = classify_price_jumps(list(dates), list(close), code)
        review_hits = jumps[LEVEL_REVIEW]
        extreme_hits = jumps[LEVEL_EXTREME]
        for hit in review_hits:
            logger.warning(
                "价格跳变需复核 code=%s %s；特殊交易阶段未核实，不据此拒写",
                hit.code, hit.describe(),
            )
        if (review_hits or extreme_hits) and persist_suspicious:
            # 立即留痕：整批中断、后续失败或重试都不会丢掉“这只票待复核”的事实
            record_suspicious_codes(
                [code],
                reason="extreme_jump" if extreme_hits else "review_jump",
                source=source,
                as_of=str(dates.iloc[-1])[:10] if len(dates) else None,
            )
        if extreme_hits:
            detail = "；".join(hit.describe() for hit in extreme_hits[:5])
            if not allow_extreme_jumps:
                raise ValueError(f"极端跳变（无缺失行情解释）拒写 {code}：{detail}")
            logger.warning("极端跳变已显式放行 code=%s：%s", code, detail)

    if close is not None and amount is not None:
        valid = (close > 0) & (amount > 0) & (volume > 0)
        if valid.any():
            expected_shares = float(amount[valid].sum()) / float(close[valid].mean())
            ratio = float(volume[valid].sum()) / expected_shares if expected_shares > 0 else 0.0
            if 0 < ratio < _VOLUME_LOT_RATIO:
                frame = frame.copy()
                frame["volume"] = volume * 100.0
                volume = frame["volume"]
                ratio *= 100.0
            if not (_VOLUME_RATIO_MIN <= ratio <= _VOLUME_RATIO_MAX):
                raise ValueError(
                    f"volume/amount 口径异常（ratio={ratio:.4f}），拒绝写入 {code}"
                )
    return frame


def _rebuild_chunk(task: Tuple[List[str], str, str, bool, bool]) -> Dict[str, Any]:
    codes, start_date, end_date, dry_run, allow_extreme_jumps = task
    fetcher: BaostockFetcher = _WORKER["fetcher"]
    db: DatabaseManager = _WORKER["db"]
    stats: Dict[str, Any] = {
        "total": len(codes),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_codes": [],
        "quarantined": 0,
        "quarantined_codes": [],
    }
    for code in codes:
        try:
            frame = fetcher.get_daily_data(
                stock_code=code,
                start_date=start_date,
                end_date=end_date,
                days=0,
            )
            if frame is None or frame.empty:
                stats["skipped"] += 1
                continue
            frame = normalize_and_validate_frame(
                frame,
                code,
                allow_extreme_jumps=allow_extreme_jumps,
                persist_suspicious=not dry_run,
                source=_WORKER.get("label") or SOURCE_LABEL,
            )
            if not dry_run:
                db.save_daily_data(frame, code, _WORKER.get("label") or SOURCE_LABEL)
            stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001 - 单票失败继续
            stats["failed"] += 1
            stats["failed_codes"].append(code)
            if "极端跳变" in str(exc):
                # 口径混写嫌疑：已登记可疑清单，单独计数以便区分“取数失败”
                stats["quarantined"] += 1
                stats["quarantined_codes"].append(code)
            logger.warning("rebuild failed for %s: %s", code, exc)
    return stats


def run_rebuild(
    codes: Sequence[str],
    *,
    db: DatabaseManager,
    start_date: str,
    end_date: str,
    workers: int = 1,
    dry_run: bool = False,
    progress_every: int = 200,
    source: str = "baostock",
    allow_extreme_jumps: bool = False,
) -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "total": len(codes),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_codes": [],
        "quarantined": 0,
        "quarantined_codes": [],
    }
    if not codes:
        return totals
    if workers and int(workers) > 1 and not dry_run:
        chunk_size = max(1, (len(codes) + int(workers) * 4 - 1) // (int(workers) * 4))
        chunks = [list(codes)[i : i + chunk_size] for i in range(0, len(codes), chunk_size)]
        tasks = [
            (chunk, start_date, end_date, dry_run, allow_extreme_jumps) for chunk in chunks
        ]
        ctx = multiprocessing.get_context("fork")
        processed = 0
        with ctx.Pool(processes=max(2, int(workers)), initializer=_worker_init, initargs=(source,)) as pool:
            for stats in pool.imap_unordered(_rebuild_chunk, tasks):
                processed += stats["total"]
                for key in ("updated", "skipped", "failed", "quarantined"):
                    totals[key] += stats[key]
                totals["failed_codes"].extend(stats.get("failed_codes") or [])
                totals["quarantined_codes"].extend(stats.get("quarantined_codes") or [])
                if progress_every and processed % max(1, int(progress_every)) < 50:
                    logger.info(
                        "progress %s/%s updated=%s skipped=%s failed=%s",
                        processed, len(codes), totals["updated"], totals["skipped"], totals["failed"],
                    )
        return totals

    _worker_init(source)
    for index, code in enumerate(codes, start=1):
        stats = _rebuild_chunk(([code], start_date, end_date, dry_run, allow_extreme_jumps))
        for key in ("updated", "skipped", "failed", "quarantined"):
            totals[key] += stats[key]
        totals["failed_codes"].extend(stats.get("failed_codes") or [])
        totals["quarantined_codes"].extend(stats.get("quarantined_codes") or [])
        if progress_every and index % int(progress_every) == 0:
            logger.info(
                "progress %s/%s updated=%s skipped=%s failed=%s",
                index, len(codes), totals["updated"], totals["skipped"], totals["failed"],
            )
    return totals


def _read_failed_codes(failed_path: Path) -> List[str]:
    """读取失败清单（去重排序；不存在或读取失败返回空列表）。"""
    path = Path(failed_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted({line.strip() for line in text.splitlines() if line.strip()})


def _write_failed_codes(failed_path: Path, codes: Sequence[str]) -> None:
    """写回失败清单；清零时删除文件，保持“清单存在 = 仍有失败”的语义。"""
    path = Path(failed_path)
    normalized = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not normalized:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def retry_failed_codes(
    *,
    db: DatabaseManager,
    failed_path: Path,
    start_date: str,
    end_date: str,
    source: str = "baostock",
    max_attempts: int = 3,
    dry_run: bool = False,
    progress_every: int = 0,
    allow_extreme_jumps: bool = False,
) -> Dict[str, Any]:
    """读取失败清单并多轮重试，直至清零或达到 max_attempts；每轮回写剩余清单。"""
    pending = _read_failed_codes(failed_path)
    attempts: List[Dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        if not pending:
            break
        logger.info("retry attempt %s/%s: %s codes", attempt, max_attempts, len(pending))
        stats = run_rebuild(
            pending,
            db=db,
            start_date=start_date,
            end_date=end_date,
            workers=1,
            dry_run=dry_run,
            progress_every=progress_every,
            source=source,
            allow_extreme_jumps=allow_extreme_jumps,
        )
        pending = sorted({str(code) for code in (stats.get("failed_codes") or [])})
        attempts.append(
            {
                "attempt": attempt,
                "tried": int(stats.get("total") or 0),
                "updated": int(stats.get("updated") or 0),
                "skipped": int(stats.get("skipped") or 0),
                "failed": int(stats.get("failed") or 0),
                "remaining": len(pending),
            }
        )
        if not dry_run:
            _write_failed_codes(failed_path, pending)
    return {"attempts": attempts, "remaining": pending}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Baostock 前复权重建 stock_daily 评估窗口（口径归一化）")
    parser.add_argument("--start-date", default=DEFAULT_START, help="起始日期（含），默认 2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="结束日期（含），默认今天")
    parser.add_argument("--codes", default="", help="逗号分隔代码（优先）；默认=窗口内信号代码")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟用）")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数；保持 1（Baostock 单会话，>1 会互相踢登录）")
    parser.add_argument(
        "--source",
        default="baostock",
        choices=["baostock", "akshare"],
        help="数据源（默认 baostock；被封禁时用 akshare，adjust=qfq，自动做 手→股 换算）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--no-scan", action="store_true", help="跳过重建前后的异常扫描")
    parser.add_argument(
        "--allow-extreme-jumps",
        action="store_true",
        help="显式放行极端跳变（仅用于已核实为复牌/重组等特殊阶段的场景）；默认拒写并记入可疑清单",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="重试模式：读取失败清单并多轮重跑直至清零（忽略 --codes/--limit；可用 --source 换源）",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="重试模式最大轮数，默认 3")
    parser.add_argument("--failed-codes-path", default=str(DEFAULT_FAILED_CODES_PATH), help=f"失败清单路径，默认 {DEFAULT_FAILED_CODES_PATH}")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    db = DatabaseManager.get_instance()

    if args.retry_failed:
        failed_path = Path(args.failed_codes_path)
        pending = _read_failed_codes(failed_path)
        if not pending:
            print(f"[rebuild] 无失败清单（{failed_path}），无需重试")
            return 0
        print(
            f"[rebuild] 重试失败清单: {failed_path}（{len(pending)} 只，source={args.source}，"
            f"max_attempts={args.max_attempts}，dry_run={args.dry_run}）"
        )
        result = retry_failed_codes(
            db=db,
            failed_path=failed_path,
            start_date=args.start_date,
            end_date=args.end_date,
            source=str(args.source),
            max_attempts=int(args.max_attempts),
            dry_run=bool(args.dry_run),
            allow_extreme_jumps=bool(args.allow_extreme_jumps),
        )
        for item in result["attempts"]:
            print(
                "[rebuild] 第 {attempt} 轮: tried={tried} updated={updated} skipped={skipped} "
                "failed={failed} 剩余={remaining}".format(**item)
            )
        remaining = result["remaining"]
        if remaining:
            preview = ",".join(remaining[:10]) + (" ..." if len(remaining) > 10 else "")
            print(f"[rebuild] 重试后仍失败 {len(remaining)} 只（前 10: {preview}）")
            return 2
        print("[rebuild] 重试完成: 失败清零")
        return 0

    before = None
    suspicious_before = load_suspicious_codes()
    if not args.no_scan:
        before = scan_abnormal_jumps(db, start_date=args.start_date, end_date=args.end_date)
        print(f"[rebuild] 重建前超板异常: {before['abnormal_count']} 条 / 涉及 {before['codes_affected']} 只")
        for item in before["samples"]:
            print(f"   - {item['code']} {item['date']}: {item['ret_pct']}%")

    if args.codes.strip():
        codes = [code.strip() for code in args.codes.split(",") if code.strip()]
    else:
        codes = load_universe_codes(db, start_date=args.start_date, end_date=args.end_date, limit=args.limit)
    logger.info("重建目标: %s 只（窗口 %s ~ %s，dry_run=%s）", len(codes), args.start_date, args.end_date, args.dry_run)

    stats = run_rebuild(
        codes,
        db=db,
        start_date=args.start_date,
        end_date=args.end_date,
        workers=int(args.workers),
        dry_run=bool(args.dry_run),
        source=str(args.source),
        allow_extreme_jumps=bool(args.allow_extreme_jumps),
    )
    print(
        "[rebuild] 完成: total={total} updated={updated} skipped={skipped} failed={failed} "
        "quarantined={quarantined}".format(**stats)
    )

    failed_count = int(stats.get("failed") or 0)
    total_count = int(stats.get("total") or 0)
    fail_rate = (failed_count / total_count) if total_count else 0.0
    failed_codes = [str(code) for code in (stats.get("failed_codes") or [])]
    if failed_codes:
        failed_path = DEFAULT_FAILED_CODES_PATH
        _write_failed_codes(failed_path, failed_codes)
        print(f"[rebuild] 失败清单已写入: {failed_path}（{len(failed_codes)} 只，可用 --retry-failed 重试）")
    if fail_rate > MAX_FAIL_RATE:
        print(
            f"[rebuild] 失败率过高: {failed_count}/{total_count} = {fail_rate:.1%} "
            f"（阈值 {MAX_FAIL_RATE:.0%}）——请检查日志与失败清单后重试"
        )
        return 2
    if failed_count:
        print(f"[rebuild] 警告: 存在失败票 {failed_count} 只（{fail_rate:.1%}），见上述失败清单")

    if not args.no_scan:
        after = scan_abnormal_jumps(
            db, start_date=args.start_date, end_date=args.end_date, include_rows=True
        )
        print(f"[rebuild] 重建后超板异常: {after['abnormal_count']} 条 / 涉及 {after['codes_affected']} 只")
        for item in after["samples"]:
            print(f"   - {item['code']} {item['date']}: {item['ret_pct']}%")
        scanned = {str(row["code"]) for row in after.get("rows", [])}
        # 本次运行中即时登记过的代码（= 当前清单 - 运行前快照）也要保留：
        # 它们在窗口内可能已被重建覆盖，但仍是待复核事实。
        flagged_during_run = load_suspicious_codes() - suspicious_before
        final_codes = sorted(scanned | flagged_during_run)
        if args.dry_run:
            print(
                f"[rebuild] 可疑行情清单（dry-run 未写入）将为 {len(final_codes)} 只："
                f"{','.join(final_codes[:10])}{' ...' if len(final_codes) > 10 else ''}"
            )
        else:
            change = replace_suspicious_codes(
                final_codes,
                reason="post_rebuild_scan",
                source=str(args.source),
                as_of=args.end_date,
            )
            print(
                f"[rebuild] 可疑行情清单: {DEFAULT_SUSPICIOUS_CODES_PATH}（{len(final_codes)} 只；"
                f"新增 {len(change['added'])}、清除 {len(change['removed'])}）"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
