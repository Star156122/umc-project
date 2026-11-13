"""Package verified offline reports; never replays prices or alters simulation."""
import json
import zipfile
from datetime import datetime
from pathlib import Path
from engine import HOME,TZ
from prepare import dump,sha

latest=json.loads((HOME/'結果/latest.json').read_text(encoding='utf-8'))
out=HOME/'結果'/latest['folder']
qa=json.loads((out/'html_qa.json').read_text(encoding='utf-8'))
assert len(qa['pages'])==9 and not qa['javascript_errors']
assert len(list(out.glob('*.html')))==9
dump(out/'delivery_manifest.json',{
    'packaged_at':datetime.now(TZ).isoformat(),
    'report_only_postprocessing':'Offline report delivery; see frozen configuration and explicit proxy authorization.',
    'report_assets':{p.name:sha(p) for p in out.iterdir() if p.suffix in ['.html','.js','.css']},
    'simulation_freeze':'freeze.json','quality_checks':'html_qa.json'})
target=HOME/'結果/聯發科2454_零股近似回測報告與明細.zip'
with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in out.rglob('*'):
        if p.is_file():
            z.write(p,Path('聯發科2454_獨立實驗報告')/p.relative_to(out))
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
print(json.dumps({'zip':str(target),'bytes':target.stat().st_size,'html_files':9},ensure_ascii=False))
