# Shortline Quality Observability Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 shortline 质量结论背后的关键判断依据显式写进结构化产物和根报告，减少人工反推。

**Architecture:** 不变更质量规则，只在现有 `quality_summary` 上补充 `signals`，并增加顶层便捷字段 `quality_failed_due_to`。同时在 runs summary 和 bundle report 根层展示关键可观测信息。

**Tech Stack:** Python, pytest, existing `scripts/summarize_shortline_runs.py`, `scripts/run_shortline_review_bundle.py`

---

## File Map

- Modify: `tests/test_shortline_runs_summary.py`
- Modify: `tests/test_shortline_review_bundle.py`
- Modify: `scripts/summarize_shortline_runs.py`
- Modify: `scripts/run_shortline_review_bundle.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

### Task 1: Add Failing Tests For Quality Signals

**Files:**
- Modify: `tests/test_shortline_runs_summary.py`
- Modify: `tests/test_shortline_review_bundle.py`

- [ ] **Step 1: 写失败测试**

覆盖：

- runs summary `latest_run_quality.signals.tracking_history_ready`
- bundle `quality_failed_due_to`
- bundle `quality_summary.signals.source_real_engine_expected_passed`
- root report 展示这些字段

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py tests/test_shortline_review_bundle.py -q`

Expected: 断言失败，因为这些依据字段还未显式落盘。

### Task 2: Implement Quality Signals

**Files:**
- Modify: `scripts/summarize_shortline_runs.py`
- Modify: `scripts/run_shortline_review_bundle.py`

- [ ] **Step 1: 实现最小依据落盘**

实现：

- `quality_summary.signals`
- bundle 顶层 `quality_failed_due_to`
- report 根层展示这些字段

- [ ] **Step 2: 跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py tests/test_shortline_review_bundle.py -q`

Expected: green

### Task 3: Update Docs And Verify

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [ ] **Step 1: 记录质量依据显式可观测化**

- [ ] **Step 2: 最终验证**

Run: `py -3.10 -m py_compile scripts/summarize_shortline_runs.py scripts/run_shortline_review_bundle.py`

Expected: no output
