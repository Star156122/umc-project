# Strategy V2 Exit Sensitivity Audit Exp001 — Preregistration

**Experiment ID:** `strategy-exit-sensitivity-exp001`  
**Status:** `PREREGISTERED_NOT_EXECUTED`  
**Registration date:** 2026-10-27  
**Purpose:** Controlled Exit Sensitivity Audit; this is not a parameter search or final strategy selection.

## Research question

Does the current 30-bar breakout exit leave too much giveback or cut trend trades too early? Compare only Exit 20, 30 and 40 while holding the entry and every other rule fixed. Exit 20 may realize exits sooner but increase churn; Exit 40 may keep trends longer but increase drawdown and exposure. These are hypotheses, not assumed effects.

## Arms and sole intervention

| Arm | `breakout_exit_period` | Role |
|---|---:|---|
| Breakout Exit 20 | 20 | Shorter exit lookback |
| Breakout Exit 30 | 30 | Frozen control |
| Breakout Exit 40 | 40 | Longer exit lookback |

Only `breakout_exit_period` may vary. `breakout_entry_period` remains 55. Technical candidates must be regenerated in memory for each arm because a different exit can change later signal availability through the existing position, holding and cooldown rules. Do not overwrite the frozen candidate configuration or prior results.

## Frozen conditions

- Technical: `BR_SENS_RANGE55_BB20_2` with `TECH_V2_B_MARKET_REGIME_FILTER`; Entry 55, all MA/Bollinger/trend/breakout logic and the peer regime filter fixed.
- Models: reuse the existing Random Forest, XGBoost and GRU OOF predictions. No retraining, threshold, label, feature or hyperparameter changes. Compare Technical Alone, Technical + RF, Technical + XGBoost and Technical + GRU. BUY allows a long entry; HOLD/SELL block; no shorting.
- Risk: Risk V1, 1% risk budget, 2.5% stop, 40% position cap; all existing quantity and capital behavior unchanged.
- Execution: completed signal 5-minute bar to next tradable 5-minute bar open.
- Other rules: market regime B, take-profit, minimum/maximum holding, cooldown, daily-entry limit, execution window, fees, tax, slippage, initial capital and account construction remain frozen.
- Subjects: 2330, 2303 and 2454 only. 2379 and 3034 remain market-proxy components, never outcome subjects.
- Folds: existing WF1, WF2 and WF3 from the frozen 2023–2024 configuration. Use independent NT$100,000 stock × fold accounts; no portfolio or short simulation.

| Fold | Training window | Evaluation window |
|---|---|---|
| WF1 | 2023-01-01–2023-06-30 | 2023-07-01–2023-12-31 |
| WF2 | 2023-01-01–2023-12-31 | 2024-01-01–2024-06-30 |
| WF3 | 2023-01-01–2024-06-30 | 2024-07-01–2024-12-31 |
- Data: exposed development data, 2023-01-01 through 2024-12-31 only. Do not access 2025, 2026, Additional Holdout or Final OOS data.

## Fair comparison and metrics

For each Exit value and stock-fold, use one identical candidate schedule for Technical Alone and all Hybrid arms after applying the same valid prior-session OOF coverage rule. Report raw candidates, eligible candidates, blocked/allowed gate counts, completed trades, exposure, holding duration, gross and net PnL, fees/tax, return, win rate, Profit Factor, maximum drawdown, Sharpe and average net edge per trade. Include overall, per-stock and per-fold tables, plus deltas against Exit 30. Cells with 1–4 completed trades are `INSUFFICIENT` under the project rule.

Do not select an Exit from total return alone. Report `MIXED` if the effect changes direction across stocks or folds, if the apparent advantage is concentrated in one stock/fold, or if return and risk/cost evidence disagree. A candidate may be described as development-supported only when improvement is directionally consistent in at least 2 of 3 subject stocks and 2 of 3 folds, without material deterioration in drawdown, per-trade net edge or cost efficiency. This classification does not promote a final strategy.

## Validity checks and stop conditions

- Confirm the exact frozen candidate, 55-bar Entry, Risk V1, fold dates, prior-session OOF alignment, data hashes and same-bar/next-bar execution contract before generating results.
- Reproduce Exit 30 control metrics against the existing frozen 2023–2024 benchmark for every strategy, stock and fold within the established tolerance.
- Fail the run if candidate/OOF coverage is not fair, control reproduction fails, any future/incomplete data is used, any fill violates next-bar-open execution, or a frozen input hash changes.
- Keep all artifacts in a new timestamped export directory. Preserve source and prior exports. Record all forbidden-data flags as false.

No Exit result has been generated or reviewed at registration time. The audit is limited to the fixed 2023–2024 exposed development sample and cannot establish independent validation or future performance.

## Frozen input identities at registration

The execution implementation currently present in the workspace is pinned below. The Exit 30 control must reproduce the existing frozen benchmark on all stock/fold/model cells before any Exit comparison is interpreted; a mismatch invalidates the run.

| Input | SHA256 |
|---|---|
| `configs/technical_v2_candidate.json` | `4784a8ca5b1d3db4fae32700bb58e0b9934f7fa7768605a26c9e25485a7bbde5` |
| `trading_system/technical_v1.py` | `077e2fabcd0b839ed7b22d51724bf365f39dbea7b4022536616256d29f7c8d36` |
| `trading_system/backtest.py` | `21d0712ab767ab02b5ff4dffd71ccd90a0a126006c56290cce292292601e7d02` |
| `hybrid/core/strategy_v2_batch.py` | `7ef2a7d1389ca777d9ee2a7552ac7e26b19703a1cdece7e0a3060739206ba8ab` |
| `hybrid/scripts/run_strategy_v2_batch.py` | `1cdbf93a6519a1ef0b3d3c92cc29c4e22c9e3fa8f045d280764f23b39afb16e0` |
| `hybrid/core/risk_v1.py` | `b7b4e292c461d338e0f1a0d7bca0d36e54e2ba03601e7d3ceed3d9072f181547` |
| `hybrid/config/risk_v1.json` | `9035e97a0c9dff8f481af47d69147566712422e46222f9b1db5bbe66135d59ec` |
| `configs/technical_v1_research.json` | `027caee3f6cd623f9347d8f00376edadeb7c798666209acdf1161e9e0df25d75` |
| `hybrid/core/hybrid_poc_v1.py` | `73298e333a75cc114f0b0b7cb409016607fc1cb8425502fc51441daa2b64151b` |
| `exports/hybrid_common_coverage_20261018T121416162413/common_coverage_candidates.csv` | `e66c808eb73c9aef37cfeba411f0e8bfd3ce8b98cf49fe7d4f82ca1f7badbaca` |
| `exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv` | `d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b` |
| `exports/strategy_v2_technical_batch_20261019T094810074002/technical_v2_results.csv` | `e514c901f68a5f34f818d52d183acf5036e8322cb8cab33738d98946cba1fae8` |
| `data/technical_v1_mltech_clean.sqlite3` | `aeabb19cd62b586e162252ed78de35f84fc8e9fa3ff22ed3baa1060f08dfc717` |
