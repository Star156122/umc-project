-- 僅首次安裝使用：建立全新的整合版資料庫，不包含歷史帳號或回測資料。
CREATE DATABASE `umc_topic_integrated` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `umc_topic_integrated`;
SET NAMES utf8mb4;
CREATE TABLE `analysis_records` (
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `user_id` bigint(20) UNSIGNED NOT NULL,
  `stock_id` varchar(10) NOT NULL,
  `favorite_id` bigint(20) UNSIGNED DEFAULT NULL,
  `prediction` varchar(100) DEFAULT NULL,
  `signal` varchar(20) DEFAULT NULL,
  `strategy_type` varchar(50) DEFAULT NULL,
  `model_version` varchar(50) DEFAULT NULL,
  `analysis_date` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `backtest_results` (
  `backtest_id` bigint(20) UNSIGNED NOT NULL,
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `strategy_name` varchar(50) DEFAULT NULL,
  `backtest_start` date DEFAULT NULL,
  `backtest_end` date DEFAULT NULL,
  `initial_capital` decimal(16,2) DEFAULT NULL,
  `final_assets` decimal(16,2) DEFAULT NULL,
  `gross_pnl` decimal(16,2) DEFAULT NULL,
  `fee_amount` decimal(16,2) DEFAULT NULL,
  `tax_amount` decimal(16,2) DEFAULT NULL,
  `transaction_cost` decimal(16,2) DEFAULT NULL,
  `net_pnl` decimal(16,2) DEFAULT NULL,
  `completed_trades` int(10) UNSIGNED DEFAULT NULL,
  `return_rate` double DEFAULT NULL,
  `win_rate` double DEFAULT NULL,
  `sharpe_ratio` double DEFAULT NULL,
  `mdd` double DEFAULT NULL,
  `equity_curve` longtext DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `favorite_stocks` (
  `favorite_id` bigint(20) UNSIGNED NOT NULL,
  `user_id` bigint(20) UNSIGNED NOT NULL,
  `stock_id` varchar(10) NOT NULL,
  `added_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `market_data_days` (
  `stock_id` varchar(10) NOT NULL,
  `trade_date` date NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'complete',
  `row_count` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `market_ticks` (
  `tick_id` bigint(20) UNSIGNED NOT NULL,
  `stock_id` varchar(10) NOT NULL,
  `trade_date` date NOT NULL,
  `sequence_no` int(10) UNSIGNED NOT NULL,
  `exchange_timestamp` decimal(20,6) NOT NULL,
  `close_price` decimal(12,4) NOT NULL,
  `quantity` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `tick_type` tinyint(4) NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `model_predictions` (
  `model_prediction_id` bigint(20) UNSIGNED NOT NULL,
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `model_name` varchar(100) NOT NULL,
  `prediction` varchar(100) NOT NULL,
  `confidence` double DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `reports` (
  `report_id` bigint(20) UNSIGNED NOT NULL,
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `report_content` longtext NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `stocks` (
  `stock_id` varchar(10) NOT NULL,
  `stock_name` varchar(100) NOT NULL,
  `market` varchar(20) NOT NULL,
  `industry` varchar(100) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `technical_indicators` (
  `indicator_id` bigint(20) UNSIGNED NOT NULL,
  `stock_id` varchar(10) NOT NULL,
  `trade_date` datetime NOT NULL,
  `ma5` double DEFAULT NULL,
  `ma20` double DEFAULT NULL,
  `rsi` double DEFAULT NULL,
  `macd` double DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `users` (
  `user_id` bigint(20) UNSIGNED NOT NULL,
  `username` varchar(50) NOT NULL,
  `email` varchar(255) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `last_login` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE `analysis_records`
  ADD PRIMARY KEY (`analysis_id`),
  ADD KEY `idx_analysis_user_date` (`user_id`,`analysis_date`),
  ADD KEY `idx_analysis_stock_date` (`stock_id`,`analysis_date`),
  ADD KEY `idx_analysis_favorite` (`favorite_id`);

ALTER TABLE `backtest_results`
  ADD PRIMARY KEY (`backtest_id`),
  ADD UNIQUE KEY `uq_backtest_analysis` (`analysis_id`);

ALTER TABLE `favorite_stocks`
  ADD PRIMARY KEY (`favorite_id`),
  ADD UNIQUE KEY `uq_favorite_user_stock` (`user_id`,`stock_id`),
  ADD KEY `idx_favorite_stock` (`stock_id`);

ALTER TABLE `market_data_days`
  ADD PRIMARY KEY (`stock_id`,`trade_date`),
  ADD KEY `idx_market_days_status` (`status`,`trade_date`);

ALTER TABLE `market_ticks`
  ADD PRIMARY KEY (`tick_id`),
  ADD UNIQUE KEY `uq_market_tick_sequence` (`stock_id`,`trade_date`,`sequence_no`),
  ADD KEY `idx_market_tick_time` (`stock_id`,`exchange_timestamp`);

ALTER TABLE `model_predictions`
  ADD PRIMARY KEY (`model_prediction_id`),
  ADD KEY `idx_prediction_analysis` (`analysis_id`);

ALTER TABLE `reports`
  ADD PRIMARY KEY (`report_id`),
  ADD UNIQUE KEY `uq_report_analysis` (`analysis_id`);

ALTER TABLE `stocks`
  ADD PRIMARY KEY (`stock_id`);

ALTER TABLE `technical_indicators`
  ADD PRIMARY KEY (`indicator_id`),
  ADD UNIQUE KEY `uq_indicator_stock_date` (`stock_id`,`trade_date`);

ALTER TABLE `users`
  ADD PRIMARY KEY (`user_id`),
  ADD UNIQUE KEY `uq_users_username` (`username`),
  ADD UNIQUE KEY `uq_users_email` (`email`);

ALTER TABLE `analysis_records`
  MODIFY `analysis_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=6;

ALTER TABLE `backtest_results`
  MODIFY `backtest_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=6;

ALTER TABLE `favorite_stocks`
  MODIFY `favorite_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

ALTER TABLE `market_ticks`
  MODIFY `tick_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

ALTER TABLE `model_predictions`
  MODIFY `model_prediction_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

ALTER TABLE `reports`
  MODIFY `report_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=6;

ALTER TABLE `technical_indicators`
  MODIFY `indicator_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

ALTER TABLE `users`
  MODIFY `user_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=2;

ALTER TABLE `analysis_records`
  ADD CONSTRAINT `fk_analysis_favorite` FOREIGN KEY (`favorite_id`) REFERENCES `favorite_stocks` (`favorite_id`) ON DELETE SET NULL,
  ADD CONSTRAINT `fk_analysis_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`),
  ADD CONSTRAINT `fk_analysis_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE;

ALTER TABLE `backtest_results`
  ADD CONSTRAINT `fk_backtest_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

ALTER TABLE `favorite_stocks`
  ADD CONSTRAINT `fk_favorite_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`),
  ADD CONSTRAINT `fk_favorite_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE;

ALTER TABLE `market_data_days`
  ADD CONSTRAINT `fk_market_days_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`) ON DELETE CASCADE;

ALTER TABLE `market_ticks`
  ADD CONSTRAINT `fk_market_ticks_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`) ON DELETE CASCADE;

ALTER TABLE `model_predictions`
  ADD CONSTRAINT `fk_prediction_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

ALTER TABLE `reports`
  ADD CONSTRAINT `fk_report_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

ALTER TABLE `technical_indicators`
  ADD CONSTRAINT `fk_indicator_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`);
