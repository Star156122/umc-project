"""下載產業泛化研究新增股票的 2023-2024 Training 5 分 K。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_ml_kbars import fetch_one, save
from trading_system.backtest import login_sinopac


DATABASE = ROOT / "data/market_data.sqlite3"

# 這次新增的六檔股票
STOCK_CODES = [
    "2379",  # 瑞昱
    "2454",  # 聯發科
    "3034",  # 聯詠
    "2884",  # 玉山金
    "2886",  # 兆豐金
    "2891",  # 中信金
]

# 目前只允許 Training
START_DATE = "2023-01-01"
END_DATE = "2024-12-31"

BAR_MINUTES = 5


def already_exists(
    database: Path,
    code: str,
    start: str,
    end: str,
    bar_minutes: int,
) -> bool:
    """檢查資料庫是否已經有這檔股票完整區間的資料。"""

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM market_kbars
            WHERE stock_code=?
              AND freq_minutes=?
              AND range_start=?
              AND range_end=?
            """,
            (code, bar_minutes, start, end),
        ).fetchone()

    return row is not None and int(row[0]) > 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下載科技與金融產業新增股票的 2023-2024 Training 5 分 K"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="即使資料已存在也重新下載",
    )

    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    print("========== Industry Training Kbars ==========")
    print(f"期間：{START_DATE} ~ {END_DATE}")
    print(f"K棒：{BAR_MINUTES} 分 K")
    print(f"股票：{', '.join(STOCK_CODES)}")
    print(f"資料庫：{DATABASE}")
    print()

    api = login_sinopac()

    try:
        for index, code in enumerate(STOCK_CODES, start=1):

            print("=" * 60)
            print(f"[{index}/{len(STOCK_CODES)}] 股票 {code}")

            if (
                not args.force
                and already_exists(
                    DATABASE,
                    code,
                    START_DATE,
                    END_DATE,
                    BAR_MINUTES,
                )
            ):
                print(f"已有資料，略過：{code}")
                continue

            rows, source_count, first_ts, last_ts = fetch_one(
                api,
                code,
                START_DATE,
                END_DATE,
                BAR_MINUTES,
            )

            if not rows:
                raise RuntimeError(
                    f"Shioaji 沒有回傳 {code} "
                    f"{START_DATE} ~ {END_DATE} 的資料"
                )

            save(
                DATABASE,
                code,
                START_DATE,
                END_DATE,
                BAR_MINUTES,
                rows,
                source_count,
                first_ts,
                last_ts,
            )

            print(
                f"完成 {code}："
                f"已存入 {len(rows):,} 根 5 分 K，"
                f"來源分鐘 K {source_count:,} 根"
            )

    finally:
        api.logout()

    print()
    print("========== 全部完成 ==========")

    # 最後做一次簡單確認
    with sqlite3.connect(DATABASE) as connection:
        for code in STOCK_CODES:
            row = connection.execute(
                """
                SELECT
                    COUNT(*),
                    MIN(kbar_timestamp),
                    MAX(kbar_timestamp)
                FROM market_kbars
                WHERE stock_code=?
                  AND freq_minutes=?
                  AND range_start=?
                  AND range_end=?
                """,
                (
                    code,
                    BAR_MINUTES,
                    START_DATE,
                    END_DATE,
                ),
            ).fetchone()

            print(
                f"{code}: "
                f"{int(row[0]):,} 根 5 分 K"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())