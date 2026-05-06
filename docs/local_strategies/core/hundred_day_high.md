# `hundred_day_high`

最后更新：2026-05-01

## 1. 定位

`hundred_day_high` 是当前本地日常主策略之一。
它不是泛化动量池，而是“新高突破确认层”，负责给日常主链路提供更直接的突破信号。

默认落库信号：`hundred_day_high`

## 2. 实现真源

- `scripts/select_hundred_day_high_candidates.py`

## 3. 当前日常口径

fast review 默认通过 `run_fast_review_bundle.py` 透传：

- `hundred_day_signal_type=hundred_day_high`
- `hundred_day_profile=breakout_loose`
- `hundred_day_max_workers=2`
- `hundred_day_skip_cause_analysis=true`

当前也已接入共享扫描壳 `KlineSelectorService.prepare_scan_universe(...)`。
入选后的 `breakout_quality` 180 日补强当前默认会复用 `max_workers` 并发抓历史，以降低后处理墙钟时间。

## 4. Profile 预设

| Profile | `lookback_days` | `min_up_ratio` | `new_high_window` | `max_total_mv_yi` |
| --- | --- | --- | --- | --- |
| `breakout_balanced` | 8 | 0.625 | 100 | 400 |
| `breakout_balanced_with_earnings` | 8 | 0.625 | 100 | 400 |
| `momentum_strict` | 8 | 0.67 | 110 | 70 |
| `breakout_loose` | 12 | 0.58 | 80 | 600 |

## 5. 当前主要规则

1. 新高突破规则：`require_new_high=True`
2. 预过滤通常会看：
   - 60 日涨幅
   - 换手率
   - 当日正涨幅
   - `exclude_st`
   - 上市天数
3. 当前重点增强字段：
   - `breakout_quality_score`
   - `minervini_template_score`
   - `minervini_template_passed`
   - `breakout_follow_through_score`

## 6. 质量底线

按 profile 有不同的突破质量门槛：

- `momentum_strict >= 8`
- `breakout_balanced* >= 6`
- `breakout_loose >= 4`

## 7. 常用参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--signal-type` | 落库信号类型 | `hundred_day_high` |
| `--profile` | profile 名称 | 脚本默认值为准，日常为 `breakout_loose` |
| `--skip-cause-analysis` | 跳过归因分析 | 日常默认开启 |
| `--disable-shared-scan-shell` | 关闭共享扫描壳做旧路径诊断 | 默认关闭 |
| `--max-workers` | 扫描并发 | 日常为 `2` |
| `--checkpoint-path` | 可选自定义 checkpoint 路径；未传时默认落到当前 `output_dir/hundred_day_high_checkpoint.json` | 默认跟随 `output_dir` |
| `--output-dir` | 导出目录；同一目录不允许两个运行中的任务并发复用，脚本会基于 `hundred_day_high_run.lock` 直接 fail-fast | 建议每次任务独立目录 |

## 8. 推荐命令

```bash
python scripts/select_hundred_day_high_candidates.py --snapshot-date 2026-04-29 --signal-type hundred_day_high --profile breakout_loose --skip-cause-analysis
```

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-29 --include-signals hundred_day_high
```
