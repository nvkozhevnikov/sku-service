from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any, Iterable

from .normalization import (
    model_identity_evidence, model_tokens, normalize_brand, normalize_model,
    normalize_model_with_flags, normalized_name,
)
from .property_normalization import (
    PROPERTY_DATA_QUALITY_WARNING,
    PROPERTY_IDENTITY_CONFLICT,
    compare_property,
)
from .product_identity import (
    OWN_MODEL,
    REFERENCE_MODEL,
    classify_model_role,
    classify_product_kind,
    compare_category_identity,
    kinds_equivalent,
)


STATUSES = {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH", "REVIEW", "NEW_CANDIDATE", "CONFLICT"}


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    brand: str = ""
    model: str = ""
    supplier_article: str = ""
    manufacturer_article: str = ""
    category: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    catalog_product_id: str | None = None
    supplier_code: str = ""
    supplier_identifiers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    product_kind: str = ""


@dataclass(frozen=True)
class MatchResult:
    status: str
    method: str
    confidence: float
    candidate_key: str | None
    evidence: dict[str, Any]
    conflicts: dict[str, Any]
    auto_accepted: bool
    warnings: dict[str, Any] = field(default_factory=dict)


def _property_identity_quality(source: Product, candidate: Product) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Compare shared properties once, preserving raw and normalized evidence."""
    conflicts: dict[str, Any] = {}
    warnings: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    for key in sorted(set(source.properties) & set(candidate.properties)):
        left, right = source.properties[key], candidate.properties[key]
        if left in (None, "") or right in (None, ""):
            continue
        comparison = compare_property(left, right, key)
        evidence[key] = comparison.evidence()
        if comparison.classification == PROPERTY_IDENTITY_CONFLICT:
            conflicts[f"property:{key}"] = comparison.evidence()
        elif comparison.classification == PROPERTY_DATA_QUALITY_WARNING:
            warnings[f"PROPERTY_{key.upper()}_DATA_QUALITY_WARNING"] = comparison.evidence()
    return conflicts, warnings, evidence


def _identity_conflicts(source: Product, candidate: Product) -> dict[str, Any]:
    conflicts: dict[str, Any] = {}
    sb, cb = normalize_brand(source.brand), normalize_brand(candidate.brand)
    sm, cm = normalize_model(source.model, source.brand), normalize_model(candidate.model, candidate.brand)
    if sb and cb and sb != cb:
        conflicts["brand"] = {"source": sb, "candidate": cb}
    if sm and cm and sm != cm:
        conflicts["model"] = {"source": sm, "candidate": cm}
    sa, ca = normalize_model(source.manufacturer_article), normalize_model(candidate.manufacturer_article)
    if sa and ca and sa != ca:
        conflicts["manufacturer_article"] = {"source": sa, "candidate": ca}
    source_kind = classify_product_kind(source.name, source.category)
    candidate_kind = classify_product_kind(candidate.name, candidate.category)
    effective_source_kind = source.product_kind or source_kind.product_kind
    effective_candidate_kind = candidate.product_kind or candidate_kind.product_kind
    if (effective_source_kind != "unknown" and effective_candidate_kind != "unknown"
            and not kinds_equivalent(effective_source_kind, effective_candidate_kind)):
        conflicts["product_kind"] = {
            "source": effective_source_kind, "candidate": effective_candidate_kind,
            "rule": "PRODUCT_KIND_MISMATCH",
        }
    source_role = classify_model_role(source.name, source.category, source.model)
    candidate_role = classify_model_role(candidate.name, candidate.category, candidate.model)
    if (source_role.role == REFERENCE_MODEL and candidate_role.role == OWN_MODEL
            and source_role.reference_model
            and source_role.reference_model == candidate_role.own_model):
        conflicts["model_role"] = {
            "source": source_role.role, "candidate": candidate_role.role,
            "reference_model": source_role.reference_model,
            "rule": "REFERENCE_MODEL_CANNOT_MATCH_OWN_MODEL",
        }
    property_conflicts, _, _ = _property_identity_quality(source, candidate)
    conflicts.update(property_conflicts)
    category_conflicts, _ = compare_category_identity(
        effective_source_kind, source.properties, candidate.properties,
    )
    conflicts.update(category_conflicts)
    return conflicts


def _supplier_article_confirmed(source: Product, candidate: Product) -> bool:
    if not source.supplier_code or not source.supplier_article:
        return False
    source_article = normalize_model(source.supplier_article)
    return bool(source_article and source_article in {
        normalize_model(value)
        for value in candidate.supplier_identifiers.get(source.supplier_code, ()) if value
    })


def _supplier_article_identity_quality(source: Product, candidate: Product) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Classify model disagreement after exact unique supplier-article evidence."""
    source_model, source_flags = normalize_model_with_flags(source.model, source.brand)
    candidate_model, candidate_flags = normalize_model_with_flags(candidate.model, candidate.brand)
    candidate_name_models = model_tokens(candidate.name)
    source_name_models = model_tokens(source.name)
    source_article = normalize_model(source.supplier_article)
    general_article = normalize_model(candidate.manufacturer_article)
    warnings: dict[str, Any] = {}
    for flag in candidate_flags:
        warnings[flag] = {"raw_model": candidate.model, "brand": candidate.brand}
    for flag in source_flags:
        warnings[f"SOURCE_{flag}"] = {"raw_model": source.model, "brand": source.brand}

    evidence = {
        "source_model_normalized": source_model,
        "sterbrust_model_property_normalized": candidate_model,
        "sterbrust_name_model_tokens": list(candidate_name_models),
        "source_name_model_tokens": list(source_name_models),
        "general_article_normalized": general_article,
        "general_article_matches_supplier_sku": bool(source_article and general_article == source_article),
    }
    if not source_model or not candidate_model or source_model == candidate_model:
        conflict_class = "EXTRACTION_CONFLICT" if warnings else ""
        return conflict_class, evidence, warnings

    shared_title_models = sorted(set(source_name_models) & set(candidate_name_models))
    if shared_title_models:
        if candidate_model in {source_model + "new", source_model.removesuffix("new")} or source_model in {candidate_model + "new", candidate_model.removesuffix("new")}:
            warnings["SOURCE_MODEL_EXTRACTION_OMITTED_EXECUTION_TOKEN"] = {
                "source_model": source.model, "sterbrust_model_property": candidate.model,
                "shared_title_models": shared_title_models,
            }
            return "EXTRACTION_CONFLICT", evidence, warnings
        warnings["STERBRUST_MODEL_PROPERTY_STALE"] = {
            "source_model": source.model, "sterbrust_model_property": candidate.model,
            "confirming_name_tokens": shared_title_models,
        }
        return "FIELD_DATA_CONFLICT", evidence, warnings

    if source_model in candidate_name_models:
        if candidate_model in {source_model + "new", source_model.removesuffix("new")} or source_model in {candidate_model + "new", candidate_model.removesuffix("new")}:
            warnings["SOURCE_MODEL_EXTRACTION_OMITTED_EXECUTION_TOKEN"] = {
                "source_model": source.model, "sterbrust_model_property": candidate.model,
            }
            return "EXTRACTION_CONFLICT", evidence, warnings
        warnings["STERBRUST_MODEL_PROPERTY_STALE"] = {
            "source_model": source.model, "sterbrust_model_property": candidate.model,
            "confirming_name_tokens": list(candidate_name_models),
        }
        return "FIELD_DATA_CONFLICT", evidence, warnings

    if evidence["general_article_matches_supplier_sku"] and candidate_model in candidate_name_models:
        source_parts = re.findall(r"[a-zа-я]+|\d+", source_model)
        candidate_parts = re.findall(r"[a-zа-я]+|\d+", candidate_model)
        if sorted(source_parts) == sorted(candidate_parts):
            warnings["SOURCE_MODEL_TOKEN_ORDER_VARIATION"] = {
                "source_model": source.model, "sterbrust_model_property": candidate.model,
            }
            return "EXTRACTION_CONFLICT", evidence, warnings

    return "IDENTITY_CONFLICT", evidence, warnings


def classify_identity_quality(source: Product, candidate: Product) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """One evidence-quality classifier for initial match and link revalidation."""
    conflicts = _identity_conflicts(source, candidate)
    property_conflicts, property_warnings, property_evidence = _property_identity_quality(source, candidate)
    warnings = dict(property_warnings)
    supplier_confirmed = _supplier_article_confirmed(source, candidate)
    evidence: dict[str, Any] = {
        "supplier_specific_article_confirmed": supplier_confirmed,
        "property_comparisons": property_evidence,
    }
    source_kind = source.product_kind or classify_product_kind(source.name, source.category).product_kind
    candidate_kind = candidate.product_kind or classify_product_kind(candidate.name, candidate.category).product_kind
    source_role = classify_model_role(source.name, source.category, source.model)
    candidate_role = classify_model_role(candidate.name, candidate.category, candidate.model)
    category_conflicts, category_comparisons = compare_category_identity(
        source_kind, source.properties, candidate.properties,
    )
    evidence.update({
        "source_product_kind": source_kind,
        "candidate_product_kind": candidate_kind,
        "source_model_role": source_role.role,
        "candidate_model_role": candidate_role.role,
        "source_own_model": source_role.own_model,
        "source_reference_model": source_role.reference_model,
        "candidate_own_model": candidate_role.own_model,
        "category_identity_comparisons": category_comparisons,
    })
    sb, cb = normalize_brand(source.brand), normalize_brand(candidate.brand)
    sm, cm = normalize_model(source.model, source.brand), normalize_model(candidate.model, candidate.brand)
    hard_keys = {key for key in conflicts if key != "model"}
    conflict_class = "IDENTITY_CONFLICT" if hard_keys else ""

    if supplier_confirmed:
        model_class, model_evidence, model_warnings = _supplier_article_identity_quality(source, candidate)
        evidence.update(model_evidence)
        warnings.update(model_warnings)
        if model_class == "IDENTITY_CONFLICT":
            conflict_class = "IDENTITY_CONFLICT"
        elif not conflict_class and model_class:
            conflict_class = model_class
    elif sm and cm and sm != cm:
        conflict_class = "IDENTITY_CONFLICT"

    if sb and cb and sb != cb:
        conflict_class = "IDENTITY_CONFLICT"
    if property_conflicts:
        conflict_class = "IDENTITY_CONFLICT"
    if category_conflicts:
        conflict_class = "IDENTITY_CONFLICT"
    evidence["conflict_class"] = conflict_class
    if warnings:
        evidence["data_quality_flags"] = sorted(warnings)
    return conflict_class, evidence, conflicts, warnings


def _brand_model_semantic_quality(source: Product, candidate: Product) -> tuple[str, dict[str, Any]]:
    """Reject weak brand+model evidence when title semantics contradict identity."""
    source_identity = model_identity_evidence(source.name)
    candidate_identity = model_identity_evidence(candidate.name)
    similarity = SequenceMatcher(None, normalized_name(source.name), normalized_name(candidate.name)).ratio()
    reasons: list[str] = []
    source_role = classify_model_role(source.name, source.category, source.model)
    candidate_role = classify_model_role(candidate.name, candidate.category, candidate.model)
    source_kind = source.product_kind or classify_product_kind(source.name, source.category).product_kind
    candidate_kind = candidate.product_kind or classify_product_kind(candidate.name, candidate.category).product_kind
    if source_role.role == REFERENCE_MODEL:
        reasons.append("source_model_is_reference_or_compatibility_model")
    if source_kind != "unknown" and candidate_kind != "unknown" and not kinds_equivalent(source_kind, candidate_kind):
        reasons.append("product_kind_mismatch")
    if (source_role.role == REFERENCE_MODEL and candidate_role.role == OWN_MODEL
            and source_role.reference_model == candidate_role.own_model):
        reasons.append("reference_model_cannot_match_candidate_own_model")
    if (source_identity["base_model"] and source_identity["base_model"] == candidate_identity["base_model"]
            and source_identity["execution_qualifier"] != candidate_identity["execution_qualifier"]):
        reasons.append("execution_or_version_mismatch")
    if similarity < 0.45:
        reasons.append("name_compatibility_below_brand_model_threshold")
    return "IDENTITY_CONFLICT" if reasons else "", {
        "source_model_identity": source_identity,
        "candidate_model_identity": candidate_identity,
        "source_product_kind": source_kind,
        "candidate_product_kind": candidate_kind,
        "source_model_role": source_role.role,
        "candidate_model_role": candidate_role.role,
        "name_similarity": round(similarity, 4),
        "semantic_identity_contradictions": reasons,
    }


def _short_model_suffix_mismatch(source: Product, candidate: Product) -> bool:
    """True when model identity differs only by a compact alphabetic execution suffix."""
    source_model = normalize_model(source.model, source.brand)
    candidate_model = normalize_model(candidate.model, candidate.brand)
    if not source_model or not candidate_model or source_model == candidate_model:
        return False
    shorter, longer = sorted((source_model, candidate_model), key=len)
    suffix = longer[len(shorter):] if longer.startswith(shorter) else ""
    return bool(re.fullmatch(r"[a-z]{1,2}", suffix))


def match_product(source: Product, candidates: Iterable[Product], existing_link: Product | None = None,
                  *, existing_link_origin: str = "", existing_link_manual: bool = False) -> MatchResult:
    if existing_link is not None:
        if existing_link_manual:
            return MatchResult(
                "EXACT_MATCH", "EXISTING_LINK_MANUAL", 1.0, existing_link.key,
                {"existing_link": True, "existing_link_revalidated": False,
                 "mapping_provenance": "MANUAL_OR_HUMAN_CONFIRMED"}, {}, False,
            )
        conflict_class, evidence, conflicts, warnings = classify_identity_quality(source, existing_link)
        evidence.update({"existing_link": True, "existing_link_revalidated": True,
                         "mapping_provenance": "AUTOMATIC_ENGINE",
                         "origin_match_method": existing_link_origin})
        if existing_link_origin == "BRAND_MODEL":
            semantic_class, semantic_evidence = _brand_model_semantic_quality(source, existing_link)
            evidence.update(semantic_evidence)
            if semantic_class:
                conflict_class = semantic_class
                conflicts["semantic_identity"] = semantic_evidence["semantic_identity_contradictions"]
        if conflict_class == "IDENTITY_CONFLICT":
            return MatchResult("CONFLICT", "EXISTING_LINK_CONFLICT", 1.0, existing_link.key, evidence, conflicts, False, warnings)
        return MatchResult("EXACT_MATCH", "EXISTING_LINK", 1.0, existing_link.key, evidence, {}, True, warnings)

    candidate_list = list(candidates)

    # Supplier articles are namespaced by supplier. They are strong identity
    # evidence only when the supplier-to-property mapping is confirmed.
    if source.supplier_code and source.supplier_article:
        source_article = normalize_model(source.supplier_article)
        exact_supplier_rows = []
        for candidate in candidate_list:
            values = candidate.supplier_identifiers.get(source.supplier_code, ())
            if source_article and source_article in {normalize_model(value) for value in values if value}:
                exact_supplier_rows.append(candidate)
        if len(exact_supplier_rows) > 1:
            return MatchResult(
                "REVIEW", "DUPLICATE_SUPPLIER_ARTICLE", 1.0, None,
                {"supplier_code": source.supplier_code, "normalized_article": source_article,
                 "candidate_keys": [candidate.key for candidate in exact_supplier_rows],
                 "candidate_count": len(exact_supplier_rows)},
                {}, False,
            )
        if len(exact_supplier_rows) == 1:
            candidate = exact_supplier_rows[0]
            evidence = {"supplier_code": source.supplier_code, "normalized_article": source_article,
                        "candidate_count": 1}
            conflict_class, quality_evidence, conflicts, warnings = classify_identity_quality(source, candidate)
            evidence.update(quality_evidence)
            if conflict_class == "IDENTITY_CONFLICT":
                evidence["conflict_class"] = "IDENTITY_CONFLICT"
                return MatchResult("CONFLICT", "SUPPLIER_ARTICLE_IDENTITY_CONFLICT", 1.0,
                                   candidate.key, evidence, conflicts, False, warnings)
            if conflict_class:
                evidence["conflict_class"] = conflict_class
            if warnings:
                evidence["data_quality_flags"] = sorted(warnings)
            return MatchResult("EXACT_MATCH", "SUPPLIER_ARTICLE_EXACT", 1.0,
                               candidate.key, evidence, {}, True, warnings)

    scored = []
    for candidate in candidate_list:
        conflicts = _identity_conflicts(source, candidate)
        sb, cb = normalize_brand(source.brand), normalize_brand(candidate.brand)
        sm, cm = normalize_model(source.model, source.brand), normalize_model(candidate.model, candidate.brand)
        manufacturer_article_equal = bool(source.manufacturer_article and candidate.manufacturer_article and normalize_model(source.manufacturer_article) == normalize_model(candidate.manufacturer_article))
        brand_equal = bool(sb and cb and sb == cb)
        model_equal = bool(sm and cm and sm == cm)
        name_similarity = SequenceMatcher(None, normalized_name(source.name), normalized_name(candidate.name)).ratio()
        # A legacy scalar candidate.supplier_article has no proven supplier
        # namespace and therefore cannot contribute positive identity evidence.
        scored.append((candidate, conflicts, manufacturer_article_equal, brand_equal, model_equal, name_similarity))

    if not scored:
        return MatchResult("NEW_CANDIDATE", "NO_CANDIDATES", 0.0, None, {}, {}, False)

    conflict_rows = [
        row for row in scored
        if row[1] and (row[5] >= 0.75 or row[2] or (row[3] and row[4]))
    ]
    viable = [row for row in scored if not row[1]]
    strong = [row for row in viable if (row[2] and row[3]) or (row[3] and row[4])]
    if len(strong) == 1:
        row = strong[0]
        method = "MANUFACTURER_IDENTIFIER" if row[2] else "BRAND_MODEL"
        status = "EXACT_MATCH" if row[2] else "HIGH_CONFIDENCE_MATCH"
        confidence = 1.0 if row[2] else 0.94
        if method == "BRAND_MODEL":
            semantic_class, semantic_evidence = _brand_model_semantic_quality(source, row[0])
            if semantic_class:
                return MatchResult(
                    "REVIEW", "BRAND_MODEL_IDENTITY_REVIEW", round(row[5], 4), row[0].key,
                    {"brand_equal": row[3], "model_equal": row[4],
                     "manufacturer_article_equal": row[2], **semantic_evidence,
                     "conflict_class": semantic_class},
                    {"semantic_identity": semantic_evidence["semantic_identity_contradictions"]}, False,
                )
        return MatchResult(status, method, confidence, row[0].key,
                           {"brand_equal": row[3], "model_equal": row[4], "manufacturer_article_equal": row[2], "name_similarity": round(row[5], 4)}, {}, True)
    if len(strong) > 1:
        return MatchResult("REVIEW", "NON_UNIQUE_STRONG_CANDIDATES", 0.8, None, {"candidate_keys": [r[0].key for r in strong]}, {}, False)
    if conflict_rows:
        best = max(conflict_rows, key=lambda row: row[5])
        if best[3] and best[4] and set(best[1]).issubset({"product_kind", "model_role"}):
            semantic_class, semantic_evidence = _brand_model_semantic_quality(source, best[0])
            return MatchResult(
                "REVIEW", "BRAND_MODEL_IDENTITY_REVIEW", round(best[5], 4), best[0].key,
                {"brand_equal": True, "model_equal": True, **semantic_evidence,
                 "conflict_class": semantic_class or "IDENTITY_CONFLICT"},
                best[1], False,
            )
        if source.supplier_article and not best[2] and set(best[1]) == {"model"}:
            return MatchResult(
                "REVIEW", "BRAND_MODEL_IDENTITY_REVIEW", round(best[5], 4), best[0].key,
                {"name_similarity": round(best[5], 4), "conflict_class": "IDENTITY_CONFLICT",
                 "semantic_identity_contradictions": ["brand_candidate_model_conflicts_without_strong_identifier"]},
                best[1], False,
            )
        if set(best[1]) == {"model"} and _short_model_suffix_mismatch(source, best[0]):
            return MatchResult(
                "REVIEW", "BRAND_MODEL_IDENTITY_REVIEW", round(best[5], 4), best[0].key,
                {"name_similarity": round(best[5], 4),
                 "conflict_class": "IDENTITY_CONFLICT",
                 "semantic_identity_contradictions": ["significant_model_suffix_mismatch"]},
                best[1], False,
            )
        return MatchResult("CONFLICT", "IDENTITY_CONFLICT", round(best[5], 4), best[0].key, {"name_similarity": round(best[5], 4)}, best[1], False)
    best = max(scored, key=lambda row: row[5])
    if best[5] >= 0.72:
        return MatchResult("REVIEW", "NAME_CANDIDATE_ONLY", round(best[5], 4), best[0].key, {"name_similarity": round(best[5], 4)}, best[1], False)
    return MatchResult("NEW_CANDIDATE", "NO_IDENTITY_EVIDENCE", round(best[5], 4), best[0].key, {"best_name_similarity": round(best[5], 4)}, best[1], False)
