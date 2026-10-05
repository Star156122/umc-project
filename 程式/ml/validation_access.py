"""Append-only access log for the 2025 H1 Controlled Validation."""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

FIELDS = ["access_id", "candidate_name", "event", "accessed_at", "validation_start",
          "validation_end", "metrics_file", "metrics_summary_json", "model_modified",
          "modification_reason", "notes"]


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in FIELDS})


def start_access(path: Path, candidate_name: str, candidate_manifest: Path, notes: str = "") -> str:
    if not candidate_manifest.is_file():
        raise FileNotFoundError("Controlled Validation 前必須先完成 Candidate。")
    access_id = uuid.uuid4().hex
    _append(path, {"access_id": access_id, "candidate_name": candidate_name, "event": "STARTED",
        "accessed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validation_start": "2025-01-01", "validation_end": "2025-06-30",
        "model_modified": False, "notes": notes})
    return access_id


def finish_access(path: Path, access_id: str, candidate_name: str, event: str,
                  metrics_file: str = "", metrics: dict[str, Any] | None = None,
                  notes: str = "") -> None:
    if event not in {"COMPLETED", "FAILED"}:
        raise ValueError("event 必須是 COMPLETED 或 FAILED。")
    _append(path, {"access_id": access_id, "candidate_name": candidate_name, "event": event,
        "accessed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validation_start": "2025-01-01", "validation_end": "2025-06-30",
        "metrics_file": metrics_file, "metrics_summary_json": json.dumps(metrics or {}, ensure_ascii=False),
        "model_modified": False,
        "modification_reason": "Controlled Validation 不會自動修改模型。", "notes": notes})
