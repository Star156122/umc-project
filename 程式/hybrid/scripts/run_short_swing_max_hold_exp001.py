"""Paired exploratory test: original Technical V2-B vs 270-bar max hold."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.multi_strategy_backtest_v1 import derive_equity_curve, summary_from_curve  # noqa: E402
from hybrid.scripts import run_short_swing_ab_test as shared  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import build_regime_context  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/short_swing_max_hold_exp001.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config["data_role"] != "historical_development" or config["period"] != {"start": "2024-12-01", "end": "2024-12-31"}:
        raise RuntimeError("BLOCKED_UNREGISTERED_DATA_SCOPE")
    baseline = shared._load_config()
    for key in ("stock_code", "initial_capital", "data_source", "technical_config", "frozen_candidate_config", "frozen_candidate_id", "execution"):
        if config.get(key) != baseline.get(key):
            raise RuntimeError(f"BLOCKED_BASELINE_CONFIG_MISMATCH:{key}")
    if baseline.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_BASELINE_MARKET_FILTER_NOT_ENABLED")
    if config["baseline"]["max_holding_bars"] != baseline["risk"]["max_holding_bars"]:
        raise RuntimeError("BLOCKED_BASELINE_HOLD_LIMIT_MISMATCH")
    if config["candidate"]["max_holding_bars"] <= config["baseline"]["max_holding_bars"]:
        raise RuntimeError("BLOCKED_CANDIDATE_HOLD_LIMIT_NOT_EXTENDED")
    return config, baseline


def run() -> Path:
    config, base_config = _load_inputs()
    tech_path = ROOT / config["technical_config"]
    frozen_path = ROOT / config["frozen_candidate_config"]
    db_path = ROOT / config["data_source"]
    if not all(path.is_file() for path in (tech_path, frozen_path, db_path)):
        raise FileNotFoundError("BLOCKED_MISSING_CONFIG_OR_DATABASE")
    tech = tv1.read_config(tech_path)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, base_candidate = tv1.candidate_by_id(tech, config["frozen_candidate_id"])
    frozen_cfg = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen_cfg.get("base_candidate") != base_candidate["id"] or frozen_cfg.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_TECHNICAL_CANDIDATE_IDENTITY_MISMATCH")

    db_hash_before = sha256(db_path)
    output = ROOT / "exports" / f"short_swing_max_hold_exp001_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    rows_by_version: dict[str, dict[str, Any]] = {}
    trade_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    annual_rows: list[dict[str, Any]] = []
    data_audit: dict[str, Any] = {}

    with tv1._readonly_connection(db_path) as connection:
        fold = {
            "id": "DEC_2024_EXPLORATORY",
            "train_start": config["warmup_start"], "train_end": "2024-11-30",
            "evaluate_start": config["period"]["start"], "evaluate_end": config["period"]["end"],
        }
        full_frame, evaluation, app_base, warmup_count, eval_lower = tv1._load_fold_frame(
            connection, tech, base_candidate, config["stock_code"], fold,
        )
        frame_times = full_frame["kbar_time"].to_list()
        schedule = {shared.iso_taipei(value) for value in frame_times[1:]}
        candidate_dates = {value[:10] for value in schedule}
        regime_context, regime_audit = build_regime_context(
            connection, tuple(frozen_cfg["market_regime_filter"]["peer_stock_codes"]), candidate_dates,
        )
        raw_rows = connection.execute(
            "SELECT stock_code,kbar_timestamp FROM market_kbars "
            "WHERE stock_code IN (?,?,?,?,?) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
            "ORDER BY stock_code,kbar_timestamp",
            (*frozen_cfg["market_regime_filter"]["peer_stock_codes"], *tv1._timestamp_bounds(config["warmup_start"], config["period"]["end"])),
        ).fetchall()
        session_counts: dict[tuple[str, str], int] = {}
        for row in raw_rows:
            local_dt = datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
            if "09:00" <= local_dt.strftime("%H:%M") <= "13:25":
                key = (str(row["stock_code"]), local_dt.date().isoformat())
                session_counts[key] = session_counts.get(key, 0) + 1
        all_days = sorted({day for _, day in session_counts})
        incomplete = [(stock, day, count) for (stock, day), count in session_counts.items() if count != 54]
        if incomplete or len(session_counts) != 5 * len(all_days):
            raise RuntimeError(f"BLOCKED_SESSION_COMPLETENESS:{len(incomplete)} incomplete stock-days")
        for stock in frozen_cfg["market_regime_filter"]["peer_stock_codes"]:
            if {day for code, day in session_counts if code == stock} != set(all_days):
                raise RuntimeError(f"BLOCKED_GLOBAL_TRADING_DATE_PARITY:{stock}")
        eval_days = sorted(day for day in all_days if config["period"]["start"] <= day <= config["period"]["end"])
        if not eval_days:
            raise RuntimeError("BLOCKED_NO_COMPLETE_EVALUATION_SESSIONS")

        variants = (
            ("A_ORIGINAL_V2B", int(config["baseline"]["max_holding_bars"])),
            ("F_MAX_HOLD_270_BARS", int(config["candidate"]["max_holding_bars"])),
        )
        common_bars: list[dict[str, Any]] | None = None
        for version, max_hold in variants:
            config_variant = copy.deepcopy(tech)
            config_variant["common_backtest"]["initial_capital"] = config["initial_capital"]
            risk = config["risk"]
            config_variant["common_backtest"].update({
                "stock_fee_rate": config["execution"]["stock_fee_rate"],
                "stock_tax_rate": config["execution"]["stock_tax_rate"],
                "slippage_rate": config["execution"]["slippage_rate"],
                "stop_loss_pct": risk["stop_loss_pct"],
                "min_hold_bars": risk["min_holding_bars"],
                "max_hold_bars": max_hold,
                "cooldown_bars": risk["cooldown_bars"],
                "max_entries_per_day": risk["max_entries_per_day"],
            })
            full, eval_frame, app, _, lower = tv1._load_fold_frame(
                connection, config_variant, base_candidate, config["stock_code"], fold,
            )
            if full["kbar_timestamp"].to_list() != full_frame["kbar_timestamp"].to_list():
                raise RuntimeError(f"BLOCKED_VARIANT_BAR_ALIGNMENT:{version}")
            bars = bt.build_kbar_rows_from_frame(eval_frame, app)
            if common_bars is None:
                common_bars = bars
            strategy_cls = shared._make_strategy_class("A_ORIGINAL_V2B")
            strategy = strategy_cls(
                config=app, study_config=config_variant, family_name=family_name, family=family,
                candidate=base_candidate, data_role="historical_development", fold_id=fold["id"],
                is_backtest=True, is_simulation=True, local_only=True,
                ml_model="technical_only", prediction_lookup={}, candidate_schedule=schedule,
                regime_context=regime_context,
            )
            strategy.configure_local_account(app)
            strategy.evaluation_start_timestamp = lower
            strategy.local_slippage_bps = float(config["execution"]["slippage_rate"]) * 10000.0
            strategy.diagnostic_sizing_enabled = True
            stats = bt.MarketDataStats(
                tick_count=full.height,
                first_timestamp=float(full["kbar_timestamp"][0]),
                last_timestamp=float(full["kbar_timestamp"][-1]),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                bt.replay_kbars(strategy, full, stats)
            trades = bt._build_trade_rows(strategy, app)
            candidates = [dict(row, variant=version) for row in strategy.candidate_audit]
            risks = [dict(row, variant=version) for row in strategy.risk_decisions]
            summary, enriched, curves = shared._metrics_and_rows(
                version=version, strategy=strategy, app=app, bars=bars,
                trade_rows=trades, risk_rows=risks, candidate_rows=candidates,
            )
            summary["max_hold_bars"] = max_hold
            summary["backtest_start"] = config["period"]["start"]
            summary["backtest_end"] = config["period"]["end"]
            if strategy.local_position != 0:
                raise RuntimeError(f"BLOCKED_OPEN_POSITION_AFTER_REPLAY:{version}")
            for row in enriched:
                row["variant"] = version
            rows_by_version[version] = summary
            trade_rows.extend(enriched)
            candidate_rows.extend(candidates)
            risk_rows.extend(risks)
            curve_rows.extend({**row, "variant": version} for row in curves)
            annual_rows.extend(summary.pop("annual_performance"))

        if sha256(db_path) != db_hash_before:
            raise RuntimeError("BLOCKED_DATABASE_CHANGED_DURING_READ_ONLY_RUN")
        data_audit = {
            "actual_first_session": eval_days[0], "actual_last_session": eval_days[-1],
            "evaluation_trading_days": len(eval_days), "full_frame_bar_count": full_frame.height,
            "evaluation_bar_count": evaluation.height, "warmup_bars": int(warmup_count),
            "complete_54_bar_sessions": True, "five_stock_common_dates": True,
            "missing_fill_used": False, "market_regime_audit": regime_audit,
        }

    baseline = rows_by_version["A_ORIGINAL_V2B"]
    prior_a_path = ROOT / "exports/short_swing_ab_test_20261104T123811/comparison.csv"
    if prior_a_path.is_file():
        prior_a = pd.read_csv(prior_a_path).loc[lambda frame: frame.variant == "A_ORIGINAL_V2B"].iloc[0]
        if int(prior_a.trade_count) != 39 or abs(float(prior_a.total_return_pct) - (-0.63843425)) > 1e-5:
            raise RuntimeError("BLOCKED_REFERENCE_BASELINE_AUDIT_UNEXPECTED")
        # A short-month slice is not expected to reproduce the two-year totals;
        # preserve the full-period reference solely as provenance.

    comparison = pd.DataFrame(list(rows_by_version.values()))
    trades_df, candidates_df = pd.DataFrame(trade_rows), pd.DataFrame(candidate_rows)
    risks_df, curves_df, annual_df = pd.DataFrame(risk_rows), pd.DataFrame(curve_rows), pd.DataFrame(annual_rows)
    output.mkdir(parents=True, exist_ok=False)
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(output / "trades.csv", index=False, encoding="utf-8-sig")
    candidates_df.to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    risks_df.to_csv(output / "risk_decisions.csv", index=False, encoding="utf-8-sig")
    curves_df.to_csv(output / "equity_curves.csv", index=False, encoding="utf-8-sig")
    annual_df.to_csv(output / "annual_performance.csv", index=False, encoding="utf-8-sig")
    snapshot = {**config, "comparison_versions": list(rows_by_version), "only_parameter_changed": "risk.max_holding_bars"}
    (output / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {
        "status": "COMPLETED", "experiment_id": config["experiment_id"], "data_role": config["data_role"],
        "stock_code": config["stock_code"], "period": config["period"], **data_audit,
        "database_path": str(db_path.relative_to(ROOT)), "database_sha256_before": db_hash_before,
        "database_sha256_after": sha256(db_path),
        "technical_config_sha256": sha256(tech_path), "frozen_candidate_config_sha256": sha256(frozen_path),
        "runner_sha256": sha256(Path(__file__)), "shared_runner_sha256": sha256(Path(shared.__file__)),
        "technical_source_sha256": sha256(ROOT / "trading_system/technical_v1.py"),
        "versions": {name: {"max_holding_bars": bars} for name, bars in (
            ("A_ORIGINAL_V2B", config["baseline"]["max_holding_bars"]),
            ("F_MAX_HOLD_270_BARS", config["candidate"]["max_holding_bars"]),
        )},
        "only_changed_setting": "max_holding_bars: 162 -> 270",
        "entry_execution": "completed 5-minute bar signal -> next actual tradable 5-minute bar open",
        "stop_execution": "same inherited OHLC stop handling; gap uses bar open; stop takes priority",
        "slippage_rate": config["execution"]["slippage_rate"], "final_oos_used": False,
        "parameter_search": False, "ml_gate_used": False,
        "original_two_year_reference": {"run": "exports/short_swing_ab_test_20261104T123811", "trades": 39, "return_pct": -0.63843425},
    }
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 2303 原始 Technical V2-B 最長持有時間試驗",
        "",
        f"- 實驗 ID：`{config['experiment_id']}`；資料角色：`historical_development`。",
        f"- 評估期間：{data_audit['actual_first_session']} 至 {data_audit['actual_last_session']}（2024 年 12 月單月）；使用 2023-01-01 起資料暖機指標，交易只在評估月執行。",
        "- 對照：原始 A 的最大持有 162 根 5 分 K；候選 F 為 270 根。以每日 54 根完整 K 棒換算，約為 3 天對 5 天等值。因進場可能在盤中，這是 bar 數上限，不保證在第五個日曆交易日精確收盤出場。",
        "- 唯一變更：`max_holding_bars`。技術進場、30 根低點技術出場、停損、Risk V1 其他設定、成本、成交與市場代理濾網相同。滑價維持 0。",
        "- 這是已曝光開發資料的單月探索比較；筆數少時只能視為不足以判斷，不能據此宣稱改善或更換正式候選。",
        "", "## 配對結果", "",
        "| 版本 | 持有上限 | 交易數 | 報酬 | 淨損益 | 勝率 | 最大回撤 | PF | Sharpe | 平均持有交易日間隔 | 時間到期出場 | 成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.to_dict("records"):
        lines.append(f"| {row['variant']} | {int(row['max_hold_bars']) if 'max_hold_bars' in row else (162 if row['variant']=='A_ORIGINAL_V2B' else 270)} | {int(row['trade_count'])} | {row['total_return_pct']:.4f}% | {row['net_pnl']:.2f} | {row['win_rate_pct'] if pd.notna(row['win_rate_pct']) else 'N/A'} | {row['max_drawdown_pct']:.4f}% | {row['profit_factor'] if pd.notna(row['profit_factor']) else 'N/A'} | {row['sharpe_ratio'] if pd.notna(row['sharpe_ratio']) else 'N/A'} | {row['average_holding_trading_day_intervals'] if pd.notna(row['average_holding_trading_day_intervals']) else 'N/A'} | {int(row['time_expiration_exit_count'])} | {row['total_transaction_cost']:.2f} |")
    a_time_exits = int(rows_by_version["A_ORIGINAL_V2B"]["time_expiration_exit_count"])
    f_time_exits = int(rows_by_version["F_MAX_HOLD_270_BARS"]["time_expiration_exit_count"])
    a_trades, f_trades = int(rows_by_version["A_ORIGINAL_V2B"]["trade_count"]), int(rows_by_version["F_MAX_HOLD_270_BARS"]["trade_count"])
    lines += ["", "## 判讀", "",
              f"本月 A 與 F 各完成 {a_trades}、{f_trades} 筆交易；時間上限出場數分別為 {a_time_exits}、{f_time_exits}。",
              "若時間上限都沒有觸發，代表本月實際交易由技術出場或停損先結束，這次資料沒有測到延長上限會如何改變持有；目前結果因此是 `INSUFFICIENT`，不能宣稱 5 日上限改善或沒有改善策略。",
              "本次只有單一月份、少量成交，且屬已曝光 Development 資料；即使某個績效數字較高，也不能視為穩定優勢。應查看逐筆交易、持有期間、技術出場與時間到期出場差異，以及成本後報酬和回撤。", "", "## 檔案", "", "- `comparison.csv`：A/F 摘要。", "- `trades.csv`：逐筆成交與持有時間。", "- `candidate_audit.csv`：候選訊號和阻擋原因。", "- `risk_decisions.csv`：Risk V1 決策。", "- `equity_curves.csv`：每根 5 分 K 資產淨值。", "- `annual_performance.csv`：評估月所在年度摘要。", "- `config_snapshot.json`、`audit.json`：註冊設定與資料來源雜湊。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(run())
