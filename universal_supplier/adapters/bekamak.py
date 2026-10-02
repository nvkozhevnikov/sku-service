"""Read-only extraction of one Beka-Mak detail-page snapshot."""

from __future__ import annotations

from dataclasses import replace
from html import unescape
import re

from universal_supplier.commercial import ReadOnlySupplierProduct, extract_public_price
from ._source_content import breadcrumb_category


_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _match(source: str, pattern: str) -> str | None:
    found = re.search(pattern, source, re.I | re.S)
    return _text(found.group(1)) if found else None


def parse_bekamak_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse a saved Beka-Mak detail page; recommendations are out of scope."""

    name = _match(source, r'<h1\s+id=["\']pagetitle["\']>(.*?)</h1>') or ""
    canonical = _match(source, r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)')
    url = source_url or canonical
    site_id_match = re.search(r'setViewedProduct\((\d+),', source, re.I)
    site_id = site_id_match.group(1) if site_id_match else None
    article = _match(source, r'item_block--article.*?itemprop=["\']value["\']>(.*?)</span>')
    title_model_match = re.search(r"\b((?:BMSY|BMSO|BMS|BMDO|BMH)[-\s]?\d+(?:[-\s]?[A-Z0-9]+)+)\b", name, re.I)
    title_model = title_model_match.group(1) if title_model_match else None
    model = article or title_model
    identity = article or name

    # The first price matrix is the current detail card.  The later
    # "Похожее оборудование" slider is intentionally never searched.
    detail = source[:source.find("Похожее оборудование")] if "Похожее оборудование" in source else source
    numeric = re.search(r'class=["\']price["\']\s+data-currency=["\']RUB["\']\s+data-value=["\']([^"\']+)', detail, re.I)
    if numeric:
        synthetic = f'<main data-product-identity="{identity}"><span class="price-current">{numeric.group(1)} RUB</span></main>'
    else:
        synthetic = f'<main data-product-identity="{identity}"></main>'
    price = extract_public_price(synthetic, product_identity=identity, source_url=url)
    price = replace(price, source_path=".prices_block .price[data-currency=RUB][data-value]", context_path="detail product before similar-products slider")
    availability = "InStock" if re.search(r'itemprop=["\']availability["\'][^>]+InStock', detail, re.I) else None
    category, category_url = breadcrumb_category(source, url)
    return ReadOnlySupplierProduct(
        supplier="beka-mak", source_url=url, name=name, supplier_model=model,
        manufacturer_article=article, site_internal_id=site_id, availability=availability,
        price=price,
        diagnostics=(("site_internal_id_is_not_manufacturer_article",) +
                     (("manufacturer_article_missing_from_detail",) if article is None else ())),
        source_category=category, source_category_url=category_url,
        raw_supplier_model=model,
    )
