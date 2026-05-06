# Shortline Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone shortline orchestration skeleton inside the current project, using stub `WonderTrader` and `FinGenius` adapters to prove the protocol, orchestrator flow, and report outputs.

**Architecture:** Keep the new capability isolated under `src/shortline_hub/` and a dedicated CLI entrypoint. The orchestrator will combine structured candidates from a `WonderTrader`-style adapter with explanation payloads from a `FinGenius`-style adapter, then export normalized JSON/Markdown artifacts without touching the current daily strategy chain.

**Tech Stack:** Python, dataclasses, pathlib, json, pytest.

---

## File Map

- Create: `src/shortline_hub/__init__.py`
- Create: `src/shortline_hub/schemas.py`
- Create: `src/shortline_hub/orchestrator.py`
- Create: `src/shortline_hub/report_builder.py`
- Create: `src/shortline_hub/adapters/__init__.py`
- Create: `src/shortline_hub/adapters/wondertrader_adapter.py`
- Create: `src/shortline_hub/adapters/fingenius_adapter.py`
- Create: `scripts/run_shortline_hub.py`
- Create: `tests/test_shortline_hub_orchestrator.py`
- Create: `tests/test_shortline_hub_cli.py`
- Create: `docs/architecture/shortline-hub-tracking.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`

## Task 1: Add Failing Orchestrator Tests

**Files:**
- Create: `tests/test_shortline_hub_orchestrator.py`

- [ ] **Step 1: Write the failing orchestrator aggregation test**

```python
from src.shortline_hub.adapters.fingenius_adapter import StubFinGeniusAdapter
from src.shortline_hub.adapters.wondertrader_adapter import StubWonderTraderAdapter
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator


def test_orchestrator_combines_candidates_and_explanations() -> None:
    orchestrator = ShortlineHubOrchestrator(
        scanner=StubWonderTraderAdapter(),
        explainer=StubFinGeniusAdapter(),
    )

    result = orchestrator.run(
        run_id="demo_run",
        trade_date="2026-05-02",
        top_n=2,
    )

    assert result.run_id == "demo_run"
    assert len(result.candidates) == 2
    assert len(result.explanations) == 2
    assert len(result.combined_results) == 2
    assert result.combined_results[0].hot_money_summary != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shortline_hub_orchestrator.py -v`

Expected: FAIL with import errors because `src/shortline_hub/*` does not exist yet.

- [ ] **Step 3: Add a failing report rendering test**

```python
from src.shortline_hub.adapters.fingenius_adapter import StubFinGeniusAdapter
from src.shortline_hub.adapters.wondertrader_adapter import StubWonderTraderAdapter
from src.shortline_hub.orchestrator import ShortlineHubOrchestrator
from src.shortline_hub.report_builder import build_shortline_report_markdown


def test_report_builder_renders_markdown_summary() -> None:
    orchestrator = ShortlineHubOrchestrator(
        scanner=StubWonderTraderAdapter(),
        explainer=StubFinGeniusAdapter(),
    )
    result = orchestrator.run(run_id="demo_run", trade_date="2026-05-02", top_n=1)

    report = build_shortline_report_markdown(result)

    assert "# Shortline Hub Report" in report
    assert "hot_money_summary" in report
    assert "000001" in report or "600000" in report
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_shortline_hub_orchestrator.py -v`

Expected: FAIL with missing module or missing symbol errors.

## Task 2: Implement Schemas, Stub Adapters, and Orchestrator

**Files:**
- Create: `src/shortline_hub/__init__.py`
- Create: `src/shortline_hub/schemas.py`
- Create: `src/shortline_hub/adapters/__init__.py`
- Create: `src/shortline_hub/adapters/wondertrader_adapter.py`
- Create: `src/shortline_hub/adapters/fingenius_adapter.py`
- Create: `src/shortline_hub/orchestrator.py`

- [ ] **Step 1: Add schema dataclasses**

Implement:

- `ShortlineCandidate`
- `ShortlineExplanation`
- `ShortlineCombinedResult`
- `ShortlineRunResult`

with fields aligned to the design doc.

- [ ] **Step 2: Add `StubWonderTraderAdapter`**

It should expose:

```python
def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
```

and return a stable, deterministic candidate list.

- [ ] **Step 3: Add `StubFinGeniusAdapter`**

It should expose:

```python
def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
```

and return deterministic explanation text based on the candidate.

- [ ] **Step 4: Add `ShortlineHubOrchestrator`**

It should expose:

```python
def run(self, *, run_id: str, trade_date: str, top_n: int) -> ShortlineRunResult:
```

and:

- fetch candidates
- explain each candidate
- merge them into `combined_results`

- [ ] **Step 5: Run tests to verify green**

Run: `python -m pytest tests/test_shortline_hub_orchestrator.py -v`

Expected: PASS.

## Task 3: Add CLI and Artifact Export

**Files:**
- Create: `scripts/run_shortline_hub.py`
- Create: `src/shortline_hub/report_builder.py`
- Create: `tests/test_shortline_hub_cli.py`

- [ ] **Step 1: Write the failing CLI artifact test**

```python
import importlib
from pathlib import Path


def test_shortline_hub_cli_writes_json_and_markdown(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("scripts.run_shortline_hub")
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "trade_date": "2026-05-02",
                "top_n": 2,
                "output_dir": str(tmp_path),
                "run_id": "demo_run",
                "log_level": "INFO",
            },
        )(),
    )

    exit_code = module.main()

    assert exit_code == 0
    assert (tmp_path / "shortline_candidates.json").exists()
    assert (tmp_path / "shortline_explanations.json").exists()
    assert (tmp_path / "shortline_report.md").exists()
    assert (tmp_path / "run_summary.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shortline_hub_cli.py -v`

Expected: FAIL because the CLI file and export helpers do not exist yet.

- [ ] **Step 3: Implement report builder**

Add helpers that:

- serialize candidates to JSON-safe dicts
- serialize explanations to JSON-safe dicts
- serialize combined results
- build a markdown report titled `# Shortline Hub Report`

- [ ] **Step 4: Implement CLI entrypoint**

The script should:

- parse `--trade-date`
- parse `--top-n`
- parse `--output-dir`
- parse `--run-id`
- run the orchestrator with stub adapters
- write:
  - `shortline_candidates.json`
  - `shortline_explanations.json`
  - `shortline_report.md`
  - `run_summary.json`

- [ ] **Step 5: Run test to verify green**

Run: `python -m pytest tests/test_shortline_hub_cli.py -v`

Expected: PASS.

## Task 4: Add Tracking Doc and Governance Notes

**Files:**
- Create: `docs/architecture/shortline-hub-tracking.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Add tracking doc**

The doc should record:

- current stage
- current scope
- non-goals
- next step
- external dependencies still mocked

- [ ] **Step 2: Update `docs/AI_MODIFICATION_LOG.md`**

Record:

- new shortline hub skeleton
- test commands
- artifact output command
- current stub-only status

- [ ] **Step 3: Update `docs/CHANGELOG.md`**

Add one flat `[Unreleased]` line noting the new shortline orchestration skeleton.

- [ ] **Step 4: Verify docs and code compile**

Run:

- `python -m py_compile scripts/run_shortline_hub.py src/shortline_hub/schemas.py src/shortline_hub/orchestrator.py src/shortline_hub/report_builder.py src/shortline_hub/adapters/wondertrader_adapter.py src/shortline_hub/adapters/fingenius_adapter.py`

Expected: PASS.

## Task 5: Final Verification

**Files:**
- Verify only

- [ ] **Step 1: Run focused tests**

Run:

- `python -m pytest tests/test_shortline_hub_orchestrator.py tests/test_shortline_hub_cli.py -v`

Expected: PASS.

- [ ] **Step 2: Run CLI smoke**

Run:

- `python scripts/run_shortline_hub.py --trade-date 2026-05-02 --top-n 2 --output-dir data/manual_runs/shortline_hub_smoke_20260502 --run-id shortline_hub_smoke_20260502`

Expected:

- exit code `0`
- output files exist in `data/manual_runs/shortline_hub_smoke_20260502`

- [ ] **Step 3: Confirm tracking doc reflects stub status**

Check:

- `docs/architecture/shortline-hub-tracking.md`

Expected:

- clearly states no real `WonderTrader` / `FinGenius` integration yet

## Self-Review

- Spec coverage:
  - independent directory: covered by `src/shortline_hub/*`
  - independent doc tracking: covered by `docs/architecture/shortline-hub-tracking.md`
  - no hard integration in v1: covered by stub adapters and non-goals
- Placeholder scan:
  - no `TODO` / `TBD`
- Type consistency:
  - all steps use `ShortlineCandidate / ShortlineExplanation / ShortlineCombinedResult / ShortlineRunResult`

## Execution Handoff

Plan complete and saved to `docs/architecture/2026-05-02-shortline-hub-implementation-plan.md`.

Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

In this repo, unless you redirect, I will continue inline in the current session.
