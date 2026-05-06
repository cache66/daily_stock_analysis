# Shortline Bundle Status Compatibility Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复 shortline bundle 顶层 `status` 的旧兼容语义，同时保留新的总体结论字段。

**Architecture:** 让 `status` 回到旧的执行结果语义，引入 `overall_status` 承担 `quality_failed` 这类组合态；保留 `execution_status` 做显式执行态，更新 pointer 和 report。

**Tech Stack:** Python, pytest, existing `scripts/run_shortline_review_bundle.py`

---

## File Map

- Modify: `tests/test_shortline_review_bundle.py`
- Modify: `scripts/run_shortline_review_bundle.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

### Task 1: Add Failing Tests For Compatibility Semantics

**Files:**
- Modify: `tests/test_shortline_review_bundle.py`

- [ ] **Step 1: 写失败测试**

覆盖：

- 质量失败时：
  - `status=success`
  - `overall_status=quality_failed`
- 正常成功时：
  - `overall_status=success`
- pointer 同步 `overall_status`

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: 断言失败，因为当前 `status` 仍直接使用 `quality_failed`。

### Task 2: Implement Compatible Top-Level Statuses

**Files:**
- Modify: `scripts/run_shortline_review_bundle.py`

- [ ] **Step 1: 实现最小兼容调整**

实现：

- `status` 恢复旧兼容语义
- 新增 `overall_status`
- pointer / report 同步

- [ ] **Step 2: 跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: green

### Task 3: Update Docs And Verify

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [ ] **Step 1: 记录兼容性调整**

- [ ] **Step 2: 最终验证**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py tests/test_shortline_runs_summary.py -q`

Expected: green

Run: `py -3.10 -m py_compile scripts/run_shortline_review_bundle.py`

Expected: no output
