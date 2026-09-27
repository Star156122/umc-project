"""六策略各自的單一問題導向進場改善；不改原V2類別。"""
from trading_system import backtest as bt


class StrategyLogicCandidate(bt.MovingAverageTsst):
    rules = None
    daily_flags = None
    rsi_armed = False

    def _can_enter(self, time):
        if self.config.strategy in ('macd', 'vote'):
            if self.daily_flags is None or not self.daily_flags.get(time.date().isoformat(), False):
                return False
        return super()._can_enter(time)

    def _ma_entry_reasons(self, close, snapshot):
        reasons = super()._ma_entry_reasons(close, snapshot)
        if not reasons:
            return []
        slow, trend, slope = snapshot.get('MA_SLOW'), snapshot.get('TREND_MA'), snapshot.get('TREND_SLOPE')
        distance = close / slow - 1 if slow else None
        slope_pct = slope / trend if trend else None
        rule = self.rules['ma']
        if distance is None or distance > rule['max_slow_ma_distance_pct']:
            return []
        if slope_pct is None or slope_pct < rule['minimum_trend_slope_pct']:
            return []
        return reasons + [f'距慢均線不超過{rule["max_slow_ma_distance_pct"]:.1%}',
                          f'趨勢均線斜率至少{rule["minimum_trend_slope_pct"]:.1%}']

    def _rsi_entry_reasons(self, snapshot):
        rsi = snapshot.get('RSI')
        close = snapshot.get('CLOSE')
        valid = (rsi is not None and self.config.rsi_buy_above <= rsi <= self.config.rsi_buy_below
                 and close is not None and self._trend_filter_ok(close, snapshot))
        previous = self.previous_snapshot.get('RSI') if self.previous_snapshot else None
        crossed = previous is not None and previous < self.config.rsi_buy_above and valid
        if crossed:
            self.rsi_armed = True
            return []
        if self.rsi_armed and valid:
            self.rsi_armed = False
            return [f'RSI突破{self.config.rsi_buy_above:g}後再維持1根完成K棒']
        self.rsi_armed = False
        return []

    def _bollinger_entry_reasons(self, close, snapshot):
        reasons = super()._bollinger_entry_reasons(close, snapshot)
        if not reasons:
            return []
        high, low = snapshot.get('HIGH'), snapshot.get('LOW')
        location = (close-low)/(high-low) if high is not None and low is not None and high > low else 0
        threshold = self.rules['bollinger']['minimum_close_location']
        return reasons + [f'收盤位於K棒上方{1-threshold:.0%}區域'] if location >= threshold else []

    def _breakout_entry_reasons(self, close, snapshot):
        reasons = super()._breakout_entry_reasons(close, snapshot)
        if not reasons:
            return []
        high = snapshot.get('BREAKOUT_HIGH')
        excess = close/high-1 if high else None
        rule = self.rules['breakout']
        if excess is None or not rule['minimum_breakout_pct'] <= excess <= rule['maximum_breakout_pct']:
            return []
        return reasons + [f'突破幅度介於{rule["minimum_breakout_pct"]:.1%}至{rule["maximum_breakout_pct"]:.1%}']


def build_strategy_logic(config, rules, daily_flags=None):
    bot = StrategyLogicCandidate(config=config, use_broker='Sino', is_simulation=True,
                                 is_backtest=True, local_only=True)
    bot.configure_local_account(config)
    bot.quote_obj = bt.BacktestQuote()
    bot.local_order_mode = True
    bot.rules = rules
    bot.daily_flags = daily_flags
    bot.rsi_armed = False
    return bot
