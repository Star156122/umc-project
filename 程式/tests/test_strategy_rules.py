from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from main02 import AppConfig, MovingAverageTsst, load_backtest_config, validate_config


def make_strategy(**config_overrides: object) -> MovingAverageTsst:
    strategy = MovingAverageTsst.__new__(MovingAverageTsst)
    strategy.config = replace(AppConfig(), **config_overrides)
    strategy.previous_snapshot = None
    strategy.entry_price = None
    strategy.bars_since_entry = 0
    strategy.is_bought = False
    strategy.is_entry = False
    strategy.is_exit = False
    strategy.cooldown_remaining = 0
    strategy.entries_today = 0
    return strategy


def snapshot(
    *,
    ma_fast: float = 100.4,
    ma_mid: float = 100.2,
    ma_slow: float = 100.0,
    rsi: float = 67.0,
    macd: float = 0.1,
    macd_signal: float = 0.05,
    close: float = 100.5,
    bb_middle: float = 100.0,
    bb_upper: float = 104.0,
    bb_lower: float = 96.0,
    breakout_high: float = 101.0,
    breakout_low: float = 95.0,
) -> dict[str, float]:
    return {
        "CLOSE": close,
        "MA_FAST": ma_fast,
        "MA_MID": ma_mid,
        "MA_SLOW": ma_slow,
        "RSI": rsi,
        "MACD": macd,
        "MACD_SIGNAL": macd_signal,
        "MACD_HIST": macd - macd_signal,
        "BB_MIDDLE": bb_middle,
        "BB_UPPER": bb_upper,
        "BB_LOWER": bb_lower,
        "BREAKOUT_HIGH": breakout_high,
        "BREAKOUT_LOW": breakout_low,
    }


class StrategyRuleTests(unittest.TestCase):
    def test_project_config_contains_six_2303_strategy_profiles(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "backtest_config.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        expected = [
            "2303_ma",
            "2303_rsi",
            "2303_macd",
            "2303_bollinger",
            "2303_breakout",
            "2303_vote",
        ]

        self.assertEqual(data["batch_profiles"], expected)
        self.assertEqual(
            [data["profiles"][name]["strategy"] for name in expected],
            ["ma", "rsi", "macd", "bollinger", "breakout", "vote"],
        )
        self.assertTrue(all(data["profiles"][name]["code"] == "2303" for name in expected))
        self.assertEqual(data["backtest_start"], "2026-01-01")
        self.assertEqual(data["backtest_end"], "2026-06-30")
        self.assertEqual(data["position_sizing"], "cash_fraction")
        self.assertEqual(data["holding_mode"], "swing")

    def test_cash_fraction_position_sizing_uses_odd_lots_when_one_lot_is_unaffordable(self) -> None:
        strategy = make_strategy(position_sizing="cash_fraction", capital_utilization=0.95)
        strategy.local_cash = 100_000.0
        strategy.local_fee_rate = strategy.config.stock_fee_rate
        strategy.last_tick = {"close": 200.0}

        quantity = strategy._order_quantity(200.0)

        self.assertEqual(quantity, 474)
        self.assertLessEqual(quantity, strategy.config.quantity)

    def test_entry_requires_new_ma_bullish_crossover(self) -> None:
        strategy = make_strategy()
        strategy.previous_snapshot = snapshot(ma_fast=99.8, ma_mid=100.0)

        reasons = strategy._entry_reasons(100.5, snapshot())

        self.assertIn("MA5 bullish crossover MA10", reasons)
        self.assertEqual(len(reasons), 2)

    def test_entry_rejects_ma_that_was_already_above_midline(self) -> None:
        strategy = make_strategy()
        strategy.previous_snapshot = snapshot(ma_fast=100.2, ma_mid=100.0)

        reasons = strategy._entry_reasons(100.5, snapshot())

        self.assertEqual(reasons, [])

    def test_entry_rejects_rsi_blocked_range(self) -> None:
        strategy = make_strategy(strategy="rsi")
        strategy.previous_snapshot = snapshot(ma_fast=99.8, ma_mid=100.0)

        reasons = strategy._entry_reasons(100.5, snapshot(rsi=64.5))

        self.assertEqual(reasons, [])

    def test_entry_accepts_blocked_rsi_when_block_disabled(self) -> None:
        strategy = make_strategy(strategy="rsi", use_rsi_block=False)
        strategy.previous_snapshot = snapshot(ma_fast=99.8, ma_mid=100.0)

        reasons = strategy._entry_reasons(100.5, snapshot(rsi=64.5))

        self.assertIn("RSI 60-70", reasons)

    def test_rsi_strategy_does_not_require_ma_crossover(self) -> None:
        strategy = make_strategy(strategy="rsi")
        strategy.previous_snapshot = snapshot(ma_fast=100.2, ma_mid=100.0)

        reasons = strategy._entry_reasons(100.5, snapshot(rsi=67.0))

        self.assertEqual(reasons, ["RSI 60-70, excluding 63-66"])

    def test_macd_strategy_requires_bullish_crossover(self) -> None:
        strategy = make_strategy(strategy="macd")
        strategy.previous_snapshot = snapshot(macd=0.01, macd_signal=0.02)

        reasons = strategy._entry_reasons(100.5, snapshot(macd=0.03, macd_signal=0.02))

        self.assertEqual(reasons, ["MACD bullish crossover signal"])

    def test_bollinger_strategy_enters_after_price_returns_inside_lower_band(self) -> None:
        strategy = make_strategy(strategy="bollinger")
        strategy.previous_snapshot = snapshot(close=95.0, bb_lower=96.0)

        reasons = strategy._entry_reasons(97.0, snapshot(close=97.0, bb_lower=96.5))

        self.assertEqual(len(reasons), 1)
        self.assertIn("由布林下軌外回到下軌內", reasons[0])

    def test_breakout_strategy_enters_above_previous_channel_high(self) -> None:
        strategy = make_strategy(strategy="breakout", breakout_entry_period=20)

        reasons = strategy._entry_reasons(101.1, snapshot(close=101.1, breakout_high=101.0))

        self.assertEqual(len(reasons), 1)
        self.assertIn("突破前 20 根 K 棒最高價", reasons[0])

    def test_bollinger_strategy_exits_at_middle_band(self) -> None:
        strategy = make_strategy(strategy="bollinger", min_hold_bars=2)
        strategy.entry_price = 99.0
        strategy.bars_since_entry = 2

        signal = strategy._exit_signal(100.1, snapshot(close=100.1, bb_middle=100.0))

        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "technical_exit")
        self.assertIn("回到布林中線", signal[1][0])

    def test_breakout_strategy_exits_below_previous_channel_low(self) -> None:
        strategy = make_strategy(strategy="breakout", breakout_exit_period=10, min_hold_bars=2)
        strategy.entry_price = 95.0
        strategy.bars_since_entry = 2

        signal = strategy._exit_signal(94.9, snapshot(close=94.9, breakout_low=95.0))

        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "technical_exit")
        self.assertIn("跌破前 10 根 K 棒最低價", signal[1][0])

    def test_vote_strategy_enters_when_two_or_more_votes_are_bullish(self) -> None:
        strategy = make_strategy(strategy="vote", vote_required=2)

        reasons = strategy._entry_reasons(
            100.5,
            snapshot(close=100.5, rsi=67.0, macd=0.03, macd_signal=0.02),
        )

        self.assertEqual(len(reasons), 1)
        self.assertIn("多數決達標 3/3", reasons[0])

    def test_vote_strategy_exits_when_two_or_more_votes_turn_bearish(self) -> None:
        strategy = make_strategy(strategy="vote", vote_required=2, min_hold_bars=2)
        strategy.entry_price = 100.0
        strategy.bars_since_entry = 2

        signal = strategy._exit_signal(
            99.0,
            snapshot(
                close=99.0,
                ma_fast=99.0,
                ma_mid=100.0,
                ma_slow=100.0,
                rsi=40.0,
                macd=0.0,
                macd_signal=0.05,
            ),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "technical_exit")
        self.assertIn("空方多數決 3/3", signal[1][0])

    def test_validate_config_allows_disabled_rsi_block_outside_entry_range(self) -> None:
        config = replace(AppConfig(), use_rsi_block=False, rsi_buy_above=66.0, rsi_buy_below=70.0)

        validate_config(config)

    def test_load_backtest_config_applies_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "backtest_config.json"
            path.write_text(
                json.dumps(
                    {
                        "active_profile": "2330_default",
                        "backtest_start": "2025-12-01",
                        "profiles": {
                            "2330_default": {
                                "code": "2330",
                                "run_name": "2330_default",
                                "use_macd": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            values = load_backtest_config(path)

        self.assertEqual(values["backtest_start"], "2025-12-01")
        self.assertEqual(values["code"], "2330")
        self.assertEqual(values["run_name"], "2330_default")
        self.assertFalse(values["use_macd"])

    def test_load_backtest_config_can_override_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "backtest_config.json"
            path.write_text(
                json.dumps(
                    {
                        "active_profile": "2313_default",
                        "profiles": {
                            "2313_default": {"code": "2313", "run_name": "2313_default"},
                            "2330_default": {"code": "2330", "run_name": "2330_default"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            values = load_backtest_config(path, "2330_default")

        self.assertEqual(values["code"], "2330")
        self.assertEqual(values["run_name"], "2330_default")

    def test_stop_loss_can_exit_before_minimum_holding_period(self) -> None:
        strategy = make_strategy(min_hold_bars=3, stop_loss_pct=0.03)
        strategy.entry_price = 100.0
        strategy.bars_since_entry = 1

        signal = strategy._exit_signal(96.9, snapshot())

        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "stop_loss")

    def test_technical_exit_waits_for_minimum_holding_period(self) -> None:
        strategy = make_strategy(min_hold_bars=3)
        strategy.entry_price = 100.0
        strategy.previous_snapshot = snapshot(ma_fast=100.4, ma_mid=100.2)
        weak_snapshot = snapshot(
            ma_fast=99.8,
            ma_mid=100.0,
            ma_slow=98.0,
            rsi=50.0,
            macd=0.0,
            macd_signal=0.05,
        )

        strategy.bars_since_entry = 2
        self.assertIsNone(strategy._exit_signal(99.0, weak_snapshot))

        strategy.bars_since_entry = 3
        signal = strategy._exit_signal(99.0, weak_snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "technical_exit")
        self.assertIn("MA5 bearish crossover MA10", signal[1])

    def test_daily_entry_limit_blocks_another_entry(self) -> None:
        strategy = make_strategy(max_entries_per_day=1)
        strategy.entries_today = 1

        self.assertFalse(strategy._can_enter(datetime(2026, 1, 2, 10, 0)))

    def test_entry_window_blocks_late_signal(self) -> None:
        strategy = make_strategy(entry_cutoff_hour=12, entry_cutoff_minute=30)

        self.assertFalse(strategy._can_enter(datetime(2026, 1, 2, 12, 35)))

    def test_entry_window_blocks_0930_to_0959(self) -> None:
        strategy = make_strategy()

        self.assertTrue(strategy._can_enter(datetime(2026, 1, 2, 9, 25)))
        self.assertFalse(strategy._can_enter(datetime(2026, 1, 2, 9, 30)))
        self.assertFalse(strategy._can_enter(datetime(2026, 1, 2, 9, 55)))
        self.assertTrue(strategy._can_enter(datetime(2026, 1, 2, 10, 0)))


if __name__ == "__main__":
    unittest.main()
