#!/usr/bin/env bash
# Same-window PIT rebuild for the offense-line comparison (default 2026-08-03 .. 2026-09-24).
#
# Usage:
#   bash scripts/pit_rebuild_offense_window.sh [START] [END]
#
# Each day runs under data/runtime/pit_run.py (fakes "today" so the history cache is
# sliced to <= that date and rules evaluate strictly point-in-time). Snapshots are
# persisted to the DB with signal_date == target date, so the standard evaluator can
# compute forward returns for every included line on a COMMON window afterwards.
#
# Lines rebuilt here: hundred_day_high / daily_slow_rise / trend_leader_unified.
# (earnings_surprise has no snapshots after 2026-07-10 and needs fundamental
#  snapshots for point-in-time rebuild, so it is out of scope for this batch.)
#
# Runtime note: full-market scans for ~40 trading days x 3 lines can take hours;
# run it overnight, e.g.:
#   nohup bash scripts/pit_rebuild_offense_window.sh > data/run_logs/pit_window_rebuild.log 2>&1 &
set -u

cd /home/wen/myfile/code/daily_stock_analysis
export HISTORY_DISK_CACHE_DIR="$PWD/data/cache/history_pit"

PY="./.venv-linux/bin/python"
ROOT="data/manual_runs/pit_rebuild_window"
START="${1:-2026-08-03}"
END="${2:-2026-09-24}"

DAYS=$($PY - "$START" "$END" <<'PY'
import sys
from datetime import date, timedelta

start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
out, cur = [], start
while cur <= end:
    if cur.weekday() < 5:
        out.append(cur.isoformat())
    cur += timedelta(days=1)
print(" ".join(out))
PY
)

echo "PIT window rebuild: $START ~ $END, trading-day candidates=$(echo "$DAYS" | wc -w)"

for D in $DAYS; do
  OUT="$ROOT/$D/signals"
  mkdir -p "$OUT/hundred_day_high" "$OUT/daily_slow_rise" "$OUT/trend_leader_unified"

  echo "########## [$D] hundred_day_high"
  $PY data/runtime/pit_run.py "$D" scripts/select_hundred_day_high_candidates.py \
    --snapshot-date "$D" --signal-type hundred_day_high --profile breakout_loose \
    --output-dir "$OUT/hundred_day_high" \
    --checkpoint-path "$OUT/hundred_day_high/hundred_day_high_checkpoint.json" \
    --max-workers 4 --log-level INFO \
    --min-listed-days-prefilter 120 --min-60d-change-pct-prefilter 12.0 \
    --min-turnover-rate-prefilter 0.8 --require-positive-change-prefilter --exclude-st-prefilter \
    || echo "FAILED hdh $D"

  echo "########## [$D] daily_slow_rise"
  $PY data/runtime/pit_run.py "$D" scripts/select_daily_slow_rise_candidates.py \
    --snapshot-date "$D" --signal-type daily_slow_rise --profile review_balanced \
    --output-dir "$OUT/daily_slow_rise" \
    --checkpoint-path "$OUT/daily_slow_rise/daily_slow_rise_checkpoint.json" \
    --max-workers 4 --log-level INFO \
    || echo "FAILED dsr $D"

  echo "########## [$D] trend_leader_unified"
  $PY data/runtime/pit_run.py "$D" scripts/select_trend_leader_candidates.py \
    --snapshot-date "$D" --signal-type trend_leader_unified \
    --max-workers 2 --log-level INFO \
    || echo "FAILED trend $D"
done

echo "PIT WINDOW REBUILD DONE: $START ~ $END"
