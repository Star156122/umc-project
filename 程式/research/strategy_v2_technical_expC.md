# Strategy V2-C — Exit Quality

**Status:** `PREREGISTERED` — performance not run.  
**Control:** `BR_SENS_RANGE55_BB20_2`; same Hybrid ML OOF gate and Risk V1 baseline.  
**Data role:** Training/Development only, 2023-01-01–2024-12-31; same five-stock universe and existing folds.

## Hypothesis and single change

The current breakout exit, a close below the prior 30-bar range low (after the existing minimum hold), may give back gains before exiting. A single faster structural trend exit may protect positions sooner, with the risk of exiting ordinary pullbacks too early.

## Exact rule

Replace only the Technical breakout exit condition `completed_bar_close < prior_30_completed_bar_low` with `completed_bar_close < EMA(20)` after the unchanged minimum holding period of 2 bars. EMA(20) is computed only from completed bars through the decision bar. Existing 2.5% stop-loss, any existing forced end-of-period closure, and other engine safety exits remain unchanged. No entry signal or entry confirmation changes.

## Frozen components and evaluation

Freeze entry Candidate and its parameters, signal timestamps, ML OOF/gate, Common Coverage, entry execution timing, Risk V1 settings, other exits, costs, universe, folds, and sizing. Report all models/stocks/folds with average/median return, worst MDD, win rate, PF, Sharpe, trade count, net PnL, costs, and stability.

## Expected benefit, downside, and risks

Expected benefit: exit weakening trends earlier and potentially reduce profit giveback. Possible downside: EMA(20) may trigger on short pullbacks and reduce trend participation. Overfitting risk is controlled by one fixed exit substitution, no period sweep, and complete stock/fold reporting; Training is exposed development data.

## Failure condition

Fail or mark insufficient if the original stop or minimum hold changes, if the EMA includes unfinished/future bars, if any entry behavior changes, or if stock/fold coverage becomes insufficient. No performance run is authorized by this preregistration alone.
