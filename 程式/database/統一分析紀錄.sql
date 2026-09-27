-- 後端統一分析紀錄：保存來源與結論，不改寫原始回測／ML 詳細表。
CREATE TABLE IF NOT EXISTS analysis_records (
  analysis_id VARCHAR(100) PRIMARY KEY,
  created_at DATETIME NOT NULL,
  stock_code VARCHAR(16),
  strategy_experiment_id VARCHAR(100),
  ml_experiment_id VARCHAR(100),
  data_role VARCHAR(60) NOT NULL,
  status VARCHAR(60) NOT NULL,
  strategy_summary_json JSON,
  ml_summary_json JSON,
  technical_indicator_json JSON,
  risk_json JSON,
  limitations_json JSON NOT NULL,
  source_files_json JSON NOT NULL
);

