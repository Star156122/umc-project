"""Snapshot the user-selected main strategy, preserving all research versions."""
import csv,json
from pathlib import Path
R=Path(__file__).resolve().parents[1];B=R/'獨立實驗';ID='main-buy-noadd-noextension-common-v1-20261114'
if (R/'hybrid/config/web_main_strategy_v1.json').exists():raise RuntimeError('One-time promotion already completed; never overwrite the published profile')
source=B/'無加碼無延期_共同完整日/程式/engine_3231.py'
s=source.read_text(encoding='utf8')
s=s.replace("HOME = Path(__file__).resolve().parents[1]\nSOURCE_HOME = HOME.parent / '緯創3231_2025上半年'\nROOT = HOME.parents[1]", "ROOT = Path(__file__).resolve().parents[2]\nHOME = ROOT")
s=s.replace('from prepare import dump, sha','')
a=s.index('def validate(');b=s.index('def holding_review(',a)
s=s[:a]+'''def validate(c):
    assert_development_period(c['period']['start'],c['period']['end'])
    if c.get('profile_id')!='main-buy-noadd-noextension-common-v1-20261114':raise ValueError('Wrong main strategy profile')
    spec=c['datasets'].get(c['stock'])
    if not spec or not spec['start']<=c['period']['start']<=c['period']['end']<=spec['end']:raise ValueError('Stock period outside prepared model/data coverage')
    if c['revised_max_adds']!=0 or c['holding_extension']['enabled'] or c['allowed_scopes']!=['common']:raise ValueError('Main strategy forbids adds, extension and non-common scope')
    if c['risk_budget_enabled'] or c['cash_allocation_fraction']!=.4 or c['initial_cash_reserve_fraction']!=.2:raise ValueError('Main cash allocation changed')
    if c['initial_capital']!=250000:raise ValueError('Current main profile uses250000 initial capital')


'''+s[b:]
a=s.index('def load_bars(');b=s.index('def indicators(',a)
s=s[:a]+'''def load_bars(path,c):
    validate(c)
    lo=int(datetime.fromisoformat(c['warmup_start']).replace(tzinfo=TZ).timestamp())
    hi=int((datetime.fromisoformat(c['period']['end'])+timedelta(days=1)).replace(tzinfo=TZ).timestamp())
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=ON')
        rows=[dict(r) for r in con.execute('SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp',(c['stock'],lo,hi))]
    assert_timestamps(r['kbar_timestamp'] for r in rows)
    by={}
    for r in rows:
        d=datetime.fromtimestamp(r['kbar_timestamp'],TZ)
        if '09:00'<=d.strftime('%H:%M')<='13:25':by.setdefault((c['stock'],d.date().isoformat()),[]).append(r)
    return by


'''+s[b:]
s=s.replace("backtest_start='2025-01-01',backtest_end='2025-06-30',\n                     backfill_start='2024-11-01',backfill_end='2025-06-30'", "backtest_start=calendar[0],backtest_end=calendar[-1],\n                     backfill_start=calendar[0],backfill_end=calendar[-1]")
a=s.index('def load_inputs(');b=s.index('def ml_status(',a)
s=s[:a]+'''def load_inputs(c):
    validate(c);spec=c['datasets'][c['stock']]
    index=json.loads((ROOT/spec['index']).read_text(encoding='utf8'))
    index=[r for r in index if c['warmup_start']<=r['date']<=c['period']['end']]
    calendar=[r['date'] for r in index]
    if not calendar or calendar!=sorted(set(calendar)):raise ValueError('Invalid official calendar')
    for day in calendar:assert_development_period(day,day)
    raw=load_bars(ROOT/spec['database'],c)
    params=json.loads((ROOT/'configs/technical_v2_candidate.json').read_text(encoding='utf8'))['candidate_parameters']
    dividends=spec.get('events',[])
    if spec.get('dividends'):dividends=json.loads((ROOT/spec['dividends']).read_text(encoding='utf8'))['events']
    dividends=[e for e in dividends if c['warmup_start']<=e['ex_date']<=c['period']['end']]
    for e in dividends:assert_development_period(e['ex_date'],e['ex_date'])
    bars,complete=indicators(raw,c['stock'],calendar,params,True,dividends)
    if not bars:raise ValueError('No prepared ordinary5m data; no proxy substitution')
    contexts={}
    for i,day in enumerate(calendar):contexts[day]={'new':i>=20 and index[i-1]['close']>np.mean([r['close'] for r in index[i-20:i]]),'old':False,'index_reference':calendar[i-1] if i else None}
    if spec['prediction_kind']=='fixed_json':
        p=json.loads((ROOT/spec['predictions']).read_text(encoding='utf8'))
    else:
        with (ROOT/spec['predictions']).open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['stock_code']==c['stock']]
        p={m:[r for r in rows if r['model']==m] for m in ['random_forest','xgboost','gru']}
    lookups={}
    for model in ['random_forest','xgboost','gru']:
        rows=p.get(model)
        if not isinstance(rows,list):raise ValueError('Missing required model coverage: '+model)
        lookup={}
        for row in rows:
            for k in ['signal_date','entry_date']:assert_development_period(row[k],row[k])
            if row['stock_code']!=c['stock'] or row['model']!=model:raise ValueError('Prediction stock/model mismatch')
            if row['entry_date'] in lookup:raise ValueError('Duplicate prediction effective date')
            lookup[row['entry_date']]=row
        lookups[model]=lookup
    return {'bars':bars,'old_map':{},'old_prev':{},'common':complete,'calendar':calendar,'contexts':contexts,'ml':lookups['random_forest'],'ml_by_model':lookups,'dividends':{e['ex_date']:e for e in dividends},'audit':{'profile_id':c['profile_id'],'database':spec['database'],'official_index':spec['index'],'predictions':spec['predictions'],'complete_sessions':len(complete),'partial_sessions':sorted({b['day'] for b in bars}-complete),'locked_data_used':False}}


'''+s[b:]
a=s.index('def ml_status(');b=s.index('def fee(',a)
s=s[:a]+'''def ml_status(day,timestamp,lookup,calendar):
    r=lookup.get(day)
    if r is None:return 'MISSING'
    i=calendar.index(day);probs=[float(r[k]) for k in ['prob_sell','prob_hold','prob_buy']]
    valid=(i>0 and r['signal_date']==calendar[i-1] and r['entry_date']==day
      and datetime.fromisoformat(r['decision_time']).timestamp()<timestamp
      and r['model'] in ['random_forest','xgboost','gru']
      and all(math.isfinite(p) and 0<=p<=1 for p in probs) and abs(sum(probs)-1)<1e-5
      and r['prediction']==('SELL','HOLD','BUY')[int(np.argmax(probs))])
    if str(r.get('is_oof','')).lower()=='true':
        valid=valid and r['data_role']=='training' and r['label_version']=='ml_v1_fixed_1pct' and r['fold_train_end']<r['signal_date'] and r['fold_validation_start']<=r['signal_date']<=r['fold_validation_end']
    else:
        valid=valid and r.get('prediction_source')=='fixed_2023_2024_new_stock_inference' and r['data_role']=='validation' and r['training_target_max']<=r['signal_date'] and r['training_target_max']<='2024-12-31'
    return r['prediction'] if valid else 'INVALID_OR_LATE'


'''+s[b:]
(R/'hybrid/core/main_buy_strategy_v1.py').write_text(s,encoding='utf8')
base=json.loads((B/'無加碼無延期_共同完整日/設定/2357.json').read_text(encoding='utf8'))
base.update(profile_id=ID,data_role='historical_development',stock_universe=['2303','2330','2379','2454','3034','2357','3231','2892'],periods={'backtest':{'start':'2025-01-01','end':'2025-06-30'}},data_protection={'final_oos_used':False},datasets={})
for code in ['2303','2330','2379','2454','3034']:
 spec={'start':'2024-07-01','end':'2024-12-30','database':'data/market_data.sqlite3','index':'獨立實驗/資料/index_daily.json','prediction_kind':'oof_csv','predictions':base['oof_path']}
 if code=='2303':spec['dividends']='獨立實驗/聯電2303/資料/2303_cash_dividends.json'
 elif code=='2454':spec['dividends']='獨立實驗/資料/2454_cash_dividends.json'
 else:spec['events']={'2330':[{'ex_date':'2024-06-13','payment_date':'2024-07-11','cash_per_share':3.49978969},{'ex_date':'2024-09-12','payment_date':'2024-10-09','cash_per_share':4.00013820},{'ex_date':'2024-12-12','payment_date':'2025-01-09','cash_per_share':3.99963706}],'2379':[{'ex_date':'2024-09-05','payment_date':'2024-10-08','cash_per_share':15.5}],'3034':[{'ex_date':'2024-07-10','payment_date':'2024-08-09','cash_per_share':32}]}[code]
 base['datasets'][code]=spec
for code,folder in [('2357','華碩2357_2025上半年'),('3231','緯創3231_2025上半年'),('2892','第一金2892_2025上半年')]:
 base['datasets'][code]={'start':'2025-01-01','end':'2025-06-30','database':f'獨立實驗/{folder}/資料/{code}_history.sqlite3','index':f'獨立實驗/{folder}/資料/index_daily.json','dividends':f'獨立實驗/{folder}/資料/{code}_cash_dividends.json','prediction_kind':'fixed_json','predictions':f'獨立實驗/{folder}/資料/predictions.json'}
(R/'hybrid/config/web_main_strategy_v1.json').write_text(json.dumps(base,ensure_ascii=False,indent=2),encoding='utf8')
print('MAIN_PROFILE_SNAPSHOT_CREATED')
