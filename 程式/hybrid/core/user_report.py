"""User-friendly presentation for an existing Hybrid POC result set.

This module intentionally performs presentation-only aggregation.  It receives
the same audit, summary, detailed results, and decisions used by the research
report and never runs a model or a backtest.
"""
from __future__ import annotations

import html
import json
from typing import Any

import numpy as np
import pandas as pd


VARIANT_NAMES = {
    "technical_alone": "Technical Only",
    "hybrid_random_forest": "Random Forest",
    "hybrid_xgboost": "XGBoost",
    "hybrid_gru": "GRU",
}
VARIANT_ORDER = tuple(VARIANT_NAMES)
MODEL_NAMES = {
    "random_forest": "Random Forest",
    "xgboost": "XGBoost",
    "gru": "GRU",
}
STOCK_NAMES = {
    "2303": "聯電",
    "2330": "台積電",
    "2379": "瑞昱",
    "2454": "聯發科",
    "3034": "聯詠",
}


def _pct(value: Any, *, signed: bool = False) -> str:
    number = float(value)
    return f"{number:+.2%}" if signed else f"{number:.2%}"


def _number(value: Any, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}"


def _money(value: Any) -> str:
    return f"{float(value):+,.0f}"


def _variant_rows(summary: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    variants = summary.get("variants", {})
    missing = [key for key in VARIANT_ORDER if key not in variants]
    if missing:
        raise ValueError(f"User report is missing required variants: {missing}")
    return [(key, variants[key]) for key in VARIANT_ORDER]


def _metric_cards(summary: dict[str, Any]) -> str:
    cards: list[str] = []
    for key, row in _variant_rows(summary):
        return_class = "positive" if float(row["total_return_mean"]) >= 0 else "negative"
        cards.append(f"""
        <article class="result-card {html.escape(key)}">
          <div class="card-kicker">{html.escape(VARIANT_NAMES[key])}</div>
          <div class="hero-number {return_class}">{_pct(row['total_return_mean'], signed=True)}</div>
          <div class="hero-label">平均報酬率 <span class="info" title="每個股票 × 測試期間回測結果的平均報酬，不是單一實際投資帳戶報酬。">i</span></div>
          <div class="mini-metrics">
            <div><span>勝率 <span class="info" title="完成的交易中，獲利交易所占比例。">i</span></span><b>{_pct(row['win_rate_mean'])}</b></div>
            <div><span>最大回撤 <span class="info" title="歷史測試期間，資產從高點跌到低點最嚴重的一次；數字越低通常代表歷史波動風險較小。">i</span></span><b>{_pct(row['max_drawdown_worst'])}</b></div>
            <div><span>交易次數</span><b>{int(row['trade_count_total']):,}</b></div>
          </div>
        </article>""")
    return "".join(cards)


def _plain_findings(summary: dict[str, Any], results: pd.DataFrame) -> list[str]:
    rows = dict(_variant_rows(summary))
    baseline = rows["technical_alone"]
    hybrid_keys = [key for key in VARIANT_ORDER if key != "technical_alone"]
    higher_return = [VARIANT_NAMES[key] for key in hybrid_keys if rows[key]["total_return_mean"] > baseline["total_return_mean"]]
    lower_drawdown = [VARIANT_NAMES[key] for key in hybrid_keys if rows[key]["max_drawdown_worst"] < baseline["max_drawdown_worst"]]
    fewer_trades = [VARIANT_NAMES[key] for key in hybrid_keys if rows[key]["trade_count_total"] < baseline["trade_count_total"]]
    highest_return_key = max(VARIANT_ORDER, key=lambda key: float(rows[key]["total_return_mean"]))
    highest_win_key = max(VARIANT_ORDER, key=lambda key: float(rows[key]["win_rate_mean"]))

    fold_means = (
        results.groupby(["variant", "fold_id"], as_index=False)["total_return"]
        .mean()
        .groupby("variant")["total_return"]
        .std(ddof=0)
    )
    available = [key for key in VARIANT_ORDER if key in fold_means.index]
    stable_key = min(available, key=lambda key: float(fold_means.loc[key])) if available else None

    findings: list[str] = []
    if len(higher_return) == len(hybrid_keys):
        findings.append("與單純使用技術指標相比，本次歷史測試中三個 AI Hybrid 版本的平均報酬皆較高。")
    elif higher_return:
        findings.append(f"平均報酬高於 Technical Only 的 Hybrid 版本為：{'\u3001'.join(higher_return)}。")
    else:
        findings.append("本次歷史測試中，Hybrid 版本的平均報酬沒有高於 Technical Only。")
    findings.append(
        f"本次平均報酬最高的版本是 {VARIANT_NAMES[highest_return_key]}"
        f"（{_pct(rows[highest_return_key]['total_return_mean'], signed=True)}）；"
        f"平均勝率最高的是 {VARIANT_NAMES[highest_win_key]}"
        f"（{_pct(rows[highest_win_key]['win_rate_mean'])}）。"
    )
    if len(lower_drawdown) == len(hybrid_keys):
        findings.append("三個 Hybrid 版本的最大回撤都低於 Technical Only，表示這次測試中最嚴重的資產下跌幅度有縮小。")
    elif lower_drawdown:
        findings.append(f"最大回撤低於 Technical Only 的版本為：{'\u3001'.join(lower_drawdown)}。")
    else:
        findings.append("本次測試中，Hybrid 版本沒有降低最大回撤。")
    if len(fewer_trades) == len(hybrid_keys):
        findings.append("三個 AI 模型都過濾了大部分技術指標買進機會，因此交易次數明顯減少。")
    if stable_key is not None:
        findings.append(
            f"若只比較各測試期間平均報酬的起伏，"
            f"{VARIANT_NAMES[stable_key]} 在這次結果中的期間差異相對較小。"
        )
    return findings[:5]


def _trade_filter(summary: dict[str, Any], audit: dict[str, Any]) -> str:
    rows = dict(_variant_rows(summary))
    original = int(audit.get("technical_candidate_count", rows["technical_alone"]["trade_count_total"]))
    entries = [(key, int(rows[key]["trade_count_total"])) for key in VARIANT_ORDER if key != "technical_alone"]
    maximum = max(original, 1)
    bars = [
        f'<div class="filter-row"><span>Technical 原始機會</span><div class="track"><i style="width:100%"></i></div><b>{original} 次</b></div>'
    ]
    for key, count in entries:
        bars.append(
            f'<div class="filter-row"><span>{html.escape(VARIANT_NAMES[key])}</span>'
            f'<div class="track"><i class="ai" style="width:{count / maximum * 100:.2f}%"></i></div><b>{count} 次通過</b></div>'
        )
    return "".join(bars)


def _drawdown_chart(summary: dict[str, Any]) -> str:
    rows = dict(_variant_rows(summary))
    maximum = max(float(row["max_drawdown_worst"]) for row in rows.values()) or 1.0
    bars = []
    for key in VARIANT_ORDER:
        value = float(rows[key]["max_drawdown_worst"])
        bars.append(
            f'<div class="risk-row"><span>{html.escape(VARIANT_NAMES[key])}</span>'
            f'<div class="risk-track"><i style="width:{value / maximum * 100:.2f}%"></i></div><b>{_pct(value)}</b></div>'
        )
    return "".join(bars)


def _comparison_label(value: float, baseline: float) -> str:
    if np.isclose(value, baseline, rtol=0, atol=1e-12):
        return "與 Technical 相同"
    return "相較 Technical 較高" if value > baseline else "相較 Technical 較低"


def _stock_results(results: pd.DataFrame) -> str:
    grouped = results.groupby(["stock_code", "variant"], as_index=False)["total_return"].mean()
    cards: list[str] = []
    for code in STOCK_NAMES:
        stock = grouped.loc[grouped["stock_code"].astype(str) == code].set_index("variant")["total_return"]
        if "technical_alone" not in stock:
            continue
        baseline = float(stock["technical_alone"])
        rows = [f'<div class="stock-line baseline"><span>Technical</span><b>{_pct(baseline, signed=True)}</b><small>比較基準</small></div>']
        for key in VARIANT_ORDER[1:]:
            if key not in stock:
                continue
            value = float(stock[key])
            relation = _comparison_label(value, baseline)
            css = "up" if value > baseline else "down" if value < baseline else "same"
            rows.append(
                f'<div class="stock-line"><span>{html.escape(VARIANT_NAMES[key])}</span>'
                f'<b>{_pct(value, signed=True)}</b><small class="{css}">{html.escape(relation)}</small></div>'
            )
        cards.append(
            f'<article class="stock-card"><h3>{code} {html.escape(STOCK_NAMES[code])}</h3>{"".join(rows)}</article>'
        )
    return "".join(cards)


def _fold_results(results: pd.DataFrame) -> str:
    grouped = (
        results.groupby(["fold_id", "variant"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "max"),
            trade_count=("trade_count", "sum"),
        )
    )
    fold_ids = sorted(grouped["fold_id"].astype(str).unique(), key=lambda item: int("".join(filter(str.isdigit, item)) or 0))
    sections: list[str] = []
    for index, fold_id in enumerate(fold_ids, start=1):
        rows = grouped.loc[grouped["fold_id"].astype(str) == fold_id].set_index("variant")
        lines = []
        for key in VARIANT_ORDER:
            if key not in rows.index:
                continue
            row = rows.loc[key]
            lines.append(
                f'<tr><th>{html.escape(VARIANT_NAMES[key])}</th><td>{_pct(row["total_return"], signed=True)}</td>'
                f'<td>{_pct(row["win_rate"])}</td><td>{_pct(row["max_drawdown"])}</td><td>{int(row["trade_count"])}</td></tr>'
            )
        sections.append(f"""
        <article class="fold-card">
          <h3>測試期間 {index}（{html.escape(fold_id)}）</h3>
          <div class="table-wrap"><table><thead><tr><th>版本</th><th>平均報酬</th><th>勝率</th><th>最大回撤</th><th>交易</th></tr></thead><tbody>{''.join(lines)}</tbody></table></div>
        </article>""")
    return "".join(sections)


def _decision_cases(decisions: pd.DataFrame) -> str:
    action_meta = {
        "ENTRY": ("通過範例", "執行交易", "entry"),
        "BLOCK_HOLD": ("觀望範例", "不執行交易", "hold"),
        "BLOCK_SELL": ("阻擋範例", "不執行交易", "sell"),
    }
    action_column = "hybrid_action" if "hybrid_action" in decisions.columns else "hybrid_decision"
    cards: list[str] = []
    for action, (title, outcome, css) in action_meta.items():
        candidates = decisions.loc[decisions[action_column].astype(str) == action]
        if candidates.empty:
            continue
        row = candidates.iloc[0]
        code = str(row["stock_code"])
        technical_date = str(row.get("technical_signal_date", row.get("technical_signal_time", "")))[:10]
        ml_date = str(row.get("ml_prediction_date", ""))[:10]
        predicted = str(row.get("ml_predicted_class", row.get("ml_prediction", "")))
        model = MODEL_NAMES.get(str(row.get("model", row.get("ml_model", ""))), str(row.get("model", "")))
        cards.append(f"""
        <article class="case-card {css}">
          <div class="case-title">{html.escape(title)}</div>
          <h3>{html.escape(technical_date)} · {html.escape(code)} {html.escape(STOCK_NAMES.get(code, ''))}</h3>
          <dl>
            <div><dt>技術指標</dt><dd>出現買進候選</dd></div>
            <div><dt>前一交易日 AI</dt><dd>{html.escape(model)} 判斷為 <b>{html.escape(predicted)}</b><small>{html.escape(ml_date)}</small></dd></div>
            <div><dt>系統決定</dt><dd><strong>{html.escape(outcome)}</strong></dd></div>
          </dl>
        </article>""")
    return "".join(cards)


def _advanced_metrics(summary: dict[str, Any]) -> str:
    body = []
    for key, row in _variant_rows(summary):
        body.append(
            f'<tr><th>{html.escape(VARIANT_NAMES[key])}</th><td>{_number(row["profit_factor_mean"])}</td>'
            f'<td>{_number(row["sharpe_mean"])}</td><td>{_money(row["net_pnl_total"])}</td>'
            f'<td>{_money(row["gross_pnl_total"])}</td><td>{_money(row["transaction_cost_total"])}</td></tr>'
        )
    return "".join(body)


def render_user_report(
    audit: dict[str, Any],
    summary: dict[str, Any],
    results: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    """Render the user report from the exact artifacts used by the research report."""
    if results.empty or decisions.empty:
        raise ValueError("User report requires detailed results and Hybrid decisions")
    for column in ("stock_code", "variant", "fold_id", "total_return"):
        if column not in results.columns:
            raise ValueError(f"User report results are missing column: {column}")

    findings = _plain_findings(summary, results)
    period_start = str(results["evaluate_start"].min())[:10]
    period_end = str(results["evaluate_end"].max())[:10]
    stock_codes = [code for code in STOCK_NAMES if code in set(results["stock_code"].astype(str))]
    stocks_text = "、".join(f"{code} {STOCK_NAMES[code]}" for code in stock_codes)
    models_text = "、".join(VARIANT_NAMES[key] for key in VARIANT_ORDER[1:])
    finding_html = "".join(f"<li>{html.escape(item)}</li>" for item in findings)
    audit_json = html.escape(json.dumps(audit, ensure_ascii=False, indent=2))
    alignment = audit.get("alignment", {})
    oof = audit.get("oof_validation", {})

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI 股票策略回測結果</title>
  <style>
    :root{{--bg:#06131f;--panel:#0d2232;--panel2:#102b3e;--line:#294a60;--text:#f1f7fb;--muted:#a9c3d4;--blue:#48b7ff;--blue2:#167ff2;--green:#72dfa0;--red:#ff7c82;--amber:#ffc36d}}
    *{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 80% 0,#0d3249 0,transparent 28rem),var(--bg);color:var(--text);font-family:Inter,"Microsoft JhengHei",Arial,sans-serif;line-height:1.65;font-size:17px}}
    main{{width:min(1180px,calc(100% - 32px));margin:auto;padding:34px 0 56px}}.hero{{padding:38px;border:1px solid #1f5878;background:linear-gradient(135deg,#0d3d59,#0b2638 65%);border-radius:24px;box-shadow:0 22px 60px #0005}}.eyebrow{{color:#84d3ff;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}h1{{font-size:clamp(2rem,5vw,4rem);line-height:1.08;margin:.25rem 0}}h1 span{{display:block;color:#9edbff;font-size:.42em;margin-top:13px}}h2{{font-size:clamp(1.45rem,3vw,2.2rem);margin:0 0 8px}}h3{{margin:.25rem 0 1rem}}p{{margin:.45rem 0}}.lead{{font-size:1.2rem;max-width:760px}}.notice{{margin-top:22px;padding:13px 16px;background:#40281a;border:1px solid #865d35;border-radius:12px;color:#ffdfad;font-weight:700}}
    .facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:24px}}.fact{{padding:14px;background:#071c2a99;border:1px solid #2e607b;border-radius:13px}}.fact span{{display:block;color:var(--muted);font-size:.82rem}}.fact b{{font-size:1rem}}section{{margin-top:24px;padding:27px;background:linear-gradient(160deg,var(--panel),#091c2a);border:1px solid var(--line);border-radius:20px;overflow:hidden}}.section-intro{{color:var(--muted);max-width:840px}}
    .result-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:18px}}.result-card{{padding:20px;background:#0a1d2a;border:1px solid #2d536a;border-radius:18px;position:relative;overflow:hidden}}.result-card:before{{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:#587386}}.result-card:not(.technical_alone):before{{background:var(--blue)}}.card-kicker{{font-weight:800;color:#cae8f9}}.hero-number{{font-size:clamp(2rem,4vw,3.15rem);font-weight:900;line-height:1.15;margin:12px 0 0}}.positive{{color:var(--green)}}.negative{{color:var(--red)}}.hero-label{{color:var(--muted);font-size:.86rem}}.mini-metrics{{margin-top:20px;border-top:1px solid #294657}}.mini-metrics div{{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #1f394a}}.info{{display:inline-grid;place-items:center;width:18px;height:18px;border:1px solid #56839e;border-radius:50%;font-size:.72rem;cursor:help;color:#bfe6ff}}
    .reading{{display:grid;grid-template-columns:1fr .85fr;gap:18px;align-items:start}}.finding-list{{padding-left:1.25rem;margin:14px 0 0}}.finding-list li{{margin:10px 0;padding-left:6px}}.simple-note{{background:#0a3147;border:1px solid #276684;border-radius:16px;padding:18px}}.simple-note b{{color:#8ed7ff}}
    .filter-row,.risk-row{{display:grid;grid-template-columns:minmax(130px,190px) 1fr minmax(90px,130px);gap:12px;align-items:center;margin:14px 0}}.track,.risk-track{{height:18px;background:#1b3546;border-radius:999px;overflow:hidden}}.track i,.risk-track i{{height:100%;display:block;background:#698093;border-radius:999px}}.track i.ai{{background:linear-gradient(90deg,#1489ef,#61c4ff)}}.risk-track i{{background:linear-gradient(90deg,#ffbe6b,#ff747c)}}
    .risk-layout{{display:grid;grid-template-columns:1.2fr .8fr;gap:22px;align-items:center}}.risk-explain{{padding:20px;background:#261f24;border:1px solid #67444a;border-radius:16px}}
    .stock-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:18px}}.stock-card,.fold-card,.case-card{{background:#091d2b;border:1px solid #294c62;border-radius:16px;padding:18px}}.stock-line{{display:grid;grid-template-columns:1fr auto minmax(150px,auto);gap:12px;padding:8px 0;border-top:1px solid #1f3c4e;align-items:center}}.stock-line small{{text-align:right}}.stock-line .up{{color:var(--green)}}.stock-line .down{{color:var(--red)}}.stock-line .same{{color:var(--muted)}}
    .fold-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:18px}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}th,td{{padding:9px;border-bottom:1px solid #284759;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}
    .case-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:18px}}.case-card{{border-top:4px solid var(--green)}}.case-card.hold{{border-top-color:var(--amber)}}.case-card.sell{{border-top-color:var(--red)}}.case-title{{font-weight:800;color:#bde5fa}}dl{{margin:0}}dl div{{padding:10px 0;border-top:1px solid #244354}}dt{{color:var(--muted);font-size:.82rem}}dd{{margin:2px 0}}dd small{{display:block;color:var(--muted)}}dd strong{{color:#fff}}
    .warning{{background:#321f22;border-color:#82454c}}.warning h2{{color:#ffb2b5}}.warning ul{{margin-bottom:0}}details{{margin-top:14px;background:#071722;border:1px solid #29495c;border-radius:14px;padding:14px 17px}}summary{{cursor:pointer;font-size:1.1rem;font-weight:800;color:#9edaff}}details h3{{margin-top:20px}}pre{{max-height:330px;overflow:auto;white-space:pre-wrap;background:#04101a;border-radius:10px;padding:14px;font-size:.72rem;color:#bfd4e0}}.research-link{{display:inline-block;margin-top:16px;padding:10px 15px;border:1px solid #3991c4;border-radius:10px;color:#a9dcff;text-decoration:none;font-weight:800}}.research-link:hover{{background:#123d55}}footer{{color:var(--muted);text-align:center;padding:28px 0 0;font-size:.88rem}}
    @media(max-width:900px){{.facts,.result-grid{{grid-template-columns:repeat(2,1fr)}}.reading,.risk-layout{{grid-template-columns:1fr}}.case-grid{{grid-template-columns:1fr}}}}
    @media(max-width:620px){{body{{font-size:16px}}main{{width:min(100% - 20px,1180px);padding-top:10px}}.hero,section{{padding:20px;border-radius:16px}}.facts,.result-grid,.stock-grid{{grid-template-columns:1fr}}.filter-row,.risk-row{{grid-template-columns:110px 1fr 90px;gap:8px;font-size:.86rem}}.stock-line{{grid-template-columns:1fr auto}}.stock-line small{{grid-column:1/-1;text-align:left}}}}
  </style>
</head>
<body><main>
  <header class="hero">
    <div class="eyebrow">歷史回測·流程驗證</div>
    <h1>AI 股票策略回測結果<span>Technical Indicators × Machine Learning</span></h1>
    <p class="lead">技術指標先尋找可能的買進機會，再由 AI 模型判斷是否值得執行交易。</p>
    <div class="facts">
      <div class="fact"><span>資料期間</span><b>{html.escape(period_start)} ～ {html.escape(period_end)}</b></div>
      <div class="fact"><span>股票數量</span><b>{len(stock_codes)} 檔</b></div>
      <div class="fact"><span>測試股票</span><b>{html.escape(stocks_text)}</b></div>
      <div class="fact"><span>AI 模型</span><b>{html.escape(models_text)}</b></div>
    </div>
    <div class="notice">此結果為歷史資料測試，不代表未來投資績效。</div>
  </header>

  <section>
    <h2>最重要的結果</h2>
    <p class="section-intro">先看四個版本的平均結果。滑鼠移到 <span class="info">i</span> 可查看名詞說明。</p>
    <div class="result-grid">{_metric_cards(summary)}</div>
  </section>

  <section class="reading">
    <div><h2>結果怎麼看？</h2><ul class="finding-list">{finding_html}</ul></div>
    <aside class="simple-note"><b>閱讀方式</b><p>報酬較高、回撤較低或交易較少，都只是這段歷史資料中的現象。要經過未見過的資料驗證，才能評估是否能穩定重現。</p></aside>
  </section>

  <section>
    <h2>AI 過濾了多少交易機會？</h2>
    <p class="section-intro">AI 並不是自己尋找買點，而是協助過濾技術指標產生的交易機會。交易變少不一定代表比較差，本系統要測試的是 AI 能否過濾品質較差的訊號。</p>
    <div class="filter-chart">{_trade_filter(summary, audit)}</div>
  </section>

  <section>
    <div class="risk-layout"><div><h2>最大回撤比較</h2>{_drawdown_chart(summary)}</div><aside class="risk-explain"><b>白話說明</b><p>最大回撤可以想像成：如果剛好在資產最高點開始觀察，最嚴重時曾經跌多少。這個數字越低，代表這次歷史測試的跌幅較小。</p></aside></div>
  </section>

  <section>
    <h2>每檔股票的平均報酬</h2>
    <p class="section-intro">「較高／較低」只是同一檔股票與 Technical 版本的比較，不是股票評分。</p>
    <div class="stock-grid">{_stock_results(results)}</div>
  </section>

  <section>
    <h2>不同測試期間</h2>
    <p class="section-intro">將歷史資料分成不同時間區段測試，是為了確認策略不是只有某一段行情表現良好。</p>
    <div class="fold-grid">{_fold_results(results)}</div>
  </section>

  <section>
    <h2>真實交易決定範例</h2>
    <p class="section-intro">以下三筆都直接取自本次 Hybrid Decision 紀錄。</p>
    <div class="case-grid">{_decision_cases(decisions)}</div>
  </section>

  <section>
    <h2>這次測試我們看到了什麼？</h2>
    <ul class="finding-list">{finding_html}</ul>
  </section>

  <section class="warning">
    <h2>請注意</h2>
    <ul><li>這是 2023–2024 歷史資料回測結果，不能代表未來市場。</li><li>目前仍是 Hybrid POC 流程驗證版本。</li><li>尚未使用 Final OOS 做最終驗證。</li><li>本頁不是投資建議。</li></ul>
  </section>

  <section>
    <h2>想看更完整的資料？</h2>
    <p>一般讀者可以先看到這裡。下方進階資料保留了研究人員需要的數據與稽核記錄。</p>
    <details>
      <summary>查看進階研究數據</summary>
      <h3>進階績效指標</h3>
      <div class="table-wrap"><table><thead><tr><th>版本</th><th>Profit Factor</th><th>Sharpe Ratio</th><th>Net PnL</th><th>Gross PnL</th><th>Transaction Cost</th></tr></thead><tbody>{_advanced_metrics(summary)}</tbody></table></div>
      <h3>Alignment / OOF</h3>
      <p>Alignment：{int(alignment.get('matched', 0))} 筆成功對齊，{int(alignment.get('missing', 0))} 筆缺少可用預測，{int(alignment.get('invalid', 0))} 筆時間無效。</p>
      <p>OOF：{int(oof.get('rows', 0))} 筆，日期 {html.escape(str(oof.get('date_min', '待確認')))} ～ {html.escape(str(oof.get('date_max', '待確認')))}，稽核狀態 {html.escape(str(oof.get('status', '待確認')))}。OOF 表示每筆預測都來自當時沒有用來訓練該預測的時間區段。</p>
      <h3>Audit</h3><pre>{audit_json}</pre>
    </details>
    <p style="margin-top:20px">此頁為簡化版成果說明。</p>
    <a class="research-link" href="hybrid_poc_report.html">Technical Research Report：hybrid_poc_report.html</a>
  </section>
  <footer>SAME SOURCE DATA · PRESENTATION ONLY · NO EXTERNAL DEPENDENCIES</footer>
</main></body></html>"""

