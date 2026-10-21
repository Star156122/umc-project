# Strategy Representative Stocks Benchmark Exp001

**Experiment ID:** `strategy-representative-benchmark-exp001`  
**Status:** PREREGISTERED; do not inspect new benchmark outputs until the single fixed run completes.  
**Data role:** exposed Training / Development, 2023-01-01 through 2024-12-31.

## Question and fixed subjects

Compare Buy & Hold, Technical V2-B alone, and Technical V2-B plus each of the existing RF, XGBoost, and GRU ML V1 OOF gates for three already selected representative stocks:

| Stock | Role |
|---|---|
| 2330 台積電 | TREND |
| 2454 聯發科 | VOLATILE_SWING |
| 2303 聯電 | SIDEWAYS_WEAK_TREND |

2379 and 3034 are not backtest subjects and must not appear in performance tables or summaries. They may be read solely as constituents needed to preserve the already frozen five-stock core-universe proxy used by `TECH_V2_B_MARKET_REGIME_FILTER`; no standalone metrics or outcomes for those proxy-only stocks may be computed, reported, or used. The user explicitly authorized this proxy-only use on 2026-10-20.

## Frozen rules

- Technical candidate: `BR_SENS_RANGE55_BB20_2` plus the existing `TECH_V2_B_MARKET_REGIME_FILTER`; entry lookback 55 and exit lookback 30.
- The existing Technical V2-B regime proxy definition and implementation are reused unchanged. Its completed-session proxy requires all five core-universe stocks, excludes the target stock from the equal-weight mean, and uses only eligible prior common sessions.
- Hybrid arms reuse the existing ML V1 OOF predictions (no retraining), with BUY allowing a long entry and HOLD/SELL blocking it. SELL never means short.
- Technical Alone and all three Hybrid arms use the same Technical candidate schedule, common OOF coverage, Risk V1 sizing/stops, execution timing, transaction fees/tax, slippage, minimum/maximum hold, cooldown, and exits. The sole difference between Technical Alone and each Hybrid arm is the existing ML gate.
- Signal execution remains completed 5-minute signal to next tradable 5-minute bar Open. No current-close fill is introduced.
- Risk V1: 1% risk budget, 2.5% stop loss, 40% maximum position fraction, integer-share sizing, initial capital NT$100,000 per stock-fold account.
- The exact existing three folds are retained: WF1 trains 2023-01-01–2023-06-30 and evaluates 2023-07-01–2023-12-31; WF2 trains 2023-01-01–2023-12-31 and evaluates 2024-01-01–2024-06-30; WF3 trains 2023-01-01–2024-06-30 and evaluates 2024-07-01–2024-12-31.
- Buy & Hold is calculated independently per stock-fold from the first eligible evaluation-session open through the last eligible evaluation-session close. Buy the maximum whole-share quantity affordable from NT$100,000 after allowing the purchase commission; charge buy and sell commissions and sell transaction tax. It has no strategy, ML, risk stop, regime filter, or intermediate trades. Its long exposure is reported separately; PF/win rate/trade count are descriptive only and not primary cross-strategy measures.
- Capital exposure for all arms is defined as the average, over eligible evaluation 5-minute bar closes, of marked position notional divided by marked account equity. The report also shows maximum exposure. Buy & Hold's high sustained exposure is not by itself evidence that either method is superior.
- Primary metrics: return, maximum drawdown, Sharpe, final equity, net PnL, transaction cost, and capital exposure. Technical/Hybrid additionally report win rate, profit factor, trade count, and gross PnL. All calculations are per independent stock-fold account.
- Common coverage is restricted to Technical candidates for which all three models have a valid previous-session OOF prediction. Candidate counts, missing-OOF drops, invalid alignments, and date/model alignment must be audited. Technical Alone is evaluated on the same common-coverage candidate schedule as the Hybrid arms.
- Overall Development Summary is the mean across independent Stock × Fold accounts (with per-account fields retained); it is not a portfolio return.

## Predeclared comparison labels

For each ML model, compare its Hybrid arm with Technical Alone over the same nine stock-fold cells. A cell is a paired observation; lower positive maximum drawdown is better.

- `CLEARLY_STRONGER`: mean return is not lower; at least two of three stock-level mean returns are not lower; at least 6/9 cells improve or tie in return, at least 6/9 improve or tie in MDD, and at least 6/9 improve or tie in Sharpe; strict improvements must occur across at least two stocks and two folds.
- `RISK_ADJUSTED_IMPROVEMENT`: mean return is lower, but at least 6/9 cells improve or tie in both MDD and Sharpe, with strict improvements across at least two stocks and two folds.
- `NOT_SUPPORTED`: mean return is not higher and neither mean MDD nor mean Sharpe improves, with no cross-stock/fold pattern meeting the preceding criteria.
- `MIXED`: all other valid result patterns. No Final Champion is chosen.

For Buy & Hold comparisons, describe return, MDD, Sharpe and exposure together. Do not declare a winner from return alone.

## Data and exclusions

- Only Training / exposed Development OHLCV from 2023-01-01 through 2024-12-31 is allowed.
- 2025 H1, 2025 H2, 2026 H1, and Final OOS are forbidden for this run.
- No parameter, gate, label, feature, strategy, risk, or stock changes; no search, retraining, shorting, portfolio simulation, Filter Funnel Audit, or Strategy V3.
- Preserve all outputs from the one fixed execution. Do not rerun after viewing performance.

## Planned outputs

Create one `exports/representative_benchmark_exp001_<timestamp>/` folder containing the specified CSV, JSON, and offline HTML reports. Record final counts, hashes, checks, and results here after the run; do not alter this preregistration after results are known.

## Execution record

- Final output: `exports/representative_benchmark_exp001_20261020T111920272910/`.
- The first two code attempts stopped during result assembly (a Pandas aggregation name collision, then an old-result column-name mismatch). Neither attempt wrote a report or exposed performance output for review. Both were corrected before the final completed run; the registered experiment parameters and decision rule were unchanged.
- The final completed run produced 45 independent account rows: 3 subjects × 3 fixed folds × 5 arms. The 2023–2024 market proxy had 422 complete common sessions. Common Coverage retained 119 candidates and excluded 28 selected-stock candidates (18 missing at least one model OOF row; 10 invalid prior-session alignment).
- OOF alignment checks passed for all 119 retained candidates in each of RF, XGBoost, and GRU. All Technical and Hybrid arms shared the same candidate schedule. All 128 buy fills matched the next tradable 5-minute bar open. Each Hybrid model's selected-stock/fold V2-B results reproduced the frozen Technical V2 batch to floating-point tolerance.
- 2379 and 3034 were used only as V2-B proxy components. No individual performance metrics were calculated, saved, or used for these two stocks.
- The SQLite source hash was unchanged before and after execution; the frozen source-export hashes also remained unchanged. No 2025, 2026, or Final OOS data were used. ML was not retrained; no search, strategy change, risk change, short, or portfolio run occurred.

### Aggregate results

Metrics are arithmetic means across nine independent stock-fold accounts, except worst drawdown (the largest account drawdown), total trades/cost/PnL, and aggregate profit factor. This is not a portfolio return.

| Arm | Mean return | Worst MDD | Mean Sharpe | Trades | Win rate | Profit Factor | Net PnL | Costs | Mean exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Buy & Hold | 17.11% | 32.54% | 1.137 | 9 | n/a | 7.65* | NT$154,001 | NT$5,954 | 99.77% |
| Technical V2-B | 0.53% | 4.52% | -0.332 | 82 | 31.83% | 1.16 | NT$4,735 | NT$18,021 | 4.90% |
| Technical + RF | 0.67% | 1.35% | 0.274 | 19 | 36.90% | 2.70 | NT$6,046 | NT$4,280 | 1.33% |
| Technical + XGBoost | 0.92% | 1.35% | 0.144 | 16 | 44.05% | 3.15 | NT$8,315 | NT$3,596 | 1.12% |
| Technical + GRU | -0.38% | 1.76% | -0.745 | 11 | 13.89% | 0.37 | -NT$3,413 | NT$2,410 | 0.59% |

\* Buy & Hold has one continuous entry-to-exit position per stock-fold. Its PF is descriptive only and not a primary comparison measure.

### Per-stock mean results

Values are means across the three fixed evaluation folds for each stock. Profit factor is pooled over the stock's three folds.

| Stock | Arm | Mean return | Worst MDD | Mean Sharpe | Trades | PF |
|---|---|---:|---:|---:|---:|---:|
| 2330 | Buy & Hold | 25.30% | 24.60% | 1.628 | 3 | n/a |
| 2330 | Technical V2-B | 0.16% | 4.52% | -0.839 | 30 | 1.05 |
| 2330 | Technical + RF | 1.43% | 1.35% | 1.099 | 8 | 5.07 |
| 2330 | Technical + XGBoost | 0.79% | 1.35% | 0.345 | 8 | 2.52 |
| 2330 | Technical + GRU | -0.65% | 1.76% | -0.972 | 6 | 0.39 |
| 2454 | Buy & Hold | 29.09% | 32.54% | 1.973 | 3 | n/a |
| 2454 | Technical V2-B | 2.14% | 2.98% | 0.465 | 25 | 1.68 |
| 2454 | Technical + RF | 0.79% | 0.85% | 0.627 | 6 | 2.86 |
| 2454 | Technical + XGBoost | 1.97% | 1.28% | 0.070 | 4 | 4.55 |
| 2454 | Technical + GRU | -0.25% | 1.37% | -0.384 | 3 | 0.51 |
| 2303 | Buy & Hold | -3.05% | 28.79% | -0.189 | 3 | n/a |
| 2303 | Technical V2-B | -0.72% | 3.89% | -0.621 | 27 | 0.78 |
| 2303 | Technical + RF | -0.20% | 0.88% | -0.904 | 5 | 0.52 |
| 2303 | Technical + XGBoost | 0.01% | 0.88% | 0.018 | 4 | 1.03 |
| 2303 | Technical + GRU | -0.23% | 0.78% | -0.879 | 2 | 0.00 |

### Predeclared Hybrid classification

- RF: `MIXED`. Mean return improved by 0.15 percentage points; MDD tied/improved in 9/9 cells and Sharpe in 7/9, but return improved or tied in only 5/9, below the fixed classification threshold.
- XGBoost: `CLEARLY_STRONGER` against Technical V2-B under the preregistered paired-cell rule: mean return delta +0.40 percentage points; return improved/tied in 6/9 cells, MDD in 9/9, Sharpe in 6/9; strict improvement appeared across all three stocks and folds.
- GRU: `MIXED`. Mean return declined by 0.91 percentage points. MDD improved/tied in 9/9 cells, but Sharpe in only 2/9 and return in 4/9.
- No final model champion is selected. “Clearly stronger” is limited to this exposed 2023–2024 benchmark, three representative stocks, fixed folds, and the stated Technical V2-B/Risk V1 setup; it is not evidence of future performance.
- Buy & Hold had the highest average return but also much higher exposure and drawdown. Technical/Hybrid arms held substantially more cash and had far fewer trades. Compare these risk/exposure differences alongside returns; do not read the return ranking alone as a winner decision.

### Output inventory and commands

Main artifacts: `benchmark_summary.csv`, `benchmark_account_results.csv`, `benchmark_per_stock.csv`, `benchmark_per_fold.csv`, `benchmark_buy_hold.csv`, `benchmark_common_coverage.csv`, `benchmark_incremental_value.csv`, `benchmark_trades.csv`, `benchmark_audit.json`, `benchmark_report.html`, and `benchmark_user_analysis.html` in the final output folder. The HTML files are self-contained and can be opened offline.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -m pytest hybrid\tests\test_representative_benchmark_exp001.py -q
.\.venv\Scripts\python.exe hybrid\scripts\run_representative_benchmark_exp001.py --preflight
.\.venv\Scripts\python.exe hybrid\scripts\run_representative_benchmark_exp001.py --execute
```
