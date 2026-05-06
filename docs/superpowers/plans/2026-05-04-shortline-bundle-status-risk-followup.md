# Shortline Bundle Status Risk Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解决 shortline bundle 顶层 `status=success` 掩盖 `quality_summary.verdict=fail` 的风险。

**Architecture:** 保留 step 状态和 exit code 语义，只在 bundle 顶层增加 `execution_status`，并把 `status` 升级为总体状态枚举 `success / quality_failed / failed`。同时更新根报告和 pointer。

**Tech Stack:** Python, pytest, existing `scripts/run_shortline_review_bundle.py`

---

## File Map

- Modify: `tests/test_shortline_review_bundle.py`
- Modify: `scripts/run_shortline_review_bundle.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

### Task 1: Add Failing Tests For Combined Bundle Status

**Files:**
- Modify: `tests/test_shortline_review_bundle.py`

- [ ] **Step 1: 写失败测试**

补充断言：

```python
assert manifest["execution_status"] == "success"
assert manifest["status"] == "quality_failed"
assert pointer_payload["status"] == "quality_failed"
assert pointer_payload["execution_status"] == "success"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: 断言失败，因为当前还没有 `execution_status` 和 `quality_failed`。

### Task 2: Implement Combined Status

**Files:**
- Modify: `scripts/run_shortline_review_bundle.py`

- [ ] **Step 1: 实现最小状态升级**

实现：

- 顶层 `execution_status`
- 顶层 `status=success|quality_failed|failed`
- pointer 同步写 `execution_status`
- report 顶部显示 `execution_status`

- [ ] **Step 2: 跑测试确认通过**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: `... passed`

### Task 3: Update Docs And Verify

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [ ] **Step 1: 记录状态语义变化**

- [ ] **Step 2: 最终验证**

Run: `py -3.10 -m pytest tests/test_shortline_review_bundle.py -q`

Expected: green

Run: `py -3.10 -m py_compile scripts/run_shortline_review_bundle.py`

Expected: no output
