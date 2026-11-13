"""Seal exact approved artifacts before first formal data access."""
import json,hashlib,sys,subprocess
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parents[1];R=H.parents[1]
if (H/'稽核/freeze.json').exists():raise SystemExit('Never overwrite a sealed formal protocol')
t=subprocess.run([sys.executable,str(H/'測試/preflight.py')],capture_output=True,text=True)
(H/'稽核/preflight_tests.txt').write_text(t.stdout+t.stderr,encoding='utf8')
if t.returncode:raise RuntimeError('Pre-consumption tests failed; no formal data access allowed')
c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
paths=list((H/'程式').glob('*.py'))+list((H/'模型').rglob('*'))+[H/'設定/experiment.json',H/'資料/4938_cash_dividends.json',R/'configs/holdout_policy.json',R/'configs/ml_data_policy.json',R/'trading_system/research_guard.py',R/'trading_system/backtest.py',R/'configs/technical_v2_candidate.json',R/'configs/ml_v1_research.json']
hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
m={'experiment_id':c['experiment_id'],'stock':'4938','created_before_data_access':datetime.now(timezone.utc).isoformat(),'authorization':c['authorization'],'config':c,'hashes':hashes,'state_at_freeze':'UNCONSUMED','general_locks_modified':False,'formal_2025h2_only':True,'2026_final_oos_remains_locked':True}
p=H/'稽核/freeze.json';p.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf8')
(H/'稽核/freeze_receipt.json').write_text(json.dumps({'freeze_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2),encoding='utf8')
(H/'README.md').write_text('和碩4938正式保留測試：已凍結，不得依2025下半年結果調參。2025上半年為已開放驗證，全年為混合已見與保留資料，不能稱全新樣本外。另列H2獨立25萬元測試判準。主動策略不加碼不延期，固定共同完整日+三模型合法預測；一般日期鎖保持原樣。首次存取之前先建立不可重置的consumed標記，失敗亦保留已使用狀態。',encoding='utf8')
print('SEALED',hashlib.sha256(p.read_bytes()).hexdigest(),flush=True)
