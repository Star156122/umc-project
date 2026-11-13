"""One-shot five-arm fixed validation replay, guarded and audited."""
import json,csv,sys,subprocess,shutil,hashlib,math
from pathlib import Path
from datetime import datetime
import pandas as pd,numpy as np
from engine import HOME as H,ROOT as R,TZ,load_inputs,simulate,ml_status,commission,round_lot,validate
from trading_system.research_guard import assert_timestamps,assert_development_period
from prepare import dump,sha
LABELS={'BH':'買進持有（80%上限）','TECH':'純技術指標','RF':'RF＋技術','XGB':'XGBoost＋技術','GRU':'GRU＋技術'}

def csvwrite(path,rows):
 keys=list(dict.fromkeys(k for r in rows for k in r)) or ['status']
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})

def register(c,status,out):
 for kind,key in [('experiment','experiment_id'),('dataset','dataset_id')]:
  p=R/f'research/registries/{kind}_registry.csv'
  with p.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
  for r in rows:
   if r[key]==c['experiment_id']:
    if kind=='experiment':r.update(status=status,decision='INSUFFICIENT_OR_EXPLORATORY_VALIDATION',report_file=str((out/'index.html').relative_to(R)),result_file=str(out.relative_to(R)))
    else:r.update(results_viewed=str(status=='completed').lower(),current_classification='validation_seen_new_stock',report_location=str((out/'index.html').relative_to(R)),result_location=str(out.relative_to(R)))
  with p.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def buyhold(c,data,slip):
 bars=[b for b in data['bars'] if c['period']['start']<=b['day']<=c['period']['end']]
 idx=next(i for i,b in enumerate(bars) if b['time'][11:16]>='09:10')
 first=bars[idx];price=first['Open']*(1+slip);cash=c['initial_capital'];budget=cash*.8
 liq=next(b['liq'] for b in data['bars'] if b['kbar_timestamp']==first['kbar_timestamp']-300)
 q=min(round_lot(budget/price,c),round_lot(liq*c['volume_to_shares']*c['volume_fraction'],c))
 while q and q*price+commission(q,price,c)>budget:q-=c['lot_size']
 if not q:raise ValueError('BH cannot buy minimum legal unit')
 fee_in=commission(q,price,c);cash-=q*price+fee_in;assert cash>=c['initial_capital']*.2-.001
 orders=[{'time':first['time'],'timestamp':first['kbar_timestamp'],'action':'BUY','quantity':q,'price':price,'reference':first['Open'],'fee':fee_in,'tax':0,'trade_id':1,'reason':'BUY_HOLD_FIRST_LEGAL_OPEN','slippage_cost':q*(price-first['Open'])}]
 curve=[];div=0;receivables=[]
 for b in bars:
  day=b['day'];ts=b['kbar_timestamp']
  for x in receivables:
   if not x['paid'] and x['payment_date']<=day:cash+=x['amount'];x['paid']=True
  action=data['dividends'].get(day)
  if action and ts>=first['kbar_timestamp'] and not any(x['ex_date']==day for x in receivables):
   amount=q*action['cash_per_share'];div+=amount;receivables.append({'ex_date':day,'payment_date':action['payment_date'],'amount':amount,'paid':False})
  holding=ts>=first['kbar_timestamp'];assets=cash+(q*b['Close'] if holding else q*price+fee_in)+sum(x['amount'] for x in receivables if not x['paid'])
  curve.append({'timestamp':ts,'date':day,'equity':assets,'cash':cash if holding else c['initial_capital'],'exposure':q*b['Close']/assets if holding else 0})
 last=bars[-1];exitp=last['Close']*(1-slip);fee_out=commission(q,exitp,c);tax=q*exitp*c['normal_sell_tax']
 cash+=q*exitp-fee_out-tax;receivable=sum(x['amount'] for x in receivables if not x['paid']);final=cash+receivable
 curve[-1].update(equity=final,cash=cash,exposure=0)
 when=datetime.fromtimestamp(last['kbar_timestamp']+300,TZ).isoformat()
 orders.append({'time':when,'timestamp':last['kbar_timestamp']+300,'action':'SELL','quantity':q,'price':exitp,'reference':last['Close'],'fee':fee_out,'tax':tax,'trade_id':1,'reason':'BUY_HOLD_END_CLOSE','slippage_cost':q*(last['Close']-exitp)})
 net=final-c['initial_capital'];gross=q*(exitp-price)+div
 trade={'trade_id':1,'entry_time':first['time'],'exit_time':when,'entry_day':first['day'],'exit_day':last['day'],'entry_price':price,'exit_price':exitp,'quantity':q,'net_pnl':net,'gross_pnl':gross,'holding_bars':len(bars)-idx,'adds':0,'exit_reason':'BUY_HOLD_END_CLOSE','holding_extensions':0,'cash_dividends':div}
 eq=pd.DataFrame(curve);daily=eq.groupby('date').equity.last();returns=daily.pct_change().fillna(0)
 m={'variant':'BH','scope':'full','slippage_each_side':slip,'initial_capital':c['initial_capital'],'final_equity':final,'return':net/c['initial_capital'],'net_pnl':net,'closed_trade_net_pnl':net,'unrealized_pnl':0,'gross_pnl':gross,'trade_count':1,'win_rate':float(net>0),'profit_factor':None,'max_drawdown':float((1-eq['equity']/eq['equity'].cummax().clip(lower=c['initial_capital'])).max()),'sharpe':float(returns.mean()/returns.std()*np.sqrt(252)) if returns.std()>0 else None,'average_win':net if net>0 else None,'average_loss':net if net<0 else None,'fees':fee_in+fee_out,'tax':tax,'slippage_cost':sum(o['slippage_cost'] for o in orders),'avg_exposure':float(eq['exposure'].mean()),'max_exposure':float(eq['exposure'].max()),'buys':1,'adds':0,'holding_extensions':0,'holding_extension_checks':0,'receivables_at_end':receivable,'cash_dividends':div}
 assert abs(net-(gross-fee_in-fee_out-tax))<.01
 return {'metrics':m,'orders':orders,'trades':[trade],'equity':curve,'events':[],'funnel':[],'holding_events':[],'ml_counts':{},'blockers':{},'open_position':None}

def main():
 c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'));validate(c)
 out=H/'結果'/datetime.now(TZ).strftime('%Y%m%dT%H%M%S');out.mkdir()
 originals=[R/'data/market_data.sqlite3',R/'data/technical_v1_mltech_clean.sqlite3',R/'trading_system/backtest.py',H.parent/'資金配置40_保留20/程式/engine.py']
 before={str(p.relative_to(R)):sha(p) for p in originals}
 dump(out/'freeze.json',{'created_before_replay':datetime.now(TZ).isoformat(),'config':c,'authorization':'User 去做: ASUS2357 five arms, 2025H1,250k each; prior agreed no-risk cash40 reserve20 and extension.','original_hashes':before,'program_hashes':{p.name:sha(p) for p in (H/'程式').glob('*.py')},'model_hashes':{str(p.relative_to(H)):sha(p) for p in (H/'模型').rglob('*') if p.is_file()},'data_hashes':{p.name:sha(p) for p in (H/'資料').iterdir() if p.is_file()},'locked_data_used':False})
 register(c,'running',out)
 try:
  t=subprocess.run([sys.executable,str(H/'測試/test_validation.py')],capture_output=True,text=True);(out/'test_results.txt').write_text(t.stdout+t.stderr,encoding='utf8')
  if t.returncode:raise RuntimeError('Validation correctness tests failed')
  data=load_inputs(c);dump(out/'data_audit.json',data['audit'])
  predictions=json.loads((H/'資料/predictions.json').read_text(encoding='utf8'));mapping={'RF':'random_forest','XGB':'xgboost','GRU':'gru'}
  common=set.intersection(*[{r['entry_date'] for r in predictions[n]} for n in mapping.values()])
  results={};metrics=[]
  for slip in c['slippages']:
   bh=buyhold(c,data,slip)
   for scope in ['full','common']:
    for arm in ['BH','TECH','RF','XGB','GRU']:
     d=dict(data);d['ml']={r['entry_date']:r for r in predictions[mapping.get(arm,'random_forest')] if r['entry_date'] in common}
     r=__import__('copy').deepcopy(bh) if arm=='BH' else simulate(c,d,arm,slip,scope)
     r['metrics'].update(stock='2357',stock_name=LABELS[arm],scope=scope)
     m=r['metrics'];assert abs(m['net_pnl']-sum(t['net_pnl'] for t in r['trades'])-m['unrealized_pnl'])<.02
     assert all(o['time'][11:16]>='09:10' and o['quantity']==int(o['quantity']) and o['quantity']>0 for o in r['orders'])
     assert all(t['entry_day']<t['exit_day'] for t in r['trades'])
     for o in r['orders']:
      if arm!='BH' and o['action'] in ['BUY','ADD']:
       assert o['quantity']*o['price']+o['fee']<=o['cash_before']*.4+.001 and o['cash_after']>=50000-.001
     key=f'{arm}_{int(slip*10000)}_{scope}';dump(out/(key+'.json'),r)
     for kind in ['funnel','orders','trades','events','equity','holding_events']:csvwrite(out/(key+'_'+kind+'.csv'),r[kind])
     metrics.append(m);daily={e['date']:e for e in r['equity']};r['equity']=list(daily.values());results[key]=r
     print('REPLAY',key,'trades',m['trade_count'],'adds',m['adds'],'extensions',m['holding_extensions'],'return',m['return'],flush=True)
  after={str(p.relative_to(R)):sha(p) for p in originals};assert before==after
  audit={'original_files_unchanged':True,'locked_data_used':False,'model_rebuilt_on_training_only':True,'asus_in_training':False,'validation_used_for_tuning':False,'ordinary_ohlc_odd_quantity_proxy':True,'common_legal_prediction_days':len(common),'original_hashes_before':before,'original_hashes_after':after}
  csvwrite(out/'metrics.csv',metrics);dump(out/'audit.json',audit)
  bars=[b for b in data['bars'] if '2025-01-01'<=b['day']<='2025-06-30'];assert_timestamps(b['kbar_timestamp'] for b in bars)
  for arm in LABELS:results[arm+'_bars']=[[b['kbar_timestamp'],b['Open'],b['High'],b['Low'],b['Close']] for b in bars]
  coverage={arm:{'q4_prediction_status':dict(__import__('collections').Counter(ml_status(day,datetime.fromisoformat(day+'T09:00:00+08:00').timestamp(),{r['entry_date']:r for r in predictions[mapping.get(arm,'random_forest')]} ,data['calendar']) for day in sorted({b['day'] for b in bars}))) } for arm in LABELS}
  payload={'config':c,'labels':LABELS,'results':results,'audit':audit,'data_audits':coverage,'generated_at':datetime.now(TZ).isoformat()}
  (out/'data.js').write_text('window.RESEARCH='+json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'),default=lambda v:v.item())+';',encoding='utf8')
  for name in ['report.js','report.css']:shutil.copy2(H/'程式'/name,out/name)
  for arm,kind,file in [('', 'summary','index')]+[(a,k,a+'_'+k) for a in LABELS for k in ['analysis','funnel']]:
   (out/(file+'.html')).write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>華碩2357｜2025上半年五組回測</title><link rel="stylesheet" href="report.css"><body data-stock="'+arm+'" data-kind="'+kind+'"><main>載入中</main><script src="data.js"></script><script src="report.js"></script></body></html>',encoding='utf8')
  dump(H/'結果/latest.json',{'folder':out.name});register(c,'completed',out)
  (H/'結果/index.html').write_text('<meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+out.name+'/index.html">',encoding='utf8');print('REPORT',out,flush=True)
 except Exception as e:
  dump(out/'failure.json',{'error':str(e),'results_preserved':True});register(c,'failed',out);raise
if __name__=='__main__':
 if '--execute' not in sys.argv:raise SystemExit('Explicit --execute required')
 main()
