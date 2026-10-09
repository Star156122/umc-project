"""執行技術指標跨科技股泛化稽核；只讀本地行情，不自動下載。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_system.technical_generalization import run_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="技術指標跨科技股泛化稽核")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/technical_generalization_20261006.json",
    )
    parser.add_argument(
        "--market-data-path",
        type=Path,
        help="明確指定本地 market_data*.sqlite3；不指定時只接受唯一候選檔",
    )
    args = parser.parse_args()
    config_path = args.config
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    audit, paths = run_workflow(ROOT, config_path, args.market_data_path)
    print(f"Status: {audit['status']}")
    if audit.get("reason"):
        print(f"Reason: {audit['reason']}")
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
