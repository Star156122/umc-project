# Strategy V2-B — Market Regime Filter

**Status:** `PREREGISTERED` — performance not run.  
**Control:** `BR_SENS_RANGE55_BB20_2`; same Hybrid ML OOF gate and Risk V1 baseline.  
**Data role:** Training/Development only, 2023-01-01–2024-12-31; same five-stock universe and existing folds.

## Hypothesis and single change

Breakout entries may be less suitable when the broad direction of the research universe is weak. A single prior-session, universe-level trend regime filter may prevent entries during that state. The available five stocks are all semiconductor Technology stocks, so this is explicitly a **core-universe regime proxy**, not a Taiwan market index.

## Exact rule

For each target stock and decision date T, calculate an equal-weighted close index from the other four stocks (target excluded) using the latest common eligible completed daily close no later than T. Permit a Technical BUY candidate only when that proxy's latest close is above its trailing 20-trading-session simple moving average. The reference uses completed prior-session daily values only; no current-day incomplete value, centered window, forward fill, or interpolation. If any proxy component or the 20-session history is unavailable, exclude that candidate with an explicit reason.

## Frozen components and evaluation

Only the entry eligibility filter changes. Freeze Candidate parameters and signals, ML OOF/gate, exits, Risk V1, execution, costs, universe, folds, and the underlying Common Coverage sample definition apart from explicit filter exclusions. Report all models/stocks/folds with average/median return, worst MDD, win rate, PF, Sharpe, trade count, net PnL, costs, and coverage exclusions.

## Expected benefit, downside, and risks

Expected benefit: avoid breakouts while the peer-universe trend is below its intermediate trend. Possible downside: the filter may lag reversals and can suppress a strong stock because its peers are weak. Overfitting risks include the exposed Training period and the narrow, correlated universe; do not call this a broad-market regime result.

## Failure condition

Fail or mark insufficient if target exclusion, prior-session timing, or no-fill proxy eligibility cannot be proved; if any future/incomplete daily close is used; or if coverage becomes too small to compare. No performance run is authorized by this preregistration alone.
