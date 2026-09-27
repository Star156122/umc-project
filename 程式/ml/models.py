"""固定規格的 Random Forest、XGBoost、GRU 訓練與評估。"""
from __future__ import annotations

import copy
import random
import time
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score


def evaluate(y: np.ndarray, prob: np.ndarray) -> dict[str, Any]:
    pred = (prob >= 0.5).astype(int)
    result = {
        "samples": int(len(y)),
        "positive_rate": float(y.mean()) if len(y) else None,
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "confusion_matrix": confusion_matrix(y, pred, labels=[0, 1]).astype(int).tolist(),
    }
    result["roc_auc"] = float(roc_auc_score(y, prob)) if len(np.unique(y)) == 2 else None
    return result


def per_stock_metrics(y: np.ndarray, prob: np.ndarray, stock_codes: np.ndarray) -> dict[str, Any]:
    return {code: evaluate(y[stock_codes == code], prob[stock_codes == code]) for code in sorted(set(stock_codes))}


def train_random_forest(data, params: dict[str, Any]):
    model = RandomForestClassifier(**params, n_jobs=-1)
    X_train = data.X["train"].reshape(len(data.X["train"]), -1)
    started = time.perf_counter()
    model.fit(X_train, data.y["train"])
    training_seconds = time.perf_counter() - started
    return model, training_seconds, lambda X: model.predict_proba(X.reshape(len(X), -1))[:, 1]


def train_xgboost(data, params: dict[str, Any]):
    from xgboost import XGBClassifier
    model = XGBClassifier(**params, objective="binary:logistic", eval_metric="logloss", n_jobs=-1)
    X_train = data.X["train"].reshape(len(data.X["train"]), -1)
    started = time.perf_counter()
    model.fit(X_train, data.y["train"])
    training_seconds = time.perf_counter() - started
    return model, training_seconds, lambda X: model.predict_proba(X.reshape(len(X), -1))[:, 1]


def train_gru(data, params: dict[str, Any]):
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    seed = int(params["random_state"])
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)

    class GRUClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(input_size=data.X["train"].shape[2], hidden_size=int(params["hidden_size"]), num_layers=int(params["num_layers"]), dropout=float(params["dropout"]), batch_first=True)
            self.head = nn.Linear(int(params["hidden_size"]), 1)
        def forward(self, x):
            _, hidden = self.gru(x)
            return self.head(hidden[-1]).squeeze(1)

    model = GRUClassifier()
    positives = max(int(data.y["train"].sum()), 1)
    negatives = max(len(data.y["train"]) - positives, 1)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(negatives / positives, dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=float(params["learning_rate"]))
    loader = DataLoader(TensorDataset(torch.from_numpy(data.X["train"]), torch.from_numpy(data.y["train"].astype(np.float32))), batch_size=int(params["batch_size"]), shuffle=False)
    X_val = torch.from_numpy(data.X["validation"])
    y_val = torch.from_numpy(data.y["validation"].astype(np.float32))
    best_loss, best_state, stale, epochs = float("inf"), None, 0, 0
    started = time.perf_counter()
    for epoch in range(int(params["max_epochs"])):
        model.train()
        for X_batch, y_batch in loader:
            optimizer.zero_grad(); loss = loss_fn(model(X_batch), y_batch); loss.backward(); optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(X_val), y_val))
        epochs = epoch + 1
        if val_loss < best_loss - 1e-6:
            best_loss, best_state, stale = val_loss, copy.deepcopy(model.state_dict()), 0
        else:
            stale += 1
            if stale >= int(params["patience"]): break
    if best_state is not None: model.load_state_dict(best_state)
    training_seconds = time.perf_counter() - started
    def predict(X):
        model.eval()
        with torch.no_grad(): return torch.sigmoid(model(torch.from_numpy(X))).numpy()
    return model, training_seconds, predict, {"epochs_completed": epochs, "best_validation_loss": best_loss}


def run_model(name: str, data, params: dict[str, Any]):
    if name == "random_forest": model, training_seconds, predict = train_random_forest(data, params); extra = {}
    elif name == "xgboost": model, training_seconds, predict = train_xgboost(data, params); extra = {}
    elif name == "gru": model, training_seconds, predict, extra = train_gru(data, params)
    else: raise ValueError(name)
    results = {"training_seconds": training_seconds, **extra, "splits": {}}
    probabilities = {}
    for split in ("validation", "test"):
        started = time.perf_counter(); prob = np.asarray(predict(data.X[split])); elapsed = time.perf_counter() - started
        probabilities[split] = prob
        results["splits"][split] = {
            **evaluate(data.y[split], prob),
            "prediction_seconds": elapsed,
            "per_stock": per_stock_metrics(data.y[split], prob, data.metadata[split]["stock_code"].to_numpy()),
        }
    return model, results, probabilities

