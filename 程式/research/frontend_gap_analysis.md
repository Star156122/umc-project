# Frontend Integration Gap Analysis

只評估前後端串接缺口，不實作功能。`前端 Ready` 指目前網站有可操作畫面，`Backend Ready` 指已有可呼叫 route 和資料映射；若只在 exports，不能算 API ready。

| Feature | Current Status | Existing File | Backend Ready? | Frontend Ready? | Missing Work | Priority |
|---|---|---|---|---|---|---|
| Expo shell / routes | Expo Router routes and tabs implemented | `前端/src/app/`, `screens/` | Yes | Yes | Continue using existing route tree | P0 |
| Auth / user token | Login/register + JWT API implemented | `screens/AuthScreen.js`, `RegisterScreen.js`, `server/src/server.js` | Yes | Yes | Validate deployment env and error handling in normal setup | P0 |
| Yahoo stock search/quote | Live Yahoo data via Express | `server/src/marketService.js`, `HomeScreen.js` | Yes | Yes | Show source/as-of/stale state consistently | P1 |
| Watchlist | User MySQL favorites + quote | `favoriteService.js`, `WatchListScreen.js` | Yes | Yes | Existing path is adequate for current scope | P1 |
| Settings | Some profile and navigation UI; several settings are static rows | `SettingScreen.js` | Partial | Partial | Implement/remove account security, notifications, privacy and preference controls | P3 |
| Yahoo intraday 5m chart | Home history + interactive line/candlestick chart | `marketService.js`, `MarketTrendChart.js` | Yes, Yahoo only | Yes, Web-only chart | Label Yahoo source; implement empty/error/native state; current history is one day only | P1 |
| Research/canonical 5m K-line | SQLite/exports exist, no Expo route | `程式/data/`, `exports/`, `scripts/import_technical_v1_training_kbars.py` | No | No | Decide an approved read path and versioned data contract; separate from live Yahoo feed | P1 |
| Legacy strategy catalog | Six strategies offered in Analysis UI | `server/src/pythonBacktestService.js`, `AnalysisScreen.js`, `trading_system/backtest.py` | Yes | Yes | Label as legacy strategy suite; avoid implying V2 frozen implementation | P0 |
| Strategy V2 current analysis | No current Technical/ML/Hybrid/Risk API | `configs/technical_v2_candidate.json`, `hybrid/`, freeze docs | No | No | Build authorized frozen-candidate inference/data pipeline, then endpoint with source time/freeze ID; do not substitute OOF | P0 |
| Strategy V2 backtest summary | Freeze output exists locally, not API-backed | `exports/strategy_v2_development_freeze_20261019T181513514684/` | No | No | Read-only endpoint for a specific run/freeze, metric definitions, exposure label | P1 |
| Technical breakdown | Candidate, regime and signals in research exports | `hybrid/`, `trading_system/technical_v1.py`, technical exports | Export only | No | Map candidate/regime/signal/indicator fields to V2 response | P1 |
| ML V1 RF current signal | RF frozen as gate model; OOF is historical | freeze manifest, `hybrid_poc.../ml_v1_baseline_oof_predictions.csv` | Export only | No | OOF is not current; need inference pipeline and feature-date/probability contract before current display | P0 |
| XGBoost / GRU comparisons | Research OOF exists; neither frozen V2 model | same OOF export, ML reports | Export only | No | Optional research view; model/version/dataRole labels required | P2 |
| Hybrid gate decisions | Existing POC/batch decision CSVs | `hybrid_poc...`, Strategy V2 exports | Export only | No | Expose per-date `ALLOW_LONG_ENTRY`/`BLOCK` with aligned source rows | P1 |
| Risk V1 sizing | Risk decision CSV/config and freeze exist | `hybrid/config/risk_v1.json`, risk/freeze exports | Export only | No | Return capital basis, stop, max exposure, suggested shares from the frozen calculation | P1 |
| Legacy backtest summary metrics | Saved report mapper/DB has totals and equity fields | `server/src/databaseReportService.js`, `reportService.js`, `AnalysisScreen.js` | Yes, report-shaped | Yes, partial display | Normalize definitions, units, nulls and legacy versioning before merging V2 | P1 |
| V2 per-stock table | Per-stock CSV exists | freeze export `strategy_v2_per_stock_summary.csv` | Export only | No | Read-only query/endpoint with explicit aggregate semantics; do not name portfolio return | P2 |
| V2 per-fold table | WF1-WF3 CSV exists | freeze export `strategy_v2_per_fold_summary.csv` | Export only | No | Serve model/stock/fold IDs and metric definitions; display stability | P2 |
| Legacy trade log | Present in saved report/HTML/DB report data | `databaseReportService.js`, `reportHtmlService.js` | Yes, embedded | Yes, report/HTML | No dedicated paginated normalized route | P1 |
| V2 trade + signal/ML/gate/risk log | Research CSV exports exist | candidate qualification/freeze exports | Export only | No | Join by stable stock/date/fold/run keys; serve as immutable run-specific details | P1 |
| Legacy backtest LLM | Optional generation and report rendering | `trading_system/llm_analysis.py`, `AnalysisScreen.js` | Partial | Partial | Runtime provider/model/credentials can fail; show provenance and generated status | P2 |
| V2 LLM explanation | No dedicated V2 result explainer | No V2 endpoint | No | No | Generate only from saved structured result; keep separate from signal path | P2 |
| Stock news | Google News RSS via report-scoped API | `stockNewsService.js`, `StockNewsPanel.js` | Yes | Yes | Explain limited coverage and historical timing caveat | P2 |
| News headline summary | Gemini summary of title/date/source | same | Partial | Yes | Show model/time and explicitly identify headline-only summary | P2 |
| Industry news/sentiment | Not implemented | None | No | No | New source/model validation and separate research approval | P3 |
| User-entered capital | UI does not submit it; config supplies initial capital | `程式/backtest_config.json`, `configs/technical_v2_candidate.json`, `pythonBacktestService.js` | No request input | No | Add request-scoped execution capital after defining isolation from frozen research and reporting it separately | P2 |
| Current/development/OOS labels | Freeze/research docs classify roles, API UI does not normalize them | `strategy_v2_single_stock_freeze.md`, registries | Metadata only | No | Include role, period, freeze ID and warnings in every V2 report card | P0 |
| Error/loading/availability contract | Existing APIs return endpoint-specific error messages | `server.js`, `api.js` | Partial | Partial | Standardize error codes, stale state, retryability and `NOT_READY` payload | P0 |

## P0: Integration foundation

1. Agree on one versioned response contract and stable run/freeze identifiers before adding V2 pages.
2. Keep current Yahoo quote distinct from current frozen strategy analysis; the latter is currently `NOT_IMPLEMENTED`.
3. Add research role, period, source timestamps, candidate/model/risk IDs to responses and UI. Mark 2023–2024 `EXPOSED_DEVELOPMENT`; no verified clean Final OOS is available.
4. Ensure a V2 endpoint reads the frozen candidate artifacts/config identity and never presents an old OOF row as a current prediction.
5. Standardize API error, stale, unavailable and `NOT_READY` states.

## P1: Core V2 results display

1. Serve the existing frozen summary, per-stock/per-fold tables and run-specific trade/gate/risk rows through read-only, paginated endpoints.
2. Add one V2 result page with Technical → ML → Hybrid → Risk plus K-line/backtest/trade details; state that BUY allows long entry, HOLD/SELL block, SELL is not short.
3. Separate Yahoo live/history chart data from local research 5-minute data; explicitly show source, interval, coverage and timezone.
4. Normalize summary metric names, aggregation level and units before UI display.

## P2: Explanation and execution inputs

1. Add an optional LLM explanation over saved, structured V2 evidence only; include provider/model/generated time and no signal generation authority.
2. If users may select capital, make it a request-scoped execution parameter with isolated report identity; do not edit frozen strategy parameters or overwrite development research outputs.
3. Add an optional OOF research viewer for RF/XGBoost/GRU; visually separate non-frozen models and historical predictions from current signals.
4. Improve trade log search/paging and link signal/prediction/gate/risk records through stable keys.

## P3: Later product scope

Industry-wide news, sentiment scores, account notifications/preferences and portfolio/multi-stock allocation are not currently ready. Portfolio is outside the frozen single-stock, long-only Strategy V2 scope and should not be implied by aggregating five stocks.

## Dependencies / readiness

V2 UI should begin after backend has a stable read-only contract and artifact identity for each page. Current legacy website work can continue separately; its six strategy selector should not be renamed as Strategy V2. No code, endpoint, component, strategy, database or research output was changed during this audit.
