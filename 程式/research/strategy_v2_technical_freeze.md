# Technical Strategy V2 Freeze Candidate

**狀態：** `FROZEN_CANDIDATE`（資格確認通過；研究資料為已曝光的 2023–2024 Training/Development，不代表 Final OOS 驗證）

## 凍結定義

- `TECHNICAL_V2_BASE = BR_SENS_RANGE55_BB20_2`
- `TECHNICAL_V2_ADDED_COMPONENT = MARKET_REGIME_FILTER_B`
- `TECH_V2_A_INCLUDED = FALSE`
- `TECH_V2_C_INCLUDED = FALSE`
- `PARAMETER_SEARCH = FALSE`
- `FINAL_OOS_USED = FALSE`
- 設定檔：`configs/technical_v2_candidate.json`
- 資格報告：`exports\strategy_v2_technical_candidate_20261019T103707108020\technical_v2_candidate_audit.json`

唯一新增規則為 preregistered B：只用目標股票以外四檔股票的等權重 peer close proxy，取最新 prior completed common session 與其 20-session SMA 比較；proxy 高於 SMA20 才保留 Technical breakout candidate。缺資料即排除，不補值。執行沿用 completed K-bar signal → next tradable 5-minute bar open。

## 選擇理由

B 是 A/B/C 中唯一既有分類為 `SUPPORTED` 的結構變更，因此以 `SUPPORTED_STRUCTURAL_COMPONENT_SELECTION` 入選，不是依最高報酬挑選。A 成交量確認只有 `MIXED` 證據，故不納入。C EMA20 出場雖降低回撤，但令 RF/XGBoost 的報酬與 Profit Factor 受損，整體分類為 `MIXED`，故不納入。

## 資格確認

- Control reproduction：`PASS`
- B reproduction：`PASS`，原 batch 分類 `SUPPORTED`，重現分類 `SUPPORTED`
- Hybrid Gate parity：`PASS`
- 未來資料檢查：`PASS`（確認只用嚴格早於候選日的已完成 session）
- 結果矩陣：90 rows；Common Coverage candidates：192
- Training-only：是；ML 重訓、調參、Risk 改動、2025/2026/Final OOS 使用：否

| 模型 | 平均報酬 | Worst MDD | 勝率 | PF | Sharpe | 交易數 | Net PnL | 成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| random_forest | 0.72% | 1.83% | 30.56% | 2.089 | 0.366 | 29 | 10,811 | 6,474 |
| xgboost | 0.48% | 2.22% | 29.44% | 1.911 | -0.093 | 25 | 7,262 | 5,451 |
| gru | -0.14% | 1.98% | 18.33% | 0.777 | -0.469 | 24 | -2,032 | 5,285 |

Technical 參數研究至此暫停。下一階段只進入 ML / Hybrid V2 規劃與預先定義的研究，不在本輪執行 ML V2 Performance。
