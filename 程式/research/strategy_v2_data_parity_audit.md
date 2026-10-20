# Strategy V2 Training Data Parity Audit

稽核日期：2026-10-19  
範圍：Training 2023-01-01～2024-12-31、Technical / ML 兩個 SQLite、五檔科技股。  
方法：SQLite `mode=ro` 唯讀連線；OHLCV 查詢只限指定股票、5 分 K 與上述 Training 日期。沒有回測、訓練、參數搜尋或資料庫寫入。

**後續狀態更新（2026-10-19）：** 本報告原始 parity 結論是在 missing-date 根因尚未查明時作成。後續 `research/strategy_v2_missing_date_root_cause.md` 證實 422 個 Technical 日期正好等於五檔完整 session 的共同交集，295 個 weekday stock-date 已全部分類，unexplained removal = 0。因此本資料 parity 的目前狀態更新為 `PASS_WITH_EXPECTED_DIFFERENCES`。歷史 cleaner/generator provenance 仍不完整，不能宣稱 fully reproducible。

## 1. Scope

- Technical DB：`data/technical_v1_mltech_clean.sqlite3`
- ML DB：`data/market_data.sqlite3`
- 股票：2303、2330、2379、2454、3034
- 訓練範圍：2023-01-01 至 2024-12-31（台北時區）
- Technical 正式 Session：09:00–13:25；54 根 5 分 K。
- ML 使用 cache 範圍 `range_start=2023-01-01, range_end=2024-12-31`，輸入序列契約包含 13:30 收盤撮合 K。
- 未查詢或查看 2025、2026 的行情或績效。

## 2. Database Identity

| DB | Path | File size | SHA-256 |
|---|---|---:|---|
| Technical | `data/technical_v1_mltech_clean.sqlite3` | 377,675,776 bytes | `aeabb19cd62b586e162252ed78de35f84fc8e9fa3ff22ed3baa1060f08dfc717` |
| ML | `data/market_data.sqlite3` | 377,675,776 bytes | `11a19c6a3e855fab8a91d3d04e4ef75041b60da1c23c281b2a46b1785d3a45d0` |

兩個檔案 SHA-256 不同，不能視為 byte-identical。兩者都有相同四張表：`market_data_days`、`market_kbar_cache_meta`、`market_kbars`、`market_ticks`。實際 OHLCV 來源表為 `market_kbars`。

## 3. Schema Comparison

兩資料庫的表結構、欄位型別及鍵定義相同：

| Table | 欄位（型別） | Primary / Unique Key |
|---|---|---|
| `market_data_days` | `stock_code TEXT NOT NULL`, `trade_date TEXT NOT NULL`, `status TEXT NOT NULL`, `row_count INTEGER NOT NULL`, `updated_at TEXT NOT NULL` | `(stock_code, trade_date)`；無其他 unique index |
| `market_kbar_cache_meta` | `stock_code TEXT`, `freq_minutes INTEGER`, `range_start TEXT`, `range_end TEXT`, `source_row_count INTEGER`, `source_first_timestamp REAL`, `source_last_timestamp REAL`, `updated_at TEXT` | `(stock_code, freq_minutes, range_start, range_end)`；`WITHOUT ROWID` |
| `market_kbars` | `stock_code TEXT`, `freq_minutes INTEGER`, `range_start TEXT`, `range_end TEXT`, `kbar_timestamp INTEGER`, `open REAL`, `high REAL`, `low REAL`, `close REAL`, `volume INTEGER` | `(stock_code, freq_minutes, range_start, range_end, kbar_timestamp)`；`WITHOUT ROWID` |
| `market_ticks` | `stock_code TEXT`, `trade_date TEXT`, `sequence_no INTEGER`, `timestamp REAL`, `close REAL`, `qty INTEGER`, `tick_type INTEGER` | `(stock_code, trade_date, sequence_no)`；`WITHOUT ROWID` |

`market_kbars` 的主鍵允許不同 cache range 對同一股票、頻率、timestamp 各存一列；本次實際查詢範圍內，依股票與 timestamp 檢查兩邊均沒有重複列。SQLite 不保存可供本次判定的原始到達順序；依 timestamp 排序後沒有倒序。

## 4. Stock Coverage

| 股票 | Technical 首末日 | Technical 交易日 / K 棒 | ML 首末日 | ML 交易日 / K 棒 | 共同日期 | Technical-only 日期 | ML-only 日期 |
|---|---|---:|---|---:|---:|---:|---:|
| 2303 | 2023-01-04～2024-12-30 | 422 / 22,788 | 2023-01-03～2024-12-31 | 482 / 26,453 | 422 | 0 | 60 |
| 2330 | 2023-01-04～2024-12-30 | 422 / 22,788 | 2023-01-03～2024-12-31 | 482 / 26,453 | 422 | 0 | 60 |
| 2379 | 2023-01-04～2024-12-30 | 422 / 22,788 | 2023-01-03～2024-12-31 | 481 / 26,384 | 422 | 0 | 59 |
| 2454 | 2023-01-04～2024-12-30 | 422 / 22,788 | 2023-01-03～2024-12-31 | 482 / 26,451 | 422 | 0 | 60 |
| 3034 | 2023-01-04～2024-12-30 | 422 / 22,788 | 2023-01-03～2024-12-31 | 482 / 26,434 | 422 | 0 | 60 |

Technical 日期集合是 ML 日期集合的子集。總計 2,110 個 Technical stock-date，2,409 個 ML stock-date；有 299 個 ML-only stock-date（295 個平日、4 個 2024-12-01 星期日資料列）。

四檔（2303、2330、2454、3034）共同的 ML-only 日期為：2023-01-03、2023-03-27、2023-03-30、2023-04-07、2023-05-11、2023-05-15、2023-05-17、2023-05-19、2023-06-14、2023-06-15、2023-07-11、2023-07-13、2023-08-21、2023-08-23、2023-08-31、2023-10-06、2023-10-27、2023-10-31、2023-11-01、2023-11-06、2023-11-07、2023-12-07、2023-12-28、2023-12-29、2024-01-10、2024-01-12、2024-01-26、2024-01-29、2024-08-23、2024-08-26、2024-08-27、2024-08-29、2024-08-30、2024-09-03、2024-09-11、2024-09-12、2024-09-23、2024-09-24、2024-09-27、2024-09-30、2024-10-14、2024-10-15、2024-10-17、2024-10-25、2024-10-30、2024-11-05、2024-11-14、2024-11-15、2024-12-01、2024-12-05、2024-12-06、2024-12-09、2024-12-11、2024-12-12、2024-12-13、2024-12-23、2024-12-24、2024-12-25、2024-12-27、2024-12-31。2379 的 ML-only 清單相同，但不含 2024-12-01。Technical-only 日期：五檔均為空集合；Common Dates：每檔皆為該 Technical DB 的 422 個日期。

## 5. Date Coverage

五檔 Technical 資料各有相同的 422 個日期。ML 每檔有 481 或 482 個日期；共同日期皆為 422。ML-only 平日包括許多一般交易日，並非只有邊界日期。四檔在 2024-12-01 各有一筆星期日資料；2379 沒有該日資料。

ML-only 平日中，2303 有 57 日、2330 有 57 日、2379 有 16 日、2454 有 56 日、3034 有 40 日各自具備完整 09:00–13:25 的 54 根 K 棒。後續 root-cause audit 確認這些完整 stock-date 是因同日其他股票的 session 不完整而被五檔全體日期交集排除；其餘 69 個 weekday stock-date 是該股票自身 session 不完整。

`exports/technical_v1_data_clean_20261013/technical_v1_data_cleaning_audit.json` 記錄的是另一個輸出 DB `technical_v1_training_clean.sqlite3`、另一組股票（含 2317、2382）及 478 個完整交易日，不是本次 Technical DB/五檔股票的直接產製紀錄。專屬 cleaner/generator 與執行 log 未找到；歷史產製 provenance 不完整。

## 6. Timestamp Parity

| 股票 | 完全相同 timestamp | Technical-only timestamp | ML-only timestamp | Technical 13:30 | ML 13:30 |
|---|---:|---:|---:|---:|---:|
| 2303 | 22,788 | 0 | 3,665 | 0 | 481 |
| 2330 | 22,788 | 0 | 3,665 | 0 | 481 |
| 2379 | 22,788 | 0 | 3,596 | 0 | 481 |
| 2454 | 22,788 | 0 | 3,663 | 0 | 481 |
| 3034 | 22,788 | 0 | 3,646 | 0 | 481 |
| **合計** | **113,940** | **0** | **18,235** | **0** | **2,405** |

在共同的 422 個日期，每檔 09:00–13:25 的 54 個 timestamp 全部吻合；其中每檔 422 根 13:30 K 棒屬 ML-only timestamp，合計 2,110 根，是符合 ML feature contract 的 `EXPECTED_SESSION_DIFFERENCE`。ML 還有另外 295 根 13:30 K 棒落在 ML-only 日期，屬於缺少整個 stock-date 的一部分，不能單獨以 Session 差異解釋。四筆星期日列也不是 Technical 的 13:30 Session 差異。

兩 DB 本身沒有記錄原始列到達順序；用 timestamp 排序的序列為嚴格遞增，重複 timestamp 為 0。這個結果不代表能從 SQLite 還原原始匯入順序。

## 7. OHLCV Parity

- 比對列數：113,940 個共同股票 / timestamp。
- 浮點價格容差：`abs(a-b) <= 1e-8 + 1e-10 * max(abs(a), abs(b))`。
- Volume：整數完全相等。
- 完全相同或在容差內相符：113,940 列。
- 不一致列：0。
- Open / High / Low / Close / Volume 各欄 mismatch：0。
- mismatch sample：無。

## 8. Expected Session / Cleaning Differences

可解釋差異：共同日期的 2,110 根 13:30 收盤撮合 K 棒只存在 ML DB，符合 ML 的 13:30 feature contract 與 Technical 09:00–13:25 Session 規則；四筆星期日資料符合 ML pipeline 的平日篩選而不會成為 ML sample。

未解差異：295 個平日 stock-date 共少了 16,121 根 ML bar（其中 226 個 stock-date 有完整 54 根 09:00–13:25 K 棒）。目前找不到本 Technical DB 對應的清理政策紀錄能說明這些完整交易日為何被排除。此差異可能改變 Technical 訊號日期及共同樣本基礎，標記為 `UNEXPLAINED_DIFFERENCE`。

兩邊 Training 範圍內依 timestamp 的 OHLCV 都有效且共享值一致；沒有發現共同 timestamp 的重複 K 棒。資料庫沒有可追溯原始匯入順序的欄位。

## 9. Eligible Sample Alignment

唯讀核對既有 Common Coverage Training 產物 `exports/hybrid_common_coverage_20261017T233825838996/`，沒有重跑策略：

- 既有共同候選：192 筆，Candidate ID 均為 `BR_SENS_RANGE55_BB20_2`。
- 既有決策：576 筆，RF / XGBoost / GRU 各 192 筆。
- 576 筆均標記 `MATCHED`、OOF、`entry_date == technical_candidate_date`。
- 192 個 candidate date 的 `expected_previous_trading_day` 均與 ML 2023–2024 股票交易日期中前一個平日相同。
- 576 筆決策的 `ml_signal_date == expected_previous_trading_day`；ML signal 早於 Technical candidate，沒有同日或未來訊號。

因此，既有 192 個候選的 T→T+1 OOF 對齊本身合法。Technical 的 eligible date basis 已由全體完整 session 交集規則解釋；既有 Common Coverage 在該 Technical 候選集合內的對齊通過。這不表示 Technical 與 ML 的逐股日期數相同：它們採用不同、已說明的 session/sample contract。

## 10. Data Parity Classification

**DATA_PARITY_STATUS = PASS_WITH_EXPECTED_DIFFERENCES**

共同 113,940 根 OHLCV 在容差內完全相符；ML 13:30 bar 是預期 session 差異；Technical 422 個日期與五檔完整 09:00–13:25 session 的共同日期交集相同。295 個 weekday stock-date 已全部分類（226 個 global intersection、69 個自身 session incomplete），unexplained differences = 0。歷史 cleaner/generator provenance incomplete，故不宣稱 `FULLY_REPRODUCIBLE`。

## 11. Candidate Decision

- `V2_CONTROL_CANDIDATE = BR_SENS_RANGE55_BB20_2`
- `CANDIDATE_SELECTION_REASON = END_TO_END_BASELINE_CONTINUITY_NOT_PERFORMANCE_SELECTION`
- 決策依據：它是已完成 Hybrid POC → Common Coverage → Risk V1 端到端鏈所用的候選，不以績效選擇。
- `BR_BASE_RANGE60_BB20_2` 保留為 `HISTORICAL_TECHNICAL_SELECTION`；不刪除、不改寫。
- 歷史 POC 參照：候選 fingerprint `24dbe3030fe335b8e62684e908fbe139315933add7a904c38fae07e0e24707bf`。其歷史 Technical Config snapshot 無法還原，因此不宣稱歷史 55 參數與現在工作設定逐位元相同。
- Current V2 Control Definition：以本報告記錄的目前 working config/source、候選參數、資料庫雜湊、Git commit 與 dirty working-tree 狀態作為新 snapshot；Candidate 參數完整列在 manifest `v2_control.current_candidate_parameters`。

## 12. Remaining Risks

1. Technical DB 的歷史 cleaner/generator 未找到；目前交集規則由輸出日期集合推定並核實，無法聲稱歷史生成過程可完整重跑。
2. Current Technical config/source 在 Git 工作目錄有未提交修改；manifest 記錄它們的 SHA-256，但需保留該工作樹或另行凍結 snapshot 才能重現。
3. 本次只能證明共同 timestamp 的值相同，不能證明兩個 DB 的上游來源、匯入批次完全相同。
4. SQLite 中一筆 2024-12-01 星期日資料列不應成為 Training 樣本；保留原始資料，本輪沒有修正或刪除。

建議的 canonical data source：暫以 `data/market_data.sqlite3` 作為 Training canonical raw source 候選，因它包含較完整日期集合，且共享的 113,940 根 OHLCV 已與 Technical DB 相符。這是資料治理建議，尚非來源真實性的證明。應另建版本化 Technical clean dataset，依明確 Session / 完整日規則由此來源生成；之後才視需要重新產生 ML Feature / OOF 與 Hybrid Baseline。不得覆寫既有 POC 輸出。本輪沒有執行這些動作。

## 13. V2 Readiness

- SV2-01（Candidate/control 正式指定）：**RESOLVED**。
- SV2-03（Training raw dataset identity / sample parity）：**RESOLVED — PASS_WITH_EXPECTED_DIFFERENCES**。
- Remaining baseline issue count：**0**。
- `BASELINE_READY_FOR_V2 = TRUE`（適用於已聲明的 Training-only control baseline；Strategy V2 Experiment 尚未開始）。
- 不開始 Strategy V2 Experiment；先處理 canonical data source、Technical clean dataset 的完整日差異與一致的 sample basis。

## 14. Audit Execution Flags

```text
STRATEGY_CHANGED = FALSE
TECHNICAL_CHANGED = FALSE
ML_CHANGED = FALSE
HYBRID_CHANGED = FALSE
RISK_CHANGED = FALSE
BACKTEST_CHANGED = FALSE
NEW_BACKTEST_RUN = FALSE
NEW_ML_TRAINING = FALSE
FINAL_OOS_USED = FALSE
PARAMETER_SEARCH_PERFORMED = FALSE
DATABASE_MODIFIED = FALSE
```
