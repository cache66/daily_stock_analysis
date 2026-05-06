# Shortline Bundle Status Risk Follow-Up Design

## 1. 背景

上一轮 shortline quality guardrails 已经在 bundle 根层产出 `quality_summary`，但当前仍有一个可用性风险：

- 子步骤全部成功时，`bundle_manifest.json.status` 仍为 `success`
- 即使 `quality_summary.verdict=fail`，顶层状态也不会升级

这会让调用方或人工复盘者更容易先看到 `status=success`，再忽略质量失败。

## 2. 目标

把 bundle 的“执行结果”和“质量结果”拆成两个并列但不混淆的信号：

- `execution_status`：只表示 step 是否执行成功
- `status`：表示顶层总体结论，可反映质量失败

完成后：

- step 级状态语义保持不变
- 顶层 `status` 可出现 `quality_failed`
- 根报告和 pointer 都能直接反映这一点

## 3. 非目标

- 不修改 `runs summary` 的质量规则
- 不让 `quality=degraded` 直接变成执行失败
- 不修改 exit code 语义

## 4. 设计

### 4.1 状态语义

- `execution_status`
  - `success`：三个 step 都执行完成
  - `failed`：任一 step 失败
- `status`
  - `failed`：执行失败
  - `quality_failed`：执行成功，但 `quality_summary.verdict == "fail"`
  - `success`：执行成功，且质量未失败

### 4.2 兼容策略

- `steps.*.status` 保持不变
- `exit code` 保持现有语义：
  - step 失败返回 `1`
  - `quality_failed` 先不改变返回码，保持 `0`
- `pointer_payload.status` 改为复用新的顶层 `status`
- 新增 `pointer_payload.execution_status`

### 4.3 Markdown 输出

在 `bundle_report.md` 顶部同时显示：

- `status`
- `execution_status`
- `quality_verdict`

这样用户无需翻 JSON 也能直接识别“执行成功但质量失败”的情况。

## 5. 测试策略

至少覆盖：

- `quality_summary.verdict=fail` 时，顶层 `status=quality_failed`
- 此时 `execution_status=success`
- pointer 同步写入 `status=quality_failed`
- step 失败时仍然是 `status=failed`
