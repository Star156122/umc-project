# Strategy V2 Technical Batch — Execution Plan

**Status:** Frozen before batch execution. No performance results were viewed when this rule was written.

## Scope

Run `CONTROL`, `TECH_V2_A`, `TECH_V2_B`, and `TECH_V2_C` as four independent variants using the declared `BR_SENS_RANGE55_BB20_2` Candidate, the existing 2023–2024 Training Common-Coverage candidates, the existing RF/XGBoost/GRU OOF predictions, the same 3 Walk-Forward folds, Risk V1 sizing, costs, capital, and the current completed-K-bar → next tradable 5-minute bar Open execution contract. Each variant is evaluated against the same original Common-Coverage schedule; A/B may reject otherwise eligible entries only under their registered filter, and C changes only the registered exit.

Only 2023-01-01 through 2024-12-31 is queried. No model fitting, retraining, tuning, parameter search, combination variant, or forbidden-period data access is part of this run.

## Implementation details required by preregistration

- V2-A uses the signal bar's completed volume and exactly the preceding 20 completed 5-minute bar volumes. The signal bar is excluded from the reference mean. Any absent/non-finite required value rejects that entry without fill.
- V2-B uses the arithmetic equal-weight mean of the other four stocks' completed daily closes, excluding the target. A daily close is eligible only when all five stocks have a complete 54-bar 09:00–13:25 session. The latest eligible date must be strictly earlier than the candidate date; the latest proxy close is compared with its trailing 20 eligible-session proxy-close SMA. Missing history/components reject the candidate without imputation. This is a core-universe proxy, not an external market index.
- V2-C replaces only the breakout close-below-prior-30-bar-low exit with completed close below EMA(20), calculated through the completed decision bar. Existing minimum hold, 2.5% stop, period closure, and engine safety exits remain.

## Classification rule

`INVALID` means a data-role/date guard, immutable-source check, control reproduction, execution chronology, OOF/gate provenance, or required result-matrix check fails. It is a validity status, not a performance judgment.

For A and B, a model is directionally supportive when its aggregate Profit Factor improves and its worst MDD does not worsen versus Control. For C, it is directionally supportive when worst MDD improves and Profit Factor does not worsen. An experiment is `SUPPORTED` only when at least 2 of 3 models meet their criterion, at least 3 of 5 stock aggregates are non-degraded on the same two targeted measures, and at least 2 of 3 fold aggregates are non-degraded. It is `NOT_SUPPORTED` when at least 2 of 3 models worsen on both targeted measures and fewer than 3 stocks or fewer than 2 folds are non-degraded. Otherwise it is `MIXED`. No variant is selected or ranked as a winner; average return alone cannot determine classification.

If any expected sample is too small to calculate a metric, preserve it as unavailable, report the count, and classify no stronger than `MIXED` unless a validity guard fails (`INVALID`).
