# 股票交易策略回測系統

本專案是一套以 Python 建立的股票交易策略回測系統，可從永豐 Shioaji 或 TSST 取得歷史 tick 資料，將 tick 重新回放成盤中資料流，再依照策略條件產生買賣訊號、模擬成交、計算損益，最後輸出 CSV 與 HTML 圖表報告。系統目前支援 MA、RSI、MACD 三種獨立交易策略，也保留固定價格策略作為測試用途。

## 1. 專案結構

```text
.
├── main02.py                  # 主要單檔回測程式
├── main.py                    # 相容入口，會轉呼叫 main02.py
├── tests/                     # 策略規則測試
├── docs/                      # 專案說明文件
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

平常測股票或策略時，優先使用 `backtest_config.json`。單檔和多檔股票的執行方式不同：

1. 開啟 `backtest_config.json`。
2. 只跑一檔時，修改最上方的 `active_profile`，例如改成 `2330_ma` 或 `2313_rsi`。
3. 要一次跑多檔時，修改 `batch_profiles` 清單。
4. 在 VS Code 左側選「執行與偵錯」。
5. 只跑一檔選 `Run main02.py`；一次跑多檔選 `Run batch profiles`。
6. 按綠色執行按鈕，或直接按 `F5`。
7. 到 `reports/` 查看新產生的報表資料夾。

`Run main02.py` 只會輸出一個股票；`Run batch profiles` 會依照 `batch_profiles` 逐一輸出多個股票。

預設批次清單：

```text
2313_ma
2313_rsi
2313_macd
2330_ma
2330_rsi
2330_macd
2303_ma
2303_rsi
2303_macd
2317_ma
2317_rsi
2317_macd
```

目前設定檔已先放好這些 profile：

```text
2313_ma / 2313_rsi / 2313_macd
2330_ma / 2330_rsi / 2330_macd
2303_ma / 2303_rsi / 2303_macd
2317_ma / 2317_rsi / 2317_macd
```

如果要新增股票，可以在 `profiles` 裡複製一組設定，修改 `code`、`run_name` 與策略參數；如果要讓它加入批次回測，再把 profile 名稱放進 `batch_profiles`。

每個 profile 都可以設定自己的股票與策略參數，例如：

- `2330_ma`：股票 `2330` 使用 MA 均線策略
- `2330_rsi`：股票 `2330` 使用 RSI 策略
- `2330_macd`：股票 `2330` 使用 MACD 策略

## 5. 可重現操作流程

1. 確認 Python 版本與依賴已安裝。
2. 在 `.env` 填入資料來源所需帳號與 API key。
3. 固定回測日期、股票代號、策略與參數。
4. 使用 VS Code 執行 `Run main02.py`，或用 PowerShell 執行 `main02.py`。
5. 到 `reports/` 檢查本次產生的 CSV 與 HTML 報表。
6. 報告中保留執行指令、參數與輸出檔名，即可重現同一套回測設定。

範例：使用永豐 tick 資料，回測股票 `2313` 在指定期間的均線策略：

```powershell
uv run python main02.py `
  --strategy ma `
  --tick-source sinopac `
  --run-name 2313_ma `
  --code 2313 `
  --backtest-start 2026-01-01 `
  --backtest-end 2026-06-30
```

若已啟用虛擬環境，也可以執行：

```powershell
python main02.py --strategy ma --tick-source sinopac --code 2313 --backtest-start 2026-01-01 --backtest-end 2026-06-30
```

## 6. 系統流程

```text
讀取 .env 與命令列參數
        ↓
驗證日期、策略參數、資料來源與安全設定
        ↓
建立 TSST 策略物件與本地回測報價物件
        ↓
依資料來源取得歷史 tick
        ↓
依時間順序回放 tick，重建 K 線
        ↓
計算 MA、RSI、MACD 技術指標
        ↓
策略判斷進場、出場或強制出場
        ↓
本地模擬成交並記錄訊號
        ↓
輸出 trades.csv、signals.csv、pnl.csv 與 report.html
```

## 7. 策略說明

### 7.1 MA 均線策略 `ma`

MA 策略以均線趨勢作為主要訊號，不再把 RSI 或 MACD 當作進場濾網。進場條件包含：

- 快速均線 `MA_FAST` 由下往上穿越中期均線 `MA_MID`
- 收盤價位於慢均線 `MA_SLOW` 上方
- `--entry-near-ma-points` 大於 0 時，才限制收盤價與慢均線的距離；預設 `0` 為停用

MA 策略出場條件包含：

- 報酬率達到 `--stop-loss-pct` 停損或 `--take-profit-pct` 停利
- 技術指標出場前，至少持有 `--min-hold-bars` 根 K 棒
- 收盤價跌破慢均線，或快速均線由上往下跌破中期均線

### 7.2 RSI 策略 `rsi`

RSI 策略以 RSI 區間作為主要訊號，不要求均線交叉或 MACD 確認。進場條件包含：

- RSI 介於 `--rsi-buy-above` 與 `--rsi-buy-below`
- 若啟用 RSI 避開區間，RSI 位於 `--rsi-block-low` 到 `--rsi-block-high` 時不進場

RSI 策略出場條件包含：

- 報酬率達到停損或停利
- 至少持有指定 K 棒數後，RSI 低於 `--rsi-sell-below`

### 7.3 MACD 策略 `macd`

MACD 策略以 MACD 與 signal 的交叉作為主要訊號，不要求均線交叉或 RSI 區間。進場條件包含：

- MACD 由下往上穿越 signal，形成黃金交叉

MACD 策略出場條件包含：

- 報酬率達到停損或停利
- 至少持有指定 K 棒數後，MACD 由上往下跌破 signal，形成死亡交叉

三種策略都共用時間限制、每日最多進場次數、冷卻 K 棒數、停損停利，以及 13:20 強制出場設定。

### 7.4 固定價格策略 `fixed`

固定價格策略會嘗試以 `--buy-price` 的限價買進一次，並在指定出場時間後以市價賣出。此策略主要用於測試下單、成交紀錄與報表輸出流程。

## 8. 常用參數

| 參數 | 預設值 | 說明 |
| --- | --- | --- |
| `--strategy` | `ma` | 策略名稱，可選 `ma`、`rsi`、`macd` 或 `fixed` |
| `--tick-source` | `sinopac` | tick 資料來源，可選 `sinopac` 或 `tsst` |
| `--config` | `backtest_config.json` | 設定檔路徑；平常不需要輸入，程式會自動讀取 |
| `--profile` | `active_profile` | 指定設定檔中的某一組 profile；批次程式會自動使用 |
| `--run-name` | 空白 | 本次測試標籤，會放進 `reports/` 資料夾名稱，方便比較策略版本 |
| `--code` | `2313` | 股票代號 |
| `--backtest-start` | `2026-01-01` | 回測起始日期，格式為 `YYYY-MM-DD` |
| `--backtest-end` | `2026-06-30` | 回測結束日期，格式為 `YYYY-MM-DD` |
| `--backfill-start` | `2026-02-01` | TSST 回補資料起始日期 |
| `--backfill-end` | `2026-02-28` | TSST 回補資料結束日期 |
| `--lots` | `1` | 每次下單張數，1 張等於 1000 股 |
| `--initial-capital` | `100000` | 初始資金，用於計算總報酬率 |
| `--buy-price` | `1790` | 固定價格策略的限價買進價格 |
| `--sell-hour` | `13` | 強制出場小時 |
| `--sell-minute` | `20` | 強制出場分鐘 |
| `--stock-fee-rate` | `0.001425` | 股票手續費率 |
| `--stock-tax-rate` | `0.003` | 股票交易稅率，僅賣出時計算 |
| `--kbar-unit` | `m` | K 線時間單位，目前主要使用分鐘 |
| `--kbar-freq` | `5` | K 線週期，例如 `5` 代表 5 分 K |
| `--ma-fast-period` | `5` | 快速均線週期 |
| `--ma-mid-period` | `10` | 中期均線週期 |
| `--ma-slow-period` | `20` | 慢速均線週期 |
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
| `--rsi-buy-above` | `60` | RSI 進場下限 |
| `--rsi-buy-below` | `70` | RSI 進場上限 |
| `--rsi-block-low` | `63` | RSI 禁止進場區間下限 |
| `--rsi-block-high` | `66` | RSI 禁止進場區間上限 |
| `--use-rsi-block` / `--disable-rsi-block` | 啟用 | 是否避開 RSI 禁止進場區間 |
| `--rsi-sell-below` | `45` | RSI 弱勢出場門檻 |
| `--macd-fast-period` | `12` | MACD 快線 EMA 週期 |
| `--macd-slow-period` | `26` | MACD 慢線 EMA 週期 |
| `--macd-signal-period` | `9` | MACD signal EMA 週期 |
| `--stop-loss-pct` | `0.015` | 停損比例，`0.015` 代表 1.5% |
| `--take-profit-pct` | `0.03` | 停利比例，`0.03` 代表 3% |
| `--min-hold-bars` | `2` | 技術出場前至少持有的 K 棒數 |
| `--cooldown-bars` | `6` | 賣出後暫停進場的 K 棒數 |
| `--max-entries-per-day` | `1` | 每個交易日最多進場次數 |
| `--disable-rsi` | 啟用 RSI | 暫時停用 RSI 條件，但報表仍會計算 RSI |
| `--disable-macd` | 啟用 MACD | 暫時停用 MACD 條件，但報表仍會計算 MACD |

測試其他股票：

```powershell
python main02.py --strategy ma --code 2330 --run-name 2330_ma
python main02.py --strategy rsi --code 2303 --run-name 2303_rsi
python main02.py --strategy macd --code 2317 --run-name 2317_macd
```

測試不同策略版本：

```powershell
# MA 均線策略
python main02.py --strategy ma --code 2313 --run-name 2313_ma

# RSI 策略
python main02.py --strategy rsi --code 2313 --run-name 2313_rsi

# MACD 策略
python main02.py --strategy macd --code 2313 --run-name 2313_macd
```

完整範例：

```powershell
uv run python main02.py `
  --strategy ma `
  --tick-source sinopac `
  --code 2313 `
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
└── 2313/
    └── 20260706_071852_2313_ma_2313_ma_2026-01-01_2026-06-30/
        ├── report.html
        ├── trades.csv
        ├── signals.csv
        └── pnl.csv
```

| 檔案 | 說明 |
| --- | --- |
| `trades.csv` | 本地模擬成交紀錄，包含成交時間、買賣方向、成交價、股數、成交金額、手續費、交易稅與現金流 |
| `signals.csv` | 策略訊號紀錄，包含訊號時間、買賣方向、訊號類型、參考價格、觸發原因，以及當下 MA、RSI、MACD 快照 |
| `pnl.csv` | 將買進與賣出配對後的損益明細，包含買賣價格、交易數量、毛損益、費用、稅額、淨損益與報酬率 |
| `report.html` | 圖表化回測報告，包含 K 線、均線、買賣訊號、RSI、MACD、策略設定、績效摘要與輸出檔名 |

HTML 報表可直接用瀏覽器開啟。報表中的「回測設定」與「策略參數」區塊會記錄本次執行使用的股票、日期、K 線週期、指標參數、費率與資金設定，方便日後核對與重現。

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
- `reports/` 中本次產生的回測資料夾與四個輸出檔案
- HTML 報表截圖或原始 HTML 檔
- 若調整策略，列出有修改的參數與修改原因

只要使用相同資料來源、股票代號、回測區間、策略與參數，就能重新產生同格式的成交、訊號、損益與圖表報告。

