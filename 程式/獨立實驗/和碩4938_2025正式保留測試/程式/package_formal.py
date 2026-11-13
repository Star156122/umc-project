"""Finalize consumed formal results; no new market access or strategy replay."""
import json,csv,zipfile,hashlib,sys
from pathlib import Path
H=Path(__file__).resolve().parents[1];R=H.parents[1];sys.path.insert(0,str(H/'程式'))
from formal_guard import verify
verify();c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'));O=H/'結果'/json.loads((H/'結果/latest.json').read_text(encoding='utf8'))['folder']
qa=json.loads((O/'html_qa.json').read_text(encoding='utf8'));assert len(qa['pages'])==25 and not qa['javascript_errors']
with (O/'metrics.csv').open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if float(r['slippage_each_side'])==.0005]
periods={'Q1':'2025/01～03（三個月）','H1':'2025/01～06（六個月）','YEAR':'2025/01～12（一年；混合已見與保留）','H2':'2025/07～12（正式保留判定）'}
labels={'BH':'買進持有（最多80%）','TECH':'純技術','RF':'RF＋技術','XGB':'XGBoost＋技術','GRU':'GRU＋技術'}
lines=['# 和碩4938：多期間回測及正式2025下半年保留測試','',
'使用者明確選擇「申請使用2025全年作正式最終保留測試，先凍結規格再驗證」。先凍結股、模型、交易程式、參數、費稅、期間與門檻，8項事前測試通過，才建立consumed標記並發起第一次行情請求。',
'2025上半年為已開放Validation，只有下半年依本次授權正式使用。全年不是全新未見樣本。2026/7起Final OOS未解鎖；原一般日期鎖及政策檔完整保留，無環境變數或普通回測解鎖。',
'每個期間每組各25萬元、空手開始，不承接其他期間的部位，不合併或相加期間報酬。主動策略不加碼、不延期，只允許完整交易日且三個模型皆有合法預測的共同日期進場；模型不必都BUY，混合組看各自BUY，純技術不看預測內容。',
'現金40%、保留初始20%=5萬元、無1%風險額度，停損2.5%、162根期限、12:30進場截止、前20棒成交量1%限量與原再進場規則保留。非共同日仍處理持倉出場。普通K線近似零股成交，非實際零股撮合或保證成交。',
'模型沿用凍結科技五股2023～2024成熟樣本模型，無和碩或2025年訓練、調參或門檻搜尋；相同模型用於全部期間。主結果買賣各0.05%滑價，零滑價只補充。','']
for w,name in periods.items():
 lines += ['## '+name,'','|策略|報酬率|淨損益元|交易數|最大回撤|平均曝險|','|---|---:|---:|---:|---:|---:|']
 for r in rows:
  if r['window']==w:lines.append(f"|{labels[r['variant']]}|{float(r['return'])*100:.2f}%|{float(r['net_pnl']):,.2f}|{r['trade_count']}|{float(r['max_drawdown'])*100:.2f}%|{float(r['avg_exposure'])*100:.2f}%|")
lines+=['','正式H2固定門檻：各ML成本後報酬須優於純技術、最大回撤不惡化，且至少5筆。RF與XGBoost未通過，GRU僅通過本次固定門檻、5筆；這不是泛化、部署或產業適配成功。不能拿結果回頭改本版再測同一保留資料。',
'前三個月GRU零交易，不當成獲利或風險優勢；上半年只有1筆，證據不足。全年GRU6筆且平均曝險很低，報酬差異不能視為同曝險比較；未另外執行可交易相近曝險回放。',
'買進持有最多80%資金，仍受期初成交量限制，不保證買滿80%；6/17每股4.50282529元現金股利認列應收，7/9才變現，上半年應收不當可用現金，全年在付款日變現，單獨H2期初沒有6月持股，不補發股利。',
'各筆資金限制、股數及PnL守恆通過；所有加碼與延期次數均0。25頁HTML/互動圖與手機預覽通過，無JavaScript錯誤。凍結的交易與模型來源於執行前後皆驗證一致；後續只加入結果呈現與打包，沒有變更策略。',
'資料狀態已consumed，包含失敗也永不重置為unseen；將保留所有本次結果。若日後因結果改策略，須另找未使用驗證資料。']
(O/'結果說明.md').write_text('\n'.join(lines),encoding='utf8')
(H/'README.md').write_text('\n'.join(lines[:8])+f'\n\n最新報告：結果/{O.name}/index.html\n資料狀態：CONSUMED，正式測試完成，不可重置或依此結果再調參重測。\n',encoding='utf8')
for kind,key in [('experiment','experiment_id'),('dataset','dataset_id')]:
 p=R/f'research/registries/{kind}_registry.csv'
 with p.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;registry=list(r)
 for row in registry:
  if row[key]==c['experiment_id']:
   if kind=='experiment':row.update(status='formal_completed_consumed',decision='RF_FAIL_XGB_FAIL_GRU_PASS_FIXED_SINGLE_STOCK_ONLY',holdout_used='true',report_file=str((O/'index.html').relative_to(R)),result_file=str(O.relative_to(R)))
   else:row.update(results_viewed='true',current_classification='formal_additional_holdout_consumed',report_location=str((O/'index.html').relative_to(R)),result_location=str(O.relative_to(R)),clean_validation_eligible='false')
 with p.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(registry)
(H/'稽核/completed.json').write_text(json.dumps({'state':'COMPLETED_CONSUMED','replay_count':1,'code_or_model_changed_after_holdout_access':False,'report_folder':O.name,'never_reset':True},ensure_ascii=False,indent=2),encoding='utf8')
archive=H/'結果/和碩4938_多期間與正式保留測試_完整報告.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in H.rglob('*'):
  if p.is_file() and p.suffix not in ['.zip','.log','.pyc'] and '__pycache__' not in p.parts:z.write(p,p.relative_to(H))
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
verify();print('FORMAL_PACKAGE',archive.stat().st_size,'bytes',flush=True)
