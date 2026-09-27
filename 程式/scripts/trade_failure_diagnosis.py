"""只分析既有MACD／多數決修改版成交，不重跑或修改策略。"""
from __future__ import annotations
import csv, hashlib, json, sqlite3, statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from trading_system import backtest as bt
from trading_system.research_guard import assert_development_period, assert_payload

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'configs/trade_failure_diagnosis_20260926.json'
SOURCE=ROOT/'exports/six_strategy_logic/results.json'
OUT=ROOT/'exports/trade_failure_diagnosis'
NAMES={'macd':'MACD修改版','vote':'三指標多數決修改版'}


def med(values):
    clean=[v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def mean(values):
    clean=[v for v in values if v is not None]
    return statistics.fmean(clean) if clean else None


def time_bucket(value):
    hour=int(value[11:13]);minute=int(value[14:16]);total=hour*60+minute
    return '09:00～10:00' if total<600 else ('10:00～11:30' if total<690 else '11:30～12:30')


def load_bars(connection,code,start,end):
    assert_development_period(start,end)
    rows=[dict(r) for r in connection.execute(
        'SELECT kbar_timestamp,open,high,low,close,volume FROM market_kbars '
        'WHERE stock_code=? AND freq_minutes=5 AND range_start=? AND range_end=? ORDER BY kbar_timestamp',
        (code,start,end))]
    if not rows:raise ValueError(f'{code} 找不到固定開發期K棒')
    return rows


def diagnose_record(record,bars,definitions):
    positions={int(b['kbar_timestamp']):i for i,b in enumerate(bars)}
    buy_signals={s['datetime']:s for s in record['signals'] if s['action']=='Buy'}
    sell_signals={s['datetime']:s for s in record['signals'] if s['action']=='Sell'}
    buys={t['datetime']:t for t in record['trades'] if t['action']=='Buy'}
    sells={t['datetime']:t for t in record['trades'] if t['action']=='Sell'}
    previous_diag={(d['buy_datetime'],d['sell_datetime']):d for d in record['trade_diagnostics']}
    output=[]
    for pnl in bt._build_pnl_rows(record['trades']):
        buy,sell=buys[pnl['buy_datetime']],sells[pnl['sell_datetime']]
        signal=buy_signals.get(buy['datetime'],{});exit_signal=sell_signals.get(sell['datetime'],{})
        a=positions[int(buy['timestamp'])];b=positions[int(sell['timestamp'])]
        held=bars[a:b+1];prior=bars[max(0,a-definitions['consolidation_lookback_bars']):a]
        closes=[float(x['close']) for x in held] or [buy['price']]
        highs=[float(x['high']) for x in held] or [buy['price']]
        lows=[float(x['low']) for x in held] or [buy['price']]
        peak_index=max(range(len(closes)),key=closes.__getitem__);peak=closes[peak_index]
        peak_high=max(highs);trough_low=min(lows)
        peak_return=peak/buy['price']-1;peak_high_return=peak_high/buy['price']-1
        adverse_return=trough_low/buy['price']-1
        exit_return=sell['price']/buy['price']-1
        giveback_return=max(0.0,peak/buy['price']-sell['price']/buy['price'])
        giveback_fraction=giveback_return/peak_return if peak_return>0 else 0.0
        first=closes[:definitions['immediate_reversal_bars']]
        first_continuation=max(first)/buy['price']-1 if first else 0.0
        first_end=first[-1]/buy['price']-1 if first else 0.0
        prior_range=(max(float(x['high']) for x in prior)-min(float(x['low']) for x in prior))/float(prior[-1]['close']) if prior else None
        prior_return=float(prior[-1]['close'])/float(prior[0]['close'])-1 if len(prior)>1 else None
        near_high=(float(prior[-1]['close'])/max(float(x['high']) for x in prior)-1) if prior else None
        trend=signal.get('trend_ma');slope=signal.get('trend_slope')
        trend_distance=signal['price']/trend-1 if trend else None
        trend_slope_pct=slope/trend if trend and slope is not None else None
        volume_ratio=signal.get('volume')/signal.get('volume_ma') if signal.get('volume_ma') else None
        consolidation=bool(prior_range is not None and trend_slope_pct is not None and
                           prior_range<=definitions['consolidation_range_pct'] and
                           abs(trend_slope_pct)<=definitions['flat_trend_slope_pct'])
        late=bool(near_high is not None and prior_return is not None and
                  near_high>=-definitions['late_entry_near_high_pct'] and
                  prior_return>=definitions['late_entry_prior_return_pct'])
        immediate=bool(first_end<=-definitions['immediate_reversal_pct'] and
                       first_continuation<definitions['effective_profit_pct'])
        exit_reason=exit_signal.get('signal_type',previous_diag.get((buy['datetime'],sell['datetime']),{}).get('exit_reason','unknown'))
        labels=[];evidence=[];inference=[]
        if peak_return<definitions['effective_profit_pct']:
            labels.append('進場後幾乎沒有有效浮盈');evidence.append('最高完成收盤浮盈低於0.6%')
        if peak_return>=definitions['significant_peak_pct'] and giveback_fraction>=definitions['large_giveback_fraction']:
            labels.append('有明顯浮盈但吐回大部分獲利');evidence.append('最高浮盈至少1%且吐回至少50%')
        exit_map={'stop_loss':'固定停損出場','trend_break':'趨勢破壞出場','technical_exit':'一般技術訊號出場','trailing_stop':'移動停利出場'}
        if exit_reason in exit_map:labels.append(exit_map[exit_reason]);evidence.append('成交訊號紀錄')
        if pnl['gross_pnl']>0 and pnl['net_pnl']<=0:
            labels.append('價差為正但被成本轉成淨虧損');evidence.append('毛損益正且淨損益非正')
        if consolidation:
            labels.append('盤整行情造成的假訊號');inference.append('進場前12棒區間≤1.5%且趨勢斜率絕對值≤0.2%')
        if immediate:
            labels.append('進場後短時間內立刻反向');evidence.append('前三根完成K棒跌幅至少0.5%且未出現0.6%浮盈')
        if late:
            labels.append('進場太晚，可能接近短期高點');inference.append('進場前12棒漲幅≥1.5%且收盤距前高≤0.3%')
        net_return=pnl['return_pct']
        quality='成功交易' if net_return>=definitions['success_minimum_net_return_pct'] else ('低品質小利' if pnl['net_pnl']>0 else '失敗交易')
        if quality!='成功交易' and not labels:labels.append('其他無法歸類的異常交易')
        macd_position=('零軸上方' if signal.get('macd') is not None and signal['macd']>0 else
                       '零軸下方' if signal.get('macd') is not None else '無資料')
        old=previous_diag.get((buy['datetime'],sell['datetime']),{})
        output.append({'股票代號':record['code'],'股票名稱':record['name'],'策略':NAMES[record['strategy']],
          '交易品質':quality,'進場時間':buy['datetime'],'出場時間':sell['datetime'],'進場時段':time_bucket(buy['datetime']),
          '進場價格':buy['price'],'出場價格':sell['price'],'淨損益':pnl['net_pnl'],'報酬率':net_return,
          '毛損益':pnl['gross_pnl'],'交易成本':pnl['fee']+pnl['tax'],'持有K棒數':b-a,
          '最高完成收盤浮盈':peak_return,'盤中最高浮盈':peak_high_return,'最大盤中浮虧':adverse_return,
          '最高浮盈時間':bt.taipei_datetime_from_timestamp(int(held[peak_index]['kbar_timestamp'])).strftime('%Y-%m-%d %H:%M:%S'),
          '進場後幾根出現最高浮盈':peak_index,'浮盈吐回比例':giveback_fraction,'浮盈吐回報酬百分點':giveback_return,
          '浮盈吐回金額':giveback_return*buy['price']*pnl['quantity'],'出場原因':exit_reason,
          '日線趨勢成立':bool(old.get('entry_daily_trend_ok',False)),'成交量比':volume_ratio,
          '距趨勢均線':trend_distance,'趨勢均線斜率比':trend_slope_pct,'MACD交叉位置':macd_position,
          'MACD與訊號線差距占價格':((signal.get('macd')-signal.get('macd_signal'))/signal['price']
                                    if signal.get('macd') is not None and signal.get('macd_signal') is not None and signal.get('price') else None),
          '前三棒最大延續':first_continuation,'第三棒相對進場':first_end,'進場前12棒區間':prior_range,
          '進場前12棒報酬':prior_return,'可能位於盤整區':consolidation,'可能接近短期高點':late,
          '失敗分類':'｜'.join(labels),'已確認證據':'；'.join(evidence),'推測線索':'；'.join(inference)})
    return output


def group_comparison(rows,strategy):
    selected=[r for r in rows if r['策略']==NAMES[strategy]]
    result=[]
    for group in ('成功交易','失敗交易','低品質小利'):
        part=[r for r in selected if r['交易品質']==group]
        result.append({'策略':NAMES[strategy],'交易品質':group,'筆數':len(part),
          '距趨勢均線中位數':med([r['距趨勢均線'] for r in part]),'成交量比中位數':med([r['成交量比'] for r in part]),
          '日線趨勢成立率':mean([float(r['日線趨勢成立']) for r in part]),'前三棒最大延續中位數':med([r['前三棒最大延續'] for r in part]),
          'MACD零軸上方率':mean([float(r['MACD交叉位置']=='零軸上方') for r in part]),
          'MACD線差占價格中位數':med([r['MACD與訊號線差距占價格'] for r in part]),
          '持有K棒中位數':med([r['持有K棒數'] for r in part]),'最高浮盈中位數':med([r['最高完成收盤浮盈'] for r in part]),
          '最大浮虧中位數':med([r['最大盤中浮虧'] for r in part]),'盤整線索率':mean([float(r['可能位於盤整區']) for r in part]),
          '晚進場線索率':mean([float(r['可能接近短期高點']) for r in part]),
          '09至10點占比':mean([float(r['進場時段']=='09:00～10:00') for r in part]),
          '主要出場':Counter(r['出場原因'] for r in part).most_common(3)})
    return result


def write_csv(path,rows):
    if not rows:return
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'));assert_payload(plan);assert_development_period(plan['period_start'],plan['period_end'])
    OUT.mkdir(parents=True,exist_ok=True)
    frozen=OUT/'registered_plan.json'
    if frozen.exists() and json.loads(frozen.read_text(encoding='utf-8'))!=plan:raise ValueError('診斷定義已登記，不可依結果改門檻')
    if not frozen.exists():frozen.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    payload=json.loads(SOURCE.read_text(encoding='utf-8'));assert_payload(payload)
    rows=[]
    with sqlite3.connect((ROOT/'data/market_data.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        bars={code:load_bars(conn,code,plan['period_start'],plan['period_end']) for code in plan['stock_codes']}
    for record in payload['records']:
        if record['variant']==plan['variant'] and record['strategy'] in plan['strategies'] and record['code'] in plan['stock_codes']:
            rows.extend(diagnose_record(record,bars[record['code']],plan['definitions']))
    comparisons=[item for strategy in plan['strategies'] for item in group_comparison(rows,strategy)]
    failures=[r for r in rows if r['交易品質']!='成功交易']
    write_csv(OUT/'all_trades.csv',rows)
    write_csv(OUT/'macd_failed_and_low_quality.csv',[r for r in failures if r['策略']==NAMES['macd']])
    write_csv(OUT/'vote_failed_and_low_quality.csv',[r for r in failures if r['策略']==NAMES['vote']])
    write_csv(OUT/'success_vs_failure.csv',comparisons)
    stocks=[]
    for strategy in plan['strategies']:
        for code in plan['stock_codes']:
            part=[r for r in rows if r['策略']==NAMES[strategy] and r['股票代號']==code]
            bad=[r for r in part if r['交易品質']!='成功交易'];counter=Counter()
            for r in bad:counter.update(r['失敗分類'].split('｜'))
            source=next(r for r in payload['records'] if r['variant']=='candidate' and r['strategy']==strategy and r['code']==code)
            stocks.append({'策略':NAMES[strategy],'股票代號':code,'股票名稱':source['name'],'總交易':len(part),
                           '成功交易':sum(r['交易品質']=='成功交易' for r in part),'失敗與低品質':len(bad),
                           '淨損益':source['metrics']['net_pnl'],'報酬率':source['metrics']['total_return'],
                           '前三大失敗模式':'；'.join(f'{k}({v})' for k,v in counter.most_common(3))})
    write_csv(OUT/'stock_failure_modes.csv',stocks)
    result={'plan':plan,'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'created_at':datetime.now().isoformat(),'holdout_accessed':False,'rows':rows,'comparisons':comparisons,'stocks':stocks}
    (OUT/'diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    from scripts.trade_failure_report import write_report
    write_report(result)
    print(f'完成既有成交診斷：{len(rows)}筆；失敗或低品質{len(failures)}筆。未執行新回測。')


if __name__=='__main__':main()
