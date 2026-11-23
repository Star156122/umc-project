"""Run read-only website dataset integration checks; never run a backtest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hybrid.core.web_dataset_preflight import preflight_dataset
from trading_system.research_guard import assert_website_period


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stock_codes", nargs="*")
    args = parser.parse_args()
    config = json.loads((ROOT / "hybrid/config/web_main_strategy_v1.json").read_text(encoding="utf8"))
    stocks = args.stock_codes or [str(code) for code in config.get("stock_universe", [])]
    reports = []
    failed = False
    for stock in stocks:
        try:
            reports.append(preflight_dataset(ROOT, config, stock, check_period=assert_website_period))
        except Exception as error:
            failed = True
            reports.append({
                "stock_code": str(stock), "status": "QA_FAILED",
                "reason": str(error), "performance_calculated": False,
                "locked_data_used": False,
            })
    print(json.dumps({"status": "QA_FAILED" if failed else "QA_PASSED", "datasets": reports},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

