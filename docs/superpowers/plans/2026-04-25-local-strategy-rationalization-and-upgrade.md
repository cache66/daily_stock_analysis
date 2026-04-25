# Local Strategy Rationalization And Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce default daily strategies to a cleaner core set, demote weak/overlapping signals to observation tooling, and upgrade the retained strategies with stronger trend, earnings, industry-strength, and quality filters.

**Architecture:** Keep the strategy system layered instead of adding more peer strategies. The daily default set becomes a compact "core alpha layer" (`trend_leader_unified`, `earnings_surprise`, `hundred_day_high`), `monthly_slow_rise` remains a lower-frequency extension, and `continuous_up_*` becomes observation-only output. Enhancements are added inside existing strategy scripts and services rather than introducing parallel scanners.

**Tech Stack:** Python, existing local strategy scripts under `scripts/`, service layer under `src/services/`, markdown governance docs under `docs/`, existing performance evaluator `scripts/run_signal_performance_bundle.py`.

---

## File Structure

### Core files to modify

- `config/local_strategy_profile.json`
  - Daily default strategy set and fast-review behavior switches.
- `scripts/run_fast_review_bundle.py`
  - Daily bundle wiring, continuous signal handling, observation output placement.
- `scripts/run_signal_performance_bundle.py`
  - Cross-signal performance comparison; extend default signal list for comparison runs when needed.
- `scripts/select_hundred_day_high_candidates.py`
  - Add breakout-quality / industry-strength / trend-template style filters.
- `scripts/select_earnings_surprise_candidates.py`
  - Add post-event reaction, multi-quarter continuity, and richer quality confirmation.
- `scripts/select_trend_leader_candidates.py`
  - Add explicit trend-template / base-quality / industry-strength gating and ranking fields.
- `src/services/trend_leader_strategy_service.py`
  - Centralize any new trend-leader scoring and risk penalties.
- `scripts/select_monthly_slow_rise_candidates.py`
  - Add quality continuity, weekly-volatility compression, and liquidity filters.

### Secondary files likely to modify

- `docs/LOCAL_STRATEGY_BASELINE.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/KLINE_SELECTOR_GUIDE.md`
- `docs/EARNINGS_SURPRISE_TRACKING.md`
- `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`
- `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
- `docs/MONTHLY_SLOW_RISE_SCAN.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`

### Existing evidence / verification tooling to reuse

- `scripts/evaluate_signal_snapshot_performance.py`
- `scripts/run_signal_performance_bundle.py`
- `data/fast_review_daily/*`
- `data/manual_runs/monthly_slow_rise_*`

---

### Task 1: Freeze Baseline And Define The Keep / Demote Decision

**Files:**
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Use: `config/local_strategy_profile.json`
- Use: `scripts/run_signal_performance_bundle.py`

- [x] **Step 1: Confirm the target strategy layering before code changes**

Record this target layering in notes and later in docs:

- Core daily strategies:
  - `trend_leader_unified`
  - `earnings_surprise`
  - `hundred_day_high`
- Extension strategy:
  - `monthly_slow_rise`
- Observation-only signals:
  - `continuous_up_ratio`
  - `continuous_up_streak`
- Topic / research tools, not daily defaults:
  - `dragon_head_candidate`
  - `theme_core_mapper`
  - `commodity_price_pass_through`
  - generic `select_kline_candidates.py`

- [x] **Step 2: Run a baseline performance comparison before removing anything**

Run:

```bash
python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high,continuous_up_ratio,continuous_up_streak --start-date 2026-04-01 --end-date 2026-04-24 --windows 1,3,5,10
```

Expected:
- Generate `signal_performance_bundle_summary.csv` and `.md`
- Produce one row per `signal_type x window`
- Give a comparable baseline for `continuous_up_*` versus core strategies

- [x] **Step 3: Make the decision rule explicit**

Use this decision rule in docs and implementation notes:

- Keep as default only if the signal has:
  - a distinct role in the stack
  - non-trivial filters beyond a single simple metric
  - acceptable candidate quality in recent evaluation
- Demote if the signal is:
  - mostly descriptive / observational
  - highly overlapping with another strategy
  - weakly filtered and better used as a supporting field

- [x] **Step 4: Document the decision before editing runtime defaults**

Update:
- `docs/LOCAL_STRATEGY_BASELINE.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`

Add language that `continuous_up_ratio` and `continuous_up_streak` are observation signals, not future core daily selection strategies.

---

### Task 2: Remove Weak Signals From The Default Daily Set Without Deleting Their Output

**Files:**
- Modify: `config/local_strategy_profile.json`
- Modify: `scripts/run_fast_review_bundle.py`
- Test: fast-review output under `data/fast_review_daily/<date>/signals/`

- [x] **Step 1: Change the default include list**

Update `config/local_strategy_profile.json` so `defaults.include_signals` becomes:

```json
[
  "earnings",
  "hundred_day_high",
  "trend_leader"
]
```

Do not delete support code for `continuous_up_ratio` / `continuous_up_streak`.

- [x] **Step 2: Keep continuous signals available behind explicit opt-in**

Retain CLI support in `scripts/run_fast_review_bundle.py` so users can still run:

```bash
python scripts/run_fast_review_bundle.py --include-signals continuous_up_ratio,continuous_up_streak
```

Expected:
- Signals remain runnable
- They stop being part of the implicit default daily bundle

- [x] **Step 3: Reposition continuous signals as observation output**

In `scripts/run_fast_review_bundle.py`, keep `_collect_continuous_signals(...)`, but route messaging and report labels to observation wording:

- `continuous_up_ratio` -> short-term rhythm observation
- `continuous_up_streak` -> current streak observation

Do not present them in code comments or docs as peer alpha strategies.

- [x] **Step 4: Run the default bundle smoke test**

Run:

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-24 --limit 100
```

Expected:
- Default bundle runs without `continuous_up_*`
- Output still contains `earnings`, `hundred_day_high`, `trend_leader`
- No regression in signal directory creation

---

### Task 3: Upgrade `hundred_day_high` From "New High" To "Quality Breakout"

**Files:**
- Modify: `scripts/select_hundred_day_high_candidates.py`
- Modify: `docs/KLINE_SELECTOR_GUIDE.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [x] **Step 1: Add breakout-quality metrics to the candidate payload**

Introduce metrics for:

- breakout distance to rolling high
- pre-breakout volatility contraction
- recent turnover expansion on breakout
- industry / board relative strength if available
- trend-template alignment fields:
  - long-trend healthy
  - not too extended from major averages
  - not too close to long-period lows

Add them to the selected DataFrame and markdown export.

- [x] **Step 2: Add at least one pre-breakout compression rule**

Use recent daily history to calculate a simple contraction measure such as:

- average true range or daily range contraction over the last 5 to 10 bars
- or last-N range percentile versus the previous window

Require minimum contraction quality for stricter profiles; expose looser behavior for broader profiles.

- [x] **Step 3: Add breakout confirmation logic**

Add one or both of:

- breakout-day turnover / volume expansion over recent average
- breakout follow-through rule over the next 1 to 3 bars for post-analysis or later tagging

If follow-through cannot be used at scan time, at least save the metric for later evaluation.

- [x] **Step 4: Add industry-strength enrichment**

Reuse existing board / sector context where available and store:

- primary board / industry
- industry rank or relative strength bucket
- a simple `industry_strength_confirmed` boolean or score

- [x] **Step 5: Verify with profile comparison**

Run:

```bash
python scripts/select_hundred_day_high_candidates.py --profile breakout_balanced --snapshot-date 2026-04-24 --limit 300
python scripts/select_hundred_day_high_candidates.py --profile breakout_loose --snapshot-date 2026-04-24 --limit 300
```

Expected:
- Candidate count does not collapse unexpectedly
- Stronger profiles show more structured breakouts
- New fields appear in CSV / markdown outputs

---

### Task 4: Upgrade `earnings_surprise` Toward PEAD + Multi-Quarter Confirmation

**Files:**
- Modify: `scripts/select_earnings_surprise_candidates.py`
- Modify: `docs/EARNINGS_SURPRISE_TRACKING.md`
- Modify: `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`
- Modify: `docs/EARNINGS_SURPRISE_PLAYBOOK.md`

- [x] **Step 1: Add post-event price reaction metrics**

Extend evaluation to capture, where history permits:

- 1-day return after event
- 3-day return after event
- event gap / first reaction strength
- abnormal reaction label if the move materially exceeds a recent baseline

Store these under earnings-specific metrics instead of generic text notes.

- [x] **Step 2: Add multi-quarter continuity fields**

Use existing fundamental blocks to compute and store:

- consecutive quarters with positive revenue growth
- consecutive quarters with positive profit growth
- margin / ROE continuity if available
- a summarized `earnings_continuity_score`

- [x] **Step 3: Separate "single-event" from "persistent quality"**

Adjust scoring so the final `earnings_strategy_score` explicitly combines:

- fresh event surprise
- earnings quality
- multi-quarter continuity
- risk penalties

Do not let a single positive text summary dominate weak historical quality.

- [x] **Step 4: Add industry confirmation when available**

If board / sector context is available, store:

- industry
- same-board earnings risk / confirmation count
- whether the stock is a lonely beat or part of a broader earnings-strength cluster

- [x] **Step 5: Re-run low and high depth scans**

Run:

```bash
python scripts/select_earnings_surprise_candidates.py --strategy-profile balanced --scan-depth low --recent-event-scope latest_report_period --snapshot-date 2026-04-24 --limit 200
python scripts/select_earnings_surprise_candidates.py --strategy-profile balanced --scan-depth high --recent-event-scope latest_report_period --snapshot-date 2026-04-24 --limit 200
```

Expected:
- Low-depth remains operationally cheap
- High-depth adds continuity / reaction fields
- Candidate ranking is more defensible than text-only or single-quarter logic

---

### Task 5: Make `trend_leader_unified` The Clear Daily Core Strategy

**Files:**
- Modify: `src/services/trend_leader_strategy_service.py`
- Modify: `scripts/select_trend_leader_candidates.py`
- Modify: `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [x] **Step 1: Add explicit trend-template gating**

Add or expose fields for:

- near long-period highs
- distance from longer moving-average structure
- whether long-trend structure is intact
- whether price is overextended versus recent base

Do not rely only on current breakout / pullback booleans.

- [x] **Step 2: Add base-quality / VCP-like scoring**

Introduce a compact scoring block covering:

- volatility contraction quality
- shallow-pullback quality
- breakout readiness
- penalty for late-stage vertical extension

This should be a score contribution or risk penalty, not a separate strategy.

- [x] **Step 3: Strengthen industry leadership as a first-class field**

Use current board / sector data to add:

- board rank
- board breadth / health hint
- whether the candidate is leading a strong board or only strong in a weak board

- [x] **Step 4: Tighten fallback semantics**

Retain fallback output for operational continuity, but make sure fallback rows are clearly tagged as:

- not strict-core selections
- lower-confidence observations

Keep them queryable without letting them look identical to strict hits.

- [x] **Step 5: Verify score ordering and runtime**

Run:

```bash
python scripts/select_trend_leader_candidates.py --snapshot-date 2026-04-24 --limit 200 --fallback-top-n 20
```

Expected:
- Top-ranked names show clearer "leader + trend + industry + capital" consistency
- Fallback rows remain separated
- Runtime does not regress materially from current baseline

---

### Task 6: Upgrade `monthly_slow_rise` From Pure Shape To "Quality Slow Compounder"

**Files:**
- Modify: `scripts/select_monthly_slow_rise_candidates.py`
- Modify: `docs/MONTHLY_SLOW_RISE_SCAN.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`

- [x] **Step 1: Add weekly stability / compression metrics**

Using existing daily history, derive weekly-level fields such as:

- weekly volatility percentile
- recent weekly range compression
- shallow-pullback frequency

Attach them to the monthly candidate row rather than creating a separate weekly strategy.

- [x] **Step 2: Add earnings continuity filters**

Require or score:

- multi-quarter revenue continuity
- multi-quarter profit continuity
- optional profitability / cash-flow confirmation if available

Make this stricter in `robust`, lighter in `balanced`, optional in `loose`.

- [x] **Step 3: Add trading-quality filters**

Strengthen exclusion of weak names by enforcing:

- minimum liquidity or turnover
- minimum listed history
- avoid tiny illiquid microcaps even if the chart looks smooth

- [x] **Step 4: Improve ranking**

Sort retained rows with more weight on:

- higher-low stability
- moderate total return
- lower drawdown
- relative strength
- quality continuity

Avoid ranking by chart smoothness alone.

- [x] **Step 5: Re-run `robust` and `balanced` full comparisons**

Run:

```bash
python scripts/select_monthly_slow_rise_candidates.py --profile balanced --snapshot-date 2026-04-24 --limit 300
python scripts/select_monthly_slow_rise_candidates.py --profile robust --snapshot-date 2026-04-24 --limit 300
```

Expected:
- `robust` remains selective
- `balanced` remains usable
- rankings better reflect "slow bull + quality" rather than only shape

---

### Task 7: Keep Topic Tools But Stop Treating Them As Core Daily Selection

**Files:**
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/MAIN_STRATEGY_BLUEPRINT.md`
- Optional modify: related topic script help text if wording is misleading

- [x] **Step 1: Reclassify supporting strategy families in docs**

Explicitly document:

- `dragon_head_candidate` = topic / hot-sector specialist pool
- `theme_core_mapper` = theme decomposition tool
- `commodity_price_pass_through` = thematic logic framework
- `select_kline_candidates.py` = generic utility scan

- [x] **Step 2: Remove ambiguity about their runtime role**

State that these tools:

- are valuable for drill-down
- are not part of the core default daily selection loop
- should enrich decisions around core candidates instead of replacing the core layer

---

### Task 8: Update Governance Docs And Re-Measure Whole Strategy Stack

**Files:**
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`
- Use: `scripts/run_signal_performance_bundle.py`

- [x] **Step 1: Update all required local-strategy governance docs**

Because this task changes local strategy assets, update:

- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`

Keep `docs/CHANGELOG.md` `[Unreleased]` flat.

- [x] **Step 2: Run comparison after the core changes land**

Run:

```bash
python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high,monthly_slow_rise,continuous_up_ratio,continuous_up_streak --start-date 2026-04-01 --end-date 2026-04-24 --windows 1,3,5,10
```

Expected:
- `monthly_slow_rise` joins the comparison as an extension benchmark
- `continuous_up_*` can be shown as observation baselines
- core strategies have clearer justification than before

- [x] **Step 3: Re-run a practical daily smoke**

Run:

```bash
python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-24 --limit 200
```

Expected:
- Default daily output is simpler
- Default strategy set is clearer
- No topic-tool regression leaks into the daily path

---

## Recommended Execution Order

1. Task 1: freeze baseline and performance evidence
2. Task 2: remove weak defaults without deleting tooling
3. Task 5: strengthen `trend_leader_unified`
4. Task 3: strengthen `hundred_day_high`
5. Task 4: strengthen `earnings_surprise`
6. Task 6: strengthen `monthly_slow_rise`
7. Task 7: reclassify topic tools
8. Task 8: update docs and re-measure

## Verification Matrix For This Plan

- Python syntax check after each changed script:

```bash
python -m py_compile scripts/run_fast_review_bundle.py scripts/select_hundred_day_high_candidates.py scripts/select_earnings_surprise_candidates.py scripts/select_trend_leader_candidates.py scripts/select_monthly_slow_rise_candidates.py src/services/trend_leader_strategy_service.py
```

- Targeted tests if new unit-testable logic is extracted:

```bash
python -m pytest -q
```

- Signal performance comparison:

```bash
python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high,monthly_slow_rise,continuous_up_ratio,continuous_up_streak --start-date 2026-04-01 --end-date 2026-04-24 --windows 1,3,5,10
```

## Risks To Watch While Implementing

- Do not let signal count collapse so hard that the daily bundle becomes unusably sparse.
- Do not add new factors that require slow per-stock network fetches inside already hot loops without cache or prefilter support.
- Do not duplicate industry / board / quality logic separately in multiple scripts if a shared helper can be reused cleanly.
- Do not remove `continuous_up_*` support entirely; demotion is the goal, not deletion.
- Do not forget the required doc updates for local strategy assets.

---

## Runtime Verification Delta (2026-04-25)

### Re-run Rule

Use the same 4-signal full-range command and check progress/results every 5 minutes:

```bash
python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high,monthly_slow_rise --start-date 2026-04-01 --end-date 2026-04-24 --windows 1,3,5,10 --fill-missing-daily-data --fill-max-attempts 200 --output-dir data/manual_runs/task8_perf_observe_rerun_20260425 --log-level INFO
```

### Before / After Snapshot

| Item | Previous run (`task8_perf_observe_3m`) | Re-run (`task8_perf_observe_rerun_20260425`) |
| --- | --- | --- |
| Evaluator rule | natural-day horizon, no fill budget fields | trading-day horizon + `fill_max_attempts=200` |
| Artifact completion | partial (2/4): `trend_leader_unified`, `earnings_surprise`; no summary CSV | full (4/4) + summary CSV |
| First report mtime | `2026-04-25 21:02:43` (`trend_leader_unified`) | `2026-04-25 21:47:34` (`trend_leader_unified`) |
| Last report mtime | `2026-04-25 21:31:06` (`earnings_surprise`) then run interrupted | `2026-04-25 21:49:23` (`monthly_slow_rise`) |
| Fill attempts surfaced | not reported (`fill_stats` absent) | reported per signal (`0/4/3/0` for trend/earnings/hundred/monthly) |
| Horizon diagnostics | `missing_forward_bars` more common in recent windows | mostly `insufficient_forward_horizon` (expected for 2026-04-24 tail) |

### Re-run Outputs

- Summary:
  - `data/manual_runs/task8_perf_observe_rerun_20260425/2026-04-01_to_2026-04-24/signal_performance_bundle_summary.csv`
  - `data/manual_runs/task8_perf_observe_rerun_20260425/2026-04-01_to_2026-04-24/signal_performance_bundle_summary.md`
- Per-signal reports:
  - `trend_leader_unified`: `fill_attempted_count=0`
  - `earnings_surprise`: `fill_attempted_count=4`
  - `hundred_day_high`: `fill_attempted_count=3`
  - `monthly_slow_rise`: `fill_attempted_count=0`

### Quick Conclusion

- Trading-day horizon gating + fill budget control removes most no-op fill pressure.
- The same 4-signal window now converges to complete artifacts quickly and predictably.
- Remaining long-tail risk is external provider instability on a small subset of symbols, not evaluator control flow.
