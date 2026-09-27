"""更新本地 SQLite 股票歷史資料庫，不執行策略回測。

修改日期：2026-08-24
修改摘要：支援一次更新多檔股票，並輸出各股票筆數、耗時及資料庫涵蓋範圍。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace

import shioaji as sj
from dotenv import load_dotenv

from trading_system.backtest import (
    fetch_sinopac_ticks,
    is_cache_range_complete,
    login_sinopac,
    parse_args,
    resolve_data_cache_path,
    validate_config,
    validate_env,
)


def parse_stock_codes(primary_code: str, stock_codes: str) -> list[str]:
    """解析股票清單、去除重複值，未設定時維持單一股票行為。"""

    raw_codes = stock_codes.split(",") if stock_codes.strip() else [primary_code]
    result: list[str] = []
    for raw_code in raw_codes:
        code = raw_code.strip()
        if code and code not in result:
            result.append(code)
    if not result:
        raise RuntimeError("股票清單不可為空。")
    return result


def database_coverage(database_path: str) -> list[tuple[str, str, str, int]]:
    """回傳資料庫內每檔股票的首日、末日與 Tick 筆數。"""

    connection = sqlite3.connect(database_path)
    try:
        cursor = connection.execute(
            """SELECT code, MIN(trade_date), MAX(trade_date), COUNT(*)
               FROM market_ticks GROUP BY code ORDER BY code"""
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        connection.close()
    return rows


def main() -> None:
    load_dotenv()
    config = parse_args()
    validate_config(config)
    if config.tick_source != "sinopac":
        raise RuntimeError("update_data.py 目前只更新 Shioaji tick 資料。")
    if not config.use_data_cache:
        raise RuntimeError("請啟用 use_data_cache，否則沒有資料庫可更新。")

    validate_env(config)
    codes = parse_stock_codes(config.code, config.stock_codes)
    api: sj.Shioaji | None = None
    started_at = time.perf_counter()
    results: list[tuple[str, int, float]] = []
    try:
        configs = [replace(config, code=code) for code in codes]
        if any(not is_cache_range_complete(item) for item in configs):
            api = login_sinopac()
        for item in configs:
            stock_started_at = time.perf_counter()
            ticks = fetch_sinopac_ticks(api, item)
            results.append((item.code, len(ticks), time.perf_counter() - stock_started_at))
    finally:
        if api is not None:
            api.logout()

    print("========== Data Update Finished ==========")
    print(f"Stocks: {', '.join(codes)}")
    print(f"Range: {config.backtest_start} ~ {config.backtest_end}")
    for code, tick_count, elapsed in results:
        print(f"- {code}: {tick_count:,} ticks, {elapsed:.2f} seconds")
    database_path = resolve_data_cache_path(config)
    print(f"Total elapsed: {time.perf_counter() - started_at:.2f} seconds")
    print(f"Database: {database_path}")
    print("Database coverage:")
    for code, first_date, last_date, tick_count in database_coverage(str(database_path)):
        print(f"- {code}: {first_date} ~ {last_date}, {tick_count:,} ticks")


if __name__ == "__main__":
    main()
