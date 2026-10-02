"""Pure cross-source NEW proposals; no crawl, database or canonical creation.

Call after canonical matching. Inputs must carry independently extracted brand,
full model, OWN_MODEL role, product context and sanitized evidence reference.
Every member pair needs positive equivalence evidence, not merely no conflict.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from itertools import combinations
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from sterbrust_matching.matching import Product, classify_identity_quality
from sterbrust_matching.normalization import extract_model, normalize_brand, normalize_model
from sterbrust_matching.product_identity import (
    OWN_MODEL, TAXON_BY_KIND, PROPERTY_POLICIES as CATEGORY_PROPERTY_POLICIES, canonical_property_name,
    classify_model_role, classify_product_kind,
)
from sterbrust_matching.property_normalization import (
    DEFAULT_PROPERTY_POLICIES, IDENTITY_CRITICAL, compare_property, normalize_property,
    PropertyPolicy,
)
from .execution_evidence import axis_execution_guard, axis_signature, condition_execution_guard


RULE_VERSION = "cross-source-new-1"
SOURCES = frozenset({"intervesp", "beka_mak", "beka_mak_tr"})
BRAND_ALIASES = {alias: "beka-mak" for alias in (
    "bekamak", "beka mak", "beka-mak", "бекамак", "бека-мак",
)}
PROPERTY_POLICIES = {**DEFAULT_PROPERTY_POLICIES, **CATEGORY_PROPERTY_POLICIES}
# Namespaced geometric anchors supplied only by the scoped SAFE-evidence
# bridge. These are local proposal policies, not global matcher synonyms.
TYPED_GEOMETRIC_ANCHORS = {
    'typed_bandsaw_blade_length': 'length', 'typed_bandsaw_blade_width': 'width',
    'typed_bandsaw_blade_thickness': 'height', 'typed_bandsaw_cut_round_0': 'diameter',
    'typed_lathe_distance_between_centers': 'length', 'typed_lathe_spindle_bore': 'diameter',
    'typed_vise_jaw_width': 'width',
}
PROPERTY_POLICIES.update({key: PropertyPolicy(kind, IDENTITY_CRITICAL)
                         for key, kind in TYPED_GEOMETRIC_ANCHORS.items()})
# Electrical ratings alone are common to many different configurations.
ANCHOR_PROPERTIES = {key for key, policy in PROPERTY_POLICIES.items()
                     if policy.importance == IDENTITY_CRITICAL
                     and key not in {"voltage", "power", "motor_power", "execution"}}


def _text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _source_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return _text(row, "source"), _text(row, "external_id")


def _has_conflicts(value):
    if isinstance(value,str):
        try: return bool(json.loads(value))
        except (ValueError,TypeError): return bool(value.strip())
    return bool(value)


def _model_family(model: str) -> str:
    # Retrieval/quarantine only: this prefix never establishes equivalence.
    match = re.match(r"[a-zа-я]+\d+", model)
    return match.group(0) if match else model


def _properties(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for name, value in (row.get("properties") or {}).items():
        key = canonical_property_name(name) or str(name)
        if key in result and str(result[key]) != str(value):
            raise ValueError("contradictory aliases for one property")
        result[key] = value
    return result


def _parsed(value: Any, key: str) -> bool:
    normalized = normalize_property(value, PROPERTY_POLICIES[key].property_type)
    return bool(normalized.numeric_value is not None or normalized.normalized_text) and not any(
        token in normalized.normalization_rule for token in ("unparsed", "unknown_unit", "invalid"))


def _product(row: Mapping[str, Any]) -> Product:
    article = _text(row, "manufacturer_article")
    if (not _text(row, "manufacturer_article_evidence_ref")
            or normalize_model(article) == normalize_model(row.get("external_id"))):
        article = ""
    return Product(
        key=json.dumps(_source_key(row)), supplier_code=_text(row, "source"),
        name=_text(row, "name"), brand=normalize_brand(row.get("brand"), BRAND_ALIASES),
        model=_text(row, "model"), category=_text(row, "source_category"),
        product_kind=_text(row, "product_kind"), properties=_properties(row),
        manufacturer_article=article,
    )


def _individual_reasons(row: Mapping[str, Any], product: Product, allowed_sources=SOURCES) -> list[str]:
    reasons = []
    model = normalize_model(product.model, product.brand)
    if row.get("classification") != "NEW_CANDIDATE":
        reasons.append("not_new_candidate")
    if product.supplier_code not in allowed_sources or not _text(row, "external_id"):
        reasons.append("invalid_source_identity")
    if not _text(row, "new_candidate_id"):
        reasons.append("missing_new_candidate_id")
    if _text(row, "sterbrust_product_id") or _text(row, "proposed_sterbrust_id"):
        reasons.append("existing_canonical_candidate_present")
    if not product.brand or not model or not re.search(r"[a-zа-я]", model) or not re.search(r"\d", model):
        reasons.append("insufficient_brand_or_full_model")
    if not product.name or not _text(row, "source_url") or not _text(row, "evidence_ref"):
        reasons.append("missing_source_evidence")
    inferred_kind = classify_product_kind(product.name, product.category).product_kind
    if product.product_kind not in TAXON_BY_KIND:
        reasons.append("unknown_product_context")
    elif inferred_kind != "unknown" and inferred_kind != product.product_kind:
        reasons.append("product_context_contradiction")
    role = classify_model_role(product.name, product.category, product.model)
    if row.get("model_role") != OWN_MODEL or role.role != OWN_MODEL:
        reasons.append("own_model_not_proven")
    title_model = normalize_model(extract_model(product.name), product.brand)
    if title_model and model and title_model != model:
        reasons.append("title_model_execution_contradiction")
    execution = normalize_model(row.get("execution"))
    if execution and model and not model.endswith(execution):
        reasons.append("full_model_execution_contradiction")
    if _has_conflicts(row.get("match_conflicts")) or _has_conflicts(row.get("semantic_conflicts")):
        reasons.append("upstream_semantic_contradiction")
    article = normalize_model(product.manufacturer_article)
    typed = product.properties.keys() & TYPED_GEOMETRIC_ANCHORS.keys()
    proof = row.get('typed_group_anchor_evidence') or {}
    if typed and (proof.get('basis') != 'SAFE_confirmed_pairs_scoped_unit_conversion_only'
                  or proof.get('source_category') != product.category
                  or not proof.get('scope')
                  or any(not key.startswith('typed_' + proof['scope'] + '_')
                         or proof.get('anchors', {}).get(key) != product.properties[key] for key in typed)):
        reasons.append('typed_anchor_provenance_missing_or_scope_mismatch')
    anchors = {key for key in ANCHOR_PROPERTIES & product.properties.keys()
               if _parsed(product.properties[key], key)}
    if not anchors and (not article or article == model):
        reasons.append("independent_equivalence_evidence_missing")
    return reasons


def _pair_evidence(left: Product, right: Product) -> tuple[list[str], dict[str, Any]]:
    reasons = []
    checks = []
    axis = axis_execution_guard(left.name,left.properties,right.name,right.properties)
    if axis['blocked']: reasons.append(axis['reason'])
    condition = condition_execution_guard(left.name,left.properties,right.name,right.properties)
    if condition['blocked']: reasons.append(condition['reason'])
    for source, target in ((left, right), (right, left)):
        # Semantic guards are shared; canonical property keys are compared below
        # with their typed category policies rather than fallback string policy.
        conflict_class, evidence, conflicts, warnings = classify_identity_quality(
            replace(source, properties={}), replace(target, properties={}))
        checks.append({"evidence": evidence, "conflicts": conflicts, "warnings": warnings})
        if conflict_class or conflicts or warnings:
            reasons.append("semantic_or_property_contradiction")
    article = normalize_model(left.manufacturer_article)
    article_equal = bool(article and article == normalize_model(right.manufacturer_article)
                         and article != normalize_model(left.model, left.brand))
    anchors = {}
    comparisons = {}
    for key in sorted(left.properties.keys() & right.properties.keys()):
        if left.properties[key] in (None, "") or right.properties[key] in (None, ""):
            continue
        comparison = compare_property(left.properties[key], right.properties[key], key, PROPERTY_POLICIES)
        comparisons[key] = comparison.evidence()
        if not comparison.compatible or "WARNING" in comparison.classification:
            reasons.append("semantic_or_property_contradiction")
        if (key in ANCHOR_PROPERTIES and comparison.compatible and _parsed(left.properties[key], key)
                and _parsed(right.properties[key], key)
                and "WARNING" not in comparison.classification):
            anchors[key] = comparison.evidence()
    if not article_equal and not anchors:
        reasons.append("shared_equivalence_evidence_missing")
    return sorted(set(reasons)), {
        "manufacturer_article_equal": article_equal, "shared_identity_properties": anchors,
        "property_comparisons": comparisons, "semantic_checks": checks,
        "axis_execution_evidence": axis,
    }


def _group_id(product: Product) -> str:
    # Membership-independent: adding a third supplier preserves the same ID.
    signature = [RULE_VERSION, product.brand, normalize_model(product.model, product.brand),
                 product.product_kind, OWN_MODEL]
    body = json.dumps(signature, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "NEWG-" + hashlib.sha256(body).hexdigest()[:24].upper()


def propose_new_groups(rows: Iterable[Mapping[str, Any]], *, allowed_sources=SOURCES) -> dict[str, list[dict[str, Any]]]:
    """Return groups, ungrouped single-source NEWs, and explicit reviews.

    Duplicate source identities/IDs and variant ambiguity quarantine the whole
    related cohort. Pairwise checks prevent a transitive evidence bridge. This
    proves only cross-source equivalence; canonical absence and SECTION_ID
    remain independent downstream gates.
    """
    if not set(allowed_sources).issubset(SOURCES | {'partner_st','optimum'}):
        raise ValueError('Unknown supplier namespace in grouping scope')
    records = [dict(row) for row in rows]
    result: dict[str, list[dict[str, Any]]] = {"groups": [], "ungrouped": [], "review": []}
    identities = defaultdict(list)
    candidate_ids = defaultdict(set)
    for row in records:
        identities[_source_key(row)].append(row)
        if _text(row, "new_candidate_id"):
            candidate_ids[_text(row, "new_candidate_id")].add(_source_key(row))

    def review(row: dict[str, Any], reasons: list[str], evidence: Any = None) -> None:
        result["review"].append({**row, "new_group_id": None,
            "classification": row.get("classification") if row.get("classification") in {"REVIEW", "CONFLICT"} else "REVIEW",
            "grouping_status": "REVIEW", "grouping_reasons": sorted(set(reasons)),
            "grouping_evidence": evidence, "grouping_rule_version": RULE_VERSION})

    cohorts = defaultdict(list)
    for key, copies in sorted(identities.items()):
        # Identical replay is a no-op; conflicting copies never choose a winner.
        unique = {json.dumps(row, sort_keys=True, ensure_ascii=False, default=str): row for row in copies}
        for _, row in sorted(unique.items()):
            try:
                product = _product(row)
            except (AttributeError, TypeError, ValueError):
                product = _product({**row, "properties": {}})
                property_error = ["invalid_or_contradictory_property_evidence"]
            else:
                property_error = []
            reasons = _individual_reasons(row, product, allowed_sources)
            reasons.extend(property_error)
            if len(unique) != 1:
                reasons.append("duplicate_source_identity_conflict")
            if len(candidate_ids[_text(row, "new_candidate_id")]) > 1:
                reasons.append("new_candidate_id_collision")
            # Missing evidence still travels with its related cohort so a partial
            # group cannot silently ignore an ambiguous third supplier.
            model = normalize_model(product.model, product.brand)
            retrieval_model = model or normalize_model(extract_model(product.name))
            cohorts[_model_family(retrieval_model) or json.dumps(key)].append((row, product, reasons))

    for _, members in sorted(cohorts.items()):
        reasons = [reason for _, _, errors in members for reason in errors]
        products = [product for _, product, _ in members]
        if len({normalize_model(product.model, product.brand) for product in products}) != 1:
            reasons.append("full_model_or_execution_mismatch")
        if len({product.brand for product in products}) != 1:
            reasons.append("brand_mismatch_or_missing")
        if len({product.product_kind for product in products}) != 1:
            reasons.append("product_context_mismatch")
        if len({product.supplier_code for product in products}) != len(members):
            reasons.append("multiple_products_from_one_supplier")
        pair_evidence = []
        for (left_row, left, _), (right_row, right, _) in combinations(members, 2):
            errors, evidence = _pair_evidence(left, right)
            reasons.extend(errors)
            pair_evidence.append({"sources": [list(_source_key(left_row)), list(_source_key(right_row))],
                                  **evidence})
        if reasons:
            for row, _, _ in members:
                review(row, reasons, pair_evidence)
        elif len(members) == 1:
            result["ungrouped"].append({**members[0][0], "new_group_id": None,
                                       "grouping_status": "SINGLE_SOURCE_NEW"})
        else:
            product = products[0]
            group_id = _group_id(product)
            result["groups"].append({
                "new_group_id": group_id, "brand": product.brand,
                "model_key": normalize_model(product.model, product.brand),
                "product_kind": product.product_kind, "model_role": OWN_MODEL,
                "sterbrust_product_id": None, "status": "PROVEN_EQUIVALENT_NEW_PROPOSAL",
                "rule_version": RULE_VERSION, "equivalence_evidence": pair_evidence,
                "source_records": [{**row, "new_group_id": group_id} for row, _, _ in members],
            })
    result["groups"].sort(key=lambda group: group["new_group_id"])
    result["review"].sort(key=lambda row: (*_source_key(row), _text(row, "new_candidate_id")))
    return result


def singleton_proposals(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Technical groups for already validated single-source cohorts.

    This does not assert cross-source equivalence or full discovery coverage.
    Only the strict grouping evaluator's eligible singleton output is accepted;
    callers must still prove canonical absence, section and completeness.
    The ID is membership-independent if a proven second source appears later.
    """
    groups = []
    for row in result.get("ungrouped", []):
        product = _product(row)
        if row.get("grouping_status") != "SINGLE_SOURCE_NEW" or _individual_reasons(row, product):
            raise ValueError("Singleton requires strict, independent identity evidence")
        group_id = _group_id(product)
        groups.append({"new_group_id": group_id, "status": "SINGLE_SOURCE_NEW_PROPOSAL",
                       "rule_version": RULE_VERSION, "sterbrust_product_id": None,
                       "equivalence_evidence": [], "cross_source_equivalence_claimed": False,
                       "source_records": [{**row, "new_group_id": group_id}]})
    return sorted(groups, key=lambda g: g["new_group_id"])
