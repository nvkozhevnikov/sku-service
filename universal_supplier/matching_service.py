from __future__ import annotations

import json
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    brand_evidenced_by_name, model_identity_evidence, normalize_brand, normalize_model,
    normalized_name, model_tokens,
)
from sterbrust_matching.product_identity import classify_model_role

from .models import ProductCard


@dataclass
class RegistryContext:
    products: dict[str, dict]
    identifier_index: dict[tuple[str, str], list[str]]
    by_model: dict[str, list[str]]
    by_brand: dict[str, list[str]]
    title_identifier_index: dict[tuple[str, str], list[str]]


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
    return RegistryContext(products, identifier_index, by_model, by_brand, title_identifier_index)




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
        normalized = normalize_model(raw)
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)

def load_registry_from_postgres(connection) -> RegistryContext:
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
               FROM sterbrust_products WHERE active IS DISTINCT FROM false"""
        )
        for product_id, name, brand, model, article, category, properties in cursor.fetchall():
            key = str(product_id)
            prop_items = properties.get("items", []) if isinstance(properties, dict) else []
            product_properties = {
                str(item.get("property_name") or ""): item.get("value_flat", "")
                for item in prop_items if item.get("property_name") and item.get("value_flat") not in (None, "")
            }
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
               FROM sterbrust_product_supplier_identifiers WHERE active"""
        )
        for product_id, supplier_code, raw_value, normalized_value in cursor.fetchall():
            key = str(product_id)
            if key not in products:
                continue
            products[key]["supplier_identifiers"][supplier_code].append(raw_value)
            identifier_index[(supplier_code, normalized_value)].append(key)
    return RegistryContext(products, identifier_index, by_model, by_brand, title_identifier_index)


def _candidate(row: dict) -> Product:
    return Product(key=row["sterbrust_product_id"], name=row["name"], brand=row["brand"],
                   model=row["model"], manufacturer_article=row["article"],
                   category=row["category"], properties=row.get("properties", {}),
                   supplier_identifiers={key: tuple(values) for key, values in row["supplier_identifiers"].items()})


def _source(card: ProductCard) -> Product:
    category = " / ".join(node.name for node in card.categories if node.name)
    role = classify_model_role(card.name, category)
    # supplier_code is not a brand, and a compatibility model is not own_model.
    brand = (card.brand or "").strip() or brand_evidenced_by_name(card.name)
    return Product(key=card.external_id, name=card.name, brand=brand,
                   model=role.own_model, supplier_article=card.sku,
                   supplier_code=card.supplier_code,
                   category=category, properties={item.name: item.value for item in card.properties})


def _candidates(source: Product, registry: RegistryContext) -> list[dict]:
    article = normalize_model(source.supplier_article)
    ids = sorted(set(registry.identifier_index.get((source.supplier_code, article), [])), key=int) if article else []
    if not ids and article:
        # Safe fallback for legacy Sterbrust cards where the supplier-specific
        # article is printed in the product title but the dedicated property is
        # empty.  Candidate discovery only: match_product still enforces
        # product-kind/model/property conflicts and does not treat this as a
        # persisted supplier identifier.
        ids = sorted(set(registry.title_identifier_index.get((source.supplier_code, article), [])), key=int)
    if not ids:
        model = normalize_model(source.model, source.brand)
        ids = list(registry.by_model.get(model, [])) if model else []
    if not ids:
        base_model = str(model_identity_evidence(source.name).get("base_model") or "")
        ids = list(registry.by_model.get(base_model, [])) if base_model else []
    if not ids and source.brand:
        scored = []
        for product_id in registry.by_brand.get(normalize_brand(source.brand), []):
            row = registry.products[product_id]
            score = SequenceMatcher(None, normalized_name(source.name), normalized_name(row["name"])).ratio()
            if score >= 0.60:
                scored.append((score, product_id))
        ids = [product_id for _, product_id in sorted(scored, reverse=True)[:10]]
    return [registry.products[product_id] for product_id in ids]


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
        rows.append({
            "source_external_id": card.external_id, "SKU": card.sku, "name": card.name,
            "brand": card.brand, "model": source.model, "status": result.status,
            "match_method": result.method, "confidence_score": result.confidence,
            "sterbrust_product_id": result.candidate_key or "",
            "conflict_class": result.evidence.get("conflict_class", ""),
            "warnings": json.dumps(result.warnings, ensure_ascii=False, sort_keys=True),
            "data_quality_flags": json.dumps(flags, ensure_ascii=False, sort_keys=True),
            "conflicts": json.dumps(result.conflicts, ensure_ascii=False, sort_keys=True),
            "evidence": json.dumps(result.evidence, ensure_ascii=False, sort_keys=True),
            "auto_accepted": str(result.auto_accepted).upper(),
        })
    return rows
