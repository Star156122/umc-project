"""跨股票技術指標泛化實驗的資料治理、彙整與報告工具。"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from trading_system.research_guard import assert_development_period

TAIPEI = ZoneInfo("Asia/Taipei")
CANONICAL_STOCKS = ("2303", "2330", "2454", "2317", "2382")
CANONICAL_ROLES = {
    "training": ("2023-01-01", "2024-12-31"),
    "validation": ("2025-01-01", "2025-06-30"),
    "development_seen": ("2026-01-01", "2026-06-30"),
    "additional_holdout": ("2025-07-01", "2025-12-31"),
    "final_out_of_sample": ("2026-07-01", None),
}
RESULT_FIELDS = (
    "experiment_id", "period_role", "fold_id", "version", "candidate_id",
    "stock_code", "status", "parameter_fingerprint", "period_start", "period_end",
    "effective_evaluation_start", "initial_capital", "ending_capital", "total_return",
    "gross_pnl", "net_pnl", "gross_profit", "gross_loss", "total_fee", "total_tax",
    "transaction_cost", "cost_to_gross_profit", "sharpe_ratio", "profit_factor",
    "max_drawdown", "win_rate", "average_win", "average_loss", "payoff_ratio",
    "break_even_win_rate", "completed_trades", "buy_and_hold_return",
    "buy_and_hold_net_pnl", "excess_return",
)
REQUIRED_MARKET_COLUMNS = {
    "stock_code", "freq_minutes", "kbar_timestamp", "open", "high", "low",
    "close", "volume",
}


class GeneralizationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_config(data)
    return data


def validate_config(config: dict[str, Any]) -> None:
    stocks = tuple(str(code) for code in config.get("stock_codes", []))
    if stocks != CANONICAL_STOCKS:
        raise GeneralizationError(
            "廣義科技股必須固定為 " + ", ".join(CANONICAL_STOCKS)
        )
    periods = config.get("periods", {})
    for role, (start, end) in CANONICAL_ROLES.items():
        actual = periods.get(role, {})
        if actual.get("start") != start or actual.get("end") != end:
            raise GeneralizationError(f"{role} 日期與統一資料政策不一致")
        if role in {"additional_holdout", "final_out_of_sample"}:
            if actual.get("access") != "forbidden":
                raise GeneralizationError(f"{role} 必須保持 forbidden")
        else:
            assert_development_period(start, end)
    validate_walk_forward(config["walk_forward_folds"], periods["training"])
    common = config["common_backtest"]
    fixed = {
        "kbar_freq": 5,
        "initial_capital": 100000,
        "stock_fee_rate": 0.001425,
        "stock_tax_rate": 0.003,
    }
    for key, expected in fixed.items():
        if common.get(key) != expected:
            raise GeneralizationError(f"共同回測設定 {key} 必須是 {expected}")
    selection = config["selection"]
    for key in (
        "minimum_trades_per_stock_fold",
        "minimum_training_trades_per_stock",
        "minimum_total_trades",
    ):
        if int(selection.get(key, 0)) <= 0:
            raise GeneralizationError(f"selection.{key} 必須大於 0")
    concentration_limit = float(selection.get("maximum_single_stock_profit_concentration", 0))
    if not 0 < concentration_limit <= 1:
        raise GeneralizationError("獲利集中度門檻必須介於 0 與 1 之間")
    quality = config["data_quality"]
    if int(quality.get("minimum_evaluation_bars", 0)) < 1000:
        raise GeneralizationError("每個評估期間至少需要 1,000 根 K 棒")
    if int(quality.get("minimum_warmup_bars", 0)) < 400:
        raise GeneralizationError("指標暖機至少需要 400 根 K 棒")
    assert_development_period(quality["validation_warmup_start"], periods["validation"]["end"])
    if quality.get("development_seen_warmup_mode") != "period_prefix":
        raise GeneralizationError("Development Seen 必須使用期間內前綴暖機")
    if config["versions"]["D"].get("enabled"):
        raise GeneralizationError("D 尚未取得啟用證據，不得預先啟用")


def validate_walk_forward(
    folds: list[dict[str, str]],
    training: dict[str, Any],
) -> None:
    if not folds:
        raise GeneralizationError("Training 必須至少有一個 walk-forward fold")
    role_start = date.fromisoformat(training["start"])
    role_end = date.fromisoformat(training["end"])
    previous_eval_end: date | None = None
    previous_train_end: date | None = None
    seen_ids: set[str] = set()
    for fold in folds:
        fold_id = fold["id"]
        if fold_id in seen_ids:
            raise GeneralizationError(f"walk-forward fold id 重複：{fold_id}")
        seen_ids.add(fold_id)
        train_start = date.fromisoformat(fold["train_start"])
        train_end = date.fromisoformat(fold["train_end"])
        evaluate_start = date.fromisoformat(fold["evaluate_start"])
        evaluate_end = date.fromisoformat(fold["evaluate_end"])
        if not (role_start <= train_start <= train_end < evaluate_start <= evaluate_end <= role_end):
            raise GeneralizationError(f"{fold_id} 未保持 Training 時間順序")
        if previous_eval_end is not None and evaluate_start <= previous_eval_end:
            raise GeneralizationError(f"{fold_id} 的評估期間與前一 fold 重疊")
        if previous_train_end is not None and train_end <= previous_train_end:
            raise GeneralizationError(f"{fold_id} 必須採擴張式訓練視窗")
        previous_eval_end = evaluate_end
        previous_train_end = train_end


def candidates(config: dict[str, Any], versions: Iterable[str] = ("A", "B", "C")):
    for version in versions:
        for candidate in config["versions"][version]["candidates"]:
            yield version, candidate


def candidate_fingerprint(config: dict[str, Any], candidate: dict[str, Any]) -> str:
    return fingerprint(
        {
            "common_backtest": config["common_backtest"],
            "strategy": candidate["strategy"],
            "params": candidate["params"],
        }
    )


def parameter_fingerprints(config: dict[str, Any]) -> dict[str, str]:
    return {
        candidate["id"]: candidate_fingerprint(config, candidate)
        for _, candidate in candidates(config)
    }


def assert_shared_parameter_assignments(
    rows: Iterable[dict[str, Any]],
    expected_stocks: Iterable[str] = CANONICAL_STOCKS,
    expected_folds: Iterable[str] | None = None,
) -> None:
    expected = set(expected_stocks)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    candidate_folds: dict[str, set[str]] = defaultdict(set)
    candidate_fingerprints: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        candidate_id = str(row["candidate_id"])
        fold_id = str(row["fold_id"])
        grouped[(candidate_id, fold_id)].append(row)
        candidate_folds[candidate_id].add(fold_id)
        candidate_fingerprints[candidate_id].add(str(row["parameter_fingerprint"]))
    if not grouped:
        raise GeneralizationError("沒有參數指紋資料可驗證")
    required_folds = set(expected_folds or set().union(*candidate_folds.values()))
    for candidate_id, folds in candidate_folds.items():
        if folds != required_folds:
            raise GeneralizationError(
                f"{candidate_id} fold 不完整：實際 {sorted(folds)}，要求 {sorted(required_folds)}"
            )
        if len(candidate_fingerprints[candidate_id]) != 1:
            raise GeneralizationError(f"{candidate_id} 在不同股票或 fold 出現不同參數")
    for (candidate_id, fold_id), items in grouped.items():
        stocks = [str(row["stock_code"]) for row in items]
        if len(stocks) != len(set(stocks)):
            raise GeneralizationError(f"{candidate_id}/{fold_id} 有重複股票結果")
        if set(stocks) != expected:
            raise GeneralizationError(
                f"{candidate_id}/{fold_id} 股票不完整：實際 {sorted(stocks)}，要求 {sorted(expected)}"
            )


def _safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _safe_stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def aggregate_candidate(
    rows: list[dict[str, Any]],
    minimum_trades_per_stock_fold: int,
    minimum_training_trades_per_stock: int,
    minimum_total_trades: int,
    maximum_single_stock_profit_concentration: float = 0.60,
) -> dict[str, Any]:
    if not rows:
        raise GeneralizationError("無結果可彙整")
    stock_return_samples: dict[str, list[float]] = defaultdict(list)
    stock_sharpe_samples: dict[str, list[float]] = defaultdict(list)
    stock_pnl: dict[str, float] = defaultdict(float)
    stock_trades: dict[str, int] = defaultdict(int)
    fold_returns: dict[str, list[float]] = defaultdict(list)
    gross_profit = 0.0
    gross_loss = 0.0
    weighted_wins = 0.0
    total_trades = 0
    stock_fold_trades: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        stock = str(row["stock_code"])
        fold = str(row["fold_id"])
        trades = int(row["completed_trades"])
        value = float(row["total_return"])
        stock_return_samples[stock].append(value)
        stock_sharpe_samples[stock].append(float(row["sharpe_ratio"]))
        stock_pnl[stock] += float(row["net_pnl"])
        stock_trades[stock] += trades
        stock_fold_trades[(stock, fold)] += trades
        fold_returns[fold].append(value)
        gross_profit += float(row.get("gross_profit", 0.0))
        gross_loss += float(row.get("gross_loss", 0.0))
        weighted_wins += float(row["win_rate"]) * trades
        total_trades += trades
    stock_returns = {
        stock: _safe_mean(stock_return_samples.get(stock, []))
        for stock in CANONICAL_STOCKS
    }
    stock_sharpes = {
        stock: _safe_mean(stock_sharpe_samples.get(stock, []))
        for stock in CANONICAL_STOCKS
    }
    returns = list(stock_returns.values())
    sharpe = list(stock_sharpes.values())
    positive_pnl = {key: max(value, 0.0) for key, value in stock_pnl.items()}
    positive_total = sum(positive_pnl.values())
    concentration = max(positive_pnl.values(), default=0.0) / positive_total if positive_total else None
    folds = sorted(fold_returns)
    per_fold_warnings = [
        {
            "level": "stock_fold",
            "stock_code": stock,
            "fold_id": fold,
            "actual_trades": stock_fold_trades.get((stock, fold), 0),
            "required_trades": minimum_trades_per_stock_fold,
        }
        for fold in folds
        for stock in CANONICAL_STOCKS
        if stock_fold_trades.get((stock, fold), 0) < minimum_trades_per_stock_fold
    ]
    is_training = all(str(row.get("period_role")) == "training" for row in rows)
    stock_total_warnings = [
        {
            "level": "stock_training_total",
            "stock_code": stock,
            "fold_id": "ALL_TRAINING_FOLDS",
            "actual_trades": stock_trades.get(stock, 0),
            "required_trades": minimum_training_trades_per_stock,
        }
        for stock in CANONICAL_STOCKS
        if is_training and stock_trades.get(stock, 0) < minimum_training_trades_per_stock
    ]
    total_trade_warning = [] if total_trades >= minimum_total_trades else [
        {
            "level": "all_stocks_folds",
            "stock_code": "ALL",
            "fold_id": "ALL",
            "actual_trades": total_trades,
            "required_trades": minimum_total_trades,
        }
    ]
    trade_warnings = per_fold_warnings + stock_total_warnings + total_trade_warning
    concentration_exceeded = (
        concentration is not None
        and concentration > maximum_single_stock_profit_concentration
    )
    fold_medians = [_safe_median(values) for values in fold_returns.values()]
    return {
        "version": rows[0]["version"],
        "candidate_id": rows[0]["candidate_id"],
        "parameter_fingerprint": rows[0]["parameter_fingerprint"],
        "period_role": rows[0]["period_role"],
        "eligible": not trade_warnings and not concentration_exceeded,
        "trade_count_warnings": trade_warnings,
        "insufficient_stocks": sorted({
            item["stock_code"] for item in trade_warnings if item["stock_code"] != "ALL"
        }),
        "insufficient_folds": sorted({
            item["fold_id"] for item in per_fold_warnings
        }),
        "total_trades": total_trades,
        "net_pnl": sum(stock_pnl.values()),
        "total_return_mean": _safe_mean(returns),
        "total_return_median": _safe_median(returns),
        "total_return_stdev": _safe_stdev(returns),
        "total_return_worst": min(returns, default=0.0),
        "sharpe_median": _safe_median(sharpe),
        "sharpe_worst": min(sharpe, default=0.0),
        "profit_factor": gross_profit / abs(gross_loss) if gross_loss < 0 else 0.0,
        "max_drawdown_worst": max(float(row["max_drawdown"]) for row in rows),
        "win_rate": weighted_wins / total_trades if total_trades else 0.0,
        "fold_median_return_stdev": _safe_stdev(fold_medians),
        "single_stock_profit_concentration": concentration,
        "maximum_single_stock_profit_concentration": maximum_single_stock_profit_concentration,
        "profit_concentration_exceeded": concentration_exceeded,
        "return_median_excluding_2303": _safe_median([
            value for stock, value in stock_returns.items() if stock != "2303"
        ]),
        "stock_returns": dict(stock_returns),
        "stock_sharpes": dict(stock_sharpes),
        "stock_trades": dict(stock_trades),
    }


def aggregate_training(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["period_role"] == "training" and row["status"] == "COMPLETED":
            grouped[str(row["candidate_id"])].append(row)
    selection = config["selection"]
    return [
        aggregate_candidate(
            grouped[candidate_id],
            int(selection["minimum_trades_per_stock_fold"]),
            int(selection["minimum_training_trades_per_stock"]),
            int(selection["minimum_total_trades"]),
            float(selection["maximum_single_stock_profit_concentration"]),
        )
        for candidate_id in sorted(grouped)
    ]


def mark_parameter_spikes(
    aggregates: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    by_id = {row["candidate_id"]: row for row in aggregates}
    candidate_map = {
        candidate["id"]: candidate
        for _, candidate in candidates(config)
    }
    limits = config["selection"]
    for row in aggregates:
        neighbours = [
            by_id[item] for item in candidate_map[row["candidate_id"]].get("neighbours", [])
            if item in by_id and by_id[item]["eligible"]
        ]
        if not neighbours:
            row["possible_parameter_spike"] = False
            continue
        neighbour_sharpe = _safe_median([item["sharpe_median"] for item in neighbours])
        neighbour_return = _safe_median([item["total_return_median"] for item in neighbours])
        row["possible_parameter_spike"] = (
            row["sharpe_median"] - neighbour_sharpe
            > float(limits["parameter_spike_sharpe_gap"])
            and row["total_return_median"] - neighbour_return
            > float(limits["parameter_spike_return_gap"])
        )


def select_candidate(
    version: str,
    aggregates: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    mark_parameter_spikes(aggregates, config)
    complexity = {
        candidate["id"]: int(candidate.get("complexity", 99))
        for candidate_version, candidate in candidates(config)
        if candidate_version == version
    }
    pool = [
        row for row in aggregates
        if row["version"] == version
        and row["eligible"]
        and not row.get("possible_parameter_spike", False)
    ]
    if not pool:
        return None
    return sorted(
        pool,
        key=lambda row: (
            -row["sharpe_median"],
            -row["sharpe_worst"],
            -row["total_return_worst"],
            row["max_drawdown_worst"],
            row["total_return_stdev"],
            row["fold_median_return_stdev"],
            row["single_stock_profit_concentration"]
            if row["single_stock_profit_concentration"] is not None else 1.0,
            -row["total_trades"],
            complexity[row["candidate_id"]],
            row["candidate_id"],
        ),
    )[0]


def _timestamp_bounds(start: str, end: str) -> tuple[int, int]:
    first = datetime.combine(date.fromisoformat(start), time.min, TAIPEI)
    last = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, TAIPEI)
    return int(first.timestamp()), int(last.timestamp())


def planned_periods(config: dict[str, Any]) -> list[dict[str, str]]:
    periods = [
        {
            "period_role": "training",
            "fold_id": fold["id"],
            "period_start": fold["evaluate_start"],
            "period_end": fold["evaluate_end"],
            "warmup_start": fold["train_start"],
            "warmup_mode": "before_period",
        }
        for fold in config["walk_forward_folds"]
    ]
    periods.append({
        "period_role": "validation",
        "fold_id": "VALIDATION",
        "period_start": config["periods"]["validation"]["start"],
        "period_end": config["periods"]["validation"]["end"],
        "warmup_start": config["data_quality"]["validation_warmup_start"],
        "warmup_mode": "before_period",
    })
    periods.append({
        "period_role": "development_seen",
        "fold_id": "DEVELOPMENT_SEEN",
        "period_start": config["periods"]["development_seen"]["start"],
        "period_end": config["periods"]["development_seen"]["end"],
        "warmup_start": config["periods"]["development_seen"]["start"],
        "warmup_mode": "period_prefix",
    })
    return periods


def resolve_market_data_path(
    root: Path,
    config: dict[str, Any],
    override: str | Path | None = None,
) -> dict[str, Any]:
    configured = root / config["market_data_path"]
    if override is not None:
        selected = Path(override)
        if not selected.is_absolute():
            selected = root / selected
        return {
            "available": selected.is_file(),
            "path": str(selected),
            "selection": "explicit_override",
            "candidates": [str(selected)] if selected.is_file() else [],
            "failure_status": "BLOCKED_MISSING_DATA",
            "reason": "" if selected.is_file() else f"指定行情資料庫不存在：{selected}",
        }
    if configured.is_file():
        return {
            "available": True,
            "path": str(configured),
            "selection": "configured_path",
            "candidates": [str(configured)],
            "failure_status": None,
            "reason": "",
        }
    data_dir = root / "data"
    alternatives = sorted(data_dir.rglob("market_data*.sqlite3")) if data_dir.exists() else []
    if len(alternatives) == 1:
        return {
            "available": True,
            "path": str(alternatives[0]),
            "selection": "single_discovered_alternative",
            "candidates": [str(alternatives[0])],
            "failure_status": None,
            "reason": "",
        }
    if len(alternatives) > 1:
        names = ", ".join(path.name for path in alternatives)
        return {
            "available": False,
            "path": str(configured),
            "selection": "ambiguous",
            "candidates": [str(path) for path in alternatives],
            "failure_status": "BLOCKED_MISSING_DATA",
            "reason": f"找到多個行情資料庫，請用 --market-data-path 明確指定：{names}",
        }
    return {
        "available": False,
        "path": str(configured),
        "selection": "not_found",
        "candidates": [],
        "failure_status": "BLOCKED_MISSING_DATA",
        "reason": "data 下找不到 market_data*.sqlite3",
    }


def _count_bars(
    connection: sqlite3.Connection,
    stock: str,
    frequency: int,
    start: str,
    end: str,
) -> tuple[int, int | None, int | None]:
    lower, upper = _timestamp_bounds(start, end)
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT kbar_timestamp), MIN(kbar_timestamp), MAX(kbar_timestamp)
        FROM market_kbars
        WHERE stock_code=? AND freq_minutes=? AND kbar_timestamp>=? AND kbar_timestamp<?
        """,
        (stock, frequency, lower, upper),
    ).fetchone()
    return int(row[0] or 0), row[1], row[2]


def _count_warmup_bars(
    connection: sqlite3.Connection,
    stock: str,
    frequency: int,
    period: dict[str, str],
    minimum_warmup: int,
) -> int:
    if period["warmup_mode"] == "period_prefix":
        count, _, _ = _count_bars(
            connection, stock, frequency, period["period_start"], period["period_end"]
        )
        return min(count, minimum_warmup)
    lower, _ = _timestamp_bounds(period["warmup_start"], period["warmup_start"])
    upper, _ = _timestamp_bounds(period["period_start"], period["period_start"])
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT kbar_timestamp)
        FROM market_kbars
        WHERE stock_code=? AND freq_minutes=? AND kbar_timestamp>=? AND kbar_timestamp<?
        """,
        (stock, frequency, lower, upper),
    ).fetchone()
    return int(row[0] or 0)


def inspect_market_cache(
    root: Path,
    config: dict[str, Any],
    market_data_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved = resolve_market_data_path(root, config, market_data_path)
    checks: list[dict[str, Any]] = []
    if not resolved["available"]:
        return {**resolved, "checks": checks, "schema_columns": []}
    cache = Path(resolved["path"])
    try:
        connection = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {
            **resolved,
            "available": False,
            "failure_status": "FAILED_EXECUTION",
            "reason": f"無法唯讀開啟行情資料庫：{exc}",
            "checks": checks,
            "schema_columns": [],
        }
    schema_columns: list[str] = []
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_kbars'"
        ).fetchone()
        if table is None:
            return {
                **resolved,
                "available": False,
                "failure_status": "FAILED_EXECUTION",
                "reason": "行情資料庫缺少 market_kbars 資料表",
                "checks": checks,
                "schema_columns": schema_columns,
            }
        schema_columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(market_kbars)")]
        missing_columns = sorted(REQUIRED_MARKET_COLUMNS - set(schema_columns))
        if missing_columns:
            return {
                **resolved,
                "available": False,
                "failure_status": "FAILED_EXECUTION",
                "reason": "market_kbars 缺少欄位：" + ", ".join(missing_columns),
                "checks": checks,
                "schema_columns": schema_columns,
            }
        quality = config["data_quality"]
        minimum_evaluation = int(quality["minimum_evaluation_bars"])
        minimum_warmup = int(quality["minimum_warmup_bars"])
        frequency = int(config["common_backtest"]["kbar_freq"])
        for period in planned_periods(config):
            assert_development_period(period["warmup_start"], period["period_end"])
            for stock in config["stock_codes"]:
                raw_count, first_stamp, last_stamp = _count_bars(
                    connection, stock, frequency, period["period_start"], period["period_end"]
                )
                warmup_count = _count_warmup_bars(
                    connection, stock, frequency, period, minimum_warmup
                )
                evaluation_count = (
                    max(raw_count - minimum_warmup, 0)
                    if period["warmup_mode"] == "period_prefix"
                    else raw_count
                )
                problems = []
                if evaluation_count < minimum_evaluation:
                    problems.append(f"評估 K 棒 {evaluation_count} < {minimum_evaluation}")
                if warmup_count < minimum_warmup:
                    problems.append(f"暖機 K 棒 {warmup_count} < {minimum_warmup}")
                checks.append({
                    **period,
                    "stock_code": stock,
                    "raw_period_bar_count": raw_count,
                    "evaluation_bar_count": evaluation_count,
                    "minimum_evaluation_bars": minimum_evaluation,
                    "warmup_bar_count": warmup_count,
                    "minimum_warmup_bars": minimum_warmup,
                    "first_timestamp": first_stamp,
                    "last_timestamp": last_stamp,
                    "available": not problems,
                    "problems": problems,
                })
    except (sqlite3.Error, ValueError) as exc:
        return {
            **resolved,
            "available": False,
            "failure_status": "FAILED_EXECUTION",
            "reason": f"行情資料檢查失敗：{exc}",
            "checks": checks,
            "schema_columns": schema_columns,
        }
    finally:
        connection.close()
    failures = [row for row in checks if not row["available"]]
    reason = ""
    if failures:
        reason = "；".join(
            f"{row['stock_code']}/{row['fold_id']}：{', '.join(row['problems'])}"
            for row in failures
        )
    return {
        **resolved,
        "available": not failures,
        "failure_status": None if not failures else "FAILED_EXECUTION",
        "reason": reason,
        "checks": checks,
        "schema_columns": schema_columns,
    }


def planned_result_rows(config: dict[str, Any], status: str) -> list[dict[str, Any]]:
    rows = []
    fingerprints = parameter_fingerprints(config)
    for period in planned_periods(config):
        for version, candidate in candidates(config):
            for stock in config["stock_codes"]:
                row = {key: "" for key in RESULT_FIELDS}
                row.update({
                    "experiment_id": config["experiment_id"],
                    **period,
                    "version": version,
                    "candidate_id": candidate["id"],
                    "stock_code": stock,
                    "status": status,
                    "parameter_fingerprint": fingerprints[candidate["id"]],
                })
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in RESULT_FIELDS} for row in rows)


def _metric(value: Any, percent: bool = False) -> str:
    if value in (None, ""):
        return "—"
    return f"{float(value):.2%}" if percent else f"{float(value):.3f}"


def _aggregate_table(rows: list[dict[str, Any]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('version', '')))}</td>"
        f"<td>{html.escape(str(row.get('candidate_id', '')))}</td>"
        f"<td>{'是' if row.get('eligible') else '否'}</td>"
        f"<td>{_metric(row.get('total_return_median'), True)}</td>"
        f"<td>{_metric(row.get('return_median_excluding_2303'), True)}</td>"
        f"<td>{_metric(row.get('sharpe_median'))}</td>"
        f"<td>{_metric(row.get('sharpe_worst'))}</td>"
        f"<td>{_metric(row.get('total_return_worst'), True)}</td>"
        f"<td>{_metric(row.get('max_drawdown_worst'), True)}</td>"
        f"<td>{_metric(row.get('total_return_stdev'), True)}</td>"
        f"<td>{_metric(row.get('fold_median_return_stdev'), True)}</td>"
        f"<td>{row.get('total_trades', '—')}</td>"
        f"<td>{_metric(row.get('single_stock_profit_concentration'), True)}</td>"
        f"<td>{'是' if row.get('possible_parameter_spike') else '否'}</td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="14">尚無真實績效資料</td></tr>'
    return (
        "<table><thead><tr><th>版本</th><th>候選</th><th>合格</th>"
        "<th>報酬中位數</th><th>排除2303中位數</th><th>Sharpe中位數</th>"
        "<th>最差Sharpe</th><th>最差報酬</th><th>最差回撤</th><th>股票間標準差</th>"
        "<th>Fold穩定度</th><th>交易數</th><th>獲利集中</th><th>參數尖峰</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _detail_table(rows: list[dict[str, Any]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('version', '')))}</td>"
        f"<td>{html.escape(str(row.get('candidate_id', '')))}</td>"
        f"<td>{html.escape(str(row.get('fold_id', '')))}</td>"
        f"<td>{html.escape(str(row.get('stock_code', '')))}</td>"
        f"<td>{html.escape(str(row.get('status', '')))}</td>"
        f"<td>{_metric(row.get('total_return'), True)}</td>"
        f"<td>{_metric(row.get('excess_return'), True)}</td>"
        f"<td>{_metric(row.get('sharpe_ratio'))}</td>"
        f"<td>{_metric(row.get('profit_factor'))}</td>"
        f"<td>{_metric(row.get('max_drawdown'), True)}</td>"
        f"<td>{row.get('completed_trades') or '—'}</td>"
        f"<td><code>{html.escape(str(row.get('parameter_fingerprint', '')))}</code></td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="12">尚無結果列</td></tr>'
    return (
        "<table><thead><tr><th>版本</th><th>候選</th><th>Fold</th><th>股票</th>"
        "<th>狀態</th><th>總報酬</th><th>超額報酬</th><th>Sharpe</th><th>PF</th>"
        "<th>最大回撤</th><th>交易數</th><th>參數指紋</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_html(audit: dict[str, Any]) -> str:
    config = audit["config"]
    status = html.escape(audit["status"])
    reason = html.escape(audit.get("reason", ""))
    result_rows = audit.get("results") or audit.get("planned_results", [])
    period_sections = []
    for role in ("training", "validation", "development_seen"):
        period = config["periods"][role]
        aggregates = [row for row in audit.get("aggregates", []) if row.get("period_role") == role]
        details = [row for row in result_rows if row.get("period_role") == role]
        period_sections.append(
            f"<section><h2>{html.escape(period['report_block'])}</h2>"
            f"<p>{period['start']}～{period['end']}，各區塊獨立呈現。</p>"
            f"{_aggregate_table(aggregates)}<details><summary>每檔股票／Fold 明細</summary>"
            f"{_detail_table(details)}</details></section>"
        )
    definitions = "".join(
        f"<li><b>{version}</b>：{html.escape(str(item.get('label', '')))}；"
        f"{html.escape(str(item.get('description', item.get('decision', ''))))}</li>"
        for version, item in config["versions"].items()
    )
    warning_items = []
    for row in audit.get("aggregates", []):
        for warning in row.get("trade_count_warnings", []):
            warning_items.append(
                f"{row['candidate_id']}／{warning['stock_code']}／{warning['fold_id']}："
                f"交易 {warning['actual_trades']}，要求 {warning['required_trades']}"
            )
        if row.get("profit_concentration_exceeded"):
            warning_items.append(
                f"{row['candidate_id']}：單一股票正獲利占比 "
                f"{_metric(row.get('single_stock_profit_concentration'), True)} 超過門檻"
            )
        if row.get("possible_parameter_spike"):
            warning_items.append(f"{row['candidate_id']}：possible_parameter_spike")
    warning_html = "".join(f"<li>{html.escape(item)}</li>" for item in warning_items) or "<li>尚無真實績效可判斷</li>"
    checks = audit.get("market_data", {}).get("checks", [])
    check_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['period_role']))}</td><td>{html.escape(str(row['fold_id']))}</td>"
        f"<td>{html.escape(str(row['stock_code']))}</td><td>{row.get('evaluation_bar_count', '—')}</td>"
        f"<td>{row.get('warmup_bar_count', '—')}</td><td>{'通過' if row.get('available') else '不足'}</td>"
        f"<td>{html.escape(', '.join(row.get('problems', [])))}</td></tr>"
        for row in checks
    ) or '<tr><td colspan="7">未找到可檢查的本地行情資料庫</td></tr>'
    selection_html = html.escape(json.dumps(
        audit.get("final_common_parameters", {}), ensure_ascii=False, indent=2
    ))
    comparison_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('version', '')))}</td>"
        f"<td>{html.escape(str(row.get('candidate_id', '—')))}</td>"
        f"<td>{html.escape(str(row.get('status', '')))}</td>"
        f"<td>{html.escape(', '.join(row.get('improved_stocks', [])) or '無')}</td>"
        f"<td>{html.escape(', '.join(row.get('declined_stocks', [])) or '無')}</td>"
        "</tr>"
        for row in audit.get("validation_comparisons", [])
    ) or '<tr><td colspan="5">尚無真實 A／B／C 結果，不能判斷改善或退步</td></tr>'
    database_path = html.escape(str(audit.get("market_data", {}).get("path", "")))
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>技術指標跨科技股泛化稽核</title>
<style>
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:0;color:#17202a;background:#f4f6f7}}
main{{max-width:1440px;margin:auto;padding:28px}}h1{{font-size:28px}}h2{{font-size:20px;margin-top:28px}}
.status{{border-left:5px solid #b03a2e;background:#fff;padding:14px}}.locked{{background:#17202a;color:#fff;padding:14px;margin-top:18px}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}th,td{{padding:7px;border:1px solid #ccd1d1;text-align:right}}
th:first-child,td:first-child{{text-align:left}}code{{word-break:break-all}}details{{margin-top:12px}}pre{{white-space:pre-wrap;background:#fff;padding:12px}}
</style></head><body><main>
<h1>技術指標跨科技股泛化稽核</h1>
<div class="status"><b>狀態：{status}</b><br>{reason}</div>
<p>股票：{", ".join(config["stock_codes"])}。共同設定指紋：<code>{audit["config_fingerprint"]}</code></p>
<p>行情資料庫：<code>{database_path}</code></p>
<section><h2>A／B／C／D 定義</h2><ul>{definitions}</ul>
<p>D 觸發：{'是' if audit.get('atr_triggered') else '否'}。{html.escape(str(audit.get('atr_decision', '')))}</p></section>
{''.join(period_sections)}
<section><h2>最終共用參數</h2><pre>{selection_html}</pre></section>
<section><h2>Legacy 與 Generalized 比較</h2><table><thead><tr><th>版本</th><th>候選</th>
<th>判定</th><th>改善股票</th><th>退步股票</th></tr></thead><tbody>{comparison_rows}</tbody></table></section>
<section><h2>警告</h2><ul>{warning_html}</ul></section>
<section><h2>資料完整性</h2><table><thead><tr><th>角色</th><th>Fold</th><th>股票</th>
<th>評估K棒</th><th>暖機K棒</th><th>狀態</th><th>原因</th></tr></thead><tbody>{check_rows}</tbody></table></section>
<section class="locked"><h2>鎖定資料</h2>
<p>Additional Holdout：2025-07-01～2025-12-31，LOCKED／NOT USED。</p>
<p>Final Out-of-Sample：2026-07-01～最新資料，LOCKED／NOT USED。</p></section>
<section><h2>保守結論</h2><p>{html.escape(audit["conclusion"])}</p></section>
</main></body></html>"""


def write_outputs(
    root: Path,
    config: dict[str, Any],
    audit: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    output = root / config["output_directory"]
    paths = {
        key: output / filename
        for key, filename in config["outputs"].items()
    }
    write_csv(paths["csv"], rows)
    paths["audit_json"].write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    paths["html"].write_text(render_html(audit), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _candidate_map(config: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        candidate["id"]: (version, candidate)
        for version, candidate in candidates(config)
    }


def selected_parameter_payload(
    config: dict[str, Any],
    selections: dict[str, Any],
) -> dict[str, Any]:
    mapping = _candidate_map(config)
    payload = {}
    for version, selection in selections.items():
        if selection is None:
            payload[version] = None
            continue
        candidate_id = selection["candidate_id"]
        _, candidate = mapping[candidate_id]
        payload[version] = {
            "candidate_id": candidate_id,
            "strategy": candidate["strategy"],
            "parameter_fingerprint": candidate_fingerprint(config, candidate),
            "params": candidate["params"],
        }
    return payload


def _load_evaluation_frame(
    connection: sqlite3.Connection,
    stock_code: str,
    period_start: str,
    period_end: str,
    warmup_start: str,
    warmup_mode: str,
    minimum_warmup_bars: int,
    app_config: Any,
) -> tuple[Any, Any, str]:
    """只讀允許期間；先算暖機指標，再切出實際評估視窗。"""
    assert_development_period(warmup_start, period_end)
    from trading_system import backtest as bt
    import polars as pl

    lower, upper = _timestamp_bounds(warmup_start, period_end)
    rows = connection.execute(
        """
        SELECT kbar_timestamp, open, high, low, close, volume
        FROM market_kbars
        WHERE stock_code=? AND freq_minutes=? AND kbar_timestamp>=? AND kbar_timestamp<?
        ORDER BY kbar_timestamp
        """,
        (stock_code, app_config.kbar_freq, lower, upper),
    ).fetchall()
    unique: dict[int, tuple[float, float, float, float, int]] = {}
    for row in rows:
        stamp = int(row["kbar_timestamp"])
        values = (
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), int(row["volume"]),
        )
        if stamp in unique and unique[stamp] != values:
            raise GeneralizationError(f"{stock_code} 在 {stamp} 有互相衝突的重複 K 棒")
        unique[stamp] = values
    if not unique:
        raise GeneralizationError(
            f"{stock_code} 缺少 {warmup_start}～{period_end} 五分 K"
        )
    raw = [
        {
            "kbar_timestamp": stamp,
            "Open": values[0],
            "High": values[1],
            "Low": values[2],
            "Close": values[3],
            "Volume": values[4],
        }
        for stamp, values in sorted(unique.items())
    ]
    frame = pl.DataFrame(raw).with_columns(
        pl.col("kbar_timestamp")
        .map_elements(bt.taipei_datetime_from_timestamp, return_dtype=pl.Datetime)
        .alias("kbar_time")
    )
    calculated = bt.add_indicators(frame, app_config)
    eval_lower, eval_upper = _timestamp_bounds(period_start, period_end)
    period_frame = calculated.filter(
        (pl.col("kbar_timestamp") >= eval_lower)
        & (pl.col("kbar_timestamp") < eval_upper)
    )
    evaluated = (
        period_frame.slice(minimum_warmup_bars)
        if warmup_mode == "period_prefix"
        else period_frame
    )
    if evaluated.is_empty():
        raise GeneralizationError(
            f"{stock_code} 缺少 {period_start}～{period_end} 評估 K 棒"
        )
    first = int(evaluated["kbar_timestamp"][0])
    last = int(evaluated["kbar_timestamp"][-1])
    stats = bt.MarketDataStats(
        tick_count=evaluated.height,
        first_timestamp=float(first),
        last_timestamp=float(last),
    )
    effective_start = datetime.fromtimestamp(first, TAIPEI).date().isoformat()
    return evaluated, stats, effective_start


def _app_config(
    config: dict[str, Any],
    version: str,
    candidate: dict[str, Any],
    stock_code: str,
    period_start: str,
    period_end: str,
) -> Any:
    import dataclasses
    from trading_system import backtest as bt

    values = {
        **config["common_backtest"],
        **candidate["params"],
        "strategy": candidate["strategy"],
        "run_name": f"{config['experiment_id']}_{version}_{candidate['id']}_{stock_code}",
        "code": stock_code,
        "stock_codes": stock_code,
        "stock_name": config["stock_names"][stock_code],
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
    fields = {field.name for field in dataclasses.fields(bt.AppConfig)}
    result = bt.AppConfig(**{key: value for key, value in values.items() if key in fields})
    bt.validate_config(result)
    return result


def _simulate_candidate(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    version: str,
    candidate: dict[str, Any],
    stock_code: str,
    period_role: str,
    fold_id: str,
    period_start: str,
    period_end: str,
    warmup_start: str,
    warmup_mode: str,
) -> dict[str, Any]:
    import contextlib
    import io
    from trading_system import backtest as bt

    app = _app_config(
        config, version, candidate, stock_code, period_start, period_end
    )
    frame, stats, effective_start = _load_evaluation_frame(
        connection,
        stock_code,
        period_start,
        period_end,
        warmup_start,
        warmup_mode,
        int(config["data_quality"]["minimum_warmup_bars"]),
        app,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        bot = bt.build_tsst(app)
        bt.replay_kbars(bot, frame, stats)
    trades = bt._build_trade_rows(bot, app)
    pnl_rows = bt._build_pnl_rows(trades)
    bar_rows = bt.build_kbar_rows_from_frame(frame, app)
    trading_days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(
        pnl_rows, app.initial_capital, trading_days=trading_days
    )
    metrics.update(
        bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital)
    )
    benchmark = bt.calculate_buy_and_hold_benchmark(bar_rows, app)
    if bot.local_position != 0:
        raise GeneralizationError("期末持倉未結清")
    if abs(
        float(bot.local_cash)
        - (float(app.initial_capital) + float(metrics["net_pnl"]))
    ) > 0.02:
        raise GeneralizationError("帳戶現金與逐筆損益不一致")
    return {
        "experiment_id": config["experiment_id"],
        "period_role": period_role,
        "fold_id": fold_id,
        "version": version,
        "candidate_id": candidate["id"],
        "stock_code": stock_code,
        "status": "COMPLETED",
        "parameter_fingerprint": candidate_fingerprint(config, candidate),
        "period_start": period_start,
        "period_end": period_end,
        "effective_evaluation_start": effective_start,
        **{
            key: metrics[key]
            for key in (
                "initial_capital", "ending_capital", "total_return", "gross_pnl",
                "net_pnl", "gross_profit", "gross_loss", "total_fee", "total_tax",
                "transaction_cost", "sharpe_ratio", "profit_factor", "max_drawdown",
                "win_rate", "average_win", "average_loss", "payoff_ratio",
                "break_even_win_rate", "completed_trades",
            )
        },
        "cost_to_gross_profit": (
            float(metrics["transaction_cost"]) / float(metrics["gross_profit"])
            if float(metrics["gross_profit"]) > 0 else None
        ),
        "buy_and_hold_return": benchmark["total_return"],
        "buy_and_hold_net_pnl": benchmark["net_pnl"],
        "excess_return": float(metrics["total_return"]) - float(benchmark["total_return"]),
    }


def _run_period(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    period_role: str,
    fold_id: str,
    period_start: str,
    period_end: str,
    warmup_start: str,
    warmup_mode: str,
    candidate_ids: Iterable[str],
) -> list[dict[str, Any]]:
    mapping = _candidate_map(config)
    rows = []
    for candidate_id in candidate_ids:
        version, candidate = mapping[candidate_id]
        for stock in config["stock_codes"]:
            rows.append(
                _simulate_candidate(
                    connection,
                    config,
                    version,
                    candidate,
                    stock,
                    period_role,
                    fold_id,
                    period_start,
                    period_end,
                    warmup_start,
                    warmup_mode,
                )
            )
    return rows


def aggregate_period(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    period_role: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["period_role"] == period_role and row["status"] == "COMPLETED":
            grouped[str(row["candidate_id"])].append(row)
    selection = config["selection"]
    aggregates = []
    for candidate_id in sorted(grouped):
        result = aggregate_candidate(
            grouped[candidate_id],
            int(selection["minimum_trades_per_stock_fold"]),
            int(selection["minimum_training_trades_per_stock"]),
            int(selection["minimum_total_trades"]),
            float(selection["maximum_single_stock_profit_concentration"]),
        )
        aggregates.append(result)
    return aggregates


def _validation_comparison(
    rows: list[dict[str, Any]],
    aggregates: list[dict[str, Any]],
    selections: dict[str, Any],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    validation = {
        row["candidate_id"]: row
        for row in aggregates
        if row["period_role"] == "validation"
    }
    baseline_id = selections["A"]["candidate_id"]
    baseline = validation.get(baseline_id)
    if baseline is None:
        return [], "Validation 沒有 baseline 結果，不能判斷泛化能力。"
    detailed = [row for row in rows if row["period_role"] == "validation"]
    comparisons = []
    passed = []
    for version in ("B", "C"):
        selected = selections.get(version)
        if not selected:
            comparisons.append(
                {"version": version, "status": "INSUFFICIENT_TRAINING_SAMPLE"}
            )
            continue
        candidate_id = selected["candidate_id"]
        candidate = validation.get(candidate_id)
        if candidate is None:
            comparisons.append(
                {"version": version, "candidate_id": candidate_id, "status": "NO_VALIDATION_RESULT"}
            )
            continue
        base_by_stock = {
            row["stock_code"]: row for row in detailed
            if row["candidate_id"] == baseline_id
        }
        candidate_by_stock = {
            row["stock_code"]: row for row in detailed
            if row["candidate_id"] == candidate_id
        }
        changes = {
            stock: (
                float(candidate_by_stock[stock]["total_return"])
                - float(base_by_stock[stock]["total_return"])
            )
            for stock in CANONICAL_STOCKS
        }
        stable = (
            candidate["eligible"]
            and candidate["sharpe_median"] >= baseline["sharpe_median"]
            and candidate["total_return_worst"] >= baseline["total_return_worst"]
            and candidate["max_drawdown_worst"] <= baseline["max_drawdown_worst"]
            and (
                candidate["single_stock_profit_concentration"] is None
                or candidate["single_stock_profit_concentration"]
                <= float(config["selection"]["maximum_single_stock_profit_concentration"])
            )
        )
        result = {
            "version": version,
            "candidate_id": candidate_id,
            "status": "PASS" if stable else "FAIL",
            "improved_stocks": [stock for stock, value in changes.items() if value > 0],
            "declined_stocks": [stock for stock, value in changes.items() if value < 0],
            "return_delta_by_stock": changes,
        }
        comparisons.append(result)
        if stable:
            passed.append(version)
    if passed:
        conclusion = (
            "Validation 的預先定義穩定度門檻由 "
            + "、".join(passed)
            + " 通過；這是開發期 Validation，不是 Final Out-of-Sample 結論。"
        )
    else:
        conclusion = (
            "沒有新版本在 Validation 穩定通過預先定義門檻；保留 legacy baseline，"
            "不宣稱泛化能力已改善。"
        )
    return comparisons, conclusion


def execute_backtests(
    cache: Path,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], str]:
    connection = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = []
    try:
        training_ids = [candidate["id"] for _, candidate in candidates(config)]
        for fold in config["walk_forward_folds"]:
            rows.extend(
                _run_period(
                    connection,
                    config,
                    "training",
                    fold["id"],
                    fold["evaluate_start"],
                    fold["evaluate_end"],
                    fold["train_start"],
                    "before_period",
                    training_ids,
                )
            )
        assert_shared_parameter_assignments(
            rows,
            expected_folds=[fold["id"] for fold in config["walk_forward_folds"]],
        )
        training_aggregates = aggregate_training(rows, config)
        mark_parameter_spikes(training_aggregates, config)
        selections: dict[str, Any] = {}
        fixed_a = config["versions"]["A"]["candidates"][0]["id"]
        selections["A"] = next(
            row for row in training_aggregates if row["candidate_id"] == fixed_a
        )
        for version in ("B", "C"):
            selected = select_candidate(version, training_aggregates, config)
            selections[version] = selected
        frozen_ids = [
            value["candidate_id"] for value in selections.values() if value is not None
        ]
        validation = config["periods"]["validation"]
        validation_rows = _run_period(
                connection,
                config,
                "validation",
                "VALIDATION",
                validation["start"],
                validation["end"],
                config["data_quality"]["validation_warmup_start"],
                "before_period",
                frozen_ids,
        )
        assert_shared_parameter_assignments(validation_rows, expected_folds=["VALIDATION"])
        rows.extend(validation_rows)
        development = config["periods"]["development_seen"]
        development_rows = _run_period(
                connection,
                config,
                "development_seen",
                "DEVELOPMENT_SEEN",
                development["start"],
                development["end"],
                development["start"],
                "period_prefix",
                frozen_ids,
        )
        assert_shared_parameter_assignments(
            development_rows, expected_folds=["DEVELOPMENT_SEEN"]
        )
        rows.extend(development_rows)
    finally:
        connection.close()
    aggregates = training_aggregates
    aggregates.extend(aggregate_period(rows, config, "validation"))
    aggregates.extend(aggregate_period(rows, config, "development_seen"))
    comparisons, conclusion = _validation_comparison(
        rows, aggregates, selections, config
    )
    return rows, aggregates, selections, comparisons, conclusion


def run_workflow(
    root: Path,
    config_path: Path,
    market_data_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    config = read_config(config_path)
    market_data = inspect_market_cache(root, config, market_data_path)
    audit: dict[str, Any] = {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "experiment_id": config["experiment_id"],
        "config": config,
        "config_fingerprint": fingerprint(config),
        "parameter_fingerprints": parameter_fingerprints(config),
        "market_data": market_data,
        "locked_periods": {
            "additional_holdout": "LOCKED_NOT_USED",
            "final_out_of_sample": "LOCKED_NOT_USED",
        },
        "atr_decision": config["versions"]["D"]["decision"],
        "atr_triggered": False,
        "results": [],
        "aggregates": [],
        "selections": {},
        "final_common_parameters": {},
        "validation_comparisons": [],
    }
    if not market_data["available"]:
        failure_status = market_data.get("failure_status") or "FAILED_EXECUTION"
        audit.update(
            {
                "status": failure_status,
                "reason": market_data["reason"],
                "conclusion": (
                    "行情資料不可用或不完整，未執行回測、未選參數；"
                    "Additional Holdout 與 Final Out-of-Sample 均未讀取。"
                ),
            }
        )
        rows = planned_result_rows(config, audit["status"])
    else:
        try:
            rows, aggregates, selections, comparisons, conclusion = execute_backtests(
                Path(market_data["path"]), config
            )
        except (ImportError, ModuleNotFoundError) as exc:
            audit.update(
                {
                    "status": "BLOCKED_ENVIRONMENT",
                    "reason": f"缺少回測執行套件：{exc}",
                    "conclusion": "環境不完整，沒有產生或推測任何績效。",
                }
            )
            rows = planned_result_rows(config, audit["status"])
        except Exception as exc:
            audit.update(
                {
                    "status": "FAILED_EXECUTION",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "conclusion": (
                        "回測執行失敗，未使用失敗中的部分結果進行選參；"
                        "Additional Holdout 與 Final Out-of-Sample 均未讀取。"
                    ),
                }
            )
            rows = planned_result_rows(config, audit["status"])
        else:
            audit.update(
                {
                    "status": "COMPLETED",
                    "reason": "",
                    "results": rows,
                    "aggregates": aggregates,
                    "selections": selections,
                    "final_common_parameters": selected_parameter_payload(config, selections),
                    "validation_comparisons": comparisons,
                    "conclusion": conclusion,
                }
            )
    if audit["status"] != "COMPLETED":
        audit["planned_results"] = rows
    paths = write_outputs(root, config, audit, rows)
    return audit, paths
