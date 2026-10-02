"""Atomically expose verified diagnostic RC files in the local panel.

Only local artifacts are copied. No database write, Bitrix call or ESOL import.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUN2_PROOF = ROOT / "reports" / "RC_LOCAL" / "RUN2_VERIFIED.json"
FINAL = ROOT / "reports" / "RC_LOCAL" / "FINAL_PROPOSALS"
PUBLIC = ROOT / "reports" / "RC_LOCAL" / "proposals"
FILES = ("STERBRUST_UPDATE_EXISTING_PROPOSED.xml", "STERBRUST_UPDATE_EXISTING.xlsx",
         "STERBRUST_CREATE_NEW_CANDIDATES.xml", "STERBRUST_CREATE_NEW_CANDIDATES.xlsx",
         "STERBRUST_REVIEW.xlsx", "MATCHING_REPORT.csv")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish() -> dict:
    proof = json.loads(RUN2_PROOF.read_text(encoding="utf-8"))
    if proof.get("verified") is not True:
        raise RuntimeError("complete RUN2 SQL proof is required before panel publication")
    index = json.loads((FINAL / "PROPOSAL_TABLES.json").read_text(encoding="utf-8"))
    if (index.get("diagnostic_only") is not True or index.get("esol_payload") is not False
            or index.get("manifest_sha256") != proof.get("manifest_sha256")):
        raise RuntimeError("proposal index does not match completed RUN2")
    for name in FILES:
        path = FINAL / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"diagnostic file missing: {name}")
        if name.endswith(".xml"):
            root = ET.fromstring(path.read_bytes())
            if root.tag != "UniversalSupplierProposal" or root.attrib.get("diagnosticOnly") != "true" or root.attrib.get("esolPayload") != "false":
                raise RuntimeError(f"XML is not a diagnostic proposal: {name}")
        if name.endswith(".xlsx") and not zipfile.is_zipfile(path):
            raise RuntimeError(f"invalid XLSX container: {name}")
    public_name = "files-" + sha(FINAL / "PROPOSAL_TABLES.json")[:32]
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if (PUBLIC / "PROPOSAL_TABLES.json").exists():
        raise RuntimeError("an existing panel proposal index requires an explicit versioned update")
    version = PUBLIC / public_name
    if version.exists():
        raise RuntimeError("diagnostic proposal version already published")
    pending = PUBLIC / ("." + public_name + ".pending")
    if pending.exists():
        raise RuntimeError("unfinished proposal publication exists; inspect it first")
    pending.mkdir()
    try:
        for name in FILES:
            shutil.copy2(FINAL / name, pending / name)
            if sha(FINAL / name) != sha(pending / name):
                raise RuntimeError(f"proposal copy checksum mismatch: {name}")
        os.replace(pending, version)
    finally:
        # A failure deliberately leaves the pending directory for inspection.
        pass
    index["artifact_dir"] = public_name
    temporary = PUBLIC / ".PROPOSAL_TABLES.json.pending"
    if temporary.exists():
        raise RuntimeError("unfinished proposal index publication exists")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, PUBLIC / "PROPOSAL_TABLES.json")
    return {"published_index": str(PUBLIC / "PROPOSAL_TABLES.json"),
            "artifact_dir": public_name,
            "sha256": {name: sha(version / name) for name in FILES}}


if __name__ == "__main__":
    print(json.dumps(publish(), ensure_ascii=False))
