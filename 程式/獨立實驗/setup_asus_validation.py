"""Freeze the user-authorized ASUS five-arm H1 2025 experiment before data access."""
import csv,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'獨立實驗/資金配置40_保留20';H=R/'獨立實驗/華碩2357_2025上半年'
if H.exists():raise RuntimeError('Preserve existing experiment; do not overwrite')
for d in ['程式','資料','設定','測試','結果','模型']:(H/d).mkdir(parents=True)
c=json.loads((P/'設定/experiment.json').read_text(encoding='utf8'))
c.update(experiment_id='asus2357-five-arm-validation-20261114-exp001',stock='2357',stock_name='華碩',stocks={'2357':'華碩'},
 period={'start':'2025-01-01','end':'2025-06-30'},warmup_start='2024-11-01',initial_capital=250000,
 data_role='validation_seen_period_new_stock',variants=['TECH','RF','XGB','GRU','BH'],
 training_stocks=['2303','2330','2379','2454','3034'],training_period={'start':'2023-01-01','end':'2024-12-31'},
 model_policy='Rebuild fixed ML V1 technology models on mature 2023-2024 labels only if matching saved models unavailable. No ASUS training, no 2025 labels, GRU fixed10 epochs; save artifacts and training/scaler hashes. No tuning.',
 buy_hold_fraction=.8,folds=[{'id':'VALIDATION_2025H1','start':'2025-01-01','end':'2025-06-30'}],
 status='PREREGISTERED_AUTHORIZED',comparison_plan='Five arms same stock, dates, initial capital, costs, bars and corporate actions; BH80% buy first legal09:10 open, hold to final close, reserve20%; four active arms cash40 reserve20 and conditional extension, ML BUY-only; full-period and common legal ML coverage separately; disclose exposure, not pooled. 1-4 trades insufficient. No parameter changes after outcomes. Proxy research, not final OOS.')
(H/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
for n in ['engine.py','prepare.py']:shutil.copy2(P/'程式'/n,H/'程式'/n)
ID=c['experiment_id']
for kind,key,row in [('experiment','experiment_id',dict(experiment_id=ID,strategy='buy_isolated_cash40_reserve20_extension',variant='BH_TECH_RF_XGB_GRU',test_start='2025-01-01',test_end='2025-06-30',dataset_role='validation_seen_period_new_stock',stock_codes='2357',status='preregistered',decision='NOT_RUN',holdout_used='false',notes=c['comparison_plan']+' User explicit: 去做. Data/model preflight first; no original DB writes; ordinary OHLC proxy as prior. Prior stock registry/config check no2357, confirm actual training rows. No post-result retuning.')),
 ('dataset','dataset_id',dict(dataset_id=ID,stock_code='2357',stock_name='華碩',industry='電腦及週邊設備',test_start='2025-01-01',test_end='2025-06-30',participated_in_development='false',results_viewed='false',influenced_strategy='false',current_classification='validation_seen_period_new_stock_pending_audit',clean_validation_eligible='false',experiment_ids=ID,notes='2025H1 already available validation period; new stock does not establish wholly untouched Final OOS. Warmup2024Nov onward. No locked dates. Fixed stock before outcomes.'))]:
 path=R/f'research/registries/{kind}_registry.csv'
 with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 assert not any(r[key]==ID for r in rows)
 if kind=='experiment':row.update(report_file='',result_file=str((H/'結果').relative_to(R)))
 else:row.update(report_location='',result_location=str((H/'資料').relative_to(R)))
 rows.append({k:row.get(k,'') for k in fields})
 with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 (H/f'設定/{kind}_preregistered.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
(H/'README.md').write_text('華碩2357，2025/01/01～06/30，每組25萬元；BH80%與TECH/RF/XGBoost/GRU五組，現金40%保留初始20%、無1%風險額度、條件延期。資料/模型資格先檢查；不是Final OOS。原策略與資料庫保持不變，失敗結果保留。',encoding='utf8')
print('REGISTERED',H)
