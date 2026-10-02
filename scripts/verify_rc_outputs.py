"""Read-only cross-file verification of final local RC diagnostic outputs."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reports" / "RC_LOCAL"
FILES = ("STERBRUST_UPDATE_EXISTING_PROPOSED.xml", "STERBRUST_UPDATE_EXISTING.xlsx",
         "STERBRUST_CREATE_NEW_CANDIDATES.xml", "STERBRUST_CREATE_NEW_CANDIDATES.xlsx",
         "STERBRUST_REVIEW.xlsx", "MATCHING_REPORT.csv")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(*, base: Path = BASE) -> dict:
    proof = json.loads((base / "RUN2_VERIFIED.json").read_text(encoding="utf-8"))
    matching = json.loads((base / "MATCHING_FINAL.json").read_text(encoding="utf-8"))
    final = base / "FINAL_PROPOSALS"
    proposals = json.loads((final / "PROPOSAL_TABLES.json").read_text(encoding="utf-8"))
    diagnostic = json.loads((base / "DIAGNOSTIC_QA.summary.json").read_text(encoding="utf-8"))
    if (proof.get("verified") is not True or matching.get("manifest_sha256") != proof.get("manifest_sha256")
            or proposals.get("manifest_sha256") != proof.get("manifest_sha256")
            or proposals.get("diagnostic_only") is not True or proposals.get("esol_payload") is not False
            or diagnostic.get("diagnostic_only") is not True):
        raise RuntimeError("final files do not share the verified diagnostic RUN2 basis")
    tables = proposals["tables"]
    existing, new, review = (tables[name] for name in ("existing", "new", "review"))
    if any(not str(row.get("sterbrust_product_id") or "").isdigit() or
           row.get("currency") != "RUB" or float(row.get("price") or 0) <= 0
           for row in existing):
        raise RuntimeError("existing proposal lacks real canonical ID or comparable RUB price")
    if any(not str(row.get("new_candidate_id") or "").startswith("NEWC-") or
           row.get("sterbrust_product_id") or not row.get("section_id") or
           not row.get("section_path") or not row.get("source") or not row.get("external_id")
           for row in new):
        raise RuntimeError("new proposal lacks scoped identity or verified section")
    confirmed_keys = {(str(offer.get("source")), str(offer.get("external_id")))
                      for row in existing for offer in row.get("all_source_offers", [])}
    confirmed_keys.update((str(row.get("source")), str(row.get("external_id"))) for row in new)
    if any((str(row.get("source")), str(row.get("external_id"))) in confirmed_keys
           for row in review if row.get("external_id")):
        raise RuntimeError("REVIEW identity leaked into confirmed proposal")
    hashes = {}
    for name in FILES:
        path = final / name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"missing final diagnostic file: {name}")
        hashes[name] = _sha(path)
        if name.endswith(".xlsx") and not zipfile.is_zipfile(path):
            raise RuntimeError(f"invalid XLSX file: {name}")
    for filename, expected in (("STERBRUST_UPDATE_EXISTING_PROPOSED.xml", len(existing)),
                               ("STERBRUST_CREATE_NEW_CANDIDATES.xml", len(new))):
        root = ET.fromstring((final / filename).read_bytes())
        if (root.tag != "UniversalSupplierProposal" or root.attrib.get("diagnosticOnly") != "true"
                or root.attrib.get("esolPayload") != "false" or len(root.findall("Product")) != expected):
            raise RuntimeError(f"proposal XML identity or count mismatch: {filename}")
    with (final / "MATCHING_REPORT.csv").open(encoding="utf-8-sig", newline="") as handle:
        matching_csv_rows = list(csv.DictReader(handle))
    if len(matching_csv_rows) != len(matching["rows"]):
        raise RuntimeError("matching CSV row count differs from final SQL classification")
    if diagnostic.get("products") != sum(proof["source_counts"][code]["source_products"]
                                         for code in ("intervesp", "beka_mak", "beka_mak_tr")):
        raise RuntimeError("QA diagnostic XML count differs from final RC SQL")
    diagnostic_path = base / "DIAGNOSTIC_QA.xml"
    if _sha(diagnostic_path) != diagnostic.get("sha256"):
        raise RuntimeError("QA diagnostic XML checksum changed")
    return {"verified": True, "diagnostic_only": True, "esol_payload": False,
            "matching_rows": len(matching["rows"]), "existing": len(existing),
            "new": len(new), "review": len(review), "qa_products": diagnostic["products"],
            "files_sha256": hashes, "diagnostic_qa_xml_sha256": _sha(diagnostic_path),
            "matching_report_sha256": _sha(base / "MATCHING_FINAL.json")}


def main() -> None:
    result = verify()
    output = BASE / "FINAL_OUTPUTS_VERIFIED.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
