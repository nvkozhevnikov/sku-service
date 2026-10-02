import json
from zipfile import ZipFile

import pytest

from scripts import publish_rc_proposals as publisher


def test_publish_requires_run2_proof_and_never_overwrites_panel_index(tmp_path, monkeypatch):
    proof, final, public = tmp_path / "proof.json", tmp_path / "final", tmp_path / "public"
    final.mkdir()
    monkeypatch.setattr(publisher, "RUN2_PROOF", proof)
    monkeypatch.setattr(publisher, "FINAL", final)
    monkeypatch.setattr(publisher, "PUBLIC", public)
    manifest = "a" * 64
    proof.write_text(json.dumps({"verified": False, "manifest_sha256": manifest}), encoding="utf-8")
    (final / "PROPOSAL_TABLES.json").write_text(json.dumps({"diagnostic_only": True,
        "esol_payload": False, "manifest_sha256": manifest}), encoding="utf-8")
    for name in publisher.FILES:
        path = final / name
        if name.endswith(".xml"):
            path.write_text('<UniversalSupplierProposal diagnosticOnly="true" esolPayload="false"/>',
                            encoding="utf-8")
        elif name.endswith(".xlsx"):
            with ZipFile(path, "w") as handle:
                handle.writestr("xl/workbook.xml", "<workbook/>")
        else:
            path.write_text("source,classification\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="RUN2"):
        publisher.publish()
    assert not public.exists()
    proof.write_text(json.dumps({"verified": True, "manifest_sha256": manifest}), encoding="utf-8")
    result = publisher.publish()
    assert (public / "PROPOSAL_TABLES.json").is_file()
    assert (public / result["artifact_dir"] / "MATCHING_REPORT.csv").is_file()
    with pytest.raises(RuntimeError, match="existing panel proposal index"):
        publisher.publish()
