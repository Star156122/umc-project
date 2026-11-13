"""Bounded official daily data and volume preflight; never runs a strategy."""
import json
import sqlite3
from datetime import datetime
from engine import HOME,ROOT,TZ,validate
from prepare import fetch,dump,sha
from trading_system.research_guard import assert_development_period

c=json.loads((HOME/'設定/experiment.json').read_text(encoding='utf-8'))
validate(c)
daily=[];sources=[]
for year in [2023,2024]:
    for month in range(1,13):
        stamp=f'{year}{month:02d}01'
        assert_development_period(f'{year}-{month:02d}-01',f'{year}-{month:02d}-01')
        url=f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={stamp}&stockNo=2303'
        path=HOME/'資料'/f'2303_daily_{stamp}.json'
        data=fetch(url,path)
        for r in data['data']:
            y,m,d=map(int,r[0].split('/'))
            day=f'{y+1911:04d}-{m:02d}-{d:02d}'
            assert_development_period(day,day)
            if not '2023-01-01'<=day<='2024-12-31':
                raise ValueError('Official response escaped approved dates')
            daily.append({'date':day,'volume_shares':int(r[1].replace(',','')),'close':float(r[6].replace(',',''))})
        sources.append({'url':url,'file':path.name,'sha256':sha(path),'fields':data['fields']})
        print('Official UMC month',stamp,flush=True)
dump(HOME/'資料/2303_official_daily.json',daily)
dump(HOME/'資料/official_sources.json',sources)
lo=int(datetime(2023,1,1,tzinfo=TZ).timestamp());hi=int(datetime(2025,1,1,tzinfo=TZ).timestamp())
with sqlite3.connect((ROOT/c['revised_db']).resolve().as_uri()+'?mode=ro',uri=True) as db:
    db.execute('PRAGMA query_only=ON')
    rows=db.execute('SELECT kbar_timestamp,volume FROM market_kbars WHERE stock_code=? AND freq_minutes=5 '
                    'AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp',('2303',lo,hi)).fetchall()
totals={}
for ts,v in rows:
    dt=datetime.fromtimestamp(ts,TZ)
    if '09:00'<=dt.strftime('%H:%M')<='13:25':
        day=dt.date().isoformat();totals[day]=totals.get(day,0)+v
official={r['date']:r for r in daily}
ratios=sorted(official[d]['volume_shares']/v for d,v in totals.items() if d in official and v>0)
median=ratios[len(ratios)//2]
if not 500<median<1500:
    raise ValueError('Volume unit cannot be automatically verified: '+str(median))
dump(HOME/'資料/preflight.json',{'stock':'2303','official_daily_rows':len(daily),'days_compared':len(ratios),
    'official_to_local_volume_median':median,'volume_to_shares':1000,'status':'PASS_UNIT_MAGNITUDE_CHECK',
    'source':'Source importer preserves Shioaji ordinary Volume lots; official daily shares confirm magnitude, not exact session-volume parity.',
    'locked_data_used':False,'index_sha256':sha(HOME/'資料/index_daily.json')})
print('Volume preflight PASS',median,flush=True)
