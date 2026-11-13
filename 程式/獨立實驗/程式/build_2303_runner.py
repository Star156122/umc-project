"""Adapt reporting/IO only; numerical engine is a stock-routed snapshot."""
from pathlib import Path

parent=Path(__file__).resolve().parents[1]
home=parent/'聯電2303'
s=(parent/'程式/run_experiment.py').read_text(encoding='utf-8')
s=s.replace('from prepare import dump,sha,register,ID','from prepare import dump,sha\nID="isolated-buy-2303-20261113-fixed-replication-exp001"')
s=s.replace("    if config_name=='experiment.json':\n        register()\n    elif c.get('execution_data_mode')", "    if c.get('execution_data_mode')")
s=s.replace("ROOT/c['old_db'],ROOT/c['revised_db'],ROOT/c['oof_path']]", "ROOT/c['old_db'],ROOT/c['revised_db'],ROOT/c['oof_path'],\n               HOME.parent/'結果/20261113T224843/metrics.csv',HOME.parent/'程式/engine.py',HOME.parent/'設定/experiment_odd_lot.json']")
s=s.replace('2454_cash_dividends.json','2303_cash_dividends.json')
s=s.replace("'bars':[[b['kbar_timestamp']", "'previous_stock_metrics':previous_stock_metrics(),\n                 'bars':[[b['kbar_timestamp']")
s=s.replace("for v in 'ABCD'", "for v in 'CD'").replace('四組總比較','兩組總比較').replace('聯發科 BUY','聯電 BUY')
s=s.replace('stock-independent', 'stock-independent')
helper='''
def previous_stock_metrics():
    path=HOME.parent/'結果/20261113T224843/metrics.csv'
    frame=pd.read_csv(path)
    frame=frame[(frame.scope=='common') & (frame.slippage_each_side==0.0005) & frame.variant.isin(['C','D'])]
    columns=['variant','return','net_pnl','trade_count','win_rate','max_drawdown','avg_exposure','max_exposure']
    rows=frame[columns].to_dict('records')
    if len(rows)!=2:
        raise ValueError('Parent fixed reference is incomplete')
    return rows

'''
s=s.replace("if __name__=='__main__':",helper+"if __name__=='__main__':")
(home/'程式/run_umc.py').write_text(s,encoding='utf-8')

js=(home/'程式/report.js').read_text(encoding='utf-8')
needle='    lineChart(\'curve\',rs.map(r=>r.equity));'
assert needle in js
extra='''    content.insertAdjacentHTML('beforeend',`<section class="panel"><h2>換股票後，結果是否相同？</h2><p>固定使用每邊 0.05% 滑價、各股票內部相同日期的主對照，不隨上方選單改變。兩股的資料資格各自判定，不能合併平均或當作獨立樣本外成功。</p>${table(['股票','策略','報酬率','淨損益（元）','交易數','最大回撤','平均投入部位'],[...D.previous_stock_metrics.map(m=>['聯發科 2454',labels[m.variant],pct(m.return),n(m.net_pnl,2),n(m.trade_count),pct(m.max_drawdown),pct(m.avg_exposure)]),...['C','D'].map(v=>{const m=D.results[v+'_5_common'].metrics;return ['聯電 2303',labels[v],pct(m.return),n(m.net_pnl,2),n(m.trade_count),pct(m.max_drawdown),pct(m.avg_exposure)];})])}<p>聯發科僅引用上一輪已保存結果，沒有重跑；本輪沒有因聯電結果修改設定。</p></section>`);
'''
js=js.replace(needle,extra+needle)
(home/'程式/report.js').write_text(js,encoding='utf-8')
with (home/'程式/report.css').open('a',encoding='utf-8') as f:
    f.write('\n.strategy-nav{grid-template-columns:repeat(2,minmax(0,1fr))}@media(max-width:520px){.strategy-nav{grid-template-columns:1fr}}\n')

qa=(parent/'測試/qa_reports.cjs').read_text(encoding='utf-8')
qa=qa.replace("['A','B','C','D']","['C','D']").replace("name==='B_funnel'","name==='D_funnel'").replace("name==='B_analysis'","name==='D_analysis'").replace("'B_analysis.html'","'D_analysis.html'").replace('聯發科','聯電')
(home/'測試/qa_reports.cjs').write_text(qa,encoding='utf-8')
print('UMC runner/report built; numerical rules unchanged')
