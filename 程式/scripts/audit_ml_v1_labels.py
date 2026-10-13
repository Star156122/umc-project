"""ML V1 Phase A: Training-only data quality and label distribution audit.

This script never fits a model. It reads only the configured 2023-2024 Training
period and compares Fixed ±1% with the single preregistered volatility K=0.5.
"""
from __future__ import annotations

import csv
import html
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_roles import assert_ml_read_period
from ml.ml_v1_labels import (
    LABEL_NAMES,
    WARMUP_ELIGIBLE,
    aggregate_daily_market,
    build_label_frame,
    eligible_common_samples,
    finite_distribution,
    label_distribution,
)

CONFIG_PATH = ROOT / "configs/ml_v1_research.json"
OUTPUT = ROOT / "exports/ml_v1_label_audit_20261012"


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data = config["data"]
    if assert_ml_read_period(data["start"], data["end"]) != "train":
        raise RuntimeError("ML V1 Phase A may read Training only")
    candidate = config["target"]["candidate"]
    if candidate["k"] != 0.5 or candidate["volatility_lookback_sessions"] != 20:
        raise RuntimeError("ML V1 Phase A fixes K=0.5 and volatility lookback=20")
    if config["prediction"]["decision_time"] != "13:30:00":
        raise RuntimeError("ML V1 Phase A fixes Decision Time at 13:30")
    return config


def run_formula_guardrails(config: dict[str, Any]) -> dict[str, Any]:
    """Execute deterministic, data-independent leakage/cost/warmup checks."""
    index = np.arange(32, dtype=float)
    close = 100.0 + index + np.sin(index) * 0.5
    daily = pd.DataFrame({
        "stock_code": "SYNTHETIC",
        "trade_date": pd.bdate_range("2023-01-02", periods=len(index)).date,
        "open": close - 0.2,
        "close": close,
    })
    target = config["target"]
    candidate = target["candidate"]
    kwargs = {
        "holding_sessions": config["prediction"]["prediction_horizon_sessions"],
        "volatility_lookback": candidate["volatility_lookback_sessions"],
        "commission_rate": target["commission_rate"],
        "transaction_tax_rate": target["transaction_tax_rate"],
        "k": candidate["k"],
    }
    baseline = build_label_frame(daily, **kwargs)
    changed = daily.copy()
    changed.loc[changed.index > 22, "close"] *= 5.0
    future_changed = build_label_frame(changed, **kwargs)
    sigma_no_future_leakage = bool(np.isclose(
        baseline.loc[22, "sigma_20d"], future_changed.loc[22, "sigma_20d"]
    ))
    sample = baseline.iloc[20]
    expected_net = (
        daily.iloc[23]["close"] * (1.0 - target["commission_rate"] - target["transaction_tax_rate"])
        / (daily.iloc[21]["open"] * (1.0 + target["commission_rate"]))
        - 1.0
    )
    cost_formula_exact = bool(np.isclose(sample["future_net_return"], expected_net))
    target_dates_exact = bool(
        sample["entry_date"] == daily.iloc[21]["trade_date"]
        and sample["target_date"] == daily.iloc[23]["trade_date"]
    )
    warmup_exact = bool(
        baseline.iloc[:20]["warmup_status"].eq("INSUFFICIENT_20D_VOLATILITY").all()
        and baseline.iloc[20]["warmup_status"] == WARMUP_ELIGIBLE
    )
    passed = sigma_no_future_leakage and cost_formula_exact and target_dates_exact and warmup_exact
    return {
        "passed": passed,
        "sigma_no_future_leakage": sigma_no_future_leakage,
        "cost_formula_exactly_once": cost_formula_exact,
        "day_t1_open_to_day_t3_close": target_dates_exact,
        "twenty_valid_returns_required": warmup_exact,
    }


def all_codes(config: dict[str, Any]) -> list[str]:
    universe = config["stock_universe"]
    return [*universe["technology"], *universe["financial"]]


def load_training_kbars(config: dict[str, Any]) -> pd.DataFrame:
    data = config["data"]
    path = ROOT / data["database"]
    if not path.is_file():
        raise FileNotFoundError(f"Training database not found: {path}")
    codes = all_codes(config)
    placeholders = ",".join("?" for _ in codes)
    start = int(pd.Timestamp(data["start"], tz="Asia/Taipei").timestamp())
    end = int((pd.Timestamp(data["end"], tz="Asia/Taipei") + pd.Timedelta(days=1)).timestamp())
    query = f"""
        SELECT stock_code, kbar_timestamp, open, high, low, close, volume
        FROM market_kbars
        WHERE stock_code IN ({placeholders})
          AND freq_minutes = ?
          AND kbar_timestamp >= ? AND kbar_timestamp < ?
        ORDER BY stock_code, kbar_timestamp
    """
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        frame = pd.read_sql_query(
            query, connection,
            params=[*codes, int(data["bar_minutes"]), start, end],
        )
    finally:
        connection.close()
    frame["stock_code"] = frame["stock_code"].astype(str)
    frame["datetime"] = pd.to_datetime(frame["kbar_timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    return frame


def expected_times() -> set[str]:
    current = datetime.combine(datetime.today(), time(9, 0))
    last = datetime.combine(datetime.today(), time(13, 30))
    values = set()
    while current <= last:
        values.add(current.strftime("%H:%M:%S"))
        current += timedelta(minutes=5)
    return values


def data_quality_audit(raw: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    codes = all_codes(config)
    expected = expected_times()
    frame = raw.copy()
    frame["trade_date"] = frame["datetime"].dt.date
    frame["clock_time"] = frame["datetime"].dt.strftime("%H:%M:%S")
    frame["is_weekend"] = frame["datetime"].dt.dayofweek >= 5
    weekday_frame = frame.loc[~frame["is_weekend"]].copy()
    union_dates = sorted(weekday_frame["trade_date"].unique())
    by_stock: dict[str, Any] = {}
    total_duplicates = 0
    total_invalid = 0
    unresolved_full_day_gaps = 0
    total_weekend_rows = 0
    total_missing_regular_bars = 0

    for code in codes:
        g = frame.loc[frame["stock_code"] == code].copy()
        weekday_g = g.loc[~g["is_weekend"]].copy()
        weekend_rows = g.loc[g["is_weekend"], [
            "datetime", "open", "high", "low", "close", "volume"
        ]]
        duplicates = int(g.duplicated(["kbar_timestamp"], keep=False).sum())
        numeric = g[["open", "high", "low", "close", "volume"]]
        nan = numeric.isna().any(axis=1)
        finite = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
        invalid = (
            nan | ~finite
            | (g[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (g["volume"] < 0)
            | (g["high"] < g[["open", "close", "low"]].max(axis=1))
            | (g["low"] > g[["open", "close", "high"]].min(axis=1))
        )
        dates = set(weekday_g["trade_date"])
        missing_dates = [str(value) for value in union_dates if value not in dates]
        unresolved_full_day_gaps += len(missing_dates)
        day_rows = []
        count_distribution = Counter()
        missing_clock_counter: Counter[str] = Counter()
        extra_clock_counter: Counter[str] = Counter()
        for day, day_frame in weekday_g.groupby("trade_date", sort=True):
            observed = set(day_frame["clock_time"])
            count_distribution[int(len(day_frame))] += 1
            missing_clock_counter.update(expected - observed)
            extra_clock_counter.update(observed - expected)
            if observed != expected or len(day_frame) != len(expected):
                day_rows.append({
                    "trade_date": str(day), "kbar_count": int(len(day_frame)),
                    "missing_times": sorted(expected - observed),
                    "extra_times": sorted(observed - expected),
                })
                total_missing_regular_bars += len(expected - observed)
        by_stock[code] = {
            "rows": int(len(g)),
            "raw_distinct_dates": int(g["trade_date"].nunique()),
            "valid_weekday_trading_days": int(len(dates)),
            "first_timestamp": g["datetime"].min().isoformat() if len(g) else None,
            "last_timestamp": g["datetime"].max().isoformat() if len(g) else None,
            "duplicate_timestamp_rows": duplicates,
            "invalid_ohlcv_rows": int(invalid.sum()),
            "negative_volume_rows": int((g["volume"] < 0).sum()),
            "weekend_rows": int(len(weekend_rows)),
            "weekend_observations": [
                {
                    "datetime": row.datetime.isoformat(),
                    "open": float(row.open), "high": float(row.high),
                    "low": float(row.low), "close": float(row.close),
                    "volume": float(row.volume),
                }
                for row in weekend_rows.itertuples(index=False)
            ],
            "missing_dates_against_universe_union": missing_dates,
            "missing_date_reason": (
                "No rows exist for this stock while peer stocks have data; database alone cannot distinguish a trading halt/no-trade day from a source omission. No values were filled."
                if missing_dates else None
            ),
            "daily_kbar_count_distribution": {str(k): int(v) for k, v in sorted(count_distribution.items())},
            "nonstandard_days": day_rows,
            "missing_clock_time_counts": dict(sorted(missing_clock_counter.items())),
            "extra_clock_time_counts": dict(sorted(extra_clock_counter.items())),
        }
        total_duplicates += duplicates
        total_invalid += int(invalid.sum())
        total_weekend_rows += int(len(weekend_rows))

    issues = []
    if total_duplicates or total_invalid:
        issues.append("FAIL_INVALID_OR_DUPLICATE_DATA")
    if total_weekend_rows:
        issues.append("REVIEW_REQUIRED_NON_TRADING_DATE_ROWS")
    if unresolved_full_day_gaps:
        issues.append("REVIEW_REQUIRED_FULL_DAY_GAPS")
    if total_missing_regular_bars:
        issues.append("REVIEW_REQUIRED_MISSING_INTRADAY_KBARS")
    quality_status = "PASS" if not issues else "|".join(issues)
    return {
        "status": quality_status,
        "issues": issues,
        "expected_regular_times": sorted(expected),
        "expected_regular_kbars_per_day": len(expected),
        "universe_union_trading_days": len(union_dates),
        "duplicate_timestamp_rows": total_duplicates,
        "invalid_ohlcv_rows": total_invalid,
        "weekend_rows": total_weekend_rows,
        "missing_regular_intraday_kbars": total_missing_regular_bars,
        "unresolved_full_day_gaps": unresolved_full_day_gaps,
        "by_stock": by_stock,
        "no_automatic_fill": True,
        "label_audit_excludes_weekend_rows": True,
        "day_count_difference_explanation": (
            "2379 and 2884 have 481 raw dates. The other eight stocks have one extra 2024-12-01 Sunday row, so all ten have the same 481 weekday trading dates. The difference is an extra non-trading-date observation in eight stocks, not a missing weekday in 2379/2884."
        ),
    }


def imbalance_flags(payload: dict[str, Any], threshold: float) -> list[str]:
    return [
        f"SEVERE_CLASS_IMBALANCE:{name}<{threshold:.0%}"
        for name, item in payload["classes"].items()
        if item["proportion"] is not None and item["proportion"] < threshold
    ]


def distribution_audit(samples: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    threshold = float(config["phase_a"]["severe_class_imbalance_threshold"])
    industries = config["stock_universe"]
    by_stock: dict[str, Any] = {}
    for code, g in samples.groupby("stock_code", sort=True):
        fixed = label_distribution(g["fixed_label"])
        volatility = label_distribution(g["volatility_label"])
        by_stock[str(code)] = {
            "fixed_1pct": {**fixed, "flags": imbalance_flags(fixed, threshold)},
            "volatility_k_0_50": {**volatility, "flags": imbalance_flags(volatility, threshold)},
            "future_net_return": finite_distribution(g["future_net_return"]),
            "sigma_20d": finite_distribution(g["sigma_20d"]),
            "sigma_3d": finite_distribution(g["sigma_3d"]),
            "z_return": finite_distribution(g["z_return"]),
        }

    industry_output: dict[str, Any] = {}
    severe = False
    for industry in ("technology", "financial"):
        group = samples.loc[samples["stock_code"].isin(industries[industry])]
        fixed = label_distribution(group["fixed_label"])
        volatility = label_distribution(group["volatility_label"])
        fixed_flags = imbalance_flags(fixed, threshold)
        volatility_flags = imbalance_flags(volatility, threshold)
        severe = severe or bool(fixed_flags or volatility_flags)
        industry_output[industry] = {
            "stock_codes": industries[industry],
            "fixed_1pct": {**fixed, "flags": fixed_flags},
            "volatility_k_0_50": {**volatility, "flags": volatility_flags},
        }
    proportions = {
        version: {
            code: by_stock[code][version]["classes"]
            for code in sorted(by_stock)
        }
        for version in ("fixed_1pct", "volatility_k_0_50")
    }
    spread: dict[str, Any] = {}
    for version, stocks in proportions.items():
        spread[version] = {}
        for label in LABEL_NAMES.values():
            values = [item[label]["proportion"] for item in stocks.values()]
            spread[version][label] = {
                "min": float(min(values)), "max": float(max(values)),
                "range": float(max(values) - min(values)),
            }
    return {
        "by_stock": by_stock,
        "by_industry": industry_output,
        "cross_stock_proportion_spread": spread,
        "severe_class_imbalance": severe,
    }


def table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: dict[str, Any]) -> str:
    stock_rows = []
    for code, payload in report["label_audit"]["by_stock"].items():
        for version in ("fixed_1pct", "volatility_k_0_50"):
            classes = payload[version]["classes"]
            stock_rows.append([
                code, version, payload[version]["samples"],
                f"{classes['SELL']['count']} ({classes['SELL']['proportion']:.2%})",
                f"{classes['HOLD']['count']} ({classes['HOLD']['proportion']:.2%})",
                f"{classes['BUY']['count']} ({classes['BUY']['proportion']:.2%})",
                ", ".join(payload[version]["flags"]) or "-",
            ])
    industry_rows = []
    for industry, payload in report["label_audit"]["by_industry"].items():
        for version in ("fixed_1pct", "volatility_k_0_50"):
            classes = payload[version]["classes"]
            industry_rows.append([
                industry, version, payload[version]["samples"],
                f"{classes['SELL']['proportion']:.2%}",
                f"{classes['HOLD']['proportion']:.2%}",
                f"{classes['BUY']['proportion']:.2%}",
                ", ".join(payload[version]["flags"]) or "-",
            ])
    quality_rows = []
    for code, payload in report["data_quality"]["by_stock"].items():
        quality_rows.append([
            code, payload["rows"], payload["raw_distinct_dates"], payload["valid_weekday_trading_days"],
            payload["duplicate_timestamp_rows"], payload["invalid_ohlcv_rows"],
            payload["weekend_rows"],
            ", ".join(payload["missing_dates_against_universe_union"]) or "-",
            json.dumps(payload["daily_kbar_count_distribution"], ensure_ascii=False),
        ])
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>
<title>ML V1 Phase A Label Audit</title><style>
body{{font-family:system-ui;max-width:1280px;margin:30px auto;padding:0 18px;color:#172033}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #ccd3df;padding:7px;text-align:left}}
th{{background:#eef3f8}}pre{{background:#f5f7fa;padding:14px;overflow:auto}}.pass{{color:#087b37}}.stop{{color:#b42318}}</style></head><body>
<h1>ML V1 Phase A：Label Distribution Audit</h1>
<p>Training-only：2023-01-01～2024-12-31。未訓練模型、未使用 Validation／Development／Holdout／Final OOS。</p>
<h2>Phase A Gate</h2><pre>{html.escape(json.dumps(report['phase_a_gate'], ensure_ascii=False, indent=2))}</pre>
<h2>Data Quality</h2><p>{html.escape(report['data_quality']['day_count_difference_explanation'])}</p>{table(['股票','K棒','原始日期數','有效平日數','重複','Invalid OHLCV','週末資料','相對共同平日缺日','每日筆數分布'],quality_rows)}
<h2>各股票 Label Distribution</h2>{table(['股票','Label','樣本','SELL','HOLD','BUY','Flag'],stock_rows)}
<h2>產業 Aggregate</h2>{table(['產業','Label','樣本','SELL','HOLD','BUY','Flag'],industry_rows)}
<h2>Warmup 與防洩漏</h2><pre>{html.escape(json.dumps(report['label_construction_audit'], ensure_ascii=False, indent=2))}</pre>
<h2>完整統計</h2><pre>{html.escape(json.dumps(report['label_audit'], ensure_ascii=False, indent=2))}</pre>
</body></html>"""


def write_csvs(report: dict[str, Any]) -> None:
    with (OUTPUT / "label_distribution.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["stock_code", "label_version", "samples", "sell_count", "sell_pct", "hold_count", "hold_pct", "buy_count", "buy_pct", "flags"])
        for code, payload in report["label_audit"]["by_stock"].items():
            for version in ("fixed_1pct", "volatility_k_0_50"):
                value = payload[version]
                classes = value["classes"]
                writer.writerow([code, version, value["samples"], classes["SELL"]["count"], classes["SELL"]["proportion"], classes["HOLD"]["count"], classes["HOLD"]["proportion"], classes["BUY"]["count"], classes["BUY"]["proportion"], "|".join(value["flags"])])
    with (OUTPUT / "return_volatility_stats.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["stock_code", "metric", "count", "mean", "median", "std", "min", "max", "q10", "q25", "q50", "q75", "q90"])
        for code, payload in report["label_audit"]["by_stock"].items():
            for metric in ("future_net_return", "sigma_20d", "sigma_3d", "z_return"):
                s = payload[metric]
                writer.writerow([code, metric, s["count"], s["mean"], s["median"], s["standard_deviation"], s["min"], s["max"], s["q10"], s["q25"], s["q50"], s["q75"], s["q90"]])


def main() -> int:
    config = load_config()
    guardrails = run_formula_guardrails(config)
    if not guardrails["passed"]:
        raise RuntimeError(f"ML V1 label guardrail failure: {guardrails}")
    raw = load_training_kbars(config)
    quality = data_quality_audit(raw, config)
    # Do not fill or mutate source data. The clearly invalid Sunday rows are
    # excluded from this audit's analytical sample and remain reported above.
    analysis_raw = raw.loc[raw["datetime"].dt.dayofweek < 5].copy()
    daily = aggregate_daily_market(analysis_raw)
    target = config["target"]
    candidate = target["candidate"]
    labels = build_label_frame(
        daily,
        holding_sessions=config["prediction"]["prediction_horizon_sessions"],
        volatility_lookback=candidate["volatility_lookback_sessions"],
        commission_rate=target["commission_rate"],
        transaction_tax_rate=target["transaction_tax_rate"],
        k=candidate["k"],
    )
    eligible = eligible_common_samples(labels)
    label_audit = distribution_audit(eligible, config)
    warmup_by_stock = {
        str(code): {
            "target_complete_samples": int(len(group)),
            "eligible_samples": int(group["warmup_status"].eq(WARMUP_ELIGIBLE).sum()),
            "warmup_excluded": int((~group["warmup_status"].eq(WARMUP_ELIGIBLE)).sum()),
        }
        for code, group in labels.groupby("stock_code", sort=True)
    }
    leakage_tests_pass = bool(guardrails["passed"])
    data_quality_normal = quality["status"] == "PASS"
    no_severe = not label_audit["severe_class_imbalance"]
    phase_b_allowed = data_quality_normal and no_severe and leakage_tests_pass
    report = {
        "report_name": "ML V1 Confirmatory Label Experiment - Phase A",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": "configs/ml_v1_research.json",
        "experiment_id": config["experiment_id"],
        "data_role": "train",
        "training_period": {"start": config["data"]["start"], "end": config["data"]["end"]},
        "historical_experiment_recorded": config["historical_experiment"],
        "data_quality": quality,
        "label_construction_audit": {
            "target_complete_samples": int(len(labels)),
            "eligible_common_samples": int(len(eligible)),
            "warmup_excluded_total": int(len(labels) - len(eligible)),
            "warmup_by_stock": warmup_by_stock,
            "sigma_formula": "trailing non-centered per-stock std(ddof=1) of 20 daily close returns through Day T",
            "sigma_3d_formula": "sigma_20d * sqrt(3)",
            "candidate_k": 0.5,
            "cost_formula": "target_close*(1-commission-tax)/(entry_open*(1+commission))-1",
            "cost_deducted_once": True,
            "future_fill_used": False,
            "backward_fill_used": False,
            "global_mean_fill_used": False,
            "common_sample_set_for_both_labels": True,
            "leakage_tests_pass": leakage_tests_pass,
            "runtime_formula_guardrails": guardrails,
        },
        "label_audit": label_audit,
        "phase_a_gate": {
            "data_quality_normal": data_quality_normal,
            "no_industry_severe_class_imbalance": no_severe,
            "label_leakage_tests_pass": leakage_tests_pass,
            "phase_b_allowed_without_further_action": False,
            "phase_b_technical_conditions_pass": phase_b_allowed,
            "phase_b_requires_explicit_user_authorization": True,
            "decision": "PASS_WAIT_USER" if phase_b_allowed else "STOP_AFTER_PHASE_A",
            "blocking_reasons": [
                *([] if data_quality_normal else quality["issues"]),
                *([] if no_severe else ["SEVERE_CLASS_IMBALANCE"]),
                *([] if leakage_tests_pass else ["LABEL_LEAKAGE_TEST_FAILED"]),
            ],
        },
        "execution_confirmation": {
            "models_trained": False,
            "phase_b_started": False,
            "controlled_validation_used": False,
            "development_used": False,
            "additional_holdout_used": False,
            "final_oos_used": False,
            "k_search_performed": False,
            "feature_search_performed": False,
            "hyperparameter_search_performed": False,
            "technical_modified": False,
            "hybrid_backtest_run": False,
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "label_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "data_quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "experiment_manifest.json").write_text(json.dumps({
        "experiment_id": config["experiment_id"],
        "phase": "A_LABEL_DISTRIBUTION_AUDIT",
        "config": "configs/ml_v1_research.json",
        "only_label_versions": ["ml_v1_fixed_1pct", "ml_v1_volatility_k_0_50"],
        "phase_b_started": False,
        "output_files": ["label_audit.json", "data_quality.json", "label_distribution.csv", "return_volatility_stats.csv", "label_audit.html"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csvs(report)
    (OUTPUT / "label_audit.html").write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report["phase_a_gate"], ensure_ascii=False, indent=2))
    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
