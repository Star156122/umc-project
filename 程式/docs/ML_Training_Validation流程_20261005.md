# ML V3 Training / Validation 新流程

本流程只重整訓練與驗證架構。V3 的 Features、Label、模型主要參數與交易規則均未變更，也沒有建立 V4。

## 執行順序

1. `uv run python scripts/run_ml_training_cv.py`
   - 只讀取 2023–2024 Training。
   - 執行四折 expanding-window walk-forward。
   - 每折只用 Fold Training fit scaler、前處理與 class weight，並排除 `target_date` 跨界樣本。
   - GRU 只用 Fold Validation early stopping。
   - 依最差 Fold、平均及標準差選 V3 Candidate，再以完整 Training 重訓。
2. `uv run python scripts/run_ml_candidate_validation.py`
   - Candidate 完成後才可讀取 2025 H1 Controlled Validation。
   - 每次存取都寫入 `research/registries/ml_validation_access_log.csv`。
   - 結果不會自動修改模型。
3. `uv run python scripts/diagnose_ml_candidate_development.py`
   - 2026 H1 只做已看過資料的診斷，不選 Candidate、不自動調參。

2025 H2 Additional Holdout 與 2026-07-01 起 Final Out-of-Sample 仍由資料政策封鎖。舊的 `run_ml_trading_v3.py` 已停用，避免一次載入三個資料角色。
