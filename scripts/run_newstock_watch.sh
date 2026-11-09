#!/usr/bin/env bash
# 次新股「装死→放量」每日观察 · cron 入口
#
# 用法：
#   ./scripts/run_newstock_watch.sh              # 跑一次并落日志
#   ./scripts/run_newstock_watch.sh --top 10     # 参数透传给 Python 脚本
#
# crontab 示例（交易日 16:10 收盘后）：
#   10 16 * * 1-5 cd /home/wen/myfile/code/daily_stock_analysis && ./scripts/run_newstock_watch.sh >> /dev/null 2>&1
set -u

cd "$(dirname "$0")/.." || exit 1

PY=".venv-linux/bin/python"
[ -x "$PY" ] || PY="python3"

OUT_DIR="data/strategy_review/newstock_watch"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/run-$(date +%F).log"

echo "=== $(date '+%F %T') 开始 ===" | tee -a "$LOG"
"$PY" scripts/watch_newstock_deadwater.py "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "=== $(date '+%F %T') 结束 rc=$rc ===" | tee -a "$LOG"
exit "$rc"
