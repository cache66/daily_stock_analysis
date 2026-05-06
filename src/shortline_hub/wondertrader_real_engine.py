# -*- coding: utf-8 -*-
"""Real WonderTrader-backed candidate scan helpers for shortline_hub."""

from __future__ import annotations

import csv
import os
import sys
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any


MIN_SIGNAL_HISTORY_BARS = 25
DEFAULT_PREFILTER_LIMIT = 24
DEFAULT_REAL_SCAN_SOURCE = "wondertrader_real_engine"


def infer_a_share_exchange(code: str) -> str:
    normalized = str(code or "").strip()
    if normalized.startswith(("6", "9")):
        return "SSE"
    if normalized.startswith(("0", "2", "3")):
        return "SZSE"
    raise ValueError(f"unsupported A-share code: {code}")


def to_wt_std_code(code: str) -> str:
    normalized = str(code or "").strip()
    return f"{infer_a_share_exchange(normalized)}.STK.{normalized}"


def strip_wt_adjust_suffix(std_code: str) -> str:
    normalized = str(std_code or "").strip()
    while normalized.endswith(("-", "/", "Q")):
        normalized = normalized[:-1]
    return normalized


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _prefer_positive_metric(primary: Any, fallback: Any) -> float:
    primary_value = _to_optional_float(primary)
    if primary_value is not None and primary_value > 0:
        return primary_value

    fallback_value = _to_optional_float(fallback)
    if fallback_value is not None:
        return fallback_value
    return 0.0


def _prefer_nonzero_metric(primary: Any, fallback: Any) -> float:
    primary_value = _to_optional_float(primary)
    if primary_value is not None and abs(primary_value) > 1e-9:
        return primary_value

    fallback_value = _to_optional_float(fallback)
    if fallback_value is not None:
        return fallback_value
    return 0.0


def _compute_history_change_pct(
    rows: list[dict[str, Any]],
    *,
    lookback_days: int,
) -> float | None:
    if len(rows) <= lookback_days:
        return None

    latest_close = _to_optional_float(rows[-1].get("close"))
    base_close = _to_optional_float(rows[-(lookback_days + 1)].get("close"))
    if latest_close is None or base_close in (None, 0):
        return None
    return ((latest_close / base_close) - 1.0) * 100.0


def _history_has_missing_volume(rows: list[dict[str, Any]], *, recent_days: int = 5) -> bool:
    if not rows:
        return False

    recent_rows = rows[-recent_days:]
    recent_volumes = [_to_float(row.get("volume")) for row in recent_rows]
    recent_amounts = [_to_float(row.get("amount")) for row in recent_rows]
    return all(abs(value) <= 1e-9 for value in recent_volumes) and any(
        value > 0 for value in recent_amounts
    )


def _compute_amount_ratio(rows: list[dict[str, Any]], *, recent_days: int = 5) -> float | None:
    if len(rows) <= recent_days:
        return None

    latest_amount = _to_optional_float(rows[-1].get("amount"))
    if latest_amount in (None, 0.0):
        return None

    base_amounts = [
        _to_optional_float(row.get("amount"))
        for row in rows[-(recent_days + 1):-1]
    ]
    valid_base_amounts = [value for value in base_amounts if value not in (None, 0.0)]
    if not valid_base_amounts:
        return None

    avg_amount = mean(valid_base_amounts)
    if avg_amount <= 0:
        return None
    return latest_amount / avg_amount


def _should_downgrade_reconstructed_volume_risk(
    rows: list[dict[str, Any]],
    *,
    snapshot_volume_ratio: float,
    recent_days: int = 5,
    max_reconstructed_ratio: float = 0.2,
    ratio_tolerance: float = 0.25,
) -> bool:
    if not rows or snapshot_volume_ratio <= 0:
        return False

    reconstructed_total_count = sum(
        1 for row in rows if bool(row.get("_volume_reconstructed_from_amount"))
    )
    if reconstructed_total_count <= 0:
        return False

    reconstructed_ratio = reconstructed_total_count / max(len(rows), 1)
    if reconstructed_ratio > max_reconstructed_ratio:
        return False

    reconstructed_recent = any(
        bool(row.get("_volume_reconstructed_from_amount")) for row in rows[-recent_days:]
    )
    if not reconstructed_recent:
        return False

    amount_ratio = _compute_amount_ratio(rows, recent_days=recent_days)
    if amount_ratio is None or amount_ratio <= 0:
        return False

    ratio_gap = abs(amount_ratio - snapshot_volume_ratio) / max(snapshot_volume_ratio, 1e-9)
    return ratio_gap <= ratio_tolerance


def repair_history_rows_from_amount(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []

    repaired_rows = [dict(row) for row in rows]
    calibration_ratios: list[float] = []
    calibration_volumes: list[float] = []
    calibration_amounts: list[float] = []

    for row in repaired_rows:
        close = _to_optional_float(row.get("close"))
        volume = _to_optional_float(row.get("volume"))
        amount = _to_optional_float(row.get("amount"))
        if close in (None, 0.0) or volume in (None, 0.0) or amount in (None, 0.0):
            continue
        ratio = amount / (volume * close)
        if 0.01 <= ratio <= 100.0:
            calibration_ratios.append(ratio)
            calibration_volumes.append(volume)
            calibration_amounts.append(amount)

    amount_to_turnover_ratio = median(calibration_ratios) if calibration_ratios else 1.0
    median_valid_volume = median(calibration_volumes) if calibration_volumes else 0.0
    median_valid_amount = median(calibration_amounts) if calibration_amounts else 0.0

    for row in repaired_rows:
        close = _to_optional_float(row.get("close"))
        volume = _to_optional_float(row.get("volume"))
        amount = _to_optional_float(row.get("amount"))
        already_reconstructed = bool(row.get("_volume_reconstructed_from_amount"))
        already_scaled = bool(row.get("_amount_scale_repaired"))
        row["_volume_reconstructed_from_amount"] = already_reconstructed
        row["_amount_scale_repaired"] = already_scaled
        if close in (None, 0.0) or amount in (None, 0.0):
            continue
        if volume is not None and volume > 0:
            continue

        normalized_amount = amount
        implied_volume = normalized_amount / max(close * amount_to_turnover_ratio, 1e-9)

        scale_up_candidate = normalized_amount * 1000.0
        scaled_implied_volume = scale_up_candidate / max(close * amount_to_turnover_ratio, 1e-9)

        needs_scale_repair = False
        if median_valid_volume > 0:
            needs_scale_repair = implied_volume < (median_valid_volume * 0.02) and scaled_implied_volume >= (
                median_valid_volume * 0.10
            )
        elif median_valid_amount > 0:
            needs_scale_repair = normalized_amount < (median_valid_amount * 0.02) and scale_up_candidate >= (
                median_valid_amount * 0.10
            )

        if needs_scale_repair:
            normalized_amount = scale_up_candidate
            implied_volume = scaled_implied_volume
            row["_amount_scale_repaired"] = True

        if implied_volume > 0:
            row["amount"] = normalized_amount
            row["volume"] = implied_volume
            row["_volume_reconstructed_from_amount"] = True

    return repaired_rows


def _is_weekend_trade_date(trade_date: str) -> bool:
    normalized = str(trade_date or "").strip()
    try:
        return date.fromisoformat(normalized).weekday() >= 5
    except ValueError:
        return False


def _truncate_history_rows_to_trade_date(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
) -> list[dict[str, Any]]:
    normalized_trade_date = str(trade_date or "").strip()
    try:
        date.fromisoformat(normalized_trade_date)
    except ValueError:
        return list(rows)

    truncated_rows = [
        row for row in rows if str(row.get("date") or "").strip() <= normalized_trade_date
    ]
    return truncated_rows if truncated_rows else list(rows)


def _score_spot_prefilter_row(row: dict[str, Any]) -> float:
    pct_change = _to_float(row.get("pct_change"))
    turnover_rate = _to_float(row.get("turnover_rate"))
    volume_ratio = _to_float(row.get("volume_ratio"))
    change_pct_60d = _to_float(row.get("change_pct_60d"))
    amount = _to_float(row.get("amount"))
    liquidity_bonus = min(max(amount, 0.0) / 100000000.0, 8.0)
    return (
        pct_change * 6.0
        + min(max(turnover_rate, 0.0), 20.0) * 2.0
        + min(max(volume_ratio, 0.0), 5.0) * 8.0
        + min(max(change_pct_60d, 0.0), 60.0) * 0.2
        + liquidity_bonus
    )


def _spot_universe_csv_path(repo_root: Path) -> Path:
    return repo_root / "data" / "cache" / "reference" / "kline_selector_spot_universe.csv"


def _stock_basic_csv_path(repo_root: Path) -> Path:
    return repo_root / "data" / "cache" / "reference" / "tushare_stock_basic_list.csv"


def _trade_calendar_csv_path(repo_root: Path) -> Path:
    return repo_root / "data" / "cache" / "reference" / "tushare_trade_cal_sse.csv"


def _akshare_trade_calendar_cache_path(repo_root: Path) -> Path:
    return repo_root / "data" / "cache" / "reference" / "akshare_trade_dates_sina.csv"


def _read_akshare_trade_dates_cache(repo_root: Path) -> set[str]:
    cache_path = _akshare_trade_calendar_cache_path(repo_root)
    if not cache_path.exists():
        return set()
    try:
        with cache_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {
                str(row.get("trade_date") or "").strip().replace("-", "")
                for row in csv.DictReader(handle)
                if str(row.get("trade_date") or "").strip()
            }
    except Exception:
        return set()


def _write_akshare_trade_dates_cache(repo_root: Path, trade_dates: set[str]) -> None:
    cache_path = _akshare_trade_calendar_cache_path(repo_root)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with cache_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["trade_date"])
            writer.writeheader()
            for trade_date in sorted(trade_dates):
                writer.writerow({"trade_date": trade_date})
    except Exception:
        return


def _load_akshare_trade_dates(repo_root: Path, *, min_cover_date: str) -> set[str]:
    cached_trade_dates = _read_akshare_trade_dates_cache(repo_root)
    if cached_trade_dates and max(cached_trade_dates) >= min_cover_date:
        return cached_trade_dates

    try:
        import akshare as ak  # type: ignore

        df = ak.tool_trade_date_hist_sina()
    except Exception:
        return cached_trade_dates

    fetched_trade_dates = {
        str(value).strip().replace("-", "")
        for value in getattr(df, "get", lambda *_args, **_kwargs: [])("trade_date", [])
        if str(value).strip()
    }
    if fetched_trade_dates:
        _write_akshare_trade_dates_cache(repo_root, fetched_trade_dates)
        return fetched_trade_dates
    return cached_trade_dates


def _is_non_trading_trade_date(repo_root: Path, trade_date: str) -> bool:
    if _is_weekend_trade_date(trade_date):
        return True

    normalized = str(trade_date or "").strip()
    compact_date = normalized.replace("-", "")
    if len(compact_date) != 8 or not compact_date.isdigit():
        return False

    akshare_trade_dates = _load_akshare_trade_dates(repo_root, min_cover_date=compact_date)
    if akshare_trade_dates and max(akshare_trade_dates) >= compact_date:
        return compact_date not in akshare_trade_dates

    calendar_path = _trade_calendar_csv_path(repo_root)
    if not calendar_path.exists():
        return False

    try:
        with calendar_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                cal_date = str(row.get("cal_date") or "").strip()
                if cal_date != compact_date:
                    continue
                return str(row.get("is_open") or "").strip() == "0"
    except Exception:
        return False
    return False


def _history_csv_path(repo_root: Path, code: str) -> Path:
    return repo_root / "data" / "cache" / "history" / "cn" / f"{code}.csv"


def select_prefilter_snapshots(
    repo_root: Path | str,
    *,
    prefilter_limit: int = DEFAULT_PREFILTER_LIMIT,
) -> list[dict[str, Any]]:
    repo_path = Path(repo_root)
    csv_path = _spot_universe_csv_path(repo_path)
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    selected: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or row.get("symbol") or "").strip()
        name = str(row.get("name") or code).strip()
        if len(code) != 6 or not code.isdigit():
            continue
        if "ST" in name.upper():
            continue

        latest_price = _to_float(row.get("latest_price"))
        pct_change = _to_float(row.get("pct_change"))
        turnover_rate = _to_float(row.get("turnover_rate"))
        volume_ratio = _to_float(row.get("volume_ratio"))

        if pct_change < 2.0:
            continue
        if turnover_rate < 1.0 and volume_ratio < 1.2:
            continue

        enriched = dict(row)
        enriched["code"] = code
        enriched["name"] = name
        enriched["latest_price"] = latest_price
        enriched["pct_change"] = pct_change
        enriched["turnover_rate"] = turnover_rate
        enriched["volume_ratio"] = volume_ratio
        enriched["_prefilter_score"] = _score_spot_prefilter_row(row)
        selected.append(enriched)

    selected.sort(
        key=lambda item: (
            float(item["_prefilter_score"]),
            float(item["pct_change"]),
            float(item["volume_ratio"]),
            float(item["turnover_rate"]),
        ),
        reverse=True,
    )
    return selected[: max(0, int(prefilter_limit))]


def load_stock_profile_map(repo_root: Path | str) -> dict[str, dict[str, str]]:
    repo_path = Path(repo_root)
    csv_path = _stock_basic_csv_path(repo_path)
    if not csv_path.exists():
        return {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    profile_map: dict[str, dict[str, str]] = {}
    for row in rows:
        code = str(row.get("code") or row.get("symbol") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        profile_map[code] = {
            "name": str(row.get("name") or row.get("fullname") or code).strip(),
            "industry": str(row.get("industry") or "").strip(),
        }
    return profile_map


def load_history_rows(repo_root: Path | str, code: str) -> list[dict[str, Any]]:
    csv_path = _history_csv_path(Path(repo_root), code)
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return repair_history_rows_from_amount(list(csv.DictReader(handle)))


def compute_shortline_signal_from_history_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(rows) < MIN_SIGNAL_HISTORY_BARS:
        return None

    closes = [_to_float(row.get("close")) for row in rows]
    highs = [_to_float(row.get("high")) for row in rows]
    lows = [_to_float(row.get("low")) for row in rows]
    volumes = [_to_float(row.get("volume")) for row in rows]
    opens = [_to_float(row.get("open")) for row in rows]

    last_close = closes[-1]
    prev_close = closes[-2]
    last_high = highs[-1]
    last_low = lows[-1]
    last_open = opens[-1]
    last_volume = volumes[-1]
    asof_date = str(rows[-1].get("date") or "").strip()

    pct_change = ((last_close / prev_close) - 1.0) * 100.0 if prev_close else 0.0
    prev_20_high = max(highs[-21:-1])
    prev_20_close_high = max(closes[-21:-1])
    avg_volume_5 = mean(volumes[-6:-1]) if any(volumes[-6:-1]) else 0.0
    volume_ratio = (last_volume / avg_volume_5) if avg_volume_5 else 0.0
    trend_pct_60 = (
        ((last_close / closes[-21]) - 1.0) * 100.0 if closes[-21] else 0.0
    )

    breakout_strength = ((last_close / prev_20_high) - 1.0) * 100.0 if prev_20_high else 0.0
    intraday_range_pct = ((last_high / last_low) - 1.0) * 100.0 if last_low else 0.0
    dual_thrust_days = 4
    hh = max(highs[-(dual_thrust_days + 1):-1])
    hc = max(closes[-(dual_thrust_days + 1):-1])
    ll = min(lows[-(dual_thrust_days + 1):-1])
    lc = min(closes[-(dual_thrust_days + 1):-1])
    dual_thrust_range = max(hh - lc, hc - ll)
    dual_thrust_upper = last_open + dual_thrust_range * 0.20

    trigger_type = ""
    trigger_reason = ""
    if pct_change >= 9.5:
        trigger_type = "limit_up_momentum"
        trigger_reason = "daily limit-up style momentum"
    elif (
        last_high >= dual_thrust_upper
        and last_high < prev_20_high * 0.995
        and pct_change >= 3.0
        and volume_ratio >= 1.2
    ):
        trigger_type = "dual_thrust_breakout"
        trigger_reason = "daily dual thrust breakout above dynamic upper bound"
    elif pct_change >= 5.0 and last_high >= prev_20_high * 0.995 and volume_ratio >= 1.2:
        trigger_type = "momentum_breakout"
        trigger_reason = "daily breakout above prior 20-day high with volume expansion"
    elif pct_change >= 3.0 and last_close >= prev_20_close_high * 0.99 and trend_pct_60 >= 8.0:
        trigger_type = "strong_relative_strength"
        trigger_reason = "daily relative-strength continuation near prior swing high"
    elif pct_change >= 2.0 and volume_ratio >= 1.8 and intraday_range_pct >= 3.0:
        trigger_type = "active_turnover_push"
        trigger_reason = "daily turnover push with expanding range"

    if not trigger_type:
        return None

    trigger_score = round(
        pct_change * 8.0
        + min(max(volume_ratio, 0.0), 5.0) * 10.0
        + min(max(trend_pct_60, 0.0), 60.0) * 0.5
        + min(max(breakout_strength, 0.0), 10.0) * 6.0,
        2,
    )

    return {
        "asof_date": asof_date,
        "close": round(last_close, 2),
        "open": round(last_open, 2),
        "high": round(last_high, 2),
        "low": round(last_low, 2),
        "pct_change": round(pct_change, 2),
        "volume_ratio": round(volume_ratio, 2),
        "trend_pct_60": round(trend_pct_60, 2),
        "breakout_strength": round(breakout_strength, 2),
        "trigger_type": trigger_type,
        "trigger_reason": trigger_reason,
        "trigger_score": trigger_score,
    }


def _build_setup_tag(
    *, trigger_type: str, pct_change: float, turnover_rate: float, volume_ratio: float
) -> str:
    if trigger_type == "limit_up_momentum":
        if turnover_rate >= 18.0:
            return "涨停后高换手分歧"
        if turnover_rate >= 10.0:
            return "涨停后分歧承接"
        return "涨停强势延续"
    if trigger_type == "dual_thrust_breakout":
        return "dual_thrust_breakout"
    if trigger_type == "momentum_breakout":
        if volume_ratio >= 2.4 and pct_change >= 8.0:
            return "强势放量抢筹"
        return "放量突破" if volume_ratio >= 2.0 and pct_change >= 6.0 else "强势突破跟进"
    if trigger_type == "active_turnover_push":
        if turnover_rate >= 15.0:
            return "高换手爆量博弈"
        if turnover_rate >= 10.0:
            return "高换手博弈"
        return "活跃换手拉升"
    if pct_change >= 4.0:
        return "板块跟涨"
    return "相对强势整理"


def _import_wtpy(wtpy_root: Path) -> None:
    root_str = str(wtpy_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


class _SignalCaptureWriter:
    def __init__(self, base_cls: type) -> None:
        self._base_cls = base_cls
        self.rows: list[dict[str, Any]] = []

    def as_writer(self) -> Any:
        parent_rows = self.rows
        base_cls = self._base_cls

        class _Writer(base_cls):  # type: ignore[misc, valid-type]
            def write_indicator(self, strategy_id: str, tag: str, time: int, data: dict) -> None:
                parent_rows.append(
                    {
                        "strategy_id": strategy_id,
                        "tag": tag,
                        "time": time,
                        "data": dict(data),
                    }
                )

        return _Writer()


def export_candidates_via_real_wondertrader(
    *,
    repo_root: Path | str,
    trade_date: str,
    top_n: int,
    prefilter_limit: int = DEFAULT_PREFILTER_LIMIT,
    scan_source: str = DEFAULT_REAL_SCAN_SOURCE,
) -> list[dict[str, Any]]:
    repo_path = Path(repo_root)
    wtpy_root = repo_path.parent / "WonderTrader" / "wtpy"
    if not wtpy_root.exists():
        raise FileNotFoundError(f"wtpy root not found: {wtpy_root}")

    _import_wtpy(wtpy_root)

    from wtpy import BaseCtaStrategy, CtaContext, EngineType, WtBtEngine  # type: ignore
    from wtpy.ExtModuleDefs import BaseExtDataLoader  # type: ignore
    from wtpy.ExtToolDefs import BaseIndexWriter  # type: ignore
    from wtpy.WtCoreDefs import WTSBarStruct  # type: ignore

    prefilter_rows = select_prefilter_snapshots(repo_path, prefilter_limit=prefilter_limit)
    if not prefilter_rows:
        return []

    stock_profiles = load_stock_profile_map(repo_path)
    demo_runtime = wtpy_root / "demos" / "cta_stk_bt"
    os.chdir(demo_runtime)

    class RepoHistoryLoader(BaseExtDataLoader):  # type: ignore[misc, valid-type]
        def load_final_his_bars(self, stdCode: str, period: str, feeder) -> bool:
            if period != "d1":
                return False

            normalized = strip_wt_adjust_suffix(stdCode)
            code = normalized.split(".")[-1]
            rows = load_history_rows(repo_path, code)
            rows = _truncate_history_rows_to_trade_date(rows, trade_date=trade_date)
            rows = repair_history_rows_from_amount(rows)
            if not rows:
                return False

            buffer_type = WTSBarStruct * len(rows)
            buffer = buffer_type()
            for idx, row in enumerate(rows):
                cur = buffer[idx]
                cur.date = int(str(row["date"]).replace("-", ""))
                cur.time = 0
                cur.open = _to_float(row.get("open"))
                cur.high = _to_float(row.get("high"))
                cur.low = _to_float(row.get("low"))
                cur.close = _to_float(row.get("close"))
                cur.vol = _to_float(row.get("volume"))
                cur.money = _to_float(row.get("amount"))
                cur.hold = 0

            feeder(buffer, len(rows))
            return True

        def load_adj_factors(self, stdCode: str = "", feeder=None) -> bool:
            if feeder is not None and stdCode:
                feeder(strip_wt_adjust_suffix(stdCode), [19900101], [1.0])
            return True

    results: list[dict[str, Any]] = []
    for snapshot in prefilter_rows:
        code = str(snapshot["code"])
        history_rows = load_history_rows(repo_path, code)
        history_rows = _truncate_history_rows_to_trade_date(history_rows, trade_date=trade_date)
        history_rows = repair_history_rows_from_amount(history_rows)
        if len(history_rows) < MIN_SIGNAL_HISTORY_BARS:
            continue

        class ShortlineSignalStrategy(BaseCtaStrategy):  # type: ignore[misc, valid-type]
            def __init__(self, name: str) -> None:
                super().__init__(name)
                self.bar_code = f"{to_wt_std_code(code)}-"
                self.latest_signal: dict[str, Any] | None = None

            def on_init(self, context: CtaContext) -> None:
                context.stra_prepare_bars(self.bar_code, "d1", 80, isMain=True)

            def on_calculate(self, context: CtaContext) -> None:
                bars = context.stra_get_bars(self.bar_code, "d1", 80, isMain=True)
                if bars is None:
                    self.latest_signal = None
                    return

                row_count = len(bars.closes)
                if row_count < MIN_SIGNAL_HISTORY_BARS:
                    self.latest_signal = None
                    return

                rows: list[dict[str, Any]] = []
                for idx in range(row_count):
                    rows.append(
                        {
                            "date": str(int(bars.bartimes[idx]))[:8],
                            "open": float(bars.opens[idx]),
                            "high": float(bars.highs[idx]),
                            "low": float(bars.lows[idx]),
                            "close": float(bars.closes[idx]),
                            "volume": float(bars.volumes[idx]),
                        }
                    )

                signal = compute_shortline_signal_from_history_rows(rows)
                if signal is not None:
                    signal["asof_date"] = (
                        f"{signal['asof_date'][:4]}-{signal['asof_date'][4:6]}-{signal['asof_date'][6:8]}"
                        if len(str(signal["asof_date"])) == 8 and "-" not in str(signal["asof_date"])
                        else signal["asof_date"]
                    )
                self.latest_signal = signal

            def on_backtest_end(self, context: CtaContext) -> None:
                if self.latest_signal is None:
                    return
                context.write_indicator("candidate", 0, dict(self.latest_signal))

        writer_holder = _SignalCaptureWriter(BaseIndexWriter)
        writer = writer_holder.as_writer()
        engine = WtBtEngine(EngineType.ET_CTA)
        engine.set_writer(writer)
        engine.set_extended_data_loader(loader=RepoHistoryLoader(), bAutoTrans=False)
        engine.init("../common/", "configbt.yaml", commfile="stk_comms.json", contractfile="stocks.json")
        engine.configBacktest(
            int(str(history_rows[0]["date"]).replace("-", "")) * 10000,
            int(str(history_rows[-1]["date"]).replace("-", "")) * 10000 + 1500,
        )
        engine.configBTStorage(mode="csv", path="../storage/")
        engine.commitBTConfig()
        engine.set_cta_strategy(ShortlineSignalStrategy(name=f"shortline_{code}"))
        engine.run_backtest(bNeedDump=False)
        engine.release_backtest()

        if not writer_holder.rows:
            continue

        signal = dict(writer_holder.rows[-1]["data"])
        profile = stock_profiles.get(code, {})
        latest_price = _prefer_positive_metric(
            snapshot.get("latest_price"),
            signal.get("close") or history_rows[-1].get("close"),
        )
        pct_change = _prefer_nonzero_metric(snapshot.get("pct_change"), signal.get("pct_change"))
        volume_ratio = _prefer_positive_metric(
            snapshot.get("volume_ratio"),
            signal.get("volume_ratio"),
        )
        turnover_rate = _prefer_positive_metric(snapshot.get("turnover_rate"), None)
        amount = _prefer_positive_metric(
            snapshot.get("amount"),
            history_rows[-1].get("amount"),
        )
        change_pct_60d = _prefer_nonzero_metric(
            snapshot.get("change_pct_60d"),
            _compute_history_change_pct(history_rows, lookback_days=60),
        )
        board_name = str(
            snapshot.get("industry")
            or profile.get("industry")
            or snapshot.get("board_name")
            or "unknown_board"
        ).strip()
        signal_asof_date = str(signal.get("asof_date") or history_rows[-1]["date"]).strip()
        risk_flags: list[str] = []
        if signal_asof_date != trade_date and not _is_non_trading_trade_date(repo_root, trade_date):
            risk_flags.append(f"history_asof_{signal_asof_date}")
        reconstructed_recent_volume = any(
            bool(row.get("_volume_reconstructed_from_amount")) for row in history_rows[-5:]
        )
        if reconstructed_recent_volume:
            if _should_downgrade_reconstructed_volume_risk(
                history_rows,
                snapshot_volume_ratio=volume_ratio,
            ):
                risk_flags.append("volume_ratio_validated_by_amount_history")
            else:
                risk_flags.append("volume_reconstructed_from_amount")
        elif volume_ratio <= 0 and _history_has_missing_volume(history_rows):
            risk_flags.append("missing_volume_history")

        results.append(
            {
                "candidate_id": f"{trade_date}-{code}",
                "symbol": code,
                "name": str(snapshot.get("name") or profile.get("name") or code).strip(),
                "trade_date": trade_date,
                "scan_source": scan_source,
                "trigger_type": str(signal["trigger_type"]),
                "trigger_reason": (
                    f"{signal['trigger_reason']}; "
                    f"engine_asof={signal_asof_date}; "
                    f"pct_change={pct_change:.2f}; volume_ratio={volume_ratio:.2f}"
                ),
                "trigger_score": _to_float(signal.get("trigger_score")),
                "price": latest_price,
                "change_pct": pct_change,
                "change_pct_60d": change_pct_60d,
                "amount": amount,
                "volume_ratio": volume_ratio,
                "turnover_rate": turnover_rate,
                "board_name": board_name,
                "setup_tag": _build_setup_tag(
                    trigger_type=str(signal["trigger_type"]),
                    pct_change=pct_change,
                    turnover_rate=turnover_rate,
                    volume_ratio=volume_ratio,
                ),
                "risk_flags": risk_flags,
            }
        )

    results.sort(
        key=lambda item: (
            _to_float(item.get("trigger_score")),
            _to_float(item.get("change_pct")),
            _to_float(item.get("volume_ratio")),
            _to_float(item.get("turnover_rate")),
        ),
        reverse=True,
    )
    return results[: max(0, int(top_n))]
