# Strategy V2 Single-Stock Research Plan

盤點日期：2026-10-19  
本文件用途：整理 Strategy V2 的單股研究範圍、已存在的 V1 POC baseline、待決問題與建議研究流程。  
本輪界線：只做來源與結構盤點及規劃；沒有執行訓練、回測、參數搜尋，也沒有讀取 2025/2026 績效或 Final OOS。

## 1. Current POC Baseline

### V1 的定位

V1 是 **POC BASELINE**：用於確認 Technical → ML → Hybrid → Risk → Backtest 的研究流程可串接，不是已選定或凍結的正式策略。Hybrid POC 設定狀態為 `POC_ONLY_NOT_FROZEN`；Risk V1 是固定參數風控基準；Current Technical V1 Working Version 尚有後續 preflight 設定，不能當成 10 月 13 日 POC 所用 Technical 版本的同義詞。

### 已確認的歷史來源鏈

| 階段 | 實際來源／識別 | 本次核對到的狀態 |
|---|---|---|
| Technical | `exports/technical_v1_20261013T024919303843/`；Candidate `BR_SENS_RANGE55_BB20_2`；Training 2023-01-01～2024-12-31 | 輸出包含 `technical_v1_audit.json`、`technical_v1_signals.csv`、結果及報告。Hybrid POC audit 記錄來源檔雜湊，並記錄 Technical alone 可重現。 |
| Technical Config | 歷史執行引用 `configs/technical_v1_research.json` | Hybrid POC audit 保存當時 SHA-256 `a637c56f…`；目前同路徑檔案為 `027caee3…`（不同）。Technical 匯出 audit 另保存 config fingerprint。此 hash 差異證明目前設定不是歷史 Config 原樣，不能拿它冒充歷史快照。 |
| ML | POC 輸出 `exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv` | 來源為 Training-only 4-fold Walk-forward OOF；使用 ML V1 Fixed ±1% label、Technology 產業模型及 RF/XGBoost/GRU。不得將此來源描述為 in-sample 預測。 |
| Hybrid | `hybrid/config/hybrid_poc_v1.json`（歷史 audit 留存舊路徑 `configs/hybrid_poc_v1.json`）；Candidate `BR_SENS_RANGE55_BB20_2` | 現行 Config SHA-256 與 POC audit 留存值一致。`BUY → ENTRY`；`HOLD → BLOCK_HOLD`；`SELL → BLOCK_SELL`；`NO_ML_MATCH → NO_ML_MATCH`。Long-only；SELL 不代表做空。 |
| Hybrid POC Export | `exports/hybrid_poc_v1_20261013T041540804059/` | 保留 alignment、decisions、results、summary、trades、Technical 輸入、ML OOF、audit 及 HTML。原始研究結果留在原位。 |
| Common Coverage | `exports/hybrid_common_coverage_20261017T233825838996/`；後續補充來源 `exports/hybrid_common_coverage_20261018T121416162413/` | Common Coverage 是相同 Technical Candidate schedule 下的模型公平比較，不是模型或策略最佳化。兩份輸出及其 audit 都保留。 |
| Risk V1 | `hybrid/config/risk_v1.json` 與 `hybrid/core/risk_v1.py`；輸出 `exports/hybrid_risk_v1_20261018T122032419364/` | 固定 `risk_per_trade=1%`、`max_position_fraction=40%`，沿用 Technical 2.5% stop loss；只在 Hybrid gate 通過後縮放股數，沒有參數搜尋。 |

Common Coverage 的 registry row 以 `report_file` 和 `result_file` 指向對應報告與結果檔，且檔案存在；因此沒有把「沒有 artifact_path 欄」誤判成錯誤，這次也沒有改 registry。

### 歷史版本與目前工作目錄

歷史 POC 由 10 月 13 日的 Technical 訊號輸出、當時保存雜湊的 Config、Training OOF 及固定 Hybrid Gate 組成。Current Working Version 有較新的 Technical preflight、資料品質政策與程式/config 修改；10 月 17 日最新 Technical 輸出是 preflight audit，不能取代 10 月 13 日已產生的 POC Technical Candidate 輸出。兩個版本都要保留並分別命名。

工作目錄有既存未提交修改與新增輸出。本輪沒有清理、還原或覆寫它們。歷史 POC audit 保存了設定和來源檔雜湊；若未來需逐位元重現，仍須確認保存的歷史 Config snapshot 是否可由版本庫或備份取回，不能只依現在相同路徑推定內容相同。

## 2. Baseline Problems Found

分類沿用 `DEFINITE_ERROR`、`BASELINE_DRIFT`、`EXPECTED_DIFFERENCE`、`DATA_EXPOSURE`。沒有找到可 100% 證明應立即修正的錯誤，因此不直接改程式、Config、Registry 或結果。

| 類別 | 發現 | 處理 |
|---|---|---|
| `DEFINITE_ERROR` | 本輪抽查的來源路徑與 Registry report/result 參照可對應到存在檔案；沒有確認出錯誤狀態或錯誤參照。 | 沒有修復項目。 |
| `BASELINE_DRIFT` | 歷史 POC 的 Technical 訊號、Candidate、Config hash 與後續工作目錄的 Technical preflight/config 修改屬不同版本。 | 保留歷史 POC 與 Current Working Version，V2 開始前選定並封存一個 baseline。 |
| `BASELINE_DRIFT / NEEDS_DECISION` | Technical 與 ML 的設定指向不同 SQLite 資料庫路徑；目前不能只靠路徑名稱證明兩者資料內容與可用樣本完全相同。 | 未來正式單股實驗前，明確指定資料來源版本、hash、eligible samples 與共同資料對齊規則。 |
| `EXPECTED_DIFFERENCE` | ML V1 有科技與金融分產業訓練；此 Hybrid POC 的 Technology Technical Universe 是 2303、2330、2379、2454、3034，ML gate 也只使用對應 Technology 模型。 | 符合當時設計範圍；不把 Finance 股票硬加進本 POC，也不據此宣稱科技一定較適合。 |
| `NEEDS_DECISION` | 現行回測引擎的模擬成交時點以訊號當下的 tick/close 作為參考，訊號完成與成交可執行性的假設需要在 V2 前決定。 | 先固定一個可說明的執行假設，再以單獨實驗研究；不得暗改 V1。 |
| `DATA_EXPOSURE` | 2025 H1、2026 H1 曾被看過；2025 H2 的未使用狀態不能由目前資料證明；專案既有紀錄指出 2026-07 起有部分 Technical exposure。 | 不把已曝光或未知資料重新標成 clean。Final OOS 不供本輪或 V2 開發使用。 |

## 3. Baseline Sync / Repair Decisions

- 將歷史 V1 POC 定義為 immutable research reference：Technical 10/13 export + Candidate `BR_SENS_RANGE55_BB20_2` + Training OOF + Hybrid POC Gate + Common Coverage + Risk V1 固定 sizing。
- 將目前工作目錄定義為另一個 Current Working Version；不以 current Technical preflight 覆寫歷史 POC 的來源身分。
- 確認 Common Coverage registry 的 `report_file`、`result_file` 參照已填且存在；未發現需修正的 registry 錯誤。
- 本輪只新增本文件；沒有修改任何研究邏輯、Registry、Config、signal、ML、Hybrid、Risk、backtest 或輸出結果。
- 「Baseline sync」在本文件中代表來源與版本身分已對照並記錄，不代表程式已同步成單一版本或歷史 Config snapshot 已重建。

## 4. Single-Stock Architecture

Strategy V2 每次執行只研究一檔股票，完整資料流如下：

`Market Data → Technical Signal → ML Prediction → Hybrid Decision → Long/Short Decision → Risk Management → Capital/Quantity → Entry → Exit → Backtest → Evaluation`

2303、2330、2379、2454、3034 可分別進行單股實驗；每檔單獨開一個研究帳戶與結果列，不共享資金、不模擬多檔同時持倉。跨股票彙總只能用來檢查策略穩定性，不能把它說成組合績效。

## 5. Capital Model and Position Sizing

### 研究本金與產品輸入分開

- **Research Initial Capital**：沿用固定 100,000 元作可比較的研究基準。股票、模型、策略與 Fold 之間不能用不同研究本金挑勝者。
- **Future User Input Capital**：50,000、100,000、500,000、1,000,000 等金額是執行/產品輸入，只影響可下單股數、現金不足與實際帳戶結果，不得改變 Technical 訊號或 ML 預測，也不得用來選策略或調參。
- 預定計算順序：`Strategy Decision → Risk Allowed Fraction → User Capital → Quantity`。目前只是架構設計，不實作前端或公式。

### 現有單股數量限制的關係

- 原始 Technical 策略提供 `Original Quantity` 上限；現行共用回測引擎亦有每次最多 1,000 股的限制。
- Risk V1 的 Risk Per Trade = 1%；停損距離沿用 2.5%；曝險上限為目前權益的 40%。概念上先計算風險預算股數 `floor(equity × 1% / (entry price × 2.5%))`，再計算 40% 曝險可買股數，最後與 Original Quantity 取最小值。
- 最終可下單股數還要受整股整數限制、現金與交易成本限制；引擎支援零股模擬，但資料/下單實務適用性需另外決定。買不起時可能縮量或不成交，不能用補值方式改變策略訊號。
- 多股票獨立測試時，各股票×Fold 的本金各自重啟；不可將各自 100,000 元的曲線拼成共享帳戶。

V2 只先確認目前引擎可將使用者本金傳入 sizing/cash 計算，不應因本金大小改變訊號。若做金額情境，這屬執行容量分析，須固定策略與 Risk 參數並另行登錄。

## 6. Technical Strategy V2 Research Scope

下列均為研究假設，尚未授權修改或執行。每輪只挑一個主要 Component，其他 Technical、ML、Hybrid、Risk 與執行假設固定。

| Hypothesis | What Changes | What Stays Frozen | Required Data | Evaluation Metrics | Overfitting Risk |
|---|---|---|---|---|---|
| 提高 Entry Quality 的確認條件可能減少假進場。 | 一個事前定義的進場確認條件。 | Candidate Universe、其餘 Technical、ML、Hybrid、Risk、成本。 | 同一單股 5 分 K、Technical Candidate 與逐筆交易。 | Net PnL、交易數、Win Rate、PF、MDD、成本、各 Fold 訊號/交易穩定度。 | 高；容易用已見交易挑到只適合特定股票的條件。 |
| Trend/Range 市場辨識可能讓趨勢訊號避開盤整。 | 一個固定、可解釋的市場狀態判斷。 | 進出場、模型、Gate、Risk 與樣本範圍。 | 單股 K 棒及只用當時可得資訊計算的狀態欄位。 | 分狀態交易數、PF、MDD、淨損益及各 Fold 穩定度。 | 高；狀態定義容易事後貼合行情。 |
| Breakout Quality 檢查可能排除低品質突破。 | 一個有交易理由的突破品質欄位/確認。 | 候選參數以外的訊號規則、ML、Risk、成本。 | OHLCV、突破訊號時間、可用量能及交易紀錄。 | 假突破比例、交易數、成本、PF、MDD、最差 Fold。 | 高；多個突破門檻會形成隱性搜尋。 |
| Exit Logic 的單一結構修改可能改善獲利保留或下檔風險。 | 一項 exit 機制，例如固定時間上限或結構性退出（二擇一另立實驗）。 | Entry、Candidate、ML、Hybrid、Risk position sizing。 | 有順序的 K 棒、持倉狀態、成本與出場原因。 | 平均/中位報酬、最差 MDD、持有時間、PF、成本及各股/Fold一致性。 | 很高；出場條件特別容易針對歷史價格調整。 |
| Holding Time 限制可能控制長時間無效持倉。 | 一個預先說明的持有時間政策。 | Entry 與其他 Exit、模型、Gate、Risk。 | 每筆進出場時間、持倉期間價格與公司行動資料。 | 持有天數分布、Net PnL、MDD、交易數、成本與穩定性。 | 高；不可搜尋最佳天數。 |
| 訊號頻率與穩定度檢查能辨識少量交易造成的表面改善。 | 不修改訊號，只預先登記頻率/穩定度診斷。 | 全部策略規則與參數。 | 各 Fold、月份及單股的訊號和交易計數。 | 每 Fold/月份交易數、訊號重疊、方向一致度、樣本不足標記。 | 中；不可只挑活躍區段作結論。 |

不使用 Grid Search、Random Search、Optuna 或「跑多個門檻再挑最高報酬」流程。

## 7. ML / Model Research Scope

保留 Random Forest、XGBoost、GRU 三種研究對象；不因既有某模型交易 Return 較高就選 Champion，也不在本輪重新選模型。

| Research Question | Prevent Cherry Picking | Validation Requirement |
|---|---|---|
| A. Same Model for All Stocks：一個共同模型是否能跨個股維持預測品質？ | 事前固定共用模型與選擇規則，不按個股報酬換模型。 | 相同時間 Fold、eligible sample 定義及特徵；報每股/每 Fold、平均、標準差、最差 Fold。 |
| B. Per-Stock Model Selection：不同股票是否有不同模型適配？ | 先固定每股模型選擇規則與最小樣本門檻；不得看過結果後為個別股票挑 Champion。 | 僅 Training 內部 Walk-forward；如要比較規則，需另外設共同的時間外評估，不把已見資料稱 clean。 |
| C. Model Voting：RF/XGB/GRU 的類別投票是否比單模型穩定？ | 固定投票方式與平手規則，不試多種投票組合後選最好的。 | 同日同股票的 OOF 預測交集，報分類品質、coverage、跨 Fold/股票穩定度。 |
| D. Model Ensemble：機率融合是否改善預測校準或分類？ | 事前固定權重或只允許一個有理由的融合方案；禁止用交易 Return 搜權重。 | 僅可用內部 Training Fold 建立融合，驗證 Fold 不 fit；需報 individual 與 ensemble 對照。 |
| E. ML only as Filter：ML 是否適合作為 Technical Long Entry 的確認 gate？ | 保留同一 Technical Candidate schedule；模型只以 prior-session OOF gate；不得依股票或交易結果修改門檻。 | Common Coverage、時間對齊、分類品質與交易層結果分開報告；缺預測理由須列出。 |

ML 選擇優先看 BUY precision/recall/coverage、Macro F1/AUC、最差 Fold、標準差、LOSO 與每股穩定性。交易報酬可作下游整體診斷，不得作 ML 模型選擇依據。

## 8. Hybrid Research Scope

- 現行 POC Gate 只允許 Technical `BUY_CANDIDATE` 經前一交易日、同股票同模型的 ML OOF 預測確認；BUY 放行，HOLD/SELL 阻擋新多單。
- V2 可提出單一 Hybrid 假設，例如固定 OOF 信心資訊是否值得作診斷，或衡量不同 gate 對 coverage 的影響；本輪不變更 Gate、不掃 probability threshold。
- 所有模型必須在相同 Technical Candidate 日期、相同 eligible 樣本與同一時間對齊規則下比較；同時列候選數、共同可用數及排除原因。
- Hybrid 評估不得把少交易造成的低回撤誤稱全面改善；需同時看 Return、交易數、PF、MDD、成本與每股/Fold 穩定性。
- 若要改 Hybrid Decision，Technical、ML model/label/features 與 Risk 全部固定，建立新 Experiment ID，保留 POC 輸出。

## 9. Long / Short Plan

### Long-only 第一階段

現行語意固定為 `BUY = Allow Long Entry`、`HOLD = No Entry`、`SELL = Block Long Entry`。先完成 Single-Stock Long-only Strategy V2 第一輪研究、跨 Fold/股票檢查與版本 Freeze，再判斷是否研究空單。

### 未來獨立 Short V1 的必要元件

Short 必須另立 `SHORT_V1`，至少包含：Technical Short Candidate、ML SELL Gate、Short Entry、Short Exit、Short Stop Loss、Short Holding Period、Short PnL、Transaction Cost、Position Accounting、Risk 及完整 tests。還需 preflight 確認借券可用性/費率、保證金、漲跌停、公司行動、回補成交與空單帳務資料。現有 SELL label 不等於 Short Entry。本輪不建立或實作 Short。

## 10. Risk V2 Scope

- Risk V1 保留為固定研究基準：Risk Per Trade 1%、Max Position 40%、沿用 2.5% stop loss。不得因 Return 或 MDD 結果調成其他比例，不進行 sweep。
- 未來 Risk V2 僅研究一種有清楚機制假設的新 Risk 元件，例如 volatility-based position sizing、consecutive-loss control 或 market-condition risk reduction；一次只選一種，Technical/ML/Hybrid 固定。
- Risk V2 指標至少要涵蓋 Return、Net PnL、MDD、PF、Sharpe、交易數、成本、風險阻擋原因與跨 Fold/股票穩定性。
- Portfolio Risk（共享曝險、組合回撤、相關性、組合風險預算）明確延至 Portfolio V1；目前禁止實作。

## 11. Validation Rules

1. 每個實驗開始前登記 Experiment ID、假設、唯一主要變更、股票、期間、資料角色、baseline、固定規則、預期效益、可能代價及判定標準。
2. 每輪只改一個主要 Component；其他 Technical/ML/Hybrid/Risk/成本/時間對齊全數固定。若改了其他項目，建立新的 Experiment ID。
3. 同股同期間同成本、同可用樣本、同本金比較；如 Common Coverage 改變，列原始候選、交集、排除原因及樣本數。
4. 以 Training 內時間順序 Walk-forward 開發，報各 Fold、平均、中位數、標準差、最差 Fold及每股結果；不要隨機切時間序列。
5. 每次至少報 Average Return、Median Return、Worst MDD、Trade Count、Win Rate、Profit Factor、Sharpe、Net PnL、Transaction Cost、Performance Across Stocks/Folds、Signal/Trade Count Stability。交易樣本太少須標記 `INSUFFICIENT`。
6. 不因單一股票、單一 Fold 或單一平均值變好就判定通過；不得只看勝率或 Return。
7. 2025 H1 是已看過的 Validation，不能充當每輪乾淨驗證；2026 H1 是已看過 Development；2025 H2 的狀態為 UNKNOWN/不可保證未看；Final OOS 自 2026-07 起在專案政策中鎖定且有部分 Technical exposure。這些資料不可用於本次或一般 V2 迭代。
8. 未來要做驗證，先 Freeze 程式、Config、Universe、成本、訊號/成交規則、資料角色及判定標準，再提出單獨驗證計畫；對未知或已曝光資料不改標成 clean。
9. 每個 V2 實驗追加到 Experiment Registry，Dataset Registry 記錄來源、hash、角色與使用狀態；保存失敗結果，不覆寫 V1/POC。

## 12. Overfitting Protection

- 假設與通過標準先登記；開始後不得依結果悄悄改同一實驗定義。
- 修改假設、樣本、期間、門檻或判定標準都建立新 Experiment ID。
- 禁止大量 Grid/Random/Optuna 搜尋、單股挑參數、單 Fold 挑策略或依交易 Return 選 ML Champion。
- Technical、ML、Hybrid、Risk 分開歸因；不在同一輪同時修改多元件後聲稱改善來自其中一項。
- 所有股票及 Fold 都要報告；不能隱藏失敗股、低交易數或最差 Fold。
- Final OOS 永遠不做開發；資料第一次接觸即留下使用紀錄，不能事後重新標成 clean。
- 結果不佳時先記錄 `FAIL` 或 `INSUFFICIENT`；是否立新研究問題另行登記，不回頭榨取同一段資料。

## 13. Deferred Portfolio Scope

`PORTFOLIO_DEFERRED = TRUE`

Strategy V2 不處理多股票共享本金、同時持有多檔、股票權重、股票間 Capital Allocation、Portfolio Drawdown、Correlation、Portfolio Risk Budget 或 Portfolio Rebalancing。每一股票獨立測試，不共享資金。

Portfolio V1 僅在以下條件全部完成後另立研究：Single-Stock Strategy V2 完成並驗證、Single-Stock ML/Hybrid 流程固定、Single-Stock Risk 設計完成、Research Rules Freeze。Portfolio V1 必須另行設計同時間多訊號排序、現金不足、部分成交與組合風險帳務。

## 14. Strategy V2 Roadmap

| 階段 | 工作 | 通過條件／界線 |
|---|---|---|
| Phase 0 — Baseline Reconciliation | 保留 10/13 歷史 POC 與目前 Working Version；核對來源、雜湊、資料庫、股票、成交假設，建立 Freeze/Run Manifest。 | 不跑模型、不回測、不動策略；所有 unresolved items 有負責決策與狀態。 |
| Phase 1 — Single-Stock Technical V2 | 先選一項 Technical 假設，其他元件固定；單股建立可比較的 Training Walk-forward。 | 報每 Fold與每股穩定性；不以單股最高報酬選規則。 |
| Phase 2 — Single-Stock ML / Hybrid Usage | 在固定 Technical Candidate 上研究一個 ML/Hybrid 使用問題；保留 RF/XGB/GRU 的完整比較。 | OOF 時間對齊、共同 coverage、公平樣本與分類/交易指標分開。 |
| Phase 3 — Long-only Consolidation | 固定第一輪通過的 Technical/ML/Hybrid 組合，確認單股完整鏈路。 | 不同元件不可同時改；先做 Training 內部穩定性檢查。 |
| Phase 4 — Single-Stock Risk V2 | 在 Long-only 基準固定後，最多研究一個具假設的新 Risk mechanism；Risk V1 保留對照。 | 不 sweep 比例；跨 Fold/股報告風險與交易指標。 |
| Phase 5 — Optional Short V1 | 僅在 Long-only V2 第一輪研究完成且 short market data/accounting preflight 通過後決定。 | Short 為新策略與新帳務，SELL 不直接當 Short。 |
| Phase 6 — Single-Stock Strategy Freeze | 凍結程式、配置、Universe、訊號、成本、成交、Risk、評估指標及版本 hash。 | 研究候選與 POC 清楚區分；無未解的可重現性問題。 |
| Phase 7 — Validation Planning | 只規劃可稽核的後續驗證資料與一次性評估標準。 | 不使用本專案已曝光/未知資料偽裝 clean OOS；如無乾淨區段，等待未來資料。 |
| Future — Portfolio V1 | 在 Single-Stock V2 完成及 Freeze 後另立共享資金研究。 | 本輪不實作、不建帳、不分配權重。 |

### 建議的第一個 V2 實驗

Phase 0 完成 baseline manifest、確認 Technical/ML 使用的資料版本與單股本金後，建議先做 **Execution Timing Experiment 1**：只比較「完成訊號的同棒 close 參考成交」與「下一根可交易 5 分 K 的 open 成交」這一項執行假設。Technical Candidate、ML OOF/Gate、Risk V1、成本、股票、期間及 Fold 全部固定；五檔股票各自獨立跑 Training Walk-forward，不共享資金。事前登記缺少下一根 K 的處理規則，並比較每 Fold/每股交易數、Net PnL、成本、PF、Sharpe 與 MDD。不得因結果再同時改 Entry/Exit 或挑單一股票。

## 15. Files Likely Needed Later

以下是未來可能新增/調整的位置，不代表本輪已修改或授權開始實作。

| Component | 預期檔案 | 後續使用原則 |
|---|---|---|
| V1 baseline manifest | 新增 `research/manifests/strategy_v1_poc_manifest.json` 或 Markdown manifest | 記錄 commit/diff、Technical config與來源檔 hash、ML label/features/OOF、Hybrid config、Risk config、資料庫 hash、成本與輸出位置；不把舊版本改寫成 current。 |
| Technical V2 | 新增 `configs/technical_v2_research.json`、`trading_system/technical_v2.py`、`scripts/run_technical_v2.py`、`tests/test_technical_v2.py`（或採專案既有版本目錄方式） | V1 保留；只實作已登記的一項假設。 |
| ML usage V2 | 新增 ML selection/use spec、runner、report 與對應 tests | RF/XGB/GRU 仍以同一選擇規則比較；不以 Return 挑模型。 |
| Hybrid V2 | 新增 `hybrid/config/hybrid_v2.json`、`hybrid/core/hybrid_v2.py`、runner、tests | 不覆寫 POC gate；保留 Common Coverage 作同一候選 schedule 的比較控制。 |
| Short V1（若核准） | 新增獨立 Short config/core/runner/accounting/tests | 先驗證借券、費率、保證金、停牌/漲跌停與空單成本資料。 |
| Risk V2 | 新增 `hybrid/config/risk_v2.json`、`hybrid/core/risk_v2.py`、runner、tests | Risk V1 固定保留；一次研究一項 risk mechanism。 |
| Research governance | 追加 `research/registries/experiment_registry.csv`、`dataset_registry.csv` 及 access log | append-only；每個新實驗先登記，不改寫歷史 exposure。 |
| Result exports | 新增 `exports/strategy_v2_<experiment_id>_<run_id>/` | HTML/JSON/CSV/audit 使用新目錄，不覆寫舊 POC。 |

## Unresolved Baseline Issues

本文件的 `UNRESOLVED_BASELINE_ISSUES` 計數為下列需要決策或補證的項目數；不把預期差異或已核實正確的 registry 參照算成問題。

1. **選定可重現的 V2 Technical 起始版本**：歷史 10/13 POC Candidate 與目前 10/17 preflight/config 屬不同時間點，需決定 Freeze 哪一個或建立新 baseline manifest。
2. **補齊歷史 Config snapshot 的可重現方式**：POC audit 保存 hash，但單靠目前可變路徑無法還原原始 Config bytes；需確認 Git history、備份或保存 manifest 是否足夠。
3. **統一並記錄 Technical/ML Dataset identity**：目前設定指向不同 SQLite 路徑，需以內容 hash、資料清理版本與共同 eligible samples 證明或指定單一來源。
4. **決定訊號與成交時間假設**：目前模擬 fill 的可執行性需要在 V2 前明確定義；不能把此決定混入其他策略實驗。
5. **確認本金與股數語意**：固定研究本金、Risk 允許曝險、1,000 股限制、零股、現金/成本拒單及未來 user capital 的傳遞方式需由產品/研究規格共同確認。
6. **規劃未來可用驗證資料**：已見、部分曝光或狀態未知的期間不能當 clean；需待 Freeze 後規劃未來未曝光資料，而非解鎖 Final OOS。

## Completion Flags

```text
SINGLE_STOCK_V2_PLAN_CREATED = TRUE
POC_BASELINE_IDENTIFIED = TRUE
BASELINE_SYNC_COMPLETED = TRUE
UNRESOLVED_BASELINE_ISSUES = 6
STRATEGY_CHANGED = FALSE
TECHNICAL_SIGNAL_CHANGED = FALSE
ML_CHANGED = FALSE
HYBRID_LOGIC_CHANGED = FALSE
RISK_LOGIC_CHANGED = FALSE
BACKTEST_LOGIC_CHANGED = FALSE
SHORT_IMPLEMENTED = FALSE
PORTFOLIO_IMPLEMENTED = FALSE
PORTFOLIO_DEFERRED = TRUE
FINAL_OOS_USED = FALSE
PARAMETER_SEARCH_PERFORMED = FALSE
```

`BASELINE_SYNC_COMPLETED = TRUE` 表示歷史 POC 與 Current Working Version 的來源身分已完成對照並寫入本計畫；本輪沒有改動程式、Config、Registry 或歷史結果，也沒有聲稱兩個版本已合併為同一份程式。
