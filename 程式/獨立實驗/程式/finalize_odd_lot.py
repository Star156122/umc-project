"""Summarize the selected fixed experiment; clean user-unwanted superseded outputs."""
import csv
import json
import shutil
from pathlib import Path
from engine import HOME,ROOT
from prepare import dump

latest=json.loads((HOME/'結果/latest.json').read_text(encoding='utf-8'))
out=(HOME/'結果'/latest['folder']).resolve()
assert (out/'html_qa.json').exists()
qa=json.loads((out/'html_qa.json').read_text(encoding='utf-8'))
assert len(qa['pages'])==9 and not qa['javascript_errors']
with (out/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:
    metrics=list(csv.DictReader(f))

labels={'A':'原策略','B':'修訂策略','C':'原策略＋RF BUY','D':'修訂策略＋RF BUY'}
def percent(v):
    return '—' if v in ['',None] else f'{float(v)*100:.2f}%'

text=['# 聯發科零股數量近似回測：最終結果','',
      '2023/01/01～2024/12/31；初始資金每組 100 萬；每段 1% 風險；每邊 0.05% 滑價為主結果。',
      '', '**這次已能產生交易，但不等於修改策略改善獲利。** 普通交易 K 線只是零股數量成交的近似，不是已核實的零股行情。',
      '', '## 主對照：共同日期與合法 RF 覆蓋','',
      '| 版本 | 完整交易 | 淨報酬 | 淨損益 | 勝率 | 最大回撤 | 平均曝險 |',
      '|---|---:|---:|---:|---:|---:|---:|']
main={}
for v in 'ABCD':
    m=next(m for m in metrics if m['variant']==v and m['scope']=='common' and float(m['slippage_each_side'])==.0005)
    main[v]=m
    text.append(f"| {v} {labels[v]} | {m['trade_count']} | {percent(m['return'])} | {float(m['net_pnl']):,.2f} 元 | {percent(m['win_rate'])} | {percent(m['max_drawdown'])} | {percent(m['avg_exposure'])} |")
text+=['','比較重點：']
for old,new in [('A','B'),('C','D')]:
    x,y=main[old],main[new]
    diff=float(y['return'])-float(x['return'])
    dd=float(y['max_drawdown'])-float(x['max_drawdown'])
    text.append(f"- {new} 對 {old}：報酬差 {diff*100:+.2f} 個百分點；最大回撤差 {dd*100:+.2f} 個百分點。回撤差為負代表下降。")
text+=['','## 各版本全日期：補充，不與主對照混接','',
       '| 版本 | 完整交易 | 淨報酬 | 最大回撤 |','|---|---:|---:|---:|']
for v in 'ABCD':
    m=next(m for m in metrics if m['variant']==v and m['scope']=='full' and float(m['slippage_each_side'])==.0005)
    text.append(f"| {v} {labels[v]} | {m['trade_count']} | {percent(m['return'])} | {percent(m['max_drawdown'])} |")
text+=['','## 必須一起看的限制','',
       '- 四組都允許按股計量，不再強迫至少一張。原策略程式及原資料庫保持不變。',
       '- 只有聯發科、已曝光開發資料，不能推論所有股票皆有效。',
       '- 零股不適用當沖。當日買進後的出場訊號須延後；09:10 前也不能捏造零股成交。這可能讓實際損失超出原停損估計。',
       '- 股數來自 1% 預算，但真實跳空、延遲與流動性損失不受該估計保證。',
       '- 普通市場 K 線與前均量只能作代理，沒有模擬零股限價排隊、撮合與部分成交。',
       '- 股利依公司官方三次歷史現金分配，因果調整指標與保護線；應收與可用現金分開。',
       '- 修訂版與原版的平均曝險不同；報酬／平均曝險僅為描述比值，不是同曝險策略重跑。',
       '- 加碼次數與首次進場分開揭露，不能把加碼當成獨立獲利交易，也不從單股少量事件宣稱加碼有效；詳見 metrics.csv 的 adds 欄。',
       '- 16 項正確性測試與九份 HTML 互動／帳務檢查通過；過程中的成交時段及期限計數錯誤已修正，作廢輸出不作結論，未根據績效調參。',
       '- 所有原策略程式、兩個來源資料庫及既有 OOF 檔案前後 SHA256 一致。',
       '', '開啟 index.html，預設為共同日期主對照；各組有獨立漏斗及含估計買賣點的 K 線分析。',
       '零滑價結果可在同一頁切換。CSV、逐筆紀錄及稽核檔都保留於此結果目錄。']
(out/'結果說明.md').write_text('\n'.join(text)+'\n',encoding='utf-8')

readme=HOME/'README.md'
s=readme.read_text(encoding='utf-8')
lines=s.splitlines()
for i,line in enumerate(lines):
    if line.startswith('狀態：'):
        lines[i]='狀態：已完成零股數量近似回測及九份 HTML（PROXY_RESEARCH_ONLY）。上一輪整股實驗成果依使用者指示刪除；策略程式及合法輸入保留。新版使用普通交易 K 線近似零股成交，不能視為實盤績效。'
    if line.startswith('最新成果入口：'):
        lines[i]='最新成果入口：[結果/index.html](結果/index.html)。執行規格以設定/experiment_odd_lot.json、設定/零股近似版事前定稿.md 及成功結果 freeze.json 為準。'
    if line.startswith('附存兩份早期事前文件'):
        lines[i]='附存兩份早期文件只供規劃追溯；最新零股版依使用者選擇採近似研究，保留 100 萬資金、1% 預算與 RF，不調參。完整帳務、成交限制及最終結果見最新成果目錄，舊版未驗證數字不再使用。'
readme.write_text('\n'.join(lines)+'\n',encoding='utf-8')

# Explicit user requested removal of previous experiment outputs. Only superseded local
# temporary runs are removed; correction records retained without old performance tables.
base=(HOME/'結果').resolve()
removed=[]
for name in ['20261113T223306','20261113T224329']:
    candidate=(base/name).resolve()
    assert candidate.parent==base and candidate!=out
    if candidate.exists():
        assert (candidate/'superseded.json').exists()
        reason=json.loads((candidate/'superseded.json').read_text(encoding='utf-8'))
        dump(HOME/'設定'/f'作廢修正紀錄_{name}.json',reason)
        shutil.rmtree(candidate)
        removed.append(name)
dump(out/'previous_outputs_cleanup.json',{'user_requested':True,'removed_superseded_temporary_outputs':removed,
     'original_reports_outside_isolated_experiment_untouched':True})

for kind in ['experiment','dataset']:
    path=ROOT/'research/registries'/f'{kind}_registry.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);rows=list(reader);fields=reader.fieldnames
    key='experiment_id' if kind=='experiment' else 'dataset_id'
    for row in rows:
        if row[key]=='isolated-buy-2454-20261113-exp002-odd-lot':
            row['notes']+='; User explicitly selected ordinary OHLC proxy; known no-same-day odd exits and >=09:10 enforced; observed-bar timeout corrected; no tuning; report=PROXY_RESEARCH_ONLY.'
            dump(HOME/'設定'/f'odd_lot_{kind}_registration.json',row)
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
print(json.dumps({'report':str(out/'index.html'),'old_outputs_removed':removed,
                  'main_comparison':[{k:main[v][k] for k in ['variant','trade_count','return','max_drawdown','net_pnl']} for v in 'ABCD']},ensure_ascii=False))
