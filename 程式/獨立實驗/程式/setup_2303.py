"""Create preregistered two-arm UMC replication, preserving the MediaTek experiment."""
import csv
import hashlib
import json
import shutil
from pathlib import Path

PARENT=Path(__file__).resolve().parents[1]
ROOT=PARENT.parent
HOME=PARENT/'聯電2303'
ID='isolated-buy-2303-20261113-fixed-replication-exp001'
for folder in ['程式','設定','測試','資料','結果']:
    (HOME/folder).mkdir(parents=True,exist_ok=True)

def save(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

base=json.loads((PARENT/'設定/experiment_odd_lot.json').read_text(encoding='utf-8'))
config=dict(base)
config.update(experiment_id=ID,stock='2303',variants=['C','D'],stock_name='聯電',
              status='PREREGISTERED_FIXED_REPLICATION',prior_results_deleted_by_user=False,
              replication_parent='isolated-buy-2454-20261113-exp002-odd-lot')
for key in ['superseded_runs','supersedes_timing_review_run']:
    config.pop(key,None)
assert all(config[k]==base[k] for k in base if k not in ['experiment_id','stock','variants','status',
           'prior_results_deleted_by_user','superseded_runs','supersedes_timing_review_run'])
save(HOME/'設定/experiment.json',config)

engine=(PARENT/'程式/engine.py').read_text(encoding='utf-8')
engine=engine.replace('ROOT = HOME.parent','ROOT = HOME.parents[1]')
engine=engine.replace("c['stock'] != '2454'","c['stock'] != '2303'")
engine=engine.replace('fixed to 2454','fixed to 2303').replace('2454_cash_dividends.json','2303_cash_dividends.json')
(HOME/'程式/engine.py').write_text(engine,encoding='utf-8')
prepare=(PARENT/'程式/prepare.py').read_text(encoding='utf-8')
prepare=prepare.replace('ROOT = HOME.parent','ROOT = HOME.parents[1]')
# Only shared serialization/hash helpers will be imported. Registration/download have a dedicated entry point.
(HOME/'程式/prepare.py').write_text(prepare,encoding='utf-8')
shutil.copy2(PARENT/'測試/test_experiment.py',HOME/'測試/test_experiment.py')
shutil.copy2(PARENT/'資料/index_daily.json',HOME/'資料/index_daily.json')
shutil.copy2(PARENT/'程式/report.css',HOME/'程式/report.css')

js=(PARENT/'程式/report.js').read_text(encoding='utf-8')
js=js.replace("const labels = {A:'原策略（不加 ML）',B:'修改策略（不加 ML）',C:'原策略＋ML 篩選',D:'修改策略＋ML 篩選'};",
              "const labels = {C:'原策略＋ML 篩選',D:'修改策略＋ML 篩選'};")
old="[['不加 ML：原策略 → 修改策略','A','B'],['有 ML：原策略 → 修改策略','C','D'],['原策略：不加 ML → 加入 ML','A','C'],['修改策略：不加 ML → 加入 ML','B','D']]"
assert old in js
js=js.replace(old,"[['原策略＋ML → 修改策略＋ML','C','D']]")
js=js.replace('A／B 不使用 ML，C／D 使用相同 RF OOF、BUY-only。','本次僅比較原策略＋ML與修改策略＋ML，兩者使用相同 RF OOF、BUY-only。')
js=js.replace('A／B 不使用 ML；C／D 的','兩個策略的')
js=js.replace('A 青綠、B 藍、C 黃、D 紫。','原策略＋ML為青綠色，修改策略＋ML為藍色。')
js=js.replace('四種','兩種').replace('四組','兩組').replace('四張','兩張').replace('聯發科','聯電').replace('2454','2303').replace('三次現金股利','兩次現金股利')
(HOME/'程式/report.js').write_text(js,encoding='utf-8')

save(HOME/'資料/2303_cash_dividends.json',{
    'stock':'2303','source':'https://www.umc.com/en/IR_Stock/dividend_information',
    'payment_sources':['https://toalpha.tw/stock/2303/filings','https://www.cna.com.tw/news/afe/202407020052.aspx'],
    'note':'Issuer table is NT$ per 1000 shares; converted to per share. Payment dates cross-checked against announcement/news; not ADS dividends.',
    'events':[{'ex_date':'2023-06-27','cash_per_share':3.60046348,'payment_date':'2023-07-19'},
              {'ex_date':'2024-07-02','cash_per_share':3.00011747,'payment_date':'2024-07-24'}]})

for kind,key,row in [
 ('experiment','experiment_id',dict(experiment_id=ID,strategy='technical_v2b_buy_isolated',variant='FIXED_C_D_STOCK_REPLICATION',
     test_start='2023-01-01',test_end='2024-12-31',dataset_role='training_development_exposed',stock_codes='2303',
     status='preregistered',decision='NOT_RUN',report_file='獨立實驗/聯電2303/結果/index.html',result_file='獨立實驗/聯電2303/結果',holdout_used='false',
     notes='User requested fixed replication on UMC, C/D only. Same 1m,1% risk,2.5% stop,RF OOF,proxy and costs as2454. Hypothesis: assess whether observed modification effects differ across stocks. Single change=target stock; no tuning; old1000-share cap retained only C; no cross-stock performance pooling.')),
 ('dataset','dataset_id',dict(dataset_id=ID,stock_code='2303',stock_name='聯電',industry='半導體',
     test_start='2023-01-01',test_end='2024-12-31',participated_in_development='true',results_viewed='false',influenced_strategy='false',
     current_classification='training_development_exposed',clean_validation_eligible='false',report_location='獨立實驗/聯電2303/結果',
     result_location='獨立實驗/聯電2303/資料',experiment_ids=ID,notes='Existing bounded SQLite plus official index, UMC daily volume unit check and issuer dividends; no locked dates; not Clean Validation.'))]:
    path=ROOT/'research/registries'/f'{kind}_registry.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f); rows=list(reader); fields=reader.fieldnames
    if not any(r[key]==ID for r in rows):
        with path.open('a',encoding='utf-8',newline='') as f:
            csv.DictWriter(f,fieldnames=fields).writerow(row)
    save(HOME/'設定'/f'{kind}_registration.json',row)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
save(HOME/'設定/replication_changes.json',{'base_engine_sha256':digest(PARENT/'程式/engine.py'),
    'engine_copy_sha256':digest(HOME/'程式/engine.py'),
    'allowed_engine_changes':['root path for nested isolated folder','stock guard 2454 -> 2303','stock cash dividend input filename'],
    'other_strategy_or_risk_changes':False,'parent_metrics_sha256':digest(PARENT/'結果/20261113T224843/metrics.csv')})
print(str(HOME))
