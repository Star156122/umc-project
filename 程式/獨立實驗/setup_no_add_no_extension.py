"""Prepare a frozen rule revision only; no historical replay."""
import csv,json,hashlib
from pathlib import Path
R=Path(__file__).resolve().parents[1];B=R/'獨立實驗';H=B/'無加碼無延期_共同完整日'
if H.exists():raise RuntimeError('Preserve existing candidate')
for d in ['程式','設定','測試']:(H/d).mkdir(parents=True)
ID='buy-noadd-noextension-commoncoverage-20261114-exp001'
sources={'2357':'華碩2357_2025上半年','3231':'緯創3231_2025上半年','2892':'第一金2892_2025上半年'}
for stock,folder in sources.items():
 P=B/folder;c=json.loads((P/'設定/experiment.json').read_text(encoding='utf8'))
 c.update(experiment_id=ID+'-'+stock,revised_max_adds=0,allowed_scopes=['common'],
  status='IMPLEMENTED_NOT_REPLAYED',coverage_policy='Complete target sessions AND all three valid model predictions, independent of label; pure TECH no BUY requirement, hybrid own BUY only. Existing positions exit on full chronological observed stream.')
 c['holding_extension']['enabled']=False
 (H/f'設定/{stock}.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
 s=(P/'程式/engine.py').read_text(encoding='utf8')
 s=s.replace('from prepare import dump, sha', 'from prepare import dump, sha')
 s=s.replace("HOME = Path(__file__).resolve().parents[1]", "HOME = Path(__file__).resolve().parents[1]\nSOURCE_HOME = HOME.parent / "+repr(folder))
 s=s.replace("HOME/'資料/", "SOURCE_HOME/'資料/")
 s=s.replace("def simulate(c,data,variant,slip,scope='full',window=None):", "def simulate(c,data,variant,slip,scope='common',window=None):")
 s=s.replace("    assert_development_period(c['period']['start'], c['period']['end'])", "    assert_development_period(c['period']['start'], c['period']['end'])\n    if c.get('revised_max_adds')!=0 or c.get('holding_extension',{}).get('enabled') is not False or c.get('allowed_scopes')!=['common']:\n        raise ValueError('Frozen revision: no adds, no extension, common scope only')")
 s=s.replace("    revised=True", "    if scope!='common':raise ValueError('Only common full-session and three-model coverage is allowed')\n    revised=True")
 old="coverage={d for d in cal if ml_status(d,datetime.fromisoformat(d+'T09:00:00+08:00').timestamp(),data['ml'],cal) in ['BUY','HOLD','SELL']}"
 new="""lookups=data.get('ml_by_model',{})
        if set(lookups)!={'random_forest','xgboost','gru'}:
            raise ValueError('All three model lookups required; never silently fall back to one model')
        coverage=set.intersection(*[{d for d in cal if ml_status(d,datetime.fromisoformat(d+'T09:00:00+08:00').timestamp(),lookup,cal) in ['BUY','HOLD','SELL']} for lookup in lookups.values()])"""
 assert old in s;s=s.replace(old,new)
 (H/f'程式/engine_{stock}.py').write_text(s,encoding='utf8')
prepare=(B/'華碩2357_2025上半年/程式/prepare.py').read_text(encoding='utf8');(H/'程式/prepare.py').write_text(prepare,encoding='utf8')
for kind,key,row in [('experiment','experiment_id',dict(experiment_id=ID,strategy='fixed_cash40_reserve20',variant='NO_ADD_NO_EXTENSION_COMMON_3MODEL',test_start='2025-01-01',test_end='2025-06-30',dataset_role='validation_seen',stock_codes='2357|3231|2892',status='implementation_not_run',decision='NOT_BACKTESTED',holdout_used='false',notes='User requested remove adds and extension, require common complete sessions and all3 legal predictions. Existing cash/stop/ML and re-entry retained. Package change frozen before any new outcome; no replay authorized in this message. Previous results preserved.')),
 ('dataset','dataset_id',dict(dataset_id=ID,stock_code='2357|3231|2892',stock_name='華碩|緯創|第一金',industry='科技與金融',test_start='2025-01-01',test_end='2025-06-30',participated_in_development='true',results_viewed='false',influenced_strategy='false',current_classification='validation_seen',clean_validation_eligible='false',experiment_ids=ID,notes='Implementation only, no new replay; previous observed validation stock data reused read-only.'))]:
 path=R/f'research/registries/{kind}_registry.csv'
 with path.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
 assert not any(r[key]==ID for r in rows);rows.append({k:row.get(k,'') for k in fields})
 with path.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
(H/'README.md').write_text('''# 無加碼、無延期、固定共同完整日

本版已實作，尚未重新回測；適用近期華碩、緯創、第一金的相同半年設定，舊版及結果保留。

- 每段持倉只首次買進一次，禁止加碼；平倉後符合原再進場規則仍可建立新持倉。
- 取消條件延期；162根觀測5分K到期安排下一合法棒出場。停損、技術轉弱、資料缺口及期末出場繼續優先。成交限制可能延後執行，不能保證第162根立即賣出。
- 只用共同完整交易日，且當天RF、XGBoost、GRU都要有合法可得預測，才檢查新進場訊號；缺一個就排除，無單模型退回路徑。
- 三模型都有預測不等於三模型都要BUY。純技術不看預測內容；各混合組仍只看自己的BUY門檻。
- 非共同日不刪行情、不暫停既有部位停損與賣出；圖表及持有期限仍依原始完整時序。
- 維持初始25萬元、首次含費最多現金40%、保留初始20%=5萬元、不使用1%風險額度、2.5%停損、12:30截止、流動性限制與原再進場規則。
- 買進持有仍作完整半年基準，不因ML日期刪除中間行情；最多80%資金並受成交量限制。前端與原策略不改。

這是規則變更，不是修復舊結果；不能把前次結果當成本版績效。所有策略回放必須明確提供三模型lookup並使用scope=common。
''',encoding='utf8')
print('CREATED',H)
