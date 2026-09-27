import unittest
from scripts.risk_ablation import check_period, paired_summary


class AblationTests(unittest.TestCase):
    def test_holdout_is_blocked(self):
        with self.assertRaises(ValueError):
            check_period({'period_start':'2025-07-01','period_end':'2025-12-31',
                          'holdout':{'start':'2025-07-01','end':'2025-12-31'}})

    def test_single_outlier_cannot_pass(self):
        def row(ret):
            return {'metrics':{'total_return':ret,'max_drawdown':.02,'transaction_cost':100,'completed_trades':10},
                    'diagnostics':{'stressed_return':ret-.001,'first_contribution':ret/2,'second_contribution':ret/2}}
        result=paired_summary([row(.5)]+[row(-.01) for _ in range(5)], [row(0) for _ in range(6)],
                              {'minimum_improved_stocks':4,'minimum_trades_per_stock':5,'minimum_stocks_with_enough_trades':4})
        self.assertEqual(result['status'],'未通過保留門檻')
        self.assertEqual(result['improved_stocks'],1)
