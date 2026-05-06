# Shortline Quality Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 shortline runs summary 和 review bundle 增加统一的 `quality_summary`，把运行指标提升为可读的质量结论。

**Architecture:** 复用现有 `run_summary.json -> summarize_shortline_runs.py -> run_shortline_review_bundle.py` 链路，不改 shortline 主选股逻辑，只在汇总层增加质量规则、质量产物和 Markdown 展示。质量规则分为系统完整性失败项与软性告警项，并通过 `quality_profile` 控制严格程度。

**Tech Stack:** Python, pytest, existing `scripts/summarize_shortline_runs.py`, existing `scripts/run_shortline_review_bundle.py`

---

## File Map

- Modify: `scripts/summarize_shortline_runs.py`
  - 增加 `quality_profile` 参数、运行级 `quality_summary`、汇总级 verdict 统计
- Modify: `scripts/run_shortline_review_bundle.py`
  - 透传 `quality_profile`，生成 bundle 级 `quality_summary`，更新 Markdown 报告
- Modify: `tests/test_shortline_runs_summary.py`
  - 覆盖 `pass / degraded / fail / off / strict`
- Modify: `tests/test_shortline_review_bundle.py`
  - 覆盖 bundle manifest/report 的质量摘要与 `quality_profile` 透传
- Modify: `docs/CHANGELOG.md`
  - 记录用户可见的 bundle 质量门禁能力
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - 记录本轮 AI 改动
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
  - 记录 shortline bundle 质量门禁入口

### Task 1: Add Runs Summary Quality Evaluation

**Files:**
- Modify: `tests/test_shortline_runs_summary.py`
- Modify: `scripts/summarize_shortline_runs.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_shortline_runs_summary.py` 增加覆盖：

```python
assert summary_json["latest_run_quality"]["verdict"] == "degraded"
assert summary_json["quality_verdict_counts"] == {"degraded": 1, "fail": 1}
assert "tracking_continuity_weak" in summary_json["latest_run_quality"]["warning_checks"]
```

并增加：

```python
quality = module.evaluate_run_quality(entry, profile="off")
assert quality["verdict"] == "off"
```

- [ ] **Step 2: 跑定向测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py -q`

Expected: 断言失败或 `AttributeError`，因为还没有 `quality_summary` 能力。

- [ ] **Step 3: 实现最小质量评估**

在 `scripts/summarize_shortline_runs.py`：

- 增加 `--quality-profile`
- 为 run entry 补充 `tracking_repeat_symbol_count` / `tracking_longest_streak_days`
- 新增 `evaluate_run_quality(...)`
- 在 payload 中补充 `quality_verdict_counts` 和 `latest_run_quality`

- [ ] **Step 4: 重跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py -q`

Expected: `... passed`

### Task 2: Add Bundle Quality Summary And Report Output

**Files:**
- Modify: `tests/test_shortline_review_bundle.py`
- Modify: `scripts/run_shortline_review_bundle.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_shortline_review_bundle.py` 增加覆盖：

```python
assert "--quality-profile" in calls[1]
assert manifest["quality_summary"]["verdict"] == "degraded"
assert "tracking_continuity_weak" in manifest["quality_summary"]["warning_checks"]
assert "quality_verdict: degraded" in bundle_report
```

并增加真实源漂移场景：

```python
assert manifest["quality_summary"]["failed_checks"] == ["source_real_engine_expected"]
```

- [ ] **Step 2: 跑定向测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: 断言失败，因为 bundle 还没有 `quality_summary`。

- [ ] **Step 3: 实现最小 bundle 质量门禁**

在 `scripts/run_shortline_review_bundle.py`：

- 增加 `--quality-profile`
- `build_runs_summary_command(...)` 透传该参数
- 新增 bundle 级 `evaluate_bundle_quality(...)`
- 将 `quality_summary` 写入 manifest
- 在 `bundle_report.md` 中新增质量摘要段

- [ ] **Step 4: 重跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: `... passed`

### Task 3: Update Strategy Documentation And Final Verification

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [ ] **Step 1: 更新文档留痕**

在上述文档补充 shortline quality guardrails 的入口、结论语义与回滚方式。

- [ ] **Step 2: 运行最终验证**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py tests/test_shortline_review_bundle.py -q`

Expected: 全绿

Run: `py -3.10 -m py_compile scripts/summarize_shortline_runs.py scripts/run_shortline_review_bundle.py`

Expected: no output
