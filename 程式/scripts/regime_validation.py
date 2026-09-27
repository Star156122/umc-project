"""以既有成交重建每日資產，按時間前後檢查行情適用性。"""
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import csv
import html
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from trading_system.research import TAIPEI, classify_days, equity_days, aggregate_days

ROOT = Path(__file__).resolve().parents[1]
REGIMES = ('上漲', '盤整', '下跌')


def assess(early, late):
    # 成交次數不是完整交易筆數；同時要求多檔與行情天數，避免一筆成交就過關。
    enough = all(x['stocks'] >= 3 and x['days'] >= 30 and x['fills'] >= 10 for x in (early, late))
    positive = all(x['median'] > 0 and x['profitable'] > x['stocks'] / 2 for x in (early, late))
    if not enough:
        return '暫不交易', '行情或成交樣本不足'
    if not positive:
        return '暫不交易', '前後段獲利未能持續，或多數股票未獲利'
    return '研究候選', '前後段多數股票與中位數皆為正；仍須未使用期間驗證'


def main():
    experiment = guarded_json_loads((ROOT/'configs/multi_stock_experiment_v2.json').read_text(encoding='utf-8'))
    records = []
    sources = []
    conn = sqlite3.connect(f"{(ROOT/'data/market_data.sqlite3').resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for code in experiment['stock_codes']:
            bars = [dict(r) for r in conn.execute('SELECT * FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND range_start=? AND range_end=? ORDER BY kbar_timestamp', (code, experiment['backtest_start'], experiment['backtest_end']))]
            if not bars:
                raise ValueError(f'{code} 缺少行情快取')
            closes = {}
            for b in bars:
                closes[datetime.fromtimestamp(b['kbar_timestamp'], TAIPEI).date().isoformat()] = float(b['close'])
            labels = classify_days(list(closes.items()), 20, .05)
            for key in experiment['strategy_keys']:
                matches = []
                for path in (ROOT/'reports'/code).glob('*/summary.json'):
                    summary = guarded_json_loads(path.read_text(encoding='utf-8'))
                    if summary.get('strategy') == key and summary.get('backtest_start') == experiment['backtest_start'] and summary.get('backtest_end') == experiment['backtest_end'] and 'cross-stock-cost-control-20260925-v2-candidate' in summary.get('run_name', ''):
                        matches.append((path, summary))
                if not matches:
                    raise ValueError(f'{code}/{key} 缺少固定v2報告')
                path, summary = max(matches, key=lambda pair: pair[1]['generated_at'])
                with (path.parent/'trades.csv').open(encoding='utf-8-sig', newline='') as stream:
                    trades = list(csv.DictReader(stream))
                days = equity_days(bars, trades, summary['initial_capital'], labels)
                if abs(days[-1]['equity'] - summary['final_assets']) > .02:
                    raise ValueError(f'{code}/{key} 資產核對失敗')
                if abs(sum(d['cost'] for d in days) - summary['transaction_cost']) > .02:
                    raise ValueError(f'{code}/{key} 成本核對失敗')
                sources.append(str(path.relative_to(ROOT)))
                for phase in ('前段', '後段'):
                    selected = [d for d in days if (d['day'] < '2026-08-15') == (phase == '前段')]
                    for group in aggregate_days(selected, summary['initial_capital']):
                        records.append(dict(group, code=code, strategy=key, label=summary['strategy_label'], phase=phase))
    finally:
        conn.close()
    decisions = []
    for key in experiment['strategy_keys']:
        for regime in REGIMES:
            phases = {}
            for phase in ('前段', '後段'):
                rows = [r for r in records if r['strategy'] == key and r['regime'] == regime and r['phase'] == phase and r['days'] > 0]
                phases[phase] = {'stocks': len(rows), 'days': sum(r['days'] for r in rows), 'fills': sum(r['fills'] for r in rows), 'profitable': sum(r['contribution'] > 0 for r in rows), 'median': statistics.median(r['contribution'] for r in rows) if rows else 0, 'pnl': sum(r['pnl'] for r in rows)}
            status, reason = assess(phases['前段'], phases['後段'])
            if regime == '下跌':
                status, reason = '暫不交易', '目前政策為下跌不做多；保留分段數據供研究'
            decisions.append({'strategy': key, 'regime': regime, 'phases': phases, 'status': status, 'reason': reason})
    payload = {'period': [experiment['backtest_start'], experiment['backtest_end']], 'split_date': '2026-08-15', 'sources': sources, 'records': records, 'decisions': decisions,
               'limitation': '已看過的歷史資料交叉檢查，非全新樣本外驗證。分段數值為原策略每日資產損益／初始本金的貢獻，不是切換策略的績效。每檔前21個交易日保留為資料不足。'}
    out = ROOT/'exports'
    (out/'regime_validation_latest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = []
    for d in decisions:
        a, b = d['phases']['前段'], d['phases']['後段']
        rows.append(f"<tr><td>{d['strategy']}</td><td>{d['regime']}</td><td>{a['median']:.2%}</td><td>{b['median']:.2%}</td><td>{a['profitable']}/{a['stocks']} → {b['profitable']}/{b['stocks']}</td><td>{a['days']} / {b['days']}</td><td>{a['fills']} / {b['fills']}</td><td>{d['status']}</td><td>{d['reason']}</td></tr>")
    detail = ''.join(f"<tr><td>{r['code']}</td><td>{r['label']}</td><td>{r['phase']}</td><td>{r['regime']}</td><td>{r['days']}</td><td>{r['contribution']:.2%}</td><td>{r['cost']:.0f}</td><td>{r['fills']}</td></tr>" for r in records)
    page = '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>行情適用性驗證</title><style>body{font-family:Microsoft JhengHei;margin:30px;background:#f5f7fb}table{border-collapse:collapse;background:white}td,th{padding:10px;border:1px solid #ccc}p{max-width:1100px;line-height:1.8}</style><h1>行情適用性與不同期間檢查</h1>'
    page += '<p>固定v2參數，6檔股票、3種策略。前段：2026/07/01～08/14；後段：08/15～09/24。候選條件：兩段各至少3檔具有該行情、合計30個股票交易日、10次成交，且兩段報酬貢獻中位數為正、多數股票獲利。門檻是本輪研究篩選規則，尚未獨立驗證。</p><p>'+html.escape(payload['limitation'])+'</p>'
    page += '<table><tr><th>策略</th><th>行情</th><th>前段中位數</th><th>後段中位數</th><th>獲利股票 前→後</th><th>股票交易日 前/後</th><th>成交 前/後</th><th>結論</th><th>原因</th></tr>'+''.join(rows)+'</table><details><summary>展開各股票分段明細（含資料不足）</summary><table><tr><th>股票</th><th>策略</th><th>期間</th><th>行情</th><th>天數</th><th>報酬貢獻</th><th>成本</th><th>成交</th></tr>'+detail+'</table></details></html>'
    (out/'regime_validation_latest.html').write_text(page, encoding='utf-8')
    lines = ['# 行情適用性驗證成果', '', payload['limitation'], '', '固定v2：6檔、3策略、18份原報告的資產與成本均已核對。前段7/1～8/14，後段8/15～9/24。', '', '|策略|行情|前段中位數|後段中位數|結論|原因|', '|---|---|---:|---:|---|---|']
    for d in decisions:
        lines.append(f"|{d['strategy']}|{d['regime']}|{d['phases']['前段']['median']:.2%}|{d['phases']['後段']['median']:.2%}|{d['status']}|{d['reason']}|")
    lines += ['', '下一步：先固定分類、分流與門檻，再取得未使用期間做逐日切換策略回測；需明確定義切換時持倉處理並計入成本。現有分段損益無法證明切換後可獲利。尚未驗證的組合保持暫不交易。', '', '本輪僅重建與分析既有資料，未修改原始報告或策略交易參數。']
    (ROOT/'docs/行情適用性驗證成果.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(decisions, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
