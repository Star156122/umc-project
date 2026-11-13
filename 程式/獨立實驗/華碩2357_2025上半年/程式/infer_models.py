"""Inference-only ASUS features; no 2025 labels or fitting."""
import json,sqlite3,sys
from pathlib import Path
from collections import Counter
import numpy as np,pandas as pd,joblib
H=Path(__file__).resolve().parents[1];R=H.parents[1];sys.path.insert(0,str(R))
from ml.trading_pipeline import _intraday_features
from ml.research_features import build_daily_stock_features
from ml.ml_v1_sequence import select_exact_sequence
from ml.models import build_gru_multiclass_model
from trading_system.research_guard import assert_timestamps,assert_development_period
research=json.loads((R/'configs/ml_v1_research.json').read_text(encoding='utf8'))
audit=json.loads((H/'模型/training_audit.json').read_text(encoding='utf8'))
assert audit['training_target_max']<='2024-12-31' and not audit['asus_in_training']
with sqlite3.connect((H/'資料/2357_history.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as d:
 raw=pd.read_sql_query('SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars ORDER BY kbar_timestamp',d)
assert_timestamps(raw.kbar_timestamp)
raw['datetime']=pd.to_datetime(raw.kbar_timestamp,unit='s',utc=True).dt.tz_convert('Asia/Taipei')
raw=raw[(raw.datetime.dt.dayofweek<5)&(raw.datetime.dt.strftime('%H:%M')>='09:00')&(raw.datetime.dt.strftime('%H:%M')<='13:30')].copy()
raw['trade_date']=raw.datetime.dt.date
features=research['feature_policy']['core_features'];contexts=research['feature_policy']['gru']['auxiliary_context']
featured,cleaning=_intraday_features(raw,features);daily=build_daily_stock_features(featured)
daily['day']=pd.to_datetime(daily.trade_date).dt.date.astype(str)
context_map={r.day:np.array([getattr(r,k) for k in contexts],dtype=np.float32) for r in daily.itertuples()}
calendar=[r['date'] for r in json.loads((H/'資料/index_daily.json').read_text(encoding='utf8'))]
xs=[];cs=[];meta=[];excluded=Counter()
for day,group in featured.groupby('trade_date',sort=True):
 day=str(day)
 if day not in calendar:continue
 i=calendar.index(day)
 if i+1>=len(calendar):continue
 entry=calendar[i+1]
 if not '2025-01-01'<=entry<='2025-06-30':continue
 sequence,state=select_exact_sequence(group)
 if sequence is None:excluded['missing_exact_sequence']+=1;continue
 x=sequence[features].to_numpy(dtype=np.float32);ctx=context_map.get(day)
 if not np.isfinite(x).all():excluded['nonfinite_features']+=1;continue
 if ctx is None or not np.isfinite(ctx).all():excluded['missing_context']+=1;continue
 xs.append(x);cs.append(ctx);meta.append((day,entry))
if not xs:raise RuntimeError('No valid ASUS inference sequences')
X=np.stack(xs);context=np.stack(cs);outputs={}
for name in ['random_forest','xgboost','gru']:
 folder=H/'模型'/name;scaler=np.load(folder/'scaler.npz');xx=((X-scaler['mean'])/scaler['scale']).astype(np.float32)
 if name=='gru':
  import torch
  ctx=((context-scaler['context_mean'])/scaler['context_scale']).astype(np.float32)
  model=build_gru_multiclass_model(24,research['models']['gru'],len(contexts))
  model.load_state_dict(torch.load(folder/'model.pt',map_location='cpu',weights_only=True));model.eval()
  with torch.no_grad():prob=torch.softmax(model(torch.from_numpy(xx),torch.from_numpy(ctx)),dim=1).numpy()
 else:model=joblib.load(folder/'model.joblib');prob=model.predict_proba(xx.reshape(len(xx),-1))
 assert prob.shape==(len(meta),3) and np.isfinite(prob).all()
 rows=[]
 for (day,entry),p in zip(meta,prob):
  assert_development_period(day,day);assert_development_period(entry,entry)
  rows.append({'stock_code':'2357','model':name,'signal_date':day,'entry_date':entry,
   'decision_time':day+'T23:59:59+08:00','prediction':('SELL','HOLD','BUY')[int(np.argmax(p))],
   'prob_sell':float(p[0]),'prob_hold':float(p[1]),'prob_buy':float(p[2]),
   'training_target_max':audit['training_target_max'],'data_role':'validation',
   'prediction_source':'fixed_2023_2024_new_stock_inference'})
 outputs[name]=rows;print('INFERENCE',name,len(rows),dict(Counter(r['prediction'] for r in rows)),flush=True)
(H/'資料/predictions.json').write_text(json.dumps(outputs,ensure_ascii=False,indent=2),encoding='utf8')
(H/'資料/inference_audit.json').write_text(json.dumps({'inference_only':True,'labels_created':False,'features':features,'context':contexts,'cleaning':cleaning,'eligible_rows':len(meta),'exclusions':dict(excluded),'decision_time_note':'Use end-of-signal-day availability, conservatively after feature computation/model fitting; always before next-session candidates.','same_feature_schema_as_training':True},ensure_ascii=False,indent=2),encoding='utf8')
