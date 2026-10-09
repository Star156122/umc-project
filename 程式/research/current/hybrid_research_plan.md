# Technical × ML Hybrid 正式研究大綱

- 生效日期：2026-10-09
- 本輪終點：Hybrid Strategy V1 與 Hybrid Backtest
- 暫不納入：LLM 報告分析、前端擴充、額外研究支線

## 研究問題

利用 Technical Indicators 產生盤中候選進場訊號，再由 Machine Learning 判斷候選機會是否值得放行，最後比較 Technical Alone、ML Alone 與 Technical + ML Hybrid 的交易品質、報酬與風險。

## 1. Technical Strategy Development

Technical 的正式角色是產生 `Technical BUY Candidate`，回答「目前是否出現合理的做多機會」。第一輪只研究三個有明確交易理由的 Strategy Family：

1. **Trend-Momentum**：MA 判斷趨勢、MACD 確認動能、RSI 避免過熱。
2. **Breakout**：Range Breakout 觸發、MA 確認方向、Bollinger Bands 判斷波動／價格位置。
3. **Majority Vote Baseline**：MA、RSI、MACD 至少兩者支持 BUY，作為簡單基準。

禁止把所有指標組合全部掃過後選最高報酬。評估必須同時看報酬、勝率、Profit Factor、最大回撤、Sharpe、交易數、單筆平均、Walk-forward mean/std/worst fold、跨股票穩定性、參數敏感度與訊號後 forward return。

### Technical V1 Freeze 條件

- 規則具有可解釋的交易理由。
- 能產生足夠候選訊號，不靠刪除大部分交易美化績效。
- Walk-forward 與跨股票結果沒有只依賴單一 Fold 或單一股票。
- 鄰近參數結果大致一致，沒有孤立尖峰。
- Entry、Exit、參數、決策時間、停損／停利、最大持有期、成本與資金規則全部固定。

## 2. ML Model Training

ML 的正式角色是方向／狀態 Filter，回答「Technical 找到的做多機會是否值得放行」，不負責尋找盤中最佳買點。固定比較 Random Forest、XGBoost 與 GRU。

主要評估 Macro F1、Macro AUC、BUY Precision、BUY Recall、各類別 AUC、Confusion Matrix、Fold mean/std/worst fold、跨股票與跨時間穩定性。因 ML 將作為進場 Filter，BUY Precision 是關鍵指標之一。

### ML V1 Freeze 條件

- Training 內的時間順序 Walk-forward 已完成，沒有 Random Shuffle 或未來資料洩漏。
- Scaler、preprocessing、class weight 與 GRU early stopping 都只使用各 Fold 可用資料。
- 跨股票結果可解釋，並記錄類別偏移與失效股票。
- Target、預測期間、Feature、模型、超參數、Training period、Scaler、Label threshold 與 Decision Time 全部固定。
- 不因 ML Alone 的單次交易報酬反覆修改 Label、Feature、參數或 Probability Threshold。

## 3. Technical 與 ML 對齊

進入 Hybrid 前必須固定並核對：

- Stock Universe
- 資料角色與時間軸
- Technical Candidate Time
- ML Decision Time 與可用資料截止點
- Prediction Horizon
- Holding / Exit Logic
- Transaction Cost 與 Capital Rule
- Look-ahead Bias 防線

若 ML 使用 Day T 收盤以前的資料產生預測，Technical 只能從規則允許的後續時間尋找 Entry，禁止把未來資訊帶回較早的 5 分 K。

## 4. Hybrid Strategy V1

第一版固定採簡單 Gate：

| Technical | ML | Hybrid 決策 |
|---|---|---|
| BUY Candidate | BUY | ENTRY |
| BUY Candidate | HOLD | 不交易 |
| BUY Candidate | SELL | 不交易 |
| 無 BUY Candidate | BUY／HOLD／SELL | 不主動進場 |

ML V1 只控制 Entry Filter。Exit 由 Technical Exit 與風險管理負責，例如趨勢失效、停損、策略既有停利與最大持有期。

## 5. Hybrid Backtest

在相同股票、期間、本金、成本與風控假設下比較：

1. Technical Alone
2. ML Alone
3. Technical + ML Hybrid

固定輸出 Total Return、Win Rate、Maximum Drawdown、Profit Factor、Sharpe Ratio、Trade Count 與 Final Equity。主要問題是「ML Filter 是否改善 Technical 的交易品質或風險」，不要求 Hybrid 一定獲利。

## 正式執行順序

```text
Technical Strategy Development
→ Technical Walk-forward / Robustness Test
→ Technical V1 Freeze

ML Training
→ Time Walk-forward
→ Cross-stock Generalization
→ ML V1 Freeze

Technical V1 + ML V1
→ 時間與持有邏輯對齊稽核
→ Hybrid Strategy V1
→ Hybrid Backtest
→ Technical Alone vs ML Alone vs Hybrid
```

Technical V1 與 ML V1 任一尚未 Freeze，或兩者時間軸未通過防洩漏檢查，就不得開始 Hybrid 正式比較。
