"""Read persisted source evidence for matching; never mutate ingestion state.

One conversion is shared by candidate matching and identity materialization.
Explicit stored values take precedence; absent values remain absent or use the
existing conservative title inference. No supplier IDs or model exceptions.
"""
from dataclasses import replace
from typing import Mapping

from sterbrust_matching.matching import Product
from sterbrust_matching.normalization import brand_evidenced_by_name
from sterbrust_matching.product_identity import classify_model_role
from .models import CategoryNode, ProductCard


def _evidence(card: Mapping):
    raw = card.get('raw_data') or {}
    return raw, raw.get('enrichment_evidence') or {}, raw.get('source_identity') or {}, raw.get('source_content') or {}


def matching_category(card: Mapping) -> str:
    _, enrichment, _, content = _evidence(card)
    return ' / '.join(str(n.get('name') or '') for n in card.get('categories', []) if n.get('name')) or str(
        enrichment.get('source_category') or content.get('source_category') or '')


def matching_product(card: Mapping) -> Product:
    _, enrichment, identity, _ = _evidence(card)
    name = str(card.get('name') or '')
    category = matching_category(card)
    stored_model = str(identity.get('raw_model') or enrichment.get('model_raw') or '')
    role = classify_model_role(name, category, stored_model)
    kind = enrichment.get('product_kind') or {}
    kind = kind.get('product_kind', '') if isinstance(kind, dict) else kind
    properties = {}
    for item in card.get('properties', []):
        if item.get('name') and item.get('value') not in (None, ''):
            value = str(item['value'])
            unit = str(item.get('unit') or '')
            if unit and unit.lower() not in value.lower():
                value += ' ' + unit
            properties[str(item['name'])] = value
    return Product(key=str(card.get('external_id') or ''), name=name,
        supplier_code=str(card.get('supplier_code') or ''), supplier_article=str(card.get('sku') or ''),
        brand=str(card.get('brand') or '').strip() or str(enrichment.get('source_brand') or '').strip() or brand_evidenced_by_name(name),
        model=role.own_model, product_kind=str(kind or ''), category=category, properties=properties)


def with_persisted_evidence(card: ProductCard) -> ProductCard:
    """Fill only matching-view fields missing from normalized relational details."""
    view = card.as_jsonable()
    product = matching_product(view)
    _, _, _, content = _evidence(view)
    categories = card.categories
    if not categories and product.category:
        categories = (CategoryNode(product.category, str(content.get('source_category_url') or ''), 0),)
    return replace(card, brand=product.brand, categories=categories)
