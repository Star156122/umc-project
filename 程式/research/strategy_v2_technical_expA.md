# Strategy V2-A — Entry Quality Confirmation

**Status:** `PREREGISTERED` — performance not run.  
**Control:** `BR_SENS_RANGE55_BB20_2`; same Hybrid ML OOF gate and Risk V1 baseline.  
**Data role:** Training/Development only, 2023-01-01–2024-12-31; five Technology stocks; same existing folds.

## Hypothesis and single change

Require a simple volume confirmation for an otherwise unchanged breakout BUY candidate. This may filter breakouts that occur without above-average trading activity, while possibly excluding valid early breakouts.

## Exact rule

On the completed signal bar, allow a Breakout BUY candidate only when that bar's volume is strictly greater than the moving average of volume over the preceding 20 completed 5-minute bars. The current signal bar is excluded from the 20-bar reference average. If any required volume value is missing or non-finite, reject the candidate without imputation. This is a single entry confirmation; it does not change the breakout threshold, other indicators, ML gate, exit, risk, costs, position sizing, or execution timing.

## Frozen components and evaluation

Freeze Candidate parameters except for this single confirmation condition, all Technical signal logic outside the condition, ML OOF/gate, Common Coverage, Risk V1, stocks, folds, costs, and data eligibility. Compare all three models, each stock and fold. Report average/median return, worst MDD, win rate, PF, Sharpe, trade count, net PnL, costs, and stability. Do not rank or select by return alone.

## Expected benefit, downside, and risks

Expected benefit: fewer low-participation breakouts. Possible downside: fewer trades and missed breakouts in stocks with naturally lower volume. Overfitting risk is reduced by one predeclared rule and no threshold sweep; the Training range is already exposed and results remain development evidence.

## Failure condition

Fail or mark insufficient if feature timing cannot exclude the current bar from its reference average, any future volume is used, or the rule causes inadequate samples / unstable stock-fold coverage. No performance run is authorized by this preregistration alone.
