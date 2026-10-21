"""Run the preregistered representative-stock fixed benchmark (2023-2024 only)."""
from __future__ import annotations

import contextlib
import hashlib
import html
import io
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridPOCError,
    build_prediction_lookup,
    sha256_file,
    validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.core.risk_v1 import calculate_risk_size  # noqa: E402
from hybrid.core.strategy_v2_batch import StrategyV2BatchStrategy, regime_confirmation_passes  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH,
    directory_hashes,
    latest_ready_preflight,
    read_json,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import MODELS, simulate_one  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
SUBJECTS = ("2330", "2454", "2303")
SUBJECT_NAMES = {"2330": "台積電", "2454": "聯發科", "2303": "聯電"}
CORE_UNIVERSE = ("2303", "2330", "2379", "2454", "3034")
FOLD_IDS = ("WF1", "WF2", "WF3")
COMMON_RUN = ROOT / "exports/hybrid_common_coverage_20261018T121416162413"
POC_RUN = ROOT / "exports/hybrid_poc_v1_20261013T041540804059"
TECH_BATCH = ROOT / "exports/strategy_v2_technical_batch_20261019T094810074002"
RISK_CONFIG = ROOT / "hybrid/config/risk_v1.json"
PREREG = ROOT / "research/strategy_representative_benchmark_exp001.md"


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, np.generic):
        return safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_subject_rows(path: Path, *, key: str = "stock_code") -> pd.DataFrame:
    """Stream CSV and retain only the three declared benchmark subjects."""
    selected = []
    for chunk in pd.read_csv(path, dtype={key: str}, chunksize=50_000):
        selected.append(chunk.loc[chunk[key].astype(str).isin(SUBJECTS)].copy())
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()


def entry_schedule_key(value: Any) -> str:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)
    return ts.isoformat(timespec="microseconds")


def build_target_regime_context(connection: sqlite3.Connection, candidate_dates: set[str]) -> tuple[dict, dict]:
    """Reuse V2-B's five-stock completed-session proxy; emit contexts only for target stocks."""
    lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    marks = ",".join("?" for _ in CORE_UNIVERSE)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp,close FROM market_kbars "
        f"WHERE stock_code IN ({marks}) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY stock_code,kbar_timestamp",
        (*CORE_UNIVERSE, lower, upper),
    ).fetchall()
    by_day: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for stock, stamp, close in rows:
        stamp = int(stamp)
        local = datetime.fromtimestamp(stamp, TZ)
        if local.strftime("%H:%M") < "09:00" or local.strftime("%H:%M") > "13:25":
            continue
        by_day.setdefault(local.date().isoformat(), {}).setdefault(str(stock), []).append((stamp, float(close)))
    complete_days: list[str] = []
    closes: dict[str, dict[str, float]] = {}
    for day, stock_rows in by_day.items():
        if set(stock_rows) != set(CORE_UNIVERSE) or any(len(stock_rows[s]) != 54 for s in CORE_UNIVERSE):
            continue
        complete_days.append(day)
        closes[day] = {s: stock_rows[s][-1][1] for s in CORE_UNIVERSE}
    complete_days.sort()
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    for target in SUBJECTS:
        peer_codes = [code for code in CORE_UNIVERSE if code != target]
        proxy_close = {day: float(np.mean([closes[day][peer] for peer in peer_codes])) for day in complete_days}
        for date in candidate_dates:
            prior = [day for day in complete_days if day < date][-20:]
            values = [proxy_close[day] for day in prior]
            eligible = len(values) == 20
            contexts[(target, date)] = {
                "eligible": eligible,
                "last_20_proxy_closes": values,
                "history_count": len(values),
                "latest_common_prior_date": prior[-1] if prior else None,
            }
    return contexts, {
        "proxy_rule": "equal-weight completed close of other four Core Universe stocks; target excluded",
        "complete_common_session_count": len(complete_days),
        "context_target_stock_count": len(SUBJECTS),
        "context_target_dates": len(contexts),
        "proxy_only_codes": [c for c in CORE_UNIVERSE if c not in SUBJECTS],
        "proxy_only_outcomes_computed": False,
        "date_bounds_queried": ["2023-01-01", "2024-12-31"],
    }


class TechnicalOnlyV2B(StrategyV2BatchStrategy):
    """Technical V2-B plus fixed Risk V1 sizing, without any ML decision gate."""

    def __init__(self, *args: Any, **kwargs: Any):
        kwargs["ml_model"] = "technical_only"
        kwargs["prediction_lookup"] = {}
        super().__init__(*args, variant="TECH_V2_B", **kwargs)

    def _try_entry(self, code, tick_time, close, snapshot):
        if not self._entry_reasons(close, snapshot):
            return
        aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TZ)
        signal_time = aware.astimezone(TZ)
        if self.candidate_schedule is not None and signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
            return
        self.filter_audit["candidate_checks"] += 1
        context = self.regime_context.get((str(code), signal_time.date().isoformat()))
        if not context or context.get("eligible") is not True:
            if not context:
                self.filter_audit["missing_context"] += 1
            self.filter_audit["rejected"] += 1
            return
        if not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
            self.filter_audit["rejected"] += 1
            return
        equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
        original_quantity = int(tv1.TechnicalV1Strategy._order_quantity(self, close))
        sizing = calculate_risk_size(
            current_equity=equity,
            entry_price=float(close),
            original_quantity=original_quantity,
            stop_loss_pct=float(self.config.stop_loss_pct),
        )
        self.risk_decisions.append({
            "timestamp": signal_time.isoformat(timespec="microseconds"),
            "stock_code": str(code), "fold_id": str(self.fold_id), "model": "technical_only", **sizing,
        })
        if sizing["final_quantity"] <= 0:
            self.record_signal(code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                               price=close, reason=", ".join(self._entry_reasons(close, snapshot)), indicators=snapshot)
            return
        self._risk_quantity_for_entry = int(sizing["final_quantity"])
        try:
            tv1.TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
        finally:
            self._risk_quantity_for_entry = None


def trade_exposure(trades: list[dict[str, Any]], bars: list[dict[str, Any]], initial: float) -> tuple[float, float]:
    ordered = sorted(trades, key=lambda row: int(row["timestamp"]))
    i, cash, qty, exposures = 0, float(initial), 0, []
    for bar in bars:
        now = int(bar["time"])
        while i < len(ordered) and int(ordered[i]["timestamp"]) <= now:
            tr = ordered[i]
            cash += float(tr["net_cash_flow"])
            qty += int(tr["quantity"]) if tr["action"] == "Buy" else -int(tr["quantity"])
            i += 1
        equity = cash + qty * float(bar["close"])
        exposures.append(qty * float(bar["close"]) / equity if equity > 0 else 0.0)
    return (float(np.mean(exposures)) if exposures else 0.0, max(exposures, default=0.0))


def metrics_row(strategy: str, model: str, stock: str, fold: dict[str, Any], app: Any,
                trades: list[dict[str, Any]], bars: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    pnl = bt._build_pnl_rows(trades)
    days = sorted({str(row["datetime"])[:10] for row in bars})
    stats = bt.calculate_performance_metrics(pnl, app.initial_capital, trading_days=days)
    stats.update(bt.calculate_mark_to_market_risk(trades, bars, app.initial_capital))
    avg_exp, max_exp = trade_exposure(trades, bars, float(app.initial_capital))
    return {
        "strategy": strategy, "model": model, "stock_code": stock, "stock_name": SUBJECT_NAMES[stock],
        "fold_id": fold["id"], "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "initial_capital": float(app.initial_capital), "final_equity": float(stats["ending_capital"]),
        "total_return": float(stats["total_return"]), "net_pnl": float(stats["net_pnl"]),
        "gross_pnl": float(stats["gross_pnl"]), "transaction_cost": float(stats["transaction_cost"]),
        "win_rate": float(stats["win_rate"]) if strategy != "BUY_HOLD" else None,
        "profit_factor": float(stats["profit_factor"]) if strategy != "BUY_HOLD" and stats["completed_trades"] else None,
        "max_drawdown": float(stats["max_drawdown"]), "sharpe_ratio": float(stats["sharpe_ratio"]),
        "trade_count": int(stats["completed_trades"]), "average_capital_exposure": avg_exp,
        "maximum_capital_exposure": max_exp, "gross_profit": float(stats["gross_profit"]),
        "gross_loss": float(stats["gross_loss"]), **(extra or {}),
    }


def simulate_technical_only(connection, config, family_name, family, candidate, stock, fold, schedule, context):
    full, evaluation, app, warmup_count, eval_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    strategy = TechnicalOnlyV2B(
        config=app, study_config=config, family_name=family_name, family=family, candidate=candidate,
        data_role="training", fold_id=fold["id"], is_backtest=True, is_simulation=True,
        local_only=True, candidate_schedule=schedule, regime_context=context,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    stats = bt.MarketDataStats(tick_count=full.height, first_timestamp=float(full["kbar_timestamp"][0]),
                               last_timestamp=float(full["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full, stats)
    if strategy.local_position != 0:
        raise HybridPOCError(f"Technical-only position remains open: {stock}/{fold['id']}")
    trades = bt._build_trade_rows(strategy, app)
    bars = bt.build_kbar_rows_from_frame(evaluation, app)
    result = metrics_row("TECHNICAL_ONLY", "ALL", stock, fold, app, trades, bars,
                         {"technical_candidate_checks": strategy.filter_audit["candidate_checks"],
                          "warmup_bar_count": int(warmup_count)})
    return result, trades, strategy.filter_audit


def simulate_buy_hold(connection, config, family_name, family, candidate, stock, fold):
    _, evaluation, app, _, _ = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    bars = bt.build_kbar_rows_from_frame(evaluation, app)
    if not bars:
        raise HybridPOCError(f"No eligible evaluation bars for Buy & Hold: {stock}/{fold['id']}")
    first, last = bars[0], bars[-1]
    capital, buy_price, sell_price = float(app.initial_capital), float(first["open"]), float(last["close"])
    quantity = max(0, math.floor(capital / (buy_price * (1 + app.stock_fee_rate))))
    buy_gross, sell_gross = buy_price * quantity, sell_price * quantity
    buy_fee, sell_fee = buy_gross * app.stock_fee_rate, sell_gross * app.stock_fee_rate
    sell_tax = sell_gross * app.stock_tax_rate
    trades = [
        {"datetime": first["datetime"], "timestamp": int(first["time"]), "code": stock,
         "action": "Buy", "price": buy_price, "quantity": quantity, "gross_amount": buy_gross,
         "fee": buy_fee, "tax": 0.0, "net_cash_flow": -buy_gross - buy_fee},
        {"datetime": last["datetime"], "timestamp": int(last["time"]), "code": stock,
         "action": "Sell", "price": sell_price, "quantity": quantity, "gross_amount": sell_gross,
         "fee": sell_fee, "tax": sell_tax, "net_cash_flow": sell_gross - sell_fee - sell_tax},
    ] if quantity else []
    result = metrics_row("BUY_HOLD", "NONE", stock, fold, app, trades, bars,
                         {"quantity": quantity, "buy_price": buy_price, "sell_price": sell_price,
                          "evaluation_bar_count": len(bars), "trade_description": "single entry/exit; descriptive only"})
    return result


def summarize(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(group_cols, sort=False, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row.update({
            "account_count": len(group), "average_return": group["total_return"].mean(),
            "median_return": group["total_return"].median(), "return_std": group["total_return"].std(ddof=1) if len(group) > 1 else 0.0,
            "worst_max_drawdown": group["max_drawdown"].max(), "average_max_drawdown": group["max_drawdown"].mean(),
            "average_sharpe": group["sharpe_ratio"].mean(), "total_trade_count": int(group["trade_count"].sum()),
            "average_win_rate": pd.to_numeric(group["win_rate"], errors="coerce").mean(),
            "profit_factor": (group["gross_profit"].sum() / abs(group["gross_loss"].sum())) if abs(group["gross_loss"].sum()) > 0 else None,
            "net_pnl_sum": group["net_pnl"].sum(), "gross_pnl_sum": group["gross_pnl"].sum(),
            "transaction_cost_sum": group["transaction_cost"].sum(),
            "average_final_equity": group["final_equity"].mean(),
            "average_capital_exposure": group["average_capital_exposure"].mean(),
            "maximum_capital_exposure": group["maximum_capital_exposure"].max(),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def classify_incremental(technical: pd.DataFrame, hybrid: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    paired = technical.merge(hybrid, on=["stock_code", "fold_id"], suffixes=("_technical", "_hybrid"), validate="one_to_one")
    n = len(paired)
    ret = paired.total_return_hybrid - paired.total_return_technical
    dd = paired.max_drawdown_hybrid - paired.max_drawdown_technical
    sharpe = paired.sharpe_ratio_hybrid - paired.sharpe_ratio_technical
    ret_ok, dd_ok, sharpe_ok = ret.ge(-1e-12), dd.le(1e-12), sharpe.ge(-1e-12)
    strict_stock = set(paired.loc[(ret.gt(1e-12) | dd.lt(-1e-12) | sharpe.gt(1e-12)), "stock_code"])
    strict_fold = set(paired.loc[(ret.gt(1e-12) | dd.lt(-1e-12) | sharpe.gt(1e-12)), "fold_id"])
    stock_return_delta = paired.assign(return_delta=ret).groupby("stock_code")["return_delta"].mean()
    if ret.mean() >= -1e-12 and int(ret_ok.sum()) >= 6 and int(dd_ok.sum()) >= 6 and int(sharpe_ok.sum()) >= 6 and int((stock_return_delta >= -1e-12).sum()) >= 2 and len(strict_stock) >= 2 and len(strict_fold) >= 2:
        label = "CLEARLY_STRONGER"
    elif ret.mean() < -1e-12 and int((dd_ok & sharpe_ok).sum()) >= 6 and len(strict_stock) >= 2 and len(strict_fold) >= 2:
        label = "RISK_ADJUSTED_IMPROVEMENT"
    elif ret.mean() <= 1e-12 and paired.max_drawdown_hybrid.mean() >= paired.max_drawdown_technical.mean() - 1e-12 and paired.sharpe_ratio_hybrid.mean() <= paired.sharpe_ratio_technical.mean() + 1e-12:
        label = "NOT_SUPPORTED"
    else:
        label = "MIXED"
    return label, {
        "paired_cells": n, "mean_return_delta": float(ret.mean()),
        "return_improve_or_tie_cells": int(ret_ok.sum()), "mdd_improve_or_tie_cells": int(dd_ok.sum()),
        "sharpe_improve_or_tie_cells": int(sharpe_ok.sum()), "stocks_with_strict_any_improvement": len(strict_stock),
        "folds_with_strict_any_improvement": len(strict_fold),
    }


def render_html(audit: dict[str, Any], summary: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> str:
    payload = json.dumps(safe({"audit": audit, "summary": summary.to_dict("records"),
                               "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records")}),
                         ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>代表股策略比較</title>
<style>body{{margin:0;background:#081521;color:#eaf3fb;font:16px/1.6 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:24px}}header,.card{{background:#10283a;border:1px solid #29485d;border-radius:16px;padding:20px;margin:14px 0}}header{{background:#10364d}}.tag{{display:inline-block;margin:3px;padding:5px 10px;border-radius:99px;background:#19445e;color:#aad9f7}}select{{padding:9px;background:#071725;color:white;border:1px solid #45667a;border-radius:8px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:right;padding:9px;border-bottom:1px solid #2c485a;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}.scroll{{overflow:auto}}.muted{{color:#aac1d1}}.warn{{border-left:4px solid #e3ac56;padding:10px;background:#342a1b}}@media(max-width:700px){{main{{padding:10px}}}}</style><main><header><span class="tag">Training / Development：2023–2024</span><span class="tag">每個股票與 Fold 各自 10 萬元</span><h1>三檔代表股：買進持有、技術策略與 ML 輔助比較</h1><p>Technical V2-B 與三種 Hybrid 共用同一批合格技術訊號、Risk V1、成本及下一根 5 分 K 開盤成交規則。2379、3034 僅作凍結市場代理資料，沒有計算其個別績效。</p><p>整體數字是 3 檔 × 3 Fold 的獨立帳戶平均，不是投資組合績效。結果屬已看過的 2023–2024 開發資料。</p><label>Hybrid 模型 <select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label></header>
<section class="card"><h2>整體比較</h2><div id="summary" class="scroll"></div></section><section class="card"><h2>依股票比較</h2><div id="stock" class="scroll"></div></section><section class="card"><h2>依時間 Fold 比較</h2><div id="fold" class="scroll"></div></section><section class="card"><h2>Hybrid 相對 Technical 的結果</h2><p id="class"></p><p class="muted">分類同時檢查報酬、最大回撤、Sharpe、3 檔股票和 3 個 Fold 的一致性，不以報酬單一指標選 Winner。</p><div class="warn">這是歷史研究比較，不代表未來報酬，也不選出最終冠軍。</div></section><section class="card"><details><summary>研究稽核細節</summary><pre id="audit"></pre></details></section></main>
<script>const D=__DATA__;const names={{BUY_HOLD:"買進持有",TECHNICAL_ONLY:"Technical V2-B",HYBRID_RF:"Technical + RF",HYBRID_XGB:"Technical + XGBoost",HYBRID_GRU:"Technical + GRU"}};const f=(x)=>x==null||!Number.isFinite(Number(x))?"—":(Number(x)*100).toFixed(2)+"%";const n=(x,d=2)=>x==null||!Number.isFinite(Number(x))?"—":Number(x).toFixed(d);function table(el,rows){{const cols=[["策略",r=>names[r.strategy]||r.strategy],["股票",r=>r.stock_code||"三檔平均"],["Fold",r=>r.fold_id||"3 Fold 平均"],["報酬",r=>f(r.average_return??r.total_return)],["最差回撤",r=>f(r.worst_max_drawdown??r.max_drawdown)],["Sharpe",r=>n(r.average_sharpe??r.sharpe_ratio)],["勝率",r=>f(r.average_win_rate??r.win_rate)],["Profit Factor",r=>n(r.profit_factor)],["交易次數",r=>r.total_trade_count??r.trade_count],["Net PnL",r=>n(r.net_pnl_sum??r.net_pnl,0)],["成本",r=>n(r.transaction_cost_sum??r.transaction_cost,0)],["平均曝險",r=>f(r.average_capital_exposure)]];el.innerHTML="<table><thead><tr>"+cols.map(c=>"<th>"+c[0]+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+cols.map(c=>"<td>"+(c[1](r)??"—")+"</td>").join("")+"</tr>").join("")+"</tbody></table>"}}function draw(){{const m=document.querySelector('#model').value;const model=m==='random_forest'?'RF':m==='xgboost'?'XGB':'GRU';const rows=D.summary.filter(r=>r.model==='NONE'||r.model==='ALL'||r.model===m);table(document.querySelector('#summary'),rows);table(document.querySelector('#stock'),D.per_stock.filter(r=>r.model==='NONE'||r.model==='ALL'||r.model===m));table(document.querySelector('#fold'),D.per_fold.filter(r=>r.model==='NONE'||r.model==='ALL'||r.model===m));document.querySelector('#class').textContent='目前選擇模型 '+model+'：'+D.audit.hybrid_classifications[m];document.querySelector('#audit').textContent=JSON.stringify(D.audit,null,2)}}document.querySelector('#model').addEventListener('change',draw);draw();</script></html>'''.replace("__DATA__", payload)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Check frozen inputs and alignment without calculating performance")
    mode.add_argument("--execute", action="store_true", help="Execute the one preregistered fixed benchmark")
    args = parser.parse_args()
    prereg_text = PREREG.read_text(encoding="utf-8")
    required_spec_tokens = ["PREREGISTERED", "2330", "2454", "2303", "Technical V2-B", "2379", "3034"]
    if any(token not in prereg_text for token in required_spec_tokens):
        raise HybridPOCError("Benchmark preregistration is incomplete or changed")
    risk_cfg = json.loads(RISK_CONFIG.read_text(encoding="utf-8"))
    validate_risk_config(risk_cfg)
    poc = read_json(POC_CONFIG_PATH)
    config_path = ROOT / poc["technical"]["config"]
    config = tv1.read_config(config_path)
    tv1.assert_execution_ready(config, "training")
    folds = config["walk_forward_folds"]
    if tuple(f["id"] for f in folds) != FOLD_IDS:
        raise HybridPOCError("Frozen three-fold schedule differs from preregistration")
    for fold in folds:
        if fold["evaluate_start"] < "2023-01-01" or fold["evaluate_end"] > "2024-12-31":
            raise HybridPOCError("Fold escaped the allowed 2023-2024 data window")
    _, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical Training preflight is not ready or queried forbidden roles")

    common_audit_path = COMMON_RUN / "common_coverage_audit.json"
    common_audit = read_json(common_audit_path)
    if common_audit.get("status") != "PASS" or common_audit.get("training_only") is not True:
        raise HybridPOCError("Common Coverage audit is not PASS / Training-only")
    cand_path, exclusion_path = COMMON_RUN / "common_coverage_candidates.csv", COMMON_RUN / "common_coverage_exclusions.csv"
    candidates, exclusions = read_subject_rows(cand_path), read_subject_rows(exclusion_path)
    if candidates.empty or not candidates.stock_code.astype(str).isin(SUBJECTS).all():
        raise HybridPOCError("Filtered Common Coverage candidate rows are empty or include an undeclared subject")
    if candidates.duplicated(["stock_code", "fold_id", "signal_time", "earliest_entry_time"]).any():
        raise HybridPOCError("Duplicate Common Coverage candidate identity")
    validate_training_only_dates(candidates["signal_time"])
    if set(candidates["candidate_id"].astype(str)) != {"BR_SENS_RANGE55_BB20_2"} or set(candidates["data_role"].astype(str)) != {"training"}:
        raise HybridPOCError("Common Coverage does not match the frozen candidate/data role")
    if set(candidates["stock_code"].astype(str)) != set(SUBJECTS):
        raise HybridPOCError("All three preregistered subjects must be represented")

    oof_path = POC_RUN / "ml_v1_baseline_oof_predictions.csv"
    oof = read_subject_rows(oof_path)
    oof_audit = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=SUBJECTS,
                                         period_start="2023-01-01", period_end="2024-12-31")
    if oof_audit.get("forbidden_roles_used") or oof_audit.get("in_sample_predictions_used"):
        raise HybridPOCError("Filtered OOF data have forbidden/in-sample provenance")
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    schedules = {(str(fold), str(stock)): {entry_schedule_key(t) for t in group["earliest_entry_time"]}
                 for (fold, stock), group in candidates.groupby(["fold_id", "stock_code"])}
    alignment_checks = {model: 0 for model in MODELS}
    for row in candidates.to_dict("records"):
        stock, candidate_day = str(row["stock_code"]), str(row["signal_time"])[:10]
        expected_prior = str(row["expected_previous_trading_day"])[:10]
        for model in MODELS:
            prediction = lookups[model].get((stock, candidate_day))
            if prediction is None or str(prediction["signal_date"])[:10] != expected_prior:
                raise HybridPOCError(f"OOF previous-session alignment mismatch: {model}/{stock}/{candidate_day}")
            if str(prediction["entry_date"])[:10] != candidate_day:
                raise HybridPOCError(f"OOF effective-date mismatch: {model}/{stock}/{candidate_day}")
            alignment_checks[model] += 1
    candidate_dates = set(pd.to_datetime(candidates["signal_time"]).dt.strftime("%Y-%m-%d"))

    database_path = tv1.resolve_market_data_path(ROOT, config)
    db_hash_before = sha256_file(database_path)
    immutable_paths = [common_audit_path, cand_path, exclusion_path, oof_path,
                       TECH_BATCH / "technical_v2_results.csv", RISK_CONFIG, config_path, PREREG]
    immutable_hashes = {str(p.relative_to(ROOT)): sha256_file(p) for p in immutable_paths}
    immutable_dirs = {str(p.relative_to(ROOT)): directory_hashes(p) for p in (COMMON_RUN, POC_RUN, TECH_BATCH)}
    family_name, family, candidate = tv1.candidate_by_id(config, "BR_SENS_RANGE55_BB20_2")
    if family_name != "breakout":
        raise HybridPOCError("Frozen V2 candidate family changed")
    prior = read_subject_rows(TECH_BATCH / "technical_v2_results.csv")
    if not {"version", "model", "stock_code", "fold_id"}.issubset(prior.columns):
        raise HybridPOCError("Existing V2-B reproduction file schema is incomplete")
    prior = prior.loc[(prior.version == "TECH_V2_B") & prior.model.astype(str).isin(MODELS)]
    if len(prior) != len(SUBJECTS) * len(FOLD_IDS) * len(MODELS):
        raise HybridPOCError("Existing V2-B comparison does not contain the exact target/model/fold matrix")
    with tv1._readonly_connection(database_path) as connection:
        context, regime_audit = build_target_regime_context(connection, candidate_dates)
    if set(stock for stock, _ in context) != set(SUBJECTS):
        raise HybridPOCError("V2-B context emitted a non-subject stock context")
    if any(value.get("history_count") != 20 for value in context.values()):
        raise HybridPOCError("A candidate context lacks the frozen prior 20 common sessions")
    if args.preflight:
        print(json.dumps({
            "status": "PREFLIGHT_PASS", "performance_calculated": False,
            "subjects": list(SUBJECTS), "proxy_only_codes": [c for c in CORE_UNIVERSE if c not in SUBJECTS],
            "proxy_only_outcomes_computed": False, "target_candidates": len(candidates),
            "target_exclusions": len(exclusions), "oof_alignment_checks": alignment_checks,
            "candidate_counts_by_stock": candidates.groupby("stock_code").size().to_dict(),
            "candidate_counts_by_fold": candidates.groupby("fold_id").size().to_dict(),
            "regime_context_audit": regime_audit, "database_path": str(database_path.relative_to(ROOT)),
            "database_sha256": db_hash_before, "period": ["2023-01-01", "2024-12-31"],
        }, ensure_ascii=False, indent=2))
        return 0
    output = ROOT / "exports" / f"representative_benchmark_exp001_{datetime.now().strftime('%Y%m%dT%H%M%S%f%z')}"
    output.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    filter_audits: dict[str, Any] = {}
    with tv1._readonly_connection(database_path) as connection:
        for fold in folds:
            for stock in SUBJECTS:
                schedule = schedules.get((fold["id"], stock), set())
                bh = simulate_buy_hold(connection, config, family_name, family, candidate, stock, fold)
                results.append(bh)
                technical, tech_trades, filter_audit = simulate_technical_only(
                    connection, config, family_name, family, candidate, stock, fold, schedule, context)
                results.append(technical)
                trade_rows.extend({"strategy": "TECHNICAL_ONLY", "model": "ALL", "stock_code": stock,
                                   "fold_id": fold["id"], **tr} for tr in tech_trades)
                filter_audits[f"TECHNICAL_ONLY/{stock}/{fold['id']}"] = filter_audit
                for model in MODELS:
                    run_result, trades, gates, risks, local_filter = simulate_one(
                        connection=connection, config=config, family_name=family_name, family=family,
                        candidate=candidate, stock=stock, fold=fold, version="TECH_V2_B", model=model,
                        lookup=lookups[model], schedule=schedule, volume_context={}, regime_context=context)
                    _, eval_frame, app, _, _ = tv1._load_fold_frame(connection, config, candidate, stock, fold)
                    bars = bt.build_kbar_rows_from_frame(eval_frame, app)
                    hybrid_row = {
                        "strategy": f"HYBRID_{'RF' if model == 'random_forest' else 'XGB' if model == 'xgboost' else 'GRU'}",
                        "model": model, "stock_code": stock, "stock_name": SUBJECT_NAMES[stock], "fold_id": fold["id"],
                        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
                        "initial_capital": run_result["initial_capital"], "final_equity": run_result["final_equity"],
                        "total_return": run_result["total_return"], "net_pnl": run_result["net_pnl"],
                        "gross_pnl": run_result["gross_pnl"], "transaction_cost": run_result["transaction_cost"],
                        "win_rate": run_result["win_rate"] if run_result["trade_count"] else None,
                        "profit_factor": run_result["profit_factor"] if run_result["trade_count"] else None,
                        "max_drawdown": run_result["max_drawdown"], "sharpe_ratio": run_result["sharpe_ratio"],
                        "trade_count": run_result["trade_count"],
                        "average_capital_exposure": trade_exposure(trades, bars, run_result["initial_capital"])[0],
                        "maximum_capital_exposure": trade_exposure(trades, bars, run_result["initial_capital"])[1],
                        "gross_profit": float(sum(float(x["net_pnl"]) for x in bt._build_pnl_rows(trades) if float(x["net_pnl"]) > 0)),
                        "gross_loss": float(sum(float(x["net_pnl"]) for x in bt._build_pnl_rows(trades) if float(x["net_pnl"]) < 0)),
                        "technical_entry_count": int(run_result["technical_entry_count"]),
                        "hybrid_decision_count": len(gates), "risk_decision_count": len(risks),
                    }
                    results.append(hybrid_row)
                    trade_rows.extend({"strategy": hybrid_row["strategy"], "model": model, "stock_code": stock,
                                       "fold_id": fold["id"], **tr} for tr in trades)

    result_frame = pd.DataFrame(results)
    expected_cells = 3 * 3 * 5
    if len(result_frame) != expected_cells or set(result_frame.stock_code.astype(str)) != set(SUBJECTS):
        raise HybridPOCError(f"Expected 45 target-only benchmark cells; got {len(result_frame)}")
    if result_frame.duplicated(["strategy", "model", "stock_code", "fold_id"]).any():
        raise HybridPOCError("Duplicate benchmark stock-fold arm")

    hybrid_classes: dict[str, str] = {}
    incremental_rows = []
    tech = result_frame.loc[result_frame.strategy == "TECHNICAL_ONLY", ["stock_code", "fold_id", "total_return", "max_drawdown", "sharpe_ratio"]]
    for model, code in zip(MODELS, ("RF", "XGB", "GRU")):
        arm = result_frame.loc[result_frame.model == model]
        label, evidence = classify_incremental(tech, arm[["stock_code", "fold_id", "total_return", "max_drawdown", "sharpe_ratio"]])
        hybrid_classes[model] = label
        incremental_rows.append({"model": model, "hybrid_strategy": f"HYBRID_{code}", "classification": label, **evidence})

    summary = summarize(result_frame, ["strategy", "model"])
    per_stock = summarize(result_frame, ["strategy", "model", "stock_code", "stock_name"])
    per_fold = summarize(result_frame, ["strategy", "model", "fold_id"])
    bh_frame = result_frame.loc[result_frame.strategy == "BUY_HOLD"].copy()
    common_by = candidates.groupby(["stock_code", "fold_id"], as_index=False).size().rename(columns={"size": "common_candidate_count"})
    excl_by = exclusions.groupby(["stock_code", "fold_id"], as_index=False).size().rename(columns={"size": "common_coverage_excluded_count"})
    coverage = common_by.merge(excl_by, on=["stock_code", "fold_id"], how="outer").fillna(0)
    coverage["common_candidate_count"] = coverage["common_candidate_count"].astype(int)
    coverage["common_coverage_excluded_count"] = coverage["common_coverage_excluded_count"].astype(int)
    coverage["excluded_reason_summary"] = ""
    for idx, row in coverage.iterrows():
        subset = exclusions.loc[(exclusions.stock_code == row.stock_code) & (exclusions.fold_id == row.fold_id)]
        coverage.loc[idx, "excluded_reason_summary"] = "; ".join(sorted(set(subset.exclusion_reason.astype(str))))
    coverage["eligibility"] = "COMMON_COVERAGE; SAME_SCHEDULE_FOR_ALL_ARMS"

    reproduced = []
    for model in MODELS:
        new = result_frame.loc[result_frame.model == model].set_index(["stock_code", "fold_id"]).sort_index()
        old_model = prior.loc[prior.model == model].set_index(["stock_code", "fold_id"]).sort_index()
        if not new.index.equals(old_model.index):
            raise HybridPOCError(f"Existing TECH_V2_B rows do not align for {model}")
        checks = {}
        for field, old_field in (("total_return", "total_return"), ("max_drawdown", "max_drawdown"),
                                 ("sharpe_ratio", "sharpe_ratio"), ("trade_count", "trade_count"),
                                 ("net_pnl", "net_pnl"), ("transaction_cost", "transaction_cost")):
            delta = (pd.to_numeric(new[field]) - pd.to_numeric(old_model[old_field])).abs()
            checks[field] = {"max_abs_diff": float(delta.max()), "match": bool(delta.max() <= 1e-8)}
        if not all(v["match"] for v in checks.values()):
            raise HybridPOCError(f"Target-only Hybrid V2-B reproduction failed for {model}: {checks}")
        reproduced.append({"model": model, "status": "PASS", "metrics": checks})

    if sha256_file(database_path) != db_hash_before:
        raise HybridPOCError("Read-only source database hash changed during benchmark")
    immutable_changed = [p for p, h in immutable_hashes.items() if sha256_file(ROOT / p) != h]
    if immutable_changed:
        raise HybridPOCError(f"Frozen inputs changed during execution: {immutable_changed}")
    changed_dirs = [p for p, h in immutable_dirs.items() if directory_hashes(ROOT / p) != h]
    if changed_dirs:
        raise HybridPOCError(f"Frozen source export changed during execution: {changed_dirs}")

    fill_bars: dict[tuple[str, int], float] = {}
    with tv1._readonly_connection(database_path) as fill_connection:
        for fold in folds:
            for stock in SUBJECTS:
                _, evaluation, app, _, _ = tv1._load_fold_frame(
                    fill_connection, config, candidate, stock, fold)
                for bar in bt.build_kbar_rows_from_frame(evaluation, app):
                    fill_bars[(stock, int(bar["time"]))] = float(bar["open"])
    from hybrid.core.risk_v2_exp001 import execution_fill_check
    fill_audit = execution_fill_check(trade_rows, fill_bars)
    if fill_audit.get("status") != "PASS":
        raise HybridPOCError(f"Next-bar-open execution validation failed: {fill_audit}")

    classification_text = "; ".join(f"{m}={c}" for m, c in hybrid_classes.items())
    audit = {
        "status": "COMPLETED", "experiment_id": "strategy-representative-benchmark-exp001",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_role": "EXPOSED_TRAINING_DEVELOPMENT", "period": {"start": "2023-01-01", "end": "2024-12-31"},
        "subjects": [{"stock_code": c, "stock_name": SUBJECT_NAMES[c]} for c in SUBJECTS],
        "proxy_only_codes": [c for c in CORE_UNIVERSE if c not in SUBJECTS],
        "proxy_only_use_authorized": True, "proxy_only_individual_metrics_computed": False,
        "proxy_only_performance_used": False, "technical_candidate": "BR_SENS_RANGE55_BB20_2",
        "technical_variant": "TECH_V2_B_MARKET_REGIME_FILTER", "models": list(MODELS),
        "folds": [{k: f[k] for k in ("id", "train_start", "train_end", "evaluate_start", "evaluate_end")} for f in folds],
        "common_coverage_status": "PASS", "common_coverage_candidate_count": len(candidates),
        "common_coverage_exclusion_count": len(exclusions),
        "coverage_exclusion_reasons": exclusions.exclusion_reason.value_counts().to_dict(),
        "walk_forward_account_count": 9, "result_row_count": len(result_frame),
        "technical_only_candidate_schedule_shared_with_hybrid": True,
        "execution_contract": "completed_signal_kbar_to_next_tradable_5min_bar_open",
        "risk_v1": {k: risk_cfg[k] for k in ("risk_per_trade", "max_position_fraction", "stop_loss_pct", "quantity_rule")},
        "buy_hold_quantity_rule": "maximum affordable whole-share quantity after purchase commission; no 1,000-share cap",
        "transaction_cost_source": "configs/technical_v1_research.json; buy/sell commission and sell tax",
        "regime_context_audit": regime_audit, "technical_only_filter_audits": filter_audits,
        "hybrid_classifications": hybrid_classes, "hybrid_incremental_evidence": incremental_rows,
        "hybrid_v2b_reproduction_against_frozen_batch": reproduced,
        "execution_fill_audit": fill_audit,
        "overall_aggregation": "arithmetic mean across independent stock-fold accounts; not portfolio return",
        "forbidden_access": {"2025_used": False, "2026_used": False, "final_oos_used": False,
                             "controlled_validation_used": False, "additional_holdout_used": False},
        "research_actions": {"parameter_search_performed": False, "ml_retrained": False,
                             "technical_changed": False, "hybrid_gate_changed": False,
                             "risk_parameters_changed": False, "portfolio_enabled": False,
                             "short_implemented": False, "v3_started": False},
        "database_path": str(database_path.relative_to(ROOT)), "database_sha256_before": db_hash_before,
        "database_sha256_after": sha256_file(database_path), "source_hashes": immutable_hashes,
        "source_export_hashes_unchanged": True, "output_directory": str(output.relative_to(ROOT)),
        "classification_rule": PREREG.relative_to(ROOT).as_posix(), "interpretation": classification_text,
    }
    summary.to_csv(output / "benchmark_summary.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "benchmark_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "benchmark_per_fold.csv", index=False, encoding="utf-8-sig")
    bh_frame.to_csv(output / "benchmark_buy_hold.csv", index=False, encoding="utf-8-sig")
    coverage.to_csv(output / "benchmark_common_coverage.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(incremental_rows).to_csv(output / "benchmark_incremental_value.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trade_rows).to_csv(output / "benchmark_trades.csv", index=False, encoding="utf-8-sig")
    result_frame.to_csv(output / "benchmark_account_results.csv", index=False, encoding="utf-8-sig")
    write_json(output / "benchmark_audit.json", audit)
    (output / "benchmark_report.html").write_text(render_html(audit, summary, per_stock, per_fold), encoding="utf-8")
    (output / "benchmark_user_analysis.html").write_text(render_html(audit, summary, per_stock, per_fold), encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "output": str(output.relative_to(ROOT)),
                      "summary_rows": len(result_frame), "coverage_candidates": len(candidates),
                      "coverage_exclusions": len(exclusions), "hybrid_classifications": hybrid_classes,
                      "execution_fill_audit": fill_audit,
                      "proxy_only_performance_used": False, "forbidden_roles_used": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
