"""Read-only extraction of one Intervesp detail-page snapshot.

No HTTP, persistence, registration, matching, or offer-selection behaviour is
implemented here.  Selectors are limited to the observed detail-price block.
"""

from __future__ import annotations

from dataclasses import replace
from html import unescape
import re

from universal_supplier.commercial import (
    PriceExtraction,
    PriceSourceKind,
    PriceState,
    ReadOnlySupplierProduct,
    extract_public_price,
)


_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _match(source: str, pattern: str) -> str | None:
    found = re.search(pattern, source, re.I | re.S)
    return _text(found.group(1)) if found else None


def _raw_match(source: str, pattern: str) -> str | None:
    found = re.search(pattern, source, re.I | re.S)
    return found.group(1) if found else None


def _model(name: str) -> str | None:
    # Preserve WP suffixes: a base model is not proof for a configured machine.
    found = re.search(r"\b(BMSY?[-\s]?\d+(?:[-\s]?[A-Z0-9]+)+)\b", name, re.I)
    return found.group(1).upper().replace(" ", "-") if found else None


def _price(source: str, identity: str, url: str | None) -> PriceExtraction:
    price_block = _raw_match(source, r'<div\s+id=["\']elPrice["\'][^>]*>(.*?)</div>\s*</div>') or ""
    numeric = re.search(r'itemprop=["\']price["\']\s+content=["\']([^"\']+)', price_block, re.I)
    if numeric:
        synthetic = f'<main data-product-identity="{identity}"><span class="price-current">{numeric.group(1)} RUB</span></main>'
        result = extract_public_price(synthetic, product_identity=identity, source_url=url)
        return replace(result, source_path="#elPrice [itemprop=price]", context_path="#elPrice")
    synthetic = f'<main data-product-identity="{identity}">{price_block}</main>'
    result = extract_public_price(synthetic, product_identity=identity, source_url=url)
    return replace(result, source_path="#elPrice", context_path="#elPrice")


def parse_intervesp_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse a saved Intervesp product detail page without requesting anything."""

    name = _match(source, r"<h1[^>]*>(.*?)</h1>") or ""
    model = _model(name)
    canonical = _match(source, r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)')
    url = source_url or canonical
    site_id_match = re.search(r'<div\s+class=["\']el_Main["\']\s+id=["\']bx_[^"\']+_(\d+)', source, re.I)
    site_id = site_id_match.group(1) if site_id_match else None
    identity = model or name
    price = _price(source, identity, url)
    availability = "InStock" if re.search(r'itemprop=["\']availability["\'][^>]+InStock', source, re.I) else None
    return ReadOnlySupplierProduct(
        supplier="intervesp", source_url=url, name=name, supplier_model=model,
        manufacturer_article=None, site_internal_id=site_id, availability=availability,
        price=price,
        diagnostics=("manufacturer_article_not_explicit_in_observed_detail_block",),
    )
