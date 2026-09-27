#!/usr/bin/env bash
# 统一验证套件：一条命令跑「同口径三件套 + 覆盖度注记」，归档到 data/strategy_review/suite_<tag>/。
#
# 用法:
#   bash scripts/run_validation_suite.sh <start-date> <end-date> <tag>
# 例:
#   bash scripts/run_validation_suite.sh 2026-04-20 2026-09-24 apr_sep_filled
#
# 口径（与《路线图》§4 判定一致，写死不随参数漂移）：
#   - 成本 31bps（slip10 + fee3 双边 + turnover5 → v2t）
#   - 入场 daily（stock_daily 前复权）、可成交性 entry、基准 000300
#   - 随机对照 = 同窗 random_baseline__<线>；分位检验按日块 bootstrap（5000 轮、种子 20260926）
#   - hdh 组合变体：全量 / Top5（按 breakout_quality_score，2026-09-27 验证的有效用法）
#
# 注意：本套件只读取现有快照（kline_signal_snapshot）+ stock_daily；
#   快照缺口窗口请先用 `bash scripts/pit_rebuild_offense_window.sh <start> <end>` 补齐后重跑。
set -u
cd /home/wen/myfile/code/daily_stock_analysis || exit 1

START="${1:?用法: bash scripts/run_validation_suite.sh <start> <end> <tag>}"
END="${2:?用法: bash scripts/run_validation_suite.sh <start> <end> <tag>}"
TAG="${3:?用法: bash scripts/run_validation_suite.sh <start> <end> <tag>}"

PY="./.venv-linux/bin/python"
OUT="data/strategy_review/suite_${TAG}"
SUMMARY="${OUT}/SUMMARY_${TAG}.md"
SIGNALS="trend_leader_unified,hundred_day_high,daily_slow_rise,earnings_surprise"
mkdir -p "${OUT}"

FAIL=0

{
  echo "# 验证套件结果（${TAG}）"
  echo
  echo "- 窗口: ${START} ~ ${END}"
  echo "- 生成时间: $(date '+%F %T')"
  echo "- 复现命令: \`bash scripts/run_validation_suite.sh ${START} ${END} ${TAG}\`"
  echo
  echo "## 1) leaderboard（成本后 w1/w3/w5 + 分月 + 随机对照）"
  echo
  echo '```'
} > "${SUMMARY}"

$PY scripts/run_strategy_leaderboard.py \
  --signals "${SIGNALS}" --start-date "${START}" --end-date "${END}" --benchmark-code 000300 \
  --output-md "${OUT}/leaderboard_${TAG}.md" --output-json "${OUT}/leaderboard_${TAG}.json" \
  >> "${SUMMARY}" 2>&1 || FAIL=1

{
  echo '```'
  echo
  echo "## 2) 随机 95 分位（§4-6，判定窗口 w3）"
  echo
} >> "${SUMMARY}"

# 分位检验只跑「窗口内既有快照、又有同窗随机对照」的线；零样本线跳过并注明。
PCT_SIGNALS=$($PY - "${START}" "${END}" "${SIGNALS}" <<'PYEOF'
import sqlite3
import sys

start, end, signals = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
con = sqlite3.connect("data/stock_analysis.db")
keep = []
for sig in signals:
    has = con.execute(
        "SELECT COUNT(*) FROM kline_signal_snapshot WHERE signal_type=? AND signal_date>=? AND signal_date<=?",
        (sig, start, end),
    ).fetchone()[0]
    has_rand = con.execute(
        "SELECT COUNT(*) FROM kline_signal_snapshot WHERE signal_type=? AND signal_date>=? AND signal_date<=?",
        (f"random_baseline__{sig}", start, end),
    ).fetchone()[0]
    if has and has_rand:
        keep.append(sig)
con.close()
print(",".join(keep))
PYEOF
)

if [ -n "${PCT_SIGNALS}" ]; then
  echo '```' >> "${SUMMARY}"
  $PY scripts/check_random_percentile.py \
    --signals "${PCT_SIGNALS}" --start-date "${START}" --end-date "${END}" \
    --output-json "${OUT}/random_percentile_${TAG}.json" >> "${SUMMARY}" 2>&1 || FAIL=1
  echo '```' >> "${SUMMARY}"
else
  echo "（本窗口内无「既含快照、又含同窗随机对照」的线，分位检验跳过。）" >> "${SUMMARY}"
fi

{
  echo
  echo "## 3) 组合层（等权滚动 w5；trend 全量 / hdh 全量 / hdh Top5）"
  echo
  echo '```'
} >> "${SUMMARY}"

$PY scripts/analyze_signal_portfolio.py \
  --signal-type trend_leader_unified --start-date "${START}" --end-date "${END}" \
  --output-md "${OUT}/portfolio_trend_all_${TAG}.md" --output-json "${OUT}/portfolio_trend_all_${TAG}.json" \
  >> "${SUMMARY}" 2>&1 || FAIL=1
$PY scripts/analyze_signal_portfolio.py \
  --signal-type hundred_day_high --start-date "${START}" --end-date "${END}" \
  --output-md "${OUT}/portfolio_hdh_all_${TAG}.md" --output-json "${OUT}/portfolio_hdh_all_${TAG}.json" \
  >> "${SUMMARY}" 2>&1 || FAIL=1
$PY scripts/analyze_signal_portfolio.py \
  --signal-type hundred_day_high --start-date "${START}" --end-date "${END}" \
  --top-n 5 --rank-by breakout_quality_score \
  --output-md "${OUT}/portfolio_hdh_top5_${TAG}.md" --output-json "${OUT}/portfolio_hdh_top5_${TAG}.json" \
  >> "${SUMMARY}" 2>&1 || FAIL=1

{
  echo '```'
  echo
  echo "## 4) 快照覆盖度与口径注记"
  echo
  $PY - "${START}" "${END}" "${SIGNALS}" <<'PYEOF'
import sqlite3
import sys

start, end, signals = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
con = sqlite3.connect("data/stock_analysis.db")
lo, hi, cnt = con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM stock_daily").fetchone()
print(f"- stock_daily 覆盖: {lo} ~ {hi}（{cnt} 行）")
print()
print("| 信号 | 月份 | 条数 |")
print("| --- | --- | ---: |")
for sig in signals + [f"random_baseline__{s}" for s in signals]:
    rows = con.execute(
        "SELECT substr(signal_date,1,7), COUNT(*) FROM kline_signal_snapshot "
        "WHERE signal_type=? AND signal_date>=? AND signal_date<=? GROUP BY 1 ORDER BY 1",
        (sig, start, end),
    ).fetchall()
    if not rows:
        print(f"| {sig} | （无） | 0 |")
    for mon, n in rows:
        print(f"| {sig} | {mon} | {n} |")
con.close()
print()
print("> 注：样本集中在少数月份或含旧管线时期时，判读需区分「实盘记录」与「同口径重建」；")
print("> 快照缺口用 `bash scripts/pit_rebuild_offense_window.sh <start> <end>` 补齐后重跑本套件。")
PYEOF
  echo
  echo "## 汇总"
  echo
  echo "- 退出码: FAIL=${FAIL}"
  echo "- 产物目录: \`${OUT}\`"
  echo "- 同类产物索引: leaderboard / random_percentile / portfolio_* 均带 \`_${TAG}\` 后缀，不覆盖历史。"
} >> "${SUMMARY}"

echo "[suite] 生成: ${SUMMARY}"
exit "${FAIL}"
