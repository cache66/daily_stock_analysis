# Trend Leader Prefilter Hotpath Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce unnecessary `trend_leader` prefilter quote hydration on the hot path without changing CLI semantics, persistence behavior, or output structure.

**Architecture:** Keep the optimization scoped to `scripts/select_trend_leader_candidates.py`. Replace the current boolean hydration decision with a field-aware plan that only hydrates quote-backed fields worth requesting, while preserving fail-open behavior for fields that quote data cannot reliably supply. Extend tests first, then update observability so hot-run logs explain which fields were requested and why.

**Tech Stack:** Python, pandas, pytest

---

### Task 1: Add the regression tests for field-aware hydration

**Files:**
- Modify: `tests/test_trend_leader_signal_flow.py`
- Test: `tests/test_trend_leader_signal_flow.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_scan_prefilter_hydration_fields_skips_change_pct_60d_only_gap() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "change_pct_60d": None, "pct_change": 1.2, "turnover_rate": 1.5},
            {"code": "600002", "change_pct_60d": None, "pct_change": 0.8, "turnover_rate": 2.0},
        ]
    )

    fields = _resolve_scan_prefilter_hydration_fields(
        universe,
        min_change_pct_60d=4.0,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert fields == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py -k hydration_fields_skips_change_pct_60d_only_gap -q`
Expected: FAIL with `NameError` or import failure because `_resolve_scan_prefilter_hydration_fields` does not exist yet.

- [ ] **Step 3: Add the second failing test for positive hydration cases**

```python
def test_resolve_scan_prefilter_hydration_fields_requests_quote_backed_fields() -> None:
    universe = pd.DataFrame(
        [
            {"code": "600001", "pct_change": None, "turnover_rate": None},
            {"code": "600002", "pct_change": None, "turnover_rate": None},
        ]
    )

    fields = _resolve_scan_prefilter_hydration_fields(
        universe,
        min_change_pct_60d=None,
        min_turnover_rate=1.0,
        require_positive_change=True,
    )

    assert fields == {"pct_change", "turnover_rate"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py -k hydration_fields_requests_quote_backed_fields -q`
Expected: FAIL because the new helper still does not exist.

- [ ] **Step 5: Add the failing integration-style test for selective hydration**

```python
def test_prepare_scan_prefilter_universe_only_hydrates_quote_backed_missing_fields() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.requested_codes = []

        def get_realtime_quote(self, code: str):
            self.requested_codes.append(code)
            return {"change_pct": 2.5, "turnover_rate": 1.8}

    manager = FakeManager()
    universe = pd.DataFrame(
        [
            {"code": "600001", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
            {"code": "600002", "change_pct_60d": None, "pct_change": None, "turnover_rate": None},
        ]
    )

    prepared, stats = _prepare_scan_prefilter_universe(
        universe,
        manager=manager,
        min_change_pct_60d=4.0,
        min_turnover_rate=1.0,
        require_positive_change=True,
        relaxed_buffer_top_n=0,
    )

    assert manager.requested_codes == ["600001", "600002"]
    assert stats["quote_requested_fields"] == "pct_change,turnover_rate"
    assert stats["quote_missing_unsupported_fields"] == "change_pct_60d"
    assert prepared["code"].tolist() == ["600001", "600002"]
```

- [ ] **Step 6: Run the targeted tests to verify red**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py -k "hydration_fields or selective_hydration" -q`
Expected: FAIL with missing helper/stat fields before implementation.

### Task 2: Implement field-aware hydration planning and selective fill

**Files:**
- Modify: `scripts/select_trend_leader_candidates.py`
- Test: `tests/test_trend_leader_signal_flow.py`

- [ ] **Step 1: Add the helper that resolves which missing fields are worth hydrating**

```python
def _resolve_scan_prefilter_hydration_fields(
    universe: pd.DataFrame,
    *,
    min_change_pct_60d: Optional[float] = None,
    min_turnover_rate: Optional[float] = None,
    require_positive_change: bool = False,
) -> Set[str]:
    requested_fields: Set[str] = set()
    if universe.empty:
        return requested_fields
    if _safe_float(min_turnover_rate) is not None and _column_has_no_numeric_values(universe, "turnover_rate"):
        requested_fields.add("turnover_rate")
    if require_positive_change and _column_has_no_numeric_values(universe, "pct_change"):
        requested_fields.add("pct_change")
    return requested_fields
```

- [ ] **Step 2: Run targeted tests and confirm they still fail on downstream behavior**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py -k "hydration_fields or selective_hydration" -q`
Expected: helper tests move forward, integration test still FAILS because hydration and stats are not selective yet.

- [ ] **Step 3: Update hydration to accept target fields and produce field-aware stats**

```python
def _hydrate_scan_prefilter_quote_fields(
    universe: pd.DataFrame,
    *,
    manager: Any,
    target_fields: Optional[Set[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    normalized_targets = {field for field in (target_fields or set()) if field in fill_columns}
    if not normalized_targets:
        stats["requested_fields"] = ""
        return hydrated.reset_index(drop=True), stats
```

- [ ] **Step 4: Wire the helper into `_prepare_scan_prefilter_universe(...)`**

```python
requested_fields = _resolve_scan_prefilter_hydration_fields(
    working,
    min_change_pct_60d=min_change_pct_60d,
    min_turnover_rate=min_turnover_rate,
    require_positive_change=require_positive_change,
)
working, hydration_stats = _hydrate_scan_prefilter_quote_fields(
    working,
    manager=manager,
    target_fields=requested_fields,
)
```

- [ ] **Step 5: Preserve fail-open semantics for unsupported gaps**

```python
unsupported_fields = _resolve_scan_prefilter_unsupported_fields(
    working,
    min_change_pct_60d=min_change_pct_60d,
    min_turnover_rate=min_turnover_rate,
    require_positive_change=require_positive_change,
)
stats["quote_missing_unsupported_fields"] = ",".join(sorted(unsupported_fields))
```

- [ ] **Step 6: Run the targeted tests to verify green**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py -k "hydration_fields or selective_hydration" -q`
Expected: PASS

### Task 3: Extend observability and protect bundle compatibility

**Files:**
- Modify: `scripts/select_trend_leader_candidates.py`
- Modify: `tests/test_trend_leader_signal_flow.py`
- Modify: `tests/test_fast_review_daily_bundle.py`

- [ ] **Step 1: Add the new hydration summary fields to prefilter stats and logging**

```python
logger.info(
    (
        "trend leader quote prefilter: enabled=%s before=%s after=%s "
        "... quote_requested_rows=%s quote_requested_fields=%s "
        "quote_missing_unsupported_fields=%s ..."
    ),
    ...,
)
```

- [ ] **Step 2: Add or update a test that asserts the new stats are available from `run_stats`**

```python
assert result["run_stats"]["scan_prefilter_stats"]["quote_requested_fields"] == "pct_change,turnover_rate"
```

- [ ] **Step 3: Run bundle-facing tests to verify no command/output regressions**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_fast_review_daily_bundle.py -q`
Expected: PASS

### Task 4: Verify, document, and capture strategy asset changes

**Files:**
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`
- Verify: `scripts/select_trend_leader_candidates.py`
- Verify: `tests/test_trend_leader_signal_flow.py`
- Verify: `tests/test_fast_review_daily_bundle.py`

- [ ] **Step 1: Update local-strategy docs to record the hot-path hydration optimization**

```markdown
- `trend_leader_unified`: quote prefilter hydration is now field-aware and skips unsupported full-market hydration for `change_pct_60d`-only gaps.
```

- [ ] **Step 2: Record the implementation and verification evidence in the AI modification log**

```markdown
- 范围（Scope）：`trend_leader` 热路径前筛按需 hydration 优化。
- 验证（Validation）：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m py_compile scripts/select_trend_leader_candidates.py tests/test_trend_leader_signal_flow.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py tests/test_fast_review_daily_bundle.py -q`
```

- [ ] **Step 3: Add a flat unreleased changelog entry**

```markdown
- [改进] Reduce trend leader hot-path quote hydration by making scan prefilter quote backfill field-aware.
```

- [ ] **Step 4: Run compile verification**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m py_compile scripts/select_trend_leader_candidates.py tests/test_trend_leader_signal_flow.py tests/test_fast_review_daily_bundle.py`
Expected: exit code `0`

- [ ] **Step 5: Run the full targeted test verification**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_trend_leader_signal_flow.py tests/test_fast_review_daily_bundle.py -q`
Expected: PASS

- [ ] **Step 6: Optional hot-run spot check after code verification**

Run: `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -u scripts\run_fast_review_bundle.py --snapshot-date 2026-04-22 --include-signals trend_leader --output-dir data\fast_review_daily_tmp_prefilter_hot --progress-every 200 --log-level INFO`
Expected: the log still contains `trend leader quote prefilter` and should show lower or more selective `quote_requested_rows`.
