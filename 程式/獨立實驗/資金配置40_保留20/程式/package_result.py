"""Describe and package the completed frozen replay, without rerunning it."""
import csv,json,statistics,zipfile,hashlib
from pathlib import Path
H=Path(__file__).resolve().parents[1]
O=H/'結果'/json.loads((H/'結果/latest.json').read_text(encoding='utf8'))['folder']
qa=json.loads((O/'html_qa.json').read_text(encoding='utf8'))
assert len(qa['pages'])==11 and not qa['javascript_errors']
assert not json.loads((O/'五檔買賣點圖/html_qa.json').read_text(encoding='utf8'))['javascript_errors']
with (O/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if float(r['slippage_each_side'])==.0005]
comp=[r for r in json.loads((O/'previous_comparison.json').read_text(encoding='utf8')) if r['slippage_each_side']==.0005]
deltas=[r['return_difference'] for r in comp]
summary={'main_slippage_each_side':.0005,'mean_return':statistics.mean(float(r['return']) for r in rows),
 'median_return':statistics.median(float(r['return']) for r in rows),'mean_return_change':statistics.mean(deltas),
 'median_return_change':statistics.median(deltas),'median_change_excluding_2303':statistics.median(r['return_difference'] for r in comp if r['stock']!='2303'),
 'leave_one_out_change_medians':{s:statistics.median(r['return_difference'] for r in comp if r['stock']!=s) for s in [r['stock'] for r in comp]},
 'improved_stocks':sum(d>1e-12 for d in deltas),'unchanged_stocks':sum(abs(d)<=1e-12 for d in deltas),'worsened_stocks':sum(d<-1e-12 for d in deltas),
 'profitable_stocks':sum(float(r['return'])>0 for r in rows),'closed_trades':sum(int(r['trade_count']) for r in rows),
 'adds':sum(int(r['adds']) for r in rows),'extensions':sum(int(r['holding_extensions']) for r in rows),
 'conclusion':'DEVELOPMENT_NO_OBSERVED_IMPROVEMENT_INSUFFICIENT_TRADES','portfolio_aggregation':False,
 'exposure_comparison':'Paired average/max exposure disclosed; no tradable matched-exposure replay performed.'}
(O/'comparison_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
lines=['# 五股2024Q4：現金40%、初始保留20%、取消1%風險上限','',
'使用者授權：同樣五檔、2024/10/01～12/31，依新規則回測；初始各100萬元，期初空手，固定RF既有OOF不重訓。',
'固定比較上一版20261114T003636，沒有因結果修改或另選股票；兩組均用各邊0.05%滑價，零滑價另列。','',
'|股票|上版報酬|本版報酬|本版淨損益|交易數|最大回撤|加碼|延期|','|---|---:|---:|---:|---:|---:|---:|---:|']
for r in rows:
 old=next(c for c in comp if c['stock']==r['stock'])
 lines.append(f"|{r['stock_name']} {r['stock']}|{old['previous_return']*100:.2f}%|{float(r['return'])*100:.2f}%|{float(r['net_pnl']):,.2f}|{r['trade_count']}|{float(r['max_drawdown'])*100:.2f}%|{r['adds']}|{r['holding_extensions']}|")
lines+=['','三檔變差、兩檔相同、零檔改善；各股只有1～3筆交易，全部證據不足。五檔報酬平均約-0.88%，中位數約-0.43%，這是獨立帳戶描述統計，不是投資組合績效。',
'聯電首次買8539股，約399537.69元含費；11/7 11:05加碼5089股，約239912.17元含費，剩現金約360550.15元；11/8 10:55全部賣出。加碼買價47.073525高於賣價47.026475，且增加費稅，使整段由上版淨賺170.63轉成本版淨虧1408.68元。',
'台積電及聯發科主要因投入股數增加而放大虧損；瑞昱、聯詠仍受成交量限制，結果未變。不能把部位增加當作進出場能力提升。平均與最大曝險對照見HTML與CSV；未執行可交易的相近曝險匹配回放。',
'本次沒有持倉到原162根期限，故延期判斷0次、獲准0次；不能宣稱延期有效或無效。加碼只1次，不能推論普遍效果。',
'每筆BUY/ADD含費支出<=當時現金40%，成交後現金>=初始20%保留金，已逐單驗證；2.5%停損不代表保證最大損失。',
'共44項母版／延期／資金配置測試通過；10組回放帳務、日期、股數、合法交易時間與現金限制通過；11頁績效及6頁互動圖表均無JavaScript錯誤。',
'本次仍為普通5分K近似零股數量的研究模擬，沒有委託簿，不保證真實成交、排隊或部分成交。來源資料庫、原策略、先前股票結果及母版前後雜湊一致；未使用鎖定資料。',
'HTML總覽：index.html；各股篩選/分析頁各一份。五檔買賣點圖/index.html 可看中文買進、加碼、賣出標記並縮放。股數明細在orders.csv，延期判斷在holding_events.csv。']
(O/'結果說明.md').write_text('\n'.join(lines),encoding='utf8')
p=H/'README.md';text=p.read_text(encoding='utf8').replace('尚未重跑歷史回測。','已依使用者後續授權完成五股2024Q4回測。')
text+=f'\n\n最新結果：結果/{O.name}/index.html\n本次44項測試通過，8筆交易、1次加碼、0次延期；三檔較上版差、兩檔相同，未依績效再調整。\n'
p.write_text(text,encoding='utf8')
manifest={'report_folder':O.name,'strategy_results_changed_after_replay':False,'qa_pages':17,
 'program_hashes_at_delivery':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (H/'程式').iterdir() if p.is_file()}}
(O/'delivery_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
archive=H/'結果/五股2024Q4_資金配置40保留20_回測報告.zip'
files=[p for p in H.rglob('*') if p.is_file() and p.suffix!='.zip' and '__pycache__' not in p.parts]
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in files:z.write(p,p.relative_to(H))
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print(json.dumps(summary,ensure_ascii=False));print('ZIP_BYTES',archive.stat().st_size)
