import unittest
from scripts.regime_validation import assess


class ValidationTests(unittest.TestCase):
    def test_no_trades_never_pass(self):
        row = dict(stocks=6, days=100, fills=0, profitable=0, median=0)
        self.assertEqual(assess(row, row)[0], '暫不交易')

    def test_later_failure_blocks_candidate(self):
        early = dict(stocks=6, days=100, fills=20, profitable=4, median=.02)
        late = dict(early, profitable=2, median=-.01)
        self.assertEqual(assess(early, late)[0], '暫不交易')

    def test_consistent_evidence_only_is_research_candidate(self):
        row = dict(stocks=6, days=100, fills=20, profitable=4, median=.02)
        self.assertEqual(assess(row, row)[0], '研究候選')
