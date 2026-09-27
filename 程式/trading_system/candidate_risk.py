"""使用者指定的風控候選；不改動原策略類別。"""
from dataclasses import dataclass
from trading_system import backtest as bt


@dataclass(frozen=True)
class RiskPolicy:
    early_trend_exit: bool = True
    trailing_enabled: bool = True
    trailing_activation: float = .03
    trailing_drawdown: float = .02
    normal_cooldown: int = 12
    stop_cooldown: int = 24
    exit_trend_period: int = 120

    def __post_init__(self):
        if not (0 < self.trailing_activation < 1 and 0 < self.trailing_drawdown < 1):
            raise ValueError('移動停利比例必須介於0與1')
        if min(self.normal_cooldown, self.stop_cooldown) < 0 or self.exit_trend_period < 1:
            raise ValueError('冷卻不可為負，出場均線週期須為正')


class CandidateRisk(bt.MovingAverageTsst):
    policy = RiskPolicy()
    peak_close = None
    pending_exit = None

    def process_completed_kbar(self, code, decision_time, completed_kbar):
        # 獨立出場均線只影響提早出場，原本進場的TREND_MA保持不變。
        self.risk_exit_ma = completed_kbar.get('RISK_EXIT_MA', completed_kbar.get('TREND_MA'))
        super().process_completed_kbar(code, decision_time, completed_kbar)

    def _roll_trading_day(self, time):
        remaining = self.cooldown_remaining
        super()._roll_trading_day(time)
        self.cooldown_remaining = remaining

    def on_deal(self, sender, response, **kwargs):
        reason = self.pending_exit
        super().on_deal(sender, response, **kwargs)
        if response['action'] == 'Buy':
            self.peak_close = float(response['price'])
            self.pending_exit = None
        else:
            # 引擎會在下一棒先減1；加1使完整12/24根棒不准進場。
            self.cooldown_remaining = (self.policy.stop_cooldown if reason == 'stop_loss'
                                       else self.policy.normal_cooldown) + 1
            self.peak_close = None
            self.pending_exit = None

    def _exit_signal(self, close, snapshot):
        if self.entry_price is not None:
            self.peak_close = max(self.peak_close or self.entry_price, close)
            reason = None
            trend_ma = getattr(self, 'risk_exit_ma', snapshot.get('TREND_MA'))
            if self.config.stop_loss_pct > 0 and close <= self.entry_price * (1-self.config.stop_loss_pct):
                reason = 'stop_loss'
            elif self.policy.early_trend_exit and trend_ma is not None and close < trend_ma:
                reason = 'trend_break'
            elif (self.policy.trailing_enabled
                  and self.peak_close >= self.entry_price * (1+self.policy.trailing_activation)
                  and close <= self.peak_close * (1-self.policy.trailing_drawdown)):
                reason = 'trailing_stop'
            if reason:
                self.pending_exit = reason
                return reason, [reason]
        result = super()._exit_signal(close, snapshot)
        self.pending_exit = result[0] if result else None
        return result


def build_candidate(config, policy=None):
    bot = CandidateRisk(config=config, use_broker='Sino', is_simulation=True,
                        is_backtest=True, local_only=True)
    bot.configure_local_account(config)
    bot.policy = policy or RiskPolicy()
    bot.quote_obj = bt.BacktestQuote()
    bot.local_order_mode = True
    return bot
