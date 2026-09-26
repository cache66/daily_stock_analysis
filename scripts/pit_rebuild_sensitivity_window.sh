#!/usr/bin/env bash
# §4-5 阈值敏感性重建（±20%），与 pit_rebuild_offense_window.sh 同窗同流程。
#
# 变体与口径（2026-09-26 跑数前写死，见 docs/个人策略文档/策略收敛与回测路线图.md §4）：
#   hdh   : --new-high-window 80 -> 64 / 96                （基准 80，±20%）
#   trend : --scan-prefilter-min-change-pct-60d 3.0 -> 2.4 / 3.6（基准 3.0，±20%）
#
# 独立 signal_type（不覆盖正式快照）：
#   sens__hdh_nhw64 / sens__hdh_nhw96
#   sens__trend_c60d2.4 / sens__trend_c60d3.6
#
# 用法：
#   bash scripts/pit_rebuild_sensitivity_window.sh <hdh|trend> <变体> [START] [END]
# 例（一个变体一条链，可并行 4 链）：
#   nohup bash scripts/pit_rebuild_sensitivity_window.sh hdh nhw64   > data/run_logs/sens_hdh_nhw64.log   2>&1 &
#   nohup bash scripts/pit_rebuild_sensitivity_window.sh trend c60d2.4 > data/run_logs/sens_trend_c60d2.4.log 2>&1 &
#
# 事后汇总（批次跑完后）：
#   用 ./scripts/check_random_percentile.py 同款 build_report 调用逐变体评估，
#   对比 §4 条目 1~4 的结论方向；产物建议落 data/verification/。
set -u

cd /home/wen/myfile/code/daily_stock_analysis
export HISTORY_DISK_CACHE_DIR="$PWD/data/cache/history_pit"

PY="./.venv-linux/bin/python"
ROOT="data/manual_runs/pit_sensitivity_window"
LINE="${1:?line: hdh|trend}"
VARIANT="${2:?variant: nhw64|nhw96|c60d2.4|c60d3.6}"
START="${3:-2026-08-04}"
END="${4:-2026-09-24}"

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

case "$LINE/$VARIANT" in
  hdh/nhw64)
    SIGNAL_TYPE="sens__hdh_nhw64"
    ;;
  hdh/nhw96)
    SIGNAL_TYPE="sens__hdh_nhw96"
    ;;
  trend/c60d2.4)
    SIGNAL_TYPE="sens__trend_c60d2.4"
    ;;
  trend/c60d3.6)
    SIGNAL_TYPE="sens__trend_c60d3.6"
    ;;
  *)
    echo "unknown variant: $LINE/$VARIANT (expect hdh/nhw64|nhw96 or trend/c60d2.4|c60d3.6)" >&2
    exit 2
    ;;
esac

echo "PIT sensitivity rebuild: line=$LINE variant=$VARIANT signal_type=$SIGNAL_TYPE $START ~ $END, trading-day candidates=$(echo "$DAYS" | wc -w)"

for D in $DAYS; do
  if [ "$LINE" = "hdh" ]; then
    OUT="$ROOT/$VARIANT/$D/signals"
    mkdir -p "$OUT/hundred_day_high"
    case "$VARIANT" in
      nhw64) WINDOW=64 ;;
      nhw96) WINDOW=96 ;;
    esac
    echo "########## [$D] $SIGNAL_TYPE"
    $PY data/runtime/pit_run.py "$D" scripts/select_hundred_day_high_candidates.py \
      --snapshot-date "$D" --signal-type "$SIGNAL_TYPE" --profile breakout_loose \
      --new-high-window "$WINDOW" \
      --output-dir "$OUT/hundred_day_high" \
      --checkpoint-path "$OUT/hundred_day_high/hundred_day_high_checkpoint.json" \
      --max-workers 4 --log-level INFO \
      --min-listed-days-prefilter 120 --min-60d-change-pct-prefilter 12.0 \
      --min-turnover-rate-prefilter 0.8 --require-positive-change-prefilter --exclude-st-prefilter \
      || echo "FAILED $SIGNAL_TYPE $D"
  else
    case "$VARIANT" in
      c60d2.4) THRESHOLD=2.4 ;;
      c60d3.6) THRESHOLD=3.6 ;;
    esac
    echo "########## [$D] $SIGNAL_TYPE"
    $PY data/runtime/pit_run.py "$D" scripts/select_trend_leader_candidates.py \
      --snapshot-date "$D" --signal-type "$SIGNAL_TYPE" \
      --scan-prefilter-min-change-pct-60d "$THRESHOLD" \
      --max-workers 2 --log-level INFO \
      || echo "FAILED $SIGNAL_TYPE $D"
  fi
done

echo "PIT SENSITIVITY DONE: $SIGNAL_TYPE $START ~ $END"
