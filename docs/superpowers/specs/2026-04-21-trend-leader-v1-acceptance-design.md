# Trend Leader V1 Acceptance Design

## Goal

Close the two remaining acceptance gaps for the trend-leader V1 work:

1. Eliminate the common "optimized run still returns 0 rows" outcome by adding a conservative last-resort fallback tier that is explicitly risk-labeled.
2. Produce reproducible before/after timing evidence for the scan optimization instead of relying on a single optimized run note.

## Scope

This design only changes the local trend-leader execution and acceptance tooling:

- `scripts/select_trend_leader_candidates.py`
- `tests/test_trend_leader_signal_flow.py`
- A new benchmark/report script under `scripts/`
- Required docs and changelog files

Out of scope:

- Reworking trend scoring semantics
- Changing API contracts outside existing fallback metadata fields
- Replacing capital-profile thresholds with market-wide quantile sampling

## Design

### 1. Conservative fallback guarantee

Keep the current layered fallback path (`tier1_near_miss -> tier2_watchlist -> tier3_broader_pool`) and make the final tier explicit and conservative:

- Only activate `tier4_last_resort` when tiers 1-3 still select no candidates.
- Exclude `pseudo_leader`.
- Exclude any item with `blocked_*` risk flags.
- Require `overall_score > 0`.
- Require at least one trend/capital/leader score signal strong enough to avoid complete noise.
- Mark every selected row with:
  - `selection_mode=fallback`
  - `fallback_tier=tier4_last_resort`
  - `risk_flags += ["relaxed_fallback_pool", "fallback_tier_tier4_last_resort"]`
  - a `[fallback:tier4_last_resort]` summary prefix

This preserves the "always show risk" behavior while reducing all-zero daily outputs.

### 2. Reproducible benchmark evidence

Add a small benchmark script that runs the same trend-leader scan twice with the same parameters:

- Baseline mode: disable scan prefilter and quote-seed reuse advantages that are being claimed as the V1 optimization.
- Optimized mode: enable the current V1 fast path.
- Capture:
  - elapsed seconds
  - selected row count
  - fallback row count
  - prefilter stats when available
  - percentage improvement
- Emit both JSON and Markdown outputs under `data/trend_leader_benchmarks/<date>/`.

This gives a stable acceptance artifact for "same params got faster" without depending on a manual note in `docs/AI_MODIFICATION_LOG.md`.

### 3. Documentation and evidence

Update:

- `docs/AI_MODIFICATION_LOG.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/CHANGELOG.md`

The log entry should distinguish:

- behavior change: last-resort fallback guarantee
- evidence change: reproducible benchmark script and latest measured result

## Testing

Add test coverage for:

- `tier4_last_resort` activation when tiers 1-3 pick nothing
- `tier4_last_resort` excluding blocked candidates
- benchmark report generation and comparison math

Then run targeted validation and one real benchmark run.
