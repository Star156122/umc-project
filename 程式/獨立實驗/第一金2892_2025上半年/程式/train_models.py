"""Rebuild fixed V1 models using five peers' mature 2023-2024 labels only."""
import json,sys,hashlib
from pathlib import Path
import numpy as np
import joblib
H=Path(__file__).resolve().parents[1];R=H.parents[1];sys.path.insert(0,str(R))
from scripts import run_ml_v1_phase_b as phase_b
from scripts.run_ml_industry_generalization import make_scaled_data
from ml.models import fit_multiclass_model
from trading_system.research_guard import assert_development_period
assert_development_period('2023-01-01','2024-12-31')
c=json.loads((H/'設定/experiment.json').read_text(encoding='utf8'))
phase,research,_=phase_b.load_phase_b_config();base,input_audit=phase_b.build_raw_inputs(phase,research)
meta=base['meta'];mask=meta.stock_code.astype(str).isin(c['training_stocks'])&(meta.signal_date>='2023-01-01')&(meta.target_date<='2024-12-31')
idx=np.flatnonzero(mask.to_numpy());assert len(idx)>0 and '2892' not in set(meta.iloc[idx].stock_code.astype(str))
assert set(np.unique(base['labels']['fixed_1pct'][idx]))=={0,1,2}
raw={'X':base['X'],'y':base['labels']['fixed_1pct'],'meta':meta,'context':base['context'],'feature_names':base['feature_names'],'context_feature_names':base['context_feature_names']}
training_meta=meta.iloc[idx].copy();training_meta.to_csv(H/'模型/training_manifest.csv',index=False,encoding='utf-8-sig')
audit={'training_stocks':c['training_stocks'],'training_rows':len(idx),'training_signal_start':training_meta.signal_date.min(),'training_signal_end':training_meta.signal_date.max(),'training_target_max':training_meta.target_date.max(),'target_in_training':False,'validation_2025_used_for_training_or_selection':False,'parameters':research['models'],'models':{},'input_audit':input_audit}
for name in ['random_forest','xgboost','gru']:
 folder=H/'模型'/name
 if folder.exists():raise RuntimeError('Preserve models; do not silently retrain')
 folder.mkdir()
 data=make_scaled_data(raw,idx,{},include_context=name=='gru',stage='fixed_2023_2024_refit',audit_extra={'validation_used_for_selection':False})
 print('TRAINING',name,len(idx),flush=True)
 model,seconds,predict,extra=fit_multiclass_model(name,data,research['models'][name],fixed_epochs=10 if name=='gru' else None)
 np.savez(folder/'scaler.npz',mean=data.scaler_mean,scale=data.scaler_scale,
          context_mean=data.context_scaler_mean if name=='gru' else np.array([]),context_scale=data.context_scaler_scale if name=='gru' else np.array([]))
 if name=='gru':
  import torch
  torch.save(model.state_dict(),folder/'model.pt')
 else:joblib.dump(model,folder/'model.joblib')
 audit['models'][name]={'training_seconds':seconds,'extra':extra,'feature_names':base['feature_names'],'context_feature_names':base['context_feature_names'] if name=='gru' else [],'training_only_scaler':True,'fixed_gru_epochs':10 if name=='gru' else None}
 (H/'模型/training_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=lambda v:v.item()),encoding='utf8')
 print('TRAINED',name,seconds,flush=True)
print('TRAINING_COMPLETE',flush=True)
