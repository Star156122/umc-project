"""Package fixed two-arm stock replication and describe cross-stock results."""
import csv
import json
import zipfile
from pathlib import Path
from engine import HOME,ROOT
from prepare import dump,sha

latest=json.loads((HOME/'結果/latest.json').read_text(encoding='utf-8'))
out=HOME/'結果'/latest['folder']
qa=json.loads((out/'html_qa.json').read_text(encoding='utf-8'))
assert len(qa['pages'])==5 and not qa['javascript_errors']
with (out/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f))
with (HOME.parent/'結果/20261113T224843/metrics.csv').open(encoding='utf-8-sig',newline='') as f:
    prior=list(csv.DictReader(f))
main=[m for m in rows if m['scope']=='common' and float(m['slippage_each_side'])==.0005]
assert {m['variant'] for m in rows}=={'C','D'}

def pct(v):
    if v in ['',None]:
        return '—'
    digits=4 if 0<abs(float(v))<.0001 else 2
    return f'{float(v)*100:.{digits}f}%'

text=['# 聯電 2303：原策略＋ML 與修改策略＋ML','',
      '期間固定 2023/01/01～2024/12/31；每組資金 100 萬。固定 RF、BUY-only、風險與技術參數，未針對聯電調參。',
      '', '## 相同日期主對照（買賣各 0.05% 滑價）','',
      '| 策略 | 報酬率 | 淨損益 | 交易數 | 勝率 | 最大回撤 | 平均投入部位 |',
      '|---|---:|---:|---:|---:|---:|---:|']
labels={'C':'原策略＋ML','D':'修改策略＋ML'}
for m in main:
    text.append(f"| {labels[m['variant']]} | {pct(m['return'])} | {float(m['net_pnl']):,.2f} 元 | {m['trade_count']} | {pct(m['win_rate'])} | {pct(m['max_drawdown'])} | {pct(m['avg_exposure'])} |")
x=next(m for m in main if m['variant']=='C');y=next(m for m in main if m['variant']=='D')
text+=['',f"修改版減原版：報酬差 {(float(y['return'])-float(x['return']))*100:+.2f} 個百分點，最大回撤差 {(float(y['max_drawdown'])-float(x['max_drawdown']))*100:+.2f} 個百分點。",'',
       '## 與聯發科既有結果對照（各股票內部共同日期，不合併平均）','',
       '| 股票 | 策略 | 報酬率 | 交易數 | 最大回撤 | 平均投入部位 |','|---|---|---:|---:|---:|---:|']
for stock,data in [('聯發科2454',prior),('聯電2303',rows)]:
    for m in data:
        if m['scope']=='common' and float(m['slippage_each_side'])==.0005 and m['variant'] in ['C','D']:
            text.append(f"| {stock} | {labels[m['variant']]} | {pct(m['return'])} | {m['trade_count']} | {pct(m['max_drawdown'])} | {pct(m['avg_exposure'])} |")
text+=['','## 全部可用日期補充結果','',
       '| 策略 | 報酬率 | 交易數 | 最大回撤 |','|---|---:|---:|---:|']
for m in rows:
    if m['scope']=='full' and float(m['slippage_each_side'])==.0005:
        text.append(f"| {labels[m['variant']]} | {pct(m['return'])} | {m['trade_count']} | {pct(m['max_drawdown'])} |")
text+=['','## 檢查與限制','',
       '- 五份離線 HTML：一份總比較、兩份篩選過程、兩份含估計買賣點的 K 線與回測分析。',
       '- 17 項測試含數值引擎複製一致性；原策略程式、資料庫、OOF 及聯發科既有成果的前後 SHA256 一致。',
       '- 普通市場 K 線與前均量估計按股成交，不是真實零股價格或流動性，不模擬排隊或部分成交。',
       '- 沿用不當沖與 09:10 前不成交的保守共用執行模型；未精細拆分聯電整股與零股混合庫存，因此非完整實盤執行驗證。',
       '- 原策略的固定最多1,000股在低價股上可能使部位遠小於修改版。務必一起看投入金額與回撤，不把更多投入直接當作選股或訊號優勢。',
       '- 本輪是固定規則換股票，不是清淨樣本外；兩股不能代表所有股票。1～4筆交易按規範標示證據不足。',
       '- 不依結果重新調參；加碼、再進場及不同出場的貢獻要看逐筆紀錄，不能從整包差異推定單一因果。',
       '- 2025下半年與2026/07起行情持續鎖定，未下載、查詢或回測。',
       '', '來源：聯電股利 https://www.umc.com/en/IR_Stock/dividend_information ；官方證交所日線與既有日期受限資料庫。']
(out/'結果說明.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
dump(out/'delivery_manifest.json',{'html_count':5,'assets':{p.name:sha(p) for p in out.iterdir() if p.suffix in ['.html','.js','.css']},
     'parent_results_preserved':True,'engine_stock_routing_only':True})
archive=HOME/'結果/聯電2303_兩策略ML比較報告與明細.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for path in out.rglob('*'):
        if path.is_file():
            z.write(path,Path('聯電2303兩策略ML比較')/path.relative_to(out))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
with (HOME/'README.md').open('a',encoding='utf-8') as f:
    f.write('\n## 最新成果\n\n已完成；入口：[結果/index.html](結果/index.html)。詳見結果目錄的結果說明.md、metrics.csv、audit.json 與 html_qa.json。\n')
print(json.dumps({'report':str(out/'index.html'),'zip':str(archive),'main':main},ensure_ascii=False))
