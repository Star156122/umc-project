"""同暖機對照，排除前25日不交易對改善的影響。"""
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import contextlib
import dataclasses
import hashlib
import io
import json
import sqlite3
from datetime import datetime
from functools import partial
from scripts.routing_simulation import ROOT, simulate
from scripts.risk_ablation import check_period, read_frame, diagnostics, paired_summary, digest
from scripts.multi_stock_batch import load_plan, build_runtime_config
from scripts.entry_exit_improvement import diagnose
from trading_system import backtest as bt
from trading_system.entry_exit_candidate import daily_entry_flags, build_entry_exit


def main():
    plan=guarded_json_loads((ROOT/'configs/entry_exit_warmup_audit_20260926.json').read_text(encoding='utf-8'))
    check_period(plan)
    output=ROOT/'exports/entry_exit_improvement'
    frozen=output/'warmup_registered_plan.json'
    if frozen.exists():
        if guarded_json_loads(frozen.read_text(encoding='utf-8'))!=plan:
            raise ValueError('禁止修改已登記補充計畫')
    else:
        frozen.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    prior=guarded_json_loads((output/'results.json').read_text(encoding='utf-8'))
    run={'started_at':datetime.now().isoformat(),'plan_sha256':digest(plan),'source_results_sha256':hashlib.sha256((output/'results.json').read_bytes()).hexdigest(),
         'code_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['scripts/entry_exit_warmup_audit.py','trading_system/entry_exit_candidate.py','trading_system/candidate_risk.py','scripts/routing_simulation.py']}}
    with (output/'warmup_execution_log.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(run,ensure_ascii=False)+'\n')
    stocks,templates,experiment=load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment.update(stock_codes=plan['stock_codes'],strategy_keys=plan['strategies'],backtest_start=plan['period_start'],backtest_end=plan['period_end'])
    fields={f.name for f in dataclasses.fields(bt.AppConfig)}
    records=[]
    with contextlib.closing(sqlite3.connect((ROOT/'data/market_data.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        for code in plan['stock_codes']:
            runtime=build_runtime_config(stocks[code],templates,experiment)
            for key in plan['strategies']:
                settings=dict(runtime,**runtime['profiles'][f'{code}_{key}'])
                settings.update(llm_enabled=False,db_enabled=False,allow_real_trading=False,is_backtest=True,is_simulation=True,only_backtest=True,tick_source='sinopac')
                base=bt.AppConfig(**{k:v for k,v in settings.items() if k in fields})
                config=dataclasses.replace(base,**prior['plan']['risk_overrides'])
                frame,stats,market_hash=read_frame(conn,base)
                if market_hash!=prior['run']['market_sha256'][code]:
                    raise ValueError('行情來源與原實驗不同')
                closes={}
                for bar in frame.iter_rows(named=True):
                    closes[bar['kbar_time'].date().isoformat()]=float(bar['Close'])
                warm_flags={day:i>=plan['warmup_complete_days'] for i,day in enumerate(closes)}
                trend_flags=daily_entry_flags(list(closes.items()))
                for comparison in plan['comparisons']:
                    factory=partial(build_entry_exit,daily_flags=warm_flags,confirmation_bars=comparison['confirmation_bars'])
                    with contextlib.redirect_stdout(io.StringIO()):
                        result=simulate({'ma':config,key:config},{'ma':frame,key:frame},stats,key,factory=factory)
                    if any(not warm_flags.get(t['datetime'][:10],False) for t in result['trades'] if t['action']=='Buy'):
                        raise ValueError('暖機限制失效')
                    records.append({'code':code,'strategy':key,'variant':comparison['control'],'metrics':result['metrics'],
                                    'diagnostics':diagnostics(frame,stats,result['trades'],base.initial_capital,5),
                                    'trades':result['trades'],'trade_diagnostics':diagnose(frame,result,trend_flags)})
            print(f'{code} 相同暖機12組完成',flush=True)
    audits=[]
    for key in plan['strategies']:
        for comparison in plan['comparisons']:
            selected=[next(r for r in prior['records'] if r['code']==c and r['strategy']==key and r['variant']==comparison['selected']) for c in plan['stock_codes']]
            controls=[next(r for r in records if r['code']==c and r['strategy']==key and r['variant']==comparison['control']) for c in plan['stock_codes']]
            evaluation=paired_summary(selected,controls,plan['evaluation'])
            original=next(s for s in prior['summary'] if s['strategy']==key and s['variant']==comparison['selected'])
            audits.append(dict(evaluation,strategy=key,variant=comparison['selected'],control=comparison['control'],
                               initially_retained=original['retained'],retained_after_audit=original['retained'] and evaluation['status']=='保留研究候選'))
    result={'plan':plan,'run':dict(run,completed_at=datetime.now().isoformat()),'records':records,'audits':audits}
    (output/'warmup_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(audits,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
