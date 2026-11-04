# Technical V2-B 最長持有時間 A/B/C/D 實驗預先登記

- Experiment ID: `technical-v2b-max-hold-2303-2023-2024-exp002`
- 狀態：PREREGISTERED；只用已曝光 2023–2024 `historical_development`。
- 標的：2303 聯電；資料庫 `data/technical_v1_mltech_clean.sqlite3`，沿用五檔共同完整交易日資格。
- A：凍結 Technical V2-B，162 根 5 分 K 上限。
- B/C/D：只將上限改為 270/540/810 根，即 54 根完整 K 棒交易時段的 5/10/15 日等值。
- 固定條件：`BR_SENS_RANGE55_BB20_2`、市場代理濾網、前 30 根低點技術出場、Risk V1、成本、滑價、成交規則及其他參數均沿用既有設定；不使用 ML Gate。
- 計時定義：引擎按成交後已觀察到的 5 分 K 棒計數，夜間不增加。因盤中進場及下一根開盤成交，N×54 是完整時段等值，不保證在第 N 個交易日收盤出場。另報告 entry-fill 至 exit-fill 跨越的實際交易日間隔及含進出場日數。
- 主要結果：逐版本交易、勝率、毛/淨損益、成本、PF、MDD、持有時間及技術/停損/到期/期末出場原因；另外逐筆追蹤 A 中因 162 根到期的交易在 B/C/D 的配對結果。
- 判定：只作探索性比較，不以單一報酬選勝者；樣本少或結果不一致時不採用，正式策略維持 162 根。
- 禁止：Final OOS、Additional Holdout、資料庫修改、ML、參數搜尋、正式策略修改。
