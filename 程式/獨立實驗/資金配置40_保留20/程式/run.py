"""Frozen Q4 five-stock replication. Explicit --execute required."""
import csv,json,sys,shutil,subprocess,statistics,zipfile,urllib.request
from datetime import datetime
from pathlib import Path
from engine import HOME,ROOT,TZ,validate,load_inputs,simulate,ml_status,approved_quantity,commission
from prepare import dump,sha
from trading_system.research_guard import assert_development_period,assert_timestamps

def write_csv(path,rows):
 fields=list(dict.fromkeys(k for r in rows for k in r)) or ['status']
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def registry(c,status,out):
 for kind,key in [('experiment','experiment_id'),('dataset','dataset_id')]:
  p=ROOT/f'research/registries/{kind}_registry.csv'
  with p.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
  for r in rows:
   if r[key]==c['experiment_id']:
    if kind=='experiment':r.update(status=status,decision='DESCRIPTIVE_PROXY_RESEARCH',report_file=str((out/'index.html').relative_to(ROOT)),result_file=str(out.relative_to(ROOT)))
    else:r.update(results_viewed=str(status=='completed').lower(),report_location=str((out/'index.html').relative_to(ROOT)))
  with p.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def preflight(c):
 """Check ordinary-volume units against official bounded Q4 daily shares."""
 import sqlite3
 lo=int(datetime(2024,10,1,tzinfo=TZ).timestamp());hi=int(datetime(2025,1,1,tzinfo=TZ).timestamp())
 assert_development_period('2024-09-16','2024-12-31')
 audits={}
 for s in c['stocks']:
  days={};sources=[]
  for month in [10,11,12]:
   stamp=f'2024{month:02d}01';p=HOME/f'資料/{s}_daily_{stamp}.json'
   url=f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={stamp}&stockNo={s}'
   cached=HOME.parent/f'資料/{s}_daily_{stamp}.json'
   if s=='2303':cached=HOME.parent/f'聯電2303/資料/{s}_daily_{stamp}.json'
   if not p.exists():
    if cached.exists():shutil.copy2(cached,p)
    else:
     req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
     with urllib.request.urlopen(req,timeout=45) as response:raw=response.read()
     obj=json.loads(raw);assert obj.get('stat')=='OK';dump(p,obj)
   obj=json.loads(p.read_text(encoding='utf8'))
   if obj.get('stat')!='OK':raise ValueError('Official daily response unavailable')
   for r in obj['data']:
    y,m,d=map(int,r[0].split('/'));day=f'{y+1911:04d}-{m:02d}-{d:02d}'
    assert_development_period(day,day)
    if not c['period']['start']<=day<=c['period']['end']:raise ValueError('Response escaped Q4')
    days[day]=int(r[1].replace(',',''))
   sources.append({'url':url,'sha256':sha(p)})
  with sqlite3.connect((ROOT/c['revised_db']).resolve().as_uri()+'?mode=ro',uri=True) as con:
   con.execute('PRAGMA query_only=ON')
   rows=con.execute('SELECT kbar_timestamp,volume FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp',(s,lo,hi)).fetchall()
  totals={}
  for ts,v in rows:
   t=datetime.fromtimestamp(ts,TZ)
   if '09:00'<=t.strftime('%H:%M')<='13:25':totals[t.date().isoformat()]=totals.get(t.date().isoformat(),0)+v
  ratios=[days[d]/v for d,v in totals.items() if d in days and v>0]
  ratio=statistics.median(ratios) if ratios else 0
  if not 500<ratio<1500:raise ValueError('Volume unit unverified: '+s)
  audits[s]={'official_sessions':len(days),'observed_sessions':len(totals),'missing_sessions':sorted(set(days)-set(totals)),
             'ratio_median':ratio,'volume_to_shares':1000,'sources':sources,
             'note':'Magnitude check only; official total-day and intraday scope differ, not exact volume parity.'}
  print('PREFLIGHT',s,len(days),len(totals),ratio,flush=True)
 dump(HOME/'資料/preflight.json',audits)
 return audits

def checks(c):
 assert any(f['start']<=c['period']['start'] and f['end']>=c['period']['end'] for f in c['folds']), 'ML eligibility windows must cover Q4; empty folds suppress all entries'
 # Retest unchanged parent execution logic before any new replay.
 parent=HOME.parent/'聯電2303'
 t=subprocess.run([sys.executable,str(parent/'測試/test_experiment.py')],capture_output=True,text=True)
 if t.returncode:raise RuntimeError(t.stdout+t.stderr)
 source=(parent/'程式/engine.py').read_text(encoding='utf8');local=(HOME/'程式/engine.py').read_text(encoding='utf8')
 extension_tests=subprocess.run([sys.executable,str(HOME/'測試/test_cash_allocation.py')],capture_output=True,text=True)
 if extension_tests.returncode:raise RuntimeError(extension_tests.stdout+extension_tests.stderr)
 holding_tests=subprocess.run([sys.executable,str(HOME.parent/'持有延期_2024Q4/測試/test_holding_extension.py')],capture_output=True,text=True)
 if holding_tests.returncode:raise RuntimeError(holding_tests.stdout+holding_tests.stderr)
 for s in c['stocks']:validate(dict(c,stock=s))
 for start,end in [('2025-07-01','2025-12-31'),('2026-07-01','2026-12-31'),('2024-01-01','2024-12-31')]:
  try:validate(dict(c,period={'start':start,'end':end}))
  except ValueError:pass
  else:raise AssertionError('Scope escaped')
 return t.stdout+t.stderr+extension_tests.stdout+extension_tests.stderr+holding_tests.stdout+holding_tests.stderr+'\nQ4 scope, cash allocation/reserve and inherited holding review: PASS\n'

def verify(r,c):
 assert all(c['period']['start']<=x['date']<=c['period']['end'] for x in r['equity'])
 assert abs(r['metrics']['net_pnl']-sum(t['net_pnl'] for t in r['trades'])-r['metrics']['unrealized_pnl'])<.02
 assert all(o['quantity']>0 and o['quantity']==int(o['quantity']) and o['price']>0 for o in r['orders'])
 assert all(o['time'][11:16]>='09:10' for o in r['orders'])
 assert all(t['entry_day']<t['exit_day'] for t in r['trades'])
 assert all(x['cash']>=-.001 for x in r['equity'])
 assert r['metrics']['adds']<=r['metrics']['buys']
 for o in r['orders']:
  if o['action'] in ['BUY','ADD']:
   spend=o['quantity']*o['price']+o['fee']
   assert spend<=o['cash_before']*.4+.001
   assert o['cash_after']>=c['initial_capital']*.2-.001
   assert spend<=o['allocation_spending_cap']+.001

def main():
 c=json.loads((HOME/'設定/experiment.json').read_text(encoding='utf8'));validate(c)
 stamp=datetime.now(TZ).strftime('%Y%m%dT%H%M%S');out=HOME/'結果'/stamp;out.mkdir()
 baseline=HOME.parent/'科技五股_2024Q4/結果/20261114T003636'
 originals=[ROOT/c[k] for k in ['old_db','revised_db','oof_path']]+[ROOT/'trading_system/backtest.py',HOME.parent/'聯電2303/程式/engine.py',HOME.parent/'聯電2303/結果/20261113T234049/metrics.csv',HOME.parent/'結果/20261113T224843/metrics.csv',baseline/'metrics.csv',HOME.parent/'科技五股_2024Q4/程式/engine.py',HOME.parent/'持有延期_2024Q4/程式/engine.py']
 before={str(p.relative_to(ROOT)):sha(p) for p in originals}
 dump(out/'freeze.json',{'created_before_replay':datetime.now(TZ).isoformat(),'config':c,'original_hashes':before,'program_hashes':{p.name:sha(p) for p in (HOME/'程式').iterdir() if p.is_file()},'data_hashes':{p.name:sha(p) for p in (HOME/'資料').glob('*.json')},'locked_data_used':False,'authorization':'2026-11-14 user: 再回測五股三個月，依照這次規則；取消1%風險，現金40%保留初始20%，條件延期保留。'})
 registry(c,'running',out)
 try:
  (out/'test_results.txt').write_text(checks(c),encoding='utf8')
  pre=preflight(c);dump(out/'preflight.json',pre)
  results={};metrics=[];data_audits={}
  for s,name in c['stocks'].items():
   cc=dict(c,stock=s,stock_name=name);data=load_inputs(cc)
   data_audits[s]=data['audit']
   q4=[b for b in data['bars'] if c['period']['start']<=b['day']<=c['period']['end']]
   if not q4:raise ValueError('No Q4 bars for '+s)
   coverage={d:ml_status(d,datetime.fromisoformat(d+'T09:00:00+08:00').timestamp(),data['ml'],data['calendar']) for d in sorted({b['day'] for b in q4})}
   data_audits[s]['q4_prediction_status']=dict(__import__('collections').Counter(coverage.values()))
   for slip in c['slippages']:
    r=simulate(cc,data,'D',slip,'full',('2024-10-01','2024-12-31'));verify(r,cc)
    r['metrics'].update(stock=s,stock_name=name);metrics.append(r['metrics'])
    key=s+'_'+str(int(slip*10000));dump(out/(key+'.json'),r)
    for kind in ['funnel','trades','orders','events','equity','holding_events']:write_csv(out/(key+'_'+kind+'.csv'),r[kind])
    # Full bar-level curves retained in CSV, compact daily curve for browser.
    daily={x['date']:x for x in r['equity']};r['equity']=list(daily.values());results[key]=r
    print('COMPLETED',s,slip,'trades',r['metrics']['trade_count'],'return',r['metrics']['return'],flush=True)
   results[s+'_bars']=[[b['kbar_timestamp'],b['Open'],b['High'],b['Low'],b['Close']] for b in q4]
   assert_timestamps(b[0] for b in results[s+'_bars'])
  after={str(p.relative_to(ROOT)):sha(p) for p in originals};assert before==after
  audit={'original_files_unchanged':True,'original_hashes_before':before,'original_hashes_after':after,'locked_data_used':False,'model_retrained':False,'parameters_changed':True,'only_strategy_change':'relative to holding-extension parent: cash40 fixed initial reserve20, no 1% loss sizing; stop/ML/extension unchanged','independent_accounts':True,'execution':'ORDINARY_OHLC_ODD_QUANTITY_PROXY','correctness_checks_passed':True}
  write_csv(out/'metrics.csv',metrics);dump(out/'audit.json',audit);dump(out/'data_audit.json',data_audits)
  with (baseline/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:previous=list(csv.DictReader(f))
  comparison=[]
  for m in metrics:
   old=next(x for x in previous if x['stock']==m['stock'] and float(x['slippage_each_side'])==m['slippage_each_side'])
   row={'stock':m['stock'],'stock_name':m['stock_name'],'slippage_each_side':m['slippage_each_side']}
   for k in ['return','net_pnl','trade_count','win_rate','max_drawdown','avg_exposure','max_exposure','gross_pnl','fees','tax','sharpe','profit_factor']:
    row['previous_'+k]=float(old[k]) if old[k] not in ['', 'None'] else None
    row['current_'+k]=m[k]
   row['return_difference']=m['return']-float(old['return'])
   comparison.append(row)
  write_csv(out/'previous_comparison.csv',comparison)
  dump(out/'previous_comparison.json',comparison)
  payload={'config':c,'results':results,'audit':audit,'data_audits':data_audits,'previous_comparison':comparison,'generated_at':datetime.now(TZ).isoformat()}
  (out/'data.js').write_text('window.RESEARCH='+json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'),default=lambda v:v.item())+';',encoding='utf8')
  for file in ['report.css','report.js']:shutil.copy2(HOME/'程式'/file,out/file)
  for s,kind,file in [('', 'summary','index')]+[(s,k,s+'_'+k) for s in c['stocks'] for k in ['funnel','analysis']]:
   (out/(file+'.html')).write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>科技五股 2024Q4｜修改策略＋ML</title><link rel="stylesheet" href="report.css"><body data-stock="'+s+'" data-kind="'+kind+'"><main>載入報告中…</main><script src="data.js"></script><script src="report.js"></script></body></html>',encoding='utf8')
  registry(c,'completed',out);dump(HOME/'結果/latest.json',{'folder':stamp})
  (HOME/'結果/index.html').write_text('<meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+stamp+'/index.html"><a href="'+stamp+'/index.html">開啟五股報告</a>',encoding='utf8')
  print('REPORT',out,flush=True)
 except Exception as e:
  dump(out/'failure.json',{'error':str(e),'preserved':True});registry(c,'failed',out);raise

if __name__=='__main__':
 if '--execute' not in sys.argv:raise SystemExit('Explicit --execute required')
 main()
