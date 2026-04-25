# `trend_leader_unified` 策略说明

最后更新：2026-04-25  
实现真源：

- `scripts/select_trend_leader_candidates.py`
- `src/services/trend_leader_strategy_service.py`

## 1. 策略定位

`trend_leader_unified` 是当前本地日常主策略之一，目标是把以下维度统一到同一套打分里：

- 龙头地位
- 趋势结构（breakout / pullback）
- 资金共识与资金流
- 业绩质量与行业确认
- 过热惩罚与风险标记

默认落库信号：`trend_leader_unified`

## 2. 严格核心命中条件（Strict Core）

候选要进入严格命中池，至少满足：

1. 龙头门槛不过弱：
   - `leader_type != pseudo_leader`
   - `leader_probability >= medium`
   - `recognizability_score >= 2`
   - `sector_leadership_score >= 1`
2. 趋势结构成立：`is_breakout_candidate` 或 `is_pullback_candidate`
3. 不命中关键阻断（如 `blocked_negative_text`、`blocked_quality_risk`）
4. `overall_score > 0`

## 3. 评分结构

评分由 `TrendLeaderStrategyService.score_candidate()` 计算，核心结构：

- `breakout_score = 0.40*leader_gate + 0.38*breakout_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `pullback_score = 0.40*leader_gate + 0.38*pullback_trend + 0.22*capital + logic_bonus + structure_bonus - extension_penalty`
- `hybrid_score = max(breakout,pullback) + 0.2*min(breakout,pullback) - risk_penalty`
- `overall_score = hybrid_score`

关键增强因子：

- `trend_stage2_score`（Stage-2 趋势模板）
- `base_quality_score`（base/VCP 质量）
- `industry_leadership_score`（行业与板块领导力）
- `extension_risk_score`（过热惩罚）

## 4. fallback 机制

当严格命中为空时，按分层兜底返回观察池（`selection_mode=fallback`）：

1. `tier1_near_miss`
2. `tier2_watchlist`
3. `tier3_broader_pool`
4. `tier4_last_resort`
5. `tier5_safety_net`

对应字段：

- `selection_mode`
- `strict_core_hit`
- `fallback_tier`
- `fallback_reason`

## 5. 关键输出字段

排序和复盘常用字段：

- 主分：`breakout_score`、`pullback_score`、`hybrid_score`、`overall_score`
- 结构：`trend_template_score`、`trend_stage2_passed`、`trend_stage2_score`
- 行业：`industry_leadership_score`、`board_leadership_rank_pct`、`board_breadth_score`
- 资金：`capital_consensus_score`、`capital_flow_score`、`capital_flow_continuity_score`
- 风险：`extension_risk_score`、`risk_flags`
- 命中模式：`selection_mode`、`strict_core_hit`、`fallback_tier`

## 6. 运行参数（常用）

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--signal-type` | 落库信号类型 | `trend_leader_unified` |
| `--fallback-top-n` | 严格命中为空时 fallback 数量 | `20` |
| `--scan-prefilter-min-listed-days` | 前筛最小上市天数 | `120` |
| `--scan-prefilter-min-change-pct-60d` | 前筛最小 60 日涨幅 | `3.0`（日常配置可覆盖） |
| `--scan-prefilter-min-turnover-rate` | 前筛最小换手率 | `0.8`（日常配置可覆盖） |
| `--scan-prefilter-require-positive-change` | 是否要求当日涨幅为正 | 默认关闭（由日常配置控制） |
| `--max-workers` | 扫描并发 | `1`（脚本默认） |
| `--disable-second-stage-news-search` | 关闭二阶段新闻补抓 | 默认开启补抓 |
| `--disable-second-stage-business-profile` | 关闭二阶段主营补抓 | 默认开启补抓 |

## 7. 日常运行口径（fast review）

通过 `run_fast_review_bundle.py` 的默认口径：

- `trend_signal_type=trend_leader_unified`
- `trend_max_workers=2`
- `trend_disable_second_stage_enrichment=true`
- `trend_fallback_top_n=20`
- 前筛默认开启，且由 `config/local_strategy_profile.json` 覆盖为：
  - `min_listed_days=120`
  - `min_change_pct_60d=4.0`
  - `min_turnover_rate=1.0`
  - `require_positive_change=true`

## 8. 示例命令

```bash
python scripts/select_trend_leader_candidates.py --snapshot-date 2026-04-25 --max-workers 2 --fallback-top-n 20
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-25 --include-signals trend_leader
```
