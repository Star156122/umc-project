import csv
from pathlib import Path

import pytest

from ml.validation_access import finish_access, start_access


def test_controlled_validation_requires_candidate_and_logs_start_finish(tmp_path: Path):
    log = tmp_path / "access.csv"
    manifest = tmp_path / "candidate_manifest.json"
    with pytest.raises(FileNotFoundError):
        start_access(log, "V3 Candidate", manifest)
    manifest.write_text("{}", encoding="utf-8")
    access_id = start_access(log, "V3 Candidate", manifest)
    finish_access(log, access_id, "V3 Candidate", "COMPLETED", "metrics.json", {"macro_f1": .5})
    with log.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["event"] for row in rows] == ["STARTED", "COMPLETED"]
    assert rows[1]["model_modified"] == "False"
    assert "macro_f1" in rows[1]["metrics_summary_json"]
