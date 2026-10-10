"""Technical Strategy V1 的 Training-only 前置檢查與 Walk-Forward runner。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_system.technical_v1 import TechnicalV1Error, run_training_workflow  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs/technical_v1_research.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="執行 Technical V1 的 2023–2024 Training Walk-Forward；其他資料角色一律拒絕。"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="只做資料 schema、逐日品質與 fold 完整性檢查，不執行回測。",
    )
    parser.add_argument("--role", default="training", help="本輪只接受 training")
    parser.add_argument("--market-data-path", type=Path)
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help="只對已存在但缺少資料表的 SQLite 做冪等 schema 初始化；不下載資料。",
    )
    parser.add_argument("--output-root", type=Path, help="測試或稽核用輸出根目錄。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit, run_directory = run_training_workflow(
        ROOT,
        args.config,
        role=args.role,
        market_data_path=args.market_data_path,
        preflight_only=args.preflight_only,
        initialize_schema=args.initialize_schema,
        output_root=args.output_root,
    )
    print(json.dumps({
        "status": audit["status"],
        "reason": audit.get("reason", ""),
        "run_directory": str(run_directory),
        "audit": str(run_directory / "technical_v1_audit.json"),
        "import_command": audit.get("market_data_preflight", {}).get("import_command"),
    }, ensure_ascii=False, indent=2))
    if audit["status"] in {"COMPLETED", "PREFLIGHT_READY"}:
        return 0
    return 2 if audit["status"] == "BLOCKED_MISSING_DATA" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TechnicalV1Error as exc:
        raise SystemExit(f"Technical V1 blocked: {exc}") from exc
