"""ML 專用資料角色與日期防線；沒有設定檔或環境變數解鎖捷徑。"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading_system.research_guard import HoldoutLockedError

POLICY_PATH = Path(__file__).resolve().parents[1] / "configs/ml_data_policy.json"
ACTIVE_ROLE_NAMES = ("train", "validation", "development")

_EXPECTED_ACTIVE = {
    "train": (date(2023, 1, 1), date(2024, 12, 31)),
    "validation": (date(2025, 1, 1), date(2025, 6, 30)),
    "development": (date(2026, 1, 1), date(2026, 6, 30)),
}
_HOLDOUT = (date(2025, 7, 1), date(2025, 12, 31))
_FINAL_OOS_START = date(2026, 7, 1)


class MlDataLockedError(HoldoutLockedError):
    """要求存取 ML Holdout 或 Final Out-of-Sample 時拋出。"""


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_ml_data_policy() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    active = policy.get("active_roles", {})
    locked = policy.get("locked_roles", {})
    actual = {
        name: (_date(active.get(name, {}).get("start")), _date(active.get(name, {}).get("end")))
        for name in ACTIVE_ROLE_NAMES
        if active.get(name, {}).get("start") and active.get(name, {}).get("end")
    }
    if actual != _EXPECTED_ACTIVE:
        raise MlDataLockedError("ML active data roles changed unexpectedly; execution blocked.")
    if locked.get("holdout") != {
        "display_name": "Additional Holdout", "start": "2025-07-01",
        "end": "2025-12-31", "access": "forbidden",
    }:
        raise MlDataLockedError("ML Holdout policy changed unexpectedly; execution blocked.")
    final = locked.get("final_out_of_sample", {})
    if final.get("start") != "2026-07-01" or final.get("end") != "latest_available" or final.get("access") != "forbidden":
        raise MlDataLockedError("ML Final Out-of-Sample policy changed unexpectedly; execution blocked.")
    if policy.get("allowed_during_model_development") != list(ACTIVE_ROLE_NAMES) or policy.get("unlock_mechanism") is not None:
        raise MlDataLockedError("ML development access policy changed unexpectedly; execution blocked.")
    return policy


def assert_ml_read_period(start: Any, end: Any) -> str:
    """只允許完整落在 Training、Validation 或 Development 的查詢。"""
    load_ml_data_policy()
    first, last = _date(start), _date(end)
    if first > last:
        raise ValueError("開始日期不能晚於結束日期")
    if first <= _HOLDOUT[1] and last >= _HOLDOUT[0]:
        raise MlDataLockedError("2025/07/01～2025/12/31 是 ML Additional Holdout，禁止讀取。")
    if last >= _FINAL_OOS_START:
        raise MlDataLockedError("2026/07/01 起是 ML Final Out-of-Sample Test，禁止讀取。")
    for role, (role_start, role_end) in _EXPECTED_ACTIVE.items():
        if role_start <= first <= last <= role_end:
            return role
    raise MlDataLockedError("要求的日期不屬於目前允許的 ML Training、Validation 或 Development。")


def validate_active_periods(periods: dict[str, Any]) -> None:
    """現行 ML Trading 計畫必須使用固定的三個資料角色與日期。"""
    load_ml_data_policy()
    if set(periods) != set(ACTIVE_ROLE_NAMES):
        raise ValueError("ML periods 必須命名為 train、validation、development。")
    for role, expected in _EXPECTED_ACTIVE.items():
        spec = periods[role]
        actual = (_date(spec["start"]), _date(spec["end"]))
        if actual != expected:
            assert_ml_read_period(*actual)
            raise ValueError(f"ML {role} 日期必須固定為 {expected[0]}～{expected[1]}。")
        assert_ml_read_period(*actual)
