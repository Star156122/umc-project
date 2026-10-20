# Strategy V2 Research Audit

稽核日期：2026-10-19  
稽核範圍：目前工作目錄中的 Source、Config、Tests、資料角色政策、Registry 與已存在的研究產物索引。  
執行界線：本次僅讀取程式與研究設計，沒有開啟 2025／2026 績效報告或執行回測/訓練，也沒有修改任何策略或設定。狀態檔欄位接觸偏差詳見下方說明。唯一新增檔案為本報告。

**稽核界線偏差說明：**讀取 `research/current/strategy_status.json` 以確認目前工作流狀態時，該檔內嵌了 2026 Development 的績效摘要欄位，該欄位曾出現在本次工具輸出中。這超出「只列出 2025／2026 存在、不讀取績效」的要求；該數值未被本報告引用、整理或用於任何判斷。其餘 2025／2026 內容僅按資料角色與曝光狀態處理。這表示本次不能宣稱完全沒有接觸該欄位；後續如要重做嚴格隔離稽核，應先以不含績效欄位的角色索引/metadata 取代此狀態檔作為來源。

## 1. Executive Summary

目前系統已串起「技術訊號 → ML OOF 預測 → Hybrid 進場 Gate → Risk V1 股數控制 → 原有回測引擎」的 Training-only POC 流程。這表示研究流程與介面已能端到端運作；它不表示 V1 已是正式、最佳或可直接實盤使用的策略。

V1 最適合稱為 **POC Baseline（概念驗證基準）**。原因是 `hybrid/config/hybrid_poc_v1.json` 明訂 `POC_ONLY_NOT_FROZEN`，`configs/technical_v1_research.json` 是 `preregistered_training_only`，而目前狀態登錄也記錄 Technical V1 僅完成 preflight、尚未回測及 Freeze。Hybrid POC、Common Coverage 與 Risk V1 已有 2023–2024 的輸出，但均為研究基準；Risk V1 稽核分類為 `RISK_V1_MIXED`，不構成最終風控結論。

最需要先處理的研究設計限制有四項：

1. **跨股票資金尚未合併**：每個股票 × fold 各自以 100,000 元重啟，不能解讀為一個帳戶同時管理五檔股票的投資組合結果。
2. **Final OOS 目前沒有可證明乾淨的資料區段**：2026 H1 已列為看過；專案紀錄也指出 2026 年 7 月以後已有部分 Technical exposure。2025 H2 雖標示 forbidden，但僅憑現有登錄不能保證它從未被使用。
3. **Technical V1 狀態需分開解讀**：目前正式設定與行情路徑已更新，但這個工作目錄的狀態標示尚未完成回測；Hybrid POC 使用的是較早產生的 Technical BUY Candidate 輸出與固定 Candidate。
4. **模擬成交時點仍需明確化**：Technical 在完成 K 棒後發訊號，現有模擬下單以當前 tick/close 參考價成交。正式 V2 前應先確認這種同一時間點成交假設是否符合可執行的交易流程。

因此 Strategy V2 應以 **凍結並保留 V1 POC 基準、一次只研究一個元件、先 Training 時序驗證，再決定是否動用已見資料** 為原則。暫時不要指定最佳股票或最佳模型，也不要用 2025／2026 資料反覆挑版本。

## 2. Current Architecture

### Repository 中的研究相關區域

| 區域 | 主要用途與重要檔案 |
|---|---|
| `trading_system/` | 共用回測、Technical V1、研究資料防線、舊版策略與診斷。核心有 `backtest.py`、`technical_v1.py`、`research_guard.py`。 |
| `ml/` | ML 標籤、資料角色、特徵管線、模型、Walk-forward、ML 交易模擬。重要檔案有 `ml_v1_labels.py`、`data_pipeline.py`、`research_features.py`、`models.py`、`walk_forward.py`、`trading_backtest.py`、`validation_access.py`。 |
| `hybrid/` | Hybrid POC、Common Coverage、Risk V1 的設定、核心與執行入口。重要檔案有 `core/hybrid_poc_v1.py`、`core/common_coverage.py`、`core/risk_v1.py`、`scripts/run_hybrid_poc_v1.py`、`scripts/run_hybrid_common_coverage.py`、`scripts/run_hybrid_risk_v1.py`。 |
| `configs/` | Technical、ML、資料角色、Walk-forward、股票 Universe 與風險設定。檔案值不一定都被目前 Hybrid 執行；必須同時核對 Runner。 |
| `data/` | SQLite 行情資料及舊批次/基準索引。存在多個行情資料庫，不能只由檔名推定其內容或可用角色。 |
| `scripts/` | Technical V1、ML Phase B、診斷與實驗執行入口。通常負責讀設定、組資料、呼叫 core、寫入具 run id 的 exports。 |
| `tests/`、`hybrid/tests/` | 回測下單與現金檢查、ML 標籤/時間序列/資料角色、Hybrid 對齊及 Risk sizing 測試。 |
| `exports/` | 已完成報告、CSV、JSON、候選及逐筆輸出。它保存多個版本，不應把 `latest` 或單一報告視為唯一正式規格。 |
| `research/current/`、`research/registries/`、`research/archive/` | 目前狀態、資料與實驗曝光登錄、歷史實驗索引。現況文件/Registry 也存在尚未提交的本機變更。 |
| `docs/`、根目錄 `README.md` | 操作說明、研究治理、資料曝露與歷史設計文件。文件用於補足曝光史，但執行規則以目前 Source + Config + 實際 Export 交叉確認。 |

目前 Git 工作目錄不是乾淨狀態：`trading_system/technical_v1.py`、`configs/technical_v1_research.json`、Technical tests、`research/current/strategy_status.json` 與 registries 等有既存修改或未追蹤內容。本次沒有清理或覆寫。以下描述以 **目前磁碟工作目錄** 為準；若要重現結果，需先保存當前 commit 與這些未提交差異。

### 實際資料流

| 階段 | Source / Function | Input → Output | 目前規則與一致性 |
|---|---|---|---|
| Market Data | `data/*.sqlite3`；`trading_system/technical_v1.py:inspect_training_market_data`；`trading_system/backtest.py:BacktestQuote` | SQLite 行情 → 5 分鐘 K 棒/指標列 | Technical V1 Config 指向 `data/technical_v1_mltech_clean.sqlite3`；ML V1 research config 指向 `data/market_data.sqlite3`。兩者資料來源路徑不同；須以每份 export 的 audit/hash 確認實際來源，不能視為同一資料版本。 |
| Technical Indicators | `trading_system/backtest.py:add_indicators`；`TechnicalV1Strategy` | 完成的 5 分 K → MA、RSI、MACD、Bollinger、Range 指標 | 指標由 AppConfig 與 Technical candidate 組合決定。 |
| Technical Candidate | `trading_system/technical_v1.py:TechnicalV1Strategy._entry_reasons`、`process_completed_kbar`、`export_candidate_signals` | 指標快照 → `BUY_CANDIDATE` 訊號檔 | Trend/Momentum、Breakout、Majority Vote 三個策略家族；訊號含股票、fold、參數 fingerprint、signal/earliest-entry 時間。Hybrid POC 使用 Breakout 候選 `BR_SENS_RANGE55_BB20_2`。 |
| ML Prediction | `scripts/run_hybrid_poc_v1.py:rebuild_baseline_oof`；`ml.models.run_multiclass_model` | 2023–2024 每日 24 根序列 → SELL/HOLD/BUY OOF 預測 | POC 明確允許只以既有 V1 Baseline pipeline 重建 Training OOF；禁止 in-sample、2025、2026 與其他來源。三個模型分別產生 OOF。 |
| Hybrid Gate | `hybrid/core/hybrid_poc_v1.py:validate_oof_predictions`、`make_alignment_decision`、`HybridGateTechnicalV1Strategy._try_entry` | Technical BUY Candidate + 前一交易日的對應 ML 預測 → ENTRY 或阻擋原因 | 預測股票、模型、signal/entry/target 日期須一致；ML 訊號須早於進場日。BUY→ENTRY；HOLD→BLOCK_HOLD；SELL→BLOCK_SELL。方向為 long-only，SELL 沒有開空。 |
| Risk Position Sizing | `hybrid/core/risk_v1.py:calculate_risk_size`、`RiskV1HybridStrategy._try_entry` | 通過 Hybrid ENTRY 的原始股數 → 風險版股數 | Risk V1 只在 ML BUY 通過後計算股數；取風險預算股數、40% 曝險上限股數、原策略股數三者最小值。零股數會不下單。 |
| Entry / Exit | `trading_system/backtest.py:BacktestSafeTsst._create_local_order`、`_try_entry`、`_try_exit`；`TechnicalV1Strategy._exit_signal` | 本地模擬委託 → Buy/Sell 成交列 | 買進檢查可用現金，賣出檢查持股數；只支援多單。風控停損沿用 Technical close-based exit。模擬成交參考當前 tick close。 |
| Metrics / Exports | `trading_system/backtest.py:calculate_performance_metrics`、`calculate_mark_to_market_risk`；各 `hybrid/scripts/run_*.py` | 完成交易、資產曲線 → return、MDD、勝率、交易數、成本等 CSV/JSON/HTML | Technical / ML / Hybrid 各有不同匯總粒度；Hybrid Common Coverage 與 Risk V1 的結果以 stock × fold 匯總，並產生 audit。 |

## 3. Current Strategy Rules

### Research scope 與正式設定

| 項目 | CURRENT VALUE | SOURCE / LINE / FUNCTION | Hardcoded / Configurable |
|---|---|---|---|
| Technical V1 股票 | 2303 聯電、2330 台積電、2379 瑞昱、2454 聯發科、3034 聯詠；目前五檔皆半導體 | `configs/technical_v1_research.json:7-33`；`trading_system/technical_v1.py:FORMAL_STOCK_CODES`、`validate_config` | Configurable 結構，但正式清單在 Source 有一致性守門，換股票需明確變更 Source + Config。 |
| ML V1 股票 | 科技：2303、2330、2379、2454、3034；金融：2881、2882、2884、2886、2891；分產業訓練 | `configs/ml_v1_research.json:22-26`；`configs/ml_v1_phase_b.json:12-18` | Configurable，但 V1 Phase B 研究範圍固定。 |
| Hybrid / Risk 股票 | 五檔 Technical 科技股 | `hybrid/config/hybrid_poc_v1.json:5-7`；`hybrid/config/risk_v1.json:4-12` | Configurable 檔案欄位；執行程式另驗證來源/股票一致。 |
| Training / WF | Technical 2023-01-01～2024-12-31，3 個 expanding folds；ML 2023-01-01～2024-12-31，4-fold expanding-window | `configs/technical_v1_research.json:35-83`；`configs/ml_training_protocol_v3.json:5-45` | Configurable；ML runner 有政策守門。 |
| Bar / Session | 5 分 K；Technical 期望每交易日 54 根，首根 09:00、末根 13:25、300 秒間隔；ML 決策時點 13:30、序列含 13:30 收盤撮合 K | `configs/technical_v1_research.json:85-96`；`configs/ml_v1_research.json:28-51` | Configurable但有驗證；Technical/ML 的日內最後位置不同，需清楚標示。 |
| Technical Candidate 家族 | Trend/Momentum、Breakout、Majority Vote；目前 Hybrid POC 固定 Breakout `BR_SENS_RANGE55_BB20_2` | `configs/technical_v1_research.json:182-375`；`hybrid/config/hybrid_poc_v1.json:8-17` | Configurable候選集合；Hybrid POC 指定固定候選。 |
| POC Breakout 參數 | MA20/60/240 方向；Trend MA120、斜率 lookback 12；Bollinger 20×2；突破期 55、出場區間 30；要求突破；成交量確認停用（ratio 0）；使用 Technical 共用風控 | `configs/technical_v1_research.json:282-303`、common backtest `102-125` | 候選參數存在 Config；執行時受 Technical V1 validation 與 fingerprint 約束。 |
| Technical 訊號 | 突破候選需 close 高於前期 Range high、符合穿越條件、close ≥ BB upper、MA 方向與趨勢濾網通過；entry cutoff 12:30 | `trading_system/technical_v1.py:578-624`；`trading_system/backtest.py:977-992` | Configurable。 |
| Entry 時間 / 成交 | Technical 訊號只能在 09:00–12:30；當根已完成 K 產生訊號，模擬下單用當前 tick close；Hybrid 以前一日 ML OOF gate 次一交易日 Candidate | `trading_system/backtest.py:874-904,1420-1460`；`hybrid/core/hybrid_poc_v1.py:146-220,242-264` | 時間由 Config 控制；當前 close fill 是引擎行為。需要列入 V2 執行可行性研究。 |
| Exit | POC 仍沿用 Technical exit：收盤低於前 30-bar 區間低點或原本風險退出；至少持有 2 根；最多 162 根 5 分 K（設定上約 3 個交易時段）；冷卻 6 根 | `trading_system/technical_v1.py:626-652`；`trading_system/backtest.py:1331-1394`；`configs/technical_v1_research.json:102-125` | Configurable。 |
| Stop / Take Profit | stop loss 2.5%；take profit 0%；停損由 close 報酬觸發既有退出程式，不是獨立保證成交的止損委託 | `configs/technical_v1_research.json:102-125`；`trading_system/backtest.py:1331-1342` | Configurable；Risk V1 限制沿用此值。 |
| 每日進場 / cooldown | 每日最多 1 次；冷卻 6 根 K；同一策略持倉時不再進場 | `configs/technical_v1_research.json:117-125`；`trading_system/backtest.py:969-992` | Configurable。 |
| 成本 / 滑價 | 手續費 0.1425%；一般股票交易稅 0.3%；同日交易稅 0.15%（啟用）；slippage 0 | `configs/technical_v1_research.json:102-125`；`trading_system/backtest.py:630-662` | Configurable；Technical V1 驗證限制滑價為 0。 |
| Initial Capital / 原始股數 | 每個模擬 100,000 元；`lots=1`，股數上限 1,000；cash fraction 95% 可用現金，按整數股向下計算 | `configs/technical_v1_research.json:102-125`；`trading_system/backtest.py:331-334,415-416,1396-1418` | Configurable，但目前研究設定固定。 |
| Long / Short | Long-only；Technical `Sell` 是既有多單出場；ML SELL 只阻擋新多單 | `configs/ml_v1_research.json:44-50`；`hybrid/core/hybrid_poc_v1.py:24-28,36-42`；`trading_system/backtest.py:630-712` | 長短方向由現有流程固定為 long-only。 |
| ML Label / Horizon | Fixed ±1% net return；SELL ≤−1%、HOLD 在中間、BUY ≥+1%；Day T+1 Open 到 Day T+3 Close；決策 13:30 | `configs/ml_v1_label_freeze.json:6-14`；`ml/ml_v1_labels.py:67-103` | Label 已 Freeze；不得在 V2 順手重搜。 |
| ML Features | RF、XGBoost 使用 Feature Set A（24 個 intraday features）；GRU 為 A + `volume_trend_5d`、`volume_trend_20d` 輔助 context | `configs/ml_v1_research.json:78-92`；`ml/models.py:172-200` | Configurable，但 Phase B 未選出最終模型。不同模型的輸入並非完全相同，跨模型比較需註明。 |
| ML Models / Parameters | RF 200 trees/depth10/leaf5；XGBoost 150/depth4/lr .05/subsample .8/colsample .8；GRU hidden16/1 layer/dropout0/batch128/max10/patience2/lr .001；random seed 42 | `configs/ml_v1_research.json:94-107`；`ml/models.py:148-264` | Configurable；各模型訓練實作不同。未確認獨立 Final Model。 |
| Hybrid Gate | BUY→ENTRY；HOLD/SELL→阻擋 LONG；缺預測不進場；不以 probability threshold 投票 | `hybrid/config/hybrid_poc_v1.json:29-45`；`hybrid/core/hybrid_poc_v1.py:24-28,146-220` | POC 固定。 |
| Common Coverage | Technical 與 Hybrid 只在兩者都有完整對應預測/候選的共同候選時段比較；不把缺 ML 預測的候選算成 Hybrid 成交 | `hybrid/core/common_coverage.py` 的 common candidate / schedule functions；`hybrid/scripts/run_hybrid_common_coverage.py:214-402` | 程式定義固定；需沿用同一集合作公平比較。 |
| Risk V1 | 每筆風險上限 1%；最大曝險 40%；停損沿用 2.5%；final quantity = min(風險股數、曝險股數、原始股數) | `hybrid/config/risk_v1.json:13-20`；`hybrid/core/risk_v1.py:21-64` | 目前值在程式驗證中固定，不能視為可自由調參功能。 |

Technical V1 選擇規則在 `configs/technical_v1_research.json:127-143`，先看樣本數，再看最差 Fold、Fold 標準差、跨股中位數、MDD、成本效率與鄰近候選穩定性，並明確禁止只用 Return 選擇。這是比單看最高報酬更好的設計，但同一批 Training 反覆比候選仍有選擇偏差。

## 4. Capital & Position Sizing

### Initial Capital 與重啟方式

- 共用 `AppConfig.initial_capital` 預設 100,000（`trading_system/backtest.py:316-334`）；Technical research config 也設定 `initial_capital: 100000`（`configs/technical_v1_research.json:102-108`）。
- `TechnicalV1Strategy` 的模擬是每次呼叫 `_simulate_candidate` 建立新的 `AppConfig`、strategy 與 local account（`trading_system/technical_v1.py:1362-1407`）。Hybrid runner 再以 stock × fold × model 各別呼叫模擬函式（`hybrid/scripts/run_hybrid_common_coverage.py:98-156,274-290`；Risk runner `:301-318`）。因此每個股票 × Fold × 模型都是獨立 100,000 元起始資金，**不是共享資金投資組合**。
- 共用帳戶模擬器會記錄現金、股數、費用與賣出稅；買進現金不足會拒絕，整數股數下單（`trading_system/backtest.py:630-712`）。`quantity` 上限仍是 `lots × 1000`，動態 cash fraction 只會在 0 至 1,000 股間調整；不是資金愈高就能不限股數擴倉。
- 委託 lot 標記：1,000 股倍數為 Common，其他數量標示 Odd/IntradayOdd（`trading_system/backtest.py:1415-1418`）。本地回測接受整數股，沒有模擬零股市場委託時段、最小報價單位或實際流動性差異；「支援零股」不等於已模擬所有零股成交限制。
- Technical `_try_entry` 以 `max(quantity, 1)` 建立買單（`trading_system/backtest.py:1420-1460`）。若原始 sizing 算出 0，會嘗試 1 股，再由現金檢查決定是否拒單。Risk V1 在 `final_quantity <= 0` 時直接不下單（`hybrid/core/risk_v1.py:128-135`）。這是產品化前應明確統一的邊界行為。

### 未來輸入不同金額的安全性

`initial_capital` 是正數驗證後可由設定/命令列輸入（`trading_system/backtest.py:3885-3892,4265`），因此 50,000、100,000、500,000、1,000,000 在算術上都能進入本地模擬；但不能直接宣稱完整安全支援：

1. 原策略仍受 1,000 股上限及 95% 現金使用率限制；大本金不會放大上限。
2. 資金不足一股或費用後不足時，Technical 與 Risk 的零股處理路徑不同。
3. 每股票／Fold 分開重啟資金，沒有跨股資金競爭、同時持倉、投資組合曝險或總部位上限。
4. Risk V1 的 1% / 40% / 2.5% 在 `hybrid/core/risk_v1.py` 中受固定值驗證，僅改 JSON 不代表可安全改參數。

所以「使用者輸入投資金額」在產品設計上是 **Execution / Product Input**；在回測中是資金與 Position Sizing 的輸入，會影響可買股數與資金不足拒單，但本身不是預測或買賣方向策略參數。若 V2 要報告投資組合結果，需另建共享資金帳戶模擬器。

## 5. Long / Short Capability

目前為 **LONG ONLY**。

- Technical 下單的入口是 Buy，既有 `Sell` 用來關閉持倉；本地引擎賣出時要求現有 `local_position >= quantity`（`trading_system/backtest.py:630-712`）。沒有負持倉或借券帳務。
- ML V1 的預測分類 SELL 代表「不允許 Technical 新多單」，並沒有 Short Entry（`configs/ml_v1_research.json:46-50`；`hybrid/core/hybrid_poc_v1.py:24-28`）。
- 若 Strategy V2 要做空，至少需新增：Short Candidate、Short Entry/借券可用條件、Short Exit、反向停損/停利、空頭逐日持倉與資金/保證金、借券費與交易成本、除權息/回補處理、盈虧計算及多空資產曲線、超賣/停牌/漲跌停成交規則，以及覆蓋做空全生命週期的單元/整合測試。現有 SELL class 不足以直接變成賣空委託。

## 6. ML Model Usage

RF、XGBoost、GRU 的共用入口是 `ml.models.fit_multiclass_model` / `run_multiclass_model`（`ml/models.py:148-280`）。RF/XGBoost 先把 24×feature 序列展平；GRU 保留時間軸並經 GRU encoder 分類。GRU Phase B 早停使用 fold validation，RF/XGBoost 為不同模型實作。

Hybrid runner 從 `poc_config["ml"]["models"]` 逐一建立 OOF Lookup，再逐模型執行 Hybrid / Risk（`hybrid/scripts/run_hybrid_poc_v1.py:98-100,565-611`；Common Coverage `:249-283`；Risk `:281-318`）。

| 能力 | 目前狀態 |
|---|---|
| 同一 Runner 連續跑三個模型 | **已支援**；三個模型各自輸出結果。 |
| 單獨指定模型 | **透過設定清單可執行單一模型**，但未見完整使用者介面式 model selector；不建議以改設定檔取代版本化實驗登錄。 |
| 三模型投票 / Ensemble | **NOT IMPLEMENTED**；目前是各模型分開跑。 |
| 每檔股票指定不同模型 | **NOT IMPLEMENTED**；目前 model 是整個 variant 的設定，不是 per-stock routing。 |
| 最終模型 | **尚未選定**；ML V1 config/Freeze manifest 說明 final model 尚未選。 |

比較時應保留 RF/XGBoost/GRU 全部結果，以相同 Training eligible sample、Fold、標籤與評估指標，先看 BUY Precision/Recall/Coverage、Macro F1/AUC、最差 Fold、Fold 標準差與 LOSO/per-stock 穩定性。不得因為目前某一模型 Return 較高就選它。需要另行處理的公平性事項是 GRU 使用 A+VOLUME context，RF/XGBoost 使用 Feature Set A；若要做純模型架構比較，應預先明定相同 Feature input 的比較版本，不能事後挑對某模型有利的輸入組合。

## 7. Risk Management Capability

Risk V1 透過 `RiskV1HybridStrategy` 繼承 Hybrid/Technical 策略，只攔截 BUY 通過後的股數，交易訊號與退出沿用既有 Technical V1。`calculate_risk_size` 使用：

```text
risk_budget = current_equity × 1%
quantity_by_risk = floor(risk_budget / (entry_price × 2.5%))
quantity_by_exposure = floor(current_equity × 40% / entry_price)
final_quantity = min(quantity_by_risk, quantity_by_exposure, original_quantity)
```

**Enable/Disable**：沒有通用布林開關；`run_hybrid_risk_v1.py` 在同一次輸出中建立 Common baseline 與 Risk V1 variants，兩個版本可並存比較。設定值由 `validate_risk_config` 與 sizing core 固定檢查（`hybrid/scripts/run_hybrid_risk_v1.py:61-75`；`hybrid/core/risk_v1.py:21-47`）。

**Risk V1 現況**：屬於 Risk Baseline。它新增風險預算與最大曝險股數控制，但停損是沿用 Technical 的 close-based exit；風險估算假設股價到達設定比例，不代表可保證以該價格成交，亦未納入跳空超額損失或滑價。Risk V1 audit 顯示其進出場時點與基準配對一致，但分類本身是 Mixed；本稽核不重讀或引用其績效數值。

以下功能沒有在目前 Risk V1 找到實作，均為 **NOT IMPLEMENTED**：

- Daily Loss Limit
- Portfolio Drawdown Stop
- Portfolio / sector total exposure cap
- Volatility-based sizing
- Trailing Stop（Technical config 的 ATR disabled；`trailing_stop` 在舊 ML 計畫亦為 null，不是 Risk V1 能力）
- ATR sizing
- Consecutive-loss control
- 保證金、借券或多空組合風控

## 8. Data Role Audit

| Role / 日期 | Repository 可確認狀態 | 使用邊界與稽核結論 |
|---|---|---|
| Training：2023-01-01～2024-12-31 | Technical V1、ML V1 Phase B、Hybrid POC、Common Coverage、Risk V1 均將研究角色記為 Training；核心設定/輸出都存在。 | 可用於預先登錄的 Training-only WF/LOSO 研究，但反覆查看和選擇已累積研究者曝光，不可視為未見資料。 |
| Controlled Validation：2025-01-01～2025-06-30 | `configs/ml_data_policy.json`、`configs/holdout_policy.json` 與 ML access registry 定義為 Validation；研究文件記錄舊 ML V1～V3 已查看。 | 已見資料；可作 Controlled Validation 的歷史紀錄，不能再宣稱首次、獨立 Validation，亦不應每次小改後重看。 |
| Additional Holdout：2025-07-01～2025-12-31 | Policies/Registry 標為 forbidden / locked。 | 本輪未查詢行情列或績效。Registry 未指派資料集，且無法由全 Repository 證明從未被人工或外部工具查看，因此狀態為 **UNKNOWN / CANNOT GUARANTEE UNUSED**。不可現在使用。 |
| Development Seen：2026-01-01～2026-06-30 | `configs/ml_data_policy.json` 標明已用於既有 ML 開發；Technical/研究文件亦將此區間列為看過資料。 | 已見，只可作診斷與歷史脈絡；未查閱對應績效輸出。另見本文件開頭的狀態檔欄位接觸偏差。不可當 Final Test。 |
| Final OOS：2026-07-01～latest | Policy 將 ML role 鎖定為 forbidden。Registry / DATA_EXPOSURE 文件指出 2026 Q3 已有部分 Technical exposure。 | 2026-07 起的區間整體不能宣稱完全乾淨；本輪只核對曝光紀錄，未查詢最新資料端點、行情列或績效。未來更晚的資料也須先有 freeze 時點與曝光稽核。 |

**資料存在範圍**：專案中有多個 SQLite 行情資料檔（`market_data.sqlite3`、`market_data_before_55bar_fix.sqlite3`、`technical_v1_mltech_clean.sqlite3`、`technical_v1_training_clean.sqlite3`）、2023–2024 Training artifacts，以及以 2025、2026 命名或登錄的研究/資料角色項目。除開頭披露的狀態檔欄位外，本次只記錄日期角色/檔案存在，沒有刻意查詢或分析 2025／2026 策略績效。由於資料庫彼此不同、部分路徑指向不同 DB，尚不能用檔名證明它們的實際資料列與 exposure 完全一致。

**DATA_ROLE_AUDIT：**

- Training：可開發；結果已看過，需以預先登錄和時間順序驗證控制後續選擇。
- 2025 H1：已見 Controlled Validation，不再視為獨立樣本。
- 2025 H2：政策鎖定，但「從未讀過」無法保證；維持禁止。
- 2026 H1：Development Seen，只作診斷。
- 2026-07 起：Final OOS policy 雖禁止存取，但已有部分 Technical exposure 記錄；不能把整段當乾淨 OOS。
- **目前 Repository 沒有可被證明未曝光、可立即指定為 Final OOS 的現存日期區段。**最安全的後續 Final OOS 候選是 Freeze 之後才新取得的前瞻資料；若要考慮 2025 H2，須由使用者另行授權，且先做只讀曝光稽核，仍需標示目前的不確定性。

## 9. Overfitting / Leakage Audit

| 項目 | 分類 | Repository 證據與判讀 |
|---|---|---|
| ML 時序洩漏 | PASS（程式規則） | `ml/walk_forward.py:54-116` 只用 fold training fit scaler/context scaler/class weights，按 signal/target 日期移除跨界樣本並 `shuffle=False`；`ml/data_roles.py:59-72` 阻擋 holdout/Final OOS。仍需在每次新 Runner 確認使用該路徑。 |
| Label 未來資料 | PASS（定義） | `ml/ml_v1_labels.py:67-103` 使用 Day T trailing volatility，target 是 T+1 open 至 T+3 close，未來報酬只作 label；目前 label 已 Freeze 為固定 ±1%。 |
| Hybrid 同日 Look-ahead | PASS（日期對齊守門） | `hybrid/core/hybrid_poc_v1.py:62-120,146-220` 檢查 signal/entry/target 日期、唯一性與預測早於入場日；`hybrid_poc_v1.json:29-34` 對缺預測與同日預測明確阻擋。 |
| Technical 完成棒與成交價一致性 | WATCH | Technical 在完成 K 棒後產生訊號，Simulator 以目前 tick close 作市場單參考價（`trading_system/backtest.py:874-904,1420-1460`）。若研究宣稱成交在訊號棒之後，需再查清 timestamp/aggregate 語義；目前同時點成交可能偏樂觀。 |
| Repeated tuning / reuse | RISK | 專案含多輪 Technical sensitivity/early-followthrough/risk/entry-exit、ML V1/V2/V3/V4A、Context/Temporal 與多份 exports/registries；同一 Training 區間反覆開發。不是單一明顯程式錯誤，但會造成研究者層級的多重比較與選擇偏差。 |
| Multiple testing | RISK | `configs/technical_v1_research.json` 同時登錄三家族與多個 baseline/sensitivity candidates，Repository 還有不同 dated experiment outputs。候選越多，Training 中偶然突出者越可能被選中。 |
| Model selection bias | WATCH | Phase B 比較 RF/XGB/GRU，Hybrid 又各自產生比較結果；目前尚未選最終模型，這是保護措施。後續若依 Training Return 或已見 Validation 選勝者，會升級為 RISK。 |
| Stock selection bias | WATCH | Technical/Hybrid 目前只用五檔同產業半導體股；ML 擴至科技＋金融，但 Hybrid 並沒有全十檔泛化證據。更換/縮小 Universe 必須在讀結果前登錄。 |
| Parameter snooping | WATCH | Technical 設計有候選與鄰近穩定性檢查且不以單一 Return 選擇（Config selection）；但候選已多輪接觸 Training，仍有事後選擇風險。ML Phase B 禁止 K/Feature/Hyperparameter/threshold search，這是正向守門。 |
| Final OOS contamination | RISK | `configs/holdout_policy.json`、`configs/ml_data_policy.json` 表面上將資料鎖住，但 `docs/DATA_EXPOSURE與研究設計盤點_20261009.md` 及 Registry 記錄 2026 Q3 存在 Technical exposure；政策檔本身不能抹除歷史曝光。2025 H2 未能證明完全未見。 |
| 單股/單段挑最好 | WATCH | Registry、候選選擇設計有跨股、跨 Fold、最差 Fold、標準差檢查；但固定 Universe 的產業涵蓋仍有限，且各 stock-fold 初始金額分開，不能由平均值代表投資組合。 |

## 10. Strategy V2 Research Opportunities

以下是研究問題，不是現在要執行的策略修改。每個候選實驗需先登錄、只改一個研究元件，並保留 V1 原始版作對照。

### A. Technical Improvement

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 訊號完成後用下一根可交易 K 的價格成交，會比同棒 close fill 更接近可執行結果。 | 減少訊號與成交同時發生造成的樂觀假設。 | `trading_system/technical_v1.py:process_completed_kbar`、`trading_system/backtest.py:_try_entry` / local fill；建立 V2 專用配置/入口。 | Training 5 分 K，需保留下一根 K、停牌/缺 K 標記及成本。 | 中；成交時點改變會影響全部候選。 | 先登錄單一執行假設，逐 Fold 比較交易數、成本、Return/MDD，檢查缺 bar 和最差 Fold；不動已見日期選參數。 | MUST |
| 先 Freeze 一個 Technical baseline，再只研究一項 exit 元件（例如持有時間上限或結構性退出）可能提高跨 Fold 的穩定性。 | 目前多種 Technical 家族共享引擎，但目前 Hybrid POC 只固定一種 breakout 候選。 | `trading_system/technical_v1.py:_exit_signal`、`configs/technical_v1_research.json` 的 V2 copy、dedicated tests。 | 2023–2024 同 Universe、共同 bar coverage。 | 高；exit 規則容易被局部行情調出來。 | 單假設、固定候選、expanding WF；需同時看各股、各 Fold、交易次數、成本、MDD 和鄰近穩定性。 | STRONG |

### B. ML / Model Usage

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 不改模型，只建立固定 model-selection rubric，會比看交易 Return 選 RF/XGB/GRU 更可重現。 | ML Phase B 尚未選最終模型；模型輸入也有 A 與 A+VOLUME 差異。 | `configs/ml_v1_research.json`（保持 Freeze；新增 V2 selection spec）、`scripts/run_ml_v1_phase_b.py`、`ml/models.py` 的報告輸出。 | 已有 Training OOF/WF/LOSO；不需新日期。 | 中；若評估指標/權重事後選擇仍會偏。 | 預先固定 BUY precision/recall/coverage、Macro F1/AUC、最差 Fold/std、LOSO/per-stock 規則；所有模型完整報告，不按 Return 選。 | MUST |
| 同輸入特徵、同 eligible samples 比較架構，能分辨差異來自模型還是 Feature Set。 | 目前 RF/XGB 是 Set A，GRU 有兩個 volume context。 | `ml/models.py`、ML config、Phase B Runner；使用新版本而不覆寫 Freeze。 | Training 2023–2024 的同一 common eligible set。 | 中；Feature 變更可能和 Architecture 混淆。 | 一次只把輸入一致性設為比較控制；WF/LOSO 分層報告，未通過不得選勝者。 | STRONG |

### C. Hybrid Decision Logic

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 用 OOF BUY 機率的校準品質檢查固定 BUY gate，可能識別目前 gate 對股票/時間段的差異。 | 現有 Gate 只看 argmax 類別，沒有校準或信心資訊。 | `hybrid/core/hybrid_poc_v1.py`、OOF schema、Hybrid 專用 tests；另立 V2 gate，不改 POC。 | Training-only prior-session OOF + Technical Candidate；保持 common coverage。 | 高；機率門檻很容易對 Training 過擬合。 | 先診斷 reliability/calibration、coverage；如要提出單一門檻，需事前登錄，使用 inner WF 定義，不能掃多個門檻。 | OPTIONAL |
| 每一個模型輸出都按相同共同候選日期比較，可避免把不同 coverage 誤當成 Gate 效果。 | Common Coverage 已有共同 schedule 設計，應成為所有 V2 Hybrid 的固定比較基準。 | `hybrid/core/common_coverage.py`、`hybrid/scripts/run_hybrid_common_coverage.py`。 | 同一 Technical signals 與三模型 OOF。 | 低至中；主要風險是只選共同可用樣本後外推到全體。 | 同時報告共同樣本數、排除理由、原始全 Candidate 數；跨 Stock × Fold 穩定性，不用單一平均 Return 選 Gate。 | MUST |

### D. Long / Short

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 將 ML SELL 重新定義成獨立 Short Candidate，而非直接拿現有 SELL class 下空單，才可能評估多空策略。 | 目前引擎和 Gate 都是 long-only；SELL label 是方向分類，不是做空交易規則。 | 新增 `short_candidate`、空單生命週期帳務、借券/保證金/成本、PnL、風控及 tests；不要改寫 V1 sell path。 | 有含借券可用/費率、公司行動、漲跌停與成交資料的 Training data；現有行情庫未證明具備這些欄位。 | 很高；做空成本和可借券限制易被忽略。 | 先做資料/帳務 preflight；若資料不足不進回測。可行後以獨立 Long-only benchmark 對照，WF 分段，不用同一 SELL 指標直接宣稱有效。 | OPTIONAL |

### E. Position Sizing / Risk Management

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 目前 Risk V1 是單筆 sizing 基準；若增加共享 Portfolio Exposure cap，可能控制多檔同時持倉的總風險。 | 現有每個 Stock × Fold 各自重啟帳戶，40% 是每筆上限，沒有組合總曝險。 | 新增 `hybrid/core/risk_v2.py` portfolio state、runner、config、交易簿測試；保留 `risk_v1.py`。 | 同步時間戳、多股票同時訊號、資金與成本的 Training 資料。 | 中至高；總曝險門檻不能從單段結果搜尋。 | 先固定一個風險預算假設；共享 cash 的時間序列模擬、最差 Fold/跨股 MDD/成本/未成交原因。 | MUST（若要報告帳戶級結果） |
| 加入 Daily Loss Limit 或 Portfolio Drawdown Stop 的研究，可能限制連續虧損期間的回撤。 | 這些控制目前不存在；Risk V1 的 per-trade sizing 無法阻止連續交易累積損失。 | 新版 portfolio risk manager、日界線/回撤狀態、重新進場狀態機及 tests。 | 按日期排序的所有持倉、市值、已實現與未實現 PnL。 | 高；stop 門檻容易針對特定 fold 擬合。 | 先選一個有理由的固定規則，Training WF；不可拿歷史最佳 MDD 門檻搜尋。 | STRONG |

### F. Capital / Portfolio Management

| Hypothesis | Why It May Help | Required Code Area | Required Data | Overfitting Risk | Recommended Validation | Priority |
|---|---|---|---|---|---|---|
| 用一個共享帳戶在同一時間處理所有股票訊號，才能回答「投入 50,000 / 100,000 等金額時實際資產如何變化」。 | 現有逐股獨立 100,000 起始資金，不代表實際組合；同日多檔訊號也不會爭用現金。 | Portfolio event loop、shared cash/positions/order queue、產品輸入欄位與 portfolio tests；V1 引擎留作單股基準。 | 同步的多股票 OHLCV、完整同日 bar、成本、公司行動與訂單可行性。 | 高；資金優先順序/下單排序會影響結果。 | 事先固定訊號排序和資金不足政策；對多個本金做情境輸入，不把本金當 alpha 參數；以 WF 報告 Portfolio return、MDD、未成交和成本。 | MUST（若面向使用者顯示本金） |

## 11. Answers to Q1–Q7

**Q1. 目前 V1 是 Final Strategy 還是 POC Baseline？**  
是 POC Baseline。Hybrid config 明寫 `POC_ONLY_NOT_FROZEN`，Risk V1 也寫 `POC_RISK_FIXED_NOT_OPTIMIZED`；Technical V1 現行狀態是 preflight ready、backtest 未執行、未 Freeze；ML 的 label 已 Freeze，但 final model 未選。這些狀態都不支持稱為 Final Strategy。

**Q2. Strategy V2 哪些可研究、哪些先 Freeze？**  
可研究執行成交時點、Technical exit、模型使用方式、Hybrid gate 診斷、portfolio sizing/共享資金；每次只改一個清楚定義的元件。先把 V1 的 Technical Config/程式 hash、ML Fixed ±1% label/Feature schema/horizon/decision time、POC OOF 對齊規則、Common Coverage 定義、Risk 1%/40%/沿用 2.5% 停損以及成本存成不可覆寫基準。Freeze 基準是為了比較，不是宣稱它們已最優。Technical 當前工作目錄有未提交變更，應先把本機版本封存再開始。

**Q3. 使用者輸入投資金額屬於什麼？**  
屬於 Execution / Product Input；回測將它傳給資金與股數計算。它會改變可買股數、現金拒單與投資組合結果，但不應改變 Technical/ML 預測訊號。Risk 比例（例如單筆風險、曝險上限）才是 Risk Parameter；目前 V1 比例被固定驗證。

**Q4. 目前 SELL 能否直接做 Short？**  
不能。ML SELL 只阻擋新多單；Local Backtest Sell 必須有持股，不存在負持倉、借券、空單保證金或空頭損益路徑。

**Q5. RF/XGBoost/GRU 最安全比較方法？**  
三個模型在同一 Training eligible samples 與時間 Fold 上分別跑，不合併投票、不依 Return 選勝者；比 BUY precision/recall/coverage、Macro F1/AUC、最差 Fold、標準差、LOSO 與 per-stock 穩定性。選擇規則先寫進 registry。若模型輸入不完全相同，需明確報告或另建同 Feature 的控制比較。2025 H1 已見，不能當每輪調整的安全測試集；2026 H1 也只能作診斷。

**Q6. Risk V1 是 Final Risk System 還是 Risk Baseline？**  
是 Risk Baseline。它只有單筆 sizing、曝險比例與沿用停損，沒有日損失限制、組合回撤停機、共享資金、波動 sizing、trailing/ATR 或連敗控制；停損按 close 觸發，不保證成交在停損價。

**Q7. 避免過擬合的資料流程？**  
Training 2023–2024 用 expanding Walk-forward/LOSO 開發，統計各 Fold 平均、標準差、最差 Fold、跨股穩定性；2026 H1 僅作已見診斷，2025 H1 是已見 Controlled Validation，不能反覆調參；2025 H2 維持 forbidden 且 unused 狀態無法保證；2026-07 起已有部分 Technical exposure。每個 Candidate 先 freeze 程式、設定、Universe、成本、選擇標準，再尋找一段 Freeze 後新取得且曝光稽核可證明乾淨的前瞻 Final OOS，並只做一次預先登錄評估。

## 12. Proposed Strategy V2 Research Rules

建議將以下規則作為 Strategy V2 的治理條款；本次只列規則，不寫入其他 Config/文件：

1. **先寫 Hypothesis**：每個實驗先登記目的、預期機制、唯一主要變更、主要指標、失敗判準與資料角色。
2. **固定實驗定義**：開始跑後不得依中途結果修改日期、股票、訊號、成本、指標或通過條件；需要改時建立新 experiment id。
3. **一次只改一個 Component**：Technical signal、Exit、ML input/model、Hybrid Gate、Risk sizing、Portfolio Accounting 分開實驗；必要的依賴修改需標為同一個整體假設。
4. **保留 V1 immutable baseline**：新檔案/新版本輸出，不覆寫舊 config、程式 hash、CSV、JSON、HTML、Candidate 或 Freeze Manifest。
5. **資料角色不可混用**：Training 用於開發；Development Seen 只診斷；2025 H1 Seen Validation 不做每輪迭代；2025 H2 及 Final OOS 維持 forbidden，需使用者明確授權與先行曝光稽核；Final OOS 不可調參。
6. **ML 保持時間順序**：只用 chronological expanding/walk-forward/LOSO；禁止 shuffle K-fold；每 Fold preprocessing/scaler/class weight 僅 fit Fold Train；移除跨 Fold target。
7. **交易時間要可執行**：訊號只能使用當時已完成資料；記錄 decision timestamp、最早可成交時間、成交價來源；不得以同一根未完成 K、未來 close 或 future fields 作決策。
8. **不以單一股票或 Return 選擇**：報告每股、每 Fold、各市場狀態的結果；模型選擇先看分類品質/泛化/穩定性，交易 Return 作後續診斷而非單獨選擇器。
9. **最低共同指標**：Return、Worst MDD、Trade Count、Transaction Cost、Stability Across Stocks、Stability Across Time Folds；另報 Win Rate、Profit Factor、Sharpe、Coverage、未成交/排除原因。不得只展示 Average Return 或勝率。
10. **公平比較**：比較版本使用同一 Universe、資料日期、eligibility、候選時點、成本、初始資金與出場；若某一端缺資料，明確 common coverage 並列樣本流失。
11. **登錄與產物**：每個 Strategy V2 Experiment 寫入 `research/registries/experiment_registry.csv`，資料集寫入 dataset registry；保存 git commit、config/code hash、資料庫 hash、命令、access log、fold metrics 和失敗結果。
12. **不做自動搜尋**：禁用 Grid/Random/Optuna 或從結果自動挑最好參數。若提出候選，須有事前理由及有限、預先登錄的候選設計。
13. **完整揭露負結果**：不能刪除不利版本、選擇性報告股票或 Fold；對已看過的資料明確標為 seen。

## 13. Recommended Next Step

先建立一份 **V1 Freeze/Run Manifest**，保存當前工作目錄的 commit、未提交 diff、Technical Config、ML label/feature contract、Hybrid OOF source、Risk Config、成本、Universe 與各輸出 hash；把「目前 Technical V1 preflight 尚未回測」和「Hybrid POC 實際使用歷史 Technical Candidate」分成兩個基準版本。接著只選一個 V2 假設，優先做成交時間/資金帳戶假設的 Training-only preflight，先不要動用已見或鎖定資料。

## 14. Files That Would Be Modified IF V2 Begins

以下只是可能涉及的檔案清單；本次沒有修改：

| V2 Component | 可能新增/修改位置 | 原則 |
|---|---|---|
| Technical V2 rules | 新增 `configs/technical_v2_research.json`、`trading_system/technical_v2.py`、`scripts/run_technical_v2.py`、`tests/test_technical_v2.py` | V1 保留；若改成交引擎則需另行版本化，不直接暗改既有 fills。 |
| Backtest execution realism | `trading_system/backtest.py` 或新建 V2 execution adapter；新增 execution tests | 只在明確假設下改 fill，需涵蓋價格、時間戳、現金與費用。 |
| ML usage / model selection | 新增 V2 selection config/runner/report/tests；必要時更新 `ml/models.py` | 保留 `ml_v1_label_freeze.json` 與 V1 feature contract，不因 Hybrid Return 選 ML。 |
| Hybrid V2 | 新增 `hybrid/config/hybrid_v2.json`、`hybrid/core/hybrid_v2.py`、`hybrid/scripts/run_hybrid_v2.py`、`hybrid/tests/test_hybrid_v2.py` | POC Gate/OOF validation/Common Coverage 不覆寫；沿用共同 coverage 作基準。 |
| Portfolio Risk V2 | 新增 `hybrid/config/risk_v2.json`、`hybrid/core/risk_v2.py`、`hybrid/scripts/run_hybrid_risk_v2.py`、`hybrid/tests/test_risk_v2.py` | 不改 `risk_v1.py`；先設計 shared cash、曝險、日界線及部分成交帳務。 |
| Experiment governance | 以 append-only 方式更新 `research/registries/experiment_registry.csv`、`dataset_registry.csv`；V2 狀態更新需另行授權/開始時處理 | 不重寫 exposure history，不改 `ml_data_policy` 以解鎖資料。 |
| Results | 新增 `exports/strategy_v2_<run_id>/` 與對應報告 | 不覆寫任何 V1/POC/Risk/歷史報告。 |

---

本文件本身不執行 V2。本輪未刻意查詢或分析 2025／2026 績效，但因開頭所述的狀態檔欄位接觸偏差，不能宣稱完全未接觸相關數值；該欄位未被本報告引用或用於判斷。對於是否曾被任何外部方式查看而 Repository 無法證明的保留區，均以 `UNKNOWN / CANNOT GUARANTEE` 處理。
