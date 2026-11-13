"""Single preregistered authorized formal run; not an unlock for general tools."""
import hashlib,json,sys
from pathlib import Path
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
H=Path(__file__).resolve().parents[1];R=H.parents[1]
_lease_checked=False
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify(require_consumed=True):
 f=H/'稽核/freeze.json';receipt=json.loads((H/'稽核/freeze_receipt.json').read_text(encoding='utf8'))
 if sha(f)!=receipt['freeze_sha256']:raise ValueError('Formal freeze changed')
 m=json.loads(f.read_text(encoding='utf8'))
 if m['experiment_id']!='pegatron4938-formal-2025h2-20261114-exp001' or m['stock']!='4938':raise ValueError('Wrong formal identity')
 for p,digest in m['hashes'].items():
  if sha(p)!=digest:raise ValueError('Frozen source/model/config changed: '+str(Path(p).name))
 if require_consumed:
  consumed=json.loads((H/'稽核/consumed.json').read_text(encoding='utf8'))
  if consumed['freeze_sha256']!=receipt['freeze_sha256'] or consumed['state']!='CONSUMED':raise ValueError('No consumed formal access lease')
 return m
def assert_period(start,end):
 global _lease_checked
 first,last=date.fromisoformat(str(start)[:10]),date.fromisoformat(str(end)[:10])
 if not date(2024,11,1)<=first<=last<=date(2025,12,31):raise ValueError('Outside exact authorized4938 warmup+2025 scope; 2026 and other dates locked')
 if not _lease_checked:verify();_lease_checked=True
def assert_timestamps(stamps):
 stamps=list(stamps)
 if stamps:assert_period(datetime.fromtimestamp(min(stamps),ZoneInfo('Asia/Taipei')).date(),datetime.fromtimestamp(max(stamps),ZoneInfo('Asia/Taipei')).date())
def consume():
 m=verify(False);receipt=json.loads((H/'稽核/freeze_receipt.json').read_text(encoding='utf8'))
 with (H/'稽核/consumed.json').open('x',encoding='utf8') as f:json.dump({'state':'CONSUMED','freeze_sha256':receipt['freeze_sha256'],'first_access_authorized_at':datetime.now(timezone.utc).isoformat(),'authorization':m['authorization'],'never_reset_even_on_failure':True},f,ensure_ascii=False,indent=2)
 # Immediately record consumption before any network/history request.
 import csv
 for kind,key in [('experiment','experiment_id'),('dataset','dataset_id')]:
  p=R/f'research/registries/{kind}_registry.csv'
  with p.open(encoding='utf-8-sig',newline='') as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
  for row in rows:
   if row[key]==m['experiment_id']:
    if kind=='experiment':row.update(status='formal_consumed',holdout_used='true')
    else:row['current_classification']='formal_additional_holdout_consumed'
  with p.open('w',encoding='utf8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
