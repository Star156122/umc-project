"""Execute fixed windows once; no selection/retraining on formal results."""
import json,csv,sys,copy
from pathlib import Path
from datetime import datetime
from formal_guard import H,verify,assert_period,assert_timestamps
from engine import load_inputs,simulate,TZ,ml_status
from benchmarks import buyhold
def save_csv(p,rows):
 keys=list(dict.fromkeys(k for r in rows for k in r)) or ['status']
 with p.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})
if '--execute-authorized-formal' not in sys.argv:raise SystemExit('Explicit formal command required')
verify();c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
if (H/'稽核/replay_started.json').exists():raise SystemExit('One shot already started; do not rerun formal results')
(H/'稽核/replay_started.json').write_text(json.dumps({'started':datetime.now(TZ).isoformat(),'never_reset':True}),encoding='utf8')
out=H/'結果'/datetime.now(TZ).strftime('%Y%m%dT%H%M%S');out.mkdir();data=load_inputs(c)
preds=json.loads((H/'資料/predictions.json').read_text(encoding='utf8'));lookups={name:{r['entry_date']:r for r in rows} for name,rows in preds.items()}
data['ml_by_model']=lookups
modelmap={'RF':'random_forest','XGB':'xgboost','GRU':'gru'};labels={'BH':'買進持有','TECH':'純技術','RF':'RF＋技術','XGB':'XGBoost＋技術','GRU':'GRU＋技術'}
results={};metrics=[]
for window in c['windows']:
 assert_period(window['start'],window['end'])
 for slip in c['slippages']:
  for arm in labels:
   d=dict(data);d['ml']=lookups[modelmap.get(arm,'random_forest')]
   r=buyhold(dict(c,period={'start':window['start'],'end':window['end']}),d,slip) if arm=='BH' else simulate(c,d,arm,slip,'common',(window['start'],window['end']))
   m=r['metrics'];m.update(stock='4938',stock_name='和碩',window=window['id'],data_role=window['role'],variant=arm,start=window['start'],end=window['end'])
   assert m['adds']==0 and m['holding_extensions']==0
   assert abs(m['net_pnl']-sum(t['net_pnl'] for t in r['trades'])-m['unrealized_pnl'])<.02
   for o in r['orders']:
    assert o['time'][11:16]>='09:10' and o['quantity']>0
    assert window['start']<=o['time'][:10]<=window['end']
    if arm!='BH' and o['action']=='BUY':assert o['cash_after']>=50000-.001 and o['quantity']*o['price']+o['fee']<=o['cash_before']*.4+.001
   key=f"{window['id']}_{arm}_{int(slip*10000)}";results[key]=r;metrics.append(m)
   (out/(key+'.json')).write_text(json.dumps(r,ensure_ascii=False,allow_nan=False,default=lambda x:x.item()),encoding='utf8')
   for kind in ['funnel','trades','orders','events','equity']:save_csv(out/(key+'_'+kind+'.csv'),r[kind])
   print('FORMAL_REPLAY',key,m['trade_count'],m['return'],flush=True)
save_csv(out/'metrics.csv',metrics)
formal=[];baseline=next(m for m in metrics if m['window']=='H2' and m['variant']=='TECH' and m['slippage_each_side']==.0005)
for arm in modelmap:
 m=next(m for m in metrics if m['window']=='H2' and m['variant']==arm and m['slippage_each_side']==.0005)
 verdict='INSUFFICIENT' if m['trade_count']<5 else ('PASS_FIXED_CRITERIA_ONLY' if m['return']>baseline['return'] and m['max_drawdown']<=baseline['max_drawdown'] else 'FAIL')
 formal.append({'model':arm,'verdict':verdict,'difference_vs_technical':m['return']-baseline['return'],'trade_count':m['trade_count']})
verify()
(out/'formal_verdicts.json').write_text(json.dumps(formal,ensure_ascii=False,indent=2),encoding='utf8')
bars=[[b['kbar_timestamp'],b['Open'],b['High'],b['Low'],b['Close']] for b in data['bars'] if '2025-01-01'<=b['day']<='2025-12-31'];assert_timestamps(b[0] for b in bars)
# Compact browser payload, retain full bar-level CSV separately.
for r in results.values():r['equity']=list({e['date']:e for e in r['equity']}.values())
payload={'config':c,'labels':labels,'results':results,'metrics':metrics,'verdicts':formal,'bars':bars,'data_audit':data['audit']}
(out/'data.js').write_text('window.FORMAL='+json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'),default=lambda x:x.item())+';',encoding='utf8')
(H/'結果/latest.json').write_text(json.dumps({'folder':out.name}),encoding='utf8')
print('FORMAL_COMPLETED',out,flush=True)
