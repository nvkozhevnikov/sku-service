import json

import pytest

from scripts.reconstruct_run1_checkpoint import reconstruct
from universal_supplier.full_supplier_collection import FullCandidate, FullManifest, _candidate_sha


def _files(tmp_path, statuses):
    candidates = tuple(FullCandidate("intervesp", f"https://intervesp.ru/catalog/beka-mak-{index}/",
                                     f"MODEL-{index}") for index in range(3))
    manifest = FullManifest(candidates, {}, (), _candidate_sha(candidates))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.as_jsonable()), encoding="utf-8")
    rows = [{"source": "intervesp", "url": candidates[index].url,
             "expected_model": candidates[index].expected_model, "status": status}
            for index, status in statuses]
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"manifest_sha256": manifest.sha256,
                                       "dry_run": False, "rows": rows}), encoding="utf-8")
    return manifest_path, report_path


def test_checkpoint_reports_first_unprocessed_without_replaying_completed(tmp_path):
    manifest, report = _files(tmp_path, [(0, "PERSISTED"), (1, "DB_ERROR")])
    result = reconstruct(manifest, [report])
    source = result["sources"]["intervesp"]
    assert source["terminal_prefix"] == 1
    assert source["first_unprocessed_offset"] == 1
    assert result["evidence_level"] == "REPORT_ONLY_SQL_NOT_RECHECKED"


def test_checkpoint_rejects_completed_item_after_gap(tmp_path):
    manifest, report = _files(tmp_path, [(0, "PERSISTED"), (2, "PERSISTED")])
    with pytest.raises(ValueError, match="after a gap"):
        reconstruct(manifest, [report])


def test_checkpoint_accepts_retry_of_failed_item_but_not_completed_item(tmp_path):
    manifest, report = _files(tmp_path, [(0, "DB_ERROR")])
    later = tmp_path / "later.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["rows"][0]["status"] = "PERSISTED"
    later.write_text(json.dumps(payload), encoding="utf-8")
    assert reconstruct(manifest, [report, later])["sources"]["intervesp"]["terminal_prefix"] == 1
    with pytest.raises(ValueError, match="repeat a completed"):
        reconstruct(manifest, [later, later])
