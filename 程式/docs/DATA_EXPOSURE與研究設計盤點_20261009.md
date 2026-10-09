# Data Exposure 與研究設計盤點

日期：2026-10-09  
範圍：Technical Strategy、Machine Learning、未來 Hybrid Strategy  
性質：只做歷史盤點與研究設計建議；未執行模型、回測或資料庫查詢，也未讀取禁止區行情資料。

## 判讀原則

本文件把資料接觸分成兩層，避免把「模型沒有拿來 fit」誤寫成「專案沒有看過」。

- **Model Exposure（M）**：資料曾用於 fit、scaler、feature preprocessing、class weight、early stopping，或其他會直接改變模型狀態的步驟。
- **Research Exposure（R）**：人員曾查看該資料的模型指標、交易結果或診斷，並可能據此改版、選擇候選或調整研究方向。
- **No evidence（N）**：目前 Registry、Git、config 與保留輸出中沒有找到實際使用證據。這不等於能百分之百證明從未接觸，因此不能直接寫成 pristine。
- **Forbidden（F）**：政策禁止讀取。本次盤點只檢查政策與歷史紀錄，不讀取該區行情。
- **Not run（NR）**：已有規劃或程式，但 Registry 明確顯示未執行。

只要同一個「股票 × 日期區間」曾被任一模組查看結果，就不能再把它宣稱為整個專案的全新獨立測試；它仍可能在清楚揭露限制後，作為另一模組的診斷或模組內驗證資料。

## 【A. 歷史 Data Exposure 摘要】

1. **ML 的 2023-01-01～2024-12-31 已是 Training exposure。** 原始六檔曾用於 V1～V3 訓練；後續科技、金融 LOSO 讓建議核心十檔都參與過 2023～2024 的 ML 研究。LOSO 中某檔在自己的 held-out run 沒有參與 fit，但它在其他 held-out run 仍可能成為同產業的訓練股票，而且其 held-out 指標已被查看。因此這十檔對「整體研究」都不是完全未見股票。
2. **2025 H1 已被舊 ML V1～V3 當成 validation 使用。** 至少原始六檔 2002、2303、2330、2412、2881、2882 的樣本與結果已產生並查看。新流程的 `ml_validation_access_log.csv` 為空，只能證明新 Candidate 流程尚未正式存取，不能抹除舊流程的歷史 exposure。
3. **2026 H1 是明確的 Development Seen。** Technical 原始六檔、Clean Validation 八檔及 ML V1～V3／baseline 都曾在這段期間產生結果。這段只能做版本比較、錯誤分析與展示，不適合再當最終獨立測試。
4. **2025 H2 目前是最接近 pristine 的共同保留區。** Registry、政策檔與 Git 搜尋沒有發現實際績效輸出，狀態維持 forbidden。這是「目前沒有找到接觸證據」，不是數學上的絕對證明；在正式使用前仍應再做一次 access audit。
5. **2026-07-01 之後不能整段宣稱 pristine。** Technical 過去已對 2454、2891、3045、4904、1301、2603 使用 2026-07-01～2026-09-24 並查看結果。政策後來鎖定 Final OOS，不會改寫既有歷史。核心十檔中的 2454 與 2891 已明確受影響；其他核心股目前只可標為「未找到存取證據」，不能連帶宣稱整段 Final OOS 完全乾淨。
6. **Hybrid 尚未形成可驗證的獨立版本。** 未來 Hybrid 的資料獨立性必須同時繼承 Technical 與 ML 的 exposure；不能只看 Hybrid 程式本身是否第一次執行。

## 【B. 股票 Exposure Matrix】

縮寫：`M/R`＝模型與研究均接觸；`R`＝至少結果曾被查看；`N`＝目前無使用證據；`F`＝禁止讀取；`NR`＝規劃存在但未執行。

| 股票 | 產業 | ML 2023–24 | ML 2025 H1 | Technical 2025 H1 | Technical 2026 H1 | ML 2026 H1 | 2025 H2 | 2026 Q3／Final OOS 歷史 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2303 聯電 | 科技 | M/R | R | NR／N | R | R | F／N | F／N |
| 2330 台積電 | 科技 | M/R | R | NR／N | R | R | F／N | F／N |
| 2379 瑞昱 | 科技 | M/R（LOSO） | N | N | N | N | F／N | F／N |
| 2454 聯發科 | 科技 | M/R（LOSO） | N | NR／N | N | N | F／N | **R（Technical，2026-07-01～09-24）** |
| 3034 聯詠 | 科技 | M/R（LOSO） | N | N | N | N | F／N | F／N |
| 2881 富邦金 | 金融 | M/R | R | N | R | R | F／N | F／N |
| 2882 國泰金 | 金融 | M/R | R | N | R | R | F／N | F／N |
| 2884 玉山金 | 金融 | M/R（LOSO） | N | N | **R（Clean Validation）** | R（baseline；內部角色細節待確認） | F／N | F／N |
| 2886 兆豐金 | 金融 | M/R（LOSO） | N | N | **R（Clean Validation）** | R（baseline；內部角色細節待確認） | F／N | F／N |
| 2891 中信金 | 金融 | M/R（LOSO） | N | N | N | N | F／N | **R（Technical，2026-07-01～09-24）** |

補充：

- LOSO 的 held-out 身分只對「該次 ML fit」成立；同一股票在其他 LOSO run 可能參與訓練，而且研究者已查看它的 held-out 成績。
- Technical 2023～2024 與 2025 H1 的新泛化流程曾被規劃，但 Registry 顯示 `blocked_missing_market_data / NOT_RUN`。目前找不到實際 Technical 績效，因此沒有把規劃本身算成 exposure。
- `N` 只代表此次可稽核來源沒有證據。若有未納入 Git、已刪除且未登記的私人輸出，狀態需更新為「待確認」。

## 【C. 時間 Exposure Matrix】

| 日期區間 | 原政策角色 | Technical 實際歷史 | ML 實際歷史 | Hybrid 可採角色 |
|---|---|---|---|---|
| 2023-01-01～2024-12-31 | Training | 新泛化流程未成功執行；可作 Technical Training，但不是專案層級未見資料 | 已用於訓練、walk-forward、LOSO 與研究比較 | 可用於開發與建構，需使用 out-of-fold 訊號避免 in-sample 樂觀偏誤 |
| 2025-01-01～2025-06-30 | Controlled Validation | 目前未找到成功執行的新 Technical 泛化結果 | 舊 V1～V3 已對原始六檔產生／查看 validation 結果 | 只能稱「部分已見 Controlled Validation」，不可作最終獨立證據 |
| 2025-07-01～2025-12-31 | Additional Holdout | 未找到實際使用證據；政策禁止 | 未找到實際使用證據；政策禁止 | 目前最有價值的一次性共同 Holdout，Freeze 前不得開啟 |
| 2026-01-01～2026-06-30 | Development Seen | 原始六檔與 Clean Validation 八檔已有大量結果 | V1～V3 與 baseline 已有結果 | 只可診斷、展示與歷史壓力分析，不用來選最佳 Hybrid |
| 2026-07-01～目前 | Final OOS（政策） | 2026 Q3 六檔曾被使用；核心股 2454、2891 明確已見 | 新 ML 政策禁止；目前未找到 ML 實際使用證據 | 整段不能統稱 pristine；需按股票與日期切開，或另設未來新起點 |

## 【D. 目前真正還能使用的資料角色】

### 可立即使用

- **2023～2024：Training / Internal Validation。** Technical 與 ML 都可使用相同核心股票與相同日期邊界，但各自維持適合的方法。Technical 使用跨時間 walk-forward；ML 使用 fold-local preprocessing、時間 walk-forward 與 LOSO。
- **2026 H1：Development Seen。** 僅供錯誤分析、既有版本比較、介面展示與研究說明。任何因這段結果做出的修改都要記錄，不能再把後續同區間績效當獨立證據。
- **2025 H1：有限度 Controlled Validation。** 只能在候選版本完整後低頻使用，並逐次留下 access log。對已被舊 ML 看過的股票，必須明確標為 `validation_seen`。

### 現在不可使用

- **2025 H2 Additional Holdout：** 維持 forbidden。只有 Technical、ML、Hybrid 規則全部 Freeze、比較指標預先登記且使用者明確授權後，才一次性開啟。
- **2026-07-01 起 Final OOS：** 維持 forbidden。既有 Technical 接觸部分另列為歷史污染；不能透過改名恢復成未見資料。

### 若需要真正新的最終證據

最穩妥的做法是設定一個「完成 Freeze 之後的未來日期」作為新 Prospective OOS 起點。從起點前就鎖定股票、參數、交易成本、資料清理與評估方式，直到期滿才一次查看。

## 【E. 建議 Core Stock Universe】

建議採用十檔作為共同 Core Universe：

- 科技：2303、2330、2379、2454、3034
- 金融：2881、2882、2884、2886、2891

採用理由：

1. 兩個產業各五檔，能把「跨時間」與「跨股票」分開檢查。
2. ML 已用這十檔完成產業 LOSO，後續 Technical 若採同一母體，Hybrid 比較較不會被股票組成差異混淆。
3. 共同股票與共同 Training 日期會提高比較可信度，不會降低可信度。研究元件不需要使用不同股票才算獨立；真正需要避免的是用未來資料調整、用同一測試結果反覆改版，以及不同模組採不同成本或成交假設。
4. 這十檔是否都具備 Technical 所需的完整 5 分 K、成交量、除權息／公司行動處理，仍須在不碰保留區的前提下做資料可用性盤點；缺漏狀況目前為待確認。

建議從 **Training 階段就統一十檔**，而非等到 Hybrid 才統一。若前期 Technical 與 ML 的股票母體不同，Hybrid 的提升可能只是股票組成差異，而不是融合本身有效。

## 【F. Technical 建議資料配置】

1. **股票：**共同核心十檔；參數原則上跨同產業股票共用，避免為每檔股票各自最佳化。
2. **Training：**2023～2024。沿用 chronological expanding-window，建議 3～4 個 fold；所有門檻選擇只看 Training fold 的平均、標準差與 worst fold。
3. **跨股票驗證：**在每個產業內做 leave-one-stock-out；某檔 held out 時，策略參數不得根據該檔該 fold 的結果調整。
4. **版本 Freeze：**固定策略定義、訊號時間、持有方式、交易成本、滑價、停損與部位規則後，產生 Technical V1 manifest。
5. **2025 H1：**只做一次低頻 Controlled Validation；因 ML 曾看過部分股票，報告需分成「歷史已見股票」與「目前無使用證據股票」。
6. **2026 H1：**只作診斷與舊成果對照，不用來重新搜尋參數。
7. **2025 H2／Final OOS：**Freeze 前禁止。

## 【G. ML 建議資料配置】

1. **股票：**與 Technical 相同的核心十檔。
2. **Training：**2023～2024，維持 4-fold expanding-window；每 fold 的 scaler、preprocessing、class weight 與 early stopping 只能使用 fold training／fold validation。
3. **跨股票泛化：**保留產業內 LOSO。LOSO held-out 指標代表該次模型的跨股票能力，不代表整個專案從未看過該股票。
4. **模型選擇：**以 Macro F1、Macro AUC、各類 AUC、fold stability、worst fold 與跨股票一致性決定，不用單一報酬或單一股票選模型。
5. **Candidate：**完成 Training-only 選擇後，用完整 2023～2024 重訓固定 Candidate；保存 manifest、feature list、label、scaler 規則與 random seed。
6. **2025 H1：**舊 V1～V3 已造成 exposure，因此只能作 Controlled Validation Seen，不應再宣稱第一次獨立驗證。
7. **2026 H1：**診斷專用；禁止自動調參。
8. **2025 H2／Final OOS：**Freeze 前禁止。

## 【H. Hybrid 建議資料配置】

1. **先決條件：**Technical V1 與 ML V1 各自 Freeze，再定義 Hybrid。不要一邊看 Hybrid 結果一邊回頭改兩個元件。
2. **Training 建構：**只用 2023～2024 的 out-of-fold Technical signal 與 ML probability／signal。不能把 ML 對訓練樣本的 in-sample 預測餵給 Hybrid，否則 Hybrid 成績會過度樂觀。
3. **比較組：**同一股票、同一日期、同一成交價與成本假設下，比較 Technical Alone、ML Alone、Hybrid。這三組必須共享資料清理與回測引擎。
4. **融合方式：**先採規則清楚、參數少的確認機制，例如 Technical 產生進場候選，ML 只做通過／降低部位／拒絕；不要立刻用大量權重搜尋最佳報酬。
5. **2025 H1：**僅作部分已見的 Controlled Validation，用來發現明顯失效，不能作最終論證。
6. **2025 H2：**在 Hybrid 規則與門檻全部 Freeze 後，作一次性共同 Holdout。這應是近期最重要的獨立比較。
7. **2026 H1：**作歷史診斷與展示，不參與選擇。
8. **最終證據：**因 2026 Q3 已有 Technical exposure，建議另設 Freeze 之後的新 prospective period，才有最清楚的 Final OOS。

## 【I. 目前研究設計存在的風險】

1. **政策名稱與實際歷史不一致。** `final_out_of_sample_locked` 看起來像未使用，但 2026 Q3 Technical 結果已存在；若只讀政策檔會誤判。
2. **舊 ML validation 已被反覆查看。** 即使 scaler 沒有 fit 2025 H1，研究者仍可因結果修改後續版本，這是 research overfitting，不是典型 scaler leakage，但同樣削弱獨立性。
3. **LOSO 容易被過度解讀。** held-out stock 對單次 model fit 是未見，對整個研究流程未必未見；更不能自動當成 Hybrid 的 clean test。
4. **Technical 與 ML 目前股票母體及成功執行狀態不完全一致。** 若直接比較，很難判斷差異來自方法還是股票組成。
5. **2026 H1 同時承載太多診斷與改版。** 它可用來理解問題，但不能繼續作為宣稱泛化的主要證據。
6. **核心十檔資料完整度待確認。** Technical 的既有泛化流程曾因 market data 缺失被阻擋；在確定共同研究設計前，應先做只讀 schema／coverage audit，但不得碰 forbidden 日期。
7. **若沒有任何真正 pristine 資料，結論必須降級。** 可以報告 Training internal validation、LOSO 與已見時段的穩健性，但不能寫成最終樣本外證明。

## 【J. 你建議我們現在下一步做什麼】

1. **先更新 Exposure Registry。** 把本文件的股票 × 時間 × 模組狀態寫成正式 registry；補上 `model_exposure`、`research_exposure`、`evidence`、`confidence`，避免只有一個含糊的 `used=true/false`。
2. **只盤點 Training 資料可用性。** 對核心十檔的 2023～2024 檢查交易日、5 分 K 覆蓋、OHLCV 合法性與公司行動處理。此步驟不跑策略，也不碰 2025 H2 或 2026 H2。
3. **完成 Technical V1 Training-only。** 用十檔、統一交易假設、時間 walk-forward 與產業 LOSO，完成後 Freeze。
4. **整理 ML V1 Freeze 狀態。** 不再擴大參數搜尋；確認現有 Candidate 的 feature、label、模型設定與 Training-only 結果能對應到十檔共同母體。
5. **預先寫 Hybrid V1 規格。** 明確規定融合邏輯、比較組、成本、主要指標、失敗條件與不得修改項目，再開始寫 Hybrid。
6. **2025 H1 只做一次候選檢查。** 結果需標示已見與較少 exposure 股票，且任何因此修改都要留下 access log。
7. **Technical、ML、Hybrid 全部 Freeze 後，再由使用者明確授權開啟 2025 H2。** 開啟前再做一次 Git、registry 與輸出稽核；只執行一次預先登記的比較。
8. **保留未來 prospective OOS。** 設定 Freeze 後的新日期起點，作為真正最終驗證。期間內不查看中途績效。

## 核心問題的直接回答

- **Technical 與 ML 要不要統一十檔？** 建議要，而且從 Training 階段就統一，不要等 Hybrid 才統一。
- **相同 Training 股票會不會降低可信度？** 不會。相同母體能提高公平性；可信度取決於時間順序、防洩漏、低頻 validation 與真正保留測試。
- **是否必須使用相同 Training 期間？** 建議使用相同角色與日期邊界 2023～2024。fold 切法可依方法不同，但不得跨越未來。
- **Technical 看過、ML 沒看過的資料能否當 ML validation？** 只能稱 ML 模組內 validation；不能稱專案或 Hybrid 的全新獨立資料，且需揭露 Technical exposure。
- **ML LOSO held-out、Technical 沒有 held out 時，該股票是什麼角色？** 對該次 ML fit 是跨股票測試；對整體專案若 Technical 已看過，只是 module-specific held-out，不是 project-level clean test。
- **目前最接近未見的是什麼？** 2025 H2 Additional Holdout；目前只找到鎖定政策、未找到實際結果。仍需在開啟前再次稽核。
- **若最後沒有 pristine data 怎麼辦？** 誠實降級結論，使用 walk-forward、LOSO、worst-fold、信賴區間與多股票一致性提供內部證據；同時建立 Freeze 後的 prospective OOS，不把已見資料改名成新測試。

## 主要證據來源

- `research/registries/dataset_registry.csv`
- `research/registries/experiment_registry.csv`
- `research/registries/ml_validation_access_log.csv`
- `configs/ml_data_policy.json`
- `configs/holdout_policy.json`
- `configs/ml_trading_v1_20261003.json`
- `configs/ml_trading_v2_20261004.json`
- `configs/ml_trading_v3_20261004.json`
- `configs/ml_industry_generalization_20261005.json`
- `configs/technical_generalization_20261006.json`
- `exports/ml_v3_candidate_20261005/candidate_manifest.json`
- `exports/regime_validation_latest.json`
- `exports/routing_simulation_latest.json`
- Git history（以日期字串與上述檔案追查歷史加入／修改）

本盤點沒有讀取禁止區的行情內容；只讀取既有政策、Registry、程式設定、已存在報告及 Git 中的研究歷史。
