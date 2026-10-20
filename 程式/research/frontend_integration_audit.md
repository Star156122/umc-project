# Frontend Integration Audit

盤點日期：2026-10-19  
範圍：Repository 現有前端、面向前端的 API、研究 API 與 Strategy V2 凍結定義。此文件為唯讀盤點，不代表已完成端到端線上驗證。

## 結論

Repository 已有可操作的 Expo Web 前端與 Express/MySQL API。登入註冊、自選股、Yahoo 即時行情與歷史單股回測報告可由目前網站使用。正式凍結的 Strategy V2 尚未接入該網站：Express 只列出六種舊版策略，未提供 V2 的即時 Technical/ML/Hybrid/Risk 決策或 V2 回測 API。V2 的模型、決策、逐股與逐 Fold 資料目前是研究程式及 exports。

| 項目 | 現況 |
|---|---|
| 前端 | Expo SDK 54、React Native Web、Expo Router；網站介面可操作 |
| 正式前端 API | 已實作 Express REST API；登入後功能依賴 MySQL |
| 現況分析 | `NOT_IMPLEMENTED`；可看即時報價，但沒有凍結 V2 當日訊號 API |
| 回測分析 | `IMPLEMENTED_FOR_LEGACY_SINGLE_STOCK_STRATEGIES`；不是 Strategy V2 |
| K 線 | Yahoo Finance 當日 5 分鐘行情供首頁圖表；無研究 DB 歷史 K 線 API |
| 交易紀錄 | 已有舊版回測報告資料與交易明細呈現；V2 的 Gate/Risk 明細未接入網站 |
| LLM | 舊版回測可選擇產生報告分析；新聞標題摘要另有 Gemini；無 V2 專用說明 API |
| 新聞 | Google News RSS 個股標題，含日期、來源、連結；可摘要標題，沒有全文情緒模型 |

## 1. Repository 前端現況與檔案樹

`前端/` 是 Expo 網站/行動介面及 Express API；主要畫面在 `screens/`，Expo Router routes 在 `src/app/`。`程式/web/research.html` 是另一個研究工作台畫面，由 `程式/scripts/research.py` 的 Python HTTP server 提供，不是 Expo 網站的一部分。`程式/exports/` 的 HTML/CSV/JSON 是實驗報告與資料輸出，不能視為已整合的網站頁面。

```text
前端/
├─ src/app/                 Expo Router routes：login、register、首頁、分析、自選股、設定
├─ src/config/              API client
├─ src/components/          Expo Router/theme/web components
├─ screens/                 實際登入、行情、分析、自選股、設定畫面
├─ components/              行情圖、股票列、新聞、HTML 報告等畫面元件
├─ styles/                  主題與樣式
├─ assets/                  圖示與圖片
├─ server/src/              Express API、Yahoo、MySQL、回測、報告、新聞服務
├─ server/tests/            Express 整合測試
├─ server/database/         MySQL schema SQL
├─ server/python-backtest/  舊版保留 Python 回測程式（非目前 Express 整合目標）
├─ package.json             Expo scripts/dependencies
└─ README*.md               啟動、資料庫與 UI 說明

程式/
├─ trading_system/          回測與 Technical/LLM 核心程式
├─ hybrid/                  Hybrid、Risk、Strategy V2 研究核心與 runner
├─ configs/                 研究與凍結設定
├─ scripts/                 資料、模型與研究工具
├─ exports/                 研究 CSV/JSON/HTML 報告與預測資料
├─ research/                凍結、資料政策、實驗紀錄與本次盤點
├─ web/research.html        獨立研究工作台頁面
└─ scripts/research.py      研究工作台 Python server/API
```

完整可追溯檔案清單見 `frontend_repository_inventory.json`。清單排除 `node_modules`、虛擬環境與大型產出目錄；列出其資料夾及具代表性的報告/資料來源。

## 2. Frontend Framework

| 屬性 | Repository 證據 |
|---|---|
| Framework | React Native + Expo；Web 由 React Native Web 執行 |
| 版本 | Expo `~54.0.35`、React `19.1.0`、React Native `0.81.5`、React Native Web `~0.21.0`、Expo Router `~6.0.24` |
| Build/dev server | Expo CLI/Metro；`expo start --web` |
| Entry point | `expo-router/entry` (`前端/package.json` 的 `main`) |
| 網站啟動 | 在 `前端/` 執行 `npm install` 後 `npm run web`；也支援 `npm start` 後按 `w` |
| API 啟動 | `前端/server/` 執行 `npm start`；或根目錄 `啟動API.cmd` |
| API client | `前端/src/config/api.js`；附 JWT Bearer、API URL 環境變數 |
| 圖表 | `MarketTrendChart.js` 透過 iframe 與 CDN `lightweight-charts@4.2.1` |

`前端/App.js` 和 `screens/` 並非另一個獨立網站 entry；App 啟動採 Expo Router，Router routes 再匯入 `screens/*`。舊的 `components/TabBar.js` 等元件應視為需確認引用狀態的保留檔案，未在本次盤點中刪除或重新整理。

## 3. 目前頁面與狀態

| 頁面 | Route | 狀態 | 可操作功能與來源 |
|---|---|---|---|
| 登入 | `/login` | `IMPLEMENTED` | Email/password 登入、保存 JWT；`src/app/login.tsx`, `screens/AuthScreen.js` |
| 註冊 | `/register` | `IMPLEMENTED` | 建立帳號；`src/app/register.tsx`, `screens/RegisterScreen.js` |
| 首頁／行情 | `/(tabs)` | `IMPLEMENTED` | Yahoo 熱門/搜尋、報價、走勢、加入自選；`screens/HomeScreen.js` |
| 分析／回測 | `/(tabs)/analysis` | `IMPLEMENTED`（舊版策略） | 選股票與六種舊版策略、執行回測、看摘要/HTML/歷史報告、新聞與 LLM 卡片；`screens/AnalysisScreen.js` |
| 自選股 | `/(tabs)/watch` | `IMPLEMENTED` | MySQL 收藏與行情；`screens/WatchListScreen.js` |
| 設定 | `/(tabs)/setting` | `PARTIAL` | 帳號基本資訊、版本、登出、自選股連結；帳號安全、通知、偏好、隱私等列項沒有完整設定流程；`screens/SettingScreen.js` |
| Strategy V2 dashboard | 無 | `MISSING` | 沒有凍結候選的專屬頁面或 current signal route |
| V2 逐股/逐 Fold | 無 | `MISSING` | 研究輸出存在，但未在 Expo 頁面載入 |

`src/app/(tabs)/_layout.tsx` 會檢查 AsyncStorage token，未登入會導回 login。Settings 中若只是畫面文字或導航項目，不應當成已完成的通知或隱私功能。

## 4. 目前資料來源

| 資料 | 目前來源 | 分類 | 限制 |
|---|---|---|---|
| 股票代號、名稱 | Yahoo 查詢結果；名稱可由已知中文對照補顯示 | `REAL_BACKEND_DATA` + 少量名稱對照 | fallback 股票清單只用來向 Yahoo 查詢，不是價格 mock |
| 最新報價、OHLCV | Express `marketService` 轉接 Yahoo Finance | `REAL_BACKEND_DATA` | 有短暫快取；可能延遲，與研究資料庫不是同一資料合同 |
| 首頁走勢/當日 K 線 | Yahoo chart 查詢 `range=1d`, `interval=5m` | `REAL_BACKEND_DATA` | 不是 `market_data.sqlite3` 的完整研究歷史序列；網站端 CDN 圖表需可連網 |
| 六種舊策略回測 | Express 啟動 `程式/app_backtest.py`，報告存 MySQL | `REAL_BACKEND_DATA`（回測工作需本機程式、資料與 DB 可用） | API 欄位未提供使用者資金參數；日期可省略並使用 Python 設定預設值 |
| 舊策略最新訊號、指標與交易明細 | Python 回測 report JSON/HTML，再映射到 MySQL report tables | `REAL_BACKEND_DATA` | 屬於選定日期的歷史回測，不是今日預測 |
| V2 Technical、ML、Hybrid、Risk | `程式/exports/` 和 `research/` 產物 | `RESEARCH_EXPORT` | 不由 Expo Express API 提供；不得當成即時訊號或 OOS 結果 |
| 報告 LLM 分析 | 舊回測程式生成，寫入回測 JSON/HTML/資料庫報告 | `REAL_BACKEND_DATA`（啟用且憑證有效時） | 不等於凍結 Strategy V2 LLM；LLM 為解讀輸出，不應改買賣規則 |
| 股票新聞 | Google News RSS，server 過濾股票名稱/代號並 cache MySQL | `REAL_BACKEND_DATA` | 最多回傳標題、來源、日期、URL；搜尋索引不完整、非全文，也不代表回測當時已知 |
| 新聞摘要 | Gemini API 對標題/日期/來源摘要 | `REAL_BACKEND_DATA`（需金鑰與 Gemini model 設定） | 明確只是標題摘要，不是全文摘要、情緒分數或買賣訊號 |
| 舊研究工作台 | Python server 查 MySQL/SQLite 研究 run | `REAL_BACKEND_DATA` 或 export | 獨立於 Expo；`/api/ml/latest` 指向舊 `ml_baseline_20260927/results.json`，需以 run/date 顯示舊版標記 |

## 5. Backend / API 現況

### Expo 使用的 Express API

Base URL 由 `EXPO_PUBLIC_API_URL` 設定，預設本機 `http://localhost:3000`（Android emulator 使用 `10.0.2.2`）。`server.js` 使用 Express、MySQL；除 health、auth、market 外，主要使用者資料 route 需 JWT Bearer。回測使用 Python child process 串到 Repository 的 `程式/app_backtest.py`，並寫入 MySQL。

| Method / endpoint | Auth | Input | Output / 用途 |
|---|---|---|---|
| `GET /api/health` | 否 | — | API、MySQL、Python runner 狀態 |
| `POST /api/auth/register` | 否 | `username,email,password` | 建立帳號與 user 摘要 |
| `POST /api/auth/login` | 否 | `email,password` | JWT token 與 user |
| `GET /api/market/hot` | 否 | — | Yahoo 熱門股票、指數、報價、history、source/時間 |
| `GET /api/market/search?q=` | 否 | 搜尋字串 | Yahoo 搜尋股票及報價/history |
| `GET /api/favorites/codes` | 是 | — | 使用者收藏代號 |
| `GET /api/favorites` | 是 | — | 收藏股票及行情 |
| `POST /api/favorites` | 是 | `code,yahooSymbol` | 加入自選股 |
| `DELETE /api/favorites/:code` | 是 | 路徑股票代號 | 移除自選股 |
| `GET /api/backtests/strategies` | 是 | — | 策略目錄及可執行 ID：`ma,rsi,macd,bollinger,breakout,vote` |
| `POST /api/backtests/run` | 是 | `code,strategy,startDate?,endDate?` | 啟動舊回測，回傳報告、`analysisId`、儲存資訊 |
| `GET /api/reports` | 是 | `code?,strategy?` | 報告清單 |
| `GET /api/reports/latest/:code` | 是 | `strategy?` | 最新匹配報告 |
| `GET /api/reports/analysis/:analysisId` | 是 | analysis ID | 指定報告 JSON |
| `GET /api/reports/history/:code` | 是 | `strategy?` | 該股策略歷史報告 |
| `GET /api/reports/analysis/:analysisId/html` | 是 | analysis ID | HTML 報告本文 |
| `GET /api/reports/analysis/:analysisId/news/:mode` | 是 | `mode=latest\|historical` | 個股 RSS 新聞標題與來源資料 |
| `POST /api/reports/analysis/:analysisId/news/:mode/summary` | 是 | mode | Gemini 標題摘要 |
| `GET /api/reports/:code/:reportId` | 是 | 舊報告 ID | 相容舊版報告查詢 |

### 獨立研究工作台 API

`程式/scripts/research.py` 是 Python HTTP server，預設 port 8765，提供 `程式/web/research.html` 使用的 `/api/options`、`/api/research?run_id=`，另有 `/api/ml/latest`（固定讀舊 baseline 路徑）與 `/api/analysis/latest`。它不是 `前端/server/src/server.js`，也不是 Expo API client 的 endpoint。因 `/api/ml/latest` 路徑命名較新但資料檔是 `ml_baseline_20260927/results.json`，若要展示必須顯示其資料日期與研究版本，不能宣稱是目前凍結 V2 prediction。

Repository 存在真正可由 Expo 呼叫的 Backend API：`FRONTEND_API_STATUS = IMPLEMENTED`。但目前正式 V2 分析 API 缺失，故 `V2_INTEGRATION_API_STATUS = NOT_IMPLEMENTED`。

## 6. Technical Strategy 現況

`程式/trading_system/backtest.py` 和 `前端/server/src/pythonBacktestService.js` 可確認六種前端可選舊策略。Technical V2 frozen candidate 另見 `程式/research/strategy_v2_single_stock_freeze.md`、`strategy_v2_freeze_manifest.json` 及 `configs/technical_v2_candidate.json`。

| Strategy ID | Strategy Name | Type | Status | Current Used | Research Only | Frontend Selectable | Source |
|---|---|---|---|---|---|---|---|
| `ma` | MA 均線交叉 | 趨勢規則策略，不是單一指標 | 舊版 API 可執行 | No | 舊版策略研究/回測 | Yes (legacy) | `trading_system/backtest.py`; `server/src/pythonBacktestService.js` |
| `rsi` | RSI 動能 | 指標規則策略 | 舊版 API 可執行 | No | 舊版策略研究/回測 | Yes (legacy) | same |
| `macd` | MACD 趨勢轉折 | 指標規則策略 | 舊版 API 可執行 | No | 舊版策略研究/回測 | Yes (legacy) | same |
| `bollinger` | 布林通道反轉 | 指標規則策略 | 舊版 API 可執行 | No | 舊版策略研究/回測 | Yes (legacy) | same |
| `breakout` | 區間突破 | 突破規則策略；V2 是不同凍結 variant | 舊版 API 可執行 | Yes, `BR_SENS_RANGE55_BB20_2` + regime filter B | 舊版比較與 V2 exposed development | Yes (legacy variant only; not the V2 selector) | `configs/technical_v2_candidate.json`; `trading_system/technical_v1.py` |
| `vote` | 三指標多數決 | MA/RSI/MACD 融合規則，不是單一 indicator | 舊版 API 可執行 | No | 舊版策略研究/回測 | Yes (legacy) | `trading_system/backtest.py`; `server/src/pythonBacktestService.js` |
| `fixed` | 固定價格測試 | 流程測試 helper，非正式市場策略 | 內部 CLI 測試 | No | 流程驗證 | No | `trading_system/backtest.py` |

使用者所說的「六種」確實是六個舊版 Technical 回測選項；其中 MA/RSI/MACD/Bollinger/Breakout 是各自規則策略，`vote` 是多指標組合。除此之外 `fixed` 是流程測試 helper，不計入六個面向使用者策略。

**Strategy V2 沒有把六個策略混在一起。** 凍結版本只用 `BR_SENS_RANGE55_BB20_2` 突破 Candidate，加 `TECH_V2_B_MARKET_REGIME_FILTER`；設定的 entry/exit 為 range trigger/MA direction/BB upper 與 prior range low / Risk V1。它不是 MA+RSI+MACD+布林等六策略融合。V2 前端尚無選擇/顯示入口。

## 7. ML、Hybrid 與 Risk 現況

| Model | Training artifact/status | Prediction artifact | Current status | Used by frozen strategy |
|---|---|---|---|---|
| Random Forest | Training/WF/LOSO records in `exports/ml_v1_phase_b_20261012/`; this audit found no serialized deployable artifact for frozen ML V1 | `exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv` (`model=random_forest`) | Research OOF only; no inference API | **Yes, ML V1 RF OOF** |
| XGBoost | Training/WF/LOSO records in phase B reports; no frozen deployable artifact found | Same OOF CSV (`model=xgboost`) | Research comparison | No |
| GRU | Training/WF/LOSO records in phase B reports; no frozen deployable artifact found | Same OOF CSV (`model=gru`) | Research comparison | No |

凍結 gate 為 `BUY → ALLOW_LONG_ENTRY`，`HOLD/SELL → BLOCK`；SELL 不是放空。Frozen Strategy 僅採用 RF，XGBoost/GRU 仍屬研究比較，不可當成目前正式模型。三模型的研究 prediction 可由 OOF export 取得，**前端不能直接取得**，因 Express 沒有 ML prediction endpoint。

Hybrid 與 Risk V1 的輸出在 `程式/exports/hybrid_poc_v1_20261013T041540804059/`、common coverage、Strategy V2 technical qualification、Risk V1/Strategy V2 freeze exports 中可查；但沒有 Express endpoint 將 Technical Candidate、previous-day RF OOF、gate、risk sizing 與交易逐步決策送給 Expo。Risk V1 frozen 定義：每筆風險預算 1%、stop 2.5%、最大曝險資金比例 40%；單股 long-only，不是 portfolio。

## 8. Current Analysis 與 Historical Backtest

`CURRENT_ANALYSIS_STATUS = NOT_IMPLEMENTED`。首頁可提供 Yahoo 即時/延遲報價和當日行情圖，但未找到以正式凍結候選生成今日 Technical Candidate、Market Regime、ML 預測、Hybrid Allow/Block、Risk 建議部位的 Express route。研究 OOF 或 frozen summary 不是今日訊號。

`BACKTEST_ANALYSIS_STATUS = IMPLEMENTED_FOR_LEGACY_SINGLE_STOCK_STRATEGIES`。網站可送出舊版六種單股策略回測、讀取摘要/HTML/歷史報告；Strategy V2 Frozen Candidate 的對應正式回測頁與 API 尚未完成。V2 研究輸出期間是 2023–2024 exposed development，前端不得顯示為 Final Test、Final OOS 或未來保證績效。

## 9. K 線、回測與交易紀錄

首頁 `marketService.js` 向 Yahoo 請求 `range=1d, interval=5m`，history 含時間、OHLC、close、volume；`MarketTrendChart.js` 可以切換線圖與 K 線圖。這足以展示 Yahoo 當日行情。它不提供完整歷史研究 K 線、固定 universe 的 2023–2024 5 分 K，亦未連到 canonical SQLite research DB。展示 K 棒時標明來源和更新時間。

舊版回測 API 接受股票、六種策略與選填日期；未傳資金。執行資金由 `程式/backtest_config.json` 等 Python config 控制，預設 `initial_capital=100000`、`lots=1`、`capital_utilization=0.95`；V2 config 亦保留其研究本金/資金利用設定。故前端未來可把 100,000/200,000/500,000 當作「執行輸入」，但目前不能直接傳入並保證隔離於固定研究條件。需要把執行本金明確分離成 request-scoped execution override，保留 config hash、研究候選與回測結果身份，且不要改凍結策略參數。

舊回測 report/DB 有摘要指標與交易資料，包括訊號/成交、數量、價格、費用、交易稅、PnL、資產曲線等；前端可讀 report JSON/HTML。對 V2，研究 exports 另有 per-stock、per-fold、gate_decisions、risk_decisions、trades CSV/JSON，尚未提供前端 endpoint。建議將 `signal_time`、ML prediction date、entry/exit time、exit reason、position、gross/cost/net PnL 以明確欄位供 V2 UI 使用。

## 10. LLM 與新聞

LLM 已實作於舊 Python 回測：`trading_system/llm_analysis.py` 把回測 summary、signal、trade/PnL 等結構化報告內容組成分析 payload，提示模型以繁體中文說明，並將 JSON/HTML/summary 保存；由 `LLM_PROVIDER` 在 OpenAI/Gemini 間選擇，model 讀 `OPENAI_MODEL`，程式預設 `gpt-5.6-luna`。Express 執行回測時設 `LLM_PROVIDER=Gemini`，model 和實際 provider credentials 取本機環境設定；本審查未讀取 `.env` 機密，因此無法確認目前實際設定的 model 或憑證是否可用。此功能不是 V2 專用，也不應改寫訊號。

新聞已實作為 Google News RSS query；以個股代碼/名稱及日期範圍過濾，回傳最多 12 筆標題、發佈時間、來源與原文連結，快取 MySQL。`latest` 為最近約 30 天，`historical` 對應該回測日期。Gemini 摘要只看標題、日期及來源，沒有讀全文。未找到產業新聞專用查詢或 sentiment model；這些屬 `NOT_IMPLEMENTED`，不是已完成新聞分析。

## 11. Output Availability Matrix

`AVAILABLE_NOW` 表示由目前 Expo API/回測報告可供前端取得（受 DB、Python runner 與外部服務可用性影響）。`AVAILABLE_FROM_RESEARCH_EXPORT` 表示資料存在於研究產物，但尚無 Expo API。具體 API proposal 見 `frontend_data_contract_proposal.md`。

| 欄位群 | 狀態 | 備註 |
|---|---|---|
| Stock Code / Name | `AVAILABLE_NOW` | Yahoo response、stock name mapping |
| Current OHLCV / Latest Price | `AVAILABLE_NOW` | Yahoo，非研究 SQLite；可能延遲 |
| K-line timestamp/OHLCV | `PARTIAL` | 只有 Yahoo 當日 5 分鐘 history；完整歷史/研究 K 缺 API |
| V2 Strategy Name / Market Regime / Technical Candidate / Signal Time / Breakout Level / indicators | `AVAILABLE_FROM_RESEARCH_EXPORT` | freeze/qualification/technical signals exports；非 current endpoint |
| V2 RF Model / BUY-HOLD-SELL / Prediction Date / Feature Date / Probability | `AVAILABLE_FROM_RESEARCH_EXPORT` | OOF CSV 有研究預測；無前端 API；勿稱 current prediction |
| XGBoost/GRU Prediction | `AVAILABLE_FROM_RESEARCH_EXPORT` | OOF 比較有資料，但不是 frozen model；無前端 API |
| V2 Hybrid result / gate decision | `AVAILABLE_FROM_RESEARCH_EXPORT` | POC/common coverage/batch exports |
| V2 Risk capital/risk/stop/max/suggested quantity | `AVAILABLE_FROM_RESEARCH_EXPORT` | Risk decisions/existing engine outputs；未接前端 |
| Legacy backtest average return, MDD, win rate, PF, Sharpe, trade count, PnL/cost/final equity | `AVAILABLE_NOW` | 舊報告 mapper/summary 與 Analysis UI 有對應欄位；定義依 legacy report |
| Legacy backtest median return | `NOT_IMPLEMENTED` in normalized report/UI | 未找到通用 mapper/UI 欄位；不能因單筆 returns 存在就視為已計算 median |
| V2 backtest metrics | `AVAILABLE_FROM_RESEARCH_EXPORT` | V2 freeze/experiments 有 summary/per-stock/per-fold；無 Expo API |
| Per-stock five-stock aggregate | `AVAILABLE_FROM_RESEARCH_EXPORT` | CSV exports；沒有前端 endpoint |
| Per-fold WF1-WF3 | `AVAILABLE_FROM_RESEARCH_EXPORT` | freeze/experiment exports；沒有前端 endpoint |
| Legacy trade log | `AVAILABLE_NOW` | saved report/HTML/DB record 有可顯示交易資訊，欄位隨歷史報告版本而異 |
| V2 trade/gate/risk audit log | `AVAILABLE_FROM_RESEARCH_EXPORT` | CSV；未接前端 |
| Legacy LLM analysis | `PARTIAL` | report JSON/HTML 有時包含；須啟用 Gemini/OpenAI 且 model/key 有效 |
| Stock news title/source/date/URL | `AVAILABLE_NOW` | Google News RSS，需 analysis report ID |
| News headline summary | `PARTIAL` | Gemini，標題級摘要；需設定有效 Gemini model/key |
| Industry news / sentiment | `NOT_IMPLEMENTED` | 沒有專用 provider/API/model |

## 12. 建議前端資訊架構

建議單一完整資訊頁，以 `Stock Selector` 開始；上方清楚分「目前行情/目前策略分析」與「歷史回測/研究」時間標籤。現階段只有行情已就緒，凍結 V2 Current Status 應顯示 `NOT READY`。

完整頁面順序可為：股票選擇與資料來源 → Current Strategy Status（未接 API 前顯示未提供）→ K 線（注明 Yahoo 來源/延遲）→ Technical Candidate/Market Regime → ML 模型與預測日期/機率 → Hybrid Allow/Block → Risk 建議部位 → LLM 解釋 → News → Backtest summary → Per Stock/Per Fold → Trade Log。技術細節放 expandable sections，術語使用 tooltip。Research exports 必須顯示 `DEVELOPMENT BACKTEST, 2023-2024`，不得顯示成 Current Analysis 或 Final OOS。

### Tooltip 詞彙需求

K Line、Breakout、Market Regime、Technical Candidate、Random Forest、XGBoost、GRU、Hybrid、Risk per Trade、Position Size、Stop Loss、Average Return、Maximum Drawdown、Profit Factor、Sharpe、Win Rate、Trade Count、Net PnL、Final OOS。

## 13. Gap / 優先工作順序

完整 Feature × Status × existing file × backend/frontend readiness 表見 `frontend_gap_analysis.md`。

- **P0：** 定義並提供 V2 current/backtest 共用資料合同與 provenance；在 API 區分 current quote、development backtest、Final OOS；固定 route 能回傳凍結候選/版本/資料日期與錯誤狀態；明確標記 current signal not ready 直至計算管線存在。
- **P1：** 以 V2 freeze snapshot 的逐股、逐 Fold、交易明細、gate/risk decisions 建立唯讀報告 API 與 UI；整理單股回測指標名稱/單位；分開 market Yahoo candles 與研究 5 分 K。
- **P2：** 讓 3 模型研究 OOF 可篩選展示（明確標研究用途）；加入 V2 LLM structured explanation，限定只能解釋結果、不可產生或改寫訊號；將使用者輸入資金作 request-scoped execution input。
- **P3：** 產業新聞、sentiment、portfolio、多股票共用資金/曝險等之後再規劃；目前無實作且 portfolio 被凍結規範禁止。

## 14. Research Label / Safety

現行 freeze 是 `FROZEN_DEVELOPMENT_CANDIDATE`，不是獨立驗證。2023–2024 exposed；2025 H1 exposed；2025 H2 unknown；2026 H1 exposed；2026-07 後部分資料 exposed。Repository 目前無已證明乾淨的 Final OOS 區間。網站應將資料期間、資料角色、freeze ID、候選 ID、產生時間和來源一併呈現；不使用「Final Test」「Future Performance」「Guaranteed Return」等用語。Overall 多股彙整不能命名為 Portfolio Return；SELL 不得顯示為 Short Signal。

## 15. 本次限制與驗證

本次僅讀取 Repository 原始碼、設定、freeze/研究文件與既有輸出清單，未啟動 API/前端、未連線 Yahoo/MySQL、未讀取 `.env` 機密，也未執行模型/策略/回測。文件指出的是 Repository 可證明的實作，不等於外部服務當下可用性測試。僅建立四份 audit/documentation 文件；程式、策略、資料庫與實驗結果沒有修改。
