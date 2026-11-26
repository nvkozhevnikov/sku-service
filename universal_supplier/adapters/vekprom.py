"""Read-only extraction of one Vekprom (vekprom.ru) detail-page snapshot."""

from __future__ import annotations

from html import unescape
import re
from dataclasses import replace
from urllib.parse import urljoin

from universal_supplier.commercial import (
    PriceState, ReadOnlySupplierProduct, extract_public_price,
)
from ._source_content import breadcrumb_category


_TAG_RE = re.compile(r"<[^>]+>")
_PRODUCT_ITEMTYPE_RE = re.compile(r"itemtype=[\"']https://schema\.org/Product[\"']", re.I)
_OFFER_ITEMTYPE_RE = re.compile(r"itemtype=[\"']https?://schema\.org/Offer[\"']", re.I)
_ELEMENT_ID_RE = re.compile(r"name=[\"']ELEMENT_ID[\"']\s+value=[\"'](\d+)[\"']", re.I)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
_CANONICAL_RE = re.compile(r"<link\s+rel=[\"']canonical[\"']\s+href=[\"']([^\"']+)", re.I)
_VISIBLE_PRICE_RE = re.compile(r"class=[\"']product__price-current[\"'][^>]*>(.*?)<", re.I | re.S)
_AVAILABILITY_RE = re.compile(
    r"itemprop=[\"']availability[\"']\s+href=[\"'][^\"']*?schema\.org/(InStock|OutOfStock|PreOrder|Discontinued)[\"']", re.I)
_ARTICLE_RE = re.compile(
    r"class=[\"']product__props-name[\"']>\s*Артикул\s*</div>\s*<div\s+class=[\"']product__props-value[\"']>\s*(.*?)</div>",
    re.I | re.S)
_PROP_ROW_RE = re.compile(
    r"class=[\"']product__props-name[\"']>\s*(.*?)\s*</div>\s*<div\s+class=[\"']product__props-value[\"']>\s*(.*?)\s*</div>",
    re.I | re.S)
_BRAND_RE = re.compile(r"<a\s+href=[\"']/brands/[^\"']*[\"'][^>]*>(.*?)</a>", re.I | re.S)
_OUTOF_RE = re.compile(r"class=[\"']product__outof[\"'][^>]*>", re.I)
_IMAGE_RE = re.compile(r"(?:src|href|data-src)=[\"']([^\"']*?/upload/iblock/[^\"']+)[\"']", re.I)


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _balance_fragment(fragment: str) -> str:
    """Make a mid-document slice safe to embed in the synthetic wrapper.

    The slider cut can leave a trailing partial tag or an unterminated HTML
    comment (its ``-->`` may live beyond the cut); either would swallow the
    appended Offer block as tag/comment content.
    """
    comment_open = fragment.rfind("<!--")
    if comment_open != -1 and fragment.find("-->", comment_open) == -1:
        fragment = fragment[:comment_open]
    last_open = fragment.rfind("<")
    if last_open != -1 and ">" not in fragment[last_open:]:
        fragment = fragment[:last_open]
    for tag in ("script", "style", "textarea", "title"):
        opens = len(re.findall(rf"<{tag}\b", fragment, re.I))
        closes = len(re.findall(rf"</{tag}\b", fragment, re.I))
        if opens > closes:
            fragment += f"</{tag}>" * (opens - closes)
    return fragment


def parse_vekprom_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse a saved Vekprom detail page; recommendations slider is out of scope.

    Identity is the Bitrix ``ELEMENT_ID`` form value, never the JS-filled
    ``div#article`` placeholder and never a URL digest.
    """

    h1 = _H1_RE.search(source)
    name = _text(h1.group(1)) if h1 else ""
    canonical = _CANONICAL_RE.search(source)
    url = source_url or (canonical.group(1) if canonical else None)

    # Scope identity/property extraction to the schema.org Product block; the
    # later recommendations slider repeats card markup with its own prices.
    marker = _PRODUCT_ITEMTYPE_RE.search(source)
    start = marker.start() if marker else 0
    slider = source.find('class="card-item', start + 1)
    detail = source[start:] if slider == -1 else source[start:slider]

    # The unique schema.org Offer block (price/currency/availability microdata)
    # renders after the first recommendations card, outside the head zone.  It
    # occurs once per page, so appending it cannot import slider prices.  The
    # slice keeps the container's opening tag so the microdata stays inside the
    # itemscope and the fragment remains balanced.
    offer = _OFFER_ITEMTYPE_RE.search(source, start)
    if offer is not None:
        container = source.rfind("<", 0, offer.start())
        block_start = container if container != -1 else offer.start()
        closes = [pos for pos in
                  (source.find("</span>", offer.start()), source.find("</div>", offer.start()))
                  if pos != -1]
        block_end = min(closes) if closes else len(source)
        offer_block = source[block_start:block_end]
        if closes:
            offer_block += "</span>" if source.startswith("</span>", block_end) else "</div>"
    else:
        offer_block = ""
    price_zone = _balance_fragment(detail) + offer_block

    element_id_match = _ELEMENT_ID_RE.search(source)
    site_id = element_id_match.group(1) if element_id_match else None

    article_match = _ARTICLE_RE.search(detail)
    article = _text(article_match.group(1)) if article_match else None
    brand_match = _BRAND_RE.search(detail)
    brand = _text(brand_match.group(1)) if brand_match else ""
    properties = tuple((_text(key), _text(value))
                       for key, value in _PROP_ROW_RE.findall(detail))

    availability_match = _AVAILABILITY_RE.search(offer_block or detail)
    availability = availability_match.group(1) if availability_match else None
    outof = _OUTOF_RE.search(detail)
    hidden = outof is not None and "display:none" in outof.group(0).lower().replace(" ", "")
    diagnostics = ["site_internal_id_is_not_manufacturer_article"]
    if site_id is None:
        diagnostics.append("element_id_missing_from_detail")
    if article is None:
        diagnostics.append("manufacturer_article_missing_from_detail")
    if outof is not None and not hidden:
        # A visible «Снят с производства» banner outranks microdata stock.
        diagnostics.append("discontinued_marker_visible")
        availability = "OutOfStock"
    if availability == "Discontinued":
        diagnostics.append("discontinued_availability_marker")
        availability = "OutOfStock"

    images: list[str] = []
    for candidate in _IMAGE_RE.findall(detail):
        absolute = urljoin(url, candidate) if url else candidate
        if absolute not in images:
            images.append(absolute)

    identity = article or name
    synthetic = f'<main data-product-identity="{identity}">{price_zone}</main>'
    price = extract_public_price(synthetic, product_identity=identity, source_url=url)
    # Audit rule: a numeric microdata price must equal the visible
    # product__price-current figure; disagreement is a parse failure, not a
    # choice between the two numbers.
    visible_match = _VISIBLE_PRICE_RE.search(detail)
    microdata_price = re.search(
        r"itemprop=[\"']price[\"']\s+content=[\"']([\d\s\u00a0\u202f]+)[\"']", offer_block, re.I)
    if visible_match and microdata_price:
        visible_digits = re.sub(r"[^\d]", "", unescape(visible_match.group(1)))
        microdata_digits = re.sub(r"[^\d]", "", microdata_price.group(1))
        if visible_digits and microdata_digits and visible_digits != microdata_digits:
            price = replace(price, state=PriceState.PARSE_ERROR, current_price=None,
                            old_price=None, currency=None,
                            raw_text=_text(visible_match.group(1)),
                            diagnostics=price.diagnostics + ("visible_price_microdata_mismatch",))
    category, category_url = breadcrumb_category(source, url or "")
    return ReadOnlySupplierProduct(
        supplier="vekprom", source_url=url, name=name, supplier_model=article,
        manufacturer_article=article, site_internal_id=site_id, availability=availability,
        price=price, diagnostics=tuple(diagnostics), technical_properties=properties,
        source_images=tuple(images), source_category=category, source_category_url=category_url,
        raw_supplier_model=article, source_brand=brand,
    )
