"""替既有回測成果補上 OpenAI 分析，不重新執行股票回測。

修改日期：2026-08-24
修改摘要：批次讀取正式報告、呼叫 OpenAI Responses API、更新 HTML/JSON，
並可選擇把新增的 LLM 結果同步至既有 MySQL/MariaDB reports 資料列。
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

from trading_system.llm_analysis import (
    DEFAULT_MODEL,
    build_analysis_payload,
    generate_openai_analysis,
    render_analysis_html,
    replace_analysis_section,
)


ROOT = Path(__file__).resolve().parents[1]


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def is_test_report(summary_path: Path) -> bool:
    summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
    run_name = str(summary.get("run_name", "")).lower()
    strategy = str(summary.get("strategy", "")).lower()
    folder_name = summary_path.parent.name.lower()
    return strategy == "fixed" or "test" in run_name or "_test_" in folder_name


def sync_database_report(summary: dict[str, Any], llm_result: dict[str, Any]) -> None:
    """只更新同一筆 reports.report_content，不新增重複回測。"""

    database = summary.get("database", {})
    report_id = database.get("report_id") if isinstance(database, dict) else None
    analysis_id = database.get("analysis_id") if isinstance(database, dict) else None
    if not report_id or not analysis_id:
        raise RuntimeError("summary.json 沒有 database.report_id / analysis_id，無法同步既有資料列。")
    if os.getenv("DB_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("DB_ENABLED 不是 true；為避免誤改資料庫，已停止同步。")
    try:
        import pymysql
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 PyMySQL；請先執行 uv sync。") from exc

    connection = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ai_stock_system"),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT report_content FROM reports WHERE report_id = %s AND analysis_id = %s FOR UPDATE",
                (int(report_id), int(analysis_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"資料庫找不到 report_id={report_id}、analysis_id={analysis_id}。")
            try:
                report_payload = guarded_json_loads(row[0]) if row[0] else {}
            except json.JSONDecodeError:
                report_payload = {"legacy_report_content": row[0]}
            report_payload["summary"] = summary
            report_payload["llm_analysis"] = llm_result
            cursor.execute(
                "UPDATE reports SET report_content = %s WHERE report_id = %s AND analysis_id = %s",
                (
                    json.dumps(report_payload, ensure_ascii=False, allow_nan=False),
                    int(report_id),
                    int(analysis_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("資料庫報告更新筆數不等於 1，已取消。")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="替既有正式回測補上 OpenAI 分析，不重跑回測")
    parser.add_argument(
        "report_dir",
        nargs="?",
        default=str(ROOT / "reports" / "2303"),
        help="單一回測資料夾，或包含多份回測的資料夾；預設 reports/2303",
    )
    parser.add_argument("--include-tests", action="store_true", help="包含名稱含 test 或 fixed 的測試報告")
    parser.add_argument("--force", action="store_true", help="忽略既有 llm_report.json，重新產生並計費")
    parser.add_argument("--sync-database", action="store_true", help="同步更新既有 reports.report_content")
    provider = os.getenv("LLM_PROVIDER", "OpenAI").strip()
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1400")),
    )
    parser.add_argument("--no-cache", action="store_true", help="不使用本地輸入快取")
    args = parser.parse_args()

    report_dir = Path(args.report_dir).expanduser().resolve()
    if not report_dir.is_dir():
        raise RuntimeError(f"找不到報告資料夾：{report_dir}")
    direct_summary = report_dir / "summary.json"
    summary_paths = [direct_summary] if direct_summary.exists() else sorted(report_dir.rglob("summary.json"))
    if not args.include_tests:
        summary_paths = [path for path in summary_paths if not is_test_report(path)]
    if not summary_paths:
        raise RuntimeError("找不到可處理的正式 summary.json。")

    api_key = os.getenv("GEMINI_API_KEY" if provider == "Gemini" else "OPENAI_API_KEY", "")
    if not api_key.strip():
        raise RuntimeError(f"請先在 .env 設定 {provider} API 金鑰。")
    cache_dir = Path(os.getenv("LLM_CACHE_PATH", "data/llm_cache"))
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir

    completed = 0
    skipped = 0
    failed: list[tuple[Path, str]] = []
    print(f"Found {len(summary_paths)} formal report(s). Model={args.model}")
    for index, summary_path in enumerate(summary_paths, start=1):
        current_dir = summary_path.parent
        llm_path = current_dir / "llm_report.json"
        html_path = current_dir / "report.html"
        print(f"[{index}/{len(summary_paths)}] {current_dir.name}")
        if llm_path.exists() and not args.force:
            try:
                existing_result = guarded_json_loads(llm_path.read_text(encoding="utf-8"))
                existing_summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
                existing_summary["llm"] = existing_result
                summary_path.write_text(
                    json.dumps(existing_summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if html_path.exists():
                    updated_html = replace_analysis_section(
                        html_path.read_text(encoding="utf-8"),
                        render_analysis_html(existing_result),
                    )
                    html_path.write_text(updated_html, encoding="utf-8")
                if args.sync_database:
                    sync_database_report(existing_summary, existing_result)
                skipped += 1
                print(
                    "  Reused existing llm_report.json"
                    + (" and synced to database" if args.sync_database else "")
                )
            except Exception as exc:
                failed.append((current_dir, str(exc)))
                print(f"  Failed while reusing existing result: {exc}")
            continue
        try:
            if not html_path.exists():
                raise RuntimeError("找不到 report.html。")
            summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
            payload = build_analysis_payload(
                summary,
                read_csv_rows(current_dir / "pnl.csv"),
                read_csv_rows(current_dir / "signals.csv"),
            )
            result = generate_openai_analysis(
                payload,
                api_key=api_key,
                model=args.model,
                max_output_tokens=args.max_output_tokens,
                cache_dir=cache_dir,
                use_cache=not args.no_cache,
                provider=provider,
            )
            llm_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["llm"] = result
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            updated_html = replace_analysis_section(
                html_path.read_text(encoding="utf-8"),
                render_analysis_html(result),
            )
            html_path.write_text(updated_html, encoding="utf-8")
            if args.sync_database:
                sync_database_report(summary, result)
            completed += 1
            print("  Completed" + (" and synced to database" if args.sync_database else ""))
        except Exception as exc:
            failed.append((current_dir, str(exc)))
            print(f"  Failed: {exc}")

    print(f"Finished: completed={completed}, skipped={skipped}, failed={len(failed)}")
    if failed:
        for path, message in failed:
            print(f"- {path.name}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
