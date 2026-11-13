"""Package the inspected report without rerunning or changing strategy results."""
import csv,json,zipfile,hashlib
from pathlib import Path
H=Path(__file__).resolve().parents[1]
latest=json.loads((H/'結果/latest.json').read_text(encoding='utf8'));O=H/'結果'/latest['folder']
qa=json.loads((O/'html_qa.json').read_text(encoding='utf8'))
assert len(qa['pages'])==11 and not qa['javascript_errors']
with (O/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['slippage_each_side']=='0.0005']
assert len(rows)==5
lines=['# 科技五股 2024Q4：修改策略＋相同 ML','',
'期間：2024/10/01～12/31。各檔獨立100萬元、期初空手，不是共用資金組合。',
'事前固定聯電2303、台積電2330、瑞昱2379、聯發科2454、聯詠3034；便利樣本，不是隨機抽樣。',
'相同母版策略與RF OOF，沒有重訓或因績效調參。暖機從2024/9/16開始，只有10～12月計績效。',
'主結果買賣各0.05%滑價；零滑價僅追溯。普通5分K估計零股數量成交，未驗證真實零股價差、排隊與部分成交。','',
'|股票|成本後報酬率|淨損益元|已平倉筆數|勝率|最大回撤|', '|---|---:|---:|---:|---:|---:|']
for r in rows:lines.append(f"|{r['stock_name']} {r['stock']}|{float(r['return'])*100:.2f}%|{float(r['net_pnl']):,.2f}|{r['trade_count']}|{float(r['win_rate'])*100:.0f}%|{float(r['max_drawdown'])*100:.2f}%|")
lines += ['', '五檔共8筆已平倉交易，只有聯電小幅獲利；每檔1～3筆均為證據不足。加碼事件全部為零，無法判定加碼效果。沒有原版對照，不宣稱改善或泛化成功。',
'HTML共11頁：總覽1頁、各股篩選與績效各1頁；K線含買賣標記、日期縮放、滑價切換及CSV。17項母版測試、獨立Q4範圍/原碼一致檢查、10組回放帳務檢查及11頁HTML檢查通過。',
'原策略、兩個來源資料庫、既有ML預測、聯發科與聯電歷史結果前後雜湊一致；未使用鎖定資料。',
'先前下載中止結果與ML使用區間誤清空結果保留在各自資料夾；後者標為無效，不當成策略零交易。修正為母版原有ML區間，無調參。詳設定/修正紀錄.md。',
'策略凍結後僅調整報告表格高度及提示外觀，未重跑或更動績效。',
'從本資料夾 index.html 開始；請保留data.js、report.js與report.css。']
(O/'結果說明.md').write_text('\n'.join(lines),encoding='utf8')
(H/'README.md').write_text('\n'.join(lines[:6])+f'\n\n最新有效報告：結果/{latest["folder"]}/index.html\n',encoding='utf8')
manifest={}
for p in (H/'程式').iterdir():
 if p.is_file():manifest[str(p.relative_to(H))]=hashlib.sha256(p.read_bytes()).hexdigest()
(O/'delivery_manifest.json').write_text(json.dumps({'report':str(O.relative_to(H)),'qa_passed':True,'post_replay_changes':'Report CSS only; execution results unchanged. Packaging utility added after replay.','program_hashes_at_delivery':manifest},ensure_ascii=False,indent=2),encoding='utf8')
archive=H/'結果/科技五股_2024Q4_修改策略加ML_報告與明細.zip'
files=[p for p in H.rglob('*') if p.is_file() and p!=archive and '__pycache__' not in p.parts and p.suffix!='.zip']
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in files:z.write(p,p.relative_to(H))
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print('PACKAGED',archive.stat().st_size,'bytes',len(files),'files')
