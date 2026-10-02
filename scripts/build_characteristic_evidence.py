"""Build a new offline evidence bundle; no SQL, HTTP or matcher execution."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup
from sterbrust_matching.normalization import extract_model, model_tokens, normalize_model
from universal_supplier.characteristic_evidence import build_evidence, equipment_scope, unit_from_label


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_properties(html: str, source: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    values = []
    if source == "intervesp":
        for name in soup.select(".elTabPropGroup .elTabPropName"):
            value = name.find_next_sibling()
            if value is not None and "elTabPropNum" in value.get("class", []):
                values.append((name.get_text(" ", strip=True), value.get_text(" ", strip=True)))
    elif source == "beka_mak":
        for row in soup.select("table.props_list [itemprop=additionalProperty]"):
            name, value = row.select_one("[itemprop=name]"), row.select_one("[itemprop=value]")
            if name is not None and value is not None:
                values.append((name.get_text(" ", strip=True), value.get_text(" ", strip=True)))
    else:
        raise ValueError("no confirmed offline extraction contract for this source")
    return [{"name": name, "value": value, "unit": unit_from_label(name)}
            for name, value in values if name and value]


def section_evidence(ids: list, sections: dict[str, dict]) -> list[dict]:
    result = []
    for sid in sorted(set(map(str, ids))):
        seen, names, active, current = set(), [], True, sid
        while current:
            if current in seen or current not in sections:
                active = False
                break
            seen.add(current)
            node = sections[current]
            names.append(str(node.get("name") or ""))
            active &= node.get("active") == "Y" and bool(names[-1])
            current = str(node.get("iblockSectionId") or "")
        result.append({"id": int(sid), "active": "Y" if active else "N", "path": " / ".join(reversed(names))})
    return result


def build_bundle(output: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise ValueError("output directory already exists; preserve the previous evidence bundle")
    matching_path = ROOT / "reports/RC_LOCAL/MATCHING_FINAL.json"
    registry_csv = ROOT / "reports/STERBRUST_REGISTRY.csv"
    registry_jsonl = ROOT / "reports/STERBRUST_REGISTRY.jsonl"
    capture_path = ROOT / "reports/RC_LOCAL/RUN2_ALL/report.json"
    sections_path = ROOT / "reports/rest/STERBRUST_SECTIONS_RAW.json"
    metadata_path = ROOT / "reports/sterbrust_snapshot_metadata.json"
    pinned_inputs = {p: sha256(p) for p in (matching_path, registry_csv, capture_path, sections_path, metadata_path)}
    matching = json.loads(matching_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    registry_sha = sha256(registry_csv)
    if matching.get("registry_sha256") != registry_sha:
        raise ValueError("pinned registry checksum mismatch")
    confirmed = [row for row in matching["rows"] if row["classification"] == "EXISTING_CONFIRMED"
                 and row.get("full_model_confirmed")]
    targets = {str(row["sterbrust_product_id"]) for row in confirmed}
    captures = json.loads(capture_path.read_text(encoding="utf-8"))
    if captures["manifest_sha256"] != matching["manifest_sha256"]:
        raise ValueError("capture/matching manifest mismatch")
    by_source = {(r["source"], str(r.get("external_id"))): r for r in captures["rows"] if r["status"] == "PERSISTED"}
    sections = {str(row["id"]): row for row in json.loads(sections_path.read_text(encoding="utf-8"))}
    found, digest, seen_ids = {}, hashlib.sha256(), set()
    print(json.dumps({"phase": "reading_saved_registry", "confirmed_source_records": len(confirmed),
                      "unique_confirmed_canonical_ids": len(targets)}), flush=True)
    with registry_jsonl.open("rb") as handle:
        for line in handle:
            digest.update(line)
            row = json.loads(line)
            sid = str(row["sterbrust_product_id"])
            if sid in seen_ids:
                raise ValueError("duplicate canonical snapshot ID")
            seen_ids.add(sid)
            if sid in targets:
                found[sid] = row
    if targets != found.keys() or len(seen_ids) != metadata["products_total"] or metadata.get("snapshot_qa") != "PASS":
        raise ValueError("saved registry coverage is incomplete")
    pairs, excluded = [], []
    for row in confirmed:
        cap = by_source.get((row["source"], str(row["external_id"])))
        if not cap or cap["url"] != row["source_url"]:
            excluded.append({"source": row["source"], "external_id": row["external_id"], "reason": "saved_capture_missing"})
            continue
        ref = str(cap["evidence_ref"])
        relative = ref.removeprefix("capture://")
        evidence_root = ROOT / "reports/RC_LOCAL/RUN2_ALL/evidence" / row["source"]
        html_path = (evidence_root / relative).resolve()
        if not html_path.is_relative_to(evidence_root.resolve()) or not re.fullmatch(r"[a-f0-9]{64}\.html", html_path.name):
            raise ValueError("unsafe evidence reference")
        if sha256(html_path) != html_path.stem:
            raise ValueError("saved capture checksum mismatch")
        canonical = found[str(row["sterbrust_product_id"])]
        scope = equipment_scope(row["name"], row.get("source_category", ""))
        props = [{"name": p["property_name"], "value": p["value_flat"], "unit": p.get("unit", ""),
                  "property_id": p.get("property_id"), "property_code": p.get("property_code")}
                 for p in canonical["properties"] if p.get("value_flat") not in (None, "", "N")]
        pairs.append({"source": row["source"], "external_id": row["external_id"], "source_url": row["source_url"],
            "classification": row["classification"], "full_model_confirmed": row["full_model_confirmed"],
            "model": row["model"], "name": row["name"], "source_category": row.get("source_category", ""),
            "equipment_type": scope, "sterbrust_product_id": row["sterbrust_product_id"],
            "sterbrust_name": canonical["name"], "canonical_snapshot_hash": canonical["snapshot_hash"],
            "evidence_ref": ref, "evidence_sha256": html_path.stem,
            "source_properties": source_properties(html_path.read_text(encoding="utf-8"), row["source"]),
            "sterbrust_properties": props,
            "sections": section_evidence(canonical.get("category_ids") or [canonical["category_id"]], sections)})
    # All active/inactive records contribute model/name retrieval evidence. The
    # index supplies absence support only; an independent absence review is needed.
    models, families, csv_ids = {}, {}, set()
    with registry_csv.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = str(row["sterbrust_product_id"])
            if sid in csv_ids:
                raise ValueError("duplicate registry CSV ID")
            csv_ids.add(sid)
            if sid in found and row["snapshot_hash"] != found[sid]["snapshot_hash"]:
                raise ValueError("CSV/JSONL canonical snapshot mismatch")
            keys = {normalize_model(row.get("model_raw"), row.get("brand_raw")),
                    extract_model(row["name"]), *model_tokens(row["name"])} - {""}
            for key in keys:
                models.setdefault(key, set()).add(sid)
                prefix = re.match(r"[a-zа-я]+\d+", key)
                families.setdefault(prefix.group(0) if prefix else key, set()).add(sid)
    if csv_ids != seen_ids:
        raise ValueError("CSV/JSONL catalog coverage differs")
    bundle = build_evidence(pairs)
    if any(sha256(path) != digest for path, digest in pinned_inputs.items()):
        raise ValueError("offline inputs changed during evidence build")
    index = {"complete": True, "sha256": registry_sha, "products_total": len(csv_ids),
             "includes_inactive": True, "absence_is_support_only": True,
             "models": {k: sorted(v) for k, v in sorted(models.items())},
             "families": {k: sorted(v) for k, v in sorted(families.items())}}
    summary = {"diagnostic_only": True, "ready_to_create_real_records": 0,
               "confirmed_input_records": len(confirmed), "usable_confirmed_pairs": len(pairs),
               "unique_canonical_examples": len(targets), "excluded": excluded,
               "synonym_mappings": len(bundle["synonym_mappings"]),
               "safe_synonym_mappings": sum(m["verdict"] == "SAFE" for m in bundle["synonym_mappings"]),
               "section_mappings": len(bundle["section_mappings"]),
               "safe_section_mappings": sum(m["verdict"] == "SAFE" for m in bundle["section_mappings"]),
               "input_sha256": {str(p.relative_to(ROOT)): digest for p, digest in pinned_inputs.items()},
               "registry_jsonl_sha256": digest.hexdigest()}
    output.mkdir(parents=True)
    for filename, body in (("CONFIRMED_PAIR_CHARACTERISTICS.json", pairs),
                           ("SCOPED_CHARACTERISTIC_EVIDENCE.json", bundle),
                           ("SECTION_EVIDENCE_MAP.json", bundle["section_mappings"]),
                           ("CANONICAL_ABSENCE_INDEX.json", index)):
        (output / filename).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["output_sha256"] = {p.name: sha256(p) for p in sorted(output.glob("*.json"))}
    (output / "BUILD_VERIFIED.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.output_dir), ensure_ascii=False, indent=2), flush=True)
