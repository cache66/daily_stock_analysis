# Earnings Surprise Quality Signal

## Summary

`earnings_quality` is now a core input of `earnings_surprise`, not just an auxiliary field.

The scan still keeps the original conservative rule:

- negative earnings text blocks first

But after that, `earnings_quality` now participates in both:

- pass/fail gating
- candidate ranking

## What Changed

The earlier integration only let `earnings_quality` act as an extra confirmation path:

- `verdict in {"good", "strong"}`
- or `score_total >= 65`

The current strategy goes one step further and reuses the quality sub-scores inside a hybrid earnings strategy:

- growth continuity
- profit quality
- profitability
- disclosure signal
- cycle phase
- risk penalty

## Why It Matters

This helps the earnings line avoid over-relying on:

- sparse earnings text
- a single quarter snapshot
- one-off YoY spikes caused by low base effects

and instead pay more attention to:

- multi-quarter continuity
- cashflow/profit matching
- profitability quality
- whether the company is in recovery, expansion, or downcycle

## Related Doc

For the full current strategy, thresholds, and exported fields, see:

- `docs/EARNINGS_SURPRISE_TRACKING.md`
