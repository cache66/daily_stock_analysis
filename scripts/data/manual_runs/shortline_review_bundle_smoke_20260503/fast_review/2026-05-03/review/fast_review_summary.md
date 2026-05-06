# 快复盘汇总（2026-05-03）

## 运行摘要
- 生成时间：`2026-05-03T22:06:07`
- 汇总 CSV：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_candidates.csv`
- 共振 CSV：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_resonance.csv`
- 共振 Markdown：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_resonance.md`
- 共振候选数：`0`
- 信号总耗时：`0.0s`

## 分信号结果
| signal_key | signal_type | count | elapsed_sec | csv |
| --- | --- | ---: | ---: | --- |

## 策略精简焦点
- 焦点 CSV：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_strategy_focus.csv`
- 焦点 Markdown：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_strategy_focus.md`
- 候选总数：`0`
- trend_leader_unified ∩ hundred_day_high：`0`
- trend_leader_unified only：`0`
- hundred_day_high only：`0`

### 核心候选（top 0 / 0）
- none

### 观察候选（top 0 / 0）
- none

### 低优先级候选（top 0 / 0）
- none

## 强势股上涨原因摘要
- none

## 今日业绩焦点 15 只
- 业绩焦点 CSV：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_earnings_focus.csv`
- 业绩焦点 Markdown：`data\manual_runs\shortline_review_bundle_smoke_20260503\fast_review\2026-05-03\review\fast_review_earnings_focus.md`
- 候选总数：`0`

- none

## Skipped / No-result Signals
| signal_key | signal_type | reason | detail |
| --- | --- | --- | --- |
| hundred_day_high | hundred_day_high | no_rows | no candidate rows loaded from csv |

## 后续独立命令（按需执行）
```bash
python scripts/select_trend_leader_candidates.py --snapshot-date 2026-05-03 --signal-type trend_leader_unified
python scripts/select_earnings_surprise_candidates.py --snapshot-date 2026-05-03 --strategy-profile balanced
python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high --start-date 2026-05-03 --end-date 2026-05-03 --windows 1,3,5,10
```

## 短线观察
- trade_date=2026-05-03 top_pick=0 watchlist=0 high_risk=3
- top_symbols: none
