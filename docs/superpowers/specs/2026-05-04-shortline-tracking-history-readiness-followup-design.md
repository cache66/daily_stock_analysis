# Shortline Tracking History Readiness Follow-Up Design

## 1. 背景

当前 shortline 质量门禁会在以下条件下标记 `tracking_continuity_weak`：

- `candidate_count > 0`
- `tracking_repeat_symbol_count == 0`

这个规则过于激进。对于首日运行、只有同日重跑样本、或者刚切换 tracking history 文件的场景，其实还不具备跨日比较基础，不应该直接给出连续性告警。

## 2. 目标

只在“tracking 历史已经足够可比较”时，才触发 `tracking_continuity_weak`。

完成后：

- 首日 / 冷启动 / 只有同日样本时，不再因为 `tracking_repeat_symbol_count=0` 自动降级
- 至少存在更早交易日样本时，连续性弱才继续作为 warning 生效

## 3. 非目标

- 不修改 `tracking` 文件写入逻辑
- 不修改 `tracking_repeat_symbol_count` / `tracking_longest_streak_days` 统计口径
- 不新增配置项

## 4. 设计

### 4.1 History Readiness

新增一个内部判定概念：`tracking_history_ready`

含义：

- 对某次 run 来说，只有当当前 `trade_date` 之前已经存在至少一个更早的 distinct `trade_date` 样本时，连续性才“可比较”

### 4.2 Runs Summary

在 `scripts/summarize_shortline_runs.py` 中：

- 先收集当前 summary 覆盖到的 distinct `trade_date`
- 对每个 run entry 计算：
  - 是否存在更早交易日样本
- 只有 `tracking_history_ready=True` 时，`tracking_repeat_symbol_count == 0` 才触发 `tracking_continuity_weak`

这样：

- 最早交易日样本不会被误伤
- 后续交易日如果仍无重复票，才会被标记为连续性偏弱

### 4.3 Bundle

在 `scripts/run_shortline_review_bundle.py` 中：

- 复用 runs summary 的 `trade_date_counts`
- 结合当前 bundle 的 `trade_date`
- 只有当前 trade_date 之前存在更早 distinct trade_date 时，bundle 自身才追加本地 `tracking_continuity_weak`

## 5. 测试策略

至少覆盖：

- 同日样本下 `tracking_repeat_symbol_count=0` 不再触发 warning
- 跨两日样本下，第二日 `tracking_repeat_symbol_count=0` 会触发 warning
- bundle 在 `trade_date_counts` 只有当前日时不触发 tracking warning
