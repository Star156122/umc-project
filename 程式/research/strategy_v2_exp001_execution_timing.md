# Strategy V2 Experiment 001 — Execution Timing Preregistration

**Experiment ID:** `strategy-v2-exp001-next-bar-open`  
**Preregistration status:** `FROZEN`  
**Execution status:** `BLOCKED_PRE_RUN`  
**Data role / range:** Training / Development only, 2023-01-01–2024-12-31  
**Universe:** 2303, 2330, 2379, 2454, 3034  
**Control Candidate:** `BR_SENS_RANGE55_BB20_2`  
**Models:** Random Forest, XGBoost, GRU

## Research question and hypothesis

Does the existing completed-K-bar execution assumption use the signal bar close, or the next tradable bar open? The planned comparison is Same-Bar Close control versus Next-Bar Open experiment. Next-Bar Open is expected to represent a more executable chronology and reduce unrealistic fill risk; it is not expected to increase return.

## Primary variable

Entry execution timing and entry fill price only. The intended Exp001 rule is: after a completed signal K bar, wait for the next available legal 5-minute Technical-session bar and fill at its Open. The control is defined as filling at the completed signal bar Close. No same-bar fallback, last-price fallback, synthetic fill, or cross-day entry is allowed when there is no legal next bar; the candidate receives `NO ENTRY` with `EXECUTION_ELIGIBILITY_DIFFERENCE`.

## Frozen components

- Technical Candidate and parameters: `BR_SENS_RANGE55_BB20_2`, unchanged.
- Technical signal generation, exit rules, stop, holding period, cooldown, costs, slippage, initial capital, and original sizing: unchanged.
- ML: existing Training-only RF / XGBoost / GRU OOF predictions; no retraining or parameter change.
- Hybrid gate, Common Coverage eligibility, folds, universe, and dates: unchanged.
- Risk V1 secondary compatibility analysis, if the same execution comparison can be established; fixed at 1% risk per trade, 40% max position, 2.5% stop. Primary comparison excludes Risk sizing.
- Session: 09:00–13:25, 5-minute bars. Existing walk-forward folds are reused without modification.

## Required parity checks

Technical candidate identity, signal timestamp, ML OOF prediction, and Hybrid BUY/HOLD/SELL decision must match between control and Exp001. Only entry timestamp/price and their natural eligibility, sizing, PnL, and risk effects may differ. Future bars cannot be accessed before the signal decision. Any signal/gate mismatch blocks the experiment.

## Metrics

Average and median return, worst maximum drawdown, win rate, profit factor, Sharpe ratio, trade count, gross and net PnL, transaction costs, and final equity. Report by model, stock, and existing fold. Entry gap reporting includes average/median price difference and gap percentage, positive/negative/zero counts, missing-next-bar count, delayed entries, and trade-count difference.

## Failure and classification rules

`EXECUTION_REALISM_VALIDATED` requires a real Next-Bar Open fill, signal parity, gate parity, no future leakage, and valid chronology. `EXECUTION_SENSITIVE` is separately assessed from return, drawdown, trade count, per-stock stability, and per-fold stability. If the control is not Same-Bar Close, either variant cannot be produced under the frozen definition, or parity/chronology fails, classify `BLOCKED`; do not manufacture a comparison or performance result.

## Pre-run source audit and block

The existing backtest path already triggers signal processing when the next bar begins. `trading_system/backtest.py::replay_kbars` feeds the previous completed bar to the strategy while setting the current tick price to the current bar `Open`; `BacktestSafeTsst._create_local_order` fills market orders at that current tick price. Therefore the repository's current replay execution is Next-Bar Open, not Same-Bar Close. No distinct historical Same-Bar Close control artifact was found in the frozen baseline. The requested control/experiment comparison cannot be validly executed against the stated baseline; Exp001 remains `BLOCKED_PRE_RUN` until a valid same-bar-close control baseline is formally established under a new, reviewed experiment definition. This finding does not alter the frozen V1 behavior or results.

## Overfitting safeguards

This is execution realism validation, not performance optimization. Use Training/Development 2023–2024 only; do not use 2025, 2026, Holdout, Controlled Validation, or Final OOS. Do not tune parameters or redefine this Experiment ID after observing results. Any future changed hypothesis/control gets a new Experiment ID. Keep every model, stock, and fold visible; do not select a winner by return.
