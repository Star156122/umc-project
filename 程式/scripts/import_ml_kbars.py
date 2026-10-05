"""依 ML 資料角色政策匯入目前允許的 5 分 K。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.trading_pipeline import coverage_report, load_trading_plan
from ml.data_roles import ACTIVE_ROLE_NAMES, assert_ml_read_period
from trading_system.backtest import login_sinopac, sino_ts_to_local_timestamp
from trading_system.research_guard import assert_development_period

PLAN_PATH = ROOT / "configs/ml_trading_v1_20261003.json"
DATABASE = ROOT / "data/market_data.sqlite3"


def request_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Shioaji 單次 K 棒查詢最多 30 個日曆日。"""
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    assert_ml_read_period(first, last)
    chunks = []
    cursor = first
    while cursor <= last:
        chunk_end = min(last, cursor + timedelta(days=29))
        assert_ml_read_period(cursor, chunk_end)
        assert_development_period(cursor, chunk_end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def fetch_one(api, code: str, start: str, end: str, bar_minutes: int) -> tuple[list[tuple], int, float | None, float | None]:
    assert_ml_read_period(start, end)
    assert_development_period(start, end)
    contract = api.Contracts.Stocks[code]
    parts = []
    source_count = 0
    for chunk_start, chunk_end in request_chunks(start, end):
        print(f"  下載 {code} {chunk_start}～{chunk_end}", flush=True)
        raw = api.kbars(contract, start=chunk_start, end=chunk_end, timeout=60000)
        frame = pd.DataFrame({**raw})
        source_count += len(frame)
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return [], 0, None, None
    minute = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
    minute["timestamp"] = minute["ts"].map(sino_ts_to_local_timestamp)
    minute["bucket"] = (minute["timestamp"] // (bar_minutes * 60) * (bar_minutes * 60)).astype("int64")
    grouped = minute.groupby("bucket", sort=True).agg(
        open=("Open", "first"), high=("High", "max"), low=("Low", "min"),
        close=("Close", "last"), volume=("Volume", "sum"),
    ).reset_index()
    rows = [
        (code, bar_minutes, start, end, int(row.bucket), float(row.open), float(row.high),
         float(row.low), float(row.close), int(row.volume))
        for row in grouped.itertuples(index=False)
    ]
    return rows, source_count, float(minute["timestamp"].min()), float(minute["timestamp"].max())


def save(database: Path, code: str, start: str, end: str, bar_minutes: int,
         rows: list[tuple], source_count: int, first_ts: float | None, last_ts: float | None) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM market_kbars WHERE stock_code=? AND freq_minutes=? AND range_start=? AND range_end=?",
            (code, bar_minutes, start, end),
        )
        connection.executemany(
            """INSERT INTO market_kbars(stock_code,freq_minutes,range_start,range_end,kbar_timestamp,open,high,low,close,volume)
               VALUES(?,?,?,?,?,?,?,?,?,?)""", rows,
        )
        connection.execute(
            """INSERT INTO market_kbar_cache_meta(stock_code,freq_minutes,range_start,range_end,source_row_count,source_first_timestamp,source_last_timestamp,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(stock_code,freq_minutes,range_start,range_end) DO UPDATE SET
               source_row_count=excluded.source_row_count,source_first_timestamp=excluded.source_first_timestamp,
               source_last_timestamp=excluded.source_last_timestamp,updated_at=excluded.updated_at""",
            (code, bar_minutes, start, end, source_count, first_ts, last_ts, datetime.now().astimezone().isoformat(timespec="seconds")),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="匯入 ML Training、Validation、Development 所需的六檔 5 分 K")
    parser.add_argument("--force", action="store_true", help="重新下載已存在的完整區間")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    plan = load_trading_plan(PLAN_PATH)
    current = coverage_report(DATABASE, plan)
    available = {(r["role"], r["stock_code"]) for r in current.rows if r["available"]}
    api = login_sinopac()
    try:
        for role in ACTIVE_ROLE_NAMES:
            spec = plan["periods"][role]
            for code in plan["stock_codes"]:
                if not args.force and (role, code) in available:
                    print(f"略過已有資料：{role} {code}", flush=True)
                    continue
                rows, source_count, first_ts, last_ts = fetch_one(api, code, spec["start"], spec["end"], plan["bar_minutes"])
                if not rows:
                    raise RuntimeError(f"Shioaji 沒有回傳 {code} {spec['start']}～{spec['end']} 的資料。")
                save(DATABASE, code, spec["start"], spec["end"], plan["bar_minutes"], rows, source_count, first_ts, last_ts)
                print(f"  已存入 {len(rows):,} 根 5 分 K（來源 {source_count:,} 根分鐘 K）", flush=True)
    finally:
        api.logout()
    final = coverage_report(DATABASE, plan)
    print(f"資料完整：{final.complete}")
    return 0 if final.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
