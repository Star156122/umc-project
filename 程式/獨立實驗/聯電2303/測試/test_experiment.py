import copy
import json
import sys
import unittest
from pathlib import Path
from datetime import datetime

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'程式'))
from engine import approved_quantity,round_lot,ml_status,validate,high_expiry,simulate,commission,HOME,TZ


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((HOME/'設定/experiment.json').read_text(encoding='utf-8'))
        # Legacy-unit test fixtures use round lots; odd-lot tests opt into one share explicitly.
        self.c['lot_size']=1000

    def test_replication_engine_has_only_stock_routing_changes(self):
        original=(HOME.parent/'程式/engine.py').read_text(encoding='utf-8')
        cloned=(HOME/'程式/engine.py').read_text(encoding='utf-8')
        expected=original.replace('ROOT = HOME.parent','ROOT = HOME.parents[1]')
        expected=expected.replace("c['stock'] != '2454'","c['stock'] != '2303'")
        expected=expected.replace('fixed to 2454','fixed to 2303').replace('2454_cash_dividends.json','2303_cash_dividends.json')
        self.assertEqual(cloned,expected)

    def test_date_lock_and_scope(self):
        for end in ['2025-07-01','2026-07-01','2025-06-30']:
            c=copy.deepcopy(self.c); c['period']['end']=end
            with self.assertRaises(ValueError):
                validate(c)

    def test_high_price_cannot_buy_one_lot(self):
        for revised in [False,True]:
            q,caps=approved_quantity(624,1000000,1000000,10000,revised,self.c,.0005)
            self.assertEqual(q,0)
            self.assertLess(caps['risk'],1000)

    def test_same_budget_can_buy_odd_quantity(self):
        self.c['lot_size']=1
        for revised in [False,True]:
            q,caps=approved_quantity(1000,1000000,1000000,10000,revised,self.c,.0005)
            self.assertGreater(q,0)
            self.assertLess(q,1000)
            self.assertLessEqual(q,caps['risk'])

    def test_small_odd_order_minimum_fees_in_risk(self):
        self.c['lot_size']=1
        pos={'budget':35,'stop':97.5,'batches':[]}
        q,_=approved_quantity(100,100000,1000000,10000,True,self.c,0,pos)
        self.assertEqual(q,0)  # Minimum buy+sell commissions alone exceed remaining budget.

    def test_mixed_board_and_odd_orders_pay_separate_fees(self):
        self.c['lot_size']=1
        self.assertEqual(commission(1001,10,self.c),40)
        self.assertEqual(commission(1000,10,self.c),20)

    def test_lot_is_not_maximum(self):
        self.assertEqual(round_lot(3999,self.c),3000)
        q,caps=approved_quantity(20,1000000,1000000,100000,True,self.c,0)
        self.assertGreater(q,1000)
        self.assertEqual(q%1000,0)

    def test_liquidity_units(self):
        # 50 lots/bar * 1000 shares/lot * 1% = 500 shares, not executable.
        q,caps=approved_quantity(20,1000000,1000000,50,True,self.c,0)
        self.assertEqual(caps['liquidity'],500)
        self.assertEqual(q,0)

    def test_cash_cannot_be_negative(self):
        q,caps=approved_quantity(20,19000,1000000,100000,True,self.c,0)
        self.assertEqual(q,0)

    def test_open_can_only_reduce(self):
        q,_=approved_quantity(20,1000000,1000000,100000,True,self.c,0,ceiling=1000)
        self.assertEqual(q,1000)
        q,_=approved_quantity(2000,1000000,1000000,100000,True,self.c,0,ceiling=1000)
        self.assertEqual(q,0)

    def test_add_does_not_get_new_budget(self):
        pos={'budget':10000,'stop':19.5,'batches':[{'q':15000,'price':20,'fee':427}]}
        q,caps=approved_quantity(21,600000,1000000,100000,True,self.c,0,pos)
        self.assertEqual(q,0)

    def test_three_market_sessions_not_calendar_days(self):
        cal=['2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11']
        self.assertEqual(high_expiry(cal[0],cal,self.c),'2024-01-10')

    def test_ml_future_and_previous_actual_session(self):
        cal=['2024-01-05','2024-01-08','2024-01-09']
        r={'signal_date':'2024-01-05','entry_date':'2024-01-08','is_oof':'True','data_role':'training',
           'label_version':'ml_v1_fixed_1pct','fold_train_end':'2023-12-31','fold_validation_start':'2024-01-01',
           'fold_validation_end':'2024-06-30','decision_time':'2024-01-05T13:30:00+08:00',
           'prediction':'BUY','prob_sell':'.1','prob_hold':'.2','prob_buy':'.7'}
        ts=datetime(2024,1,8,10,tzinfo=TZ).timestamp()
        self.assertEqual(ml_status('2024-01-08',ts,{'2024-01-08':r},cal),'BUY')
        r['decision_time']='2024-01-08T13:30:00+08:00'
        self.assertEqual(ml_status('2024-01-08',ts,{'2024-01-08':r},cal),'INVALID_OR_LATE')
        self.assertEqual(ml_status('2024-01-09',ts,{},cal),'MISSING')

    def test_chronology_and_stop_in_synthetic_trade(self):
        # Four real-time ordered bars: below breakout -> signal -> next open buy -> stop gap.
        ts=int(datetime(2024,1,8,9,tzinfo=TZ).timestamp())
        bars=[]
        for i,(o,h,l,p) in enumerate([(99,100,98,99),(100,102,99,101),(101,103,100,102),(95,97,94,96)]):
            b={'kbar_timestamp':ts+i*300,'day':'2024-01-08','time':datetime.fromtimestamp(ts+i*300,TZ).isoformat(),
               'Open':o,'High':h,'Low':l,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,
               'ready':True,'BREAKOUT_HIGH':100,'BREAKOUT_LOW':90,'BB_UPPER':100,
               'MA_FAST':99,'MA_MID':98,'MA_SLOW':95,'TREND_MA':97,'TREND_SLOPE':1}
            bars.append(b)
        data={'bars':bars,'calendar':['2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11'],
              'old_map':{b['kbar_timestamp']:b for b in bars},'old_prev':{b['kbar_timestamp']:(bars[i-1] if i else None) for i,b in enumerate(bars)},
              'contexts':{'2024-01-08':{'new':True,'old':True,'index_reference':'2024-01-05'}},
              'common':{'2024-01-08'},'ml':{}}
        r=simulate(self.c,data,'B',0)
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['orders'][0]['timestamp'],ts+600)
        self.assertEqual(r['orders'][-1]['reason'],'STOP_GAP')
        self.assertLess(r['metrics']['net_pnl'],0)
        self.assertTrue(all(e['cash']>=0 for e in r['equity']))
        self.assertEqual(r['orders'][0]['quantity']%1000,0)

    def test_odd_proxy_trade_cash_and_dividend_receivable_reconcile(self):
        self.c['lot_size']=1
        ts=int(datetime(2024,1,8,9,tzinfo=TZ).timestamp())
        bars=[]
        cases=[('2024-01-08',99,100,98,99),('2024-01-08',100,102,99,101),
               ('2024-01-08',101,103,100,102),('2024-01-09',92,94,90,93),('2024-01-09',93,95,92,94)]
        for i,(day,o,h,l,p) in enumerate(cases):
            stamp=ts+i*300 if i<3 else int(datetime(2024,1,9,9,0 if i==3 else 10,tzinfo=TZ).timestamp())
            bars.append({'kbar_timestamp':stamp,'day':day,'time':datetime.fromtimestamp(stamp,TZ).isoformat(),
               'Open':o,'High':h,'Low':l,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,
               'ready':True,'BREAKOUT_HIGH':100,'BREAKOUT_LOW':80,'BB_UPPER':100,
               'MA_FAST':99,'MA_MID':98,'MA_SLOW':85,'TREND_MA':97,'TREND_SLOPE':1})
        cal=['2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11']
        data={'bars':bars,'calendar':cal,'old_map':{},'old_prev':{},'common':set(), 'ml':{},
              'contexts':{d:{'new':True,'old':True,'index_reference':'2024-01-05'} for d in cal},
              'dividends':{'2024-01-09':{'cash_per_share':10,'payment_date':'2024-01-10'}}}
        r=simulate(self.c,data,'B',0)
        self.assertEqual(r['metrics']['trade_count'],1)
        self.assertNotIn('STOP',r['trades'][0]['exit_reason'])
        self.assertGreater(r['metrics']['cash_dividends'],0)
        self.assertEqual(r['metrics']['cash_dividends'],r['metrics']['receivables_at_end'])
        self.assertAlmostEqual(r['metrics']['final_equity']-self.c['initial_capital'],r['metrics']['net_pnl'])

    def test_odd_quantity_same_day_exit_is_not_fabricated(self):
        self.c['lot_size']=1
        ts=int(datetime(2024,1,8,9,tzinfo=TZ).timestamp())
        bars=[]
        for i,(o,h,l,p) in enumerate([(99,100,98,99),(100,102,99,101),(101,103,100,102),(95,97,94,96)]):
            bars.append({'kbar_timestamp':ts+i*300,'day':'2024-01-08','time':datetime.fromtimestamp(ts+i*300,TZ).isoformat(),
              'Open':o,'High':h,'Low':l,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,'ready':True,
              'BREAKOUT_HIGH':100,'BREAKOUT_LOW':90,'BB_UPPER':100,'MA_FAST':99,'MA_MID':98,'MA_SLOW':95,
              'TREND_MA':97,'TREND_SLOPE':1})
        data={'bars':bars,'calendar':['2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11'],
              'old_map':{},'old_prev':{},'contexts':{'2024-01-08':{'new':True,'old':True,'index_reference':'2024-01-05'}},
              'common':set(),'ml':{}}
        r=simulate(self.c,data,'B',0)
        self.assertEqual(len(r['orders']),1)
        self.assertEqual(r['metrics']['trade_count'],0)
        self.assertIsNotNone(r['open_position'])
        self.assertGreater(r['metrics']['deferred_exit_episodes'],0)
        self.assertLess(r['metrics']['unrealized_pnl'],0)

    def test_timeout_runs_even_without_legacy_signal_eligibility(self):
        self.c['max_hold_bars']=3
        ts=int(datetime(2024,1,8,9,tzinfo=TZ).timestamp())
        bars=[]
        for i,p in enumerate([99,101,102,102,102,102]):
            bars.append({'kbar_timestamp':ts+i*300,'day':'2024-01-08','time':datetime.fromtimestamp(ts+i*300,TZ).isoformat(),
              'Open':p,'High':p+1,'Low':p-1,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,'ready':True,
              'BREAKOUT_HIGH':100,'BREAKOUT_LOW':90,'BB_UPPER':100,'MA_FAST':99,'MA_MID':98,'MA_SLOW':95,
              'TREND_MA':97,'TREND_SLOPE':1})
        data={'bars':bars,'calendar':['2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11'],
              'old_map':{b['kbar_timestamp']:b for b in bars[:2]},
              'old_prev':{bars[0]['kbar_timestamp']:None,bars[1]['kbar_timestamp']:bars[0]},
              'contexts':{'2024-01-08':{'new':True,'old':True,'index_reference':'2024-01-05'}},
              'common':{'2024-01-08'},'ml':{}}
        r=simulate(self.c,data,'A',0)
        self.assertEqual(r['trades'][0]['exit_reason'],'TIMEOUT')
        self.assertEqual(r['orders'][-1]['timestamp'],ts+1500)


if __name__=='__main__':
    unittest.main(verbosity=2)
