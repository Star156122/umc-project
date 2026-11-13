"""Preregister two new-stock replications, preserving prior ASUS experiment."""
import csv,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1];B=R/'獨立實驗';P=B/'華碩2357_2025上半年'
for stock,name,industry,training in [('3231','緯創','科技',['2303','2330','2379','2454','3034']),('2892','第一金','金融',['2881','2882','2884','2886','2891'])]:
 H=B/f'{name}{stock}_2025上半年'
 if H.exists():raise RuntimeError('Preserve existing experiment: '+str(H))
 for d in ['程式','設定','資料','測試','結果','模型']:(H/d).mkdir(parents=True)
 c=json.loads((P/'設定/experiment.json').read_text(encoding='utf8'))
 c.update(experiment_id=f'new-stock-{stock}-five-arm-20261114-exp001',stock=stock,stock_name=name,stocks={stock:name},training_stocks=training,
    industry=industry,model_policy='Reuse matching fixed technology V1 artifacts with verified hashes' if stock=='3231' else 'Train fixed financial V1 on mature2023-2024 peer labels only, fixed10 GRU epochs; no target stock or2025 fitting',
    status='PREREGISTERED_AUTHORIZED',selection_rule='Two names fixed before new data/results; absent from registry/main config; target excluded from actual training manifests.')
 (H/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
 for file in ['engine.py','prepare.py','infer_models.py','train_models.py','prepare_official.py','run_asus.py','report.js','report.css','build_trade_charts.py']:
  s=(P/'程式'/file).read_text(encoding='utf8').replace('2357',stock).replace('華碩',name).replace('ASUS',name).replace('asus_in_training','target_in_training')
  s=s.replace('原五檔','原產業五檔')
  if file=='report.js':
   a=s.index('<p>');# keep generic main rendering; replace original corporate paragraph only
   s=s.replace('6/26除息每股34元：除息時認列應收，已公告的7/17付款日只作現金入帳判斷，不查7月行情；期末仍未發放的股利納入資產但不當成可用現金。',
       '6/3除息每股3.79927437元，6/27發放；除息時認列應收，發放後才成為可用現金。' if stock=='3231' else '本次暖機及2025上半年期間沒有已識別的除權息事件，不納入期間以外的股利。')
  if file=='prepare_official.py':
   a=s.index("events={'events'");b=s.index("(H/'資料/",a)
   events={'events':[{'ex_date':'2025-06-03','payment_date':'2025-06-27','cash_per_share':3.79927437}] if stock=='3231' else [],
    'sources':['https://www.wistron.com/en/Investors/ShareholdersServices/DividendHistory','https://superpy.kgieworld.com.tw/kgipythonapi/stockInfo/finance/exDividend?stockCode=3231'] if stock=='3231' else ['https://superpy.kgieworld.com.tw/kgipythonapi/stockInfo/finance/exDividend?stockCode=2892'],
    'scope':'Only 2024Nov through2025June actions; no subsequent market data accessed.'}
   s=s[:a]+'events='+repr(events)+'\n'+s[b:]
  (H/'程式'/file).write_text(s,encoding='utf8')
 for file in ['test_validation.py','qa.cjs','qa_trade_charts.cjs']:
  s=(P/'測試'/file).read_text(encoding='utf8').replace('2357',stock).replace('華碩',name).replace('asus_in_training','target_in_training')
  if file=='test_validation.py':s=s.replace("dict(self.c,stock='2330')", "dict(self.c,stock='2330')")
  (H/'測試'/file).write_text(s,encoding='utf8')
 for file in (P/'資料').glob('FMTQIK_*.json'):shutil.copy2(file,H/'資料'/file.name)
 shutil.copy2(P/'資料/index_daily.json',H/'資料/index_daily.json')
 if stock=='3231':
  for p in (P/'模型').rglob('*'):
   if p.is_file():target=H/'模型'/p.relative_to(P/'模型');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
  p=H/'模型/training_audit.json';a=json.loads(p.read_text(encoding='utf8'));a['target_in_training']=False;a['reused_from']='華碩2357 fixed technology models; artifact parameters/weights unchanged';p.write_text(json.dumps(a,ensure_ascii=False,indent=2),encoding='utf8')
 for kind,key,row in [('experiment','experiment_id',dict(experiment_id=c['experiment_id'],strategy='cash40_reserve20_extension',variant='BH_TECH_RF_XGB_GRU',test_start='2025-01-01',test_end='2025-06-30',dataset_role='validation_seen_period_new_stock',stock_codes=stock,status='preregistered',decision='NOT_RUN',holdout_used='false',notes=c['comparison_plan']+' User requested another tech+financial stock, same time/capital; fixed selection before results; no tuning. '+c['model_policy'])),
 ('dataset','dataset_id',dict(dataset_id=c['experiment_id'],stock_code=stock,stock_name=name,industry=industry,test_start='2025-01-01',test_end='2025-06-30',participated_in_development='false',results_viewed='false',influenced_strategy='false',current_classification='validation_seen_period_new_stock',clean_validation_eligible='false',experiment_ids=c['experiment_id'],notes='Stock absent from prior registry/config; actual training excludes target. New data read only bounded2024Nov-2025June; locked dates forbidden.'))]:
  path=R/f'research/registries/{kind}_registry.csv'
  with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
  assert not any(r[key]==c['experiment_id'] for r in rows)
  if kind=='experiment':row.update(report_file='',result_file=str((H/'結果').relative_to(R)))
  else:row.update(report_location='',result_location=str((H/'資料').relative_to(R)))
  rows.append({k:row.get(k,'') for k in fields})
  with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
  (H/f'設定/{kind}_preregistered.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
 (H/'README.md').write_text(f'{name}{stock} 2025上半年五組各25萬元；已事前固定名單與規則，未執行前先核對資料及模型。不是Final OOS。',encoding='utf8')
print('PREREGISTERED_3231_2892')
