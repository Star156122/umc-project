import unittest
from unittest.mock import patch
from trading_system.strategy_logic_candidate import StrategyLogicCandidate
from trading_system.early_followthrough_candidate import EarlyFollowThroughCandidate


class EarlyFollowThroughTests(unittest.TestCase):
    def bot(self):
        b=object.__new__(EarlyFollowThroughCandidate);b.pending_origin=None;b.pending_age=0
        b.origin_signals=b.confirmed_signals=b.cancelled_signals=0;b.current_completed_time='K0'
        return b

    def test_waits_k1_and_confirms_only_above_k0_high(self):
        b=self.bot()
        with patch.object(StrategyLogicCandidate,'_entry_reasons',return_value=['原訊號']):
            self.assertEqual(b._entry_reasons(100,{'HIGH':101,'LOW':99}),[])
        self.assertEqual(b.origin_signals,1)
        reasons=b._entry_reasons(102,{'HIGH':103,'LOW':100})
        self.assertTrue(any('突破K0最高價' in x for x in reasons));self.assertEqual(b.confirmed_signals,1)

    def test_cancels_after_one_non_confirming_bar(self):
        b=self.bot()
        with patch.object(StrategyLogicCandidate,'_entry_reasons',return_value=['原訊號']):
            b._entry_reasons(100,{'HIGH':101,'LOW':99})
        self.assertEqual(b._entry_reasons(101,{'HIGH':102,'LOW':99}),[])
        self.assertIsNone(b.pending_origin);self.assertEqual(b.cancelled_signals,1)

    def test_no_percentage_threshold_or_multiple_bar_setting(self):
        source=__import__('inspect').getsource(EarlyFollowThroughCandidate._entry_reasons)
        self.assertIn("close>origin['high']",source)
        self.assertNotIn('0.00',source)
