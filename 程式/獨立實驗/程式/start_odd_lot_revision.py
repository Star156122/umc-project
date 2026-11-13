"""User-authorized result cleanup and prospective revision; no market replay."""
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOME=Path(__file__).resolve().parents[1]
ROOT=HOME.parent
RESULTS=(HOME/'結果').resolve()
assert RESULTS==ROOT.resolve()/'獨立實驗'/'結果'
ID='isolated-buy-2454-20261113-exp002-odd-lot'
now=datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
if (HOME/'設定/上一輪結果刪除紀錄.json').exists():
    raise SystemExit('Cleanup already recorded; do not delete subsequent experiment outputs.')

# Fixed allowlist: only the two run directories and delivery files from exp001.
names=['20261113T214134','20261113T214214','聯發科2454_獨立實驗報告與明細.zip','index.html','latest.json']
removed=[]
for name in names:
    target=(RESULTS/name).resolve()
    if target.parent!=RESULTS:
        raise RuntimeError('Refusing path outside isolated results')
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(name)

c=json.loads((HOME/'設定/experiment.json').read_text(encoding='utf-8'))
c.update(experiment_id=ID,lot_size=1,status='AWAITING_EXECUTION_DATA_CHOICE',
         execution_data_mode='UNSELECTED',prior_results_deleted_by_user=True)
c['execution_notes']=[
    'All four arms use the same legal quantity unit; allow odd-lot quantities, no increase to 1% risk budget.',
    'No ordinary-market quote may be called a verified odd-lot fill.',
    'If user selects approximation, every page must label ordinary-OHLC proxy research and unverified fills.',
    'Original strategy and original databases are not modified.',
    'Whole-boardlot and residual odd-lot orders require distinct fees and execution handling if quantity exceeds 999.',
    'Corporate actions and nonzero-position paths must be checked before interpreting returns.'
]
(HOME/'設定/experiment_odd_lot.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8')
(HOME/'設定/上一輪結果刪除紀錄.json').write_text(json.dumps({
    'time':now,'authorization':'User explicitly requested removal of prior isolated experiment results, retain modified strategy.',
    'removed':removed,'retained':'strategy implementation, settings, tests, input sources, original strategy/databases',
    'next_experiment_id':ID},ensure_ascii=False,indent=2),encoding='utf-8')

for kind in ['experiment','dataset']:
    path=ROOT/'research/registries'/f'{kind}_registry.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f); rows=list(reader); fields=reader.fieldnames
    key='experiment_id' if kind=='experiment' else 'dataset_id'
    for row in rows:
        if row[key]=='isolated-buy-2454-20261113-exp001':
            if kind=='experiment':
                row.update(status='results_deleted_by_user',decision='USER_REQUESTED_DELETE',report_file='',result_file='')
            else:
                row.update(report_location='',result_location='')
            row['notes']+='; Previous isolated result artifacts deleted by explicit user request; strategy retained; follow-up '+ID
    if not any(row[key]==ID for row in rows):
        if kind=='experiment':
            row=dict(experiment_id=ID,strategy='technical_v2b_buy_isolated',variant='SAME_FOUR_ARMS_ALLOW_ODD_LOT',
                test_start='2023-01-01',test_end='2024-12-31',dataset_role='training_development_exposed',stock_codes='2454',
                status='preregistered_data_choice_pending',decision='NOT_RUN',report_file='獨立實驗/結果/index.html',
                result_file='獨立實驗/結果',holdout_used='false',
                notes='User authorized common odd-lot quantity model; 1m capital and 1% risk unchanged; no tuning; need exact data or explicit proxy choice. Prior exp001 results deleted by user. No inference that allowing shares improves profits.')
        else:
            row=dict(dataset_id=ID,stock_code='2454',stock_name='聯發科',industry='半導體',
                test_start='2023-01-01',test_end='2024-12-31',participated_in_development='true',results_viewed='false',
                influenced_strategy='false',current_classification='training_development_exposed',clean_validation_eligible='false',
                report_location='獨立實驗/結果',result_location='獨立實驗/資料',experiment_ids=ID,
                notes='Historical odd-lot tick data absent in local DB; execution model selection pending. Original OHLC data retained read-only.')
        rows.append(row)
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)

print(json.dumps({'removed':removed,'next_experiment':ID,'status':'AWAITING_EXECUTION_DATA_CHOICE'},ensure_ascii=False))
