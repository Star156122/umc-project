"""Register a cash-allocation candidate; no backtest execution."""
import csv,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'獨立實驗/持有延期_2024Q4';H=R/'獨立實驗/資金配置40_保留20'
if H.exists():raise RuntimeError('Do not overwrite candidate')
for n in ['程式','資料','設定','測試','結果']:(H/n).mkdir(parents=True)
c=json.loads((P/'設定/experiment.json').read_text(encoding='utf8'))
c.update(experiment_id='isolated-buy-cash40-reserve20-20261114-exp001',replication_parent=c['experiment_id'],
 position_sizing_mode='cash_fraction_with_fixed_reserve',risk_budget_enabled=False,
 cash_allocation_fraction=.4,initial_cash_reserve_fraction=.2,status='IMPLEMENTATION_ONLY_NOT_BACKTESTED')
for d,pattern in [('程式','*'),('資料','*.json')]:
 for p in (P/d).glob(pattern):
  if p.is_file():shutil.copy2(p,H/d/p.name)
(H/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
for kind,key in [('experiment','experiment_id'),('dataset','dataset_id')]:
 row=json.loads((P/f'設定/{kind}_preregistered.json').read_text(encoding='utf8'))
 row[key]=c['experiment_id']
 if kind=='experiment':row.update(status='implementation_not_run',decision='NOT_BACKTESTED',variant='D_EXTENSION_CASH40_RESERVE20',notes='User explicitly cancels risk budget. Single change position sizing: first/add max40% current available cash including fees, reserve20% initial capital fixed, prior20 volume1% cap, recompute next open and never increase queued shares. No 1% loss ceiling, no guarantee of maximum loss. Existing stop/ML/extension/one-add unchanged. No market replay.',report_file='',result_file=str((H/'結果').relative_to(R)))
 else:row.update(experiment_ids=c['experiment_id'],results_viewed='false',report_location='',result_location=str((H/'資料').relative_to(R)),notes='Implementation only; no backtest; exposed2024Q4, locked dates unused.')
 path=R/f'research/registries/{kind}_registry.csv'
 with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 assert not any(r[key]==row[key] for r in rows)
 rows.append({k:row.get(k,'') for k in fields})
 with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 (H/f'設定/{kind}_preregistered.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
print('REGISTERED',H)
