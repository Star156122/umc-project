# RSI 策略使用說明

這份文件已整理成目前專案的 RSI 策略說明。主要入口是專案根目錄的 `main.py`，回測核心位於 `trading_system/backtest.py`，設定檔是 `backtest_config.json`。

## 1. 目前 RSI 策略做什麼？

RSI 是本版六個正式策略之一，其他策略為 MA、MACD、布林通道反轉、區間突破與三指標多數決。

目前 RSI 策略的重點是：

- 使用 5 分 K 線。
- 每根新 K 線完成後才判斷一次訊號。
- RSI 進入設定區間時產生買進訊號。
- RSI 低於賣出門檻時產生技術出場訊號。
- 仍共用停損、停利、進場時間、冷卻 K 棒與強制出場設定。

## 2. RSI 策略進場條件

目前程式中的 RSI 進場條件如下：

- RSI 必須介於 `rsi_buy_above` 與 `rsi_buy_below`。
- 若 `use_rsi_block` 為 `true`，RSI 位於 `rsi_block_low` 到 `rsi_block_high` 時不進場。
- 仍需符合交易時間限制：
  - 可進場時間：09:00 到 12:30
  - 禁止進場時間：09:30 到 09:59
- 當日進場次數不得超過 `max_entries_per_day`。
- 賣出後需等待 `cooldown_bars` 根 K 棒才能再次進場。

## 3. RSI 策略出場條件

RSI 策略出場條件如下：

- 停損：虧損達 `stop_loss_pct`。
- 停利：獲利達 `take_profit_pct`。
- 技術出場：至少持有 `min_hold_bars` 根 K 棒後，RSI 低於 `rsi_sell_below`。
- 強制出場：到 13:20 仍有部位時出場，不留倉到隔天。

## 4. 要測 RSI 策略要改哪裡？

打開 `backtest_config.json`，把 `active_profile` 改成想測的 RSI profile，例如：

```json
"active_profile": "2303_rsi"
```

目前已經設定好的 RSI profile 是：

```text
2303_rsi
```

如果要一次跑完聯電的六種策略，使用 VS Code 的 `Run batch profiles`，批次清單會依照 `batch_profiles` 執行並產生總比較表。

## 5. 聯電的 RSI 設定

目前設定固定回測聯電 2303。範例：

```json
"2303_rsi": {
  "code": "2303",
  "strategy": "rsi",
  "run_name": "2303_rsi",
  "rsi_buy_above": 60,
  "rsi_buy_below": 70,
  "use_rsi_block": false,
  "use_rsi": true,
  "use_macd": false
}
```

這代表：

- 股票代號是 `2303`。
- 使用 RSI 策略。
- RSI 進場區間是 66 到 70。
- 不使用 RSI 避開區間。
- 不使用 MACD。

## 6. 輸出檔案在哪裡？

目前報表會依股票代號分類，格式如下：

```text
reports/
└── 股票代號/
    └── YYYYMMDD_HHMMSS_股票代號_策略_策略標籤_回測起日_回測迄日/
        ├── report.html
        ├── trades.csv
        ├── signals.csv
        └── pnl.csv
```

例如：

```text
reports/
└── 2303/
    └── 20260706_120000_2303_rsi_2303_rsi_2026-01-01_2026-06-30/
        ├── report.html
        ├── trades.csv
        ├── signals.csv
        ├── pnl.csv
        └── summary.json
```

## 7. 報告時可以怎麼說？

可以說：

> 目前 RSI 策略已經從原本的輔助濾網拆成獨立交易策略。它與其他五種策略使用同一檔聯電 2303、同一回測期間、初始本金與交易成本，並分別輸出成交、訊號、損益與 HTML 報告，最後由批次比較表統一比較績效。
