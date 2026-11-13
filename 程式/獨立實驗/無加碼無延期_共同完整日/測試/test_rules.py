import copy,importlib.util,json,sys,unittest
from pathlib import Path
from datetime import datetime
H=Path(__file__).resolve().parents[1];sys.path.insert(0,str(H/'程式'))

class RulesTests(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('candidate_engine',H/'程式/engine_3231.py');self.e=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.e)
  self.c=json.loads((H/'設定/3231.json').read_text(encoding='utf8'))
 def fixture(self):
  e=self.e;day='2025-01-02';ts=int(datetime.fromisoformat(day+'T10:00:00+08:00').timestamp());bars=[]
  for i,p in enumerate([99,101,102,103,105,105]):
   bars.append({'kbar_timestamp':ts+i*300,'day':day,'time':datetime.fromtimestamp(ts+i*300,e.TZ).isoformat(),
    'Open':p,'High':p+.5,'Low':p-.5,'Close':p,'CLOSE':p,'Volume':10000,'liq':10000,'slot':i,'ready':True,
    'BREAKOUT_HIGH':104 if i>=3 else 100,'BREAKOUT_LOW':90,'BB_UPPER':100,'MA_FAST':99,'MA_MID':98,'MA_SLOW':95,'TREND_MA':97,'TREND_SLOPE':1})
  lookups={}
  for model in ['random_forest','xgboost','gru']:
   r={'stock_code':'3231','model':model,'signal_date':'2024-12-31','entry_date':day,'decision_time':'2024-12-31T23:59:59+08:00','prediction_source':'fixed_2023_2024_new_stock_inference','data_role':'validation','training_target_max':'2024-12-31','prediction':'HOLD','prob_sell':.2,'prob_hold':.7,'prob_buy':.1}
   lookups[model]={day:r}
  return {'bars':bars,'calendar':['2024-12-31',day,'2025-01-03','2025-01-06'],'old_map':{},'old_prev':{},'common':{day},'contexts':{day:{'new':True,'old':False,'index_reference':'2024-12-31'}},'ml':lookups['random_forest'],'ml_by_model':lookups}
 def test_full_scope_rejected(self):
  with self.assertRaises(ValueError):self.e.simulate(self.c,self.fixture(),'TECH',0,'full')
 def test_missing_model_rejected_not_silently_downgraded(self):
  data=self.fixture();data['ml_by_model'].pop('gru')
  with self.assertRaises(ValueError):self.e.simulate(self.c,data,'TECH',0)
 def test_complete_day_all_hold_is_eligible_for_pure_technical(self):
  r=self.e.simulate(self.c,self.fixture(),'TECH',0)
  self.assertEqual(r['metrics']['buys'],1);self.assertEqual(r['metrics']['adds'],0)
 def test_missing_or_late_prediction_blocks_all_new_entries(self):
  for kind in ['missing','late']:
   data=self.fixture()
   if kind=='missing':data['ml_by_model']['xgboost']={}
   else:data['ml_by_model']['gru']['2025-01-02']['decision_time']='2025-01-02T13:30:00+08:00'
   self.assertEqual(self.e.simulate(self.c,data,'TECH',0)['metrics']['buys'],0)
 def test_incomplete_day_blocks_entry(self):
  data=self.fixture();data['common']=set()
  self.assertEqual(self.e.simulate(self.c,data,'TECH',0)['metrics']['buys'],0)
 def test_hybrid_still_needs_own_buy(self):
  self.assertEqual(self.e.simulate(self.c,self.fixture(),'RF',0)['metrics']['buys'],0)
 def test_timeout_cannot_extend(self):
  pos={'held':162,'stop':97.5,'batches':[{'q':100,'price':100,'fee':20}]}
  self.assertEqual(self.e.holding_review(pos,{'Close':110},True,'BUY',self.c,0),('TIMEOUT',None))
 def test_cannot_enable_adds_or_extension(self):
  for change in ['add','extension']:
   c=copy.deepcopy(self.c)
   if change=='add':c['revised_max_adds']=1
   else:c['holding_extension']['enabled']=True
   with self.assertRaises(ValueError):self.e.validate(c)

if __name__=='__main__':unittest.main(verbosity=2)
