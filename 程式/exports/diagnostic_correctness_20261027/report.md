# 多策略回測正確性診斷

**研究角色：歷史開發期**｜訓練 2023｜診斷驗證 2024 H1｜回測 2024/07/01–12/30｜Final OOS 未使用。

0 bps 為基準成本，5 bps 為每邊滑價壓力測試。基準前後數字可能受 XGBoost 多執行緒重訓差異影響；本次所有新策略與情境共用同一份模型預測 artifact。

## 修改後結果：0 bps

| 標的 | 策略 | 淨報酬 | 淨損益 | 毛損益 | 完成交易 | 勝率 | PF | 最大回撤 | Sharpe | Sortino | 費稅滑價總成本 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2330 | technical | -2.27% | NT$ -2,269 | NT$ -42 | 11 | 27.27% | 0.5365622582686576 | 4.30% | — | -1.5107792205735608 | NT$ 2,227 |
| 2330 | rf | -0.86% | NT$ -865 | NT$ -162 | 3 | 33.33% | 0.6180591047843224 | 2.79% | — | -0.7194596244370416 | NT$ 703 |
| 2330 | xgboost | -1.90% | NT$ -1,900 | NT$ -1,677 | 1 | 0.00% | 0.0 | 2.04% | — | -1.6863652173651722 | NT$ 223 |
| 2330 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2330 | buy_and_hold | 11.91% | NT$ 11,909 | NT$ 12,051 | — | — | — | 24.60% | — | 1.3330898332369192 | NT$ 142 |
| 2330 | exposure_matched_buy_and_hold | 4.54% | NT$ 4,544 | NT$ 4,797 | 1 | 100.00% | — | 10.43% | — | 1.155989987610456 | NT$ 253 |
| 2303 | technical | -1.86% | NT$ -1,861 | NT$ -879 | 5 | 0.00% | 0.0 | 2.95% | — | -2.3959619097808016 | NT$ 982 |
| 2303 | rf | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | xgboost | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | buy_and_hold | -22.82% | NT$ -22,824 | NT$ -22,682 | — | — | — | 28.79% | — | -2.7072852834025034 | NT$ 142 |
| 2303 | exposure_matched_buy_and_hold | -9.26% | NT$ -9,261 | NT$ -9,068 | 1 | 0.00% | 0.0 | 11.67% | — | -2.7831165763529846 | NT$ 193 |
| 2454 | technical | -3.88% | NT$ -3,877 | NT$ -2,132 | 8 | 25.00% | 0.32927860140512666 | 6.47% | — | -1.927715433180072 | NT$ 1,745 |
| 2454 | rf | -0.53% | NT$ -529 | NT$ -300 | 1 | 0.00% | 0.0 | 1.36% | — | -1.0799596800864653 | NT$ 229 |
| 2454 | xgboost | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2454 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2454 | buy_and_hold | 0.92% | NT$ 923 | NT$ 1,065 | — | — | — | 32.54% | — | 0.4264895542843019 | NT$ 142 |
| 2454 | exposure_matched_buy_and_hold | 0.19% | NT$ 188 | NT$ 420 | 1 | 100.00% | — | 13.10% | — | 0.16605298190349732 | NT$ 232 |

## 修改後結果：5 bps

| 標的 | 策略 | 淨報酬 | 淨損益 | 毛損益 | 完成交易 | 勝率 | PF | 最大回撤 | Sharpe | Sortino | 費稅滑價總成本 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2330 | technical | -2.68% | NT$ -2,677 | NT$ -28 | 11 | 18.18% | 0.4862148917697676 | 4.48% | — | -1.7441271659393915 | NT$ 2,649 |
| 2330 | rf | -0.93% | NT$ -935 | NT$ -119 | 3 | 33.33% | 0.5923432199215168 | 2.84% | — | -0.7889264515913529 | NT$ 816 |
| 2330 | xgboost | -1.94% | NT$ -1,939 | NT$ -1,677 | 1 | 0.00% | 0.0 | 2.06% | — | -1.7025487385828149 | NT$ 262 |
| 2330 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2330 | buy_and_hold | 11.86% | NT$ 11,859 | NT$ 12,051 | — | — | — | 24.61% | — | 1.3284428884068789 | NT$ 192 |
| 2330 | exposure_matched_buy_and_hold | 4.50% | NT$ 4,502 | NT$ 4,797 | 1 | 100.00% | — | 10.43% | — | 1.1461547790139668 | NT$ 295 |
| 2303 | technical | -2.06% | NT$ -2,056 | NT$ -878 | 5 | 0.00% | 0.0 | 3.12% | — | -2.5188117764067948 | NT$ 1,178 |
| 2303 | rf | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | xgboost | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2303 | buy_and_hold | -22.86% | NT$ -22,862 | NT$ -22,669 | — | — | — | 28.79% | — | -2.7129839658317514 | NT$ 192 |
| 2303 | exposure_matched_buy_and_hold | -9.30% | NT$ -9,297 | NT$ -9,068 | 1 | 0.00% | 0.0 | 11.67% | — | -2.793019504636503 | NT$ 229 |
| 2454 | technical | -4.07% | NT$ -4,066 | NT$ -2,028 | 8 | 25.00% | 0.30990363497300105 | 6.58% | — | -2.0377996859798606 | NT$ 2,038 |
| 2454 | rf | -0.57% | NT$ -569 | NT$ -300 | 1 | 0.00% | 0.0 | 1.38% | — | -1.1263956647683875 | NT$ 269 |
| 2454 | xgboost | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2454 | gru | 0.00% | NT$ 0 | NT$ 0 | 0 | — | — | 0.00% | — | — | NT$ 0 |
| 2454 | buy_and_hold | 0.87% | NT$ 873 | NT$ 1,065 | — | — | — | 32.56% | — | 0.42254835351632347 | NT$ 192 |
| 2454 | exposure_matched_buy_and_hold | 0.15% | NT$ 149 | NT$ 420 | 1 | 100.00% | — | 13.10% | — | 0.1569930101917661 | NT$ 271 |

## ML Gate 訓練與機率診斷

| 模型 | 驗證準確率 | Macro-F1 | ROC-AUC | 開發候選 BUY/HOLD/SELL | BUY 機率平均 | 門檻 |
|---|---:|---:|---:|---|---:|---|
| RF | 0.3736654804270463 | 0.3566286570382832 | 0.5211891978266382 | 3/5/10 | 0.3036096352005276 | None |
| XGBoost | 0.3398576512455516 | 0.32123624003116125 | 0.5082595236802526 | 1/8/9 | 0.21275117869178453 | None |
| GRU | 0.3309608540925267 | 0.30788904748787554 | 0.48361606925207007 | 0/3/15 | 0.2915832930141025 | None |

## 診斷重點

- 2330 technical: 技術候選 32；BUY/HOLD/SELL 0/0/0；成交 22；完成 11；MFE 均值 0.015549991369746087; MAE 均值 -0.011842390970770666。阻擋原因：`{"BLOCK_ALREADY_IN_POSITION": 7}`
- 2330 rf: 技術候選 32；BUY/HOLD/SELL 3/5/10；成交 6；完成 3；MFE 均值 0.020381859674122243; MAE 均值 -0.016239763782851413。阻擋原因：`{"BLOCK_HOLD": 5, "BLOCK_SELL": 9, "BLOCK_ALREADY_IN_POSITION": 1}`
- 2330 xgboost: 技術候選 32；BUY/HOLD/SELL 1/8/9；成交 2；完成 1；MFE 均值 0.004950495049504955; MAE 均值 -0.04257425742574261。阻擋原因：`{"BLOCK_HOLD": 8, "BLOCK_SELL": 9}`
- 2330 gru: 技術候選 32；BUY/HOLD/SELL 0/3/15；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 15, "BLOCK_HOLD": 3}`
- 2303 technical: 技術候選 15；BUY/HOLD/SELL 0/0/0；成交 10；完成 5；MFE 均值 0.012118584538869114; MAE 均值 -0.006910181479850097。阻擋原因：`{"BLOCK_ALREADY_IN_POSITION": 3}`
- 2303 rf: 技術候選 15；BUY/HOLD/SELL 0/2/6；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 6, "BLOCK_HOLD": 2}`
- 2303 xgboost: 技術候選 15；BUY/HOLD/SELL 0/2/6；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 6, "BLOCK_HOLD": 2}`
- 2303 gru: 技術候選 15；BUY/HOLD/SELL 0/2/6；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 6, "BLOCK_HOLD": 2}`
- 2454 technical: 技術候選 21；BUY/HOLD/SELL 0/0/0；成交 16；完成 8；MFE 均值 0.02728069950575468; MAE 均值 -0.01834873013531689。阻擋原因：`{"BLOCK_ALREADY_IN_POSITION": 4}`
- 2454 rf: 技術候選 21；BUY/HOLD/SELL 1/3/8；成交 2；完成 1；MFE 均值 0.02281368821292773; MAE 均值 -0.01520912547528519。阻擋原因：`{"BLOCK_SELL": 8, "BLOCK_HOLD": 3}`
- 2454 xgboost: 技術候選 21；BUY/HOLD/SELL 0/4/8；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 8, "BLOCK_HOLD": 4}`
- 2454 gru: 技術候選 21；BUY/HOLD/SELL 0/0/12；成交 0；完成 0；MFE 均值 None; MAE 均值 None。阻擋原因：`{"BLOCK_SELL": 12}`

## 解讀限制

- This is historically exposed development data, not independent final OOS.
- 0 bps is the baseline cost scenario; 5 bps is a stress scenario, not an optimized setting.
- Stops and MFE/MAE use completed five-minute OHLC; intrabar path ordering is unknown.
- At development-period end the engine force-closes any open position at the final close.
- The 0 bps before/after comparison also includes use of one shared prepared model-prediction artifact in the revised runs; old runs trained models separately.
- Small trade counts are descriptive only and do not establish model superiority.

各原始回測資料夾保留 `run.json`、`diagnostics.json`、CSV 成交/候選/訊號/風險明細；本報告為彙總索引。
