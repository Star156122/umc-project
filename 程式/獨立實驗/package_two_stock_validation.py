"""Package fixed two-stock validation results; no replay or performance tuning."""
import csv,json,zipfile,hashlib,html,statistics
from pathlib import Path
B=Path(__file__).resolve().parent;H=B/'科技金融雙股_2025上半年';H.mkdir(exist_ok=True)
labels={'BH':'買進持有（最多80%）','TECH':'純技術指標','RF':'RF＋技術','XGB':'XGBoost＋技術','GRU':'GRU＋技術'}
datasets=[];allrows=[];sections=[]
for folder in ['緯創3231_2025上半年','第一金2892_2025上半年']:
 root=B/folder;O=root/'結果'/json.loads((root/'結果/latest.json').read_text(encoding='utf8'))['folder']
 c=json.loads((root/'設定/experiment.json').read_text(encoding='utf8'))
 for path,count in [(O/'html_qa.json',11),(O/'五組買賣點圖/html_qa.json',6)]:
  qa=json.loads(path.read_text(encoding='utf8'));assert len(qa['pages'])==count and not qa['javascript_errors']
 with (O/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['scope']=='full' and float(r['slippage_each_side'])==.0005]
 assert len(rows)==5
 ta=json.loads((root/'模型/training_audit.json').read_text(encoding='utf8'))
 lines=[f"# {c['stock_name']}{c['stock']} 2025上半年固定五組回測",'',
  '2025/01/01～06/30，各組獨立25萬元；全部合法觀測日、買賣各0.05%滑價為主結果。共同覆蓋及零滑價僅補充。',
  f"模型訓練股票：{','.join(c['training_stocks'])}；2023～2024成熟訓練樣本{ta['training_rows']}筆。目標股票與2025資料均未參與訓練、預處理擬合或選參數。科技模型沿用華碩實驗已保存權重，金融模型另以固定金融五股資料建立。",'',
  '|策略|成本後報酬|淨損益|交易數|最大回撤|平均曝險|加碼|延期|','|---|---:|---:|---:|---:|---:|---:|---:|']
 table=[]
 for r in rows:
  lines.append(f"|{labels[r['variant']]}|{float(r['return'])*100:.2f}%|{float(r['net_pnl']):,.2f}|{r['trade_count']}|{float(r['max_drawdown'])*100:.2f}%|{float(r['avg_exposure'])*100:.2f}%|{r['adds']}|{r['holding_extensions']}|")
  table.append('<tr>'+''.join('<td>'+v+'</td>' for v in [labels[r['variant']],f"{float(r['return'])*100:.2f}%",f"{float(r['net_pnl']):,.2f}",r['trade_count'],f"{float(r['max_drawdown'])*100:.2f}%",f"{float(r['avg_exposure'])*100:.2f}%",r['adds']+'／'+r['holding_extensions']])+'</tr>')
 lines+=['','四個主動策略現金40%、固定保留初始20%（5萬元）、無1%風險額度；停損2.5%、ML BUY-only、一次加碼、162根期限及一次54根條件延期照前版。純技術所有階段均不使用ML；混合組使用各自模型。',
  '買進持有最多80%資金，不是保證投入80%；同樣受前20棒成交量1%限制，只在第一合法09:10開盤買一次，不補滿，持有至期末。所有成交為普通5分K近似零股數量研究，不保證實際撮合，且延後出場/跌停可能使損失不同。',
  '緯創期間內每股3.79927437元股利，6/3認列、6/27發放；第一金期間內沒有已識別除權息事件。只使用本期間及必要暖機，不使用鎖定行情。',
  '少於5筆交易證據不足；即使5筆以上仍只是單股開發驗證，不足以推論產業或模型泛化。較低虧損可能同時來自較低曝險及交易減少，不宣稱ML有因果改善。',
  '主結果延期0次；零滑價補充的第一金純技術獲准延期1次，勿混入主結果。',
  '各股6項範圍/訓練隔離/現金/時序/純技術測試通過，20組回放帳務與委託限制通過；11頁績效與6頁互動圖通過。原策略與原資料庫前後雜湊一致，舊結果保留，不依本次結果調參。']
 if c['stock']=='2892':lines.append('第一金買進持有受期初成交量限制，平均曝險約22.78%，報酬1.38%是25萬元帳戶報酬，不是股票本身半年漲幅。')
 (O/'結果說明.md').write_text('\n'.join(lines),encoding='utf8')
 (root/'README.md').write_text('\n'.join(lines[:4])+f'\n\n最新報告：結果/{O.name}/index.html\n圖表：結果/{O.name}/五組買賣點圖/index.html\n',encoding='utf8')
 rel=f'../{folder}/結果/{O.name}'
 sections.append(f'<section><h2>{c["stock_name"]} {c["stock"]}｜{c["industry"]}</h2><p><a href="{rel}/index.html">開啟五組完整報告</a>　<a href="{rel}/五組買賣點圖/index.html">查看5分K買進／加碼／賣出圖</a></p><div class="scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in ['策略','報酬','淨損益元','完成交易','最大回撤','平均曝險','加碼／延期'])+'</tr></thead><tbody>'+''.join(table)+'</tbody></table></div></section>')
 datasets.append({'folder':folder,'result':O.name,'stock':c['stock'],'training_rows':ta['training_rows'],'rows':rows})
 for r in rows:allrows.append({'stock':c['stock'],'stock_name':c['stock_name'],**r})
 if c['stock']=='3231':
  for model in ['random_forest/model.joblib','xgboost/model.joblib','gru/model.pt']:
   assert hashlib.sha256((root/'模型'/model).read_bytes()).hexdigest()==hashlib.sha256((B/'華碩2357_2025上半年/模型'/model).read_bytes()).hexdigest()
 report={'source_results_modified':False,'parameters_tuned_after_results':False,'main_slippage_each_side':.0005,'data_role':'validation_seen_period_new_stocks','stock':c['stock'],'training_excludes_target':True,'model_training_rows':ta['training_rows'],'html_pages_checked':17}
 (O/'delivery_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
stock_stats={}
for arm in ['TECH','RF','XGB','GRU']:
 rs=[float(r['return']) for r in allrows if r['variant']==arm]
 ds=[float(r['return'])-float(next(x for x in allrows if x['stock']==r['stock'] and x['variant']=='TECH')['return']) for r in allrows if r['variant']==arm]
 stock_stats[arm]={'mean_return':statistics.mean(rs),'median_return':statistics.median(rs),'mean_difference_vs_technical':statistics.mean(ds),'improved_stocks_vs_technical':sum(d>0 for d in ds),'profitable_stocks':sum(v>0 for v in rs),'not_a_portfolio':True}
(H/'雙股摘要.json').write_text(json.dumps({'stocks':datasets,'descriptive_two_stock_stats':stock_stats,'no_2303_in_sample':True,'clean_final_oos':False,'no_industry_ranking_claim':True},ensure_ascii=False,indent=2),encoding='utf8')
with (H/'主結果明細.csv').open('w',encoding='utf-8-sig',newline='') as f:
 fields=list(dict.fromkeys(k for r in allrows for k in r));w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(allrows)
page='''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>緯創與第一金｜2025上半年五組比較</title><style>body{background:#0b1220;color:#e7eef8;font:16px/1.7 system-ui,"Microsoft JhengHei",sans-serif;margin:0}main{max-width:1250px;margin:auto;padding:28px}h1{font-size:29px}h2{font-size:23px}section{background:#142033;border:1px solid #314258;border-radius:13px;padding:22px;margin:24px 0}a{color:#77d9cc}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}td,th{padding:12px;text-align:left;border-bottom:1px solid #314258}.note{padding:16px;border-left:3px solid #e2b873;background:#30291d}small{color:#b1c4d9}@media(max-width:600px){main{padding:12px}section{padding:12px}h1{font-size:24px}}</style><main><h1>緯創與第一金：五種策略回測</h1><p>2025/01/01～06/30 · 各股各組獨立25萬元 · 買賣各0.05%滑價，已扣費稅</p><p class="note">兩檔買進持有為正，主動策略都未轉正。不同策略曝險差異很大，不能只憑報酬判定模型能力。少於5筆證據不足；一股科技、一股金融不能推論整個產業。資料屬已允許的2025上半年Validation，不是Final OOS。</p>'''+''.join(sections)+'''<section><h2>先留意這三件事</h2><p>緯創買進持有雖賺15.21%，期間最大回撤30.63%；第一金買進持有受期初成交量上限限制，平均約22.78%資金投入，1.38%是帳戶報酬，不是滿倉股票報酬。</p><p>緯創沿用已保存的科技模型（原五股2023～2024）；第一金使用金融五股2023～2024模型，兩檔目標及2025資料均未用於訓練或調參。</p><p>主結果沒有獲准延期；第一金純技術只在零滑價補充對照獲准一次，請分開看。詳細報告可切換資料覆蓋與滑價。</p></section><p><a href="主結果明細.csv">下載十組主結果CSV</a> · <a href="雙股摘要.json">資料與描述統計</a></p><small>普通5分K近似零股數量模擬，不保證真實成交。原策略、資料庫、華碩及先前結果保留。</small></main></html>'''
(H/'index.html').write_text(page,encoding='utf8')
archive=H/'緯創第一金_2025上半年_五組完整報告.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 z.writestr('開啟報告.html','<meta charset="utf-8"><meta http-equiv="refresh" content="0;url=科技金融雙股_2025上半年/index.html">')
 for root in [H,*[B/d['folder'] for d in datasets]]:
  for p in root.rglob('*'):
   if p.is_file() and p.suffix not in ['.zip','.log','.pyc'] and '__pycache__' not in p.parts:z.write(p,p.relative_to(B))
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print('PACKAGED',archive.stat().st_size,'bytes');print(H/'index.html')
