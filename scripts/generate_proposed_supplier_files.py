"""Create reproducible diagnostic XML/CSV/JSON from pinned QA matching evidence.

No PostgreSQL write, ESOL import, canonical selection or Bitrix request.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.matching_service import load_registry_from_csv
from universal_supplier.proposal_export import build_proposal_tables, capture_review_rows
from universal_supplier.full_supplier_collection import FullManifest
from sterbrust_matching.normalization import model_tokens, normalize_model

EXPECTED_REGISTRY_SHA256 = "aac0d70fea301468972bb652af7d264c690f25e2d5d0b72862925d19ae1402d0"
EXPECTED_SECTIONS_SHA256 = "a99ad9cb0a295b6a5d087095d56d7298f6a2c3d468e7066a8c085199bffd7355"
MATCHING_COLUMNS = ("source", "external_id", "model", "execution", "model_key", "new_candidate_id", "classification",
                    "sterbrust_product_id", "proposed_sterbrust_id", "match_status", "match_method",
                    "price_state", "price", "currency", "availability", "observed_at", "source_url",
                    "source_category", "match_conflicts", "match_warnings")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_registry_model_keys(path: Path) -> set[str]:
    """Absence proof checks active *and inactive* canonical cards."""
    keys: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            model = normalize_model(row.get("model_raw") or "")
            if model:
                keys.add(model)
            if "beka" in str(row.get("brand_raw") or "").lower() or "beka" in str(row.get("name") or "").lower():
                keys.update(model_tokens(str(row.get("name") or "")))
    return keys


def _xml(kind: str, rows: list[dict], *, manifest_sha: str, registry_sha: str) -> bytes:
    root = ET.Element("UniversalSupplierProposal", {
        "kind": kind, "diagnosticOnly": "true", "esolPayload": "false",
        "manifestSha256": manifest_sha, "registrySha256": registry_sha,
    })
    for row in rows:
        product = ET.SubElement(root, "Product")
        fields = ("sterbrust_product_id", "new_candidate_id", "sterbrust_name", "model", "execution", "model_key",
                  "selected_source_proposed", "source_external_id", "price", "currency",
                  "availability", "observed_at", "source_url", "section_id", "section_path",
                  "source", "external_id", "proposed_name", "absence_evidence", "selection_status")
        for key in fields:
            value = row.get(key)
            if value not in (None, ""):
                ET.SubElement(product, key).text = str(value)
        for offer in row.get("all_source_offers", row.get("source_offers", [])):
            source = ET.SubElement(product, "SourceOffer")
            for key in ("source", "external_id", "model", "price_state", "price",
                        "currency", "availability", "observed_at", "source_url"):
                value = offer.get(key)
                if value not in (None, ""):
                    ET.SubElement(source, key).text = str(value)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def generate(*, matching_path: Path, registry_path: Path, sections_path: Path,
             run1_report_path: Path, output_dir: Path,
             manifest_path: Path | None = None,
             capture_report_paths: list[Path] | None = None) -> dict:
    if _sha(registry_path) != EXPECTED_REGISTRY_SHA256 or _sha(sections_path) != EXPECTED_SECTIONS_SHA256:
        raise RuntimeError("fresh GET-only Sterbrust registry or sections changed")
    matching = json.loads(matching_path.read_text(encoding="utf-8"))
    run1 = json.loads(run1_report_path.read_text(encoding="utf-8"))
    if (matching.get("manifest_sha256") != run1.get("manifest_sha256") or
            matching.get("registry_sha256") != EXPECTED_REGISTRY_SHA256):
        raise RuntimeError("matching, run and registry evidence do not agree")
    if not run1.get("started_at"):
        raise RuntimeError("RUN1 start time missing")
    freshness_after = datetime.fromisoformat(run1["started_at"])
    if freshness_after.tzinfo is None:
        raise RuntimeError("RUN1 start time has no timezone")
    registry = load_registry_from_csv(registry_path)
    sections = {int(item["id"]): item for item in json.loads(sections_path.read_text(encoding="utf-8"))}
    tables = build_proposal_tables(matching["rows"], registry.products,
                                   registry_model_keys=_all_registry_model_keys(registry_path),
                                   sections=sections, freshness_after=freshness_after)
    if manifest_path is not None:
        manifest = FullManifest.from_jsonable(json.loads(manifest_path.read_text(encoding="utf-8")))
        if manifest.sha256 != matching["manifest_sha256"]:
            raise RuntimeError("capture REVIEW manifest does not match matching evidence")
        capture_reports = [json.loads(path.read_text(encoding="utf-8"))
                           for path in capture_report_paths or [] if path.is_file()]
        if any(report.get("manifest_sha256") != manifest.sha256 for report in capture_reports):
            raise RuntimeError("capture REVIEW report manifest changed")
        tables["review"].extend(capture_review_rows(
            allowed_urls={(item.source, item.url) for item in manifest.candidates},
            discovery_reviews=list(manifest.review_urls), reports=capture_reports))
        tables["review"].sort(key=lambda row: (str(row.get("source")),
                                               str(row.get("external_id")), str(row.get("source_url"))))
    if any(row.get("sterbrust_product_id") for row in tables["new"]):
        raise RuntimeError("NEW proposal invented a Sterbrust product ID")
    output_dir.mkdir(parents=True, exist_ok=True)
    new_path = output_dir / "STERBRUST_CREATE_NEW_CANDIDATES.xml"
    existing_path = output_dir / "STERBRUST_UPDATE_EXISTING_PROPOSED.xml"
    csv_path = output_dir / "MATCHING_REPORT.csv"
    json_path = output_dir / "PROPOSAL_TABLES.json"
    existing_path.write_bytes(_xml("update_existing_proposed", tables["existing"],
                                   manifest_sha=matching["manifest_sha256"], registry_sha=EXPECTED_REGISTRY_SHA256))
    new_path.write_bytes(_xml("create_new_candidates", tables["new"],
                              manifest_sha=matching["manifest_sha256"], registry_sha=EXPECTED_REGISTRY_SHA256))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCHING_COLUMNS)
        writer.writeheader()
        for row in sorted(matching["rows"], key=lambda item: (item["source"], item["external_id"])):
            writer.writerow({key: (json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                                   if isinstance(row.get(key), (dict, list)) else row.get(key))
                             for key in MATCHING_COLUMNS})
    json_path.write_text(json.dumps({"diagnostic_only": True, "esol_payload": False,
                                     "manifest_sha256": matching["manifest_sha256"],
                                     "registry_sha256": EXPECTED_REGISTRY_SHA256,
                                     "sections_sha256": EXPECTED_SECTIONS_SHA256,
                                     "tables": tables}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"existing": len(tables["existing"]), "new": len(tables["new"]),
               "review": len(tables["review"]), "matching_rows": len(matching["rows"]),
               "files": {path.name: _sha(path) for path in (existing_path, new_path, csv_path, json_path)}}
    (output_dir / "PROPOSAL_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matching", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--sections", type=Path, required=True)
    parser.add_argument("--run1-report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--capture-report", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(matching_path=args.matching, registry_path=args.registry,
                              sections_path=args.sections, run1_report_path=args.run1_report,
                              output_dir=args.output_dir, manifest_path=args.manifest,
                              capture_report_paths=args.capture_report), ensure_ascii=False))


if __name__ == "__main__":
    main()
