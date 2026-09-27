-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- 主機： 127.0.0.1
-- 產生時間： 2026-07-19 21:30:32
-- 伺服器版本： 10.4.32-MariaDB
-- PHP 版本： 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- 資料庫： `ai_stock_system`
--
CREATE DATABASE IF NOT EXISTS `ai_stock_system` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `ai_stock_system`;

-- --------------------------------------------------------

--
-- 資料表結構 `analysis_records`
--

DROP TABLE IF EXISTS `analysis_records`;
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

-- --------------------------------------------------------

--
-- 資料表結構 `backtest_results`
--

DROP TABLE IF EXISTS `backtest_results`;
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

-- --------------------------------------------------------

--
-- 資料表結構 `favorite_stocks`
--

DROP TABLE IF EXISTS `favorite_stocks`;
CREATE TABLE `favorite_stocks` (
  `favorite_id` bigint(20) UNSIGNED NOT NULL,
  `user_id` bigint(20) UNSIGNED NOT NULL,
  `stock_id` varchar(10) NOT NULL,
  `added_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- 資料表結構 `model_predictions`
--

DROP TABLE IF EXISTS `model_predictions`;
CREATE TABLE `model_predictions` (
  `model_prediction_id` bigint(20) UNSIGNED NOT NULL,
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `model_name` varchar(100) NOT NULL,
  `prediction` varchar(100) NOT NULL,
  `confidence` double DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- 資料表結構 `reports`
--

DROP TABLE IF EXISTS `reports`;
CREATE TABLE `reports` (
  `report_id` bigint(20) UNSIGNED NOT NULL,
  `analysis_id` bigint(20) UNSIGNED NOT NULL,
  `report_content` longtext NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- 資料表結構 `stocks`
--

DROP TABLE IF EXISTS `stocks`;
CREATE TABLE `stocks` (
  `stock_id` varchar(10) NOT NULL,
  `stock_name` varchar(100) NOT NULL,
  `market` varchar(20) NOT NULL,
  `industry` varchar(100) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- 資料表結構 `market_data_days`
--

DROP TABLE IF EXISTS `market_data_days`;
CREATE TABLE `market_data_days` (
  `stock_id` varchar(10) NOT NULL,
  `trade_date` date NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'complete',
  `row_count` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- 資料表結構 `market_ticks`
--

DROP TABLE IF EXISTS `market_ticks`;
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

-- --------------------------------------------------------

--
-- 資料表結構 `technical_indicators`
--

DROP TABLE IF EXISTS `technical_indicators`;
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

-- --------------------------------------------------------

--
-- 資料表結構 `users`
--

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `user_id` bigint(20) UNSIGNED NOT NULL,
  `username` varchar(50) NOT NULL,
  `email` varchar(255) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `last_login` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- 已傾印資料表的索引
--

--
-- 資料表索引 `analysis_records`
--
ALTER TABLE `analysis_records`
  ADD PRIMARY KEY (`analysis_id`),
  ADD KEY `idx_analysis_user_date` (`user_id`,`analysis_date`),
  ADD KEY `idx_analysis_stock_date` (`stock_id`,`analysis_date`),
  ADD KEY `idx_analysis_favorite` (`favorite_id`);

--
-- 資料表索引 `backtest_results`
--
ALTER TABLE `backtest_results`
  ADD PRIMARY KEY (`backtest_id`),
  ADD UNIQUE KEY `uq_backtest_analysis` (`analysis_id`);

--
-- 資料表索引 `favorite_stocks`
--
ALTER TABLE `favorite_stocks`
  ADD PRIMARY KEY (`favorite_id`),
  ADD UNIQUE KEY `uq_favorite_user_stock` (`user_id`,`stock_id`),
  ADD KEY `idx_favorite_stock` (`stock_id`);

--
-- 資料表索引 `model_predictions`
--
ALTER TABLE `model_predictions`
  ADD PRIMARY KEY (`model_prediction_id`),
  ADD KEY `idx_prediction_analysis` (`analysis_id`);

--
-- 資料表索引 `reports`
--
ALTER TABLE `reports`
  ADD PRIMARY KEY (`report_id`),
  ADD UNIQUE KEY `uq_report_analysis` (`analysis_id`);

--
-- 資料表索引 `stocks`
--
ALTER TABLE `stocks`
  ADD PRIMARY KEY (`stock_id`);

--
-- 資料表索引 `market_data_days`
--
ALTER TABLE `market_data_days`
  ADD PRIMARY KEY (`stock_id`,`trade_date`),
  ADD KEY `idx_market_days_status` (`status`,`trade_date`);

--
-- 資料表索引 `market_ticks`
--
ALTER TABLE `market_ticks`
  ADD PRIMARY KEY (`tick_id`),
  ADD UNIQUE KEY `uq_market_tick_sequence` (`stock_id`,`trade_date`,`sequence_no`),
  ADD KEY `idx_market_tick_time` (`stock_id`,`exchange_timestamp`);

--
-- 資料表索引 `technical_indicators`
--
ALTER TABLE `technical_indicators`
  ADD PRIMARY KEY (`indicator_id`),
  ADD UNIQUE KEY `uq_indicator_stock_date` (`stock_id`,`trade_date`);

--
-- 資料表索引 `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`user_id`),
  ADD UNIQUE KEY `uq_users_username` (`username`),
  ADD UNIQUE KEY `uq_users_email` (`email`);

--
-- 在傾印的資料表使用自動遞增(AUTO_INCREMENT)
--

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `analysis_records`
--
ALTER TABLE `analysis_records`
  MODIFY `analysis_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `backtest_results`
--
ALTER TABLE `backtest_results`
  MODIFY `backtest_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `favorite_stocks`
--
ALTER TABLE `favorite_stocks`
  MODIFY `favorite_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `model_predictions`
--
ALTER TABLE `model_predictions`
  MODIFY `model_prediction_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `reports`
--
ALTER TABLE `reports`
  MODIFY `report_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `market_ticks`
--
ALTER TABLE `market_ticks`
  MODIFY `tick_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `technical_indicators`
--
ALTER TABLE `technical_indicators`
  MODIFY `indicator_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `users`
--
ALTER TABLE `users`
  MODIFY `user_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- 已傾印資料表的限制式
--

--
-- 資料表的限制式 `analysis_records`
--
ALTER TABLE `analysis_records`
  ADD CONSTRAINT `fk_analysis_favorite` FOREIGN KEY (`favorite_id`) REFERENCES `favorite_stocks` (`favorite_id`) ON DELETE SET NULL,
  ADD CONSTRAINT `fk_analysis_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`),
  ADD CONSTRAINT `fk_analysis_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `backtest_results`
--
ALTER TABLE `backtest_results`
  ADD CONSTRAINT `fk_backtest_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `favorite_stocks`
--
ALTER TABLE `favorite_stocks`
  ADD CONSTRAINT `fk_favorite_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`),
  ADD CONSTRAINT `fk_favorite_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `model_predictions`
--
ALTER TABLE `model_predictions`
  ADD CONSTRAINT `fk_prediction_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `reports`
--
ALTER TABLE `reports`
  ADD CONSTRAINT `fk_report_analysis` FOREIGN KEY (`analysis_id`) REFERENCES `analysis_records` (`analysis_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `market_data_days`
--
ALTER TABLE `market_data_days`
  ADD CONSTRAINT `fk_market_days_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `market_ticks`
--
ALTER TABLE `market_ticks`
  ADD CONSTRAINT `fk_market_ticks_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`) ON DELETE CASCADE;

--
-- 資料表的限制式 `technical_indicators`
--
ALTER TABLE `technical_indicators`
  ADD CONSTRAINT `fk_indicator_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`stock_id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
