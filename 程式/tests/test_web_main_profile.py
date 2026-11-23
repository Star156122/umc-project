import copy,json,unittest
from pathlib import Path
from hybrid.core import main_buy_strategy_v1 as engine
from hybrid.core.web_main_backtest import request_config,normalize,PROFILE
from trading_system.research_guard import assert_website_period

class MainProfileTests(unittest.TestCase):
 def test_real_profile_cash_and_no_add_extension(self):
  c=request_config('2357','2025-01-01','2025-03-31')
  self.assertEqual(c['initial_capital'],250000);self.assertFalse(c['risk_budget_enabled'])
  self.assertEqual(c['revised_max_adds'],0);self.assertFalse(c['holding_extension']['enabled'])
  self.assertEqual(c['allowed_scopes'],['common'])
  q,_=engine.approved_quantity(100,250000,250000,10000,True,c,.0005)
  self.assertLessEqual(q*100.05+engine.commission(q,100.05,c),100000)
 def test_stock_specific_coverage_and_future_guard(self):
  for code in ['2303','2330','2379','2454','3034','2357','3231','2892','4938']:
   c=request_config(code,'2025-01-01','2025-12-31',check_period=assert_website_period)
   self.assertEqual(c['datasets'][code]['predictions'],f'data/web_main_2025/predictions/{code}.json')
  with self.assertRaises(ValueError):
   request_config('2303','2025-01-01','2025-12-31')
  with self.assertRaises(ValueError):
   request_config('2303','2025-12-31','2026-01-01',check_period=assert_website_period)
 def test_period_and_policy_cannot_be_overridden(self):
  c=request_config('2357','2025-01-01','2025-03-31')
  for k,v in [('revised_max_adds',1),('risk_budget_enabled',True),('initial_capital',100000)]:
   with self.assertRaises(ValueError):engine.validate(dict(c,**{k:v}))
 def test_dataset_must_be_qa_passed(self):
  c=request_config('2357','2025-01-01','2025-03-31')
  c=copy.deepcopy(c);c['datasets']['2357']['qa_status']='PENDING'
  with self.assertRaises(ValueError):engine.validate(c)
 def test_engine_has_strict_three_model_coverage_and_no_extension(self):
  c=request_config('2357','2025-01-01','2025-03-31')
  self.assertEqual(engine.holding_review({'held':162},{},True,'BUY',c,0),('TIMEOUT',None))
  with self.assertRaises(ValueError):engine.simulate(c,{},'TECH',.0005,'full')
 def test_new_stock_data_loads_three_models(self):
  c=request_config('2357','2025-01-01','2025-03-31');data=engine.load_inputs(c)
  self.assertEqual(set(data['ml_by_model']),{'random_forest','xgboost','gru'})
  self.assertTrue(all(b['day']<='2025-03-31' for b in data['bars']))

if __name__=='__main__':unittest.main(verbosity=2)
