#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股息线（防守线）候选筛选 CLI。

用法示例：

    python scripts/select_dividend_income_candidates.py --snapshot-date 2026-09-24
    python scripts/select_dividend_income_candidates.py --min-yield 5 --prefilter-limit 100
    python scripts/select_dividend_income_candidates.py --cache-only   # 只用本地缓存，不联网
    python scripts/select_dividend_income_candidates.py --no-price-trend  # 关闭近一年涨跌校验

产物：``data/dividend_income/<snapshot>/`` 下的 CSV / Markdown / summary.json。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import setup_env  # noqa: E402
from src.services.dividend_income_service import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_OUTPUT_DIR,
    DividendIncomeOptions,
    DividendIncomeService,
    TushareDividendProvider,
)


def _resolve_tushare_token() -> str:
    for key in ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "TUSHARE_API_TOKEN"):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def _build_akshare_fetcher() -> object:
    """AKShare 提供批量分红送配（免费无配额），拿不到时返回 None 走 Tushare 兑底。"""
    try:
        import akshare as ak

        return ak
    except Exception as exc:  # noqa: BLE001
        print(f"【提示】AKShare 不可用（{type(exc).__name__}），分红明细将走 Tushare 兑底。", file=sys.stderr)
        return None


def _build_price_trend_fetcher() -> object:
    """近一年/近半年涨跌校验复用个人策略同一行情链路（DataFetcherManager，本地 data/cache/history 磁盘缓存优先、缺口自动补齐）。"""
    try:
        from src.services.kline_selector_service import KlineSelectorService

        return KlineSelectorService.build_fast_a_share_manager()
    except Exception as exc:  # noqa: BLE001
        print(
            f"【提示】行情链路不可用（{type(exc).__name__}），本轮跳过价格走势校验。",
            file=sys.stderr,
        )
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "股息线（防守线）候选筛选：高股息率（TTM）+ 连续分红 + 现金流校验，"
            "输出周频可用的股息候选池。"
        )
    )
    parser.add_argument(
        "--snapshot-date",
        default=date.today().isoformat(),
        help="快照日期 YYYY-MM-DD，默认今天；实际使用最近一个可用交易日快照。",
    )
    parser.add_argument("--min-yield", type=float, default=4.0, help="股息率（TTM）下限，默认 4.0（%%）。")
    parser.add_argument("--min-years", type=int, default=5, help="连续分红年数下限，默认 5。")
    parser.add_argument("--min-listed-years", type=int, default=5, help="上市年限下限（粗过滤），默认 5。")
    parser.add_argument(
        "--prefilter-limit",
        type=int,
        default=300,
        help="初筛后最多对多少只股票拉取分红/财务明细（按股息率降序），默认 300；<=0 表示不设上限。",
    )
    parser.add_argument("--refresh-days", type=int, default=30, help="逐票明细缓存有效期（天），默认 30。")
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=int(os.getenv("TUSHARE_RATE_LIMIT_PER_MINUTE") or 45),
        help="Tushare 每分钟请求上限（保守节流），默认读取环境变量或 45。",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="只用本地缓存，不访问 Tushare（缓存缺失时返回已有数据/失败）。",
    )
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=75.0,
        help=(
            "daily_basic 配额受限（如 1 次/小时）时的等待上限（分钟），默认 75；"
            "等待期间按退避轮询重试，0 表示快速失败。"
        ),
    )
    parser.add_argument(
        "--no-prefer-soe",
        action="store_true",
        help="关闭央国企软偏好加分（默认开启：实控人为央企/地方国企加 3 分，仅影响排序权重）。",
    )
    parser.add_argument(
        "--no-price-trend",
        action="store_true",
        help=(
            "关闭近一年/近半年涨跌校验（默认开启：复用个人策略行情链路与本地 "
            "data/cache/history 缓存，下跌按档扣分并标注，降低“高息陷阱”误选）。"
        ),
    )
    parser.add_argument(
        "--no-fundamentals",
        action="store_true",
        help=(
            "关闭业绩面数据（默认开启：AKShare 同花顺年度摘要，ROE/每股经营现金流，"
            "缓存 90 天；用于盈利质量与分红现金流覆盖校验）。"
        ),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="产物根目录。")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="缓存根目录。")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO。",
    )
    return parser.parse_args()


def main() -> int:
    setup_env()
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

    provider = None
    if not args.cache_only:
        token = _resolve_tushare_token()
        if not token:
            print(
                "【错误】未找到 Tushare Token：请在 .env 配置 TUSHARE_TOKEN / TUSHARE_PRO_TOKEN / TUSHARE_API_TOKEN，"
                "或使用 --cache-only 只读本地缓存。",
                file=sys.stderr,
            )
            return 1
        provider = TushareDividendProvider(token, rate_limit_per_minute=args.rate_limit)

    options = DividendIncomeOptions(
        snapshot_date=str(args.snapshot_date),
        min_yield_pct=float(args.min_yield),
        min_dividend_years=int(args.min_years),
        min_listed_years=int(args.min_listed_years),
        prefilter_limit=int(args.prefilter_limit),
        refresh_days=int(args.refresh_days),
        rate_limit_per_minute=int(args.rate_limit),
        cache_only=bool(args.cache_only),
        wait_minutes=float(args.wait_minutes),
        prefer_soe=not bool(args.no_prefer_soe),
        price_trend=not bool(args.no_price_trend),
        fundamentals=not bool(args.no_fundamentals),
        output_dir=Path(args.output_dir),
        cache_dir=Path(args.cache_dir),
    )
    service = DividendIncomeService(
        provider=provider,
        akshare_fetcher=_build_akshare_fetcher(),
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        rate_limit_per_minute=int(args.rate_limit),
        price_trend_fetcher=_build_price_trend_fetcher() if not args.no_price_trend else None,
    )
    summary = service.run(options)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
