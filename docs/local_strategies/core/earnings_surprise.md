# `earnings_surprise`

最后更新：2026-05-01

## 1. 定位

`earnings_surprise` 是当前本地日常主策略之一。
目标不是“正向文本命中”，而是“事件 + 财务质量 + 连续性 + 行业确认”的综合业绩强势筛选。

默认落库信号：

- `balanced` -> `earnings_surprise`
- `strict` -> `earnings_surprise_strict`
- `relaxed` -> `earnings_surprise_relaxed`

## 2. 实现真源

- `scripts/select_earnings_surprise_candidates.py`

## 3. 当前日常口径

fast review 默认口径已经收敛到：

- `--strategy-profile balanced`
- `--scan-depth low`
- `--recent-event-scope latest_report_period`
- `--recent-event-max-age-days 7`
- `--max-workers 1`
- `--capital-profile-ttl-seconds 86400`

注意：脚本原生默认和每日入口默认不完全相同。日常实际运行口径以 `run_fast_review_bundle.py` 和 `config/local_strategy_profile.json` 为准。

## 4. 三档参数预设

| 档位 | `min_revenue_yoy` | `min_net_profit_yoy` | `min_roe` | `direct/watch` |
| --- | --- | --- | --- | --- |
| `strict` | 15 | 30 | 8 | 60 / 45 |
| `balanced` | 10 | 20 | None | 55 / 35 |
| `relaxed` | 5 | 10 | None | 45 / 28 |

## 5. 放行与拦截

优先硬拦截：

- `blocked_negative_text`
- `blocked_missing_positive_text`
- `blocked_missing_growth_thresholds`
- `blocked_quality_risk`
- `blocked_duplicate_event`

未硬拦截时：

1. `earnings_strategy_score >= strategy_direct_pass_score`，或
2. `earnings_strategy_score >= strategy_watch_pass_score` 且满足确认条件

默认 `balanced/strict` 的 watch 放行需要质量确认。

## 6. 当前重点增强

当前版本重点看这些字段：

- 事件后反应：
  - `earnings_post_event_1d_return_pct`
  - `earnings_post_event_3d_return_pct`
  - `earnings_post_event_reaction_label`
- 多季度 surprise 历史：
  - `earnings_surprise_history_score`
  - `earnings_surprise_positive_quarter_count`
  - `earnings_surprise_positive_quarter_ratio`
  - `earnings_surprise_positive_quarter_streak`
- 持续质量与行业：
  - `earnings_financial_series_continuity_score`
  - `earnings_industry_confirmation_score`
  - `quality_overlay_*`
  - `industry_strength_*`

## 7. 本轮性能相关结论

- 低深度路径已新增保守的弱事件预过滤。
- `phase_timing_sec` 已暴露到 Markdown 输出，可直接看：
  - `fundamental_fetch`
  - `evaluate_candidate`
  - `capital_profile`
- 当前最干净的 like-for-like 证据是：
  - `evaluated: 3560 -> 3490`
  - `elapsed: 2159.20s -> 1833.013s`

## 8. 常用参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--strategy-profile` | `strict/balanced/relaxed` | `balanced` |
| `--scan-depth` | `low/medium/high` | 脚本默认 `high` |
| `--recent-event-scope` | `lookback/latest_report_period` | 脚本默认 `lookback` |
| `--recent-event-max-age-days` | 当前财报季最近公告窗口 | 默认不传，日常为 `7` |
| `--disable-event-dedupe` | 关闭事件去重 | 默认关闭 |
| `--capital-profile-ttl-seconds` | 资金画像 TTL | 独立脚本默认较短，日常为 `86400` |
| `--skip-db-persist` | 仅导出文件不落库 | 默认落库 |

## 9. 推荐命令

```bash
python scripts/select_earnings_surprise_candidates.py --snapshot-date 2026-04-29 --strategy-profile balanced --scan-depth low --recent-event-scope latest_report_period --recent-event-max-age-days 7
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-29 --include-signals earnings
```
