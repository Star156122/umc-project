import copy,json,sys,unittest
from pathlib import Path
H=Path(__file__).resolve().parents[1];sys.path.insert(0,str(H/'程式'))
from engine import approved_quantity,commission,validate

class CashTests(unittest.TestCase):
 def setUp(self):self.c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
 def size(self,cash=1000000,price=100,liq=1000000,pos=None,ceiling=None,equity=1000000):
  return approved_quantity(price,cash,equity,liq,True,self.c,.0005,pos,ceiling)
 def test_first_order_including_fees_at_most_40_percent(self):
  q,caps=self.size();v=q*100.05+commission(q,100.05,self.c)
  self.assertLessEqual(v,400000);self.assertGreater(v,399000);self.assertNotIn('risk',caps)
 def test_second_order_uses_remaining_cash_40_percent(self):
  q,_=self.size();cash=1e6-q*100.05-commission(q,100.05,self.c)
  add,caps=self.size(cash=cash,pos={'budget':0,'stop':1,'batches':[]})
  value=add*100.05+commission(add,100.05,self.c)
  self.assertGreater(add,0);self.assertLessEqual(value,cash*.4);self.assertGreaterEqual(cash-value,200000)
 def test_fixed_reserve_dominates_near_floor(self):
  q,caps=self.size(cash=210000);value=q*100.05+commission(q,100.05,self.c)
  self.assertEqual(caps['spending_cap'],10000);self.assertGreaterEqual(210000-value,200000)
 def test_at_below_reserve_and_negative_cash_reject(self):
  for cash in [200000,199999,0,-100]:self.assertEqual(self.size(cash=cash)[0],0)
 def test_reserve_is_initial_not_current_equity(self):
  a,ca=self.size(cash=300000,equity=5000000);b,cb=self.size(cash=300000,equity=500000)
  self.assertEqual(a,b);self.assertEqual(ca['cash_reserve'],200000)
 def test_stop_distance_and_exhausted_budget_no_longer_limit_size(self):
  a,_=self.size(pos={'budget':0,'stop':1,'batches':[{'q':99999,'price':100,'fee':999}]})
  b,_=self.size(pos={'budget':0,'stop':99,'batches':[]})
  self.assertEqual(a,b);self.assertGreater(a,0)
 def test_liquidity_still_limits_and_invalid_unit_rejects(self):
  q,caps=self.size(liq=1);self.assertEqual(q,10)
  self.assertEqual(self.size(liq=0)[0],0)
 def test_gap_recheck_never_increases_queued_quantity(self):
  q,_=self.size();higher,_=self.size(price=200,ceiling=q);lower,_=self.size(price=50,ceiling=q)
  self.assertLess(higher,q);self.assertLessEqual(lower,q)
 def test_whole_and_odd_fees_fit_reserve_exactly(self):
  q,_=self.size(cash=300100,price=100)
  p=100.05;value=q*p+commission(q,p,self.c)
  self.assertLessEqual(value,100100);self.assertGreaterEqual(300100-value,200000)
 def test_guard_and_frozen_cash_contract(self):
  validate(self.c)
  for key,value in [('risk_budget_enabled',True),('cash_allocation_fraction',.8),('initial_cash_reserve_fraction',0)]:
   c=dict(self.c,**{key:value})
   with self.assertRaises(ValueError):validate(c)
 def test_holding_logic_unchanged(self):
  parent=(H.parent/'持有延期_2024Q4/程式/engine.py').read_text(encoding='utf8')
  local=(H/'程式/engine.py').read_text(encoding='utf8')
  self.assertEqual(parent[parent.index('def holding_review'):parent.index('def load_bars')],local[local.index('def holding_review'):local.index('def load_bars')])

if __name__=='__main__':unittest.main(verbosity=2)
