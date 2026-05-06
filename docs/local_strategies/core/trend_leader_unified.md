# `trend_leader_unified`

最后更新：2026-05-01

## 1. 定位

`trend_leader_unified` 是当前本地日常主策略之一。
它不是单纯的形态扫描，而是把以下维度统一到同一套打分里：

- 龙头地位
- 趋势结构：`breakout / pullback`
- 资金共识与资金流
- 业绩质量与行业确认
- 风险与过热惩罚

默认落库信号：`trend_leader_unified`

## 2. 实现真源

- `scripts/select_trend_leader_candidates.py`
- `src/services/trend_leader_strategy_service.py`

## 3. 当前日常口径

fast review 默认通过 `run_fast_review_bundle.py` 透传：

- `trend_signal_type=trend_leader_unified`
- `trend_max_workers=2`
- `trend_fallback_top_n=20`
- `trend_watch_top_n=20`
- `trend_disable_second_stage_enrichment=true`
- `scan_prefilter_min_listed_days=120`
- `scan_prefilter_min_change_pct_60d=4.0`
- `scan_prefilter_min_turnover_rate=1.0`
- `scan_prefilter_require_positive_change=true`

当前默认 `trend_max_workers=2` 时，主扫描依赖外层 worker 并发，不再为每个候选内部再起一层 `fundamental + capital_profile` 线程池；只有 single-worker 时保留单候选内部并行。
当前还会对弱趋势样本延迟初始化 `DragonHeadAnalysisService`；对于非结构型、离高点偏远且当日价量也偏弱的样本，更早短路 `board / dragon / fundamental` 相关抓取。

趋势扫描当前优先走共享扫描壳 `KlineSelectorService.prepare_scan_universe(...)`。

当前快扫还会对远端补全链路使用单独 budget：`earnings fundamental=0.6s`、`capital_flow=0.45s`。这条策略优先保证“快速 fail-open”，避免边缘样本在基本面/资金流 endpoint 上占用接近通用 timeout 的尾部耗时。
## 4. 严格核心命中

候选进入 strict core，至少满足：

1. 龙头门槛不过弱：
   - `leader_type != pseudo_leader`
   - `leader_probability >= medium`
   - `recognizability_score >= 2`
   - `sector_leadership_score >= 1`
2. 趋势结构成立：`is_breakout_candidate` 或 `is_pullback_candidate`
3. 不命中关键阻断：如 `blocked_negative_text`、`blocked_quality_risk`
4. `overall_score > 0`

注意：当前 strict 已明确要求真实 `breakout` 或 `pullback`，非结构型正分样本不会再混入 strict。

## 5. 评分骨架

- `breakout_score = 0.40*leader + 0.38*breakout_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `pullback_score = 0.40*leader + 0.38*pullback_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `hybrid_score = max(breakout,pullback) + 0.2*min(breakout,pullback) - risk_penalty`
- `overall_score = hybrid_score`

常看增强字段：

- `trend_stage2_score`
- `base_quality_score`
- `industry_leadership_score`
- `board_breadth_score`
- `extension_risk_score`

## 6. fallback 与 watchlist

当 strict 命中为空时，会按 fallback 分层返回观察池。
当前还额外支持 review-only `watchlist` sidecar：

- 进入 `fast_review_strategy_focus.*`
- 不进入主候选表
- 不进入共振表
- 不回灌 `/signals`

## 7. 常用参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--signal-type` | 落库信号类型 | `trend_leader_unified` |
| `--fallback-top-n` | strict 为空时 fallback 数量 | `20` |
| `--watch-top-n` | review-only watchlist 数量 | `20` |
| `--scan-prefilter-min-listed-days` | 前筛最小上市天数 | `120` |
| `--scan-prefilter-min-change-pct-60d` | 前筛最小 60 日涨幅 | `3.0`，日常覆盖为 `4.0` |
| `--scan-prefilter-min-turnover-rate` | 前筛最小换手率 | `0.8`，日常覆盖为 `1.0` |
| `--scan-prefilter-require-positive-change` | 是否要求当日涨幅为正 | 日常默认开启 |
| `--disable-shared-scan-shell` | 关闭共享扫描壳做诊断 A/B | 默认关闭 |
| `--fundamental-budget-seconds` | 快扫 earnings fundamental budget | `0.6` |
| `--capital-flow-budget-seconds` | 快扫 capital flow budget | `0.45` |

补充说明：共享前筛在 `pct_change` 列里已经存在可用值时，不再为了少量空值逐票补 `quote`；只有整列都不可用时才会触发行情补水，保持“缺字段不硬拦”的快扫语义并减少准备阶段长尾。
补充说明：当前快扫对 earnings fundamental 已收窄为只取 `financial` block；manager 会兼容复用旧的更宽 earnings cache，因此从三块收窄到单块后不会把既有 warm-cache 价值完全打掉。
补充说明：`spot-enriched universe` 在 live `spot` 失败时，允许回退使用“已过 TTL 但结构完整”的本地 `spot` reference cache；该 cache 只作为失败兜底，不会替代正常的新鲜 `spot` 优先级。
补充说明：`trend_leader_unified` 当前还会显式开启 stale `spot` reference cache 的优先复用，用来压缩 `prep_universe` 墙钟时间；这是趋势快扫专项 opt-in，不是其他策略的默认行为。
补充说明：`sector_rankings` 预热当前会优先复用 stale cache，因为它只提供板块强弱辅助上下文，不是 strict/fallback 的硬门槛；对快扫来说，优先避免远端预热阻塞更重要。

## 8. 推荐命令

```bash
python scripts/select_trend_leader_candidates.py --snapshot-date 2026-04-29 --fallback-top-n 20 --watch-top-n 20
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-29 --include-signals trend_leader
```
