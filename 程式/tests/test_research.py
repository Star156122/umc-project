import tempfile
import unittest
from pathlib import Path
from trading_system.research import (classify_days, equity_days, aggregate_days,
                                     export_standalone_html, freeze_config, save_research, read_research)


class ResearchTests(unittest.TestCase):
    def test_labels_use_only_prior_days(self):
        prices = [('a', 100), ('b', 110), ('c', 1), ('d', 100)]
        self.assertEqual(classify_days(prices, 1)['c'], '上漲')
        self.assertEqual(classify_days(prices, 1)['d'], '下跌')
        self.assertEqual(classify_days(prices, 1)['b'], '資料不足')

    def test_flat_and_missing(self):
        self.assertEqual(classify_days([('a', 100), ('b', 100), ('c', 100)], 1)['c'], '盤整')
        with self.assertRaises(ValueError):
            classify_days([], 0)

    def test_mark_to_market_cost_and_conservation(self):
        bars = [{'kbar_timestamp': 1767315600, 'close': 110},
                {'kbar_timestamp': 1767402000, 'close': 105}]
        trades = [{'timestamp': 1767315600, 'action': 'Buy', 'quantity': 1,
                   'net_cash_flow': -101, 'fee': 1, 'tax': 0},
                  {'timestamp': 1767402000, 'action': 'Sell', 'quantity': 1,
                   'net_cash_flow': 103, 'fee': 1, 'tax': 1}]
        labels = {'2026-01-02': '上漲', '2026-01-03': '下跌'}
        days = equity_days(bars, trades, 1000, labels)
        self.assertEqual([d['pnl'] for d in days], [9, -7])
        self.assertAlmostEqual(days[-1]['drawdown'], 7 / 1009)
        groups = aggregate_days(days, 1000)
        self.assertEqual(sum(g['pnl'] for g in groups), 2)
        self.assertEqual(sum(g['cost'] for g in groups), 3)

    def test_freeze_preserves_baseline_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source.json', Path(directory)/'locked.json'
            source.write_text('{"period":20}')
            digest = freeze_config(source, target)
            source.write_text('{"period":30}')
            self.assertEqual(freeze_config(source, target), digest)
            target.write_text('{}')
            with self.assertRaises(ValueError):
                freeze_config(source, target)

    def test_database_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/'research.sqlite3'
            payload = {'created_at':'two', 'code':'2303', 'start':'2026-01-01',
                       'end':'2026-06-30', 'capital':100000, 'lookback':20,
                       'threshold':.05, 'config_sha256':'abc', 'parameter_status':'saved',
                       'strategies':[{'key':'ma','label':'MA','source':'report/summary.json',
                         'summary':{'total_return':.1,'max_drawdown':.02,'sharpe_ratio':1.2,
                                    'win_rate':.5,'transaction_cost':100,'buy_and_hold_return':.2},
                         'regimes':[{'regime':'上漲','days':1,'pnl':10,'contribution':.0001,
                                     'fills':1,'cost':2}],
                         'days':[{'day':'2026-01-02','regime':'上漲','equity':100010,
                                  'pnl':10,'drawdown':0,'fills':1,'cost':2}],
                         'trades':[{'datetime':'2026-01-02 09:00:00','timestamp':'1','action':'Buy',
                                    'price':'10','quantity':'1','fee':'1','tax':'0',
                                    'net_cash_flow':'-11','cash_after':'99989'}]}]}
            run_id = save_research(database, payload)
            restored = read_research(database)
            self.assertEqual(run_id, 1)
            self.assertEqual(restored['strategies'][0]['trades'][0]['action'], 'Buy')
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(database)) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'research_runs','strategy_metrics','regime_metrics',
                             'daily_risk','trade_records'} <= tables)

    def test_standalone_export_embeds_data_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            template, output = Path(directory)/'source.html', Path(directory)/'output.html'
            template.write_text('<html><main></main></html>', encoding='utf-8')
            export_standalone_html(template, output, {'text':'</script>'})
            content = output.read_text(encoding='utf-8')
            self.assertIn('window.__RESEARCH_DATA__', content)
            self.assertNotIn('</script>"', content)


if __name__ == '__main__':
    unittest.main()
