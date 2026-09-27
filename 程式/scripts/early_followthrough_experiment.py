"""MACD與多數決的單一早期價格延續拆項；固定K1收盤突破K0高點。"""
from __future__ import annotations
import contextlib,dataclasses,hashlib,io,json,sqlite3,statistics
from collections import Counter
from datetime import datetime
from functools import partial

from scripts.multi_stock_batch import build_runtime_config,load_plan
from scripts.routing_simulation import ROOT,simulate
from scripts.risk_ablation import check_period,read_frame,digest,diagnostics,paired_summary
from scripts.entry_exit_improvement import diagnose
from scripts.six_strategy_logic_experiment import aggregate
from scripts.trade_failure_diagnosis import diagnose_record
from trading_system import backtest as bt
from trading_system.entry_exit_candidate import daily_entry_flags
from trading_system.strategy_logic_candidate import build_strategy_logic
from trading_system.early_followthrough_candidate import build_early_followthrough

PLAN=ROOT/'configs/early_followthrough_20260926.json'
RULES=ROOT/'configs/six_strategy_logic_20260926.json'
DIAG=ROOT/'configs/trade_failure_diagnosis_20260926.json'
OUT=ROOT/'exports/early_followthrough'


def factory_with_holder(holder,builder,**kwargs):
    def factory(config):
        bot=builder(config,**kwargs);holder['bot']=bot;return bot
    return factory


def summarize(records,plan):
    output=[]
    for strategy in plan['strategies']:
        base=[r for r in records if r['strategy']==strategy and r['variant']=='baseline']
        cand=[r for r in records if r['strategy']==strategy and r['variant']=='followthrough']
        pair=paired_summary(cand,base,plan['evaluation'])
        deltas=[a['metrics']['total_return']-b['metrics']['total_return'] for a,b in zip(cand,base)]
        without=[d for d,r in zip(deltas,cand) if r['code']!='2303']
        def extra(rows):
            all_diag=[d for r in rows for d in r['classified_trades']]
            return {'successful_trades':sum(d['交易品質']=='成功交易' for d in all_diag),
                    'low_mfe_trades':sum('進場後幾乎沒有有效浮盈' in d['失敗分類'] for d in all_diag),
                    'immediate_reversal_trades':sum('進場後短時間內立刻反向' in d['失敗分類'] for d in all_diag)}
        output.append({'strategy':strategy,'baseline':dict(aggregate(base),**extra(base)),
                       'followthrough':dict(aggregate(cand),**extra(cand)),'paired':pair,
                       'return_deltas':dict(zip([r['code'] for r in cand],deltas)),
                       'median_delta_without_2303':statistics.median(without),
                       'confirmation':dict(sum((Counter(r.get('confirmation',{})) for r in cand),Counter())),
                       'retained':pair['status']=='保留研究候選'})
    return output


def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'));check_period(plan)
    rules=json.loads(RULES.read_text(encoding='utf-8'))['candidate_rules']
    diag_defs=json.loads(DIAG.read_text(encoding='utf-8'))['definitions']
    OUT.mkdir(parents=True,exist_ok=True)
    frozen=OUT/'registered_plan.json'
    if frozen.exists() and json.loads(frozen.read_text(encoding='utf-8'))!=plan:raise ValueError('登記規則不可依結果覆寫')
    if not frozen.exists():frozen.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    sources=['scripts/early_followthrough_experiment.py','trading_system/early_followthrough_candidate.py',
             'trading_system/strategy_logic_candidate.py','scripts/routing_simulation.py','trading_system/backtest.py']
    run={'started_at':datetime.now().isoformat(),'plan_sha256':digest(plan),
         'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    with (OUT/'execution_log.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(run,ensure_ascii=False)+'\n')
    stocks,templates,experiment=load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment.update(stock_codes=plan['stock_codes'],strategy_keys=plan['strategies'],backtest_start=plan['period_start'],backtest_end=plan['period_end'])
    fields={f.name for f in dataclasses.fields(bt.AppConfig)};records=[];hashes={};benchmarks={}
    with contextlib.closing(sqlite3.connect((ROOT/'data/market_data.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        for code in plan['stock_codes']:
            runtime=build_runtime_config(stocks[code],templates,experiment)
            for strategy in plan['strategies']:
                settings=dict(runtime,**runtime['profiles'][f'{code}_{strategy}'])
                settings.update(llm_enabled=False,db_enabled=False,allow_real_trading=False,is_backtest=True,is_simulation=True,only_backtest=True,tick_source='sinopac')
                config=bt.AppConfig(**{k:v for k,v in settings.items() if k in fields})
                frame,stats,hashes[code]=read_frame(conn,config)
                bars=[{'kbar_timestamp':int(r['kbar_timestamp']),'open':float(r['Open']),'high':float(r['High']),
                       'low':float(r['Low']),'close':float(r['Close']),'volume':float(r['Volume'])} for r in frame.iter_rows(named=True)]
                closes={}
                for row in frame.iter_rows(named=True):closes[row['kbar_time'].date().isoformat()]=float(row['Close'])
                flags=daily_entry_flags(list(closes.items()))
                benchmarks[code]=bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame,config),config)
                for variant in ('baseline','followthrough'):
                    holder={}
                    builder=build_strategy_logic if variant=='baseline' else build_early_followthrough
                    factory=factory_with_holder(holder,builder,rules=rules,daily_flags=flags)
                    with contextlib.redirect_stdout(io.StringIO()):
                        result=simulate({'ma':config,strategy:config},{'ma':frame,strategy:frame},stats,strategy,factory=factory)
                    trade_diag=diagnose(frame,result,flags)
                    record={'code':code,'name':stocks[code]['name'],'strategy':strategy,'variant':variant,
                            'metrics':result['metrics'],'diagnostics':diagnostics(frame,stats,result['trades'],config.initial_capital,plan['evaluation']['additional_one_way_cost_bps']),
                            'trades':result['trades'],'signals':result['signals'],'trade_diagnostics':trade_diag,
                            'exit_counts':dict(Counter(s['signal_type'] for s in result['signals'] if s['action']=='Sell'))}
                    record['classified_trades']=diagnose_record(record,bars,diag_defs)
                    if variant=='followthrough':
                        record['confirmation']=holder['bot'].research_diagnostics()
                        entries=[s for s in result['signals'] if s['action']=='Buy']
                        if any('本次於K2開盤成交' not in s['reason'] for s in entries):raise ValueError('發現未經K1確認的進場')
                        if len(entries)!=record['confirmation']['confirmed_signals']:raise ValueError('確認數與買進訊號數不一致')
                    records.append(record)
            print(f'{code} 完成4組；K0/K1/K2順序、下一棒開盤成交與損益核對通過',flush=True)
    payload={'plan':plan,'run':dict(run,completed_at=datetime.now().isoformat(),market_sha256=hashes),
             'records':records,'summary':summarize(records,plan),'benchmarks':benchmarks,'holdout_accessed':False}
    (OUT/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    from scripts.early_followthrough_report import write_report
    write_report(payload)
    print('完成：exports/early_followthrough_latest.html')


if __name__=='__main__':main()
