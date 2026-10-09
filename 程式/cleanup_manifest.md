# 專案清理紀錄

- 建立日期：2026-10-09
- 清理範圍：`程式/`
- 依據：專題方向調整為 Technical Strategy → ML Filter → Hybrid Strategy & Backtest。
- 原則：先確認用途與引用；原始行情、正式資料庫、核心程式、研究防洩漏機制與仍被引用的研究證據一律保留。不確定者標示 `REVIEW_REQUIRED`。
- 使用者授權：已於 2026-10-09 明確同意執行本次盤點與清理。

## 刪除前清單

| 檔案／資料夾 | 原本用途 | 判定理由 | 可重新產生 | 決定 |
|---|---|---|---|---|
| 專案程式碼內的 `__pycache__/`（排除 `.venv/`） | Python bytecode 快取 | 執行時自動生成，與研究證據無關 | 是 | Delete |
| `.pytest_cache/` | pytest 執行快取 | 測試時自動生成 | 是 | Delete |
| `.uv-cache/` | 專案區域 uv 快取 | 套件工具可重新建立；不含正式環境 `.venv` | 是 | Delete |
| `logs/`、`shioaji.log` | 執行與券商 SDK 暫存紀錄 | 已被 `.gitignore` 排除，非正式研究成果 | 是 | Delete |
| `reports/2303/*report_test*`（8 個資料夾、40 個檔案） | 2026 H1 MA 測試報表 | 未被 Git 追蹤，名稱明確為 `report_test`，內容為重複測試輸出 | 是 | Delete |
| `exports/ml_v4_research_20261005/` | V4 Feature 研究早期輸出 | 已由後續修正版與 `ml_v4_feature_ablation_20261005/` 取代，正式程式不讀取此路徑 | 是 | Delete |
| `exports/ml_v4_research_20261005_fix1/` | V4 Feature 研究中間修正版 | 已由 `ml_v4_feature_ablation_20261005/` 取代，正式程式不讀取此路徑 | 是 | Delete |
| `exports/ml_training_param_compare_20261005/` | Training-only 小範圍參數比較 | 已得出改善很小、停止擴大搜尋的結論；新方向不以此輸出繼續調參 | 是（保留腳本） | Delete |
| `exports/ml_label_feature_diagnosis_20261005/` | Label 與 Feature 一次性診斷 | 診斷階段已完成，新方向先固定 ML V1 定義 | 是（保留腳本） | Delete |
| `exports/ml_feature_audit_20261005/` | `range_vs_close` 與 Feature mapping 稽核 | 已確認不是 Feature 計算或 reshape bug，屬已結束的除錯輸出 | 是（保留腳本） | Delete |
| `exports/ml_signal_anchor_compare_20261005/` | 13:30 與 13:25 訊號錨點控制實驗 | 屬已完成的一次性控制實驗，不是新正式流程的輸入 | 是（保留腳本） | Delete |
| `exports/ml_v4a_label_compare_20261005/` | 舊 Label 結構比較 | 新方向要求先凍結 ML V1，不再沿用 V4 命名與本輪 Label 搜尋 | 是（保留腳本） | Delete |

## 保留的重要內容

| 檔案／資料夾 | 用途 | 決定 |
|---|---|---|
| `data/market_data.sqlite3`、`data/batches/`、`data/baselines/` | 正式市場資料、批次資料與基準證據 | Keep |
| `.venv/`、`pyproject.toml`、`uv.lock` | 可執行環境與依賴鎖定 | Keep |
| `trading_system/backtest.py`、策略核心模組 | Technical 與 Hybrid 後續共用的回測、策略及風控核心 | Keep |
| `trading_system/research_guard.py`、`ml/data_roles.py`、`ml/validation_access.py` | 日期鎖、資料角色與防資料洩漏核心 | Keep |
| `ml/data_pipeline.py`、`ml/models.py`、`ml/walk_forward.py`、`ml/trading_backtest.py` | ML 資料、RF／XGBoost／GRU、Walk-forward 與 ML 回測核心 | Keep |
| `configs/holdout_policy.json`、`configs/ml_data_policy.json`、`configs/ml_training_protocol_v3.json` | 正式資料限制與訓練規則 | Keep |
| `exports/ml_v3_candidate_20261005/` | 現有 ML Candidate、scaler、模型與 Walk-forward 證據 | Keep |
| `exports/ml_industry_generalization_20261005/` | LOSO／跨股票與產業泛化證據 | Keep |
| `exports/ml_training_diagnosis_20261005/` | 現有 Training-only 分類與交易診斷基準 | Keep |
| `exports/ml_v4_feature_ablation_20261005/` | 最終修正版 Feature Ablation；仍被教師展示講稿引用 | Keep |
| `exports/technical_generalization_*`、`configs/technical_generalization_20261006.json` | Technical 泛化現況與缺資料稽核 | Keep |
| `research/registries/`、`research/current/`、`docs/長期研究規範.md` | 資料使用、實驗歷史與候選狀態治理 | Keep |
| `scripts/import_ml_kbars.py`、`scripts/import_industry_training_kbars.py`、`scripts/update_data.py` | 股票 K 線下載／匯入與更新 | Keep |
| `前端/`、後端 API 與資料庫 SQL | 系統展示與資料整合 | Keep（本次不處理） |

## REVIEW_REQUIRED（本次保留）

| 檔案／資料夾 | 原因 |
|---|---|
| `exports/early_followthrough/`、`entry_exit_improvement/`、`risk_ablation/`、`six_strategy_logic/`、`trade_failure_diagnosis/` 及對應 latest HTML | 雖屬舊探索，但被研究 Registry、凍結紀錄、文件與 Dashboard 直接引用，且包含失敗研究證據；直接刪除會破壞研究追溯。 |
| `exports/ml_trading_v1_20261003/`、`ml_trading_v2_20261004/`、`ml_trading_v3_20261004/`、`ml_trading_diagnosis_20261004/` 及 latest HTML | 新方向仍需保留 ML Alone 作比較；目前尚未確認哪一版會成為正式 ML V1 對照。 |
| `exports/ml_baseline_20260927/`、`clean_validation_20260926/` 與其 latest HTML | 被研究工作台、統一分析與文件引用；刪除前需先改寫索引與歷史說明。 |
| `exports/routing_simulation_*`、`regime_validation_*`、`four_group_*`、`risk_candidate_*` | 舊 Technical 探索輸出，但 Dashboard 與文件仍有直接連結。 |
| `configs/ml_v4_research_matrix.json`、一次性診斷／V4 腳本與對應測試 | 已不屬新主線，但仍互相引用並可重建本次刪除的輸出；後續可整批移入 archive 或刪除。 |
| `data/llm_cache/`、`trading_system/llm_analysis.py`、`scripts/build_unified_analysis.py` | LLM 已退出本輪範圍，但前端／統一分析仍可能引用；本次不刪。 |
| 舊進度 Markdown 與教師講稿 | 不參與執行，但仍是會議與研究決策紀錄；待另行決定是否移至 `docs/archive/`。 |

## 刪除後執行紀錄

已於 2026-10-09 完成：

- 刪除專案程式碼內的 `__pycache__`（未處理 `.venv/`）、`.pytest_cache/` 與 `.uv-cache/`。
- 刪除 `logs/` 與 `shioaji.log`；驗證期間套件曾重新產生，於驗證結束後再次清除。
- 刪除 8 個 `reports/2303/*report_test*` 資料夾，共 40 個未追蹤測試輸出檔案。
- 刪除表格中 7 組已結束或已被取代的 ML 探索輸出；相關一次性腳本與測試暫列 `REVIEW_REQUIRED`，因此仍可依原順序重新產生。
- 未刪除任何原始行情、`data/market_data.sqlite3`、K-Bar／Tick 資料、正式設定、前後端或核心程式。

### 刪除後驗證

- 必要資料、核心程式、正式設定與保留報告路徑：全部存在。
- `configs/*.json`：全部可解析。
- 核心 import：`ml.data_roles`、`ml.data_pipeline`、`ml.models`、`ml.walk_forward`、`ml.trading_backtest`、`ml.validation_access`、`trading_system.backtest`、`trading_system.research_guard` 全部成功。
- 輕量測試：34 tests + 9 subtests 通過。
- 未執行模型訓練、策略回測或任何 Holdout／Final OOS 資料讀取。
