# Strategy V2 Risk Improvement Stage — Experiment 001 Preregistration

```text
EXPERIMENT_ID = strategy-v2-risk-exp001-atr-adaptive
STATUS = PREREGISTERED_BEFORE_EXECUTION
DATA_ROLE = EXPOSED_TRAINING_DEVELOPMENT
PERIOD = 2023-01-01 through 2024-12-31
STOCKS = 2303, 2330, 2379, 2454, 3034
FOLDS = WF1, WF2, WF3
```

## Frozen candidate

Technical is `BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER`. ML uses existing ML V1 Random Forest OOF predictions only. The gate allows a long entry only for `BUY`; `HOLD` and `SELL` block. Risk V1 is the control. No model is retrained and no other model is compared in this experiment.

## Hypothesis and sole intervention

The sole intervention is an ATR-adaptive stop distance and the position size implied by that stop distance. Risk V1 uses a fixed 2.5% stop, 1% account risk, and 40% maximum position fraction. Risk V2 uses daily ATR(14), a fixed multiplier 2.0, the same 1% risk budget, and the same 40% position cap:

```text
ATR_PCT(D) = ATR14 through the latest completed session before D / close of that session
FINAL_STOP_DISTANCE(D) = max(0.025, 2.0 * ATR_PCT(D))
POSITION_FRACTION = min(0.01 / FINAL_STOP_DISTANCE(D), 0.40)
```

Daily OHLC is aggregated from existing 5-minute bars for complete 09:00–13:25 sessions. True Range is the maximum of daily high-low, absolute high minus previous close, and absolute low minus previous close. ATR(14) uses Wilder smoothing, seeded with the arithmetic mean of the first 14 valid True Ranges. For a trade date D, every input is from dates strictly earlier than D; no fill or interpolation is allowed. Candidate dates without a valid prior ATR are excluded from both control and treatment. No 2022, 2025, or 2026 bars are read.

## Kept unchanged

Technical signal and B market-regime filter; RF OOF and BUY-only gate; candidate dates; next tradable 5-minute bar open execution; take profit; technical exit; cooldown; min/max holding; fees, taxes, slippage; initial capital and integer-share engine caps; folds; stock universe; preprocessing and ML artifacts.

## Validity checks

The run is invalid if ATR uses date D or later, the common candidate/gate decision basis differs, data alignment fails, or a fill does not use the next tradable bar open. The V1 control must reproduce the frozen Technical V2-B RF result matrix. Sizing must respect the 40% cap, approximately 1% or less estimated account risk after integer rounding, and a stop distance no tighter than 2.5%.

## Metrics and classification

Report average and median return, worst MDD, win rate, Profit Factor, Sharpe, trade count, gross/net PnL, transaction cost, mean final equity, average/median/maximum position fraction, average/median stop distance, and average/maximum estimated account risk, overall, by stock and by WF fold.

- `RISK_V2_SUPPORTED`: worst MDD no greater than V1; Sharpe no lower than V1; PF at least 1; average return positive; at least 3/5 stocks have MDD improved or unchanged; at least 2/3 folds have Sharpe improved or unchanged.
- `RISK_V2_NOT_SUPPORTED`: both worst MDD and Sharpe are worse than V1, or average return is nonpositive and PF is below 1.
- `RISK_V2_MIXED`: neither rule above applies.
- `INVALID`: any validity check fails.

No parameter search, rescue, or follow-up adjustment is permitted within this experiment after results are seen.

