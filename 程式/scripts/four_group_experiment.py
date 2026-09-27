"""開發資料四組比較：只修改進場篩選，不改出場。無下載功能。"""
import contextlib
import dataclasses
import io
import json
from scripts.routing_simulation import ROOT, simulate
from scripts.multi_stock_batch import load_plan, build_runtime_config
from trading_system import backtest as bt


def main():
    stocks, templates, experiment = load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment['strategy_keys'] = ['breakout']
    snapshot = {'purpose': 'development_only', 'experiment': experiment, 'templates': templates,
                'change': '僅新增前20交易日漲幅超過5%才准進場，沿用原版出場；前21日資料不足不新進場',
                'historical_holdout': '等待使用者確認，不下載', 'slippage': 0}
    (ROOT/'exports/four_group_config.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    results = []
    fields = {f.name for f in dataclasses.fields(bt.AppConfig)}
    for code in experiment['stock_codes']:
        runtime = build_runtime_config(stocks[code], templates, experiment)
        settings = dict(runtime, **runtime['profiles'][f'{code}_breakout'])
        settings.update(llm_enabled=False, db_enabled=False, allow_real_trading=False, tick_source='sinopac', is_backtest=True)
        config = bt.AppConfig(**{k: v for k, v in settings.items() if k in fields})
        with contextlib.redirect_stdout(io.StringIO()):
            frame, stats = bt.load_cached_kbar_frame(config)
            configs, frames = {'ma': config, 'breakout': config}, {'ma': frame, 'breakout': frame}
            original = simulate(configs, frames, stats, 'breakout')
            modified = simulate(configs, frames, stats, 'breakout', entry_filter=True)
            benchmark = bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame, config), config)
        results.append({'code': code, 'name': stocks[code]['name'], 'original': original, 'modified': modified, 'buy_hold': benchmark, 'cash_return': 0})
        print(code, original['metrics']['total_return'], modified['metrics']['total_return'], flush=True)
    (ROOT/'exports/four_group_latest.json').write_text(json.dumps({'config': snapshot, 'results': results}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    rows=[]
    lines=['# 四組開發實驗（2026/01/01～06/30）', '', '原版為固定v2區間突破，修改版只新增上漲進場限制，出場不變。資料已使用，並非獨立驗證。無滑價；初始21交易日篩選資料不足。', '', '|股票|原版|修改版|買進持有|現金|原版成本|修改成本|', '|---|---:|---:|---:|---:|---:|---:|']
    for r in results:
        a,b=r['original']['metrics'],r['modified']['metrics']
        cells=[r['code']+' '+r['name'], f"{a['total_return']:.2%}", f"{b['total_return']:.2%}", f"{r['buy_hold']['total_return']:.2%}", '0.00%', f"{a['transaction_cost']:.0f}", f"{b['transaction_cost']:.0f}"]
        lines.append('|'+ '|'.join(cells)+'|')
        cells += [f"{a['max_drawdown']:.2%} → {b['max_drawdown']:.2%}", f"{a['completed_trades']} → {b['completed_trades']}"]
        rows.append('<tr>'+''.join('<td>'+v+'</td>' for v in cells)+'</tr>')
    lines += ['', '歷史保留期間尚待使用者確認；未抓取新資料。結果只用於判斷是否保留候選，不依此改參數或宣稱有效。']
    (ROOT/'docs/四組實驗成果_20260926.md').write_text('\n'.join(lines), encoding='utf-8')
    page='<meta charset="utf-8"><title>四組對照實驗</title><style>body{font-family:Microsoft JhengHei;margin:30px}td,th{padding:12px;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>一月至六月：四組對照實驗</h1><p>'+lines[2]+'</p><table><tr>'+''.join('<th>'+x+'</th>' for x in ['股票','原版報酬','修改報酬','買進持有','現金','原版成本','修改成本','最大回撤 原→改','交易數 原→改'])+'</tr>'+''.join(rows)+'</table><p>所有組別同本金、股數上限與稅費。現金不計利息。新歷史期間待確認，尚未下載。</p>'
    (ROOT/'exports/four_group_latest.html').write_text(page, encoding='utf-8')


if __name__ == '__main__':
    main()
