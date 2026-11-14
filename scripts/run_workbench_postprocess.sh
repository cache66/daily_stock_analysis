#!/usr/bin/env bash
# 已有 matrix 包装器在生产者 done 后调用；只编排本地研究后处理，不新增调度器。
# 用法：bash scripts/run_workbench_postprocess.sh YYYY-MM-DD [python_executable]
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root" || exit 1
board_date=${1:?Usage: run_workbench_postprocess.sh YYYY-MM-DD [python_executable]}
python_bin=${2:-python3}
post_rc=0

run_step() {
  local step=$1
  shift
  "$python_bin" "$@"
  local step_rc=$?
  echo "[workbench_postprocess] step=$step exit=$step_rc"
  if (( step_rc != 0 )); then post_rc=1; fi
  return "$step_rc"
}

sync_rc=0
run_step cache_sync scripts/sync_cache_to_stock_daily.py || sync_rc=$?
run_step outcomes scripts/evaluate_track_outcomes.py --as-of "$board_date" || true
regime_rc=0
run_step signal_regime scripts/build_signal_regime.py --as-of "$board_date" || regime_rc=$?
if (( sync_rc == 0 && regime_rc == 0 )); then
  run_step admission scripts/track_from_board.py --date "$board_date" || true
else
  post_rc=1
  echo "[workbench_postprocess] step=admission skipped=dependencies_not_ready"
fi
# 方法层：发现 → 建跟踪 → 状态（失败不阻断后续统计，但会记录退出码）
run_step methods scripts/run_shortline_methods.py --date "$board_date" || true

stats_rc=0
run_step stats scripts/build_replay_stats.py --as-of "$board_date" || stats_rc=$?
run_step cards scripts/build_experience_cards.py --as-of "$board_date" || true
run_step board scripts/build_today_board.py --date "$board_date" || true
# 复盘缓存（东财涨停/炸板池）：失败只降级为日线近似，不阻塞后续步骤
run_step review scripts/build_market_review.py --date "$board_date" || true
# 首页缓存预热：让浏览器冷启动直接命中 data/cache/workbench
run_step home scripts/build_workbench_home.py --date "$board_date" || true
if (( sync_rc == 0 && regime_rc == 0 && stats_rc == 0 )); then
  run_step preview scripts/build_workbench_preview.py --date "$board_date" || true
else
  echo "[workbench_postprocess] step=preview skipped=dependencies_not_ready"
fi
exit "$post_rc"
