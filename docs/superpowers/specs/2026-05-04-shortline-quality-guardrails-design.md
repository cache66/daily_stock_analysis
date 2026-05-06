# Shortline Quality Guardrails Design

## 1. 背景

当前 `shortline` 与 `review bundle` 已经具备以下基础能力：

- `shortline_run/run_summary.json` 会输出候选数、解释数、来源分布、tool 命中、cache、tracking 等运行指标。
- `scripts/summarize_shortline_runs.py` 会汇总最近多次 shortline 运行，并产出 `anomaly_counts`、`latest_run_anomalies`。
- `scripts/run_shortline_review_bundle.py` 会把 shortline、runs summary、fast review 串成一份 bundle，并生成 `bundle_manifest.json` 与 `bundle_report.md`。

当前缺口是：这些运行指标还没有被提升为统一的“质量判定”。用户能看到信号，但不能快速判断：

- 这次运行是“可用”、“有风险”还是“应拦截”。
- 问题属于系统完整性，还是当天市场结果波动。
- 是否需要立即检查真实源、解释链路或子产物。

## 2. 目标

本次只做一层轻量质量门禁，不改选股算法本体。

完成后，用户应能在 `shortline_runs_summary.json/md`、`bundle_manifest.json`、`bundle_report.md` 中直接看到：

- 统一的 `quality_summary`
- 清晰的 `verdict`：`pass / degraded / fail / off`
- 失败项、告警项、通过项
- 简短建议，帮助定位下一步动作

## 3. 非目标

本次不做：

- 不改 `WonderTrader` / `FinGenius` 选股或解释策略本体
- 不引入复杂评分模型或机器学习质量分
- 不增加新的并行执行器或调度器
- 不默认阻断整个 shortline 主流程，只在 bundle 产物里给出门禁结论
- 不扩散到 Web / API / Desktop

## 4. 方案比较

### 方案 A：只新增质量展示

做法：

- 基于现有运行指标生成 `quality_summary`
- 只写到 JSON/Markdown，不影响 bundle 状态

优点：

- 风险最低
- 对现有脚本兼容性最好

缺点：

- 只能“看见问题”，不能形成明确门禁语义

### 方案 B：软门禁

做法：

- 统一生成 `quality_summary`
- 确定性系统问题记为 `fail`
- 市场相关或效率类问题记为 `warning`
- bundle 总体状态仍保持现有执行语义，但单独给出质量结论

优点：

- 能把“流程坏了”和“当天结果一般”拆开
- 最适合当前 shortline 日常使用场景

缺点：

- 还不是硬阻断，需要用户看报告结论

### 方案 C：硬门禁

做法：

- 只要命中质量规则就直接让 bundle 失败

优点：

- 最强硬

缺点：

- 容易把市场波动误判为系统故障
- 会增加日常人工处理成本

### 推荐

推荐方案 B。

原因：

- 仓库当前已有足够多的运行指标，缺的是判定层，不是采集层。
- 先把质量问题结构化，后续再决定是否把某些规则升级成真正阻断。
- 这样改动集中在 `summarize_shortline_runs.py` 和 `run_shortline_review_bundle.py`，不需要改动 shortline 主业务链路。

## 5. 设计

### 5.1 统一产物

新增统一字段 `quality_summary`，结构如下：

```json
{
  "profile": "standard",
  "verdict": "degraded",
  "failed_checks": ["source_real_engine_expected"],
  "warning_checks": ["tracking_continuity_weak"],
  "passed_checks": ["tool_errors_absent", "artifacts_complete"],
  "recommendations": [
    "Inspect scan_source_counts and wt_source_mode for source drift."
  ]
}
```

判定枚举：

- `pass`：没有失败项，也没有告警项
- `degraded`：没有失败项，但有告警项
- `fail`：存在失败项
- `off`：显式关闭质量判定

### 5.2 规则分层

质量规则分两类：

#### 系统完整性规则

命中后直接进入 `failed_checks`：

- `tool_errors_absent`
  - `tool_error_count > 0` 时失败
- `artifacts_complete`
  - bundle 关键产物缺失时失败
- `explanations_match_candidates`
  - `candidate_count != explanation_count` 时失败
- `source_real_engine_expected`
  - 仅在 `wt_source_mode=prefer_real_engine` 且存在候选时启用
  - 若候选来源不全是 `wondertrader_real_engine`，则失败

#### 质量风险规则

命中后进入 `warning_checks`：

- `tracking_continuity_weak`
  - 有候选但 `tracking_repeat_symbol_count == 0`
- `cache_health_low`
  - cache 开启且 `hits < misses`
- `parallelism_underused`
  - `misses >= 2` 且 `parallel_workers <= 1`

### 5.3 Profile

新增 `quality_profile`：

- `standard`
  - 失败项按系统完整性规则判断
  - 风险项只记 warning
- `strict`
  - 保持 `standard` 的失败项
  - 如果 warning 数量达到 2 个及以上，则整体 verdict 升级为 `fail`
- `off`
  - 不做质量判定，只返回 `verdict=off`

首版只支持这 3 个值，不继续增加更多参数，避免配置过度膨胀。

### 5.4 落点

#### `scripts/summarize_shortline_runs.py`

新增：

- CLI 参数 `--quality-profile`
- 每条 run entry 的 `quality_summary`
- 汇总层 `quality_verdict_counts`
- 汇总层 `latest_run_quality`

继续保留现有 `anomalies` / `anomaly_counts`，避免破坏既有使用方式。

#### `scripts/run_shortline_review_bundle.py`

新增：

- CLI 参数 `--quality-profile`
- 传递 `--quality-profile` 给 `summarize_shortline_runs.py`
- bundle 级 `quality_summary`

bundle 级 `quality_summary` 以“当前 shortline 运行 + runs summary 最新结论”为基础，其中：

- 当前运行负责真实源、候选/解释一致性、关键产物完整性
- runs summary 负责最近一次 run 的 cache / parallel / tool error / tracking 结论复用

#### Markdown 输出

在 `shortline_runs_summary.md` 与 `bundle_report.md` 中新增清晰的质量摘要段，至少显示：

- `quality_verdict`
- `failed_checks`
- `warning_checks`
- `recommendations`

## 6. 测试策略

只补聚焦单测，不跑全量长链路。

至少覆盖：

- `standard` 下的 `pass / degraded / fail`
- `strict` 下 warning 升级为 `fail`
- `off` 下返回 `verdict=off`
- `prefer_real_engine` 下非真实源触发失败
- bundle report 正确展示质量摘要

## 7. 风险与回滚

风险：

- 质量规则若过严，可能把正常波动误判成故障
- 历史 runs summary 中旧数据不一定包含所有新字段，需要保持默认值兼容

回滚方式：

- 运行时显式传 `--quality-profile off`
- 如需完全回退，撤销 `quality_summary` 相关字段和 CLI 参数即可
