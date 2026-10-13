from __future__ import annotations

import pytest

from ml.ml_v1_signal import MlV1SignalError, validate_ml_v1_signal


def valid_signal() -> dict:
    return {
        "ml_version": "ml-v1",
        "industry": "technology",
        "model": "random_forest",
        "stock_code": "2303",
        "decision_date": "2024-12-31",
        "decision_time": "2024-12-31T13:30:00+08:00",
        "prediction": "BUY",
        "p_buy": 0.6,
        "p_hold": 0.3,
        "p_sell": 0.1,
        "prediction_horizon": 3,
        "label_version": "ml_v1_fixed_1pct",
        "feature_version": "feature_set_a_v3_24",
        "fold_id": "fold_1",
        "data_role": "train",
    }


def test_valid_signal_contract_passes():
    validate_ml_v1_signal(valid_signal())


def test_signal_rejects_future_evaluation_fields():
    row = valid_signal()
    row["future_net_return"] = 0.03
    with pytest.raises(MlV1SignalError, match="evaluation-only"):
        validate_ml_v1_signal(row)


def test_signal_rejects_wrong_decision_time():
    row = valid_signal()
    row["decision_time"] = "2024-12-31T13:25:00+08:00"
    with pytest.raises(MlV1SignalError, match="13:30"):
        validate_ml_v1_signal(row)


def test_signal_rejects_invalid_probabilities():
    row = valid_signal()
    row["p_buy"] = 0.7
    with pytest.raises(MlV1SignalError, match="must equal 1"):
        validate_ml_v1_signal(row)
