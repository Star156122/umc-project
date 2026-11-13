"""Only formal4938 bounded requests. Shared project locks are unchanged."""
import contextlib,io,json,os,sqlite3,sys,urllib.request
from datetime import date,timedelta
from pathlib import Path
from formal_guard import H,R,consume,verify,assert_period,assert_timestamps
sys.path.insert(0,str(R))
if '--execute-authorized-formal' not in sys.argv:raise SystemExit('Explicit formal command required')
if (H/'稽核/consumed.json').exists():
 if '--resume-same-frozen-run' not in sys.argv:raise SystemExit('Already consumed. Never reset; resume requires exact frozen run.')
 verify()
else:consume()
from dotenv import load_dotenv
from trading_system.backtest import sino_ts_to_local_timestamp
import pandas as pd,shioaji as sj
load_dotenv(R/'.env');api=sj.Shioaji(simulation=True)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):api.login(api_key=os.environ['API_KEY'],secret_key=os.environ['API_SECRET'])
try:
 cursor=date(2024,11,1);end=date(2025,12,31);parts=[]
 while cursor<=end:
  finish=min(end,cursor+timedelta(days=29));assert_period(cursor,finish)
  p=H/f'資料/minute_{cursor}.json'
  if p.exists():raw=json.loads(p.read_text(encoding='utf8'))
  else:
   raw={k:list(v) for k,v in dict(api.kbars(api.Contracts.Stocks['4938'],start=str(cursor),end=str(finish),timeout=60000)).items()}
   p.write_text(json.dumps(raw,default=lambda x:x.item()),encoding='utf8')
  frame=pd.DataFrame(raw)
  if len(frame):parts.append(frame)
  print('FORMAL_FETCH',cursor,finish,len(frame),flush=True);cursor=finish+timedelta(days=1)
finally:
 with contextlib.redirect_stdout(io.StringIO()):api.logout()
minute=pd.concat(parts).drop_duplicates('ts').sort_values('ts');minute['timestamp']=minute.ts.map(sino_ts_to_local_timestamp)
assert_timestamps(minute.timestamp);minute['bucket']=(minute.timestamp//300*300).astype('int64')
bars=minute.groupby('bucket').agg(open=('Open','first'),high=('High','max'),low=('Low','min'),close=('Close','last'),volume=('Volume','sum')).reset_index()
p=H/'資料/4938_history.sqlite3'
if not p.exists():
 with sqlite3.connect(p) as d:
  d.execute('CREATE TABLE market_kbars(stock_code TEXT,freq_minutes INTEGER,kbar_timestamp INTEGER,open REAL,high REAL,low REAL,close REAL,volume INTEGER)')
  d.executemany('INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?)',[('4938',5,int(r.bucket),r.open,r.high,r.low,r.close,int(r.volume)) for r in bars.itertuples()])
index=[];daily=[]
for year,months in [(2024,[11,12]),(2025,list(range(1,13)))]:
 for m in months:
  stamp=f'{year}{m:02d}01';assert_period(f'{year}-{m:02d}-01',f'{year}-{m:02d}-01')
  for endpoint,dest in [('FMTQIK',index),('STOCK_DAY',daily)]:
   p=H/f'資料/{endpoint}_{stamp}.json';url=f'https://www.twse.com.tw/exchangeReport/{endpoint}?response=json&date={stamp}'+('&stockNo=4938' if endpoint=='STOCK_DAY' else '')
   if not p.exists():
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as f:obj=json.load(f)
    if obj.get('stat')!='OK':raise RuntimeError('Official source unavailable; preserve consumed state')
    p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf8')
   for row in json.loads(p.read_text(encoding='utf8'))['data']:
    y,mo,day=map(int,row[0].split('/'));day=f'{y+1911:04d}-{mo:02d}-{day:02d}';assert_period(day,day)
    dest.append({'date':day,'close':float(row[4 if endpoint=='FMTQIK' else 6].replace(',','')),**({'volume_shares':int(row[1].replace(',',''))} if endpoint=='STOCK_DAY' else {})})
  print('FORMAL_OFFICIAL',stamp,flush=True)
for name,obj in [('index_daily.json',index),('4938_official_daily.json',daily)]:
 (H/'資料'/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
print('FORMAL_DATA_READY',len(bars),flush=True)
