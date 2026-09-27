"""原訊號K0後等待K1完成，K1收盤突破K0高點才在K2開盤成交。"""
from trading_system import backtest as bt
from trading_system.strategy_logic_candidate import StrategyLogicCandidate


class EarlyFollowThroughCandidate(StrategyLogicCandidate):
    pending_origin=None
    pending_age=0
    current_completed_time=None
    origin_signals=0
    confirmed_signals=0
    cancelled_signals=0

    def process_completed_kbar(self,code,decision_time,completed_kbar):
        self.current_completed_time=completed_kbar.get('kbar_time') or completed_kbar.get('datetime')
        if self.pending_origin is not None:
            self.pending_age+=1
        super().process_completed_kbar(code,decision_time,completed_kbar)
        # K1已完成但無法進場（例如超過進場時段）時也必須取消，不能延到K2再確認。
        if self.pending_origin is not None and self.pending_age>=1:
            self.cancelled_signals+=1
            self.pending_origin=None;self.pending_age=0

    def _entry_reasons(self,close,snapshot):
        if self.pending_origin is not None:
            origin=self.pending_origin
            self.pending_origin=None;self.pending_age=0
            if close>origin['high']:
                self.confirmed_signals+=1
                return origin['reasons']+[f'K1收盤{close:.2f}突破K0最高價{origin["high"]:.2f}',
                                          f'原訊號K0={origin["time"]}，本次於K2開盤成交']
            self.cancelled_signals+=1
            return []
        reasons=super()._entry_reasons(close,snapshot)
        if reasons:
            high=snapshot.get('HIGH')
            if high is None:return []
            self.origin_signals+=1
            self.pending_origin={'high':high,'low':snapshot.get('LOW'),'close':close,
                                 'time':str(self.current_completed_time),'reasons':reasons}
            self.pending_age=0
        return []

    def research_diagnostics(self):
        return {'origin_signals':self.origin_signals,'confirmed_signals':self.confirmed_signals,
                'cancelled_signals':self.cancelled_signals,'pending_at_end':self.pending_origin is not None}


def build_early_followthrough(config,rules,daily_flags=None):
    bot=EarlyFollowThroughCandidate(config=config,use_broker='Sino',is_simulation=True,is_backtest=True,local_only=True)
    bot.configure_local_account(config);bot.quote_obj=bt.BacktestQuote();bot.local_order_mode=True
    bot.rules=rules;bot.daily_flags=daily_flags;bot.rsi_armed=False
    bot.pending_origin=None;bot.pending_age=0;bot.origin_signals=0;bot.confirmed_signals=0;bot.cancelled_signals=0
    return bot
