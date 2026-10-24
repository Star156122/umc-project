# Technical V2-B × ML Gate 基準重現與診斷（2026-10-24）

## 結論與執行狀態

- 本輪正式入口結果：`BLOCKED_MISSING_DATA`。
- 缺少 `data/technical_v1_mltech_clean.sqlite3` 與 `data/market_data.sqlite3`；沒有下載、補造或以假資料替代。
- 因無法以目前工作區重新訓練並重跑，以下基準數字來自專案已保存且期間明確受限於 2024-12-30 以前的 `exports/multi_strategy_dev_v1_1792611377073116500`。這是既有證據重核，不宣稱為本輪新回測。
- 本輪只新增診斷可觀測性與 Exposure-Matched Buy & Hold，沒有修改 Technical、ML Gate、Risk V1、出場規則或任何門檻，沒有執行改善版本。
- Additional Holdout（2025-07-01～2025-12-31）及 Final Out-of-Sample（2026-07-01 起）均未接觸。

## 資料角色與規格落差

目前 Registry 與長期研究規範的正式角色為：2023-01-01～2024-12-31=`training`、2025 上半年=`validation`、2026 上半年=`development_seen`。既有四策略入口仍保留舊的子切分名稱：

| 入口子切分 | 期間 | 本報告正式角色 | 用途 |
|---|---:|---|---|
| training | 2023-01-01～2023-12-31 | training（已暴露） | 固定訓練一次 |
| validation | 2024-01-01～2024-06-30 | training（已暴露） | 診斷指標，不挑模型 |
| backtest | 2024-07-01～2024-12-30 | training（已暴露） | 歷史開發回測 |

因此 2024 上、下半年都不能再稱為乾淨未見資料。現有入口沒有 random split，模型在回測前只訓練一次，回測期間不重訓。

## 基準結果重核

| 策略 | 總報酬 | 平倉筆數 | 毛損益 | 手續費 | 交易稅 | 總成本 | 淨損益 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Technical V2-B + Risk V1 | -2.5253% | 11 | -251.00 | 1,220.71 | 1,053.59 | 2,274.30 | -2,525.30 |
| RF Gate + Risk V1 | -0.7451% | 3 | -42.00 | 342.54 | 360.51 | 703.05 | -745.05 |
| XGBoost Gate + Risk V1 | -1.7835% | 1 | -1,560.00 | 110.04 | 113.49 | 223.53 | -1,783.53 |
| GRU Gate + Risk V1 | 0.0000% | 0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 原 100% Buy & Hold | +11.9089% | 未平倉估值 | — | 142.08（僅買入） | 0.00 | 142.08 | +11,908.92（未實現） |

逐策略均滿足 `gross_pnl - commission - tax - slippage = net_pnl`，誤差在浮點容許範圍內；滑價為 0。Technical 毛損益確為約 -251 元，RF 為約 -42 元。

原 Buy & Hold 保持不變；另以既有輸出的第一根開盤 968 元與期末收盤 1,085 元推導 40% Exposure-Matched Buy & Hold：41 股、進場曝險 39.688%、毛損益 4,797.00 元、手續費 119.95 元、交易稅 133.46 元、淨損益 4,543.60 元、淨報酬 +4.5436%。此比較包含期末賣出成本；平均實際曝險約 40.44%、最高約 42.81%（價格上漲後的市值占當時權益）、在場時間約 99.98%。這是由保存曲線推導，並非本輪重新讀取行情。

## 候選訊號與成本診斷

- 已保存輸出只保留市場狀態過濾後 18 個共同候選，精確的過濾前候選數未保存，故本輪不得補造。
- 四策略共用候選排程 SHA-256：`1743e2cb901394aa67bd29b0bc939dc2d86d11edbee8de483408ea1043fe5a17`。
- Technical 11 筆交易的毛損益已是 -251 元，代表候選與出場組合在成本前沒有正期望；成本再把結果擴大為 -2,525.30 元。
- RF 將毛損益改善到接近損益兩平（-42 元），但 703.05 元成本使淨損益仍為負，屬「Gate 有過濾作用，但交易優勢不足以覆蓋成本」。
- XGBoost 唯一放行交易的毛損益為 -1,560 元，主因不是成本。
- 保存輸出沒有逐候選固定 12 根 K 棒報酬、MFE、MAE 與出場原因，因此這些欄位本輪標示為資料不足；新程式已補上產生流程，待真實資料庫恢復後才能填值。

既有曲線可重核的曝險：

| 策略 | 在場時間 | 平均曝險 | 最高曝險 | 投入資本報酬率 | 毛報酬 | 成本 | 淨報酬 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Technical | 16.88% | 6.69% | 40.97% | -6.45% | -0.2510% | 2,274.30 | -2.5253% |
| RF | 5.61% | 2.25% | 41.18% | -1.84% | -0.0420% | 703.05 | -0.7451% |
| XGBoost | 1.06% | 0.42% | 39.53% | -4.52% | -1.5600% | 223.53 | -1.7835% |
| GRU | 0.00% | 0.00% | 0.00% | N/A | 0.0000% | 0.00 | 0.0000% |

## ML Gate 診斷

三模型的診斷驗證真實標籤相同：SELL 222、HOLD 155、BUY 185。模型類別索引為 `0=SELL, 1=HOLD, 2=BUY`。

| 模型 | 預測 SELL/HOLD/BUY | BUY Precision | BUY Recall | BUY F1 | BUY ROC-AUC | PR-AUC |
|---|---:|---:|---:|---:|---:|---:|
| RF | 268 / 155 / 139 | 0.3813 | 0.2865 | 0.3272 | 0.5225 | 舊輸出未保存 |
| XGBoost | 276 / 180 / 106 | 0.3396 | 0.1946 | 0.2474 | 0.4979 | 舊輸出未保存 |
| GRU | 288 / 169 / 105 | 0.3048 | 0.1730 | 0.2207 | 0.4630 | 舊輸出未保存 |

目前 Gate 是三類 argmax，沒有 BUY probability threshold；所以 threshold 正確表示為 `null／N/A`，不能假裝成 0.5。新診斷已新增各類 Precision、Recall、F1 及 one-vs-rest PR-AUC，後續真實重跑會保存。

2330 的市場狀態通過候選 Gate 分布：

| 模型 | ENTRY | BLOCK_HOLD | BLOCK_SELL | 未評估 | BUY 機率 min / mean / median / max |
|---|---:|---:|---:|---:|---:|
| RF | 3 | 5 | 9 | 1 | 0.2353 / 0.3044 / 0.3185 / 0.3773 |
| XGBoost | 1 | 8 | 9 | 0 | 0.0777 / 0.2128 / 0.2003 / 0.3687 |
| GRU | 0 | 3 | 15 | 0 | 0.2527 / 0.2916 / 0.2936 / 0.3354 |

RF 少一筆不是缺預測，而是既有執行引擎只在帳戶狀態允許進場時才呼叫 Gate；新診斷改為對每個共同 candidate_id 直接產生 Gate ledger，同時另記「因既有持倉／風控未實際進場」，避免把帳戶狀態誤認為模型阻擋。

XGBoost 只放行一筆的直接原因是 18 個候選中只有一筆的 BUY 類別為 argmax；其 BUY Recall 僅 0.1946，顯示模型本身偏少預測 BUY。GRU 的 18 個候選全部由 argmax 判成 HOLD 或 SELL；BUY 機率最高雖達 0.3354，但同一筆仍有更高的其他類別機率，所以不是 threshold 沒套用。

GRU 稽核未發現明確程式錯誤：

- scaler 只 fit 2023 training 樣本；
- 序列固定為 11:35～13:30 的 24 根 K 棒，缺棒不補值；
- 決策時間為前一實際交易日 13:30，且早於候選進場時間；
- 訓練 target 最晚 2023-12-29，診斷驗證 target 最晚 2024-06-28，均早於 2024-07-01 回測；
- 類別輸出順序明確映射為 SELL/HOLD/BUY；
- 固定 random seed=42，GRU 固定跑 10 epochs，不用回測期 early stopping。

因此 GRU 零交易目前較符合「模型在這批候選上過度保守且區分力弱」，不是已證實的序列、標準化、載入或類別索引錯誤。既有跨三檔／三折的 filter-funnel 稽核也將 GRU 分類為 `OVER_CONSERVATIVE_EVIDENCE`；被 GRU 阻擋候選的淨期望反而為正，但這是已暴露開發證據，不能據此直接調參後宣稱改善。

## 程式錯誤與策略問題

### 已修正的診斷缺口（不改交易行為）

1. 原輸出沒有原始候選與市場狀態過濾前後 ledger；現新增 raw／post-regime 記錄。
2. 原 Gate ledger 受帳戶狀態影響，RF 只評估 17/18 筆；現新增每個共同 candidate_id × 四策略的完整診斷 ledger。
3. 原輸出沒有固定期間報酬、成本後報酬、MFE、MAE、實際進出場與阻擋原因；現新增。
4. 原 ML 指標沒有 per-class PR-AUC；現新增且保留原欄位。
5. 原輸出沒有策略曝險與資金使用效率；現新增 time_in_market、average_exposure、max_exposure、return_on_invested_capital、gross_return、total_cost、net_return。
6. 原基準只有 100% Buy & Hold 且期末不賣出；現保留原基準，另新增 40% Exposure-Matched Buy & Hold，計入期末賣出成本。
7. 診斷 JSON 現將五分鐘 K 線、可建立序列／模型就緒樣本、ML 訓練樣本、原始／市場狀態通過候選、Gate、Risk V1、實際進場與完成平倉分層列出，避免把訓練樣本與交易樣本混用。
8. Technical 候選 ledger 現合併實際進場、未進場原因、出場原因、毛損益、手續費、交易稅、滑價與淨損益；即使期末仍持倉，也不會誤記為未進場。
9. 新增 `--preflight-only`，只檢查資料角色、必要資料庫、凍結來源雜湊與 Risk V1 契約，不會啟動模型訓練或回測。
10. 新增 `--prepare-models-only` 與 `--model-artifact`：先固定訓練一次並保存逐日三類機率與訓練稽核，再由回測嚴格載入；載入或身分驗證失敗直接阻擋，不會默認輸出 HOLD。

### 策略本身問題

- Technical：成本前已小幅負期望，優先問題仍是候選與出場組合；成本是放大器，不是唯一原因。
- RF：候選篩選後成本前接近損益兩平，但優勢不足以覆蓋成本。
- XGBoost：交易太少且唯一交易本身大幅虧損，不能以總報酬判定 Gate 有效。
- GRU：模型區分力偏弱、候選上過度保守；零交易的報酬可為 0，但勝率、Profit Factor、平均交易等維持 null／N/A。

## 防過擬合流程現況

- 既有 `ml/walk_forward.py` 使用 expanding-window、`shuffle=false`，且只以 fold training fit scaler。
- 標籤樣本要求 signal 與 target 完整位於同一 fold，邊界跨越樣本會 purge；目前沒有額外以日數表示的 embargo，但三日標籤跨界已拒絕。
- Fixed ±1% 標籤已是成本感知：以 T+1 open 至 T+3 close，扣除買賣手續費與交易稅後的 future net return 分類。
- 本輪沒有搜尋 threshold、參數、出場或風控，也沒有以單一 2330 結果挑版本。

使用者指定的跨股票集合為 2303、2330、2454、2317、2382；目前工作區兩個必要資料庫都缺失，所以五檔 2023～2024 行情均為 `BLOCKED_MISSING_DATA`。此外既有 frozen Technical V2-B universe 是 2303、2330、2379、2454、3034，2317／2382 不在此凍結集合；未經新的事前登記與資料恢復，不能偷偷替換 universe。

## 版本決策

- 保留：原 Technical V2-B、RF／XGBoost／GRU 固定模型 Gate、Risk V1 與原 100% Buy & Hold，作為基準。
- 新增但不改策略：`multi_strategy_diagnostics_v1` 與 `exposure_matched_buy_and_hold_v1`。
- 候選改善：無。基準無法在目前工作區重新執行，且精確候選 outcome 診斷尚無真實資料可產生，因此不得挑 threshold、出場或 Technical 修改。
- 淘汰：本輪無新策略版本可淘汰；既有失敗版本維持原 Registry 狀態。
- 研究分類：`BLOCKED_MISSING_DATA`，不是 `EXPLORATORY_NOT_VALIDATED`，更不是候選改善。

## 測試

- 本輪最終輕量測試：`uv run python -m pytest -q hybrid/tests/test_multi_strategy_diagnostics.py tests/test_ml_multiclass.py hybrid/tests/test_multi_strategy_backtest_v1.py hybrid/tests/test_risk_v1.py` → 40 passed；未載入行情、未訓練模型。
- 較早的精準測試：`uv run pytest hybrid/tests/test_multi_strategy_diagnostics.py hybrid/tests/test_multi_strategy_backtest_v1.py tests/test_ml_multiclass.py tests/test_ml_walk_forward.py -q` → 24 passed。
- 完整測試：`uv run pytest -q` → 394 passed、2 failed、9 subtests passed。
- 兩個失敗均在本輪修改前已存在：
  - `test_hashes_and_exposure_statuses_are_preserved`：凍結 manifest 的 `risk_config` 雜湊與目前檔案不符；不能在未釐清來源前靜默重凍結。
  - `test_config_only_enables_a_b_and_forbidden_roles_are_never_loaded`：`ml_v4_research_matrix.json` 啟用的實驗多於舊測試預期 A/B；與本輪四策略診斷無關。
- 正式入口 preflight（本輪新增旗標前的既有嘗試）：`uv run python hybrid/scripts/run_multi_strategy_backtest_v1.py --stock-code 2330 --strategies technical rf xgboost gru` → `BLOCKED_MISSING_DATABASE: .../data/technical_v1_mltech_clean.sqlite3`；本輪沒有再執行完整入口。

## 下一步（未執行）

先從可稽核來源恢復兩個原始 SQLite 資料庫，核對雜湊與日期上界，再以完全相同設定重跑本輪 baseline diagnostics。只有在取得逐候選 MFE／MAE、固定期間成本後報酬、出場原因與完整 Gate ledger 後，才選一項主要改善。現有證據較支持先做「ML target 與 Technical 候選持有期的一致性診斷」，不支持直接降低 GRU／XGBoost threshold 或大量掃描參數。
