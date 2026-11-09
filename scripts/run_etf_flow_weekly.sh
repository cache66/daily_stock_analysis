#!/usr/bin/env bash
# ETF 份额流周报 · cron 入口（顶层择时 + 中层选板块）
#
# 用法：
#   ./scripts/run_etf_flow_weekly.sh            # 跑一次并落日志
#   ./scripts/run_etf_flow_weekly.sh --top 15   # 参数透传给 Python 脚本
#
# crontab 示例（周五收盘后 17:30；脚本会自动退到最近有数据的交易日）：
#   30 17 * * 5 cd /home/wen/myfile/code/daily_stock_analysis && ./scripts/run_etf_flow_weekly.sh >> /dev/null 2>&1
set -u

cd "$(dirname "$0")/.." || exit 1

PY=".venv-linux/bin/python"
[ -x "$PY" ] || PY="python3"

OUT_DIR="data/strategy_review/etf_flow"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/run-$(date +%F).log"

echo "=== $(date '+%F %T') 开始 ===" | tee -a "$LOG"
"$PY" scripts/etf_flow_weekly.py "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "=== $(date '+%F %T') 结束 rc=$rc ===" | tee -a "$LOG"
exit "$rc"
