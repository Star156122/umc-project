"""Download bounded historical data for the two preregistered stocks only."""
import contextlib,io,json,os,sqlite3,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
from dotenv import load_dotenv
from trading_system.research_guard import assert_development_period,assert_timestamps
from scripts.import_ml_kbars import fetch_one
load_dotenv(R/'.env');assert_development_period('2024-11-01','2025-06-30')
import shioaji as sj
api=sj.Shioaji(simulation=True)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):api.login(api_key=os.environ['API_KEY'],secret_key=os.environ['API_SECRET'])
try:
 for stock,name in [('3231','緯創'),('2892','第一金')]:
  H=R/f'獨立實驗/{name}{stock}_2025上半年';p=H/f'資料/{stock}_history.sqlite3'
  if p.exists():raise RuntimeError('Preserve downloaded data; do not append duplicates')
  rows=[];count=0
  for start,end in [('2024-11-01','2024-12-31'),('2025-01-01','2025-06-30')]:
   part,n,_,_=fetch_one(api,stock,start,end,5);rows.extend(part);count+=n
  if not rows:raise RuntimeError('No source bars returned')
  assert_timestamps(r[4] for r in rows)
  with sqlite3.connect(p) as d:
   d.execute('CREATE TABLE market_kbars(stock_code TEXT,freq_minutes INTEGER,range_start TEXT,range_end TEXT,kbar_timestamp INTEGER,open REAL,high REAL,low REAL,close REAL,volume INTEGER)')
   d.executemany('INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
  (H/'資料/history_fetch_status.json').write_text(json.dumps({'stock':stock,'status':'DOWNLOADED_PENDING_AUDIT','bars':len(rows),'minute_rows':count,'locked_data_used':False},indent=2),encoding='utf8')
  print('FETCHED',stock,len(rows),flush=True)
finally:
 with contextlib.redirect_stdout(io.StringIO()):api.logout()
