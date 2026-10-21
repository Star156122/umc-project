# Strategy Representative Stocks Filter Funnel Audit Exp001

**Experiment ID:** `strategy-representative-filter-funnel-exp001`  
**Status:** COMPLETED  
**Data role:** exposed Development, 2023-01-01 through 2024-12-31 only.

## Question and subjects

This diagnostic audit measures attrition from Technical V2-B common-coverage candidates through Market Regime, the existing ML gate, execution eligibility, and actual trades. It evaluates the quality of blocked opportunities as independent diagnostic trades only; shadow outcomes are not strategy or portfolio returns.

Formal subjects are 2330 (TREND), 2454 (HIGH VOLATILITY), and 2303 (WEAK TREND), using the frozen WF1/WF2/WF3 definitions and the exact candidate schedule from Representative Stocks Benchmark Exp001. Stocks 2379 and 3034 may be read solely as components of the already frozen five-stock Market Regime proxy, under the user's explicit authorization. They are not audit subjects; no individual performance, funnel, or outcome metrics will be calculated, saved, or reported for them.

## Frozen inputs and rules

- Benchmark Exp001 Common Coverage candidates, existing RF/XGBoost/GRU OOF predictions, Technical V2-B (`BR_SENS_RANGE55_BB20_2`), Risk V1, execution timing, costs, stop, holding, cooldown, and exits are reused unchanged.
- Entry remains completed signal bar -> next tradable 5-minute bar open. No same-bar close execution.
- No retraining, parameter/model search, feature/label change, gate/risk/strategy change, or new candidate set.
- Development data only: 2023-01-01 to 2024-12-31. No 2025, 2026, or Final OOS access.
- Funnel stages: A common-coverage Technical candidates; B Market Regime pass/block; C model BUY/HOLD/SELL on regime-pass candidates; D execution eligibility and block reason for BUY; E actual entries/trades.
- Blocked candidate shadow diagnostics use frozen trade mechanics and are labelled DIAGNOSTIC ONLY. If a legal isolated outcome cannot be established because of path dependency or missing data, mark NOT_EVALUABLE. Never add shadow outcomes to policy return.
- Diagnostic classifications follow the exact preregistered criteria in the user's request: PRECISE_FILTER_EVIDENCE, OVER_CONSERVATIVE_EVIDENCE, MIXED, or INSUFFICIENT_EVIDENCE. No winner selection.
- Operationalization fixed before outcome review: fewer than 10 evaluable blocked opportunities or fewer than 2 represented formal stocks is INSUFFICIENT_EVIDENCE. "Clearly better" means higher average net return and higher Profit Factor in the admitted set, with the required cross-stock support; absent that evidence and absent the over-conservative rule, classify MIXED.
- Exposure and cost diagnostics use the completed Benchmark Exp001 policy-level results; no new policy-level performance claim is computed from shadows.

## Preflight and reproduction gates

Before outcome analysis, verify the fixed benchmark's common-coverage count and schedule, all three OOF model alignments, and next-bar-open fill evidence. If a gate fails, stop and report the failure rather than silently rebuilding or changing inputs.

## Planned outputs

Create one `exports/filter_funnel_audit_exp001_<timestamp>/` folder containing the requested funnel, blocked-opportunity, exposure, cost, JSON, and offline HTML reports. Update the experiment and dataset registries after completion. Do not modify the benchmark outputs or frozen research logic.

## Decision

Diagnostic audit only. The experiment does not select a model, change any strategy, or authorize Breakout/Exit experiments, gate changes, or Strategy V3.


## Execution record

- Final valid output: `exports/filter_funnel_audit_exp001_20261020T132127303170+0800`.
- Actual command: `.venv\Scripts\python.exe -u hybrid/scripts/run_filter_funnel_audit_exp001.py --execute --shadow-source exports/filter_funnel_audit_exp001_20261020T131535398083+0800/shadow_diagnostic_candidates.csv`.
- Common Coverage reproduction: PASS; 119 candidates, Market Regime 82 pass / 37 blocked. Existing RF/XGBoost/GRU OOF alignment and next-bar-open execution checks passed.
- Final funnel: RF 19 BUY / 19 eligible / 19 actual trades; XGBoost 16 / 16 / 16; GRU 11 / 11 / 11. Execution block reasons: none. Results match the frozen Benchmark Exp001 actual trade counts.
- Diagnostic classification: Market Regime `PRECISE_FILTER_EVIDENCE`; RF `PRECISE_FILTER_EVIDENCE`; XGBoost `PRECISE_FILTER_EVIDENCE`; GRU `OVER_CONSERVATIVE_EVIDENCE`. These are candidate-quality diagnostics only, not a winner selection.
- Low exposure is primarily attributed to the ML Gate by candidate-count attribution (63 RF, 66 XGBoost, 71 GRU candidates blocked after the 37 Market Regime blocks); this is not a path-independent causal exposure estimate.
- Technical-only costs were 53.8% of gross positive PnL; the report compares costs and net edge with existing Hybrid benchmark policies. Shadow outcomes were not used as policy returns.
- 2379 and 3034 were only proxy inputs. No proxy-stock subject rows or individual performance metrics appear in outputs. Technical DB SHA256 was unchanged.
- A prior completed export, `exports/filter_funnel_audit_exp001_20261020T131535398083+0800`, had invalid execution-event attribution and is explicitly marked superseded. Its 119 isolated shadow rows were reused as the frozen shadow source; the final run reran the execution/funnel attribution and passed actual-trade consistency checks. No strategy rules or candidate outcomes were changed.
- Tests: `hybrid/tests/test_filter_funnel_audit_exp001.py` — 4 passed.
- No 2025/2026/Final OOS access, parameter search, retraining, or strategy changes.
