# exports 時間分類索引

更新日期：2026-10-09

本檔只建立時間與用途索引，不移動既有輸出。多個程式、文件、Registry 與 Dashboard 仍使用固定相對路徑，若直接按日期搬資料夾會造成連結失效。

狀態說明：

- **CURRENT**：目前 Technical → ML → Hybrid 主線仍需要。
- **REFERENCE**：歷史基準、驗證或比較證據，暫時保留原路徑。
- **ARCHIVE_CANDIDATE**：不再是目前主線，但被文件或 Registry 引用；完成引用改寫後可移入 Archive。
- **DASHBOARD**：入口或便利副本，本身可能引用其他輸出。

> 注意：部分 9 月成果在 Git checkout 後的檔案時間顯示為 2026-09-28。本索引優先依輸出名稱、設定與研究文件記載的實驗日期分類，不把 LastWriteTime 當成唯一研究日期。

## 2026-09-26：Technical 探索、風控與驗證

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `clean_validation_20260926/`、`clean_validation_latest.html` | 舊 Technical Clean Validation 正式證據 | REFERENCE |
| `six_strategy_logic/`、`six_strategy_logic_latest.html` | 六策略問題導向改善 | ARCHIVE_CANDIDATE |
| `trade_failure_diagnosis/`、`trade_failure_diagnosis_latest.html` | MACD／多數決逐筆失敗診斷 | ARCHIVE_CANDIDATE |
| `early_followthrough/`、`early_followthrough_latest.html` | 早期價格延續確認；包含已淘汰版本證據 | ARCHIVE_CANDIDATE |
| `entry_exit_improvement/`、`entry_exit_improvement_latest.html` | 舊進出場改善實驗 | ARCHIVE_CANDIDATE |
| `risk_ablation/`、`risk_ablation_latest.html` | 舊風控拆項與敏感度實驗 | ARCHIVE_CANDIDATE |
| `risk_candidate_*` | 舊風控候選設定與輸出 | ARCHIVE_CANDIDATE |
| `four_group_*` | 舊四組比較實驗 | ARCHIVE_CANDIDATE |
| `routing_simulation_*` | 舊策略／行情路由模擬 | ARCHIVE_CANDIDATE |
| `regime_validation_*` | 舊市場狀態驗證輸出 | ARCHIVE_CANDIDATE |
| `cross_stock_dashboard_latest.*` | 舊 Technical 跨股票總覽入口 | DASHBOARD／REFERENCE |

## 2026-09-27：第一版 ML Baseline

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `ml_baseline_20260927/`、`ml_baseline_latest.html` | 第一版 ML Baseline 與舊研究工作台資料來源 | REFERENCE |

## 2026-09-28：研究入口與整合輸出

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `research_dashboard_latest.html` | 舊研究工作台入口 | DASHBOARD |
| `unified_analysis_latest.json` | Technical／ML 統一分析資料 | REVIEW_REQUIRED |
| `unified_llm_payload_latest.json` | 舊 LLM 分析輸入；LLM 不在本輪研究範圍 | ARCHIVE_CANDIDATE |

## 2026-10-03：ML Trading V1

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `ml_trading_v1_20261003/`、`ml_trading_v1_latest.html` | ML Alone 第一版交易回測 | REFERENCE／ARCHIVE_CANDIDATE |

## 2026-10-04：ML Trading V2、V3 與失敗診斷

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `ml_trading_diagnosis_20261004/`、`ml_trading_diagnosis_latest.html` | V1 交易次數不足與訊號問題診斷 | REFERENCE／ARCHIVE_CANDIDATE |
| `ml_trading_v2_20261004/`、`ml_trading_v2_latest.html` | ML Alone V2 | REFERENCE／ARCHIVE_CANDIDATE |
| `ml_trading_v3_20261004/`、`ml_trading_v3_latest.html` | 目前最新的舊 ML Alone 對照 | REFERENCE |

## 2026-10-05：Training-only ML 流程與泛化研究

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `ml_v3_candidate_20261005/` | 現有 RF Candidate、scaler、Walk-forward 與 Candidate manifest | CURRENT |
| `ml_industry_generalization_20261005/` | RF／XGBoost／GRU 的 LOSO 與產業泛化 | CURRENT |
| `ml_training_diagnosis_20261005/` | Training-only 分類及 ML Alone 交易診斷 | CURRENT |
| `ml_v4_feature_ablation_20261005/` | 修正版 Feature Ablation；證明增加 Feature 的改善不一致 | REFERENCE |

已於 2026-10-09 清除同日產生、且已結束的一次性 Label／Feature／Anchor／參數診斷與被取代的 V4 中間輸出，詳細清單見 `../cleanup_manifest.md`。

## 2026-10-06～2026-10-09：Technical 泛化重新稽核

| 路徑 | 用途 | 狀態 |
|---|---|---|
| `technical_generalization_audit.json` | Technical 泛化資料角色、日期鎖與執行稽核 | CURRENT |
| `technical_generalization_report.html` | Technical 泛化狀態報告 | CURRENT |
| `technical_generalization_results.csv` | 預定測試列；目前因資料路徑問題尚未完成績效 | CURRENT |

目前稽核狀態是 `BLOCKED_MISSING_DATA`：舊設定指向另一台電腦的絕對資料庫路徑。修正成專案內正式資料庫前，不應把這三個檔案解讀為已完成的 Technical 泛化結果。

## 建議閱讀順序

1. `../research/current/hybrid_research_plan.md`
2. `ml_v3_candidate_20261005/candidate_manifest.json`
3. `ml_industry_generalization_20261005/industry_generalization_report.html`
4. `ml_training_diagnosis_20261005/training_diagnosis_report.html`
5. `technical_generalization_report.html`
6. 歷史問題需要追溯時，再查看 2026-09-26～2026-10-04 的 REFERENCE／ARCHIVE_CANDIDATE。
