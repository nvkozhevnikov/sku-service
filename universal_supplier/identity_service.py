"""Canonical Stage 6B identity classifier and PostgreSQL runtime materializer.

The report generator and post-crawl runtime deliberately share this module.
It contains no supplier HTTP or Sterbrust write path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    brand_evidenced_by_name, model_identity_evidence, normalize_brand, normalize_model,
)
from sterbrust_matching.product_identity import (
    ACCESSORY_FAMILIES,
    REFERENCE_MODEL,
    TAXON_BY_KIND,
    canonical_properties,
    classify_model_role,
    classify_product_kind,
    profile_for_kind,
    safe_new_product_decision,
)

from .matching_service import RegistryContext, load_registry_from_postgres, match_cards
from .models import CategoryNode, ProductCard, PropertyValue
from .postgres import PostgresConfig, PostgresRepository


IDENTITY_RULE_VERSION = "stage6b-identity-1.1"
IDENTITY_DECISIONS = (
    "EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING", "REVIEW_EXISTING", "CONFLICT",
    "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY_OR_COMPATIBILITY_ITEM", "INSUFFICIENT_IDENTITY",
)


def category_path(card: dict) -> str:
    return " / ".join(str(row.get("name") or "") for row in card.get("categories", []) if row.get("name"))


def source_properties(card: dict) -> dict[str, str]:
    result: dict[str, str] = {}
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
    value = row.get("properties") or []
    items = value.get("items", []) if isinstance(value, dict) else value
    result: dict[str, str] = {}
    for item in items:
        field = item.get("value_flat") if "value_flat" in item else item.get("value")
        unit = str(item.get("unit") or "").strip()
        name = item.get("property_name") or item.get("name")
        if field not in (None, "") and name:
            rendered = str(field)
            if unit and unit.lower() not in rendered.lower():
                rendered = f"{rendered} {unit}"
            result[str(name)] = rendered
    return result


def source_product(card: dict) -> Product:
    path = category_path(card)
    role = classify_model_role(card.get("name", ""), path)
    return Product(
        key=str(card.get("external_id") or ""), name=str(card.get("name") or ""),
        brand=str(card.get("brand") or "") or brand_evidenced_by_name(card.get("name", "")),
        model=role.own_model,
        supplier_article=str(card.get("sku") or ""), category=path,
        properties=source_properties(card), supplier_code=str(card.get("supplier_code") or ""),
    )


def registry_product(row: dict, supplier_code: str = "", supplier_sku: str = "") -> Product:
    identifiers = {supplier_code: (supplier_sku,)} if supplier_code and supplier_sku else {}
    return Product(
        key=str(row.get("sterbrust_product_id") or ""), name=str(row.get("name") or ""),
        brand=str(row.get("brand_raw") or row.get("brand") or ""),
        model=str(row.get("model_raw") or row.get("model") or ""),
        manufacturer_article=str(row.get("article_raw") or row.get("article") or ""),
        category=str(row.get("category_path") or row.get("category") or ""),
        properties=registry_properties(row), supplier_identifiers=identifiers,
    )


def evidence_candidate_ids(row: dict) -> list[str]:
    result: list[str] = []
    if row.get("sterbrust_product_id"):
        result.append(str(row["sterbrust_product_id"]))
    evidence = row.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {}
    for key in ("candidate_keys",):
        value = evidence.get(key, []) if isinstance(evidence, dict) else []
        if isinstance(value, list):
            result.extend(str(item) for item in value)
    return list(dict.fromkeys(item for item in result if item))


def registry_indexes(registry: dict[str, dict]) -> tuple[dict[str, list[str]], dict[tuple[str, str], list[str]]]:
    by_model: dict[str, list[str]] = defaultdict(list)
    by_brand_model: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, row in registry.items():
        model = normalize_model(row.get("model_raw") or row.get("model"), row.get("brand_raw") or row.get("brand"))
        brand = normalize_brand(row.get("brand_raw") or row.get("brand"))
        if model:
            by_model[model].append(str(key))
            by_brand_model[(brand, model)].append(str(key))
    return by_model, by_brand_model


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


def classify_identity_card(card: dict, match_row: dict, registry: dict[str, dict],
                           by_model: dict[str, list[str]] | None = None,
                           by_brand_model: dict[tuple[str, str], list[str]] | None = None) -> dict:
    """Return the one canonical seven-class identity decision used by reports and runtime."""
    if by_model is None or by_brand_model is None:
        by_model, by_brand_model = registry_indexes(registry)
    source = source_product(card)
    kind_evidence = classify_product_kind(source.name, source.category)
    role = classify_model_role(source.name, source.category, source.model)
    explicit_ids = evidence_candidate_ids(match_row)
    branded_ids, normalized_ids = model_candidates(source, by_model, by_brand_model)
    candidate_ids = list(dict.fromkeys(explicit_ids + branded_ids + normalized_ids))[:25]
    candidate_rows = [registry[key] for key in candidate_ids if key in registry]
    conflicts: dict[str, Any] = {}
    best_id = str(match_row.get("sterbrust_product_id") or "")
    best_row = registry.get(best_id)
    revalidation = None
    if best_row:
        origin = str(match_row.get("match_method") or "")
        if origin == "EXISTING_LINK":
            evidence = match_row.get("evidence") or {}
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except json.JSONDecodeError:
                    evidence = {}
            origin = str(evidence.get("origin_match_method") or origin)
        candidate = registry_product(best_row, source.supplier_code, source.supplier_article if "SUPPLIER_ARTICLE" in origin else "")
        revalidation = match_product(source, [], existing_link=candidate, existing_link_origin=origin)
        conflicts = revalidation.conflicts

    family = TAXON_BY_KIND.get(kind_evidence.product_kind)
    is_accessory = role.role == REFERENCE_MODEL or bool(family and family.family in ACCESSORY_FAMILIES)
    manual_link = str(match_row.get("match_method") or "") == "EXISTING_LINK_MANUAL"
    if manual_link and best_row:
        decision = "EXACT_EXISTING"
    elif match_row.get("auto_accepted") in {True, "TRUE"} and revalidation and revalidation.auto_accepted:
        decision = "HIGH_CONFIDENCE_EXISTING" if "BRAND_MODEL" in str(match_row.get("match_method")) else "EXACT_EXISTING"
    elif is_accessory:
        decision = "ACCESSORY_OR_COMPATIBILITY_ITEM"
    elif match_row.get("status") == "CONFLICT" or (revalidation and revalidation.status == "CONFLICT"):
        decision = "CONFLICT"
    elif candidate_rows:
        decision = "REVIEW_EXISTING"
    else:
        decision, _ = safe_new_product_decision(
            existing_confirmed=False, exact_article_match=False,
            compatible_brand_model_candidates=branded_ids,
            normalized_model_candidates=normalized_ids, semantic_candidates=[],
            model_role=role.role, product_kind=kind_evidence.product_kind,
        )

    properties = source_properties(card)
    canonical = canonical_properties(properties)
    profile = set(profile_for_kind(kind_evidence.product_kind))
    critical = {key: canonical[key] for key in sorted(canonical) if key in profile}
    execution = model_identity_evidence(source.name)
    identity_characteristics = {
        "category": source.category,
        "identity_critical_properties": critical,
        "execution": {
            "base_model": execution.get("base_model") or "",
            "execution_qualifier": execution.get("execution_qualifier") or "",
        },
        "candidate_sterbrust_ids": candidate_ids,
        "candidate_evidence": [
            {"sterbrust_product_id": candidate_id,
             "source": "matcher" if candidate_id in explicit_ids else ("brand_model" if candidate_id in branded_ids else "model")}
            for candidate_id in candidate_ids
        ],
    }
    return {
        "supplier_external_id": source.key, "SKU": source.supplier_article,
        "source_name": source.name, "brand": source.brand, "own_model": role.own_model,
        "reference_model": role.reference_model, "model_role": role.role,
        "reference_parent_kind": role.reference_parent_kind,
        "product_kind": kind_evidence.product_kind, "category": source.category,
        "candidate_sterbrust_ids": "|".join(candidate_ids),
        "best_candidate": best_id or (candidate_ids[0] if candidate_ids else ""),
        "match_method": match_row.get("match_method", ""),
        "identity_characteristics": json.dumps(identity_characteristics, ensure_ascii=False, sort_keys=True),
        "conflicts": json.dumps(conflicts, ensure_ascii=False, sort_keys=True),
        "decision": decision,
        "decision_reason": decision_reason(decision, source, candidate_rows, conflicts),
        "auto_accepted": "TRUE" if decision in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"} else "FALSE",
    }


def _registry_rows(context: RegistryContext) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for key, row in context.products.items():
        result[key] = {
            "sterbrust_product_id": key, "name": row["name"], "brand_raw": row["brand"],
            "model_raw": row["model"], "article_raw": row["article"],
            "category_path": row["category"],
            "properties": [{"property_name": name, "value_flat": value} for name, value in row.get("properties", {}).items()],
        }
    return result


def _load_cards(connection, supplier_code: str | None) -> tuple[list[ProductCard], dict[tuple[str, str], int]]:
    params: tuple[Any, ...] = ()
    where = "sp.active"
    if supplier_code:
        where += " AND s.code=%s"
        params = (supplier_code,)
    with connection.cursor() as q:
        q.execute(f"""SELECT sp.id,s.code,sp.external_id,coalesce(sp.sku,''),sp.name,sp.source_url,
                             coalesce(sp.canonical_url,sp.source_url),coalesce(sp.brand_raw,''),
                             coalesce(sp.manufacturer_raw,''),coalesce(sp.description_text,''),
                             coalesce(sp.description_html,''),sp.raw_data,coalesce(sp.last_http_status,200),
                             o.price,o.old_price,coalesce(o.currency,''),coalesce(o.availability_raw,''),
                             coalesce(o.availability_normalized,'unknown'),o.quantity
                      FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                      LEFT JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
                      WHERE {where} ORDER BY s.code,sp.id""", params)
        rows = q.fetchall()
        ids = [row[0] for row in rows]
        properties: dict[int, list[PropertyValue]] = defaultdict(list)
        categories: dict[int, list[CategoryNode]] = defaultdict(list)
        if ids:
            q.execute("""SELECT source_product_id,raw_name,coalesce(raw_value,''),coalesce(raw_unit,''),position
                         FROM product_properties WHERE source_product_id=ANY(%s) AND offer_id IS NULL ORDER BY source_product_id,position""", (ids,))
            for source_id, name, value, unit, position in q.fetchall():
                properties[source_id].append(PropertyValue(name, value, unit, position))
            q.execute("""SELECT p.source_product_id,n.raw_name,coalesce(n.raw_url,''),n.position
                         FROM product_category_paths p JOIN product_category_path_nodes n ON n.path_id=p.id AND n.supplier_id=p.supplier_id
                         WHERE p.source_product_id=ANY(%s) AND p.is_current ORDER BY p.source_product_id,n.position""", (ids,))
            for source_id, name, url, position in q.fetchall():
                categories[source_id].append(CategoryNode(name, url, position))
    cards: list[ProductCard] = []
    source_ids: dict[tuple[str, str], int] = {}
    for row in rows:
        (source_id, code, external_id, sku, name, source_url, canonical_url, brand, manufacturer,
         description_text, description_html, raw_data, http_status, price, old_price, currency,
         availability_raw, availability, quantity) = row
        external_id = str(external_id or source_id)
        source_ids[(str(code), external_id)] = int(source_id)
        cards.append(ProductCard(
            supplier_code=str(code), external_id=external_id, sku=sku, name=name,
            requested_url=source_url, final_url=source_url, canonical_url=canonical_url,
            http_status=http_status, redirect_urls=(), price=price, old_price=old_price,
            currency=currency, availability_raw=availability_raw,
            availability_normalized=availability, quantity=quantity,
            description_text=description_text, description_html=description_html,
            brand=brand, manufacturer=manufacturer,
            properties=tuple(properties[source_id]), categories=tuple(categories[source_id]),
            raw_data=raw_data or {},
        ))
    return cards, source_ids


class RuntimeIdentityService:
    """Idempotent post-ingest matching, identity materialization and review sync."""

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    def reconcile(self, supplier_code: str | None = None) -> dict[str, Any]:
        import psycopg
        from .control_plane.admin_store import PostgresAdminStore

        repository = PostgresRepository(self.config)
        try:
            registry_context = load_registry_from_postgres(repository.connection)
            registry = _registry_rows(registry_context)
            by_model, by_brand_model = registry_indexes(registry)
            cards, source_ids = _load_cards(repository.connection, supplier_code)
            audits: list[tuple[int, dict]] = []
            match_counts: Counter[str] = Counter()
            persistence: Counter[str] = Counter()
            grouped: dict[str, list[ProductCard]] = defaultdict(list)
            for card in cards:
                grouped[card.supplier_code].append(card)
            validated_at = datetime.now(timezone.utc).isoformat()
            for code, supplier_cards in grouped.items():
                existing = repository.load_existing_link_contexts(code)
                matches = match_cards(supplier_cards, registry_context, existing)
                for card, match in zip(supplier_cards, matches, strict=True):
                    persistence[repository.persist_match(match, validated_at, code)] += 1
                    audit = classify_identity_card(card.as_jsonable(), match, registry, by_model, by_brand_model)
                    audits.append((source_ids[(code, card.external_id)], audit))
                    match_counts[audit["decision"]] += 1

            with psycopg.connect(**self.config.kwargs(), autocommit=True) as connection, connection.transaction(), connection.cursor() as q:
                for source_id, audit in audits:
                    best = audit["best_candidate"] or None
                    if best and best not in registry:
                        best = None
                    q.execute("""INSERT INTO product_identity_decisions
                                 (source_product_id,decision,own_model,reference_model,product_kind,best_sterbrust_id,
                                  decision_reason,conflicts,identity_characteristics,rule_version,audited_at)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now())
                                 ON CONFLICT(source_product_id) DO UPDATE SET
                                   decision=excluded.decision,own_model=excluded.own_model,
                                   reference_model=excluded.reference_model,product_kind=excluded.product_kind,
                                   best_sterbrust_id=excluded.best_sterbrust_id,
                                   decision_reason=excluded.decision_reason,conflicts=excluded.conflicts,
                                   identity_characteristics=excluded.identity_characteristics,
                                   rule_version=excluded.rule_version,audited_at=excluded.audited_at""",
                              (source_id, audit["decision"], audit["own_model"] or None,
                               audit["reference_model"] or None, audit["product_kind"], best,
                               audit["decision_reason"], audit["conflicts"], audit["identity_characteristics"],
                               IDENTITY_RULE_VERSION))
            review = PostgresAdminStore(self.config).generate_review_cases(supplier_code)
            from pathlib import Path
            from .offer_selection import OfferSelectionPolicy, PostgresOfferSelectionStore
            selection_policy = OfferSelectionPolicy.load(
                Path(__file__).resolve().parents[1] / "config" / "offer_selection.json"
            )
            selection = PostgresOfferSelectionStore(repository.connection).apply(
                PostgresOfferSelectionStore(repository.connection).evaluate_all(selection_policy)
            )
            return {
                "source_products": len(cards), "identity_decisions": dict(match_counts),
                "match_persistence": dict(persistence), "review_sync": review,
                "offer_selection": selection,
            }
        finally:
            repository.close()


def reconcile_runtime_identity(config: PostgresConfig, supplier_code: str | None = None) -> dict[str, Any]:
    return RuntimeIdentityService(config).reconcile(supplier_code)
