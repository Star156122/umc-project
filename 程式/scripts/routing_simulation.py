"""單一帳戶行情切換模擬；僅讀既有快取，不連券商。"""
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import contextlib
import dataclasses
import html
import io
import json
import statistics
from pathlib import Path
from trading_system import backtest as bt
from trading_system.research import classify_days
from scripts.multi_stock_batch import build_runtime_config, load_plan

ROOT = Path(__file__).resolve().parents[1]
RULE = {'上漲': 'breakout', '盤整': 'bollinger', '下跌': None, '資料不足': None}


def simulate(configs, frames, stats, mode, entry_filter=False, factory=None):
    [assert_config(config) for config in configs.values()]
    from trading_system.research_guard import assert_timestamps
    for frame in frames.values():
        assert_timestamps(frame['kbar_timestamp'].to_list())
    base = configs['ma']
    bot = (factory or bt.build_tsst)(base)
    rows = {k: list(f.iter_rows(named=True)) for k, f in frames.items()}
    raw = rows['ma']
    stamps = [r['kbar_timestamp'] for r in raw]
    assert all([r['kbar_timestamp'] for r in rr] == stamps for rr in rows.values())
    closes = {}
    for r in raw:
        closes[r['kbar_time'].date().isoformat()] = float(r['Close'])
    labels = classify_days(list(closes.items()), 20, .05)
    if entry_filter:
        original_can_enter = bot._can_enter
        bot._can_enter = lambda time: (labels.get(time.date().isoformat()) == '上漲' and original_can_enter(time))
    active = None
    changes = []
    for i in range(1, len(raw)):
        current = raw[i]
        day = current['kbar_time'].date().isoformat()
        target = RULE[labels[day]] if mode == 'routing' else mode
        bot.last_tick = {'timestamp': float(current['kbar_timestamp']), 'market_type': 'Stock', 'code': base.code, 'close': float(current['Open']), 'qty': 0}
        changed = target != active
        closed = False
        if changed:
            changes.append({'day': day, 'timestamp': current['kbar_timestamp'], 'regime': labels[day], 'from': active, 'to': target, 'position': bot.local_position})
            if bot.local_position:
                bot.is_entry = False
                bot._sell_market(base.code, float(current['Open']))
                closed = True
            active = target
            if active:
                bot.config = configs[active]
                bot.previous_snapshot = bt.indicator_snapshot(rows[active][i-2]) if i >= 2 else None
        if active and not closed:
            bot.process_completed_kbar(base.code, current['kbar_time'], rows[active][i-1])
    last = raw[-1]
    bot.last_tick = {'timestamp': stats.last_timestamp, 'market_type': 'Stock', 'code': base.code, 'close': float(last['Close']), 'qty': 0}
    bot.finalize_backtest(base.code, bt.taipei_datetime_from_timestamp(stats.last_timestamp), float(last['Close']), bt.indicator_snapshot(last))
    trades = bt._build_trade_rows(bot, base)
    bars = bt.build_kbar_rows_from_frame(frames['ma'], base)
    metrics = bt.calculate_performance_metrics(bt._build_pnl_rows(trades), base.initial_capital, trading_days=list(closes))
    metrics.update(bt.calculate_mark_to_market_risk(trades, bars, base.initial_capital))
    assert bot.local_position == 0
    assert abs(bot.local_cash - (base.initial_capital + metrics['net_pnl'])) < .02
    prices = {int(r['kbar_timestamp']): float(r['Open']) for r in raw}
    for trade in trades:
        expected = float(last['Close']) if int(trade['timestamp']) == int(stats.last_timestamp) else prices[int(trade['timestamp'])]
        assert abs(trade['price'] - expected) < .00001, '成交價未符合下一棒開盤／期末清倉規則'
    assert abs(sum(t['fee'] + t['tax'] for t in trades) - metrics['transaction_cost']) < .02
    return {'mode': mode, 'metrics': metrics, 'trades': trades, 'changes': changes,
            'signals': bt._build_signal_rows(bot)}


def main():
    stocks, templates, experiment = load_plan(ROOT/'configs/strategy_templates_v2_candidate.json', ROOT/'configs/stock_universe_v2.json', ROOT/'configs/multi_stock_experiment_v2.json')
    output = ROOT/'exports'
    snapshot = {'rule': RULE, 'lookback': 20, 'threshold': .05, 'experiment': experiment, 'templates': templates, 'exit_policy': '行情切換時開盤平倉，該棒不重新進場；期末收盤清倉', 'limitation': '已使用歷史期間的研究模擬；無滑價，非獨立樣本外驗證。前21日行情資料不足不交易。'}
    (output/'routing_simulation_config.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    results = []
    fields = {f.name for f in dataclasses.fields(bt.AppConfig)}
    for code in experiment['stock_codes']:
        runtime = build_runtime_config(stocks[code], templates, experiment)
        configs, frames = {}, {}
        with contextlib.redirect_stdout(io.StringIO()):
            for key in experiment['strategy_keys']:
                settings = dict(runtime, **runtime['profiles'][f'{code}_{key}'])
                settings.update(llm_enabled=False, db_enabled=False, allow_real_trading=False, tick_source='sinopac', is_backtest=True, is_simulation=True)
                configs[key] = bt.AppConfig(**{k: v for k, v in settings.items() if k in fields})
                frames[key], stats = bt.load_cached_kbar_frame(configs[key])
            scenarios = [simulate(configs, frames, stats, mode) for mode in ['ma', 'bollinger', 'breakout', 'routing']]
            benchmark = bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frames['ma'], configs['ma']), configs['ma'])
        results.append({'code': code, 'name': stocks[code]['name'], 'scenarios': scenarios, 'benchmark': benchmark})
        print(code, [(s['mode'], round(s['metrics']['total_return']*100, 2)) for s in scenarios])
    (output/'routing_simulation_latest.json').write_text(json.dumps({'config': snapshot, 'results': results}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    table = []
    for stock in results:
        for s in stock['scenarios']:
            m = s['metrics']
            table.append(f"<tr><td>{stock['code']} {stock['name']}</td><td>{s['mode']}</td><td>{m['total_return']:.2%}</td><td>{m['max_drawdown']:.2%}</td><td>{m['transaction_cost']:.0f}</td><td>{m['completed_trades']}</td><td>{stock['benchmark']['total_return']:.2%}</td></tr>")
    page = '<!doctype html><meta charset="utf-8"><title>行情切換實際模擬</title><style>body{font-family:Microsoft JhengHei;margin:30px}td,th{padding:10px;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>行情切換實際模擬</h1><p>routing：上漲用區間突破、盤整用布林通道、下跌與資料不足不進場。行情切換先平倉，下一根K棒才能重新進場。單一帳戶10萬元，包含手續費與交易稅。</p><p>'+html.escape(snapshot['limitation'])+'</p><table><tr><th>股票</th><th>策略</th><th>報酬</th><th>最大回撤</th><th>成本</th><th>完整交易</th><th>買進持有</th></tr>'+''.join(table)+'</table>'
    (output/'routing_simulation_latest.html').write_text(page, encoding='utf-8')
    lines = ['# 行情切換模擬成果', '', snapshot['limitation'], '', '上漲使用區間突破、盤整使用布林通道。下跌及資料不足不進場。切換時先賣出舊持倉，至少下一根K棒才進場。', '', '|股票|切換報酬|固定MA|固定布林|固定突破|', '|---|---:|---:|---:|---:|']
    for stock in results:
        m = {s['mode']: s['metrics']['total_return'] for s in stock['scenarios']}
        lines.append(f"|{stock['code']} {stock['name']}|{m['routing']:.2%}|{m['ma']:.2%}|{m['bollinger']:.2%}|{m['breakout']:.2%}|")
    lines += ['', '下一步：依本次結果決定是否保留研究候選。固定規則後，需使用未參與調整的期間驗證；本輪不直接產生正式交易推薦。']
    (ROOT/'docs/行情切換模擬成果_20260926.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()

