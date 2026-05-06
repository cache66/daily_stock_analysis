#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh local board concept pool cache from Tushare THS/DC."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_provider.tushare_fetcher import TushareFetcher  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_concept_pool"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh local board concept pool cache from Tushare THS/DC."
    )
    parser.add_argument("--source", default="auto", choices=["auto", "ths", "dc"])
    parser.add_argument("--expire-after-days", type=int, default=3)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def get_cache_paths(output_dir: Path) -> Dict[str, Path]:
    return {
        "csv": output_dir / "board_concept_pool.csv",
        "meta": output_dir / "board_concept_pool_meta.json",
        "summary": output_dir / "run_summary.txt",
    }


def read_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_cached_df(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return pd.DataFrame(columns=["board_name", "board_code", "board_source"])
    try:
        return pd.read_csv(csv_path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=["board_name", "board_code", "board_source"])


def is_cache_expired(meta: Dict[str, Any], *, expire_after_days: int, now: datetime) -> bool:
    last_updated_at = str(meta.get("last_updated_at") or "").strip()
    if not last_updated_at:
        return True
    try:
        updated_at = datetime.fromisoformat(last_updated_at)
    except Exception:
        return True
    return now - updated_at > timedelta(days=max(1, int(expire_after_days)))


def _write_meta(meta_path: Path, payload: Dict[str, Any]) -> None:
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_summary(summary_path: Path, payload: Dict[str, Any]) -> None:
    lines = [f"{key}={value}" for key, value in payload.items()]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def refresh_board_concept_pool(
    *,
    output_dir: Path,
    source: str,
    expire_after_days: int,
    force_refresh: bool,
    fetcher: object,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = get_cache_paths(output_dir)
    meta = read_meta(paths["meta"])
    cached_df = _read_cached_df(paths["csv"])
    csv_exists = paths["csv"].exists() and not cached_df.empty

    if csv_exists and not force_refresh and not is_cache_expired(
        meta,
        expire_after_days=expire_after_days,
        now=now,
    ):
        result = {
            "status": "skipped",
            "board_count": int(len(cached_df)),
            "paths": paths,
        }
        _write_summary(
            paths["summary"],
            {
                "status": "skipped",
                "source": source,
                "expire_after_days": int(expire_after_days),
                "board_count": int(len(cached_df)),
            },
        )
        return result

    try:
        df = fetcher.get_board_concept_pool(source=source)
        if df is None or df.empty:
            raise RuntimeError("board concept pool is empty")
        normalized = df[["board_name", "board_code", "board_source"]].copy()
        normalized = normalized.fillna("")
        normalized["board_name"] = normalized["board_name"].astype(str).str.strip()
        normalized["board_code"] = normalized["board_code"].astype(str).str.strip()
        normalized["board_source"] = normalized["board_source"].astype(str).str.strip()
        normalized = normalized[normalized["board_name"] != ""].drop_duplicates(
            subset=["board_name"],
            keep="first",
        )
        if normalized.empty:
            raise RuntimeError("board concept pool is empty after normalization")

        normalized.to_csv(paths["csv"], index=False, encoding="utf-8-sig")
        next_meta = {
            "source": source,
            "last_updated_at": now.isoformat(),
            "expire_after_days": int(expire_after_days),
            "refresh_status": "success",
            "board_count": int(len(normalized)),
        }
        _write_meta(paths["meta"], next_meta)
        _write_summary(
            paths["summary"],
            {
                "status": "refreshed",
                "source": source,
                "expire_after_days": int(expire_after_days),
                "board_count": int(len(normalized)),
            },
        )
        return {
            "status": "refreshed",
            "board_count": int(len(normalized)),
            "paths": paths,
        }
    except Exception as exc:
        if csv_exists:
            fallback_meta = {
                "source": meta.get("source") or source,
                "last_updated_at": meta.get("last_updated_at", ""),
                "expire_after_days": int(expire_after_days),
                "refresh_status": "failed",
                "board_count": int(meta.get("board_count") or len(cached_df)),
                "last_attempted_at": now.isoformat(),
                "notes": str(exc),
            }
            _write_meta(paths["meta"], fallback_meta)
            _write_summary(
                paths["summary"],
                {
                    "status": "stale_cache_retained",
                    "source": fallback_meta["source"],
                    "expire_after_days": int(expire_after_days),
                    "board_count": fallback_meta["board_count"],
                    "error": str(exc),
                },
            )
            return {
                "status": "stale_cache_retained",
                "board_count": fallback_meta["board_count"],
                "error": str(exc),
                "paths": paths,
            }
        raise


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        result = refresh_board_concept_pool(
            output_dir=Path(args.output_dir),
            source=args.source,
            expire_after_days=args.expire_after_days,
            force_refresh=bool(args.force_refresh),
            fetcher=TushareFetcher(),
        )
    except Exception as exc:
        logging.error("Failed to refresh board concept pool: %s", exc)
        return 1

    logging.info(
        "Board concept pool refresh finished: status=%s board_count=%s",
        result.get("status"),
        result.get("board_count"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
