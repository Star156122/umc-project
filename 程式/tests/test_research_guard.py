import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from trading_system.research_guard import assert_development_period, assert_config, assert_payload, HoldoutLockedError


class HoldoutTests(unittest.TestCase):
    def test_overlap_and_inclusive_edges(self):
        for start,end in [('2025-07-01','2025-07-01'),('2025-12-31','2025-12-31'),
                          ('2025-01-01','2026-01-01'),('2025-06-30','2025-07-02')]:
            with self.subTest(start=start), self.assertRaises(HoldoutLockedError):
                assert_development_period(start,end)

    def test_allowed_edges_and_reversed_dates(self):
        assert_development_period('2025-01-01','2025-06-30')
        assert_development_period('2026-01-01','2026-06-30')
        with self.assertRaises(ValueError):
            assert_development_period('2026-02-01','2026-01-01')

    def test_backfill_also_locked(self):
        config=SimpleNamespace(backtest_start='2026-01-01',backtest_end='2026-06-30',
                               backfill_start='2025-12-01',backfill_end='2025-12-31')
        with self.assertRaises(HoldoutLockedError):
            assert_config(config)

    def test_nested_report_rejected_but_reservation_allowed(self):
        assert_payload({'holdout':{'start':'2025-07-01','end':'2025-12-31'}})
        with self.assertRaises(HoldoutLockedError):
            assert_payload({'records':[{'backtest_start':'2025-07-01','backtest_end':'2025-12-31'}]})

    def test_direct_cache_and_fetch_block_before_io(self):
        from trading_system import backtest as bt
        config=bt.AppConfig(backtest_start='2025-07-01',backtest_end='2025-12-31')
        with patch.object(bt.sqlite3,'connect') as connect:
            for call in [lambda:bt.open_tick_cache(config), lambda:bt.load_cached_kbar_frame(config),
                         lambda:bt.fetch_sinopac_ticks(Mock(),config),lambda:bt.build_tsst(config)]:
                with self.assertRaises(HoldoutLockedError):
                    call()
            connect.assert_not_called()

    def test_report_and_experiment_block(self):
        from scripts.entry_exit_report import write_report
        from scripts.risk_ablation import read_frame
        from trading_system.backtest import AppConfig
        with self.assertRaises(HoldoutLockedError):
            write_report({'plan':{'period_start':'2025-07-01','period_end':'2025-12-31'}})
        connection=Mock()
        with self.assertRaises(HoldoutLockedError):
            read_frame(connection,AppConfig(backtest_start='2025-07-01',backtest_end='2025-12-31'))
        connection.execute.assert_not_called()

    def test_no_config_unlock(self):
        import trading_system.research_guard as guard
        with patch.object(guard.POLICY.__class__,'read_text',return_value='{"holdout":{"access":"allowed"}}'):
            with self.assertRaises(HoldoutLockedError):
                assert_development_period('2026-01-01','2026-06-30')

    def test_actual_tick_dates_block_without_config(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from trading_system.backtest import replay_ticks
        bot=Mock()
        stamp=datetime(2025,7,1,tzinfo=ZoneInfo('Asia/Taipei')).timestamp()
        with self.assertRaises(HoldoutLockedError):
            replay_ticks(bot,[{'timestamp':stamp}])
        bot.on_stock_tick.assert_not_called()
