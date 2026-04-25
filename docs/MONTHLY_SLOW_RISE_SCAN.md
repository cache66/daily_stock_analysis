# `monthly_slow_rise` 策略说明

最后更新：2026-04-25  
实现真源：`scripts/select_monthly_slow_rise_candidates.py`

## 1. 策略定位

`monthly_slow_rise` 是中期慢牛结构筛选，不是日线短打策略。  
它是“低频扩展层”，默认不进入每日主循环。

默认落库信号：`monthly_slow_rise`

## 2. 规则框架

筛选逻辑分四段：

1. 月线结构：
   - 阳线占比
   - 低点抬升占比
   - 月线均线关系（短均线 > 长均线）
2. 周线稳定性：
   - `weekly_positive_ratio`
   - `weekly_shallow_pullback_ratio`
   - `weekly_recent_range_pct`
   - `weekly_range_compression_ratio`
   - `weekly_volatility_percentile`
3. 流动性约束：
   - `avg_daily_amount_20d` 最低门槛
4. 财务连续性与行业确认：
   - 营收/利润连续正增长季度数
   - `earnings_continuity_score`
   - `industry_strength_*`

## 3. Profile 参数（当前）

| Profile | 月线窗口 | 阳线占比 | 低点抬升占比 | 单月涨幅上限 | 回撤上限 | `min_avg_daily_amount_20d` | 财务连续性门槛 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `strict` | 12 | 0.67 | 0.58 | 14% | 10% | 30,000,000 | 连续 4Q + score>=14 |
| `robust` | 15 | 0.60 | 0.60 | 15% | 12% | 25,000,000 | 连续 3Q + score>=12 |
| `balanced` | 12 | 0.58 | 0.50 | 18% | 15% | 10,000,000 | 连续 2Q + score>=8 |
| `loose` | 10 | 0.50 | 0.40 | 22% | 20% | 0 | 不强制 |

预过滤差异（重点）：

- `strict`: `min_change_pct_60d=5`，`exclude_st=true`
- `robust`: `min_change_pct_60d=3`，`exclude_st=true`，`min_listed_days=400`
- `balanced`: 默认不设 60 日涨幅阈值，`exclude_st=true`
- `loose`: 最宽松，可保留 ST

## 4. 关键输出字段

结构与稳定性：

- `monthly_positive_ratio`
- `monthly_higher_low_ratio`
- `monthly_total_return_pct`
- `monthly_max_single_gain_pct`
- `monthly_worst_drawdown_pct`
- `weekly_positive_ratio`
- `weekly_volatility_percentile`
- `weekly_range_compression_ratio`
- `avg_daily_amount_20d`

质量与行业：

- `earnings_continuity_score`
- `quality_overlay_*`
- `industry_strength_score`
- `industry_strength_confirmed`
- `industry_strength_label`

## 5. 关键运行参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--profile` | `strict/robust/balanced/loose` | `balanced` |
| `--signal-type` | 落库信号类型 | `monthly_slow_rise` |
| `--min-listed-days-prefilter` | 最小上市天数前筛 | 默认跟随 profile |
| `--disable-listed-days-prefilter` | 关闭上市天数短路 | 默认关闭 |
| `--min-60d-change-pct-prefilter` | 60 日涨幅前筛 | 默认跟随 profile |
| `--max-workers` | 并发 worker 数 | 脚本默认值为准 |
| `--skip-db-persist` | 仅导出文件不落库 | 默认落库 |

## 6. 推荐用法

稳健低频池（推荐）：

```bash
python scripts/select_monthly_slow_rise_candidates.py --profile robust --snapshot-date 2026-04-25
```

小样本快速验证：

```bash
python scripts/select_monthly_slow_rise_candidates.py --profile robust --limit 100 --max-workers 2
```

## 7. 与每日主策略的关系

- `monthly_slow_rise` 负责中期结构补充，不替代 `trend_leader_unified / earnings_surprise / hundred_day_high`。
- 建议按周/双周运行，并与主策略候选做交集观察，而不是每天并入默认主循环。
