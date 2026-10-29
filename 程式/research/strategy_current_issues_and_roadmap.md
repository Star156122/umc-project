# Strategy Research：Current Issues Registry 與 Improvement Roadmap

**更新日期：** 2026-10-21  
**研究階段：** Development Research Stage  
**主要依據：** Representative Stocks Benchmark Exp001、Filter Funnel Audit Exp001 及既有凍結設定。  
**正式診斷股票：** 2330 台積電（趨勢型）、2454 聯發科（高波動型）、2303 聯電（弱趨勢型）。2379 瑞昱、3034 聯詠僅可作既有 Market Regime Proxy，不是本輪研究標的。  
**模型比較：** Random Forest、XGBoost、GRU 均保留，不選唯一最佳模型。

## 研究流程與凍結條件

目前流程為：

```text
Technical Breakout Candidate
→ Market Regime B
→ ML Gate（BUY 放行；HOLD / SELL 阻擋）
→ Risk V1
→ 下一根可交易 5 分 K 開盤成交
→ 既有 Exit
```

目前基準是 Breakout Entry 55、Exit 30、Market Regime B、Risk V1（單筆風險 1%、停損 2.5%、部位上限 40%）、既有交易成本與執行規則。ML 的 SELL 是阻擋做多訊號，不代表放空。下一個實驗開始前，必須先登記實驗假設與判定方式；本文件本身不授權開始實驗。

目前仍屬開發研究階段，不是 Final Strategy。禁止在本路線圖工作中全面調參、搜尋最佳組合、重新訓練 ML、更換股票、啟用 Portfolio 或 Short，或使用 2025、2026、Final OOS 資料。

## Current Issues Registry

| ID | 問題／發現 | 證據與目前解讀 | 狀態／處置 |
|---|---|---|---|
| F-01 | Market Regime B 的篩選效果 | 119 個 Technical 候選中 82 個通過、37 個阻擋。被擋候選 7 正、30 負，平均淨交易報酬約 -0.60%，PF 約 0.93；目前診斷為 `PRECISE_FILTER_EVIDENCE`。這是候選品質診斷，不代表已證明跨期間有效。 | `KEEP_FROZEN_FOR_NOW`。目前相對健康，暫不修改公式。 |
| F-02 | Hybrid 資金曝險偏低 | Benchmark 平均曝險：Technical Only 4.90%、RF 1.33%、XGBoost 1.12%、GRU 0.59%。漏斗顯示主要候選數損失在 ML Gate；BUY 後沒有額外執行阻擋。 | `CONFIRMED_DIAGNOSTIC`。目標是增加有效資金使用率，不是單純提高交易數。 |
| F-03 | RF 仍擋下一部分正向候選 | 82 個 Regime PASS 中，RF 為 BUY 19、HOLD 20、SELL 43。BUY 平均淨交易報酬約 +0.81%；HOLD/SELL 阻擋集合約 20 筆正向、43 筆負向，平均淨交易報酬約 -0.05%，PF 約 1.74。整體為 `PRECISE_FILTER_EVIDENCE`，但正向機會 Recall 仍值得診斷。 | `CONFIRMED_WITH_FOLLOWUP_QUESTION`。暫不改 Threshold、Label 或超參數。 |
| F-04 | XGBoost 在高波動情境可能漏掉機會 | 82 個 Regime PASS 中，BUY 16、HOLD 20、SELL 46。BUY 平均淨交易報酬約 +1.32%；被擋集合約 21 正、45 負，平均淨交易報酬約 -0.13%，PF 約 1.68，整體為 `PRECISE_FILTER_EVIDENCE`。2454 的被擋集合仍有正向交易證據。 | `CONFIRMED_WITH_FOLLOWUP_QUESTION`。未來檢查高波動股票的正向機會 Recall，不直接改模型。 |
| F-05 | GRU BUY 訊號品質與阻擋方向有疑問 | BUY 11、HOLD 33、SELL 38。BUY 平均淨交易報酬約 -0.78%、PF 約 0.73；SELL 隔離診斷平均約 +0.82%、PF 約 3.87（17 正、21 負），被擋的 HOLD/SELL 合計平均約 +0.29%、PF 約 2.62（27 正、44 負）。診斷為 `OVER_CONSERVATIVE_EVIDENCE`。這些是單筆隔離的 Shadow 診斷，不是反向做多／放空或策略績效。現象可能涉及預測方向、候選情境與訓練分布、樣本數或訓練目標與交易目標差異。 | `CONFIRMED_SYMPTOM_CAUSE_UNKNOWN`。不得將 SELL 改成 BUY、反轉預測或事後改規則。需另立診斷。 |
| F-06 | Technical 交易成本侵蝕偏薄的 Edge | Technical Only 82 筆交易，Gross Positive PnL 約 NT$33,522，成本約 NT$18,021，成本／正毛利約 53.8%；平均每筆淨 Edge 約 NT$58。 | `CONFIRMED_DIAGNOSTIC`。可能與 Entry 時點、Exit、候選品質或交易頻率有關，現階段不能單獨歸因。 |
| H-01 | Breakout 55 可能進場偏晚 | 若確認過久，可能使進場價格提高、剩餘行情縮短並壓薄 Edge；目前是待驗證假設。 | `HYPOTHESIS_NOT_TESTED`。列為下一實驗問題。 |
| H-02 | Exit 30 可能未充分保留趨勢，或吐回獲利 | 可能影響持有時間、獲利保留及成本效率；尚未在 Entry 問題隔離前測試。 | `DEFERRED_HYPOTHESIS`。Entry Audit 完成前不啟動 Exit Audit。 |
| H-03 | ML 原始方向 Label 與 Hybrid Candidate 的交易價值可能不完全對齊 | ML 原本預測 T+1 Open 至 T+3 Close 的 BUY/HOLD/SELL；Hybrid 關心的是 Technical Candidate 經過實際 Entry、Exit、成本及風險後是否值得交易。這是合理的目標錯位假設，尚非因果結論。 | `HYPOTHESIS_NOT_TESTED`。列在 Entry／Exit 研究之後診斷；目前不改 Label。 |
| H-04 | 資金曝險需與成本、風險和機會品質一起解讀 | Buy & Hold 平均曝險約 99.77%，平均報酬約 17.11%，但回撤也明顯較高；不能用絕對報酬或曝險單獨判定策略優劣。 | `INTERPRETATION_GUARDRAIL`。保留 Buy & Hold 作 Benchmark，並同時比較風險與資金效率。 |

### 三種模型的保留原則

RF 與 XGBoost 在本次漏斗的阻擋集合呈現負平均淨報酬，但各自仍擋下部分正向候選；GRU 呈現更明顯的方向品質疑問。這些差異是保留三模型比較的理由，不是刪除模型或指定唯一 Winner 的理由。

所有 Shadow Trade 都是逐筆隔離的 `DIAGNOSTIC ONLY` 結果，不能相加成策略報酬或投資組合報酬，也不能當作策略修改後會得到的實際結果。

## Improvement Roadmap

| 優先順序 | 工作 | 目的與固定條件 | 狀態 |
|---|---|---|---|
| P0 | 建立本 Current Issues Registry 與 Roadmap | 將已確認現象、假設及研究次序集中記錄。 | `COMPLETED` |
| P1 | Entry Sensitivity Audit | 已比較 Breakout 40、55（Control）、70；固定 Exit 30、Market Regime B、RF/XGBoost/GRU、Risk V1、成本、資金、執行方式，以及 2330、2303、2454 三檔代表股。結果為 `MIXED` 且具 Fold 敏感性；沒有選出新的 Entry。 | `COMPLETED_MIXED`（[結果](strategy_entry_sensitivity_exp001.md)；僅 2023–2024 已暴露開發資料） |
| P2 | Entry Audit 後的 Entry 決策 | Entry 40／55／70 的結果不足以支持更換參數；保留 55 作為基準，不宣稱已證明最佳。樣本維持 2330、2303、2454。 | `DECIDED_RETAIN_CONTROL_55` |
| P3 | Exit Sensitivity Audit | 下一項候選研究：比較 Exit 20、30（Control）、40；檢查過早退出、獲利回吐、持有時間與成本效率。執行前須另完成 preregistration，股票和其他條件保持固定。 | `PENDING_PREREGISTRATION` |
| P4 | 固定候選的跨模型整體比較 | 在相同資料及執行條件下比較 Technical、Technical+RF、Technical+XGBoost、Technical+GRU；一起呈現報酬、MDD、Sharpe、PF、勝率、交易數、曝險、成本與單筆 Edge，並檢查跨股票／Fold 穩定性。保留 Buy & Hold 作參考，不以單一 Return 排名。 | `AFTER_P1_P3` |
| P5 | ML Target Alignment Diagnostic | 研究原始方向分類目標與 Technical Candidate 實際交易品質的差異。先建立診斷問題與資料／評估規格；未另行 preregister 前不改 Label 或重訓。 | `DEFERRED` |
| P6 | GRU Model Diagnostic | 分開檢查方向分類、Candidate Context 與訓練分布、有效樣本數、Sequence Feature/Label 與 Hybrid 交易目標是否匹配。不得翻轉類別或依目前結果救援性調參。 | `DEFERRED` |
| P7 | 建立新的 Strategy Candidate（若有必要） | 只有前述實驗提供明確、跨股票／Fold 一致的結構性理由，才建立版本化候選；依治理規範先登記單一假設、凍結評估標準及保留資料狀態。 | `CONDITIONAL` |

### Entry Robustness 與成功判準

Entry Audit 不以最高 Return 作為 Winner 選擇。如果 40、55、70 的指標大致接近，視為存在較穩健區域；若只有一個設定突然突出、鄰近設定明顯較差，標記 `POSSIBLE_PARAMETER_SENSITIVITY`，不得直接採用。結論需考慮不同股票與 Fold 是否同方向。

後續改善須同時檢查 Return、最大回撤、Sharpe、Profit Factor、資金曝險、交易數、交易成本、每筆淨 Edge、跨股票及跨 Fold 穩定度。交易增加只有在新增機會品質與成本效率可接受時才有價值。Training / Development 結果分開呈現；不得存取 2025、2026 或 Final OOS。

## 目前研究問題

- **RQ1：** Breakout 55 是否讓 Entry 太慢？
- **RQ2：** Technical 的低 Edge 是否主要來自 Entry Timing？
- **RQ3：** Exit 30 是否限制趨勢獲利？
- **RQ4：** RF／XGBoost 能否維持過濾品質，同時減少漏掉的正向機會？
- **RQ5：** GRU 為何出現 BUY 品質低於被阻擋候選的情況？
- **RQ6：** ML Direction Label 是否與 Hybrid Trade Quality 目標不完全一致？
- **RQ7：** 如何增加有效資金使用率，同時避免低品質交易及成本同步增加？

## 本輪範圍與狀態

此路線圖建立後，Entry Sensitivity Audit 已另行完成，資料與結果見 P1。沒有因該結果修改策略參數；Entry 55 保留為比較基準，股票樣本維持三檔。下一項 Exit Sensitivity Audit 尚未執行，開始前須完成正式 preregistration 與必要的 preflight。Entry 實驗僅使用 2023–2024 已暴露開發資料，未使用 2025、2026 或 Final OOS。

## Evidence

- `exports/representative_benchmark_exp001_20261020T111920272910/benchmark_summary.csv`
- `exports/representative_benchmark_exp001_20261020T111920272910/benchmark_report.html`
- `exports/filter_funnel_audit_exp001_20261020T132127303170+0800/filter_funnel_audit.json`
- `exports/filter_funnel_audit_exp001_20261020T132127303170+0800/blocked_opportunity_summary.csv`
- `exports/filter_funnel_audit_exp001_20261020T132127303170+0800/funnel_overall.csv`
- `research/strategy_representative_benchmark_exp001.md`
- `research/strategy_filter_funnel_exp001.md`
