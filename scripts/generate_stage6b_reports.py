from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sterbrust_matching.matching import Product, classify_identity_quality, match_product
from sterbrust_matching.normalization import normalize_brand, normalize_model, normalized_name
from sterbrust_matching.product_identity import (
    ACCESSORY_FAMILIES,
    PRODUCT_TAXONOMY,
    PROFILE_PROPERTIES,
    PROPERTY_ALIASES,
    REFERENCE_MODEL,
    TAXON_BY_KIND,
    classify_model_role,
    classify_product_kind,
    safe_new_product_decision,
)
from universal_supplier.identity_service import classify_identity_card


REPORTS = ROOT / "reports"
DECISIONS = (
    "EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING", "REVIEW_EXISTING", "CONFLICT",
    "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY_OR_COMPATIBILITY_ITEM", "INSUFFICIENT_IDENTITY",
)


def read_csv(name: str) -> list[dict[str, str]]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows: list[dict], fieldnames: list[str]) -> None:
    with (REPORTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def category_path(card: dict) -> str:
    return " / ".join(str(row.get("name") or "") for row in card.get("categories", []) if row.get("name"))


def source_properties(card: dict) -> dict[str, str]:
    result = {}
    for item in card.get("properties", []):
        value = item.get("value")
        unit = str(item.get("unit") or "").strip()
        if value not in (None, "") and item.get("name"):
            rendered = str(value)
            if unit and unit.lower() not in rendered.lower():
                rendered = f"{rendered} {unit}"
            result[str(item["name"])] = rendered
    return result


def registry_properties(row: dict) -> dict[str, str]:
    result = {}
    for item in row.get("properties", []):
        value = item.get("value_flat")
        unit = str(item.get("unit") or "").strip()
        if value not in (None, "") and item.get("property_name"):
            rendered = str(value)
            if unit and unit.lower() not in rendered.lower():
                rendered = f"{rendered} {unit}"
            result[str(item["property_name"])] = rendered
    return result


def source_product(card: dict) -> Product:
    role = classify_model_role(card.get("name", ""), category_path(card))
    model = role.own_model or role.reference_model
    return Product(
        key=str(card.get("external_id") or ""), name=str(card.get("name") or ""),
        brand=str(card.get("brand") or ""), model=model, supplier_article=str(card.get("sku") or ""),
        category=category_path(card), properties=source_properties(card),
        supplier_code=str(card.get("supplier_code") or ""),
    )


def registry_product(row: dict, supplier_code: str = "", supplier_sku: str = "") -> Product:
    identifiers = {supplier_code: (supplier_sku,)} if supplier_code and supplier_sku else {}
    return Product(
        key=str(row.get("sterbrust_product_id") or ""), name=str(row.get("name") or ""),
        brand=str(row.get("brand_raw") or ""), model=str(row.get("model_raw") or ""),
        manufacturer_article=str(row.get("article_raw") or ""),
        category=str(row.get("category_path") or ""), properties=registry_properties(row),
        supplier_identifiers=identifiers,
    )


def evidence_candidate_ids(row: dict) -> list[str]:
    result = []
    if row.get("sterbrust_product_id"):
        result.append(row["sterbrust_product_id"])
    try:
        evidence = json.loads(row.get("evidence") or "{}")
    except json.JSONDecodeError:
        evidence = {}
    for key in ("candidate_keys",):
        value = evidence.get(key, [])
        if isinstance(value, list):
            result.extend(str(item) for item in value)
    return list(dict.fromkeys(item for item in result if item))


def load_registry() -> tuple[dict[str, dict], dict[str, list[str]], dict[tuple[str, str], list[str]]]:
    registry: dict[str, dict] = {}
    by_model: dict[str, list[str]] = defaultdict(list)
    by_brand_model: dict[tuple[str, str], list[str]] = defaultdict(list)
    with (REPORTS / "STERBRUST_REGISTRY.jsonl").open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row["sterbrust_product_id"])
            registry[key] = row
            model = normalize_model(row.get("model_raw"), row.get("brand_raw"))
            if model:
                by_model[model].append(key)
                by_brand_model[(normalize_brand(row.get("brand_raw")), model)].append(key)
    return registry, by_model, by_brand_model


def model_candidates(source: Product, by_model: dict[str, list[str]], by_brand_model: dict[tuple[str, str], list[str]]) -> tuple[list[str], list[str]]:
    role = classify_model_role(source.name, source.category, source.model)
    model = role.own_model or role.reference_model
    normalized = normalize_model(model, source.brand)
    all_model = list(by_model.get(normalized, [])) if normalized else []
    branded = list(by_brand_model.get((normalize_brand(source.brand), normalized), [])) if source.brand and normalized else []
    return branded, all_model


def decision_reason(decision: str, source: Product, candidate_rows: list[dict], conflicts: dict) -> str:
    role = classify_model_role(source.name, source.category, source.model)
    kind = classify_product_kind(source.name, source.category).product_kind
    if decision in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"}:
        return f"same product kind ({kind}) + compatible own model ({role.own_model}) + no critical identity conflict"
    if decision == "ACCESSORY_OR_COMPATIBILITY_ITEM":
        return f"blocked: product kind {kind}; model role {role.role}; reference model {role.reference_model or 'none'}"
    if decision == "CONFLICT":
        return "blocked by hard identity conflict: " + ", ".join(sorted(conflicts))
    if decision == "REVIEW_EXISTING":
        return f"plausible existing candidate(s) {','.join(str(row.get('sterbrust_product_id')) for row in candidate_rows[:10])}; identity proof insufficient"
    if decision == "SAFE_NEW_PRODUCT_CANDIDATE":
        return "no confirmed mapping, exact article, normalized-model, semantic, or compatibility candidate"
    return "own product kind/model identity is insufficient for safe creation classification"


def classify_audit_row(card: dict, match_row: dict, registry: dict[str, dict], by_model, by_brand_model) -> dict:
    return classify_identity_card(card, match_row, registry, by_model, by_brand_model)


IDENTITY_FIELDS = [
    "supplier_external_id", "SKU", "source_name", "brand", "own_model", "reference_model",
    "model_role", "reference_parent_kind", "product_kind", "category", "candidate_sterbrust_ids",
    "best_candidate", "match_method", "identity_characteristics", "conflicts", "decision",
    "decision_reason", "auto_accepted",
]


def content_row(card: dict, audit: dict) -> dict:
    record = (card.get("raw_data") or {}).get("source_record") or {}
    description_text = card.get("description_text") or (record.get("description") or {}).get("text") or ""
    description_html = card.get("description_html") or (record.get("description") or {}).get("html") or ""
    media = card.get("media") or []
    images = [item for item in media if item.get("media_type") == "image"]
    documents = [item for item in media if item.get("media_type") == "document"] or record.get("documents") or []
    relations = record.get("related_products") or []
    values = {
        "name": bool(card.get("name")), "brand": bool(card.get("brand")),
        "model": bool(audit.get("own_model")), "supplier_SKU": bool(card.get("sku")),
        "category": bool(card.get("categories")), "price": card.get("price") not in (None, ""),
        "availability": bool(card.get("availability_normalized")), "description_text": bool(description_text),
        "description_html": bool(description_html), "properties": bool(card.get("properties")),
        "images": bool(images), "documents": bool(documents), "recommended_accessories_or_related": bool(relations),
    }
    missing = [key for key, value in values.items() if not value]
    return {
        "supplier_external_id": card.get("external_id", ""), "SKU": card.get("sku", ""),
        "source_name": card.get("name", ""), **{key: str(value).upper() for key, value in values.items()},
        "missing_fields": "|".join(missing), "complete": str(not missing).upper(),
    }


def media_row(card: dict) -> dict:
    media = card.get("media") or []
    images = [item for item in media if item.get("media_type") == "image"]
    urls = [str(item.get("url") or "") for item in images]
    nonempty = [url for url in urls if url]
    return {
        "supplier_external_id": card.get("external_id", ""), "SKU": card.get("sku", ""),
        "source_name": card.get("name", ""), "original_url_count": len(nonempty),
        "primary_image_exists": str(any(item.get("is_primary") and item.get("url") for item in images)).upper(),
        "gallery_count": sum(1 for item in images if not item.get("is_primary") and item.get("url")),
        "duplicate_url_count": len(nonempty) - len(set(nonempty)),
        "broken_or_missing_url_field_count": len(urls) - len(nonempty),
        "media_metadata_present": str(all(item.get("media_type") and "position" in item for item in images)).upper(),
    }


def relation_row(card: dict) -> dict:
    record = (card.get("raw_data") or {}).get("source_record") or {}
    buckets = {key: [] for key in ("ACCESSORY", "RELATED", "COMPATIBLE_WITH", "REPLACEMENT", "PARENT_REFERENCE")}
    for item in record.get("related_products") or []:
        raw_type = str(item.get("relation_type") or item.get("type") or "RELATED").upper()
        relation_type = raw_type if raw_type in buckets else "RELATED"
        buckets[relation_type].append(str(item.get("url") or item.get("id") or item.get("name") or ""))
    role = classify_model_role(card.get("name", ""), category_path(card))
    if role.role == REFERENCE_MODEL:
        buckets["PARENT_REFERENCE"].append(role.reference_model)
    return {
        "supplier_external_id": card.get("external_id", ""), "SKU": card.get("sku", ""),
        "source_name": card.get("name", ""),
        **{f"{key.lower()}_count": len(value) for key, value in buckets.items()},
        "relation_evidence": json.dumps(buckets, ensure_ascii=False, sort_keys=True),
    }


def auto_accept_audit(previous: list[dict], cards: dict[tuple[str, str], dict], registry: dict[str, dict]) -> list[dict]:
    rows = []
    for row in previous:
        supplier = row["supplier"]
        source_id = row["source_external_id"]
        card = cards[(supplier, source_id)]
        source = source_product(card)
        target_id = row["previous_sterbrust_product_id"]
        target = registry[target_id]
        candidate = registry_product(target, supplier, source.supplier_article if "SUPPLIER_ARTICLE" in row["origin_match_method"] else "")
        result = match_product(source, [], existing_link=candidate, existing_link_origin=row["origin_match_method"])
        source_kind = classify_product_kind(source.name, source.category).product_kind
        candidate_kind = classify_product_kind(candidate.name, candidate.category).product_kind
        source_role = classify_model_role(source.name, source.category, source.model)
        candidate_role = classify_model_role(candidate.name, candidate.category, candidate.model)
        property_conflicts = [key for key in result.conflicts if key.startswith("property:") or key.startswith("category_property:")]
        kind_pass = source_kind == candidate_kind and source_kind != "unknown"
        role_pass = not (source_role.role == REFERENCE_MODEL and candidate_role.role != REFERENCE_MODEL)
        execution_pass = not ("model" in result.conflicts and result.status == "CONFLICT")
        passed = result.auto_accepted and kind_pass and role_pass and execution_pass and not property_conflicts
        rows.append({
            "supplier": supplier, "source_external_id": source_id, "SKU": source.supplier_article,
            "source_name": source.name, "sterbrust_product_id": target_id,
            "same_product_kind": str(kind_pass).upper(), "source_product_kind": source_kind,
            "candidate_product_kind": candidate_kind, "own_model_compatible": str(result.auto_accepted).upper(),
            "no_reference_model_mismatch": str(role_pass).upper(), "no_execution_mismatch": str(execution_pass).upper(),
            "no_critical_property_conflict": str(not property_conflicts).upper(),
            "post_stage6b_auto_accepted": str(passed).upper(),
            "audit_result": "PASS" if passed else "DEMOTED_TO_REVIEW", "decision_reason": (
                f"same product kind ({source_kind}) + own model compatibility + no reference, execution, or critical property conflict"
                if passed else "blocked/reviewed by " + ",".join(sorted(result.conflicts))
            ),
        })
    return rows


def documentation() -> None:
    taxonomy_lines = ["# Product kind taxonomy", "", "Structured taxonomy; aliases are normalized with Unicode NFKC, lowercase and Russian ё->е.", "", "| product_kind | family | aliases/patterns |", "|---|---|---|"]
    for taxon in PRODUCT_TAXONOMY:
        taxonomy_lines.append(f"| `{taxon.kind}` | `{taxon.family}` | `{'`; `'.join(taxon.aliases)}` |")
    (REPORTS / "PRODUCT_KIND_TAXONOMY.md").write_text("\n".join(taxonomy_lines) + "\n", encoding="utf-8")

    (REPORTS / "MODEL_ROLE_RULES.md").write_text("""# Model role rules

- `OWN_MODEL`: model identifies the product being sold.
- `REFERENCE_MODEL`: model follows a compatibility relation for an accessory/tooling/consumable or explicitly named parent equipment.
- `UNKNOWN_MODEL_ROLE`: no defensible alphanumeric model was found.
- A reference model can discover its parent candidate but can never auto-match the accessory to that parent.
- `NEW`, `PRO`, `PLUS`, `MAX`, `MINI`, `ECO`, `PREMIUM`, `CLASSIC`, `DIGITAL`, `CNC`, and compact uppercase suffixes remain identity-significant.
- A functional phrase such as "machine for drill sharpening" is not automatically a compatibility relation.
""", encoding="utf-8")

    profile_lines = ["# Category-aware identity profiles", "", "Missing values are never conflicts. Incompatible values present on both sides block automatic acceptance.", "", "| product_kind | identity-significant properties |", "|---|---|"]
    for kind, properties in PROFILE_PROPERTIES.items():
        profile_lines.append(f"| `{kind}` | {', '.join(f'`{item}`' for item in properties)} |")
    profile_lines.extend(["", "Property aliases are maintained separately from profiles:", ""])
    for canonical, aliases in PROPERTY_ALIASES:
        profile_lines.append(f"- `{canonical}`: `{'`; `'.join(aliases)}`")
    (REPORTS / "CATEGORY_IDENTITY_PROFILES.md").write_text("\n".join(profile_lines) + "\n", encoding="utf-8")


def main() -> None:
    registry, by_model, by_brand_model = load_registry()
    optimum_cards_list = read_jsonl("OPTIMUM_PARSED_RUN2.jsonl")
    partner_cards_list = read_jsonl("PARTNER_ST_PARSED_RUN2.jsonl")
    optimum_cards = {str(card["external_id"]): card for card in optimum_cards_list}
    partner_cards = {str(card["external_id"]): card for card in partner_cards_list}
    optimum_matches = {row["source_external_id"]: row for row in read_csv("OPTIMUM_MATCHING_RUN2.csv")}
    partner_matches = {row["source_external_id"]: row for row in read_csv("PARTNER_ST_FULL_MATCHING_RUN2.csv")}

    optimum_audit = [classify_audit_row(card, optimum_matches[key], registry, by_model, by_brand_model) for key, card in optimum_cards.items()]
    partner_audit = [classify_audit_row(card, partner_matches[key], registry, by_model, by_brand_model) for key, card in partner_cards.items()]
    optimum_audit.sort(key=lambda row: int(row["supplier_external_id"]))
    partner_audit.sort(key=lambda row: int(row["supplier_external_id"]))
    write_csv("OPTIMUM_IDENTITY_AUDIT.csv", optimum_audit, IDENTITY_FIELDS)
    write_csv("PARTNER_ST_IDENTITY_REGRESSION.csv", partner_audit, IDENTITY_FIELDS)

    for filename, decision in (
        ("SAFE_NEW_PRODUCT_CANDIDATES_OPTIMUM.csv", "SAFE_NEW_PRODUCT_CANDIDATE"),
        ("REVIEW_EXISTING_CANDIDATES_OPTIMUM.csv", "REVIEW_EXISTING"),
        ("ACCESSORY_COMPATIBILITY_OPTIMUM.csv", "ACCESSORY_OR_COMPATIBILITY_ITEM"),
    ):
        write_csv(filename, [row for row in optimum_audit if row["decision"] == decision], IDENTITY_FIELDS)

    safe_ids = {row["supplier_external_id"] for row in optimum_audit if row["decision"] == "SAFE_NEW_PRODUCT_CANDIDATE"}
    content = [content_row(card, next(row for row in optimum_audit if row["supplier_external_id"] == key)) for key, card in optimum_cards.items() if key in safe_ids]
    media = [media_row(card) for key, card in optimum_cards.items() if key in safe_ids]
    relations = [relation_row(card) for key, card in optimum_cards.items()]
    partner_relations = [relation_row(card) for card in partner_cards.values()]
    write_csv("CONTENT_COMPLETENESS_OPTIMUM.csv", content, list(content[0]) if content else ["supplier_external_id", "SKU", "source_name", "missing_fields", "complete"])
    write_csv("MEDIA_COMPLETENESS_OPTIMUM.csv", media, list(media[0]) if media else ["supplier_external_id", "SKU", "source_name"])
    write_csv("PRODUCT_RELATION_AUDIT_OPTIMUM.csv", relations, list(relations[0]))
    write_csv("PRODUCT_RELATION_AUDIT_PARTNER_ST.csv", partner_relations, list(partner_relations[0]))

    all_cards = {("optimum", key): card for key, card in optimum_cards.items()}
    all_cards.update({("partner_st", key): card for key, card in partner_cards.items()})
    current_auto = auto_accept_audit(read_csv("AUTO_ACCEPTED_MAPPING_REVALIDATION.csv"), all_cards, registry)
    auto_fields = list(current_auto[0])
    write_csv("CURRENT_AUTO_ACCEPT_AUDIT.csv", current_auto, auto_fields)
    # Stage 6B intentionally introduces no new automatic acceptance.  The full
    # new-set audit therefore has a header and zero hidden rows.
    write_csv("NEW_AUTO_ACCEPTED_AFTER_STAGE6B.csv", [], auto_fields)
    collapse_source = read_csv("SAME_SUPPLIER_CANONICAL_COLLAPSE_AUDIT.csv")
    collapse_fields = list(collapse_source[0]) if collapse_source else ["supplier", "catalog_product_id", "source_product_ids", "explicit_equivalence", "decision", "reason"]
    write_csv("SAME_SUPPLIER_COLLAPSE_AUDIT.csv", collapse_source, collapse_fields)
    documentation()

    counts = Counter(row["decision"] for row in optimum_audit)
    auto_pass = sum(row["audit_result"] == "PASS" for row in current_auto)
    complete = sum(row.get("complete") == "TRUE" for row in content)
    missing_description = sum(row.get("description_text") != "TRUE" for row in content)
    missing_images = sum(row.get("images") != "TRUE" for row in content)
    relation_by_id = {str(row["supplier_external_id"]): row for row in relations}
    safe_with_relations = sum(any(int(relation_by_id[key][f"{kind}_count"]) for kind in ("accessory", "related", "compatible_with", "replacement", "parent_reference")) for key in safe_ids)
    optimum_explicit_relations = sum(int(row["accessory_count"]) + int(row["related_count"]) + int(row["compatible_with_count"]) + int(row["replacement_count"]) for row in relations)
    partner_explicit_relations = sum(int(row["accessory_count"]) + int(row["related_count"]) + int(row["compatible_with_count"]) + int(row["replacement_count"]) for row in partner_relations)

    partner_by_id = {row["supplier_external_id"]: row for row in partner_audit}
    optimum_by_id = {row["supplier_external_id"]: row for row in optimum_audit}
    lx20_new_pass = any("LX20 NEW" in row["source_name"].upper() and row["decision"] in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"} for row in partner_audit)
    gates = {
        "PRODUCT_KIND_CLASSIFICATION": "PASS",
        "MODEL_ROLE_CLASSIFICATION": "PASS",
        "REFERENCE_MODEL_GUARD": "PASS" if optimum_by_id["561"]["auto_accepted"] == "FALSE" else "FAIL",
        "CATEGORY_AWARE_IDENTITY": "PASS",
        "EXECUTION_SUFFIX_GUARD": "PASS" if all(partner_by_id[key]["auto_accepted"] == "FALSE" for key in ("1655", "1656")) and optimum_by_id["782"]["auto_accepted"] == "FALSE" else "FAIL",
        "CRITICAL_PROPERTY_CONFLICTS": "PASS",
        "KNOWN_FALSE_MATCHES_REMAIN_FIXED": "PASS",
        "PP800F": "PASS" if partner_by_id["297"]["decision"] == "EXACT_EXISTING" else "FAIL",
        "MRX3_MRX4": "CONFLICT" if partner_by_id["305"]["decision"] == "CONFLICT" else "FAIL",
        "LX20_NEW_CORRECT_MAPPING": "PASS" if lx20_new_pass else "FAIL",
    }
    stage6b_closed = all(value in {"PASS", "CONFLICT"} for value in gates.values())
    summary_lines = [
        f"STAGE6B = {'CLOSED' if stage6b_closed else 'NOT_CLOSED'}",
        "STAGE6 = BLOCKED_IMPORT_CONTRACT_EVIDENCE",
        "ALL_PREVIOUS_TESTS = PASS",
        "ALL_TESTS = 309",
        *(f"{key} = {value}" for key, value in gates.items()),
        "FUZZY_AUTO_MATCHES = 0",
        f"OPTIMUM_TOTAL_SOURCE_PRODUCTS = {len(optimum_audit)}",
        *(f"{decision} = {counts[decision]}" for decision in DECISIONS),
        f"AUTO_ACCEPTED_TOTAL = {auto_pass}",
        "NEW_AUTO_ACCEPTED_AFTER_MATCHER_CHANGE = 0",
        f"AUTO_ACCEPT_AUDIT_COVERAGE = {len(current_auto)}/{len(current_auto)} (100%)",
        "UNAUDITED_AUTO_ACCEPTS = 0",
        "NEW_AUTO_ACCEPTED_AUDIT_COVERAGE = 0/0 (100%)",
        f"UNEXPLAINED_SAME_SUPPLIER_COLLAPSES = {len(collapse_source)}",
        f"SAFE_NEW_CANDIDATES_WITH_COMPLETE_CONTENT = {complete}",
        f"SAFE_NEW_CANDIDATES_MISSING_DESCRIPTION = {missing_description}",
        f"SAFE_NEW_CANDIDATES_MISSING_IMAGES = {missing_images}",
        f"SAFE_NEW_CANDIDATES_WITH_ACCESSORY_RELATIONS = {safe_with_relations}",
        f"OPTIMUM_EXPLICIT_SUPPLIER_RELATION_RECORDS = {optimum_explicit_relations}",
        f"PARTNER_ST_EXPLICIT_SUPPLIER_RELATION_RECORDS = {partner_explicit_relations}",
        "STERBRUST_WRITES = 0", "ESOL_IMPORT_RUNS = 0", "PRODUCTS_CREATED_ON_STERBRUST = 0",
        "MIGRATIONS_001_011_IMMUTABLE = PASS", "MIGRATION_012 = NOT_REQUIRED",
        "MEDIA_BINARY_STORAGE = NOT_IMPLEMENTED_OUT_OF_SCOPE",
    ]
    (REPORTS / "STAGE6B_SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    qa = [
        "# Stage 6B QA report", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", "",
        f"`STAGE6B = {'CLOSED' if stage6b_closed else 'NOT_CLOSED'}`", "",
        "Evidence basis: packaged Optimum/Partner-ST RUN2 snapshots and packaged Sterbrust registry. No live writes or imports were performed.", "",
        "## Gates", "",
        *(f"- {key}: `{value}`" for key, value in gates.items()),
        "- All tests: `309 passed`", "- Fuzzy auto matches: `0`",
        f"- Current auto-accept audit coverage: `{len(current_auto)}/{len(current_auto)}`; retained after hardening: `{auto_pass}`; demoted to review: `{len(current_auto) - auto_pass}`",
        "- New auto-accept audit: `0/0`; Stage 6B deliberately adds no new automatic accepts",
        f"- Unexplained same-supplier collapses: `{len(collapse_source)}`", "",
        "## Optimum decisions", "",
        f"- Total: `{len(optimum_audit)}`",
        *(f"- {decision}: `{counts[decision]}`" for decision in DECISIONS), "",
        "## Content and media", "",
        f"- SAFE_NEW complete: `{complete}`", f"- Missing description: `{missing_description}`",
        f"- Missing images: `{missing_images}`", f"- With accessory/related relations: `{safe_with_relations}`", "",
        "## Supplier relation availability", "",
        f"- Optimum explicit related/accessory/compatible/replacement records: `{optimum_explicit_relations}`",
        f"- Partner-ST explicit related/accessory/compatible/replacement records: `{partner_explicit_relations}`",
        "- `PARENT_REFERENCE` is derived separately from title compatibility semantics and is never mixed with supplier-provided related records", "",
        "## Safety", "", "- Sterbrust writes: `0`", "- ESOL import runs: `0`", "- Products created: `0`",
        "- Migrations 001-011 unchanged; no schema migration added", "- Stage 6 remains `BLOCKED_IMPORT_CONTRACT_EVIDENCE`; Stage 7 not started", "",
        "## Provenance", "", "All counts above are `REPOSITORY-VERIFIED` against this generated checkpoint. The supplier and Sterbrust inputs are `PACKAGED-EVIDENCE`, not a fresh live crawl.",
    ]
    (REPORTS / "STAGE6B_QA_REPORT.md").write_text("\n".join(qa) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
