"""Summarize the existing 2023-2024 Technical V2-B 2454 run by half-year.

This is a read-only analysis of an already completed export. It does not load
market data, rerun a backtest, or modify the frozen strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "exports/technical_v2b_cross_stock_exp001_20261104T201723+0800"
PERIODS = [
    ("2023_H1", "2023-01-01", "2023-06-30"),
    ("2023_H2", "2023-07-01", "2023-12-31"),
    ("2024_H1", "2024-01-01", "2024-06-30"),
    ("2024_H2", "2024-07-01", "2024-12-31"),
]
SLIPPAGE_RATES = (0.0, 0.0005, 0.001, 0.002)  # 0, 5, 10, 20 bps per side


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def period_for(value: pd.Timestamp) -> str | None:
    value = pd.Timestamp(value)
    for name, start, end in PERIODS:
        if pd.Timestamp(start) <= value.normalize() <= pd.Timestamp(end):
            return name
    return None


def pf(values: pd.Series) -> float | None:
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return wins / losses if losses > 0 else (None if wins == 0 else float("inf"))


def max_consecutive_losses(values: pd.Series) -> int:
    best = current = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def slippage_net(trades: pd.DataFrame, rate: float) -> pd.Series:
    qty = trades["quantity"].astype(float)
    buy = trades["buy_amount"].astype(float)
    sell = trades["sell_amount"].astype(float)
    buy_fee = trades["buy_fee"].astype(float)
    sell_fee = trades["sell_fee"].astype(float)
    tax = trades["transaction_tax"].astype(float)
    slipped_buy = buy * (1 + rate)
    slipped_sell = sell * (1 - rate)
    gross = slipped_sell - slipped_buy
    fees = buy_fee * (1 + rate) + sell_fee * (1 - rate)
    slipped_tax = tax * (1 - rate)
    return gross - fees - slipped_tax


def summarize(source: Path, output: Path) -> None:
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    if audit.get("requested_period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise ValueError("Source export period is not the authorized 2023-2024 period")
    if audit.get("data_role") != "historical_development" or audit.get("forbidden_periods_used") is not False:
        raise ValueError("Source export does not meet historical-development guardrails")
    if audit.get("frozen_candidate") != "BR_SENS_RANGE55_BB20_2":
        raise ValueError("Source export is not the frozen Technical V2-B candidate")

    trades_path = source / "trades.csv"
    curves_path = source / "equity_curves.csv"
    trades_all = pd.read_csv(trades_path, dtype={"stock_code": str})
    trades = trades_all.loc[
        (trades_all["stock_code"] == "2454")
        & (trades_all["variant"] == "TECH_V2B_RISK_V1")
        & (trades_all["year_run"].astype(str) == "FULL")
    ].copy()
    trades["buy_datetime"] = pd.to_datetime(trades["buy_datetime"])
    trades["sell_datetime"] = pd.to_datetime(trades["sell_datetime"])
    trades["entry_period"] = trades["buy_datetime"].map(period_for)
    trades["exit_period"] = trades["sell_datetime"].map(period_for)
    if len(trades) != 43:
        raise ValueError(f"Expected 43 source trades for 2454, found {len(trades)}")

    curves = pd.read_csv(
        curves_path,
        usecols=["variant", "timestamp", "equity", "cash", "position_value", "drawdown_pct", "stock_code"],
        dtype={"stock_code": str},
    )
    curves = curves.loc[
        (curves["stock_code"] == "2454") & (curves["variant"].isin(["TECH_V2B_RISK_V1", "buy_and_hold_100pct"]))
    ].copy()
    curves["timestamp"] = pd.to_datetime(curves["timestamp"])

    strategy_curve = curves.loc[curves["variant"] == "TECH_V2B_RISK_V1"].sort_values("timestamp")
    bh_curve = curves.loc[curves["variant"] == "buy_and_hold_100pct"].sort_values("timestamp")
    start_equity = 1_000_000.0
    summaries: list[dict] = []
    slippage_rows: list[dict] = []

    for name, start, end in PERIODS:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        equity_before = strategy_curve.loc[strategy_curve["timestamp"] < start_ts, "equity"]
        opening_equity = float(equity_before.iloc[-1]) if len(equity_before) else start_equity
        seg = strategy_curve.loc[strategy_curve["timestamp"].between(start_ts, end_ts)].copy()
        if seg.empty:
            raise ValueError(f"No stored strategy equity points for {name}")
        end_equity = float(seg.iloc[-1]["equity"])
        equity_values = pd.concat([pd.Series([opening_equity]), seg["equity"].astype(float)], ignore_index=True)
        peaks = equity_values.cummax()
        segment_dd = ((peaks - equity_values) / peaks.replace(0, pd.NA) * 100).max()

        cohort = trades.loc[trades["entry_period"] == name].sort_values("sell_datetime")
        closed_exits = trades.loc[trades["exit_period"] == name]
        net = cohort["net_pnl"].astype(float)
        gross = cohort["gross_pnl"].astype(float)
        costs = cohort["total_cost"].astype(float)
        positive = net[net > 0].sort_values(ascending=False)
        wins_total = float(positive.sum())
        loss_streak = max_consecutive_losses(net)

        daily_close = seg.loc[seg["timestamp"].dt.strftime("%H:%M:%S") == "13:25:00", ["timestamp", "equity"]]
        prior_daily = strategy_curve.loc[
            (strategy_curve["timestamp"] < start_ts)
            & (strategy_curve["timestamp"].dt.strftime("%H:%M:%S") == "13:25:00"),
            ["timestamp", "equity"],
        ].tail(1)
        daily_values = pd.concat([prior_daily, daily_close]).drop_duplicates("timestamp").sort_values("timestamp")
        daily_returns = daily_values["equity"].astype(float).pct_change().dropna()
        sharpe = None
        if len(daily_returns) > 1 and float(daily_returns.std(ddof=0)) > 0:
            sharpe = float(daily_returns.mean() / daily_returns.std(ddof=0) * (252**0.5))

        bh_seg = bh_curve.loc[bh_curve["timestamp"].between(start_ts, end_ts)]
        bh_before = bh_curve.loc[bh_curve["timestamp"] < start_ts, "equity"]
        bh_start = float(bh_before.iloc[-1]) if len(bh_before) else start_equity
        bh_end = float(bh_seg.iloc[-1]["equity"]) if len(bh_seg) else None
        bh_return = (bh_end / bh_start - 1) * 100 if bh_start else None

        summaries.append({
            "period": name,
            "period_start_equity": opening_equity,
            "period_end_equity": end_equity,
            "equity_return_pct_mtm": (end_equity / opening_equity - 1) * 100,
            "max_drawdown_pct_reset_at_period_start": float(segment_dd),
            "trade_count_by_entry_date": int(len(cohort)),
            "trade_count_exited_in_period": int(len(closed_exits)),
            "closed_trade_win_rate_pct_entry_cohort": float((net > 0).mean() * 100) if len(net) else None,
            "gross_pnl_entry_cohort": float(gross.sum()),
            "gross_trade_cohort_return_pct_vs_period_start_equity": float(gross.sum() / opening_equity * 100),
            "fees_entry_cohort": float(cohort["fee"].sum()),
            "tax_entry_cohort": float(cohort["transaction_tax"].sum()),
            "total_cost_entry_cohort": float(costs.sum()),
            "net_pnl_entry_cohort": float(net.sum()),
            "net_trade_cohort_return_pct_vs_period_start_equity": float(net.sum() / opening_equity * 100),
            "profit_factor_net_entry_cohort": pf(net),
            "mean_trade_net_pnl": float(net.mean()) if len(net) else None,
            "top_1_winner_share_of_winning_trade_pnl_pct": float(positive.head(1).sum() / wins_total * 100) if wins_total else None,
            "top_3_winners_share_of_winning_trade_pnl_pct": float(positive.head(3).sum() / wins_total * 100) if wins_total else None,
            "top_5_winners_share_of_winning_trade_pnl_pct": float(positive.head(5).sum() / wins_total * 100) if wins_total else None,
            "max_consecutive_losing_trades_entry_cohort": loss_streak,
            "sharpe_daily_close_marked_equity": sharpe,
            "buy_and_hold_100pct_period_return_pct": bh_return,
            "trade_count_by_exit_date": int(len(closed_exits)),
        })

        for rate in SLIPPAGE_RATES:
            adjusted_net = slippage_net(cohort, rate)
            slippage_rows.append({
                "period": name,
                "slippage_bps_each_side": int(round(rate * 10000)),
                "trade_count_by_entry_date": int(len(cohort)),
                "approx_net_pnl_fixed_quantity": float(adjusted_net.sum()),
                "approx_return_pct_vs_period_start_equity": float(adjusted_net.sum() / opening_equity * 100),
                "positive_trades_after_slippage": int((adjusted_net > 0).sum()),
                "profit_factor_after_slippage": pf(adjusted_net),
                "slippage_method": "adverse price adjustment on stored buy/sell notionals; fixed original quantity; no engine rerun",
            })

    summary_df = pd.DataFrame(summaries)
    slippage_df = pd.DataFrame(slippage_rows)
    monthly = trades.copy()
    monthly["exit_month"] = monthly["sell_datetime"].dt.to_period("M").astype(str)
    monthly_profit = monthly.groupby("exit_month", as_index=False).agg(
        trade_count=("net_pnl", "size"),
        gross_pnl=("gross_pnl", "sum"),
        total_cost=("total_cost", "sum"),
        net_pnl=("net_pnl", "sum"),
        winners=("net_pnl", lambda s: int((s.astype(float) > 0).sum())),
    )
    monthly_profit["win_rate_pct"] = monthly_profit["winners"] / monthly_profit["trade_count"] * 100
    monthly_profit = monthly_profit.sort_values("net_pnl", ascending=False)

    output.mkdir(parents=True, exist_ok=False)
    summary_df.to_csv(output / "half_year_comparison.csv", index=False, encoding="utf-8-sig")
    slippage_df.to_csv(output / "slippage_sensitivity.csv", index=False, encoding="utf-8-sig")
    monthly_profit.to_csv(output / "monthly_profit_contribution.csv", index=False, encoding="utf-8-sig")

    top_winners = trades.sort_values("net_pnl", ascending=False).head(5)
    all_winners = trades.loc[trades["net_pnl"].astype(float) > 0, "net_pnl"].astype(float).sort_values(ascending=False)
    all_winning_pnl = float(all_winners.sum())
    full_top_shares = {
        n: float(all_winners.head(n).sum() / all_winning_pnl * 100) if all_winning_pnl else None
        for n in (1, 3, 5)
    }
    source_hashes = {name: sha256(source / name) for name in ["audit.json", "config_snapshot.json", "trades.csv", "equity_curves.csv"]}
    total_net = float(trades["net_pnl"].sum())
    summary_md = summary_df.to_markdown(index=False, floatfmt=".3f")
    slip_md = slippage_df.pivot(index="period", columns="slippage_bps_each_side", values="approx_return_pct_vs_period_start_equity").to_markdown(floatfmt=".3f")
    month_md = monthly_profit.head(8).to_markdown(index=False, floatfmt=".2f")
    winner_rows = "\n".join(
        f"- {row.buy_datetime:%Y-%m-%d} 進場、{row.sell_datetime:%Y-%m-%d} 出場：淨損益 NT${row.net_pnl:,.0f}（{row.exit_type}）"
        for row in top_winners.itertuples()
    )
    longest = max((max_consecutive_losses(g.sort_values("sell_datetime")["net_pnl"].astype(float)) for _, g in trades.groupby("entry_period")), default=0)
    report = f"""# 2454 聯發科 Technical V2-B 分段績效穩定性診斷

## 範圍與來源

- 只分析既有 2023-01-01 至 2024-12-31 `historical_development` 回測匯出，不讀行情資料庫、不重跑回測。
- 策略：`BR_SENS_RANGE55_BB20_2` / Technical V2-B + Risk V1；無 ML Gate。
- 成交：完成 5 分 K 收盤訊號，下一根實際可交易 5 分 K 開盤成交。
- 風控與成本沿用來源回測設定：1% 風險預算、40% 部位上限、2.5% 停損、30 根低點出場、162 根持倉上限；手續費 0.1425%、賣出交易稅 0.3%、原始滑價 0。
- 來源輸出：`{source.relative_to(ROOT)}`。
- 來源 audit：`{audit['experiment_id']}`；資料品質日期為 `{audit['first_common_session']}` 至 `{audit['last_common_session']}`。
- 本報告將交易績效按**進場日期**歸入半年 cohort；資金報酬與最大回撤則從逐 5 分 K equity curve 計算，包含未實現損益。跨半年未平倉交易可能使兩種歸屬口徑不同。
- 成本前後交易績效以來源 ledger 定義：成本前為 gross PnL；成本後為 gross PnL 減來源手續費、交易稅及滑價。

## 四個半年比較

{summary_md}

`Sharpe` 使用來源逐 5 分 K equity curve 中每日 13:25 收盤標記值，日報酬、無風險利率 0、母體標準差、年化 √252。半年交易勝率/PF 是按進場日分組後的已完成交易結果；交易統計不等同該半年 MTM 資產報酬。

## 獲利集中、月份與連敗

- 全期交易數：{len(trades)}；全期總淨損益：NT${total_net:,.0f}；全期勝率：{(trades['net_pnl'].astype(float) > 0).mean()*100:.1f}%；全期 PF：{pf(trades['net_pnl'].astype(float)):.3f}。
- 全期獲利交易淨損益合計：NT${all_winning_pnl:,.0f}；最大 1 / 3 / 5 筆獲利交易占比：{full_top_shares[1]:.1f}% / {full_top_shares[3]:.1f}% / {full_top_shares[5]:.1f}%。四個半年各自的集中度見比較表。這些比例描述已實現獲利集中，不代表單筆交易能預測未來。
- 最長連續虧損：{max_consecutive_losses(trades.sort_values('sell_datetime')['net_pnl'].astype(float))} 筆（全期，依出場時間排列）。
- 最大單筆獲利交易：
{winner_rows}
- 淨損益最高的出場月份：

{month_md}

月度損益按交易**出場月份**歸屬，未平倉浮動損益仍反映在半年 equity return，不列入已實現月度損益。

## 滑價敏感度（近似，不是重新成交模擬）

每邊分別假設 0、5、10、20 bps 不利成交價；按已存交易的買賣金額調整價格、費用與賣出稅，固定原股數並加總按進場日期分組的交易結果：

{slip_md}

這是 ledger 的固定股數成本壓力測試；沒有因滑價重算股數、停損觸發、後續資金或訊號，因此不能當成完整引擎滑價回測。正式要判斷「加滑價後是否仍正報酬」，需另做僅改 slippage 的凍結引擎敏感度執行並記錄新 run ID。本表若為正，只能表示簡化成本扣減後的 cohort 交易損益為正。

## 行情適配觀察

半年 Buy & Hold 報酬列於比較表，作為同期價格環境背景。若 Technical 的半年淨值正報酬與 Buy & Hold 同方向，可描述該段行情較支持突破策略；但只有四個半年、每期交易樣本不多，不能證明行情狀態造成策略結果，也不能據此調參。

## 治理與限制

- 全部數據均來自已曝光 2023–2024 development/training，不是樣本外驗證。
- 沒有使用 2025、2026、Additional Holdout 或 Final OOS；沒有重新讀取 SQLite 行情、沒有重跑回測、沒有調參或改策略。
- 來源 audit 記載 422 個共同完整 session、2454 自身 422/422 完整；代理計算使用凍結的排除目標股票等權重同儕池及前一完整交易日資料。執行規則為完成 K 棒後下一根實際可交易開盤。
- 此次未重新做程式級 look-ahead 測試；沿用來源回測的凍結設定和 audit identity。報告的分段統計是既有輸出的再整理。
- 年度／半年樣本不足以支持策略有效或穩定的結論；不要選擇性挑期間或個別股票。

## 建議結論

應以四個半年是否方向一致、PF、回撤、成本後期望與獲利集中度共同判斷。即使 2023–2024 全期 +4.05%，也不能單憑全期正報酬判定穩定；正式驗證仍缺少候選與評估規則在看結果前凍結、合規的一次性前瞻資料、預先登錄，以及完整含滑價的執行驗證。

## 輸入檔案雜湊

```json
{json.dumps(source_hashes, ensure_ascii=False, indent=2)}
```
"""
    (output / "period_stability_report.md").write_text(report, encoding="utf-8")
    audit_out = {
        "status": "COMPLETED_READ_ONLY_DIAGNOSIS",
        "source_export": str(source.relative_to(ROOT)),
        "source_experiment_id": audit["experiment_id"],
        "stock_code": "2454",
        "data_role": "historical_development_exposed",
        "periods": [{"id": n, "start": s, "end": e} for n, s, e in PERIODS],
        "source_hashes": source_hashes,
        "existing_trade_rows_used": int(len(trades)),
        "existing_equity_curve_rows_used": int(len(strategy_curve)),
        "market_database_read": False,
        "backtest_rerun": False,
        "strategy_or_risk_changed": False,
        "parameter_search": False,
        "2025_used": False,
        "2026_used": False,
        "additional_holdout_used": False,
        "final_oos_used": False,
        "slippage_scenarios_bps_each_side": [0, 5, 10, 20],
        "slippage_analysis_is_engine_rerun": False,
        "period_comparison_csv": "half_year_comparison.csv",
        "slippage_csv": "slippage_sensitivity.csv",
        "monthly_profit_csv": "monthly_profit_contribution.csv",
    }
    (output / "audit.json").write_text(json.dumps(audit_out, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not (source / "audit.json").is_file():
        raise SystemExit(f"Source audit not found: {source}")
    summarize(source, output)
    print(f"DIAGNOSIS_OUTPUT={output}")


if __name__ == "__main__":
    main()
