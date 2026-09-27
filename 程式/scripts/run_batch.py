"""Run multiple backtest profiles from backtest_config.json."""

from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import argparse
import csv
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "backtest_config.json"
REPORT_DIR = ROOT / "reports"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"找不到設定檔：{path}")
    try:
        data = guarded_json_loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} JSON 格式錯誤：第 {exc.lineno} 行第 {exc.colno} 欄") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path.name} 最外層必須是 JSON 物件")
    return data


def resolve_profiles(config: dict[str, Any], selected: list[str]) -> list[str]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SystemExit("backtest_config.json 的 profiles 必須是 JSON 物件")

    names = selected or config.get("batch_profiles") or [config.get("active_profile")]
    names = [str(name).strip() for name in names if str(name).strip()]
    if not names:
        raise SystemExit("沒有可執行的 profile，請設定 batch_profiles 或 active_profile")

    missing = [name for name in names if name not in profiles]
    if missing:
        raise SystemExit("找不到 profile：" + ", ".join(missing))
    return names


def find_new_summary(before: set[Path], profile: str) -> Path:
    candidates = [
        path
        for path in REPORT_DIR.rglob("summary.json")
        if path not in before and profile in path.parent.name
    ]
    if not candidates:
        raise SystemExit(f"{profile} 執行完成，但找不到新產生的 summary.json")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def write_comparison(summaries: list[dict[str, Any]]) -> Path:
    assert_payload(summaries)
    if not summaries:
        raise SystemExit("沒有策略摘要可比較")
    stock_code = str(summaries[0].get("stock_code", "unknown"))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = REPORT_DIR / stock_code / f"strategy_comparison_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "strategy_comparison.csv"
    json_path = output_dir / "strategy_comparison.json"
    html_path = output_dir / "strategy_comparison.html"

    columns = [
        "strategy",
        "strategy_label",
        "strategy_category",
        "suitable_market",
        "backtest_start",
        "backtest_end",
        "initial_capital",
        "final_assets",
        "gross_pnl",
        "fee",
        "tax",
        "transaction_cost",
        "net_pnl",
        "total_return",
        "completed_trades",
        "win_rate",
        "break_even_win_rate",
        "payoff_ratio",
        "profit_factor",
        "max_drawdown",
        "sharpe_ratio",
        "buy_and_hold_return",
        "research_status",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)
    json_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")

    best_return = max(float(item.get("total_return", 0.0)) for item in summaries)
    rows = []
    for item in summaries:
        return_value = float(item.get("total_return", 0.0))
        row_class = ' class="table-success"' if return_value == best_return else ""
        rows.append(
            f"""<tr{row_class}>
            <td>{html.escape(str(item.get('strategy_label', item.get('strategy', ''))))}</td>
            <td>{html.escape(str(item.get('strategy_category', '')))}</td>
            <td>{html.escape(str(item.get('suitable_market', '')))}</td>
            <td class="text-end">{float(item.get('initial_capital', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('final_assets', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('gross_pnl', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('fee', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('tax', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('transaction_cost', 0)):,.0f}</td>
            <td class="text-end">{float(item.get('net_pnl', 0)):,.0f}</td>
            <td class="text-end">{return_value:.2%}</td>
            <td class="text-end">{int(item.get('completed_trades', 0))}</td>
            <td class="text-end">{float(item.get('win_rate', 0)):.2%}</td>
            <td class="text-end">{float(item.get('break_even_win_rate', 0)):.2%}</td>
            <td class="text-end">{float(item.get('payoff_ratio', 0)):.2f}</td>
            <td class="text-end">{float(item.get('profit_factor', 0)):.2f}</td>
            <td class="text-end">{float(item.get('max_drawdown', 0)):.2%}</td>
            <td class="text-end">{float(item.get('sharpe_ratio', 0)):.3f}</td>
            <td class="text-end">{float(item.get('buy_and_hold_return', 0)):.2%}</td>
            <td>{html.escape(str(item.get('research_status', '')))}</td>
            </tr>"""
        )

    start = html.escape(str(summaries[0].get("backtest_start", "")))
    end = html.escape(str(summaries[0].get("backtest_end", "")))
    benchmark_return = float(summaries[0].get("buy_and_hold_return", 0.0))
    benchmark_note = (
        "本批次最佳策略仍低於同期買進持有基準。"
        if best_return < benchmark_return
        else "本批次最佳策略高於同期買進持有基準。"
    )
    html_path.write_text(
        f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(stock_code)} 策略比較</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="bg-light"><main class="container-fluid py-4">
<h1 class="h3">{html.escape(stock_code)} 六策略回測比較</h1>
<p>統一回測區間：{start} ～ {end}。綠色列為本批次總報酬率最高的策略。</p>
<div class="alert alert-warning py-2">這是同一期間的參數研究結果，不是獨立樣本外驗證或未來獲利保證。{benchmark_note}</div>
<div class="table-responsive"><table class="table table-bordered table-sm bg-white align-middle">
<thead><tr><th>策略</th><th>類型</th><th>較適合行情</th><th>初始本金</th><th>最終資產</th>
<th>未扣成本損益</th><th>手續費</th><th>交易稅</th><th>交易成本</th><th>淨損益</th><th>報酬率</th><th>交易數</th>
<th>勝率</th><th>損益平衡勝率</th><th>賺賠比</th><th>Profit Factor</th><th>最大回撤</th><th>Sharpe</th>
<th>同期買進持有</th><th>初步判定</th></tr></thead><tbody>{''.join(rows)}</tbody>
</table></div>
<p class="small text-muted">淨損益＝賣出收入－買進成本－買賣手續費－賣出交易稅。</p>
</main></body></html>""",
        encoding="utf-8",
    )
    return html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="依照 backtest_config.json 批次執行多個回測 profile")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--profiles", nargs="*", default=[])
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    config = load_config(config_path)
    profiles = resolve_profiles(config, args.profiles)

    print("========== Batch Backtest ==========")
    print(f"Config: {config_path}")
    print("Profiles: " + ", ".join(profiles))
    print("====================================")

    summaries: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles, start=1):
        print(f"\n[{index}/{len(profiles)}] Running profile: {profile}")
        before = set(REPORT_DIR.rglob("summary.json")) if REPORT_DIR.exists() else set()
        command = [
            sys.executable,
            str(ROOT / "main02.py"),
            "--config",
            str(config_path),
            "--profile",
            profile,
        ]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
        summary_path = find_new_summary(before, profile)
        summaries.append(guarded_json_loads(summary_path.read_text(encoding="utf-8")))

    comparison_path = write_comparison(summaries)
    print("\nBatch backtest finished.")
    print(f"Comparison report: {comparison_path}")


if __name__ == "__main__":
    main()
