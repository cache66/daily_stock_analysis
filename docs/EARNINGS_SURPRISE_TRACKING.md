# `earnings_surprise` 策略说明

最后更新：2026-04-25  
实现真源：`scripts/select_earnings_surprise_candidates.py`

## 1. 策略定位

`earnings_surprise` 是当前本地日常主策略之一。  
目标不是“纯文本命中”，而是“事件 + 财务质量 + 连续性 + 行业确认”的综合业绩强势筛选。

默认落库信号：

- `balanced` -> `earnings_surprise`
- `strict` -> `earnings_surprise_strict`
- `relaxed` -> `earnings_surprise_relaxed`

## 2. 三档参数预设

| 档位 | `min_revenue_yoy` | `min_net_profit_yoy` | `min_roe` | `require_positive_text` | `require_growth_thresholds` | `direct/watch` |
| --- | --- | --- | --- | --- | --- | --- |
| `strict` | 15 | 30 | 8 | 是 | 是 | 60 / 45 |
| `balanced` | 10 | 20 | None | 否 | 否 | 55 / 35 |
| `relaxed` | 5 | 10 | None | 否 | 否 | 45 / 28 |

## 3. 通过/拦截逻辑

### 3.1 硬拦截

以下状态优先拦截：

- `blocked_negative_text`
- `blocked_missing_positive_text`
- `blocked_missing_growth_thresholds`
- `blocked_quality_risk`
- `blocked_duplicate_event`

### 3.2 放行规则

在未硬拦截时：

1. `earnings_strategy_score >= strategy_direct_pass_score`，或
2. `earnings_strategy_score >= strategy_watch_pass_score` 且满足确认条件

默认 `balanced/strict` 档 watch 放行要求质量确认（`earnings_quality_signal`）。

## 4. 评分结构（当前版本）

主分由以下因子加权后扣风险得到（总分上限 100）：

| 因子 | 权重 |
| --- | --- |
| `event_surprise`（事件强度） | 24 |
| `growth_continuity`（增长连续性） | 26 |
| `profit_quality`（利润质量） | 22 |
| `profitability`（盈利能力） | 14 |
| `disclosure_signal`（披露质量） | 8 |
| `cycle_phase`（周期位置） | 10 |
| `event_freshness`（事件新鲜度） | 10 |
| `event_reaction`（事件后价格反应） | 6 |
| `persistent_quality`（多季度持续质量） | 6 |
| `surprise_history`（多季度 surprise 历史） | 6 |
| `industry_confirmation`（行业确认） | 4 |

并扣除 `risk_penalty`。

## 5. 本轮强化字段（重点）

事件后反应：

- `earnings_post_event_1d_return_pct`
- `earnings_post_event_3d_return_pct`
- `earnings_post_event_reaction_label`

多季度 surprise 历史：

- `earnings_surprise_history_score`
- `earnings_surprise_positive_quarter_count`
- `earnings_surprise_positive_quarter_ratio`
- `earnings_surprise_positive_quarter_streak`

持续质量与行业：

- `earnings_financial_series_continuity_score`
- `earnings_industry_confirmation_score`
- `earnings_industry_confirmed`
- `quality_overlay_*`
- `industry_strength_*`

## 6. 关键运行参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--strategy-profile` | 档位：`strict/balanced/relaxed` | `balanced` |
| `--scan-depth` | 基础面扫描深度：`low/medium/high` | `high`（脚本默认） |
| `--recent-event-scope` | 事件范围：`lookback/latest_report_period` | `lookback`（脚本默认） |
| `--event-lookback-days` | 事件窗口天数 | `120` |
| `--disable-event-dedupe` | 关闭事件去重 | 关闭（默认去重开启） |
| `--max-total-mv-yi` | 市值上限（亿） | 可选 |
| `--max-workers` | 并发 worker | 可选 |

## 7. 日常运行口径（fast review）

每日入口对脚本默认有覆盖，当前口径：

- `--strategy-profile balanced`
- `--scan-depth low`
- `--recent-event-scope latest_report_period`

这也是为什么“脚本默认值”和“日常实际值”看起来不一致：以每日入口配置为准。

## 8. 示例命令

```bash
python scripts/select_earnings_surprise_candidates.py --strategy-profile balanced --scan-depth low --recent-event-scope latest_report_period
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-25 --include-signals earnings
```
