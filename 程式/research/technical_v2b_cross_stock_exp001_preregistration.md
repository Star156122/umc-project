# Technical V2-B Cross-Stock Applicability Exp001

## Registration

- Experiment ID: `technical-v2b-cross-stock-2023-2024-exp001`
- Status: preregistered, research only
- Targets: 2303, 2330, 2382, 2454, 2317
- Period: 2023-01-01 through 2024-12-31; exposed Training / historical development only
- Baseline: frozen `BR_SENS_RANGE55_BB20_2` Technical V2-B + Risk V1, no ML Gate
- Capital: NT$1,000,000 independent account per target stock

## Fixed rules

No entry, exit, Risk V1, cost, or model rule is changed. Keep the frozen V2-B entry conditions (55-bar breakout confirmation, Bollinger upper-band condition, MA20 > MA60, MA120 trend/slope condition, close > MA240, and the frozen market regime filter), prior 30-bar-low exit, 2.5% stop, 162-bar maximum hold, 2-bar minimum hold, 6-bar cooldown, one entry per day, 1% risk budget and 40% position cap. Signals use completed bars and orders execute at the next actual tradable bar open. Fees are 0.1425%, tax 0.3%, slippage 0; report slippage honestly as zero.

## Data and eligibility

Use only `data/technical_v1_mltech_clean.sqlite3` unless preflight proves it lacks one or more requested symbols; if so, stop and report the gap rather than silently switching datasets. Do not fill, interpolate, synthesize, or forward/backfill bars. A valid session has exactly the real 54 timestamps from 09:00 through 13:25, unique and ordered, with valid OHLCV. Use the common intersection of complete dates across all five target stocks and the frozen V2-B peer pool (2303, 2330, 2379, 2454, 3034), so targets share the same eligible sessions and all proxy inputs are available.

Preserve the frozen market-proxy definition and component universe. For targets inside the original peer pool, exclude the target and use the other four peers. For 2382 and 2317, retain all five frozen proxy peers; do not redefine the frozen pool as the newly selected test set. Compute the equal-weight arithmetic mean of absolute 13:25 close prices and compare the latest prior eligible completed session with the mean of the preceding 20 eligible proxy sessions. Do not use the target day’s unfinished data or future data.

## Outcomes and analysis

Report per stock and per year: eligible sessions and completeness, Technical candidates, risk-accepted entries and completed trades, gross/net PnL, fees, tax, net return, win rate, PF, maximum drawdown, Sharpe (bar-return frequency and annualization stated), mean net trade return, mean holding days, false-breakout share using the preregistered definition (within the next three observed eligible session closes, any close at/below the recorded breakout line), and 100%, 40%, and average-active-exposure-scaled Buy & Hold benchmarks. The scaled benchmark is a nominal average-exposure comparison and must not be described as pathwise exposure matched.

Annual summaries are separate calendar-year entry cohorts, with each cohort initialized independently; indicators may use earlier permitted Training bars as warmup. Cross-year open positions are attributed to their entry year and are marked at the end of the corresponding calendar-year run under the same exit rules.

No per-stock parameter tuning, threshold search, or result-dependent changes are allowed. Report limitations and do not claim out-of-sample validation. This experiment does not modify the frozen strategy or its source/configuration.
