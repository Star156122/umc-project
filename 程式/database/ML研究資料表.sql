-- 機器學習研究資料表（SQLite 版本由 scripts/run_ml_baseline.py 自動建立）
-- MySQL / MariaDB 可使用本檔建立同樣的研究紀錄；不會修改既有回測資料表。
CREATE TABLE IF NOT EXISTS ml_experiments (
  experiment_id VARCHAR(100) PRIMARY KEY,
  status VARCHAR(40) NOT NULL,
  data_role VARCHAR(60) NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  target VARCHAR(120) NOT NULL,
  created_at DATETIME NOT NULL,
  plan_json JSON NOT NULL,
  audit_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_model_metrics (
  experiment_id VARCHAR(100) NOT NULL,
  model VARCHAR(40) NOT NULL,
  split_name VARCHAR(20) NOT NULL,
  samples INT NOT NULL,
  accuracy DOUBLE, precision_score DOUBLE, recall_score DOUBLE, f1 DOUBLE, roc_auc DOUBLE,
  training_seconds DOUBLE, prediction_seconds DOUBLE,
  confusion_matrix_json JSON NOT NULL,
  PRIMARY KEY (experiment_id, model, split_name)
);

CREATE TABLE IF NOT EXISTS ml_stock_metrics (
  experiment_id VARCHAR(100) NOT NULL,
  model VARCHAR(40) NOT NULL,
  split_name VARCHAR(20) NOT NULL,
  stock_code VARCHAR(16) NOT NULL,
  samples INT NOT NULL,
  accuracy DOUBLE, precision_score DOUBLE, recall_score DOUBLE, f1 DOUBLE, roc_auc DOUBLE,
  confusion_matrix_json JSON NOT NULL,
  PRIMARY KEY (experiment_id, model, split_name, stock_code)
);

