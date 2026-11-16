"""Read-only extraction of one Intervesp detail-page snapshot.

No HTTP, persistence, registration, matching, or offer-selection behaviour is
implemented here.  Selectors are limited to the observed detail-price block.
"""

from __future__ import annotations

from dataclasses import replace
from html import escape, unescape
import re

from universal_supplier.commercial import (
    PriceExtraction,
    PriceSourceKind,
    PriceState,
    ReadOnlySupplierProduct,
    extract_public_price,
)
from ._source_content import breadcrumb_category
from ._commercial_dom import soup, schema_availability
from universal_supplier.price_semantics import typed_extraction


_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _match(source: str, pattern: str) -> str | None:
    found = re.search(pattern, source, re.I | re.S)
    return _text(found.group(1)) if found else None


def _raw_match(source: str, pattern: str) -> str | None:
    found = re.search(pattern, source, re.I | re.S)
    return found.group(1) if found else None


def _raw_model(name: str) -> str | None:
    # Preserve WP suffixes: a base model is not proof for a configured machine.
    found = re.search(r"\b((?:BMSY|BMSO|BMS|BMDO|BMH)[-\s]?\d+(?:[-\s]?[A-Z0-9]+)+)\b", name, re.I)
    return found.group(1) if found else None


def _model(name: str) -> str | None:
    raw = _raw_model(name)
    return raw.upper().replace(" ", "-") if raw else None


def _price(source: str, identity: str, url: str | None) -> PriceExtraction:
    dom=soup(source);block=dom.select_one('#elPrice')
    price_block=str(block) if block else ''
    nodes=block.select('[itemprop="price"][content]') if block else []
    currencies={n.get('content') or n.get_text(strip=True) for n in block.select('[itemprop="priceCurrency"]')} if block else set()
    # Visible ruble label inside this price block is evidence; a bare numeric
    # content attribute alone does not establish a currency.
    if not currencies and block and re.search(r'₽|\bруб\.?|\bRUB\b',block.get_text(' ',strip=True),re.I):currencies={'RUB'}
    values={n.get('content') for n in nodes}
    source_path='#elPrice'
    if len(values)==1 and len(currencies)==1:
        synthetic = f'<main data-product-identity="{escape(identity, quote=True)}"><span class="price-current">{escape(next(iter(values)))} {escape(next(iter(currencies)))}</span></main>'
        result = extract_public_price(synthetic, product_identity=identity, source_url=url)
        source_path='#elPrice [itemprop=price]'
    else:
        synthetic = f'<main data-product-identity="{escape(identity, quote=True)}">{price_block}</main>'
        result = extract_public_price(synthetic, product_identity=identity, source_url=url)
    result=replace(result,source_path=source_path,context_path='#elPrice')
    return typed_extraction(result,block.get_text(' ',strip=True) if block else '',source_path=source_path,declared=block.get('data-price-type') if block else None)


def parse_intervesp_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse a saved Intervesp product detail page without requesting anything."""

    name = _match(source, r"<h1[^>]*>(.*?)</h1>") or ""
    model = _model(name)
    canonical = _match(source, r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)')
    url = source_url or canonical
    dom=soup(source);own=dom.select_one('.el_Main[id]')
    site_id_match = re.search(r'^bx_.+_(\d+)$',own.get('id','')) if own else None
    site_id = site_id_match.group(1) if site_id_match else None
    identity = model or name
    price = _price(source, identity, url)
    availability = schema_availability(own) if own else None
    category, category_url = breadcrumb_category(source, url)
    return ReadOnlySupplierProduct(
        supplier="intervesp", source_url=url, name=name, supplier_model=model,
        manufacturer_article=None, site_internal_id=site_id, availability=availability,
        price=price,
        diagnostics=("manufacturer_article_not_explicit_in_observed_detail_block",),
        source_category=category, source_category_url=category_url,
        raw_supplier_model=_raw_model(name),
    )
