"""No market reads: check frozen contract and isolation before consumption."""
import ast,importlib.util,json,sys,unittest
from pathlib import Path
H=Path(__file__).resolve().parents[1];sys.path.insert(0,str(H/'程式'))
import formal_guard,engine
class Preflight(unittest.TestCase):
 def setUp(self):self.c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
 def test_sources_compile(self):
  for p in (H/'程式').glob('*.py'):ast.parse(p.read_text(encoding='utf8'))
 def test_scope_does_not_include_2026(self):
  for a,b in [('2026-01-01','2026-01-02'),('2026-07-01','2026-12-31'),('2023-01-01','2025-12-31')]:
   with self.assertRaises(ValueError):formal_guard.assert_period(a,b)
 def test_unconsumed_access_is_blocked(self):
  self.assertFalse((H/'稽核/consumed.json').exists())
  with self.assertRaises((FileNotFoundError,ValueError)):formal_guard.assert_period('2025-07-01','2025-07-01')
 def test_no_add_extension_common_and_capital(self):
  self.assertEqual(self.c['revised_max_adds'],0);self.assertFalse(self.c['holding_extension']['enabled'])
  self.assertEqual(self.c['allowed_scopes'],['common']);self.assertEqual(self.c['initial_capital'],250000)
 def test_cash_contract(self):
  q,cap=engine.cash_quantity(100,250000,100000,self.c,.0005)
  self.assertLessEqual(q*100.05+engine.commission(q,100.05,self.c),100000)
  self.assertEqual(engine.cash_quantity(100,50000,100000,self.c,0)[0],0)
 def test_timeout_still_exists(self):
  self.assertEqual(engine.holding_review({'held':162},{},True,'BUY',self.c,0),('TIMEOUT',None))
 def test_target_excluded_from_training(self):
  import csv
  rows=list(csv.DictReader((H/'模型/training_manifest.csv').open(encoding='utf-8-sig')))
  self.assertNotIn('4938',{r['stock_code'] for r in rows});self.assertTrue(all(r['target_date']<='2024-12-31' for r in rows))
 def test_general_guard_still_rejects_holdout(self):
  from trading_system.research_guard import assert_development_period
  with self.assertRaises(ValueError):assert_development_period('2025-07-01','2025-12-31')
if __name__=='__main__':unittest.main(verbosity=2)
