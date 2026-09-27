import unittest
from scripts.trade_failure_diagnosis import time_bucket,med,mean


class TradeFailureDiagnosisTests(unittest.TestCase):
    def test_time_buckets_have_fixed_edges(self):
        self.assertEqual(time_bucket('2026-01-01 09:59:59'),'09:00～10:00')
        self.assertEqual(time_bucket('2026-01-01 10:00:00'),'10:00～11:30')
        self.assertEqual(time_bucket('2026-01-01 11:30:00'),'11:30～12:30')

    def test_missing_values_do_not_become_zero(self):
        self.assertEqual(med([None,1,3]),2)
        self.assertEqual(mean([None,1,3]),2)
        self.assertIsNone(med([None]))
