import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_roles import (
    MlDataLockedError,
    POLICY_PATH,
    assert_ml_read_period,
    load_ml_data_policy,
    validate_active_periods,
)


class MlDataRoleTests(unittest.TestCase):
    def test_policy_has_three_active_and_two_locked_roles(self):
        policy = load_ml_data_policy()
        self.assertEqual(list(policy["active_roles"]), ["train", "validation", "development"])
        self.assertEqual(set(policy["locked_roles"]), {"holdout", "final_out_of_sample"})

    def test_allowed_periods_have_canonical_role_names(self):
        self.assertEqual(assert_ml_read_period("2023-01-01", "2024-12-31"), "train")
        self.assertEqual(assert_ml_read_period("2025-01-01", "2025-06-30"), "validation")
        self.assertEqual(assert_ml_read_period("2026-01-01", "2026-06-30"), "development")

    def test_both_reserved_periods_are_blocked(self):
        for start, end in (
            ("2025-07-01", "2025-12-31"),
            ("2026-07-01", "2026-10-05"),
        ):
            with self.subTest(start=start), self.assertRaises(MlDataLockedError):
                assert_ml_read_period(start, end)

    def test_old_test_name_is_rejected(self):
        periods = {
            "train": {"start": "2023-01-01", "end": "2024-12-31"},
            "validation": {"start": "2025-01-01", "end": "2025-06-30"},
            "test": {"start": "2026-01-01", "end": "2026-06-30"},
        }
        with self.assertRaises(ValueError):
            validate_active_periods(periods)

    def test_policy_cannot_be_changed_to_unlock_final_oos(self):
        changed = json.loads((ROOT / "configs/ml_data_policy.json").read_text(encoding="utf-8"))
        changed["locked_roles"]["final_out_of_sample"]["access"] = "allowed"
        with patch.object(POLICY_PATH.__class__, "read_text", return_value=json.dumps(changed)):
            with self.assertRaises(MlDataLockedError):
                assert_ml_read_period("2026-01-01", "2026-06-30")


if __name__ == "__main__":
    unittest.main()
