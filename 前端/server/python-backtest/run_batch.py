"""Run multiple backtest profiles from backtest_config.json."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "backtest_config.json"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"找不到設定檔：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} JSON 格式錯誤：第 {exc.lineno} 行第 {exc.colno} 欄") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path.name} 最外層必須是 JSON 物件")
    return data


def resolve_profiles(config: dict[str, Any], selected: list[str]) -> list[str]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SystemExit("backtest_config.json 的 profiles 必須是 JSON 物件")

    names = selected or config.get("batch_profiles") or [config.get("active_profile")]
    names = [str(name).strip() for name in names if str(name).strip()]
    if not names:
        raise SystemExit("沒有可執行的 profile，請設定 batch_profiles 或 active_profile")

    missing = [name for name in names if name not in profiles]
    if missing:
        raise SystemExit("找不到 profile：" + ", ".join(missing))
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="依照 backtest_config.json 批次執行多個回測 profile")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--profiles", nargs="*", default=[])
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    config = load_config(config_path)
    profiles = resolve_profiles(config, args.profiles)

    print("========== Batch Backtest ==========")
    print(f"Config: {config_path}")
    print("Profiles: " + ", ".join(profiles))
    print("====================================")

    for index, profile in enumerate(profiles, start=1):
        print(f"\n[{index}/{len(profiles)}] Running profile: {profile}")
        command = [
            sys.executable,
            str(ROOT / "main02.py"),
            "--config",
            str(config_path),
            "--profile",
            profile,
        ]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            raise SystemExit(result.returncode)

    print("\nBatch backtest finished.")


if __name__ == "__main__":
    main()
