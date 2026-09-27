"""研究工具的命令列與本機網站入口。"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymysql
from dotenv import load_dotenv

from scripts.sync_research_mysql import connection_settings, list_mysql_research_runs, read_latest_mysql
from trading_system.research import (build_research, export_standalone_html, freeze_config,
                                     read_research, save_research)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/research.sqlite3"
EXPORT = ROOT / "exports/research_dashboard_latest.html"


def read_guarded_json(path: Path) -> dict:
    from trading_system.research_guard import guarded_json_loads
    return guarded_json_loads(path.read_text(encoding="utf-8"))


def read_active_research(run_id: int | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    if os.getenv("DB_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            connection = pymysql.connect(**connection_settings())
            try:
                return read_latest_mysql(connection, run_id)
            finally:
                connection.close()
        except pymysql.MySQLError:
            # MySQL 未啟動時仍可查看本機 SQLite 與靜態 ML 報告。
            pass
    payload = read_research(DATABASE)
    payload["data_source"] = "本機 SQLite（MySQL 未連線時的備援）"
    return payload


class ResearchHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            content = (ROOT / "web/research.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path in ("/early_followthrough_latest.html", "/trade_failure_diagnosis_latest.html", "/six_strategy_logic_latest.html", "/entry_exit_improvement_latest.html", "/risk_ablation_latest.html", "/risk_candidate_latest.html", "/four_group_latest.html"):
            content = (ROOT / "exports" / parsed.path.lstrip("/")).read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path == "/routing_simulation_latest.html":
            content = (ROOT / "exports/routing_simulation_latest.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path == "/regime_validation_latest.html":
            content = (ROOT / "exports/regime_validation_latest.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path == "/cross-stock":
            content = (ROOT / "exports/cross_stock_dashboard_latest.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path == "/ml-latest":
            content = (ROOT / "exports/ml_baseline_latest.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif parsed.path == "/api/ml/latest":
            try:
                payload = read_guarded_json(ROOT / "exports/ml_baseline_20260927/results.json")
                content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (ValueError, OSError, json.JSONDecodeError):
                self.send_error(503, "ML baseline unavailable")
                return
            kind = "application/json; charset=utf-8"
        elif parsed.path == "/api/analysis/latest":
            try:
                payload = read_guarded_json(ROOT / "exports/unified_analysis_latest.json")
                content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (ValueError, OSError, json.JSONDecodeError):
                self.send_error(503, "Unified analysis unavailable")
                return
            kind = "application/json; charset=utf-8"
        elif parsed.path == "/api/research":
            try:
                requested = parse_qs(parsed.query).get("run_id", [None])[0]
                run_id = int(requested) if requested else None
                content = json.dumps(read_active_research(run_id), ensure_ascii=False).encode("utf-8")
            except (ValueError, OSError, sqlite3.Error, pymysql.MySQLError):
                self.send_error(503, "Build research data first")
                return
            kind = "application/json; charset=utf-8"
        elif parsed.path == "/api/options":
            try:
                connection = pymysql.connect(**connection_settings())
                try:
                    options = list_mysql_research_runs(connection)
                finally:
                    connection.close()
                content = json.dumps(options, ensure_ascii=False).encode("utf-8")
            except (ValueError, OSError, pymysql.MySQLError):
                self.send_error(503, "Database options unavailable")
                return
            kind = "application/json; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="市場階段分析與策略研究網頁")
    parser.add_argument("command", choices=["build", "serve", "export", "run-fixed"])
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "build":
        result = build_research(ROOT, args.lookback, args.threshold)
        run_id = save_research(DATABASE, result)
        export_standalone_html(ROOT / "web/research.html", EXPORT, result)
        print(f"已建立研究批次 {run_id}（{len(result['strategies'])} 個策略）：{DATABASE}")
        print(f"已輸出可直接開啟的網頁：{EXPORT}")
    elif args.command == "export":
        export_standalone_html(ROOT / "web/research.html", EXPORT, read_research(DATABASE))
        print(f"已輸出可直接開啟的網頁：{EXPORT}")
    elif args.command == "run-fixed":
        frozen = ROOT / "configs/frozen_baseline.json"
        freeze_config(ROOT / "backtest_config.json", frozen)
        subprocess.run([sys.executable, str(ROOT / "run_batch.py"), "--config", str(frozen)],
                       cwd=ROOT, check=True)
    else:
        read_active_research()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), ResearchHandler)
        print(f"研究頁面：http://127.0.0.1:{args.port}（Ctrl+C 結束）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
