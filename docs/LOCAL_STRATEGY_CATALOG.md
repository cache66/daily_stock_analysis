# 本地策略目录（Local Strategy Catalog）

最后更新：2026-04-25

本文档回答 3 个问题：

1. 哪些本地策略脚本可直接运行；
2. 默认每日到底跑哪些；
3. 每条策略落什么 `signal_type`、该看哪份说明。

## 1. 默认每日主链路

主入口：

```bash
python scripts/run_fast_review_bundle.py --strategy-profile-file config/local_strategy_profile.json
```

当前默认每日只保留 3 条核心策略：

- `earnings` -> `earnings_surprise`
- `hundred_day_high`
- `trend_leader` -> `trend_leader_unified`

默认不进入每日：

- `monthly_slow_rise`（低频扩展）
- `continuous_up_ratio` / `continuous_up_streak`（观察层）

## 2. 策略资产总表

| 层级 | 策略/信号 | 入口脚本 | 默认 `signal_type` | 默认每日 | 说明文档 |
| --- | --- | --- | --- | --- | --- |
| 核心 | `trend_leader_unified` | `scripts/select_trend_leader_candidates.py` | `trend_leader_unified` | 是 | [`docs/TREND_LEADER_UNIFIED_STRATEGY.md`](./TREND_LEADER_UNIFIED_STRATEGY.md) |
| 核心 | `earnings_surprise` | `scripts/select_earnings_surprise_candidates.py` | `earnings_surprise` | 是 | [`docs/EARNINGS_SURPRISE_TRACKING.md`](./EARNINGS_SURPRISE_TRACKING.md) |
| 核心 | `hundred_day_high` | `scripts/select_hundred_day_high_candidates.py` | `hundred_day_high` | 是 | [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md) |
| 扩展 | `monthly_slow_rise` | `scripts/select_monthly_slow_rise_candidates.py` | `monthly_slow_rise` | 否 | [`docs/MONTHLY_SLOW_RISE_SCAN.md`](./MONTHLY_SLOW_RISE_SCAN.md) |
| 观察 | `continuous_up_ratio` | `scripts/run_fast_review_bundle.py`（内部收集） | `continuous_up_ratio` | 否 | [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md) |
| 观察 | `continuous_up_streak` | `scripts/run_fast_review_bundle.py`（内部收集） | `continuous_up_streak` | 否 | [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md) |
| 专题 | `dragon_head_candidate` | `scripts/select_dragon_head_candidates.py` | `dragon_head_candidate` | 否 | [`docs/DRAGON_HEAD_CANDIDATE_SCAN.md`](./DRAGON_HEAD_CANDIDATE_SCAN.md) |
| 专题 | `theme_core_mapper` | `scripts/select_theme_core_candidates.py` | `theme_core_mapper` | 否 | [`docs/THEME_CORE_MAPPER.md`](./THEME_CORE_MAPPER.md) |
| 专题 | `commodity_price_pass_through` | `scripts/select_commodity_beneficiaries.py` | `commodity_price_pass_through` | 否 | [`docs/COMMODITY_PRICE_PASS_THROUGH.md`](./COMMODITY_PRICE_PASS_THROUGH.md) |
| 工具 | 通用 K 线筛选 | `scripts/select_kline_candidates.py` | 无固定落库命名 | 否 | [`docs/KLINE_SELECTOR_GUIDE.md`](./KLINE_SELECTOR_GUIDE.md) |

## 3. 四条主策略关键参数速查

| 策略 | 推荐命令 | 最关键参数 | 当前默认口径 |
| --- | --- | --- | --- |
| `trend_leader_unified` | `python scripts/select_trend_leader_candidates.py` | `--fallback-top-n` `--scan-prefilter-*` | `fallback_top_n=20`，并启用上市天数/涨幅/换手前筛 |
| `earnings_surprise` | `python scripts/select_earnings_surprise_candidates.py` | `--strategy-profile` `--scan-depth` `--recent-event-scope` | 日常 `balanced + low + latest_report_period` |
| `hundred_day_high` | `python scripts/select_hundred_day_high_candidates.py` | `--profile` `--skip-cause-analysis` | 日常入口默认 `profile=breakout_loose` 且跳过归因 |
| `monthly_slow_rise` | `python scripts/select_monthly_slow_rise_candidates.py --profile robust` | `--profile` `--min-listed-days-prefilter` | 低频运行，`robust` 常用于慢牛池 |

## 4. 与 `/signals` 的关系

这些脚本写入 `kline_signal_snapshot` 后，会出现在 `/signals` 页面。

常用 API：

- `GET /api/v1/signals/kline-snapshots?signal_type=<type>&signal_date=<YYYY-MM-DD>`
- `GET /api/v1/signals/kline-snapshot-counts?signal_date=<YYYY-MM-DD>`

## 5. 文档治理要求

本目录是“入口索引”，不记录长历史运行日志。  
策略行为变更请写入：

- [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md)：当前默认行为与参数
- [`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)：按日期记录变更
- [`docs/CHANGELOG.md`](./CHANGELOG.md)：`[Unreleased]` 单行扁平记录
