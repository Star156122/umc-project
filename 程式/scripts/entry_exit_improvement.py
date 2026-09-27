"""六策略損失診斷及少量結構改善；只讀既有開發資料。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import bisect
import contextlib
import dataclasses
import hashlib
import io
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from functools import partial

from scripts.multi_stock_batch import build_runtime_config, load_plan
from scripts.routing_simulation import ROOT, simulate
from scripts.risk_ablation import check_period, read_frame, digest, diagnostics, paired_summary
from trading_system import backtest as bt
from trading_system.entry_exit_candidate import daily_entry_flags, build_entry_exit

PLAN=ROOT/'configs/entry_exit_improvement_20260926.json'
OUTPUT=ROOT/'exports/entry_exit_improvement'


def diagnose(frame, result, flags):
    """互斥損益分類；事後價格僅供診斷，不傳回策略。"""
    bars=list(frame.iter_rows(named=True))
    stamps=[r['kbar_timestamp'] for r in bars]
    sell_reasons={s['datetime']:s['signal_type'] for s in result['signals'] if s['action']=='Sell'}
    rows=[]
    fills=result['trades']
    buys={t['datetime']:t for t in fills if t['action']=='Buy'}
    sells={t['datetime']:t for t in fills if t['action']=='Sell'}
    for pnl in bt._build_pnl_rows(fills):
        buy=buys[pnl['buy_datetime']]; sell=sells[pnl['sell_datetime']]
        a=bisect.bisect_left(stamps,buy['timestamp']); b=bisect.bisect_left(stamps,sell['timestamp'])
        previous=bars[a-1] if a else {}
        held=bars[a:b]
        peak=max([buy['price']]+[float(bar['Close']) for bar in held])
        trough=min([buy['price']]+[float(bar['Close']) for bar in held])
        if pnl['net_pnl']>0:
            category='淨獲利'
        elif pnl['net_pnl']==0:
            category='損益兩平'
        elif pnl['gross_pnl']>=0:
            category='成本轉虧'
        else:
            category='價差與成本皆虧'
        close=previous.get('Close'); trend=previous.get('TREND_MA'); vol=previous.get('Volume'); mean_vol=previous.get('VOLUME_MA')
        rows.append(dict(pnl,category=category,exit_reason=sell_reasons.get(sell['datetime'],'unknown'),
                         holding_bars=b-a,peak_close_return=peak/buy['price']-1,
                         worst_close_return=trough/buy['price']-1,
                         gross_giveback_from_peak=(peak-sell['price'])*pnl['quantity'],
                         entry_trend_distance=close/trend-1 if close and trend else None,
                         entry_volume_ratio=vol/mean_vol if vol is not None and mean_vol else None,
                         entry_daily_trend_ok=flags.get(buy['datetime'][:10],False)))
    if abs(sum(r['net_pnl'] for r in rows)-result['metrics']['net_pnl'])>.02:
        raise ValueError('逐筆診斷與帳戶淨損益不一致')
    return rows


def summarize(records, plan):
    output=[]
    for key in plan['strategies']:
        for variant in plan['variants']:
            selected=[r for r in records if r['strategy']==key and r['variant']==variant['id']]
            original=[next(r for r in records if r['code']==a['code'] and r['strategy']==key and r['variant']=='v2') for a in selected]
            summary={'strategy':key,'variant':variant['id'],'label':variant['label'],
                     'median_return':statistics.median(r['metrics']['total_return'] for r in selected),
                     'mean_return':statistics.fmean(r['metrics']['total_return'] for r in selected),
                     'profitable_stocks':sum(r['metrics']['total_return']>0 for r in selected),
                     'mean_cost':statistics.fmean(r['metrics']['transaction_cost'] for r in selected),
                     'mean_drawdown':statistics.fmean(r['metrics']['max_drawdown'] for r in selected),
                     'trades':sum(r['metrics']['completed_trades'] for r in selected),
                     'cost_flip_count':sum(t['category']=='成本轉虧' for r in selected for t in r['trade_diagnostics']),
                     'price_loss_count':sum(t['category']=='價差與成本皆虧' for r in selected for t in r['trade_diagnostics']),
                     'exit_counts':dict(sum((Counter(r['exit_counts']) for r in selected),Counter()))}
            if variant['compare_to']:
                controls=[next(r for r in records if r['code']==a['code'] and r['strategy']==key and r['variant']==variant['compare_to']) for a in selected]
                summary['against_control']=paired_summary(selected,controls,plan['evaluation'])
                summary['against_v2']=paired_summary(selected,original,plan['evaluation'])
                summary['retained']=all(summary[k]['status']=='保留研究候選' for k in ('against_control','against_v2'))
            else:
                summary['retained']=False
            output.append(summary)
    return output


def main():
    plan=guarded_json_loads(PLAN.read_text(encoding='utf-8'))
    check_period(plan)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    registered=OUTPUT/'registered_plan.json'
    if registered.exists():
        if guarded_json_loads(registered.read_text(encoding='utf-8'))!=plan:
            raise ValueError('禁止覆寫已登記計畫')
    else:
        registered.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    run={'started_at':datetime.now().isoformat(),'plan_sha256':digest(plan),'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['scripts/entry_exit_improvement.py','scripts/routing_simulation.py','scripts/risk_ablation.py','trading_system/entry_exit_candidate.py','trading_system/candidate_risk.py','trading_system/backtest.py','configs/strategy_templates_v2_candidate.json','configs/frozen_baseline.json']}}
    with (OUTPUT/'execution_log.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(run,ensure_ascii=False)+'\n')
    print('已登記5版本×6策略×6檔＝180組；不讀取保留期間',flush=True)
    stocks,templates,experiment=load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment.update(stock_codes=plan['stock_codes'],strategy_keys=plan['strategies'],backtest_start=plan['period_start'],backtest_end=plan['period_end'])
    fields={f.name for f in dataclasses.fields(bt.AppConfig)}
    records=[]; hashes={}; benchmarks={}
    with contextlib.closing(sqlite3.connect((ROOT/'data/market_data.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        for code in plan['stock_codes']:
            runtime=build_runtime_config(stocks[code],templates,experiment)
            for key in plan['strategies']:
                settings=dict(runtime,**runtime['profiles'][f'{code}_{key}'])
                settings.update(llm_enabled=False,db_enabled=False,allow_real_trading=False,is_backtest=True,is_simulation=True,only_backtest=True,tick_source='sinopac')
                base=bt.AppConfig(**{k:v for k,v in settings.items() if k in fields})
                frame,stats,hashes[code]=read_frame(conn,base)
                closes={}
                for bar in frame.iter_rows(named=True):
                    closes[bar['kbar_time'].date().isoformat()]=float(bar['Close'])
                flags=daily_entry_flags(list(closes.items()))
                benchmarks[code]=bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame,base),base)
                for variant in plan['variants']:
                    config=dataclasses.replace(base,**plan['risk_overrides']) if variant['risk'] else base
                    factory=partial(build_entry_exit,daily_flags=flags if variant['daily_entry'] else None,
                                    confirmation_bars=plan['normal_exit_confirmation_bars'] if variant['confirm_exit'] else 1) if variant['risk'] else None
                    with contextlib.redirect_stdout(io.StringIO()):
                        result=simulate({'ma':config,key:config},{'ma':frame,key:frame},stats,key,factory=factory)
                    diag=diagnostics(frame,stats,result['trades'],base.initial_capital,plan['evaluation']['additional_one_way_cost_bps'])
                    trades=diagnose(frame,result,flags)
                    if variant['daily_entry']:
                        if any(not t['entry_daily_trend_ok'] for t in trades):
                            raise ValueError('日線進場限制未生效')
                    records.append({'code':code,'name':stocks[code]['name'],'strategy':key,'variant':variant['id'],
                                    'metrics':result['metrics'],'diagnostics':diag,'trade_diagnostics':trades,
                                    'trades':result['trades'],'signals':result['signals'],
                                    'exit_counts':dict(Counter(s['signal_type'] for s in result['signals'] if s['action']=='Sell'))})
            print(f'{code} 30組完成：日線限制、逐筆損益、成本、成交核對通過',flush=True)
    payload={'plan':plan,'run':dict(run,completed_at=datetime.now().isoformat(),market_sha256=hashes),
             'records':records,'summary':summarize(records,plan),'benchmarks':benchmarks}
    (OUTPUT/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    from scripts.entry_exit_report import write_report
    write_report(payload)
    print('完成；exports/entry_exit_improvement_latest.html',flush=True)


if __name__=='__main__':
    main()
