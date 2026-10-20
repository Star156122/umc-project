# Strategy V2 ML / Hybrid Experiment 001 結論

本文件只記錄凍結 Technical V2 上的 ML Gate 增量價值評估；不選 Champion、不建立 Ensemble、不修改或訓練 ML。研究區間為已曝光的 2023–2024 Training/Development。

共同候選數：138。四版本使用相同候選、Technical V2-B、Risk V1、成本、執行規則與 WF1–WF3。

## 模型結果

### random_forest

- Incremental Value：`INCREMENTAL_VALUE_SUPPORTED`。
- Technical V2 Alone 平均報酬 0.42%；加 Gate 後 0.72%。
- Worst MDD 5.49% → 1.83%；交易數 138 → 29。
- 平均報酬改善股票 4/5（2303,2330,2379,3034）；改善 Fold 2/3（WF1,WF3）。
- PF 改善股票 4/5；改善 Fold 3/3。

### xgboost

- Incremental Value：`INCREMENTAL_VALUE_MIXED`。
- Technical V2 Alone 平均報酬 0.42%；加 Gate 後 0.48%。
- Worst MDD 5.49% → 2.22%；交易數 138 → 25。
- 平均報酬改善股票 3/5（2303,2330,3034）；改善 Fold 2/3（WF1,WF3）。
- PF 改善股票 2/5；改善 Fold 2/3。

### gru

- Incremental Value：`INCREMENTAL_VALUE_MIXED`。
- Technical V2 Alone 平均報酬 0.42%；加 Gate 後 -0.14%。
- Worst MDD 5.49% → 1.98%；交易數 138 → 24。
- 平均報酬改善股票 1/5（2303）；改善 Fold 1/3（WF3）。
- PF 改善股票 1/5；改善 Fold 0/3。

## Cross-stock / Cross-fold Evidence

詳見 `ml_v2_comparison.csv` 及 `ml_v2_per_stock.csv`、`ml_v2_per_fold.csv`。分類規則要求整體報酬、PF、最差 MDD，且多數股票與 Fold 的報酬及 PF 同時提供支持；交易數下降本身不構成支持證據。

## ML Gate 弱點

Gate 僅使用前一交易日 OOF BUY 放行；其 HOLD/SELL 都會阻擋 Technical 候選。本輪可呈現覆蓋率和跨股票／Fold 差異，但不能由本輪結果事後調整門檻或挑 Champion。

## 下一輪可能方向（僅建議，未執行）

1. 依本輪穩定性證據，預先定義 ML Gate 的候選規則和最低 coverage。
2. 若未來要測試其他分類或 Ensemble，先登記假設、比較方法與選擇規則。
3. 維持 Technical V2 凍結；另行批准前不做 ML 重訓、門檻搜尋或 Risk V2。

## 限制

本報告使用已曝光的 2023–2024 Training/Development，不是 Final OOS，且每個 Stock × Fold 使用獨立初始本金，不是 Portfolio Backtest。歷史結果不保證未來表現。
