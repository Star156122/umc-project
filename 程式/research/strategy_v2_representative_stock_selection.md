# Strategy V2 Representative Stock Selection Audit

**Audit ID:** `strategy-v2-representative-stock-selection`  
**Status:** preregistered selection rule; execution result is added below after the one-shot audit.  
**Purpose:** Select three unique representative stocks from 2303, 2330, 2379, 2454, 3034 using only each stock's own completed daily OHLCV characteristics. These representatives may be used in later Buy & Hold benchmark, parameter sensitivity, and filter-funnel audits.

## Scope and exclusions

- Data role: exposed Training / Development, `2023-01-01` through `2024-12-31` only.
- Universe: 2303 聯電, 2330 台積電, 2379 瑞昱, 2454 聯發科, 3034 聯詠.
- Source: `data/market_data.sqlite3`, exact 5-minute Training cache range; query only `market_kbars` with the five stock codes and local-date timestamp bounds.
- Session eligibility: all five stocks must each have exactly the 54 timestamps 09:00, 09:05, …, 13:25 on a date. Use the global shared complete-session intersection. No fill, interpolation, or synthetic bars.
- Daily OHLCV: first bar open, session high maximum, session low minimum, last bar close, and summed volume.
- No strategy, Technical, RF, XGBoost, GRU, Hybrid, Risk, backtest, Profit Factor, Sharpe, Win Rate, trade count, PnL, or strategy drawdown fields may be queried or used.
- This is stock-characteristic selection only, not a Buy & Hold benchmark experiment and not a comparison with Hybrid.

## Fixed metric definitions

1. **Total price return:** first eligible daily close to last eligible daily close.
2. **Annualized volatility:** sample standard deviation of daily close-to-close return × √252.
3. **Daily ATR(14):** Wilder smoothing. First ATR is the arithmetic mean of the first 14 true ranges; later values are `(previous_ATR × 13 + current_TR) / 14`. First true range is high-low; later true range is the maximum of high-low, abs(high-previous close), abs(low-previous close). Report mean and median `ATR14 / close`.
4. **Trend slope:** OLS of natural log daily close against the zero-based eligible trading-session index. Annualized slope is `(exp(session_slope × 252) - 1) × 100`; R² is OLS coefficient of determination. Direction is flat only when absolute slope ≤ `1e-12`; otherwise use its sign.
5. **20-session direction ratios:** each observation is `close[t] / close[t-20] - 1`; positive/negative ratio is the count above/below zero divided by all available observations. Zero stays in the denominator and neither numerator.
6. **10-session reversal frequency:** use the sign of `close[t] / close[t-10] - 1`; discard zero returns, count changes between adjacent remaining signs, and divide by the number of adjacent non-zero comparisons.
7. **Price maximum drawdown:** minimum `close / running_max_close - 1` over the eligible daily closing-price series. This is not strategy drawdown.
8. **Z-score:** for each metric, use the five-stock population mean and population standard deviation. If standard deviation is zero, z-score is zero. Use the same five-stock normalization for all role scores.
9. **Ties:** sort descending score, then ascending stock code.

## Fixed role scores and sequence

1. `TREND_SCORE = .40*Z(total return) + .30*Z(annualized trend slope) + .20*Z(R²) + .10*Z(positive 20-session ratio)`. Only stocks with total price return > 0 are eligible; select the highest.
2. Exclude the Trend stock. `SWING_SCORE = .40*Z(annualized volatility) + .30*Z(mean ATR%) + .30*Z(10-session sign-change rate)`. Select the highest.
3. Exclude both earlier selections. `SIDEWAYS_SCORE = .40*Z(-abs(total return)) + .30*Z(-abs(annualized trend slope)) + .20*Z(-R²) + .10*Z(10-session sign-change rate)`. Select the highest. This is a relative representative if absolute sideways behavior is weak.
4. The three role stocks must be distinct. If no positive-return stock exists, stop without forcing a Trend representative.

## Output and audit

Create one timestamped folder under `exports/representative_stock_selection_<timestamp>/` with metrics, score table, JSON results/audit and two offline HTML reports. Save this report at `research/strategy_v2_representative_stock_selection.md`; record completed experiment metadata in `research/registries/experiment_registry.csv` after the run. Do not modify strategies, configs, databases, or historical outputs.

## Pre-execution access note

During initial source verification, a read-only SQLite cache-metadata query on the Technical clean database returned cache-range descriptors and source row counts for 2025/2026 ranges. It did **not** query those periods' OHLCV rows or any performance results, and it will not be used by the selection analysis. The actual selection runner uses only the bounded 2023–2024 `market_kbars` query above. This metadata-only preflight access is disclosed so the audit does not overstate that no restricted-period metadata was ever observed.

## Execution results

**Execution:** completed with the preregistered formulas on 2026-10-20. The first implementation output was invalidated because it used `-abs(Z(raw metric))` rather than `Z(-abs(raw metric))` for two Sideways terms. That output is preserved with an `INVALIDATED.md` marker; the corrected run below is the only valid selection result.

- Eligible shared complete sessions: **422**, from 2023-01-04 through 2024-12-30; all five stocks use the exact same dates.
- 5-minute OHLCV rows read from the bounded 2023–2024 query: **132,175**. Non-session bars excluded: **2,405**, including 13:30 bars. No fill or synthetic bars were used.
- `TREND`: **2330 台積電** (score 1.1544), highest preregistered Trend Score among positive-total-return stocks.
- `VOLATILE_SWING`: **2454 聯發科** (score 0.6345), highest Swing Score after excluding 2330.
- `SIDEWAYS_WEAK_TREND`: **2303 聯電** (score 1.3896), highest corrected Sideways Score among the remaining stocks. This is a relative representative only; it is not a claim that 2303 was absolutely sideways.

### Price-characteristic metrics

| Stock | Total price return | Annualized volatility | Mean ATR% | Trend R² | 10-session reversal rate | Price max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 2303 聯電 | 4.85% | 25.61% | 2.07% | 0.0450 | 12.69% | -28.26% |
| 2330 台積電 | 140.84% | 29.69% | 1.95% | 0.8842 | 13.93% | -24.54% |
| 2379 瑞昱 | 93.78% | 34.12% | 2.95% | 0.7151 | 13.17% | -23.18% |
| 2454 聯發科 | 122.57% | 38.59% | 2.93% | 0.8420 | 11.82% | -33.78% |
| 3034 聯詠 | 49.33% | 30.13% | 2.55% | 0.5626 | 17.28% | -24.84% |

These are stock price descriptors, not strategy performance. The report does not establish that the selected stocks are profitable or that a strategy works better on them. No Buy & Hold strategy benchmark, backtest, model run, or parameter search was performed.

### Access and integrity record

- Analysis rows were limited to 2023-01-01 through 2024-12-31 and the five preregistered stocks. 2025/2026 OHLCV rows, strategy results, and Final OOS were not used.
- During initial source verification, a read-only cache-metadata query on the Technical clean database exposed 2025/2026 cache-range descriptors and source row counts only. No OHLCV or performance rows from those periods were queried or used. This metadata-only access is disclosed in the JSON audit.
- Validation passed: three unique selected representatives, same complete date set for all stocks, sufficient observations, finite metric values, no look-ahead, complete bars only, and no strategy-result columns queried.
- Candidate selection used only the fixed price-characteristic scoring rules. No strategy or strategy-performance fields were read.
- Strategy / Technical / ML / Hybrid / Risk / backtest logic: unchanged. Database: unchanged. 2025, 2026 and Final OOS performance: not used.

### Files

- Metrics: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_metrics.csv`
- Scores: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_scores.csv`
- Selection: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_selection.json`
- Audit: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_selection_audit.json`
- Full report: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_selection_report.html`
- User report: `exports/representative_stock_selection_20261020T104245668731+0800/representative_stock_selection_user_analysis.html`
- Preserved invalid first output: `exports/representative_stock_selection_20261020T104115967348+0800/INVALIDATED.md`
