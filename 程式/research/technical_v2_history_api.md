# Technical V2-B 歷史成果 API

## Endpoint

`GET /api/research/v2/technical/:stockCode`

需要既有 Express JWT Bearer 登入。`stockCode` 僅接受 4～6 位數字，作為固定研究輸出中的篩選值；不會組成檔案路徑。沒有該股票的 V2-B 研究列時回傳 HTTP 404，不會回退至舊版回測。

## 固定研究來源

- Batch: `exports/strategy_v2_technical_batch_20261019T094810074002/`
- Fold 明細: `technical_v2_expB.csv`
- 逐股摘要: `technical_v2_per_stock.csv`
- Batch audit: `technical_v2_batch_audit.json`
- 凍結版本: `exports/strategy_v2_development_freeze_20261019T181513514684/strategy_v2_freeze_manifest.json`
- Batch experiment: `strategy-v2-technical-exp-b`
- Freeze ID: `strategy-v2-single-stock-development-freeze-20261019T181513514684`
- Candidate: `BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER`
- 期間與資料角色: 2023-01-01～2024-12-31，已暴露的歷史 Development
- 研究名稱: Technical V2-B + RF Gate + Risk V1（歷史開發研究）

服務只接受與 batch audit、凍結 manifest 一致的資料版本，並只回傳 frozen candidate 所用 Random Forest OOF gate 的 TECH_V2_B 列。若摘要列與三個 Fold 明細不一致，API 回傳資料錯誤，不提供結果。

## 欄位與計算

- `metrics.average_return_pct`、`median_return_pct`：三個獨立 Fold 報酬率的算術平均／中位數；不是連續投資的累積報酬。
- `metrics.cumulative_return_pct` 固定為 `null`，`cumulative_return_status` 說明原因：這些 Fold 是獨立模擬，沒有可由現有彙總值推導的連續投資組合累積報酬。
- `metrics.return_std_pct`：三個 Fold 報酬率樣本標準差。
- `metrics.worst_max_drawdown_pct`：各 Fold 最大回撤中的最大值。
- `metrics.win_rate_pct`：相容舊欄位，等同 `mean_fold_win_rate_pct`，是包含零交易 Fold 原始勝率值的算術平均。零交易 Fold 的原始 0% 不代表交易失敗。
- `metrics.mean_fold_win_rate_pct`：明確命名的 Fold 勝率算術平均，仍包含零交易 Fold 的原始 0% 值。
- `metrics.trade_weighted_win_rate_pct`：各 Fold 勝率乘交易數後推算整數獲利筆數，再除以總交易數。只有交易數是非負整數、勝率在 0～100% 且推算獲利筆數與整數差距不超過 0.01 筆時才提供；否則為 `null`，並由 `trade_weighted_win_rate_status` / `trade_weighted_win_rate_reason` 說明原因。
- Fold 的 `metric_applicability` 標示勝率與 Profit Factor 是否因零交易而不適用。原始 CSV 數值仍保留在回應中。
- `metrics.profit_factor`、`metrics.sharpe`：沿用逐股摘要中的既有 Fold 指標算術平均，不是合併所有交易後重算的整體值。`profit_factor_applicability` 說明零交易 Fold 的 0 值雖保留，但該 Fold 的 Profit Factor 不適用。
- Sharpe 由回測引擎以每日 mark-to-market 報酬計算，使用年化因子 `sqrt(252)`；每日報酬少於兩筆或標準差為零時引擎回傳 0。零交易 Fold 的 flat equity 因此會得到 0，但這不代表有可解讀的風險調整績效；Fold 的 `metric_applicability.sharpe` 會標示此情形。
- `net_pnl`、`gross_pnl`、`transaction_cost`、`trade_count`：跨獨立 Fold 加總。
- 每一 Fold 回傳其原始回測區間和原始指標。Fold 報酬不複利、不串成連續資產曲線。
- 百分比欄位由原始比例乘以 100；金額以原輸出數值回傳。

## 研究脈絡與限制

這批結果應稱為「Technical V2-B + RF Gate + Risk V1（歷史開發研究）」，沿用 ML V1 Random Forest OOF Gate 和 Risk V1 固定 sizing；不是純 Technical-only 結果。市場狀態條件以排除標的後的核心股票等權重代理計算，不是外部市場指數。資料屬已暴露的 2023–2024 歷史開發資料，不代表即時訊號或未來表現。

成功回應包含 `stock_code`、`strategy_id`、`strategy_version`、`data_period`、`data_role`、`folds`、`metrics`、`evaluation_context`、`source` 和 `limitations`。錯誤使用 `{ error: { code, message } }`。
