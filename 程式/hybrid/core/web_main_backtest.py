"""Website runner for the explicitly selected cash-only common-coverage strategy."""
import copy,csv,json,hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from trading_system.research_guard import assert_development_period
from hybrid.core import main_buy_strategy_v1 as engine

ROOT=Path(__file__).resolve().parents[2]
PROFILE='main-buy-noadd-noextension-common-v1-20261114'
STRATEGIES={'technical':'TECH','rf':'RF','xgboost':'XGB','gru':'GRU'}
MODELS={'technical':'random_forest','rf':'random_forest','xgboost':'xgboost','gru':'gru'}

def request_config(code,start,end):
    assert_development_period(start,end)
    c=json.loads((ROOT/'hybrid/config/web_main_strategy_v1.json').read_text(encoding='utf8'))
    c.update(stock=str(code),period={'start':start,'end':end})
    spec=c['datasets'].get(str(code))
    if not spec:raise ValueError('股票尚未具備完整行情與三模型預測，不能套用其他股票或模型代替')
    c['warmup_start']='2024-05-01' if spec['prediction_kind']=='oof_csv' else '2024-11-01'
    c['folds']=[{'start':spec['start'],'end':spec['end']}]
    engine.validate(c)
    return c

def summarize(r):
    m=r['metrics']
    return {'initial_capital':m['initial_capital'],'final_equity':m['final_equity'],
      'total_return_pct':m['return']*100,'net_pnl':m['net_pnl'],'trade_count':m['trade_count'],
      'win_rate_pct':None if m['win_rate'] is None else m['win_rate']*100,'max_drawdown_pct':m['max_drawdown']*100}

def normalize(r,strategy,c):
    m=r['metrics'];orders=[]
    for o in r['orders']:
        row={**o,'datetime':o['time'],'action':'Buy' if o['action']=='BUY' else 'Sell',
          'net_cash_flow':-(o['quantity']*o['price']+o['fee']) if o['action']=='BUY' else o['quantity']*o['price']-o['fee']-o['tax']}
        if o['reason'] in ['END_OF_WINDOW','BUY_HOLD_END_CLOSE']:row['chart_timestamp']=o['timestamp']-1
        orders.append(row)
    ml=next((f for f in r['funnel'] if f['stage']=='ML Gate'),{'input':0,'pass':0})
    stages=[f['stage'] for f in r['funnel']]
    after_gate=set(stages[stages.index('ML Gate'):]+['ALLOW']) if 'ML Gate' in stages else set()
    gate_events=[e for e in r['events'] if e['first_blocker'] in after_gate]
    gate_blocks={}
    for e in gate_events:
        if e['first_blocker']=='ML Gate':gate_blocks[e['ml']]=gate_blocks.get(e['ml'],0)+1
    details={'profit_factor':m['profit_factor'],'sharpe_ratio':m['sharpe'],'average_win':m['average_win'],'average_loss':m['average_loss'],
      'payoff_ratio':m['average_win']/abs(m['average_loss']) if m['average_win'] and m['average_loss'] else None,
      'commission_total':m['fees'],'transaction_tax_total':m['tax'],'total_transaction_cost':m['fees']+m['tax'],
      'slippage_cost':m['slippage_cost'],'average_exposure_pct':m['avg_exposure']*100,'max_exposure_pct':m['max_exposure']*100,
      'gate_applicable':strategy not in ['technical','buy_and_hold'],'gate_evaluated_candidate_count':ml['input'],
      'gate_entry_count':ml['pass'],'gate_not_evaluated_candidate_count':len(r['events'])-len(gate_events),'gate_block_counts':gate_blocks,
      'gate_decisions':gate_events,'filter_funnel':r['funnel'],'trades':orders,'closed_trades':r['trades'],
      'risk_contract':{'risk_budget_enabled':False,'sizing_reference':'每次買入含費最多使用可用現金40%，保留初始20%現金','initial_cash_reserve':50000,'stop_loss_pct':.025,'adds_enabled':False,'holding_extension_enabled':False,'max_hold_bars':162,'coverage':'共同完整日＋三模型合法預測'},
      'evidence':'買進持有基準' if strategy=='buy_and_hold' else ('證據不足' if m['trade_count']<5 else '歷史回測，未保證泛化'),
      'model_training':{'version':'ML_V1_FIXED_1PCT','source':c['datasets'][c['stock']]['prediction_kind'],'training_cutoff_policy':'2024年使用時間順序OOF；2025年使用2023～2024固定訓練模型，不以回測期間重訓'},
      'exposure_assumption':'期初最多80%資金，受流動性限制，期末賣出並計賣出費稅' if strategy=='buy_and_hold' else None}
    summary=summarize(r)
    if strategy=='buy_and_hold':summary['win_rate_pct']=None
    return {'strategy_id':strategy,'strategy_version':PROFILE,'summary':summary,'details':details,'trades':orders,
      'equity_curve':[{'timestamp':datetime.fromtimestamp(e['timestamp'],ZoneInfo('Asia/Taipei')).isoformat(),'equity':e['equity']} for e in r['equity']],
      'audit':{'profile_id':PROFILE,'adds':m['adds'],'holding_extensions':m['holding_extensions']}}

def run(code,start,end,strategies):
    c=request_config(code,start,end)
    if not strategies or len(set(strategies))!=len(strategies) or any(s not in STRATEGIES for s in strategies):raise ValueError('Invalid selected strategies')
    data=engine.load_inputs(c)
    # Three model lookups are always loaded even when only pure technical is selected.
    if set(data['ml_by_model'])!={'random_forest','xgboost','gru'}:raise ValueError('Required three-model coverage is missing')
    results=[];raw={}
    for strategy in strategies:
        d=dict(data);d['ml']=data['ml_by_model'][MODELS[strategy]]
        r=engine.simulate(c,d,STRATEGIES[strategy],.0005,'common',(start,end))
        if r['metrics']['adds'] or r['metrics']['holding_extensions']:raise AssertionError('Main strategy cannot add/extend')
        raw[strategy]=r;results.append(normalize(r,strategy,c))
    from hybrid.core.main_buy_benchmark import buyhold
    bh=buyhold(c,data,.0005);benchmark=normalize(bh,'buy_and_hold',c)
    spec=c['datasets'][c['stock']];sources=[ROOT/spec[k] for k in ['database','index','predictions']]
    audit={'profile_id':PROFILE,'data_role':'historical_development','dataset_role':'training_exposed' if end<'2025-01-01' else 'validation_seen',
      'stock_code':str(code),'periods':{'backtest':{'start':start,'end':end}},'initial_capital':250000,
      'rules':{'adds_enabled':False,'holding_extension_enabled':False,'risk_budget_enabled':False,'cash_fraction':.4,'initial_cash_reserve_fraction':.2,'stop_loss_pct':.025,'coverage':'COMMON_COMPLETE_AND_THREE_VALID_MODELS','slippage_each_side':.0005},
      'coverage_audit':data['audit'],'final_oos_used':False,'holdout_policy_modified':False,
      'source_files':[str(p.relative_to(ROOT)) for p in sources],
      'known_limitations':['普通K線近似零股數量成交，未驗證真實零股價差與部分成交','共同日期只限制新進場，既有部位持續處理出場','買進持有最多80%投入，曝險與主動策略不同','不足5筆證據不足，不宣稱新策略已驗證獲利']}
    bars=[b for b in data['bars'] if start<=b['day']<=end]
    output=ROOT/'exports'/('web_main_'+datetime.now().strftime('%Y%m%dT%H%M%S%f'));output.mkdir(parents=True)
    payload={'results':results,'buy_and_hold':benchmark,'comparison':{'time_axis_identical':True,'profile_id':PROFILE},'audit':audit,
      'market_chart':{'source':'新版主要策略實際回測5分K','interval_minutes':5,'period':{'start':start,'end':end},'candles':[{'time':b['kbar_timestamp'],'open':b['Open'],'high':b['High'],'low':b['Low'],'close':b['Close']} for b in bars]}}
    (output/'run.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False,default=lambda x:x.item()),encoding='utf8')
    for strategy,r in raw.items():
        (output/(strategy+'_ledger.json')).write_text(json.dumps(r,ensure_ascii=False,default=lambda x:x.item()),encoding='utf8')
    return output
