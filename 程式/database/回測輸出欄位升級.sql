-- 修改日期：2026-08-24
-- 用途：將朋友匯出的舊版 ai_stock_system.backtest_results 升級為 main02.py 可寫入的格式。
-- 本檔只新增缺少欄位，不刪除既有資料；請先備份資料庫再由 phpMyAdmin 執行。

USE `ai_stock_system`;

ALTER TABLE `backtest_results`
  ADD COLUMN IF NOT EXISTS `strategy_name` varchar(50) DEFAULT NULL AFTER `analysis_id`,
  ADD COLUMN IF NOT EXISTS `backtest_start` date DEFAULT NULL AFTER `strategy_name`,
  ADD COLUMN IF NOT EXISTS `backtest_end` date DEFAULT NULL AFTER `backtest_start`,
  ADD COLUMN IF NOT EXISTS `initial_capital` decimal(16,2) DEFAULT NULL AFTER `backtest_end`,
  ADD COLUMN IF NOT EXISTS `final_assets` decimal(16,2) DEFAULT NULL AFTER `initial_capital`,
  ADD COLUMN IF NOT EXISTS `gross_pnl` decimal(16,2) DEFAULT NULL AFTER `final_assets`,
  ADD COLUMN IF NOT EXISTS `fee_amount` decimal(16,2) DEFAULT NULL AFTER `gross_pnl`,
  ADD COLUMN IF NOT EXISTS `tax_amount` decimal(16,2) DEFAULT NULL AFTER `fee_amount`,
  ADD COLUMN IF NOT EXISTS `transaction_cost` decimal(16,2) DEFAULT NULL AFTER `tax_amount`,
  ADD COLUMN IF NOT EXISTS `net_pnl` decimal(16,2) DEFAULT NULL AFTER `transaction_cost`,
  ADD COLUMN IF NOT EXISTS `completed_trades` int(10) UNSIGNED DEFAULT NULL AFTER `net_pnl`;
