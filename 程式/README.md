# 股票交易策略回測系統

> **目前正式研究方向（2026-10）**：先以 Technical Strategy V1 產生可稽核的 `BUY_CANDIDATE`，再由另行凍結且股票集合一致的 ML V1 擔任 Filter，最後才進入 Hybrid Strategy。Technical V1 本輪只允許 `2023-01-01～2024-12-31` Training 三個 Walk-Forward fold；Validation、Additional Holdout、Development Seen 與 Final Out-of-Sample 均不在本輪執行範圍。使用入口為 `scripts/run_technical_v1.py`，研究設定為 `configs/technical_v1_research.json`。

> 下方聯電單股六策略、LLM 與既有報表流程保留為歷史研究及操作功能，不代表目前正式候選，也不得用來繞過 `docs/長期研究規範.md` 的資料角色與日期鎖。Technical V1 使用聯電、台積電、聯發科、鴻海、廣達五檔廣義科技股，所有股票共用參數、成本與風險控制。

本專案是一套以 Python 建立的聯電（2303）策略回測系統，可從永豐 Shioaji 或 TSST 取得歷史 tick 資料，先快取成 SQLite，再聚合為 5 分 K 進行訊號判斷、模擬成交、計算損益，最後輸出 CSV、JSON 與 HTML 報表。系統支援 MA、RSI、MACD、布林通道反轉、區間突破與三指標多數決六種比較策略，也保留固定價格策略作為流程測試用途。

> 2026-09-11 改善版：回測日期仍固定為 `2026-01-01～2026-06-30`。修正固定一張造成資金不足、每筆 tick 重算導致耗時過久、趨勢策略被每日平倉切斷，以及當沖／跨日稅率混用等問題。結果屬於同期間參數研究，不是未來獲利保證。

### 改善版完整期間結果

| 策略 | 總報酬率 | 完成交易 | 勝率 | Profit Factor | 最大回撤 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 三指標多數決 | +75.10% | 36 | 47.22% | 3.36 | 9.84% | 通過初步篩選 |
| 區間突破 | +63.76% | 22 | 59.09% | 4.43 | 11.20% | 通過初步篩選 |
| MA | +56.86% | 10 | 50.00% | 7.74 | 10.03% | 通過初步篩選 |
| MACD | +51.89% | 27 | 40.74% | 2.33 | 11.29% | 通過初步篩選 |
| RSI | +19.40% | 53 | 24.53% | 1.50 | 13.50% | 通過初步篩選 |
| 布林通道 | -0.54% | 7 | 57.14% | 0.88 | 3.62% | 未通過，僅保留對照 |

同期間買進持有基準約為 `+114.35%`，六個策略皆未超越基準；正報酬只代表本次改善有效降低原版問題，不能解讀成策略已具備樣本外優勢。完整解讀見 `docs/reports/策略改善與回測結果_20260911.md`。

## 1. 專案結構

```text
.
├── main.py                    # 主要回測執行入口
├── main02.py                  # 舊指令相容入口
├── trading_system/            # 回測核心與 LLM 分析模組
├── scripts/                   # 批次、資料更新、報表補寫與資料庫匯入工具
├── run_batch.py               # 批次工具的相容入口
├── update_data.py             # 資料更新工具的相容入口
├── backtest_config.json       # 聯電 2303 六策略共同設定
├── data/                      # 執行後自動建立 SQLite 資料庫
├── resources/news/            # 選配的新聞／LLM 摘要匯入範例
├── tests/                     # 策略規則測試
├── docs/                      # 指南、研究報告與交接文件
├── database/                  # MariaDB 結構、升級腳本、ERD 與匯出檔
├── reports/                   # 每次回測的輸出檔案
├── logs/                      # TSST 與 broker 執行紀錄
├── pyproject.toml             # Python 版本與套件依賴
└── uv.lock                    # uv 鎖定檔，協助重現套件版本
```

## 2. 執行環境

- Python：`3.12` 以上
- 套件管理：建議使用 `uv`
- 主要套件：`shioaji`、`tsst`、`polars`、`polars-talib`、`python-dotenv`

安裝依賴：

```powershell
uv sync
```

如果沒有使用 `uv`，也可以建立虛擬環境後安裝專案依賴：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## 3. 環境變數設定

程式會從專案根目錄的 `.env` 讀取帳號與 API key。股票代號、回測日期與策略版本可以寫在 `backtest_config.json`，不需要把策略參數寫進 `.env`。請勿將真實金鑰公開或放入報告本文。

最小設定範例：

```env
API_KEY=你的永豐 API KEY
API_SECRET=你的永豐 API SECRET

BACKTEST_START=2026-01-01
BACKTEST_END=2026-06-30
```

若使用 TSST 資料來源，需補上：

```env
EMAIL=你的 TSST 帳號
TSST_TOKEN=你的 TSST TOKEN
```

若要連接非純回測模式，才需要憑證檔相關設定：

```env
CA_PATH=憑證檔路徑
CA_PASSWORD=憑證密碼
ALLOW_REAL_TRADING=false
```

系統預設為回測與模擬模式，且會阻擋真實下單；除非非常確定用途，否則不要開啟 `ALLOW_REAL_TRADING=true` 或 `--allow-real-trading`。

## 4. 不用 PowerShell 的執行方式

平常測股票或策略時，優先使用 `backtest_config.json`。目前研究對象固定為聯電（2303）：

1. 開啟 `backtest_config.json`。
2. 單次回測時，修改最上方的 `active_profile`，可選擇 `2303_ma`、`2303_rsi`、`2303_macd`、`2303_bollinger`、`2303_breakout` 或 `2303_vote`。
3. 要一次跑完六種策略時，使用 `batch_profiles` 清單。
4. 在 VS Code 左側選「執行與偵錯」。
5. 只跑一檔選 `Run main02.py`；一次跑多檔選 `Run batch profiles`。
6. 按綠色執行按鈕，或直接按 `F5`。
7. 到 `reports/` 查看新產生的報表資料夾。

`Run main02.py` 只會輸出聯電的一種策略；`Run batch profiles` 會依照 `batch_profiles` 逐一執行聯電的六種策略。

預設批次清單：

```text
2303_ma
2303_rsi
2303_macd
2303_bollinger
2303_breakout
2303_vote
```

目前設定檔已放好聯電的六種策略 profile：

```text
2303_ma / 2303_rsi / 2303_macd / 2303_bollinger / 2303_breakout / 2303_vote
```

如果要新增股票，可以在 `profiles` 裡複製一組設定，修改 `code`、`run_name` 與策略參數；如果要讓它加入批次回測，再把 profile 名稱放進 `batch_profiles`。

每個 profile 都可以設定自己的股票與策略參數，例如：

- `2303_ma`：聯電 `2303` 使用 MA 均線策略
- `2303_rsi`：聯電 `2303` 使用 RSI 策略
- `2303_macd`：聯電 `2303` 使用 MACD 策略
- `2303_bollinger`：聯電 `2303` 使用布林通道反轉策略
- `2303_breakout`：聯電 `2303` 使用區間突破策略
- `2303_vote`：聯電 `2303` 使用 MA、RSI、MACD 三選二多數決策略

## 5. 可重現操作流程

1. 確認 Python 版本與依賴已安裝。
2. 在 `.env` 填入資料來源所需帳號與 API key。
3. 固定回測日期、股票代號、策略與參數。
4. 使用 VS Code 執行 `Run main02.py`，或用 PowerShell 執行 `main02.py`。
5. 到 `reports/` 檢查本次產生的 CSV、JSON 與 HTML 報表。
6. 報告中保留執行指令、參數與輸出檔名，即可重現同一套回測設定。

### OpenAI LLM 分析（選配）

LLM 預設關閉，所以不會自行呼叫 API 或產生費用。要啟用時，在 `.env` 設定：

```env
LLM_ENABLED=true
LLM_PROVIDER=OpenAI
OPENAI_API_KEY=你的 OpenAI API Key
OPENAI_MODEL=gpt-5.6-luna
LLM_MAX_OUTPUT_TOKENS=1400
LLM_CACHE_PATH=data/llm_cache
LLM_USE_CACHE=true
```

若改用 Gemini，可設定 `LLM_PROVIDER=Gemini`、`GEMINI_API_KEY=你的 Gemini API Key`，並將 `OPENAI_MODEL` 設為可用的 Gemini 模型（例如 `gemini-3.6-flash`）。兩種供應商都只解讀回測結果，不修改交易訊號或績效數字。

新回測完成後會多產生 `llm_report.json`，並把繁體中文分析加入 `report.html` 與 `summary.json`；若同時開啟資料庫上傳，也會隨 `reports.report_content` 一起保存。新聞內容只會作為不可信的引用資料，模型不得遵循其中的指令。

既有正式報告不需要重跑回測，可使用：

```powershell
uv run python enrich_existing_reports.py reports/2303
```

若還要同步更新已上傳的資料庫報告，確認 `.env` 的資料庫設定正確後加上 `--sync-database`。這個命令會對尚未產生 `llm_report.json` 的正式報告各呼叫一次 API；重複執行預設會略過，只有明確加上 `--force` 才會重新產生。

範例：使用永豐 tick 資料，回測聯電 `2303` 在指定期間的均線策略：

```powershell
uv run python main02.py `
  --strategy ma `
  --tick-source sinopac `
  --run-name 2303_ma `
  --code 2303 `
  --backtest-start 2026-01-01 `
  --backtest-end 2026-06-30
```

若已啟用虛擬環境，也可以執行：

```powershell
python main02.py --strategy ma --tick-source sinopac --code 2303 --backtest-start 2026-01-01 --backtest-end 2026-06-30
```

## 6. 系統流程

```text
讀取 .env 與命令列參數
        ↓
驗證日期、策略參數、資料來源與安全設定
        ↓
建立 TSST 策略物件與本地回測報價物件
        ↓
先查詢本地 SQLite；缺少的日期才從 Shioaji 取得 tick 並寫回資料庫
        ↓
讀取／建立原始 5 分 K 快取（避免六個策略重複掃描數百萬筆 tick）
        ↓
計算 MA、RSI、MACD、布林通道與突破區間
        ↓
用已完成 K 棒判斷訊號，以下一根 K 棒開盤價模擬成交
        ↓
依可用現金動態計算零股股數，模擬成交並扣除手續費與適用稅率
        ↓
輸出 trades.csv、signals.csv、pnl.csv、summary.json 與 report.html
        ↓
若啟用 LLM：OpenAI 依結構化回測數據產生分析，寫入 llm_report.json、HTML 與資料庫報告內容
```

## 7. 策略說明

六種正式策略皆為「只做多、跨日波段」，不做放空。訊號只使用當時已完成的 K 棒，訂單在下一根 K 棒開盤價成交，避免使用未來資料。若回測結束仍有持股，才在最後一根 K 棒強制平倉。

| 策略 | 類型 | 較適合觀察的行情 | 核心目的 |
| --- | --- | --- | --- |
| MA | 趨勢追蹤 | 中長期上升趨勢形成 | 20／60 黃金交叉配合 240、360 均線濾網 |
| RSI | 動能策略 | 上升趨勢中的動能轉強 | RSI 由下向上進入 55～70，再由趨勢濾網確認 |
| MACD | 趨勢／動能 | 正動能波段起漲 | MACD 黃金交叉、位於零軸上並通過趨勢濾網 |
| 布林通道反轉 | 均值回歸 | 多頭中的短線回檔 | 下軌反轉、RSI、K 棒與潛在報酬共同過濾；本期未通過 |
| 區間突破 | 趨勢突破 | 放量突破整理區間 | 60 根新高、量能與上升趨勢共同確認 |
| 三指標多數決 | 多指標組合 | 趨勢中但單一指標雜訊較多 | MA、RSI、MACD 至少兩票進場，三票轉空出場 |

### 7.1 MA 均線策略 `ma`

進場條件：

- MA20 由下往上穿越 MA60。
- 收盤價高於 MA240。
- 收盤價高於 MA360，且 MA360 相較 36 根 K 棒前上升。

出場條件為收盤價跌破 MA240，或虧損達 2.5%；不設固定停利，讓趨勢獲利延伸。

### 7.2 RSI 策略 `rsi`

RSI14 必須由 55 以下向上穿入 55～70，收盤價同時位於上升中的 MA360 之上。RSI 跌破 50 或虧損達 2.5% 時出場。原本不具技術意義的 63～66 禁止區間已停用。

### 7.3 MACD 策略 `macd`

MACD 使用 12／26／9。進場需 MACD 上穿 signal、MACD 大於零，且收盤價位於上升中的 MA240 之上。MACD 跌破零軸、收盤價跌破 MA240，或虧損達 2.5% 時出場。

### 7.4 布林通道反轉策略 `bollinger`

布林通道使用 78 根 K 棒中線與 2.5 倍標準差。進場必須同時符合：

- 前一根收盤價低於下軌，本根重新站回下軌。
- 本根為紅 K 且高於前收；RSI 不高於 55。
- 回到中線的潛在空間至少 0.6%。
- 收盤價位於上升中的 MA360 之上。

收盤價回到中線出場，另設 2% 停損。本策略本期報酬為負、Profit Factor 小於 1，已標記為「未通過，僅保留對照」。

### 7.5 區間突破策略 `breakout`

計算前期高低點時排除目前 K 棒，避免前視偏誤。進場條件：

- 收盤價首次突破前 60 根 K 棒最高價。
- 成交量至少為 20 根均量的 1.1 倍。
- 收盤價位於上升中的 MA120 之上。

跌破前 30 根低點、跌破 MA120，或虧損達 2.5% 時出場。

### 7.6 三指標多數決策略 `vote`

多數決策略將 MA、RSI 與 MACD 各視為一票，至少 2／3 票為多方且收盤價位於上升中的 MA240 之上才進場：

- MA 多方票：MA20 高於 MA60，且收盤價高於 MA120。
- RSI 多方票：RSI 位於 55～70。
- MACD 多方票：MACD 高於 signal 且大於零。

MA 轉弱、RSI 低於 50、MACD 低於 signal 三張空方票全部成立，或虧損達 3% 時出場。

### 7.7 共用成交與風控規則

- 初始本金 100,000 元；每次最多 1,000 股。
- 動態使用最多 95% 可用現金，因此股價上漲後會自動改買零股，不會因買不起一整張而停止交易。
- 訊號於完成 K 棒收盤確認，下一根 K 棒開盤成交。
- 買賣手續費率 0.1425%；一般跨日賣出稅率 0.3%，符合現股當沖條件才使用 0.15%。
- 跨日波段持有；回測最後仍有部位才強制平倉。
- 每日最多進場一次，出場後冷卻 6 根 K 棒。

### 7.8 固定價格策略 `fixed`

固定價格策略會嘗試以 `--buy-price` 的限價買進一次，並在指定出場時間後以市價賣出。此策略主要用於測試下單、成交紀錄與報表輸出流程。

## 8. 常用參數

下表以目前 `backtest_config.json` 為主；六個 profile 可各自覆寫參數，實際研究值請以設定檔與產生的 `summary.json` 為準。

| 參數 | 預設值 | 說明 |
| --- | --- | --- |
| `--strategy` | `ma` | 策略名稱，可選 `ma`、`rsi`、`macd`、`bollinger`、`breakout`、`vote` 或 `fixed` |
| `--tick-source` | `sinopac` | tick 資料來源，可選 `sinopac` 或 `tsst` |
| `--config` | `backtest_config.json` | 設定檔路徑；平常不需要輸入，程式會自動讀取 |
| `--profile` | `active_profile` | 指定設定檔中的某一組 profile；批次程式會自動使用 |
| `--run-name` | 空白 | 本次測試標籤，會放進 `reports/` 資料夾名稱，方便比較策略版本 |
| `--code` | `2303` | 股票代號 |
| `--backtest-start` | `2026-01-01` | 回測起始日期，格式為 `YYYY-MM-DD` |
| `--backtest-end` | `2026-06-30` | 回測結束日期，格式為 `YYYY-MM-DD` |
| `--backfill-start` | `2026-02-01` | TSST 回補資料起始日期 |
| `--backfill-end` | `2026-02-28` | TSST 回補資料結束日期 |
| `--lots` | `1` | 每次下單股數上限，1 張等於 1000 股 |
| `--initial-capital` | `100000` | 初始本金；同時限制可買進金額並用於計算報酬率 |
| `--position-sizing` | `cash_fraction` | 依現金動態計算股數；也可改為固定張數 |
| `--capital-utilization` | `0.95` | 每次最多使用 95% 可用現金 |
| `--holding-mode` | `swing` | 跨日波段；`intraday` 才會每日強制平倉 |
| `--buy-price` | `1790` | 固定價格策略的限價買進價格 |
| `--sell-hour` | `13` | 強制出場小時 |
| `--sell-minute` | `20` | 強制出場分鐘 |
| `--stock-fee-rate` | `0.001425` | 股票手續費率 |
| `--stock-tax-rate` | `0.003` | 一般賣出交易稅率 |
| `--day-trade-tax-rate` | `0.0015` | 符合現股當沖條件時的賣出稅率 |
| `--data-cache-path` | `data/market_data.sqlite3` | 本地 SQLite 歷史 tick 資料庫路徑 |
| `--use-data-cache` / `--disable-data-cache` | 啟用 | 是否讀寫本地歷史資料庫 |
| `--news-summary-path` | 空白 | 選配的 `.json` 或 `.txt` 新聞／LLM 摘要檔 |
| `--kbar-unit` | `m` | K 線時間單位，目前主要使用分鐘 |
| `--kbar-freq` | `5` | K 線週期，例如 `5` 代表 5 分 K |
| `--ma-fast-period` | 依 profile | 快速均線週期 |
| `--ma-mid-period` | 依 profile | 中期均線週期 |
| `--ma-slow-period` | 依 profile | 慢速均線週期 |
| `--trend-ma-period` | 依 profile | 趨勢濾網均線週期 |
| `--trend-slope-lookback` | 依 profile | 判斷趨勢均線斜率的回看期數 |
| `--entry-near-ma-points` | `0` | 進場時價格與慢均線的距離上限，`0` 代表停用 |
| `--entry-start-hour` | `9` | 允許進場的開始小時 |
| `--entry-start-minute` | `0` | 允許進場的開始分鐘 |
| `--entry-cutoff-hour` | `12` | 允許進場的截止小時 |
| `--entry-cutoff-minute` | `30` | 允許進場的截止分鐘 |
| `--entry-block-start-hour` | `9` | 禁止進場區間的開始小時 |
| `--entry-block-start-minute` | `30` | 禁止進場區間的開始分鐘 |
| `--entry-block-end-hour` | `10` | 禁止進場區間的結束小時 |
| `--entry-block-end-minute` | `0` | 禁止進場區間的結束分鐘 |
| `--rsi-period` | `14` | RSI 計算週期 |
| `--rsi-buy-above` | `55` | RSI 進場下限 |
| `--rsi-buy-below` | `70` | RSI 進場上限 |
| `--rsi-block-low` | `63` | RSI 禁止進場區間下限 |
| `--rsi-block-high` | `66` | RSI 禁止進場區間上限 |
| `--use-rsi-block` / `--disable-rsi-block` | 停用 | 是否避開 RSI 禁止進場區間 |
| `--rsi-sell-below` | `50` | RSI 弱勢出場門檻 |
| `--macd-fast-period` | `12` | MACD 快線 EMA 週期 |
| `--macd-slow-period` | `26` | MACD 慢線 EMA 週期 |
| `--macd-signal-period` | `9` | MACD signal EMA 週期 |
| `--bollinger-period` | `78` | 布林通道中線與標準差的計算週期 |
| `--bollinger-stddev` | `2.5` | 布林通道上下軌的標準差倍數 |
| `--breakout-entry-period` | `60` | 突破策略進場高點的回看 K 棒數 |
| `--breakout-exit-period` | `30` | 突破策略出場低點的回看 K 棒數 |
| `--vote-required` | `2` | 多數決策略需要的票數，範圍 1–3 |
| `--vote-exit-required` | `3` | 多數決策略出場需要的空方票數 |
| `--stop-loss-pct` | 依 profile | 目前為 2%～3% |
| `--take-profit-pct` | `0` | 不設固定停利，以技術訊號退出趨勢 |
| `--min-hold-bars` | `2` | 技術出場前至少持有的 K 棒數 |
| `--cooldown-bars` | `6` | 賣出後暫停進場的 K 棒數 |
| `--max-entries-per-day` | `1` | 每個交易日最多進場次數 |
| `--disable-rsi` | 啟用 RSI | 暫時停用 RSI 條件，但報表仍會計算 RSI |
| `--disable-macd` | 啟用 MACD | 暫時停用 MACD 條件，但報表仍會計算 MACD |

單獨測試聯電的不同策略：

```powershell
python main02.py --strategy ma --code 2303 --run-name 2303_ma
python main02.py --strategy rsi --code 2303 --run-name 2303_rsi
python main02.py --strategy macd --code 2303 --run-name 2303_macd
python main02.py --strategy bollinger --code 2303 --run-name 2303_bollinger
python main02.py --strategy breakout --code 2303 --run-name 2303_breakout
python main02.py --strategy vote --code 2303 --run-name 2303_vote
```

測試不同策略版本：

```powershell
# MA 均線策略
python main02.py --strategy ma --code 2303 --run-name 2303_ma

# RSI 策略
python main02.py --strategy rsi --code 2303 --run-name 2303_rsi

# MACD 策略
python main02.py --strategy macd --code 2303 --run-name 2303_macd

# 布林通道反轉策略
python main02.py --strategy bollinger --code 2303 --run-name 2303_bollinger

# 區間突破策略
python main02.py --strategy breakout --code 2303 --run-name 2303_breakout

# 三指標多數決策略
python main02.py --strategy vote --code 2303 --run-name 2303_vote
```

完整範例：

```powershell
uv run python main02.py `
  --strategy ma `
  --tick-source sinopac `
  --code 2303 `
  --backtest-start 2026-01-01 `
  --backtest-end 2026-06-30 `
  --kbar-freq 5 `
  --ma-fast-period 5 `
  --ma-mid-period 10 `
  --ma-slow-period 20 `
  --entry-near-ma-points 0 `
  --rsi-period 14 `
  --rsi-buy-above 60 `
  --rsi-buy-below 70 `
  --use-rsi-block `
  --rsi-block-low 63 `
  --rsi-block-high 66 `
  --rsi-sell-below 45 `
  --macd-fast-period 12 `
  --macd-slow-period 26 `
  --macd-signal-period 9 `
  --lots 1 `
  --initial-capital 100000
```

## 9. 輸出檔案說明

每次回測會先依股票代號分類，再在 `reports/股票代號/` 底下建立一個獨立資料夾，資料夾名稱格式如下：

```text
reports/股票代號/YYYYMMDD_HHMMSS_股票代號_策略_策略標籤_回測起日_回測迄日
```

例如：

```text
reports/
└── 2303/
    └── 20260706_071852_2303_ma_2303_ma_2026-01-01_2026-06-30/
        ├── report.html
        ├── trades.csv
        ├── signals.csv
        ├── pnl.csv
        └── summary.json
```

| 檔案 | 說明 |
| --- | --- |
| `trades.csv` | 本地模擬成交紀錄，包含成交時間、買賣方向、成交價、股數、成交金額、手續費、交易稅與現金流 |
| `signals.csv` | 策略訊號紀錄，包含訊號時間、買賣方向、訊號類型、參考價格、觸發原因，以及當下 MA、RSI、MACD、布林通道與突破區間快照 |
| `pnl.csv` | 將買進與賣出配對後的損益明細，包含買賣價格、交易數量、毛損益、費用、稅額、淨損益與報酬率 |
| `summary.json` | 本次策略的結構化摘要，供六策略批次比較程式讀取 |
| `report.html` | 圖表化回測報告，包含 K 線、均線、布林／突破線（使用對應策略時顯示）、買賣訊號、RSI、MACD、策略設定、績效摘要與輸出檔名 |

HTML 報表可直接用瀏覽器開啟。報表中的「回測設定」與「策略參數」區塊會記錄本次執行使用的股票、日期、K 線週期、指標參數、費率與資金設定，方便日後核對與重現。

### 9.1 歷史資料庫與自動更新

程式預設使用 `data/market_data.sqlite3`。第一次執行時，缺少的日期會從 Shioaji 抓取並寫入 SQLite；同一股票與日期之後會直接讀本地資料庫。因此批次執行六種策略時，只有第一個策略需要補資料，其餘策略重用同一份 tick，速度會明顯改善，也能確保比較基礎一致。

只更新資料、不跑策略：

```powershell
python update_data.py --profile 2303_ma
```

一次預先更新多檔股票，可使用設定檔的 `stock_codes`，或在命令列指定：

```powershell
python update_data.py --stock-codes 2303,2330,2317,2454,2881
```

完成後會列出每檔股票的 Tick 數、更新耗時，以及 SQLite 中各股票目前涵蓋的日期範圍，方便在展示時交代系統支援的股票清單與處理速度。回測主程式仍以 `--code` 選擇單一標的；同一套策略可套用到資料來源支援的其他台股代號。

可將這個指令放入 Windows 工作排程器定期執行。API 失敗的日期不會標示為完成，下次會自動重試；休市日若 API 正常回傳空資料，則會記錄為已完成，避免反覆查詢。

### 9.2 本金、淨利／淨損與風險指標

本地模擬帳戶以 `initial_capital` 作為真正可用現金。買進所需金額若超過可用現金，訂單會以 `LOCAL-INSUFFICIENT-CASH` 拒絕，不會再出現用 10 萬本金卻無限制買進的結果。

報表使用下列公式：

```text
買進成本 = 買進價 × 股數
賣出收入 = 賣出價 × 股數
手續費 = 成交金額 × STOCK_FEE_RATE（買進與賣出都計算）
交易稅 = 賣出成交金額 × 適用稅率（一般賣出 0.3%；符合現股當沖條件 0.15%）
買賣價差損益 = 賣出收入 − 買進成本
淨利／淨損 = 買賣價差損益 − 買進手續費 − 賣出手續費 − 交易稅
最終資產 = 現金餘額 + 期末持股 × 最後價格
總報酬率 = (最終資產 − 初始本金) ÷ 初始本金
```

實際範例：初始本金 100,000 元，以 50 元買進聯電 1,000 股，再以 55 元跨日賣出；手續費率 0.1425%、一般賣出稅率 0.3%。買進手續費為 71.25 元、賣出手續費為 78.375 元、交易稅為 165 元。未扣成本損益為 5,000 元，淨利為 `5,000 − 71.25 − 78.375 − 165 = 4,685.375` 元，最終資產 104,685.375 元，總報酬率約 4.6854%。若同日完成且符合現股當沖條件，交易稅改用 0.15%。

HTML 與 `summary.json` 會顯示回測日期、初始本金、最終資產、成本、淨損益、總報酬率、勝率、損益平衡勝率、賺賠比、Profit Factor、買進持有基準、最大回撤與 Sharpe。最大回撤與 Sharpe 以每根 K 棒的持倉市值變化估算，Sharpe 假設無風險利率為 0。

### 9.3 六策略總比較與新聞摘要

執行 `python run_batch.py` 後，除六份個別報表外，還會產生：

```text
reports/2303/strategy_comparison_日期時間/
├── strategy_comparison.html
├── strategy_comparison.csv
└── strategy_comparison.json
```

比較表會把六個策略的類型、適合行情、本金、最終資產、交易成本、淨損益、報酬率、勝率、最大回撤與 Sharpe 放在同一張表。

新聞／LLM 是選配功能，不會阻擋回測。先複製 `resources/news/2303_news.example.json`、填入經查證的日期、來源、標題與摘要，再把 `backtest_config.json` 的 `news_summary_path` 指向該檔案。程式只負責匯入摘要，不會捏造即時新聞；正式報告仍應保留原始來源連結並人工確認。

## 9.4 將回測結果寫入 MySQL／MariaDB

資料庫上傳預設關閉，不影響原本的 HTML、CSV、JSON 與 SQLite 行情快取。第一次串接朋友的舊版資料庫時，先備份資料庫，再由 phpMyAdmin 執行 `database/回測輸出欄位升級.sql`。

接著在本機 `.env` 設定（密碼不可提交版本控制）：

```dotenv
DB_ENABLED=true
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ai_stock_system
DB_USER=你的資料庫帳號
DB_PASSWORD=你的資料庫密碼
DB_USER_ID=1
STOCK_NAME=聯電
```

啟用後，每次成功產生回測報表會以同一筆資料庫交易寫入 `stocks`、`analysis_records`、`backtest_results` 與 `reports`。任一步驟失敗會 rollback，不留下半套資料；本機報表仍會保留供除錯。成功時終端機會顯示三個新增編號：`analysis_id`、`backtest_id`、`report_id`。

若只想產生本機檔案，可保留 `DB_ENABLED=false`，或在執行時加入 `--db-disabled`。

既有報表不必重新回測。設定好 `.env` 後，可將某個輸出資料夾直接上傳：

```powershell
python import_existing_report.py "reports/2303/回測資料夾名稱"
```

若要將某檔股票底下的所有既有成果整批上傳，直接指定上一層資料夾：

```powershell
python import_existing_report.py "reports/2303"
```

整批模式預設排除資料夾或 `run_name` 含 `test` 的結果，以及 `fixed` 流程測試策略。只有明確加入 `--include-tests` 才會上傳測試資料。

匯入器會讀取 `summary.json`、`trades.csv`、`signals.csv`、`pnl.csv` 與 `report.html`；同一份報表若已存在，會停止並顯示既有 `analysis_id`，避免重複上傳。

## 10. Log 檔案

執行過程中的紀錄會輸出到：

- `logs/tsst.log`
- `logs/broker.log`
- `logs/backtest.log`
- `shioaji.log`

若資料下載失敗、登入失敗或回測流程中斷，可先檢查終端機訊息與上述 log 檔案。

## 11. 常見問題

### 11.1 缺少 `.env` 變數

若出現缺少環境變數的錯誤，請確認資料來源需要的欄位是否已填入：

- `sinopac`：需要 `API_KEY`、`API_SECRET`
- `tsst`：需要 `EMAIL`、`TSST_TOKEN`
- 非純回測模式：另需 `CA_PATH`、`CA_PASSWORD`

### 11.2 沒有產生交易

可能原因包含：

- 回測期間內沒有足夠 tick 資料
- 進場條件過嚴格
- RSI 或 MACD 過濾條件使訊號被排除
- K 線週期過長，導致樣本數不足

可先用 `--disable-rsi` 或 `--disable-macd` 暫時排除單一指標限制，再觀察交易次數是否增加。

### 11.3 報表有檔案但圖表沒有資料

請先確認：

- `--code` 是否正確
- `--backtest-start` 與 `--backtest-end` 是否有交易日資料
- API 帳號是否有權限取得該股票的 tick 資料
- 該次回測資料夾中的 `signals.csv` 與 `trades.csv` 是否為空檔

## 12. 重現性整理

為了讓回測結果可被檢查與重現，建議在專題報告中保留以下資訊：

- 執行日期與程式版本
- 完整執行指令
- `.env` 中使用的回測日期與資料來源，不需揭露 API key
- `reports/` 中本次產生的回測資料夾與五個輸出檔案
- HTML 報表截圖或原始 HTML 檔
- 若調整策略，列出有修改的參數與修改原因

只要使用相同資料來源、股票代號、回測區間、策略與參數，就能重新產生同格式的成交、訊號、損益與圖表報告。

