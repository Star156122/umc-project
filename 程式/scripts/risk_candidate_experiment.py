"""固定六檔開發資料：突破原版、風控版，及各自加行情篩選。"""
import contextlib
import dataclasses
import io
import json
from scripts.routing_simulation import ROOT, simulate
from scripts.multi_stock_batch import load_plan, build_runtime_config
from trading_system import backtest as bt
from trading_system.candidate_risk import build_candidate


def main():
    stocks, templates, experiment = load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment['strategy_keys'] = ['breakout']
    rule = dict(min_hold_bars=4, stop_loss_pct=.02, cooldown_bars=12,
                max_entries_per_day=1, take_profit_pct=0)
    snapshot = {'experiment': experiment, 'templates': templates, 'overrides': rule,
                'stop_cooldown': 24, 'trailing_activation': .03, 'trailing_drawdown': .02,
                'peak': '持倉期間已完成K棒收盤最高價', 'trend': '沿用突破TREND_MA120',
                'cooldown': '完整K棒計數，跨日保留，非夜間經過分鐘',
                'execution': '收盤判斷、下一棒開盤成交；期末收盤清倉',
                'holdout': '2025/07/01～12/31保留，未使用', 'slippage': 0}
    (ROOT/'exports/risk_candidate_config.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    output=[]
    fields={f.name for f in dataclasses.fields(bt.AppConfig)}
    for code in experiment['stock_codes']:
        runtime=build_runtime_config(stocks[code], templates, experiment)
        settings=dict(runtime, **runtime['profiles'][f'{code}_breakout'])
        settings.update(llm_enabled=False, db_enabled=False, allow_real_trading=False, is_backtest=True, tick_source='sinopac')
        base=bt.AppConfig(**{k:v for k,v in settings.items() if k in fields})
        candidate=dataclasses.replace(base, **rule)
        with contextlib.redirect_stdout(io.StringIO()):
            frame, stats=bt.load_cached_kbar_frame(base)
            frames={'ma':frame,'breakout':frame}
            groups={}
            for name, config, filtered, factory in [('原版',base,False,None),('原版加行情',base,True,None),('新風控',candidate,False,build_candidate),('新風控加行情',candidate,True,build_candidate)]:
                groups[name]=simulate({'ma':config,'breakout':config},frames,stats,'breakout',filtered,factory)
            benchmark=bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame,base),base)
        output.append({'code':code,'name':stocks[code]['name'],'groups':groups,'buy_hold':benchmark,'cash_return':0})
        print(code,{k:round(v['metrics']['total_return']*100,2) for k,v in groups.items()},flush=True)
    (ROOT/'exports/risk_candidate_latest.json').write_text(json.dumps({'config':snapshot,'results':output},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    rows=[]
    lines=['# 新風控規則開發實驗', '', '2026/01/01～06/30，六檔區間突破。一次測整套風控，不能判定每項規則各自貢獻。無滑價、非獨立驗證。保留期間未使用。', '', '移動停利以最高已完成收盤价回跌2%（不是獲利減2個百分點）；達到3%後持續啟用。停損、跌破MA120、移動停利不受最短持有限制。正常訊號至少4根，正常冷卻12根、停損24根，跨日保留。', '', '|股票|組別|報酬|成本|交易|回撤|', '|---|---|---:|---:|---:|---:|']
    for stock in output:
        for name,group in stock['groups'].items():
            m=group['metrics']; cells=[stock['code']+' '+stock['name'],name,f"{m['total_return']:.2%}",f"{m['transaction_cost']:.0f}",str(m['completed_trades']),f"{m['max_drawdown']:.2%}"]
            rows.append('<tr>'+''.join('<td>'+v+'</td>' for v in cells)+'</tr>'); lines.append('|'+ '|'.join(cells)+'|')
        rows.append(f"<tr><td>{stock['code']}</td><td>買進持有／現金</td><td>{stock['buy_hold']['total_return']:.2%}／0%</td><td colspan='3'>買進持有含稅費；現金不計息</td></tr>")
    (ROOT/'docs/新風控實驗_20260926.md').write_text('\n'.join(lines),encoding='utf-8')
    (ROOT/'exports/risk_candidate_latest.html').write_text('<meta charset="utf-8"><title>新風控對照</title><style>body{font-family:Microsoft JhengHei;margin:30px}td,th{border:1px solid #ccc;padding:10px}table{border-collapse:collapse}</style><h1>新風控：區間突破六檔開發實驗</h1><p>'+lines[2]+'</p><p>'+lines[4]+'</p><table><tr><th>股票</th><th>組別</th><th>報酬</th><th>成本</th><th>交易</th><th>回撤</th></tr>'+''.join(rows)+'</table>',encoding='utf-8')


if __name__=='__main__':
    main()
