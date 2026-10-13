"""Build the shared, Training-only ML V1 exact-sequence sample manifest.

No model is fitted. No missing bar is filled. The market database is opened
read-only and its hash is checked before/after the audit.
"""
from __future__ import annotations

import hashlib
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.ml_v1_labels import (
    LABEL_NAMES,
    aggregate_daily_market,
    build_label_frame,
    eligible_common_samples,
    label_distribution,
)
from ml.ml_v1_sequence import (
    EXACT_SEQUENCE_TIMES,
    EXCLUDE_NO_FILL,
    MISSING_SEQUENCE_POLICY,
    SEQUENCE_COMPLETE,
    SEQUENCE_POLICY,
    evaluate_exact_sequence,
)
from scripts.audit_ml_v1_labels import load_config, load_training_kbars

OUTPUT = ROOT / "exports/ml_v1_model_eligible_20261012"
PROTOCOL_PATH = ROOT / "configs/ml_training_protocol_v3.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fold_id(decision_date: Any, target_date: Any, protocol: dict[str, Any]) -> str:
    decision = pd.Timestamp(decision_date)
    target = pd.Timestamp(target_date)
    folds = protocol["walk_forward"]["folds"]
    first = folds[0]
    if pd.Timestamp(first["train_start"]) <= decision <= pd.Timestamp(first["train_end"]) and pd.Timestamp(
        first["train_start"]
    ) <= target <= pd.Timestamp(first["train_end"]):
        return "FOLD_1_TRAIN"
    for item in folds:
        if pd.Timestamp(item["validation_start"]) <= decision <= pd.Timestamp(item["validation_end"]) and pd.Timestamp(
            item["validation_start"]
        ) <= target <= pd.Timestamp(item["validation_end"]):
            return f"FOLD_{int(item['fold'])}_VALIDATION"
    return "FULL_TRAIN_ONLY_BOUNDARY"


def flags(distribution: dict[str, Any], threshold: float) -> list[str]:
    return [
        f"SEVERE_CLASS_IMBALANCE:{name}<{threshold:.0%}"
        for name, value in distribution["classes"].items()
        if value["proportion"] is not None and value["proportion"] < threshold
    ]


def distributions(samples: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    threshold = float(config["phase_a"]["severe_class_imbalance_threshold"])
    by_stock: dict[str, Any] = {}
    for code, group in samples.groupby("stock_code", sort=True):
        fixed = label_distribution(group["fixed_label_id"])
        volatility = label_distribution(group["volatility_label_id"])
        by_stock[str(code)] = {
            "samples": int(len(group)),
            "fixed_1pct": {**fixed, "buy_coverage": fixed["classes"]["BUY"]["proportion"], "flags": flags(fixed, threshold)},
            "volatility_k_0_50": {**volatility, "buy_coverage": volatility["classes"]["BUY"]["proportion"], "flags": flags(volatility, threshold)},
        }
    by_industry: dict[str, Any] = {}
    severe = False
    for industry in ("technology", "financial"):
        group = samples.loc[samples["industry"] == industry]
        fixed = label_distribution(group["fixed_label_id"])
        volatility = label_distribution(group["volatility_label_id"])
        fixed_flags, volatility_flags = flags(fixed, threshold), flags(volatility, threshold)
        severe = severe or bool(fixed_flags or volatility_flags)
        by_industry[industry] = {
            "samples": int(len(group)),
            "fixed_1pct": {**fixed, "buy_coverage": fixed["classes"]["BUY"]["proportion"], "flags": fixed_flags},
            "volatility_k_0_50": {**volatility, "buy_coverage": volatility["classes"]["BUY"]["proportion"], "flags": volatility_flags},
        }
    return {"by_stock": by_stock, "by_industry": by_industry, "severe_class_imbalance": severe}


def render_html(report: dict[str, Any]) -> str:
    rows = []
    for code, item in report["label_distribution"]["by_stock"].items():
        for version in ("fixed_1pct", "volatility_k_0_50"):
            value = item[version]
            c = value["classes"]
            rows.append(
                f"<tr><td>{code}</td><td>{version}</td><td>{item['samples']}</td>"
                f"<td>{c['SELL']['proportion']:.2%}</td><td>{c['HOLD']['proportion']:.2%}</td>"
                f"<td>{c['BUY']['proportion']:.2%}</td><td>{html.escape(', '.join(value['flags']) or '-')}</td></tr>"
            )
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>ML V1 Model-Eligible Audit</title>
<style>body{{font-family:system-ui;max-width:1200px;margin:30px auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd;padding:7px}}th{{background:#eef}}</style></head><body>
<h1>ML V1 Model-Eligible Label Audit</h1><pre>{html.escape(json.dumps(report['summary'], ensure_ascii=False, indent=2))}</pre>
<table><tr><th>股票</th><th>Label</th><th>Samples</th><th>SELL</th><th>HOLD</th><th>BUY</th><th>Flag</th></tr>{''.join(rows)}</table>
<h2>Industry Aggregate</h2><pre>{html.escape(json.dumps(report['label_distribution']['by_industry'], ensure_ascii=False, indent=2))}</pre>
</body></html>"""


def main() -> int:
    config = load_config()
    if config["prediction"]["sequence_policy"] != SEQUENCE_POLICY:
        raise RuntimeError("ML V1 must use the exact 24-bar sequence policy")
    if tuple(config["prediction"]["required_sequence_times"]) != EXACT_SEQUENCE_TIMES:
        raise RuntimeError("Configured sequence times differ from the shared policy")
    if config["prediction"]["missing_sequence_policy"] != MISSING_SEQUENCE_POLICY or config["prediction"]["sequence_fill_allowed"] is not False:
        raise RuntimeError("ML V1 missing sequences must be excluded without fill")

    database = ROOT / config["data"]["database"]
    database_hash_before = sha256(database)
    raw = load_training_kbars(config)
    weekday = raw.loc[raw["datetime"].dt.dayofweek < 5].copy()
    weekday["decision_date"] = weekday["datetime"].dt.date.astype(str)

    statuses = []
    for (code, day), group in weekday.groupby(["stock_code", "decision_date"], sort=True):
        result = evaluate_exact_sequence(group)
        statuses.append({
            "stock_code": str(code), "decision_date": str(day),
            "sequence_status": result.status,
            "missing_sequence_times": "|".join(result.missing_times),
            "duplicate_sequence_times": "|".join(result.duplicate_times),
        })
    status_frame = pd.DataFrame(statuses)

    daily = aggregate_daily_market(weekday)
    target = config["target"]
    candidate = target["candidate"]
    labels = eligible_common_samples(build_label_frame(
        daily,
        holding_sessions=config["prediction"]["prediction_horizon_sessions"],
        volatility_lookback=candidate["volatility_lookback_sessions"],
        commission_rate=target["commission_rate"],
        transaction_tax_rate=target["transaction_tax_rate"],
        k=candidate["k"],
    ))
    labels["decision_date"] = labels["trade_date"].astype(str)
    merged = labels.merge(status_frame, on=["stock_code", "decision_date"], how="left", validate="one_to_one")
    if merged["sequence_status"].isna().any():
        raise RuntimeError("A Label-eligible row has no sequence audit status")

    industries = {
        code: industry
        for industry in ("technology", "financial")
        for code in config["stock_universe"][industry]
    }
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    merged["industry"] = merged["stock_code"].map(industries)
    merged["fold_id"] = [fold_id(day, target_day, protocol) for day, target_day in zip(merged["trade_date"], merged["target_date"])]
    merged["fixed_label_id"] = merged["fixed_label"].astype(int)
    merged["volatility_label_id"] = merged["volatility_label"].astype(int)
    merged["fixed_label"] = merged["fixed_label_id"].map(LABEL_NAMES)
    merged["volatility_label"] = merged["volatility_label_id"].map(LABEL_NAMES)

    eligible = merged.loc[merged["sequence_status"] == SEQUENCE_COMPLETE].copy()
    excluded = merged.loc[merged["sequence_status"] == EXCLUDE_NO_FILL].copy()
    audit = distributions(eligible, config)
    per_stock = {
        code: int((eligible["stock_code"] == code).sum())
        for code in [*config["stock_universe"]["technology"], *config["stock_universe"]["financial"]]
    }
    database_hash_after = sha256(database)
    if database_hash_before != database_hash_after:
        raise RuntimeError("market_data.sqlite3 changed during the read-only audit")

    summary = {
        "label_eligible_before_sequence_rule": int(len(merged)),
        "model_eligible_samples": int(len(eligible)),
        "excluded_incomplete_samples": int(len(excluded)),
        "per_stock_model_eligible_samples": per_stock,
        "sequence_policy": SEQUENCE_POLICY,
        "missing_sequence_policy": MISSING_SEQUENCE_POLICY,
        "shared_by_models": ["random_forest", "xgboost", "gru"],
        "shared_by_labels": ["fixed_1pct", "volatility_k_0_50"],
        "model_eligible_label_audit": "STOP_SEVERE_CLASS_IMBALANCE" if audit["severe_class_imbalance"] else "PASS",
        "pre_phase_b_data_conditions_pass": not audit["severe_class_imbalance"],
        "phase_b_started": False,
        "models_trained": False,
    }
    report = {
        "report_name": "ML V1 Model-Eligible Sample and Label Audit",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "label_distribution": audit,
        "database_integrity": {
            "opened_read_only": True,
            "sha256_before": database_hash_before,
            "sha256_after": database_hash_after,
            "modified": False,
        },
        "fill_policy": {
            "forward_fill": False, "backward_fill": False,
            "interpolation": False, "synthetic_kbar": False,
            "previous_close_fake_bar": False,
        },
        "weekend_policy": {"2024-12-01_excluded": True, "raw_rows_deleted": False},
        "locked_data_used": False,
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    columns = ["stock_code", "decision_date", "sequence_status", "fixed_label", "volatility_label", "fold_id", "industry"]
    eligible[columns].sort_values(["stock_code", "decision_date"]).to_csv(
        OUTPUT / "model_eligible_samples.csv", index=False, encoding="utf-8-sig"
    )
    excluded[[*columns, "missing_sequence_times"]].sort_values(["stock_code", "decision_date"]).to_csv(
        OUTPUT / "excluded_incomplete_samples.csv", index=False, encoding="utf-8-sig"
    )
    (OUTPUT / "model_eligible_label_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (OUTPUT / "model_eligible_label_audit.html").write_text(render_html(report), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
