import unittest
from datetime import datetime,date
from trading_system.backtest import AppConfig
from trading_system.candidate_risk import CandidateRisk, RiskPolicy


class RiskTests(unittest.TestCase):
    def bot(self):
        b=object.__new__(CandidateRisk)
        b.config=AppConfig(strategy='breakout',min_hold_bars=4,stop_loss_pct=.02,take_profit_pct=0)
        b.entry_price=100; b.peak_close=100; b.bars_since_entry=1
        return b

    def test_stop_precedes_trend_and_minimum_hold(self):
        self.assertEqual(self.bot()._exit_signal(97,{'TREND_MA':99})[0],'stop_loss')

    def test_trend_bypasses_minimum_hold(self):
        self.assertEqual(self.bot()._exit_signal(99,{'TREND_MA':100})[0],'trend_break')

    def test_trailing_stays_armed(self):
        b=self.bot()
        self.assertIsNone(b._exit_signal(105,{'TREND_MA':90}))
        self.assertEqual(b._exit_signal(102.8,{'TREND_MA':90})[0],'trailing_stop')

    def test_normal_exit_waits(self):
        self.assertIsNone(self.bot()._exit_signal(100,{'TREND_MA':90,'BREAKOUT_LOW':101}))

    def test_day_change_keeps_cooldown(self):
        b=self.bot(); b.cooldown_remaining=24; b.current_trading_day=date(2026,1,2); b.entries_today=1
        b._roll_trading_day(datetime(2026,1,5,9))
        self.assertEqual(b.cooldown_remaining,24)
        self.assertEqual(b.entries_today,0)

    def test_disabling_trailing_does_not_exit_early(self):
        b=self.bot(); b.policy=RiskPolicy(early_trend_exit=False,trailing_enabled=False)
        b.peak_close=105
        self.assertIsNone(b._exit_signal(102,{'TREND_MA':90}))

    def test_trend_period_does_not_replace_entry_snapshot(self):
        b=self.bot(); b.risk_exit_ma=101
        snapshot={'TREND_MA':90}
        self.assertEqual(b._exit_signal(100,snapshot)[0],'trend_break')
        self.assertEqual(snapshot['TREND_MA'],90)

    def test_stop_cooldown_blocks_exact_number_of_bars(self):
        from datetime import timedelta
        b=self.bot(); b.policy=RiskPolicy(normal_cooldown=2,stop_cooldown=4)
        b.pending_exit='stop_loss'; b.is_entry=False; b.entries_today=0
        b.current_trading_day=date(2026,1,2)
        b.on_deal('test',{'action':'Sell','price':98})
        self.assertEqual(b.cooldown_remaining,5)
        b._has_required_indicators=lambda snapshot:True
        b._print_bar=lambda *args:None
        allowed=[]
        b._try_entry=lambda *args:allowed.append(args[1])
        start=datetime(2026,1,5,9)
        for n in range(5):
            b.process_completed_kbar('2303',start+timedelta(minutes=5*n),{'Close':100,'TREND_MA':90})
        self.assertEqual(allowed,[start+timedelta(minutes=20)])

    def test_below_activation_never_trails(self):
        b=self.bot(); b.policy=RiskPolicy(early_trend_exit=False)
        b.peak_close=102
        self.assertIsNone(b._exit_signal(99.5,{'TREND_MA':90}))
