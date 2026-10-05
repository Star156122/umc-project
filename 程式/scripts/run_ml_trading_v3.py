"""執行固定規格的 V3 市場結構特徵實驗。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> int:
    print("舊版 V3 一次載入 Training/Validation/Development 的入口已停用。")
    print("請依序執行 run_ml_training_cv.py、run_ml_candidate_validation.py、diagnose_ml_candidate_development.py。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
