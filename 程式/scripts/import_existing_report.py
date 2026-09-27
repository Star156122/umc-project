"""將 reports/ 內既有回測成果上傳至 MySQL/MariaDB，不重新執行回測。

修改日期：2026-08-24
修改摘要：讀取既有 summary.json、trades.csv、signals.csv 與 pnl.csv，沿用
main02.py 的交易式資料庫寫入流程，並避免同一份報表重複上傳。
"""

from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from trading_system.backtest import AppConfig, save_backtest_to_database, str_to_bool


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def build_database_config(summary: dict[str, Any]) -> AppConfig:
    return AppConfig(
        code=str(summary["stock_code"]),
        stock_name=os.getenv("STOCK_NAME", "").strip(),
        strategy=str(summary["strategy"]),  # type: ignore[arg-type]
        run_name=str(summary.get("run_name", "")),
        backtest_start=str(summary["backtest_start"]),
        backtest_end=str(summary["backtest_end"]),
        initial_capital=int(float(summary["initial_capital"])),
        db_enabled=str_to_bool(os.getenv("DB_ENABLED", "false")),
        db_host=os.getenv("DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("DB_PORT", "3306")),
        db_name=os.getenv("DB_NAME", "ai_stock_system"),
        db_user=os.getenv("DB_USER", ""),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_user_id=int(os.getenv("DB_USER_ID", "1")),
    )


def is_test_report(summary_path: Path) -> bool:
    """辨識流程測試、單日測試等不應進正式資料庫的報表。"""

    summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
    run_name = str(summary.get("run_name", "")).lower()
    strategy = str(summary.get("strategy", "")).lower()
    folder_name = summary_path.parent.name.lower()
    return strategy == "fixed" or "test" in run_name or "_test_" in folder_name


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="上傳單一或整批既有回測報表，不重新執行回測")
    parser.add_argument(
        "report_dir",
        help="單一回測輸出資料夾，或包含多個回測資料夾的 reports/股票代號",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="連同 run_name/folder 含 test 或 fixed 策略一併上傳；預設排除",
    )
    args = parser.parse_args()

    report_dir = Path(args.report_dir).expanduser().resolve()
    if not report_dir.is_dir():
        raise RuntimeError(f"找不到資料夾：{report_dir}")

    direct_summary = report_dir / "summary.json"
    summary_paths = [direct_summary] if direct_summary.exists() else sorted(report_dir.rglob("summary.json"))
    if not summary_paths:
        raise RuntimeError(f"在資料夾內找不到任何 summary.json：{report_dir}")
    excluded_tests = 0
    if not direct_summary.exists() and not args.include_tests:
        formal_paths = [path for path in summary_paths if not is_test_report(path)]
        excluded_tests = len(summary_paths) - len(formal_paths)
        summary_paths = formal_paths
    if not summary_paths:
        raise RuntimeError("沒有可上傳的正式報表；測試報表預設排除。")

    uploaded = 0
    skipped = 0
    failed: list[tuple[Path, str]] = []
    print(f"Found {len(summary_paths)} formal report(s); excluded_tests={excluded_tests}.")
    for index, summary_path in enumerate(summary_paths, start=1):
        current_dir = summary_path.parent
        report_path = current_dir / "report.html"
        print(f"[{index}/{len(summary_paths)}] {current_dir.name}")
        if not report_path.exists():
            failed.append((current_dir, "找不到 report.html"))
            print("  Failed: 找不到 report.html")
            continue

        try:
            summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
            config = build_database_config(summary)
            if not config.db_enabled:
                raise RuntimeError("DB_ENABLED 目前不是 true；為避免誤傳，已停止執行。")
            database_ids = save_backtest_to_database(
                config,
                summary,
                read_csv_rows(current_dir / "trades.csv"),
                read_csv_rows(current_dir / "signals.csv"),
                read_csv_rows(current_dir / "pnl.csv"),
                report_path,
            )
            if database_ids is None:
                raise RuntimeError("資料庫上傳未啟用。")
            summary["database"] = database_ids
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            uploaded += 1
            print(
                "  Uploaded: "
                f"analysis_id={database_ids['analysis_id']}, "
                f"backtest_id={database_ids['backtest_id']}, report_id={database_ids['report_id']}"
            )
        except Exception as exc:
            message = str(exc)
            if "已上傳過" in message:
                skipped += 1
                print(f"  Skipped: {message}")
            else:
                failed.append((current_dir, message))
                print(f"  Failed: {message}")

    print(
        f"Finished: uploaded={uploaded}, skipped={skipped}, "
        f"excluded_tests={excluded_tests}, failed={len(failed)}"
    )
    if failed:
        for path, message in failed:
            print(f"- {path.name}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
