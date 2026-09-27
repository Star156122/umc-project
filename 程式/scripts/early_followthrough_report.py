"""早期價格延續拆項結果，完整保留成功與失敗。"""
import csv,html,json
from pathlib import Path
from trading_system.research_guard import assert_payload

ROOT=Path(__file__).resolve().parents[1]
NAMES={'macd':'MACD修改版','vote':'三指標多數決修改版'}


def pct(v):return f'{v*100:+.2f}%'
def money(v):return f'{v:,.0f}'


def write_csv(path,rows):
    if not rows:return
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def write_report(payload):
    assert_payload(payload)
    out=ROOT/'exports/early_followthrough'
    comparison=[];trades=[]
    for strategy in payload['plan']['strategies']:
        for code in payload['plan']['stock_codes']:
            base=next(r for r in payload['records'] if r['strategy']==strategy and r['code']==code and r['variant']=='baseline')
            cand=next(r for r in payload['records'] if r['strategy']==strategy and r['code']==code and r['variant']=='followthrough')
            base_low=sum('進場後幾乎沒有有效浮盈' in d['失敗分類'] for d in base['classified_trades'])
            cand_low=sum('進場後幾乎沒有有效浮盈' in d['失敗分類'] for d in cand['classified_trades'])
            base_reverse=sum('進場後短時間內立刻反向' in d['失敗分類'] for d in base['classified_trades'])
            cand_reverse=sum('進場後短時間內立刻反向' in d['失敗分類'] for d in cand['classified_trades'])
            comparison.append({'策略':NAMES[strategy],'股票':code,'股票名稱':base['name'],
              '原修改版報酬':base['metrics']['total_return'],'延續確認版報酬':cand['metrics']['total_return'],
              '報酬差':cand['metrics']['total_return']-base['metrics']['total_return'],
              '原交易數':base['metrics']['completed_trades'],'延續確認交易數':cand['metrics']['completed_trades'],
              '原淨損益':base['metrics']['net_pnl'],'延續確認淨損益':cand['metrics']['net_pnl'],
              '原成本':base['metrics']['transaction_cost'],'延續確認成本':cand['metrics']['transaction_cost'],
              '原無有效浮盈':base_low,'確認版無有效浮盈':cand_low,
              '原立即反向':base_reverse,'確認版立即反向':cand_reverse,
              '樣本判定':'足夠作本輪配對比較' if cand['metrics']['completed_trades']>=payload['plan']['evaluation']['minimum_trades_per_stock'] else '證據不足（少於5筆）',
              '同期買進持有':payload['benchmarks'][code]['total_return']})
            for row in cand['classified_trades']:trades.append(row)
    write_csv(out/'stock_comparison.csv',comparison);write_csv(out/'followthrough_all_trades.csv',trades)
    registry={'experiment_id':payload['plan']['experiment_id'],'data_role':'development_only',
              'formal_strategy_changed':False,'holdout_accessed':False,
              'retained':[{'strategy':s['strategy'],'paired':s['paired']} for s in payload['summary'] if s['retained']]}
    (out/'candidates.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 早期價格延續確認拆項回測（2026/09/26）','',
      '只測MACD修改版與三指標多數決修改版。原訊號K0完成後不成交；K1完成收盤必須高於K0最高價，才在K2開盤成交。K1未確認即取消。沒有百分比或根數掃描。','',
      'MACD 12/26/9、RSI門檻、MA週期、停損停利、technical exit、trailing stop、日線濾網與其他規則完全不變。沒有加入盤整濾網。','',
      '## 六檔合計','',
      '|策略|版本|平均報酬|中位數|獲利股票|交易數|毛損益|淨損益|成本|勝率|PF|平均回撤|平均Sharpe|成功交易|無有效浮盈|立即反向|',
      '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in payload['summary']:
        for label,key in [('原修改版','baseline'),('延續確認版','followthrough')]:
            m=s[key]
            lines.append(f"|{NAMES[s['strategy']]}|{label}|{pct(m['mean_return'])}|{pct(m['median_return'])}|{m['profitable_stocks']}/6|{int(m['completed_trades'])}|{money(m['gross_pnl'])}|{money(m['net_pnl'])}|{money(m['transaction_cost'])}|{m['win_rate']:.2%}|{m['profit_factor']:.2f}|{m['mean_drawdown']:.2%}|{m['mean_sharpe']:.3f}|{m['successful_trades']}|{m['low_mfe_trades']}|{m['immediate_reversal_trades']}|")
        pair=s['paired'];conf=s['confirmation']
        lines += ['',f"{NAMES[s['strategy']]}：改善 {pair['improved_stocks']}/6；報酬差中位數 {pct(pair['median_return_delta'])}；不含2303為 {pct(s['median_delta_without_2303'])}；原訊號 {conf.get('origin_signals',0)}、確認 {conf.get('confirmed_signals',0)}、取消 {conf.get('cancelled_signals',0)}。判定：{'保留研究候選' if s['retained'] else '未通過固定門檻'}。",'']
    lines += ['## 各股票結果','',
              '|策略|股票|原修改版|延續確認版|差異|原交易|確認版交易|無有效浮盈 原→確認|立即反向 原→確認|原成本|確認版成本|樣本判定|買進持有|',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|']
    for r in comparison:
        mark='（重點）' if r['股票'] in payload['plan']['priority_stock_codes'] else ''
        lines.append(f"|{r['策略']}|{r['股票']} {r['股票名稱']}{mark}|{pct(r['原修改版報酬'])}|{pct(r['延續確認版報酬'])}|{pct(r['報酬差'])}|{r['原交易數']}|{r['延續確認交易數']}|{r['原無有效浮盈']}→{r['確認版無有效浮盈']}|{r['原立即反向']}→{r['確認版立即反向']}|{money(r['原成本'])}|{money(r['延續確認成本'])}|{r['樣本判定']}|{pct(r['同期買進持有'])}|")
    lines += ['','## 判定原則與限制','',
              '- 改善不能只靠平均值；同時檢查改善股票數、不含2303中位數、成本、回撤、額外成本壓力及每檔樣本。',
              '- 交易大幅減少時，即使報酬上升也會標示樣本不足。',
              '- 「無有效浮盈」沿用已登記診斷定義：最高完成收盤浮盈低於0.6%；「立即反向」為前三根完成K棒跌至少0.5%且未出現0.6%浮盈。',
              '- 這是已看過的2026上半年開發資料，未執行Holdout，不能宣稱為最終策略。',
              '- 所有交易保留於 results.json 與 followthrough_all_trades.csv。']
    (ROOT/'docs/早期價格延續確認_20260926.md').write_text('\n'.join(lines),encoding='utf-8')

    cards=[]
    for s in payload['summary']:
        rows=[]
        for label,key in [('原修改版','baseline'),('延續確認版','followthrough')]:
            m=s[key];rows.append(f"<tr class=\"{'pass' if key=='followthrough' and s['retained'] else ''}\"><td>{label}</td><td>{pct(m['mean_return'])}</td><td>{pct(m['median_return'])}</td><td>{m['profitable_stocks']}/6</td><td>{int(m['completed_trades'])}</td><td>{money(m['gross_pnl'])}</td><td>{money(m['net_pnl'])}</td><td>{money(m['transaction_cost'])}</td><td>{m['win_rate']:.2%}</td><td>{m['profit_factor']:.2f}</td><td>{m['mean_drawdown']:.2%}</td><td>{m['mean_sharpe']:.3f}</td><td>{m['successful_trades']}</td><td>{m['low_mfe_trades']}</td><td>{m['immediate_reversal_trades']}</td></tr>")
        p=s['paired'];c=s['confirmation']
        cards.append(f'''<section><h2>{NAMES[s['strategy']]}</h2><div class="scroll"><table><tr><th>版本</th><th>平均報酬</th><th>中位數</th><th>獲利股票</th><th>交易</th><th>毛損益</th><th>淨損益</th><th>成本</th><th>勝率</th><th>PF</th><th>回撤</th><th>Sharpe</th><th>成功</th><th>無有效浮盈</th><th>立即反向</th></tr>{''.join(rows)}</table></div><p>改善 {p['improved_stocks']}/6；不含2303改善中位數 {pct(s['median_delta_without_2303'])}。原訊號 {c.get('origin_signals',0)}，確認 {c.get('confirmed_signals',0)}，取消 {c.get('cancelled_signals',0)}。<strong>{'保留研究候選' if s['retained'] else '未通過固定門檻'}</strong></p></section>''')
    stock=''.join(f"<tr><td>{r['策略']}</td><td>{r['股票']} {html.escape(r['股票名稱'])}</td><td>{pct(r['原修改版報酬'])}</td><td>{pct(r['延續確認版報酬'])}</td><td>{pct(r['報酬差'])}</td><td>{r['原交易數']}</td><td>{r['延續確認交易數']}</td><td>{r['原無有效浮盈']}→{r['確認版無有效浮盈']}</td><td>{r['原立即反向']}→{r['確認版立即反向']}</td><td>{money(r['原成本'])}</td><td>{money(r['延續確認成本'])}</td><td>{html.escape(r['樣本判定'])}</td><td>{pct(r['同期買進持有'])}</td></tr>" for r in comparison)
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>早期價格延續確認拆項</title><style>body{{font-family:Microsoft JhengHei,sans-serif;background:#f4f6fa;color:#17243a;margin:0}}main{{max-width:1700px;margin:auto;padding:28px}}section,.note{{background:white;padding:18px;margin:16px 0;border-radius:10px}}.note{{background:#fff0c9}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d7dee8;padding:8px;white-space:nowrap;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#e8eef6}}.scroll{{overflow:auto}}.pass{{background:#dff1e5}}p{{line-height:1.7}}</style><main><h1>早期價格延續確認拆項回測</h1><p>K0原訊號 → K1收盤突破K0最高價 → K2開盤成交</p><div class="note">只新增一項價格延續確認；其他進出場、風控、指標參數與日線濾網不變。這是開發資料，不是Holdout。</div><p><a href="early_followthrough/stock_comparison.csv">各股票CSV</a>｜<a href="early_followthrough/followthrough_all_trades.csv">全部確認版交易</a></p>{''.join(cards)}<section><h2>六檔個別結果</h2><div class="scroll"><table><tr><th>策略</th><th>股票</th><th>原修改版</th><th>確認版</th><th>差異</th><th>原交易</th><th>確認交易</th><th>無有效浮盈</th><th>立即反向</th><th>原成本</th><th>確認成本</th><th>樣本判定</th><th>買進持有</th></tr>{stock}</table></div></section></main></html>'''
    (ROOT/'exports/early_followthrough_latest.html').write_text(page,encoding='utf-8')


if __name__=='__main__':write_report(json.loads((ROOT/'exports/early_followthrough/results.json').read_text(encoding='utf-8')))
