"""在固定使用者風控上，測試因果日線濾網與一般出場確認。"""
from trading_system import backtest as bt
from trading_system.candidate_risk import CandidateRisk


def daily_entry_flags(closes):
    """當日只用之前25個交易日，完全不讀當日收盤。"""
    flags = {}
    for i, (day, _) in enumerate(closes):
        if i < 25:
            flags[day] = False
            continue
        ma = sum(value for _, value in closes[i-20:i]) / 20
        old_ma = sum(value for _, value in closes[i-25:i-5]) / 20
        flags[day] = closes[i-1][1] > ma and ma > old_ma
    return flags


class EntryExitCandidate(CandidateRisk):
    daily_flags = None
    confirmation_bars = 1
    normal_exit_streak = 0

    def _can_enter(self, time):
        return ((self.daily_flags is None or self.daily_flags.get(time.date().isoformat(), False))
                and super()._can_enter(time))

    def on_deal(self, sender, response, **kwargs):
        self.normal_exit_streak = 0
        super().on_deal(sender, response, **kwargs)

    def _exit_signal(self, close, snapshot):
        signal = super()._exit_signal(close, snapshot)
        if signal is None or signal[0] != 'technical_exit':
            self.normal_exit_streak = 0
            return signal
        self.normal_exit_streak += 1
        if self.normal_exit_streak < self.confirmation_bars:
            self.pending_exit = None
            return None
        return signal[0], signal[1] + [f'一般訊號連續{self.confirmation_bars}根成立']


def build_entry_exit(config, daily_flags=None, confirmation_bars=1):
    if confirmation_bars < 1:
        raise ValueError('確認根數須為正')
    bot = EntryExitCandidate(config=config, use_broker='Sino', is_simulation=True, is_backtest=True, local_only=True)
    bot.configure_local_account(config)
    bot.quote_obj = bt.BacktestQuote()
    bot.local_order_mode = True
    bot.daily_flags = daily_flags
    bot.confirmation_bars = confirmation_bars
    return bot
