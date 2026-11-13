"""Run frozen, isolated package experiment and export nine offline reports."""
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from engine import HOME,ROOT,TZ,load_inputs,simulate,validate
from prepare import dump,sha,register,ID


def write_csv(path,rows):
    if not rows:
        path.write_text('status\nNO_ROWS\n',encoding='utf-8-sig')
        return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})


def update_registry(status,decision,relative):
    path=ROOT/'research/registries/experiment_registry.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f); rows=list(r); fields=r.fieldnames
    for r in rows:
        if r['experiment_id']==ID:
            r.update(status=status,decision=decision,report_file=relative+'/index.html',result_file=relative)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def main():
    global ID
    config_name=sys.argv[sys.argv.index('--config')+1] if '--config' in sys.argv else 'experiment.json'
    if config_name not in ['experiment.json','experiment_odd_lot.json']:
        raise ValueError('Unsupported isolated experiment config')
    config_path=HOME/'設定'/config_name
    c=json.loads(config_path.read_text(encoding='utf-8'))
    ID=c['experiment_id']
    if config_name=='experiment.json':
        register()
    elif c.get('execution_data_mode')!='ORDINARY_OHLC_ODD_QUANTITY_PROXY' or not c.get('user_proxy_authorization'):
        raise ValueError('Odd-lot execution model must have explicit user selection')
    validate(c)
    stamp=datetime.now(TZ).strftime('%Y%m%dT%H%M%S')
    out=HOME/'結果'/stamp
    out.mkdir(parents=True,exist_ok=False)
    originals=[ROOT/'trading_system/backtest.py',ROOT/'trading_system/technical_v1.py',
               ROOT/'hybrid/core/risk_v1.py',ROOT/'configs/technical_v2_candidate.json',
               ROOT/c['old_db'],ROOT/c['revised_db'],ROOT/c['oof_path']]
    before={str(p.relative_to(ROOT)):sha(p) for p in originals}
    program_files=list((HOME/'程式').glob('*.py'))+list((HOME/'程式').glob('*.js'))+list((HOME/'測試').glob('*.py'))
    frozen={'created_before_simulation':datetime.now(TZ).isoformat(),'config':c,'original_hashes':before,
            'program_hashes':{p.name:sha(p) for p in program_files},'config_sha256':sha(config_path),
            'authorization':c.get('user_proxy_authorization','User: 開始吧; 2454 only; 2023–2024; 4 arms; no original modifications'),
            'cash_dividend_source_sha256':sha(HOME/'資料/2454_cash_dividends.json'),
            'data_role':'training_development_exposed','locked_data_used':False}
    dump(out/'freeze.json',frozen)
    dump(HOME/'設定/latest_freeze.json',frozen)
    tests=subprocess.run([sys.executable,str(HOME/'測試/test_experiment.py')],capture_output=True,text=True)
    (out/'test_results.txt').write_text(tests.stdout+tests.stderr,encoding='utf-8')
    if tests.returncode:
        raise RuntimeError('Correctness tests failed before market replay')
    update_registry('running','NOT_YET_EVALUATED',str(out.relative_to(ROOT)).replace('\\','/'))
    try:
        data=load_inputs(c)
        dump(out/'data_audit.json',data['audit'])
        outputs={}; all_metrics=[]; folds=[]
        for scope in ['full','common']:
            for slip in c['slippages']:
                for variant in c['variants']:
                    key=f'{variant}_{int(slip*10000)}_{scope}'
                    result=simulate(c,data,variant,slip,scope)
                    m=result['metrics'];all_metrics.append(m)
                    dump(out/(key+'.json'),result)
                    for kind in ['events','orders','trades','funnel','equity']:
                        write_csv(out/(key+'_'+kind+'.csv'),result[kind])
                    # HTML keeps daily equity and all events; bar chart uses shared observed OHLC.
                    curve=pd.DataFrame(result['equity'])
                    if len(curve):
                        result['equity']=curve.groupby('date').last().reset_index().to_dict('records')
                    outputs[key]=result
                    print('Completed',key,'events',m['candidate_events'],'trades',m['trade_count'],flush=True)
        # Same fixed windows across all arms; no pooled faux continuous ML equity curve.
        for fold in c['folds']:
            for variant in c['variants']:
                r=simulate(c,data,variant,.0005,'common',(fold['start'],fold['end']))
                folds.append({'fold':fold['id'],**r['metrics']})
            print('Completed paired fold',fold['id'],flush=True)
        after={str(p.relative_to(ROOT)):sha(p) for p in originals}
        if before!=after:
            raise AssertionError('Original file or source database changed')
        audit={'original_files_unchanged':True,'original_hashes_before':before,'original_hashes_after':after,
               'tests_passed':True,'locked_data_used':False,'model_retrained':False,
               'status':'PROXY_RESEARCH_ONLY' if c['lot_size']==1 else 'INSUFFICIENT',
               'notes':['No strategy adoption claim. One stock, exposed development data.',
                        'All full-source equity curves replay chronological raw bars; old signal indicators retain old common stream.',
                        'Ordinary OHLC approximates odd-quantity fills, explicitly selected by user; not verified odd-lot execution.',
                        'Causal cash dividend adjustment from issuer history, ex-day receivables and delayed cash payment; no personal tax model.',
                        'Exposure-normalized returns are descriptive only, not a tradable matched-risk strategy.',
                        'Technical-only full scope retains the full 2023–2024 warm-up period; ML applies only within frozen OOF windows.',
                        'Common scope restricts all four arms to legal ML coverage independent of predicted label.',
                        'No simulated marker is invented for a rejected event.']}
        write_csv(out/'metrics.csv',all_metrics);write_csv(out/'fold_metrics.csv',folds)
        dump(out/'audit.json',audit)
        benchmark=buy_hold_reference(c,data['bars'])
        dump(out/'benchmarks.json',benchmark)
        payload={'config':c,'data_audit':data['audit'],'audit':audit,'results':outputs,'folds':folds,
                 'benchmark':benchmark,'generated_at':datetime.now(TZ).isoformat(),
                 'bars':[[b['kbar_timestamp'],b['Open'],b['High'],b['Low'],b['Close'],b['Volume']*c['volume_to_shares']] for b in data['bars']]}
        # Reports are also research entry points: validate every chart timestamp before exporting.
        from trading_system.research_guard import assert_timestamps
        assert_timestamps(b[0] for b in payload['bars'])
        (out/'data.js').write_text('window.EXPERIMENT='+json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'),default=lambda v:v.item())+';',encoding='utf-8')
        build_html(out)
        relative=str(out.relative_to(ROOT)).replace('\\','/')
        update_registry('completed',audit['status'],relative)
        dataset=ROOT/'research/registries/dataset_registry.csv'
        with dataset.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);rows=list(reader);fields=reader.fieldnames
        for row in rows:
            if row['dataset_id']==ID:
                row['results_viewed']='true';row['report_location']=relative+'/index.html';row['result_location']=relative
        with dataset.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
        dump(out/'registry_snapshot.json',{'experiment_id':ID,'status':'completed','decision':audit['status'],'relative_path':relative})
        (HOME/'結果/index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+stamp+'/index.html"><a href="'+stamp+'/index.html">開啟最新獨立實驗報告</a>',encoding='utf-8')
        dump(HOME/'結果/latest.json',{'folder':stamp,'status':audit['status'],'report':'index.html'})
        print('REPORT',str(out/'index.html'),flush=True)
        print(json.dumps(all_metrics[:4],ensure_ascii=False,indent=2),flush=True)
    except Exception as exc:
        dump(out/'failure.json',{'error':str(exc),'time':datetime.now(TZ).isoformat(),'results_preserved':True})
        update_registry('failed','EXECUTION_FAILURE',str(out.relative_to(ROOT)).replace('\\','/'))
        raise


def buy_hold_reference(c,bars):
    # Nominal ordinary-share benchmark, excludes unverified cash dividends explicitly.
    from engine import fee,round_lot
    p=bars[0]['Open']*1.0005
    q=round_lot((c['initial_capital']-20)/(p*(1+c['fee_rate'])),c)
    end=bars[-1]['Close']*.9995
    value=c['initial_capital']-q*p-fee(q*p,c)+q*end-fee(q*end,c)-q*end*c['normal_sell_tax']
    return {'cash_return':0,'buy_hold_price_only_return':value/c['initial_capital']-1,'quantity':q,
            'status':'REFERENCE_ONLY_NOT_TOTAL_RETURN','note':'價格報酬參考，不含未核實股利與公司行動，不作策略優劣判準；首末觀測價、費稅及5bps滑價。'}


def build_html(out):
    shutil.copy2(HOME/'程式/report.js',out/'report.js')
    shutil.copy2(HOME/'程式/report.css',out/'report.css')
    for kind,variant,name in [('summary','ALL','index')]+[(kind,v,f'{v}_{kind}') for v in 'ABCD' for kind in ['funnel','analysis']]:
        title='四組總比較' if kind=='summary' else variant+(' 篩選漏斗' if kind=='funnel' else ' 回測分析')
        html='''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>聯發科 BUY 獨立實驗 · TITLE</title><link rel="stylesheet" href="report.css"></head><body data-kind="KIND" data-variant="VARIANT"><main><p>正在載入離線報告…</p></main><script src="data.js"></script><script src="report.js"></script></body></html>'''
        (out/(name+'.html')).write_text(html.replace('TITLE',title).replace('KIND',kind).replace('VARIANT',variant),encoding='utf-8')


if __name__=='__main__':
    if '--execute' not in sys.argv:
        raise SystemExit('Use --execute for the authorized frozen experiment; no implicit market replay.')
    main()
