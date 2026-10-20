# Strategy V2 Technical Dataset Missing-Date Root Cause Audit

稽核日期：2026-10-19  
範圍：僅 Training 2023-01-01～2024-12-31；2303、2330、2379、2454、3034。  
方法：以唯讀方式查閱程式、設定、既有 audit、Git 追蹤資訊與前一輪 parity 證據；沒有修改資料庫、策略或程式，也沒有執行訓練或回測。

## 結論

Technical DB 的 422 個日期，與 ML DB 中「五檔股票在同一日期都各有完整 09:00～13:25、54 根 K 棒」的日期交集完全相同。這解釋了 295 個平日 stock-date 差異：226 個日期是該股票本身完整、但至少另一檔股票不完整，因此整個日期沒有進 Technical DB；其餘 69 個則是該股票自身的 09:00～13:25 session 不完整。未發現額外的邊界移除或無法分類的平日差異。

這是從兩份資料庫的結果集合驗證出的規則，並非已從產製程式或執行 log 證明。未找到專屬於 `technical_v1_mltech_clean.sqlite3` 的清理 script、run log 或 cleaning audit。因此根因行為可解釋，但產製流程 provenance 尚未完整留存；正式 V2 metadata 尚未更新，baseline readiness 仍維持 FALSE。

## 1. Scope

- Technical DB：`data/technical_v1_mltech_clean.sqlite3`
- ML DB：`data/market_data.sqlite3`
- 日期：2023-01-01～2024-12-31
- 股票：2303、2330、2379、2454、3034
- Technical session：09:00～13:25，預期每日 54 根 5 分 K。
- 將 2024-12-01 星期日資料列視為額外的非交易日異常，不納入題目指定的 295 個 weekday stock-date。
- 未查詢 2025 或 2026 的行情或績效。

## 2. 資料產生鏈追查

找到的原始 K 棒匯入程式為 `scripts/import_technical_v1_training_kbars.py`。它從 Shioaji K 棒來源按日期區間取得資料、將分鐘資料整理為 5 分 K，並寫入 `market_kbars` 與 cache metadata。檢視到的匯入流程負責資料取得與儲存，未發現它實施「五檔全體完整日交集」或建立目前 `technical_v1_mltech_clean.sqlite3` 的清理／輸出步驟。

設定歷史顯示 Technical 資料路徑曾由 `data/market_data.sqlite3` 改為 `data/technical_v1_training_clean.sqlite3`，目前工作設定使用 `data/technical_v1_mltech_clean.sqlite3`。目前設定註記提到 `EXCLUDE_NO_FILL` 與固定 ML-Tech universe，但這項設定文字本身不是產生目前資料庫的可重現 recipe。

Git 檢查未找到目前 Technical DB 的追蹤歷史或對應的清理程式提交；該 DB 不在 Git 追蹤中。沒有找到記錄其輸出 hash、輸入 hash、逐日排除原因與 run id 的專屬 log。

### 找到但不相符的清理 audit

`exports/technical_v1_data_clean_20261013/technical_v1_data_cleaning_audit.json` 記錄 `EXCLUDE_NO_FILL`、來源 `data/market_data.sqlite3`、輸出 `data/technical_v1_training_clean.sqlite3`、股票 2303、2330、2454、2317、2382，以及 478 個完整交易日。它不是本次使用的 `technical_v1_mltech_clean.sqlite3`，universe 也不同，不能作為目前資料集的直接產製證據。

因此，這輪找到了 raw importer、相關設定與一份相近但不匹配的 audit；沒有找到能準確回答「哪一個 cleaner function / command 產生了目前這份 DB」的記錄。具體產生 run 為 UNKNOWN。

## 3. 為何五檔都是 422 個日期

逐股比較兩 DB 的 Training 日期後，五檔 Technical 日期集合皆為 422 天，而且彼此完全相同。進一步由 ML DB 對每檔逐日檢查預期的 54 個 timestamp（09:00、09:05、…、13:25）：

| 股票 | ML 中完整 54-bar 日期數 | Technical 日期數 |
|---|---:|---:|
| 2303 | 479 | 422 |
| 2330 | 479 | 422 |
| 2379 | 438 | 422 |
| 2454 | 478 | 422 |
| 3034 | 462 | 422 |
| 五檔同時完整的日期交集 | **422** | **422** |

五檔同時完整的 422 日期交集，與 Technical DB 五檔共同的日期集合完全相等。這是「全 universe complete-date intersection」被用於輸出資料的直接結果證據。精確 ETL 程式未找到，所以不能宣稱已從產生程式確認其實作位置。

觀察到的規則等價於：某日只要五檔中的任一檔缺少 09:00～13:25 任一預期 K 棒，該日五檔都不進入 Technical DB；沒有使用較早 K 棒補足、也沒有把缺 K 日留成不同長度序列的跡象。這和單純逐股 `EXCLUDE_NO_FILL` 的結果不同，因為它會額外移除同日其他完整股票。

## 4. 295 個 weekday stock-date 分類

| 分類 | 筆數 | 判定依據 |
|---|---:|---|
| `EXPECTED_GLOBAL_INTERSECTION` | **226** | 該股票在 ML DB 有完整 54 根，但至少另一檔在該日不完整；該日不在五檔共同完整日期交集。 |
| `EXPECTED_SESSION_INCOMPLETE` | **69** | 該股票自身在 ML DB 缺少 09:00～13:25 預期 timestamp，依完整 session 條件排除。 |
| `EXPECTED_BOUNDARY_RULE` | **0** | 移除日期分散於 Training 區間，沒有發現只由首尾邊界規則造成的日期。 |
| `DOCUMENTED_CLEANING_RULE` | **0** | 找不到與目前 DB / universe 相符的產製 audit；規則雖可由結果推知，尚非該 DB 的既有正式紀錄。 |
| `UNEXPLAINED_REMOVAL` | **0** | 295 個平日差異均可由上述 session 完整性／全體日期交集結果集合解釋。 |
| `BUG` | **0（未發現）** | 所見資料集合與完整日交集精確一致，未發現日期集合內部矛盾。 |

逐股分解：

| 股票 | 自身 session 不完整而排除 | 自身完整、因其他股票不完整而排除 | 合計 weekday missing |
|---|---:|---:|---:|
| 2303 | 2 | 57 | 59 |
| 2330 | 2 | 57 | 59 |
| 2379 | 43 | 16 | 59 |
| 2454 | 3 | 56 | 59 |
| 3034 | 19 | 40 | 59 |
| **合計** | **69** | **226** | **295** |

所以上述三項 breakdown 為：

- Excluded Because Another Stock Was Incomplete：**226**
- Excluded Because This Stock Was Incomplete：**69**
- Excluded For Other Reasons：**0**（在 295 個平日範圍內）

四檔另有 `2024-12-01` 星期日資料列，不屬於 295 個平日差異。這是額外的非交易日資料異常，應由明確的交易日篩選排除；本輪保留原始資料，沒有刪除或修改。

## 5. 其他候選原因檢查

- **五檔共同完整日期交集 / all-stocks-complete rule：** 結果集合證據為 TRUE，且精確產生 script 未找到。
- **Global trading-date filter：** 日期交集形態符合全體共同日期 filter；沒找到具體實作位置。
- **Session completeness filter：** 54 個預期時間點的完整性可解釋 69 個自身缺失日，也構成 226 個全體交集排除日的觸發條件。
- **Warmup / boundary removal：** 無法解釋這批散布於期間內的 295 天；未發現它們是單純邊界移除。
- **`EXCLUDE_NO_FILL`：** 相近設定與另一份清理 audit 有此政策，但沒有證據確認目前 DB 的產生 run 使用了該程式。不可把它當作目前 DB 已有的正式 provenance。
- **13:30：** 是 ML 與 Technical session contract 的預期差異，與這 295 個平日缺少 Technical stock-date 無關。

## 6. Cleaning audit、README、Registry 與 Commit

搜尋結果中沒有找到明確指向 `technical_v1_mltech_clean.sqlite3` 的 cleaning audit、run log 或 README 產製命令。Registry 與 Common Coverage 輸出可確認目前策略使用這份 DB，但只記錄資料路徑／實驗使用情況，沒有逐日 drop reason 或輸出生成程序。Git 中亦無該資料庫的可追蹤版本或對應 cleaner commit。

其他證據：兩 DB 在本次查詢 universe 的 cache metadata 具有相同日期範圍及 source row metadata；這與 Technical DB 由 ML raw data 衍生的可能性一致，但不能證明複製／清理的具體執行方式。前一輪已比對共同的 **113,940** 根 OHLCV，容差內 mismatch **0**。因此可支持值一致與子集關係，不能取代產製 provenance。

## 7. Canonical Raw Source 建議

建議未來 Strategy V2 Training 以 `data/market_data.sqlite3` 作 canonical raw source，狀態為 **RECOMMENDED**：它保留較完整的逐股日期與 K 棒，且與 Technical DB 共同 timestamp 的 OHLCV 完全吻合，適合作為未清理的來源，再由版本化、可重現的資料準備流程產生策略輸入資料。

這是工程上的來源建議，不是對上游資料供應商或歷史匯入批次的真實性證明。使用時需在明確的 universe 與日期範圍下做交易日和 OHLCV 品質檢查；保留 raw DB 不變，不應先施加五檔共同完整日 filter，除非研究設計明確要求所有股票共用日期。

## 8. 如何正式記錄規則（不重建資料）

不需要為了解釋目前 295 個日期而重建資料，也不需要覆寫 V1 DB。建議在下一次純 metadata 更新時，為目前資料集記錄下列內容：

1. 資料集識別、DB 路徑及 SHA-256、source DB 路徑及 SHA-256、五檔 universe、Training 日期、產生時間與 Git commit / working-tree 狀態。
2. `session = 09:00–13:25`、`expected_bars = 54`、`incomplete_session_policy = EXCLUDE_NO_FILL`、不補值、不建立 synthetic K。
3. `date_eligibility = GLOBAL_INTERSECTION_OF_ALL_STOCKS_WITH_COMPLETE_SESSION`，並寫清楚一檔不完整就排除該日期全部五檔。
4. 每檔自己的缺 K 數、該日因他股缺 K 而全體排除的數量、非交易日排除數量，以及可供重現的日期清單與 reason code。
5. 保留來源 importer、cleaner 命令／函式版本、輸入輸出 hash、stdout/stderr log 位置。由於目前 cleaner 不明，將歷史 recipe 註記為 `INFERRED_FROM_OUTPUT_SET; GENERATOR_NOT_FOUND`，不要補造歷史命令。

這些是後續文件化建議，本輪沒有更新 Registry 或 Manifest。

## 9. 下游重跑需求

在接受並正式記錄「五檔全體完整日期交集」作為當前 Technical dataset 規則、且不改動任何資料與邏輯的前提下，缺日根因本身不要求重跑下游：

| 下游 | 本輪是否需重跑 | 說明 |
|---|---|---|
| Technical Baseline | **FALSE** | 本輪只解釋現有資料集日期集合，未更換 dataset 或 Technical 邏輯。 |
| Common Coverage | **FALSE** | 既有 192 個 Technical candidate date 與 576 筆 OOF 的 previous-trading-day 對齊在前一輪已通過；本輪沒有改樣本。 |
| Risk Baseline | **FALSE** | Common Coverage 與風險輸出未改變。 |
| ML Features / OOF | **不需重產** | ML raw DB 未改，V2 的 Technical 日期交集不等於 ML Training eligibility 政策。若未來決定讓 ML 也只在該共同日期訓練，屆時才需重建 ML features / OOF，並重跑其下游比較。 |

若未來改成逐股保留完整 session（不要求五檔共同日期），或修正 session/drop 規則並生成新 Technical dataset，則 Technical Baseline、Common Coverage 與 Risk Baseline 應重新產生。只有在 ML sample eligibility 或其輸入資料改變時，才需要重建 ML Features / OOF。

## 10. SV2 狀態與 Readiness

- `ROOT_CAUSE_STATUS = EXPLAINED`（缺日結果集合與全體完整日期交集完全一致；歷史產製程式仍缺失）。
- `GLOBAL_DATE_INTERSECTION_USED = TRUE`（由輸出日期集合核實，非由 ETL 原始碼核實）。
- `MATCHING_CLEANING_AUDIT_FOUND = FALSE`。
- `SV2-01 = RESOLVED`，`SV2-03` 的資料差異現可解釋；正式標記前仍應把規則與本報告納入 baseline metadata。
- `SV2_03_CAN_BE_RESOLVED = TRUE`（在正式記錄該交集規則且沿用當前樣本定義的前提下）。
- `BASELINE_READY_FOR_V2 = FALSE`：目前 manifest 仍記錄 `data_parity_status = FAIL`、`sv2_03_resolved = false`、readiness false；本輪依 ONLY ROOT CAUSE AUDIT 範圍沒有更新它。需要後續只做 metadata 的同步更新後，才可依治理流程重新判定 ready。

## 11. 本輪輸出與執行旗標

本輪建立本報告，沒有修改 Manifest、Registry、資料庫、程式或研究結果。

```text
ROOT_CAUSE_STATUS = EXPLAINED
GLOBAL_DATE_INTERSECTION_USED = TRUE
MISSING_STOCK_DATES = 295
COMPLETE_SESSIONS_MISSING = 226
MATCHING_CLEANING_AUDIT_FOUND = FALSE
CANONICAL_RAW_SOURCE = data/market_data.sqlite3
CANONICAL_RAW_SOURCE_STATUS = RECOMMENDED
NEW_TECHNICAL_DATASET_REQUIRED = FALSE
ML_RETRAIN_REQUIRED = FALSE
TECHNICAL_RERUN_REQUIRED = FALSE
COMMON_COVERAGE_RERUN_REQUIRED = FALSE
RISK_BASELINE_RERUN_REQUIRED = FALSE
SV2_03_CAN_BE_RESOLVED = TRUE
BASELINE_READY_FOR_V2 = FALSE

STRATEGY_CHANGED = FALSE
DATABASE_MODIFIED = FALSE
BACKTEST_RUN = FALSE
ML_TRAINING_RUN = FALSE
FINAL_OOS_USED = FALSE
```
