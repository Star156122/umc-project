"""Create a preregistered frozen five-stock replication; no simulation here."""
import csv,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'獨立實驗'
HOME=BASE/'科技五股_2024Q4'
SOURCE=BASE/'聯電2303'
ID='isolated-buy-five-tech-2024q4-20261114-exp001'
stocks={'2303':'聯電','2330':'台積電','2379':'瑞昱','2454':'聯發科','3034':'聯詠'}
for name in ['程式','設定','資料','結果','測試']:(HOME/name).mkdir(parents=True,exist_ok=True)
c=json.loads((SOURCE/'設定/experiment.json').read_text(encoding='utf8'))
c.update(experiment_id=ID,period={'start':'2024-10-01','end':'2024-12-31'},variants=['D'],stocks=stocks,
         warmup_start='2024-09-16',status='PREREGISTERED_FIXED_REPLICATION',stock_name='聯電',
         replication_parent='isolated-buy-2303-20261113-fixed-replication-exp001',
         selection_rule='Freeze the five existing peer-pool listed technology stocks before simulation; not random or representative of all technology stocks.',
         interpretation='Only revised+same ML; no original-strategy improvement claim; independent 1m per stock, not a pooled portfolio.')
(HOME/'設定/experiment.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
e=(SOURCE/'程式/engine.py').read_text(encoding='utf8')
e=e.replace("{'start':'2023-01-01','end':'2024-12-31'} or c['stock'] != '2303'","{'start':'2024-10-01','end':'2024-12-31'} or c['stock'] not in ['2303','2330','2379','2454','3034']")
e=e.replace('Experiment is fixed to 2303, 2023–2024','Experiment is fixed to preregistered five stocks, 2024Q4')
e=e.replace('datetime(2023,1,1,tzinfo=TZ)','datetime(2024,9,16,tzinfo=TZ)')
e=e.replace("HOME/'資料/2303_cash_dividends.json'","HOME/('資料/'+c['stock']+'_cash_dividends.json')")
e=e.replace("window=('2023-01-01','2024-12-31')","window=(c['period']['start'],c['period']['end'])")
(HOME/'程式/engine.py').write_text(e,encoding='utf8')
shutil.copy2(SOURCE/'程式/prepare.py',HOME/'程式/prepare.py')
shutil.copy2(BASE/'資料/index_daily.json',HOME/'資料/index_daily.json')
shutil.copy2(SOURCE/'程式/report.css',HOME/'程式/report.css')
for s in stocks:
 events=[{'ex_date':'2024-12-12','payment_date':'2025-01-09','cash_per_share':3.99963706}] if s=='2330' else []
 obj={'events':events,'scope':'Only actions from warmup start 2024-09-16 through 2024-12-31; earlier actions outside 240-bar rolling window.',
      'source':'https://investor.tsmc.com/english/dividends/2q24' if s=='2330' else {'2303':'https://www.umc.com/en/IR_Stock/dividend_information','2379':'https://www.realtek.com/InvestorRelations/DividendInfo?menu_id=712','2454':'https://corp.mediatek.com/investor-relations/shareholder-services/dividend-information','3034':'https://www.novatek.com.tw/zh-TW/IR_info/ir_dividendInformation'}[s]}
 (HOME/f'資料/{s}_cash_dividends.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
for kind,key,row in [
 ('experiment','experiment_id',dict(experiment_id=ID,strategy='technical_v2b_buy_isolated',variant='D_MODIFIED_SAME_RF_BUY_ONLY',test_start='2024-10-01',test_end='2024-12-31',dataset_role='training_development_exposed',stock_codes='|'.join(stocks),status='preregistered',decision='NOT_RUN',holdout_used='false',notes='User requested five technology stocks, Q4 2024, revised+ML only. Fixed existing peer pool before results. No tuning/retraining. Hypothesis: sparse trades and heterogeneous outcomes across stocks; descriptive replication, not before/after improvement. 1m independent per stock, cash start Oct1. Warmup Sep16 only. Same risk/cost/proxy execution. Preserve zero and failed results. No parameter scan.')),
 ('dataset','dataset_id',dict(dataset_id=ID,stock_code='|'.join(stocks),stock_name='|'.join(stocks.values()),industry='科技／半導體（便利樣本）',test_start='2024-10-01',test_end='2024-12-31',participated_in_development='true',results_viewed='false',influenced_strategy='false',current_classification='training_development_exposed',clean_validation_eligible='false',experiment_ids=ID,notes='Existing RF OOF; 2024Q4 only; warmup 2024-09-16. Locked dates forbidden. Five independent accounts; no original arm requested.'))]:
 path=ROOT/f'research/registries/{kind}_registry.csv'
 with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 if any(r[key]==ID for r in rows):raise RuntimeError('Experiment already registered; do not overwrite')
 row.update(report_file='獨立實驗/科技五股_2024Q4/結果/index.html',result_file='獨立實驗/科技五股_2024Q4/結果') if kind=='experiment' else row.update(report_location='獨立實驗/科技五股_2024Q4/結果/index.html',result_location='獨立實驗/科技五股_2024Q4/資料')
 rows.append({k:row.get(k,'') for k in fields})
 with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 (HOME/f'設定/{kind}_registry_prerun.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
(HOME/'README.md').write_text('科技五股 2024Q4：修改策略＋相同 ML。事前固定聯電、台積電、瑞昱、聯發科、聯詠；只作描述性開發研究，不宣稱改善。五檔各自100萬元，不是共用資金投資組合。原策略、資料庫、先前結果保留。普通K線近似零股成交，未驗證真實零股流動性。',encoding='utf8')
print('PREREGISTERED',HOME)
