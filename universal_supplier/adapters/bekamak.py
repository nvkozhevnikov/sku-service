"""Read-only extraction of one Beka-Mak detail-page snapshot."""

from __future__ import annotations

from dataclasses import replace
from html import escape, unescape
import re

from universal_supplier.commercial import ReadOnlySupplierProduct, extract_public_price
from ._source_content import breadcrumb_category
from ._commercial_dom import soup, schema_availability
from universal_supplier.price_semantics import typed_extraction


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
    dom=soup(detail)
    prices=dom.select('.prices_block .price[data-currency][data-value]')
    # Some saved detail snapshots omit the wrapper, but still carry the own
    # product price matrix. Never choose an arbitrary second price.
    if not prices:prices=dom.select('.price[data-currency][data-value]')
    values={(p.get('data-value'),p.get('data-currency')) for p in prices}
    price_node=prices[0] if len(values)==1 else None
    if price_node:
        synthetic = f'<main data-product-identity="{escape(identity, quote=True)}"><span class="price-current">{escape(price_node.get("data-value", ""))} {escape(price_node.get("data-currency", ""))}</span></main>'
    else:
        synthetic = f'<main data-product-identity="{escape(identity, quote=True)}"></main>'
    price = extract_public_price(synthetic, product_identity=identity, source_url=url)
    price = replace(price, source_path=".prices_block .price[data-currency=RUB][data-value]", context_path="detail product before similar-products slider")
    price=typed_extraction(price,price_node.get_text(' ',strip=True) if price_node else '',source_path='.prices_block .price',declared=price_node.get('data-price-type') if price_node else None)
    availability = schema_availability(dom)
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
