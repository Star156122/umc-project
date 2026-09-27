"""六策略問題導向修改的完整開發期比較報告。"""
import html,json
from pathlib import Path
from trading_system.research_guard import assert_payload

ROOT=Path(__file__).resolve().parents[1]
NAMES={'ma':'MA均線','rsi':'RSI動能','macd':'MACD','bollinger':'布林通道','breakout':'區間突破','vote':'三指標多數決'}


def pct(value):return f'{value*100:+.2f}%'
def money(value):return f'{value:,.0f}'


def write_report(payload):
    assert_payload(payload)
    registry={'experiment_id':payload['plan']['experiment_id'],'data_role':'development_only',
              'holdout_accessed':False,'formal_strategy_changed':False,
              'candidates':[{'strategy':s['strategy'],'rule':s['rule'],'against_v2':s['against_v2'],
                             'against_warmup':s['against_warmup']} for s in payload['summary'] if s['retained']]}
    (ROOT/'exports/six_strategy_logic/candidates.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 六策略問題導向修改與回測結果（2026/09/26）','',
           '固定使用2026/01/01～06/30開發資料，六檔股票共用同一套規則。沒有讀取2025下半年Holdout，沒有依個股挑參數。這是開發期結果，不是未來獲利保證。','',
           '## 各策略實際修改','']
    explanations={
      'ma':'在原本均線交叉、價格高於慢均線及趨勢濾網之外，要求價格距慢均線不超過3%，而且長期趨勢均線在回看區間至少上升0.2%。目標是同時減少追高與品質不足的假趨勢。',
      'rsi':'原本RSI由下往上穿越55就進場；修改後先武裝訊號，再等下一根完成5分K的RSI仍在55～70且趨勢仍成立才進場。目標是排除只出現一根的動能尖峰。',
      'macd':'保留MACD黃金交叉、MACD大於0及盤中趨勢條件，另外要求前一日收盤高於20日均線，且20日均線高於5個交易日前。當日完全不讀當日收盤，目標是過濾盤整及日線弱勢。',
      'bollinger':'保留下軌外回到下軌內、RSI、報酬空間與紅K要求，再要求收盤位於該根K棒上方25%區域。目標是確認回到通道時確實有明顯承接。',
      'breakout':'保留前高、交叉、1.1倍均量及趨勢條件，再要求突破幅度介於0.2%～2%。目標是排除剛擦過前高的假突破，也避免訊號確認時已追價過遠。',
      'vote':'保留原本三票全數通過及盤中趨勢條件，另外加入與MACD相同、只用前一日以前資料的日線多頭濾網，目標是減少弱勢行情錯誤進場。'}
    profiles={
      'ma':('長處是能抓到大型上升波段，原V2六檔合計PF達2.14、交易僅77筆。','短處是績效受少數強勢股票拉高，均線交叉較慢，容易在價格離均線過遠後進場。'),
      'rsi':('長處是能捕捉短線動能，獲利交易的平均金額高於平均虧損。','短處是原V2有291筆交易、六檔成本超過10萬元，勝率僅17.5%，而且毛損益也不足，問題不只有成本。'),
      'macd':('長處是在明顯趨勢股能保留大波段，原V2六檔毛損益為正。','短處是盤整時容易反覆交叉；原V2只有1/6檔淨獲利，結果也受到聯電拉高。'),
      'bollinger':('長處是交易頻率低、平均回撤較小，適合作為均值回歸對照。','短處是原V2六檔毛損益已為負，表示進場優勢不足；樣本也少，不能因勝率看似較高就認定有效。'),
      'breakout':('長處是能在真正趨勢突破時取得較大獲利，原V2合計PF為1.53。','短處是假突破及追價會造成價差損失，平均報酬主要由少數強勢股票支撐。'),
      'vote':('長處是多個指標共同確認後，原V2六檔毛損益及淨損益為正。','短處是弱勢市場中三個盤中指標仍可能一起偏多，原V2只有1/6檔獲利且交易成本高。')}
    for key in payload['plan']['strategies']:
        lines+= [f'### {NAMES[key]}','',f'- {profiles[key][0]}',f'- {profiles[key][1]}',f'- 修改：{explanations[key]}','']
    lines += ['## 六檔合計比較','',
              '|策略|版本|平均報酬|中位數報酬|獲利股票|毛損益|淨損益|成本|交易筆數|勝率|平均獲利|平均虧損|PF|平均回撤|平均Sharpe|',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in payload['summary']:
        for version,key in [('原V2','baseline'),('修改版','candidate')]:
            m=s[key]
            lines.append(f"|{NAMES[s['strategy']]}|{version}|{pct(m['mean_return'])}|{pct(m['median_return'])}|{m['profitable_stocks']}/6|{money(m['gross_pnl'])}|{money(m['net_pnl'])}|{money(m['transaction_cost'])}|{int(m['completed_trades'])}|{m['win_rate']:.2%}|{money(m['average_win'])}|{money(m['average_loss'])}|{m['profit_factor']:.2f}|{m['mean_drawdown']:.2%}|{m['mean_sharpe']:.3f}|")
    lines += ['', '## 跨股票判定','',
              '|策略|改善股票|報酬差中位數|不含2303差異中位數|成本差中位數|回撤差中位數|判定|',
              '|---|---:|---:|---:|---:|---:|---|']
    for s in payload['summary']:
        a=s['against_v2'];status='保留研究候選' if s['retained'] else '未通過固定門檻'
        lines.append(f"|{NAMES[s['strategy']]}|{a['improved_stocks']}/6|{pct(a['median_return_delta'])}|{pct(s['median_delta_without_2303'])}|{money(a['median_cost_delta'])}|{pct(a['median_drawdown_delta'])}|{status}|")
    lines += ['', '日線濾網的MACD與多數決另與「同樣等待25個交易日、但不使用日線方向」的對照比較；只有同時通過原V2與相同暖機對照才保留。', '',
              '|策略|相對暖機對照改善股票|報酬差中位數|判定|','|---|---:|---:|---|']
    for s in payload['summary']:
        if s['against_warmup']:
            a=s['against_warmup'];lines.append(f"|{NAMES[s['strategy']]}|{a['improved_stocks']}/6|{pct(a['median_return_delta'])}|{a['status']}|")
    lines += ['', '## 未通過原因','']
    for s in payload['summary']:
        if not s['retained']:
            reasons='、'.join(s['against_v2']['reasons'] or (s['against_warmup'] or {}).get('reasons',[]))
            lines.append(f"- {NAMES[s['strategy']]}：{reasons}。")
    lines += ['',
              '## 各股票報酬','', '|策略|股票|原V2|修改版|差異|同期買進持有|', '|---|---|---:|---:|---:|---:|']
    for strategy in payload['plan']['strategies']:
        for code in payload['plan']['stock_codes']:
            base=next(r for r in payload['records'] if r['strategy']==strategy and r['code']==code and r['variant']=='v2')
            cand=next(r for r in payload['records'] if r['strategy']==strategy and r['code']==code and r['variant']=='candidate')
            lines.append(f"|{NAMES[strategy]}|{code} {base['name']}|{pct(base['metrics']['total_return'])}|{pct(cand['metrics']['total_return'])}|{pct(cand['metrics']['total_return']-base['metrics']['total_return'])}|{pct(payload['benchmarks'][code]['total_return'])}|")
    lines += ['', '## 判讀限制','',
              '- 通過代表在已知開發資料上有跨股票相對改善；不等於已通過獨立驗證。',
              '- 修改版若仍為負報酬，只能稱為少虧，不能稱為策略有效。',
              '- 交易筆數下降會同時影響勝率、成本及統計可信度，因此列出完整筆數並要求至少4檔各有5筆交易。',
              '- 任何失敗版本均保留在結果JSON，沒有覆蓋原V2。']
    (ROOT/'docs/六策略問題導向改善_20260926.md').write_text('\n'.join(lines),encoding='utf-8')

    cards=[];stock_rows=[]
    for s in payload['summary']:
        b,c=s['baseline'],s['candidate'];a=s['against_v2']
        cards.append(f'''<section><h2>{NAMES[s['strategy']]}</h2><p><strong>長處：</strong>{html.escape(profiles[s['strategy']][0])}<br><strong>短處：</strong>{html.escape(profiles[s['strategy']][1])}<br><strong>本輪修改：</strong>{html.escape(explanations[s['strategy']])}</p>
        <table><tr><th></th><th>平均報酬</th><th>中位數</th><th>獲利股票</th><th>毛損益</th><th>淨損益</th><th>成本</th><th>交易</th><th>勝率</th><th>PF</th><th>回撤</th><th>Sharpe</th></tr>
        <tr><td>原V2</td><td>{pct(b['mean_return'])}</td><td>{pct(b['median_return'])}</td><td>{b['profitable_stocks']}/6</td><td>{money(b['gross_pnl'])}</td><td>{money(b['net_pnl'])}</td><td>{money(b['transaction_cost'])}</td><td>{int(b['completed_trades'])}</td><td>{b['win_rate']:.2%}</td><td>{b['profit_factor']:.2f}</td><td>{b['mean_drawdown']:.2%}</td><td>{b['mean_sharpe']:.3f}</td></tr>
        <tr class="{'pass' if s['retained'] else ''}"><td>修改版</td><td>{pct(c['mean_return'])}</td><td>{pct(c['median_return'])}</td><td>{c['profitable_stocks']}/6</td><td>{money(c['gross_pnl'])}</td><td>{money(c['net_pnl'])}</td><td>{money(c['transaction_cost'])}</td><td>{int(c['completed_trades'])}</td><td>{c['win_rate']:.2%}</td><td>{c['profit_factor']:.2f}</td><td>{c['mean_drawdown']:.2%}</td><td>{c['mean_sharpe']:.3f}</td></tr></table>
        <p>跨股票：改善 {a['improved_stocks']}/6；報酬差中位數 {pct(a['median_return_delta'])}；不含2303為 {pct(s['median_delta_without_2303'])}。判定：<strong>{'保留研究候選' if s['retained'] else '未通過固定門檻'}</strong>。</p></section>''')
        for code,delta in s['return_deltas'].items():
            base=next(r for r in payload['records'] if r['strategy']==s['strategy'] and r['code']==code and r['variant']=='v2')
            cand=next(r for r in payload['records'] if r['strategy']==s['strategy'] and r['code']==code and r['variant']=='candidate')
            stock_rows.append(f"<tr><td>{NAMES[s['strategy']]}</td><td>{code} {base['name']}</td><td>{pct(base['metrics']['total_return'])}</td><td>{pct(cand['metrics']['total_return'])}</td><td>{pct(delta)}</td><td>{pct(payload['benchmarks'][code]['total_return'])}</td></tr>")
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>六策略問題導向改善</title><style>body{{font-family:Microsoft JhengHei,sans-serif;background:#f4f6fa;color:#17243a;margin:0}}main{{max-width:1600px;margin:auto;padding:28px}}section,.note{{background:white;padding:18px;margin:16px 0;border-radius:10px}}.note{{background:#fff0c9}}table{{border-collapse:collapse;width:100%;overflow:auto;display:block}}th,td{{border:1px solid #d7dee8;padding:9px;white-space:nowrap;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#e8eef6}}.pass{{background:#dff1e5}}p{{line-height:1.7}}</style><main><h1>六策略問題導向改善</h1><p>2026/01/01～06/30｜6檔｜原V2與修改版；MACD、多數決另做相同暖機對照</p><div class="note">這是已看過的開發資料，不是Holdout。綠色只代表通過本輪相對改善門檻；負報酬仍是虧損。</div>{''.join(cards)}<section><h2>各股票報酬</h2><table><tr><th>策略</th><th>股票</th><th>原V2</th><th>修改版</th><th>差異</th><th>買進持有</th></tr>{''.join(stock_rows)}</table></section></main></html>'''
    (ROOT/'exports/six_strategy_logic_latest.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    write_report(json.loads((ROOT/'exports/six_strategy_logic/results.json').read_text(encoding='utf-8')))
