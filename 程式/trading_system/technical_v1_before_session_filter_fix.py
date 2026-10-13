"""Technical V1 的研究治理、三個策略家族與候選訊號契約。"""
from __future__ import annotations

import csv
import contextlib
import dataclasses
import hashlib
import html
import io
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import polars as pl

from trading_system import backtest as bt
from trading_system.backtest import (
    AppConfig,
    MovingAverageTsst,
    number_or_none,
    validate_config as validate_app_config,
)
from trading_system.research_guard import assert_development_period

FAMILY_NAMES = ("trend_momentum", "breakout", "majority_vote")
LEGACY_FAMILY_NAME = "legacy_vote"
TAIPEI = ZoneInfo("Asia/Taipei")
PERIOD_POLICY = {
    "training": ("2023-01-01", "2024-12-31"),
    "validation": ("2025-01-01", "2025-06-30"),
    "development_seen": ("2026-01-01", "2026-06-30"),
    "additional_holdout": ("2025-07-01", "2025-12-31"),
    "final_out_of_sample": ("2026-07-01", None),
}
LOCKED_ROLES = {"additional_holdout", "final_out_of_sample"}
CANDIDATE_SIGNAL_FIELDS = (
    "technical_version",
    "strategy_family",
    "candidate_id",
    "parameter_fingerprint",
    "stock_code",
    "data_role",
    "fold_id",
    "timezone",
    "signal_time",
    "signal_bar_end_time",
    "earliest_entry_time",
    "technical_signal",
    "entry_rule_id",
    "exit_rule_id",
    "close_at_signal",
    "reason",
)
REQUIRED_MARKET_COLUMNS = {
    "stock_code",
    "freq_minutes",
    "range_start",
    "range_end",
    "kbar_timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
}
FORBIDDEN_REALTIME_FIELDS = {
    "forward_return",
    "future_return",
    "future_close",
    "label",
    "target",
}


class TechnicalV1Error(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TechnicalV1Error(f"{label} 必須是 YYYY-MM-DD") from exc


def iter_candidates(config: dict[str, Any]):
    for family_name in FAMILY_NAMES:
        family = config["strategy_families"][family_name]
        for candidate in family["candidates"]:
            yield family_name, family, candidate


def iter_execution_candidates(config: dict[str, Any]):
    legacy = config["legacy_baseline"]
    yield LEGACY_FAMILY_NAME, legacy, legacy["candidate"]
    yield from iter_candidates(config)


def _validate_periods(config: dict[str, Any]) -> None:
    periods = config.get("periods", {})
    if set(periods) != set(PERIOD_POLICY):
        raise TechnicalV1Error("資料角色必須完整且不可新增未治理期間")
    for role, (expected_start, expected_end) in PERIOD_POLICY.items():
        period = periods[role]
        if period.get("start") != expected_start or period.get("end") != expected_end:
            raise TechnicalV1Error(f"{role} 日期與固定資料政策不一致")
        if role in LOCKED_ROLES:
            if period.get("access") != "forbidden":
                raise TechnicalV1Error(f"{role} 必須保持 forbidden")
        else:
            assert_development_period(expected_start, expected_end)


def _validate_walk_forward(config: dict[str, Any]) -> None:
    folds = config.get("walk_forward_folds", [])
    if not folds:
        raise TechnicalV1Error("至少需要一個時間順序 Walk-forward fold")
    training_start = _parse_date(PERIOD_POLICY["training"][0], "training.start")
    training_end = _parse_date(PERIOD_POLICY["training"][1], "training.end")
    previous_evaluate_end: date | None = None
    ids: set[str] = set()
    for fold in folds:
        fold_id = str(fold.get("id", ""))
        if not fold_id or fold_id in ids:
            raise TechnicalV1Error("Walk-forward fold id 不可空白或重複")
        ids.add(fold_id)
        train_start = _parse_date(fold.get("train_start"), f"{fold_id}.train_start")
        train_end = _parse_date(fold.get("train_end"), f"{fold_id}.train_end")
        evaluate_start = _parse_date(fold.get("evaluate_start"), f"{fold_id}.evaluate_start")
        evaluate_end = _parse_date(fold.get("evaluate_end"), f"{fold_id}.evaluate_end")
        if not training_start <= train_start <= train_end < evaluate_start <= evaluate_end <= training_end:
            raise TechnicalV1Error(f"{fold_id} 必須採 Training 內的 expanding-window 時間順序")
        if previous_evaluate_end is not None and evaluate_start <= previous_evaluate_end:
            raise TechnicalV1Error("Walk-forward 評估區間必須依時間前進且不可重疊")
        previous_evaluate_end = evaluate_end


def _validate_candidates(config: dict[str, Any]) -> None:
    families = config.get("strategy_families", {})
    if tuple(families) != FAMILY_NAMES:
        raise TechnicalV1Error("Technical V1 只能包含 Trend-Momentum、Breakout、Majority Vote")
    ids: set[str] = set()
    for family_name, family, candidate in iter_candidates(config):
        family_candidates = family.get("candidates", [])
        if not 1 <= len(family_candidates) <= 2:
            raise TechnicalV1Error(f"{family_name} 第一輪最多只能有 baseline 與一個 sensitivity")
        candidate_id = str(candidate.get("id", ""))
        if not candidate_id or candidate_id in ids:
            raise TechnicalV1Error("Candidate id 不可空白或重複")
        ids.add(candidate_id)
        if candidate.get("kind") not in {"baseline", "sensitivity"}:
            raise TechnicalV1Error(f"{candidate_id} kind 必須是 baseline 或 sensitivity")
        if candidate.get("kind") == "sensitivity" and not candidate.get("single_change"):
            raise TechnicalV1Error(f"{candidate_id} 必須記錄單一主要修改")
        if not candidate.get("trade_reason"):
            raise TechnicalV1Error(f"{candidate_id} 必須記錄交易理由")
        params = candidate.get("params")
        if not isinstance(params, dict) or not params:
            raise TechnicalV1Error(f"{candidate_id} 缺少固定參數")
        if any(key in params for key in ("stock_code", "stock_codes", "per_stock")):
            raise TechnicalV1Error(f"{candidate_id} 不得包含個股專屬參數")
        _validate_candidate_app_config(config, candidate)
    for family_name in FAMILY_NAMES:
        kinds = [item["kind"] for item in families[family_name]["candidates"]]
        if kinds.count("baseline") != 1:
            raise TechnicalV1Error(f"{family_name} 必須且只能有一個 baseline")
    if families["breakout"].get("bollinger_mode") != "upper_breakout_only":
        raise TechnicalV1Error("Breakout 家族不得混入布林下軌均值回歸")
    majority_name = families["majority_vote"].get("display_name", "")
    if "長期趨勢" not in majority_name:
        raise TechnicalV1Error("Majority Vote 名稱必須揭露長期趨勢濾網")


def _validate_legacy_baseline(config: dict[str, Any]) -> None:
    legacy = config.get("legacy_baseline", {})
    candidate = legacy.get("candidate", {})
    expected = {
        "ma_fast_period": 20,
        "ma_mid_period": 60,
        "ma_slow_period": 120,
        "trend_ma_period": 240,
        "trend_slope_lookback": 24,
        "require_trend_filter": True,
        "rsi_period": 14,
        "rsi_buy_above": 55,
        "rsi_buy_below": 70,
        "rsi_sell_below": 50,
        "rsi_require_cross": False,
        "use_rsi_block": False,
        "macd_fast_period": 12,
        "macd_slow_period": 26,
        "macd_signal_period": 9,
        "macd_require_positive": True,
        "vote_required": 2,
        "vote_exit_required": 3,
        "use_rsi": True,
        "use_macd": True,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.0,
        "max_hold_bars": 0,
    }
    if candidate.get("id") != "LEGACY_VOTE_2_OF_3":
        raise TechnicalV1Error("Legacy Baseline id 不得更動")
    params = candidate.get("params", {})
    changed = [key for key, value in expected.items() if params.get(key) != value]
    if changed:
        raise TechnicalV1Error("Legacy Baseline 不得被新 V1 參數覆蓋：" + ", ".join(changed))
    _validate_candidate_app_config(config, candidate)


def _validate_candidate_app_config(config: dict[str, Any], candidate: dict[str, Any]) -> None:
    values = {
        **config.get("common_backtest", {}),
        **candidate["params"],
        "strategy": candidate["base_strategy"],
        "backtest_start": PERIOD_POLICY["training"][0],
        "backtest_end": PERIOD_POLICY["training"][1],
    }
    fields = {field.name for field in dataclasses.fields(AppConfig)}
    app = AppConfig(**{key: value for key, value in values.items() if key in fields})
    validate_app_config(app)


def validate_config(config: dict[str, Any]) -> None:
    if config.get("scope") != "technical_strategy_only":
        raise TechnicalV1Error("本設定只能用於 Technical Strategy")
    universe = config.get("stock_universe", {})
    stocks = [str(code) for code in universe.get("stock_codes", [])]
    if len(stocks) < 2 or len(stocks) != len(set(stocks)):
        raise TechnicalV1Error("股票集合至少兩檔且不可重複")
    if universe.get("status") != "confirmed":
        raise TechnicalV1Error("Technical V1 正式股票集合必須已確認")
    if set(universe.get("stock_names", {})) != set(stocks):
        raise TechnicalV1Error("股票名稱與代號集合不一致")
    _validate_periods(config)
    _validate_walk_forward(config)
    _validate_candidates(config)
    _validate_legacy_baseline(config)
    common = config.get("common_backtest", {})
    if common.get("kbar_unit") != "m" or common.get("kbar_freq") != 5:
        raise TechnicalV1Error("Technical V1 固定使用 5 分 K")
    if float(common.get("slippage_rate", 0)) != 0:
        raise TechnicalV1Error("第一輪滑價必須固定為 0")
    quality = config.get("data_quality", {})
    if int(quality.get("expected_bars_per_trading_day", 0)) != 54:
        raise TechnicalV1Error("台股 5 分 K 每日完整性基準必須為 54 根")
    if int(quality.get("expected_cadence_seconds", 0)) != 300:
        raise TechnicalV1Error("Technical V1 K 棒間隔必須為 300 秒")
    if int(quality.get("minimum_training_trading_days", 0)) < 400:
        raise TechnicalV1Error("Training 完整性必須設定合理的最低交易日數")
    if config.get("atr", {}).get("enabled") is not False:
        raise TechnicalV1Error("本輪 ATR 必須停用")
    if config.get("output_policy", {}).get("overwrite_existing") is not False:
        raise TechnicalV1Error("研究輸出不得覆寫")
    if config.get("output_policy", {}).get("freeze_manifest_allowed") is not False:
        raise TechnicalV1Error("使用者明確 Freeze 前不得建立 freeze manifest")


def assert_role_can_execute(
    config: dict[str, Any],
    role: str,
    *,
    validation_authorized: bool = False,
    freeze_manifest_path: Path | None = None,
    access_log_path: Path | None = None,
) -> None:
    if role not in PERIOD_POLICY:
        raise TechnicalV1Error(f"未知資料角色：{role}")
    period = config["periods"][role]
    if role in LOCKED_ROLES or period.get("access") == "forbidden":
        raise TechnicalV1Error(f"{role} 為鎖定資料，Technical V1 開發入口禁止存取")
    assert_development_period(period["start"], period["end"])
    if role == "validation":
        if not validation_authorized:
            raise TechnicalV1Error("Validation 需要使用者明確授權")
        if config.get("status") != "FROZEN_RESEARCH_CANDIDATE":
            raise TechnicalV1Error("Validation 前 Technical V1 必須先標記 FROZEN_RESEARCH_CANDIDATE")
        if freeze_manifest_path is None or not freeze_manifest_path.is_file():
            raise TechnicalV1Error("Validation 前需要有效且可稽核的 freeze manifest")
        try:
            manifest = json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TechnicalV1Error("freeze manifest 無法讀取或不是有效 JSON") from exc
        if (
            manifest.get("status") != "FROZEN_RESEARCH_CANDIDATE"
            or manifest.get("config_fingerprint") != fingerprint(config)
        ):
            raise TechnicalV1Error("freeze manifest 與目前 Technical V1 設定不一致")
        if access_log_path is None or not access_log_path.is_file():
            raise TechnicalV1Error("Validation 前必須先準備不可覆寫的 access log")
    if role == "development_seen":
        raise TechnicalV1Error("本輪明確禁止讀取 Development Seen")


def assert_execution_ready(config: dict[str, Any], role: str = "training") -> None:
    if role != "training":
        raise TechnicalV1Error("Technical V1 本輪 runner 只允許 --role training")
    assert_role_can_execute(config, role)
    if config["stock_universe"]["status"] != "confirmed":
        raise TechnicalV1Error("股票集合尚未經使用者確認，禁止讀取行情或執行回測")


def candidate_fingerprint(config: dict[str, Any], candidate: dict[str, Any]) -> str:
    family_name = next(
        name
        for name, _, item in iter_execution_candidates(config)
        if item["id"] == candidate["id"]
    )
    family = (
        config["legacy_baseline"]
        if family_name == LEGACY_FAMILY_NAME
        else config["strategy_families"][family_name]
    )
    return fingerprint(
        {
            "technical_version": config["technical_version"],
            "family": family_name,
            "entry_rule_id": family["entry_rule_id"],
            "exit_rule_id": family["exit_rule_id"],
            "common_backtest": {**config["common_backtest"], **candidate["params"]},
            "base_strategy": candidate["base_strategy"],
            "params": candidate["params"],
        }
    )


def candidate_by_id(
    config: dict[str, Any],
    candidate_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    for family_name, family, candidate in iter_execution_candidates(config):
        if candidate["id"] == candidate_id:
            return family_name, family, candidate
    raise TechnicalV1Error(f"找不到 Candidate：{candidate_id}")


def assert_shared_parameter_assignments(
    rows: Iterable[dict[str, Any]],
    expected_stocks: Iterable[str],
    *,
    expected_candidates: Iterable[str] | None = None,
    expected_folds: Iterable[str] | None = None,
) -> None:
    expected = set(str(code) for code in expected_stocks)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["candidate_id"]), str(row["fold_id"]))
        grouped.setdefault(key, []).append(row)
    if not grouped:
        raise TechnicalV1Error("沒有參數指紋資料可驗證")
    if expected_candidates is not None and expected_folds is not None:
        expected_groups = {
            (str(candidate), str(fold))
            for candidate in expected_candidates
            for fold in expected_folds
        }
        if set(grouped) != expected_groups:
            missing = sorted(expected_groups - set(grouped))
            extra = sorted(set(grouped) - expected_groups)
            raise TechnicalV1Error(
                f"Candidate/Fold 組合不完整；missing={missing}, extra={extra}"
            )
    for (candidate_id, fold_id), items in grouped.items():
        stocks = [str(item["stock_code"]) for item in items]
        fingerprints = {str(item["parameter_fingerprint"]) for item in items}
        if set(stocks) != expected or len(stocks) != len(set(stocks)):
            raise TechnicalV1Error(f"{candidate_id}/{fold_id} 股票集合不完整或重複")
        if len(fingerprints) != 1:
            raise TechnicalV1Error(f"{candidate_id}/{fold_id} 出現個股專屬參數")


def assert_complete_execution_matrix(
    rows: Iterable[dict[str, Any]],
    expected_candidates: Iterable[str],
    expected_folds: Iterable[str],
    expected_stocks: Iterable[str],
) -> None:
    materialized = list(rows)
    expected = {
        (str(candidate), str(fold), str(stock))
        for candidate in expected_candidates
        for fold in expected_folds
        for stock in expected_stocks
    }
    actual = {
        (str(row["candidate_id"]), str(row["fold_id"]), str(row["stock_code"]))
        for row in materialized
    }
    if actual != expected or len(actual) != len(materialized):
        raise TechnicalV1Error(
            "回測矩陣不完整或重複；"
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    incomplete = [
        key for key, row in zip(
            ((str(row["candidate_id"]), str(row["fold_id"]), str(row["stock_code"])) for row in materialized),
            materialized,
        )
        if row.get("status") != "COMPLETED"
    ]
    if incomplete:
        raise TechnicalV1Error(f"回測矩陣含未完成項目：{incomplete}")
    assert_shared_parameter_assignments(
        materialized,
        expected_stocks,
        expected_candidates=expected_candidates,
        expected_folds=expected_folds,
    )


def build_app_config(
    config: dict[str, Any],
    candidate: dict[str, Any],
    stock_code: str,
    period_start: str,
    period_end: str,
) -> AppConfig:
    values = {
        **config["common_backtest"],
        **candidate["params"],
        "strategy": candidate["base_strategy"],
        "run_name": f"{config['experiment_id']}_{candidate['id']}_{stock_code}",
        "code": stock_code,
        "stock_codes": stock_code,
        "stock_name": config["stock_universe"]["stock_names"][stock_code],
        "backtest_start": period_start,
        "backtest_end": period_end,
        "backfill_start": period_start,
        "backfill_end": period_end,
        "tick_source": "sinopac",
        "is_backtest": True,
        "is_simulation": True,
        "only_backtest": True,
        "allow_real_trading": False,
        "llm_enabled": False,
        "db_enabled": False,
    }
    fields = {field.name for field in dataclasses.fields(AppConfig)}
    app = AppConfig(**{key: value for key, value in values.items() if key in fields})
    validate_app_config(app)
    return app


class TechnicalV1Strategy(MovingAverageTsst):
    """沿用既有成交與帳戶核心，只替換 Technical V1 的進出場判斷。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        study_config: dict[str, Any],
        family_name: str,
        family: dict[str, Any],
        candidate: dict[str, Any],
        data_role: str,
        fold_id: str,
        **kwargs: Any,
    ):
        super().__init__(config=config, **kwargs)
        self.study_config = study_config
        self.family_name = family_name
        self.family = family
        self.candidate = candidate
        self.data_role = data_role
        self.fold_id = fold_id
        self.parameter_fingerprint = candidate_fingerprint(study_config, candidate)
        self.candidate_signals: list[dict[str, Any]] = []
        self.evaluation_start_timestamp: int | None = None

    def _has_required_indicators(self, snapshot: dict[str, float | None]) -> bool:
        required = {
            "trend_momentum": (
                "MA_FAST", "MA_MID", "MA_SLOW", "TREND_MA", "TREND_SLOPE",
                "RSI", "MACD", "MACD_SIGNAL",
            ),
            "breakout": (
                "MA_FAST", "MA_MID", "MA_SLOW", "TREND_MA", "TREND_SLOPE",
                "BB_MIDDLE", "BB_UPPER", "BB_LOWER", "BREAKOUT_HIGH", "BREAKOUT_LOW",
            ),
            "majority_vote": (
                "MA_FAST", "MA_MID", "MA_SLOW", "TREND_MA", "TREND_SLOPE",
                "RSI", "MACD", "MACD_SIGNAL",
            ),
            "legacy_vote": (
                "MA_FAST", "MA_MID", "MA_SLOW", "TREND_MA", "TREND_SLOPE",
                "RSI", "MACD", "MACD_SIGNAL",
            ),
        }[self.family_name]
        if self.family_name == "breakout" and self.config.breakout_volume_ratio > 0:
            required = (*required, "VOLUME", "VOLUME_MA")
        return all(snapshot.get(column) is not None for column in required)

    def _entry_reasons(self, close: float, snapshot: dict[str, float | None]) -> list[str]:
        if self.family_name == "trend_momentum":
            return self._trend_momentum_entry_reasons(close, snapshot)
        if self.family_name == "breakout":
            return self._breakout_family_entry_reasons(close, snapshot)
        return self._vote_entry_reasons(close, snapshot)

    def _trend_momentum_entry_reasons(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> list[str]:
        ma_fast = snapshot.get("MA_FAST")
        ma_mid = snapshot.get("MA_MID")
        ma_slow = snapshot.get("MA_SLOW")
        macd = snapshot.get("MACD")
        macd_signal = snapshot.get("MACD_SIGNAL")
        rsi = snapshot.get("RSI")
        ma_ok = (
            ma_fast is not None and ma_mid is not None and ma_slow is not None
            and ma_fast > ma_mid > ma_slow
            and close > ma_slow
            and self._trend_filter_ok(close, snapshot)
        )
        macd_ok = (
            macd is not None and macd_signal is not None and macd > macd_signal
            and (not self.config.macd_require_positive or macd > 0)
        )
        rsi_ok = (
            rsi is not None
            and self.config.rsi_buy_above <= rsi <= self.config.rsi_buy_below
        )
        if not (ma_ok and macd_ok and rsi_ok):
            return []
        return [
            f"MA{self.config.ma_fast_period}>{self.config.ma_mid_period}>{self.config.ma_slow_period}",
            "MACD momentum positive",
            f"RSI {self.config.rsi_buy_above:g}-{self.config.rsi_buy_below:g}",
        ]

    def _breakout_family_entry_reasons(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> list[str]:
        breakout_high = snapshot.get("BREAKOUT_HIGH")
        bb_upper = snapshot.get("BB_UPPER")
        ma_fast = snapshot.get("MA_FAST")
        ma_mid = snapshot.get("MA_MID")
        ma_slow = snapshot.get("MA_SLOW")
        crossed = True
        if self.config.breakout_require_cross:
            previous_close = self.previous_snapshot.get("CLOSE") if self.previous_snapshot else None
            previous_high = self.previous_snapshot.get("BREAKOUT_HIGH") if self.previous_snapshot else None
            crossed = (
                previous_close is not None and previous_high is not None
                and previous_close <= previous_high
            )
        direction_ok = (
            ma_fast is not None and ma_mid is not None and ma_slow is not None
            and ma_fast > ma_mid
            and close > ma_slow
            and self._trend_filter_ok(close, snapshot)
        )
        volume = snapshot.get("VOLUME")
        volume_ma = snapshot.get("VOLUME_MA")
        volume_ok = (
            self.config.breakout_volume_ratio <= 0
            or (
                volume is not None and volume_ma is not None
                and volume >= volume_ma * self.config.breakout_volume_ratio
            )
        )
        if not (
            breakout_high is not None and close > breakout_high and crossed
            and bb_upper is not None and close >= bb_upper
            and direction_ok and volume_ok
        ):
            return []
        reasons = [
            f"close breaks prior {self.config.breakout_entry_period}-bar high",
            "MA direction bullish",
            "close at or above Bollinger upper band",
        ]
        if self.config.breakout_volume_ratio > 0:
            reasons.append(f"volume >= {self.config.breakout_volume_ratio:g}x average")
        return reasons

    def _exit_signal(
        self,
        close: float,
        snapshot: dict[str, float | None],
    ) -> tuple[str, list[str]] | None:
        risk_signal = self._risk_exit_signal(close)
        if risk_signal is not None:
            return risk_signal
        if self.bars_since_entry < self.config.min_hold_bars:
            return None
        if self.family_name == "trend_momentum":
            ma_slow = snapshot.get("MA_SLOW")
            if ma_slow is not None and close < ma_slow:
                return "technical_exit", [f"close < MA{self.config.ma_slow_period}"]
        elif self.family_name == "breakout":
            breakout_low = snapshot.get("BREAKOUT_LOW")
            if breakout_low is not None and close < breakout_low:
                return "technical_exit", [
                    f"close < prior {self.config.breakout_exit_period}-bar low"
                ]
        else:
            _, bearish = self._vote_components(close, snapshot)
            if len(bearish) >= self.config.vote_exit_required:
                return "technical_exit", [
                    f"bearish vote {len(bearish)}/3 ({', '.join(bearish)})"
                ]
        return None

    def process_completed_kbar(
        self,
        code: str,
        decision_time: datetime,
        completed_kbar: dict[str, Any],
    ) -> None:
        completed_timestamp = completed_kbar.get("kbar_timestamp")
        if (
            self.evaluation_start_timestamp is not None
            and completed_timestamp is not None
            and int(completed_timestamp) < self.evaluation_start_timestamp
        ):
            close = number_or_none(completed_kbar.get("Close"))
            warmup_snapshot = bt.indicator_snapshot(completed_kbar)
            if close is not None and self._has_required_indicators(warmup_snapshot):
                self.previous_snapshot = warmup_snapshot
            return
        signal_count = len(self.local_signals)
        super().process_completed_kbar(code, decision_time, completed_kbar)
        for signal in self.local_signals[signal_count:]:
            if signal.get("action") != "Buy" or signal.get("signal_type") != "entry":
                continue
            aware_decision = (
                decision_time.replace(tzinfo=TAIPEI)
                if decision_time.tzinfo is None
                else decision_time.astimezone(TAIPEI)
            )
            bar_end = aware_decision - timedelta(microseconds=1)
            close = number_or_none(completed_kbar.get("Close"))
            row = {
                "technical_version": self.study_config["technical_version"],
                "strategy_family": self.family_name,
                "candidate_id": self.candidate["id"],
                "parameter_fingerprint": self.parameter_fingerprint,
                "stock_code": code,
                "data_role": self.data_role,
                "fold_id": self.fold_id,
                "timezone": "Asia/Taipei",
                "signal_time": bar_end.isoformat(timespec="microseconds"),
                "signal_bar_end_time": bar_end.isoformat(timespec="microseconds"),
                "earliest_entry_time": aware_decision.isoformat(timespec="microseconds"),
                "technical_signal": "BUY_CANDIDATE",
                "entry_rule_id": self.family["entry_rule_id"],
                "exit_rule_id": self.family["exit_rule_id"],
                "close_at_signal": close,
                "reason": signal.get("reason", ""),
            }
            validate_candidate_signal_row(row)
            self.candidate_signals.append(row)


def build_strategy(
    config: dict[str, Any],
    candidate_id: str,
    stock_code: str,
    data_role: str,
    fold_id: str,
    period_start: str,
    period_end: str,
) -> TechnicalV1Strategy:
    assert_execution_ready(config, data_role)
    family_name, family, candidate = candidate_by_id(config, candidate_id)
    if stock_code not in config["stock_universe"]["stock_codes"]:
        raise TechnicalV1Error(f"{stock_code} 不在已確認的股票集合")
    app = build_app_config(config, candidate, stock_code, period_start, period_end)
    strategy = TechnicalV1Strategy(
        app,
        study_config=config,
        family_name=family_name,
        family=family,
        candidate=candidate,
        data_role=data_role,
        fold_id=fold_id,
        is_backtest=True,
        is_simulation=True,
        local_only=True,
    )
    strategy.configure_local_account(app)
    return strategy


def validate_candidate_signal_row(row: dict[str, Any]) -> None:
    missing = [field for field in CANDIDATE_SIGNAL_FIELDS if field not in row]
    if missing:
        raise TechnicalV1Error("Candidate Signal 缺少欄位：" + ", ".join(missing))
    leaked = FORBIDDEN_REALTIME_FIELDS.intersection(row)
    if leaked:
        raise TechnicalV1Error("Candidate Signal 不得包含未來資訊：" + ", ".join(sorted(leaked)))
    unexpected = set(row) - set(CANDIDATE_SIGNAL_FIELDS)
    if unexpected:
        raise TechnicalV1Error("Candidate Signal 含未登記欄位：" + ", ".join(sorted(unexpected)))
    if row["technical_signal"] != "BUY_CANDIDATE":
        raise TechnicalV1Error("technical_signal 必須是 BUY_CANDIDATE")
    signal_time = datetime.fromisoformat(str(row["signal_time"]))
    bar_end = datetime.fromisoformat(str(row["signal_bar_end_time"]))
    earliest_entry = datetime.fromisoformat(str(row["earliest_entry_time"]))
    if row["timezone"] != "Asia/Taipei":
        raise TechnicalV1Error("Candidate Signal 時區必須明確標示 Asia/Taipei")
    expected_offset = timedelta(hours=8)
    if any(value.utcoffset() != expected_offset for value in (signal_time, bar_end, earliest_entry)):
        raise TechnicalV1Error("Candidate Signal 時間必須包含 Asia/Taipei 的 +08:00 offset")
    if signal_time != bar_end:
        raise TechnicalV1Error("signal_time 必須對應已完成 K 棒結束時間")
    if earliest_entry <= signal_time:
        raise TechnicalV1Error("earliest_entry_time 必須晚於訊號確認時間")
    if number_or_none(row["close_at_signal"]) is None:
        raise TechnicalV1Error("close_at_signal 必須是有限數值")


def export_candidate_signals(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    materialized = list(rows)
    for row in materialized:
        validate_candidate_signal_row(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_SIGNAL_FIELDS)
        writer.writeheader()
        writer.writerows(
            {field: row.get(field) for field in CANDIDATE_SIGNAL_FIELDS}
            for row in materialized
        )
    return path


def config_summary(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": config["experiment_id"],
        "technical_version": config["technical_version"],
        "status": config["status"],
        "stock_universe_status": config["stock_universe"]["status"],
        "stock_codes": config["stock_universe"]["stock_codes"],
        "families": {
            LEGACY_FAMILY_NAME: [config["legacy_baseline"]["candidate"]["id"]],
            **{
            family_name: [candidate["id"] for candidate in family["candidates"]]
            for family_name, family in config["strategy_families"].items()
            },
        },
        "walk_forward_folds": [fold["id"] for fold in config["walk_forward_folds"]],
        "forbidden_roles_used": False,
        "execution_ready": config["stock_universe"]["status"] == "confirmed",
    }


def resolve_market_data_path(
    project_root: Path,
    config: dict[str, Any],
    override: str | Path | None = None,
) -> Path:
    configured = Path(override) if override is not None else Path(config["market_data_path"])
    return configured.resolve() if configured.is_absolute() else (project_root / configured).resolve()


def initialize_market_schema(database_path: Path) -> None:
    """只對已存在的 SQLite 檔案做冪等 schema 初始化；絕不建立缺少的資料庫。"""

    if not database_path.is_file():
        raise TechnicalV1Error(f"資料庫不存在，禁止自動建立：{database_path}")
    with contextlib.closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(
            """
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
            """
        )
        connection.commit()


def _timestamp_bounds(start: str, end: str) -> tuple[int, int]:
    assert_development_period(start, end)
    first = datetime.combine(_parse_date(start, "start"), datetime.min.time(), tzinfo=TAIPEI)
    last = datetime.combine(
        _parse_date(end, "end") + timedelta(days=1),
        datetime.min.time(),
        tzinfo=TAIPEI,
    )
    return int(first.timestamp()), int(last.timestamp())


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _import_command(project_root: Path, database_path: Path) -> str:
    script = project_root / "scripts" / "import_technical_v1_training_kbars.py"
    return f'uv run python "{script}" --database "{database_path}"'


def _blocked_preflight(
    database_path: Path,
    project_root: Path,
    reason: str,
    *,
    schema_columns: Iterable[str] = (),
    schema_initialized: bool = False,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED_MISSING_DATA",
        "complete": False,
        "database_path": str(database_path),
        "reason": reason,
        "schema_columns": list(schema_columns),
        "schema_initialized": schema_initialized,
        "query_role": "training",
        "query_period": {"start": PERIOD_POLICY["training"][0], "end": PERIOD_POLICY["training"][1]},
        "forbidden_roles_queried": [],
        "stock_checks": [],
        "import_command": _import_command(project_root, database_path),
    }


def inspect_training_market_data(
    project_root: Path,
    config: dict[str, Any],
    market_data_path: str | Path | None = None,
    *,
    initialize_schema: bool = False,
) -> dict[str, Any]:
    """只檢查 2023–2024 Training；不以下載或 COUNT(*) 取代完整性檢查。"""

    assert_execution_ready(config, "training")
    database_path = resolve_market_data_path(project_root, config, market_data_path)
    if not database_path.is_file():
        return _blocked_preflight(
            database_path,
            project_root,
            "找不到 Technical V1 行情資料庫；未建立假資料、未下載行情、未執行回測。",
        )

    schema_initialized = False
    try:
        with contextlib.closing(_readonly_connection(database_path)) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_kbars'"
            ).fetchone() is not None
    except sqlite3.Error as exc:
        return _blocked_preflight(
            database_path,
            project_root,
            f"無法唯讀開啟行情資料庫：{exc}",
        )
    if not table_exists and initialize_schema:
        initialize_market_schema(database_path)
        schema_initialized = True
        table_exists = True
    if not table_exists:
        return _blocked_preflight(
            database_path,
            project_root,
            "既有 SQLite 缺少 market_kbars；可用 --initialize-schema 做冪等初始化，之後仍須另行匯入 Training 行情。",
        )

    training_start, training_end = PERIOD_POLICY["training"]
    lower, upper = _timestamp_bounds(training_start, training_end)
    stocks = [str(code) for code in config["stock_universe"]["stock_codes"]]
    placeholders = ",".join("?" for _ in stocks)
    try:
        with contextlib.closing(_readonly_connection(database_path)) as connection:
            schema_columns = [
                str(row[1]) for row in connection.execute("PRAGMA table_info(market_kbars)")
            ]
            missing_columns = sorted(REQUIRED_MARKET_COLUMNS - set(schema_columns))
            if missing_columns:
                return _blocked_preflight(
                    database_path,
                    project_root,
                    "market_kbars 缺少必要欄位：" + ", ".join(missing_columns),
                    schema_columns=schema_columns,
                    schema_initialized=schema_initialized,
                )
            query = f"""
                SELECT stock_code, freq_minutes, kbar_timestamp, open, high, low, close, volume
                FROM market_kbars
                WHERE stock_code IN ({placeholders})
                  AND kbar_timestamp >= ? AND kbar_timestamp < ?
                ORDER BY stock_code, kbar_timestamp
            """
            all_rows = connection.execute(query, (*stocks, lower, upper)).fetchall()
    except sqlite3.Error as exc:
        return _blocked_preflight(
            database_path,
            project_root,
            f"Training 行情完整性檢查失敗：{exc}",
            schema_initialized=schema_initialized,
        )

    rows_by_stock: dict[str, list[sqlite3.Row]] = {stock: [] for stock in stocks}
    non_five_minute = 0
    for row in all_rows:
        if int(row["freq_minutes"]) != int(config["common_backtest"]["kbar_freq"]):
            non_five_minute += 1
            continue
        rows_by_stock[str(row["stock_code"])].append(row)

    quality = config["data_quality"]
    expected_count = int(quality["expected_bars_per_trading_day"])
    expected_first = str(quality["expected_first_bar_time"])
    expected_last = str(quality["expected_last_bar_time"])
    expected_cadence = int(quality["expected_cadence_seconds"])
    minimum_warmup = int(quality["minimum_warmup_bars"])
    minimum_evaluation = int(quality["minimum_evaluation_bars"])
    minimum_training_days = int(quality["minimum_training_trading_days"])
    minimum_evaluation_days = int(quality["minimum_evaluation_trading_days"])

    stock_days: dict[str, set[str]] = {}
    normalized: dict[str, dict[int, sqlite3.Row]] = {}
    duplicate_counts: dict[str, int] = {}
    for stock, rows in rows_by_stock.items():
        unique: dict[int, sqlite3.Row] = {}
        duplicates = 0
        for row in rows:
            stamp = int(row["kbar_timestamp"])
            if stamp in unique:
                duplicates += 1
            else:
                unique[stamp] = row
        normalized[stock] = unique
        duplicate_counts[stock] = duplicates
        stock_days[stock] = {
            datetime.fromtimestamp(stamp, TAIPEI).date().isoformat() for stamp in unique
        }
    calendar_days = set().union(*stock_days.values()) if stock_days else set()

    checks: list[dict[str, Any]] = []
    all_problems: list[str] = []
    for stock in stocks:
        unique = normalized[stock]
        timestamps = sorted(unique)
        by_day: dict[str, list[int]] = defaultdict(list)
        invalid_ohlcv = 0
        for stamp in timestamps:
            row = unique[stamp]
            current = datetime.fromtimestamp(stamp, TAIPEI)
            by_day[current.date().isoformat()].append(stamp)
            values = [float(row[key]) for key in ("open", "high", "low", "close")]
            volume = float(row["volume"])
            open_, high, low, close = values
            if (
                not all(math.isfinite(value) and value > 0 for value in values)
                or not math.isfinite(volume)
                or volume < 0
                or high < max(open_, low, close)
                or low > min(open_, high, close)
            ):
                invalid_ohlcv += 1
        incomplete_days: list[dict[str, Any]] = []
        cadence_errors = 0
        for day, day_stamps in sorted(by_day.items()):
            local_times = [datetime.fromtimestamp(stamp, TAIPEI) for stamp in day_stamps]
            bad_deltas = sum(
                1
                for previous, current in zip(day_stamps, day_stamps[1:])
                if current - previous != expected_cadence
            )
            cadence_errors += bad_deltas
            first_time = local_times[0].strftime("%H:%M")
            last_time = local_times[-1].strftime("%H:%M")
            if len(day_stamps) != expected_count or first_time != expected_first or last_time != expected_last or bad_deltas:
                incomplete_days.append(
                    {
                        "date": day,
                        "bar_count": len(day_stamps),
                        "first_bar_time": first_time,
                        "last_bar_time": last_time,
                        "cadence_errors": bad_deltas,
                    }
                )
        missing_days = sorted(calendar_days - stock_days[stock])
        fold_checks = []
        for fold in config["walk_forward_folds"]:
            load_lower, _ = _timestamp_bounds(fold["train_start"], fold["train_end"])
            evaluate_lower, evaluate_upper = _timestamp_bounds(
                fold["evaluate_start"], fold["evaluate_end"]
            )
            warmup_count = sum(load_lower <= stamp < evaluate_lower for stamp in timestamps)
            evaluation_count = sum(evaluate_lower <= stamp < evaluate_upper for stamp in timestamps)
            evaluation_days = {
                datetime.fromtimestamp(stamp, TAIPEI).date().isoformat()
                for stamp in timestamps
                if evaluate_lower <= stamp < evaluate_upper
            }
            fold_problems = []
            if warmup_count < minimum_warmup:
                fold_problems.append(f"暖機 K 棒 {warmup_count} < {minimum_warmup}")
            if evaluation_count < minimum_evaluation:
                fold_problems.append(f"評估 K 棒 {evaluation_count} < {minimum_evaluation}")
            if len(evaluation_days) < minimum_evaluation_days:
                fold_problems.append(
                    f"評估交易日 {len(evaluation_days)} < {minimum_evaluation_days}"
                )
            fold_checks.append(
                {
                    "fold_id": fold["id"],
                    "load_start": fold["train_start"],
                    "evaluate_start": fold["evaluate_start"],
                    "evaluate_end": fold["evaluate_end"],
                    "warmup_bar_count": warmup_count,
                    "evaluation_bar_count": evaluation_count,
                    "evaluation_trading_day_count": len(evaluation_days),
                    "complete": not fold_problems,
                    "problems": fold_problems,
                }
            )
        problems = []
        if not timestamps:
            problems.append("Training 期間沒有 5 分 K")
        if len(by_day) < minimum_training_days:
            problems.append(f"Training 交易日 {len(by_day)} < {minimum_training_days}")
        if duplicate_counts[stock]:
            problems.append(f"重複 timestamp {duplicate_counts[stock]} 筆")
        if missing_days:
            problems.append(f"相對共同交易日曆缺少 {len(missing_days)} 日")
        if incomplete_days:
            problems.append(f"每日根數／首末棒／5 分鐘間隔異常 {len(incomplete_days)} 日")
        if invalid_ohlcv:
            problems.append(f"OHLCV 不合理 {invalid_ohlcv} 筆")
        for fold_check in fold_checks:
            problems.extend(f"{fold_check['fold_id']} {item}" for item in fold_check["problems"])
        check = {
            "stock_code": stock,
            "bar_count": len(timestamps),
            "actual_first_timestamp": (
                datetime.fromtimestamp(timestamps[0], TAIPEI).isoformat() if timestamps else None
            ),
            "actual_last_timestamp": (
                datetime.fromtimestamp(timestamps[-1], TAIPEI).isoformat() if timestamps else None
            ),
            "trading_day_count": len(by_day),
            "duplicate_timestamp_count": duplicate_counts[stock],
            "missing_trading_days": missing_days,
            "incomplete_trading_days": incomplete_days,
            "cadence_error_count": cadence_errors,
            "invalid_ohlcv_count": invalid_ohlcv,
            "fold_checks": fold_checks,
            "complete": not problems,
            "problems": problems,
        }
        checks.append(check)
        all_problems.extend(f"{stock}: {problem}" for problem in problems)

    complete = bool(checks) and all(check["complete"] for check in checks)
    return {
        "status": "READY" if complete else "BLOCKED_MISSING_DATA",
        "complete": complete,
        "database_path": str(database_path),
        "reason": "" if complete else "；".join(all_problems),
        "schema_columns": schema_columns,
        "schema_initialized": schema_initialized,
        "query_role": "training",
        "query_period": {"start": training_start, "end": training_end},
        "query_timestamp_bounds": {"lower_inclusive": lower, "upper_exclusive": upper},
        "forbidden_roles_queried": [],
        "non_five_minute_rows_ignored": non_five_minute,
        "calendar_basis": quality["missing_day_calendar_basis"],
        "calendar_day_count": len(calendar_days),
        "stock_checks": checks,
        "import_command": _import_command(project_root, database_path),
    }


def _load_fold_frame(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    candidate: dict[str, Any],
    stock_code: str,
    fold: dict[str, str],
) -> tuple[pl.DataFrame, pl.DataFrame, AppConfig, int, int]:
    load_start = fold["train_start"]
    evaluate_start = fold["evaluate_start"]
    evaluate_end = fold["evaluate_end"]
    assert_development_period(load_start, evaluate_end)
    lower, upper = _timestamp_bounds(load_start, evaluate_end)
    evaluate_lower, evaluate_upper = _timestamp_bounds(evaluate_start, evaluate_end)
    app = build_app_config(config, candidate, stock_code, evaluate_start, evaluate_end)
    rows = connection.execute(
        """
        SELECT kbar_timestamp, open, high, low, close, volume
        FROM market_kbars
        WHERE stock_code=? AND freq_minutes=?
          AND kbar_timestamp>=? AND kbar_timestamp<?
        ORDER BY kbar_timestamp
        """,
        (stock_code, app.kbar_freq, lower, upper),
    ).fetchall()
    raw = []
    seen: set[int] = set()
    for row in rows:
        stamp = int(row["kbar_timestamp"])
        if stamp in seen:
            raise TechnicalV1Error(f"{stock_code}/{fold['id']} 有重複 K 棒 timestamp：{stamp}")
        seen.add(stamp)
        raw.append(
            {
                "kbar_timestamp": stamp,
                "Open": float(row["open"]),
                "High": float(row["high"]),
                "Low": float(row["low"]),
                "Close": float(row["close"]),
                "Volume": int(row["volume"]),
            }
        )
    if not raw:
        raise TechnicalV1Error(f"{stock_code}/{fold['id']} 沒有可回放的 Training 5 分 K")
    frame = pl.DataFrame(raw).with_columns(
        pl.col("kbar_timestamp")
        .map_elements(bt.taipei_datetime_from_timestamp, return_dtype=pl.Datetime)
        .alias("kbar_time")
    )
    calculated = bt.add_indicators(frame, app)
    evaluation = calculated.filter(
        (pl.col("kbar_timestamp") >= evaluate_lower)
        & (pl.col("kbar_timestamp") < evaluate_upper)
    )
    warmup_count = calculated.filter(pl.col("kbar_timestamp") < evaluate_lower).height
    if evaluation.is_empty():
        raise TechnicalV1Error(f"{stock_code}/{fold['id']} 評估期沒有 K 棒")
    return calculated, evaluation, app, warmup_count, evaluate_lower


def _forward_return_metrics(
    signals: list[dict[str, Any]],
    evaluation: pl.DataFrame,
    horizon_bars: int,
) -> dict[str, Any]:
    rows = evaluation.select("kbar_timestamp", "Close").to_dicts()
    positions = {int(row["kbar_timestamp"]): index for index, row in enumerate(rows)}
    returns = []
    for signal in signals:
        entry_time = datetime.fromisoformat(str(signal["earliest_entry_time"]))
        entry_stamp = int(entry_time.timestamp())
        start_index = positions.get(entry_stamp)
        if start_index is None:
            continue
        future_index = start_index + horizon_bars - 1
        if future_index >= len(rows):
            continue
        signal_close = float(signal["close_at_signal"])
        future_close = float(rows[future_index]["Close"])
        returns.append(future_close / signal_close - 1.0)
    return {
        "forward_return_horizon_bars": horizon_bars,
        "forward_return_sample_count": len(returns),
        "forward_return_after_signal_mean": statistics.fmean(returns) if returns else None,
        "forward_return_after_signal_median": statistics.median(returns) if returns else None,
    }


def _simulate_candidate(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    family_name: str,
    family: dict[str, Any],
    candidate: dict[str, Any],
    stock_code: str,
    fold: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    full_frame, evaluation, app, warmup_count, evaluate_lower = _load_fold_frame(
        connection, config, candidate, stock_code, fold
    )
    strategy = TechnicalV1Strategy(
        app,
        study_config=config,
        family_name=family_name,
        family=family,
        candidate=candidate,
        data_role="training",
        fold_id=fold["id"],
        is_backtest=True,
        is_simulation=True,
        local_only=True,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = evaluate_lower
    first_stamp = int(full_frame["kbar_timestamp"][0])
    last_stamp = int(full_frame["kbar_timestamp"][-1])
    stats = bt.MarketDataStats(
        tick_count=full_frame.height,
        first_timestamp=float(first_stamp),
        last_timestamp=float(last_stamp),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full_frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    pnl_rows = bt._build_pnl_rows(trades)
    bar_rows = bt.build_kbar_rows_from_frame(evaluation, app)
    trading_days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(
        pnl_rows, app.initial_capital, trading_days=trading_days
    )
    metrics.update(bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital))
    if strategy.local_position != 0:
        raise TechnicalV1Error(f"{candidate['id']}/{stock_code}/{fold['id']} 期末持倉未結清")
    expected_cash = float(app.initial_capital) + float(metrics["net_pnl"])
    if strategy.local_cash is None or abs(float(strategy.local_cash) - expected_cash) > 0.02:
        raise TechnicalV1Error(
            f"{candidate['id']}/{stock_code}/{fold['id']} 帳戶現金與逐筆損益不一致"
        )
    forward = _forward_return_metrics(
        strategy.candidate_signals,
        evaluation,
        int(config["evaluation"]["forward_return_horizon_bars"]),
    )
    gross_profit = float(metrics["gross_profit"])
    completed_trades = int(metrics["completed_trades"])
    result = {
        "experiment_id": config["experiment_id"],
        "technical_version": config["technical_version"],
        "data_role": "training",
        "strategy_family": family_name,
        "strategy_family_display": family.get("display_name", family_name),
        "candidate_id": candidate["id"],
        "candidate_kind": candidate["kind"],
        "parameter_fingerprint": candidate_fingerprint(config, candidate),
        "stock_code": stock_code,
        "fold_id": fold["id"],
        "status": "COMPLETED",
        "load_start": fold["train_start"],
        "evaluate_start": fold["evaluate_start"],
        "evaluate_end": fold["evaluate_end"],
        "warmup_bar_count": warmup_count,
        "evaluation_bar_count": evaluation.height,
        "total_return": float(metrics["total_return"]),
        "net_pnl": float(metrics["net_pnl"]),
        "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "trade_count": completed_trades,
        "average_profit_per_trade": (
            float(metrics["net_pnl"]) / completed_trades if completed_trades else 0.0
        ),
        "gross_profit": gross_profit,
        "transaction_cost": float(metrics["transaction_cost"]),
        "cost_to_gross_profit": (
            float(metrics["transaction_cost"]) / gross_profit if gross_profit > 0 else None
        ),
        "signal_count": len(strategy.candidate_signals),
        **forward,
    }
    return result, strategy.candidate_signals


def execute_training_backtests(
    database_path: Path,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert_execution_ready(config, "training")
    results: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    with contextlib.closing(_readonly_connection(database_path)) as connection:
        for family_name, family, candidate in iter_execution_candidates(config):
            for fold in config["walk_forward_folds"]:
                for stock in config["stock_universe"]["stock_codes"]:
                    result, candidate_signals = _simulate_candidate(
                        connection,
                        config,
                        family_name,
                        family,
                        candidate,
                        str(stock),
                        fold,
                    )
                    results.append(result)
                    signals.extend(candidate_signals)
    candidate_ids = [candidate["id"] for _, _, candidate in iter_execution_candidates(config)]
    fold_ids = [fold["id"] for fold in config["walk_forward_folds"]]
    assert_complete_execution_matrix(
        results,
        candidate_ids,
        fold_ids,
        config["stock_universe"]["stock_codes"],
    )
    return results, signals


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return statistics.fmean(materialized) if materialized else 0.0


def _standard_deviation(values: Iterable[float]) -> float:
    materialized = list(values)
    return statistics.pstdev(materialized) if len(materialized) >= 2 else 0.0


def aggregate_training_results(
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[str(row["candidate_id"])].append(row)
    selection = config["selection"]
    aggregates: list[dict[str, Any]] = []
    for candidate_id, rows in grouped.items():
        returns = [float(row["total_return"]) for row in rows]
        sharpes = [float(row["sharpe_ratio"]) for row in rows]
        drawdowns = [float(row["max_drawdown"]) for row in rows]
        by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_fold: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_stock[str(row["stock_code"])].append(row)
            by_fold[str(row["fold_id"])].append(row)
        stock_returns = {
            stock: _mean(float(row["total_return"]) for row in stock_rows)
            for stock, stock_rows in by_stock.items()
        }
        fold_returns = {
            fold: _mean(float(row["total_return"]) for row in fold_rows)
            for fold, fold_rows in by_fold.items()
        }
        stock_net = {
            stock: sum(float(row["net_pnl"]) for row in stock_rows)
            for stock, stock_rows in by_stock.items()
        }
        positive_stock_profit = {stock: max(value, 0.0) for stock, value in stock_net.items()}
        positive_total = sum(positive_stock_profit.values())
        concentration = (
            max(positive_stock_profit.values()) / positive_total if positive_total > 0 else None
        )
        trade_count_total = sum(int(row["trade_count"]) for row in rows)
        per_stock_trades = {
            stock: sum(int(row["trade_count"]) for row in stock_rows)
            for stock, stock_rows in by_stock.items()
        }
        warnings = []
        sparse_pairs = [
            f"{row['stock_code']}/{row['fold_id']}"
            for row in rows
            if int(row["trade_count"]) < int(selection["minimum_trades_per_stock_fold"])
        ]
        if sparse_pairs:
            warnings.append("個股/Fold 交易樣本不足：" + ", ".join(sparse_pairs))
        sparse_stocks = [
            stock
            for stock, count in per_stock_trades.items()
            if count < int(selection["minimum_training_trades_per_stock"])
        ]
        if sparse_stocks:
            warnings.append("個股 Training 總交易不足：" + ", ".join(sorted(sparse_stocks)))
        if trade_count_total < int(selection["minimum_total_trades"]):
            warnings.append(f"總交易 {trade_count_total} < {selection['minimum_total_trades']}")
        if concentration is not None and concentration > float(selection["maximum_single_stock_profit_concentration"]):
            warnings.append(f"單一股票正獲利集中度 {concentration:.2%} 過高")
        gross_profit = sum(float(row["gross_profit"]) for row in rows)
        transaction_cost = sum(float(row["transaction_cost"]) for row in rows)
        forward_values = [
            float(row["forward_return_after_signal_mean"])
            for row in rows
            if row["forward_return_after_signal_mean"] is not None
        ]
        aggregates.append(
            {
                "candidate_id": candidate_id,
                "strategy_family": rows[0]["strategy_family"],
                "strategy_family_display": rows[0]["strategy_family_display"],
                "candidate_kind": rows[0]["candidate_kind"],
                "parameter_fingerprint": rows[0]["parameter_fingerprint"],
                "result_count": len(rows),
                "trade_count_total": trade_count_total,
                "total_return_mean": _mean(returns),
                "total_return_median": statistics.median(returns),
                "total_return_std": _standard_deviation(returns),
                "total_return_worst": min(returns),
                "sharpe_mean": _mean(sharpes),
                "sharpe_median": statistics.median(sharpes),
                "sharpe_std": _standard_deviation(sharpes),
                "max_drawdown_worst": max(drawdowns),
                "cross_stock_return_median": statistics.median(stock_returns.values()),
                "worst_stock": min(stock_returns, key=stock_returns.get),
                "worst_stock_return": min(stock_returns.values()),
                "worst_fold": min(fold_returns, key=fold_returns.get),
                "worst_fold_return": min(fold_returns.values()),
                "walk_forward_return_mean": _mean(fold_returns.values()),
                "walk_forward_return_std": _standard_deviation(fold_returns.values()),
                "stock_return_means": stock_returns,
                "fold_return_means": fold_returns,
                "single_stock_profit_concentration": concentration,
                "transaction_cost": transaction_cost,
                "cost_to_gross_profit": transaction_cost / gross_profit if gross_profit > 0 else None,
                "forward_return_after_signal_mean": _mean(forward_values) if forward_values else None,
                "warnings": warnings,
                "sample_sufficient": not any("交易" in warning for warning in warnings),
                "neighbour_stable": True,
                "parameter_spike": False,
            }
        )

    aggregate_by_id = {row["candidate_id"]: row for row in aggregates}
    for family in config["strategy_families"].values():
        baseline = next(item for item in family["candidates"] if item["kind"] == "baseline")
        sensitivity = next(item for item in family["candidates"] if item["kind"] == "sensitivity")
        base_row = aggregate_by_id[baseline["id"]]
        sens_row = aggregate_by_id[sensitivity["id"]]
        return_gap = abs(base_row["total_return_mean"] - sens_row["total_return_mean"])
        sharpe_gap = abs(base_row["sharpe_mean"] - sens_row["sharpe_mean"])
        spike = (
            return_gap > float(selection["parameter_spike_return_gap"])
            or sharpe_gap > float(selection["parameter_spike_sharpe_gap"])
        )
        for row in (base_row, sens_row):
            row["neighbour_return_gap"] = return_gap
            row["neighbour_sharpe_gap"] = sharpe_gap
            row["neighbour_stable"] = not spike
            row["parameter_spike"] = spike
            if spike:
                row["warnings"].append("鄰近敏感度結果出現參數尖峰")
    legacy = aggregate_by_id[config["legacy_baseline"]["candidate"]["id"]]
    legacy["neighbour_return_gap"] = None
    legacy["neighbour_sharpe_gap"] = None
    return aggregates


def _selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    cost = row["cost_to_gross_profit"]
    return (
        bool(row["sample_sufficient"]),
        float(row["worst_fold_return"]),
        -float(row["walk_forward_return_std"]),
        float(row["cross_stock_return_median"]),
        -float(row["max_drawdown_worst"]),
        -float(cost) if cost is not None else float("-inf"),
        bool(row["neighbour_stable"]),
        float(row["total_return_mean"]),
    )


def select_training_candidate(
    aggregates: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    by_id = {row["candidate_id"]: row for row in aggregates}
    legacy_id = config["legacy_baseline"]["candidate"]["id"]
    legacy = by_id[legacy_id]
    alternatives = [
        row
        for row in aggregates
        if row["candidate_id"] != legacy_id
        and row["sample_sufficient"]
        and row["neighbour_stable"]
    ]
    alternatives.sort(key=_selection_key, reverse=True)
    for candidate in alternatives:
        candidate_cost = candidate["cost_to_gross_profit"]
        legacy_cost = legacy["cost_to_gross_profit"]
        cost_ok = (
            legacy_cost is None
            or (candidate_cost is not None and float(candidate_cost) <= float(legacy_cost))
        )
        stable_improvement = (
            candidate["worst_fold_return"] >= legacy["worst_fold_return"]
            and candidate["walk_forward_return_std"] <= legacy["walk_forward_return_std"]
            and candidate["cross_stock_return_median"] >= legacy["cross_stock_return_median"]
            and candidate["max_drawdown_worst"] <= legacy["max_drawdown_worst"]
            and cost_ok
        )
        if stable_improvement:
            return {
                "selected_candidate_id": candidate["candidate_id"],
                "decision": "NEW_CANDIDATE_STABLY_BEATS_LEGACY_ON_PREREGISTERED_PRIORITY",
                "legacy_candidate_id": legacy_id,
                "selection_order": config["selection"]["selection_order"],
            }
    return {
        "selected_candidate_id": legacy_id,
        "decision": "KEEP_LEGACY_NO_NEW_CANDIDATE_STABLY_BEATS_BASELINE",
        "legacy_candidate_id": legacy_id,
        "selection_order": config["selection"]["selection_order"],
    }


def _write_json_exclusive(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "candidate_id", "strategy_family", "strategy_family_display", "candidate_kind",
        "parameter_fingerprint", "result_count", "trade_count_total", "total_return_mean",
        "total_return_median", "total_return_std", "total_return_worst", "sharpe_mean",
        "sharpe_median", "sharpe_std", "max_drawdown_worst", "cross_stock_return_median",
        "worst_stock", "worst_stock_return", "worst_fold", "worst_fold_return",
        "walk_forward_return_mean", "walk_forward_return_std",
        "single_stock_profit_concentration", "transaction_cost", "cost_to_gross_profit",
        "forward_return_after_signal_mean", "sample_sufficient", "neighbour_stable",
        "parameter_spike", "warnings",
    )
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {field: row.get(field) for field in fields}
            output["warnings"] = " | ".join(row["warnings"])
            writer.writerow(output)


def render_training_report(
    config: dict[str, Any],
    aggregates: list[dict[str, Any]],
    selection: dict[str, Any],
    run_id: str,
) -> str:
    rows = []
    for row in sorted(aggregates, key=lambda item: item["candidate_id"]):
        warnings = "；".join(row["warnings"]) or "無"
        rows.append(
            "<tr>"
            f"<td>{html.escape(row['candidate_id'])}</td>"
            f"<td>{html.escape(row['strategy_family_display'])}</td>"
            f"<td>{row['trade_count_total']}</td>"
            f"<td>{row['total_return_median']:.2%}</td>"
            f"<td>{row['worst_fold_return']:.2%}</td>"
            f"<td>{row['walk_forward_return_std']:.2%}</td>"
            f"<td>{row['max_drawdown_worst']:.2%}</td>"
            f"<td>{html.escape(warnings)}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>Technical Strategy V1 Training 報告</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1180px;margin:32px auto;padding:0 20px;color:#182026}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #ccd3d8;padding:8px;text-align:left;vertical-align:top}}th{{background:#edf2f4}}code{{background:#eef1f3;padding:2px 4px}}.note{{border-left:4px solid #167d74;padding:8px 12px;background:#f2f8f7}}</style></head>
<body><h1>Technical Strategy V1：Training Walk-Forward</h1>
<p>Run ID：<code>{html.escape(run_id)}</code></p>
<p class="note">本報告只使用 2023–2024 Training。Validation、Additional Holdout、Development Seen 與 Final Out-of-Sample 均未查詢。五檔為跨半導體、其他電子、電腦及週邊設備的廣義科技股集合，所有股票共用參數、成本與風險控制。</p>
<p>選擇結果：<strong>{html.escape(selection['selected_candidate_id'])}</strong>（{html.escape(selection['decision'])}）</p>
<table><thead><tr><th>候選</th><th>策略</th><th>交易數</th><th>跨組合報酬中位數</th><th>最差 Fold</th><th>WF 標準差</th><th>最差最大回撤</th><th>警示</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>研究限制</h2><ul>
<li>Legacy Baseline 與新的 Majority Vote V1 分開；後者為「2-of-3 多數決 + 長期趨勢濾網」。</li>
<li>停損為五分 K 收盤價觸發，不是盤中真實停損單；新 V1 的 162 根上限等於 3 個台股交易時段（54 根／日）。</li>
<li>滑價固定為 0，結果可能低估成交摩擦；手續費、一般交易稅與僅限同日平倉的當沖稅已納入。</li>
<li>每日最多一次進場、冷卻 6 根、至少持有 2 根、12:30 後不新進場，期末強制平倉。</li>
<li>目前 ML Candidate 股票集合尚未與本研究集合凍結對齊；本輪未修改或重訓 ML，Hybrid 開始前必須先對齊 ML V1 或取得另行核准。</li>
</ul></body></html>"""


def _new_run_directory(output_root: Path) -> tuple[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(TAIPEI).strftime("%Y%m%dT%H%M%S%f")
    run_directory = output_root / f"technical_v1_{run_id}"
    run_directory.mkdir(exist_ok=False)
    return run_id, run_directory


def run_training_workflow(
    project_root: Path,
    config_path: Path,
    *,
    role: str = "training",
    market_data_path: str | Path | None = None,
    preflight_only: bool = False,
    initialize_schema: bool = False,
    output_root: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    config = read_config(config_path)
    assert_execution_ready(config, role)
    run_id, run_directory = _new_run_directory(output_root or project_root / "exports")
    audit: dict[str, Any] = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "run_id": run_id,
        "experiment_id": config["experiment_id"],
        "config_path": str(config_path.resolve()),
        "config_fingerprint": fingerprint(config),
        "requested_role": role,
        "queried_roles": ["training"],
        "forbidden_roles_queried": [],
        "locked_periods": {
            "validation": "NOT_QUERIED_THIS_ROUND",
            "additional_holdout": "LOCKED_NOT_QUERIED",
            "development_seen": "NOT_QUERIED_THIS_ROUND",
            "final_out_of_sample": "LOCKED_NOT_QUERIED",
        },
        "preflight_only": preflight_only,
        "freeze_manifest_created": False,
        "outputs": [],
    }
    try:
        preflight = inspect_training_market_data(
            project_root,
            config,
            market_data_path,
            initialize_schema=initialize_schema,
        )
        audit["market_data_preflight"] = preflight
        if not preflight["complete"]:
            audit.update(
                {
                    "status": "BLOCKED_MISSING_DATA",
                    "reason": preflight["reason"],
                    "conclusion": "資料缺少或不完整；只產生 audit，未執行回測、未產生績效檔、未凍結候選。",
                }
            )
        elif preflight_only:
            audit.update(
                {
                    "status": "PREFLIGHT_READY",
                    "reason": "Training 五檔資料通過完整性檢查；依 --preflight-only 未執行回測。",
                    "conclusion": "只完成 Training 資料前置檢查。",
                }
            )
        else:
            results, signals = execute_training_backtests(Path(preflight["database_path"]), config)
            aggregates = aggregate_training_results(results, config)
            selection = select_training_candidate(aggregates, config)
            payload = {
                "run_id": run_id,
                "experiment_id": config["experiment_id"],
                "technical_version": config["technical_version"],
                "data_role": "training",
                "walk_forward_folds": config["walk_forward_folds"],
                "detailed_results": results,
                "aggregates": aggregates,
                "selection": selection,
                "disclosures": {
                    "majority_vote": "2-of-3 多數決 + 長期趨勢濾網",
                    "stop_loss": "五分 K 收盤價觸發，不是盤中真實停損單",
                    "max_hold_bars": "162 根等於 3 個台股交易時段（54 根／日）",
                    "slippage": "固定為 0；可能低估成交摩擦",
                    "ml_universe": "尚未凍結對齊；本輪未修改或重訓 ML",
                },
            }
            results_path = run_directory / "technical_v1_results.json"
            summary_path = run_directory / "technical_v1_summary.csv"
            signals_path = run_directory / "technical_v1_signals.csv"
            report_path = run_directory / "technical_v1_report.html"
            _write_json_exclusive(results_path, payload)
            _write_summary_csv(summary_path, aggregates)
            export_candidate_signals(signals_path, signals)
            with report_path.open("x", encoding="utf-8") as stream:
                stream.write(render_training_report(config, aggregates, selection, run_id))
            audit.update(
                {
                    "status": "COMPLETED",
                    "reason": "",
                    "conclusion": selection["decision"],
                    "expected_result_count": len(list(iter_execution_candidates(config)))
                    * len(config["walk_forward_folds"])
                    * len(config["stock_universe"]["stock_codes"]),
                    "actual_result_count": len(results),
                    "candidate_signal_count": len(signals),
                    "selection": selection,
                    "outputs": [
                        str(results_path), str(summary_path), str(signals_path), str(report_path)
                    ],
                }
            )
    except Exception as exc:
        audit.update(
            {
                "status": "FAILED_EXECUTION",
                "reason": f"{type(exc).__name__}: {exc}",
                "conclusion": "執行失敗；未以部分結果選參、未建立 freeze manifest。",
            }
        )
    audit_path = run_directory / "technical_v1_audit.json"
    audit["outputs"].append(str(audit_path))
    _write_json_exclusive(audit_path, audit)
    return audit, run_directory
