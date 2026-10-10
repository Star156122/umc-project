"""明確授權後，匯入 Technical V1 僅限 2023–2024 Training 的五分 K。"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_system.backtest import login_sinopac, sino_ts_to_local_timestamp  # noqa: E402
from trading_system.research_guard import assert_development_period  # noqa: E402
from trading_system.technical_v1 import (  # noqa: E402
    initialize_market_schema,
    inspect_training_market_data,
    read_config,
    resolve_market_data_path,
)

CONFIG = ROOT / "configs" / "technical_v1_research.json"
TAIPEI = ZoneInfo("Asia/Taipei")


def _assert_training_only(start: str, end: str) -> None:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if first < date(2023, 1, 1) or last > date(2024, 12, 31) or first > last:
        raise ValueError("Technical V1 匯入器只允許 2023-01-01～2024-12-31 Training")
    assert_development_period(first, last)


def request_chunks(start: str, end: str) -> list[tuple[str, str]]:
    _assert_training_only(start, end)
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    chunks = []
    while cursor <= last:
        chunk_end = min(last, cursor + timedelta(days=29))
        _assert_training_only(cursor.isoformat(), chunk_end.isoformat())
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def fetch_one(api, code: str, start: str, end: str, bar_minutes: int) -> tuple[list[tuple], int]:
    _assert_training_only(start, end)
    contract = api.Contracts.Stocks[code]
    parts = []
    source_count = 0
    for chunk_start, chunk_end in request_chunks(start, end):
        print(f"下載 {code} {chunk_start}～{chunk_end}", flush=True)
        raw = api.kbars(contract, start=chunk_start, end=chunk_end, timeout=60000)
        frame = pd.DataFrame({**raw})
        source_count += len(frame)
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return [], 0
    minute = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
    minute["timestamp"] = minute["ts"].map(sino_ts_to_local_timestamp)
    minute["bucket"] = (
        minute["timestamp"] // (bar_minutes * 60) * (bar_minutes * 60)
    ).astype("int64")
    grouped = minute.groupby("bucket", sort=True).agg(
        open=("Open", "first"),
        high=("High", "max"),
        low=("Low", "min"),
        close=("Close", "last"),
        volume=("Volume", "sum"),
    ).reset_index()
    rows = [
        (
            code,
            bar_minutes,
            start,
            end,
            int(row.bucket),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
        )
        for row in grouped.itertuples(index=False)
    ]
    return rows, source_count


def save_training_range(
    database: Path,
    code: str,
    start: str,
    end: str,
    bar_minutes: int,
    rows: list[tuple],
    source_count: int,
) -> None:
    _assert_training_only(start, end)
    lower = int(datetime.combine(date.fromisoformat(start), datetime.min.time(), tzinfo=TAIPEI).timestamp())
    upper = int(
        datetime.combine(
            date.fromisoformat(end) + timedelta(days=1),
            datetime.min.time(),
            tzinfo=TAIPEI,
        ).timestamp()
    )
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "DELETE FROM market_kbars WHERE stock_code=? AND freq_minutes=? AND kbar_timestamp>=? AND kbar_timestamp<?",
            (code, bar_minutes, lower, upper),
        )
        connection.executemany(
            """INSERT INTO market_kbars(
                   stock_code,freq_minutes,range_start,range_end,kbar_timestamp,
                   open,high,low,close,volume
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        connection.execute(
            """INSERT INTO market_kbar_cache_meta(
                   stock_code,freq_minutes,range_start,range_end,source_row_count,
                   source_first_timestamp,source_last_timestamp,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(stock_code,freq_minutes,range_start,range_end) DO UPDATE SET
                   source_row_count=excluded.source_row_count,
                   source_first_timestamp=excluded.source_first_timestamp,
                   source_last_timestamp=excluded.source_last_timestamp,
                   updated_at=excluded.updated_at""",
            (
                code,
                bar_minutes,
                start,
                end,
                source_count,
                rows[0][4] if rows else None,
                rows[-1][4] if rows else None,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="匯入 Technical V1 五檔股票 2023–2024 Training 五分 K；不碰其他資料角色。"
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--force", action="store_true", help="重新匯入已通過完整性檢查的股票")
    args = parser.parse_args()
    config = read_config(CONFIG)
    database = resolve_market_data_path(ROOT, config, args.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    if not database.exists():
        with contextlib.closing(sqlite3.connect(database)):
            pass
    initialize_market_schema(database)
    current = inspect_training_market_data(ROOT, config, database)
    complete_stocks = {
        row["stock_code"] for row in current["stock_checks"] if row["complete"]
    }
    start = config["periods"]["training"]["start"]
    end = config["periods"]["training"]["end"]
    _assert_training_only(start, end)
    load_dotenv(ROOT / ".env")
    api = login_sinopac()
    try:
        for code in config["stock_universe"]["stock_codes"]:
            if code in complete_stocks and not args.force:
                print(f"略過已通過完整性檢查：{code}", flush=True)
                continue
            rows, source_count = fetch_one(
                api, code, start, end, int(config["common_backtest"]["kbar_freq"])
            )
            if not rows:
                raise RuntimeError(f"Shioaji 沒有回傳 {code} 的 Training 行情")
            save_training_range(
                database,
                code,
                start,
                end,
                int(config["common_backtest"]["kbar_freq"]),
                rows,
                source_count,
            )
            print(f"已存入 {code}：{len(rows):,} 根五分 K", flush=True)
    finally:
        api.logout()
    final = inspect_training_market_data(ROOT, config, database)
    print(f"Training 資料完整：{final['complete']}")
    if not final["complete"]:
        print(final["reason"])
    return 0 if final["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
