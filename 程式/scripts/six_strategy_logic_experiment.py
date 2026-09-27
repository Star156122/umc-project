"""六策略各一組問題導向進場改善；只讀2026上半年開發快取。"""
from __future__ import annotations
import contextlib, dataclasses, hashlib, io, json, sqlite3, statistics
from collections import Counter
from datetime import datetime
from functools import partial

from scripts.multi_stock_batch import build_runtime_config, load_plan
from scripts.routing_simulation import ROOT, simulate
from scripts.risk_ablation import check_period, read_frame, digest, diagnostics, paired_summary
from scripts.entry_exit_improvement import diagnose
from trading_system import backtest as bt
from trading_system.entry_exit_candidate import daily_entry_flags
from trading_system.strategy_logic_candidate import build_strategy_logic

PLAN=ROOT/'configs/six_strategy_logic_20260926.json'
OUTPUT=ROOT/'exports/six_strategy_logic'


class WarmupOnly(bt.MovingAverageTsst):
    daily_flags=None
    def _can_enter(self,time):
        return bool(self.daily_flags and self.daily_flags.get(time.date().isoformat(),False)) and super()._can_enter(time)


def build_warmup(config,daily_flags):
    bot=WarmupOnly(config=config,use_broker='Sino',is_simulation=True,is_backtest=True,local_only=True)
    bot.configure_local_account(config);bot.quote_obj=bt.BacktestQuote();bot.local_order_mode=True
    bot.daily_flags=daily_flags
    return bot


def aggregate(rows):
    keys=('gross_pnl','net_pnl','transaction_cost','completed_trades','wins','losses')
    total={key:sum(float(r['metrics'][key]) for r in rows) for key in keys}
    pnl=[p for r in rows for p in bt._build_pnl_rows(r['trades'])]
    wins=[p['net_pnl'] for p in pnl if p['net_pnl']>0];losses=[p['net_pnl'] for p in pnl if p['net_pnl']<0]
    total.update(mean_return=statistics.fmean(r['metrics']['total_return'] for r in rows),
                 median_return=statistics.median(r['metrics']['total_return'] for r in rows),
                 profitable_stocks=sum(r['metrics']['total_return']>0 for r in rows),
                 win_rate=total['wins']/total['completed_trades'] if total['completed_trades'] else 0,
                 average_win=statistics.fmean(wins) if wins else 0,
                 average_loss=abs(statistics.fmean(losses)) if losses else 0,
                 profit_factor=sum(wins)/abs(sum(losses)) if losses else 0,
                 mean_drawdown=statistics.fmean(r['metrics']['max_drawdown'] for r in rows),
                 mean_sharpe=statistics.fmean(r['metrics']['sharpe_ratio'] for r in rows))
    return total


def summarize(records,plan):
    output=[]
    for strategy in plan['strategies']:
        base=[r for r in records if r['strategy']==strategy and r['variant']=='v2']
        candidate=[r for r in records if r['strategy']==strategy and r['variant']=='candidate']
        comparison=paired_summary(candidate,base,plan['evaluation'])
        control=None
        if strategy in ('macd','vote'):
            warm=[r for r in records if r['strategy']==strategy and r['variant']=='warmup_control']
            control=paired_summary(candidate,warm,plan['evaluation'])
            retained=comparison['status']=='保留研究候選' and control['status']=='保留研究候選'
        else:
            retained=comparison['status']=='保留研究候選'
        deltas=[a['metrics']['total_return']-b['metrics']['total_return'] for a,b in zip(candidate,base)]
        without_2303=[d for d,r in zip(deltas,candidate) if r['code']!='2303']
        output.append({'strategy':strategy,'rule':plan['candidate_rules'][strategy],
                       'baseline':aggregate(base),'candidate':aggregate(candidate),
                       'against_v2':comparison,'against_warmup':control,'retained':retained,
                       'return_deltas':dict(zip([r['code'] for r in candidate],deltas)),
                       'median_delta_without_2303':statistics.median(without_2303)})
    return output


def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'));check_period(plan)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    registered=OUTPUT/'registered_plan.json'
    if registered.exists() and json.loads(registered.read_text(encoding='utf-8'))!=plan:
        raise ValueError('已登記計畫不可依結果覆寫')
    if not registered.exists():registered.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    sources=['scripts/six_strategy_logic_experiment.py','scripts/routing_simulation.py','scripts/risk_ablation.py',
             'trading_system/strategy_logic_candidate.py','trading_system/backtest.py','configs/strategy_templates_v2_candidate.json']
    run={'started_at':datetime.now().isoformat(),'plan_sha256':digest(plan),
         'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    with (OUTPUT/'execution_log.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(run,ensure_ascii=False)+'\n')
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
                closes={}
                for bar in frame.iter_rows(named=True):closes[bar['kbar_time'].date().isoformat()]=float(bar['Close'])
                flags=daily_entry_flags(list(closes.items()))
                warmup={day:i>=25 for i,day in enumerate(closes)}
                benchmarks[code]=bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame,config),config)
                variants=[('v2',None),('candidate',partial(build_strategy_logic,rules=plan['candidate_rules'],daily_flags=flags))]
                if strategy in ('macd','vote'):variants.append(('warmup_control',partial(build_warmup,daily_flags=warmup)))
                for variant,factory in variants:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result=simulate({'ma':config,strategy:config},{'ma':frame,strategy:frame},stats,strategy,factory=factory)
                    diag=diagnostics(frame,stats,result['trades'],config.initial_capital,plan['evaluation']['additional_one_way_cost_bps'])
                    trades=diagnose(frame,result,flags)
                    records.append({'code':code,'name':stocks[code]['name'],'strategy':strategy,'variant':variant,
                                    'metrics':result['metrics'],'diagnostics':diag,'trades':result['trades'],
                                    'trade_diagnostics':trades,'signals':result['signals'],
                                    'exit_counts':dict(Counter(s['signal_type'] for s in result['signals'] if s['action']=='Sell'))})
            print(f'{code} 完成14組並核對損益、成本、下一棒成交',flush=True)
    payload={'plan':plan,'run':dict(run,completed_at=datetime.now().isoformat(),market_sha256=hashes),
             'records':records,'summary':summarize(records,plan),'benchmarks':benchmarks}
    (OUTPUT/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    from scripts.six_strategy_logic_report import write_report
    write_report(payload)
    print('完成：exports/six_strategy_logic_latest.html',flush=True)


if __name__=='__main__':main()
