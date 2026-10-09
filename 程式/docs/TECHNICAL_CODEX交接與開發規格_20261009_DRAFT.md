# Technical Strategy Codex 交接與開發規格（草稿）

- 文件日期：2026-10-09
- 狀態：**DRAFT／需組內確認後再交給 Codex 執行**
- 負責範圍：Technical Strategy Development、Technical Walk-forward、Robustness Test、Technical V1 Freeze
- 本文件不授權：ML 修改、Hybrid 實作、正式 Holdout／Final OOS、LLM、前端擴充

---

## 可直接交給 Codex 的任務說明

你現在負責本畢業專題的 **Technical Strategy** 部分。請先完整閱讀專案與本文件，再進行任何修改。

專題目前的正式研究方向是：

```text
Technical Indicators
→ 產生盤中 Candidate Entry Signal

Machine Learning
→ 判斷 Technical Candidate 是否值得放行

Technical V1 + ML V1
→ Hybrid Strategy V1
→ Hybrid Backtest
```

Technical 的任務不是建立一套與 ML 無關的最終系統，也不是追求 Training 期間最高報酬。Technical 必須產生合理、穩定、可解釋且數量足夠的候選進場訊號，供後續 ML 作為第二層 Filter。

### 你的第一個回合只能做什麼

第一個回合請先：

1. 閱讀專案規範、核心程式、設定、測試及目前輸出。
2. 確認資料庫、股票、K 棒頻率、交易時間與成本設定。
3. 檢查現有程式能否支援本文件定義的三個 Strategy Family。
4. 列出預計新增／修改的檔案與原因。
5. 提出 Technical V1 開發方案與少量候選參數。

第一個回合禁止：

- 直接開始大量回測。
- 大量掃描參數或排列全部指標組合。
- 修改 ML、Hybrid、前端或 LLM。
- 讀取 Additional Holdout 或 Final OOS。
- 覆寫既有歷史設定與研究輸出。

完成盤點後先回報，等待使用者確認，再進入實作。

---

## 一、開始前必讀

請依序閱讀：

1. `AGENTS.md`
2. `程式/docs/長期研究規範.md`
3. `程式/research/current/hybrid_research_plan.md`
4. `程式/cleanup_manifest.md`
5. `程式/exports/README.md`
6. `程式/configs/technical_generalization_20261006.json`
7. `程式/trading_system/backtest.py`
8. `程式/trading_system/technical_generalization.py`
9. `程式/scripts/technical_generalization.py`
10. `程式/tests/test_technical_generalization.py`
11. `程式/trading_system/research_guard.py`

若文件與程式不一致，以資料鎖與防洩漏規則較嚴格者優先，並把不一致列入回報，不要自行繞過。

---

## 二、目前專案實際狀態

### 已有能力

- 5 分 K 技術指標與回測核心。
- MA、RSI、MACD、Bollinger Bands、Range Breakout、Majority Vote。
- 交易成本、動態資金部位、Swing 持有、停損與冷卻規則。
- Technical 跨股票與時間順序 Walk-forward 工作流。
- 參數 fingerprint、共同參數檢查、最低交易樣本與單一股票獲利集中度檢查。
- 日期鎖與禁止期間防線。

### 目前尚未完成

- 新大綱的三個 Strategy Family 尚未形成正式 Technical V1 設定。
- 現有 `technical_generalization_20261006.json` 主要比較 Legacy、單一 MA／RSI／MACD與 Majority Vote，尚未完整包含新的 Trend-Momentum 與 Breakout Family。
- 尚未產生供 ML 使用的正式 `Technical BUY Candidate` 訊號介面。
- 尚未 Freeze Technical V1 的 Entry、Exit、參數、決策時間、持有與資金規則。

### 現有稽核注意事項

現有 `technical_generalization_audit.json` 曾回報 `BLOCKED_MISSING_DATA`，理由是舊執行紀錄指向另一台電腦的絕對資料庫路徑。目前設定檔已寫成：

```text
data/market_data.sqlite3
```

不要直接假設問題已解決。請先做唯讀 preflight，確認目前工作目錄解析後確實指向專案內的正式資料庫，並確認需要的股票及期間具有足夠 5 分 K。禁止自動下載禁用期間資料。

---

## 三、資料角色與禁止期間

| 角色 | 日期 | Technical 可否使用 | 用途 |
|---|---|---:|---|
| Training | 2023-01-01～2024-12-31 | 可以 | Strategy Family 開發、時間序列 Walk-forward、少量參數敏感度 |
| Validation | 2025-01-01～2025-06-30 | 候選凍結後才使用 | Controlled Validation；不可每次小修改都查看 |
| Development Seen | 2026-01-01～2026-06-30 | 只作歷史診斷 | 已看過，不得自動搜尋最佳參數 |
| Additional Holdout | 2025-07-01～2025-12-31 | **禁止** | 不得下載、讀取、回測或產生績效 |
| Final OOS | 2026-07-01～最新 | **禁止** | 只有未來取得正式授權且全部 Freeze 後才可使用 |

所有入口都必須通過 `trading_system/research_guard.py`。一般「全部跑、再測一次、產生 HTML」不代表解除禁止期間。

### Walk-forward

目前 Technical 設定使用三個 Expanding-window Fold：

1. 2023 H1 訓練 → 2023 H2 評估
2. 2023 全年訓練 → 2024 H1 評估
3. 2023～2024 H1 訓練 → 2024 H2 評估

若要修改 Fold，必須先說明資料量與交易樣本理由，並保證時間順序；禁止 Random Shuffle。

---

## 四、股票集合需先確認

目前 Technical 泛化設定預先登記五檔科技股：

```text
2303 聯電
2330 台積電
2454 聯發科
2317 鴻海
2382 廣達
```

現有 ML 研究曾使用其他六檔與科技／金融產業集合，兩邊股票集合目前並未完全一致。

在建立 Technical V1 前，請先回報這個不一致，提出以下其中一種方案供使用者決定：

1. Technical 先以五檔科技股完成開發，Hybrid 前再建立共同股票交集。
2. Technical 與 ML 先統一成相同 Stock Universe，再進行 Technical V1。

禁止自行更換股票後直接回測，也不能依個股設定不同參數。

---

## 五、Technical V1 的三個 Strategy Family

只研究以下三組。不要把所有指標組合排列後選最高報酬。

### A. Trend-Momentum Strategy

#### 交易假設

價格處於可辨識的上升趨勢，而且 MACD 顯示趨勢仍具有動能；RSI 用來避免在過熱區域追高。

#### 指標角色

- **MA**：趨勢方向與結構。
- **MACD**：動能確認。
- **RSI**：進場品質／過熱 Filter。

#### Candidate Entry 概念

以下條件應在已完成的 5 分 K 上確認：

1. MA 結構與長期趨勢方向支持做多。
2. MACD 顯示正向動能或多方確認。
3. RSI 位於合理動能區間，不能只是極端超買追價。
4. 全部條件成立時產生 `Technical BUY Candidate`。
5. 使用下一個可交易 K 棒開盤價模擬成交，禁止使用產生訊號 K 棒尚未完成的價格。

#### 第一輪參數原則

- 以現有 `strategy_templates.json`、`strategy_templates_v2_candidate.json` 與泛化設定作為 Baseline 來源。
- 只允許少量、事前登記的鄰近參數，例如 MA 週期附近一組、RSI 區間附近一組。
- 每一個變體必須說明解決的交易問題。
- 不得以完整 Grid Search 尋找最高 Return。

### B. Breakout Strategy

#### 交易假設

價格突破近期整理區間，方向與長期趨勢一致，而且波動／價格位置支持真正突破，而不是盤整中的假訊號。

#### 指標角色

- **Range Breakout**：唯一主要 Entry Trigger。
- **MA**：確認突破方向與大方向一致。
- **Bollinger Bands**：描述波動環境或價格位置。
- **Volume Confirmation**：只有在既有欄位完整且有明確假突破假設時才可納入，第一輪不強制增加。

#### Candidate Entry 概念

1. 使用已完成 K 棒判斷是否突破先前區間，不得把當根未來高點放入突破基準。
2. MA 趨勢支持做多。
3. Bollinger 僅採「突破／波動環境」邏輯。
4. 不得同時混入「跌到下軌買進」的均值回歸邏輯。
5. 條件成立後產生 `Technical BUY Candidate`，下一個可交易 K 棒才允許成交。

### C. Majority Vote Baseline

#### 目的

保留一個簡單且可解釋的對照，判斷角色式 Strategy Family 是否真的優於一般多數決。

#### 規則

- MA、RSI、MACD 各自給出是否支持 BUY。
- Baseline 優先使用 `2 of 3`。
- `3 of 3` 只能作少量敏感度對照，不可延伸成大量門檻搜尋。
- 投票策略仍必須遵守完成 K 棒與下一根 K 棒成交規則。

---

## 六、Exit 與 Risk Management

Technical V1 負責 Entry Candidate，也必須提供一致且可回測的 Exit／Risk Rule，因第一版 Hybrid 的 ML 只過濾 Entry，不控制 Exit。

第一輪應從既有核心選定並固定：

- Technical Trend Breakdown／Strategy Exit
- Stop Loss
- Take Profit（若不使用需明確記錄為 `null` 或 disabled）
- Maximum Holding Period
- Minimum Holding Bars
- Cooldown Bars
- Maximum Entries per Day
- Position Sizing／Capital Utilization
- Commission、Tax、Day-trade Tax、Slippage

不要同時加入多個新 Exit 技巧來救 Entry。若策略毛損益已經是負值，優先檢查進場邏輯；若毛損益為正但淨損益為負，再研究交易頻率與成本。

---

## 七、參數研究規則

### 可以做

- 少量有理論理由的參數鄰近比較。
- 每輪只改一個主要因素。
- 檢查相鄰參數是否有相似方向。
- 保留失敗結果與原始 Baseline。

### 禁止

- 暴力排列 MA + RSI、MA + MACD、MA + Bollinger 等所有組合。
- 大量 Grid／Random／Bayesian Search 後挑最高報酬。
- 為每檔股票各自設定最佳參數。
- 看到 Validation 或 Development Seen 結果後反覆調參。
- 只因勝率或單一股票報酬高就選 Candidate。

### Parameter Spike 判定

若某組參數明顯優於相鄰參數，但相鄰組合立即失效，應標記 `POSSIBLE_OVERFIT`，不能直接成為 Technical V1。

---

## 八、固定回測條件

除非使用者另行核准，第一輪沿用目前 Technical 泛化設定：

- K 棒：5 分 K
- 初始本金：100,000 元
- 部位：cash fraction，資金使用率 95%
- 單一共用參數套用所有股票
- 持有模式：Swing
- 手續費率：0.001425
- 股票交易稅：0.003
- 當沖稅：0.0015（是否套用依實際同日進出）
- 滑價：0；若日後加入需另立敏感度測試
- 同一時間每檔僅允許符合核心回測設定的部位數

執行前必須核對 `AppConfig` 實際欄位與現有回測行為，不能只照文件猜測。

---

## 九、評估指標與選擇原則

每個 Strategy Family、Fold 與股票至少輸出：

### 交易層

- Total Return
- Gross PnL
- Net PnL
- Transaction Cost
- Win Rate
- Profit Factor
- Maximum Drawdown
- Sharpe Ratio
- Trade Count
- Average Profit／Trade
- Average Win
- Average Loss
- Payoff Ratio
- Final Equity

### 穩定性層

- Walk-forward Mean
- Walk-forward Median
- Walk-forward Standard Deviation
- Worst Fold
- Profitable Stocks／Total Stocks
- 每檔股票 Return、PF、MDD、Trade Count
- Cross-stock Return Standard Deviation
- Leave-one-stock-out 後是否仍穩定
- 單一股票獲利集中度
- 鄰近參數敏感度
- Forward Return after Signal

### 最低樣本提醒

- 每個 stock-fold 少於 5 筆交易：標記 `INSUFFICIENT`。
- 每檔 Training 合計少於 10 筆：不可據此宣稱穩定。
- 全部 Training 少於 30 筆：不可直接 Freeze。

### Candidate 選擇

不能只看最高 Return。必須同時考慮：

1. 平均結果
2. 中位數
3. Worst Fold
4. Fold Std
5. 跨股票方向
6. 交易樣本數
7. 回撤與成本
8. 參數鄰近穩定性
9. 是否依賴單一股票

---

## 十、Technical 與 ML 的介面契約

Technical 最終不能只輸出總績效，必須輸出可供 ML／Hybrid 對齊的 Candidate Signal 明細。

建議每筆 Candidate 至少包含：

```text
technical_version
strategy_family
candidate_id
parameter_fingerprint
stock_code
data_role
fold_id
signal_time
signal_bar_end_time
earliest_entry_time
technical_signal = BUY_CANDIDATE
entry_rule_id
exit_rule_id
close_at_signal
```

要求：

- `signal_time` 只能使用當時已完成資料。
- `earliest_entry_time` 必須晚於訊號確認時間。
- 不要在 Candidate 檔中混入未來報酬作為即時可用欄位。
- 若另附 forward return，只能放在研究評估區並清楚標示 `evaluation_only`。
- ML 之後依 `stock_code + decision time` 對齊，不應重新推測 Technical 訊號。

---

## 十一、建議的檔案安排

不要覆寫既有歷史設定或輸出。盤點後可優先提出以下新檔案，但正式名稱須先讓使用者確認：

```text
configs/technical_v1_research.json
trading_system/technical_v1.py
scripts/run_technical_v1.py
tests/test_technical_v1.py
exports/technical_v1_<date>/
```

預期輸出：

```text
technical_v1_audit.json
technical_v1_results.json
technical_v1_summary.csv
technical_v1_signals.csv
technical_v1_report.html
technical_v1_freeze_manifest.json   # 只有正式 Freeze 時才建立
```

若現有 `technical_generalization.py` 已可乾淨擴充，可以重用核心函式；不要複製整套回測邏輯造成兩份實作分歧。

---

## 十二、必要測試

實作後至少新增或確認：

1. 禁用期間被拒絕。
2. Walk-forward 時間順序正確。
3. 相同 Candidate 在所有股票使用相同參數 fingerprint。
4. 指標只使用訊號時間以前資料。
5. Range Breakout 不包含當根未完成資料。
6. 訊號在完成 K 棒確認，成交不早於下一個可交易 K 棒。
7. 交易成本、稅與資金規則確實套用。
8. 每個 Fold、股票與 Strategy Family 都輸出樣本數。
9. 不足樣本會標記 `INSUFFICIENT`。
10. Candidate Signal schema 可供 ML Join。
11. 不會覆寫歷史 exports。
12. 未獲授權時不建立或讀取 Holdout／Final OOS 結果。

測試應使用小型 fixture／合成資料，禁止為了測試而啟動完整模型或大型回測。

---

## 十三、Technical V1 Freeze 條件

只有同時滿足以下條件，才能提出 Freeze：

- Entry／Exit 與各指標角色已明確記錄。
- 指標與參數已固定。
- Signal Decision Time 與 Next-bar Execution 已固定。
- 風控、最大持有期、成本與資金規則已固定。
- Training Walk-forward 完成。
- Worst Fold 與 Std 沒有顯示嚴重不穩定。
- 跨股票不是只靠單一股票獲利。
- 交易樣本數足夠。
- 鄰近參數沒有明顯孤立尖峰。
- Candidate Signal 明細已輸出並通過防洩漏測試。
- 使用者已審查並明確同意 Freeze。

Freeze 後不得因 Hybrid 表現不好，再回頭利用相同資料任意調 Technical。

---

## 十四、什麼時候可以交給 ML／進入 Hybrid

Technical 團隊交付以下內容後，ML 才能開始正式對齊：

1. Technical V1 Freeze Manifest
2. 固定 Stock Universe
3. 固定 Decision Time 與 Earliest Entry Time
4. 固定持有／Exit／成本規則
5. Candidate Signal CSV／JSON
6. Training Walk-forward 與跨股票報告
7. 參數 fingerprint
8. 防洩漏與禁用期間測試結果

Technical V1 與 ML V1 任一未 Freeze，或股票、時間、Prediction Horizon 尚未對齊，就不能開始正式 Hybrid 比較。

---

## 十五、每次回報格式

請使用以下格式回報使用者：

```text
【目前階段】
盤點／實作／Training Walk-forward／Candidate Review／Freeze

【使用資料】
列出角色、日期、股票；明確確認 forbidden_roles_used = false

【本次單一主要修改】
只寫一項主要變更及交易理由

【修改檔案】
完整路徑與用途

【驗證結果】
測試、資料品質、時間順序與防洩漏

【研究結果】
Mean、Median、Std、Worst Fold、股票穩定性、樣本數、成本與回撤

【是否可進下一階段】
PASS／FAIL／INSUFFICIENT／REVIEW_REQUIRED

【需要使用者決定】
例如 Stock Universe、候選規則或是否 Freeze
```

---

## 十六、本輪停止點

本輪 Technical 工作完成到：

```text
Technical Strategy Families
→ Training Walk-forward
→ Robustness / Sensitivity
→ Candidate Review
→ Technical V1 Freeze
→ Candidate Signal 交付 ML
```

不要自行延伸到：

- ML 訓練或修改
- Hybrid 回測
- LLM 分析
- 前端功能
- Additional Holdout
- Final OOS

完成 Technical V1 交付後停止，等待 ML V1 與兩邊時間軸對齊。
