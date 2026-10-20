from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STOCK_NAMES = {
    "2303": "聯電",
    "2330": "台積電",
    "2379": "瑞昱",
    "2454": "聯發科",
    "3034": "聯詠",
}
MODEL_NAMES = {
    "random_forest": "Random Forest",
    "xgboost": "XGBoost",
    "gru": "GRU",
}
MODEL_VARIANTS = {
    "random_forest": ("hybrid_random_forest_common", "hybrid_random_forest_risk_v1"),
    "xgboost": ("hybrid_xgboost_common", "hybrid_xgboost_risk_v1"),
    "gru": ("hybrid_gru_common", "hybrid_gru_risk_v1"),
}


def _records(path: Path) -> list[dict]:
    frame = pd.read_csv(path, dtype={"stock_code": str})
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def build_payload(export_dir: Path) -> dict:
    required = [
        "risk_v1_summary.csv",
        "risk_v1_per_stock.csv",
        "risk_v1_per_fold.csv",
        "risk_v1_decisions.csv",
        "risk_v1_trades.csv",
        "risk_v1_audit.json",
    ]
    missing = [name for name in required if not (export_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Risk V1 export is missing required files: {', '.join(missing)}")

    audit = json.loads((export_dir / "risk_v1_audit.json").read_text(encoding="utf-8"))
    summary = _records(export_dir / "risk_v1_summary.csv")
    per_stock = _records(export_dir / "risk_v1_per_stock.csv")
    per_fold = _records(export_dir / "risk_v1_per_fold.csv")
    decisions = pd.read_csv(export_dir / "risk_v1_decisions.csv", dtype={"stock_code": str})
    trades = pd.read_csv(export_dir / "risk_v1_trades.csv", dtype={"stock_code": str})

    # Pair actual Risk V1 buy/sell ledger rows. Decisions and trades are sorted
    # within each stock/fold/model, so each selected decision maps to its actual entry.
    pairs: dict[tuple[str, str, str], list[tuple[pd.Series, pd.Series]]] = {}
    for (variant, fold_id, stock), group in trades[trades["variant"].str.endswith("_risk_v1")].groupby(
        ["variant", "fold_id", "stock_code"], sort=False
    ):
        group = group.sort_values(["datetime", "trade_id"])
        pending = None
        grouped_pairs = []
        for _, row in group.iterrows():
            if row["action"] == "Buy":
                if pending is not None:
                    raise ValueError(f"Unclosed buy in {variant}/{fold_id}/{stock}")
                pending = row
            elif row["action"] == "Sell" and pending is not None:
                grouped_pairs.append((pending, row))
                pending = None
        if pending is not None:
            raise ValueError(f"Unmatched risk trade in {variant}/{fold_id}/{stock}")
        pairs[(variant, str(fold_id), str(stock))] = grouped_pairs

    examples = []
    for model in MODEL_NAMES:
        model_decisions = decisions[decisions["model"] == model].copy()
        model_decisions = model_decisions.sort_values(["timestamp", "stock_code"])
        if model_decisions.empty:
            continue
        selected = model_decisions.iloc[0]
        key = (str(selected["variant"]), str(selected["fold_id"]), str(selected["stock_code"]))
        matched = None
        for entry, exit_ in pairs.get(key, []):
            if str(entry["datetime"]) == str(pd.Timestamp(selected["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")):
                matched = (entry, exit_)
                break
        if matched is None:
            # Timestamp-level exact match is the standard path. Fail clearly rather
            # than inventing a trade if this export changes its execution convention.
            raise ValueError(f"Could not pair decision with executed trade: {key} {selected['timestamp']}")
        entry, exit_ = matched
        examples.append(
            {
                "stock_code": str(selected["stock_code"]),
                "stock_name": STOCK_NAMES.get(str(selected["stock_code"]), str(selected["stock_code"])),
                "model": model,
                "model_name": MODEL_NAMES[model],
                "fold_id": str(selected["fold_id"]),
                "decision_date": str(selected["timestamp"])[:10],
                "entry_price": float(entry["price"]),
                "exit_date": str(exit_["datetime"])[:10],
                "exit_price": float(exit_["price"]),
                "original_quantity": int(selected["original_quantity"]),
                "final_quantity": int(entry["quantity"]),
                "position_fraction": float(selected["position_fraction"]),
                "stop_loss_pct": float(selected["stop_loss_pct"]),
                "stop_level": float(entry["price"] * (1 - selected["stop_loss_pct"])),
                "net_pnl": float(entry["net_cash_flow"] + exit_["net_cash_flow"]),
                "exit_quantity": int(exit_["quantity"]),
            }
        )

    return {
        "stocks": [{"code": code, "name": name} for code, name in STOCK_NAMES.items()],
        "models": [{"id": key, "name": name} for key, name in MODEL_NAMES.items()],
        "model_variants": MODEL_VARIANTS,
        "summary": summary,
        "per_stock": per_stock,
        "per_fold": per_fold,
        "decisions": _records(export_dir / "risk_v1_decisions.csv"),
        "audit": audit,
        "examples": examples,
        "export_name": export_dir.name,
        "period": audit.get("period", ["2023-01-01", "2024-12-31"]),
        "risk_settings": {
            "risk_per_trade": audit.get("risk_per_trade"),
            "max_position_fraction": audit.get("max_position_fraction"),
            "stop_loss_pct": audit.get("existing_stop_loss_pct"),
        },
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>股票策略回測分析</title>
<style>
:root{--bg:#07111c;--panel:#0d1c2a;--panel2:#102438;--line:#1c3449;--text:#edf4fb;--muted:#a4b6c8;--blue:#48a3ff;--green:#76d6a4;--red:#ff8585;--amber:#ffc46b;--radius:22px;--shadow:0 18px 50px #0003}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#12304a 0,transparent 32%),var(--bg);color:var(--text);font:16px/1.65 system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif}button,select{font:inherit}.wrap{width:min(1180px,calc(100% - 32px));margin:auto;padding:32px 0 60px}.hero{padding:28px 30px;background:linear-gradient(125deg,#0e3d5d,#10273a 70%);border:1px solid #245472;border-radius:28px;box-shadow:var(--shadow);display:flex;justify-content:space-between;align-items:center;gap:20px}.eyebrow{color:#9fc7e7;font-size:13px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.hero h1{font-size:clamp(30px,5vw,46px);line-height:1.15;margin:5px 0 8px}.hero p{margin:0;color:#bbd0e2;font-size:17px}.badge{border:1px solid #3b6784;border-radius:999px;padding:8px 14px;color:#b8d8ef;white-space:nowrap;font-size:13px}.panel{background:linear-gradient(180deg,#0f2030,#0b1a27);border:1px solid var(--line);border-radius:var(--radius);padding:24px;margin-top:22px;box-shadow:var(--shadow)}.panel h2{font-size:24px;margin:0 0 6px}.lead{color:var(--muted);margin:0 0 18px}.controls{display:grid;grid-template-columns:1fr 1fr;gap:16px}.field label{display:block;font-weight:750;margin:0 0 7px}.field select{width:100%;background:#081722;border:1px solid #29435a;color:var(--text);border-radius:13px;padding:13px 15px;outline-color:var(--blue)}.resultline{margin-top:18px;padding:17px 20px;border-radius:15px;background:#0b2b3a;border:1px solid #1e5368;font-size:18px;font-weight:700}.resultline .dot{color:var(--blue);margin-right:8px}.flow{display:grid;grid-template-columns:repeat(7,1fr);align-items:stretch;gap:10px;margin-top:20px}.flowstep{min-height:130px;padding:15px 13px;background:#0a1824;border:1px solid var(--line);border-radius:16px;display:flex;flex-direction:column;justify-content:center}.flowstep b{font-size:16px}.flowstep span{color:var(--muted);font-size:13px;line-height:1.45;margin-top:5px}.flowarrow{align-self:center;text-align:center;color:var(--blue);font-size:21px}.sectionhead{display:flex;justify-content:space-between;align-items:end;gap:18px;margin-bottom:17px}.sectionhead p{margin:0;color:var(--muted)}.compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}.version{border:1px solid var(--line);background:#091925;border-radius:19px;padding:20px}.version.risk{border-color:#70562b;background:linear-gradient(160deg,#292213,#101c27 62%)}.version h3{font-size:20px;margin:0}.version .sub{font-size:13px;color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:11px;margin-top:15px}.metric{background:#0e2232;border:1px solid #1c394e;border-radius:14px;padding:13px}.metric .label{font-size:13px;color:var(--muted)}.metric .value{font-size:24px;font-weight:850;letter-spacing:-.02em;margin-top:2px}.metric .hint{font-size:12px;color:#9aafc0;line-height:1.4;margin-top:3px}.positive{color:var(--green)}.negative{color:var(--red)}.neutral{color:#e7f0f8}.risknote{margin-top:15px;padding:14px 16px;border-radius:14px;background:#201d14;border:1px solid #594928;color:#ffe0a8}.risknote strong{color:#ffd080}.impactgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.impact{padding:18px;border-radius:16px;background:#0a1925;border:1px solid var(--line)}.impact .big{font-size:28px;font-weight:850}.impact p{color:var(--muted);margin:5px 0 0}.risk-explain{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}.plain-list{display:grid;gap:10px}.plain-item{padding:14px 16px;background:#0a1925;border-radius:14px;border:1px solid var(--line)}.examplebox{background:#211c12;border:1px solid #554426;border-radius:17px;padding:20px}.examplebox .big{font-size:29px;font-weight:850;color:#ffd080}.examplebox p{margin:5px 0;color:#ddcda9}.bars{display:grid;gap:13px}.stockrow{display:grid;grid-template-columns:130px 1fr 100px;gap:14px;align-items:center}.stockname{font-weight:750}.barpair{display:grid;gap:5px}.barline{height:12px;background:#152a39;border-radius:99px;overflow:hidden}.bar{height:100%;border-radius:99px;min-width:2px}.bar.base{background:#7895ad}.bar.risk{background:var(--blue)}.stocknums{font-size:12px;color:var(--muted);text-align:right}.legend{display:flex;gap:16px;color:var(--muted);font-size:13px;margin-bottom:12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}.foldgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.foldcard{padding:17px;border:1px solid var(--line);background:#0a1925;border-radius:16px}.foldcard h3{margin:0 0 10px;font-size:17px}.foldmetric{display:flex;justify-content:space-between;gap:8px;font-size:14px;padding:6px 0;border-top:1px solid #172b3b}.foldmetric span:last-child{font-weight:750}.smallmuted{font-size:13px;color:var(--muted)}.capitalgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.capital{padding:17px;background:#0a1925;border:1px solid var(--line);border-radius:15px}.capital b{display:block;font-size:25px}.capital span{color:var(--muted);font-size:13px}.trades{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.trade{padding:18px;background:#0a1925;border:1px solid var(--line);border-radius:16px}.trade h3{margin:0 0 8px}.trade .tradepnl{font-size:24px;font-weight:850}.trade dl{margin:12px 0 0;display:grid;grid-template-columns:1fr auto;gap:5px 8px;font-size:13px}.trade dt{color:var(--muted)}.trade dd{margin:0;text-align:right;font-weight:700}details{border:1px solid var(--line);border-radius:16px;background:#0a1925;padding:0 17px;margin-top:12px}summary{cursor:pointer;padding:15px 0;font-weight:800;font-size:17px}details .detailbody{padding:0 0 17px;color:#c2d0dc}details p{margin:7px 0}.glossary{display:grid;grid-template-columns:1fr 1fr;gap:10px}.term{padding:12px;background:#102233;border-radius:12px}.term b{display:block;color:#d7e9f7}.term span{font-size:14px;color:var(--muted)}.advanced{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.adv{padding:12px;background:#102233;border-radius:12px}.adv b{display:block;font-size:18px}.adv span{font-size:12px;color:var(--muted)}.warning{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.warning div{padding:14px 16px;border-radius:13px;background:#261f15;border:1px solid #5d4825;color:#ffe0a8;font-weight:750}.footer{text-align:center;color:#8da1b3;font-size:13px;margin-top:22px}.pill{display:inline-block;padding:3px 9px;border-radius:99px;background:#172b3a;color:#adc5d7;font-size:12px}.empty{padding:20px;text-align:center;color:var(--muted)}@media(max-width:850px){.flow{grid-template-columns:repeat(4,1fr)}.flowarrow{display:none}.capitalgrid{grid-template-columns:repeat(2,1fr)}.trades{grid-template-columns:1fr}.foldgrid{grid-template-columns:1fr}.risk-explain{grid-template-columns:1fr}}@media(max-width:600px){.wrap{width:min(100% - 20px,1180px);padding-top:14px}.hero{align-items:flex-start;padding:21px 18px;flex-direction:column}.hero p{font-size:15px}.badge{white-space:normal}.panel{padding:17px;margin-top:14px}.panel h2{font-size:21px}.controls,.compare,.impactgrid{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}.metric .value{font-size:21px}.flow{grid-template-columns:repeat(2,1fr)}.flowstep{min-height:110px}.stockrow{grid-template-columns:86px 1fr;gap:8px}.stocknums{grid-column:2;text-align:left}.glossary{grid-template-columns:1fr}.advanced{grid-template-columns:repeat(2,1fr)}.warning{grid-template-columns:1fr}.sectionhead{display:block}}
</style></head><body><main class="wrap"><header class="hero"><div><div class="eyebrow">歷史回測成果｜使用者版</div><h1>股票策略回測分析</h1><p>Technical Indicators × Machine Learning × Risk Management</p></div><div class="badge" id="periodBadge"></div></header>
<section class="panel"><div class="sectionhead"><div><h2>先選擇要看的結果</h2><p>切換股票或 AI 模型，頁面會直接更新已完成的回測資料。</p></div><span class="pill">離線可用</span></div><div class="controls"><div class="field"><label for="stockSelect">選擇股票</label><select id="stockSelect"></select></div><div class="field"><label for="modelSelect">選擇 AI 模型</label><select id="modelSelect"></select></div></div><div class="resultline" id="resultSentence"></div></section>
<section class="panel"><h2>這套系統怎麼做交易？</h2><p class="lead">先找出可能的機會，再由 AI 和風控分別把關。</p><div class="flow"><div class="flowstep"><b>技術指標</b><span>從股價資料找出可能的買進機會</span></div><div class="flowarrow">→</div><div class="flowstep"><b>AI 判斷方向</b><span>檢查這次訊號屬於買進、觀望或賣出</span></div><div class="flowarrow">→</div><div class="flowstep"><b>AI = BUY</b><span>通過 AI 檢查，允許進場</span></div><div class="flowarrow">→</div><div class="flowstep"><b>Risk Manager</b><span>依帳戶與停損設定決定買進數量</span></div></div></section>
<section class="panel"><div class="sectionhead"><div><h2>原始策略 vs 加入風險控制</h2><p>比較同一股票、同一模型的已完成歷史回測。</p></div></div><div class="compare" id="compareCards"></div><div class="risknote" id="riskImpact"></div></section>
<section class="panel"><h2>這次風控做了什麼？</h2><p class="lead">系統不會每次都把大量資金投入同一筆交易，而是依設定控制投入與可能損失。</p><div class="risk-explain"><div class="plain-list"><div class="plain-item">每次最多使用約 <strong id="maxExposure"></strong> 的帳戶資金。</div><div class="plain-item">如果股價跌到設定的 <strong id="stopPct"></strong> 停損位置，單筆預估損失約控制在帳戶的 <strong id="riskPct"></strong> 左右。</div><div class="plain-item">實際每筆投入會依價格、帳戶金額與股數計算，因此不一定剛好等於上限。</div></div><div class="examplebox"><div class="smallmuted">用 100,000 元帳戶舉例</div><div class="big">約 40,000 元</div><p>投入上限約為帳戶 40%。</p><div class="big">約 1,000 元</div><p>若投入金額約 40,000 元，股價下跌 2.5%，損失約 1,000 元，也就是帳戶的 1% 左右。</p></div></div></section>
<section class="panel"><div class="sectionhead"><div><h2>五檔股票比較</h2><p id="stockChartCaption">目前選取模型的各股票結果。圖表刻度以報酬絕對值自動調整。</p></div></div><div class="legend"><span><i style="background:#7895ad"></i>原始策略</span><span><i style="background:#48a3ff"></i>加入風險控制</span></div><div class="bars" id="stockBars"></div></section>
<section class="panel"><div class="sectionhead"><div><h2>不同市場期間表現</h2><p>把歷史資料拆成不同時間測試，避免只看某一段行情。</p></div></div><div class="foldgrid" id="foldCards"></div></section>
<section class="panel"><h2>系統每次用了多少資金？</h2><p class="lead">以下為所選模型在 Risk V1 回測中的實際記錄。</p><div class="capitalgrid" id="capitalCards"></div></section>
<section class="panel"><h2>三筆實際交易案例</h2><p class="lead">案例取自回測中的真實進出場紀錄，停損價是依設定估算的參考位置。</p><div class="trades" id="tradeExamples"></div></section>
<section class="panel"><h2>這份結果代表什麼？</h2><div id="interpretation" class="plain-list"></div></section>
<details><summary>看不懂？這些數字是什麼意思？</summary><div class="detailbody glossary"><div class="term"><b>平均報酬率</b><span>這次歷史測試平均賺或虧多少。</span></div><div class="term"><b>勝率</b><span>獲利交易占所有完成交易的比例；不代表未來勝率。</span></div><div class="term"><b>最大回撤（測試期間最大下跌）</b><span>資產從高點跌下來，最嚴重曾經跌多少。</span></div><div class="term"><b>交易次數</b><span>系統完成多少次買進到賣出的交易。</span></div><div class="term"><b>投入比例</b><span>一筆交易使用帳戶多少比例的資金。</span></div><div class="term"><b>每筆風險</b><span>如果碰到停損，預估整個帳戶最多承受多少損失。</span></div><div class="term"><b>停損</b><span>股價跌到設定程度後退出交易，限制這筆交易的預估損失。</span></div><div class="term"><b>總損益</b><span>回測期間所有已完成交易扣除交易成本後的合計損益。</span></div></div></details>
<details><summary>查看進階數據</summary><div class="detailbody"><div class="advanced" id="advancedData"></div></div></details>
<details><summary>這些結果怎麼測出來？（研究方法）</summary><div class="detailbody"><p>使用 <span id="methodPeriod"></span> 的歷史資料，包含 2303 聯電、2330 台積電、2379 瑞昱、2454 聯發科、3034 聯詠。</p><p>技術指標先尋找交易機會，AI 再判斷是否允許進場，最後比較原始策略與加入 Risk V1。為公平比較，兩邊使用相同股票、日期與買賣規則；主要差異是 Risk V1 會控制每次買進數量。</p><p><b>共同涵蓋期間（Common-Coverage）</b>：只計入原始策略與 AI 都有完整資料的相同交易日期。</p><p><b>測試期間（WF1～WF3）</b>：依時間順序切分的三段測試期間。</p><p>本頁只呈現已完成結果，不會重新執行回測。</p></div></details>
<section class="panel"><h2>重要提醒</h2><div class="warning"><div>歷史回測不代表未來績效。</div><div>本系統目前仍為研究階段。</div><div>此頁內容不是買賣建議。</div><div>目前尚未使用 Final OOS 做最終驗證。</div></div></section><footer class="footer">本頁為離線成果展示，數據來自已完成的 Risk V1 匯出資料。<br>研究分類：<span id="classification"></span></footer></main>
<script>
const DATA=__DATA__;
const names=Object.fromEntries(DATA.stocks.map(x=>[x.code,x.name]));
const models=Object.fromEntries(DATA.models.map(x=>[x.id,x.name]));
const $=s=>document.querySelector(s);
const pct=x=>Number.isFinite(+x)?`${(+x*100>=0?'+':'')}${(+x*100).toFixed(2)}%`:'—';
const pctAbs=x=>Number.isFinite(+x)?`${(+x*100).toFixed(2)}%`:'—';
const money=x=>Number.isFinite(+x)?`${+x<0?'−':''}${Math.abs(+x).toLocaleString('zh-TW',{maximumFractionDigits:0})} 元`:'—';
const num=x=>Number.isFinite(+x)?(+x).toLocaleString('zh-TW',{maximumFractionDigits:0}):'—';
const variant=(model,risk)=>DATA.model_variants[model][risk?1:0];
function pick(rows,fn){return rows.find(fn)||null}
function metricCard(label,value,hint,cls='neutral'){return `<div class="metric"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="hint">${hint}</div></div>`}
function rowFor(rows,model,risk,stock=null,fold=null){let v=variant(model,risk);return pick(rows,r=>r.variant===v&&(stock===null||String(r.stock_code)===String(stock))&&(fold===null||r.fold_id===fold))}
function paint(){const stock=$('#stockSelect').value, model=$('#modelSelect').value, s0=rowFor(DATA.per_stock,model,false,stock),s1=rowFor(DATA.per_stock,model,true,stock), all0=rowFor(DATA.summary,model,false),all1=rowFor(DATA.summary,model,true);const n=names[stock],mn=models[model];
  $('#resultSentence').innerHTML=`<span class="dot">●</span>${n}（${stock}）搭配 ${mn}：${s1?`加入風控後平均報酬 ${pct(s1.average_return)}，測試期間最大下跌 ${pctAbs(s1.worst_mdd)}。`: '目前沒有此組合的資料。'}`;
  const makeVersion=(title,sub,r, risk)=>{if(!r)return `<article class="version"><h3>${title}</h3><div class="empty">找不到此組合的結果</div></article>`;let ret=+r.average_return,mdd=+r.worst_mdd,win=+r.win_rate;return `<article class="version ${risk?'risk':''}"><h3>${title}</h3><div class="sub">${sub}</div><div class="cards">${metricCard('平均報酬率',pct(ret),`此次歷史測試平均${ret>=0?'賺':'虧'}約 ${pctAbs(ret)}`,ret>0?'positive':ret<0?'negative':'neutral')}${metricCard('測試期間最大下跌',pctAbs(mdd),`資產從高點最多下跌約 ${pctAbs(mdd)}`,'neutral')}${metricCard('勝率',pct(win),`每 100 筆約 ${Math.round(win*100)} 筆獲利`,'neutral')}${metricCard('交易次數',`${num(r.trade_count)} 次`,'已完成買進到賣出的交易','neutral')}${metricCard('回測結束後資產',money(r.final_equity_mean),'每組測試初始資產為 100,000 元','neutral')}${metricCard('總共賺／虧多少',money(r.net_pnl),'已扣除回測交易成本',+r.net_pnl>=0?'positive':'negative')}</div></article>`};
  $('#compareCards').innerHTML=makeVersion('原始策略','Hybrid｜未加入 Risk V1',s0,false)+makeVersion('加入風險控制','Hybrid + Risk V1',s1,true);
  if(s0&&s1){let pp=(+s1.average_return-+s0.average_return)*100, reduction=+s0.worst_mdd?((+s0.worst_mdd-+s1.worst_mdd)/+s0.worst_mdd*100):null;$('#riskImpact').innerHTML=`<strong>風控比較：</strong>平均報酬${pp<0?'減少':'增加'} <strong>${Math.abs(pp).toFixed(2)} 個百分點</strong>${reduction!==null?`；最大下跌${reduction>=0?'減少':'增加'}約 <strong>${Math.abs(reduction).toFixed(1)}%</strong>`:''}。這是本次歷史資料的觀察，不代表未來結果。`}
  const f0=DATA.per_fold.filter(r=>r.variant===variant(model,false)).sort((a,b)=>String(a.fold_id).localeCompare(String(b.fold_id))),f1=DATA.per_fold.filter(r=>r.variant===variant(model,true)).sort((a,b)=>String(a.fold_id).localeCompare(String(b.fold_id)));
  $('#foldCards').innerHTML=f0.map((a,i)=>{let b=f1.find(x=>x.fold_id===a.fold_id);return `<article class="foldcard"><h3>測試期間 ${i+1} <span class="smallmuted">（${a.fold_id}）</span></h3><div class="foldmetric"><span>原始策略報酬</span><span class="${+a.average_return>=0?'positive':'negative'}">${pct(a.average_return)}</span></div><div class="foldmetric"><span>風控版本報酬</span><span class="${b&&+b.average_return>=0?'positive':'negative'}">${b?pct(b.average_return):'—'}</span></div><div class="foldmetric"><span>原始最大下跌</span><span>${pctAbs(a.worst_mdd)}</span></div><div class="foldmetric"><span>風控最大下跌</span><span>${b?pctAbs(b.worst_mdd):'—'}</span></div></article>`}).join('');
  const stocks=DATA.stocks.map(x=>{let a=rowFor(DATA.per_stock,model,false,x.code),b=rowFor(DATA.per_stock,model,true,x.code);return {...x,a,b}});let max=Math.max(0.01,...stocks.flatMap(x=>[Math.abs(+(x.a?.average_return||0)),Math.abs(+(x.b?.average_return||0))]));$('#stockBars').innerHTML=stocks.map(x=>`<div class="stockrow"><div class="stockname">${x.code} ${x.name}</div><div class="barpair"><div class="barline"><div class="bar base" style="width:${Math.min(100,Math.abs(+(x.a?.average_return||0))/max*100)}%"></div></div><div class="barline"><div class="bar risk" style="width:${Math.min(100,Math.abs(+(x.b?.average_return||0))/max*100)}%"></div></div></div><div class="stocknums">${x.a?pct(x.a.average_return):'—'}<br>${x.b?pct(x.b.average_return):'—'}<br><span>下跌 ${x.a?pctAbs(x.a.worst_mdd):'—'} / ${x.b?pctAbs(x.b.worst_mdd):'—'}</span></div></div>`).join('');
  const dec=DATA.decisions.filter(d=>d.model===model);const avg=dec.length?dec.reduce((a,x)=>a+(+x.position_fraction),0)/dec.length:NaN,maxp=dec.length?Math.max(...dec.map(x=>+x.position_fraction)):NaN, avgr=dec.length?dec.reduce((a,x)=>a+(+x.estimated_risk_fraction),0)/dec.length:NaN,maxr=dec.length?Math.max(...dec.map(x=>+x.estimated_risk_fraction)):NaN;
  $('#capitalCards').innerHTML=[[pctAbs(avg),'平均投入比例','平均每次使用帳戶資金'],[pctAbs(maxp),'最大投入比例','單筆投入最高占比'],[pctAbs(avgr),'平均預估單筆風險','碰到停損時占帳戶比例'],[pctAbs(maxr),'最大預估單筆風險','單筆最高預估風險']].map(x=>`<div class="capital"><b>${x[0]}</b><span><strong>${x[1]}</strong><br>${x[2]}</span></div>`).join('');
  $('#tradeExamples').innerHTML=DATA.examples.map(e=>`<article class="trade"><h3>${e.stock_code} ${e.stock_name} <span class="pill">${e.model_name}</span></h3><div class="smallmuted">${e.decision_date} 進場 · ${e.exit_date} 出場</div><div class="tradepnl ${e.net_pnl>=0?'positive':'negative'}">${money(e.net_pnl)}</div><dl><dt>AI 判斷</dt><dd>BUY（允許進場）</dd><dt>進場價格</dt><dd>${Number(e.entry_price).toFixed(2)} 元</dd><dt>原本可買股數</dt><dd>${num(e.original_quantity)} 股</dd><dt>風控後股數</dt><dd>${num(e.final_quantity)} 股</dd><dt>投入比例</dt><dd>${pctAbs(e.position_fraction)}</dd><dt>估算停損位置</dt><dd>${Number(e.stop_level).toFixed(2)} 元（${pctAbs(e.stop_loss_pct)}）</dd><dt>Fold</dt><dd>${e.fold_id}</dd></dl></article>`).join('');
  const insights=[];if(s0&&s1){if(+s1.worst_mdd<+s0.worst_mdd)insights.push('加入風控後，此次歷史測試的最大下跌幅度降低。');else if(+s1.worst_mdd>+s0.worst_mdd)insights.push('加入風控後，此次歷史測試的最大下跌幅度增加。');else insights.push('加入風控前後，此次歷史測試的最大下跌幅度相同。');if(+s1.average_return<+s0.average_return)insights.push('代價是平均報酬也有所下降。');else if(+s1.average_return>+s0.average_return)insights.push('同時，平均報酬在這組歷史測試中有所增加。');else insights.push('平均報酬在這組歷史測試中沒有變化。');insights.push(`交易次數由 ${num(s0.trade_count)} 次變為 ${num(s1.trade_count)} 次；回測損益已包含交易成本。`)}$('#interpretation').innerHTML=insights.map(x=>`<div class="plain-item">${x}</div>`).join('');
  const advanced=[[all1?.profit_factor,'Profit Factor','獲利總額相對虧損總額的比值'],[all1?.sharpe,'Sharpe Ratio','風險調整後報酬參考指標'],[all1?.gross_pnl,'Gross PnL','扣成本前損益'],[all1?.transaction_cost,'Transaction Cost','手續費與交易稅等成本'],[all1?.average_position_fraction,'Average Position Fraction','平均投入比例'],[all1?.average_estimated_risk_per_trade,'Estimated Risk Fraction','平均預估單筆帳戶風險'],[null,'Fold ID',f0.map(x=>x.fold_id).join('、')],[null,'Model',mn]];$('#advancedData').innerHTML=advanced.map((x,i)=>`<div class="adv"><b>${i===2||i===3?money(x[0]):i===4||i===5?pctAbs(x[0]):x[0]==null?x[2]:Number(x[0]).toFixed(3)}</b><span>${x[1]} · ${i===0?'獲利相對虧損':i===1?'風險調整指標':i===2?'扣成本前損益':i===3?'交易成本':i===4?'平均投入占比':i===5?'平均預估風險':i===6?'時間測試區段':'目前選取模型'}</span></div>`).join('');
}
const stockSelect=$('#stockSelect'),modelSelect=$('#modelSelect');DATA.stocks.forEach(x=>stockSelect.add(new Option(`${x.code} ${x.name}`,x.code)));DATA.models.forEach(x=>modelSelect.add(new Option(x.name,x.id)));stockSelect.addEventListener('change',paint);modelSelect.addEventListener('change',paint);
$('#periodBadge').textContent=`歷史資料 ${DATA.period[0]} 至 ${DATA.period[1]}`;$('#methodPeriod').textContent=`${DATA.period[0]}～${DATA.period[1]}`;$('#maxExposure').textContent=pctAbs(DATA.risk_settings.max_position_fraction);$('#stopPct').textContent=pctAbs(DATA.risk_settings.stop_loss_pct);$('#riskPct').textContent=pctAbs(DATA.risk_settings.risk_per_trade);$('#classification').textContent=DATA.audit.result_classification||'—';paint();
</script></body></html>'''


def render(export_dir: Path) -> Path:
    payload = build_payload(export_dir)
    embedded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    embedded = embedded.replace("</", "<\\/")
    document = HTML_TEMPLATE.replace("__DATA__", embedded)
    output = export_dir / "hybrid_user_backtest_report.html"
    output.write_text(document, encoding="utf-8")
    return output


def latest_export(exports_dir: Path) -> Path:
    candidates = [p for p in exports_dir.glob("hybrid_risk_v1_*") if p.is_dir() and (p / "risk_v1_audit.json").is_file()]
    if not candidates:
        raise FileNotFoundError(f"No Risk V1 export found under {exports_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline, user-facing Hybrid Risk V1 report.")
    parser.add_argument("--export-dir", type=Path, help="Risk V1 export folder; defaults to the newest completed export.")
    args = parser.parse_args()
    export_dir = (args.export_dir or latest_export(ROOT / "exports")).resolve()
    output = render(export_dir)
    print(f"USER_REPORT_CREATED = TRUE")
    print(f"User Report Path: {output}")
    print(f"Research Report Path: {export_dir / 'risk_v1_report.html'}")


if __name__ == "__main__":
    main()
