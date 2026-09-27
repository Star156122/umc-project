"""既有回測的市場階段分析；不重新下單、不修改原始報告。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAIPEI = timezone(timedelta(hours=8))
REGIMES = ("上漲", "盤整", "下跌", "資料不足")


def freeze_config(source: Path, destination: Path) -> str:
    """第一次保存設定，之後只檢查完整性，絕不覆蓋已保存的版本。"""
    checksum = destination.with_suffix(".sha256")
    if destination.exists():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if not checksum.exists() or checksum.read_text().strip() != digest:
            raise ValueError("固定參數檔已被修改或缺少檢查碼，請確認原始版本。")
        return digest
    payload = source.read_bytes()
    guarded_json_loads(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    destination.write_bytes(payload)
    checksum.write_text(digest + "\n", encoding="ascii")
    return digest


def classify_days(closes: list[tuple[str, float]], lookback=20, threshold=0.05):
    """當天標籤只使用前一天以前的收盤價，避免看見當日或未來行情。"""
    if lookback < 1 or not 0 < threshold < 1:
        raise ValueError("觀察天數須大於零，漲跌門檻須介於 0 與 1。")
    result = {}
    for i, (day, _) in enumerate(closes):
        label = "資料不足"
        if i > lookback:
            change = closes[i - 1][1] / closes[i - 1 - lookback][1] - 1
            label = "上漲" if change > threshold else "下跌" if change < -threshold else "盤整"
        result[day] = label
    return result


def equity_days(bars, trades, capital, labels):
    ordered = sorted(trades, key=lambda t: int(t["timestamp"]))
    cash, position, index, peak = float(capital), 0, 0, float(capital)
    daily = {}
    for bar in bars:
        stamp = int(bar["kbar_timestamp"])
        day = datetime.fromtimestamp(stamp, TAIPEI).date().isoformat()
        row = daily.setdefault(day, {"day": day, "regime": labels[day], "fills": 0,
                                     "cost": 0.0, "drawdown": 0.0})
        while index < len(ordered) and int(ordered[index]["timestamp"]) <= stamp:
            trade = ordered[index]
            if trade["action"] not in ("Buy", "Sell"):
                raise ValueError("未知的成交方向")
            cash += float(trade["net_cash_flow"])
            position += int(trade["quantity"]) * (1 if trade["action"] == "Buy" else -1)
            row["fills"] += 1
            row["cost"] += float(trade["fee"]) + float(trade["tax"])
            index += 1
        equity = cash + position * float(bar["close"])
        peak = max(peak, equity)
        row["drawdown"] = max(row["drawdown"], (peak - equity) / peak)
        row["equity"] = equity
    if index != len(ordered):
        raise ValueError("部分成交晚於最後一根 K 棒，無法完整計算資產。")
    previous = float(capital)
    for row in daily.values():
        row["pnl"] = row["equity"] - previous
        previous = row["equity"]
    return list(daily.values())


def aggregate_days(days, capital):
    result = []
    for label in REGIMES:
        selected = [day for day in days if day["regime"] == label]
        pnl = sum(day["pnl"] for day in selected)
        result.append({"regime": label, "days": len(selected), "pnl": pnl,
                       "contribution": pnl / capital,
                       "fills": sum(day["fills"] for day in selected),
                       "cost": sum(day["cost"] for day in selected)})
    return result


def build_research(root: Path, lookback=20, threshold=0.05):
    frozen = root / "configs/frozen_baseline.json"
    digest = freeze_config(root / "backtest_config.json", frozen)
    config = guarded_json_loads(frozen.read_text(encoding="utf-8"))
    start, end = config["backtest_start"], config["backtest_end"]
    code = str(config["profiles"][config["batch_profiles"][0]]["code"])
    reports = root / "reports" / code
    latest = {}
    for path in reports.glob("*/summary.json"):
        summary = guarded_json_loads(path.read_text(encoding="utf-8"))
        if (summary.get("backtest_start"), summary.get("backtest_end")) != (start, end):
            continue
        if "test" in str(summary.get("run_name", "")).lower():
            continue
        key = summary["strategy"]
        if key not in {"ma", "rsi", "macd", "bollinger", "breakout", "vote"}:
            continue
        if key not in latest or summary["generated_at"] > latest[key][1]["generated_at"]:
            latest[key] = (path, summary)
    if len(latest) != 6:
        raise ValueError("同一期間需有完整六策略報告，請先完成回測。")
    capital = float(next(iter(latest.values()))[1]["initial_capital"])
    if any(float(s["initial_capital"]) != capital for _, s in latest.values()):
        raise ValueError("六策略本金不同，不能直接放在同一組比較。")
    market = root / config.get("data_cache_path", "data/market_data.sqlite3")
    with closing(sqlite3.connect(market.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        bars = [dict(row) for row in conn.execute(
            "SELECT * FROM market_kbars WHERE stock_code=? AND freq_minutes=? "
            "AND range_start=? AND range_end=? ORDER BY kbar_timestamp",
            (code, config["kbar_freq"], start, end))]
    if not bars:
        raise ValueError("找不到這個期間的 K 棒快取，請先執行原本的回測。")
    closes = {}
    for bar in bars:
        day = datetime.fromtimestamp(bar["kbar_timestamp"], TAIPEI).date().isoformat()
        closes[day] = float(bar["close"])
    labels = classify_days(list(closes.items()), lookback, threshold)
    strategies = []
    for key, (path, summary) in latest.items():
        with (path.parent / "trades.csv").open(encoding="utf-8-sig", newline="") as stream:
            trades = list(csv.DictReader(stream))
        days = equity_days(bars, trades, capital, labels)
        if abs(days[-1]["equity"] - float(summary["final_assets"])) > 0.02:
            raise ValueError(f"{key} 的重建資產與原報告不符，停止產生比較。")
        strategies.append({"key": key, "label": summary.get("strategy_label", key),
                           "summary": {k: summary[k] for k in (
                               "total_return", "max_drawdown", "sharpe_ratio", "win_rate",
                               "transaction_cost", "buy_and_hold_return")},
                           "source": str(path.relative_to(root)),
                           "days": days, "regimes": aggregate_days(days, capital), "trades": trades})
    return {"created_at": datetime.now(TAIPEI).isoformat(), "code": code,
            "start": start, "end": end, "capital": capital, "lookback": lookback,
            "threshold": threshold, "config_sha256": digest,
            "parameter_status": "既有報告未保存完整參數，無法證明與新保存的固定版本完全相同。",
            "strategies": sorted(strategies, key=lambda s: s["summary"]["total_return"], reverse=True)}


def save_research(database: Path, payload):
    """將研究結果拆表保存；整批寫入共用同一個交易，避免留下半套資料。"""
    assert_payload(payload)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS research_runs (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, stock_code TEXT,
            period_start TEXT, period_end TEXT, initial_capital REAL,
            lookback_days INTEGER, regime_threshold REAL, config_sha256 TEXT,
            parameter_status TEXT, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS strategy_metrics (
            run_id INTEGER NOT NULL, strategy_key TEXT NOT NULL, strategy_label TEXT NOT NULL,
            source_report TEXT NOT NULL, ranking INTEGER NOT NULL,
            total_return REAL NOT NULL, max_drawdown REAL NOT NULL,
            sharpe_ratio REAL NOT NULL, win_rate REAL NOT NULL,
            transaction_cost REAL NOT NULL, buy_and_hold_return REAL NOT NULL,
            PRIMARY KEY(run_id, strategy_key),
            FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS regime_metrics (
            run_id INTEGER NOT NULL, strategy_key TEXT NOT NULL, regime TEXT NOT NULL,
            trading_days INTEGER NOT NULL, pnl REAL NOT NULL, contribution REAL NOT NULL,
            fills INTEGER NOT NULL, transaction_cost REAL NOT NULL,
            PRIMARY KEY(run_id, strategy_key, regime),
            FOREIGN KEY(run_id, strategy_key) REFERENCES strategy_metrics(run_id, strategy_key) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS daily_risk (
            run_id INTEGER NOT NULL, strategy_key TEXT NOT NULL, trade_date TEXT NOT NULL,
            regime TEXT NOT NULL, equity REAL NOT NULL, daily_pnl REAL NOT NULL,
            max_drawdown REAL NOT NULL, fills INTEGER NOT NULL, transaction_cost REAL NOT NULL,
            PRIMARY KEY(run_id, strategy_key, trade_date),
            FOREIGN KEY(run_id, strategy_key) REFERENCES strategy_metrics(run_id, strategy_key) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS trade_records (
            run_id INTEGER NOT NULL, strategy_key TEXT NOT NULL, sequence INTEGER NOT NULL,
            traded_at TEXT NOT NULL, timestamp INTEGER NOT NULL, action TEXT NOT NULL,
            price REAL NOT NULL, quantity INTEGER NOT NULL, fee REAL NOT NULL, tax REAL NOT NULL,
            net_cash_flow REAL NOT NULL, cash_after REAL NOT NULL, raw_json TEXT NOT NULL,
            PRIMARY KEY(run_id, strategy_key, sequence),
            FOREIGN KEY(run_id, strategy_key) REFERENCES strategy_metrics(run_id, strategy_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_metrics_run ON strategy_metrics(run_id, ranking);
        CREATE INDEX IF NOT EXISTS idx_daily_risk_lookup ON daily_risk(run_id, strategy_key, trade_date);
        CREATE INDEX IF NOT EXISTS idx_trade_records_lookup ON trade_records(run_id, strategy_key, timestamp);
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
        additions = {
            "stock_code": "TEXT", "period_start": "TEXT", "period_end": "TEXT",
            "initial_capital": "REAL", "lookback_days": "INTEGER", "regime_threshold": "REAL",
            "config_sha256": "TEXT", "parameter_status": "TEXT", "payload": "TEXT",
        }
        for name, kind in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE research_runs ADD COLUMN {name} {kind}")
        cursor = conn.execute(
            "INSERT INTO research_runs(created_at,stock_code,period_start,period_end,initial_capital,"
            "lookback_days,regime_threshold,config_sha256,parameter_status,payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (payload["created_at"], payload["code"], payload["start"], payload["end"],
             payload["capital"], payload["lookback"], payload["threshold"],
             payload["config_sha256"], payload["parameter_status"], "{}"),
        )
        run_id = int(cursor.lastrowid)
        for ranking, strategy in enumerate(payload["strategies"], start=1):
            summary = strategy["summary"]
            key = strategy["key"]
            conn.execute(
                "INSERT INTO strategy_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, key, strategy["label"], strategy["source"], ranking,
                 summary["total_return"], summary["max_drawdown"], summary["sharpe_ratio"],
                 summary["win_rate"], summary["transaction_cost"], summary["buy_and_hold_return"]),
            )
            conn.executemany(
                "INSERT INTO regime_metrics VALUES (?,?,?,?,?,?,?,?)",
                [(run_id, key, row["regime"], row["days"], row["pnl"], row["contribution"],
                  row["fills"], row["cost"]) for row in strategy["regimes"]],
            )
            conn.executemany(
                "INSERT INTO daily_risk VALUES (?,?,?,?,?,?,?,?,?)",
                [(run_id, key, row["day"], row["regime"], row["equity"], row["pnl"],
                  row["drawdown"], row["fills"], row["cost"]) for row in strategy["days"]],
            )
            conn.executemany(
                "INSERT INTO trade_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, key, sequence, row["datetime"], int(row["timestamp"]), row["action"],
                  float(row["price"]), int(row["quantity"]), float(row["fee"]), float(row["tax"]),
                  float(row["net_cash_flow"]), float(row["cash_after"]),
                  json.dumps(row, ensure_ascii=False))
                 for sequence, row in enumerate(strategy["trades"], start=1)],
            )
    return run_id


def read_research(database: Path):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM research_runs ORDER BY id DESC LIMIT 1").fetchone()
        if run is None:
            raise ValueError("研究資料庫沒有分析結果")
        assert_payload(dict(run))
        if run["stock_code"] is None and run["payload"]:
            return guarded_json_loads(run["payload"])
        strategies = []
        for metric in conn.execute(
            "SELECT * FROM strategy_metrics WHERE run_id=? ORDER BY ranking", (run["id"],)
        ):
            key = metric["strategy_key"]
            regimes = [{"regime": row["regime"], "days": row["trading_days"], "pnl": row["pnl"],
                        "contribution": row["contribution"], "fills": row["fills"],
                        "cost": row["transaction_cost"]} for row in conn.execute(
                            "SELECT * FROM regime_metrics WHERE run_id=? AND strategy_key=? "
                            "ORDER BY CASE regime WHEN '上漲' THEN 1 WHEN '盤整' THEN 2 WHEN '下跌' THEN 3 ELSE 4 END",
                            (run["id"], key))]
            days = [{"day": row["trade_date"], "regime": row["regime"], "equity": row["equity"],
                     "pnl": row["daily_pnl"], "drawdown": row["max_drawdown"],
                     "fills": row["fills"], "cost": row["transaction_cost"]} for row in conn.execute(
                         "SELECT * FROM daily_risk WHERE run_id=? AND strategy_key=? ORDER BY trade_date",
                         (run["id"], key))]
            trades = [guarded_json_loads(row["raw_json"]) for row in conn.execute(
                "SELECT raw_json FROM trade_records WHERE run_id=? AND strategy_key=? ORDER BY sequence",
                (run["id"], key))]
            strategies.append({"key": key, "label": metric["strategy_label"],
                               "source": metric["source_report"],
                               "summary": {name: metric[name] for name in (
                                   "total_return", "max_drawdown", "sharpe_ratio", "win_rate",
                                   "transaction_cost", "buy_and_hold_return")},
                               "regimes": regimes, "days": days, "trades": trades})
        return {"created_at": run["created_at"], "code": run["stock_code"],
                "start": run["period_start"], "end": run["period_end"],
                "capital": run["initial_capital"], "lookback": run["lookback_days"],
                "threshold": run["regime_threshold"], "config_sha256": run["config_sha256"],
                "parameter_status": run["parameter_status"], "strategies": strategies}


def export_standalone_html(template: Path, destination: Path, payload: dict) -> Path:
    """把研究資料嵌入網頁，產生不需啟動伺服器即可開啟的單一 HTML。"""
    assert_payload(payload)
    source = template.read_text(encoding="utf-8")
    marker = "<main>"
    if marker not in source:
        raise ValueError("研究網頁缺少 <main>，無法建立獨立版。")
    safe_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    source = source.replace(marker, f"<script>window.__RESEARCH_DATA__={safe_json};</script>\n{marker}", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return destination
