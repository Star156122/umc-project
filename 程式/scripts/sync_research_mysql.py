"""將本機研究成果同步至 XAMPP MariaDB，不重新執行回測。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from trading_system.research import read_research

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATABASE = ROOT / "data/research.sqlite3"
SCHEMA = ROOT / "database/研究工作台資料表.sql"


def connection_settings() -> dict:
    if os.getenv("DB_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("DB_ENABLED 不是 true，已停止同步。")
    user = os.getenv("DB_USER", "").strip()
    if not user:
        raise RuntimeError("DB_USER 尚未設定。")
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": user,
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "ai_stock_system"),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def create_schema(cursor) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    statements = []
    current = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    for statement in statements:
        if statement:
            cursor.execute(statement)


def sync_stock_catalog(cursor) -> None:
    catalog = guarded_json_loads((ROOT / "configs/stocks.json").read_text(encoding="utf-8"))
    cursor.executemany(
        """INSERT INTO stocks(stock_id,stock_name,market,industry) VALUES (%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE stock_name=VALUES(stock_name),market=VALUES(market),
             industry=VALUES(industry)""",
        [(row["code"], row["name"], row["market"], row["industry"])
         for row in catalog["stocks"]],
    )


def sync_payload(connection, payload: dict) -> int:
    assert_payload(payload)
    identity = "|".join((payload["created_at"], payload["code"], payload["start"],
                         payload["end"], payload["config_sha256"]))
    source_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    created_at = datetime.fromisoformat(payload["created_at"]).replace(tzinfo=None)
    with connection.cursor() as cursor:
        create_schema(cursor)
        sync_stock_catalog(cursor)
        cursor.execute(
            """INSERT INTO research_runs
               (source_key,created_at,stock_code,period_start,period_end,initial_capital,
                lookback_days,regime_threshold,config_sha256,parameter_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE run_id=LAST_INSERT_ID(run_id),
                 initial_capital=VALUES(initial_capital), lookback_days=VALUES(lookback_days),
                 regime_threshold=VALUES(regime_threshold), parameter_status=VALUES(parameter_status)""",
            (source_key, created_at, payload["code"], payload["start"], payload["end"],
             payload["capital"], payload["lookback"], payload["threshold"],
             payload["config_sha256"], payload["parameter_status"]),
        )
        run_id = int(cursor.lastrowid)
        cursor.execute("DELETE FROM strategy_metrics WHERE run_id=%s", (run_id,))
        for ranking, strategy in enumerate(payload["strategies"], start=1):
            key, summary = strategy["key"], strategy["summary"]
            cursor.execute(
                """INSERT INTO strategy_metrics VALUES
                   (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, key, strategy["label"], strategy["source"], ranking,
                 summary["total_return"], summary["max_drawdown"], summary["sharpe_ratio"],
                 summary["win_rate"], summary["transaction_cost"], summary["buy_and_hold_return"]),
            )
            cursor.executemany(
                "INSERT INTO regime_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(run_id, key, row["regime"], row["days"], row["pnl"], row["contribution"],
                  row["fills"], row["cost"]) for row in strategy["regimes"]],
            )
            cursor.executemany(
                "INSERT INTO daily_risk VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(run_id, key, row["day"], row["regime"], row["equity"], row["pnl"],
                  row["drawdown"], row["fills"], row["cost"]) for row in strategy["days"]],
            )
            cursor.executemany(
                "INSERT INTO trade_records VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(run_id, key, sequence, row["datetime"], int(row["timestamp"]), row["action"],
                  float(row["price"]), int(row["quantity"]), float(row["fee"]), float(row["tax"]),
                  float(row["net_cash_flow"]), float(row["cash_after"]))
                 for sequence, row in enumerate(strategy["trades"], start=1)],
            )
    connection.commit()
    return run_id


def list_mysql_research_runs(connection) -> list[dict]:
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(
            """SELECT r.run_id,r.stock_code,r.period_start,r.period_end,r.created_at,
                      COALESCE(s.stock_name,r.stock_code) AS stock_name,
                      COALESCE(s.industry,'未分類') AS industry
               FROM research_runs r LEFT JOIN stocks s ON s.stock_id=r.stock_code
               ORDER BY r.created_at DESC,r.run_id DESC"""
        )
        return [{**row, "period_start": row["period_start"].isoformat(),
                 "period_end": row["period_end"].isoformat(),
                 "created_at": row["created_at"].isoformat()} for row in cursor.fetchall()]


def read_latest_mysql(connection, run_id: int | None = None) -> dict:
    """從 MariaDB 五張研究表重組工作台需要的資料。"""
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        if run_id is None:
            cursor.execute("SELECT * FROM research_runs ORDER BY run_id DESC LIMIT 1")
        else:
            cursor.execute("SELECT * FROM research_runs WHERE run_id=%s", (run_id,))
        run = cursor.fetchone()
        if not run:
            raise ValueError("MariaDB 尚無研究資料，請先執行同步。")
        assert_payload(run)
        cursor.execute("SELECT * FROM strategy_metrics WHERE run_id=%s ORDER BY ranking", (run["run_id"],))
        metrics = cursor.fetchall()
        strategies = []
        for metric in metrics:
            key = metric["strategy_key"]
            cursor.execute(
                """SELECT regime,trading_days,pnl,contribution,fills,transaction_cost
                   FROM regime_metrics WHERE run_id=%s AND strategy_key=%s
                   ORDER BY FIELD(regime,'上漲','盤整','下跌','資料不足')""",
                (run["run_id"], key),
            )
            regimes = [{"regime": row["regime"], "days": row["trading_days"],
                        "pnl": float(row["pnl"]), "contribution": row["contribution"],
                        "fills": row["fills"], "cost": float(row["transaction_cost"])}
                       for row in cursor.fetchall()]
            cursor.execute(
                """SELECT trade_date,regime,equity,daily_pnl,max_drawdown,fills,transaction_cost
                   FROM daily_risk WHERE run_id=%s AND strategy_key=%s ORDER BY trade_date""",
                (run["run_id"], key),
            )
            days = [{"day": row["trade_date"].isoformat(), "regime": row["regime"],
                     "equity": float(row["equity"]), "pnl": float(row["daily_pnl"]),
                     "drawdown": row["max_drawdown"], "fills": row["fills"],
                     "cost": float(row["transaction_cost"])} for row in cursor.fetchall()]
            cursor.execute(
                """SELECT traded_at,trade_timestamp,action,price,quantity,fee,tax,net_cash_flow,cash_after
                   FROM trade_records WHERE run_id=%s AND strategy_key=%s ORDER BY sequence_no""",
                (run["run_id"], key),
            )
            trades = [{"datetime": row["traded_at"].strftime("%Y-%m-%d %H:%M:%S"),
                       "timestamp": row["trade_timestamp"], "action": row["action"],
                       "price": float(row["price"]), "quantity": row["quantity"],
                       "fee": float(row["fee"]), "tax": float(row["tax"]),
                       "net_cash_flow": float(row["net_cash_flow"]),
                       "cash_after": float(row["cash_after"])} for row in cursor.fetchall()]
            strategies.append({
                "key": key, "label": metric["strategy_label"], "source": metric["source_report"],
                "summary": {name: float(metric[name]) for name in (
                    "total_return", "max_drawdown", "sharpe_ratio", "win_rate",
                    "transaction_cost", "buy_and_hold_return")},
                "regimes": regimes, "days": days, "trades": trades,
            })
    return {"created_at": run["created_at"].isoformat(), "code": run["stock_code"],
            "start": run["period_start"].isoformat(), "end": run["period_end"].isoformat(),
            "capital": float(run["initial_capital"]), "lookback": run["lookback_days"],
            "threshold": run["regime_threshold"], "config_sha256": run["config_sha256"],
            "parameter_status": run["parameter_status"], "data_source": "MariaDB",
            "strategies": strategies}


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="同步最新研究成果到 MariaDB")
    parser.add_argument("--check", action="store_true", help="只測試連線及顯示資料筆數")
    args = parser.parse_args()
    connection = pymysql.connect(**connection_settings())
    try:
        if not args.check:
            run_id = sync_payload(connection, read_research(LOCAL_DATABASE))
            print(f"同步完成：research_runs.run_id={run_id}")
        with connection.cursor() as cursor:
            counts = {}
            for table in ("research_runs", "strategy_metrics", "regime_metrics", "daily_risk", "trade_records"):
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = cursor.fetchone()[0]
        print("資料筆數：" + ", ".join(f"{name}={count}" for name, count in counts.items()))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
