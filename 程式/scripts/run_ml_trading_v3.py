"""執行固定規格的 V3 市場結構特徵實驗。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ml_trading_v2 import execute


def main() -> int:
    return execute(
        ROOT / "configs/ml_trading_v3_20261004.json",
        ROOT / "exports/ml_trading_v3_20261004",
        ROOT / "exports/ml_trading_v3_latest.html",
        "ML Trading V3：市場結構 Features",
        "唯一核心修改：在 V2 三分類上加入固定的動能、波動、量能、K 棒實體與收盤位置 Features。Label、模型主要參數與交易規則全部維持 V2。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
