# Entry Sensitivity Audit Exp001 — Preregistration

**Experiment ID:** `strategy-entry-sensitivity-exp001`  
**Status:** `PREREGISTERED_NOT_EXECUTED`  
**Registration date:** 2026-10-21  
**Purpose:** Controlled Entry Sensitivity / Robustness Audit; not a highest-return parameter search.

## 1. Research question and rationale

This experiment tests whether the current Breakout Entry lookback of 55 may delay candidate generation, leave less price movement available, thin per-trade net edge, and increase the relative impact of transaction costs. It will compare only the three prespecified Entry values below. No experiment output has been generated or reviewed for this registration.

## 2. Arms and the single changed parameter

| Arm | `breakout_entry_period` | Role |
|---|---:|---|
| Breakout Entry 40 | 40 | Earlier Entry candidate |
| Breakout Entry 55 | 55 | Frozen Control |
| Breakout Entry 70 | 70 | Stricter Entry candidate |

The only allowed configuration difference is `breakout_entry_period`. No other Entry value may be added after results are seen. Candidate variants will be produced in memory or as separately identified frozen run configs; the current candidate config will not be overwritten.

The existing Technical V2 control is `BR_SENS_RANGE55_BB20_2`, Breakout Entry 55 and Exit 30. All non-Entry technical parameters remain as captured in `configs/technical_v2_candidate.json` at registration.

## 3. Frozen conditions

- Technical: existing Breakout rule `breakout_range_trigger_ma_direction_bb_upper_v1`; all MA, Bollinger, trend and breakout conditions fixed.
- Exit: Breakout Exit lookback 30; existing exit rule and all existing stop / hold / cooldown behavior remain fixed.
- Market filter: `TECH_V2_B_MARKET_REGIME_FILTER`, unchanged.
- Risk: Risk V1; `risk_per_trade=1%`, `stop_loss=2.5%`, `max_position_fraction=40%`; existing quantity, capital and account behavior unchanged.
- Execution: completed signal K-bar → next tradable 5-minute bar Open. Same-bar close execution is prohibited.
- Costs: use the existing formal commission, tax and slippage settings in `configs/technical_v1_research.json`; do not change costs.
- Other execution controls: cooldown, min/max hold, daily-entry restriction, entry window/cutoff, capital and sizing remain as in the frozen benchmark/config.
- ML gate: reuse the existing ML V1 OOF predictions. Keep Random Forest, XGBoost and GRU. No retraining, threshold changes, Label / Feature changes, or hyperparameter changes. BUY allows a long entry; HOLD and SELL block a long entry. SELL is never a short signal.
- Arms per Entry value: Technical Alone, Technical + RF, Technical + XGBoost, Technical + GRU.
- Buy & Hold is an external reference only: cite the existing Representative Stocks Benchmark Exp001; do not recalculate it for each Entry value.
- Stocks: 2330 台積電 (Trend), 2454 聯發科 (High Volatility), 2303 聯電 (Weak Trend). 2379 and 3034 may only be read as components of the unchanged Market Regime proxy. They must not appear as performance subjects or receive individual outcome metrics.
- Folds: reuse the existing Benchmark folds exactly; no fold changes.

| Fold | Training window | Evaluation window |
|---|---|---|
| WF1 | 2023-01-01–2023-06-30 | 2023-07-01–2023-12-31 |
| WF2 | 2023-01-01–2023-12-31 | 2024-01-01–2024-06-30 |
| WF3 | 2023-01-01–2024-06-30 | 2024-07-01–2024-12-31 |
- Starting capital/account structure: reuse the existing benchmark's independent NT$100,000 stock × fold accounts; no portfolio or short simulation.

## 4. Data scope and access policy

Use only 2023-01-01 through 2024-12-31 (`EXPOSED_DEVELOPMENT_DATA`). Use only the three formal subjects for reported stock results. Do not query, join, calculate, or otherwise access 2025, 2026, or Final OOS for this experiment.

2025 H2 remains `UNKNOWN`, `NOT_PROVABLY_UNSEEN`, and `NOT_FINAL_OOS`; it is forbidden for this run. No claim that it is unseen or available as Final OOS may be made.

## 5. Fair comparison and candidate coverage

Each Entry value generates its own legal Technical Candidate set; the sets are expected to differ and must not be forced to match. Technical Alone and all three Hybrid arms at a given Entry value must use the same candidate schedule after coverage eligibility is applied.

For Hybrid eligibility, align each candidate to the existing previous-actual-trading-day ML V1 OOF prediction. A candidate missing a valid prior-day OOF prediction for any of the three models is excluded from all four arms for that Entry value and reported as a common-coverage exclusion. Do not silently substitute another date or prediction. Report raw Technical candidates and eligible common-coverage candidates separately. Use the same fixed stock-fold evaluation windows for all values.

## 6. Required outputs and metric definitions

Report each `Entry value × Strategy arm × Stock × Fold`, plus rollups that retain the underlying stock-fold cells. At minimum include:

- Technical Candidate Count, Market Regime Pass Count, ML BUY Count and Actual Trade Count.
- Capital Exposure, Average Holding Bars and Average Entry Timing.
- Return, Maximum Drawdown, Sharpe, Profit Factor, Win Rate, Final Equity.
- Gross Positive PnL, Gross PnL, Transaction Cost, Net PnL, Average Net Edge Per Trade and Cost / Gross Positive PnL.

`Average Entry Timing` is the mean signal-bar position measured in 5-minute bars after the 09:00 session start; also record the next-bar-open fill timestamp under the unchanged execution contract. Use the same eligible account bars and exposure definition as the frozen benchmark.

For the time-difference diagnostic, form candidate episodes separately within each stock and fold by sorting the union of Entry 40/55/70 signal times and grouping consecutive signals no more than the existing 162 tradable-bar maximum-hold interval apart. Within each episode, take the first legal candidate for each Entry value. Report 40-vs-55 and 55-vs-70 signal-time differences only when both values occur in that episode; report matched episode counts and unmatched candidates. Express the signed difference in 5-minute bars and state positive values as the earlier signal. This pairing uses signal timestamps only, never future returns.

For candidate overlap, use exact candidate identity `(stock_code, fold_id, signal timestamp, next tradable fill timestamp)`. Report the three-way intersection, candidates exclusive to each Entry value, and pairwise overlap counts / Jaccard values. Do not merge candidates based on their eventual outcomes.

## 7. Fixed research questions

1. Does Entry 55 produce candidates later than Entry 40 for comparable signal episodes?
2. Does Entry 40 increase eligible opportunities, exposure or net edge, and does it also increase false-breakout loss, cost, or drawdown?
3. Does Entry 70 reduce trade count while improving candidate quality, net edge per trade or cost efficiency?
4. Does Entry 55 lie in a stable region, or is performance unusually concentrated at one Entry value?

## 8. Interpretation and robustness rules

- Highest Return alone never selects a winner.
- `ROBUST_REGION_EVIDENCE`: 40/55/70 show broadly consistent results across stocks and folds without an isolated single-value spike; evaluate return, risk, exposure, cost and per-trade edge together.
- `POSSIBLE_PARAMETER_SENSITIVITY`: one value appears distinctly better while the neighboring values are materially worse, or the apparent advantage is concentrated in one stock or one fold. Do not adopt that value from this evidence alone.
- `EARLIER_ENTRY_NOT_SUPPORTED`: Entry 40 adds trades/exposure but the added opportunities have weaker net edge or materially worse cost efficiency / drawdown across the cross-stock/fold results.
- `EARLIER_ENTRY_SUPPORTED`: Entry 40 improves net edge, exposure and return versus Control without clear risk or cost-efficiency deterioration, with directionally consistent evidence in at least 2 of 3 formal stocks and at least 2 of 3 folds.
- `STRICTER_ENTRY_SUPPORTED`: Entry 70 reduces trades but improves net edge, Profit Factor, Sharpe and cost efficiency with evidence across at least 2 stocks and 2 folds.
- If an effect appears only in one stock, also mark `MARKET_SPECIFIC_EFFECT`; do not call it a general improvement. If an apparent effect appears only in one fold, also mark `FOLD_SENSITIVE`; do not adopt it directly.
- Missing metrics remain missing and are labelled; do not impute them. Results based on 1–4 trades in a cell are `INSUFFICIENT`.
- These are audit classifications, not automatic adoption rules. No parameter or strategy is selected in this experiment.

## 9. Prohibited post-result actions

After any result is produced, do not add Entry values, alter Exit, Market Regime, Risk, costs, execution, ML gate/model/threshold/Label/Feature, stocks, folds, metric definitions or classification rules; do not drop poor-performing stocks or folds; do not retrain ML or launch a second audit. Preserve the full result and stop for reporting after this experiment.

## 10. Planned artifacts

If later explicitly authorized for execution, save outputs in a new, non-overwriting directory:

`exports/strategy_entry_sensitivity_exp001_<timestamp>/`

Include a machine-readable audit, per-entry/per-arm/per-stock/per-fold metrics, candidate overlap and timing diagnostics, and a report. The existing Benchmark Exp001 and Filter Funnel Audit Exp001 outputs remain unchanged. This registration alone creates no results and does not authorize execution.

## 11. Input identity captured at registration

| Input | SHA256 |
|---|---|
| `configs/technical_v2_candidate.json` | `4784a8ca5b1d3db4fae32700bb58e0b9934f7fa7768605a26c9e25485a7bbde5` |
| `trading_system/technical_v1.py` | `077e2fabcd0b839ed7b22d51724bf365f39dbea7b4022536616256d29f7c8d36` |
| `trading_system/backtest.py` | `94c2eb8da35dccf19f4a7c8cc3b542f22cf62a0b927e7f1a404cd33bb7f842c8` |
| `hybrid/core/strategy_v2_batch.py` | `7ef2a7d1389ca777d9ee2a7552ac7e26b19703a1cdece7e0a3060739206ba8ab` |
| `hybrid/scripts/run_strategy_v2_batch.py` | `1cdbf93a6519a1ef0b3d3c92cc29c4e22c9e3fa8f045d280764f23b39afb16e0` |
| `hybrid/core/risk_v1.py` | `b7b4e292c461d338e0f1a0d7bca0d36e54e2ba03601e7d3ceed3d9072f181547` |
| `hybrid/config/risk_v1.json` | `9035e97a0c9dff8f481af47d69147566712422e46222f9b1db5bbe66135d59ec` |
| `configs/technical_v1_research.json` | `027caee3f6cd623f9347d8f00376edadeb7c798666209acdf1161e9e0df25d75` |
| `hybrid/core/hybrid_poc_v1.py` | `73298e333a75cc114f0b0b7cb409016607fc1cb8425502fc51441daa2b64151b` |
| Existing common candidate schedule `exports/hybrid_common_coverage_20261018T121416162413/common_coverage_candidates.csv` | `e66c808eb73c9aef37cfeba411f0e8bfd3ce8b98cf49fe7d4f82ca1f7badbaca` |
| Existing ML V1 OOF `exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv` | `d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b` |
| Existing Technical V2 batch `exports/strategy_v2_technical_batch_20261019T094810074002/technical_v2_results.csv` | `e514c901f68a5f34f818d52d183acf5036e8322cb8cab33738d98946cba1fae8` |
| Technical source database `data/technical_v1_mltech_clean.sqlite3` | `aeabb19cd62b586e162252ed78de35f84fc8e9fa3ff22ed3baa1060f08dfc717` |

Before any future authorized execution, capture the execution runner hash, Git commit and working-tree state before producing performance output. If any frozen input hash changes, stop and request a new preregistration; do not silently refresh an input.

## 12. Registration decision

`ENTRY_SENSITIVITY_EXP001 = PREREGISTERED_NOT_EXECUTED`  
`BACKTEST_EXECUTED = FALSE`  
`PARAMETER_SEARCH = FALSE`  
`STRATEGY_CHANGED = FALSE`  
`ML_RETRAINED = FALSE`  
`2025_USED = FALSE`  
`2026_USED = FALSE`  
`FINAL_OOS_USED = FALSE`
