# 当前本地策略基线（Current Baseline）

最后更新：2026-04-25  
真源优先级：代码与脚本 > 本文档

## 1. 当前策略分层

| 层级 | 策略/信号 | 是否默认每日 | 说明 |
| --- | --- | --- | --- |
| 核心主策略 | `trend_leader_unified` | 是 | 龙头+趋势+资金+业绩的统一主骨架。 |
| 核心主策略 | `earnings_surprise` | 是 | 财报事件驱动 + 质量连续性 + 事件后反应。 |
| 核心主策略 | `hundred_day_high` | 是 | 新高突破确认层，含突破质量字段。 |
| 低频扩展 | `monthly_slow_rise` | 否 | 中期慢牛结构层，建议低频运行。 |
| 观察信号 | `continuous_up_ratio` | 否 | 近端上涨占比观察，不作为独立 alpha。 |
| 观察信号 | `continuous_up_streak` | 否 | 连涨天数观察，不作为独立 alpha。 |

默认每日是否启用，以 [`config/local_strategy_profile.json`](../config/local_strategy_profile.json) 的 `defaults.include_signals` 为准。

## 2. 默认每日入口与默认值

主入口：`python scripts/run_fast_review_bundle.py`

当前默认（配置文件 + 入口参数合并后的日常口径）：

- `include_signals = earnings,hundred_day_high,trend_leader`
- `external_parallelism = 3`
- `max_workers = 1`（外部脚本内部并发另有独立参数）
- `trend_max_workers = 2`
- `hundred_day_max_workers = 2`
- `continuous_max_workers = 2`
- `earnings_strategy_profile = balanced`
- `earnings_recent_event_scope = latest_report_period`
- `hundred_day_signal_type = hundred_day_high`
- `hundred_day_skip_cause_analysis = true`
- `trend_signal_type = trend_leader_unified`
- `trend_fallback_top_n = 20`
- `trend_disable_second_stage_enrichment = true`
- `trend_scan_prefilter_min_listed_days = 120`
- `trend_scan_prefilter_min_change_pct_60d = 4.0`
- `trend_scan_prefilter_min_turnover_rate = 1.0`
- `trend_scan_prefilter_require_positive_change = true`

## 3. 四条策略当前条件与参数

### 3.1 `trend_leader_unified`

脚本：`scripts/select_trend_leader_candidates.py`  
服务：`src/services/trend_leader_strategy_service.py`  
默认信号：`trend_leader_unified`

严格核心命中（strict core）主条件：

1. 龙头门槛不过弱：
   - `leader_type != pseudo_leader`
   - `leader_probability >= medium`
   - `recognizability_score >= 2`
   - `sector_leadership_score >= 1`
2. 趋势结构成立：`is_breakout_candidate` 或 `is_pullback_candidate`
3. 不命中关键阻断：如 `blocked_negative_text`、`blocked_quality_risk` 等
4. 综合得分 `overall_score > 0`

评分骨架（核心）：

- `breakout_score = 0.40*leader + 0.38*breakout_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `pullback_score = 0.40*leader + 0.38*pullback_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `hybrid_score = max(breakout,pullback) + 0.2*min(breakout,pullback) - risk_penalty`
- `overall_score = hybrid_score`

增强字段（已上线）：

- Stage-2：`trend_stage2_passed`、`trend_stage2_score`
- 行业领导力：`industry_leadership_score`、`board_leadership_rank_pct`、`board_breadth_score`
- 过热惩罚：`extension_risk_score`

fallback（严格命中为空时）：

- `tier1_near_miss`: `overall_score>=45`，无硬风险
- `tier2_watchlist`: `overall_score>=30`，硬风险最多 1 个
- `tier3_broader_pool`: `overall_score>=20`，组件分至少 20
- `tier4_last_resort`: `overall_score>0`，组件分至少 10
- `tier5_safety_net`: 仅保留非阻断样本

对应标记：`selection_mode`、`strict_core_hit`、`fallback_tier`

关键参数：

- `--fallback-top-n`（默认 20）
- `--scan-prefilter-min-listed-days`（默认 120）
- `--scan-prefilter-min-change-pct-60d`（脚本默认 3.0，日常配置覆盖为 4.0）
- `--scan-prefilter-min-turnover-rate`（脚本默认 0.8，日常配置覆盖为 1.0）
- `--scan-prefilter-require-positive-change`

### 3.2 `earnings_surprise`

脚本：`scripts/select_earnings_surprise_candidates.py`  
默认信号：`earnings_surprise`（strict/relaxed 会映射到各自 signal_type）

档位预设：

| 档位 | `min_revenue_yoy` | `min_net_profit_yoy` | `min_roe` | `direct/watch` |
| --- | --- | --- | --- | --- |
| `strict` | 15 | 30 | 8 | 60 / 45 |
| `balanced` | 10 | 20 | None | 55 / 35 |
| `relaxed` | 5 | 10 | None | 45 / 28 |

硬拦截（优先于评分）：

- `blocked_negative_text`
- `blocked_missing_positive_text`（当要求正向文本时）
- `blocked_missing_growth_thresholds`（当要求增长阈值时）
- `blocked_quality_risk`
- `blocked_duplicate_event`（默认开启事件去重）

通过规则：

1. `direct_score_reached`，或
2. `watch_score_reached` 且有确认信号（默认 `balanced/strict` 需质量确认）

评分新增重点（已上线）：

- 事件后反应：`earnings_post_event_1d_return_pct`、`earnings_post_event_3d_return_pct`、`earnings_post_event_reaction_label`
- 多季度 surprise 历史：`earnings_surprise_history_score` 等
- 持续质量：`earnings_financial_series_continuity_score`
- 行业确认：`earnings_industry_*`

日常默认：

- `--strategy-profile balanced`
- `--scan-depth low`
- `--recent-event-scope latest_report_period`

注意：脚本原生默认 `recent_event_scope=lookback`，每日入口通过配置文件覆盖为 `latest_report_period`。

### 3.3 `hundred_day_high`

脚本：`scripts/select_hundred_day_high_candidates.py`  
默认信号：`hundred_day_high`

Profile 预设（主用）：

| Profile | `lookback_days` | `min_up_ratio` | `new_high_window` | `max_total_mv_yi` |
| --- | --- | --- | --- | --- |
| `breakout_balanced` | 8 | 0.625 | 100 | 400 |
| `breakout_balanced_with_earnings` | 8 | 0.625 | 100 | 400 |
| `momentum_strict` | 8 | 0.67 | 110 | 70 |
| `breakout_loose` | 12 | 0.58 | 80 | 600 |

规则与过滤：

1. 新高规则：`require_new_high=True`
2. 预过滤（默认开启）：60 日涨幅、换手率、正涨幅、`exclude_st`、上市天数
3. 突破质量增强：
   - `breakout_quality_score`
   - `minervini_template_score / passed`
   - `breakout_follow_through_score`
4. 质量底线（按 profile）：
   - `momentum_strict >= 8`
   - `breakout_balanced* >= 6`
   - `breakout_loose >= 0`

日常默认：

- daily bundle 默认 `--hundred-day-profile breakout_loose`
- 默认 `--skip-cause-analysis`
- `hundred_day_max_workers=2`

### 3.4 `monthly_slow_rise`（低频扩展）

脚本：`scripts/select_monthly_slow_rise_candidates.py`  
默认信号：`monthly_slow_rise`

定位：不进默认每日，作为中期慢牛结构补充层。

Profile 核心阈值（节选）：

| Profile | 月线窗口 | 阳线占比 | 低点抬升占比 | 回撤上限 | `min_listed_days` |
| --- | --- | --- | --- | --- | --- |
| `strict` | 12 | 0.67 | 0.58 | 10% | 跟随历史要求 |
| `robust` | 15 | 0.60 | 0.60 | 12% | 400 |
| `balanced` | 12 | 0.58 | 0.50 | 15% | 跟随历史要求 |
| `loose` | 10 | 0.50 | 0.40 | 20% | 跟随历史要求 |

新增过滤（已上线）：

- 周线稳定：`weekly_positive_ratio`、`weekly_shallow_pullback_ratio`、`weekly_range_compression_ratio`、`weekly_volatility_percentile`
- 流动性：`avg_daily_amount_20d` 最低门槛
- 财务连续性：营收/利润连续正增长季度数与 `earnings_continuity_score`
- 行业确认：`industry_strength_*`

典型适用：周/月频复盘、中期结构池，不建议替代三条日常主策略。

## 4. 跨策略参数速查表

| 策略 | 主要脚本 | 关键参数 | 每日默认 |
| --- | --- | --- | --- |
| `trend_leader_unified` | `select_trend_leader_candidates.py` | `--fallback-top-n` `--scan-prefilter-*` | `fallback_top_n=20`；前筛阈值由 `local_strategy_profile.json` 覆盖 |
| `earnings_surprise` | `select_earnings_surprise_candidates.py` | `--strategy-profile` `--scan-depth` `--recent-event-scope` | `balanced + low + latest_report_period` |
| `hundred_day_high` | `select_hundred_day_high_candidates.py` | `--profile` `--skip-cause-analysis` `--min-listed-days-prefilter` | `profile=breakout_loose`；默认跳过归因 |
| `monthly_slow_rise` | `select_monthly_slow_rise_candidates.py` | `--profile` `--min-listed-days-prefilter` `--max-workers` | 不进默认每日，按需单跑 |

## 5. 文档同步约定

涉及本地策略资产变更（脚本、signal_type、默认参数、落库字段）时，必须同步更新：

- [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md)
- [`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md)
- [`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)
- [`docs/CHANGELOG.md`](./CHANGELOG.md)（`[Unreleased]` 扁平单行）
