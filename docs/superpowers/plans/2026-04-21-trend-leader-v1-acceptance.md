# Trend Leader V1 Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining V1 acceptance gaps by guaranteeing a conservative fallback candidate path and generating reproducible before/after timing evidence.

**Architecture:** Extend the existing fallback selector instead of changing the whole strategy pipeline, then add a narrow benchmark wrapper that reuses the current scan entrypoint with toggled fast-path options. Keep evidence generation outside the main scan path so daily usage stays simple.

**Tech Stack:** Python, pandas, existing trend-leader scripts, unittest/pytest-style tests, markdown/json report generation.

---

### Task 1: Lock the fallback guarantee with tests

**Files:**
- Modify: `d:/bb/daily_stock_analysis/tests/test_trend_leader_signal_flow.py`
- Modify: `d:/bb/daily_stock_analysis/scripts/select_trend_leader_candidates.py`

- [ ] **Step 1: Write the failing tests**

Add tests that expect:

- `tier4_last_resort` to appear when tiers 1-3 produce no rows
- blocked candidates to stay excluded even in `tier4_last_resort`

- [ ] **Step 2: Run targeted test execution and verify failure**

Run: inline Python runner for the new `test_*` functions in `tests/test_trend_leader_signal_flow.py`
Expected: FAIL because current fallback behavior does not yet satisfy the new assertions.

- [ ] **Step 3: Implement the minimal fallback change**

Update `_pick_fallback_pool(...)` in `d:/bb/daily_stock_analysis/scripts/select_trend_leader_candidates.py` so `tier4_last_resort` is only used after earlier tiers are empty and still excludes blocked or pseudo-leader rows.

- [ ] **Step 4: Re-run the targeted tests and verify pass**

Run: inline Python runner for the same `test_*` functions
Expected: PASS

### Task 2: Add reproducible benchmark reporting

**Files:**
- Create: `d:/bb/daily_stock_analysis/scripts/benchmark_trend_leader_v1.py`
- Create: `d:/bb/daily_stock_analysis/tests/test_trend_leader_benchmark.py`

- [ ] **Step 1: Write the failing benchmark tests**

Add tests that expect:

- report payload to include baseline/optimized elapsed values
- improvement percent calculation
- markdown output to contain a before/after comparison table

- [ ] **Step 2: Run the new benchmark tests and verify failure**

Run: `python -m unittest tests.test_trend_leader_benchmark`
Expected: FAIL because the script/module does not exist yet.

- [ ] **Step 3: Implement the benchmark script**

Create a wrapper script that:

- runs the same scan twice
- records elapsed seconds and selection counts
- writes json/md outputs under `data/trend_leader_benchmarks/<date>/`

- [ ] **Step 4: Re-run the benchmark tests and verify pass**

Run: `python -m unittest tests.test_trend_leader_benchmark`
Expected: PASS

### Task 3: Update required docs and evidence

**Files:**
- Modify: `d:/bb/daily_stock_analysis/docs/AI_MODIFICATION_LOG.md`
- Modify: `d:/bb/daily_stock_analysis/docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `d:/bb/daily_stock_analysis/docs/CHANGELOG.md`

- [ ] **Step 1: Document the behavior change**

Describe the new conservative `tier4_last_resort` guarantee and make it clear that results remain risk-labeled fallback rows.

- [ ] **Step 2: Document the evidence path**

Add the benchmark script and report output location to the strategy docs and changelog.

### Task 4: Run final verification and collect evidence

**Files:**
- No code changes required unless verification reveals issues

- [ ] **Step 1: Run syntax verification**

Run: `python -m py_compile scripts/select_trend_leader_candidates.py scripts/benchmark_trend_leader_v1.py tests/test_trend_leader_signal_flow.py tests/test_trend_leader_benchmark.py`
Expected: exit 0

- [ ] **Step 2: Run targeted tests**

Run: inline Python runner for `tests.test_trend_leader_signal_flow` plus `python -m unittest tests.test_trend_leader_benchmark tests.test_capital_profile_service`
Expected: all pass

- [ ] **Step 3: Run one real benchmark**

Run the new benchmark script with a bounded `--limit` and `--skip-db-persist` so it produces fresh before/after evidence.

- [ ] **Step 4: Review generated report**

Confirm the report includes baseline elapsed, optimized elapsed, improvement percent, selected counts, and output paths.
