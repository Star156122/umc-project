# ML / Hybrid V2 下一階段研究計畫（規劃，未執行）

Technical V2 候選已凍結為 `BR_SENS_RANGE55_BB20_2 + preregistered B Market Regime Filter`。本文件只規劃問題；不代表已開始 ML / Hybrid V2 實驗。

1. **Gate 穩定性：** 在 Technical V2 固定候選下，按模型與股票／Fold 描述 RF、XGBoost、GRU 的既有 OOF Gate 行為；先定義最低 coverage 與穩定性判準。
2. **BUY-only Gate：** 研究是否保留「只有前一交易日 OOF 為 BUY 才放行」，或使用其他已校準訊號；任何新規則須事前登記，不得看結果後調 threshold。
3. **ML 增量價值：** 在相同候選日期與 common coverage 上配對比較 Technical-only 與 Technical+ML，隔離 ML Gate 的增量效果。
4. **Ensemble：** 先定義是否測試、組合方式、缺值處理與選擇規則，避免事後挑選單一模型或 Ensemble。
5. **範圍控制：** 不重做大規模 Hyperparameter Tuning；沿用凍結的 ML 模型與 OOF，除非另行批准且事前登記新的訓練研究。

**本輪狀態：** 僅完成 Technical V2 Candidate Qualification / Freeze；ML V2 Performance、Hybrid V2 Performance 均未執行。
