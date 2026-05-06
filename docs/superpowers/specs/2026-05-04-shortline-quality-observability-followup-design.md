# Shortline Quality Observability Follow-Up Design

## 1. 背景

当前 shortline 已经有 `quality_summary`，但对使用者仍有一个明显问题：

- 能看到 `verdict=degraded` 或 `overall_status=quality_failed`
- 但仍需要人工反推，才能知道到底是哪条前置条件触发了这个结论

这会降低真实 bundle 稳定验证时的排障效率。

## 2. 目标

把现有质量判定依据显式写进结构化产物和根报告，而不是只留下最终 verdict。

完成后，使用者应能直接从 `bundle_manifest.json` / `bundle_report.md` 看出：

- `tracking_history_ready`
- `source_real_engine_expected_passed`
- `quality_failed_due_to`

以及对应的 warning/fail 来源。

## 3. 非目标

- 不新增新的质量规则
- 不修改现有 verdict 判定逻辑
- 不调整 exit code、step status 或 bundle 顶层状态语义

## 4. 设计

### 4.1 新增显式依据字段

在 `quality_summary` 下新增 `signals`，首版至少包含：

- `tracking_history_ready`
  - `bool`
  - 表示当前 run 是否具备跨日比较基础
- `source_real_engine_expected_passed`
  - `bool | null`
  - 在 `wt_source_mode=prefer_real_engine` 且有候选时有效
  - 其他场景可为 `null`

新增顶层便捷字段：

- `quality_failed_due_to`
  - 等于 `quality_summary.failed_checks`
  - 仅为减少外部消费方深入解析 `quality_summary` 的成本

### 4.2 Runs Summary

在 `scripts/summarize_shortline_runs.py` 中：

- 每条 run 的 `quality_summary.signals` 都写出
- `latest_run_quality` 自然透传这些 signals

### 4.3 Bundle

在 `scripts/run_shortline_review_bundle.py` 中：

- bundle 级 `quality_summary.signals` 写出
- 根层新增 `quality_failed_due_to`
- `bundle_report.md` 根层直接显示：
  - `quality_failed_due_to`
  - `tracking_history_ready`
  - `source_real_engine_expected_passed`

## 5. 测试策略

至少覆盖：

- runs summary 产物包含 `tracking_history_ready`
- bundle 产物包含 `quality_failed_due_to`
- bundle report 展示这两个关键 signals
- 真实源失败场景下 `source_real_engine_expected_passed=False`
