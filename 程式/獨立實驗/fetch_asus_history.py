"""Guarded data-only fetch; local isolated SQLite only, no trades/orders."""
import contextlib,io,json,os,sqlite3,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];H=R/'獨立實驗/華碩2357_2025上半年';sys.path.insert(0,str(R))
from dotenv import load_dotenv
from trading_system.research_guard import assert_development_period,assert_timestamps
assert_development_period('2024-11-01','2025-06-30')
load_dotenv(R/'.env')
status={'stock':'2357','start':'2024-11-01','end':'2025-06-30','original_databases_modified':False,'locked_data_used':False}
try:
 if not os.getenv('API_KEY') or not os.getenv('API_SECRET'):raise RuntimeError('MISSING_HISTORICAL_DATA_API_CREDENTIALS')
 import shioaji as sj
 from scripts.import_ml_kbars import fetch_one
 api=sj.Shioaji(simulation=True)
 # Suppress SDK login details; credentials and account information are never exported.
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  api.login(api_key=os.getenv('API_KEY'),secret_key=os.getenv('API_SECRET'))
 try:
  # Keep Training warmup and Validation requests separate, as required by ML role guard.
  warm,n1,f1,l1=fetch_one(api,'2357','2024-11-01','2024-12-31',5)
  evaluation,n2,f2,l2=fetch_one(api,'2357','2025-01-01','2025-06-30',5)
  rows=warm+evaluation;count=n1+n2;first=f1;last=l2
 finally:
  with contextlib.redirect_stdout(io.StringIO()):api.logout()
 if not rows:raise RuntimeError('PROVIDER_RETURNED_NO_HISTORY')
 assert_timestamps(r[4] for r in rows)
 with sqlite3.connect(H/'資料/2357_history.sqlite3') as d:
  d.execute('CREATE TABLE IF NOT EXISTS market_kbars(stock_code TEXT,freq_minutes INTEGER,range_start TEXT,range_end TEXT,kbar_timestamp INTEGER,open REAL,high REAL,low REAL,close REAL,volume INTEGER)')
  d.executemany('INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
 status.update(status='FETCHED_PENDING_SESSION_AUDIT',bars=len(rows),source_minute_rows=count,first=first,last=last)
except Exception as e:
 status.update(status='BLOCKED_DATA',error_type=type(e).__name__,error=str(e) if isinstance(e,RuntimeError) else 'Historical provider connection/download failed; account details suppressed.')
(H/'資料/history_fetch_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(status,ensure_ascii=True))
if status['status']=='BLOCKED_DATA':raise SystemExit(2)
