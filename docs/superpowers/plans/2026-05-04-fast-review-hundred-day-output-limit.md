# Fast Review Hundred Day Output Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `fast review bundle` from incorrectly shrinking `hundred_day_high` input universe via the shared `--limit`, and instead cap only the bundle-visible hundred-day output rows.

**Architecture:** Keep the selector behavior unchanged. The bundle will stop forwarding shared `--limit` into the hundred-day selector command, then apply a dedicated post-load row cap for `hundred_day_high` before downstream unified/resonance/strategy-focus aggregation.

**Tech Stack:** Python 3.10, pytest, existing `scripts/run_fast_review_bundle.py` CLI + bundle aggregation tests.

---

### Task 1: Bundle CLI and Command Semantics

**Files:**
- Modify: `scripts/run_fast_review_bundle.py`
- Test: `tests/test_fast_review_daily_bundle.py`

- [ ] Add a dedicated bundle CLI argument for hundred-day output limiting, with a safe default.
- [ ] Keep shared `--limit` behavior unchanged for earnings / monthly / trend commands.
- [ ] Remove shared `--limit` forwarding from `build_hundred_day_high_command`.

### Task 2: Bundle-Side Output Clipping

**Files:**
- Modify: `scripts/run_fast_review_bundle.py`
- Test: `tests/test_fast_review_daily_bundle.py`

- [ ] Add a tiny helper that clips `SignalResult.rows` only for `SIGNAL_HUNDRED_DAY_HIGH`.
- [ ] Apply clipping immediately after external results are loaded, before unified/resonance/strategy-focus rows are built.
- [ ] Preserve row order from the selector CSV so clipping respects selector ranking.

### Task 3: Regression Coverage and Verification

**Files:**
- Modify: `tests/test_fast_review_daily_bundle.py`

- [ ] Add a failing command-build test proving `hundred_day_high` no longer inherits shared `--limit`.
- [ ] Add a failing bundle aggregation test proving `hundred_day_high` rows are clipped to the dedicated output limit.
- [ ] Run focused pytest and `py_compile` on changed Python files.
