"""Preregister and isolate a holding-extension candidate. No market replay."""
import csv,json,shutil,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'獨立實驗';PARENT=BASE/'科技五股_2024Q4';H=BASE/'持有延期_2024Q4'
ID='isolated-buy-holding-extension-20261114-exp001'
if H.exists():raise RuntimeError('Candidate exists; do not overwrite')
for name in ['程式','設定','測試','資料','結果']:(H/name).mkdir(parents=True)
c=json.loads((PARENT/'設定/experiment.json').read_text(encoding='utf8'))
c.update(experiment_id=ID,replication_parent=c['experiment_id'],status='IMPLEMENTATION_ONLY_NOT_BACKTESTED',
 holding_extension={'enabled':True,'extra_bars':54,'max_extensions':1,'require_net_profit':True,
 'require_ml_buy':True,'require_market':True,'trend_rule':'ready AND Close>MA_FAST>MA_MID AND Close>TREND_MA AND TREND_SLOPE>0',
 'review':'every_completed_bar_from_original_deadline','exit':'next_legal_observed_open; existing non-daytrade restrictions remain',
 'protective_exits_have_priority':True,'stop_can_only_tighten':True,'allow_adds_during_extension':False,
 'hard_cap':'162+54 observed holding bars; execution restrictions can delay fills beyond decision cap',
 'purpose':'Do not exit a profitable continuing trend solely due to timeout; no claim of predicting future prices.'})
(H/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
for p in (PARENT/'資料').glob('*.json'):shutil.copy2(p,H/'資料'/p.name)
for name in ['engine.py','prepare.py','report.js','report.css','run.py']:shutil.copy2(PARENT/'程式'/name,H/'程式'/name)
for kind,key,row in [
 ('experiment','experiment_id',dict(experiment_id=ID,strategy='technical_v2b_buy_isolated',variant='D_PLUS_SINGLE_HOLDING_EXTENSION',test_start='2024-10-01',test_end='2024-12-31',dataset_role='training_development_exposed',stock_codes='2303|2330|2379|2454|3034',status='implementation_not_run',decision='NOT_BACKTESTED',holdout_used='false',notes='User explicitly requested enabling conditional holding extension. One change: from 162 observed bars, profitable trend+official market+legal RF BUY may extend once by54, check each close, no stop widening/add reset, max decision216. Protective exits first, no adds during extension. Defaults research starting values, no scan. Parent8 trades all exited before timeout: no evidence extension fixes them. Preserve baseline. Implement+synthetic tests only; no market replay requested.')),
 ('dataset','dataset_id',dict(dataset_id=ID,stock_code='2303|2330|2379|2454|3034',stock_name='聯電|台積電|瑞昱|聯發科|聯詠',industry='科技／半導體便利樣本',test_start='2024-10-01',test_end='2024-12-31',participated_in_development='true',results_viewed='false',influenced_strategy='false',current_classification='training_development_exposed',clean_validation_eligible='false',experiment_ids=ID,notes='Implementation only, no new market replay. Existing exposed data and frozen RF OOF; locked periods forbidden.'))]:
 path=ROOT/f'research/registries/{kind}_registry.csv'
 with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 if any(r[key]==ID for r in rows):raise RuntimeError('Duplicate registration')
 if kind=='experiment':row.update(report_file='',result_file='獨立實驗/持有延期_2024Q4/結果')
 else:row.update(report_location='',result_location='獨立實驗/持有延期_2024Q4/資料')
 rows.append({k:row.get(k,'') for k in fields})
 with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 (H/f'設定/{kind}_preregistered.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
(H/'設定/baseline_hashes.json').write_text(json.dumps({str(p.relative_to(PARENT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [PARENT/'程式/engine.py',PARENT/'設定/experiment.json',PARENT/'結果/20261114T003636/metrics.csv']},indent=2),encoding='utf8')
print('PREREGISTERED_AND_COPIED',H)
