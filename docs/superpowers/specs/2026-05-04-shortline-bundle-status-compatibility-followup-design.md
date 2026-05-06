# Shortline Bundle Status Compatibility Follow-Up Design

## 1. 背景

上一轮为了避免 `quality_summary.verdict=fail` 被 `status=success` 掩盖，bundle 顶层 `status` 被升级成了组合态：

- `success`
- `quality_failed`
- `failed`

这样对人工复盘更醒目，但也引入了兼容性风险：外部若只认 `success/failed` 两值，可能因为新枚举 `quality_failed` 直接中断。

## 2. 目标

在保留“质量失败可见性”的前提下，恢复 `status` 的旧兼容语义。

完成后：

- `status` 继续保持旧语义，适合历史消费者直接使用
- `overall_status` 承担新的总体结论语义
- `execution_status` 保留明确执行态

## 3. 非目标

- 不撤销 `quality_summary`
- 不改变 exit code
- 不改变 step 级状态

## 4. 设计

### 4.1 字段语义

- `status`
  - 兼容旧语义
  - `success`：没有 step 失败
  - `failed`：任一 step 失败
- `execution_status`
  - 显式执行态
  - `success / failed / dry_run`
- `overall_status`
  - 新的总体结论
  - `failed`：step 失败
  - `quality_failed`：step 成功，但 `quality_summary.verdict=fail`
  - `success`：step 成功，且质量未失败
  - `dry_run`：dry run

### 4.2 优先级

优先级：

1. `failed`
2. `quality_failed`
3. `success`

`dry_run` 只在不执行子步骤时出现。

### 4.3 报告与指针

在 `bundle_report.md` 顶部同时显示：

- `status`
- `execution_status`
- `overall_status`
- `quality_verdict`

在 `bundle_pointer.json` 中同步写入：

- `status`
- `execution_status`
- `overall_status`

## 5. 测试策略

至少覆盖：

- 正常成功：`status=success`，`overall_status=success`
- 质量失败：`status=success`，`overall_status=quality_failed`
- step 失败：`status=failed`，`overall_status=failed`
