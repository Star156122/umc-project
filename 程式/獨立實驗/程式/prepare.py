"""Register and prepare bounded 2023–2024 inputs. Never runs a strategy."""
import csv
import hashlib
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parent
sys.path.insert(0, str(ROOT))
from trading_system.research_guard import assert_development_period, assert_timestamps

TZ = ZoneInfo('Asia/Taipei')
ID = 'isolated-buy-2454-20261113-exp001'


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False,
                               default=lambda v:v.item()), encoding='utf-8')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def register():
    assert_development_period('2023-01-01', '2024-12-31')
    for kind, key, row in [
        ('experiment', 'experiment_id', dict(experiment_id=ID, strategy='technical_v2b_buy_isolated',
          variant='A_B_C_D_PACKAGE_COMPARISON', test_start='2023-01-01', test_end='2024-12-31',
          dataset_role='training_development_exposed', stock_codes='2454', status='preregistered',
          decision='NOT_RUN', report_file='獨立實驗/結果/index.html', result_file='獨立實驗/結果',
          holdout_used='false', notes='User authorized isolated package comparison 2026-11-13; frozen original unchanged; no tuning; 1m independent capital; RF existing OOF; 5bps each side plus zero; hypothesis improved rule coherence and net outcome; risk increased exposure and fewer round-lot trades; one package change; single-stock exploratory only.')),
        ('dataset', 'dataset_id', dict(dataset_id=ID, stock_code='2454', stock_name='聯發科', industry='半導體',
          test_start='2023-01-01', test_end='2024-12-31', participated_in_development='true', results_viewed='false',
          influenced_strategy='false', current_classification='training_development_exposed', clean_validation_eligible='false',
          report_location='獨立實驗/結果', result_location='獨立實驗/資料', experiment_ids=ID,
          notes='2023–2024 only; existing stock database read-only; official TAIEX separated; no locked-period access.')),
    ]:
        path = ROOT / 'research/registries' / f'{kind}_registry.csv'
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows, fields = list(reader), reader.fieldnames
        if not any(r[key] == ID for r in rows):
            with path.open('a', encoding='utf-8', newline='') as f:
                csv.DictWriter(f, fieldnames=fields).writerow(row)
        # Save the registration row only; do not copy unrelated historical results.
        dump(HOME / '設定' / f'{kind}_registration.json', row)


def fetch(url, path):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    # TWSE legacy endpoints use UTF-8 or Big5. Numeric fields are identical.
    try:
        data = json.loads(raw.decode('utf-8'))
    except UnicodeDecodeError:
        data = json.loads(raw.decode('big5'))
    if data.get('stat') != 'OK':
        raise ValueError(f'Official response not OK: {url}: {data.get("stat")}')
    dump(path, data)
    return data


def download():
    register()
    index, stock, sources = [], [], []
    for year in (2023, 2024):
        for month in range(1, 13):
            date = f'{year}{month:02d}01'
            assert_development_period(f'{year}-{month:02d}-01', f'{year}-{month:02d}-01')
            for endpoint, target, output in [
                ('FMTQIK', index, HOME / '資料' / f'index_{date}.json'),
                ('STOCK_DAY', stock, HOME / '資料' / f'2454_daily_{date}.json'),
            ]:
                url = f'https://www.twse.com.tw/exchangeReport/{endpoint}?response=json&date={date}'
                if endpoint == 'STOCK_DAY':
                    url += '&stockNo=2454'
                data = fetch(url, output)
                for row in data['data']:
                    parts = row[0].split('/')
                    day = f'{int(parts[0])+1911:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}'
                    assert_development_period(day, day)
                    if not '2023-01-01' <= day <= '2024-12-31':
                        raise ValueError('Official response escaped allowed years')
                    if endpoint == 'FMTQIK':
                        target.append({'date': day, 'close': float(row[4].replace(',', '')),
                                       'available_time': day + 'T23:59:59+08:00'})
                    else:
                        target.append({'date': day, 'volume_shares': int(row[1].replace(',', '')),
                                       'close': float(row[6].replace(',', ''))})
                sources.append({'url': url, 'path': str(output.relative_to(HOME)), 'sha256': sha(output),
                                'retrieved_at': datetime.now(TZ).isoformat(), 'fields': data['fields']})
            print('Prepared official month', date, flush=True)
    if len({r['date'] for r in index}) != len(index):
        raise ValueError('Duplicate index dates')
    dump(HOME / '資料/index_daily.json', sorted(index, key=lambda r: r['date']))
    dump(HOME / '資料/2454_official_daily.json', sorted(stock, key=lambda r: r['date']))
    dump(HOME / '資料/official_sources.json', sources)
    # Official ex-right/dividend schedule, strictly bounded; no current market query.
    for year in (2023, 2024):
        url = f'https://www.twse.com.tw/exchangeReport/TWT48U?response=json&startDate={year}0101&endDate={year}1231'
        path = HOME / '資料' / f'corporate_actions_{year}.json'
        try:
            data = fetch(url, path)
            print('Corporate action fields', year, data.get('fields'), flush=True)
        except Exception as e:
            dump(HOME / '資料' / f'corporate_actions_{year}_unavailable.json', {'url':url,'error':str(e)})


def inspect():
    register()
    db = ROOT / 'data/technical_v1_mltech_clean.sqlite3'
    lower = int(datetime(2023, 1, 1, tzinfo=TZ).timestamp())
    upper = int(datetime(2025, 1, 1, tzinfo=TZ).timestamp())
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        rows = con.execute('SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars '
                           'WHERE stock_code IN (?,?,?,?,?) AND freq_minutes=5 AND kbar_timestamp>=? '
                           'AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp',
                           ('2303','2330','2379','2454','3034',lower,upper)).fetchall()
    assert_timestamps(r[1] for r in rows)
    # Metadata only. No trading or PnL computation.
    by = {}
    for r in rows:
        s = by.setdefault(r[0], {'bars': 0, 'dates': set(), 'min_close': r[5], 'max_close': r[5]})
        s['bars'] += 1
        s['dates'].add(datetime.fromtimestamp(r[1], TZ).date().isoformat())
        s['min_close'], s['max_close'] = min(s['min_close'], r[5]), max(s['max_close'], r[5])
    for s in by.values():
        s['dates'] = len(s['dates'])
    official_path = HOME / '資料/2454_official_daily.json'
    volume_audit = {'status': 'NEEDS_OFFICIAL_DAILY'}
    if official_path.exists():
        official = {r['date']: r for r in json.loads(official_path.read_text(encoding='utf-8'))}
        totals = {}
        for r in rows:
            if r[0] == '2454':
                day = datetime.fromtimestamp(r[1], TZ).date().isoformat()
                totals[day] = totals.get(day, 0) + r[6]
        ratios = sorted(official[d]['volume_shares']/v for d,v in totals.items() if d in official and v > 0)
        median = ratios[len(ratios)//2]
        # Official daily volume may include other sessions/odd lots. Orders of magnitude identify unit only.
        if 500 < median < 1500:
            factor = 1000
        elif 0.5 < median < 1.5:
            factor = 1
        else:
            raise ValueError(f'Ambiguous volume units {median}')
        volume_audit = {'status':'PASS_UNIT_MAGNITUDE_CHECK', 'to_shares_factor':factor,
                        'official_to_local_daily_volume_ratio_median': median,
                        'days_compared':len(ratios), 'ratio_min':min(ratios),'ratio_max':max(ratios),
                        'note':'Magnitude cross-check plus source importer; exact daily parity not claimed (auction/odd-lot/session differences).'}
    oof = ROOT / 'exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv'
    with oof.open(encoding='utf-8-sig',newline='') as f:
        selected = [r for r in csv.DictReader(f) if r['stock_code']=='2454' and r['model']=='random_forest']
    audit = {'experiment_id':ID,'stock_metadata':by,'volume':volume_audit,
             'oof_path':str(oof.relative_to(ROOT)), 'oof_sha256':sha(oof), 'rf_oof_2454_rows':len(selected),
             'oof_columns':list(selected[0]) if selected else [],
             'database_sha256':sha(db),'data_role':'training_development_exposed','locked_data_used':False}
    dump(HOME / '資料/preflight.json',audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if '--download' in sys.argv:
        download()
    inspect()
