import unittest
from trading_system.backtest import AppConfig
from trading_system.entry_exit_candidate import EntryExitCandidate,daily_entry_flags


class EntryExitTests(unittest.TestCase):
    def bot(self):
        b=object.__new__(EntryExitCandidate)
        b.config=AppConfig(strategy='rsi',min_hold_bars=4,stop_loss_pct=.02,take_profit_pct=0,rsi_sell_below=50)
        b.entry_price=100;b.peak_close=100;b.bars_since_entry=4;b.confirmation_bars=3;b.normal_exit_streak=0
        return b

    def test_daily_filter_has_no_same_day_or_future_leakage(self):
        prices=[(str(i),100+i) for i in range(30)]
        before=daily_entry_flags(prices)
        changed=prices[:25]+[(str(i),1) for i in range(25,30)]
        self.assertTrue(before['25'])
        self.assertEqual(before['25'],daily_entry_flags(changed)['25'])
        self.assertFalse(before['24'])

    def test_technical_exit_requires_consecutive_bars(self):
        b=self.bot(); weak={'RSI':40,'TREND_MA':90}; good={'RSI':60,'TREND_MA':90}
        self.assertIsNone(b._exit_signal(100,weak))
        self.assertIsNone(b._exit_signal(100,weak))
        self.assertIsNone(b._exit_signal(100,good))
        self.assertIsNone(b._exit_signal(100,weak))
        self.assertIsNone(b._exit_signal(100,weak))
        self.assertEqual(b._exit_signal(100,weak)[0],'technical_exit')

    def test_emergency_exits_never_wait_for_confirmation(self):
        b=self.bot();b.bars_since_entry=1
        self.assertEqual(b._exit_signal(97,{'TREND_MA':90,'RSI':40})[0],'stop_loss')
        b=self.bot();b.bars_since_entry=1
        self.assertEqual(b._exit_signal(100,{'TREND_MA':101,'RSI':40})[0],'trend_break')
        b=self.bot();b.peak_close=105;b.bars_since_entry=1
        self.assertEqual(b._exit_signal(102,{'TREND_MA':90,'RSI':40})[0],'trailing_stop')
