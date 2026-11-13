"""Isolated, guarded chronological simulator. Does not mutate legacy code or DBs."""
from __future__ import annotations
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import polars as pl

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(ROOT))
from trading_system.research_guard import assert_development_period, assert_timestamps
from trading_system import backtest as bt
from hybrid.core.short_swing_ab_test import entry_components
from hybrid.core.data_eligibility_v1 import audit_session, select_unambiguous_valid_bars
from prepare import dump, sha

TZ = ZoneInfo('Asia/Taipei')
REQUIRED = ['MA_FAST','MA_MID','MA_SLOW','TREND_MA','TREND_SLOPE','BB_UPPER','BREAKOUT_HIGH','BREAKOUT_LOW']
STAGES = ['資料資格與暖機','新突破事件','布林上軌','MA20高於MA60','MA120與斜率','收盤高於MA240',
          '進場時間','市場濾網','ML Gate','持倉與每日次數','再進場高點','下一根成交資格',
          '流動性至少一張','風險至少一張','現金至少一張','委託核准','實際買進']


def validate(c):
    assert_development_period(c['period']['start'], c['period']['end'])
    if c['period'] != {'start':'2024-10-01','end':'2024-12-31'} or c['stock'] not in ['2303','2330','2379','2454','3034']:
        raise ValueError('Experiment is fixed to preregistered five stocks, 2024Q4')
    if c['locked_data_used'] or c['parameter_search'] or not c['no_retraining']:
        raise ValueError('Invalid research scope')
    x=c.get('holding_extension',{})
    if x.get('enabled') and (x.get('extra_bars')!=54 or x.get('max_extensions')!=1
                            or x.get('allow_adds_during_extension') is not False):
        raise ValueError('Holding extension is frozen to one 54-bar extension, no extension adds')


def holding_review(pos, bar, market, ml, c, slip, pending_exit=None):
    """Only completed-bar observations; never override an existing protective exit."""
    if pos['held']<c['max_hold_bars']:
        return pending_exit,None
    x=c.get('holding_extension',{})
    if not x.get('enabled'):
        return pending_exit or 'TIMEOUT',None
    if pending_exit:
        return pending_exit,{'action':'PROTECTIVE_EXIT_PRIORITY','reason':pending_exit}
    if pos['held']>=c['max_hold_bars']+x['extra_bars']:
        return 'TIMEOUT_EXTENSION_CAP',{'action':'EXIT','reason':'HARD_CAP'}
    q=sum(batch['q'] for batch in pos['batches'])
    value=q*bar['Close']*(1-slip)
    cost=sum(batch['q']*batch['price']+batch['fee'] for batch in pos['batches'])
    profit=value-commission(q,bar['Close']*(1-slip),c)-value*c['normal_sell_tax']-cost+pos.get('dividends',0)
    keys=['MA_FAST','MA_MID','TREND_MA','TREND_SLOPE']
    trend=bool(bar.get('ready') and all(bar.get(k) is not None and math.isfinite(bar[k]) for k in keys)
               and bar['Close']>bar['MA_FAST']>bar['MA_MID'] and bar['Close']>bar['TREND_MA'] and bar['TREND_SLOPE']>0)
    checks={'net_profit_positive':profit>0,'trend':trend,'market':bool(market),'ml_buy':ml=='BUY'}
    failed=[k for k,v in checks.items() if not v]
    event={'checks':checks,'failed':failed,'estimated_net_profit':profit,'ml':ml,
           'stop_unchanged':pos['stop'],'held':pos['held'],'hard_cap':c['max_hold_bars']+x['extra_bars']}
    if failed:
        return ('EXTENSION_REVIEW_FAILED' if pos.get('extension_count',0) else 'TIMEOUT_EXTENSION_DENIED'),dict(event,action='EXIT',reason=','.join(failed))
    if not pos.get('extension_count',0):
        pos['extension_count']=1
        return None,dict(event,action='GRANT',reason='ALL_CONDITIONS_PASS')
    return None,dict(event,action='CONTINUE',reason='ALL_CONDITIONS_PASS')


def load_bars(path, c):
    validate(c)
    lo = int(datetime(2024,9,16,tzinfo=TZ).timestamp())
    hi = int(datetime(2025,1,1,tzinfo=TZ).timestamp())
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        rows = [dict(r) for r in con.execute('SELECT stock_code,kbar_timestamp,open,high,low,close,volume '
                'FROM market_kbars WHERE stock_code IN (?,?,?,?,?) AND freq_minutes=5 '
                'AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp',
                (*c['peer_pool'],lo,hi))]
    assert_timestamps(r['kbar_timestamp'] for r in rows)
    by={}
    for r in rows:
        d=datetime.fromtimestamp(r['kbar_timestamp'],TZ)
        if '09:00' <= d.strftime('%H:%M') <= '13:25':
            by.setdefault((r['stock_code'],d.date().isoformat()),[]).append(r)
    return by


def indicators(by, stock, calendar, params, gap_mask=True, dividends=None):
    rows=[]
    valid_dates=set()
    slots={}
    for day_index, day in enumerate(calendar):
        raw=by.get((stock,day),[])
        if audit_session(raw,day)['session_complete']:
            valid_dates.add(day)
        for r in select_unambiguous_valid_bars(raw,day):
            minute=datetime.fromtimestamp(r['kbar_timestamp'],TZ)
            slot=day_index*54+(minute.hour*60+minute.minute-540)//5
            slots[r['kbar_timestamp']]=slot
            rows.append(r)
    app=bt.AppConfig(**params,code=stock,backtest_start='2023-01-01',backtest_end='2024-12-31',
                     backfill_start='2023-01-01',backfill_end='2024-12-31')
    adjusted=[]
    cumulative={d:sum(e['cash_per_share'] for e in (dividends or []) if e['ex_date']<=d) for d in calendar}
    for r in rows:
        day=datetime.fromtimestamp(r['kbar_timestamp'],TZ).date().isoformat()
        adj=cumulative[day]
        adjusted.append({'kbar_timestamp':r['kbar_timestamp'],'Open':r['open']+adj,'High':r['high']+adj,
                         'Low':r['low']+adj,'Close':r['close']+adj,'Volume':r['volume']})
    raw=pl.DataFrame(adjusted)
    records=bt.add_indicators(raw,app).to_dicts()
    for i,r in enumerate(records):
        ts=r['kbar_timestamp']
        r['day']=datetime.fromtimestamp(ts,TZ).date().isoformat()
        adj=cumulative[r['day']]
        # Causal forward cash adjustment; convert price levels back to current raw-price units.
        for k in ['Open','High','Low','Close','MA_FAST','MA_MID','MA_SLOW','TREND_MA','BB_MIDDLE',
                  'BB_UPPER','BB_LOWER','BREAKOUT_HIGH','BREAKOUT_LOW']:
            if r.get(k) is not None:
                r[k]-=adj
        r['time']=datetime.fromtimestamp(ts,TZ).isoformat()
        r['CLOSE']=r['Close']
        r['slot']=slots[ts]
        r['clear']=i>=239 and (not gap_mask or slots[ts]-slots[records[i-239]['kbar_timestamp']]==239)
        r['liq']=float(np.mean([x['Volume'] for x in records[max(0,i-19):i+1]])) if i>=19 else 0.0
        for k in REQUIRED:
            if r[k] is not None and not math.isfinite(r[k]):
                r[k]=None
        r['ready']=r['clear'] and all(r[k] is not None for k in REQUIRED)
    return records,valid_dates


def load_inputs(c):
    validate(c)
    index=json.loads((HOME/'資料/index_daily.json').read_text(encoding='utf-8'))
    calendar=[r['date'] for r in index]
    if len(calendar)!=len(set(calendar)) or calendar != sorted(calendar):
        raise ValueError('Invalid official calendar')
    for day in calendar:
        assert_development_period(day,day)
    old=load_bars(ROOT/c['old_db'],c)
    raw=load_bars(ROOT/c['revised_db'],c)
    common=[d for d in calendar if all(audit_session(old.get((s,d),[]),d)['session_complete'] for s in c['peer_pool'])]
    params=json.loads((ROOT/'configs/technical_v2_candidate.json').read_text(encoding='utf-8'))['candidate_parameters']
    dividends=[]
    if c.get('corporate_action_mode')=='causal_cash_adjustment':
        dividends=json.loads((HOME/('資料/'+c['stock']+'_cash_dividends.json')).read_text(encoding='utf-8'))['events']
        for e in dividends:
            assert_development_period(e['ex_date'],e['payment_date'])
    # Old indicators use the same complete-day stream as the frozen legacy runner.
    old_records,_=indicators(old,c['stock'],common,params,gap_mask=False,dividends=dividends)
    new_records,new_complete=indicators(raw,c['stock'],calendar,params,gap_mask=True,dividends=dividends)
    old_map={r['kbar_timestamp']:r for r in old_records}
    old_prev={r['kbar_timestamp']:(old_records[i-1] if i else None) for i,r in enumerate(old_records)}
    proxy={d:np.mean([old[(s,d)][-1]['close'] for s in c['peer_pool'] if s!=c['stock']]) for d in common}
    contexts={}
    for i,d in enumerate(calendar):
        prior=[p for p in common if p<d][-20:]
        contexts[d]={
            'old':len(prior)==20 and proxy[prior[-1]]>np.mean([proxy[p] for p in prior]),
            'new':i>=20 and index[i-1]['close']>np.mean([x['close'] for x in index[i-20:i]]),
            'old_ready':len(prior)==20,'new_ready':i>=20,
            'index_reference':calendar[i-1] if i else None,
            'index_close':index[i-1]['close'] if i else None,
            'index_sma20':float(np.mean([x['close'] for x in index[i-20:i]])) if i>=20 else None}
    with (ROOT/c['oof_path']).open(encoding='utf-8-sig',newline='') as f:
        oof=[r for r in csv.DictReader(f) if r['stock_code']==c['stock'] and r['model']==c['model']]
    lookup={}
    for r in oof:
        for key in ['signal_date','entry_date','target_date']:
            assert_development_period(r[key],r[key])
        if r['entry_date'] in lookup:
            raise ValueError('Duplicate ML effective date')
        lookup[r['entry_date']]=r
    # Validate official calendar overlap and ordinary OHLC for old/new shared rows.
    raw_map={r['kbar_timestamp']:r for r in new_records}
    parity=all(all(abs(old_map[t][k]-raw_map[t][k])<1e-8 for k in ['Open','High','Low','Close','Volume'])
               for t in old_map if t in raw_map)
    if not parity:
        raise ValueError('Old/raw shared OHLCV mismatch')
    audit={'official_sessions':len(calendar),'old_complete_common_sessions':len(common),
           'target_complete_sessions':len(new_complete),'target_observed_bars':len(new_records),
           'old_indicator_bars':len(old_records),'shared_ohlcv_parity':parity,'oof_rows':len(oof),
           'new_dates':sorted({r['day'] for r in new_records}-set(common)),
           'legacy_indicator_method':'original backtest.add_indicators; original common-date stream',
           'new_indicator_method':'same original add_indicators; real raw stream; expected 240-slot gap mask',
           'calendar_source':'official FMTQIK all 24 requested months; no independent holiday-calendar cross-validation',
           'corporate_action_support':'CAUSAL_CASH_DIVIDENDS_ISSUER_SOURCE' if dividends else 'UNVERIFIED',
           'cash_dividend_events':dividends,
           'execution_data_mode':c.get('execution_data_mode','BOARD_LOT_RESEARCH')}
    return {'bars':new_records,'old_map':old_map,'old_prev':old_prev,'common':set(common),'contexts':contexts,
            'calendar':calendar,'ml':lookup,'audit':audit,'dividends':{e['ex_date']:e for e in dividends}}


def ml_status(day, timestamp, lookup, calendar):
    r=lookup.get(day)
    if r is None:
        return 'MISSING'
    i=calendar.index(day)
    probabilities=[float(r[k]) for k in ['prob_sell','prob_hold','prob_buy']]
    valid=(i>0 and r['signal_date']==calendar[i-1] and r['entry_date']==day
           and str(r['is_oof']).lower()=='true' and r['data_role']=='training'
           and r['label_version']=='ml_v1_fixed_1pct'
           and r['fold_train_end']<r['signal_date']
           and r['fold_validation_start']<=r['signal_date']<=r['fold_validation_end']
           and datetime.fromisoformat(r['decision_time']).timestamp()<timestamp
           and all(math.isfinite(p) and 0<=p<=1 for p in probabilities)
           and abs(sum(probabilities)-1)<1e-5
           and r['prediction']==('SELL','HOLD','BUY')[int(np.argmax(probabilities))])
    return r['prediction'] if valid else 'INVALID_OR_LATE'


def fee(value,c):
    return max(c['minimum_fee'],math.floor(value*c['fee_rate'])) if value>0 else 0


def commission(q,p,c):
    if c['lot_size']==1 and q>=1000 and q%1000:
        return fee((q//1000)*1000*p,c)+fee((q%1000)*p,c)
    return fee(q*p,c)


def round_lot(q,c):
    return max(0,int(q)//c['lot_size']*c['lot_size'])


def approved_quantity(price, cash, equity, liq, revised, c, slip, position=None, ceiling=None):
    p=price*(1+slip)
    pos=position or {'batches':[],'budget':equity*c['risk_fraction'],'stop':p*(1-c['stop_pct'])}
    budget=min(pos['budget'],equity*c['risk_fraction'])
    occupied=0.0
    for b in pos['batches']:
        occupied+=b['q']*max(b['price']-b.get('dividend_per_share',0)-pos['stop'],0)+b['fee']
        occupied+=b['q']*pos['stop']*(c['fee_rate']+c['normal_sell_tax']+slip+c['risk_gap_buffer'])
    if revised:
        per=max(p-pos['stop'],0)+p*c['fee_rate']+pos['stop']*(c['fee_rate']+c['normal_sell_tax']+slip+c['risk_gap_buffer'])
        risk=math.floor(max(0,budget-occupied)/per) if per>0 and p>pos['stop'] else 0
        exposure=math.inf
        engine=math.inf
    else:
        risk=math.floor(equity*c['risk_fraction']/(p*c['stop_pct']))
        exposure=math.floor(equity*0.4/p)
        engine=1000
    liquidity=math.floor(liq*c['volume_to_shares']*c['volume_fraction'])
    affordable=math.floor(max(0,cash-c['minimum_fee'])/(p*(1+c['fee_rate'])))
    caps={'risk':risk,'liquidity':liquidity,'cash':affordable,'exposure':exposure,'engine':engine}
    q=round_lot(min(caps.values()),c)
    if ceiling is not None:
        q=min(q,ceiling)
    while q and q*p+commission(q,p,c)>cash:
        q-=c['lot_size']
    if revised:
        # Minimum fees matter for small odd-lot adds; never grant a fresh risk budget.
        while q>0:
            risk_exact=q*max(p-pos['stop'],0)+commission(q,p,c)+commission(q,pos['stop'],c)
            risk_exact+=q*pos['stop']*(c['normal_sell_tax']+slip+c['risk_gap_buffer'])
            if risk_exact<=max(0,budget-occupied)+1e-8:
                break
            q-=c['lot_size']
    return q,{k:(None if math.isinf(v) else v) for k,v in caps.items()}


def high_expiry(exit_day,calendar,c):
    return calendar[min(len(calendar)-1,calendar.index(exit_day)+c['reentry_expiry_sessions'])]


def simulate(c,data,variant,slip,scope='full',window=None):
    validate(c)
    revised=variant in ['B','D']
    hybrid=variant in ['C','D']
    bars=data['bars']; cal=data['calendar']; old_map=data['old_map']
    cash=float(c['initial_capital']); pos=None; pending=None; pending_exit=None
    receivables=[]
    orders=[]; trades=[]; events=[]; equity=[]; blocked=Counter(); ml_counts=Counter()
    holding_events=[]
    minimum_quantity=int(c['lot_size'])
    stages=[s.replace('至少一張','至少一股') if minimum_quantity==1 else s for s in STAGES]
    funnel={s:{'input':0,'pass':0,'blocked':0} for s in stages}
    daily={}; cooldown=0; last_day=None; last_exit_i=-999; H=None; expires=None; armed=True
    accepted_dates=None
    if scope=='common':
        accepted_dates=set(data['common'])
    if window is None:
        window=(c['period']['start'],c['period']['end'])
    if scope=='common':
        # Effective-date coverage restriction independent of BUY classification.
        coverage={d for d in cal if ml_status(d,datetime.fromisoformat(d+'T09:00:00+08:00').timestamp(),data['ml'],cal) in ['BUY','HOLD','SELL']}
        accepted_dates=accepted_dates & coverage

    def exit_position(i,reference,reason,when):
        nonlocal cash,pos,H,expires,cooldown,last_exit_i,pending_exit,armed
        if c['lot_size']==1:
            same_day=any(batch['day']==bars[i]['day'] for batch in pos['batches'])
            too_early=datetime.fromisoformat(when).strftime('%H:%M')<'09:10'
            if same_day or too_early:
                why='SAME_DAY_ODD_EXIT_UNAVAILABLE' if same_day else 'BEFORE_ODD_OPEN'
                history=pos.setdefault('deferred_exits',[])
                if not any(x['date']==bars[i]['day'] and x['constraint']==why for x in history):
                    history.append({'date':bars[i]['day'],'time':when,'constraint':why,'trigger':reason})
                if pending_exit is None or reason.startswith('STOP'):
                    pending_exit=reason if reason.endswith('_DEFERRED') else reason+'_DEFERRED'
                return False
        price=reference*(1-slip); q=sum(b['q'] for b in pos['batches'])
        cost=sum(b['q']*b['price']+b['fee'] for b in pos['batches'])
        fee_out=commission(q,price,c)
        tax=sum(price*b['q']*(c['same_day_sell_tax'] if b['day']==bars[i]['day'] else c['normal_sell_tax']) for b in pos['batches'])
        cash+=price*q-fee_out-tax
        net=price*q-fee_out-tax-cost+pos.get('dividends',0)
        orders.append({'time':when,'timestamp':int(datetime.fromisoformat(when).timestamp()),'action':'SELL','quantity':q,'price':price,
                       'reference':reference,'fee':fee_out,'tax':tax,'reason':reason,'trade_id':len(trades)+1,
                       'slippage_cost':abs(price-reference)*q})
        trades.append({'trade_id':len(trades)+1,'entry_time':pos['entry_time'],'exit_time':when,
                       'entry_price':pos['batches'][0]['price'],'exit_price':price,'quantity':q,'net_pnl':net,
                       'gross_pnl':price*q-sum(b['q']*b['price'] for b in pos['batches'])+pos.get('dividends',0),
                       'net_return':net/cost,'holding_bars':pos['held'],'exit_reason':reason,'adds':pos['adds'],
                       'entry_day':pos['batches'][0]['day'],'exit_day':bars[i]['day'],
                       'cash_dividends':pos.get('dividends',0),'deferred_exits':pos.get('deferred_exits',[]),
                       'holding_extensions':pos.get('extension_count',0)})
        H=pos['high']; expires=high_expiry(bars[i]['day'],cal,c); armed=True
        pos=None; cooldown=c['original_cooldown']; last_exit_i=i; pending_exit=None
        return True

    for i,b in enumerate(bars):
        day=b['day']; ts=b['kbar_timestamp']
        if not window[0]<=day<=window[1]:
            continue
        assert_development_period(day,day)
        if day!=last_day:
            daily.setdefault(day,0)
            if not revised:
                cooldown=0
            last_day=day
            for item in receivables:
                if not item['paid'] and item['payment_date']<=day:
                    cash+=item['amount']; item['paid']=True
            action=data.get('dividends',{}).get(day)
            if action:
                d=action['cash_per_share']
                if H is not None:
                    H-=d
                if pos:
                    amount=sum(x['q'] for x in pos['batches'])*d
                    pos['dividends']=pos.get('dividends',0)+amount
                    receivables.append({'payment_date':action['payment_date'],'amount':amount,'paid':False})
                    pos['stop']-=d;pos['high']-=d
                    for batch in pos['batches']:
                        batch['dividend_per_share']=batch.get('dividend_per_share',0)+d
        if revised and H is not None and day>expires:
            H=None; expires=None; armed=False
        # All queued actions were decided on a prior completed bar. Exit before add.
        if pos and i and b['slot']-bars[i-1]['slot']>1:
            exit_position(i,b['Open'],'DATA_GAP_NEXT_OBSERVED_OPEN',b['time'])
        if pos and pending_exit:
            exit_position(i,b['Open'],pending_exit,b['time'])
        if pending:
            if pending['fill']==ts and last_exit_i!=i:
                current=cash+(sum(x['q'] for x in pos['batches'])*b['Open'] if pos else 0)+sum(x['amount'] for x in receivables if not x['paid'])
                q,caps=approved_quantity(b['Open'],cash,current,pending['liq'],revised,c,slip,pos,pending['q'])
                event=events[pending['event']]
                if q:
                    price=b['Open']*(1+slip); fee_in=commission(q,price,c)
                    cash-=price*q+fee_in
                    if cash<-1e-7:
                        raise AssertionError('Negative cash')
                    add=pos is not None
                    if not add:
                        pos={'batches':[],'budget':current*c['risk_fraction'],'stop':price*(1-c['stop_pct']),
                             'entry_time':b['time'],'held':0,'adds':0,'high':b['Open']}
                        daily[day]+=1
                    else:
                        pos['adds']+=1
                    pos['batches'].append({'q':q,'price':price,'fee':fee_in,'day':day})
                    orders.append({'time':b['time'],'timestamp':ts,'action':'ADD' if add else 'BUY','quantity':q,
                                   'price':price,'reference':b['Open'],'fee':fee_in,'tax':0,'reason':'SIGNAL',
                                   'trade_id':len(trades)+1,'slippage_cost':abs(price-b['Open'])*q})
                    event['filled_quantity']=q
                    funnel['實際買進']['pass']+=1
                else:
                    event['fill_reject']='OPEN_GAP_BELOW_MINIMUM_UNIT'; blocked['OPEN_GAP_BELOW_MINIMUM_UNIT']+=1
                    funnel['實際買進']['blocked']+=1
            pending=None
        if pos:
            pos['held']+=1
            # Prior highest excludes exit bar future high; intrabar order cannot be reconstructed.
            if b['Open']<=pos['stop'] or b['Low']<=pos['stop']:
                ref=b['Open'] if b['Open']<=pos['stop'] else pos['stop']
                exit_position(i,ref,'STOP_GAP' if b['Open']<=pos['stop'] else 'STOP_INTRABAR',b['time'])
                if pos:
                    pos['high']=max(pos['high'],b['High'])
            else:
                pos['high']=max(pos['high'],b['High'])
        record=b if revised else old_map.get(ts)
        if pos:
            if record and pos['held']>=c['min_hold_bars'] and record['BREAKOUT_LOW'] is not None and b['Close']<record['BREAKOUT_LOW']:
                pending_exit='TECHNICAL_30_LOW'
            if pos['held']>=c['max_hold_bars']:
                if revised:
                    close_ml=ml_status(day,ts+300,data['ml'],cal)
                    pending_exit,review=holding_review(pos,b,data['contexts'][day]['new'],close_ml,c,slip,pending_exit)
                    if review:
                        holding_events.append({'time':datetime.fromtimestamp(ts+300,TZ).isoformat(),
                                               'trade_id':len(trades)+1,**review})
                else:
                    pending_exit=pending_exit or 'TIMEOUT'
        total_q=sum(x['q'] for x in pos['batches']) if pos else 0
        eq=cash+total_q*b['Close']+sum(x['amount'] for x in receivables if not x['paid'])
        equity.append({'timestamp':ts,'date':day,'equity':eq,'cash':cash,'exposure':total_q*b['Close']/eq if eq else 0})
        if not revised and cooldown>0 and not pos and i>last_exit_i:
            cooldown-=1
        # Diagnostic candidates include every observed bar; all blockers separately counted on raw crosses.
        prev_record=(bars[i-1] if i else None) if revised else data['old_prev'].get(ts)
        components=entry_components(b['Close'],record,prev_record) if record else None
        cross=bool(components and components.breakout)
        if record and record.get('BREAKOUT_HIGH') is not None and b['Close']<=record['BREAKOUT_HIGH']:
            armed=True
        ready=bool(record and record['ready'])
        eligible=ready and (revised or day in data['common'])
        market=data['contexts'][day]['new' if revised else 'old']
        ms=ml_status(day,ts+300,data['ml'],cal) if hybrid else 'NOT_APPLICABLE'
        same_day_next=i+1<len(bars) and bars[i+1]['day']==day and bars[i+1]['kbar_timestamp']==ts+300
        next_ok=same_day_next
        if minimum_quantity==1:
            # Odd-lot market has no execution before 09:10. Proxy price only, never actual odd-lot quote.
            next_ok=next_ok and datetime.fromtimestamp(ts+300,TZ).strftime('%H:%M')>='09:10'
        time_ok='09:00'<=datetime.fromtimestamp(ts+300,TZ).strftime('%H:%M')<=c['entry_cutoff']
        scope_ok=(accepted_dates is None or day in accepted_dates)
        scope_ok=scope_ok and (not hybrid or any(f['start']<=day<=f['end'] for f in c['folds']))
        adding=pos is not None
        if revised:
            state_ok=last_exit_i!=i and not pending_exit and (not adding or pos['adds']<c['revised_max_adds'])
            if adding and pos.get('extension_count',0):
                state_ok=False  # Freeze size during the one-time extension.
        else:
            state_ok=not adding and last_exit_i!=i and not pending_exit and cooldown==0
        state_ok=bool(state_ok and (adding or daily[day]<(c['revised_max_entries'] if revised else c['original_max_entries'])))
        if adding:
            cost=sum(x['q']*x['price']+x['fee'] for x in pos['batches'])-pos.get('dividends',0)
            value=total_q*b['Close']*(1-slip)
            state_ok=state_ok and value-fee(value,c)-value*c['normal_sell_tax']>cost
        high_ok=not revised or adding or ((H is None or b['Close']>H) and armed)
        q,caps=approved_quantity(b['Close'],cash,eq,b['liq'],revised,c,slip,pos)
        checks=[eligible and scope_ok,cross, bool(components and components.bollinger_upper),
                bool(components and components.ma20_over_ma60),bool(components and components.ma120_and_slope),
                bool(components and components.close_over_ma240),time_ok,market,ms in ['BUY','NOT_APPLICABLE'],
                state_ok,high_ok,next_ok,caps['liquidity']>=minimum_quantity,caps['risk']>=minimum_quantity,
                caps['cash']>=minimum_quantity,q>=minimum_quantity]
        # Sequential funnel starts with every bar, then strictly nested pass counts.
        live=True
        for stage,passed in zip(stages,checks):
            if live:
                funnel[stage]['input']+=1
                funnel[stage]['pass' if passed else 'blocked']+=1
                live=bool(passed)
        if cross:
            reasons=[stages[j] for j,x in enumerate(checks) if not x]
            for reason in reasons:
                blocked['ALL:'+reason]+=1
            if reasons:
                blocked['FIRST:'+reasons[0]]+=1
            ml_counts[ms]+=1
            event={'event_id':len(events)+1,'time':datetime.fromtimestamp(ts+300,TZ).isoformat(),
                   'timestamp':ts,'day':day,'scope_allowed':scope_ok,'ready':ready,'ml':ms,'market':market,
                   'market_reference':data['contexts'][day]['index_reference'] if revised else 'PRIOR_COMMON_PEER',
                   'close':b['Close'],'high_threshold':H,'is_add':adding,'all_blockers':reasons,
                   'first_blocker':reasons[0] if reasons else 'ALLOW','approved_quantity':q,
                   'caps':caps,'filled_quantity':0,'new_date':day not in data['common']}
            events.append(event)
            if live:
                pending={'fill':ts+300,'q':q,'liq':b['liq'],'event':len(events)-1}
                funnel['實際買進']['input']+=1
            if revised:
                armed=False
        if pos and (i==len(bars)-1 or bars[i+1]['day']>window[1]):
            exit_position(i,b['Close'],'END_OF_WINDOW',datetime.fromtimestamp(ts+300,TZ).isoformat())
            if pos is None:
                equity[-1]['equity']=cash+sum(x['amount'] for x in receivables if not x['paid']); equity[-1]['cash']=cash; equity[-1]['exposure']=0
    # No-position path is the likely economic result, never fabricate markers/trades.
    curves=pd.DataFrame(equity)
    final=curves.iloc[-1]['equity'] if len(curves) else c['initial_capital']
    pnl=[t['net_pnl'] for t in trades]
    unrealized=0.0
    open_position=None
    if pos:
        last_mark=next(b for b in reversed(bars) if window[0]<=b['day']<=window[1])['Close']
        unrealized=sum(b['q']*(last_mark-b['price'])-b['fee'] for b in pos['batches'])+pos.get('dividends',0)
        open_position={'quantity':sum(b['q'] for b in pos['batches']),'mark_price':last_mark,
                       'unrealized_pnl':unrealized,'deferred_exits':pos.get('deferred_exits',[])}
    if abs(float(final)-c['initial_capital']-sum(pnl)-unrealized)>0.02:
        raise AssertionError('Closed-trade PnL does not reconcile with cash plus dividend receivables')
    maxdd=float((1-curves.equity/curves.equity.cummax().clip(lower=c['initial_capital'])).max()) if len(curves) else 0.0
    daily_eq=curves.groupby('date').equity.last() if len(curves) else pd.Series(dtype=float)
    ret=daily_eq.pct_change().fillna(0)
    sharpe=float(ret.mean()/ret.std(ddof=1)*np.sqrt(252)) if len(ret)>1 and ret.std(ddof=1)>0 else None
    gain=sum(x for x in pnl if x>0); loss=-sum(x for x in pnl if x<0)
    metrics={'variant':variant,'scope':scope,'slippage_each_side':slip,'start':window[0],'end':window[1],
             'initial_capital':c['initial_capital'],'final_equity':float(final),'return':float(final/c['initial_capital']-1),
             'net_pnl':float(final-c['initial_capital']),'closed_trade_net_pnl':float(sum(pnl)),
             'unrealized_pnl':unrealized,'gross_pnl':sum(t['gross_pnl'] for t in trades)+unrealized+(sum(b['fee'] for b in pos['batches']) if pos else 0),
             'trade_count':len(trades),'win_rate':sum(x>0 for x in pnl)/len(pnl) if pnl else None,
             'profit_factor':gain/loss if loss else None,'max_drawdown':maxdd,'sharpe':sharpe,
             'average_win':gain/sum(x>0 for x in pnl) if gain else None,
             'average_loss':-loss/sum(x<0 for x in pnl) if loss else None,
             'fees':sum(o['fee'] for o in orders),'tax':sum(o['tax'] for o in orders),
             'slippage_cost':sum(o['slippage_cost'] for o in orders),
             'avg_exposure':float(curves['exposure'].mean()) if len(curves) else 0,
             'max_exposure':float(curves['exposure'].max()) if len(curves) else 0,
             'average_holding_bars':float(np.mean([t['holding_bars'] for t in trades])) if trades else None,
             'buys':sum(o['action']=='BUY' for o in orders),'adds':sum(o['action']=='ADD' for o in orders),
             'candidate_events':len(events),'new_date_events':sum(e['new_date'] for e in events),
             'holding_extensions':sum(e['action']=='GRANT' for e in holding_events),
             'holding_extension_checks':len(holding_events),
             'status':'INSUFFICIENT' if len(trades)<5 else 'EXPLORATORY_SINGLE_STOCK',
             'cash_dividends':sum(t.get('cash_dividends',0) for t in trades),
             'deferred_exit_episodes':sum(len(t.get('deferred_exits',[])) for t in trades)+(len(pos.get('deferred_exits',[])) if pos else 0),
             'receivables_at_end':sum(x['amount'] for x in receivables if not x['paid']),
             'liquidity_assumption':'ordinary-market historical mean-volume proxy; no odd-lot or order-book fill guarantee',
             'execution_model':c.get('execution_data_mode','BOARD_LOT_RESEARCH')}
    if orders:
        metrics['execution_validity']='REQUIRES_ORDERBOOK_AND_CORPORATE_ACTION_REVIEW'
    else:
        metrics['execution_validity']='NO_ORDERS_NO_FILL_CLAIM'
    return {'metrics':metrics,'events':events,'orders':orders,'trades':trades,'equity':equity,'open_position':open_position,
            'holding_events':holding_events,
            'funnel':[{'stage':k,**v} for k,v in funnel.items()], 'blockers':dict(blocked),'ml_counts':dict(ml_counts)}
