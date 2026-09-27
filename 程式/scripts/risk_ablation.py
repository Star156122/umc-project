"""事先登記的小範圍風控拆項實驗，只讀開發期快取。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import contextlib
import dataclasses
import hashlib
import io
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from functools import partial

import polars as pl

from scripts.multi_stock_batch import build_runtime_config, load_plan
from scripts.routing_simulation import ROOT, simulate
from trading_system import backtest as bt
from trading_system.candidate_risk import RiskPolicy, build_candidate

PLAN = ROOT / 'configs/risk_ablation_20260926.json'
OUTPUT = ROOT / 'exports/risk_ablation'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def check_period(plan):
    assert_payload(plan)
    start, end = plan['period_start'], plan['period_end']
    hold = plan['holdout']
    if start > end or not (end < hold['start'] or start > hold['end']):
        raise ValueError('本實驗禁止讀取保留期間')
    if (start, end) != ('2026-01-01', '2026-06-30'):
        raise ValueError('本實驗只授權一月至六月開發資料')


def read_frame(connection, config):
    assert_config(config)
    records = [dict(row) for row in connection.execute(
        'SELECT kbar_timestamp,open,high,low,close,volume FROM market_kbars '
        'WHERE stock_code=? AND freq_minutes=5 AND range_start=? AND range_end=? ORDER BY kbar_timestamp',
        (config.code, config.backtest_start, config.backtest_end))]
    meta = connection.execute('SELECT source_row_count,source_first_timestamp,source_last_timestamp '
                              'FROM market_kbar_cache_meta WHERE stock_code=? AND freq_minutes=5 '
                              'AND range_start=? AND range_end=?',
                              (config.code, config.backtest_start, config.backtest_end)).fetchone()
    if not records or meta is None:
        raise ValueError(f'{config.code} 快取不完整；停止，不自動下載')
    raw = [{'kbar_timestamp': r['kbar_timestamp'], 'Open': r['open'], 'High': r['high'],
            'Low': r['low'], 'Close': r['close'], 'Volume': int(r['volume'])} for r in records]
    frame = pl.DataFrame(raw).with_columns(pl.col('kbar_timestamp').map_elements(
        bt.taipei_datetime_from_timestamp, return_dtype=pl.Datetime).alias('kbar_time'))
    stats = bt.MarketDataStats(meta['source_row_count'], meta['source_first_timestamp'], meta['source_last_timestamp'])
    return bt.add_indicators(frame, config), stats, digest(records)


def diagnostics(frame, stats, trades, capital, cost_bps):
    """按逐棒資產計算暴露比例、前後段貢獻與成交額成本壓力。"""
    fills = sorted(trades, key=lambda t: t['timestamp'])
    bars = list(frame.iter_rows(named=True))
    cash, position, index = float(capital), 0, 0
    daily, exposed, capital_fraction = {}, 0, 0.0
    for bar in bars:
        stamp = int(bar['kbar_timestamp'])
        while index < len(fills) and fills[index]['timestamp'] <= stamp:
            trade = fills[index]
            cash += trade['net_cash_flow']
            position += trade['quantity'] * (1 if trade['action'] == 'Buy' else -1)
            index += 1
        value = position * float(bar['Close'])
        equity = cash + value
        exposed += position > 0
        capital_fraction += value / equity if equity > 0 else 0
        daily[bar['kbar_time'].date().isoformat()] = equity
    # 期末成交可能在最後K棒起始時間之後，明確計入期末平倉費用。
    for trade in fills[index:]:
        if trade['timestamp'] > stats.last_timestamp:
            raise ValueError('成交超過資料結束時間')
        cash += trade['net_cash_flow']
        position += trade['quantity'] * (1 if trade['action'] == 'Buy' else -1)
    if position != 0:
        raise ValueError('期末持倉未結清')
    daily[next(reversed(daily))] = cash
    previous = capital
    phases = {'first': 0.0, 'second': 0.0}
    for day, equity in daily.items():
        phases['first' if day < '2026-04-01' else 'second'] += (equity-previous)/capital
        previous = equity
    turnover = sum(t['gross_amount'] for t in fills)
    stress = turnover * cost_bps / 10000
    return {'holding_bar_fraction': exposed/len(bars), 'average_invested_fraction': capital_fraction/len(bars),
            'first_contribution': phases['first'], 'second_contribution': phases['second'],
            'turnover': turnover, 'extra_cost': stress, 'stressed_return': (cash-capital-stress)/capital}


def paired_summary(selected, controls, evaluation):
    deltas = [a['metrics']['total_return']-b['metrics']['total_return'] for a,b in zip(selected,controls)]
    risk = [a['metrics']['max_drawdown']-b['metrics']['max_drawdown'] for a,b in zip(selected,controls)]
    cost = [a['metrics']['transaction_cost']-b['metrics']['transaction_cost'] for a,b in zip(selected,controls)]
    stress = [a['diagnostics']['stressed_return']-b['diagnostics']['stressed_return'] for a,b in zip(selected,controls)]
    leave_one_out = [statistics.median(deltas[:i]+deltas[i+1:]) for i in range(len(deltas))]
    failures = []
    if sum(d > 1e-10 for d in deltas) < evaluation['minimum_improved_stocks']:
        failures.append('改善股票不足4檔')
    if statistics.median(deltas) <= 1e-10:
        failures.append('報酬改善中位數非正')
    if statistics.median(risk) > 1e-10:
        failures.append('回撤中位數增加')
    if statistics.median(cost) > .01:
        failures.append('成本中位數增加')
    if min(leave_one_out) <= 1e-10:
        failures.append('拿掉一檔後改善不穩定')
    if statistics.median(stress) <= 1e-10:
        failures.append('額外成本後改善非正')
    if sum(a['metrics']['completed_trades'] >= evaluation['minimum_trades_per_stock'] for a in selected) < evaluation['minimum_stocks_with_enough_trades']:
        failures.append('完整交易樣本不足')
    return {'improved_stocks': sum(d>1e-10 for d in deltas), 'median_return_delta': statistics.median(deltas),
            'median_drawdown_delta': statistics.median(risk), 'median_cost_delta': statistics.median(cost),
            'min_leave_one_out_median_delta': min(leave_one_out), 'median_stressed_delta': statistics.median(stress),
            'median_first_delta': statistics.median(a['diagnostics']['first_contribution']-b['diagnostics']['first_contribution'] for a,b in zip(selected,controls)),
            'median_second_delta': statistics.median(a['diagnostics']['second_contribution']-b['diagnostics']['second_contribution'] for a,b in zip(selected,controls)),
            'status': '保留研究候選' if not failures else '未通過保留門檻', 'reasons': failures}


def summarize(records, plan):
    result = []
    for filtered in plan['entry_filters']:
        for variant in plan['variants']:
            rows = [r for r in records if r['filtered'] == filtered and r['variant'] == variant['id']]
            summary = {'variant': variant['id'], 'label': variant['label'], 'kind': variant['kind'], 'filtered': filtered,
                       'compare_to': variant['compare_to'],
                       'mean_return': statistics.fmean(r['metrics']['total_return'] for r in rows),
                       'median_return': statistics.median(r['metrics']['total_return'] for r in rows),
                       'profitable_stocks': sum(r['metrics']['total_return'] > 0 for r in rows),
                       'mean_cost': statistics.fmean(r['metrics']['transaction_cost'] for r in rows),
                       'mean_drawdown': statistics.fmean(r['metrics']['max_drawdown'] for r in rows),
                       'mean_holding_fraction': statistics.fmean(r['diagnostics']['holding_bar_fraction'] for r in rows),
                       'exit_counts': dict(sum((Counter(r['exit_counts']) for r in rows), Counter()))}
            if variant['compare_to']:
                controls = [next(r for r in records if r['code'] == a['code'] and r['filtered'] == filtered and r['variant'] == variant['compare_to']) for a in rows]
                summary.update(paired_summary(rows, controls, plan['evaluation']))
            result.append(summary)
    return result


def main():
    plan = guarded_json_loads(PLAN.read_text(encoding='utf-8'))
    check_period(plan)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frozen = OUTPUT/'registered_plan.json'
    if frozen.exists():
        if guarded_json_loads(frozen.read_text(encoding='utf-8')) != plan:
            raise ValueError('計畫已登記，禁止依結果覆寫；新實驗須另編版本')
    else:
        frozen.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    sources = ['scripts/risk_ablation.py','scripts/routing_simulation.py','trading_system/candidate_risk.py',
               'trading_system/backtest.py','configs/strategy_templates_v2_candidate.json','configs/frozen_baseline.json']
    run = {'started_at': datetime.now().isoformat(), 'plan_sha256': digest(plan),
           'source_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    with (OUTPUT/'execution_log.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(run, ensure_ascii=False)+'\n')
    print('計畫已登記：17版本 × 2種篩選 × 6檔；不使用保留期間', flush=True)
    stocks, templates, experiment = load_plan(ROOT/'configs/strategy_templates_v2_candidate.json')
    experiment.update(stock_codes=plan['stock_codes'], strategy_keys=['breakout'],
                      backtest_start=plan['period_start'], backtest_end=plan['period_end'])
    fields = {f.name for f in dataclasses.fields(bt.AppConfig)}
    records, benchmarks, market_hashes = [], {}, {}
    old = guarded_json_loads((ROOT/'exports/risk_candidate_latest.json').read_text(encoding='utf-8'))
    old_rows = {r['code']: r for r in old['results']}
    with contextlib.closing(sqlite3.connect((ROOT/'data/market_data.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for code in plan['stock_codes']:
            runtime = build_runtime_config(stocks[code], templates, experiment)
            settings = dict(runtime, **runtime['profiles'][f'{code}_breakout'])
            settings.update(llm_enabled=False, db_enabled=False, allow_real_trading=False, is_backtest=True,
                            is_simulation=True, only_backtest=True, tick_source='sinopac')
            base = bt.AppConfig(**{k:v for k,v in settings.items() if k in fields})
            frame, stats, market_hashes[code] = read_frame(connection, base)
            benchmarks[code] = bt.calculate_buy_and_hold_benchmark(bt.build_kbar_rows_from_frame(frame,base), base)
            for filtered in plan['entry_filters']:
                for variant in plan['variants']:
                    config = base if variant['kind']=='anchor' else dataclasses.replace(base, **plan['common'])
                    policy = None if variant['policy'] is None else RiskPolicy(**dict(plan['policy_reference'], **variant['policy']))
                    calculated = frame if policy is None else frame.with_columns(
                        pl.col('Close').rolling_mean(window_size=policy.exit_trend_period).alias('RISK_EXIT_MA'))
                    factory = None if policy is None else partial(build_candidate, policy=policy)
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = simulate({'ma':config,'breakout':config}, {'ma':calculated,'breakout':calculated}, stats,
                                          'breakout', entry_filter=filtered, factory=factory)
                    diag = diagnostics(frame, stats, result['trades'], base.initial_capital,
                                       plan['evaluation']['additional_one_way_cost_bps'])
                    if abs(diag['first_contribution']+diag['second_contribution']-result['metrics']['total_return']) > 1e-8:
                        raise ValueError('前後段損益與帳戶總損益不一致')
                    # 改寫為可切換規則後，原版與完整候選必須重現上一輪。
                    if variant['id'] in ('v2_original','full'):
                        name = ('原版' if variant['id']=='v2_original' else '新風控') + ('加行情' if filtered else '')
                        prior = old_rows[code]['groups'][name]
                        if abs(result['metrics']['net_pnl'] - prior['metrics']['net_pnl']) > .02:
                            raise ValueError(f'{code}/{name} 未重現上一輪')
                    records.append({'code':code,'name':stocks[code]['name'],'variant':variant['id'], 'filtered':filtered,
                                    'metrics':result['metrics'],'diagnostics':diag,'trades':result['trades'],
                                    'exit_counts':dict(Counter(s['signal_type'] for s in result['signals'] if s['action']=='Sell')),
                                    'signals': result['signals']})
            print(f'{code} 完成34組；帳戶、成本、成交價格、前後段損益及舊版重現核對通過', flush=True)
    payload = {'run': dict(run, completed_at=datetime.now().isoformat(), market_sha256=market_hashes),
               'plan':plan,'records':records,'summary':summarize(records,plan),'benchmarks':benchmarks}
    (OUTPUT/'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    from scripts.risk_ablation_report import write_report
    write_report(payload)
    print('完成204組。報告：exports/risk_ablation_latest.html', flush=True)


if __name__=='__main__':
    main()
