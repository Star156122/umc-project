import copy,json,sys,unittest
from pathlib import Path
from datetime import datetime
H=Path(__file__).resolve().parents[1];sys.path.insert(0,str(H/'程式'))
import engine
class ValidationTests(unittest.TestCase):
 def setUp(self):self.c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
 def test_fixed_scope_and_locked_dates(self):
  engine.validate(self.c)
  for start,end in [('2025-07-01','2025-12-31'),('2026-07-01','2026-12-31'),('2024-01-01','2024-12-31')]:
   c=dict(self.c,period={'start':start,'end':end})
   with self.assertRaises(ValueError):engine.validate(c)
  with self.assertRaises(ValueError):engine.validate(dict(self.c,stock='2330'))
 def test_cash40_reserve20_on_250k(self):
  q,caps=engine.approved_quantity(600,250000,250000,100000,True,self.c,.0005)
  spend=q*600.3+engine.commission(q,600.3,self.c)
  self.assertLessEqual(spend,100000);self.assertGreaterEqual(250000-spend,50000)
  self.assertEqual(engine.approved_quantity(600,50000,250000,100000,True,self.c,.0005)[0],0)
 def test_fixed_prediction_is_legal_without_fake_oof(self):
  cal=['2024-12-31','2025-01-02'];r={'signal_date':cal[0],'entry_date':cal[1],'stock_code':'3231','model':'gru',
    'decision_time':'2024-12-31T23:59:59+08:00','prediction_source':'fixed_2023_2024_new_stock_inference','data_role':'validation',
    'training_target_max':'2024-12-31','prediction':'BUY','prob_sell':.1,'prob_hold':.2,'prob_buy':.7}
  ts=datetime.fromisoformat('2025-01-02T09:10:00+08:00').timestamp()
  self.assertEqual(engine.ml_status(cal[1],ts,{cal[1]:r},cal),'BUY')
  for key,val in [('training_target_max','2025-01-02'),('stock_code','2330'),('decision_time','2025-01-02T13:30:00+08:00')]:
   wrong=dict(r,**{key:val});self.assertEqual(engine.ml_status(cal[1],ts,{cal[1]:wrong},cal),'INVALID_OR_LATE')
 def test_sizing_and_holding_functions_match_parent(self):
  parent=(H.parent/'資金配置40_保留20/程式/engine.py').read_text(encoding='utf8');current=(H/'程式/engine.py').read_text(encoding='utf8')
  parent=parent.replace("'ml_buy':ml=='BUY'","'ml_buy':(ml=='BUY' if c.get('extension_requires_ml',True) else True)")
  for start,end in [('def holding_review','def load_bars'),('def cash_quantity','def high_expiry')]:
   self.assertEqual(parent[parent.index(start):parent.index(end)],current[current.index(start):current.index(end)])
 def test_training_manifest_excludes_asus_and_future(self):
  import pandas as pd
  m=pd.read_csv(H/'模型/training_manifest.csv',dtype=str)
  self.assertNotIn('3231',set(m.stock_code));self.assertTrue((m.target_date<='2024-12-31').all())
  self.assertEqual(set(m.stock_code),set(self.c['training_stocks']))
 def test_pure_technical_extension_does_not_require_any_ml(self):
  pos={'held':162,'stop':97.5,'batches':[{'q':100,'price':100,'fee':20}]}
  bar={'ready':True,'Close':110,'MA_FAST':107,'MA_MID':105,'TREND_MA':103,'TREND_SLOPE':1}
  exit,event=engine.holding_review(pos,bar,True,'NOT_APPLICABLE',dict(self.c,extension_requires_ml=False),0)
  self.assertIsNone(exit);self.assertEqual(event['action'],'GRANT')
  pos.pop('extension_count')
  exit,event=engine.holding_review(pos,bar,True,'MISSING',dict(self.c,extension_requires_ml=True),0)
  self.assertEqual(exit,'TIMEOUT_EXTENSION_DENIED')
if __name__=='__main__':unittest.main(verbosity=2)
