# Frontend Data Contract Proposal

本文件只提出 API 合約，沒有建立 API。真實存在的 Express routes 在「既有實作」欄說明；帶 `/analysis/current` 等名稱的是建議介面，尚未實作。Response 建議使用繁體中文前端文案、機器可讀 enum、ISO-8601 時間、明確單位及資料角色。

## 共通規則

每個研究 response 建議包含 `asOf`, `dataRole`, `period`, `source`, `runId`, `candidateId`, `freezeId`, `status`；所有值要能追到既有 snapshot/report，不由 API 臨時重算策略。錯誤統一為 `{error:{code,message,retryable,details}}`。日期/時間皆含 timezone 或明確標示 Asia/Taipei。百分比統一為 0–100 數字，金額為 TWD 元，股數為股。任何 current response 不可取代已保存 backtest identity。

## `GET /analysis/current?stock_code=2303`

**Purpose:** 提供目前行情時點下，凍結 V2 candidate 的 Technical→RF→Hybrid→Risk 判斷。現況 `NOT_IMPLEMENTED`。現有 `/api/market/hot`、`/api/market/search` 只提供 Yahoo 報價/history；不得將它們說成策略 current analysis。

**Input:** 股票代號；optional `as_of` 僅供查詢支援的歷史快照，不應靜默回退最新。

**Output example:**

```json
{
  "status": "NOT_READY",
  "analysis_type": "current_analysis",
  "stock": {"code": "2303", "name": "聯電"},
  "as_of": null,
  "data_role": null,
  "source": {"market": "Yahoo Finance", "strategy_data": null},
  "strategy": {"freeze_id": null, "candidate_id": null},
  "technical": {"candidate": null, "market_regime": null, "signal_time": null},
  "ml": {"model": null, "prediction": null, "prediction_date": null, "feature_date": null, "probabilities": null},
  "hybrid": {"decision": null, "reason": null},
  "risk": {"capital": null, "risk_per_trade_pct": null, "stop_loss_pct": null, "max_position_fraction_pct": null, "suggested_shares": null},
  "message": "凍結 Strategy V2 即時分析尚未提供"
}
```

**Source of truth:** Current live data service plus a future frozen-strategy inference pipeline. It must not use OOF rows as today’s prediction. The current contract is only a proposal.

## `GET /backtest/summary?run_id=...`

**Purpose:** Normalize historical run summary. No dedicated route by this name. Existing equivalent is authenticated `GET /api/reports/latest/:code`, `GET /api/reports/analysis/:analysisId`, and `GET /api/reports` which returns legacy reports. V2 freeze summary currently exists in exports, without Expo API.

**Input:** Prefer immutable `run_id`/`analysis_id`; optional filters should never choose a different candidate silently.

**Output example:**

```json
{
  "analysis_type": "development_backtest",
  "run_id": "strategy-v2-freeze-id",
  "candidate_id": "BR_SENS_RANGE55_BB20_2+TECH_V2_B_MARKET_REGIME_FILTER",
  "model": "random_forest",
  "stock_scope": ["2303", "2330", "2379", "2454", "3034"],
  "data_role": "EXPOSED_DEVELOPMENT",
  "period": {"start": "2023-01-01", "end": "2024-12-31"},
  "metrics": {
    "average_return_pct": null, "median_return_pct": null,
    "worst_max_drawdown_pct": null, "win_rate_pct": null,
    "profit_factor": null, "sharpe": null, "trade_count": null,
    "gross_pnl_twd": null, "net_pnl_twd": null,
    "transaction_cost_twd": null, "final_equity_twd": null
  },
  "provenance": {"freeze_id": "...", "source_artifact": "...", "generated_at": "..."}
}
```

**Source of truth:** Legacy: persisted report JSON/MySQL; V2: frozen summary/per-stock/per-fold exports. Do not average fields with incompatible aggregation definitions; return metric definitions/version.

## `GET /backtest/trades?run_id=...&stock_code=2303`

**Purpose:** Retrieve immutable transaction/trade log. No dedicated route. Existing legacy reports can contain transaction/PnL rows and HTML; V2 candidate exports have trades and decision audit CSVs, not served by Expo.

**Input:** `run_id`; optional stock/fold filters.

**Output example:**

```json
{
  "run_id": "...",
  "data_role": "EXPOSED_DEVELOPMENT",
  "items": [{
    "stock_code": "2303", "fold_id": "WF1",
    "signal_time": "2023-07-10T13:25:00+08:00",
    "ml_prediction_date": "2023-07-07", "ml_prediction": "BUY",
    "entry_time": "2023-07-11T09:00:00+08:00", "entry_price": 0,
    "exit_time": null, "exit_price": null, "exit_reason": null,
    "position_shares": 0, "gross_pnl_twd": 0,
    "cost_twd": 0, "net_pnl_twd": 0,
    "hybrid_decision": "ALLOW_LONG_ENTRY", "risk": {}
  }],
  "page": {"limit": 100, "next_cursor": null}
}
```

**Source of truth:** Run-specific trade CSV plus gate/risk decisions, or the saved legacy report. Avoid inventing absent values; null means unavailable, and `SELL` is classification/veto, never a short order.

## `GET /market/candles?stock_code=2303&interval=5m&start=...&end=...`

**Purpose:** Get explicitly sourced OHLCV candles. No dedicated route. Current `/api/market/hot` and `/api/market/search` stock `history` includes Yahoo one-day, 5-minute OHLCV where available. The repository raw research SQLite is not exposed through Express.

**Output example:**

```json
{
  "stock_code": "2303", "interval": "5m", "source": "Yahoo Finance",
  "data_role": "LIVE_MARKET_DISPLAY", "as_of": "...",
  "timezone": "Asia/Taipei", "candles": [
    {"timestamp": "2026-10-19T09:00:00+08:00", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
  ],
  "warning": "Yahoo 行情可能延遲；本資料不代表研究回測資料"
}
```

**Source of truth:** Yahoo for current displayed history; versioned research market database for frozen historical research, if a future authorized read API is designed. Do not merge these silently.

## `GET /news?stock_code=2303&mode=latest|historical&run_id=...`

**Purpose:** Normalize existing report-scoped news endpoints. No top-level `/news` route. Existing authenticated endpoints are `GET /api/reports/analysis/:analysisId/news/:mode` and `POST .../summary`.

**Input:** Current code requires a saved `analysisId` to establish date context. `mode=historical` uses the report period; latest uses recent ~30 days. Existing holdout date guard applies.

**Output example:**

```json
{
  "stock_code": "2303", "mode": "historical", "period": {"start": "...", "end": "..."},
  "source": "Google News RSS", "fetched_at": "...",
  "items": [{"title": "...", "published_at": "...", "source": "...", "url": "https://news.google.com/..."}],
  "summary": null,
  "coverage_note": "僅有標題索引，非完整新聞檔案；請開啟來源核實。"
}
```

**Source of truth:** Google News RSS response and MySQL `stock_news_cache`. Industry news and sentiment are not currently implemented.

## `GET /llm/explanation?run_id=...`

**Purpose:** Retrieve an explanation constrained to immutable, structured V2 analysis. No dedicated route exists. Legacy report LLM is generated during Python backtest and stored in report; news headline summary is a separate Gemini feature.

**Input:** immutable `run_id`, selected section IDs, locale. Future implementation should pass only structured metrics/decisions and redact secrets.

**Output example:**

```json
{
  "run_id": "...", "provider": "Gemini", "model": null,
  "source_type": "structured_backtest_result", "generated_at": "...",
  "text": "...", "citations": [],
  "limitations": ["僅解釋既有結果，不產生或修改買賣訊號。"]
}
```

**Source of truth:** Saved backtest summary and audited signals/trades. Actual provider/model are runtime settings; repository code alone cannot establish current secret environment values. LLM output must be labelled explanation, not a signal.

## Existing route to proposal mapping

| Proposed resource | Current nearest implementation |
|---|---|
| Current analysis | None; `/api/market/hot` is quote only |
| Backtest summary | `/api/reports`, `/api/reports/latest/:code`, `/api/reports/analysis/:analysisId` for legacy saved reports |
| Backtest trades | Embedded report/HTML or report transaction fields; no normalized trades route |
| Market candles | `history` property in `/api/market/hot` / `/api/market/search`, one-day Yahoo 5m |
| News | Report-scoped `.../news/:mode` routes |
| LLM explanation | Embedded legacy backtest report; no standalone V2 explanation route |
