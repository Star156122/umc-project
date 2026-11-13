"""Prepare, but do not consume, explicitly authorized 2025H2 formal test."""
import csv,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1];B=R/'獨立實驗';P=B/'緯創3231_2025上半年';H=B/'和碩4938_2025正式保留測試'
if H.exists():raise RuntimeError('Preserve existing formal run')
for d in ['程式','設定','資料','模型','結果','測試','稽核']:(H/d).mkdir(parents=True)
c=json.loads((B/'無加碼無延期_共同完整日/設定/3231.json').read_text(encoding='utf8'))
c.update(experiment_id='pegatron4938-formal-2025h2-20261114-exp001',stock='4938',stock_name='和碩',stocks={'4938':'和碩'},
 period={'start':'2025-01-01','end':'2025-12-31'},data_role='formal_additional_holdout_with_seen_h1_context',
 windows=[{'id':'Q1','start':'2025-01-01','end':'2025-03-31','role':'validation_seen'},
 {'id':'H1','start':'2025-01-01','end':'2025-06-30','role':'validation_seen'},
 {'id':'YEAR','start':'2025-01-01','end':'2025-12-31','role':'mixed_seen_and_formal_holdout'},
 {'id':'H2','start':'2025-07-01','end':'2025-12-31','role':'formal_additional_holdout'}],
 folds=[{'id':'FIXED_2025','start':'2025-01-01','end':'2025-12-31'}],
 authorization='User explicitly selected: 申請使用2025全年作正式最終保留測試，先凍結規格再驗證。',
 evaluation='Report all five arms; main5bps, common complete day+3 legal models, no adds or extension. Formal judgment only H2, each ML vsTECH net return positive difference AND MDD no worse AND >=5 trades; otherwise FAIL/INSUFFICIENT. All results retained, no result-based tuning. YEAR not wholly unseen. Single stock cannot prove generalization. Final2026July stays locked.',
 status='PREPARED_NOT_CONSUMED')
(H/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
s=(B/'無加碼無延期_共同完整日/程式/engine_3231.py').read_text(encoding='utf8').replace('3231','4938').replace('緯創','和碩')
s=s.replace("SOURCE_HOME = HOME.parent / '和碩4938_2025上半年'","SOURCE_HOME = HOME")
s=s.replace('from trading_system.research_guard import assert_development_period, assert_timestamps','from formal_guard import assert_period as assert_development_period, assert_timestamps')
s=s.replace("{'start':'2025-01-01','end':'2025-06-30'}","{'start':'2025-01-01','end':'2025-12-31'}")
s=s.replace('datetime(2025,7,1,tzinfo=TZ)','datetime(2026,1,1,tzinfo=TZ)').replace("backtest_end='2025-06-30'","backtest_end='2025-12-31'").replace("backfill_end='2025-06-30'","backfill_end='2025-12-31'")
s=s.replace("r['data_role']=='validation'", "r['data_role']==('additional_holdout' if day>='2025-07-01' else 'validation')")
(H/'程式/engine.py').write_text(s,encoding='utf8')
for file in ['prepare.py','infer_models.py','report.css']:
 s=(P/'程式'/file).read_text(encoding='utf8').replace('3231','4938').replace('緯創','和碩')
 if file=='infer_models.py':
  s=s.replace('from trading_system.research_guard import assert_timestamps,assert_development_period','from formal_guard import assert_timestamps,assert_period as assert_development_period')
  s=s.replace("entry<='2025-06-30'","entry<='2025-12-31'")
  s=s.replace("'data_role':'validation'", "'data_role':('additional_holdout' if entry>='2025-07-01' else 'validation')")
 (H/'程式'/file).write_text(s,encoding='utf8')
for p in (P/'模型').rglob('*'):
 if p.is_file():target=H/'模型'/p.relative_to(P/'模型');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
events={'events':[{'ex_date':'2025-06-17','payment_date':'2025-07-09','cash_per_share':4.50282529}],
 'sources':['https://superpy.kgieworld.com.tw/kgipythonapi/stockInfo/finance/exDividend?stockCode=4938','https://ww2.money-link.com.tw/TWStock/StockBasic.aspx?SymId=4938&TWMId=Basic_fic04'],
 'announced_before_ex_date':True,'note':'Adjusted cash/share; July payment known metadata, not hindsight price adjustment.'}
(H/'資料/4938_cash_dividends.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf8')
for kind,key,row in [('experiment','experiment_id',dict(experiment_id=c['experiment_id'],strategy='cash40reserve20_noadds_noextension',variant='FORMAL_FIXED_BH_TECH_RF_XGB_GRU',test_start='2025-01-01',test_end='2025-12-31',dataset_role='seen_H1_plus_formal_additional_holdout_H2',stock_codes='4938',status='prepared_unconsumed',decision='NOT_RUN',holdout_used='pending_explicitly_authorized',notes=c['authorization']+' '+c['evaluation'])),
 ('dataset','dataset_id',dict(dataset_id=c['experiment_id'],stock_code='4938',stock_name='和碩',industry='科技',test_start='2025-01-01',test_end='2025-12-31',participated_in_development='false',results_viewed='false',influenced_strategy='false',current_classification='formal_h2_authorized_unconsumed',clean_validation_eligible='false',experiment_ids=c['experiment_id'],notes='First authorized2025H2 access must atomically create consumed marker before request. No reset;2026July remains forbidden.'))]:
 p=R/f'research/registries/{kind}_registry.csv'
 with p.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 assert not any(r[key]==c['experiment_id'] for r in rows);rows.append({k:row.get(k,'') for k in fields})
 with p.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
print('PREPARED_NOT_ACCESSED',H)
