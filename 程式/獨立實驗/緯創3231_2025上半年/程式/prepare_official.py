"""Bounded official daily price/volume/calendar snapshots; no strategy replay."""
import json,sys,urllib.request,statistics,sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
H=Path(__file__).resolve().parents[1];R=H.parents[1];sys.path.insert(0,str(R))
from trading_system.research_guard import assert_development_period
index=[];daily=[];sources=[]
for year,months in [(2024,[11,12]),(2025,list(range(1,7)))]:
 for m in months:
  date=f'{year}{m:02d}01';assert_development_period(f'{year}-{m:02d}-01',f'{year}-{m:02d}-01')
  for endpoint,out in [('FMTQIK',index),('STOCK_DAY',daily)]:
   p=H/f'資料/{endpoint}_{date}.json';url=f'https://www.twse.com.tw/exchangeReport/{endpoint}?response=json&date={date}'+('&stockNo=3231' if endpoint=='STOCK_DAY' else '')
   if not p.exists():
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as f:obj=json.load(f)
    if obj.get('stat')!='OK':raise RuntimeError('Official daily source unavailable')
    p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf8')
   obj=json.loads(p.read_text(encoding='utf8'))
   for row in obj['data']:
    y,mo,d=map(int,row[0].split('/'));day=f'{y+1911:04d}-{mo:02d}-{d:02d}';assert_development_period(day,day)
    if not '2024-11-01'<=day<='2025-06-30':raise ValueError('Escaped authorized dates')
    out.append({'date':day,'close':float(row[4 if endpoint=='FMTQIK' else 6].replace(',','')),
                **({'volume_shares':int(row[1].replace(',',''))} if endpoint=='STOCK_DAY' else {})})
   sources.append({'url':url,'file':p.name})
  print('OFFICIAL',date,flush=True)
for name,obj in [('index_daily.json',index),('3231_official_daily.json',daily),('official_sources.json',sources)]:
 (H/'資料'/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
events={'events': [{'ex_date': '2025-06-03', 'payment_date': '2025-06-27', 'cash_per_share': 3.79927437}], 'sources': ['https://www.wistron.com/en/Investors/ShareholdersServices/DividendHistory', 'https://superpy.kgieworld.com.tw/kgipythonapi/stockInfo/finance/exDividend?stockCode=3231'], 'scope': 'Only 2024Nov through2025June actions; no subsequent market data accessed.'}
(H/'資料/3231_cash_dividends.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf8')
tz=ZoneInfo('Asia/Taipei');totals={}
with sqlite3.connect((H/'資料/3231_history.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as d:
 for ts,v in d.execute('SELECT kbar_timestamp,volume FROM market_kbars ORDER BY kbar_timestamp'):
  t=datetime.fromtimestamp(ts,tz)
  if '09:00'<=t.strftime('%H:%M')<='13:25':totals[t.date().isoformat()]=totals.get(t.date().isoformat(),0)+v
ratios=[x['volume_shares']/totals[x['date']] for x in daily if totals.get(x['date'],0)>0]
median=statistics.median(ratios)
if not 500<median<1500:raise RuntimeError('Volume unit cannot be verified')
(H/'資料/preflight.json').write_text(json.dumps({'calendar_sessions':len(index),'official_stock_days':len(daily),'volume_unit_ratio_median':median,'volume_to_shares':1000,'missing_entire_days':sorted({x['date'] for x in daily}-set(totals)),'status':'PASS_UNIT_MAGNITUDE_ONLY','locked_market_data_used':False},indent=2),encoding='utf8')
print('OFFICIAL_PREFLIGHT_PASS',len(index),median)
