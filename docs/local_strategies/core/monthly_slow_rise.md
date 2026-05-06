# `monthly_slow_rise`

最后更新：2026-05-01

## 1. 定位

`monthly_slow_rise` 是中期慢牛结构筛选。
它属于低频扩展层，不替代日常三条主策略，也不进入默认每日主循环。

默认落库信号：`monthly_slow_rise`

## 2. 实现真源

- `scripts/select_monthly_slow_rise_candidates.py`

## 3. 规则框架

当前逻辑主要分四段：

1. 月线结构：
   - 阳线占比
   - 低点抬升占比
   - 月线均线关系
2. 周线稳定性：
   - `weekly_positive_ratio`
   - `weekly_shallow_pullback_ratio`
   - `weekly_range_compression_ratio`
   - `weekly_volatility_percentile`
3. 流动性约束：
   - `avg_daily_amount_20d`
4. 财务连续性与行业确认：
   - 连续增长季度数
   - `earnings_continuity_score`
   - `industry_strength_*`

## 4. Profile

| Profile | 月线窗口 | 阳线占比 | 低点抬升占比 | 单月涨幅上限 | 回撤上限 | `min_avg_daily_amount_20d` |
| --- | --- | --- | --- | --- | --- | --- |
| `strict` | 12 | 0.67 | 0.58 | 14% | 10% | 30,000,000 |
| `robust` | 15 | 0.60 | 0.60 | 15% | 12% | 25,000,000 |
| `balanced` | 12 | 0.58 | 0.50 | 18% | 15% | 10,000,000 |
| `loose` | 10 | 0.50 | 0.40 | 22% | 20% | 0 |

当前最常用的是 `robust`，更适合作为慢牛观察池。

## 5. 当前使用建议

- 不进默认每日。
- 建议周频或双周频运行。
- 更适合和 `trend_leader_unified / earnings_surprise / hundred_day_high` 做交集观察，而不是替代它们。

## 6. 常用参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--profile` | `strict/robust/balanced/loose` | `balanced` |
| `--signal-type` | 落库信号类型 | `monthly_slow_rise` |
| `--min-listed-days-prefilter` | 最小上市天数前筛 | 默认跟随 profile |
| `--disable-listed-days-prefilter` | 关闭上市天数短路 | 默认关闭 |
| `--disable-shared-scan-shell` | 关闭共享扫描壳做 A/B 诊断 | 默认关闭 |
| `--max-workers` | 并发 worker | 脚本默认值为准 |
| `--skip-db-persist` | 仅导出文件不落库 | 默认落库 |

## 7. 推荐命令

```bash
python scripts/select_monthly_slow_rise_candidates.py --profile robust --snapshot-date 2026-04-29
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-29 --include-signals monthly_slow_rise
```
