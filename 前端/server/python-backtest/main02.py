"""單檔版股票回測程式，保留作為開發紀錄與功能對照。"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any, Literal

import polars as pl
import polars_talib as plta
import shioaji as sj
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(ROOT / "reports"))).resolve()
CONFIG_FILE = ROOT / "backtest_config.json"


def safe_path_part(value: str) -> str:
    """Convert an optional label into a folder-name friendly ASCII segment."""

    text = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return text.strip("._-")


def build_report_prefix(run_id: str, config: "AppConfig") -> str:
    parts = [run_id, config.code, config.strategy]
    run_name = safe_path_part(config.run_name)
    if run_name:
        parts.append(run_name)
    parts.extend([config.backtest_start, config.backtest_end])
    return "_".join(parts)


def stock_report_dir(config: "AppConfig") -> Path:
    return REPORT_DIR / (safe_path_part(config.code) or "unknown")


def patch_tsst_log_paths() -> None:
    """將 TSST 套件預設的 log 檔案導向本專案的 logs/ 目錄。"""
    LOG_DIR.mkdir(exist_ok=True)
    original_file_handler = logging.FileHandler

    class ProjectFileHandler(original_file_handler):  # type: ignore[misc, valid-type]
        def __init__(self, filename: str | os.PathLike[str], *args: Any, **kwargs: Any):
            path = Path(filename)
            if path.name in {"tsst.log", "broker.log"}:
                path = LOG_DIR / path.name
            super().__init__(path, *args, **kwargs)

    logging.FileHandler = ProjectFileHandler  # type: ignore[assignment]


patch_tsst_log_paths()
from tsst.broker import QuoteManager  # noqa: E402
from tsst.main import Tsst  # noqa: E402


StrategyName = Literal["ma", "rsi", "macd", "fixed"]
TickSource = Literal["sinopac", "tsst"]


class BacktestQuote:
    """回測用報價物件，讓 TSST 可以從本地 tick 重建 K 線。"""

    def __init__(self):
        self.quote_manager = QuoteManager()

    def get_kbar(
        self,
        code: str,
        unit: str,
        freq: int,
        exprs: list[pl.Expr] | None = None,
        to_pandas_df: bool = False,
    ):
        return self.quote_manager.get_kbar(
            code,
            unit,
            freq,
            exprs or [],
            to_pandas_df=to_pandas_df,
        )


def str_to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要的 .env 設定值：{name}")
    return value


CONFIG_ENV_NAMES: dict[str, str] = {
    "strategy": "STRATEGY",
    "tick_source": "TICK_SOURCE",
    "run_name": "RUN_NAME",
    "code": "STOCK_CODE",
    "backtest_start": "BACKTEST_START",
    "backtest_end": "BACKTEST_END",
    "backfill_start": "BACKFILL_START",
    "backfill_end": "BACKFILL_END",
    "is_backtest": "IS_BACKTEST",
    "is_simulation": "IS_SIMULATION",
    "only_backtest": "ONLY_BACKTEST",
    "allow_real_trading": "ALLOW_REAL_TRADING",
    "initial_capital": "INITIAL_CAPITAL",
    "lots": "ORDER_LOTS",
    "buy_price": "BUY_PRICE",
    "sell_hour": "SELL_HOUR",
    "sell_minute": "SELL_MINUTE",
    "entry_near_ma_points": "ENTRY_NEAR_MA_POINTS",
    "stock_fee_rate": "STOCK_FEE_RATE",
    "stock_tax_rate": "STOCK_TAX_RATE",
    "kbar_unit": "KBAR_UNIT",
    "kbar_freq": "KBAR_FREQ",
    "ma_fast_period": "MA_FAST_PERIOD",
    "ma_mid_period": "MA_MID_PERIOD",
    "ma_slow_period": "MA_SLOW_PERIOD",
    "entry_start_hour": "ENTRY_START_HOUR",
    "entry_start_minute": "ENTRY_START_MINUTE",
    "entry_cutoff_hour": "ENTRY_CUTOFF_HOUR",
    "entry_cutoff_minute": "ENTRY_CUTOFF_MINUTE",
    "entry_block_start_hour": "ENTRY_BLOCK_START_HOUR",
    "entry_block_start_minute": "ENTRY_BLOCK_START_MINUTE",
    "entry_block_end_hour": "ENTRY_BLOCK_END_HOUR",
    "entry_block_end_minute": "ENTRY_BLOCK_END_MINUTE",
    "use_rsi": "USE_RSI",
    "rsi_period": "RSI_PERIOD",
    "rsi_buy_above": "RSI_BUY_ABOVE",
    "rsi_buy_below": "RSI_BUY_BELOW",
    "use_rsi_block": "USE_RSI_BLOCK",
    "rsi_block_low": "RSI_BLOCK_LOW",
    "rsi_block_high": "RSI_BLOCK_HIGH",
    "rsi_sell_below": "RSI_SELL_BELOW",
    "use_macd": "USE_MACD",
    "macd_fast_period": "MACD_FAST_PERIOD",
    "macd_slow_period": "MACD_SLOW_PERIOD",
    "macd_signal_period": "MACD_SIGNAL_PERIOD",
    "stop_loss_pct": "STOP_LOSS_PCT",
    "take_profit_pct": "TAKE_PROFIT_PCT",
    "min_hold_bars": "MIN_HOLD_BARS",
    "cooldown_bars": "COOLDOWN_BARS",
    "max_entries_per_day": "MAX_ENTRIES_PER_DAY",
}
CONFIG_RESERVED_KEYS = {"active_profile", "batch_profiles", "profiles"}


def resolve_config_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def _stringify_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _validate_config_keys(values: dict[str, Any], source: str, allow_reserved: bool = False) -> None:
    allowed = set(CONFIG_ENV_NAMES)
    if allow_reserved:
        allowed |= CONFIG_RESERVED_KEYS
    unknown = sorted(key for key in values if key not in allowed)
    if unknown:
        raise RuntimeError(f"{source} 有不支援的設定欄位：" + ", ".join(unknown))


def load_backtest_config(path: Path, profile_name: str = "") -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path.name} JSON 格式錯誤：第 {exc.lineno} 行第 {exc.colno} 欄") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} 最外層必須是 JSON 物件")

    _validate_config_keys(data, path.name, allow_reserved=True)
    values = {key: value for key, value in data.items() if key in CONFIG_ENV_NAMES}
    active_profile = profile_name.strip() or str(data.get("active_profile", "")).strip()
    profiles = data.get("profiles", {})
    if profiles and not isinstance(profiles, dict):
        raise RuntimeError(f"{path.name} 的 profiles 必須是 JSON 物件")
    if active_profile:
        profile = profiles.get(active_profile)
        if not isinstance(profile, dict):
            raise RuntimeError(f"{path.name} 找不到 profile：{active_profile}")
        _validate_config_keys(profile, f"{path.name} profile {active_profile}")
        values.update(profile)
        values.setdefault("run_name", active_profile)
    return values


def apply_backtest_config_defaults(values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            continue
        os.environ[CONFIG_ENV_NAMES[key]] = _stringify_config_value(value)


@dataclass(frozen=True)
class AppConfig:
    """集中管理回測日期、股票代號、策略參數與費率設定。"""

    strategy: StrategyName = "ma"
    tick_source: TickSource = "sinopac"
    run_name: str = ""
    code: str = "2313"
    backtest_start: str = "2026-01-01"
    backtest_end: str = "2026-06-30"
    backfill_start: str = "2026-02-01"
    backfill_end: str = "2026-02-28"
    is_backtest: bool = True
    is_simulation: bool = True
    only_backtest: bool = True
    allow_real_trading: bool = False
    initial_capital: int = 100_000
    lots: int = 1
    buy_price: float = 1790
    sell_hour: int = 13
    sell_minute: int = 20
    entry_near_ma20_points: float = 0.0
    stock_fee_rate: float = 0.001425
    stock_tax_rate: float = 0.003
    kbar_unit: str = "m"
    kbar_freq: int = 5
    ma_fast_period: int = 5
    ma_mid_period: int = 10
    ma_slow_period: int = 20
    entry_near_ma_points: float = 0.0
    entry_start_hour: int = 9
    entry_start_minute: int = 0
    entry_cutoff_hour: int = 12
    entry_cutoff_minute: int = 30
    entry_block_start_hour: int = 9
    entry_block_start_minute: int = 30
    entry_block_end_hour: int = 10
    entry_block_end_minute: int = 0
    use_rsi: bool = True
    rsi_period: int = 14
    rsi_buy_above: float = 60.0
    rsi_buy_below: float = 70.0
    use_rsi_block: bool = True
    rsi_block_low: float = 63.0
    rsi_block_high: float = 66.0
    rsi_sell_below: float = 45.0
    use_macd: bool = True
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.03
    min_hold_bars: int = 2
    cooldown_bars: int = 6
    max_entries_per_day: int = 1

    @property
    def quantity(self) -> int:
        return self.lots * 1000

    @property
    def kbar_interval(self) -> str:
        return f"{self.kbar_freq}{self.kbar_unit}"


INDICATOR_COLUMNS = [
    "MA_FAST",
    "MA_MID",
    "MA_SLOW",
    "RSI",
    "MACD",
    "MACD_SIGNAL",
    "MACD_HIST",
]


def indicator_exprs(config: AppConfig) -> list[pl.Expr]:
    """建立 MA 與 RSI 的 Polars 指標計算式。"""

    return [
        plta.ma(pl.col("Close"), config.ma_fast_period).alias("MA_FAST"),
        plta.ma(pl.col("Close"), config.ma_mid_period).alias("MA_MID"),
        plta.ma(pl.col("Close"), config.ma_slow_period).alias("MA_SLOW"),
        plta.rsi(pl.col("Close"), config.rsi_period).alias("RSI"),
    ]


def add_macd_columns(df: pl.DataFrame, config: AppConfig) -> pl.DataFrame:
    """用 EMA 自行計算 MACD，避免資料太短時套件回傳空欄位。"""

    if df.is_empty():
        return df

    fast_ema = "_MACD_FAST_EMA"
    slow_ema = "_MACD_SLOW_EMA"
    return (
        df.with_columns(
            [
                pl.col("Close").ewm_mean(span=config.macd_fast_period, adjust=False).alias(fast_ema),
                pl.col("Close").ewm_mean(span=config.macd_slow_period, adjust=False).alias(slow_ema),
            ]
        )
        .with_columns((pl.col(fast_ema) - pl.col(slow_ema)).alias("MACD"))
        .with_columns(pl.col("MACD").ewm_mean(span=config.macd_signal_period, adjust=False).alias("MACD_SIGNAL"))
        .with_columns((pl.col("MACD") - pl.col("MACD_SIGNAL")).alias("MACD_HIST"))
        .drop([fast_ema, slow_ema])
    )


def add_indicators(df: pl.DataFrame, config: AppConfig) -> pl.DataFrame:
    """一次補上策略和圖表都會用到的技術指標。"""

    if df.is_empty():
        return df
    return add_macd_columns(df.with_columns(indicator_exprs(config)), config)


def number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def indicator_snapshot(row: dict[str, Any]) -> dict[str, float | None]:
    """擷取單根 K 線上的指標值，供訊號紀錄與策略判斷使用。"""

    return {column: number_or_none(row.get(column)) for column in INDICATOR_COLUMNS}


def format_indicator_value(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


class BacktestSafeTsst(Tsst):
    """包一層安全回測模式，避免回測時真的送出委託。"""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.local_order_mode = False
        self.last_tick: dict[str, Any] | None = None
        self.local_order_id = 0
        self.local_position = 0
        self.local_trades: list[dict[str, Any]] = []
        self.local_signals: list[dict[str, Any]] = []

    def init_quote(self) -> None:
        if self.is_backtest:
            if not self.quote_obj:
                self.quote_obj = BacktestQuote()
            return
        super().init_quote()

    def init_trade(self) -> None:
        if self.is_backtest:
            return
        super().init_trade()

    def create_order(self, code: str, product_type: Literal["Stock", "Future"], params: dict, **kwargs: Any):
        if self.local_order_mode:
            return self._create_local_order(code, product_type, params)
        return super().create_order(code, product_type, params, **kwargs)

    def unsubscribe(self, params: dict, **kwargs: Any) -> dict:
        if self.local_order_mode:
            return {"code": "0000", "message": "local unsubscribe", "params": params}
        return super().unsubscribe(params, **kwargs)

    def record_signal(
        self,
        *,
        code: str,
        action: Literal["Buy", "Sell"],
        signal_type: str,
        signal_time: datetime,
        price: float,
        reason: str,
        indicators: dict[str, float | None] | None = None,
    ) -> None:
        # 策略產生買賣判斷時先記錄訊號，之後會輸出到 signals.csv。
        self.local_signals.append(
            {
                "exchange_ts": signal_time.timestamp(),
                "datetime": signal_time.strftime("%Y-%m-%d %H:%M:%S"),
                "code": code,
                "action": action,
                "signal_type": signal_type,
                "price": price,
                "reason": reason,
                **(indicators or {}),
            }
        )

    def _create_local_order(self, code: str, product_type: str, params: dict) -> dict:
        # 本地模擬成交：市價單直接成交，限價單依照目前 tick 價判斷是否成交。
        self.local_order_id += 1
        order_id = f"LOCAL-{self.local_order_id:06d}"
        action = params["action"]
        price_type = params["price_type"]
        limit_price = float(params.get("price", 0) or 0)
        quantity = int(params["quantity"])
        tick_price = float(self.last_tick["close"]) if self.last_tick else limit_price
        can_fill = price_type in {"MKT", "MKP"}

        if price_type == "LMT":
            if action == "Buy":
                can_fill = tick_price <= limit_price
            elif action == "Sell":
                can_fill = tick_price >= limit_price

        response = {
            "operation_status_code": "BT-00000" if can_fill else "LOCAL-NOT-FILLED",
            "operation_message": "local fill" if can_fill else "local limit not filled",
            "order": {"id": order_id, "code": code, **params},
        }
        self.on_order("local_order", response=response)

        if not can_fill:
            return response

        if action == "Buy":
            self.local_position += quantity
        elif action == "Sell":
            self.local_position -= quantity

        deal = {
            "operation_type": "New",
            "operation_code": "BT-00000",
            "trade_id": order_id,
            "code": code,
            "product_type": product_type,
            "action": action,
            "price": tick_price,
            "quantity": quantity,
            "price_type": price_type,
            "order_type": params.get("order_type"),
            "exchange_ts": self.last_tick["timestamp"] if self.last_tick else datetime.now().timestamp(),
            "order_cond": params.get("order_cond"),
            "order_lot": params.get("order_lot"),
        }
        self.local_trades.append(deal)
        self.on_deal("local_deal", response=deal)
        return response


class FixedPriceTsst(BacktestSafeTsst):
    """固定價格策略：買一次，並在指定時間後出場。"""

    def __init__(self, config: AppConfig, **kwargs: Any):
        super().__init__(**kwargs)
        self.config = config
        self.buy_flag = True
        self.already_bought = False
        self.already_sold = False

    def on_order(self, sender: str, response: dict, **kwargs: Any) -> None:
        print(f"Order received: {response}")
        order = response.get("order", {})
        action = order.get("action")
        status_code = response.get("operation_status_code")

        if action == "Buy":
            if status_code == "BT-00000":
                self.buy_flag = False
            else:
                self.already_bought = False
        elif action == "Sell" and status_code != "BT-00000":
            self.already_sold = False

    def on_stock_tick(self, sender: str, response: dict, **kwargs: Any) -> None:
        timestamp = response.get("timestamp")
        tick_time = datetime.fromtimestamp(timestamp) if timestamp is not None else datetime.now()

        if not self.already_bought and self.buy_flag:
            self.already_bought = True
            self.record_signal(
                code=self.config.code,
                action="Buy",
                signal_type="fixed_entry",
                signal_time=tick_time,
                price=self.config.buy_price,
                reason=f"fixed limit price {self.config.buy_price}",
            )
            print(
                f"Fixed entry: buy {self.config.code}, "
                f"price={self.config.buy_price}, lots={self.config.lots}, shares={self.config.quantity}"
            )
            self.create_order(
                code=self.config.code,
                product_type="Stock",
                params={
                    "price": self.config.buy_price,
                    "quantity": self.config.quantity,
                    "action": "Buy",
                    "price_type": "LMT",
                    "order_type": "ROD",
                    "order_cond": "Cash",
                    "order_lot": "Common",
                },
            )

        if timestamp is None:
            return

        if not self.already_sold and is_after_exit_time(tick_time, self.config):
            self.already_sold = True
            exit_price = float(response.get("close", 0) or 0)
            self.record_signal(
                code=self.config.code,
                action="Sell",
                signal_type="time_exit",
                signal_time=tick_time,
                price=exit_price,
                reason=f"after {self.config.sell_hour:02d}:{self.config.sell_minute:02d}",
            )
            print(f"Fixed exit: sell {self.config.code} at market, time={tick_time}")
            self.create_order(
                code=self.config.code,
                product_type="Stock",
                params={
                    "price": 0,
                    "quantity": self.config.quantity,
                    "action": "Sell",
                    "price_type": "MKT",
                    "order_type": "ROD",
                    "order_cond": "Cash",
                    "order_lot": "Common",
                },
            )


class MovingAverageTsst(BacktestSafeTsst):
    """MA、RSI、MACD 三種技術指標策略共用的回測執行類別。"""

    def __init__(self, config: AppConfig, **kwargs: Any):
        super().__init__(**kwargs)
        self.config = config
        self.is_entry = False
        self.is_exit = False
        self.is_bought = False
        self.is_sold = False
        self.current_kbar_count = 0
        self.previous_snapshot: dict[str, float | None] | None = None
        self.entry_price: float | None = None
        self.bars_since_entry = 0
        self.cooldown_remaining = 0
        self.current_trading_day: date | None = None
        self.entries_today = 0

    def on_stock_tick(self, sender: str, response: dict, **kwargs: Any) -> None:
        # 每次收到 tick 時更新 K 線；只有新 K 棒完成時才進行策略判斷。
        kbar = self.get_kbar(
            response["code"],
            unit=self.config.kbar_unit,
            freq=self.config.kbar_freq,
            exprs=indicator_exprs(self.config),
        )
        kbar = add_macd_columns(kbar, self.config)

        if kbar.shape[0] < 2:
            return

        if self.current_kbar_count == 0:
            self.current_kbar_count = kbar.shape[0]
            return

        if kbar.shape[0] == self.current_kbar_count:
            return

        self.current_kbar_count = kbar.shape[0]
        last_kbar = kbar.tail(2).head(1).to_dicts()[0]
        close = number_or_none(last_kbar.get("Close"))
        snapshot = indicator_snapshot(last_kbar)
        tick_time = datetime.fromtimestamp(response["timestamp"])

        if close is None or not self._has_required_indicators(snapshot):
            return

        self._roll_trading_day(tick_time)
        if self.cooldown_remaining > 0 and not self.is_bought:
            self.cooldown_remaining -= 1
        if self.is_bought:
            self.bars_since_entry += 1

        self._print_bar(response["code"], tick_time, close, snapshot)

        if is_after_exit_time(tick_time, self.config):
            self._force_exit(response["code"], tick_time, close, snapshot)
            self.previous_snapshot = snapshot
            return

        if self.is_bought and not self.is_sold and not self.is_exit:
            self._try_exit(response["code"], tick_time, close, snapshot)
        elif self._can_enter(tick_time):
            self._try_entry(response["code"], tick_time, close, snapshot)

        self.previous_snapshot = snapshot

    def on_deal(self, sender: str, response: dict, **kwargs: Any) -> None:
        print(f"Deal received: {response}")
        if response["action"] == "Buy":
            self.is_bought = True
            self.is_sold = False
            self.entry_price = float(response["price"])
            self.bars_since_entry = 0
            self.entries_today += 1
        elif response["action"] == "Sell":
            self.is_sold = True
            self.is_bought = False
            self.is_exit = False
            self.entry_price = None
            self.bars_since_entry = 0
            self.cooldown_remaining = self.config.cooldown_bars

    def on_order(self, sender: str, response: dict, **kwargs: Any) -> None:
        print(f"Order received: {response}")
        action = response.get("order", {}).get("action")
        is_success = response.get("operation_status_code") == "BT-00000"

        if action == "Buy" and not is_success:
            self.is_entry = False
        elif action == "Sell" and not is_success:
            self.is_exit = False

    def _has_required_indicators(self, snapshot: dict[str, float | None]) -> bool:
        if self.config.strategy == "rsi":
            required = ["RSI"]
        elif self.config.strategy == "macd":
            required = ["MACD", "MACD_SIGNAL"]
        else:
            required = ["MA_FAST", "MA_MID", "MA_SLOW"]
        return all(snapshot.get(column) is not None for column in required)

    def _roll_trading_day(self, tick_time: datetime) -> None:
        trading_day = tick_time.date()
        if trading_day == self.current_trading_day:
            return
        self.current_trading_day = trading_day
        self.entries_today = 0
        self.cooldown_remaining = 0

    def _can_enter(self, tick_time: datetime) -> bool:
        current_minute = tick_time.hour * 60 + tick_time.minute
        start_minute = self.config.entry_start_hour * 60 + self.config.entry_start_minute
        cutoff_minute = self.config.entry_cutoff_hour * 60 + self.config.entry_cutoff_minute
        block_start = self.config.entry_block_start_hour * 60 + self.config.entry_block_start_minute
        block_end = self.config.entry_block_end_hour * 60 + self.config.entry_block_end_minute
        is_blocked_time = block_start <= current_minute < block_end
        return (
            not self.is_bought
            and not self.is_entry
            and not self.is_exit
            and self.cooldown_remaining == 0
            and self.entries_today < self.config.max_entries_per_day
            and start_minute <= current_minute <= cutoff_minute
            and not is_blocked_time
        )

    def _is_ma_bullish_cross(self, snapshot: dict[str, float | None]) -> bool:
        if self.previous_snapshot is None:
            return False
        previous_fast = self.previous_snapshot.get("MA_FAST")
        previous_mid = self.previous_snapshot.get("MA_MID")
        fast = snapshot.get("MA_FAST")
        mid = snapshot.get("MA_MID")
        return (
            previous_fast is not None
            and previous_mid is not None
            and fast is not None
            and mid is not None
            and previous_fast <= previous_mid
            and fast > mid
        )

    def _is_ma_bearish_cross(self, snapshot: dict[str, float | None]) -> bool:
        if self.previous_snapshot is None:
            return False
        previous_fast = self.previous_snapshot.get("MA_FAST")
        previous_mid = self.previous_snapshot.get("MA_MID")
        fast = snapshot.get("MA_FAST")
        mid = snapshot.get("MA_MID")
        return (
            previous_fast is not None
            and previous_mid is not None
            and fast is not None
            and mid is not None
            and previous_fast >= previous_mid
            and fast < mid
        )

    def _is_macd_bullish_cross(self, snapshot: dict[str, float | None]) -> bool:
        if self.previous_snapshot is None:
            return False
        previous_macd = self.previous_snapshot.get("MACD")
        previous_signal = self.previous_snapshot.get("MACD_SIGNAL")
        macd = snapshot.get("MACD")
        signal = snapshot.get("MACD_SIGNAL")
        return (
            previous_macd is not None
            and previous_signal is not None
            and macd is not None
            and signal is not None
            and previous_macd <= previous_signal
            and macd > signal
        )

    def _is_macd_bearish_cross(self, snapshot: dict[str, float | None]) -> bool:
        if self.previous_snapshot is None:
            return False
        previous_macd = self.previous_snapshot.get("MACD")
        previous_signal = self.previous_snapshot.get("MACD_SIGNAL")
        macd = snapshot.get("MACD")
        signal = snapshot.get("MACD_SIGNAL")
        return (
            previous_macd is not None
            and previous_signal is not None
            and macd is not None
            and signal is not None
            and previous_macd >= previous_signal
            and macd < signal
        )

    def _print_bar(
        self,
        code: str,
        tick_time: datetime,
        close: float,
        snapshot: dict[str, float | None],
    ) -> None:
        print(
            f"{tick_time:%H:%M:%S} {code} "
            f"Close={close:.2f} "
            f"MA{self.config.ma_fast_period}={format_indicator_value(snapshot['MA_FAST'])} "
            f"MA{self.config.ma_mid_period}={format_indicator_value(snapshot['MA_MID'])} "
            f"MA{self.config.ma_slow_period}={format_indicator_value(snapshot['MA_SLOW'])} "
            f"RSI={format_indicator_value(snapshot['RSI'])} "
            f"MACD={format_indicator_value(snapshot['MACD'], 4)}/"
            f"{format_indicator_value(snapshot['MACD_SIGNAL'], 4)}"
        )

    def _entry_reasons(self, close: float, snapshot: dict[str, float | None]) -> list[str]:
        if self.config.strategy == "rsi":
            return self._rsi_entry_reasons(snapshot)
        if self.config.strategy == "macd":
            return self._macd_entry_reasons(snapshot)
        return self._ma_entry_reasons(close, snapshot)

    def _ma_entry_reasons(self, close: float, snapshot: dict[str, float | None]) -> list[str]:
        ma_slow = snapshot["MA_SLOW"]
        reasons = []

        distance_ok = (
            ma_slow is not None
            and close > ma_slow
            and (
                self.config.entry_near_ma_points <= 0
                or close - ma_slow <= self.config.entry_near_ma_points
            )
        )
        if distance_ok:
            reasons.append(f"close > MA{self.config.ma_slow_period}")
        if self._is_ma_bullish_cross(snapshot):
            reasons.append(
                f"MA{self.config.ma_fast_period} bullish crossover MA{self.config.ma_mid_period}"
            )
        return reasons if len(reasons) == 2 else []

    def _rsi_entry_reasons(self, snapshot: dict[str, float | None]) -> list[str]:
        rsi = snapshot["RSI"]
        rsi_in_range = (
            rsi is not None
            and self.config.rsi_buy_above <= rsi <= self.config.rsi_buy_below
        )
        rsi_in_blocked_range = (
            self.config.use_rsi_block
            and rsi is not None
            and self.config.rsi_block_low <= rsi < self.config.rsi_block_high
        )
        if not rsi_in_range or rsi_in_blocked_range:
            return []
        rsi_reason = f"RSI {self.config.rsi_buy_above:g}-{self.config.rsi_buy_below:g}"
        if self.config.use_rsi_block:
            rsi_reason += f", excluding {self.config.rsi_block_low:g}-{self.config.rsi_block_high:g}"
        return [rsi_reason]

    def _macd_entry_reasons(self, snapshot: dict[str, float | None]) -> list[str]:
        if self._is_macd_bullish_cross(snapshot):
            return ["MACD bullish crossover signal"]
        return []

    def _exit_signal(self, close: float, snapshot: dict[str, float | None]) -> tuple[str, list[str]] | None:
        if self.entry_price is not None:
            return_pct = (close - self.entry_price) / self.entry_price
            if self.config.stop_loss_pct > 0 and return_pct <= -self.config.stop_loss_pct:
                return "stop_loss", [f"return {return_pct:.2%}"]
            if self.config.take_profit_pct > 0 and return_pct >= self.config.take_profit_pct:
                return "take_profit", [f"return {return_pct:.2%}"]

        if self.bars_since_entry < self.config.min_hold_bars:
            return None

        reasons = []
        if self.config.strategy == "rsi":
            rsi = snapshot["RSI"]
            if rsi is not None and rsi < self.config.rsi_sell_below:
                reasons.append(f"RSI < {self.config.rsi_sell_below:g}")
        elif self.config.strategy == "macd":
            if self._is_macd_bearish_cross(snapshot):
                reasons.append("MACD bearish crossover")
        else:
            ma_slow = snapshot["MA_SLOW"]
            if ma_slow is not None and close < ma_slow:
                reasons.append(f"close < MA{self.config.ma_slow_period}")
            if self._is_ma_bearish_cross(snapshot):
                reasons.append(
                    f"MA{self.config.ma_fast_period} bearish crossover MA{self.config.ma_mid_period}"
                )
        return ("technical_exit", reasons) if reasons else None

    def _try_entry(
        self,
        code: str,
        tick_time: datetime,
        close: float,
        snapshot: dict[str, float | None],
    ) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return

        self.is_entry = True
        reason_text = ", ".join(reasons)
        # 先記錄訊號，再送出模擬委託，方便之後對照買賣點。
        self.record_signal(
            code=code,
            action="Buy",
            signal_type="entry",
            signal_time=tick_time,
            price=close,
            reason=reason_text,
            indicators=snapshot,
        )
        print(f"MA/RSI/MACD entry: buy {code} at market, reference close={close:.2f}; {reason_text}")
        self.create_order(
            code=code,
            product_type="Stock",
            params={
                "price": close,
                "quantity": self.config.quantity,
                "action": "Buy",
                "price_type": "MKT",
                "order_type": "ROD",
                "order_cond": "Cash",
                "order_lot": "Common",
            },
        )

    def _try_exit(
        self,
        code: str,
        tick_time: datetime,
        close: float,
        snapshot: dict[str, float | None],
    ) -> None:
        signal = self._exit_signal(close, snapshot)
        if signal is None:
            return

        signal_type, reasons = signal
        self.is_exit = True
        self.is_entry = False
        reason_text = ", ".join(reasons)
        self.record_signal(
            code=code,
            action="Sell",
            signal_type=signal_type,
            signal_time=tick_time,
            price=close,
            reason=reason_text,
            indicators=snapshot,
        )
        print(f"MA/RSI/MACD exit: sell {code}; {reason_text}")
        self._sell_market(code, close)

    def _force_exit(
        self,
        code: str,
        tick_time: datetime,
        close: float,
        snapshot: dict[str, float | None],
    ) -> None:
        # 接近收盤或指定時間後，若仍有部位就強制出場，避免留倉。
        if self.is_bought and not self.is_sold:
            self.is_exit = True
            self.is_entry = False
            reason = f"after {self.config.sell_hour:02d}:{self.config.sell_minute:02d}"
            self.record_signal(
                code=code,
                action="Sell",
                signal_type="force_exit",
                signal_time=tick_time,
                price=close,
                reason=reason,
                indicators=snapshot,
            )
            print(f"Force exit {code}; {reason}")
            self._sell_market(code, close)
            return

        if self.is_sold:
            self.unsubscribe({"codes": [{"code": code, "market": "Stock"}]})

    def _sell_market(self, code: str, close: float) -> None:
        self.create_order(
            code=code,
            product_type="Stock",
            params={
                "price": close,
                "quantity": self.config.quantity,
                "action": "Sell",
                "price_type": "MKT",
                "order_type": "ROD",
                "order_cond": "Cash",
                "order_lot": "Common",
            },
        )


def is_after_exit_time(tick_time: datetime, config: AppConfig) -> bool:
    return tick_time.hour > config.sell_hour or (
        tick_time.hour == config.sell_hour and tick_time.minute >= config.sell_minute
    )


def parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def iter_dates(start: str, end: str):
    current = parse_ymd(start)
    end_date = parse_ymd(end)
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def sino_ts_to_local_timestamp(value: int | float) -> float:
    if value > 10_000_000_000_000:
        return value / 1e9 - 8 * 3600
    return float(value)


def login_sinopac() -> sj.Shioaji:
    """登入永豐 Shioaji，供 sinopac tick 回測資料使用。"""

    api = sj.Shioaji(simulation=True)
    api.login(api_key=require_env("API_KEY"), secret_key=require_env("API_SECRET"))

    ca_path = os.getenv("CA_PATH", "").strip()
    ca_password = os.getenv("CA_PASSWORD", "").strip()
    if ca_path and ca_password:
        try:
            api.activate_ca(ca_path=ca_path, ca_passwd=ca_password)
        except Exception as exc:
            print(f"CA activation skipped: {exc}")

    return api


def fetch_sinopac_ticks(api: sj.Shioaji, config: AppConfig) -> list[dict[str, Any]]:
    """依照回測日期區間逐日抓 tick，整理成 TSST 可回放的格式。"""

    contract = api.Contracts.Stocks[config.code]
    all_ticks: list[dict[str, Any]] = []

    for day in iter_dates(config.backtest_start, config.backtest_end):
        day_text = day.strftime("%Y-%m-%d")
        try:
            ticks = api.ticks(contract=contract, date=day_text)
            df = pl.DataFrame({**ticks})
        except Exception as exc:
            print(f"[{day_text}] skip: {exc}")
            continue

        if df.is_empty():
            print(f"[{day_text}] no ticks")
            continue

        day_ticks = []
        for tick in df.iter_rows(named=True):
            day_ticks.append(
                {
                    "timestamp": sino_ts_to_local_timestamp(tick["ts"]),
                    "market_type": "Stock",
                    "code": config.code,
                    "close": float(tick["close"]),
                    "qty": int(tick.get("volume", tick.get("qty", 0)) or 0),
                    "tick_type": int(tick.get("tick_type", 0) or 0),
                    "is_simulate": False,
                    "is_backfilling": False,
                }
            )

        all_ticks.extend(day_ticks)
        print(f"[{day_text}] fetched {len(day_ticks)} ticks")

    all_ticks.sort(key=lambda item: item["timestamp"])
    return all_ticks


def replay_ticks(tsst: BacktestSafeTsst, ticks: list[dict[str, Any]]) -> None:
    """把歷史 tick 依時間順序餵回策略，模擬當時盤中的觸發流程。"""

    for index, tick in enumerate(ticks, start=1):
        tsst.last_tick = tick
        tsst.quote_obj.quote_manager.add_tick(tick)
        if tick["market_type"] == "Stock":
            tsst.on_stock_tick("sinopac_replay", response=tick)

        if index % 10000 == 0:
            tick_time = datetime.fromtimestamp(tick["timestamp"])
            print(f"Replayed {index} ticks, current={tick_time:%Y-%m-%d %H:%M:%S}")

    print(f"Replay finished: {len(ticks)} ticks")
    print(f"Local position: {tsst.local_position}")
    print(f"Local trades: {len(tsst.local_trades)}")
    print(f"Local signals: {len(tsst.local_signals)}")


def build_kbar_rows_from_ticks(ticks: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    """把 tick 聚合成 K 線，並補上 MA、RSI、MACD 給圖表使用。"""

    if not ticks:
        return []

    df = pl.DataFrame(
        {
            "dt": [datetime.fromtimestamp(float(tick["timestamp"])) for tick in ticks],
            "code": [tick["code"] for tick in ticks],
            "close": [float(tick["close"]) for tick in ticks],
            "volume": [int(tick.get("qty", 0) or 0) for tick in ticks],
        }
    )

    filtered = df.filter(pl.col("code") == config.code)
    if filtered.is_empty():
        return []

    kbar_df = (
        filtered.with_columns(pl.col("dt").dt.truncate(config.kbar_interval).alias("kbar_time"))
        .group_by("kbar_time", maintain_order=True)
        .agg(
            [
                pl.col("close").first().alias("Open"),
                pl.col("close").max().alias("High"),
                pl.col("close").min().alias("Low"),
                pl.col("close").last().alias("Close"),
                pl.col("volume").sum().alias("Volume"),
            ]
        )
        .sort("kbar_time")
    )
    kbar_df = add_indicators(kbar_df, config)

    rows = []
    for row in kbar_df.iter_rows(named=True):
        kbar_time = row["kbar_time"]
        rows.append(
            {
            "time": int(kbar_time.timestamp()),
            "datetime": kbar_time.strftime("%Y-%m-%d %H:%M:%S"),
            "code": config.code,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
            "ma_fast": number_or_none(row.get("MA_FAST")),
            "ma_mid": number_or_none(row.get("MA_MID")),
            "ma_slow": number_or_none(row.get("MA_SLOW")),
            "rsi": number_or_none(row.get("RSI")),
            "macd": number_or_none(row.get("MACD")),
            "macd_signal": number_or_none(row.get("MACD_SIGNAL")),
            "macd_hist": number_or_none(row.get("MACD_HIST")),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        pl.DataFrame(rows).write_csv(path)
        return
    path.write_text("", encoding="utf-8")


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _format_number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{float(value):,.{digits}f}"


def _format_money(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value):,.0f}"


def _bucket_timestamp(timestamp: float, config: AppConfig) -> int:
    if config.kbar_unit == "m":
        seconds = max(config.kbar_freq, 1) * 60
        return int(timestamp // seconds * seconds)
    return int(timestamp)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


def _display_tick_source(config: AppConfig) -> str:
    """把程式內部資料來源代碼轉成報表上比較好讀的文字。"""

    if config.tick_source == "sinopac":
        return "永豐 Shioaji API ticks"
    return "TSST 回測資料"


def _build_trade_rows(tsst: BacktestSafeTsst, config: AppConfig) -> list[dict[str, Any]]:
    """整理實際成交紀錄，輸出為 trades.csv。"""

    rows = []
    for trade in tsst.local_trades:
        ts = datetime.fromtimestamp(float(trade["exchange_ts"]))
        quantity = int(trade["quantity"])
        price = float(trade["price"])
        gross = price * quantity
        fee = gross * config.stock_fee_rate
        tax = gross * config.stock_tax_rate if trade["action"] == "Sell" else 0.0
        rows.append(
            {
                "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": int(ts.timestamp()),
                "trade_id": trade["trade_id"],
                "code": trade["code"],
                "action": trade["action"],
                "price": price,
                "quantity": quantity,
                "gross_amount": gross,
                "fee": fee,
                "tax": tax,
                "net_cash_flow": -gross - fee if trade["action"] == "Buy" else gross - fee - tax,
            }
        )
    return rows


def _build_signal_rows(tsst: BacktestSafeTsst) -> list[dict[str, Any]]:
    """整理策略觸發原因與指標快照，輸出為 signals.csv。"""

    rows = []
    for signal in tsst.local_signals:
        rows.append(
            {
                "datetime": signal["datetime"],
                "timestamp": int(float(signal["exchange_ts"])),
                "code": signal["code"],
                "action": signal["action"],
                "signal_type": signal["signal_type"],
                "price": number_or_none(signal.get("price")),
                "reason": signal.get("reason", ""),
                "ma_fast": number_or_none(signal.get("MA_FAST")),
                "ma_mid": number_or_none(signal.get("MA_MID")),
                "ma_slow": number_or_none(signal.get("MA_SLOW")),
                "rsi": number_or_none(signal.get("RSI")),
                "macd": number_or_none(signal.get("MACD")),
                "macd_signal": number_or_none(signal.get("MACD_SIGNAL")),
                "macd_hist": number_or_none(signal.get("MACD_HIST")),
            }
        )
    return rows


def _build_pnl_rows(trades_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """將買進與賣出配對，計算每筆交易的損益、稅費與報酬率。"""

    open_lots: list[dict[str, Any]] = []
    pnl_rows = []

    for row in trades_rows:
        quantity = int(row["quantity"])
        if row["action"] == "Buy":
            open_lots.append(
                {
                    "datetime": row["datetime"],
                    "remaining_quantity": quantity,
                    "price": row["price"],
                    "remaining_fee": row["fee"],
                    "gross_amount": row["gross_amount"],
                }
            )
            continue

        remaining = quantity
        while remaining > 0 and open_lots:
            lot = open_lots[0]
            lot_remaining = int(lot["remaining_quantity"])
            matched_qty = min(remaining, lot_remaining)
            buy_cost = float(lot["price"]) * matched_qty
            sell_value = float(row["price"]) * matched_qty
            buy_fee = float(lot["remaining_fee"]) * matched_qty / lot_remaining if lot_remaining else 0.0
            sell_fee = float(row["fee"]) * matched_qty / quantity if quantity else 0.0
            sell_tax = float(row["tax"]) * matched_qty / quantity if quantity else 0.0
            net_pnl = sell_value - buy_cost - buy_fee - sell_fee - sell_tax
            pnl_rows.append(
                {
                    "code": row["code"],
                    "buy_datetime": lot["datetime"],
                    "sell_datetime": row["datetime"],
                    "quantity": matched_qty,
                    "buy_price": float(lot["price"]),
                    "sell_price": float(row["price"]),
                    "buy_amount": buy_cost,
                    "sell_amount": sell_value,
                    "gross_pnl": sell_value - buy_cost,
                    "fee": buy_fee + sell_fee,
                    "tax": sell_tax,
                    "net_pnl": net_pnl,
                    "return_pct": net_pnl / buy_cost if buy_cost else 0.0,
                }
            )
            lot["remaining_quantity"] = lot_remaining - matched_qty
            lot["remaining_fee"] = float(lot["remaining_fee"]) - buy_fee
            remaining -= matched_qty
            if lot["remaining_quantity"] <= 0:
                open_lots.pop(0)

    return pnl_rows


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>'


def _trade_table_rows(pnl_rows: list[dict[str, Any]]) -> str:
    if not pnl_rows:
        return '<tr><td colspan="13" class="text-center text-muted">沒有已完成的買賣配對</td></tr>'

    rows = []
    for row in pnl_rows:
        rows.append(
            f"""
          <tr>
            <td>{_escape(row['code'])}</td>
            <td>{_escape(row['buy_datetime'])}</td>
            <td class="text-end">{_format_number(row['buy_price'], 4)}</td>
            <td class="text-end">{int(row['quantity']) // 1000:,}</td>
            <td class="text-end">{int(row['quantity']):,}</td>
            <td class="text-end">{_format_money(row['buy_amount'])}</td>
            <td>{_escape(row['sell_datetime'])}</td>
            <td class="text-end">{_format_number(row['sell_price'], 4)}</td>
            <td class="text-end">{_format_money(row['sell_amount'])}</td>
            <td class="text-end">{_format_money(row['fee'])}</td>
            <td class="text-end">{_format_money(row['tax'])}</td>
            <td class="text-end">{_format_money(row['net_pnl'])}</td>
            <td class="text-end">{float(row['return_pct']):.2%}</td>
          </tr>"""
        )
    return "\n".join(rows)


def _signal_table_rows(signal_rows: list[dict[str, Any]]) -> str:
    if not signal_rows:
        return '<tr><td colspan="10" class="text-center text-muted">沒有買賣訊號</td></tr>'

    rows = []
    for row in signal_rows:
        rows.append(
            f"""
          <tr>
            <td>{_escape(row['datetime'])}</td>
            <td>{_escape(row['code'])}</td>
            <td>{'買進' if row['action'] == 'Buy' else '賣出'}</td>
            <td>{_escape(row['signal_type'])}</td>
            <td class="text-end">{_format_number(row['price'], 4)}</td>
            <td>{_escape(row['reason'])}</td>
            <td class="text-end">{_format_number(row['rsi'], 2)}</td>
            <td class="text-end">{_format_number(row['macd'], 4)}</td>
            <td class="text-end">{_format_number(row['macd_signal'], 4)}</td>
            <td class="text-end">{_format_number(row['macd_hist'], 4)}</td>
          </tr>"""
        )
    return "\n".join(rows)


def generate_local_report_legacy(tsst: BacktestSafeTsst, config: AppConfig, ticks: list[dict[str, Any]]) -> Path:
    """舊版 HTML 報表保留作為備份；目前主流程會使用新版報表函式。"""

    REPORT_DIR.mkdir(exist_ok=True)
    ticks_count = len(ticks)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = build_report_prefix(run_id, config)
    output_dir = stock_report_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = output_dir / f"{prefix}_trades.csv"
    pnl_path = output_dir / f"{prefix}_pnl.csv"
    html_path = output_dir / f"{prefix}_report.html"

    trades_rows = []
    for trade in tsst.local_trades:
        ts = datetime.fromtimestamp(float(trade["exchange_ts"]))
        gross = float(trade["price"]) * int(trade["quantity"])
        fee = gross * config.stock_fee_rate
        tax = gross * config.stock_tax_rate if trade["action"] == "Sell" else 0.0
        trades_rows.append(
            {
                "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "trade_id": trade["trade_id"],
                "code": trade["code"],
                "action": trade["action"],
                "price": float(trade["price"]),
                "quantity": int(trade["quantity"]),
                "gross_amount": gross,
                "fee": fee,
                "tax": tax,
                "net_cash_flow": -gross - fee if trade["action"] == "Buy" else gross - fee - tax,
            }
        )

    pl.DataFrame(trades_rows).write_csv(trades_path) if trades_rows else pl.DataFrame().write_csv(trades_path)

    open_lots: list[dict[str, Any]] = []
    pnl_rows = []
    for row in trades_rows:
        quantity = int(row["quantity"])
        if row["action"] == "Buy":
            open_lots.append(
                {
                    "datetime": row["datetime"],
                    "quantity": quantity,
                    "price": row["price"],
                    "fee": row["fee"],
                    "gross_amount": row["gross_amount"],
                }
            )
            continue

        remaining = quantity
        while remaining > 0 and open_lots:
            lot = open_lots[0]
            matched_qty = min(remaining, int(lot["quantity"]))
            buy_cost = float(lot["price"]) * matched_qty
            sell_value = float(row["price"]) * matched_qty
            buy_fee = float(lot["fee"]) * matched_qty / int(lot["quantity"])
            sell_fee = float(row["fee"]) * matched_qty / quantity
            sell_tax = float(row["tax"]) * matched_qty / quantity
            pnl_rows.append(
                {
                    "code": row["code"],
                    "buy_datetime": lot["datetime"],
                    "sell_datetime": row["datetime"],
                    "quantity": matched_qty,
                    "buy_price": float(lot["price"]),
                    "sell_price": float(row["price"]),
                    "buy_amount": float(lot["price"]) * matched_qty,
                    "sell_amount": float(row["price"]) * matched_qty,
                    "gross_pnl": sell_value - buy_cost,
                    "fee": buy_fee + sell_fee,
                    "tax": sell_tax,
                    "net_pnl": sell_value - buy_cost - buy_fee - sell_fee - sell_tax,
                    "return_pct": (sell_value - buy_cost - buy_fee - sell_fee - sell_tax) / buy_cost
                    if buy_cost
                    else 0.0,
                }
            )
            lot["quantity"] = int(lot["quantity"]) - matched_qty
            remaining -= matched_qty
            if lot["quantity"] <= 0:
                open_lots.pop(0)

    pl.DataFrame(pnl_rows).write_csv(pnl_path) if pnl_rows else pl.DataFrame().write_csv(pnl_path)

    total_net_pnl = sum(float(row["net_pnl"]) for row in pnl_rows)
    total_return = total_net_pnl / config.initial_capital if config.initial_capital else 0.0
    wins = sum(1 for row in pnl_rows if float(row["net_pnl"]) > 0)
    win_rate = wins / len(pnl_rows) if pnl_rows else 0.0
    gross_profit = sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) > 0)
    gross_loss = sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) < 0)
    ending_capital = config.initial_capital + total_net_pnl

    kbar_rows = build_kbar_rows_from_ticks(ticks, config)

    marker_rows = []
    for row in trades_rows:
        marker_rows.append(
            {
                "code": row["code"],
                "type": "entry" if row["action"] == "Buy" else "exit",
                "dt": row["datetime"],
                "qty": row["quantity"],
                "price": row["price"],
                "origin_price": row["price"],
            }
        )

    trade_table_rows = "\n".join(
        f"""
                    <tr>
                        <td>{row['code']}</td>
                        <td>買進</td>
                        <td>{row['buy_datetime']}</td>
                        <td>{row['buy_price']:.4f}</td>
                        <td>{row['quantity'] // 1000:,}</td>
                        <td>{row['quantity']:,}</td>
                        <td>{row['buy_amount']:,.0f}</td>
                        <td>賣出</td>
                        <td>{row['sell_datetime']}</td>
                        <td>{row['sell_price']:.4f}</td>
                        <td>{row['sell_amount']:,.0f}</td>
                        <td>{row['fee']:,.0f}</td>
                        <td>{row['tax']:,.0f}</td>
                        <td>{row['net_pnl']:,.0f}</td>
                        <td>{row['return_pct']:.2%}</td>
                    </tr>"""
        for row in pnl_rows
    )

    if not trade_table_rows:
        trade_table_rows = """
                    <tr>
                        <td colspan="14" class="text-center">本次期間沒有已平倉交易</td>
                    </tr>"""

    html_path.write_text(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{config.code} 回測報告</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/remixicon@3.5.0/fonts/remixicon.css" rel="stylesheet">
  <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    body {{ background: #fafafa; color: #222; font-family: "Microsoft JhengHei", Arial, sans-serif; }}
    .section-title {{ font-size: 1.25rem; font-weight: 700; margin: 56px 0 20px; }}
    .section-bar {{ border-bottom: 2px solid #ddd; margin-bottom: 16px; }}
    .second-title {{ color: #4183b2; font-weight: 700; margin-bottom: 16px; }}
    .metric {{ background: #efefef; min-height: 40px; padding: 8px 16px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; font-weight: 700; }}
    .table th, .table td {{ font-size: 0.92rem; white-space: nowrap; vertical-align: middle; }}
    .tooltip-box {{ position: absolute; top: 0; z-index: 10; display: none; flex-direction: column; gap: 4px; background: #666; color: white; padding: 8px 12px; margin: 5px; border-radius: 5px; font-weight: 700; pointer-events: none; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="d-flex align-items-center section-title mt-5">
      <i class="ri-bar-chart-box-fill me-2"></i>
      K 線交易圖
    </div>
    <div class="section-bar"></div>
    <div class="mb-3 position-relative">
      <div id="chart" style="width: 100%; height: 500px;"></div>
      <div id="tooltip" class="tooltip-box"></div>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-information-fill me-2"></i>
      回測資訊
    </div>
    <div class="section-bar"></div>
    <div class="row">
      <div class="col-md-6">
        <div class="second-title">策略設定</div>
        <div class="metric"><span>股票代號</span><span>{config.code}</span></div>
        <div class="metric"><span>策略</span><span>{config.strategy}</span></div>
        <div class="metric"><span>資料來源</span><span>永豐 Shioaji API ticks</span></div>
        <div class="metric"><span>回測期間</span><span>{config.backtest_start} ~ {config.backtest_end}</span></div>
        <div class="metric"><span>每次進出場張數</span><span>{config.lots:,} 張（{config.quantity:,} 股）</span></div>
        <div class="metric"><span>Tick 筆數</span><span>{ticks_count:,}</span></div>
      </div>
      <div class="col-md-6">
        <div class="second-title">股票費用設定</div>
        <div class="metric"><span>手續費率</span><span>{config.stock_fee_rate:.6f}</span></div>
        <div class="metric"><span>交易稅率</span><span>{config.stock_tax_rate:.6f}</span></div>
        <div class="metric"><span>初始資金</span><span>{config.initial_capital:,.0f}</span></div>
        <div class="metric"><span>期末資金</span><span>{ending_capital:,.0f}</span></div>
        <div class="metric"><span>期末持倉</span><span>{tsst.local_position}</span></div>
      </div>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-file-list-fill me-2"></i>
      交易明細
    </div>
    <div class="section-bar"></div>
    <div class="table-responsive">
      <table class="table table-bordered table-sm mb-4">
        <thead>
          <tr>
            <th>代號</th>
            <th>進場</th>
            <th>進場時間</th>
            <th>進場價</th>
            <th>張數</th>
            <th>股數</th>
            <th>進場金額</th>
            <th>出場</th>
            <th>出場時間</th>
            <th>出場價</th>
            <th>出場金額</th>
            <th>手續費</th>
            <th>交易稅</th>
            <th>損益</th>
            <th>報酬率</th>
          </tr>
        </thead>
        <tbody>
{trade_table_rows}
        </tbody>
      </table>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-pie-chart-2-fill me-2"></i>
      回測統計
    </div>
    <div class="section-bar"></div>
    <div class="row mb-5">
      <div class="col-md-6">
        <div class="metric"><span>交易筆數</span><span>{len(trades_rows)}</span></div>
        <div class="metric"><span>平倉筆數</span><span>{len(pnl_rows)}</span></div>
        <div class="metric"><span>勝率</span><span>{win_rate:.2%}</span></div>
        <div class="metric"><span>報酬率</span><span>{total_return:.4%}</span></div>
      </div>
      <div class="col-md-6">
        <div class="metric"><span>總淨損益</span><span>{total_net_pnl:,.0f}</span></div>
        <div class="metric"><span>總獲利</span><span>{gross_profit:,.0f}</span></div>
        <div class="metric"><span>總虧損</span><span>{gross_loss:,.0f}</span></div>
        <div class="metric"><span>輸出檔案</span><span>{trades_path.name}</span></div>
      </div>
    </div>
  </div>
  <script>
    const kbar = {json.dumps(kbar_rows, ensure_ascii=False)};
    const tradeMarkers = {json.dumps(marker_rows, ensure_ascii=False)};
    if (kbar.length === 0) {{
      document.getElementById('chart').innerHTML = '<div class="d-flex align-items-center justify-content-center h-100 text-muted">沒有 K 線資料可顯示</div>';
    }} else {{
    const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
      layout: {{ textColor: 'black', background: {{ type: 'solid', color: 'white' }} }},
      timeScale: {{ timeVisible: true }},
      localization: {{
        timeFormatter: (timestamp) => new Date(timestamp * 1000).toLocaleString('zh-TW', {{ timeZone: 'Asia/Taipei' }})
      }}
    }});
    const series = chart.addCandlestickSeries({{
      upColor: '#ef5350',
      downColor: '#26a69a',
      borderVisible: false,
      wickUpColor: '#ef5350',
      wickDownColor: '#26a69a'
    }});
    series.setData(kbar.map((item) => ({{
      time: new Date(item.time).valueOf() / 1000,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close
    }})));
    series.setMarkers(tradeMarkers.map((item) => ({{
      time: new Date(item.dt).valueOf() / 1000,
      position: item.type === 'entry' ? 'belowBar' : 'aboveBar',
      color: item.type === 'entry' ? 'red' : 'green',
      shape: item.type === 'entry' ? 'arrowUp' : 'arrowDown',
      text: `${{item.type === 'entry' ? '進場' : '出場'}} ${{item.qty}}`
    }})));
    chart.timeScale().fitContent();

    const tooltip = document.getElementById('tooltip');
    chart.subscribeCrosshairMove((param) => {{
      if (!param || !param.time || !param.seriesData.get(series)) {{
        tooltip.style.display = 'none';
        return;
      }}
      const data = param.seriesData.get(series);
      const date = new Date(param.time * 1000);
      tooltip.innerHTML = `
        <div>時間：${{date.toLocaleString('zh-TW', {{ timeZone: 'Asia/Taipei' }})}}</div>
        <div>開：${{data.open}}</div>
        <div>高：${{data.high}}</div>
        <div>低：${{data.low}}</div>
        <div>收：${{data.close}}</div>
      `;
      tooltip.style.display = 'flex';
    }});
    }}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    print(f"Report generated: {html_path}")
    print(f"Trade records: {trades_path}")
    print(f"PnL records: {pnl_path}")
    return html_path


def generate_local_report(tsst: BacktestSafeTsst, config: AppConfig, ticks: list[dict[str, Any]]) -> Path:
    """產生本次回測的 CSV 與 HTML 圖表報告。"""

    REPORT_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = build_report_prefix(run_id, config)
    run_dir = stock_report_dir(config) / prefix
    run_dir.mkdir(parents=True, exist_ok=True)
    trades_path = run_dir / "trades.csv"
    signals_path = run_dir / "signals.csv"
    pnl_path = run_dir / "pnl.csv"
    html_path = run_dir / "report.html"

    trades_rows = _build_trade_rows(tsst, config)
    signal_rows = _build_signal_rows(tsst)
    pnl_rows = _build_pnl_rows(trades_rows)
    kbar_rows = build_kbar_rows_from_ticks(ticks, config)

    _write_csv(trades_path, trades_rows)
    _write_csv(signals_path, signal_rows)
    _write_csv(pnl_path, pnl_rows)

    total_net_pnl = sum(float(row["net_pnl"]) for row in pnl_rows)
    total_return = total_net_pnl / config.initial_capital if config.initial_capital else 0.0
    wins = sum(1 for row in pnl_rows if float(row["net_pnl"]) > 0)
    win_rate = wins / len(pnl_rows) if pnl_rows else 0.0
    gross_profit = sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) > 0)
    gross_loss = sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) < 0)
    ending_capital = config.initial_capital + total_net_pnl

    signal_markers = [
        {
            "time": _bucket_timestamp(float(row["timestamp"]), config),
            "position": "belowBar" if row["action"] == "Buy" else "aboveBar",
            "color": "#ef5350" if row["action"] == "Buy" else "#26a69a",
            "shape": "arrowUp" if row["action"] == "Buy" else "arrowDown",
            "text": ("買訊 " if row["action"] == "Buy" else "賣訊 ") + row["signal_type"],
        }
        for row in signal_rows
    ]

    settings_metrics = "\n".join(
        [
            _metric("股票代號", config.code),
            _metric("執行標籤", config.run_name or "未設定"),
            _metric("策略", config.strategy),
            _metric("資料來源", _display_tick_source(config)),
            _metric("回測期間", f"{config.backtest_start} ~ {config.backtest_end}"),
            _metric("K 線週期", config.kbar_interval),
            _metric("每次張數", f"{config.lots:,} 張 ({config.quantity:,} 股)"),
            _metric("Tick 筆數", f"{len(ticks):,}"),
        ]
    )
    if config.use_rsi:
        rsi_block_text = (
            f"，避開 {config.rsi_block_low:g}–{config.rsi_block_high:g}"
            if config.use_rsi_block
            else "，不避開特定區間"
        )
        rsi_rule_text = (
            f"啟用，週期 {config.rsi_period}，買 "
            f"{config.rsi_buy_above:g}–{config.rsi_buy_below:g}"
            f"{rsi_block_text}，賣 < {config.rsi_sell_below:g}"
        )
    else:
        rsi_rule_text = f"停用，週期 {config.rsi_period}"
    if config.strategy == "macd":
        macd_rule_text = (
            f"策略主訊號，{config.macd_fast_period}/{config.macd_slow_period}/"
            f"{config.macd_signal_period}，進場需上穿 signal"
        )
    else:
        macd_rule_text = (
            f"{'啟用' if config.use_macd else '停用'}，"
            f"{config.macd_fast_period}/{config.macd_slow_period}/{config.macd_signal_period}"
        )
    strategy_rule_text = {
        "ma": (
            f"MA 策略：收盤價站上 MA{config.ma_slow_period}，且 "
            f"MA{config.ma_fast_period} 上穿 MA{config.ma_mid_period}"
        ),
        "rsi": (
            f"RSI 策略：RSI 進入 {config.rsi_buy_above:g}–{config.rsi_buy_below:g}"
            + (
                f"，避開 {config.rsi_block_low:g}–{config.rsi_block_high:g}"
                if config.use_rsi_block
                else ""
            )
        ),
        "macd": "MACD 策略：MACD 上穿 signal",
        "fixed": f"固定價格策略：限價 {config.buy_price:g} 買進",
    }[config.strategy]
    exit_rule_text = {
        "ma": f"跌破 MA{config.ma_slow_period} 或 MA{config.ma_fast_period} 下穿 MA{config.ma_mid_period}",
        "rsi": f"RSI < {config.rsi_sell_below:g}",
        "macd": "MACD 下穿 signal",
        "fixed": f"{config.sell_hour:02d}:{config.sell_minute:02d} 出場",
    }[config.strategy]
    rule_metrics = "\n".join(
        [
            _metric("策略規則", strategy_rule_text),
            _metric("均線", f"MA{config.ma_fast_period} / MA{config.ma_mid_period} / MA{config.ma_slow_period}"),
            _metric("出場訊號", exit_rule_text),
            _metric("慢均線距離限制", "停用" if config.entry_near_ma_points <= 0 else f"{config.entry_near_ma_points:g} 點內"),
            _metric("RSI", rsi_rule_text),
            _metric("MACD", macd_rule_text),
            _metric("進場時段", f"{config.entry_start_hour:02d}:{config.entry_start_minute:02d}–{config.entry_cutoff_hour:02d}:{config.entry_cutoff_minute:02d}"),
            _metric("禁止進場時段", f"{config.entry_block_start_hour:02d}:{config.entry_block_start_minute:02d}–{config.entry_block_end_hour:02d}:{config.entry_block_end_minute:02d}"),
            _metric("停損 / 停利", f"{config.stop_loss_pct:.2%} / {config.take_profit_pct:.2%}"),
            _metric("最少持有 / 冷卻", f"{config.min_hold_bars} / {config.cooldown_bars} 根 K 棒"),
            _metric("每日最多進場", f"{config.max_entries_per_day} 次"),
            _metric("強制出場", f"{config.sell_hour:02d}:{config.sell_minute:02d}"),
        ]
    )
    result_metrics = "\n".join(
        [
            _metric("成交紀錄", len(trades_rows)),
            _metric("訊號紀錄", len(signal_rows)),
            _metric("完成配對", len(pnl_rows)),
            _metric("勝率", f"{win_rate:.2%}"),
            _metric("總報酬率", f"{total_return:.4%}"),
            _metric("淨損益", _format_money(total_net_pnl)),
            _metric("總獲利", _format_money(gross_profit)),
            _metric("總虧損", _format_money(gross_loss)),
            _metric("結束資金", _format_money(ending_capital)),
            _metric("結束部位", f"{tsst.local_position:,} 股"),
        ]
    )
    output_metrics = "\n".join(
        [
            _metric("交易 CSV", trades_path.name),
            _metric("訊號 CSV", signals_path.name),
            _metric("損益 CSV", pnl_path.name),
            _metric("HTML 報表", html_path.name),
        ]
    )

    template = Template(
        """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>$code 回測報告</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/remixicon@3.5.0/fonts/remixicon.css" rel="stylesheet">
  <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    body { background: #f7f7f5; color: #222; font-family: "Microsoft JhengHei", Arial, sans-serif; }
    .section-title { font-size: 1.2rem; font-weight: 700; margin: 44px 0 14px; }
    .section-bar { border-bottom: 2px solid #d9d5ce; margin-bottom: 16px; }
    .subhead { color: #315f72; font-weight: 700; margin-bottom: 12px; }
    .metric { background: #fff; min-height: 40px; padding: 8px 14px; margin-bottom: 8px; display: flex; justify-content: space-between; gap: 16px; align-items: center; border-left: 4px solid #315f72; }
    .metric span { color: #555; }
    .metric strong { text-align: right; }
    .table th, .table td { font-size: 0.9rem; white-space: nowrap; vertical-align: middle; }
    #price-chart, #rsi-chart, #macd-chart { width: 100%; background: white; border: 1px solid #ddd; }
    #price-chart { height: 520px; }
    #rsi-chart, #macd-chart { height: 220px; }
  </style>
</head>
<body>
  <main class="container py-4">
    <div class="d-flex align-items-center section-title mt-2">
      <i class="ri-line-chart-fill me-2"></i>
      K 線與買賣訊號
    </div>
    <div class="section-bar"></div>
    <div id="price-chart"></div>
    <div class="row g-3 mt-1">
      <div class="col-lg-6"><div id="rsi-chart"></div></div>
      <div class="col-lg-6"><div id="macd-chart"></div></div>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-information-fill me-2"></i>
      回測設定
    </div>
    <div class="section-bar"></div>
    <div class="row">
      <div class="col-lg-6">
        <div class="subhead">資料與下單</div>
        $settings_metrics
      </div>
      <div class="col-lg-6">
        <div class="subhead">策略參數</div>
        $rule_metrics
      </div>
    </div>
    <div class="row mt-3">
      <div class="col-lg-6">
        <div class="subhead">績效</div>
        $result_metrics
      </div>
      <div class="col-lg-6">
        <div class="subhead">輸出檔案</div>
        $output_metrics
      </div>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-file-list-3-fill me-2"></i>
      買賣訊號紀錄
    </div>
    <div class="section-bar"></div>
    <div class="table-responsive">
      <table class="table table-bordered table-sm bg-white">
        <thead>
          <tr>
            <th>時間</th><th>股票</th><th>動作</th><th>類型</th><th>參考價</th><th>原因</th>
            <th>RSI</th><th>MACD</th><th>Signal</th><th>Hist</th>
          </tr>
        </thead>
        <tbody>
        $signal_table_rows
        </tbody>
      </table>
    </div>

    <div class="d-flex align-items-center section-title">
      <i class="ri-exchange-dollar-fill me-2"></i>
      損益明細
    </div>
    <div class="section-bar"></div>
    <div class="table-responsive">
      <table class="table table-bordered table-sm bg-white">
        <thead>
          <tr>
            <th>股票</th><th>買進時間</th><th>買進價</th><th>張數</th><th>股數</th><th>買進金額</th>
            <th>賣出時間</th><th>賣出價</th><th>賣出金額</th><th>手續費</th><th>交易稅</th><th>淨損益</th><th>報酬率</th>
          </tr>
        </thead>
        <tbody>
        $trade_table_rows
        </tbody>
      </table>
    </div>
  </main>

  <script>
    const kbar = $kbar_json;
    const signalMarkers = $signal_marker_json;
    const maLabels = $ma_label_json;
    const rsiBuyAbove = $rsi_buy_above;
    const rsiBuyBelow = $rsi_buy_below;
    const useRsiBlock = $use_rsi_block;
    const rsiBlockLow = $rsi_block_low;
    const rsiBlockHigh = $rsi_block_high;
    const rsiSellBelow = $rsi_sell_below;

    function lineRows(key) {
      return kbar.filter((item) => item[key] !== null).map((item) => ({ time: item.time, value: item[key] }));
    }

    if (kbar.length === 0) {
      document.getElementById("price-chart").innerHTML = '<div class="d-flex align-items-center justify-content-center h-100 text-muted">沒有 K 線資料可顯示</div>';
      document.getElementById("rsi-chart").innerHTML = '<div class="d-flex align-items-center justify-content-center h-100 text-muted">沒有 RSI 資料</div>';
      document.getElementById("macd-chart").innerHTML = '<div class="d-flex align-items-center justify-content-center h-100 text-muted">沒有 MACD 資料</div>';
    } else {
      const priceChart = LightweightCharts.createChart(document.getElementById("price-chart"), {
        layout: { textColor: "#222", background: { type: "solid", color: "white" } },
        timeScale: { timeVisible: true },
        localization: {
          timeFormatter: (timestamp) => new Date(timestamp * 1000).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })
        }
      });
      const candles = priceChart.addCandlestickSeries({
        upColor: "#ef5350",
        downColor: "#26a69a",
        borderVisible: false,
        wickUpColor: "#ef5350",
        wickDownColor: "#26a69a"
      });
      candles.setData(kbar.map((item) => ({
        time: item.time,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close
      })));
      candles.setMarkers(signalMarkers);

      const maFast = priceChart.addLineSeries({ color: "#1f77b4", lineWidth: 1, title: maLabels.fast });
      const maMid = priceChart.addLineSeries({ color: "#f59e0b", lineWidth: 1, title: maLabels.mid });
      const maSlow = priceChart.addLineSeries({ color: "#7c3aed", lineWidth: 1, title: maLabels.slow });
      maFast.setData(lineRows("ma_fast"));
      maMid.setData(lineRows("ma_mid"));
      maSlow.setData(lineRows("ma_slow"));
      priceChart.timeScale().fitContent();

      const rsiChart = LightweightCharts.createChart(document.getElementById("rsi-chart"), {
        layout: { textColor: "#222", background: { type: "solid", color: "white" } },
        timeScale: { timeVisible: true }
      });
      const rsiSeries = rsiChart.addLineSeries({ color: "#315f72", lineWidth: 2, title: "RSI" });
      rsiSeries.setData(lineRows("rsi"));
      rsiSeries.createPriceLine({ price: rsiBuyAbove, color: "#f59e0b", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Buy above" });
      rsiSeries.createPriceLine({ price: rsiBuyBelow, color: "#ef5350", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Buy below" });
      if (useRsiBlock) {
        rsiSeries.createPriceLine({ price: rsiBlockLow, color: "#777", lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: "Block low" });
        rsiSeries.createPriceLine({ price: rsiBlockHigh, color: "#777", lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: "Block high" });
      }
      rsiSeries.createPriceLine({ price: rsiSellBelow, color: "#26a69a", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Sell below" });
      rsiChart.timeScale().fitContent();

      const macdChart = LightweightCharts.createChart(document.getElementById("macd-chart"), {
        layout: { textColor: "#222", background: { type: "solid", color: "white" } },
        timeScale: { timeVisible: true }
      });
      const macdHist = macdChart.addHistogramSeries({ priceFormat: { type: "price", precision: 4, minMove: 0.0001 } });
      macdHist.setData(kbar.filter((item) => item.macd_hist !== null).map((item) => ({
        time: item.time,
        value: item.macd_hist,
        color: item.macd_hist >= 0 ? "#ef5350" : "#26a69a"
      })));
      const macdLine = macdChart.addLineSeries({ color: "#1f77b4", lineWidth: 2, title: "MACD" });
      const signalLine = macdChart.addLineSeries({ color: "#f59e0b", lineWidth: 2, title: "Signal" });
      macdLine.setData(lineRows("macd"));
      signalLine.setData(lineRows("macd_signal"));
      macdChart.timeScale().fitContent();
    }
  </script>
</body>
</html>
"""
    )

    html_path.write_text(
        template.safe_substitute(
            code=_escape(config.code),
            settings_metrics=settings_metrics,
            rule_metrics=rule_metrics,
            result_metrics=result_metrics,
            output_metrics=output_metrics,
            signal_table_rows=_signal_table_rows(signal_rows),
            trade_table_rows=_trade_table_rows(pnl_rows),
            kbar_json=_json(kbar_rows),
            signal_marker_json=_json(signal_markers),
            ma_label_json=_json(
                {
                    "fast": f"MA{config.ma_fast_period}",
                    "mid": f"MA{config.ma_mid_period}",
                    "slow": f"MA{config.ma_slow_period}",
                }
            ),
            rsi_buy_above=f"{config.rsi_buy_above:g}",
            rsi_buy_below=f"{config.rsi_buy_below:g}",
            use_rsi_block=_json(config.use_rsi_block),
            rsi_block_low=f"{config.rsi_block_low:g}",
            rsi_block_high=f"{config.rsi_block_high:g}",
            rsi_sell_below=f"{config.rsi_sell_below:g}",
        ),
        encoding="utf-8",
    )

    print(f"Report generated: {html_path}")
    print(f"Trade records: {trades_path}")
    print(f"Signal records: {signals_path}")
    print(f"PnL records: {pnl_path}")
    return html_path


def validate_safety_settings(config: AppConfig) -> None:
    if (not config.is_backtest) and (not config.is_simulation) and (not config.allow_real_trading):
        raise RuntimeError(
            "系統已阻擋真實下單；只有在確認用途後，才可設定 ALLOW_REAL_TRADING=true。"
        )


def validate_env(config: AppConfig) -> None:
    if config.tick_source == "sinopac":
        required = ["API_KEY", "API_SECRET"]
    else:
        required = ["EMAIL", "TSST_TOKEN"]

    if config.tick_source == "tsst" and not config.only_backtest:
        required += ["API_KEY", "API_SECRET", "CA_PATH", "CA_PASSWORD"]

    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError("缺少必要的 .env 設定值：" + ", ".join(missing))


def validate_config(config: AppConfig) -> None:
    """檢查參數是否合理，避免回測跑到一半才因設定錯誤中斷。"""

    if config.lots <= 0:
        raise RuntimeError("--lots 必須大於 0")
    if config.kbar_freq <= 0:
        raise RuntimeError("--kbar-freq 必須大於 0")
    if min(config.ma_fast_period, config.ma_mid_period, config.ma_slow_period) <= 0:
        raise RuntimeError("均線週期必須大於 0")
    entry_start = config.entry_start_hour * 60 + config.entry_start_minute
    entry_cutoff = config.entry_cutoff_hour * 60 + config.entry_cutoff_minute
    block_start = config.entry_block_start_hour * 60 + config.entry_block_start_minute
    block_end = config.entry_block_end_hour * 60 + config.entry_block_end_minute
    forced_exit = config.sell_hour * 60 + config.sell_minute
    if not 0 <= entry_start < entry_cutoff < forced_exit < 24 * 60:
        raise RuntimeError("進場開始時間、截止時間與強制出場時間的先後順序不正確")
    if not entry_start <= block_start < block_end <= entry_cutoff:
        raise RuntimeError("禁止進場區間必須位於允許進場時段內")
    if config.rsi_period <= 0:
        raise RuntimeError("--rsi-period 必須大於 0")
    if not 0 <= config.rsi_sell_below < config.rsi_buy_above < config.rsi_buy_below <= 100:
        raise RuntimeError("RSI 門檻必須符合：0 <= 賣出門檻 < 買進下限 < 買進上限 <= 100")
    if (
        config.use_rsi
        and config.use_rsi_block
        and not config.rsi_buy_above < config.rsi_block_low < config.rsi_block_high < config.rsi_buy_below
    ):
        raise RuntimeError("RSI 避開區間必須位於買進範圍內")
    if min(config.macd_fast_period, config.macd_slow_period, config.macd_signal_period) <= 0:
        raise RuntimeError("MACD 週期必須大於 0")
    if config.macd_fast_period >= config.macd_slow_period:
        raise RuntimeError("--macd-fast-period 必須小於 --macd-slow-period")
    if min(config.stop_loss_pct, config.take_profit_pct) < 0:
        raise RuntimeError("停損與停利比例不可小於 0")
    if min(config.min_hold_bars, config.cooldown_bars) < 0:
        raise RuntimeError("最少持有與冷卻 K 棒數不可小於 0")
    if config.max_entries_per_day <= 0:
        raise RuntimeError("--max-entries-per-day 必須大於 0")


def build_tsst(config: AppConfig) -> BacktestSafeTsst:
    """依照策略參數建立 TSST 物件，並設定回測或交易模式。"""

    strategy_cls: type[BacktestSafeTsst]
    strategy_cls = FixedPriceTsst if config.strategy == "fixed" else MovingAverageTsst

    tsst = strategy_cls(
        config=config,
        use_broker="Sino",
        is_simulation=config.is_simulation,
        is_backtest=config.is_backtest,
    )

    if config.tick_source == "tsst":
        tsst.login(
            {
                "email": require_env("EMAIL"),
                "tsst_token": require_env("TSST_TOKEN"),
                "api_key": os.getenv("API_KEY", ""),
                "secret_key": os.getenv("API_SECRET", ""),
                "ca_path": os.getenv("CA_PATH", ""),
                "ca_password": os.getenv("CA_PASSWORD", ""),
                "only_backtest": config.only_backtest,
            }
        )

    if config.is_backtest or config.tick_source == "sinopac":
        tsst.quote_obj = BacktestQuote()
    if config.tick_source == "sinopac":
        tsst.local_order_mode = True

    if config.tick_source == "tsst":
        tsst.set_initial_capital(config.initial_capital)
        tsst.update_fee_settings(
            "stock",
            "buy",
            fee=config.stock_fee_rate,
            tax=config.stock_tax_rate,
            tick_count=0,
            direction="up",
        )
        tsst.update_fee_settings(
            "stock",
            "sell",
            fee=config.stock_fee_rate,
            tax=config.stock_tax_rate,
            tick_count=0,
            direction="down",
        )
    return tsst


def parse_args() -> AppConfig:
    """將 PowerShell 參數與 .env 預設值轉成 AppConfig。"""

    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=os.getenv("BACKTEST_CONFIG", CONFIG_FILE.name))
    pre_parser.add_argument("--profile", default=os.getenv("BACKTEST_PROFILE", ""))
    pre_args, _ = pre_parser.parse_known_args()
    apply_backtest_config_defaults(load_backtest_config(resolve_config_path(pre_args.config), pre_args.profile))

    parser = argparse.ArgumentParser(
        description="單檔版 TSST/Sinopac 股票回測與報表產生器",
        parents=[pre_parser],
    )
    parser.add_argument("--strategy", choices=["ma", "rsi", "macd", "fixed"], default=os.getenv("STRATEGY", "ma"))
    parser.add_argument("--tick-source", choices=["sinopac", "tsst"], default=os.getenv("TICK_SOURCE", "sinopac"))
    parser.add_argument("--run-name", default=os.getenv("RUN_NAME", ""))
    parser.add_argument("--code", default=os.getenv("STOCK_CODE", "2313"))
    parser.add_argument("--backtest-start", default=os.getenv("BACKTEST_START", os.getenv("BACKTEST_DATE", "2026-01-01")))
    parser.add_argument("--backtest-end", default=os.getenv("BACKTEST_END", "2026-06-30"))
    parser.add_argument("--backfill-start", default=os.getenv("BACKFILL_START", os.getenv("FILLING_DATE", "2026-02-01")))
    parser.add_argument("--backfill-end", default=os.getenv("BACKFILL_END", "2026-02-28"))
    parser.add_argument("--lots", type=int, default=int(os.getenv("ORDER_LOTS", "1")))
    parser.add_argument("--buy-price", type=float, default=float(os.getenv("BUY_PRICE", "1790")))
    parser.add_argument("--sell-hour", type=int, default=int(os.getenv("SELL_HOUR", "13")))
    parser.add_argument("--sell-minute", type=int, default=int(os.getenv("SELL_MINUTE", "20")))
    parser.add_argument("--initial-capital", type=int, default=int(os.getenv("INITIAL_CAPITAL", "100000")))
    parser.add_argument("--stock-fee-rate", type=float, default=float(os.getenv("STOCK_FEE_RATE", "0.001425")))
    parser.add_argument("--stock-tax-rate", type=float, default=float(os.getenv("STOCK_TAX_RATE", "0.003")))
    parser.add_argument("--kbar-unit", default=os.getenv("KBAR_UNIT", "m"))
    parser.add_argument("--kbar-freq", type=int, default=int(os.getenv("KBAR_FREQ", "5")))
    parser.add_argument("--ma-fast-period", type=int, default=int(os.getenv("MA_FAST_PERIOD", "5")))
    parser.add_argument("--ma-mid-period", type=int, default=int(os.getenv("MA_MID_PERIOD", "10")))
    parser.add_argument("--ma-slow-period", type=int, default=int(os.getenv("MA_SLOW_PERIOD", "20")))
    parser.add_argument(
        "--entry-near-ma-points",
        "--entry-near-ma20-points",
        dest="entry_near_ma_points",
        type=float,
        default=float(os.getenv("ENTRY_NEAR_MA_POINTS", os.getenv("ENTRY_NEAR_MA20_POINTS", "0"))),
    )
    parser.add_argument("--entry-start-hour", type=int, default=int(os.getenv("ENTRY_START_HOUR", "9")))
    parser.add_argument("--entry-start-minute", type=int, default=int(os.getenv("ENTRY_START_MINUTE", "0")))
    parser.add_argument("--entry-cutoff-hour", type=int, default=int(os.getenv("ENTRY_CUTOFF_HOUR", "12")))
    parser.add_argument("--entry-cutoff-minute", type=int, default=int(os.getenv("ENTRY_CUTOFF_MINUTE", "30")))
    parser.add_argument("--entry-block-start-hour", type=int, default=int(os.getenv("ENTRY_BLOCK_START_HOUR", "9")))
    parser.add_argument("--entry-block-start-minute", type=int, default=int(os.getenv("ENTRY_BLOCK_START_MINUTE", "30")))
    parser.add_argument("--entry-block-end-hour", type=int, default=int(os.getenv("ENTRY_BLOCK_END_HOUR", "10")))
    parser.add_argument("--entry-block-end-minute", type=int, default=int(os.getenv("ENTRY_BLOCK_END_MINUTE", "0")))
    parser.add_argument("--rsi-period", type=int, default=int(os.getenv("RSI_PERIOD", "14")))
    parser.add_argument("--rsi-buy-above", type=float, default=float(os.getenv("RSI_BUY_ABOVE", "60")))
    parser.add_argument("--rsi-buy-below", type=float, default=float(os.getenv("RSI_BUY_BELOW", "70")))
    parser.add_argument("--rsi-block-low", type=float, default=float(os.getenv("RSI_BLOCK_LOW", "63")))
    parser.add_argument("--rsi-block-high", type=float, default=float(os.getenv("RSI_BLOCK_HIGH", "66")))
    parser.add_argument("--rsi-sell-below", type=float, default=float(os.getenv("RSI_SELL_BELOW", "45")))
    parser.add_argument("--macd-fast-period", type=int, default=int(os.getenv("MACD_FAST_PERIOD", "12")))
    parser.add_argument("--macd-slow-period", type=int, default=int(os.getenv("MACD_SLOW_PERIOD", "26")))
    parser.add_argument("--macd-signal-period", type=int, default=int(os.getenv("MACD_SIGNAL_PERIOD", "9")))
    parser.add_argument("--stop-loss-pct", type=float, default=float(os.getenv("STOP_LOSS_PCT", "0.015")))
    parser.add_argument("--take-profit-pct", type=float, default=float(os.getenv("TAKE_PROFIT_PCT", "0.03")))
    parser.add_argument("--min-hold-bars", type=int, default=int(os.getenv("MIN_HOLD_BARS", "2")))
    parser.add_argument("--cooldown-bars", type=int, default=int(os.getenv("COOLDOWN_BARS", "6")))
    parser.add_argument("--max-entries-per-day", type=int, default=int(os.getenv("MAX_ENTRIES_PER_DAY", "1")))
    parser.set_defaults(
        is_backtest=str_to_bool(os.getenv("IS_BACKTEST", "true"), default=True),
        is_simulation=str_to_bool(os.getenv("IS_SIMULATION", "true"), default=True),
        only_backtest=str_to_bool(os.getenv("ONLY_BACKTEST", "true"), default=True),
        allow_real_trading=str_to_bool(os.getenv("ALLOW_REAL_TRADING", "false"), default=False),
        use_rsi=str_to_bool(os.getenv("USE_RSI", "true"), default=True),
        use_rsi_block=str_to_bool(os.getenv("USE_RSI_BLOCK", "true"), default=True),
        use_macd=str_to_bool(os.getenv("USE_MACD", "true"), default=True),
    )
    parser.add_argument("--backtest", dest="is_backtest", action="store_true")
    parser.add_argument("--live", dest="is_backtest", action="store_false")
    parser.add_argument("--simulation", dest="is_simulation", action="store_true")
    parser.add_argument("--real-market", dest="is_simulation", action="store_false")
    parser.add_argument("--only-backtest", dest="only_backtest", action="store_true")
    parser.add_argument("--allow-real-trading", dest="allow_real_trading", action="store_true")
    parser.add_argument("--use-rsi", dest="use_rsi", action="store_true")
    parser.add_argument("--disable-rsi", dest="use_rsi", action="store_false")
    parser.add_argument("--use-rsi-block", dest="use_rsi_block", action="store_true")
    parser.add_argument("--disable-rsi-block", dest="use_rsi_block", action="store_false")
    parser.add_argument("--use-macd", dest="use_macd", action="store_true")
    parser.add_argument("--disable-macd", dest="use_macd", action="store_false")
    args = parser.parse_args()

    return AppConfig(
        strategy=args.strategy,
        tick_source=args.tick_source,
        run_name=args.run_name,
        code=args.code,
        backtest_start=args.backtest_start,
        backtest_end=args.backtest_end,
        backfill_start=args.backfill_start,
        backfill_end=args.backfill_end,
        is_backtest=args.is_backtest,
        is_simulation=args.is_simulation,
        only_backtest=args.only_backtest,
        allow_real_trading=args.allow_real_trading,
        initial_capital=args.initial_capital,
        lots=args.lots,
        buy_price=args.buy_price,
        sell_hour=args.sell_hour,
        sell_minute=args.sell_minute,
        entry_near_ma20_points=args.entry_near_ma_points,
        stock_fee_rate=args.stock_fee_rate,
        stock_tax_rate=args.stock_tax_rate,
        kbar_unit=args.kbar_unit,
        kbar_freq=args.kbar_freq,
        ma_fast_period=args.ma_fast_period,
        ma_mid_period=args.ma_mid_period,
        ma_slow_period=args.ma_slow_period,
        entry_near_ma_points=args.entry_near_ma_points,
        entry_start_hour=args.entry_start_hour,
        entry_start_minute=args.entry_start_minute,
        entry_cutoff_hour=args.entry_cutoff_hour,
        entry_cutoff_minute=args.entry_cutoff_minute,
        entry_block_start_hour=args.entry_block_start_hour,
        entry_block_start_minute=args.entry_block_start_minute,
        entry_block_end_hour=args.entry_block_end_hour,
        entry_block_end_minute=args.entry_block_end_minute,
        use_rsi=args.use_rsi,
        rsi_period=args.rsi_period,
        rsi_buy_above=args.rsi_buy_above,
        rsi_buy_below=args.rsi_buy_below,
        use_rsi_block=args.use_rsi_block,
        rsi_block_low=args.rsi_block_low,
        rsi_block_high=args.rsi_block_high,
        rsi_sell_below=args.rsi_sell_below,
        use_macd=args.use_macd,
        macd_fast_period=args.macd_fast_period,
        macd_slow_period=args.macd_slow_period,
        macd_signal_period=args.macd_signal_period,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        min_hold_bars=args.min_hold_bars,
        cooldown_bars=args.cooldown_bars,
        max_entries_per_day=args.max_entries_per_day,
    )


def main() -> None:
    """主流程：驗證設定、抓資料、回放 tick，最後產生回測報表。"""

    load_dotenv()
    config = parse_args()
    validate_config(config)
    validate_safety_settings(config)
    validate_env(config)

    rsi_block_console = (
        f"exclude={config.rsi_block_low:g}-{config.rsi_block_high:g}"
        if config.use_rsi_block
        else "exclude=off"
    )
    print("========== TSST Settings ==========")
    print(f"Strategy: {config.strategy}")
    print(f"Tick source: {config.tick_source}")
    print(f"Run name: {config.run_name or '(none)'}")
    print(f"Stock: {config.code}")
    print(f"Backtest: {config.is_backtest}")
    print(f"Simulation: {config.is_simulation}")
    print(f"Only backtest: {config.only_backtest}")
    print(f"Backtest range: {config.backtest_start} ~ {config.backtest_end}")
    print(f"Backfill range: {config.backfill_start} ~ {config.backfill_end}")
    print(f"K bar: {config.kbar_interval}")
    print(f"MA: {config.ma_fast_period}/{config.ma_mid_period}/{config.ma_slow_period}")
    print(
        f"RSI: {'on' if config.use_rsi else 'off'} period={config.rsi_period} "
        f"entry={config.rsi_buy_above:g}-{config.rsi_buy_below:g} "
        f"{rsi_block_console} "
        f"exit<{config.rsi_sell_below:g}"
    )
    print(
        "MACD: "
        f"{'on' if config.use_macd else 'off'} "
        f"{config.macd_fast_period}/{config.macd_slow_period}/{config.macd_signal_period}"
    )
    print(
        f"Risk: stop={config.stop_loss_pct:.2%} take={config.take_profit_pct:.2%} "
        f"min_hold={config.min_hold_bars} cooldown={config.cooldown_bars} "
        f"max_entries/day={config.max_entries_per_day}"
    )
    print(
        f"Entry window: {config.entry_start_hour:02d}:{config.entry_start_minute:02d}"
        f"-{config.entry_cutoff_hour:02d}:{config.entry_cutoff_minute:02d}"
    )
    print(
        f"Blocked entry window: {config.entry_block_start_hour:02d}:{config.entry_block_start_minute:02d}"
        f"-{config.entry_block_end_hour:02d}:{config.entry_block_end_minute:02d}"
    )
    print(f"Order lots: {config.lots} lot(s), {config.quantity} shares")
    print("===================================")

    tsst = build_tsst(config)
    if config.tick_source == "sinopac":
        api = login_sinopac()
        ticks = fetch_sinopac_ticks(api, config)
        replay_ticks(tsst, ticks)
        generate_local_report(tsst, config, ticks)
        api.logout()
        return

    tsst.subscribe(
        params={"codes": [{"code": config.code, "market": "Stock"}]},
        backtest_params={
            "start_from": config.backtest_start,
            "end_to": config.backtest_end,
            "clear_quote_manager": True,
            "backfilling_start_from": config.backfill_start,
            "backfilling_end_to": config.backfill_end,
        },
    )
    tsst.keep_running()


if __name__ == "__main__":
    main()
