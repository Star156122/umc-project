"""將策略研究、ML 結果與資料治理狀態整理成單一後端紀錄。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from trading_system.llm_analysis import build_system_analysis_payload
from trading_system.research_guard import guarded_json_loads


def read_json(path: Path):
    return guarded_json_loads(path.read_text(encoding="utf-8"))


def main():
    ml = read_json(ROOT / "exports/ml_baseline_20260927/results.json")
    clean = read_json(ROOT / "exports/clean_validation_20260926/results.json")
    record = {
        "schema_version": "analysis-record-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "research_status": "策略線已凍結；ML baseline 已完成；APP 不在本輪範圍。",
        "strategy_research": {
            "clean_validation_experiment": clean.get("experiment_id", "clean-validation-20260926"),
            "status": "候選策略在首次 clean validation 失效，保留研究紀錄，不繼續用同批資料調參。",
            "clean_validation_summary": clean.get("summary", {}),
            "source": "exports/clean_validation_20260926/results.json",
        },
        "machine_learning": {
            "experiment_id": ml["experiment_id"], "data_role": ml["data_role"],
            "period": ml["period"], "target": ml["target"],
            "test_metrics": {name: payload["splits"]["test"] for name, payload in ml["models"].items()},
            "source": "exports/ml_baseline_20260927/results.json",
            "strategy_integration": "尚未整合；本輪只比較模型分類能力。",
        },
        "technical_indicators": {
            "feature_source": "15 個只使用當時及過去 K 棒的價格、量能、均線、RSI、MACD、波動與趨勢特徵。",
            "feature_list": json.loads((ROOT / "configs/ml_baseline_20260927.json").read_text(encoding="utf-8"))["features"],
        },
        "risk": {
            "strategy_risk": "沿用既有策略回測的成本、最大回撤與 Sharpe；本輪沒有變更策略。",
            "ml_risk": "方向分類分數不等於報酬；尚未建立交易成本後的 ML 策略績效。",
        },
        "data_governance": {
            "development": "2026-01-01～2026-06-30（已看過）",
            "holdout": {"start": "2025-07-01", "end": "2025-12-31", "access": "forbidden", "status": "unseen_and_locked"},
            "standardization": "只以 train split 估計 mean/std",
        },
        "known_limitations": [
            "本次股票與期間都已看過，只能稱 ML baseline，不能稱獨立驗證。",
            "方向分類指標不能直接等同投資報酬率。",
            "目前沒有把模型預測轉成交易訊號，避免同時改動策略研究線。",
        ],
    }
    output = ROOT / "exports/unified_analysis_latest.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (ROOT / "exports/unified_llm_payload_latest.json").write_text(json.dumps(build_system_analysis_payload(record), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(output)


if __name__ == "__main__": main()
