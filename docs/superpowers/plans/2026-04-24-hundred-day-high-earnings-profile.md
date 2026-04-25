# Hundred-Day High Earnings Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `hundred_day_high` profile that keeps the existing breakout-balanced K-line rules but only retains candidates that pass the existing `earnings_surprise balanced` evaluation, and persist those hits under a separate `signal_type`.

**Architecture:** Extend `scripts/select_hundred_day_high_candidates.py` instead of adding a parallel script. The new profile reuses `breakout_balanced` K-line defaults, then runs a thin post-filter that loads fundamentals for already-selected candidates and evaluates them with the existing `evaluate_earnings_surprise_candidate(...)` path using the balanced preset. The filtered profile defaults to a new signal namespace so historical snapshots and existing `hundred_day_high` runs remain untouched.

**Tech Stack:** Python, argparse, pandas, unittest/pytest, markdown docs

---

## File Structure Map

- `D:\bb\daily_stock_analysis\scripts\select_hundred_day_high_candidates.py`
  Responsibility: profile resolution, runtime `signal_type`, selection/export/persist flow, new earnings post-filter.
- `D:\bb\daily_stock_analysis\tests\test_hundred_day_high_signal_flow.py`
  Responsibility: regression coverage for the new profile signal type, filtered selection, and persisted metrics.
- `D:\bb\daily_stock_analysis\docs\KLINE_SELECTOR_GUIDE.md`
  Responsibility: CLI usage and profile documentation for hundred-day-high runs.
- `D:\bb\daily_stock_analysis\docs\LOCAL_STRATEGY_CATALOG.md`
  Responsibility: local strategy entry and signal namespace inventory.
- `D:\bb\daily_stock_analysis\docs\AI_MODIFICATION_LOG.md`
  Responsibility: AI change log entry for this strategy-asset update.
- `D:\bb\daily_stock_analysis\docs\CHANGELOG.md`
  Responsibility: `[Unreleased]` flat changelog entries required by `AGENTS.md`.

### Task 1: Add Failing Tests For The New Profile

**Files:**
- Modify: `D:\bb\daily_stock_analysis\tests\test_hundred_day_high_signal_flow.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_runtime_signal_type_uses_earnings_namespace_for_new_profile(self):
    self.assertEqual(
        resolve_runtime_signal_type(
            signal_type=SIGNAL_TYPE,
            profile_name="breakout_balanced_with_earnings",
        ),
        "hundred_day_high__earnings_balanced",
    )


def test_filter_selected_results_by_earnings_balanced_keeps_only_passed_candidates(self):
    run_result = KlineSelectorRunResult(
        criteria=KlineSelectorCriteria(require_up_day_ratio=False, require_recent_limit_up=False, require_new_high=True),
        universe_size=2,
        evaluated_count=2,
        skipped_market_cap_count=0,
        skipped_prefilter_count=0,
        universe_codes=["600519", "000001"],
        selected=[
            KlineSelectionEvaluation(stock_code="600519", stock_name="贵州茅台", passed=True, history_source="fake", metrics={"close": 1800.0, "latest_high": 1818.0}),
            KlineSelectionEvaluation(stock_code="000001", stock_name="平安银行", passed=True, history_source="fake", metrics={"close": 12.0, "latest_high": 12.5}),
        ],
        failed=[],
    )
    filtered = filter_selected_results_by_earnings_balanced(
        run_result,
        snapshot_date=date(2026, 4, 24),
        db=self.db,
        bundle_loader=lambda code: {"growth": {}, "earnings": {}, "earnings_quality": {}, "mock_code": code},
        evaluation_loader=lambda code, name, bundle: code == "600519",
    )
    self.assertEqual([item.stock_code for item in filtered.selected], ["600519"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hundred_day_high_signal_flow.py -k "earnings_namespace or filter_selected_results_by_earnings_balanced" -v`
Expected: FAIL because the helper(s) do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
EARNINGS_BALANCED_PROFILE_NAME = "breakout_balanced_with_earnings"
EARNINGS_BALANCED_SIGNAL_TYPE = "hundred_day_high__earnings_balanced"

def resolve_runtime_signal_type(*, signal_type: str, profile_name: str) -> str:
    normalized_signal_type = str(signal_type or SIGNAL_TYPE).strip() or SIGNAL_TYPE
    if normalized_signal_type != SIGNAL_TYPE:
        return normalized_signal_type
    if profile_name == EARNINGS_BALANCED_PROFILE_NAME:
        return EARNINGS_BALANCED_SIGNAL_TYPE
    return SIGNAL_TYPE
```

- [ ] **Step 4: Re-run tests to verify the first helper is green**

Run: `python -m pytest tests/test_hundred_day_high_signal_flow.py -k "earnings_namespace" -v`
Expected: PASS.

### Task 2: Implement The Earnings Post-Filter

**Files:**
- Modify: `D:\bb\daily_stock_analysis\scripts\select_hundred_day_high_candidates.py`
- Modify: `D:\bb\daily_stock_analysis\tests\test_hundred_day_high_signal_flow.py`

- [ ] **Step 1: Add a failing regression for persisted metrics**

```python
def test_persist_selected_snapshot_includes_earnings_metrics(self):
    evaluation = KlineSelectionEvaluation(
        stock_code="600519",
        stock_name="贵州茅台",
        passed=True,
        history_source="fake",
        metrics={
            "close": 1800.0,
            "latest_high": 1818.0,
            "earnings_strategy_score": 71.0,
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_quality_signal": True,
        },
    )
    payloads = persist_selected_snapshot(
        evaluation,
        signal_type="hundred_day_high__earnings_balanced",
        snapshot_date=date(2026, 4, 24),
        criteria_payload={"profile_name": "breakout_balanced_with_earnings"},
        history_lookback_days=180,
        db=self.db,
    )
    assert payloads["metrics_payload"]["earnings_strategy_score"] == 71.0
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_hundred_day_high_signal_flow.py -k "persist_selected_snapshot_includes_earnings_metrics or filter_selected_results_by_earnings_balanced" -v`
Expected: FAIL because the filter helper does not merge the earnings metrics yet.

- [ ] **Step 3: Implement the minimal post-filter**

```python
def filter_selected_results_by_earnings_balanced(...):
    balanced_criteria = build_balanced_earnings_criteria()
    retained = []
    rejected = []
    for evaluation in run_result.selected:
        earnings_evaluation = evaluate_earnings_surprise_candidate(...)
        if earnings_evaluation.passed:
            evaluation.metrics.update(_extract_earnings_metrics(earnings_evaluation.metrics))
            retained.append(evaluation)
        else:
            rejected.append(_clone_with_failure_reason(...))
    return KlineSelectorRunResult(..., selected=retained, failed=run_result.failed + rejected)
```

- [ ] **Step 4: Wire the post-filter into main flow**

Run the helper after `scan_hundred_day_high_candidates(...)` and before persistence/export when `profile_name == "breakout_balanced_with_earnings"`. Also replace direct CLI `signal_type` usage with `resolve_runtime_signal_type(...)`.

- [ ] **Step 5: Re-run the focused tests**

Run: `python -m pytest tests/test_hundred_day_high_signal_flow.py -k "earnings_namespace or filter_selected_results_by_earnings_balanced or persist_selected_snapshot_includes_earnings_metrics" -v`
Expected: PASS.

### Task 3: Update Strategy Docs And Logs

**Files:**
- Modify: `D:\bb\daily_stock_analysis\docs\KLINE_SELECTOR_GUIDE.md`
- Modify: `D:\bb\daily_stock_analysis\docs\LOCAL_STRATEGY_CATALOG.md`
- Modify: `D:\bb\daily_stock_analysis\docs\AI_MODIFICATION_LOG.md`
- Modify: `D:\bb\daily_stock_analysis\docs\CHANGELOG.md`

- [ ] **Step 1: Document the new profile**

Add one CLI example and one explanation block for:
- `--profile breakout_balanced_with_earnings`
- default `signal_type=hundred_day_high__earnings_balanced`
- behavior: hundred-day-high breakout-balanced + `earnings_surprise balanced` confirmation

- [ ] **Step 2: Add catalog/log/changelog entries**

Update:
- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`

Use flat `[Unreleased]` lines only in `docs/CHANGELOG.md`.

- [ ] **Step 3: Verify the docs mention the new namespace consistently**

Run: `rg -n "breakout_balanced_with_earnings|hundred_day_high__earnings_balanced" docs/KLINE_SELECTOR_GUIDE.md docs/LOCAL_STRATEGY_CATALOG.md docs/AI_MODIFICATION_LOG.md docs/CHANGELOG.md`
Expected: the new profile and signal type appear in all four docs with no conflicting names.

### Task 4: Run Verification

**Files:**
- Verify only

- [ ] **Step 1: Run targeted tests**

Run: `python -m pytest tests/test_hundred_day_high_signal_flow.py -v`
Expected: PASS.

- [ ] **Step 2: Run syntax checks**

Run: `python -m py_compile scripts/select_hundred_day_high_candidates.py tests/test_hundred_day_high_signal_flow.py`
Expected: no output.

- [ ] **Step 3: Check scoped diff**

Run: `git diff -- scripts/select_hundred_day_high_candidates.py tests/test_hundred_day_high_signal_flow.py docs/KLINE_SELECTOR_GUIDE.md docs/LOCAL_STRATEGY_CATALOG.md docs/AI_MODIFICATION_LOG.md docs/CHANGELOG.md`
Expected: diff stays within this feature scope only.
