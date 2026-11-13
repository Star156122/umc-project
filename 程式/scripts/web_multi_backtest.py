"""Web date adapter. Keeps frozen strategy, training and risk settings unchanged."""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trading_system.research_guard import assert_development_period

def validate_period(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    assert_development_period(first, last)
    if not date(2024,7,1) <= first <= last <= date(2025,6,30):
        raise ValueError('BLOCKED_PERIOD_OUTSIDE_AUTHORIZED_DEVELOPMENT')

def chart_data(config, code):
    period = config['periods']['backtest']
    validate_period(period['start'], period['end'])
    if code not in config['stock_universe']:
        raise ValueError('BLOCKED_UNSUPPORTED_STOCK')
    tz=ZoneInfo('Asia/Taipei')
    lower=int(datetime.fromisoformat(period['start']).replace(tzinfo=tz).timestamp())
    upper=int((datetime.fromisoformat(period['end'])+timedelta(days=1)).replace(tzinfo=tz).timestamp())
    db=(ROOT/config['technical']['data_path']).resolve()
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        rows=connection.execute('SELECT kbar_timestamp,open,high,low,close FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp',(code,lower,upper)).fetchall()
    return {'source':'回測使用的技術指標資料庫','interval_minutes':5,'period':period,'candles':[dict(zip(['time','open','high','low','close'],r)) for r in rows]}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--start-date',required=True)
    parser.add_argument('--end-date',required=True)
    parser.add_argument('--stock-code',required=True)
    parser.add_argument('--strategies',nargs='+',required=True)
    args=parser.parse_args()
    try:
        validate_period(args.start_date,args.end_date)
        if (ROOT/'hybrid/config/web_main_strategy_v1.json').exists():
            from hybrid.core.web_main_backtest import run
            output=run(args.stock_code,args.start_date,args.end_date,args.strategies)
            print(json.dumps({'status':'COMPLETED_DEVELOPMENT_RESEARCH','output_directory':str(output),'data_role':'historical_development','profile_id':'main-buy-noadd-noextension-common-v1-20261114','final_oos_used':False,'holdout_policy_modified':False},ensure_ascii=False))
            return 0
        config=json.loads((ROOT/'hybrid/config/multi_strategy_development_v1.json').read_text(encoding='utf-8'))
        config['periods']['backtest']={'start':args.start_date,'end':args.end_date}
        # This snapshot is per request; never rewrite the shared research configuration.
        folder=ROOT/'exports'/'web_requests';folder.mkdir(parents=True,exist_ok=True)
        snapshot=folder/(str(uuid4())+'.json')
        snapshot.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        from hybrid.scripts.run_multi_strategy_backtest_v1 import run_preflight, run_experiment
        run_preflight(config,args.stock_code,args.strategies)
        output=run_experiment(config,args.stock_code,args.strategies)
        report=Path(output)/'run.json'
        payload=json.loads(report.read_text(encoding='utf-8'))
        try: payload['market_chart']=chart_data(config,args.stock_code)
        except Exception: payload['market_chart']={'candles':[],'error':'回測已完成，但 K 線資料無法讀取。'}
        payload['audit']['web_request_config']=str(snapshot)
        report.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':'COMPLETED_DEVELOPMENT_RESEARCH','output_directory':str(output),'data_role':config['data_role'],'final_oos_used':False,'holdout_policy_modified':False},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)},ensure_ascii=False))
        return 2
if __name__=='__main__':sys.exit(main())
