import pandas as pd,numpy as np
from datetime import datetime
from engine import TZ,round_lot,commission
def buyhold(c,data,slip):
 bars=[b for b in data['bars'] if c['period']['start']<=b['day']<=c['period']['end']]
 idx=next(i for i,b in enumerate(bars) if b['time'][11:16]>='09:10')
 first=bars[idx];price=first['Open']*(1+slip);cash=c['initial_capital'];budget=cash*.8
 liq=next(b['liq'] for b in data['bars'] if b['kbar_timestamp']==first['kbar_timestamp']-300)
 q=min(round_lot(budget/price,c),round_lot(liq*c['volume_to_shares']*c['volume_fraction'],c))
 while q and q*price+commission(q,price,c)>budget:q-=c['lot_size']
 if not q:raise ValueError('BH cannot buy minimum legal unit')
 fee_in=commission(q,price,c);cash-=q*price+fee_in;assert cash>=c['initial_capital']*.2-.001
 orders=[{'time':first['time'],'timestamp':first['kbar_timestamp'],'action':'BUY','quantity':q,'price':price,'reference':first['Open'],'fee':fee_in,'tax':0,'trade_id':1,'reason':'BUY_HOLD_FIRST_LEGAL_OPEN','slippage_cost':q*(price-first['Open'])}]
 curve=[];div=0;receivables=[]
 for b in bars:
  day=b['day'];ts=b['kbar_timestamp']
  for x in receivables:
   if not x['paid'] and x['payment_date']<=day:cash+=x['amount'];x['paid']=True
  action=data['dividends'].get(day)
  if action and ts>=first['kbar_timestamp'] and not any(x['ex_date']==day for x in receivables):
   amount=q*action['cash_per_share'];div+=amount;receivables.append({'ex_date':day,'payment_date':action['payment_date'],'amount':amount,'paid':False})
  holding=ts>=first['kbar_timestamp'];assets=cash+(q*b['Close'] if holding else q*price+fee_in)+sum(x['amount'] for x in receivables if not x['paid'])
  curve.append({'timestamp':ts,'date':day,'equity':assets,'cash':cash if holding else c['initial_capital'],'exposure':q*b['Close']/assets if holding else 0})
 last=bars[-1];exitp=last['Close']*(1-slip);fee_out=commission(q,exitp,c);tax=q*exitp*c['normal_sell_tax']
 cash+=q*exitp-fee_out-tax;receivable=sum(x['amount'] for x in receivables if not x['paid']);final=cash+receivable
 curve[-1].update(equity=final,cash=cash,exposure=0)
 when=datetime.fromtimestamp(last['kbar_timestamp']+300,TZ).isoformat()
 orders.append({'time':when,'timestamp':last['kbar_timestamp']+300,'action':'SELL','quantity':q,'price':exitp,'reference':last['Close'],'fee':fee_out,'tax':tax,'trade_id':1,'reason':'BUY_HOLD_END_CLOSE','slippage_cost':q*(last['Close']-exitp)})
 net=final-c['initial_capital'];gross=q*(exitp-price)+div
 trade={'trade_id':1,'entry_time':first['time'],'exit_time':when,'entry_day':first['day'],'exit_day':last['day'],'entry_price':price,'exit_price':exitp,'quantity':q,'net_pnl':net,'gross_pnl':gross,'holding_bars':len(bars)-idx,'adds':0,'exit_reason':'BUY_HOLD_END_CLOSE','holding_extensions':0,'cash_dividends':div}
 eq=pd.DataFrame(curve);daily=eq.groupby('date').equity.last();returns=daily.pct_change().fillna(0)
 m={'variant':'BH','scope':'full','slippage_each_side':slip,'initial_capital':c['initial_capital'],'final_equity':final,'return':net/c['initial_capital'],'net_pnl':net,'closed_trade_net_pnl':net,'unrealized_pnl':0,'gross_pnl':gross,'trade_count':1,'win_rate':float(net>0),'profit_factor':None,'max_drawdown':float((1-eq['equity']/eq['equity'].cummax().clip(lower=c['initial_capital'])).max()),'sharpe':float(returns.mean()/returns.std()*np.sqrt(252)) if returns.std()>0 else None,'average_win':net if net>0 else None,'average_loss':net if net<0 else None,'fees':fee_in+fee_out,'tax':tax,'slippage_cost':sum(o['slippage_cost'] for o in orders),'avg_exposure':float(eq['exposure'].mean()),'max_exposure':float(eq['exposure'].max()),'buys':1,'adds':0,'holding_extensions':0,'holding_extension_checks':0,'receivables_at_end':receivable,'cash_dividends':div}
 assert abs(net-(gross-fee_in-fee_out-tax))<.01
 return {'metrics':m,'orders':orders,'trades':[trade],'equity':curve,'events':[],'funnel':[],'holding_events':[],'ml_counts':{},'blockers':{},'open_position':None}
