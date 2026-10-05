"""固定規格的 Random Forest、XGBoost、GRU 訓練與評估。"""
from __future__ import annotations

import copy
import random
import time
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.utils.class_weight import compute_class_weight, compute_sample_weight


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
    for split in ("validation", "development"):
        started = time.perf_counter(); prob = np.asarray(predict(data.X[split])); elapsed = time.perf_counter() - started
        probabilities[split] = prob
        results["splits"][split] = {
            **evaluate(data.y[split], prob),
            "prediction_seconds": elapsed,
            "per_stock": per_stock_metrics(data.y[split], prob, data.metadata[split]["stock_code"].to_numpy()),
        }
    return model, results, probabilities


def evaluate_multiclass(y: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    pred = probabilities.argmax(axis=1)
    labels = [0, 1, 2]
    result = {
        "samples": int(len(y)),
        "accuracy": float(accuracy_score(y, pred)),
        "macro_precision": float(precision_score(y, pred, labels=labels, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y, pred, labels=labels, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)),
        "confusion_matrix": confusion_matrix(y, pred, labels=labels).astype(int).tolist(),
        "actual_class_counts": {str(label): int((y == label).sum()) for label in labels},
        "predicted_class_counts": {str(label): int((pred == label).sum()) for label in labels},
        "actual_class_proportions": {str(label): float((y == label).mean()) for label in labels},
        "predicted_class_proportions": {str(label): float((pred == label).mean()) for label in labels},
    }
    result["macro_roc_auc_ovr"] = (
        float(roc_auc_score(y, probabilities, labels=labels, multi_class="ovr", average="macro"))
        if set(np.unique(y)) == set(labels) else None
    )
    label_names = {0: "SELL", 1: "HOLD", 2: "BUY"}
    result["individual_roc_auc_ovr"] = {
        label_names[label]: (
            float(roc_auc_score((y == label).astype(int), probabilities[:, label]))
            if len(np.unique(y == label)) == 2 else None
        ) for label in labels
    }
    return result


def _train_random_forest_multiclass(data, params: dict[str, Any]):
    model = RandomForestClassifier(**params, n_jobs=-1)
    X_train = tabular_multiclass_input(data.X["train"], _context_for(data, "train"))
    started = time.perf_counter()
    model.fit(X_train, data.y["train"])
    seconds = time.perf_counter() - started
    return model, seconds, lambda X, context=None: model.predict_proba(tabular_multiclass_input(X, context))


def _train_xgboost_multiclass(data, params: dict[str, Any]):
    from xgboost import XGBClassifier
    model = XGBClassifier(**params, objective="multi:softprob", num_class=3, eval_metric="mlogloss", n_jobs=-1)
    X_train = tabular_multiclass_input(data.X["train"], _context_for(data, "train"))
    weights = compute_sample_weight(class_weight="balanced", y=data.y["train"])
    started = time.perf_counter()
    model.fit(X_train, data.y["train"], sample_weight=weights)
    seconds = time.perf_counter() - started
    return model, seconds, lambda X, context=None: model.predict_proba(tabular_multiclass_input(X, context))


def _context_for(data, split: str):
    return None if getattr(data, "context_X", None) is None else data.context_X[split]


def tabular_multiclass_input(sequence: np.ndarray, context: np.ndarray | None = None) -> np.ndarray:
    """Flatten the sequence and append each static context vector exactly once."""
    flattened = sequence.reshape(len(sequence), -1)
    if context is None:
        return flattened
    if len(context) != len(sequence) or context.ndim != 2:
        raise ValueError("Static context 必須是 samples × features，且樣本數一致。")
    return np.concatenate([flattened, context], axis=1)


def build_gru_multiclass_model(input_size: int, params: dict[str, Any], context_size: int = 0):
    import torch
    from torch import nn

    class GRUClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.context_size = int(context_size)
            self.gru = nn.GRU(input_size=input_size, hidden_size=int(params["hidden_size"]), num_layers=int(params["num_layers"]), dropout=float(params["dropout"]), batch_first=True)
            self.head = nn.Linear(int(params["hidden_size"]) + self.context_size, 3)
        def forward(self, x, context=None):
            _, hidden = self.gru(x)
            encoded = hidden[-1]
            if self.context_size:
                if context is None or context.ndim != 2 or context.shape[1] != self.context_size:
                    raise ValueError("GRU static context shape 不正確。")
                encoded = torch.cat([encoded, context], dim=1)
            return self.head(encoded)

    return GRUClassifier()


def _train_gru_multiclass(data, params: dict[str, Any], fixed_epochs: int | None = None):
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    if fixed_epochs is None and data.audit.get("workflow_stage") != "walk_forward_fold":
        raise ValueError("GRU Early Stopping 只能使用 Training 內部的 Fold Validation。")
    seed = int(params["random_state"])
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    context_train = _context_for(data, "train")
    context_size = 0 if context_train is None else int(context_train.shape[1])
    model = build_gru_multiclass_model(data.X["train"].shape[2], params, context_size)
    classes = np.asarray([0, 1, 2])
    class_weights = compute_class_weight(class_weight="balanced", classes=classes, y=data.y["train"])
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(class_weights, dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=float(params["learning_rate"]))
    train_tensors = [torch.from_numpy(data.X["train"]), torch.from_numpy(data.y["train"].astype(np.int64))]
    if context_train is not None:
        train_tensors.append(torch.from_numpy(context_train))
    loader = DataLoader(TensorDataset(*train_tensors), batch_size=int(params["batch_size"]), shuffle=False)
    X_val = torch.from_numpy(data.X["validation"]) if fixed_epochs is None else None
    y_val = torch.from_numpy(data.y["validation"].astype(np.int64)) if fixed_epochs is None else None
    context_val = torch.from_numpy(_context_for(data, "validation")) if fixed_epochs is None and context_size else None
    best_loss, best_state, stale, epochs = float("inf"), None, 0, 0
    started = time.perf_counter()
    epoch_limit = int(fixed_epochs if fixed_epochs is not None else params["max_epochs"])
    for epoch in range(epoch_limit):
        model.train()
        for batch in loader:
            X_batch, y_batch = batch[0], batch[1]
            context_batch = batch[2] if context_size else None
            optimizer.zero_grad(); loss = loss_fn(model(X_batch, context_batch), y_batch); loss.backward(); optimizer.step()
        epochs = epoch + 1
        if fixed_epochs is None:
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(X_val, context_val), y_val))
            if val_loss < best_loss - 1e-6:
                best_loss, best_state, stale = val_loss, copy.deepcopy(model.state_dict()), 0
            else:
                stale += 1
                if stale >= int(params["patience"]): break
    if best_state is not None: model.load_state_dict(best_state)
    seconds = time.perf_counter() - started
    def predict(X, context=None):
        model.eval()
        context_tensor = None if context is None else torch.from_numpy(context)
        with torch.no_grad(): return torch.softmax(model(torch.from_numpy(X), context_tensor), dim=1).numpy()
    return model, seconds, predict, {
        "epochs_completed": epochs,
        "best_validation_loss": None if fixed_epochs is not None else best_loss,
        "early_stopping_source": None if fixed_epochs is not None else "fold_validation",
    }


def fit_multiclass_model(name: str, data, params: dict[str, Any], fixed_epochs: int | None = None):
    if name == "random_forest": model, training_seconds, predict = _train_random_forest_multiclass(data, params); extra = {}
    elif name == "xgboost": model, training_seconds, predict = _train_xgboost_multiclass(data, params); extra = {}
    elif name == "gru": model, training_seconds, predict, extra = _train_gru_multiclass(data, params, fixed_epochs)
    else: raise ValueError(name)
    return model, training_seconds, predict, extra


def run_multiclass_model(name: str, data, params: dict[str, Any], *, fixed_epochs: int | None = None, evaluation_splits: tuple[str, ...] | None = None):
    model, training_seconds, predict, extra = fit_multiclass_model(name, data, params, fixed_epochs)
    results = {"training_seconds": training_seconds, **extra, "splits": {}}
    probabilities = {}
    for split in (evaluation_splits or tuple(key for key in ("validation", "development") if key in data.X)):
        started = time.perf_counter(); prob = np.asarray(predict(data.X[split], _context_for(data, split))); elapsed = time.perf_counter() - started
        probabilities[split] = prob
        overall = evaluate_multiclass(data.y[split], prob)
        per_stock = {}
        codes = data.metadata[split]["stock_code"].to_numpy()
        for code in sorted(set(codes)):
            mask = codes == code
            per_stock[code] = evaluate_multiclass(data.y[split][mask], prob[mask])
        results["splits"][split] = {**overall, "prediction_seconds": elapsed, "per_stock": per_stock}
    return model, results, probabilities

