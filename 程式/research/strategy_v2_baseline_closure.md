# Strategy V2 Baseline Closure

日期：2026-10-19  
本輪工作：從現有 plan、audit、Config、Registry、logs 與既存 audit metadata 核對六項問題；建立 POC archive manifest。沒有訓練、回測、讀取新績效、搜尋參數或接觸 Final OOS 行情/績效。

**Metadata finalization update（2026-10-19）：** 後續的 Training-only parity 與 missing-date root-cause audit 已完成。本文件以下原始 issue 分析保留為 closure 建立時的歷史紀錄；本節 Resolution Summary、Baseline Readiness 與 Completion Flags 記錄目前狀態。SV2-01、SV2-03 已解決，資料 parity 為 `PASS_WITH_EXPECTED_DIFFERENCES`。歷史 Technical Config snapshot 與 cleaner/generator provenance 仍不完整，不宣稱 fully reproducible。

## 1. Six Baseline Issues

以下六項逐一承接 `research/strategy_v2_single_stock_plan.md` 的 Unresolved Baseline Issues。`Resolved` 表示該問題已依證據明確定義或封存；`Remaining` 表示仍有未知會影響 V2 結果解讀。

### SV2-01 — Technical Candidate 與 V2 起始版本

- **ISSUE_ID:** `SV2-01`
- **DESCRIPTION:** 確定 V2 要沿用哪個 Technical Candidate，並區分實際 Hybrid POC 與 Technical V1 的候選選擇結果。
- **CURRENT_STATE:** Current Technical Working Config 有 `BR_BASE_RANGE60_BB20_2` 與 `BR_SENS_RANGE55_BB20_2` 候選；10/17 最新 Technical 執行是 `PREFLIGHT_READY`，沒有選出 Candidate。Hybrid/Risk 後續輸出實際使用 55 根候選。
- **HISTORICAL_STATE:** Technical 10/13 audit 的 selection 欄位記錄 `BR_BASE_RANGE60_BB20_2`；Hybrid POC config/audit 記錄的 Technical Candidate 是 `BR_SENS_RANGE55_BB20_2`。兩個記錄均保留，不互相覆蓋。
- **SOURCE_EVIDENCE:** `exports/technical_v1_20261013T024919303843/technical_v1_audit.json`（experiment `technical-v1-20261010-v1`，selection）；`hybrid/config/hybrid_poc_v1.json`；`exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json`；`hybrid/config/risk_v1.json`；`configs/technical_v1_research.json`；`exports/technical_v1_20261017T232930995429/technical_v1_audit.json`。
- **RISK:** 若把 Technical 選出的 60 候選與 Hybrid/Risk 使用的 55 候選當成同一 baseline，V2 前後比較會改變 Technical 訊號來源，無法把差異歸因到 V2 Component。
- **DECISION:** `BR_SENS_RANGE55_BB20_2` 是 V2 Control Candidate，理由為端到端 baseline continuity，不是績效選擇。`BR_BASE_RANGE60_BB20_2` 保留為 Historical Technical Selection。
- **ACTION:** 後續 V2 baseline 沿用既有 Hybrid POC → Common Coverage → Risk V1 的 55 Candidate 輸出鏈；本次 metadata finalization 未重跑或替換任何輸出。
- **STATUS:** `RESOLVED_BY_CONTROL_CANDIDATE_DECLARATION`

### SV2-02 — 歷史 Technical Config 快照與逐位元重現

- **ISSUE_ID:** `SV2-02`
- **DESCRIPTION:** 確認 10/13 POC 的 Technical Config 是否能由目前 Repository/Git 還原。
- **CURRENT_STATE:** 工作目錄 Config SHA-256 為 `027caee3f6cd623f9347d8f00376edadeb7c798666209acdf1161e9e0df25d75`；工作目錄存在未提交修改。
- **HISTORICAL_STATE:** Hybrid POC audit 保存當時 Config SHA-256 `a637c56fd98524668570803854302dbfe658753f27268854371deaa6234d9a46`；Technical export audit 保存 fingerprint `3f9f66ad8071dccc171dd2be5665466310783518699f3099ed266598bc5879c7`。檢查過的 Git versions `6b4bf97`、`ea8c43f`、`35ec22e/HEAD` 對應 Config SHA-256 均與 POC audit 保存值不同；未找到相符快照。
- **SOURCE_EVIDENCE:** `exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json`；`exports/technical_v1_20261013T024919303843/technical_v1_audit.json`；Git history for `configs/technical_v1_research.json`（commits `6b4bf97`、`ea8c43f`、`35ec22e`）；目前 Config 檔案雜湊。
- **RISK:** 歷史 POC 的輸出與 source hashes 可識別並保留，但目前無法聲稱可從當前 Config/Source 完整逐位元重跑歷史 Technical 版本。
- **DECISION:** `HISTORICAL_BASELINE`。將該限制記入 manifest；凍結歷史輸出及 audit identity，不用目前設定假裝重建舊設定。這不阻止另行封存完整的 Current Working Version。
- **ACTION:** 保留 10/13 Technical export 與 POC audit；未來如建立 V2 baseline，對當時實際檔案另存 snapshot/hash，而非只保存可變路徑。
- **STATUS:** `RESOLVED_AS_DOCUMENTED_HISTORICAL_BASELINE`（歷史輸出可識別；舊設定不可逐位元還原的限制已明確記錄）

### SV2-03 — Technical 與 ML 的 Dataset Identity

- **ISSUE_ID:** `SV2-03`
- **DESCRIPTION:** 確認 Technical Candidate 與 ML OOF 是否來自相同的底層行情資料版本及相同 eligible price/bar basis。
- **CURRENT_STATE:** Current Technical Config/Technical 10/13 audit 記錄 `data/technical_v1_mltech_clean.sqlite3`；ML V1 research Config 指向 `data/market_data.sqlite3`。Hybrid OOF audit 驗證日期、股票、Fold 與 OOF 狀態；它沒有記錄 ML database content hash，也沒有證明兩個 SQLite 的 bar-by-bar parity。
- **HISTORICAL_STATE:** Hybrid POC audit 確認 Technical signals 與 ML OOF 的來源檔、各自 Training role、日期對齊檢查與 audit 狀態；沒有記錄可交叉驗證的兩個原始資料庫 hash。
- **SOURCE_EVIDENCE:** `exports/technical_v1_20261013T024919303843/technical_v1_audit.json` 的 `market_data_preflight.database_path`；`configs/ml_v1_research.json` 的 `data.database`；`exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json` 的 `source_files`、`source_roles`、`oof_validation`；Risk audit 的 database hash 欄位未提供對應資料庫路徑。
- **RISK:** 不同資料來源若有缺 K、價格或清理差異，會改變 Technical Candidate 與 ML feature/label 的關係，進而影響 gate 結果與 V2 歸因。
- **DECISION:** `PASS_WITH_EXPECTED_DIFFERENCES`。共同 113,940 根 OHLCV mismatch 為 0；ML 13:30 K 棒是預期 session 差異；Technical 的 422 日期與五檔同日完整 09:00–13:25、54 根 K 棒交集完全一致。295 個 weekday stock-date 已分類為 226 個 global-intersection 排除與 69 個自身 session 不完整，unexplained removal = 0。
- **ACTION:** 以 `research/strategy_v2_missing_date_root_cause.md` 記錄規則及限制，並在 Manifest 保存 data eligibility 與 provenance 狀態。歷史 cleaner/generator 未找到，因此標記 `INFERRED_FROM_OUTPUT_SET_GENERATOR_NOT_FOUND`，不宣稱 `FULLY_REPRODUCIBLE`。
- **STATUS:** `RESOLVED_WITH_EXPECTED_DIFFERENCES_AND_PROVENANCE_LIMITATION`

### SV2-04 — Signal / Execution Timing

- **ISSUE_ID:** `SV2-04`
- **DESCRIPTION:** 明確記錄完成 K 棒後產生訊號時，baseline 的模擬成交價假設。
- **CURRENT_STATE:** Current Working Version 在完成 K 棒後產生訊號，現有 local backtest 以當前 tick close 作模擬成交參考；Technical entry window 為 09:00–12:30。這是研究假設，不等於可保證取得的實際成交價。
- **HISTORICAL_STATE:** Strategy V2 audit 將同棒 close fill 記為目前流程行為；Hybrid POC 記錄沒有變更 Technical execution/exit/risk。歷史 Technical Config/Source 快照限制見 `SV2-02`。
- **SOURCE_EVIDENCE:** `research/strategy_v2_audit.md` 的 Data Flow、Entry/Exit 與 Technical 規則表；`configs/technical_v1_research.json` 的 entry window；`trading_system/backtest.py` 的 local order/fill 路徑；Hybrid POC audit 的 `technical_exit_or_risk_changed=false`。
- **RISK:** 同棒 close 成交可能高估可執行性，特別是訊號和成交參考價落在同一根已完成 K 棒時。
- **DECISION:** `CURRENT_WORKING_VERSION`。把同棒 close 明確保留為 POC/control 假設，不悄悄變更歷史結果；下一個候選 V2 實驗只研究成交時點。
- **ACTION:** 開始任何 V2 run 前，將同棒 close 寫入 baseline manifest/experiment entry；若測下一根可交易 K 開盤，另立只改 execution timing 的 Experiment ID，並事前固定缺少下一根 K 的處理方式。
- **STATUS:** `RESOLVED_AS_DOCUMENTED_BASELINE_ASSUMPTION`

### SV2-05 — Research Capital 與 Position Sizing

- **ISSUE_ID:** `SV2-05`
- **DESCRIPTION:** 將固定研究本金、原始股數限制、Risk V1 sizing、現金/成本限制及未來使用者本金分開說清楚。
- **CURRENT_STATE:** Technical Working Config 為每次初始本金 100,000 元、`lots=1`（最多 1,000 股）、現金使用比例 95%、整數股與現金/費用檢查。Risk V1 固定 risk per trade 1%、position fraction 40%、沿用 2.5% stop；Risk quantity 受風險股數、曝險股數與原始股數共同限制。各股票/Fold 分開重啟資金，不是組合帳戶。Future User Input Capital 尚未接入前端。
- **HISTORICAL_STATE:** Hybrid POC 明訂原資金、position sizing、交易成本與 slippage 不變；Risk V1 是另外套用固定 sizing 的 baseline，未搜尋 Risk 參數。
- **SOURCE_EVIDENCE:** `configs/technical_v1_research.json` 的 `common_backtest`；`trading_system/backtest.py` 的 local cash/quantity/order 邏輯；`hybrid/config/risk_v1.json`；`hybrid/core/risk_v1.py`；`exports/hybrid_risk_v1_20261018T122032419364/risk_v1_audit.json`；`research/strategy_v2_audit.md` 的 Capital & Position Sizing 分析。
- **RISK:** 把每股獨立 100,000 元結果說成共享資金組合，或把本金當策略選擇參數，會產生錯誤解讀。Technical 原始 sizing 與 Risk V1 的零股/零數量邊界行為不同，不能假設兩者 quantity 相同。
- **DECISION:** `CURRENT_WORKING_VERSION`。V2 研究比較固定使用 100,000 元；Risk V1 數值維持 1%/40%/2.5%；股票分開跑、不共享資金。未來使用者本金屬 execution/product input，延後實作；本輪不修改既有 quantity 行為。
- **ACTION:** 新增/執行 V2 實驗前，設定中明確寫出是 Technical 原始 sizing 還是 Risk V1 sizing；保持研究本金固定，報告現金拒單與實際股數。未來若加入 user capital，獨立做產品/執行測試，不以它選模型或策略。
- **STATUS:** `RESOLVED_AS_DOCUMENTED_CURRENT_WORKING_VERSION`

### SV2-06 — Data Exposure / 未來驗證資格

- **ISSUE_ID:** `SV2-06`
- **DESCRIPTION:** 依既有 metadata/Registry/政策記錄 2023–2026 資料曝光，不重新查看任何績效或 raw Final OOS。
- **CURRENT_STATE:** `2023–2024 = EXPOSED`；`2025 H1 = EXPOSED`；`2025 H2 = UNKNOWN`；`2026 H1 = EXPOSED`；`2026-07 起 = PARTIALLY_EXPOSED`。2025 H2 registry 有 `results_viewed=false`，但不足以證明所有外部/未登錄存取皆未發生。Final OOS aggregate row 與部分逐股 rows 的 exposure metadata 不一致；逐股 rows 顯示部分 2026 Q3 已查看。
- **HISTORICAL_STATE:** 研究規範持續將 2025 H2 與 2026-07 起列為 forbidden；已曝光/未知狀態不可透過 metadata 重新標成 clean。
- **SOURCE_EVIDENCE:** `research/registries/dataset_registry.csv`（`current_classification`、`results_viewed`、`participated_in_development`）；`configs/holdout_policy.json`；`docs/長期研究規範.md`；既有 `research/strategy_v2_audit.md` 曝光說明。本輪只讀 metadata，未讀績效或行情。
- **RISK:** 將已見或狀態未知資料當成 Final OOS 會造成驗證污染；目前沒有可由 Repository 證明完全未曝光的既有 Final OOS 區段。
- **DECISION:** `DATA_EXPOSURE`。保留上述分類，2025 H2 維持 UNKNOWN；2026-07 起只標 PARTIALLY_EXPOSED，並保持 policy locked。不能稱 CLEAN。
- **ACTION:** Strategy V2 使用 Training-only 2023–2024 做開發；另行規劃 Freeze 後的未來資料驗證。不得解鎖或查看本輪受限期間。
- **STATUS:** `RESOLVED_AS_DOCUMENTED_DATA_EXPOSURE`

## 2. Resolution Summary

| Issue | Classification | Status | 是否有程式/策略修復 |
|---|---|---|---|
| SV2-01 Candidate 與起始版本 | `END_TO_END_BASELINE_CONTINUITY` | Resolved | 否 |
| SV2-02 歷史 Config snapshot | `HISTORICAL_BASELINE` | Resolved as documented limitation | 否 |
| SV2-03 Technical/ML dataset identity | `PASS_WITH_EXPECTED_DIFFERENCES` | Resolved with provenance limitation | 否 |
| SV2-04 Fill timing | `CURRENT_WORKING_VERSION` | Resolved as documented baseline assumption | 否 |
| SV2-05 Capital / sizing | `CURRENT_WORKING_VERSION` | Resolved as documented current behavior | 否 |
| SV2-06 Data exposure | `DATA_EXPOSURE` | Resolved as documented exposure status | 否 |

`FIX_REQUIRED = 0`。本輪未發現可 100% 證明錯誤的 Status、Registry Reference、Config Reference 或 Source Reference；沒有為了消除差異而改寫來源。

ML Tech 5 stocks 與完整 ML Tech + Finance 10 stocks 分類為 `EXPECTED_DIFFERENCE`：ML Config 明訂 industry-specific training；Hybrid POC/Risk V1 的 Technical scope 是 Tech 5，gate 只取 Technology OOF。這是原本設計範圍差異，不是錯誤，不應把 Finance 強制加入這份 Hybrid POC。

## 3. Historical POC Baseline

Manifest：`research/strategy_v2_baseline_manifest.json`  
POC baseline ID：`strategy_v1_hybrid_risk_poc_20261013_20261018`  
狀態：`FROZEN_POC_BASELINE`，範圍是封存歷史輸出與現有 identity/hash；不表示舊 Technical Config 可逐位元重建。

### Technical

- Experiment ID：`technical-v1-20261010-v1`；run ID：`20261013T024919303843`。
- Export：`exports/technical_v1_20261013T024919303843/`；Dataset：`data/technical_v1_mltech_clean.sqlite3`；Training 2023-01-01～2024-12-31；Universe：2303、2330、2379、2454、3034。
- Technical audit 的 selection 記為 `BR_BASE_RANGE60_BB20_2`；Hybrid POC 實際引用 `BR_SENS_RANGE55_BB20_2`，parameter fingerprint `24dbe303…`。現有歷史 audit 沒保存該 Technical Config 原始 bytes，所以不把目前 Config 中同 ID 的數值冒稱歷史精確參數。
- Technical 10/13 audit 記錄 5 分鐘 K，觀測範圍 09:00～13:25、422 個 Training 交易日；Hybrid/Common Coverage audit 記錄 coverage guardrail 400 training days / 90 evaluation days。當時完整 numeric Config 設定請以不可重建標記處理。

### ML

- Version：ML V1 Phase B；模型 RF、XGBoost、GRU。
- Training 2023-01-01～2024-12-31，industry-specific training。完整 ML Universe 為 Tech 5（2303、2330、2379、2454、3034）及 Financial 5（2881、2882、2884、2886、2891）；此 POC 使用 Technology 5，屬 `EXPECTED_DIFFERENCE`。
- POC label：Fixed ±1%；SELL ≤ -1%，HOLD 介於 -1% 與 +1%，BUY ≥ +1%。Target price 為 T+1 Open → T+3 Close 的成本後報酬；Prediction Horizon 3 trading sessions；Decision Time 13:30 Asia/Taipei。
- OOF：`exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv`；由 audit 驗證為 Training-only 4-fold OOF，非 in-sample，沒有 forbidden roles。該檔 hash 載於 Manifest。

### Hybrid、Common Coverage、Risk

- Hybrid experiment：`technical-ml-hybrid-poc-v1`；Config `hybrid/config/hybrid_poc_v1.json`，其內容 SHA-256 與 POC audit 保存值一致。POC export 為 `exports/hybrid_poc_v1_20261013T041540804059/`。
- Common Coverage：primary registry experiment `hybrid-common-coverage-20261017`，`exports/hybrid_common_coverage_20261017T233825838996/`；Risk V1 實際引用 `exports/hybrid_common_coverage_20261018T121416162413/`。
- Risk V1：`hybrid/config/risk_v1.json` / `hybrid/core/risk_v1.py`；1% risk per trade、40% max position、沿用 2.5% stop loss；來源輸出 `exports/hybrid_risk_v1_20261018T122032419364/`。
- BUY = allow long entry；HOLD = no entry；SELL = block long entry。POC long-only，不啟用 Short。

## 4. Current Working Version

- Git commit：`35ec22eea83cabcacaf22a45e0fc66a4eb1209d3`；Working Tree dirty，相關 Source/Config 有本地修改，Manifest 保存主要 Technical Config/Source hash。
- Technical Config：`configs/technical_v1_research.json`；目前 SHA-256 `027caee3…`，與 POC audit 記錄的 `a637c56f…` 不同。最新 Technical audit `exports/technical_v1_20261017T232930995429/technical_v1_audit.json` 是 preflight ready，不是完整的 Hybrid/Risk POC rerun。
- Current Technical dataset path 仍為 `data/technical_v1_mltech_clean.sqlite3`；V2 Control Candidate 已指定為 `BR_SENS_RANGE55_BB20_2`。Technical V1 standalone audit 的 historical selection 仍是 60，不覆蓋該歷史記錄。當前候選參數、執行/出場、成本與資金值另記於 Manifest `technical.current_working_version`，不宣稱為舊 POC 精確值。
- Current ML Config `configs/ml_v1_research.json` 的 Database 欄位為 `data/market_data.sqlite3`；Training-only parity audit 已確認共同 OHLCV 一致，且缺日差異符合 Technical 全體完整 session 交集。POC 時期 cleaner/generator provenance 仍不完整，已記錄為限制。
- 既存 dirty changes 與輸出均未還原、刪除或覆寫。

## 5. Differences and Repairs

- 歷史 Technical 10/13 選擇結果是 60 根 Candidate；Hybrid/Risk POC 的實際 Candidate 是 55 根。這是可證明的歷史差異，兩者保持原樣；V2 control 依 end-to-end continuity 指定為 55。
- Historical Technical Config SHA 與 Current Config SHA 不同，且目前 Git versions 都不等於歷史保存值；不回填或猜測歷史參數。
- Technical 與 ML 指向不同 SQLite 路徑；後續 Training-only parity audit 已證明共同 OHLCV 一致，並由 root-cause audit 解釋缺日規則。
- ML Tech 5 vs Tech+Finance 10 為 `EXPECTED_DIFFERENCE`，沒有修正必要。
- Common Coverage registry 已以 `report_file`、`result_file` 指向既存 outputs；沒有 registry reference 需要改寫。
- Baseline metadata finalization：只更新 Manifest、Closure 與 current strategy status；沒有改策略、Source、Config、Registry、Dataset 或 Results。

## 6. Data Exposure Status

| Period | Classification | 證據與使用規則 |
|---|---|---|
| 2023–2024 | `EXPOSED` | Training 已用於多輪 ML/Technical/Hybrid/Risk 研究。可用於研究開發，但不是未看過的 OOS。 |
| 2025 H1 | `EXPOSED` | Registry 中 ML Validation rows 記錄 `results_viewed=true` 及 development participation；不得稱 clean validation。 |
| 2025 H2 | `UNKNOWN` | Policy 鎖定；Registry 的 `results_viewed=false` 只說明記錄狀態，不能證明所有未登錄存取都沒有發生。保持禁止讀取。 |
| 2026 H1 | `EXPOSED` | Registry 將 Development Seen 列為已查看/參與開發。 |
| 2026-07 起 | `PARTIALLY_EXPOSED` | 逐股 Registry 有 2026-07-01～2026-09-24 已查看項目；另有 aggregate row 記為未查看。以較保守的逐股證據判斷整段不是 clean。保持 policy locked。 |

本輪只讀既有 Registry/Policy/Audit metadata，沒有打開這些期間的績效報告、回測結果或行情資料。`FINAL_OOS_USED = FALSE` 指本次 closure。

## 7. Baseline Manifest

已建立 `research/strategy_v2_baseline_manifest.json`，狀態為 `FROZEN_POC_BASELINE`。它封存實際 POC run/export/config/OOF/Risk/Common Coverage 來源、可用 hash、資料角色與曝光資訊，並把無法證明的欄位設為 `null` 或標記不確定。該狀態代表 archival POC identity frozen，不代表可逐位元重現、候選已和 Technical V1 selection 對齊，或 V2 已可開始。

## 8. Remaining Risks

1. **歷史資料產製 provenance 不完整：** Technical cleaner/generator 未找到；目前 422 日期交集規則由輸出資料集合推定並精確核實，不宣稱歷史產製 fully reproducible。
2. **歷史重現性有限：** 10/13 Technical Config snapshot 未能從查核的 Git revisions 還原；POC artifacts/hashes 可保留辨識，不能宣稱完整 rerun reproducibility。
3. **成交模型只是模擬：** 同棒 close 作為 baseline fill 是既有模擬假設，不代表實際可成交。未來若研究成交時點，需另立實驗。
4. **組合結果不在範圍：** 每檔/Fold 獨立重啟 100,000 元；不代表共享本金下的多股投資組合。
5. **沒有可證明的既有 Clean Final OOS：** 已曝光與未知資料不重新標 clean；應等待 Freeze 後的新資料規劃驗證。

## 9. V2 Readiness

`BASELINE_READY_FOR_V2 = TRUE`，適用於已聲明的 Training-only V2 control baseline。SV2-01 已指定 `BR_SENS_RANGE55_BB20_2`；SV2-03 為 `PASS_WITH_EXPECTED_DIFFERENCES`。歷史 cleaner/generator 與 Technical Config provenance 不完整仍是已揭露的重現性限制，但不再是未解的資料公平比較 blocking issue。

V2 control 沿用已完成的歷史 `POC_BASELINE` 端到端輸出，並以 Current Working Version 的 Candidate/config/source/data hashes 識別新的 control 定義。Technical V1 10/17 preflight 仍不是新的完整 backtest，也不取代已封存的 POC 輸出。Baseline readiness 只解除進入下一階段的 metadata/data-parity gate；本文件不啟動任何 Strategy V2 Experiment。

1. 若後續啟動 V2，使用已聲明的 55 Candidate control 與 Training-only 範圍，並為每個新 experiment 另立 ID、固定假設與輸出 manifest。
2. Cleaner/generator provenance 為歷史限制；任何未來重建都應採版本化輸出與完整 run log，不覆寫 V1。
3. 遵守現有 Exposure/Forbidden 狀態，不把 2025/2026 或 Final OOS 標記為乾淨資料。

尚未開始任何 Strategy V2 Experiment。

## Completion Flags

```text
BASELINE_CLOSURE_CREATED = TRUE
BASELINE_MANIFEST_CREATED = TRUE
TOTAL_BASELINE_ISSUES = 6
RESOLVED_BASELINE_ISSUES = 6
REMAINING_BASELINE_ISSUES = 0
BASELINE_READY_FOR_V2 = TRUE
STRATEGY_CHANGED = FALSE
TECHNICAL_SIGNAL_CHANGED = FALSE
ML_CHANGED = FALSE
HYBRID_LOGIC_CHANGED = FALSE
RISK_LOGIC_CHANGED = FALSE
BACKTEST_LOGIC_CHANGED = FALSE
SHORT_IMPLEMENTED = FALSE
PORTFOLIO_IMPLEMENTED = FALSE
FINAL_OOS_USED = FALSE
PARAMETER_SEARCH_PERFORMED = FALSE
```
