"""建立多股票固定參數批次；預設只做檢查，不會直接耗時回測。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from scripts.sync_research_mysql import connection_settings, create_schema, sync_stock_catalog

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
BATCH_DIR = ROOT / "data/batches"
MARKET_CACHE = ROOT / "data/market_data.sqlite3"


def load_json(path: Path) -> dict:
    try:
        data = guarded_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"無法讀取設定檔 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"設定檔最外層必須是物件：{path}")
    return data


def load_plan(template_file: Path | None = None, stock_file: Path | None = None,
              experiment_file: Path | None = None) -> tuple[dict, dict, dict]:
    stocks = load_json(stock_file or CONFIGS / "stocks.json")
    templates = load_json(template_file or CONFIGS / "strategy_templates.json")
    experiment = load_json(experiment_file or CONFIGS / "multi_stock_experiment.json")
    stock_map = {str(row["code"]): row for row in stocks["stocks"]}
    missing_stocks = [code for code in experiment["stock_codes"] if code not in stock_map]
    missing_strategies = [key for key in experiment["strategy_keys"] if key not in templates["strategies"]]
    if missing_stocks or missing_strategies:
        raise RuntimeError(f"設定不完整：股票={missing_stocks}，策略={missing_strategies}")
    return stock_map, templates, experiment


def build_runtime_config(stock: dict, templates: dict, experiment: dict) -> dict:
    assert_payload(experiment)
    baseline = load_json(CONFIGS / "frozen_baseline.json")
    for key in ("profiles", "batch_profiles", "active_profile", "stock_codes"):
        baseline.pop(key, None)
    baseline.update({"backtest_start": experiment["backtest_start"],
                     "backtest_end": experiment["backtest_end"],
                     "initial_capital": experiment["initial_capital"],
                     "kbar_freq": experiment["kbar_freq"],
                     "stock_codes": stock["code"], "stock_name": stock["name"],
                     "llm_enabled": bool(experiment.get("llm_during_batch", False)),
                     "db_enabled": True})
    profiles = {}
    names = []
    for strategy in experiment["strategy_keys"]:
        name = f"{stock['code']}_{strategy}"
        profile = dict(templates["strategies"][strategy])
        profile.update({"code": stock["code"], "strategy": strategy,
                        "run_name": f"{stock['code']}_{strategy}_{templates['version']}"})
        profiles[name] = profile
        names.append(name)
    baseline.update({"active_profile": names[0], "batch_profiles": names, "profiles": profiles})
    return baseline


def plan_fingerprint(stock_codes: list[str], templates: dict, experiment: dict) -> str:
    content = json.dumps({"stocks": stock_codes, "templates": templates,
                          "experiment": experiment}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stocks_missing_market_cache(stock_codes: list[str], experiment: dict) -> list[str]:
    """找出本機沒有指定期間 5 分 K 快取的股票。"""
    assert_payload(experiment)
    if not MARKET_CACHE.exists():
        return list(stock_codes)
    try:
        with sqlite3.connect(MARKET_CACHE) as cache:
            return [
                code
                for code in stock_codes
                if cache.execute(
                    """SELECT 1 FROM market_kbars
                       WHERE stock_code=? AND freq_minutes=? AND range_start=? AND range_end=?
                       LIMIT 1""",
                    (code, int(experiment["kbar_freq"]), experiment["backtest_start"],
                     experiment["backtest_end"]),
                ).fetchone() is None
            ]
    except sqlite3.Error:
        return list(stock_codes)


def validate_market_data_access(stock_codes: list[str], experiment: dict) -> None:
    """在建立批次紀錄前確認：缺快取時必須能登入行情來源。"""
    missing_cache = stocks_missing_market_cache(stock_codes, experiment)
    if not missing_cache:
        return
    missing_keys = [name for name in ("API_KEY", "API_SECRET") if not os.getenv(name, "").strip()]
    if not missing_keys:
        return
    raise SystemExit(
        "無法開始回測：股票 " + ", ".join(missing_cache)
        + " 沒有本機行情快取，而且 .env 缺少 " + ", ".join(missing_keys) + "。\n"
        "請在「程式/.env」填入永豐 Shioaji 的 API_KEY 與 API_SECRET，儲存後再執行。\n"
        "金鑰請只放在自己的 .env，不要貼到聊天室、截圖或 GitHub。\n"
        "本次已在建立批次紀錄前停止，不會新增失敗報告或資料庫批次。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="多股票固定參數批次")
    parser.add_argument("--execute", action="store_true", help="實際開始回測；未指定時只檢查計畫")
    parser.add_argument("--all", action="store_true", help="執行清單全部股票；預設只使用首檔驗證股票")
    parser.add_argument("--stock", action="append", default=[], help="只選指定股票，可重複使用")
    parser.add_argument("--template", type=Path, help="另選策略範本；未指定時使用固定參數 v1")
    parser.add_argument("--stocks-file", type=Path, help="另選股票清單；未指定時使用第一輪6檔")
    parser.add_argument("--experiment", type=Path, help="另選實驗設定；未指定時使用第一輪設定")
    args = parser.parse_args()
    stock_map, templates, experiment = load_plan(args.template, args.stocks_file, args.experiment)
    selected = args.stock or (experiment["stock_codes"] if args.all else [experiment["smoke_test_stock"]])
    unknown = [code for code in selected if code not in stock_map]
    if unknown:
        raise SystemExit("股票不在測試清單：" + ", ".join(unknown))
    if args.execute:
        load_dotenv(ROOT / ".env")
        validate_market_data_access(selected, experiment)
    fingerprint = plan_fingerprint(selected, templates, experiment)
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + experiment["experiment_name"]
    manifest = {"batch_id": batch_id, "created_at": datetime.now().isoformat(),
                "status": "planned" if not args.execute else "running",
                "stock_codes": selected, "strategy_keys": experiment["strategy_keys"],
                "parameter_version": templates["version"], "parameter_sha256": fingerprint,
                "results": []}
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = BATCH_DIR / f"{batch_id}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"批次：{batch_id}")
    print("股票：" + ", ".join(f"{code} {stock_map[code]['name']}" for code in selected))
    print(f"固定策略版本：{templates['version']}，共 {len(experiment['strategy_keys'])} 種策略")
    if not args.execute:
        print(f"檢查完成，尚未回測。計畫檔：{manifest_path}")
        return
    database = pymysql.connect(**connection_settings())
    try:
        with database.cursor() as cursor:
            create_schema(cursor)
            sync_stock_catalog(cursor)
            cursor.execute(
                """INSERT INTO experiment_batches
                   (batch_key,experiment_name,parameter_version,parameter_sha256,period_start,period_end,status)
                   VALUES (%s,%s,%s,%s,%s,%s,'running')""",
                (batch_id, experiment["experiment_name"], templates["version"], fingerprint,
                 experiment["backtest_start"], experiment["backtest_end"]),
            )
            database_batch_id = cursor.lastrowid
            cursor.executemany(
                "INSERT INTO experiment_stocks(batch_id,stock_code,stock_name,industry,status) VALUES (%s,%s,%s,%s,'pending')",
                [(database_batch_id, code, stock_map[code]["name"], stock_map[code]["industry"])
                 for code in selected],
            )
        database.commit()
    except Exception:
        database.rollback()
        database.close()
        raise
    with tempfile.TemporaryDirectory(prefix="stock_batch_") as directory:
        for code in selected:
            runtime = Path(directory) / f"{code}.json"
            runtime.write_text(json.dumps(build_runtime_config(stock_map[code], templates, experiment),
                                          ensure_ascii=False, indent=2), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "run_batch.py"), "--config", str(runtime)], cwd=ROOT)
            manifest["results"].append({"stock_code": code,
                                        "status": "completed" if result.returncode == 0 else "failed",
                                        "return_code": result.returncode})
            with database.cursor() as cursor:
                cursor.execute(
                    "UPDATE experiment_stocks SET status=%s,error_message=%s WHERE batch_id=%s AND stock_code=%s",
                    ("completed" if result.returncode == 0 else "failed",
                     None if result.returncode == 0 else f"回測結束碼 {result.returncode}",
                     database_batch_id, code),
                )
            database.commit()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["status"] = "completed" if all(row["status"] == "completed" for row in manifest["results"]) else "partial"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with database.cursor() as cursor:
        cursor.execute("UPDATE experiment_batches SET status=%s,finished_at=NOW() WHERE batch_id=%s",
                       (manifest["status"], database_batch_id))
    database.commit()
    database.close()
    print(f"批次結束：{manifest['status']}，計畫檔：{manifest_path}")


if __name__ == "__main__":
    main()
