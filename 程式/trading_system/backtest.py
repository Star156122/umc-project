"""股票策略回測核心：行情、策略、模擬成交、績效與報表。

修改日期：2026-08-24
修改摘要：補強老師會議要求的本金、期間、費稅與績效說明，加入多股票資料庫
更新支援與資料涵蓋統計，相容新版 TSST 建構參數，加入選配的 MySQL/MariaDB
回測結果上傳功能，以及 OpenAI LLM 結構化分析與網頁報告呈現。
"""

from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import argparse
import html
import json
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any, Literal
from zoneinfo import ZoneInfo

import polars as pl
import polars_talib as plta
import shioaji as sj
from dotenv import load_dotenv

from trading_system.llm_analysis import (
    build_analysis_payload,
    generate_openai_analysis,
    render_analysis_html,
)


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
REPORT_DIR = ROOT / "reports"
CONFIG_FILE = ROOT / "backtest_config.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


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


StrategyName = Literal[
    "ma",
    "rsi",
    "macd",
    "bollinger",
    "breakout",
    "vote",
    "fixed",
]
TickSource = Literal["sinopac", "tsst"]
PositionSizing = Literal["fixed_lots", "cash_fraction"]
HoldingMode = Literal["intraday", "swing"]
STRATEGY_LABELS: dict[str, str] = {
    "ma": "MA 均線交叉",
    "rsi": "RSI 動能",
    "macd": "MACD 趨勢轉折",
    "bollinger": "布林通道反轉",
    "breakout": "區間突破",
    "vote": "三指標多數決",
    "fixed": "固定價格測試",
}
STRATEGY_CATEGORIES: dict[str, str] = {
    "ma": "趨勢追蹤",
    "rsi": "動能",
    "macd": "趨勢／動能轉折",
    "bollinger": "均值回歸",
    "breakout": "趨勢突破",
    "vote": "多指標組合",
    "fixed": "流程測試",
}
STRATEGY_MARKET_REGIMES: dict[str, str] = {
    "ma": "上升趨勢形成時",
    "rsi": "已有明顯強勢動能時",
    "macd": "弱轉強或波段起漲時",
    "bollinger": "盤整或急跌後反彈時",
    "breakout": "盤整結束並向上突破時",
    "vote": "單一指標雜訊較多時",
    "fixed": "僅用於驗證成交與報表流程",
}


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
    "position_sizing": "POSITION_SIZING",
    "capital_utilization": "CAPITAL_UTILIZATION",
    "holding_mode": "HOLDING_MODE",
    "buy_price": "BUY_PRICE",
    "sell_hour": "SELL_HOUR",
    "sell_minute": "SELL_MINUTE",
    "entry_near_ma_points": "ENTRY_NEAR_MA_POINTS",
    "stock_fee_rate": "STOCK_FEE_RATE",
    "stock_tax_rate": "STOCK_TAX_RATE",
    "day_trade_tax_rate": "DAY_TRADE_TAX_RATE",
    "apply_day_trade_tax": "APPLY_DAY_TRADE_TAX",
    "kbar_unit": "KBAR_UNIT",
    "kbar_freq": "KBAR_FREQ",
    "ma_fast_period": "MA_FAST_PERIOD",
    "ma_mid_period": "MA_MID_PERIOD",
    "ma_slow_period": "MA_SLOW_PERIOD",
    "trend_ma_period": "TREND_MA_PERIOD",
    "trend_slope_lookback": "TREND_SLOPE_LOOKBACK",
    "require_trend_filter": "REQUIRE_TREND_FILTER",
    "ma_exit_on_cross": "MA_EXIT_ON_CROSS",
    "entry_start_hour": "ENTRY_START_HOUR",
    "entry_start_minute": "ENTRY_START_MINUTE",
    "entry_cutoff_hour": "ENTRY_CUTOFF_HOUR",
    "entry_cutoff_minute": "ENTRY_CUTOFF_MINUTE",
    "entry_block_start_hour": "ENTRY_BLOCK_START_HOUR",
    "entry_block_start_minute": "ENTRY_BLOCK_START_MINUTE",
    "entry_block_end_hour": "ENTRY_BLOCK_END_HOUR",
    "entry_block_end_minute": "ENTRY_BLOCK_END_MINUTE",
    "use_entry_block": "USE_ENTRY_BLOCK",
    "use_rsi": "USE_RSI",
    "rsi_period": "RSI_PERIOD",
    "rsi_buy_above": "RSI_BUY_ABOVE",
    "rsi_buy_below": "RSI_BUY_BELOW",
    "use_rsi_block": "USE_RSI_BLOCK",
    "rsi_block_low": "RSI_BLOCK_LOW",
    "rsi_block_high": "RSI_BLOCK_HIGH",
    "rsi_sell_below": "RSI_SELL_BELOW",
    "rsi_require_cross": "RSI_REQUIRE_CROSS",
    "use_macd": "USE_MACD",
    "macd_fast_period": "MACD_FAST_PERIOD",
    "macd_slow_period": "MACD_SLOW_PERIOD",
    "macd_signal_period": "MACD_SIGNAL_PERIOD",
    "macd_require_positive": "MACD_REQUIRE_POSITIVE",
    "macd_exit_on_cross": "MACD_EXIT_ON_CROSS",
    "bollinger_period": "BOLLINGER_PERIOD",
    "bollinger_stddev": "BOLLINGER_STDDEV",
    "bollinger_rsi_max": "BOLLINGER_RSI_MAX",
    "bollinger_min_reward_pct": "BOLLINGER_MIN_REWARD_PCT",
    "bollinger_require_bullish_candle": "BOLLINGER_REQUIRE_BULLISH_CANDLE",
    "breakout_entry_period": "BREAKOUT_ENTRY_PERIOD",
    "breakout_exit_period": "BREAKOUT_EXIT_PERIOD",
    "breakout_require_cross": "BREAKOUT_REQUIRE_CROSS",
    "breakout_volume_ratio": "BREAKOUT_VOLUME_RATIO",
    "volume_ma_period": "VOLUME_MA_PERIOD",
    "vote_required": "VOTE_REQUIRED",
    "vote_exit_required": "VOTE_EXIT_REQUIRED",
    "stop_loss_pct": "STOP_LOSS_PCT",
    "take_profit_pct": "TAKE_PROFIT_PCT",
    "min_hold_bars": "MIN_HOLD_BARS",
    "max_hold_bars": "MAX_HOLD_BARS",
    "cooldown_bars": "COOLDOWN_BARS",
    "max_entries_per_day": "MAX_ENTRIES_PER_DAY",
    "verbose_bars": "VERBOSE_BARS",
    "use_data_cache": "USE_DATA_CACHE",
    "data_cache_path": "DATA_CACHE_PATH",
    "news_summary_path": "NEWS_SUMMARY_PATH",
    "stock_codes": "STOCK_CODES",
    "db_enabled": "DB_ENABLED",
    "db_host": "DB_HOST",
    "db_port": "DB_PORT",
    "db_name": "DB_NAME",
    "db_user": "DB_USER",
    "db_password": "DB_PASSWORD",
    "db_user_id": "DB_USER_ID",
    "stock_name": "STOCK_NAME",
    "llm_enabled": "LLM_ENABLED",
    "openai_model": "OPENAI_MODEL",
    "llm_max_output_tokens": "LLM_MAX_OUTPUT_TOKENS",
    "llm_cache_path": "LLM_CACHE_PATH",
    "llm_use_cache": "LLM_USE_CACHE",
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
    code: str = "2303"
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
    position_sizing: PositionSizing = "cash_fraction"
    capital_utilization: float = 0.95
    holding_mode: HoldingMode = "swing"
    buy_price: float = 1790
    sell_hour: int = 13
    sell_minute: int = 20
    stock_fee_rate: float = 0.001425
    stock_tax_rate: float = 0.003
    day_trade_tax_rate: float = 0.0015
    apply_day_trade_tax: bool = True
    kbar_unit: str = "m"
    kbar_freq: int = 5
    ma_fast_period: int = 5
    ma_mid_period: int = 10
    ma_slow_period: int = 20
    trend_ma_period: int = 120
    trend_slope_lookback: int = 12
    require_trend_filter: bool = False
    ma_exit_on_cross: bool = True
    entry_near_ma_points: float = 0.0
    entry_start_hour: int = 9
    entry_start_minute: int = 0
    entry_cutoff_hour: int = 12
    entry_cutoff_minute: int = 30
    entry_block_start_hour: int = 9
    entry_block_start_minute: int = 30
    entry_block_end_hour: int = 10
    entry_block_end_minute: int = 0
    use_entry_block: bool = True
    use_rsi: bool = True
    rsi_period: int = 14
    rsi_buy_above: float = 60.0
    rsi_buy_below: float = 70.0
    use_rsi_block: bool = True
    rsi_block_low: float = 63.0
    rsi_block_high: float = 66.0
    rsi_sell_below: float = 45.0
    rsi_require_cross: bool = False
    use_macd: bool = True
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    macd_require_positive: bool = False
    macd_exit_on_cross: bool = True
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    bollinger_rsi_max: float = 100.0
    bollinger_min_reward_pct: float = 0.0
    bollinger_require_bullish_candle: bool = False
    breakout_entry_period: int = 20
    breakout_exit_period: int = 10
    breakout_require_cross: bool = False
    breakout_volume_ratio: float = 0.0
    volume_ma_period: int = 20
    vote_required: int = 2
    vote_exit_required: int = 2
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.03
    min_hold_bars: int = 2
    max_hold_bars: int = 0
    cooldown_bars: int = 6
    max_entries_per_day: int = 1
    verbose_bars: bool = False
    use_data_cache: bool = True
    data_cache_path: str = "data/market_data.sqlite3"
    news_summary_path: str = ""
    stock_codes: str = ""
    db_enabled: bool = False
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "ai_stock_system"
    db_user: str = ""
    db_password: str = ""
    db_user_id: int = 1
    stock_name: str = ""
    llm_enabled: bool = False
    openai_model: str = "gpt-5.6-luna"
    llm_max_output_tokens: int = 1400
    llm_cache_path: str = "data/llm_cache"
    llm_use_cache: bool = True

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
    "BB_MIDDLE",
    "BB_UPPER",
    "BB_LOWER",
    "BREAKOUT_HIGH",
    "BREAKOUT_LOW",
    "TREND_MA",
    "TREND_SLOPE",
    "VOLUME_MA",
]


def indicator_exprs(config: AppConfig) -> list[pl.Expr]:
    """建立各策略共用的 Polars 技術指標計算式。"""

    close = pl.col("Close")
    bollinger_middle = close.rolling_mean(window_size=config.bollinger_period)
    bollinger_std = close.rolling_std(window_size=config.bollinger_period, ddof=0)
    trend_ma = close.rolling_mean(window_size=config.trend_ma_period)

    return [
        plta.ma(close, config.ma_fast_period).alias("MA_FAST"),
        plta.ma(close, config.ma_mid_period).alias("MA_MID"),
        plta.ma(close, config.ma_slow_period).alias("MA_SLOW"),
        plta.rsi(close, config.rsi_period).alias("RSI"),
        bollinger_middle.alias("BB_MIDDLE"),
        (bollinger_middle + config.bollinger_stddev * bollinger_std).alias("BB_UPPER"),
        (bollinger_middle - config.bollinger_stddev * bollinger_std).alias("BB_LOWER"),
        pl.col("High")
        .rolling_max(window_size=config.breakout_entry_period)
        .shift(1)
        .alias("BREAKOUT_HIGH"),
        pl.col("Low")
        .rolling_min(window_size=config.breakout_exit_period)
        .shift(1)
        .alias("BREAKOUT_LOW"),
        trend_ma.alias("TREND_MA"),
        (trend_ma - trend_ma.shift(config.trend_slope_lookback)).alias("TREND_SLOPE"),
        pl.col("Volume").rolling_mean(window_size=config.volume_ma_period).alias("VOLUME_MA"),
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


def taipei_datetime_from_timestamp(value: int | float) -> datetime:
    """把 Unix timestamp 固定轉成台灣時間，避免不同電腦時區造成回測偏移。"""

    return datetime.fromtimestamp(float(value), TAIPEI_TZ).replace(tzinfo=None)


def taipei_timestamp(value: datetime) -> float:
    """把台灣本地時間穩定轉成 Unix timestamp。"""

    aware = value if value.tzinfo is not None else value.replace(tzinfo=TAIPEI_TZ)
    return aware.timestamp()


def indicator_snapshot(row: dict[str, Any]) -> dict[str, float | None]:
    """擷取單根 K 線上的指標值，供訊號紀錄與策略判斷使用。"""

    snapshot = {column: number_or_none(row.get(column)) for column in INDICATOR_COLUMNS}
    snapshot["CLOSE"] = number_or_none(row.get("Close"))
    snapshot["OPEN"] = number_or_none(row.get("Open"))
    snapshot["HIGH"] = number_or_none(row.get("High"))
    snapshot["LOW"] = number_or_none(row.get("Low"))
    snapshot["VOLUME"] = number_or_none(row.get("Volume"))
    return snapshot


def format_indicator_value(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


class BacktestSafeTsst(Tsst):
    """包一層安全回測模式，避免回測時真的送出委託。"""

    def __init__(self, **kwargs: Any):
        # TSST 0.8.7 起這兩個參數改為必填；預設值也讓離線測試保持安全回測模式。
        kwargs.setdefault("is_simulation", True)
        kwargs.setdefault("use_broker", "Sino")
        local_only = bool(kwargs.pop("local_only", kwargs.get("is_backtest", False)))
        if local_only:
            self.is_backtest = bool(kwargs.get("is_backtest", True))
            self.is_simulation = bool(kwargs.get("is_simulation", True))
            self.quote_obj = None
        else:
            super().__init__(**kwargs)
        self.local_only = local_only
        self.local_order_mode = local_only
        self.last_tick: dict[str, Any] | None = None
        self.local_order_id = 0
        self.local_position = 0
        self.local_initial_capital = 0.0
        self.local_cash: float | None = None
        self.local_fee_rate = 0.0
        self.local_tax_rate = 0.0
        self.local_day_trade_tax_rate = 0.0
        self.local_apply_day_trade_tax = False
        self.local_position_open_date: date | None = None
        self.local_orders: list[dict[str, Any]] = []
        self.local_trades: list[dict[str, Any]] = []
        self.local_signals: list[dict[str, Any]] = []

    def configure_local_account(self, config: AppConfig) -> None:
        """設定本地回測帳戶；下單時會檢查現金並立即扣除稅費。"""

        self.local_initial_capital = float(config.initial_capital)
        self.local_cash = float(config.initial_capital)
        self.local_fee_rate = float(config.stock_fee_rate)
        self.local_tax_rate = float(config.stock_tax_rate)
        self.local_day_trade_tax_rate = float(config.day_trade_tax_rate)
        self.local_apply_day_trade_tax = bool(config.apply_day_trade_tax)

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
                "exchange_ts": taipei_timestamp(signal_time),
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

        gross = tick_price * quantity
        fee = gross * self.local_fee_rate
        trade_time = (
            taipei_datetime_from_timestamp(self.last_tick["timestamp"])
            if self.last_tick
            else datetime.now(TAIPEI_TZ).replace(tzinfo=None)
        )
        tax_rate = self.local_tax_rate
        if (
            action == "Sell"
            and self.local_apply_day_trade_tax
            and self.local_position_open_date == trade_time.date()
        ):
            tax_rate = self.local_day_trade_tax_rate
        tax = gross * tax_rate if action == "Sell" else 0.0
        cash_before = self.local_cash
        status_code = "BT-00000" if can_fill else "LOCAL-NOT-FILLED"
        status_message = "local fill" if can_fill else "local limit not filled"

        if can_fill and action == "Buy" and cash_before is not None and cash_before < gross + fee:
            can_fill = False
            status_code = "LOCAL-INSUFFICIENT-CASH"
            status_message = (
                f"insufficient cash: need {gross + fee:,.2f}, available {cash_before:,.2f}"
            )
        elif can_fill and action == "Sell" and self.local_position < quantity:
            can_fill = False
            status_code = "LOCAL-INSUFFICIENT-POSITION"
            status_message = (
                f"insufficient position: need {quantity}, available {self.local_position}"
            )

        response = {
            "operation_status_code": status_code,
            "operation_message": status_message,
            "order": {"id": order_id, "code": code, **params},
        }
        self.local_orders.append(
            {
                "order_id": order_id,
                "code": code,
                "action": action,
                "quantity": quantity,
                "reference_price": tick_price,
                "status_code": status_code,
                "status_message": status_message,
                "exchange_ts": self.last_tick["timestamp"] if self.last_tick else datetime.now(TAIPEI_TZ).timestamp(),
            }
        )
        self.on_order("local_order", response=response)

        if not can_fill:
            return response

        if action == "Buy":
            if self.local_position == 0:
                self.local_position_open_date = trade_time.date()
            self.local_position += quantity
            if self.local_cash is not None:
                self.local_cash -= gross + fee
        elif action == "Sell":
            self.local_position -= quantity
            if self.local_cash is not None:
                self.local_cash += gross - fee - tax
            if self.local_position == 0:
                self.local_position_open_date = None

        deal = {
            "operation_type": "New",
            "operation_code": "BT-00000",
            "trade_id": order_id,
            "code": code,
            "product_type": product_type,
            "action": action,
            "price": tick_price,
            "quantity": quantity,
            "gross_amount": gross,
            "fee": fee,
            "tax": tax,
            "tax_rate": tax_rate if action == "Sell" else 0.0,
            "cash_before": cash_before,
            "cash_after": self.local_cash,
            "price_type": price_type,
            "order_type": params.get("order_type"),
            "exchange_ts": self.last_tick["timestamp"] if self.last_tick else datetime.now(TAIPEI_TZ).timestamp(),
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
        tick_time = (
            taipei_datetime_from_timestamp(timestamp)
            if timestamp is not None
            else datetime.now(TAIPEI_TZ).replace(tzinfo=None)
        )

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
    """技術指標策略共用的回測執行類別。"""

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
        self.entry_quantity = 0
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
        tick_time = taipei_datetime_from_timestamp(response["timestamp"])
        self.process_completed_kbar(response["code"], tick_time, last_kbar)

    def process_completed_kbar(
        self,
        code: str,
        decision_time: datetime,
        completed_kbar: dict[str, Any],
    ) -> None:
        """使用已完成 K 棒產生訊號，並在下一根 K 棒的第一價模擬成交。"""

        close = number_or_none(completed_kbar.get("Close"))
        snapshot = indicator_snapshot(completed_kbar)

        if close is None or not self._has_required_indicators(snapshot):
            return

        self._roll_trading_day(decision_time)
        if self.cooldown_remaining > 0 and not self.is_bought:
            self.cooldown_remaining -= 1
        if self.is_bought:
            self.bars_since_entry += 1

        self._print_bar(code, decision_time, close, snapshot)

        if self.config.holding_mode == "intraday" and is_after_exit_time(decision_time, self.config):
            self._force_exit(code, decision_time, close, snapshot)
            self.previous_snapshot = snapshot
            return

        if self.is_bought and not self.is_sold and not self.is_exit:
            self._try_exit(code, decision_time, close, snapshot)
        elif self._can_enter(decision_time):
            self._try_entry(code, decision_time, close, snapshot)

        self.previous_snapshot = snapshot

    def on_deal(self, sender: str, response: dict, **kwargs: Any) -> None:
        print(f"Deal received: {response}")
        if response["action"] == "Buy":
            self.is_bought = True
            self.is_sold = False
            self.entry_price = float(response["price"])
            self.entry_quantity = int(response["quantity"])
            self.bars_since_entry = 0
            self.entries_today += 1
        elif response["action"] == "Sell":
            self.is_sold = True
            self.is_bought = False
            self.is_exit = False
            self.entry_price = None
            self.entry_quantity = 0
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
        elif self.config.strategy == "bollinger":
            required = ["BB_MIDDLE", "BB_UPPER", "BB_LOWER"]
        elif self.config.strategy == "breakout":
            required = ["BREAKOUT_HIGH", "BREAKOUT_LOW"]
        elif self.config.strategy == "vote":
            required = ["MA_FAST", "MA_MID", "MA_SLOW", "RSI", "MACD", "MACD_SIGNAL"]
        else:
            required = ["MA_FAST", "MA_MID", "MA_SLOW"]
        if self.config.require_trend_filter:
            required.extend(["TREND_MA", "TREND_SLOPE"])
        if self.config.strategy == "bollinger" and self.config.bollinger_rsi_max < 100:
            required.append("RSI")
        if self.config.strategy == "breakout" and self.config.breakout_volume_ratio > 0:
            required.extend(["VOLUME", "VOLUME_MA"])
        return all(snapshot.get(column) is not None for column in required)

    def _trend_filter_ok(self, close: float, snapshot: dict[str, float | None]) -> bool:
        if not self.config.require_trend_filter:
            return True
        trend_ma = snapshot.get("TREND_MA")
        trend_slope = snapshot.get("TREND_SLOPE")
        return (
            trend_ma is not None
            and trend_slope is not None
            and close > trend_ma
            and trend_slope > 0
        )

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
        is_blocked_time = self.config.use_entry_block and block_start <= current_minute < block_end
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
        if not self.config.verbose_bars:
            return
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
        if self.config.strategy == "bollinger":
            return self._bollinger_entry_reasons(close, snapshot)
        if self.config.strategy == "breakout":
            return self._breakout_entry_reasons(close, snapshot)
        if self.config.strategy == "vote":
            return self._vote_entry_reasons(close, snapshot)
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
        if len(reasons) != 2 or not self._trend_filter_ok(close, snapshot):
            return []
        if self.config.require_trend_filter:
            reasons.append(
                f"close > trend MA{self.config.trend_ma_period}, trend slope > 0"
            )
        return reasons

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
        crossed_into_range = True
        if self.config.rsi_require_cross:
            previous_rsi = self.previous_snapshot.get("RSI") if self.previous_snapshot else None
            crossed_into_range = (
                previous_rsi is not None
                and previous_rsi < self.config.rsi_buy_above
                and rsi is not None
                and rsi >= self.config.rsi_buy_above
            )
        close = snapshot.get("CLOSE")
        if (
            not rsi_in_range
            or rsi_in_blocked_range
            or not crossed_into_range
            or close is None
            or not self._trend_filter_ok(close, snapshot)
        ):
            return []
        rsi_reason = f"RSI {self.config.rsi_buy_above:g}-{self.config.rsi_buy_below:g}"
        if self.config.rsi_require_cross:
            rsi_reason += ", crosses into range"
        if self.config.use_rsi_block:
            rsi_reason += f", excluding {self.config.rsi_block_low:g}-{self.config.rsi_block_high:g}"
        if self.config.require_trend_filter:
            rsi_reason += f", above rising trend MA{self.config.trend_ma_period}"
        return [rsi_reason]

    def _macd_entry_reasons(self, snapshot: dict[str, float | None]) -> list[str]:
        close = snapshot.get("CLOSE")
        macd = snapshot.get("MACD")
        if (
            close is not None
            and self._is_macd_bullish_cross(snapshot)
            and (not self.config.macd_require_positive or (macd is not None and macd > 0))
            and self._trend_filter_ok(close, snapshot)
        ):
            reasons = ["MACD bullish crossover signal"]
            if self.config.macd_require_positive:
                reasons.append("MACD > 0")
            if self.config.require_trend_filter:
                reasons.append(f"above rising trend MA{self.config.trend_ma_period}")
            return reasons
        return []

    def _bollinger_entry_reasons(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> list[str]:
        if self.previous_snapshot is None:
            return []
        previous_close = self.previous_snapshot.get("CLOSE")
        previous_lower = self.previous_snapshot.get("BB_LOWER")
        lower = snapshot.get("BB_LOWER")
        middle = snapshot.get("BB_MIDDLE")
        rsi = snapshot.get("RSI")
        bar_open = snapshot.get("OPEN")
        reward_pct = (
            (middle - close) / close
            if middle is not None and close > 0
            else None
        )
        if (
            previous_close is not None
            and previous_lower is not None
            and lower is not None
            and previous_close < previous_lower
            and close >= lower
            and (rsi is None or rsi <= self.config.bollinger_rsi_max)
            and (
                reward_pct is not None
                and reward_pct >= self.config.bollinger_min_reward_pct
            )
            and (
                not self.config.bollinger_require_bullish_candle
                or (
                    bar_open is not None
                    and close > bar_open
                    and close > previous_close
                )
            )
            and self._trend_filter_ok(close, snapshot)
        ):
            return [
                f"收盤價由布林下軌外回到下軌內（下軌 {lower:.2f}），"
                f"至中線空間 {reward_pct:.2%}"
            ]
        return []

    def _breakout_entry_reasons(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> list[str]:
        breakout_high = snapshot.get("BREAKOUT_HIGH")
        crossed = True
        if self.config.breakout_require_cross:
            previous_close = self.previous_snapshot.get("CLOSE") if self.previous_snapshot else None
            previous_high = self.previous_snapshot.get("BREAKOUT_HIGH") if self.previous_snapshot else None
            crossed = (
                previous_close is not None
                and previous_high is not None
                and previous_close <= previous_high
            )
        volume = snapshot.get("VOLUME")
        volume_ma = snapshot.get("VOLUME_MA")
        volume_ok = (
            self.config.breakout_volume_ratio <= 0
            or (
                volume is not None
                and volume_ma is not None
                and volume >= volume_ma * self.config.breakout_volume_ratio
            )
        )
        if (
            breakout_high is not None
            and close > breakout_high
            and crossed
            and volume_ok
            and self._trend_filter_ok(close, snapshot)
        ):
            reasons = [
                f"收盤價突破前 {self.config.breakout_entry_period} 根 K 棒最高價 "
                f"{breakout_high:.2f}"
            ]
            if self.config.breakout_volume_ratio > 0:
                reasons.append(f"成交量 >= {self.config.breakout_volume_ratio:g} 倍均量")
            if self.config.require_trend_filter:
                reasons.append(f"above rising trend MA{self.config.trend_ma_period}")
            return reasons
        return []

    def _vote_components(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> tuple[list[str], list[str]]:
        """回傳三指標多數決中的多方票與空方票。"""

        bullish: list[str] = []
        bearish: list[str] = []
        ma_fast = snapshot.get("MA_FAST")
        ma_mid = snapshot.get("MA_MID")
        ma_slow = snapshot.get("MA_SLOW")
        rsi = snapshot.get("RSI")
        macd = snapshot.get("MACD")
        macd_signal = snapshot.get("MACD_SIGNAL")

        if (
            ma_fast is not None
            and ma_mid is not None
            and ma_slow is not None
            and ma_fast > ma_mid
            and close > ma_slow
        ):
            bullish.append("MA")
        if (
            ma_fast is not None
            and ma_mid is not None
            and ma_slow is not None
            and (ma_fast < ma_mid or close < ma_slow)
        ):
            bearish.append("MA")

        rsi_in_range = (
            rsi is not None
            and self.config.rsi_buy_above <= rsi <= self.config.rsi_buy_below
            and not (
                self.config.use_rsi_block
                and self.config.rsi_block_low <= rsi < self.config.rsi_block_high
            )
        )
        if rsi_in_range:
            bullish.append("RSI")
        if rsi is not None and rsi < self.config.rsi_sell_below:
            bearish.append("RSI")

        if (
            macd is not None
            and macd_signal is not None
            and macd > macd_signal
            and (not self.config.macd_require_positive or macd > 0)
        ):
            bullish.append("MACD")
        if macd is not None and macd_signal is not None and macd < macd_signal:
            bearish.append("MACD")
        return bullish, bearish

    def _vote_entry_reasons(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> list[str]:
        bullish, _ = self._vote_components(close, snapshot)
        if (
            len(bullish) >= self.config.vote_required
            and self._trend_filter_ok(close, snapshot)
        ):
            return [
                f"多數決達標 {len(bullish)}/3（{'、'.join(bullish)}）"
            ]
        return []

    def _risk_exit_signal(self, close: float) -> tuple[str, list[str]] | None:
        if self.entry_price is not None:
            return_pct = (close - self.entry_price) / self.entry_price
            if self.config.stop_loss_pct > 0 and return_pct <= -self.config.stop_loss_pct:
                return "stop_loss", [f"return {return_pct:.2%}"]
            if self.config.take_profit_pct > 0 and return_pct >= self.config.take_profit_pct:
                return "take_profit", [f"return {return_pct:.2%}"]
        if self.config.max_hold_bars > 0 and self.bars_since_entry >= self.config.max_hold_bars:
            return "max_holding_period", [
                f"held {self.bars_since_entry} bars (limit {self.config.max_hold_bars})"
            ]
        return None

    def _exit_signal(self, close: float, snapshot: dict[str, float | None]) -> tuple[str, list[str]] | None:
        risk_signal = self._risk_exit_signal(close)
        if risk_signal is not None:
            return risk_signal

        if self.bars_since_entry < self.config.min_hold_bars:
            return None

        reasons = []
        if self.config.strategy == "rsi":
            rsi = snapshot["RSI"]
            if rsi is not None and rsi < self.config.rsi_sell_below:
                reasons.append(f"RSI < {self.config.rsi_sell_below:g}")
        elif self.config.strategy == "macd":
            if self.config.macd_exit_on_cross:
                if self._is_macd_bearish_cross(snapshot):
                    reasons.append("MACD bearish crossover")
            else:
                macd = snapshot.get("MACD")
                trend_ma = snapshot.get("TREND_MA")
                if macd is not None and macd < 0:
                    reasons.append("MACD < 0")
                if trend_ma is not None and close < trend_ma:
                    reasons.append(f"close < trend MA{self.config.trend_ma_period}")
        elif self.config.strategy == "bollinger":
            middle = snapshot.get("BB_MIDDLE")
            if middle is not None and close >= middle:
                reasons.append(f"收盤價回到布林中線 {middle:.2f}")
        elif self.config.strategy == "breakout":
            breakout_low = snapshot.get("BREAKOUT_LOW")
            if breakout_low is not None and close < breakout_low:
                reasons.append(
                    f"收盤價跌破前 {self.config.breakout_exit_period} 根 K 棒最低價 "
                    f"{breakout_low:.2f}"
                )
            trend_ma = snapshot.get("TREND_MA")
            if self.config.require_trend_filter and trend_ma is not None and close < trend_ma:
                reasons.append(f"收盤價跌破趨勢 MA{self.config.trend_ma_period}")
        elif self.config.strategy == "vote":
            _, bearish = self._vote_components(close, snapshot)
            if len(bearish) >= self.config.vote_exit_required:
                reasons.append(f"空方多數決 {len(bearish)}/3（{'、'.join(bearish)}）")
        else:
            ma_slow = snapshot["MA_SLOW"]
            if ma_slow is not None and close < ma_slow:
                reasons.append(f"close < MA{self.config.ma_slow_period}")
            if self.config.ma_exit_on_cross and self._is_ma_bearish_cross(snapshot):
                reasons.append(
                    f"MA{self.config.ma_fast_period} bearish crossover MA{self.config.ma_mid_period}"
                )
        return ("technical_exit", reasons) if reasons else None

    def _order_quantity(self, reference_price: float) -> int:
        """依設定計算股數；動態模式最多買到 ORDER_LOTS 的上限。"""

        if self.config.position_sizing == "fixed_lots" or self.local_cash is None:
            return self.config.quantity
        execution_price = (
            float(self.last_tick["close"])
            if self.last_tick and self.last_tick.get("close") is not None
            else reference_price
        )
        if execution_price <= 0:
            return 0
        affordable = math.floor(
            self.local_cash
            * self.config.capital_utilization
            / (execution_price * (1 + self.local_fee_rate))
        )
        return max(0, min(self.config.quantity, affordable))

    def _order_lot(self, quantity: int) -> str:
        if quantity % 1000 == 0:
            return "Common"
        return "IntradayOdd" if self.config.holding_mode == "intraday" else "Odd"

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
        quantity = self._order_quantity(close)
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
        print(
            f"Strategy entry: buy {code} at market, reference close={close:.2f}, "
            f"shares={quantity}; {reason_text}"
        )
        self.create_order(
            code=code,
            product_type="Stock",
            params={
                "price": close,
                "quantity": max(quantity, 1),
                "action": "Buy",
                "price_type": "MKT",
                "order_type": "ROD",
                "order_cond": "Cash",
                "order_lot": self._order_lot(max(quantity, 1)),
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
        print(f"Strategy exit: sell {code}; {reason_text}")
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
        quantity = self.local_position if self.local_order_mode else self.entry_quantity
        if quantity <= 0:
            quantity = self.config.quantity
        self.create_order(
            code=code,
            product_type="Stock",
            params={
                "price": close,
                "quantity": quantity,
                "action": "Sell",
                "price_type": "MKT",
                "order_type": "ROD",
                "order_cond": "Cash",
                "order_lot": self._order_lot(quantity),
            },
        )

    def finalize_backtest(
        self,
        code: str,
        final_time: datetime,
        final_price: float,
        snapshot: dict[str, float | None],
    ) -> None:
        """資料結束時平掉波段部位，避免績效漏算未實現損益。"""

        if not self.is_bought or self.is_sold:
            return
        self.is_exit = True
        self.is_entry = False
        self.record_signal(
            code=code,
            action="Sell",
            signal_type="end_of_backtest",
            signal_time=final_time,
            price=final_price,
            reason="回測資料結束，強制平倉",
            indicators=snapshot,
        )
        self._sell_market(code, final_price)


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


def resolve_data_cache_path(config: AppConfig) -> Path:
    """將相對路徑固定解析到專案根目錄，避免從不同資料夾執行時換位置。"""

    raw_path = Path(config.data_cache_path).expanduser()
    return raw_path if raw_path.is_absolute() else ROOT / raw_path


def open_tick_cache(config: AppConfig) -> sqlite3.Connection:
    """開啟並初始化本地 SQLite 歷史 tick 資料庫。"""
    assert_config(config)

    database_path = resolve_data_cache_path(config)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    # 單機批次回測不需要 WAL；DELETE 模式能讓資料庫單檔複製與壓縮時保持完整。
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_ticks (
            stock_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            close REAL NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            tick_type INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(stock_code, trade_date, sequence_no)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS market_data_days (
            stock_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(stock_code, trade_date)
        );
        CREATE TABLE IF NOT EXISTS market_kbars (
            stock_code TEXT NOT NULL,
            freq_minutes INTEGER NOT NULL,
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            kbar_timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY(stock_code, freq_minutes, range_start, range_end, kbar_timestamp)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS market_kbar_cache_meta (
            stock_code TEXT NOT NULL,
            freq_minutes INTEGER NOT NULL,
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            source_row_count INTEGER NOT NULL,
            source_first_timestamp REAL,
            source_last_timestamp REAL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(stock_code, freq_minutes, range_start, range_end)
        ) WITHOUT ROWID;
        DROP INDEX IF EXISTS idx_market_ticks_lookup;
        """
    )
    return connection


def _load_cached_day(
    connection: sqlite3.Connection,
    code: str,
    day_text: str,
) -> list[dict[str, Any]] | None:
    assert_development_period(day_text, day_text)
    status = connection.execute(
        "SELECT status FROM market_data_days WHERE stock_code = ? AND trade_date = ?",
        (code, day_text),
    ).fetchone()
    if status is None or status["status"] != "complete":
        return None

    rows = connection.execute(
        """
        SELECT timestamp, close, qty, tick_type
        FROM market_ticks
        WHERE stock_code = ? AND trade_date = ?
        ORDER BY sequence_no
        """,
        (code, day_text),
    ).fetchall()
    return [
        {
            "timestamp": float(row["timestamp"]),
            "market_type": "Stock",
            "code": code,
            "close": float(row["close"]),
            "qty": int(row["qty"]),
            "tick_type": int(row["tick_type"]),
            "is_simulate": False,
            "is_backfilling": False,
        }
        for row in rows
    ]


def _save_cached_day(
    connection: sqlite3.Connection,
    code: str,
    day_text: str,
    ticks: list[dict[str, Any]],
) -> None:
    with connection:
        connection.execute(
            "DELETE FROM market_ticks WHERE stock_code = ? AND trade_date = ?",
            (code, day_text),
        )
        connection.executemany(
            """
            INSERT INTO market_ticks(
                stock_code, trade_date, sequence_no, timestamp, close, qty, tick_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    code,
                    day_text,
                    index,
                    float(tick["timestamp"]),
                    float(tick["close"]),
                    int(tick.get("qty", 0) or 0),
                    int(tick.get("tick_type", 0) or 0),
                )
                for index, tick in enumerate(ticks)
            ],
        )
        connection.execute(
            """
            INSERT INTO market_data_days(stock_code, trade_date, status, row_count, updated_at)
            VALUES (?, ?, 'complete', ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                status = excluded.status,
                row_count = excluded.row_count,
                updated_at = excluded.updated_at
            """,
            (code, day_text, len(ticks), datetime.now().isoformat(timespec="seconds")),
        )
        connection.execute("DELETE FROM market_kbars WHERE stock_code = ?", (code,))
        connection.execute("DELETE FROM market_kbar_cache_meta WHERE stock_code = ?", (code,))


def is_cache_range_complete(config: AppConfig) -> bool:
    """確認指定股票與日期區間是否已完整寫入本地資料庫。"""
    assert_config(config)

    if not config.use_data_cache:
        return False
    database_path = resolve_data_cache_path(config)
    if not database_path.exists():
        return False
    try:
        connection = open_tick_cache(config)
        cached_dates = {
            row["trade_date"]
            for row in connection.execute(
                """
                SELECT trade_date FROM market_data_days
                WHERE stock_code = ? AND status = 'complete'
                  AND trade_date BETWEEN ? AND ?
                """,
                (config.code, config.backtest_start, config.backtest_end),
            )
        }
    except sqlite3.DatabaseError:
        return False
    finally:
        if "connection" in locals():
            connection.close()
    return all(day.strftime("%Y-%m-%d") in cached_dates for day in iter_dates(
        config.backtest_start,
        config.backtest_end,
    ))


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


def fetch_sinopac_ticks(
    api: sj.Shioaji | None,
    config: AppConfig,
    *,
    collect_ticks: bool = True,
) -> list[dict[str, Any]]:
    """補齊 SQLite 快取；只有相容舊流程時才把全部 tick 載入記憶體。"""
    assert_config(config)

    contract = api.Contracts.Stocks[config.code] if api is not None else None
    connection = open_tick_cache(config) if config.use_data_cache else None
    all_ticks: list[dict[str, Any]] = []
    failed_dates: list[str] = []

    try:
        for day in iter_dates(config.backtest_start, config.backtest_end):
            day_text = day.strftime("%Y-%m-%d")
            if connection is not None:
                cache_status = connection.execute(
                    """
                    SELECT status, row_count FROM market_data_days
                    WHERE stock_code = ? AND trade_date = ?
                    """,
                    (config.code, day_text),
                ).fetchone()
                if cache_status is not None and cache_status["status"] == "complete":
                    if collect_ticks:
                        cached_ticks = _load_cached_day(connection, config.code, day_text) or []
                        all_ticks.extend(cached_ticks)
                        cached_count = len(cached_ticks)
                    else:
                        cached_count = int(cache_status["row_count"])
                    print(f"[{day_text}] cache {cached_count} ticks")
                    continue

            if api is None or contract is None:
                raise RuntimeError(
                    f"本地資料庫缺少 {config.code} {day_text}，且未提供 Shioaji 登入連線。"
                )

            try:
                ticks = api.ticks(contract=contract, date=day_text)
                df = pl.DataFrame({**ticks})
            except Exception as exc:
                print(f"[{day_text}] fetch failed: {exc}")
                failed_dates.append(day_text)
                continue

            day_ticks: list[dict[str, Any]] = []
            if not df.is_empty():
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

            if connection is not None:
                _save_cached_day(connection, config.code, day_text, day_ticks)
            if collect_ticks:
                all_ticks.extend(day_ticks)
            print(f"[{day_text}] fetched {len(day_ticks)} ticks")
    finally:
        if connection is not None:
            connection.close()

    if collect_ticks:
        all_ticks.sort(key=lambda item: item["timestamp"])
    if failed_dates:
        raise RuntimeError(
            "歷史資料尚未完整下載，以下日期抓取失敗：" + ", ".join(failed_dates)
        )
    if collect_ticks and not all_ticks:
        raise RuntimeError(
            f"{config.code} 在 {config.backtest_start} ~ {config.backtest_end} 沒有可回測的 tick 資料。"
        )
    return all_ticks


@dataclass(frozen=True)
class MarketDataStats:
    tick_count: int
    first_timestamp: float | None
    last_timestamp: float | None


def _market_data_stats(connection: sqlite3.Connection, config: AppConfig) -> MarketDataStats:
    assert_config(config)
    row = connection.execute(
        """
        SELECT COUNT(*) AS tick_count,
               MIN(timestamp) AS first_timestamp,
               MAX(timestamp) AS last_timestamp
        FROM market_ticks
        WHERE stock_code = ? AND trade_date BETWEEN ? AND ?
        """,
        (config.code, config.backtest_start, config.backtest_end),
    ).fetchone()
    return MarketDataStats(
        tick_count=int(row["tick_count"]),
        first_timestamp=number_or_none(row["first_timestamp"]),
        last_timestamp=number_or_none(row["last_timestamp"]),
    )


def _build_raw_kbars_from_cache(
    connection: sqlite3.Connection,
    config: AppConfig,
    stats: MarketDataStats,
) -> list[dict[str, Any]]:
    """以單次串流把 SQLite tick 聚合成原始 K 棒，避免建立數百萬個 dict。"""
    assert_config(config)

    if config.kbar_unit != "m":
        raise RuntimeError("快速 SQLite K 棒回放目前僅支援分鐘週期（KBAR_UNIT=m）")
    seconds = config.kbar_freq * 60
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    cursor = connection.execute(
        """
        SELECT timestamp, close, qty
        FROM market_ticks
        WHERE stock_code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date, sequence_no
        """,
        (config.code, config.backtest_start, config.backtest_end),
    )
    for tick in cursor:
        timestamp = float(tick["timestamp"])
        price = float(tick["close"])
        bucket = int(timestamp // seconds * seconds)
        if current is None or bucket != current["kbar_timestamp"]:
            if current is not None:
                rows.append(current)
            current = {
                "kbar_timestamp": bucket,
                "Open": price,
                "High": price,
                "Low": price,
                "Close": price,
                "Volume": int(tick["qty"] or 0),
            }
            continue
        current["High"] = max(float(current["High"]), price)
        current["Low"] = min(float(current["Low"]), price)
        current["Close"] = price
        current["Volume"] = int(current["Volume"]) + int(tick["qty"] or 0)
    if current is not None:
        rows.append(current)

    with connection:
        connection.execute(
            """
            DELETE FROM market_kbars
            WHERE stock_code = ? AND freq_minutes = ? AND range_start = ? AND range_end = ?
            """,
            (config.code, config.kbar_freq, config.backtest_start, config.backtest_end),
        )
        connection.executemany(
            """
            INSERT INTO market_kbars(
                stock_code, freq_minutes, range_start, range_end, kbar_timestamp,
                open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    config.code,
                    config.kbar_freq,
                    config.backtest_start,
                    config.backtest_end,
                    int(row["kbar_timestamp"]),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    int(row["Volume"]),
                )
                for row in rows
            ],
        )
        connection.execute(
            """
            INSERT INTO market_kbar_cache_meta(
                stock_code, freq_minutes, range_start, range_end,
                source_row_count, source_first_timestamp, source_last_timestamp, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, freq_minutes, range_start, range_end) DO UPDATE SET
                source_row_count = excluded.source_row_count,
                source_first_timestamp = excluded.source_first_timestamp,
                source_last_timestamp = excluded.source_last_timestamp,
                updated_at = excluded.updated_at
            """,
            (
                config.code,
                config.kbar_freq,
                config.backtest_start,
                config.backtest_end,
                stats.tick_count,
                stats.first_timestamp,
                stats.last_timestamp,
                datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
            ),
        )
    return rows


def load_cached_kbar_frame(config: AppConfig) -> tuple[pl.DataFrame, MarketDataStats]:
    """讀取或建立原始 K 棒快取，再依目前 profile 計算技術指標。"""
    assert_config(config)

    connection = open_tick_cache(config)
    try:
        stats = _market_data_stats(connection, config)
        if stats.tick_count <= 0:
            raise RuntimeError(
                f"{config.code} 在 {config.backtest_start} ~ {config.backtest_end} 沒有可回測資料。"
            )
        meta = connection.execute(
            """
            SELECT source_row_count, source_first_timestamp, source_last_timestamp
            FROM market_kbar_cache_meta
            WHERE stock_code = ? AND freq_minutes = ? AND range_start = ? AND range_end = ?
            """,
            (config.code, config.kbar_freq, config.backtest_start, config.backtest_end),
        ).fetchone()
        cache_valid = (
            meta is not None
            and int(meta["source_row_count"]) == stats.tick_count
            and number_or_none(meta["source_first_timestamp"]) == stats.first_timestamp
            and number_or_none(meta["source_last_timestamp"]) == stats.last_timestamp
        )
        if cache_valid:
            cached = connection.execute(
                """
                SELECT kbar_timestamp, open, high, low, close, volume
                FROM market_kbars
                WHERE stock_code = ? AND freq_minutes = ? AND range_start = ? AND range_end = ?
                ORDER BY kbar_timestamp
                """,
                (config.code, config.kbar_freq, config.backtest_start, config.backtest_end),
            ).fetchall()
            raw_rows = [
                {
                    "kbar_timestamp": int(row["kbar_timestamp"]),
                    "Open": float(row["open"]),
                    "High": float(row["high"]),
                    "Low": float(row["low"]),
                    "Close": float(row["close"]),
                    "Volume": int(row["volume"]),
                }
                for row in cached
            ]
            print(f"K-bar cache: {len(raw_rows):,} bars")
        else:
            raw_rows = _build_raw_kbars_from_cache(connection, config, stats)
            print(f"K-bar cache built: {len(raw_rows):,} bars")
    finally:
        connection.close()

    frame = pl.DataFrame(raw_rows).with_columns(
        pl.col("kbar_timestamp")
        .map_elements(taipei_datetime_from_timestamp, return_dtype=pl.Datetime)
        .alias("kbar_time")
    )
    return add_indicators(frame, config), stats


def replay_kbars(
    tsst: BacktestSafeTsst,
    kbar: pl.DataFrame,
    stats: MarketDataStats,
) -> None:
    """直接回放完成 K 棒；訊號用本棒收盤判斷，成交使用下一棒開盤。"""
    from trading_system.research_guard import assert_timestamps
    assert_timestamps(kbar['kbar_timestamp'].to_list())

    if not isinstance(tsst, MovingAverageTsst):
        raise RuntimeError("快速 K 棒回放僅支援技術指標策略")
    rows = kbar.iter_rows(named=True)
    previous = next(rows, None)
    processed = 0
    if previous is None:
        raise RuntimeError("沒有可回放的 K 棒")
    last_row = previous
    for current in rows:
        decision_time = current["kbar_time"]
        tsst.last_tick = {
            "timestamp": float(current["kbar_timestamp"]),
            "market_type": "Stock",
            "code": tsst.config.code,
            "close": float(current["Open"]),
            "qty": 0,
        }
        tsst.process_completed_kbar(tsst.config.code, decision_time, previous)
        previous = current
        last_row = current
        processed += 1
        if processed % 1000 == 0:
            print(f"Replayed {processed:,} K bars, current={decision_time:%Y-%m-%d %H:%M}")

    final_timestamp = stats.last_timestamp or float(last_row["kbar_timestamp"])
    final_time = taipei_datetime_from_timestamp(final_timestamp)
    final_price = float(last_row["Close"])
    tsst.last_tick = {
        "timestamp": final_timestamp,
        "market_type": "Stock",
        "code": tsst.config.code,
        "close": final_price,
        "qty": 0,
    }
    tsst.finalize_backtest(
        tsst.config.code,
        final_time,
        final_price,
        indicator_snapshot(last_row),
    )
    print(f"Replay finished: {processed + 1:,} K bars from {stats.tick_count:,} ticks")
    print(f"Local position: {tsst.local_position}")
    if tsst.local_cash is not None:
        print(f"Local cash: {tsst.local_cash:,.2f}")
    print(f"Local orders: {len(tsst.local_orders)}")
    print(f"Local trades: {len(tsst.local_trades)}")
    print(f"Local signals: {len(tsst.local_signals)}")


def replay_ticks(tsst: BacktestSafeTsst, ticks: list[dict[str, Any]]) -> None:
    """把歷史 tick 依時間順序餵回策略，模擬當時盤中的觸發流程。"""
    from trading_system.research_guard import assert_timestamps
    assert_timestamps(t['timestamp'] for t in ticks)

    for index, tick in enumerate(ticks, start=1):
        tsst.last_tick = tick
        tsst.quote_obj.quote_manager.add_tick(tick)
        if tick["market_type"] == "Stock":
            tsst.on_stock_tick("sinopac_replay", response=tick)

        if index % 10000 == 0:
            tick_time = taipei_datetime_from_timestamp(tick["timestamp"])
            print(f"Replayed {index} ticks, current={tick_time:%Y-%m-%d %H:%M:%S}")

    print(f"Replay finished: {len(ticks)} ticks")
    print(f"Local position: {tsst.local_position}")
    if tsst.local_cash is not None:
        print(f"Local cash: {tsst.local_cash:,.2f}")
    print(f"Local orders: {len(tsst.local_orders)}")
    print(f"Local trades: {len(tsst.local_trades)}")
    print(f"Local signals: {len(tsst.local_signals)}")


def build_kbar_rows_from_ticks(ticks: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    """把 tick 聚合成 K 線，並補上所有策略指標給圖表使用。"""
    assert_config(config)

    if not ticks:
        return []

    df = pl.DataFrame(
        {
            "dt": [taipei_datetime_from_timestamp(float(tick["timestamp"])) for tick in ticks],
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
    return build_kbar_rows_from_frame(kbar_df, config)


def build_kbar_rows_from_frame(kbar_df: pl.DataFrame, config: AppConfig) -> list[dict[str, Any]]:
    """把已計算指標的 K 棒轉成報表 JSON 列。"""
    assert_config(config)

    rows = []
    for row in kbar_df.iter_rows(named=True):
        kbar_time = row["kbar_time"]
        kbar_timestamp = row.get("kbar_timestamp")
        rows.append(
            {
            "time": int(kbar_timestamp if kbar_timestamp is not None else taipei_timestamp(kbar_time)),
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
            "bb_middle": number_or_none(row.get("BB_MIDDLE")),
            "bb_upper": number_or_none(row.get("BB_UPPER")),
            "bb_lower": number_or_none(row.get("BB_LOWER")),
            "breakout_high": number_or_none(row.get("BREAKOUT_HIGH")),
            "breakout_low": number_or_none(row.get("BREAKOUT_LOW")),
            "trend_ma": number_or_none(row.get("TREND_MA")),
            "trend_slope": number_or_none(row.get("TREND_SLOPE")),
            "volume_ma": number_or_none(row.get("VOLUME_MA")),
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
    return f"NT$ {float(value):,.2f}"


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
        return "永豐 Shioaji API ticks" + ("（SQLite 本地快取）" if config.use_data_cache else "")
    return "TSST 回測資料"


def _build_trade_rows(tsst: BacktestSafeTsst, config: AppConfig) -> list[dict[str, Any]]:
    """整理實際成交紀錄，輸出為 trades.csv。"""

    rows = []
    for trade in tsst.local_trades:
        ts = taipei_datetime_from_timestamp(float(trade["exchange_ts"]))
        quantity = int(trade["quantity"])
        price = float(trade["price"])
        gross = float(trade.get("gross_amount", price * quantity))
        fee = float(trade.get("fee", gross * config.stock_fee_rate))
        tax = float(
            trade.get(
                "tax",
                gross * config.stock_tax_rate if trade["action"] == "Sell" else 0.0,
            )
        )
        rows.append(
            {
                "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": int(float(trade["exchange_ts"])),
                "trade_id": trade["trade_id"],
                "code": trade["code"],
                "action": trade["action"],
                "price": price,
                "quantity": quantity,
                "gross_amount": gross,
                "fee": fee,
                "tax": tax,
                "net_cash_flow": -gross - fee if trade["action"] == "Buy" else gross - fee - tax,
                "cash_before": trade.get("cash_before"),
                "cash_after": trade.get("cash_after"),
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
                "bb_middle": number_or_none(signal.get("BB_MIDDLE")),
                "bb_upper": number_or_none(signal.get("BB_UPPER")),
                "bb_lower": number_or_none(signal.get("BB_LOWER")),
                "breakout_high": number_or_none(signal.get("BREAKOUT_HIGH")),
                "breakout_low": number_or_none(signal.get("BREAKOUT_LOW")),
                "trend_ma": number_or_none(signal.get("TREND_MA")),
                "trend_slope": number_or_none(signal.get("TREND_SLOPE")),
                "volume": number_or_none(signal.get("VOLUME")),
                "volume_ma": number_or_none(signal.get("VOLUME_MA")),
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


def calculate_performance_metrics(
    pnl_rows: list[dict[str, Any]],
    initial_capital: float,
    trading_days: list[str] | None = None,
) -> dict[str, float | int]:
    """依已完成交易計算損益、報酬率、最大回撤與年化 Sharpe。"""

    ordered_rows = sorted(pnl_rows, key=lambda row: str(row["sell_datetime"]))
    gross_pnl = sum(float(row["gross_pnl"]) for row in ordered_rows)
    total_fee = sum(float(row["fee"]) for row in ordered_rows)
    total_tax = sum(float(row["tax"]) for row in ordered_rows)
    net_pnl = gross_pnl - total_fee - total_tax
    ending_capital = initial_capital + net_pnl
    wins = sum(1 for row in ordered_rows if float(row["net_pnl"]) > 0)
    losses = sum(1 for row in ordered_rows if float(row["net_pnl"]) < 0)
    gross_profit = sum(
        float(row["net_pnl"]) for row in ordered_rows if float(row["net_pnl"]) > 0
    )
    gross_loss = sum(
        float(row["net_pnl"]) for row in ordered_rows if float(row["net_pnl"]) < 0
    )

    equity = float(initial_capital)
    peak = equity
    max_drawdown = 0.0
    daily_pnl: dict[str, float] = {}
    for row in ordered_rows:
        equity += float(row["net_pnl"])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        sell_day = str(row["sell_datetime"])[:10]
        daily_pnl[sell_day] = daily_pnl.get(sell_day, 0.0) + float(row["net_pnl"])

    daily_returns: list[float] = []
    equity = float(initial_capital)
    performance_days = sorted(set(trading_days or daily_pnl.keys()))
    for day in performance_days:
        start_equity = equity
        day_pnl = daily_pnl.get(day, 0.0)
        equity += day_pnl
        if start_equity:
            daily_returns.append(day_pnl / start_equity)

    sharpe_ratio = 0.0
    if len(daily_returns) >= 2:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean_return) ** 2 for value in daily_returns) / len(daily_returns)
        standard_deviation = math.sqrt(variance)
        if standard_deviation > 0:
            sharpe_ratio = mean_return / standard_deviation * math.sqrt(252)

    average_win = gross_profit / wins if wins else 0.0
    average_loss = abs(gross_loss) / losses if losses else 0.0
    payoff_ratio = average_win / average_loss if average_loss else 0.0
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else 0.0
    break_even_win_rate = (
        average_loss / (average_win + average_loss)
        if average_win + average_loss > 0
        else 0.0
    )

    return {
        "initial_capital": float(initial_capital),
        "ending_capital": ending_capital,
        "gross_pnl": gross_pnl,
        "total_fee": total_fee,
        "total_tax": total_tax,
        "transaction_cost": total_fee + total_tax,
        "net_pnl": net_pnl,
        "total_return": net_pnl / initial_capital if initial_capital else 0.0,
        "completed_trades": len(ordered_rows),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(ordered_rows) if ordered_rows else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "break_even_win_rate": break_even_win_rate,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
    }


def calculate_buy_and_hold_benchmark(
    kbar_rows: list[dict[str, Any]],
    config: AppConfig,
) -> dict[str, float | int]:
    """用相同本金、股數上限與一般稅費計算同期買進持有基準。"""

    if not kbar_rows:
        return {"quantity": 0, "net_pnl": 0.0, "total_return": 0.0}
    buy_price = float(kbar_rows[0]["open"])
    sell_price = float(kbar_rows[-1]["close"])
    affordable = math.floor(
        config.initial_capital
        * (config.capital_utilization if config.position_sizing == "cash_fraction" else 1.0)
        / (buy_price * (1 + config.stock_fee_rate))
    )
    quantity = min(config.quantity, max(affordable, 0))
    buy_amount = buy_price * quantity
    sell_amount = sell_price * quantity
    fee = (buy_amount + sell_amount) * config.stock_fee_rate
    tax = sell_amount * config.stock_tax_rate
    net_pnl = sell_amount - buy_amount - fee - tax
    return {
        "quantity": quantity,
        "net_pnl": net_pnl,
        "total_return": net_pnl / config.initial_capital if config.initial_capital else 0.0,
    }


def calculate_mark_to_market_risk(
    trades_rows: list[dict[str, Any]],
    kbar_rows: list[dict[str, Any]],
    initial_capital: float,
) -> dict[str, float]:
    """用每根 K 棒收盤價估算持倉市值、最大回撤與每日 Sharpe。"""

    if not kbar_rows:
        return {"max_drawdown": 0.0, "sharpe_ratio": 0.0}
    ordered_trades = sorted(trades_rows, key=lambda row: int(row["timestamp"]))
    trade_index = 0
    cash = float(initial_capital)
    position = 0
    peak = float(initial_capital)
    max_drawdown = 0.0
    daily_equity: dict[str, float] = {}

    for bar in kbar_rows:
        bar_timestamp = int(bar["time"])
        while (
            trade_index < len(ordered_trades)
            and int(ordered_trades[trade_index]["timestamp"]) <= bar_timestamp
        ):
            trade = ordered_trades[trade_index]
            cash += float(trade["net_cash_flow"])
            quantity = int(trade["quantity"])
            position += quantity if trade["action"] == "Buy" else -quantity
            trade_index += 1
        equity = cash + position * float(bar["close"])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        daily_equity[str(bar["datetime"])[:10]] = equity

    daily_returns: list[float] = []
    previous_equity = float(initial_capital)
    for day in sorted(daily_equity):
        equity = daily_equity[day]
        if previous_equity:
            daily_returns.append((equity - previous_equity) / previous_equity)
        previous_equity = equity

    sharpe_ratio = 0.0
    if len(daily_returns) >= 2:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean_return) ** 2 for value in daily_returns) / len(daily_returns)
        deviation = math.sqrt(variance)
        if deviation > 0:
            sharpe_ratio = mean_return / deviation * math.sqrt(252)
    return {"max_drawdown": max_drawdown, "sharpe_ratio": sharpe_ratio}


def load_optional_news_summary(config: AppConfig) -> str:
    """載入外部新聞／LLM 摘要檔；未設定時完全不影響核心回測。"""

    if not config.news_summary_path.strip():
        return ""
    raw_path = Path(config.news_summary_path).expanduser()
    summary_path = raw_path if raw_path.is_absolute() else ROOT / raw_path
    if not summary_path.exists():
        return f"已設定摘要檔，但找不到：{summary_path}"

    try:
        raw_text = summary_path.read_text(encoding="utf-8")
        if summary_path.suffix.lower() != ".json":
            return raw_text.strip()[:8000]
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        return f"摘要檔讀取失敗：{exc}"

    if isinstance(data, dict):
        if isinstance(data.get("summary"), str):
            return data["summary"].strip()[:8000]
        items = data.get("items", [])
    else:
        items = data
    if not isinstance(items, list):
        return json.dumps(data, ensure_ascii=False, indent=2)[:8000]

    lines: list[str] = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "未命名消息")).strip()
        published = str(item.get("date", item.get("published_at", "日期未提供"))).strip()
        source = str(item.get("source", "來源未提供")).strip()
        summary = str(item.get("summary", "")).strip()
        url = str(item.get("url", "")).strip()
        lines.append(
            f"{published}｜{source}｜{title}"
            + (f"\n{summary}" if summary else "")
            + (f"\n來源網址：{url}" if url else "")
        )
    return "\n\n".join(lines)[:8000]


def _build_equity_curve(pnl_rows: list[dict[str, Any]], initial_capital: float) -> list[dict[str, Any]]:
    """依已平倉交易的淨損益建立可儲存於資料庫的資產曲線。"""

    capital = float(initial_capital)
    curve: list[dict[str, Any]] = []
    for row in pnl_rows:
        capital += float(row["net_pnl"])
        curve.append({"time": row["sell_datetime"], "assets": round(capital, 6)})
    return curve


def save_backtest_to_database(
    config: AppConfig,
    summary: dict[str, Any],
    trades_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    pnl_rows: list[dict[str, Any]],
    report_path: Path,
) -> dict[str, int] | None:
    """將一次回測以單一交易寫入 MySQL/MariaDB；未啟用時不做任何事。"""
    assert_config(config)

    if not config.db_enabled:
        return None
    if not config.db_user:
        raise RuntimeError("已啟用資料庫上傳，但 DB_USER 尚未設定。")

    try:
        import pymysql
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 PyMySQL；請先執行 uv sync 安裝專案依賴。") from exc

    report_payload = {
        "schema_version": 1,
        "storage": "database",
        "report_file": str(report_path),
        "summary": summary,
        "transactions": trades_rows,
        "signals": signal_rows,
        "pnl_rows": pnl_rows,
    }
    prediction = "策略獲利" if summary["net_pnl"] > 0 else "策略虧損" if summary["net_pnl"] < 0 else "策略持平"
    signal = signal_rows[-1]["action"] if signal_rows else "Hold"
    equity_curve = _build_equity_curve(pnl_rows, float(summary["initial_capital"]))

    connection = pymysql.connect(
        host=config.db_host,
        port=config.db_port,
        user=config.db_user,
        password=config.db_password,
        database=config.db_name,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE user_id = %s", (config.db_user_id,))
            if cursor.fetchone() is None:
                raise RuntimeError(f"資料庫找不到 user_id={config.db_user_id}，請先建立使用者或修改 DB_USER_ID。")

            cursor.execute(
                """SELECT analysis_id FROM analysis_records
                   WHERE stock_id = %s AND strategy_type = %s
                     AND model_version = 'python-main02-db-v3' AND analysis_date = %s""",
                (config.code, config.strategy, summary["generated_at"].replace("T", " ")),
            )
            duplicate = cursor.fetchone()
            if duplicate is not None:
                raise RuntimeError(f"這份回測已上傳過（analysis_id={int(duplicate[0])}），已停止避免重複資料。")

            cursor.execute(
                """INSERT INTO stocks (stock_id, stock_name, market)
                   VALUES (%s, %s, 'TWSE')
                   ON DUPLICATE KEY UPDATE stock_name = VALUES(stock_name), updated_at = CURRENT_TIMESTAMP""",
                (config.code, config.stock_name.strip() or config.code),
            )
            cursor.execute(
                """INSERT INTO analysis_records
                   (user_id, stock_id, prediction, `signal`, strategy_type, model_version, analysis_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    config.db_user_id,
                    config.code,
                    prediction,
                    signal,
                    config.strategy,
                    "python-main02-db-v3",
                    summary["generated_at"].replace("T", " "),
                ),
            )
            analysis_id = int(cursor.lastrowid)
            cursor.execute(
                """INSERT INTO backtest_results
                   (analysis_id, strategy_name, backtest_start, backtest_end,
                    initial_capital, final_assets, gross_pnl, fee_amount, tax_amount,
                    transaction_cost, net_pnl, completed_trades, return_rate, win_rate,
                    sharpe_ratio, mdd, equity_curve)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    analysis_id,
                    config.strategy,
                    summary["backtest_start"],
                    summary["backtest_end"],
                    summary["initial_capital"],
                    summary["final_assets"],
                    summary["gross_pnl"],
                    summary["fee"],
                    summary["tax"],
                    summary["transaction_cost"],
                    summary["net_pnl"],
                    summary["completed_trades"],
                    summary["total_return"],
                    summary["win_rate"],
                    summary["sharpe_ratio"],
                    summary["max_drawdown"],
                    json.dumps(equity_curve, ensure_ascii=False),
                ),
            )
            backtest_id = int(cursor.lastrowid)
            cursor.execute(
                "INSERT INTO reports (analysis_id, report_content) VALUES (%s, %s)",
                (analysis_id, json.dumps(report_payload, ensure_ascii=False, allow_nan=False)),
            )
            report_id = int(cursor.lastrowid)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {"analysis_id": analysis_id, "backtest_id": backtest_id, "report_id": report_id}


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
        return '<tr><td colspan="15" class="text-center text-muted">沒有買賣訊號</td></tr>'

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
            <td class="text-end">{_format_number(row['bb_lower'], 4)}</td>
            <td class="text-end">{_format_number(row['bb_middle'], 4)}</td>
            <td class="text-end">{_format_number(row['bb_upper'], 4)}</td>
            <td class="text-end">{_format_number(row['breakout_high'], 4)}</td>
            <td class="text-end">{_format_number(row['breakout_low'], 4)}</td>
          </tr>"""
        )
    return "\n".join(rows)


def generate_local_report_legacy(tsst: BacktestSafeTsst, config: AppConfig, ticks: list[dict[str, Any]]) -> Path:
    """舊版 HTML 報表保留作為備份；目前主流程會使用新版報表函式。"""
    assert_config(config)

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
        ts = taipei_datetime_from_timestamp(float(trade["exchange_ts"]))
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


def generate_local_report(
    tsst: BacktestSafeTsst,
    config: AppConfig,
    ticks: list[dict[str, Any]] | None = None,
    *,
    kbar_rows: list[dict[str, Any]] | None = None,
    market_stats: MarketDataStats | None = None,
) -> Path:
    """產生本次回測的 CSV 與 HTML 圖表報告。"""
    assert_config(config)

    ticks = ticks or []

    REPORT_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = build_report_prefix(run_id, config)
    run_dir = stock_report_dir(config) / prefix
    run_dir.mkdir(parents=True, exist_ok=True)
    trades_path = run_dir / "trades.csv"
    signals_path = run_dir / "signals.csv"
    pnl_path = run_dir / "pnl.csv"
    html_path = run_dir / "report.html"
    summary_path = run_dir / "summary.json"
    llm_path = run_dir / "llm_report.json"

    trades_rows = _build_trade_rows(tsst, config)
    signal_rows = _build_signal_rows(tsst)
    pnl_rows = _build_pnl_rows(trades_rows)
    if kbar_rows is None:
        kbar_rows = build_kbar_rows_from_ticks(ticks, config)

    _write_csv(trades_path, trades_rows)
    _write_csv(signals_path, signal_rows)
    _write_csv(pnl_path, pnl_rows)

    trading_days = sorted({str(row["datetime"])[:10] for row in kbar_rows})
    performance = calculate_performance_metrics(
        pnl_rows,
        config.initial_capital,
        trading_days=trading_days,
    )
    risk_metrics = calculate_mark_to_market_risk(
        trades_rows,
        kbar_rows,
        config.initial_capital,
    )
    performance["max_drawdown"] = risk_metrics["max_drawdown"]
    performance["sharpe_ratio"] = risk_metrics["sharpe_ratio"]
    total_net_pnl = float(performance["net_pnl"])
    total_gross_pnl = float(performance["gross_pnl"])
    total_fee = float(performance["total_fee"])
    total_tax = float(performance["total_tax"])
    transaction_cost = float(performance["transaction_cost"])
    win_rate = float(performance["win_rate"])
    gross_profit = float(performance["gross_profit"])
    gross_loss = float(performance["gross_loss"])
    profit_factor = float(performance["profit_factor"])
    payoff_ratio = float(performance["payoff_ratio"])
    break_even_win_rate = float(performance["break_even_win_rate"])
    last_price = (
        float(kbar_rows[-1]["close"])
        if kbar_rows
        else (float(ticks[-1]["close"]) if ticks else 0.0)
    )
    ending_assets = (
        float(tsst.local_cash) + tsst.local_position * last_price
        if tsst.local_cash is not None
        else float(performance["ending_capital"])
    )
    total_return = (
        (ending_assets - config.initial_capital) / config.initial_capital
        if config.initial_capital
        else 0.0
    )
    benchmark = calculate_buy_and_hold_benchmark(kbar_rows, config)
    research_status = (
        "通過初步篩選"
        if total_return > 0 and profit_factor > 1 and len(pnl_rows) >= 5
        else "未通過，保留作對照"
    )
    news_summary = load_optional_news_summary(config)
    rejected_orders = sum(
        1 for order in tsst.local_orders if order.get("status_code") != "BT-00000"
    )
    first_timestamp = market_stats.first_timestamp if market_stats else (
        float(ticks[0]["timestamp"]) if ticks else None
    )
    last_timestamp = market_stats.last_timestamp if market_stats else (
        float(ticks[-1]["timestamp"]) if ticks else None
    )
    tick_count = market_stats.tick_count if market_stats else len(ticks)
    actual_tick_start = (
        taipei_datetime_from_timestamp(first_timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if first_timestamp is not None
        else "無資料"
    )
    actual_tick_end = (
        taipei_datetime_from_timestamp(last_timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if last_timestamp is not None
        else "無資料"
    )

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
            _metric("策略", f"{STRATEGY_LABELS[config.strategy]} ({config.strategy})"),
            _metric("資料來源", _display_tick_source(config)),
            _metric("回測開始日期", config.backtest_start),
            _metric("回測結束日期", config.backtest_end),
            _metric("回測期間", f"{config.backtest_start} ~ {config.backtest_end}"),
            _metric("實際第一筆 Tick", actual_tick_start),
            _metric("實際最後一筆 Tick", actual_tick_end),
            _metric("K 線週期", config.kbar_interval),
            _metric("初始本金", _format_money(config.initial_capital)),
            _metric("單次股數上限", f"{config.quantity:,} 股"),
            _metric(
                "資金配置",
                (
                    f"動態股數，最多使用 {config.capital_utilization:.0%} 可用現金"
                    if config.position_sizing == "cash_fraction"
                    else f"固定 {config.lots:,} 張"
                ),
            ),
            _metric("持倉模式", "跨日波段" if config.holding_mode == "swing" else "當日沖銷"),
            _metric("買賣手續費率", f"{config.stock_fee_rate:.4%}"),
            _metric("一般賣出交易稅率", f"{config.stock_tax_rate:.4%}"),
            _metric(
                "符合條件的現股當沖稅率",
                f"{config.day_trade_tax_rate:.4%}" if config.apply_day_trade_tax else "未套用",
            ),
            _metric(
                "本地歷史資料庫",
                config.data_cache_path if config.use_data_cache else "停用",
            ),
            _metric("Tick 筆數", f"{tick_count:,}"),
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
            + (" 且 MACD > 0" if config.macd_require_positive else "")
        )
    elif config.strategy == "vote":
        macd_rule_text = (
            f"多數決輸入，{config.macd_fast_period}/{config.macd_slow_period}/"
            f"{config.macd_signal_period}，MACD > signal 為多方票"
        )
    else:
        macd_rule_text = (
            f"{'啟用' if config.use_macd else '停用'}，"
            f"{config.macd_fast_period}/{config.macd_slow_period}/{config.macd_signal_period}"
        )
    strategy_rule_text = {
        "ma": (
            f"MA 策略：收盤價站上 MA{config.ma_slow_period}，且 "
            f"MA{config.ma_fast_period} 上穿 MA{config.ma_mid_period}；"
            f"使用上升中的趨勢 MA{config.trend_ma_period} 過濾"
        ),
        "rsi": (
            f"RSI 策略：RSI "
            + ("由下往上進入 " if config.rsi_require_cross else "位於 ")
            + f"{config.rsi_buy_above:g}–{config.rsi_buy_below:g}"
            + (
                f"，避開 {config.rsi_block_low:g}–{config.rsi_block_high:g}"
                if config.use_rsi_block
                else ""
            )
            + (
                f"；使用上升中的趨勢 MA{config.trend_ma_period} 過濾"
                if config.require_trend_filter
                else ""
            )
        ),
        "macd": (
            "MACD 策略：MACD 上穿 signal"
            + (" 且 MACD > 0" if config.macd_require_positive else "")
            + (
                f"；使用上升中的趨勢 MA{config.trend_ma_period} 過濾"
                if config.require_trend_filter
                else ""
            )
        ),
        "bollinger": (
            f"布林通道反轉：前一根收盤價低於下軌，本根回到下軌之上；"
            f"週期 {config.bollinger_period}、{config.bollinger_stddev:g} 倍標準差，"
            f"RSI ≤ {config.bollinger_rsi_max:g}，至中線空間至少 "
            f"{config.bollinger_min_reward_pct:.2%}"
        ),
        "breakout": (
            f"區間突破：收盤價首次突破前 {config.breakout_entry_period} 根 K 棒最高價，"
            f"成交量至少為 {config.volume_ma_period} 根均量的 "
            f"{config.breakout_volume_ratio:g} 倍"
        ),
        "vote": (
            f"三指標多數決：MA、RSI、MACD 至少 {config.vote_required}/3 票為多方"
        ),
        "fixed": f"固定價格策略：限價 {config.buy_price:g} 買進",
    }[config.strategy]
    exit_rule_text = {
        "ma": (
            f"跌破 MA{config.ma_slow_period}"
            + (
                f"，或 MA{config.ma_fast_period} 下穿 MA{config.ma_mid_period}"
                if config.ma_exit_on_cross
                else ""
            )
        ),
        "rsi": f"RSI < {config.rsi_sell_below:g}",
        "macd": (
            "MACD 下穿 signal"
            if config.macd_exit_on_cross
            else f"MACD < 0 或跌破趨勢 MA{config.trend_ma_period}"
        ),
        "bollinger": f"收盤價回到布林中線（{config.bollinger_period} 期均線）",
        "breakout": (
            f"跌破前 {config.breakout_exit_period} 根 K 棒最低價"
            + (
                f"，或跌破趨勢 MA{config.trend_ma_period}"
                if config.require_trend_filter
                else ""
            )
        ),
        "vote": f"MA、RSI、MACD 至少 {config.vote_exit_required}/3 票為空方",
        "fixed": f"{config.sell_hour:02d}:{config.sell_minute:02d} 出場",
    }[config.strategy]
    rule_metrics = "\n".join(
        [
            _metric("策略規則", strategy_rule_text),
            _metric("策略類型", STRATEGY_CATEGORIES[config.strategy]),
            _metric("較適合行情", STRATEGY_MARKET_REGIMES[config.strategy]),
            _metric("均線", f"MA{config.ma_fast_period} / MA{config.ma_mid_period} / MA{config.ma_slow_period}"),
            _metric(
                "趨勢濾網",
                (
                    f"收盤價高於 MA{config.trend_ma_period}，且相較前 "
                    f"{config.trend_slope_lookback} 根呈上升"
                    if config.require_trend_filter
                    else "停用"
                ),
            ),
            _metric("出場訊號", exit_rule_text),
            _metric("慢均線距離限制", "停用" if config.entry_near_ma_points <= 0 else f"{config.entry_near_ma_points:g} 點內"),
            _metric("RSI", rsi_rule_text),
            _metric("MACD", macd_rule_text),
            _metric(
                "布林通道",
                f"{config.bollinger_period} 期 / {config.bollinger_stddev:g} 倍標準差",
            ),
            _metric(
                "突破區間",
                f"進場前 {config.breakout_entry_period} 根 / 出場前 {config.breakout_exit_period} 根 K 棒",
            ),
            _metric(
                "多數決門檻",
                f"進場 {config.vote_required}/3 票；出場 {config.vote_exit_required}/3 票",
            ),
            _metric("進場時段", f"{config.entry_start_hour:02d}:{config.entry_start_minute:02d}–{config.entry_cutoff_hour:02d}:{config.entry_cutoff_minute:02d}"),
            _metric(
                "禁止進場時段",
                (
                    f"{config.entry_block_start_hour:02d}:{config.entry_block_start_minute:02d}–"
                    f"{config.entry_block_end_hour:02d}:{config.entry_block_end_minute:02d}"
                    if config.use_entry_block
                    else "停用"
                ),
            ),
            _metric("停損 / 停利", f"{config.stop_loss_pct:.2%} / {config.take_profit_pct:.2%}"),
            _metric("最少持有 / 冷卻", f"{config.min_hold_bars} / {config.cooldown_bars} 根 K 棒"),
            _metric(
                "最長持有",
                f"{config.max_hold_bars} 根 K 棒" if config.max_hold_bars > 0 else "停用",
            ),
            _metric("每日最多進場", f"{config.max_entries_per_day} 次"),
            _metric(
                "強制出場",
                (
                    "僅於回測結束平倉"
                    if config.holding_mode == "swing"
                    else f"每日 {config.sell_hour:02d}:{config.sell_minute:02d}"
                ),
            ),
        ]
    )
    result_metrics = "\n".join(
        [
            _metric("成交紀錄", len(trades_rows)),
            _metric("未成交／拒絕訂單", rejected_orders),
            _metric("訊號紀錄", len(signal_rows)),
            _metric("完成配對", len(pnl_rows)),
            _metric("勝率", f"{win_rate:.2%}"),
            _metric("損益平衡勝率", f"{break_even_win_rate:.2%}"),
            _metric("賺賠比", f"{payoff_ratio:.3f}"),
            _metric("Profit Factor", f"{profit_factor:.3f}"),
            _metric("總報酬率", f"{total_return:.4%}"),
            _metric("策略初步判定", research_status),
            _metric("買賣價差損益（未扣成本）", _format_money(total_gross_pnl)),
            _metric("手續費合計", _format_money(total_fee)),
            _metric("交易稅合計", _format_money(total_tax)),
            _metric("交易成本合計", _format_money(transaction_cost)),
            _metric("淨利／淨損", _format_money(total_net_pnl)),
            _metric(
                "成本造成的績效差異",
                f"{_format_money(total_gross_pnl)} → {_format_money(total_net_pnl)}（減少 {_format_money(transaction_cost)}）",
            ),
            _metric("淨損益公式", "賣出收入－買進成本－買賣手續費－賣出交易稅"),
            _metric(
                "公式示例（非本次交易）",
                "50 元買 1,000 股、55 元跨日賣出：5,000－71.25－78.375－165＝4,685.375 元；符合當沖條件時交易稅改為 82.5 元",
            ),
            _metric("正負號意義", "正數＝淨利／資產增加；負數＝淨損／資產減少"),
            _metric("獲利交易合計", _format_money(gross_profit)),
            _metric("虧損交易合計", _format_money(gross_loss)),
            _metric("最終資產", _format_money(ending_assets)),
            _metric("最大回撤", f"{float(performance['max_drawdown']):.2%}"),
            _metric("年化 Sharpe（無風險利率 0）", f"{float(performance['sharpe_ratio']):.3f}"),
            _metric("結束部位", f"{tsst.local_position:,} 股"),
            _metric("同期買進持有報酬率", f"{benchmark['total_return']:.4%}"),
        ]
    )
    definition_metrics = "\n".join(
        [
            _metric("金額單位", "新臺幣（NT$）；正數為獲利，負數為虧損"),
            _metric("買進成本", "買進價 × 股數"),
            _metric("賣出收入", "賣出價 × 股數"),
            _metric("未扣成本損益", "賣出收入 − 買進成本"),
            _metric("交易成本", "買進手續費 + 賣出手續費 + 賣出交易稅"),
            _metric("淨利／淨損", "未扣成本損益 − 交易成本"),
            _metric("最終資產", "現金餘額 + 期末持股 × 最後價格"),
            _metric("總報酬率", "（最終資產 − 初始本金）÷ 初始本金"),
            _metric("勝率", "獲利的已平倉交易筆數 ÷ 全部已平倉交易筆數"),
            _metric("最大回撤", "資產曲線相對先前高點的最大跌幅；越低通常代表風險越小"),
            _metric("Sharpe", "已實現交易報酬的年化風險調整績效；本報告假設無風險利率為 0"),
        ]
    )
    output_metrics = "\n".join(
        [
            _metric("交易 CSV", trades_path.name),
            _metric("訊號 CSV", signals_path.name),
            _metric("損益 CSV", pnl_path.name),
            _metric("摘要 JSON", summary_path.name),
            _metric("HTML 報表", html_path.name),
        ]
    )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stock_code": config.code,
        "stock_name": config.stock_name.strip() or config.code,
        "run_name": config.run_name,
        "strategy": config.strategy,
        "strategy_label": STRATEGY_LABELS[config.strategy],
        "strategy_category": STRATEGY_CATEGORIES[config.strategy],
        "suitable_market": STRATEGY_MARKET_REGIMES[config.strategy],
        "backtest_start": config.backtest_start,
        "backtest_end": config.backtest_end,
        "actual_tick_start": actual_tick_start,
        "actual_tick_end": actual_tick_end,
        "tick_count": tick_count,
        "trade_records": len(trades_rows),
        "rejected_orders": rejected_orders,
        "signal_records": len(signal_rows),
        "initial_capital": float(config.initial_capital),
        "final_assets": ending_assets,
        "gross_pnl": total_gross_pnl,
        "fee": total_fee,
        "tax": total_tax,
        "transaction_cost": transaction_cost,
        "net_pnl": total_net_pnl,
        "total_return": total_return,
        "completed_trades": int(performance["completed_trades"]),
        "win_rate": win_rate,
        "break_even_win_rate": break_even_win_rate,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "max_drawdown": float(performance["max_drawdown"]),
        "sharpe_ratio": float(performance["sharpe_ratio"]),
        "ending_position": tsst.local_position,
        "fee_rate": config.stock_fee_rate,
        "tax_rate": config.stock_tax_rate,
        "day_trade_tax_rate": config.day_trade_tax_rate,
        "position_sizing": config.position_sizing,
        "capital_utilization": config.capital_utilization,
        "max_order_shares": config.quantity,
        "holding_mode": config.holding_mode,
        "research_status": research_status,
        "buy_and_hold_return": benchmark["total_return"],
        "buy_and_hold_net_pnl": benchmark["net_pnl"],
        "net_pnl_formula": "sell_revenue - buy_cost - buy_fee - sell_fee - sell_tax",
    }

    llm_result: dict[str, Any] | None = None
    llm_error = ""
    if config.llm_enabled:
        try:
            cache_path = Path(config.llm_cache_path).expanduser()
            if not cache_path.is_absolute():
                cache_path = ROOT / cache_path
            llm_payload = build_analysis_payload(
                summary,
                pnl_rows,
                signal_rows,
                kbar_rows,
                news_summary,
            )
            llm_result = generate_openai_analysis(
                llm_payload,
                api_key=os.getenv("GEMINI_API_KEY" if os.getenv("LLM_PROVIDER") == "Gemini" else "OPENAI_API_KEY", ""),
                model=config.openai_model,
                max_output_tokens=config.llm_max_output_tokens,
                cache_dir=cache_path,
                use_cache=config.llm_use_cache,
                provider=os.getenv("LLM_PROVIDER", "OpenAI"),
            )
            llm_path.write_text(json.dumps(llm_result, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["llm"] = llm_result
        except Exception as exc:
            llm_error = str(exc)
            summary["llm"] = {
                "status": "error",
                "provider": os.getenv("LLM_PROVIDER", "OpenAI"),
                "model": config.openai_model,
                "error": llm_error,
            }
            print(f"LLM analysis skipped: {llm_error}")
    else:
        summary["llm"] = {"status": "disabled", "provider": os.getenv("LLM_PROVIDER", "OpenAI")}

    output_metrics += "\n" + _metric(
        "LLM 分析",
        llm_path.name if llm_result else (f"未完成：{llm_error}" if llm_error else "未啟用"),
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    llm_section = render_analysis_html(llm_result, llm_error)

    if news_summary:
        news_content = _escape(news_summary)
        news_note = "內容由外部新聞或 LLM 摘要檔匯入；請於報告中保留來源與日期。"
    else:
        news_content = "尚未設定新聞摘要檔；核心回測仍可正常執行。"
        news_note = "可在設定檔加入 news_summary_path，匯入經查證的新聞或 LLM 摘要。"
    news_section = f"""
    <div class="d-flex align-items-center section-title">
      <i class="ri-newspaper-fill me-2"></i>
      個股近期資訊（選配）
    </div>
    <div class="section-bar"></div>
    <div class="bg-white border p-3" style="white-space: pre-wrap">{news_content}</div>
    <div class="small text-muted mt-2">{_escape(news_note)}</div>
    """

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
      <i class="ri-question-fill me-2"></i>
      欄位與計算方式
    </div>
    <div class="section-bar"></div>
    <div class="row"><div class="col-lg-12">$definition_metrics</div></div>

    $news_section

    $llm_section

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
            <th>布林下軌</th><th>布林中線</th><th>布林上軌</th><th>突破高點</th><th>突破低點</th>
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
    const strategyName = $strategy_name_json;
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
      if (strategyName === "bollinger") {
        const bbUpper = priceChart.addLineSeries({ color: "#ef5350", lineWidth: 1, lineStyle: 2, title: "布林上軌" });
        const bbMiddle = priceChart.addLineSeries({ color: "#64748b", lineWidth: 1, lineStyle: 2, title: "布林中線" });
        const bbLower = priceChart.addLineSeries({ color: "#26a69a", lineWidth: 1, lineStyle: 2, title: "布林下軌" });
        bbUpper.setData(lineRows("bb_upper"));
        bbMiddle.setData(lineRows("bb_middle"));
        bbLower.setData(lineRows("bb_lower"));
      }
      if (strategyName === "breakout") {
        const breakoutHigh = priceChart.addLineSeries({ color: "#ef5350", lineWidth: 1, lineStyle: 2, title: "突破高點" });
        const breakoutLow = priceChart.addLineSeries({ color: "#26a69a", lineWidth: 1, lineStyle: 2, title: "突破低點" });
        breakoutHigh.setData(lineRows("breakout_high"));
        breakoutLow.setData(lineRows("breakout_low"));
      }
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
            definition_metrics=definition_metrics,
            news_section=news_section,
            llm_section=llm_section,
            signal_table_rows=_signal_table_rows(signal_rows),
            trade_table_rows=_trade_table_rows(pnl_rows),
            kbar_json=_json(kbar_rows),
            signal_marker_json=_json(signal_markers),
            strategy_name_json=_json(config.strategy),
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

    database_ids = save_backtest_to_database(
        config,
        summary,
        trades_rows,
        signal_rows,
        pnl_rows,
        html_path,
    )
    if database_ids:
        summary["database"] = database_ids
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "Database saved: "
            f"analysis_id={database_ids['analysis_id']}, "
            f"backtest_id={database_ids['backtest_id']}, report_id={database_ids['report_id']}"
        )

    print(f"Report generated: {html_path}")
    print(f"Trade records: {trades_path}")
    print(f"Signal records: {signals_path}")
    print(f"PnL records: {pnl_path}")
    print(f"Summary: {summary_path}")
    return html_path


def validate_safety_settings(config: AppConfig) -> None:
    if (not config.is_backtest) and (not config.is_simulation) and (not config.allow_real_trading):
        raise RuntimeError(
            "系統已阻擋真實下單；只有在確認用途後，才可設定 ALLOW_REAL_TRADING=true。"
        )


def validate_env(config: AppConfig) -> None:
    if config.tick_source == "sinopac":
        required = [] if is_cache_range_complete(config) else ["API_KEY", "API_SECRET"]
    else:
        required = ["EMAIL", "TSST_TOKEN"]

    if config.tick_source == "tsst" and not config.only_backtest:
        required += ["API_KEY", "API_SECRET", "CA_PATH", "CA_PASSWORD"]

    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError("缺少必要的 .env 設定值：" + ", ".join(missing))


def validate_config(config: AppConfig) -> None:
    """檢查參數是否合理，避免回測跑到一半才因設定錯誤中斷。"""
    assert_config(config)

    if config.lots <= 0:
        raise RuntimeError("--lots 必須大於 0")
    if config.initial_capital <= 0:
        raise RuntimeError("--initial-capital 必須大於 0")
    if not 0 < config.capital_utilization <= 1:
        raise RuntimeError("--capital-utilization 必須大於 0 且不超過 1")
    if (
        not 0 <= config.stock_fee_rate < 1
        or not 0 <= config.stock_tax_rate < 1
        or not 0 <= config.day_trade_tax_rate < 1
    ):
        raise RuntimeError("手續費率與交易稅率必須介於 0（含）到 1（不含）")
    try:
        start_date = parse_ymd(config.backtest_start)
        end_date = parse_ymd(config.backtest_end)
    except ValueError as exc:
        raise RuntimeError("回測日期必須使用 YYYY-MM-DD 格式") from exc
    if start_date > end_date:
        raise RuntimeError("回測開始日期不可晚於結束日期")
    if config.use_data_cache and not config.data_cache_path.strip():
        raise RuntimeError("啟用本地資料庫時，--data-cache-path 不可留白")
    if config.kbar_freq <= 0:
        raise RuntimeError("--kbar-freq 必須大於 0")
    if min(config.ma_fast_period, config.ma_mid_period, config.ma_slow_period) <= 0:
        raise RuntimeError("均線週期必須大於 0")
    if not config.ma_fast_period < config.ma_mid_period < config.ma_slow_period:
        raise RuntimeError("均線週期必須符合：快速 < 中期 < 慢速")
    if min(config.trend_ma_period, config.trend_slope_lookback) <= 0:
        raise RuntimeError("趨勢均線週期與斜率回看期必須大於 0")
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
    if config.bollinger_period <= 1:
        raise RuntimeError("--bollinger-period 必須大於 1")
    if config.bollinger_stddev <= 0:
        raise RuntimeError("--bollinger-stddev 必須大於 0")
    if not 0 <= config.bollinger_rsi_max <= 100:
        raise RuntimeError("--bollinger-rsi-max 必須介於 0 到 100")
    if config.bollinger_min_reward_pct < 0:
        raise RuntimeError("--bollinger-min-reward-pct 不可小於 0")
    if min(config.breakout_entry_period, config.breakout_exit_period) <= 0:
        raise RuntimeError("突破策略的進場與出場週期必須大於 0")
    if config.breakout_volume_ratio < 0 or config.volume_ma_period <= 0:
        raise RuntimeError("突破量能倍率不可小於 0，均量週期必須大於 0")
    if not 1 <= config.vote_required <= 3:
        raise RuntimeError("--vote-required 必須介於 1 到 3")
    if not 1 <= config.vote_exit_required <= 3:
        raise RuntimeError("--vote-exit-required 必須介於 1 到 3")
    if min(config.stop_loss_pct, config.take_profit_pct) < 0:
        raise RuntimeError("停損與停利比例不可小於 0")
    if min(config.min_hold_bars, config.max_hold_bars, config.cooldown_bars) < 0:
        raise RuntimeError("最少持有、最長持有與冷卻 K 棒數不可小於 0")
    if config.max_hold_bars > 0 and config.max_hold_bars < config.min_hold_bars:
        raise RuntimeError("最長持有 K 棒數不可小於最少持有 K 棒數")
    if config.max_entries_per_day <= 0:
        raise RuntimeError("--max-entries-per-day 必須大於 0")
    if config.llm_enabled and not config.openai_model.strip():
        raise RuntimeError("啟用 OpenAI LLM 時，--openai-model 不可留白")
    if config.llm_max_output_tokens <= 0:
        raise RuntimeError("--llm-max-output-tokens 必須大於 0")
    if config.llm_enabled and not config.llm_cache_path.strip():
        raise RuntimeError("啟用 OpenAI LLM 時，--llm-cache-path 不可留白")


def build_tsst(config: AppConfig) -> BacktestSafeTsst:
    """依照策略參數建立 TSST 物件，並設定回測或交易模式。"""
    assert_config(config)

    strategy_cls: type[BacktestSafeTsst]
    strategy_cls = FixedPriceTsst if config.strategy == "fixed" else MovingAverageTsst

    tsst = strategy_cls(
        config=config,
        use_broker="Sino",
        is_simulation=config.is_simulation,
        is_backtest=config.is_backtest,
        local_only=config.tick_source == "sinopac" and config.is_backtest,
    )
    tsst.configure_local_account(config)

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
        description="TSST/Sinopac 股票回測與報表產生器",
        parents=[pre_parser],
    )
    parser.add_argument(
        "--strategy",
        choices=["ma", "rsi", "macd", "bollinger", "breakout", "vote", "fixed"],
        default=os.getenv("STRATEGY", "ma"),
    )
    parser.add_argument("--tick-source", choices=["sinopac", "tsst"], default=os.getenv("TICK_SOURCE", "sinopac"))
    parser.add_argument("--run-name", default=os.getenv("RUN_NAME", ""))
    parser.add_argument("--code", default=os.getenv("STOCK_CODE", "2303"))
    parser.add_argument("--backtest-start", default=os.getenv("BACKTEST_START", os.getenv("BACKTEST_DATE", "2026-01-01")))
    parser.add_argument("--backtest-end", default=os.getenv("BACKTEST_END", "2026-06-30"))
    parser.add_argument("--backfill-start", default=os.getenv("BACKFILL_START", os.getenv("FILLING_DATE", "2026-02-01")))
    parser.add_argument("--backfill-end", default=os.getenv("BACKFILL_END", "2026-02-28"))
    parser.add_argument("--lots", type=int, default=int(os.getenv("ORDER_LOTS", "1")))
    parser.add_argument(
        "--position-sizing",
        choices=["fixed_lots", "cash_fraction"],
        default=os.getenv("POSITION_SIZING", "cash_fraction"),
    )
    parser.add_argument(
        "--capital-utilization",
        type=float,
        default=float(os.getenv("CAPITAL_UTILIZATION", "0.95")),
    )
    parser.add_argument(
        "--holding-mode",
        choices=["intraday", "swing"],
        default=os.getenv("HOLDING_MODE", "swing"),
    )
    parser.add_argument("--buy-price", type=float, default=float(os.getenv("BUY_PRICE", "1790")))
    parser.add_argument("--sell-hour", type=int, default=int(os.getenv("SELL_HOUR", "13")))
    parser.add_argument("--sell-minute", type=int, default=int(os.getenv("SELL_MINUTE", "20")))
    parser.add_argument("--initial-capital", type=int, default=int(os.getenv("INITIAL_CAPITAL", "100000")))
    parser.add_argument("--stock-fee-rate", type=float, default=float(os.getenv("STOCK_FEE_RATE", "0.001425")))
    parser.add_argument("--stock-tax-rate", type=float, default=float(os.getenv("STOCK_TAX_RATE", "0.003")))
    parser.add_argument(
        "--day-trade-tax-rate",
        type=float,
        default=float(os.getenv("DAY_TRADE_TAX_RATE", "0.0015")),
    )
    parser.add_argument(
        "--data-cache-path",
        default=os.getenv("DATA_CACHE_PATH", "data/market_data.sqlite3"),
    )
    parser.add_argument("--news-summary-path", default=os.getenv("NEWS_SUMMARY_PATH", ""))
    parser.add_argument(
        "--stock-codes",
        default=os.getenv("STOCK_CODES", ""),
        help="供 update_data.py 使用的逗號分隔股票清單；單次回測仍使用 --code",
    )
    parser.add_argument("--db-host", default=os.getenv("DB_HOST", "127.0.0.1"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", "ai_stock_system"))
    parser.add_argument("--db-user", default=os.getenv("DB_USER", ""))
    parser.add_argument("--db-password", default=os.getenv("DB_PASSWORD", ""))
    parser.add_argument("--db-user-id", type=int, default=int(os.getenv("DB_USER_ID", "1")))
    parser.add_argument("--stock-name", default=os.getenv("STOCK_NAME", ""))
    parser.add_argument("--openai-model", default=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
    parser.add_argument(
        "--llm-max-output-tokens",
        type=int,
        default=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1400")),
    )
    parser.add_argument("--llm-cache-path", default=os.getenv("LLM_CACHE_PATH", "data/llm_cache"))
    parser.add_argument("--kbar-unit", default=os.getenv("KBAR_UNIT", "m"))
    parser.add_argument("--kbar-freq", type=int, default=int(os.getenv("KBAR_FREQ", "5")))
    parser.add_argument("--ma-fast-period", type=int, default=int(os.getenv("MA_FAST_PERIOD", "5")))
    parser.add_argument("--ma-mid-period", type=int, default=int(os.getenv("MA_MID_PERIOD", "10")))
    parser.add_argument("--ma-slow-period", type=int, default=int(os.getenv("MA_SLOW_PERIOD", "20")))
    parser.add_argument("--trend-ma-period", type=int, default=int(os.getenv("TREND_MA_PERIOD", "120")))
    parser.add_argument(
        "--trend-slope-lookback",
        type=int,
        default=int(os.getenv("TREND_SLOPE_LOOKBACK", "12")),
    )
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
    parser.add_argument("--bollinger-period", type=int, default=int(os.getenv("BOLLINGER_PERIOD", "20")))
    parser.add_argument("--bollinger-stddev", type=float, default=float(os.getenv("BOLLINGER_STDDEV", "2")))
    parser.add_argument(
        "--bollinger-rsi-max",
        type=float,
        default=float(os.getenv("BOLLINGER_RSI_MAX", "100")),
    )
    parser.add_argument(
        "--bollinger-min-reward-pct",
        type=float,
        default=float(os.getenv("BOLLINGER_MIN_REWARD_PCT", "0")),
    )
    parser.add_argument(
        "--breakout-entry-period",
        type=int,
        default=int(os.getenv("BREAKOUT_ENTRY_PERIOD", "20")),
    )
    parser.add_argument(
        "--breakout-exit-period",
        type=int,
        default=int(os.getenv("BREAKOUT_EXIT_PERIOD", "10")),
    )
    parser.add_argument(
        "--breakout-volume-ratio",
        type=float,
        default=float(os.getenv("BREAKOUT_VOLUME_RATIO", "0")),
    )
    parser.add_argument(
        "--volume-ma-period",
        type=int,
        default=int(os.getenv("VOLUME_MA_PERIOD", "20")),
    )
    parser.add_argument("--vote-required", type=int, default=int(os.getenv("VOTE_REQUIRED", "2")))
    parser.add_argument(
        "--vote-exit-required",
        type=int,
        default=int(os.getenv("VOTE_EXIT_REQUIRED", "2")),
    )
    parser.add_argument("--stop-loss-pct", type=float, default=float(os.getenv("STOP_LOSS_PCT", "0.015")))
    parser.add_argument("--take-profit-pct", type=float, default=float(os.getenv("TAKE_PROFIT_PCT", "0.03")))
    parser.add_argument("--min-hold-bars", type=int, default=int(os.getenv("MIN_HOLD_BARS", "2")))
    parser.add_argument("--max-hold-bars", type=int, default=int(os.getenv("MAX_HOLD_BARS", "0")))
    parser.add_argument("--cooldown-bars", type=int, default=int(os.getenv("COOLDOWN_BARS", "6")))
    parser.add_argument("--max-entries-per-day", type=int, default=int(os.getenv("MAX_ENTRIES_PER_DAY", "1")))
    parser.set_defaults(
        is_backtest=str_to_bool(os.getenv("IS_BACKTEST", "true"), default=True),
        is_simulation=str_to_bool(os.getenv("IS_SIMULATION", "true"), default=True),
        only_backtest=str_to_bool(os.getenv("ONLY_BACKTEST", "true"), default=True),
        allow_real_trading=str_to_bool(os.getenv("ALLOW_REAL_TRADING", "false"), default=False),
        use_rsi=str_to_bool(os.getenv("USE_RSI", "true"), default=True),
        use_entry_block=str_to_bool(os.getenv("USE_ENTRY_BLOCK", "true"), default=True),
        use_rsi_block=str_to_bool(os.getenv("USE_RSI_BLOCK", "true"), default=True),
        use_macd=str_to_bool(os.getenv("USE_MACD", "true"), default=True),
        use_data_cache=str_to_bool(os.getenv("USE_DATA_CACHE", "true"), default=True),
        apply_day_trade_tax=str_to_bool(os.getenv("APPLY_DAY_TRADE_TAX", "true"), default=True),
        require_trend_filter=str_to_bool(os.getenv("REQUIRE_TREND_FILTER", "false"), default=False),
        ma_exit_on_cross=str_to_bool(os.getenv("MA_EXIT_ON_CROSS", "true"), default=True),
        rsi_require_cross=str_to_bool(os.getenv("RSI_REQUIRE_CROSS", "false"), default=False),
        macd_require_positive=str_to_bool(os.getenv("MACD_REQUIRE_POSITIVE", "false"), default=False),
        macd_exit_on_cross=str_to_bool(os.getenv("MACD_EXIT_ON_CROSS", "true"), default=True),
        bollinger_require_bullish_candle=str_to_bool(
            os.getenv("BOLLINGER_REQUIRE_BULLISH_CANDLE", "false"), default=False
        ),
        breakout_require_cross=str_to_bool(os.getenv("BREAKOUT_REQUIRE_CROSS", "false"), default=False),
        verbose_bars=str_to_bool(os.getenv("VERBOSE_BARS", "false"), default=False),
        db_enabled=str_to_bool(os.getenv("DB_ENABLED", "false"), default=False),
        llm_enabled=str_to_bool(os.getenv("LLM_ENABLED", "false"), default=False),
        llm_use_cache=str_to_bool(os.getenv("LLM_USE_CACHE", "true"), default=True),
    )
    parser.add_argument("--backtest", dest="is_backtest", action="store_true")
    parser.add_argument("--live", dest="is_backtest", action="store_false")
    parser.add_argument("--simulation", dest="is_simulation", action="store_true")
    parser.add_argument("--real-market", dest="is_simulation", action="store_false")
    parser.add_argument("--only-backtest", dest="only_backtest", action="store_true")
    parser.add_argument("--allow-real-trading", dest="allow_real_trading", action="store_true")
    parser.add_argument("--use-rsi", dest="use_rsi", action="store_true")
    parser.add_argument("--disable-rsi", dest="use_rsi", action="store_false")
    parser.add_argument("--use-entry-block", dest="use_entry_block", action="store_true")
    parser.add_argument("--disable-entry-block", dest="use_entry_block", action="store_false")
    parser.add_argument("--use-rsi-block", dest="use_rsi_block", action="store_true")
    parser.add_argument("--disable-rsi-block", dest="use_rsi_block", action="store_false")
    parser.add_argument("--use-macd", dest="use_macd", action="store_true")
    parser.add_argument("--disable-macd", dest="use_macd", action="store_false")
    parser.add_argument("--use-data-cache", dest="use_data_cache", action="store_true")
    parser.add_argument("--disable-data-cache", dest="use_data_cache", action="store_false")
    parser.add_argument("--apply-day-trade-tax", dest="apply_day_trade_tax", action="store_true")
    parser.add_argument("--disable-day-trade-tax", dest="apply_day_trade_tax", action="store_false")
    parser.add_argument("--require-trend-filter", dest="require_trend_filter", action="store_true")
    parser.add_argument("--disable-trend-filter", dest="require_trend_filter", action="store_false")
    parser.add_argument("--ma-exit-on-cross", dest="ma_exit_on_cross", action="store_true")
    parser.add_argument("--ma-keep-until-slow", dest="ma_exit_on_cross", action="store_false")
    parser.add_argument("--rsi-require-cross", dest="rsi_require_cross", action="store_true")
    parser.add_argument("--rsi-allow-range-entry", dest="rsi_require_cross", action="store_false")
    parser.add_argument("--macd-require-positive", dest="macd_require_positive", action="store_true")
    parser.add_argument("--macd-allow-negative", dest="macd_require_positive", action="store_false")
    parser.add_argument("--macd-exit-on-cross", dest="macd_exit_on_cross", action="store_true")
    parser.add_argument("--macd-exit-on-zero", dest="macd_exit_on_cross", action="store_false")
    parser.add_argument(
        "--bollinger-require-bullish-candle",
        dest="bollinger_require_bullish_candle",
        action="store_true",
    )
    parser.add_argument(
        "--bollinger-allow-any-candle",
        dest="bollinger_require_bullish_candle",
        action="store_false",
    )
    parser.add_argument("--breakout-require-cross", dest="breakout_require_cross", action="store_true")
    parser.add_argument("--breakout-allow-state", dest="breakout_require_cross", action="store_false")
    parser.add_argument("--verbose-bars", dest="verbose_bars", action="store_true")
    parser.add_argument("--quiet-bars", dest="verbose_bars", action="store_false")
    parser.add_argument("--db-enabled", dest="db_enabled", action="store_true")
    parser.add_argument("--db-disabled", dest="db_enabled", action="store_false")
    parser.add_argument("--llm-enabled", dest="llm_enabled", action="store_true")
    parser.add_argument("--llm-disabled", dest="llm_enabled", action="store_false")
    parser.add_argument("--llm-use-cache", dest="llm_use_cache", action="store_true")
    parser.add_argument("--llm-no-cache", dest="llm_use_cache", action="store_false")
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
        position_sizing=args.position_sizing,
        capital_utilization=args.capital_utilization,
        holding_mode=args.holding_mode,
        buy_price=args.buy_price,
        sell_hour=args.sell_hour,
        sell_minute=args.sell_minute,
        stock_fee_rate=args.stock_fee_rate,
        stock_tax_rate=args.stock_tax_rate,
        day_trade_tax_rate=args.day_trade_tax_rate,
        apply_day_trade_tax=args.apply_day_trade_tax,
        kbar_unit=args.kbar_unit,
        kbar_freq=args.kbar_freq,
        ma_fast_period=args.ma_fast_period,
        ma_mid_period=args.ma_mid_period,
        ma_slow_period=args.ma_slow_period,
        trend_ma_period=args.trend_ma_period,
        trend_slope_lookback=args.trend_slope_lookback,
        require_trend_filter=args.require_trend_filter,
        ma_exit_on_cross=args.ma_exit_on_cross,
        entry_near_ma_points=args.entry_near_ma_points,
        entry_start_hour=args.entry_start_hour,
        entry_start_minute=args.entry_start_minute,
        entry_cutoff_hour=args.entry_cutoff_hour,
        entry_cutoff_minute=args.entry_cutoff_minute,
        entry_block_start_hour=args.entry_block_start_hour,
        entry_block_start_minute=args.entry_block_start_minute,
        entry_block_end_hour=args.entry_block_end_hour,
        entry_block_end_minute=args.entry_block_end_minute,
        use_entry_block=args.use_entry_block,
        use_rsi=args.use_rsi,
        rsi_period=args.rsi_period,
        rsi_buy_above=args.rsi_buy_above,
        rsi_buy_below=args.rsi_buy_below,
        use_rsi_block=args.use_rsi_block,
        rsi_block_low=args.rsi_block_low,
        rsi_block_high=args.rsi_block_high,
        rsi_sell_below=args.rsi_sell_below,
        rsi_require_cross=args.rsi_require_cross,
        use_macd=args.use_macd,
        macd_fast_period=args.macd_fast_period,
        macd_slow_period=args.macd_slow_period,
        macd_signal_period=args.macd_signal_period,
        macd_require_positive=args.macd_require_positive,
        macd_exit_on_cross=args.macd_exit_on_cross,
        bollinger_period=args.bollinger_period,
        bollinger_stddev=args.bollinger_stddev,
        bollinger_rsi_max=args.bollinger_rsi_max,
        bollinger_min_reward_pct=args.bollinger_min_reward_pct,
        bollinger_require_bullish_candle=args.bollinger_require_bullish_candle,
        breakout_entry_period=args.breakout_entry_period,
        breakout_exit_period=args.breakout_exit_period,
        breakout_require_cross=args.breakout_require_cross,
        breakout_volume_ratio=args.breakout_volume_ratio,
        volume_ma_period=args.volume_ma_period,
        vote_required=args.vote_required,
        vote_exit_required=args.vote_exit_required,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        min_hold_bars=args.min_hold_bars,
        max_hold_bars=args.max_hold_bars,
        cooldown_bars=args.cooldown_bars,
        max_entries_per_day=args.max_entries_per_day,
        verbose_bars=args.verbose_bars,
        use_data_cache=args.use_data_cache,
        data_cache_path=args.data_cache_path,
        news_summary_path=args.news_summary_path,
        stock_codes=args.stock_codes,
        db_enabled=args.db_enabled,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        db_user_id=args.db_user_id,
        stock_name=args.stock_name,
        llm_enabled=args.llm_enabled,
        openai_model=args.openai_model,
        llm_max_output_tokens=args.llm_max_output_tokens,
        llm_cache_path=args.llm_cache_path,
        llm_use_cache=args.llm_use_cache,
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
        f"Trend filter: {'on' if config.require_trend_filter else 'off'} "
        f"MA{config.trend_ma_period}, slope lookback={config.trend_slope_lookback}"
    )
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
    print(f"Bollinger: period={config.bollinger_period} stddev={config.bollinger_stddev:g}")
    print(
        f"Breakout: entry={config.breakout_entry_period} bars "
        f"exit={config.breakout_exit_period} bars"
    )
    print(f"Vote: required={config.vote_required}/3")
    print(
        f"Risk: stop={config.stop_loss_pct:.2%} take={config.take_profit_pct:.2%} "
        f"min_hold={config.min_hold_bars} max_hold={config.max_hold_bars} "
        f"cooldown={config.cooldown_bars} "
        f"max_entries/day={config.max_entries_per_day}"
    )
    print(
        f"Entry window: {config.entry_start_hour:02d}:{config.entry_start_minute:02d}"
        f"-{config.entry_cutoff_hour:02d}:{config.entry_cutoff_minute:02d}"
    )
    print(
        "Blocked entry window: "
        + (
            f"{config.entry_block_start_hour:02d}:{config.entry_block_start_minute:02d}"
            f"-{config.entry_block_end_hour:02d}:{config.entry_block_end_minute:02d}"
            if config.use_entry_block
            else "off"
        )
    )
    print(
        f"Position sizing: {config.position_sizing}, max={config.quantity} shares, "
        f"cash utilization={config.capital_utilization:.0%}"
    )
    print(f"Holding mode: {config.holding_mode}")
    print(f"Initial capital: {config.initial_capital:,.0f}")
    print(
        f"Fee/general tax/day-trade tax: {config.stock_fee_rate:.4%}/"
        f"{config.stock_tax_rate:.4%}/{config.day_trade_tax_rate:.4%}"
    )
    print(
        "Local database: "
        + (str(resolve_data_cache_path(config)) if config.use_data_cache else "disabled")
    )
    print("===================================")

    tsst = build_tsst(config)
    if config.tick_source == "sinopac":
        api: sj.Shioaji | None = None
        try:
            if not is_cache_range_complete(config):
                api = login_sinopac()
            else:
                print("指定期間已完整快取，略過外部 API 登入。")
            if config.strategy == "fixed":
                ticks = fetch_sinopac_ticks(api, config)
                replay_ticks(tsst, ticks)
                generate_local_report(tsst, config, ticks)
            else:
                # 先補齊快取但不把數百萬筆 tick 載入記憶體，再直接回放約六千根 K 棒。
                fetch_sinopac_ticks(api, config, collect_ticks=False)
                kbar_frame, market_stats = load_cached_kbar_frame(config)
                replay_kbars(tsst, kbar_frame, market_stats)
                generate_local_report(
                    tsst,
                    config,
                    kbar_rows=build_kbar_rows_from_frame(kbar_frame, config),
                    market_stats=market_stats,
                )
        finally:
            if api is not None:
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
