"""Pair Exit 30 vs Exit 40 completed trades from Exit Sensitivity Exp001."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.scripts.run_hybrid_common_coverage import POC_CONFIG_PATH, read_json  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402


DEFAULT_RUN = ROOT / "exports/strategy_exit_sensitivity_exp001_20261027T194032701720+0800"


def make_round_trips(ledger: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_cols = ["exit_value", "strategy", "stock_code", "fold_id"]
    for key, group in ledger.groupby(group_cols, sort=True):
        exit_value, strategy, stock_code, fold_id = key
        group = group.sort_values("timestamp")
        buys = group.loc[group.action == "Buy"].reset_index(drop=True)
        sells = group.loc[group.action == "Sell"].reset_index(drop=True)
        if len(buys) != len(sells):
            raise ValueError(f"Unpaired execution ledger rows: {key}: buys={len(buys)} sells={len(sells)}")
        for index, (buy, sell) in enumerate(zip(buys.to_dict("records"), sells.to_dict("records")), 1):
            if int(buy["quantity"]) != int(sell["quantity"]):
                raise ValueError(f"Quantity mismatch: {key} trade {index}")
            net_pnl = float(buy["net_cash_flow"]) + float(sell["net_cash_flow"])
            notional = float(buy["gross_amount"])
            rows.append({
                "exit_value": int(exit_value), "strategy": strategy, "stock_code": str(stock_code),
                "fold_id": fold_id, "trade_number": index,
                "entry_timestamp": int(buy["timestamp"]), "exit_timestamp": int(sell["timestamp"]),
                "entry_time": buy["datetime"], "exit_time": sell["datetime"],
                "entry_price": float(buy["price"]), "exit_price": float(sell["price"]),
                "quantity": int(buy["quantity"]), "holding_minutes": (int(sell["timestamp"])-int(buy["timestamp"]))/60,
                "net_pnl": net_pnl, "gross_pnl": (float(sell["price"])-float(buy["price"])) * int(buy["quantity"]),
                "fees_tax": float(buy["fee"])+float(buy["tax"])+float(sell["fee"])+float(sell["tax"]),
                "net_return_pct": net_pnl / notional * 100 if notional else None,
                "gross_return_pct": (float(sell["price"])/float(buy["price"])-1) * 100 if float(buy["price"]) else None,
                "entry_notional": notional,
            })
    return pd.DataFrame(rows)


def add_excursions(trades: pd.DataFrame) -> pd.DataFrame:
    poc = read_json(POC_CONFIG_PATH)
    config = tv1.read_config(ROOT / poc["technical"]["config"])
    _, _, candidate = tv1.candidate_by_id(config, "BR_SENS_RANGE55_BB20_2")
    database = tv1.resolve_market_data_path(ROOT, config)
    out = trades.copy()
    out["mfe_pct"] = pd.NA
    out["mae_pct"] = pd.NA
    out["giveback_pct"] = pd.NA
    for (stock, fold_id), indexes in out.groupby(["stock_code", "fold_id"]).groups.items():
        fold = next(item for item in config["walk_forward_folds"] if item["id"] == fold_id)
        with tv1._readonly_connection(database) as connection:
            full, _, _, _, _ = tv1._load_fold_frame(connection, config, candidate, str(stock), fold)
        bars = pd.DataFrame({
            "timestamp": full["kbar_timestamp"].to_list(),
            "high": full["High"].to_list(),
            "low": full["Low"].to_list(),
        })
        bars["timestamp"] = bars.timestamp.astype("int64")
        for idx in indexes:
            trade = out.loc[idx]
            window = bars.loc[(bars.timestamp >= int(trade.entry_timestamp)) &
                              (bars.timestamp < int(trade.exit_timestamp))]
            if window.empty:
                continue
            entry = float(trade.entry_price)
            mfe = (float(window.high.max()) / entry - 1) * 100
            mae = (float(window.low.min()) / entry - 1) * 100
            out.loc[idx, ["mfe_pct", "mae_pct", "giveback_pct"]] = [mfe, mae, mfe-float(trade.gross_return_pct)]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    args = parser.parse_args()
    ledger_path = args.run / "exit_sensitivity_trade_ledger.csv"
    ledger = pd.read_csv(ledger_path, dtype={"stock_code": str})
    trades = add_excursions(make_round_trips(ledger))
    left = trades.loc[trades.exit_value == 30].copy()
    right = trades.loc[trades.exit_value == 40].copy()
    keys = ["strategy", "stock_code", "fold_id", "entry_timestamp"]
    paired = left.merge(right, on=keys, how="outer", suffixes=("_30", "_40"), indicator=True)
    paired["pair_status"] = paired["_merge"].map({"both": "MATCHED_ENTRY", "left_only": "EXIT30_ONLY_ENTRY", "right_only": "EXIT40_ONLY_ENTRY"})
    paired["net_pnl_delta_40_minus_30"] = paired.net_pnl_40 - paired.net_pnl_30
    paired["net_return_delta_pp_40_minus_30"] = paired.net_return_pct_40 - paired.net_return_pct_30
    paired["holding_minutes_delta_40_minus_30"] = paired.holding_minutes_40 - paired.holding_minutes_30
    paired["mfe_delta_pp_40_minus_30"] = paired.mfe_pct_40 - paired.mfe_pct_30
    paired["mae_delta_pp_40_minus_30"] = paired.mae_pct_40 - paired.mae_pct_30
    paired["giveback_delta_pp_40_minus_30"] = paired.giveback_pct_40 - paired.giveback_pct_30
    paired["exit_price_delta_40_minus_30"] = paired.exit_price_40 - paired.exit_price_30
    paired.drop(columns="_merge", inplace=True)

    summaries = []
    for strategy in sorted(trades.strategy.unique()):
        group = paired.loc[paired.strategy == strategy]
        matched = group.loc[group.pair_status == "MATCHED_ENTRY"]
        summaries.append({
            "strategy": strategy,
            "exit30_trades": int((left.strategy == strategy).sum()),
            "exit40_trades": int((right.strategy == strategy).sum()),
            "matched_entries": len(matched),
            "exit30_only_entries": int((group.pair_status == "EXIT30_ONLY_ENTRY").sum()),
            "exit40_only_entries": int((group.pair_status == "EXIT40_ONLY_ENTRY").sum()),
            "matched_exit30_net_pnl": matched.net_pnl_30.sum(),
            "matched_exit40_net_pnl": matched.net_pnl_40.sum(),
            "matched_net_pnl_delta": matched.net_pnl_delta_40_minus_30.sum(),
            "mean_matched_return_delta_pp": matched.net_return_delta_pp_40_minus_30.mean(),
            "mean_holding_minutes_delta": matched.holding_minutes_delta_40_minus_30.mean(),
            "exit40_later_count": int((matched.holding_minutes_delta_40_minus_30 > 0).sum()),
            "same_exit_time_count": int((matched.holding_minutes_delta_40_minus_30 == 0).sum()),
            "mean_mfe_delta_pp": matched.mfe_delta_pp_40_minus_30.mean(),
            "mean_mae_delta_pp": matched.mae_delta_pp_40_minus_30.mean(),
            "mean_giveback_delta_pp": matched.giveback_delta_pp_40_minus_30.mean(),
        })
    summary = pd.DataFrame(summaries)
    out = ROOT / "exports" / f"exit_30_40_trade_analysis_{pd.Timestamp.now(tz='Asia/Taipei').strftime('%Y%m%dT%H%M%S%z')}"
    out.mkdir(parents=True, exist_ok=False)
    trades.to_csv(out / "round_trip_ledger_with_excursions.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(out / "exit30_exit40_paired_trades.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out / "exit30_exit40_trade_summary.csv", index=False, encoding="utf-8-sig")
    lines = [
        "# Exit 30 vs Exit 40 逐筆交易診斷", "",
        "來源：Exit Sensitivity Exp001 交易執行帳本；股票 2330、2303、2454；2023–2024 暴露開發資料。",
        "逐筆配對鍵為策略、股票、Fold 與相同進場成交時間。報酬已含當筆買賣費用與證交稅。",
        "MFE/MAE 由進場成交時間起至出場成交時間前的 5 分 K 高低價計算；出場成交 K 棒之後的價格不計。",
        "原始成交帳本沒有策略出場原因欄位，因此本分析不推測 stop、技術訊號或持倉期限是哪個規則觸發。", "",
        "## 匹配交易摘要", "",
        summary.to_markdown(index=False, floatfmt=".3f"), "",
        "## 解讀限制", "",
        "本配對只包含 Exit 30 與 Exit 40 都有相同進場成交時間的交易；兩種 Exit 因持倉／冷卻而新增或消失的進場，另列為未配對。MFE/MAE 是觀察區間內價格極值，不等於可實現收益。此診斷不改策略參數，也不是獨立 OOS 驗證。", "",
    ]
    (out / "exit30_exit40_trade_analysis.md").write_text("\n".join(lines), encoding="utf-8")
    summary_view = summary.rename(columns={
        "strategy": "策略", "exit30_trades": "Exit 30 筆數", "exit40_trades": "Exit 40 筆數",
        "matched_entries": "相同進場配對數", "exit30_only_entries": "僅 Exit 30 進場",
        "exit40_only_entries": "僅 Exit 40 進場", "matched_net_pnl_delta": "配對淨損益差（40−30）",
        "mean_matched_return_delta_pp": "平均單筆報酬差（百分點）",
        "mean_holding_minutes_delta": "平均持有時間差（分鐘）", "exit40_later_count": "Exit 40 較晚出場筆數",
        "same_exit_time_count": "相同出場時間筆數", "mean_mfe_delta_pp": "MFE 差（百分點）",
        "mean_mae_delta_pp": "MAE 差（百分點）", "mean_giveback_delta_pp": "獲利回吐差（百分點）",
    })[["策略", "Exit 30 筆數", "Exit 40 筆數", "相同進場配對數", "僅 Exit 30 進場", "僅 Exit 40 進場",
        "配對淨損益差（40−30）", "平均單筆報酬差（百分點）", "Exit 40 較晚出場筆數", "相同出場時間筆數",
        "MFE 差（百分點）", "MAE 差（百分點）", "獲利回吐差（百分點）"]]
    detail_cols = ["strategy", "stock_code", "fold_id", "pair_status", "entry_time_30", "exit_time_30",
        "exit_time_40", "net_return_pct_30", "net_return_pct_40", "net_return_delta_pp_40_minus_30",
        "holding_minutes_delta_40_minus_30", "mfe_pct_30", "mfe_pct_40", "mae_pct_30", "mae_pct_40",
        "giveback_pct_30", "giveback_pct_40"]
    detail_view = paired[detail_cols].rename(columns={
        "strategy":"策略", "stock_code":"股票", "fold_id":"Fold", "pair_status":"配對狀態",
        "entry_time_30":"進場時間", "exit_time_30":"Exit 30 出場", "exit_time_40":"Exit 40 出場",
        "net_return_pct_30":"Exit 30 淨報酬%", "net_return_pct_40":"Exit 40 淨報酬%",
        "net_return_delta_pp_40_minus_30":"報酬差百分點", "holding_minutes_delta_40_minus_30":"持有時間差分鐘",
        "mfe_pct_30":"Exit 30 MFE%", "mfe_pct_40":"Exit 40 MFE%", "mae_pct_30":"Exit 30 MAE%",
        "mae_pct_40":"Exit 40 MAE%", "giveback_pct_30":"Exit 30 回吐%", "giveback_pct_40":"Exit 40 回吐%",
    })
    html_doc = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Exit 30 vs Exit 40 逐筆診斷</title><style>body{{font:15px/1.6 system-ui,"Microsoft JhengHei",sans-serif;background:#07141f;color:#edf5fb;margin:0}}main{{max-width:1400px;margin:auto;padding:24px}}header,section{{background:#102536;border:1px solid #29485d;border-radius:14px;padding:20px;margin:16px 0}}h1,h2{{color:#fff}}.muted{{color:#abc0d0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:7px;border-bottom:1px solid #29485d;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}.warning{{color:#ffd98b}}</style><main><header><h1>Exit 30 vs Exit 40 逐筆交易診斷</h1><p>2330、2303、2454｜2023–2024 開發資料｜相同進場時間逐筆配對</p><p class="muted">淨報酬含手續費與證交稅。MFE／MAE 由進場成交到出場成交前的 5 分 K 高低價計算。</p></header><section><h2>配對摘要</h2><div class="scroll">{summary_view.to_html(index=False, border=0, float_format=lambda x:f"{x:.3f}")}</div></section><section><h2>逐筆配對</h2><div class="scroll">{detail_view.to_html(index=False, border=0, float_format=lambda x:f"{x:.3f}")}</div></section><section><h2>解讀限制</h2><p class="warning">Exit 30 與 Exit 40 的總報酬方向不一致，仍維持 MIXED；此分析不改策略參數。</p><p>未配對的進場代表兩個 Exit 造成可交易時點不同。原始成交帳本未記錄策略出場觸發原因，因此無法可靠地逐筆判斷是技術出場、停損或持倉期限觸發；MFE／MAE 也只是期間內價格極值，不代表可成交收益。</p><p>本資料屬於已曝光開發資料，不是獨立 OOS 驗證。</p></section></main></html>'''
    (out / "exit30_exit40_trade_analysis.html").write_text(html_doc, encoding="utf-8")
    print(f"output={out.relative_to(ROOT).as_posix()}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
