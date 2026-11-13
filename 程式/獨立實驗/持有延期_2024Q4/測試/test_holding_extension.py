import copy,importlib.util,json,sys,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
H=Path(__file__).resolve().parents[1];sys.path.insert(0,str(H/'程式'))
import engine

class ExtensionTests(unittest.TestCase):
 def setUp(self):
  self.c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
  self.pos={'held':162,'stop':97.5,'batches':[{'q':100,'price':100,'fee':20}],'adds':0}
  self.bar={'ready':True,'Close':110,'MA_FAST':107,'MA_MID':105,'TREND_MA':103,'TREND_SLOPE':1}
 def test_grant_once_without_changing_stop_size_or_clock(self):
  old=copy.deepcopy(self.pos)
  exit,event=engine.holding_review(self.pos,self.bar,True,'BUY',self.c,.0005)
  self.assertIsNone(exit);self.assertEqual(event['action'],'GRANT')
  for key in ['held','stop','batches','adds']:self.assertEqual(self.pos[key],old[key])
  self.pos['held']=163
  self.assertEqual(engine.holding_review(self.pos,self.bar,True,'BUY',self.c,0)[1]['action'],'CONTINUE')
  self.assertEqual(self.pos['extension_count'],1)
 def test_before_deadline_is_untouched(self):
  self.pos['held']=161
  self.assertEqual(engine.holding_review(self.pos,self.bar,True,'BUY',self.c,0),(None,None))
 def test_gross_gain_insufficient_for_fees_is_denied(self):
  self.bar['Close']=100.1
  exit,event=engine.holding_review(self.pos,self.bar,True,'BUY',self.c,0)
  self.assertIn('net_profit_positive',event['failed']);self.assertIsNotNone(exit)
 def test_all_non_buy_statuses_deny_extension(self):
  for ml in ['HOLD','SELL','MISSING','INVALID_OR_LATE']:
   pos=copy.deepcopy(self.pos)
   self.assertEqual(engine.holding_review(pos,self.bar,True,ml,self.c,0)[0],'TIMEOUT_EXTENSION_DENIED')
 def test_market_trend_and_missing_indicators_deny(self):
  self.assertIsNotNone(engine.holding_review(self.pos,self.bar,False,'BUY',self.c,0)[0])
  for k,v in [('MA_FAST',None),('TREND_SLOPE',0),('ready',False),('TREND_MA',111)]:
   bar=dict(self.bar,**{k:v});self.assertIsNotNone(engine.holding_review(self.pos,bar,True,'BUY',self.c,0)[0])
 def test_protective_exit_cannot_be_cancelled(self):
  for reason in ['STOP_GAP_DEFERRED','TECHNICAL_30_LOW','DATA_GAP_NEXT_OBSERVED_OPEN_DEFERRED']:
   self.assertEqual(engine.holding_review(self.pos,self.bar,True,'BUY',self.c,0,reason)[0],reason)
   self.assertNotIn('extension_count',self.pos)
 def test_hard_cap_never_renews(self):
  for held in [216,217,300]:
   self.pos.update(held=held,extension_count=1)
   self.assertEqual(engine.holding_review(self.pos,self.bar,True,'BUY',self.c,0)[0],'TIMEOUT_EXTENSION_CAP')
 def test_failure_during_extension_exits(self):
  self.pos.update(held=180,extension_count=1)
  self.assertEqual(engine.holding_review(self.pos,self.bar,True,'HOLD',self.c,0)[0],'EXTENSION_REVIEW_FAILED')
 def test_disabled_preserves_timeout(self):
  c=copy.deepcopy(self.c);c['holding_extension']['enabled']=False
  self.assertEqual(engine.holding_review(self.pos,self.bar,True,'BUY',c,0),('TIMEOUT',None))
 def test_guard_and_frozen_max_extension(self):
  for start,end in [('2025-07-01','2025-12-31'),('2026-07-01','2026-12-31')]:
   c=dict(self.c,period={'start':start,'end':end})
   with self.assertRaises(ValueError):engine.validate(c)
  c=copy.deepcopy(self.c);c['holding_extension']['extra_bars']=108
  with self.assertRaises(ValueError):engine.validate(c)
 def fixture(self):
  bars=[];days=['2024-10-07','2024-10-08']
  for i in range(80):
   day=days[i//50];ts=int(datetime.fromisoformat(day+'T09:10:00+08:00').timestamp())+i%50*300
   p=[99,101,102,104,106][min(i,4)]
   bars.append({'kbar_timestamp':ts,'day':day,'time':datetime.fromtimestamp(ts,engine.TZ).isoformat(),
     'Open':p,'High':p+.5,'Low':p-.5,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,'ready':True,
     'BREAKOUT_HIGH':100,'BREAKOUT_LOW':90,'BB_UPPER':100,'MA_FAST':99,'MA_MID':98,'MA_SLOW':95,'TREND_MA':97,'TREND_SLOPE':1})
  return {'bars':bars,'calendar':['2024-10-04',*days,'2024-10-09','2024-10-11'],
    'old_map':{},'old_prev':{},'common':set(),'ml':{},'contexts':{d:{'new':True,'old':True,'index_reference':'2024-10-04'} for d in days}}
 def test_real_simulator_grants_once_and_exits_next_open_at_cap(self):
  c=copy.deepcopy(self.c);c.update(max_hold_bars=3,lot_size=1000)
  data=self.fixture()
  with patch.object(engine,'ml_status',return_value='BUY'):
   r=engine.simulate(c,data,'D',0)
  self.assertEqual(r['metrics']['holding_extensions'],1)
  self.assertEqual(r['trades'][0]['exit_reason'],'TIMEOUT_EXTENSION_CAP')
  self.assertEqual(r['trades'][0]['holding_bars'],57)
  self.assertEqual(r['orders'][-1]['timestamp'],data['bars'][59]['kbar_timestamp'])
  self.assertAlmostEqual(r['metrics']['net_pnl'],sum(t['net_pnl'] for t in r['trades']))
 def test_disabled_simulator_matches_frozen_parent(self):
  parent=H.parent/'科技五股_2024Q4/程式/engine.py'
  spec=importlib.util.spec_from_file_location('frozen_parent_engine',parent);baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
  c=copy.deepcopy(self.c);c.update(max_hold_bars=3,lot_size=1000);c['holding_extension']['enabled']=False
  data=self.fixture()
  with patch.object(engine,'ml_status',return_value='BUY'),patch.object(baseline,'ml_status',return_value='BUY'):
   new=engine.simulate(c,data,'D',0);old=baseline.simulate(c,data,'D',0)
  self.assertEqual(new['orders'],old['orders']);self.assertEqual(new['equity'],old['equity'])
  for k,v in old['metrics'].items():self.assertEqual(new['metrics'][k],v)
 def test_failed_extension_review_sells_on_next_bar_not_signal_close(self):
  c=copy.deepcopy(self.c);c.update(max_hold_bars=3,lot_size=1000);data=self.fixture()
  boundary=data['bars'][5]['kbar_timestamp']+300
  def ml(day,ts,*args):return 'HOLD' if ts>=boundary else 'BUY'
  with patch.object(engine,'ml_status',side_effect=ml):r=engine.simulate(c,data,'D',0)
  self.assertEqual(r['trades'][0]['exit_reason'],'EXTENSION_REVIEW_FAILED')
  self.assertEqual(r['orders'][-1]['timestamp'],data['bars'][6]['kbar_timestamp'])
 def test_original_stop_still_exits_during_extension(self):
  c=copy.deepcopy(self.c);c.update(max_hold_bars=3,lot_size=1000);data=self.fixture()
  data['bars'][5]['Low']=90
  with patch.object(engine,'ml_status',return_value='BUY'):r=engine.simulate(c,data,'D',0)
  self.assertEqual(r['trades'][0]['exit_reason'],'STOP_INTRABAR')
  self.assertEqual(r['orders'][-1]['timestamp'],data['bars'][5]['kbar_timestamp'])
 def test_new_breakout_during_extension_cannot_add(self):
  c=copy.deepcopy(self.c);c.update(max_hold_bars=3,lot_size=1000);data=self.fixture()
  data['bars'][5]['BREAKOUT_HIGH']=150
  with patch.object(engine,'ml_status',return_value='BUY'):r=engine.simulate(c,data,'D',0)
  events=[e for e in r['events'] if e['timestamp']==data['bars'][6]['kbar_timestamp']]
  self.assertEqual(len(events),1);self.assertTrue(events[0]['is_add'])
  self.assertIn('持倉與每日次數',events[0]['all_blockers'])
  self.assertEqual(r['metrics']['adds'],0)
 def test_add_budget_logic_is_unchanged(self):
  parent=(H.parent/'科技五股_2024Q4/程式/engine.py').read_text(encoding='utf8')
  current=(H/'程式/engine.py').read_text(encoding='utf8')
  self.assertEqual(parent[parent.index('def approved_quantity'):parent.index('def high_expiry')],current[current.index('def approved_quantity'):current.index('def high_expiry')])

if __name__=='__main__':unittest.main(verbosity=2)
