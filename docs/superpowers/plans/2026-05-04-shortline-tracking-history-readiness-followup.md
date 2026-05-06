# Shortline Tracking History Readiness Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 避免 shortline 质量门禁在 tracking 历史不足时误报 `tracking_continuity_weak`。

**Architecture:** 在 runs summary 和 bundle 里都引入内部 `tracking_history_ready` 判断，只在存在更早交易日样本时才启用 tracking 连续性告警，不改 tracking 基础统计口径。

**Tech Stack:** Python, pytest, existing `scripts/summarize_shortline_runs.py`, existing `scripts/run_shortline_review_bundle.py`

---

## File Map

- Modify: `tests/test_shortline_runs_summary.py`
- Modify: `tests/test_shortline_review_bundle.py`
- Modify: `scripts/summarize_shortline_runs.py`
- Modify: `scripts/run_shortline_review_bundle.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

### Task 1: Add Failing Tests For Tracking History Readiness

**Files:**
- Modify: `tests/test_shortline_runs_summary.py`
- Modify: `tests/test_shortline_review_bundle.py`

- [ ] **Step 1: 写失败测试**

覆盖：

- 同日样本不再触发 `tracking_continuity_weak`
- 跨日样本第二日会触发 `tracking_continuity_weak`
- bundle 在只有当前日 `trade_date_counts` 时不触发本地 tracking warning

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py tests/test_shortline_review_bundle.py -q`

Expected: 断言失败，因为当前 tracking warning 还不区分历史是否充足。

### Task 2: Implement Tracking History Readiness

**Files:**
- Modify: `scripts/summarize_shortline_runs.py`
- Modify: `scripts/run_shortline_review_bundle.py`

- [ ] **Step 1: 实现最小 readiness 判断**

实现：

- runs summary 内部 helper
- bundle 内部 helper
- 只在 readiness 为真时才追加 `tracking_continuity_weak`

- [ ] **Step 2: 跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_runs_summary.py tests/test_shortline_review_bundle.py -q`

Expected: green

### Task 3: Update Docs And Verify

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [ ] **Step 1: 记录 tracking warning 触发条件收紧**

- [ ] **Step 2: 最终验证**

Run: `py -3.10 -m py_compile scripts/summarize_shortline_runs.py scripts/run_shortline_review_bundle.py`

Expected: no output
