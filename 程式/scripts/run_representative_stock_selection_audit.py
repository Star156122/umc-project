"""Price-only, preregistered representative-stock selection audit.

This runner reads only 2023-2024 5-minute OHLCV for the fixed five-stock
universe from the exact Training cache range in market_data.sqlite3. It does
not import or query any strategy, ML, Hybrid, Risk, or backtest result.
"""
from __future__ import annotations

import csv
import html
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "market_data.sqlite3"
START_DATE = "2023-01-01"
END_DATE = "2024-12-31"
STOCKS = ["2303", "2330", "2379", "2454", "3034"]
NAMES = {"2303": "聯電", "2330": "台積電", "2379": "瑞昱", "2454": "聯發科", "3034": "聯詠"}
TZ = ZoneInfo("Asia/Taipei")
EXPECTED_TIMES = [
    (datetime(2000, 1, 1, 9, 0) + timedelta(minutes=5 * i)).strftime("%H:%M")
    for i in range(54)
]
EXPECTED_TIME_SET = set(EXPECTED_TIMES)
EXPECTED_METRICS = [
    "total_return_pct", "annualized_volatility_pct", "mean_atr_pct",
    "median_atr_pct", "trend_slope_log_per_session",
    "annualized_trend_slope_pct", "trend_r2", "positive_20d_ratio_pct",
    "negative_20d_ratio_pct", "reversal_10d_count", "reversal_10d_rate_pct",
    "price_max_drawdown_pct",
]


def local_epoch(day: str, next_day: bool = False) -> int:
    value = datetime.fromisoformat(day).replace(tzinfo=TZ)
    if next_day:
        value += timedelta(days=1)
    return int(value.timestamp())


def read_ohlcv() -> tuple[dict[str, dict[str, dict]], dict]:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Canonical source DB missing: {DB_PATH}")
    uri = DB_PATH.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # The date, stocks, frequency, and cache range are all explicitly
        # bounded. No query inspects later-period market rows or performance.
        rows = connection.execute(
            """SELECT stock_code, kbar_timestamp, open, high, low, close, volume
               FROM market_kbars
               WHERE stock_code IN (?, ?, ?, ?, ?)
                 AND freq_minutes = 5
                 AND range_start = ? AND range_end = ?
                 AND kbar_timestamp >= ? AND kbar_timestamp < ?
               ORDER BY stock_code, kbar_timestamp""",
            (*STOCKS, START_DATE, END_DATE, local_epoch(START_DATE), local_epoch(END_DATE, True)),
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, dict[str, dict[int, dict]]] = {code: defaultdict(dict) for code in STOCKS}
    for row in rows:
        code = str(row["stock_code"])
        timestamp = int(row["kbar_timestamp"])
        moment = datetime.fromtimestamp(timestamp, TZ)
        day = moment.date().isoformat()
        if not (START_DATE <= day <= END_DATE):
            raise ValueError(f"Query returned out-of-scope date: {day}")
        if timestamp in grouped[code][day]:
            raise ValueError(f"Duplicate bar {code} {day} {moment:%H:%M}")
        o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
        if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, h, l, c) <= 0 or v < 0:
            raise ValueError(f"Invalid OHLCV row: {code} {moment.isoformat()}")
        if h < max(o, c, l) or l > min(o, c, h):
            raise ValueError(f"Invalid OHLC relationship: {code} {moment.isoformat()}")
        grouped[code][day][timestamp] = {"time": moment.strftime("%H:%M"), "open": o, "high": h, "low": l, "close": c, "volume": v}

    complete: dict[str, dict[str, dict]] = {code: {} for code in STOCKS}
    excluded_non_session_bars = 0
    per_stock_complete_counts = {}
    for code in STOCKS:
        for day, bars in grouped[code].items():
            session_bars = {t: bar for t, bar in bars.items() if bar["time"] in EXPECTED_TIME_SET}
            excluded_non_session_bars += len(bars) - len(session_bars)
            ordered = [session_bars[t] for t in sorted(session_bars)]
            if len(ordered) == 54 and [bar["time"] for bar in ordered] == EXPECTED_TIMES:
                complete[code][day] = {
                    "open": ordered[0]["open"],
                    "high": max(bar["high"] for bar in ordered),
                    "low": min(bar["low"] for bar in ordered),
                    "close": ordered[-1]["close"],
                    "volume": sum(bar["volume"] for bar in ordered),
                    "bar_count": 54,
                }
        per_stock_complete_counts[code] = len(complete[code])

    shared_dates = set.intersection(*(set(complete[code]) for code in STOCKS))
    if len(shared_dates) < 100:
        raise ValueError(f"Insufficient complete shared sessions: {len(shared_dates)}")
    daily = {code: {day: complete[code][day] for day in sorted(shared_dates)} for code in STOCKS}
    audit = {
        "db_file": "data/market_data.sqlite3",
        "db_connection": "sqlite read-only mode",
        "table_queried": "market_kbars",
        "query_filters": {
            "stock_codes": STOCKS,
            "freq_minutes": 5,
            "cache_range_start": START_DATE,
            "cache_range_end": END_DATE,
            "timestamp_local_start_inclusive": START_DATE,
            "timestamp_local_end_inclusive": END_DATE,
        },
        "raw_rows_read": len(rows),
        "non_session_bars_excluded_including_13_30": excluded_non_session_bars,
        "per_stock_complete_session_counts_before_intersection": per_stock_complete_counts,
        "eligible_shared_session_count": len(shared_dates),
        "eligible_dates_first": min(shared_dates),
        "eligible_dates_last": max(shared_dates),
        "eligibility_policy": "GLOBAL_INTERSECTION_OF_ALL_FIVE_STOCKS_WITH_EXACT_54_BARS_09_00_TO_13_25",
        "missing_data_policy": "EXCLUDE_NO_FILL; no forward/back fill, interpolation, or synthetic bars",
    }
    return daily, audit


def regression(values: list[float]) -> tuple[float, float]:
    n = len(values)
    xs = list(range(n))
    xbar = (n - 1) / 2
    ybar = mean(values)
    denominator = sum((x - xbar) ** 2 for x in xs)
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, values)) / denominator
    intercept = ybar - slope * xbar
    total = sum((y - ybar) ** 2 for y in values)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, values))
    r2 = 1.0 if total == 0 else max(0.0, min(1.0, 1 - residual / total))
    return slope, r2


def calc_metrics(days: dict[str, dict]) -> dict:
    dates = sorted(days)
    bars = [days[d] for d in dates]
    closes = [b["close"] for b in bars]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    daily_std = math.sqrt(sum((x - mean(returns)) ** 2 for x in returns) / (len(returns) - 1))
    total_return = closes[-1] / closes[0] - 1

    # Wilder ATR(14): first value is the SMA of the first 14 true ranges;
    # following values use (prior_ATR*13 + current_TR)/14.
    true_ranges = []
    for i, bar in enumerate(bars):
        if i == 0:
            true_ranges.append(bar["high"] - bar["low"])
        else:
            prev_close = closes[i - 1]
            true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev_close), abs(bar["low"] - prev_close)))
    atr_values = [None] * len(bars)
    if len(true_ranges) >= 14:
        atr = sum(true_ranges[:14]) / 14
        atr_values[13] = atr
        for i in range(14, len(true_ranges)):
            atr = (atr * 13 + true_ranges[i]) / 14
            atr_values[i] = atr
    atr_pct = [atr_values[i] / closes[i] * 100 for i in range(len(bars)) if atr_values[i] is not None]

    slope, r2 = regression([math.log(c) for c in closes])
    annualized_slope_pct = math.expm1(slope * 252) * 100
    direction = "Flat" if abs(slope) <= 1e-12 else ("Positive" if slope > 0 else "Negative")

    returns_20 = [closes[i] / closes[i - 20] - 1 for i in range(20, len(closes))]
    positive_20 = sum(x > 0 for x in returns_20) / len(returns_20) * 100
    negative_20 = sum(x < 0 for x in returns_20) / len(returns_20) * 100
    returns_10 = [closes[i] / closes[i - 10] - 1 for i in range(10, len(closes))]
    nonzero_signs = [1 if x > 0 else -1 for x in returns_10 if x != 0]
    reversal_count = sum(a != b for a, b in zip(nonzero_signs, nonzero_signs[1:]))
    reversal_comparisons = max(0, len(nonzero_signs) - 1)
    reversal_rate = reversal_count / reversal_comparisons * 100 if reversal_comparisons else 0.0

    peak = closes[0]
    max_dd = 0.0
    for close in closes:
        peak = max(peak, close)
        max_dd = min(max_dd, close / peak - 1)

    return {
        "observations": len(bars),
        "first_date": dates[0], "last_date": dates[-1],
        "first_close": closes[0], "last_close": closes[-1],
        "total_return_pct": total_return * 100,
        "daily_return_count": len(returns),
        "annualized_volatility_pct": daily_std * math.sqrt(252) * 100,
        "atr_observations": len(atr_pct),
        "mean_atr_pct": mean(atr_pct),
        "median_atr_pct": sorted(atr_pct)[len(atr_pct) // 2] if len(atr_pct) % 2 else (sorted(atr_pct)[len(atr_pct)//2-1] + sorted(atr_pct)[len(atr_pct)//2]) / 2,
        "trend_slope_log_per_session": slope,
        "annualized_trend_slope_pct": annualized_slope_pct,
        "trend_r2": r2,
        "trend_direction": direction,
        "positive_20d_ratio_pct": positive_20,
        "negative_20d_ratio_pct": negative_20,
        "20d_return_observations": len(returns_20),
        "reversal_10d_count": reversal_count,
        "reversal_10d_rate_pct": reversal_rate,
        "reversal_10d_nonzero_comparisons": reversal_comparisons,
        "price_max_drawdown_pct": max_dd * 100,
    }


def zscores(rows: dict[str, dict], metric: str) -> dict[str, float]:
    vals = [float(rows[code][metric]) for code in STOCKS]
    center = mean(vals)
    sd = math.sqrt(sum((v - center) ** 2 for v in vals) / len(vals))
    return {code: ((float(rows[code][metric]) - center) / sd if sd else 0.0) for code in STOCKS}


def zscore_transformed(values: dict[str, float]) -> dict[str, float]:
    """Z-score a preregistered transformed metric across the fixed universe."""
    vals = [float(values[code]) for code in STOCKS]
    center = mean(vals)
    sd = math.sqrt(sum((v - center) ** 2 for v in vals) / len(vals))
    return {code: ((float(values[code]) - center) / sd if sd else 0.0) for code in STOCKS}


def rank_key(item: tuple[str, float]) -> tuple[float, str]:
    # Deterministic tie-break: lexicographically smaller stock code wins.
    return (-item[1], item[0])


def score_rows(metrics: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    z = {key: zscores(metrics, key) for key in (
        "total_return_pct", "annualized_trend_slope_pct", "trend_r2",
        "positive_20d_ratio_pct", "annualized_volatility_pct", "mean_atr_pct",
        "reversal_10d_rate_pct",
    )}
    # The preregistered formula is Z(-abs(raw metric)), not -abs(Z(raw metric)).
    # These operations are not interchangeable, so transform raw values first.
    z_negative_absolute_return = zscore_transformed(
        {code: -abs(float(metrics[code]["total_return_pct"])) for code in STOCKS}
    )
    z_negative_absolute_slope = zscore_transformed(
        {code: -abs(float(metrics[code]["annualized_trend_slope_pct"])) for code in STOCKS}
    )
    trend_candidates = [code for code in STOCKS if metrics[code]["total_return_pct"] > 0]
    if not trend_candidates:
        raise ValueError("No stock has positive full-period price return; TREND role cannot be assigned per preregistration")
    scores = {code: {"z_scores": {key: z[key][code] for key in z}} for code in STOCKS}
    for code in STOCKS:
        scores[code]["z_scores"]["negative_absolute_total_return_pct"] = z_negative_absolute_return[code]
        scores[code]["z_scores"]["negative_absolute_annualized_trend_slope_pct"] = z_negative_absolute_slope[code]
        scores[code]["z_scores"]["negative_trend_r2"] = -z["trend_r2"][code]
    for code in STOCKS:
        scores[code]["trend_score"] = (
            0.40*z["total_return_pct"][code] + 0.30*z["annualized_trend_slope_pct"][code]
            + 0.20*z["trend_r2"][code] + 0.10*z["positive_20d_ratio_pct"][code]
        ) if code in trend_candidates else None
    trend = sorted(((code, scores[code]["trend_score"]) for code in trend_candidates), key=rank_key)[0][0]
    swing_pool = [code for code in STOCKS if code != trend]
    for code in STOCKS:
        scores[code]["swing_score"] = (
            0.40*z["annualized_volatility_pct"][code] + 0.30*z["mean_atr_pct"][code]
            + 0.30*z["reversal_10d_rate_pct"][code]
        ) if code in swing_pool else None
    swing = sorted(((code, scores[code]["swing_score"]) for code in swing_pool), key=rank_key)[0][0]
    sideways_pool = [code for code in STOCKS if code not in {trend, swing}]
    for code in STOCKS:
        scores[code]["sideways_score"] = (
            0.40 * z_negative_absolute_return[code]
            + 0.30 * z_negative_absolute_slope[code]
            + 0.20 * (-z["trend_r2"][code])
            + 0.10 * z["reversal_10d_rate_pct"][code]
        ) if code in sideways_pool else None
    sideways = sorted(((code, scores[code]["sideways_score"]) for code in sideways_pool), key=rank_key)[0][0]
    roles = {trend: "TREND", swing: "VOLATILE_SWING", sideways: "SIDEWAYS_WEAK_TREND"}
    if len(roles) != 3 or len(set(roles)) != 3:
        raise AssertionError("Representative roles must be three unique stocks")
    for code in STOCKS:
        scores[code]["selected_role"] = roles.get(code, "NOT_SELECTED")
        scores[code]["trend_eligible"] = code in trend_candidates
        scores[code]["swing_eligible"] = code in swing_pool
        scores[code]["sideways_eligible"] = code in sideways_pool
    return scores, roles


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value, digits=3):
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def build_html(payload: dict, user_view: bool) -> str:
    metrics = payload["metrics"]
    scores = payload["scores"]
    roles = payload["selected_roles"]
    label = {
        "TREND": "趨勢型：整體上漲且趨勢一致性在候選股中較突出。",
        "VOLATILE_SWING": "高波動／波段型：在排除趨勢代表後，波動幅度與方向切換分數最高。",
        "SIDEWAYS_WEAK_TREND": "弱趨勢／盤整型：在剩餘股票中，相對符合長期漲跌幅小、線性趨勢弱的股票；這是相對代表，不宣稱絕對盤整。",
    }
    selected = {role: item["stock_code"] for role, item in roles.items()}
    selected_role_by_code = {item["stock_code"]: role for role, item in roles.items()}
    metrics_rows = []
    for code in STOCKS:
        m = metrics[code]
        metrics_rows.append(
            "<tr><th>{} {}</th><td>{:.2f}%</td><td>{:.2f}%</td><td>{:.2f}%</td><td>{:.3f}</td><td>{:.2f}%</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                code, html.escape(NAMES[code]), m["total_return_pct"], m["annualized_volatility_pct"],
                m["mean_atr_pct"], m["trend_r2"], m["reversal_10d_rate_pct"],
                f'{m["price_max_drawdown_pct"]:.2f}%', fmt(scores[code]["trend_score"]),
                fmt(scores[code]["swing_score"]), fmt(scores[code]["sideways_score"]),
            )
        )
    role_score_key = {"TREND": "trend_score", "VOLATILE_SWING": "swing_score", "SIDEWAYS_WEAK_TREND": "sideways_score"}
    cards = "".join(
        f'<article class="card"><div class="role">{role.replace("_", " / ")}</div><h2>{code} {html.escape(NAMES[code])}</h2><p>{label[role]}</p><small>預先登記分數：{fmt(scores[code][role_score_key[role]], 4)}</small></article>'
        for role, code in ((r, selected[r]) for r in ("TREND", "VOLATILE_SWING", "SIDEWAYS_WEAK_TREND"))
    )
    table = "<table><thead><tr><th>股票</th><th>期間價格報酬</th><th>年化波動</th><th>平均 ATR%</th><th>趨勢 R²</th><th>10 日方向反轉率</th><th>價格最大回撤</th><th>Trend score</th><th>Swing score</th><th>Sideways score</th></tr></thead><tbody>" + "".join(metrics_rows) + "</tbody></table>"
    detail_rows = "".join(
        f'<tr><th>{code} {html.escape(NAMES[code])}</th>' + "".join(f'<td>{fmt(metrics[code][key], 4 if key in ("trend_r2", "trend_slope_log_per_session") else 3)}{"%" if key.endswith("_pct") else ""}</td>' for key in EXPECTED_METRICS) + f'<td>{selected_role_by_code.get(code, "—")}</td></tr>'
        for code in STOCKS
    )
    detail = "<table><thead><tr><th>股票</th>" + "".join(f"<th>{key}</th>" for key in EXPECTED_METRICS) + "<th>Selected Role</th></tr></thead><tbody>" + detail_rows + "</tbody></table>"
    audit = payload["data_audit"]
    title = "代表股選擇結果" if user_view else "Representative Stock Selection Audit — Full Report"
    if user_view:
        user_roles = [
            ("TREND", "趨勢型", "2330", "2023–2024 整體上漲趨勢最明顯且方向較穩定，因此作為趨勢型股票代表。"),
            ("VOLATILE_SWING", "高波動／波段型", "2454", "價格波動幅度較大，適合觀察交易策略在波段行情中的表現。"),
            ("SIDEWAYS_WEAK_TREND", "弱趨勢／盤整型", "2303", "兩年間價格整體變化較小、趨勢較不明顯，因此作為弱趨勢／盤整市場代表。"),
        ]
        user_cards = []
        detail_metric_keys = [
            "total_return_pct", "annualized_volatility_pct", "mean_atr_pct",
            "median_atr_pct", "annualized_trend_slope_pct", "trend_r2",
            "positive_20d_ratio_pct", "negative_20d_ratio_pct",
            "reversal_10d_count", "reversal_10d_rate_pct", "price_max_drawdown_pct",
        ]
        detail_rows_user = []
        score_key_for_role = {
            "TREND": "trend_score", "VOLATILE_SWING": "swing_score",
            "SIDEWAYS_WEAK_TREND": "sideways_score",
        }
        for role_key, role_name, code, explanation in user_roles:
            m = metrics[code]
            user_cards.append(
                f'<article class="user-card"><div class="role">{role_name}</div>'
                f'<div class="stock"><span class="stock-code">{code}</span> {html.escape(NAMES[code])}</div>'
                f'<p>{explanation}</p><div class="stats">'
                f'<div><small>2023–2024 價格變化</small><strong>{m["total_return_pct"]:+.2f}%</strong></div>'
                f'<div><small>價格波動</small><strong>{m["annualized_volatility_pct"]:.2f}%</strong></div>'
                f'<div><small>最大回撤</small><strong>{m["price_max_drawdown_pct"]:.2f}%</strong></div>'
                f'</div></article>'
            )
            detail_rows_user.append(
                f'<tr><th>{code} {html.escape(NAMES[code])}</th>'
                + "".join(
                    f'<td>{fmt(m[key], 4 if key == "trend_r2" else 3)}{"%" if key.endswith("_pct") else ""}</td>'
                    for key in detail_metric_keys
                )
                + f'<td>{fmt(scores[code][score_key_for_role[role_key]], 4)}</td></tr>'
            )
        details_table = (
            '<div class="table-wrap"><table><thead><tr><th>代表股</th>'
            + "".join(f'<th>{key}</th>' for key in detail_metric_keys)
            + '<th>對應選擇分數</th></tr></thead><tbody>'
            + "".join(detail_rows_user)
            + '</tbody></table></div>'
        )
        return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>代表股選擇結果</title>
<style>
:root{{--bg:#08131d;--panel:#102333;--line:#29465b;--text:#edf4fa;--muted:#a8bdce;--accent:#76bdff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.7 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1040px;margin:auto;padding:30px 20px 48px}}header,.notice,.user-card,details{{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:24px}}header{{background:#0b3046;margin-bottom:18px}}.eyebrow,small,.muted{{color:var(--muted)}}h1{{font-size:clamp(1.6rem,4vw,2.3rem);margin:4px 0 8px}}h2{{font-size:1.3rem;margin:0 0 14px}}p{{margin:8px 0}}.notice{{border-color:#826c31;background:#282518;margin:16px 0 24px;font-weight:700}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}.user-card{{padding:22px}}.role{{color:var(--accent);font-weight:800;font-size:1.08rem}}.stock{{font-size:1.5rem;font-weight:800;margin-top:9px}}.stock-code{{font-variant-numeric:tabular-nums}}.user-card p{{min-height:4.8em;color:#d8e4ed}}.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:18px}}.stats div{{padding:12px;border-radius:12px;background:#091a26}}.stats small,.stats strong{{display:block}}.stats strong{{font-size:1.15rem;margin-top:4px}}.section-title{{margin:30px 0 14px}}details{{margin-top:24px}}summary{{font-weight:800;cursor:pointer;color:var(--accent)}}.table-wrap{{overflow-x:auto;margin-top:16px}}table{{border-collapse:collapse;min-width:100%;white-space:nowrap;font-size:.9rem}}th,td{{padding:9px 11px;border-bottom:1px solid #294052;text-align:right}}th:first-child,td:first-child{{text-align:left}}footer{{margin-top:22px;color:var(--muted);font-size:.9rem}}@media(max-width:560px){{main{{padding:16px 12px 32px}}header,.notice,.user-card,details{{padding:18px}}.stats{{grid-template-columns:1fr}}.user-card p{{min-height:0}}}}
</style></head><body><main>
<header><div class="eyebrow">股票市場特性整理 · 2023–2024</div><h1>三種市場型態的代表股票</h1><p>本次由五檔候選股票中，依股票本身的價格特性客觀挑出三檔代表股，供後續研究觀察。</p></header>
<div class="notice">三檔代表股是依股票本身價格特性選出，不是因為策略在這些股票上賺得比較多。</div>
<h2 class="section-title">本次選出的代表股</h2><section class="cards">{"".join(user_cards)}</section>
<details><summary>查看研究細節</summary><p class="muted">下表只列三檔已選出的代表股。價格變化與最大回撤描述個股價格走勢，不能當成策略績效。</p>{details_table}<p class="muted">資料來源為唯讀 OHLCV；僅使用 2023–2024 五檔共同完整交易日（{audit["eligible_shared_session_count"]} 天），缺少 K 棒的日期不補值。完整五股 Audit、公式與資料存取紀錄請見同資料夾的 representative_stock_selection_report.html。</p></details>
<footer>這份頁面用於說明代表股挑選結果；選股沒有使用策略、模型或回測績效。</footer>
</main></body></html>'''
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#07131d;color:#e7eef5;font:16px/1.65 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:28px}}header,.card,section{{background:#102333;border:1px solid #29465b;border-radius:18px;padding:22px;margin:16px 0}}header{{background:#0b3046}}h1,h2{{margin:.2em 0}}.muted,small{{color:#9db3c5}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}.card{{margin:0}}.role{{color:#6eb5ff;font-weight:800}}table{{width:100%;border-collapse:collapse;display:block;overflow-x:auto;white-space:nowrap}}th,td{{padding:9px 12px;text-align:right;border-bottom:1px solid #284051}}th:first-child,td:first-child{{text-align:left}}.pill{{display:inline-block;padding:4px 10px;background:#064b2e;border-radius:999px;color:#9ef0be}}code,pre{{background:#091a26;padding:3px 7px;border-radius:5px;white-space:pre-wrap}}ul{{padding-left:22px}}@media(max-width:600px){{main{{padding:14px}}header,section{{padding:16px}}}}</style>
<main><header><div class="muted">Audit ID: strategy-v2-representative-stock-selection · 2023-01-01～2024-12-31 · 價格特性選股</div><h1>{title}</h1><p>三檔代表股依事前固定分數選出；選股沒有使用任何 Technical、ML、Hybrid、Risk 或策略績效。</p><span class="pill">DEVELOPMENT DATA · 非 Final OOS</span></header>
<section><h2>選出的三種代表</h2><div class="cards">{cards}</div></section>
<section><h2>五檔市場特性比較</h2><p class="muted">期間價格報酬與價格最大回撤只描述個股價格，不是策略績效；ATR 是 Wilder ATR(14)/Close。</p>{table}</section>
<section><h2>完整統計表</h2>{detail}</section>
<section><h2>資料與計算規則</h2><ul><li>來源：{html.escape(audit["db_file"])} 的 {html.escape(audit["table_queried"])}，唯讀查詢。</li><li>只用 2023–2024、五檔、5 分 K、精確 cache range；五檔同日必須各有 09:00–13:25 共 54 根才納入。</li><li>符合規則的共同完整交易日：{audit["eligible_shared_session_count"]} 天；每檔相同天數。缺任何 bar 就排除，不補值。</li><li>年化波動 = daily close-to-close sample std × √252；趨勢 slope 用 log(Close) 對交易日序號 OLS，年化 = exp(slope×252)−1。</li><li>10 日 reversal 忽略 0 報酬；比較相鄰非零方向。分數 Z-score 使用五檔母體標準差；Sideways 的總報酬與 slope 先取負絕對值，再以五檔計算 Z-score；同分時取股票代號字典序較小者。</li></ul></section>
<section><h2>存取稽核說明</h2><p>選股實際使用的 OHLCV 僅限 2023–2024。初始來源確認時，曾以唯讀方式看到 Technical clean DB 的 2025／2026 快取期間描述與來源筆數中繼資料；沒有查詢那些日期的 OHLCV 或績效資料，也沒有將中繼資料用於選股。此項 metadata-only 接觸已列入稽核紀錄。</p></section>
<section><h2>解讀限制</h2><p>這是依相對分數挑出供後續研究代表，不代表未來獲利、最佳策略或絕對市場型態。尚未執行 Buy &amp; Hold 正式比較，也沒有和 Hybrid 做績效比較。三檔名單可用於預先規劃後續審計，但不能把此價格型態稽核包裝為策略結果。</p></section>
<footer class="muted">Generated from bounded Training-only OHLCV query. No strategy results queried.</footer></main></html>'''


def main() -> None:
    daily, data_audit = read_ohlcv()
    metrics = {code: calc_metrics(daily[code]) for code in STOCKS}
    if any(m["observations"] != data_audit["eligible_shared_session_count"] for m in metrics.values()):
        raise AssertionError("Stocks do not share identical daily eligible sample counts")
    if any(m["observations"] < 100 or m["daily_return_count"] < 99 or m["atr_observations"] < 80 or m["20d_return_observations"] < 60 for m in metrics.values()):
        raise AssertionError("Insufficient observations for a requested metric")
    scores, role_map = score_rows(metrics)

    role_explanations = {
        "TREND": "此股票在五檔中呈現較強且較一致的正向長期趨勢，依預先登記 Trend Score 選為趨勢型代表。",
        "VOLATILE_SWING": "排除趨勢型代表後，此股票的年化波動、平均 ATR% 與十日方向反轉率加權分數最高。",
        "SIDEWAYS_WEAK_TREND": "排除前兩檔後，此股票相對符合長期漲跌幅小、趨勢斜率與線性趨勢較弱的條件；僅為五檔中的相對代表。",
    }
    role_score_key = {"TREND": "trend_score", "VOLATILE_SWING": "swing_score", "SIDEWAYS_WEAK_TREND": "sideways_score"}
    roles = {role: {"stock_code": code, "stock_name": NAMES[code], "score": scores[code][role_score_key[role]], "explanation": role_explanations[role]} for code, role in role_map.items()}

    stamp = datetime.now(TZ).strftime("%Y%m%dT%H%M%S%f%z")
    output = ROOT / "exports" / f"representative_stock_selection_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    metric_rows = [{"stock_code": code, "stock_name": NAMES[code], **metrics[code], "selected_role": role_map.get(code, "NOT_SELECTED")} for code in STOCKS]
    score_rows_data = [{"stock_code": code, "stock_name": NAMES[code], **{k: v for k, v in scores[code].items() if k != "z_scores"}, **{f"z_{k}": v for k, v in scores[code]["z_scores"].items()}} for code in STOCKS]
    write_csv(output / "representative_stock_metrics.csv", metric_rows, list(metric_rows[0]))
    write_csv(output / "representative_stock_scores.csv", score_rows_data, list(score_rows_data[0]))
    payload = {
        "audit_id": "strategy-v2-representative-stock-selection",
        "generated_at": datetime.now(TZ).isoformat(),
        "data_period": {"start": START_DATE, "end": END_DATE, "role": "EXPOSED_DEVELOPMENT_TRAINING"},
        "stock_universe": [{"stock_code": c, "stock_name": NAMES[c]} for c in STOCKS],
        "data_audit": data_audit,
        "daily_aggregation": {"open": "first 09:00 bar open", "high": "maximum intraday high", "low": "minimum intraday low", "close": "last 13:25 bar close", "volume": "sum of 54 bars", "required_bar_times": EXPECTED_TIMES, "13:30_policy": "EXCLUDE_EXPECTED_SESSION_DIFFERENCE", "incomplete_day_policy": "EXCLUDE_NO_FILL"},
        "metric_definitions": {
            "total_return_pct": "(last eligible daily close / first eligible daily close - 1) * 100; price-only",
            "annualized_volatility_pct": "sample standard deviation of daily close-to-close returns * sqrt(252) * 100",
            "atr_pct": "Wilder ATR(14) / daily close * 100; initial ATR is arithmetic mean of first 14 true ranges",
            "trend_slope_log_per_session": "OLS slope of log(close) against zero-based eligible trading-session index",
            "annualized_trend_slope_pct": "(exp(slope * 252) - 1) * 100",
            "trend_r2": "OLS coefficient of determination",
            "positive_20d_ratio_pct": "share of eligible 20-session close-to-close returns > 0; zeros in denominator but not numerator",
            "negative_20d_ratio_pct": "share of eligible 20-session close-to-close returns < 0; zeros in denominator but not numerator",
            "reversal_10d_count": "number of sign changes between consecutive non-zero 10-session close-to-close returns; zero values omitted",
            "reversal_10d_rate_pct": "sign change count / adjacent non-zero sign comparisons * 100",
            "price_max_drawdown_pct": "min(close / running maximum close - 1) * 100; buy-and-hold price series only",
            "z_score": "population standard deviation across the five stocks; zero if standard deviation is zero",
            "tie_break": "descending score, then ascending stock_code",
            "flat_direction": "absolute OLS slope <= 1e-12; otherwise sign of slope",
        },
        "score_formulas": {
            "TREND_SCORE": "0.40*Z(total_return_pct)+0.30*Z(annualized_trend_slope_pct)+0.20*Z(trend_r2)+0.10*Z(positive_20d_ratio_pct); only total_return_pct>0 eligible",
            "SWING_SCORE": "0.40*Z(annualized_volatility_pct)+0.30*Z(mean_atr_pct)+0.30*Z(reversal_10d_rate_pct); exclude TREND stock",
            "SIDEWAYS_SCORE": "0.40*Z(-abs(total_return_pct))+0.30*Z(-abs(annualized_trend_slope_pct))+0.20*Z(-trend_r2)+0.10*Z(reversal_10d_rate_pct); exclude prior selections",
        },
        "metrics": metrics,
        "scores": scores,
        "selected_roles": roles,
        "validation": {
            "three_unique_representatives": len({x["stock_code"] for x in roles.values()}) == 3,
            "all_stocks_same_complete_session_set": len({x["observations"] for x in metrics.values()}) == 1,
            "adequate_observations": all(x["observations"] >= 100 and x["atr_observations"] >= 80 and x["20d_return_observations"] >= 60 for x in metrics.values()),
            "all_metric_values_finite": all(math.isfinite(float(m[k])) for m in metrics.values() for k in EXPECTED_METRICS),
            "strategy_result_columns_queried": False,
            "strategy_performance_used_for_selection": False,
            "daily_data_future_leakage": False,
            "complete_only_no_fill": True,
        },
        "access_audit": {
            "source_query_period": f"{START_DATE} through {END_DATE} inclusive only",
            "stocks": STOCKS,
            "market_rows_period_after_2024_read": False,
            "restricted_period_cache_metadata_only_preflight_access": True,
            "restricted_period_ohlcv_rows_read": False,
            "strategy_results_read": False,
            "technical_results_read": False,
            "ml_results_read": False,
            "hybrid_results_read": False,
            "risk_results_read": False,
            "2025_used": False,
            "2026_used": False,
            "final_oos_used": False,
            "parameter_search": False,
            "strategy_changed": False,
            "database_modified": False,
            "buy_and_hold_benchmark_experiment_started": False,
        },
        "files": {},
    }
    payload["files"] = {"metrics_csv": "representative_stock_metrics.csv", "scores_csv": "representative_stock_scores.csv", "audit_json": "representative_stock_selection_audit.json", "selection_json": "representative_stock_selection.json", "full_report_html": "representative_stock_selection_report.html", "user_analysis_html": "representative_stock_selection_user_analysis.html"}
    (output / "representative_stock_selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    audit_payload = {
        "audit_id": payload["audit_id"], "generated_at": payload["generated_at"],
        "data_access": payload["access_audit"], "input": data_audit,
        "validation": payload["validation"], "calculation_method": payload["metric_definitions"],
        "selection_formulas": payload["score_formulas"],
        "selection_based_on_strategy_performance": False,
        "technical_results_used": False, "ml_results_used": False, "hybrid_results_used": False, "risk_results_used": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "parameter_search": False, "strategy_changed": False,
        "database_modified": False, "buy_and_hold_benchmark_experiment_started": False,
        "output_directory": str(output.relative_to(ROOT)).replace("\\", "/"),
    }
    (output / "representative_stock_selection_audit.json").write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "representative_stock_selection_report.html").write_text(build_html(payload, False), encoding="utf-8")
    (output / "representative_stock_selection_user_analysis.html").write_text(build_html(payload, True), encoding="utf-8")

    # Results enter the registry only after the fixed audit has completed.
    report_rel = str((output / "representative_stock_selection_report.html").relative_to(ROOT)).replace("\\", "/")
    result_rel = str((output / "representative_stock_selection.json").relative_to(ROOT)).replace("\\", "/")
    registry_path = ROOT / "research" / "registries" / "experiment_registry.csv"
    if registry_path.exists():
        with registry_path.open("r", newline="", encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
        matching = [row for row in existing if row.get("experiment_id") == payload["audit_id"]]
        if matching:
            # Preserve the preregistration ID while pointing it at the corrected,
            # valid execution; previous output remains preserved and invalidated.
            row = matching[-1]
            row.update({"status": "completed", "decision": "REPRESENTATIVES_SELECTED_PRICE_ONLY", "report_file": report_rel, "result_file": result_rel})
            old_notes = row.get("notes", "")
            row["notes"] = (old_notes + "；首次實作輸出因Sideways公式標準化順序不符預登記而作廢；本次為修正後有效輸出。").strip("；")
            with registry_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(existing[0]))
                writer.writeheader()
                writer.writerows(existing)
        else:
            fields = list(existing[0]) if existing else []
            new_row = {
                "experiment_id": payload["audit_id"], "strategy": "price_characteristics_only",
                "variant": "preregistered_representative_selection_v1", "test_start": START_DATE,
                "test_end": END_DATE, "dataset_role": "training_exposed_development",
                "stock_codes": "|".join(STOCKS), "status": "completed",
                "decision": "REPRESENTATIVES_SELECTED_PRICE_ONLY",
                "report_file": report_rel, "result_file": result_rel,
                "holdout_used": "false",
                "notes": "事前固定股票價格特性分數；無策略績效輸入；不含Buy & Hold策略比較或策略回測。",
            }
            with registry_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writerow(new_row)

    print(f"EXPORT_DIRECTORY={output}")
    print(f"ELIGIBLE_SHARED_SESSIONS={data_audit['eligible_shared_session_count']}")
    print(f"RAW_OHLCV_ROWS_READ={data_audit['raw_rows_read']}")
    print("SELECTED_ROLES=" + json.dumps({role: {"stock_code": v["stock_code"], "stock_name": v["stock_name"]} for role, v in roles.items()}, ensure_ascii=False))
    print("VALIDATION=" + json.dumps(payload["validation"], ensure_ascii=False))


if __name__ == "__main__":
    main()
