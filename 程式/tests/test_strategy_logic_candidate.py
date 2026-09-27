import unittest
from unittest.mock import patch
from trading_system import backtest as bt
from trading_system.backtest import AppConfig
from trading_system.strategy_logic_candidate import StrategyLogicCandidate


RULES={'ma':{'max_slow_ma_distance_pct':.03,'minimum_trend_slope_pct':.002},
       'bollinger':{'minimum_close_location':.75},
       'breakout':{'minimum_breakout_pct':.002,'maximum_breakout_pct':.02}}


class LogicCandidateTests(unittest.TestCase):
    def bot(self,strategy):
        b=object.__new__(StrategyLogicCandidate); b.config=AppConfig(strategy=strategy,require_trend_filter=True)
        b.rules=RULES;b.daily_flags={'2026-01-02':True};b.rsi_armed=False;b.previous_snapshot=None
        b.is_bought=b.is_entry=b.is_exit=False;b.cooldown_remaining=0;b.entries_today=0
        return b

    def test_ma_rejects_chasing_and_weak_slope(self):
        b=self.bot('ma'); b.previous_snapshot={'MA_FAST':9,'MA_MID':10}
        snap={'MA_FAST':11,'MA_MID':10,'MA_SLOW':100,'TREND_MA':100,'TREND_SLOPE':1}
        self.assertTrue(b._ma_entry_reasons(102,snap))
        self.assertFalse(b._ma_entry_reasons(104,snap))
        snap['TREND_SLOPE']=.1
        self.assertFalse(b._ma_entry_reasons(102,snap))

    def test_rsi_needs_next_completed_bar(self):
        b=self.bot('rsi'); b.config=AppConfig(strategy='rsi',require_trend_filter=True,rsi_buy_above=55,rsi_buy_below=70)
        b.previous_snapshot={'RSI':54}
        snap={'RSI':56,'CLOSE':101,'TREND_MA':100,'TREND_SLOPE':1}
        self.assertFalse(b._rsi_entry_reasons(snap))
        b.previous_snapshot=snap
        self.assertTrue(b._rsi_entry_reasons({'RSI':57,'CLOSE':102,'TREND_MA':100,'TREND_SLOPE':1}))

    def test_daily_filter_applies_only_macd_vote(self):
        from datetime import datetime
        b=self.bot('macd'); b.daily_flags={'2026-01-02':False}
        self.assertFalse(b._can_enter(datetime(2026,1,2,10)))
        b.config=AppConfig(strategy='ma');self.assertTrue(b._can_enter(datetime(2026,1,2,10)))

    def test_bollinger_close_location_and_breakout_band(self):
        b=self.bot('bollinger')
        with patch.object(bt.MovingAverageTsst,'_bollinger_entry_reasons',return_value=['回到下軌']):
            self.assertTrue(b._bollinger_entry_reasons(109,{'HIGH':110,'LOW':100}))
            self.assertFalse(b._bollinger_entry_reasons(106,{'HIGH':110,'LOW':100}))
        b=self.bot('breakout')
        with patch.object(bt.MovingAverageTsst,'_breakout_entry_reasons',return_value=['突破']):
            self.assertTrue(b._breakout_entry_reasons(101,{'BREAKOUT_HIGH':100}))
            self.assertFalse(b._breakout_entry_reasons(100.1,{'BREAKOUT_HIGH':100}))
            self.assertFalse(b._breakout_entry_reasons(103,{'BREAKOUT_HIGH':100}))
