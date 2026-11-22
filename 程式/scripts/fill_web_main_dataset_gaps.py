"""Build one bounded website market database without touching source databases.

Only the user-confirmed gaps are downloaded.  The output is restricted to
2024-05-01 through 2025-06-30; Additional Holdout and Final OOS are never
queried.  This script does not train models or run a backtest.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_ml_kbars import fetch_one
from trading_system.research_guard import assert_development_period, assert_timestamps


TZ = ZoneInfo("Asia/Taipei")
BOUND_START = "2024-05-01"
BOUND_END = "2025-06-30"
STOCKS = ("2303", "2330", "2379", "2454", "3034", "2357", "3231", "2892", "4938")
GAPS = {
    "2379": [("2025-01-01", "2025-06-30")],
    "2454": [("2025-01-01", "2025-06-30")],
    "3034": [("2025-01-01", "2025-06-30")],
    "2357": [("2024-05-01", "2024-10-31")],
    "3231": [("2024-05-01", "2024-10-31")],
    "2892": [("2024-05-01", "2024-10-31")],
    "4938": [("2024-05-01", "2024-10-31")],
}
SOURCES = {
    "2303": ROOT / "data/market_data.sqlite3",
    "2330": ROOT / "data/market_data.sqlite3",
    "2379": ROOT / "data/market_data.sqlite3",
    "2454": ROOT / "data/market_data.sqlite3",
    "3034": ROOT / "data/market_data.sqlite3",
    "2357": ROOT / "獨立實驗/華碩2357_2025上半年/資料/2357_history.sqlite3",
    "3231": ROOT / "獨立實驗/緯創3231_2025上半年/資料/3231_history.sqlite3",
    "2892": ROOT / "獨立實驗/第一金2892_2025上半年/資料/2892_history.sqlite3",
    # Use the already separated H1-only copy.  Never open the formal H2 database here.
    "4938": ROOT / "data/web_main_4938_h1/history.sqlite3",
}
INDEX_SOURCES = (
    ROOT / "獨立實驗/資料/index_daily.json",
    ROOT / "獨立實驗/華碩2357_2025上半年/資料/index_daily.json",
)
OUTPUT = ROOT / "data/web_main_bounded_2024h2_2025h1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bounds() -> tuple[int, int]:
    first = int(datetime.fromisoformat(BOUND_START).replace(tzinfo=TZ).timestamp())
    last = int((datetime.fromisoformat(BOUND_END) + timedelta(days=1)).replace(tzinfo=TZ).timestamp())
    return first, last


def read_source_rows(path: Path, stock: str) -> list[tuple]:
    if not path.is_file():
        raise FileNotFoundError(path)
    first, last = bounds()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(market_kbars)")}
        if {"range_start", "range_end"}.issubset(columns):
            rows = list(connection.execute(
                "SELECT stock_code,freq_minutes,range_start,range_end,kbar_timestamp,open,high,low,close,volume "
                "FROM market_kbars WHERE stock_code=? AND freq_minutes=5 "
                "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
                (stock, first, last),
            ))
        else:
            raw = list(connection.execute(
                "SELECT stock_code,freq_minutes,kbar_timestamp,open,high,low,close,volume "
                "FROM market_kbars WHERE stock_code=? AND freq_minutes=5 "
                "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
                (stock, first, last),
            ))
            rows = [(row[0], row[1], BOUND_START, BOUND_END, *row[2:]) for row in raw]
    assert_timestamps(row[4] for row in rows)
    return rows


def merge_rows(existing: list[tuple], downloaded: list[tuple]) -> list[tuple]:
    merged: dict[tuple[str, int, int], tuple] = {}
    for row in existing + downloaded:
        key = (str(row[0]), int(row[1]), int(row[4]))
        old = merged.get(key)
        if old is not None and tuple(old[5:]) != tuple(row[5:]):
            raise RuntimeError(f"Conflicting OHLCV rows for {key}")
        merged[key] = tuple(row)
    rows = sorted(merged.values(), key=lambda row: (str(row[0]), int(row[4])))
    assert_timestamps(row[4] for row in rows)
    return rows


def merged_index() -> list[dict]:
    by_date: dict[str, dict] = {}
    for path in INDEX_SOURCES:
        rows = json.loads(path.read_text(encoding="utf8"))
        for row in rows:
            day = str(row["date"])
            if BOUND_START <= day <= BOUND_END:
                old = by_date.get(day)
                if old is not None and float(old["close"]) != float(row["close"]):
                    raise RuntimeError(f"Conflicting official index close on {day}")
                by_date[day] = row
    result = [by_date[day] for day in sorted(by_date)]
    # The bound may begin on a holiday (2024-05-01); require the official
    # calendar's in-range rows, not a fabricated row for every calendar date.
    if not result or result[-1]["date"] != BOUND_END:
        raise RuntimeError("Official index does not cover the bounded website interval")
    for row in result:
        assert_development_period(row["date"], row["date"])
    return result


def fetch_gaps() -> tuple[list[tuple], list[dict]]:
    load_dotenv(ROOT / ".env")
    if not os.getenv("API_KEY") or not os.getenv("API_SECRET"):
        raise RuntimeError("MISSING_SHIOAJI_API_CREDENTIALS: set API_KEY and API_SECRET in 程式/.env")
    import shioaji as sj

    api = sj.Shioaji(simulation=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        api.login(api_key=os.environ["API_KEY"], secret_key=os.environ["API_SECRET"])
    rows: list[tuple] = []
    audit: list[dict] = []
    try:
        for stock, periods in GAPS.items():
            for start, end in periods:
                assert_development_period(start, end)
                part, minute_rows, first, last = fetch_one(api, stock, start, end, 5)
                if not part:
                    raise RuntimeError(f"Provider returned no rows for {stock} {start}..{end}")
                assert_timestamps(row[4] for row in part)
                rows.extend(part)
                audit.append({
                    "stock_code": stock, "start": start, "end": end,
                    "five_minute_rows": len(part), "source_minute_rows": minute_rows,
                    "source_first_timestamp": first, "source_last_timestamp": last,
                })
    finally:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            api.logout()
    return rows, audit


def write_output(rows: list[tuple], index: list[dict], audit: dict) -> None:
    database = OUTPUT / "history.sqlite3"
    index_path = OUTPUT / "index.json"
    audit_path = OUTPUT / "fetch_audit.json"
    if any(path.exists() for path in (database, index_path, audit_path)):
        raise RuntimeError("Bounded website output already exists; preserve it and audit before replacing")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT / "history.sqlite3.incomplete"
    if temporary.exists():
        raise RuntimeError("Incomplete prior output exists; inspect it before retrying")
    with closing(sqlite3.connect(temporary)) as connection:
        connection.execute(
            "CREATE TABLE market_kbars(stock_code TEXT,freq_minutes INTEGER,range_start TEXT,range_end TEXT,"
            "kbar_timestamp INTEGER,open REAL,high REAL,low REAL,close REAL,volume INTEGER,"
            "PRIMARY KEY(stock_code,freq_minutes,kbar_timestamp))"
        )
        connection.executemany("INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        connection.commit()
    temporary.replace(database)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf8")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-only", action="store_true", help="Print bounded sources and gaps without downloading")
    args = parser.parse_args()
    assert_development_period(BOUND_START, BOUND_END)
    plan = {
        "bounded_period": {"start": BOUND_START, "end": BOUND_END},
        "source_databases": {stock: str(path.relative_to(ROOT)) for stock, path in SOURCES.items()},
        "download_gaps": GAPS,
        "additional_holdout_accessed": False,
        "final_oos_accessed": False,
    }
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    source_hashes_before = {str(path.relative_to(ROOT)): sha256(path) for path in sorted(set(SOURCES.values()))}
    existing = []
    for stock in STOCKS:
        existing.extend(read_source_rows(SOURCES[stock], stock))
    downloaded, fetch_audit = fetch_gaps()
    rows = merge_rows(existing, downloaded)
    source_hashes_after = {str(path.relative_to(ROOT)): sha256(path) for path in sorted(set(SOURCES.values()))}
    if source_hashes_before != source_hashes_after:
        raise RuntimeError("A source database changed during the bounded data build")
    audit = {
        "experiment_id": "web-main-data-completion-20261122",
        **plan,
        "source_hashes_before": source_hashes_before,
        "source_hashes_after": source_hashes_after,
        "source_databases_modified": False,
        "fetches": fetch_audit,
        "output_rows": len(rows),
        "output_by_stock": {stock: sum(str(row[0]) == stock for row in rows) for stock in STOCKS},
        "performance_calculated": False,
    }
    write_output(rows, merged_index(), audit)
    print(json.dumps({"status": "DATA_READY_FOR_PREDICTIONS", **audit}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
