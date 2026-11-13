"""Package inspected validation results and reproducible model/data provenance."""
import json,csv,zipfile,hashlib
from pathlib import Path
H=Path(__file__).resolve().parents[1];O=H/'結果'/json.loads((H/'結果/latest.json').read_text(encoding='utf8'))['folder']
assert len(json.loads((O/'html_qa.json').read_text(encoding='utf8'))['pages'])==11
assert not json.loads((O/'html_qa.json').read_text(encoding='utf8'))['javascript_errors']
assert not json.loads((O/'五組買賣點圖/html_qa.json').read_text(encoding='utf8'))['javascript_errors']
with (O/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['scope']=='full' and float(r['slippage_each_side'])==.0005]
assert len(rows)==5
lines=['# 華碩2357：2025上半年五組固定規則回測','',
'2025/01/01～06/30，各組獨立25萬元，固定同一檔華碩；屬既有Validation期間的新股票研究，不是Final OOS。',
'RF/XGBoost/GRU因無匹配的已存V1模型檔，以原五檔2023～2024成熟標籤重新建模；訓練2256樣本，不含華碩或2025資料，預處理只fit訓練，GRU固定10 epochs，不用此次報酬選模型、門檻或參數。',
'主結果使用全部合法觀測日、各邊0.05%滑價；共同完整日及三模型合法覆蓋、零滑價皆為補充，不按較好結果挑主結果。','',
'|組別|報酬率|淨損益元|完成交易|勝率|最大回撤|平均曝險|加碼|延期|', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
for r in rows:
 win='單段基準不作統計' if r['variant']=='BH' else (f"{float(r['win_rate'])*100:.2f}%" if r['win_rate'] else '不適用')
 lines.append(f"|{r['stock_name']}|{float(r['return'])*100:.2f}%|{float(r['net_pnl']):,.2f}|{r['trade_count']}|{win}|{float(r['max_drawdown'])*100:.2f}%|{float(r['avg_exposure'])*100:.2f}%|{r['adds']}|{r['holding_extensions']}|")
lines += ['', '買進持有為期初80%資金上限，並非100%滿倉；與主動策略曝險不同。買進持有正報酬，但最大回撤31.31%，不能只看期末報酬。',
'主動組中GRU虧損最少，不代表模型較好：RF僅1筆、XGBoost/GRU各3筆，證據不足。純技術11筆、7次加碼，三個混合組各1次加碼；所有組延期為0。純技術首次/加碼/延期均不使用ML，混合組只要求各自模型合法BUY。',
'主動組每次含費最多使用當下現金40%、成交後保留初始20%=5萬元；無1%損失預算。保留2.5%停損、前20棒普通成交量1%上限、一次加碼、162棒到期和一次54棒條件延期；停損與技術出場優先，不下調停損，延期不加碼。',
'期初期末均使用同一套費用與0.05%滑價。普通K線近似零股數量成交，未驗證實際零股價差、排隊、限價、部分成交、停牌或跌停成交；同日新增部位不得當日賣、09:10以前不成交，可能延後停損。不能視為可保證執行的交易結果。',
'6/26每股34元現金股利按權利認列；買進持有應收10880元於期末尚未發放，已計入報酬與資產，不計入可用現金。公告的7/17付款日僅作metadata，不讀取7月行情。來源KGI除權息表與6/10公司公告交叉核對；公司英文表將7/2基準日寫在exclude dividend欄，不以其當成除息日。',
'實際觀測8581根含暖機普通5分K；半年圖6261根。部分場次有缺棒，技術240棒完整性遮蔽；ML必須完整11:35～13:30精確24棒，113個有效預測交易日，缺資料不補值。資料明細見data_audit與inference_audit。',
'原始資料庫及母版程式前後雜湊一致。模型、scaler、訓練樣本manifest、設定、原始隔離行情、官方來源、逐筆交易與漏斗均包含於交付包。沒有使用鎖定行情，也沒有因績效修改設定。',
'完成6項新版範圍/特徵來源/時序/資金/純技術延期測試；20組回放逐筆驗證PnL一致、合法時間、現金40%與保留金；11頁績效HTML與6頁互動圖表通過。初次買進持有報表有DataFrame欄位存取錯誤，未產生策略結果，修正存取寫法後依相同規則重跑，失敗紀錄保留。',
'總覽index.html；各組analysis與funnel頁；五組買賣點圖/index.html可縮放、拖曳並點選交易看中文標記。',
'此處只有一檔股票，跨股票改善數、中位數、排除2303等跨股票泛化指標不適用。模型與純技術比較揭露曝險，但未執行另一路可交易的相近曝險配對回測，不宣稱等風險勝出。']
(O/'結果說明.md').write_text('\n'.join(lines),encoding='utf8')
summary={'stock':'2357','period':{'start':'2025-01-01','end':'2025-06-30'},'capital_per_arm':250000,'main_scope':'full','main_slippage_each_side':.0005,'validation_period_seen':True,'new_stock_excluded_from_training':True,'training_samples':2256,'training_target_max':'2024-12-31','parameters_tuned_on_result':False,'conclusion':'BH_POSITIVE_HIGH_DRAWDOWN_ACTIVE_NEGATIVE_ML_SAMPLES_INSUFFICIENT','independent_portfolios_not_pooled':True,'rows':rows}
(O/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
(H/'README.md').write_text('\n'.join(lines[:5])+f'\n\n最新有效報告：結果/{O.name}/index.html\n圖表：結果/{O.name}/五組買賣點圖/index.html\n保留下載中止與首輪報表失敗記錄，不作模型選擇。\n',encoding='utf8')
manifest={'latest_report':O.name,'html_pages_checked':17,'synthetic_tests_passed':6,'strategy_results_changed_after_replay':False,'program_hashes_at_delivery':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (H/'程式').glob('*.py')}}
(O/'delivery_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
archive=H/'結果/華碩2357_2025上半年_五組回測完整報告.zip'
files=[p for p in H.rglob('*') if p.is_file() and p.suffix not in ['.zip','.log','.pyc'] and '__pycache__' not in p.parts]
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in files:z.write(p,p.relative_to(H))
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print('PACKAGE',len(files),'files',archive.stat().st_size,'bytes')
