"""V3 Candidate artifact persistence; it does not freeze or promote a model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from ml.models import build_gru_multiclass_model


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_candidate(directory: Path, model_name: str, model: Any, scaler_mean: np.ndarray,
                   scaler_scale: np.ndarray, manifest: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    np.savez(directory / "scaler.npz", mean=scaler_mean, scale=scaler_scale)
    if model_name == "gru":
        import torch
        torch.save(model.state_dict(), directory / "model.pt")
    else:
        joblib.dump(model, directory / "model.joblib")
    path = directory / "candidate_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_candidate(directory: Path, plan: dict[str, Any]):
    manifest = json.loads((directory / "candidate_manifest.json").read_text(encoding="utf-8"))
    scaler = np.load(directory / "scaler.npz")
    name = manifest["selected_model"]
    if name == "gru":
        import torch
        model = build_gru_multiclass_model(len(plan["features"]), plan["models"][name])
        model.load_state_dict(torch.load(directory / "model.pt", map_location="cpu", weights_only=True))
        model.eval()
    else:
        model = joblib.load(directory / "model.joblib")
    return manifest, model, scaler["mean"], scaler["scale"]


def predict_candidate(name: str, model: Any, X: np.ndarray) -> np.ndarray:
    if name == "gru":
        import torch
        model.eval()
        with torch.no_grad():
            return torch.softmax(model(torch.from_numpy(X)), dim=1).numpy()
    return model.predict_proba(X.reshape(len(X), -1))
