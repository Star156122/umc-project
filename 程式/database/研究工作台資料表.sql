-- 策略研究工作台使用的 MariaDB 資料表。
-- 只新增資料表，不刪除既有的 users、stocks、backtest_results 或 reports。
USE `ai_stock_system`;

CREATE TABLE IF NOT EXISTS `experiment_batches` (
  `batch_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `batch_key` VARCHAR(100) NOT NULL,
  `experiment_name` VARCHAR(100) NOT NULL,
  `parameter_version` VARCHAR(100) NOT NULL,
  `parameter_sha256` CHAR(64) NOT NULL,
  `period_start` DATE NOT NULL,
  `period_end` DATE NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'planned',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`batch_id`),
  UNIQUE KEY `uq_experiment_batch_key` (`batch_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `experiment_stocks` (
  `batch_id` BIGINT UNSIGNED NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `stock_name` VARCHAR(100) NOT NULL,
  `industry` VARCHAR(100) NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  `error_message` TEXT DEFAULT NULL,
  PRIMARY KEY (`batch_id`,`stock_code`),
  CONSTRAINT `fk_experiment_stock_batch` FOREIGN KEY (`batch_id`) REFERENCES `experiment_batches` (`batch_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `research_runs` (
  `run_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `source_key` CHAR(64) NOT NULL,
  `created_at` DATETIME NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `period_start` DATE NOT NULL,
  `period_end` DATE NOT NULL,
  `initial_capital` DECIMAL(16,2) NOT NULL,
  `lookback_days` INT UNSIGNED NOT NULL,
  `regime_threshold` DOUBLE NOT NULL,
  `config_sha256` CHAR(64) NOT NULL,
  `parameter_status` VARCHAR(500) NOT NULL,
  `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`run_id`),
  UNIQUE KEY `uq_research_source` (`source_key`),
  KEY `idx_research_stock_period` (`stock_code`,`period_start`,`period_end`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE `research_runs` ADD COLUMN IF NOT EXISTS `batch_id` BIGINT UNSIGNED NULL AFTER `run_id`;

CREATE TABLE IF NOT EXISTS `strategy_metrics` (
  `run_id` BIGINT UNSIGNED NOT NULL,
  `strategy_key` VARCHAR(30) NOT NULL,
  `strategy_label` VARCHAR(100) NOT NULL,
  `source_report` VARCHAR(500) NOT NULL,
  `ranking` INT UNSIGNED NOT NULL,
  `total_return` DOUBLE NOT NULL,
  `max_drawdown` DOUBLE NOT NULL,
  `sharpe_ratio` DOUBLE NOT NULL,
  `win_rate` DOUBLE NOT NULL,
  `transaction_cost` DECIMAL(16,4) NOT NULL,
  `buy_and_hold_return` DOUBLE NOT NULL,
  PRIMARY KEY (`run_id`,`strategy_key`),
  KEY `idx_strategy_ranking` (`run_id`,`ranking`),
  CONSTRAINT `fk_strategy_research_run` FOREIGN KEY (`run_id`) REFERENCES `research_runs` (`run_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `regime_metrics` (
  `run_id` BIGINT UNSIGNED NOT NULL,
  `strategy_key` VARCHAR(30) NOT NULL,
  `regime` VARCHAR(20) NOT NULL,
  `trading_days` INT UNSIGNED NOT NULL,
  `pnl` DECIMAL(16,4) NOT NULL,
  `contribution` DOUBLE NOT NULL,
  `fills` INT UNSIGNED NOT NULL,
  `transaction_cost` DECIMAL(16,4) NOT NULL,
  PRIMARY KEY (`run_id`,`strategy_key`,`regime`),
  CONSTRAINT `fk_regime_strategy` FOREIGN KEY (`run_id`,`strategy_key`) REFERENCES `strategy_metrics` (`run_id`,`strategy_key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `daily_risk` (
  `run_id` BIGINT UNSIGNED NOT NULL,
  `strategy_key` VARCHAR(30) NOT NULL,
  `trade_date` DATE NOT NULL,
  `regime` VARCHAR(20) NOT NULL,
  `equity` DECIMAL(16,4) NOT NULL,
  `daily_pnl` DECIMAL(16,4) NOT NULL,
  `max_drawdown` DOUBLE NOT NULL,
  `fills` INT UNSIGNED NOT NULL,
  `transaction_cost` DECIMAL(16,4) NOT NULL,
  PRIMARY KEY (`run_id`,`strategy_key`,`trade_date`),
  KEY `idx_daily_risk_regime` (`run_id`,`regime`,`trade_date`),
  CONSTRAINT `fk_daily_strategy` FOREIGN KEY (`run_id`,`strategy_key`) REFERENCES `strategy_metrics` (`run_id`,`strategy_key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `trade_records` (
  `run_id` BIGINT UNSIGNED NOT NULL,
  `strategy_key` VARCHAR(30) NOT NULL,
  `sequence_no` INT UNSIGNED NOT NULL,
  `traded_at` DATETIME NOT NULL,
  `trade_timestamp` BIGINT NOT NULL,
  `action` VARCHAR(10) NOT NULL,
  `price` DECIMAL(16,4) NOT NULL,
  `quantity` INT UNSIGNED NOT NULL,
  `fee` DECIMAL(16,4) NOT NULL,
  `tax` DECIMAL(16,4) NOT NULL,
  `net_cash_flow` DECIMAL(16,4) NOT NULL,
  `cash_after` DECIMAL(16,4) NOT NULL,
  PRIMARY KEY (`run_id`,`strategy_key`,`sequence_no`),
  KEY `idx_trade_time` (`run_id`,`strategy_key`,`traded_at`),
  CONSTRAINT `fk_trade_strategy` FOREIGN KEY (`run_id`,`strategy_key`) REFERENCES `strategy_metrics` (`run_id`,`strategy_key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
