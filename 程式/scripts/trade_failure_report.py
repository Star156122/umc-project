"""將既有成交失敗診斷輸出為HTML與MD，不產生任何策略訊號。"""
import html,json
from collections import Counter
from pathlib import Path
from trading_system.research_guard import assert_payload

ROOT=Path(__file__).resolve().parents[1]
NAMES={'macd':'MACD修改版','vote':'三指標多數決修改版'}
REQUIRED=['進場後幾乎沒有有效浮盈','有明顯浮盈但吐回大部分獲利','固定停損出場','趨勢破壞出場',
          '一般技術訊號出場','移動停利出場','價差為正但被成本轉成淨虧損','盤整行情造成的假訊號',
          '進場後短時間內立刻反向','進場太晚，可能接近短期高點','其他無法歸類的異常交易']


def pct(v):return '—' if v is None else f'{v*100:.2f}%'
def num(v,d=2):return '—' if v is None else f'{v:.{d}f}'
def money(v):return f'{v:,.0f}'


def category_counts(rows):
    c=Counter()
    for r in rows:
        if r['交易品質']!='成功交易':c.update(r['失敗分類'].split('｜'))
    return c


def strategy_findings(payload,key):
    rows=[r for r in payload['rows'] if r['策略']==NAMES[key]]
    bad=[r for r in rows if r['交易品質']!='成功交易'];counts=category_counts(rows)
    stocks=[s for s in payload['stocks'] if s['策略']==NAMES[key]]
    losing=[s['股票代號']+' '+s['股票名稱'] for s in stocks if s['淨損益']<0]
    entry=sum('進場後幾乎沒有有效浮盈' in r['失敗分類'] or '進場後短時間內立刻反向' in r['失敗分類'] for r in bad)
    exit_issue=sum('有明顯浮盈但吐回大部分獲利' in r['失敗分類'] for r in bad)
    cost=sum('成本轉成淨虧損' in r['失敗分類'] for r in bad)
    regime=sum('盤整行情' in r['失敗分類'] for r in bad)
    return {'rows':rows,'bad':bad,'counts':counts,'stocks':stocks,'losing':losing,
            'problem_counts':{'進場問題線索':entry,'出場問題線索':exit_issue,'成本問題':cost,'盤整狀態線索':regime}}


def comparison_table(payload,key):
    return [r for r in payload['comparisons'] if r['策略']==NAMES[key]]


def write_report(payload):
    assert_payload(payload)
    findings={key:strategy_findings(payload,key) for key in ('macd','vote')}
    all_bad=[r for r in payload['rows'] if r['交易品質']!='成功交易']
    confirmed=Counter();inferred=Counter()
    for r in all_bad:
        for label in r['失敗分類'].split('｜'):
            (inferred if label in ('盤整行情造成的假訊號','進場太晚，可能接近短期高點') else confirmed)[label]+=1
    shared=set(findings['macd']['counts'])&set(findings['vote']['counts'])
    only={key:set(findings[key]['counts'])-set(findings['vote' if key=='macd' else 'macd']['counts']) for key in ('macd','vote')}
    directions=[
      ('盤整狀態過濾候選','解決兩策略在日線仍偏多、但盤中窄幅整理時產生的假訊號。下一輪須先固定一個因果、只讀進場前資料的盤整定義，再做單一拆項比較。'),
      ('早期延續確認候選','解決進場後前三根K棒沒有延續或立刻反向的交易。研究重點是訊號後是否出現價格延續，不掃描MACD參數或RSI門檻。'),
      ('浮盈保護／出場反應候選','解決已出現至少1%浮盈、最後吐回一半以上的交易。下一輪只比較原出場與一種事先固定的保護方式，不與進場修改同時混用。'),
      ('成本可行性候選','解決毛價差為正但不足支付費稅的交易；進場前須有合理的可得報酬空間依據，而不是事後刪除虧損交易。'),
      ('多數決新鮮度候選','只針對多數決：解決三個指標早已偏多、到較晚位置才進場的問題；可研究是否至少一票在近期才由空轉多，不調整票數或大量掃參數。'),
      ('MACD交叉品質候選','只針對MACD：解決零軸上方但交叉後沒有延續的交易；可比較交叉後柱狀體是否持續擴張，先做診斷定義，不改12/26/9。')]

    lines=['# MACD與三指標多數決逐筆交易失敗診斷（2026/09/26）','',
      '本報告只分析已完成的2026/01/01～06/30修改版成交，沒有修改參數、沒有新回測、沒有使用2025下半年Holdout。MACD與多數決仍只是保留研究候選。','',
      '## 分類定義','',
      '- 已確認：成交、損益、費稅、出場訊號、實際持有期間的價格路徑。最高浮盈以完成5分K收盤計算，另保留盤中最高價欄位。',
      '- 推測：盤整假訊號與進場接近短期高點，是固定價格結構規則得到的診斷線索，尚未證明因果。',
      '- 證據不足：無法從這一段資料證明某個新條件未來有效；交易少的股票或出場類型不做強結論。','']
    for key,title in (('macd','A. MACD 修改版'),('vote','B. 三指標多數決修改版')):
        f=findings[key]
        lines += [f'## {title}','',f"仍虧損股票：{'、'.join(f['losing']) if f['losing'] else '無'}。",
                  f"失敗或低品質交易 {len(f['bad'])}/{len(f['rows'])} 筆。主要類型："+'、'.join(f'{k} {v}筆' for k,v in f['counts'].most_common(5))+'。','',
                  '|問題面向|交易筆數線索|','|---|---:|']
        for name,value in f['problem_counts'].items():lines.append(f'|{name}|{value}|')
        lines += ['', '完整分類筆數（同一筆可有多個標籤）：','', '|分類|筆數|證據層級|','|---|---:|---|']
        for label in REQUIRED:
            level='推測線索' if label in ('盤整行情造成的假訊號','進場太晚，可能接近短期高點') else '已確認資料'
            lines.append(f"|{label}|{f['counts'][label]}|{level}|")
        lines += ['', '### 成功、失敗與低品質小利對照','',
                  '|類型|筆數|距趨勢均線中位數|量比中位數|日線成立率|MACD零軸上方|MACD線差／價格|前三棒延續|持有K棒|最高浮盈|最大浮虧|盤整線索|晚進場線索|09～10點占比|主要出場|',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
        for r in comparison_table(payload,key):
            exits='、'.join(f'{name}:{count}' for name,count in r['主要出場'])
            lines.append(f"|{r['交易品質']}|{r['筆數']}|{pct(r['距趨勢均線中位數'])}|{num(r['成交量比中位數'])}|{pct(r['日線趨勢成立率'])}|{pct(r['MACD零軸上方率'])}|{pct(r['MACD線差占價格中位數'])}|{pct(r['前三棒最大延續中位數'])}|{num(r['持有K棒中位數'],0)}|{pct(r['最高浮盈中位數'])}|{pct(r['最大浮虧中位數'])}|{pct(r['盤整線索率'])}|{pct(r['晚進場線索率'])}|{pct(r['09至10點占比'])}|{exits}|")
        lines += ['', '各股票明細：','', '|股票|報酬率|淨損益|總交易|成功|失敗與低品質|前三大模式|','|---|---:|---:|---:|---:|---:|---|']
        for s in f['stocks']:
            lines.append(f"|{s['股票代號']} {s['股票名稱']}|{pct(s['報酬率'])}|{money(s['淨損益'])}|{s['總交易']}|{s['成功交易']}|{s['失敗與低品質']}|{s['前三大失敗模式']}|")
        lines += ['']
    lines += ['## C. 兩策略共同問題','',
              '共同出現的失敗模式：'+'、'.join(f'{x}（MACD {findings["macd"]["counts"][x]}、多數決 {findings["vote"]["counts"][x]}）' for x in sorted(shared))+'。','',
              '只在本批MACD失敗／低品質交易出現：'+('、'.join(sorted(only['macd'])) or '無')+'。',
              '只在本批多數決失敗／低品質交易出現：'+('、'.join(sorted(only['vote'])) or '無')+'。','',
              '日線趨勢在兩策略所有修改版進場都成立，因此它無法解釋同一修改版內成功與失敗的差異；只能確認日線濾網沒有完全消除盤中失敗訊號。','',
              '## 已確認、推測與證據不足','',
              '### 已確認','']
    lines += [f'- {k}：{v}筆。' for k,v in confirmed.most_common()]
    lines += ['', '### 推測中的問題','']+[f'- {k}：{v}筆診斷線索，尚未證明因果。' for k,v in inferred.most_common()]
    lines += ['', '### 證據不足','',
              '- 無法用本輪診斷直接決定新的最佳門檻、均線週期、MACD參數、停損或停利比例。',
              '- 布林、MA、RSI與突破不在本次逐筆範圍，不能用這份報告替它們下新結論。',
              '- 2303仍納入完整紀錄，但泛化判讀以另外五檔為主；候選尚未經獨立Holdout。','',
              '## D. 下一輪候選改善方向（尚未實作）','']
    for name,reason in directions:lines += [f'### {name}','',f'具體想解決：{reason}','']
    lines += ['## 輸出檔案','',
              '- `exports/trade_failure_diagnosis/macd_failed_and_low_quality.csv`',
              '- `exports/trade_failure_diagnosis/vote_failed_and_low_quality.csv`',
              '- `exports/trade_failure_diagnosis/success_vs_failure.csv`',
              '- `exports/trade_failure_diagnosis/stock_failure_modes.csv`',
              '- `exports/trade_failure_diagnosis/all_trades.csv`']
    (ROOT/'docs/MACD_多數決逐筆失敗診斷_20260926.md').write_text('\n'.join(lines),encoding='utf-8')

    sections=[]
    for key in ('macd','vote'):
        f=findings[key];feature=[]
        for r in comparison_table(payload,key):
            feature.append(f"<tr><td>{r['交易品質']}</td><td>{r['筆數']}</td><td>{pct(r['距趨勢均線中位數'])}</td><td>{num(r['成交量比中位數'])}</td><td>{pct(r['前三棒最大延續中位數'])}</td><td>{num(r['持有K棒中位數'],0)}</td><td>{pct(r['最高浮盈中位數'])}</td><td>{pct(r['最大浮虧中位數'])}</td><td>{pct(r['盤整線索率'])}</td><td>{pct(r['晚進場線索率'])}</td></tr>")
        stock=''.join(f"<tr><td>{s['股票代號']} {s['股票名稱']}</td><td>{pct(s['報酬率'])}</td><td>{money(s['淨損益'])}</td><td>{s['總交易']}</td><td>{s['成功交易']}</td><td>{s['失敗與低品質']}</td><td>{html.escape(s['前三大失敗模式'])}</td></tr>" for s in f['stocks'])
        detail=[]
        for r in f['bad']:
            detail.append(f"<tr><td>{r['股票代號']}</td><td>{r['進場時間']}</td><td>{r['出場時間']}</td><td>{r['進場價格']}</td><td>{r['出場價格']}</td><td>{money(r['淨損益'])}</td><td>{pct(r['報酬率'])}</td><td>{r['持有K棒數']}</td><td>{pct(r['最高完成收盤浮盈'])}</td><td>{pct(r['最大盤中浮虧'])}</td><td>{r['進場後幾根出現最高浮盈']}</td><td>{pct(r['浮盈吐回比例'])}</td><td>{html.escape(r['出場原因'])}</td><td>{num(r['成交量比'])}</td><td>{'是' if r['可能位於盤整區'] else '否'}</td><td>{html.escape(r['失敗分類'])}</td></tr>")
        sections.append(f'''<section><h2>{NAMES[key]}</h2><p>仍虧損股票：{html.escape('、'.join(f['losing']))}。失敗／低品質 {len(f['bad'])}/{len(f['rows'])}筆。主要模式：{html.escape('、'.join(f'{k} {v}筆' for k,v in f['counts'].most_common(5)))}</p>
        <h3>成功與失敗特徵</h3><div class="scroll"><table><tr><th>類型</th><th>筆數</th><th>距趨勢MA</th><th>量比</th><th>前三棒延續</th><th>持有K棒</th><th>最高浮盈</th><th>最大浮虧</th><th>盤整線索</th><th>晚進場線索</th></tr>{''.join(feature)}</table></div>
        <h3>各股票</h3><div class="scroll"><table><tr><th>股票</th><th>報酬</th><th>淨損益</th><th>交易</th><th>成功</th><th>失敗／低品質</th><th>前三大模式</th></tr>{stock}</table></div>
        <details><summary>展開逐筆失敗與低品質交易</summary><div class="scroll"><table><tr><th>股票</th><th>進場</th><th>出場</th><th>進價</th><th>出價</th><th>淨損益</th><th>報酬</th><th>持有棒</th><th>最高浮盈</th><th>最大浮虧</th><th>幾棒達高點</th><th>吐回比例</th><th>出場</th><th>量比</th><th>盤整</th><th>分類</th></tr>{''.join(detail)}</table></div></details></section>''')
    direction_html=''.join(f'<li><strong>{html.escape(name)}</strong>：{html.escape(reason)}</li>' for name,reason in directions)
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MACD與多數決逐筆失敗診斷</title><style>body{{font-family:Microsoft JhengHei,sans-serif;background:#f4f6fa;color:#17243a;margin:0}}main{{max-width:1700px;margin:auto;padding:28px}}section,.note{{background:white;padding:18px;margin:16px 0;border-radius:10px}}.note{{background:#fff0c9}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d7dee8;padding:8px;white-space:nowrap;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#e8eef6}}.scroll{{overflow:auto}}summary{{cursor:pointer;background:#e8eef6;padding:12px}}li,p{{line-height:1.7}}</style><main><h1>MACD與三指標多數決：逐筆交易失敗診斷</h1><p>既有2026上半年修改版成交｜沒有新回測｜沒有修改參數｜Holdout未使用</p><div class="note">已確認資料與推測線索分開呈現。盤整、晚進場是事後診斷，不是已證實因果。兩策略仍只是保留研究候選。</div>
    <p><a href="trade_failure_diagnosis/macd_failed_and_low_quality.csv">MACD逐筆表</a>｜<a href="trade_failure_diagnosis/vote_failed_and_low_quality.csv">多數決逐筆表</a>｜<a href="trade_failure_diagnosis/success_vs_failure.csv">成功vs失敗</a>｜<a href="trade_failure_diagnosis/stock_failure_modes.csv">各股票模式</a></p>{''.join(sections)}
    <section><h2>共同問題與證據分級</h2><p><strong>共同模式：</strong>{html.escape('、'.join(sorted(shared)))}</p><p><strong>已確認：</strong>{html.escape('、'.join(f'{k} {v}筆' for k,v in confirmed.most_common()))}</p><p><strong>推測：</strong>{html.escape('、'.join(f'{k} {v}筆' for k,v in inferred.most_common()))}</p><p><strong>證據不足：</strong>目前不能決定新參數，也不能宣稱已通過獨立驗證。</p></section>
    <section><h2>下一輪候選方向（未實作）</h2><ol>{direction_html}</ol></section></main></html>'''
    (ROOT/'exports/trade_failure_diagnosis_latest.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    write_report(json.loads((ROOT/'exports/trade_failure_diagnosis/diagnosis.json').read_text(encoding='utf-8')))
