# Multi-Strategy Backtest API V1

This authenticated API runs the existing `multi_strategy_backtest_v1` Python CLI in its approved historical development mode. It does not replace the legacy `POST /api/backtests/run` endpoint.

## Scope

- Supported stock universe: `2303`, `2330`, `2379`, `2454`, `3034` (loaded from `hybrid/config/multi_strategy_development_v1.json`).
- Supported selectable strategies: `technical`, `rf`, `xgboost`, `gru`.
- `buy_and_hold` is generated automatically as a benchmark and must not be included in the `strategies` request array.
- Period: fixed to the configured 2024 development backtest period (`2024-07-01` through `2024-12-30`). Training and diagnostic validation remain fixed by the Python config.
- Data role: `historical_development`. No 2025/2026 data or Final OOS is authorized by this endpoint.
- Results and job status are held in server memory. A server restart clears the status lookup; completed Python exports remain under the project `exports/` directory.
- The API permits one active multi-strategy run per server process to avoid concurrent model fitting and resource contention.

## Start a run

`POST /api/backtests/multi/run` requires the existing JWT bearer authentication.

Request:

```json
{
  "stock_code": "2330",
  "strategies": ["technical", "rf", "xgboost", "gru"]
}
```

The route returns HTTP `202` immediately:

```json
{
  "run_id": "<server-generated-id>",
  "status": "pending",
  "status_url": "/api/backtests/multi/<server-generated-id>/status",
  "result_url": "/api/backtests/multi/<server-generated-id>/result",
  "stock_code": "2330",
  "strategies": ["technical", "rf", "xgboost", "gru"]
}
```

The Python process is started with `child_process.spawn` and an argument array (`shell: false`). Stock and strategy values are allowlisted. Dates, data role and initial capital cannot be supplied by the client in this development endpoint.

## Poll status

`GET /api/backtests/multi/:run_id/status` returns one of `pending`, `running`, `completed`, `failed`, or `blocked`. A Python preflight or policy rejection is `blocked`; runtime or output schema errors are `failed`.

## Read result

`GET /api/backtests/multi/:run_id/result` returns HTTP `202` while pending/running, HTTP `409` with a safe error object on failed/blocked work, and HTTP `200` when completed. Unknown IDs return HTTP `404`.

Completed JSON structure:

```json
{
  "api_version": "multi_strategy_backtest_v1",
  "run_id": "<server-generated-id>",
  "stock_code": "2330",
  "data_role": "historical_development",
  "backtest_period": {"start": "2024-07-01", "end": "2024-12-30"},
  "status": "completed",
  "strategies": [
    {
      "strategy_id": "technical",
      "strategy_version": "technical_v2_b+risk_v1",
      "summary": {
        "initial_capital": 100000,
        "final_equity": 0,
        "total_return_pct": 0,
        "net_pnl": 0,
        "win_rate_pct": null,
        "trade_count": 0,
        "max_drawdown_pct": 0
      },
      "details": {
        "profit_factor": null,
        "sharpe_ratio": null,
        "total_transaction_cost": 0,
        "gate_applicable": false,
        "risk_contract": {"version": "risk_v1"},
        "trades": [],
        "model_training": null
      },
      "equity_curve": [
        {"timestamp": "<bar time>", "equity": 100000, "cumulative_return_pct": 0, "cash": 100000, "position_value": 0}
      ]
    }
  ],
  "comparison": {"time_axis_identical": true},
  "buy_and_hold": {
    "strategy_id": "buy_and_hold",
    "summary": {},
    "details": {"risk_contract": null, "exposure_assumption": "..."},
    "equity_curve": []
  },
  "audit": {"experiment_id": "multi-strategy-development-v1", "final_oos_used": false}
}
```

The zeroes above illustrate field types only and are not research results. Actual responses carry the generated run's values. Each strategy result includes the Python core's summary, details, trades, risk/gate decisions, model training audit where applicable and its per-bar equity curve. Null metrics remain null when not applicable.

## Verified local development run

On 2026-10-22, the new asynchronous Express route was exercised over local HTTP with the real Python runner for 2330 and all four strategies. The result was HTTP `202` on submission, `completed` on status polling, and HTTP `200` on result retrieval. All five equity curves (four strategies plus Buy & Hold) had the same 5,076-point time axis. The harness used a local-only test authentication middleware; actual production JWT login was not exercised because no account credentials were supplied.

The real run is retained at `exports/multi_strategy_dev_v1_1792609222237710700/`. Selected returned values:

| Strategy | Return | Net PnL | Win rate | Trades | Max drawdown | Total costs |
|---|---:|---:|---:|---:|---:|---:|
| Technical V2-B + Risk V1 | -2.5253% | -2,525.30 | 27.27% | 11 | 4.5523% | 2,274.30 |
| Technical V2-B + RF Gate + Risk V1 | -0.7451% | -745.05 | 33.33% | 3 | 2.6751% | 703.05 |
| Technical V2-B + XGBoost Gate + Risk V1 | -1.7835% | -1,783.53 | 0.00% | 1 | 1.9197% | 223.53 |
| Technical V2-B + GRU Gate + Risk V1 | 0.0000% | 0.00 | null | 0 | 0.0000% | 0.00 |
| Buy & Hold benchmark | 11.9089% | 11,908.92 unrealized | null | null | 24.5956% | 142.08 entry commission |

All values are 2024 historical development results, not current signals or final out-of-sample performance. The production JWT HTTP flow remains a manual acceptance item; route authentication and real Python subprocess integration were tested separately.

## Buy & Hold assumptions

The benchmark is generated from the same clean Technical V2-B 5-minute data and period. It buys the maximum affordable whole-share quantity at the first available in-period bar open, deducts entry commission, and marks shares to market at every close through the final bar. It does not sell at period end, so no exit commission or transaction tax is booked. It has no Risk V1 stop-loss or 40% position cap. Summary `trade_count` is null because there are no closed trades; `transaction_count` is one in benchmark details.

## Error format

Request validation and missing-run errors use:

```json
{"error":{"code":"INVALID_STRATEGIES","message":"..."}}
```

Important codes include `INVALID_STOCK_CODE`, `INVALID_STRATEGIES`, `BACKTEST_BLOCKED`, `DEVELOPMENT_DEFAULTS_ONLY`, `BACKTEST_QUEUE_BUSY`, `BACKTEST_RUN_NOT_FOUND`, `BACKTEST_EXECUTION_FAILED`, and `BACKTEST_TIMEOUT`.

## Local checks

From `umc-project/前端/server`:

```powershell
npm test
npm run check
```

For real local HTTP validation, start the existing Express server and submit a run with a valid existing JWT. Poll `status_url` until `completed`, then GET `result_url`. No account or token should be written into project files.

Windows PowerShell acceptance script (it prompts for the existing email and hidden password; the JWT remains in process memory):

```powershell
cd "C:\Users\yunso\OneDrive\桌面\專題0928\umc-project\前端\server"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tests\accept-multi-backtest-api.ps1
```
