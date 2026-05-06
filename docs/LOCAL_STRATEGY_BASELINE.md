# 当前本地策略基线（Current Baseline）

最后更新：2026-05-02  
真源优先级：代码与脚本 > 本文档

## 0. 这份文档负责什么

本文档只负责两类信息：

1. 当前默认每日主链路怎么跑；
2. 四条主策略现在采用什么默认口径、关键参数是什么。

它不负责承载逐次优化过程、完整实跑证据和长历史 benchmark。对应分工：

- 主策略中文目录：[`docs/local_strategies/README.md`](./local_strategies/README.md)
- 策略资产入口与脚本索引：[`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md)
- 详细变更、验证命令、实跑证据：[`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)
- 用户可见摘要：[`docs/CHANGELOG.md`](./CHANGELOG.md)

## 1. 当前策略分层

| 层级 | 策略/信号 | 是否默认每日 | 说明 | 现行文档 |
| --- | --- | --- | --- | --- |
| 核心主策略 | `trend_leader_unified` | 是 | 龙头 + 趋势 + 资金 + 业绩兑现的统一主骨架。 | [`core/trend_leader_unified.md`](./local_strategies/core/trend_leader_unified.md) |
| 核心主策略 | `earnings_surprise` | 是 | 财报事件驱动的业绩强势筛选。 | [`core/earnings_surprise.md`](./local_strategies/core/earnings_surprise.md) |
| 核心主策略 | `hundred_day_high` | 是 | 新高突破确认层。 | [`core/hundred_day_high.md`](./local_strategies/core/hundred_day_high.md) |
| 低频扩展 | `monthly_slow_rise` | 否 | 中期慢牛结构层，建议低频运行。 | [`core/monthly_slow_rise.md`](./local_strategies/core/monthly_slow_rise.md) |
| 观察信号 | `continuous_up_ratio` | 否 | 近端上涨占比观察，不作为独立 alpha。 | [`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md) |
| 观察信号 | `continuous_up_streak` | 否 | 连涨天数观察，不作为独立 alpha。 | [`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md) |

默认每日是否启用，以 [`config/local_strategy_profile.json`](../config/local_strategy_profile.json) 的 `defaults.include_signals` 为准。

## 2. 默认每日入口与默认值

主入口：

```bash
python scripts/run_fast_review_bundle.py --strategy-profile-file config/local_strategy_profile.json
```

当前默认每日主链路：

- `include_signals = earnings,hundred_day_high,trend_leader`
- `external_parallelism = 3`
- `max_workers = 1`
- `trend_max_workers = 2`
- `hundred_day_max_workers = 2`
- `continuous_max_workers = 2`

当前默认参数口径：

- `earnings_strategy_profile = balanced`
- `earnings_scan_depth = low`
- `earnings_recent_event_scope = latest_report_period`
- `earnings_recent_event_max_age_days = 7`
- `hundred_day_profile = breakout_loose`
- `hundred_day_signal_type = hundred_day_high`
- `hundred_day_skip_cause_analysis = true`
- `trend_signal_type = trend_leader_unified`
- `trend_fallback_top_n = 20`
- `trend_watch_top_n = 20`
- `trend_disable_second_stage_enrichment = true`
- `trend_scan_prefilter_min_listed_days = 120`
- `trend_scan_prefilter_min_change_pct_60d = 4.0`
- `trend_scan_prefilter_min_turnover_rate = 1.0`
- `trend_scan_prefilter_require_positive_change = true`

## 3. 当前默认行为速记

- 默认每日只跑三条核心策略：`earnings_surprise`、`hundred_day_high`、`trend_leader_unified`
- `monthly_slow_rise` 不进默认每日，只作为低频扩展层按需单跑
- `board_cycle_scan` 不进默认每日，只作为专题扫描按需运行，当前入口为 `scripts/select_board_cycle_candidates.py`
- `trend_leader_unified_watchlist` 只进入复盘阅读层，不进入主候选表、共振表或 `/signals`
- `strategy_focus` 和 `earnings_focus` 属于聚合阅读层后处理，不改变子策略筛选规则

## 4. 四条主策略当前口径

- `strategy_focus` 现在会为焦点强势股补充“上涨原因摘要/标签”：优先复用子策略已有 `reason_summary / cause_tags`，缺失时才在复盘层做轻量补算；当前仅进入 `fast_review_strategy_focus.csv/.md` 与 `fast_review_summary.md`，不写回 snapshot
### 4.1 `trend_leader_unified`

脚本：`scripts/select_trend_leader_candidates.py`  
服务：`src/services/trend_leader_strategy_service.py`  
默认信号：`trend_leader_unified`  
现行说明：[`docs/local_strategies/core/trend_leader_unified.md`](./local_strategies/core/trend_leader_unified.md)

严格核心命中主条件：

1. 龙头门槛不过弱：
   - `leader_type != pseudo_leader`
   - `leader_probability >= medium`
   - `recognizability_score >= 2`
   - `sector_leadership_score >= 1`
2. 趋势结构成立：`is_breakout_candidate` 或 `is_pullback_candidate`
3. 不命中关键阻断
4. 综合得分 `overall_score > 0`

当前重点：

- strict 只允许真实 `breakout/pullback`
- review-only `watchlist` 单独导出，不回灌主结果
- 日常快扫默认启用 shared scan shell 与前筛参数
- 默认 `trend_max_workers = 2` 时，外层扫描已并发的情况下不会再为单候选内部额外创建 `ThreadPoolExecutor(max_workers=2)`；single-worker 仍保留内部 enrichment 并行。
- `DragonHeadAnalysisService` 改为弱样本可跳过的懒初始化；非 `breakout/pullback` 且趋势结构边缘、当日价量也偏弱的样本，会更早短路 `board/dragon/fundamental` 链路。
- 快扫默认还会对远端补全链路使用更紧的 budget：`earnings fundamental=0.6s`、`capital_flow=0.45s`，优先控制尾部慢样本耗时；如需诊断可通过 `--fundamental-budget-seconds`、`--capital-flow-budget-seconds` 覆盖。
- 共享前筛现在只在 `pct_change` 整列都不可用时才补单票行情；如果列内已经有部分可用值，则保留“缺失即放行”的现行快扫语义，不再为了少量空值触发逐票 quote 长尾。
- `trend_leader_unified` 快扫当前只请求 `financial` 这一个 earnings fundamental block；旧版更宽 cache 仍可被兼容复用，因此从三块收窄到单块后不会把既有 warm-cache 价值完全打掉。
- `trend_leader_unified` 的 `spot` 引用缓存即使超过 TTL，也只会在 live `spot` 失败时作为降级兜底使用，不会覆盖正常的新鲜 `spot` 优先级；这样保留了稳态新鲜度，同时减少异常日掉回 generic provider 前的额外空耗。
- `trend_leader_unified` 作为快扫特例，当前会显式优先复用 stale `spot` 引用缓存；这是该策略的专项提速口径，不是全局 `spot` universe 默认行为。
- `trend_leader_unified` 的 `sector_rankings` 预热默认允许优先复用 stale cache，因为这部分只是板块强弱辅助上下文，不直接决定主筛选入口；这样可以避免 cache 过期时先被远端预热阻塞。

关键参数：

- `--fallback-top-n`（默认 20）
- `--watch-top-n`（默认 20）
- `--scan-prefilter-min-listed-days`（默认 120）
- `--scan-prefilter-min-change-pct-60d`（脚本默认 3.0，日常配置覆盖为 4.0）
- `--scan-prefilter-min-turnover-rate`（脚本默认 0.8，日常配置覆盖为 1.0）
- `--scan-prefilter-require-positive-change`
- `--fundamental-budget-seconds`（默认 `0.6`）
- `--capital-flow-budget-seconds`（默认 `0.45`）

### 4.2 `earnings_surprise`

脚本：`scripts/select_earnings_surprise_candidates.py`  
默认信号：`earnings_surprise`  
现行说明：[`docs/local_strategies/core/earnings_surprise.md`](./local_strategies/core/earnings_surprise.md)

档位预设：

| 档位 | `min_revenue_yoy` | `min_net_profit_yoy` | `min_roe` | `direct/watch` |
| --- | --- | --- | --- | --- |
| `strict` | 15 | 30 | 8 | 60 / 45 |
| `balanced` | 10 | 20 | None | 55 / 35 |
| `relaxed` | 5 | 10 | None | 45 / 28 |

通过规则：

1. 先过硬拦截：负向文本、缺少增长阈值、质量风险、重复事件等
2. `direct_score_reached`，或
3. `watch_score_reached` 且有确认信号

当前重点：

- 日常默认是 `balanced + low + latest_report_period + recent_event_max_age_days=7`
- 低深度路径已加弱事件预过滤
- `phase_timing_sec` 已进入候选 Markdown 的效率摘要
- 市场预期层只用于复盘参考，不参与当前策略打分

关键参数：

- `--strategy-profile`
- `--scan-depth`
- `--recent-event-scope`
- `--recent-event-max-age-days`
- `--capital-profile-ttl-seconds`

### 4.3 `hundred_day_high`

脚本：`scripts/select_hundred_day_high_candidates.py`  
默认信号：`hundred_day_high`  
现行说明：[`docs/local_strategies/core/hundred_day_high.md`](./local_strategies/core/hundred_day_high.md)

当前主用 profile：

| Profile | `lookback_days` | `min_up_ratio` | `new_high_window` | `max_total_mv_yi` |
| --- | --- | --- | --- | --- |
| `breakout_balanced` | 8 | 0.625 | 100 | 400 |
| `breakout_balanced_with_earnings` | 8 | 0.625 | 100 | 400 |
| `momentum_strict` | 8 | 0.67 | 110 | 70 |
| `breakout_loose` | 12 | 0.58 | 80 | 600 |

当前重点：

- 日常默认 `profile=breakout_loose`
- 默认 `--skip-cause-analysis`
- 默认走 `KlineSelectorService.prepare_scan_universe(...)` shared scan shell
- 入选后 `breakout_quality` 的 180 日补强默认会复用 `max_workers` 做并发历史抓取
- `breakout_loose` 现已从“无质量底线”收紧为 `breakout_quality_score >= 4`
- 质量增强字段已包含 `breakout_quality_score`、`minervini_template_score`、`breakout_follow_through_score`

关键参数：

- `--profile`
- `--skip-cause-analysis`
- `--min-listed-days-prefilter`
- `--disable-shared-scan-shell`

### 4.4 `monthly_slow_rise`

脚本：`scripts/select_monthly_slow_rise_candidates.py`  
默认信号：`monthly_slow_rise`  
现行说明：[`docs/local_strategies/core/monthly_slow_rise.md`](./local_strategies/core/monthly_slow_rise.md)

定位：

- 不进默认每日
- 用于周/月频复盘和中期慢牛结构池

主用 profile：

| Profile | 月线窗口 | 阳线占比 | 低点抬升占比 | 回撤上限 | `min_listed_days` |
| --- | --- | --- | --- | --- | --- |
| `strict` | 12 | 0.67 | 0.58 | 10% | 跟随历史要求 |
| `robust` | 15 | 0.60 | 0.60 | 12% | 400 |
| `balanced` | 12 | 0.58 | 0.50 | 15% | 跟随历史要求 |
| `loose` | 10 | 0.50 | 0.40 | 20% | 跟随历史要求 |

当前重点：

- `robust` 仍是更常用的慢牛池口径
- 已接入 shared scan shell
- 行业确认、周线稳定和财务连续性字段已进入输出

关键参数：

- `--profile`
- `--min-listed-days-prefilter`
- `--max-workers`
- `--disable-shared-scan-shell`

## 5. Supporting / Topics 的关系

- `supporting/` 文档负责解释主策略共用的方法论、资金层和业绩线拆解
- `topics/` 文档负责专题扫描、题材映射和专题快照
- `board_cycle_scan` 属于 `topics/` 专题扫描，不改变默认每日 `include_signals = earnings,hundred_day_high,trend_leader`
- 二者都不改变默认每日主链路，只提供补充阅读和专题扩展

## 6. 文档同步约定

涉及本地策略资产变更时，必须同步更新：

- [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md)
- [`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md)
- [`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)
- [`docs/CHANGELOG.md`](./CHANGELOG.md)（`[Unreleased]` 扁平单行）

如果文档和代码冲突，以脚本与实现为准。
