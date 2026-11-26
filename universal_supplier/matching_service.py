from __future__ import annotations

import json
import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    article_lookup_key, brand_evidenced_by_name, model_identity_evidence, normalize_brand, normalize_model,
    normalized_name, model_tokens, normalize_model_strict,
)
from sterbrust_matching.product_identity import identity_properties, own_model_for_identity, classify_model_role, OWN_MODEL, property_values

from .models import ProductCard
from .matching_input import matching_product


@dataclass
class RegistryContext:
    products: dict[str, dict]
    identifier_index: dict[tuple[str, str], list[str]]
    by_model: dict[str, list[str]]
    by_brand: dict[str, list[str]]
    title_identifier_index: dict[tuple[str, str], list[str]]
    manufacturer_identifier_index: dict[str, list[str]] = field(default_factory=dict)
    by_title_model: dict[str, list[str]] = field(default_factory=dict)


@lru_cache(maxsize=100000)
def _visible_own_model(name: str, category: str) -> str:
    if classify_model_role(name, category).role != OWN_MODEL:
        return ''
    model = own_model_for_identity(name, category)
    return '' if nonidentifying_model(model) else model


def nonidentifying_model(model: str) -> bool:
    """A roller/axis/spindle count describes equipment, not its execution ID."""
    return bool(re.fullmatch(r'\d+\s*[xх×-]?\s*-?\s*(?:валков|осев|шпиндел|сторон|позицион|координат)\w*',
                             str(model or '').strip(), re.I))


def index_visible_models(registry: RegistryContext) -> RegistryContext:
    """Discovery over actual catalog titles; never rewrite stored model facts."""
    index: dict[str, list[str]] = defaultdict(list)
    for key, row in registry.products.items():
        model = _visible_own_model(row['name'], row['category'])
        normalized = normalize_model(model, row['brand'])
        if normalized:
            index[normalized].append(key)
    registry.by_title_model = dict(index)
    return registry


def _catalog_model(row: dict) -> str:
    if row['model']:
        return row['model']
    visible = _visible_own_model(row['name'], row['category'])
    # An explicit contradictory property must not be hidden by the title.
    for name, values in row.get('properties', {}).items():
        if str(name).strip().casefold() in {'model', 'модель'}:
            if any(normalize_model_strict(value, row['brand']) != normalize_model_strict(visible, row['brand'])
                   for value in property_values(values)):
                return ''
    return visible


def load_registry_from_csv(path: str | Path) -> RegistryContext:
    """Use a fresh GET-only registry without changing the supplier database.

    Title model tokens are candidate-discovery evidence only; matching still
    uses the existing semantic/identity gates before accepting a link.
    """
    products: dict[str, dict] = {}
    identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_model: dict[str, list[str]] = defaultdict(list)
    by_brand: dict[str, list[str]] = defaultdict(list)
    title_identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            key = item["sterbrust_product_id"]
            if key in products:
                raise ValueError("duplicate Sterbrust product ID in registry CSV")
            if item.get("active") == "N":
                continue
            name = item.get("name") or ""
            brand = item.get("brand_raw") or ""
            model = item.get("model_raw") or ""
            article = item.get("article_raw") or ""
            products[key] = {
                "sterbrust_product_id": key, "name": name, "brand": brand,
                "model": model, "article": article,
                "category": item.get("category_path") or "",
                "properties": {}, "supplier_identifiers": defaultdict(list),
            }
            model_keys = {normalize_model(model, brand)} if model else set()
            if normalize_brand(brand) == "beka-mak":
                model_keys.update(model_tokens(name))
            for model_key in model_keys - {""}:
                by_model[model_key].append(key)
            if brand:
                by_brand[normalize_brand(brand)].append(key)
            for inferred_article in _optimum_title_articles(name):
                title_identifier_index[("optimum", inferred_article)].append(key)
    manufacturer_index: dict[str, list[str]] = defaultdict(list)
    for key, row in products.items():
        if article_lookup_key(row['article']):
            manufacturer_index[article_lookup_key(row['article'])].append(key)
    return index_visible_models(RegistryContext(products, identifier_index, by_model, by_brand, title_identifier_index, manufacturer_index))




def _optimum_title_articles(name: str) -> tuple[str, ...]:
    """Extract explicit Optimum article tokens printed in Sterbrust titles.

    Sterbrust historically stores many Optimum supplier SKUs directly in the
    product name (for example ``Optimum 3034252 OPTIdrill DH35V``) while the
    dedicated ARTIKUL_OPTIMUM property is not populated.  These tokens are
    candidate-discovery evidence only; they are not persisted back into the
    Sterbrust registry and do not bypass identity conflict checks.
    """
    if not re.search(r"(?i)\boptimum\b|\bopti(?:turn|drill|mill|saw|grind|sand|press|polish)\b", name or ""):
        return ()
    values: list[str] = []
    for raw in re.findall(r"(?<!\d)(\d{5,10})(?!\d)", name or ""):
        normalized = article_lookup_key(raw)
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)

def load_registry_from_postgres(connection, *, catalog_product_id=None) -> RegistryContext:
    products: dict[str, dict] = {}
    identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_model: dict[str, list[str]] = defaultdict(list)
    by_brand: dict[str, list[str]] = defaultdict(list)
    title_identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT sterbrust_product_id, name, coalesce(brand_raw,''),
                      coalesce(model_raw,''), coalesce(article_raw,''),
                      coalesce(category_path,''), properties
               FROM sterbrust_products WHERE active IS DISTINCT FROM false
               AND (%s::bigint IS NULL OR catalog_product_id=%s)
               ORDER BY sterbrust_product_id""", (catalog_product_id, catalog_product_id)
        )
        for product_id, name, brand, model, article, category, properties in cursor.fetchall():
            key = str(product_id)
            prop_items = properties.get("items", []) if isinstance(properties, dict) else []
            product_properties = identity_properties(
                (item.get("property_name"), item.get("value_flat"), item.get("unit"))
                for item in prop_items
            )
            products[key] = {"sterbrust_product_id": key, "name": name, "brand": brand,
                             "model": model, "article": article, "category": category,
                             "properties": product_properties, "supplier_identifiers": defaultdict(list)}
            normalized_model = normalize_model(model, brand)
            normalized_brand = normalize_brand(brand)
            if normalized_model:
                by_model[normalized_model].append(key)
            if normalized_brand:
                by_brand[normalized_brand].append(key)
            for inferred_article in _optimum_title_articles(name):
                title_identifier_index[("optimum", inferred_article)].append(key)
        cursor.execute(
            """SELECT sterbrust_product_id, supplier_code, raw_value, normalized_value
               FROM sterbrust_product_supplier_identifiers WHERE active
               ORDER BY sterbrust_product_id, supplier_code, raw_value"""
        )
        for product_id, supplier_code, raw_value, normalized_value in cursor.fetchall():
            key = str(product_id)
            if key not in products:
                continue
            products[key]["supplier_identifiers"][supplier_code].append(raw_value)
            # Rebuild the retrieval key from raw_value with the same function
            # lookup uses. Whether a retrieved article really is the same one
            # (10-100 != 101-00) is decided by articles_compatible in the matcher.
            article_key = article_lookup_key(raw_value)
            if article_key:
                identifier_index[(supplier_code, article_key)].append(key)
    manufacturer_index: dict[str, list[str]] = defaultdict(list)
    for key, row in products.items():
        if article_lookup_key(row['article']):
            manufacturer_index[article_lookup_key(row['article'])].append(key)
    return index_visible_models(RegistryContext(products, identifier_index, by_model, by_brand, title_identifier_index, manufacturer_index))


def _id_order(key: object) -> tuple[int, int, str]:
    text = str(key or "")
    return (0, int(text), text) if text.isdigit() else (1, 0, text)


def _candidate(row: dict) -> Product:
    return Product(key=row["sterbrust_product_id"], name=row["name"], brand=row["brand"],
                   model=_catalog_model(row), manufacturer_article=row["article"],
                   category=row["category"], properties=row.get("properties", {}),
                   supplier_identifiers={key: tuple(values) for key, values in row["supplier_identifiers"].items()})


def _source(card: ProductCard) -> Product:
    # Same builder as identity_service.source_product (matching_input).
    return matching_product(card.as_jsonable())


def _candidates(source: Product, registry: RegistryContext) -> list[dict]:
    article = article_lookup_key(source.supplier_article)
    ids = set(registry.identifier_index.get((source.supplier_code, article), [])) if article else set()
    if article:
        # Safe fallback for legacy Sterbrust cards where the supplier-specific
        # article is printed in the product title but the dedicated property is
        # empty.  Candidate discovery only: match_product still enforces
        # product-kind/model/property conflicts and does not treat this as a
        # persisted supplier identifier.
        ids.update(registry.title_identifier_index.get((source.supplier_code, article), []))
    if source.manufacturer_article:
        # Candidate discovery only. The existing matcher checks article syntax,
        # brand, model, execution and properties before any AUTO acceptance.
        manufacturer_article = article_lookup_key(source.manufacturer_article)
        ids.update(registry.manufacturer_identifier_index.get(manufacturer_article, []))
    model = normalize_model(source.model, source.brand)
    if model:
        ids.update(registry.by_model.get(model, []))
        ids.update(registry.by_title_model.get(model, []))
    if not ids:
        base_model = str(model_identity_evidence(source.name).get("base_model") or "")
        if base_model:
            ids.update(registry.by_model.get(base_model, []))
            ids.update(registry.by_title_model.get(base_model, []))
    ids = sorted(ids, key=_id_order)
    if not ids and source.brand:
        scored = []
        for product_id in registry.by_brand.get(normalize_brand(source.brand), []):
            row = registry.products[product_id]
            score = SequenceMatcher(None, normalized_name(source.name), normalized_name(row["name"])).ratio()
            if score >= 0.60:
                scored.append((score, product_id))
        ids = [product_id for _, product_id in sorted(scored, key=lambda item: (-item[0], _id_order(item[1])))[:10]]
    return [registry.products[product_id] for product_id in ids]


def _manual_target_unavailable_row(card: ProductCard, source: Product, manual_key: str) -> dict:
    evidence = {"existing_link": True, "mapping_provenance": "MANUAL_OR_HUMAN_CONFIRMED",
                "manual_mapping_guard": {"manual_sterbrust_product_id": manual_key,
                                         "reason": "MANUAL_TARGET_NOT_IN_ACTIVE_REGISTRY"}}
    return {
        "source_external_id": card.external_id, "SKU": card.sku, "name": card.name,
        "brand": card.brand, "model": source.model, "status": "REVIEW",
        "match_method": "REVIEW_MANUAL_TARGET_CHANGE", "confidence_score": 0.0,
        "sterbrust_product_id": manual_key, "conflict_class": "MANUAL_TARGET_UNAVAILABLE",
        "warnings": json.dumps({"manual_target_change": "MANUAL_TARGET_NOT_IN_ACTIVE_REGISTRY"}, ensure_ascii=False, sort_keys=True),
        "data_quality_flags": "[]",
        "conflicts": json.dumps({"manual_mapping": "MANUAL_TARGET_NOT_IN_ACTIVE_REGISTRY"}, ensure_ascii=False, sort_keys=True),
        "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        "auto_accepted": "FALSE",
    }


def match_cards(cards: list[ProductCard] | tuple[ProductCard, ...], registry: RegistryContext,
                existing_links: dict[str, str | dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    for card in cards:
        source = _source(card)
        existing_value = (existing_links or {}).get(card.external_id)
        existing_key = (existing_value.get("sterbrust_product_id") if isinstance(existing_value, dict)
                        else existing_value)
        existing = _candidate(registry.products[existing_key]) if existing_key in registry.products else None
        origin = str(existing_value.get("origin_match_method") or "") if isinstance(existing_value, dict) else ""
        manual = bool(existing_value.get("manual_or_human_confirmed")) if isinstance(existing_value, dict) else False
        if manual and existing_key and existing is None:
            # The human-confirmed target left the active registry. Never rematch
            # automatically (that could auto-accept a different product): keep
            # the manual target as evidence and send the card to an operator.
            rows.append(_manual_target_unavailable_row(card, source, str(existing_key)))
            continue
        candidates = [_candidate(row) for row in _candidates(source, registry)]
        result = match_product(source, candidates,
                               existing_link=existing, existing_link_origin=origin,
                               existing_link_manual=manual)
        if (isinstance(existing_value, dict)
                and bool(existing_value.get("current_auto_accepted"))
                and not result.auto_accepted
                and result.status in {"CONFLICT", "REVIEW", "NEW_CANDIDATE"}):
            # Persist the stable post-quarantine decision in the same reconcile.
            # Otherwise clearing source_products.catalog_product_id makes the next
            # identical run rematch without existing_link and append a technical
            # second event with a different method/fingerprint.
            result = match_product(source, candidates)
        flags = result.evidence.get("data_quality_flags", [])
        evidence = dict(result.evidence)
        chosen = registry.products.get(str(result.candidate_key))
        if chosen and not chosen['model'] and _catalog_model(chosen):
            evidence['catalog_model_projection'] = {
                'origin': 'VISIBLE_OWN_TITLE', 'raw_model': '',
                'title': chosen['name'], 'projected_model': _catalog_model(chosen),
                'stored_fact_modified': False,
            }
        rows.append({
            "source_external_id": card.external_id, "SKU": card.sku, "name": card.name,
            "brand": card.brand, "model": source.model, "status": result.status,
            "match_method": result.method, "confidence_score": result.confidence,
            "sterbrust_product_id": result.candidate_key or "",
            "conflict_class": result.evidence.get("conflict_class", ""),
            "warnings": json.dumps(result.warnings, ensure_ascii=False, sort_keys=True),
            "data_quality_flags": json.dumps(flags, ensure_ascii=False, sort_keys=True),
            "conflicts": json.dumps(result.conflicts, ensure_ascii=False, sort_keys=True),
            "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            "auto_accepted": str(result.auto_accepted).upper(),
        })
    return rows
