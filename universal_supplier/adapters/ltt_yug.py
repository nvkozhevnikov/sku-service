"""LTT Yug (ltt-yug.ru) detail parser for the custom DNage catalog CMS.

Identity is the numeric ``data-product`` of the product's own buy button,
matched through its ``data-label-modal`` title so related offers can never
donate their ID.  Prices are roubles with space grouping and an optional dot
decimal; availability is the ``countBlock`` wording with its quantity.
"""
from __future__ import annotations

from decimal import Decimal
from html import unescape
import re
from urllib.parse import urlparse
from .ltt_uss_html import soup, text, price_value, data_rows, source_model, source_evidence

from universal_supplier.commercial import (
    PriceExtraction, PriceSourceKind, PriceState, ReadOnlySupplierProduct,
)

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
_BUTTON_RE = re.compile(
    r"<button[^>]*data-label-modal=[\"']([^\"']*)[\"'][^>]*data-product=[\"'](\d+)[\"'][^>]*>.*?</button>",
    re.I | re.S)
_BUTTON_REV_RE = re.compile(
    r"<button[^>]*data-product=[\"'](\d+)[\"'][^>]*data-label-modal=[\"']([^\"']*)[\"'][^>]*>.*?</button>",
    re.I | re.S)
_RELATED_CUT_RE = re.compile(r"<div\s+class=[\"']offer[\"']", re.I)
_COUNT_RE = re.compile(r"<div\s+class=[\"']countBlock[\"'][^>]*>(.*?)</div>", re.I | re.S)
_PRICE_RE = re.compile(r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*руб(?:лей|\b|\.)", re.I)
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.I | re.S)
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9*+/.,\-]+$")

BASE_URL = "https://ltt-yug.ru/"


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _model_from_title(name: str) -> str | None:
    return source_model(name)


def _identity(source: str, name: str) -> tuple[str | None, list[str]]:
    diagnostics: list[str] = []
    label = re.sub(r"[\s\-–—]+", "", _text(name)).lower()
    candidates: list[tuple[str, str]] = []
    for element in soup(source).select('[data-product][data-label-modal]'):
        pid = element.get('data-product', '')
        if pid.isdigit():
            candidates.append((pid, element.get('data-label-modal', '')))
    if not candidates:
        return None, ["yug_data_product_button_missing"]
    matching = [pid for pid, modal in candidates
                if label and label in re.sub(r"[\s\-–—]+", "", _text(modal)).lower()]
    if len(set(matching)) == 1:
        return matching[0], []
    if len(set(matching)) > 1:
        diagnostics.append("yug_multiple_buttons_match_title")
    diagnostics.append("yug_identity_unresolved")
    return None, diagnostics


def _price(zone: str, identity: str, source_url: str | None) -> PriceExtraction:
    own = soup(zone).select_one('.prices')
    if own is not None and re.search(r'запрос|уточняйте', text(own), re.I):
        return price_value(text(own), identity=identity, url=source_url, path='div.prices')
    zone = text(own) if own is not None else ''
    if '/' in zone:
        return price_value(zone, identity=identity, url=source_url, path='div.prices')
    match = _PRICE_RE.search(zone)
    if not match:
        return PriceExtraction(
            state=PriceState.MISSING, current_price=None, old_price=None, currency=None,
            raw_text="", source_kind=PriceSourceKind.NONE, source_path=None,
            diagnostics=("yug_price_text_not_found",), product_identity=identity,
            source_url=source_url, context_path=None)
    raw = match.group(1)
    cleaned = re.sub(r"[\s\u00a0]", "", raw).replace(",", ".")
    try:
        value = Decimal(cleaned)
    except Exception:
        return PriceExtraction(
            state=PriceState.PARSE_ERROR, current_price=None, old_price=None, currency=None,
            raw_text=_text(raw), source_kind=PriceSourceKind.PRODUCT_PRICE_DOM,
            source_path="main-product-price-text", diagnostics=("yug_price_unparseable",),
            product_identity=identity, source_url=source_url, context_path=None)
    state = PriceState.NUMERIC_PUBLIC if value > 0 else PriceState.ZERO_INVALID
    return PriceExtraction(
        state=state, current_price=value if value > 0 else None, old_price=None,
        currency="RUB", raw_text=f"{raw} руб.", source_kind=PriceSourceKind.PRODUCT_PRICE_DOM,
        source_path="main-product-price-text", diagnostics=(), product_identity=identity,
        source_url=source_url, context_path=None)



def _availability(zone: str) -> tuple[str | None, list[str]]:
    match = _COUNT_RE.search(zone)
    if not match:
        return None, ["yug_countblock_missing"]
    wording = _text(match.group(1)).lower()
    if "нет в наличии" in wording:
        return "OutOfStock", []
    if "под заказ" in wording:
        return "PreOrder", []
    if wording.startswith("в наличии"):
        diagnostics = []
        qty = re.search(r"в наличии\s*-\s*(\d+)\s*шт", wording)
        if qty:
            diagnostics.append(f"yug_stock_quantity:{qty.group(1)}")
        return "InStock", diagnostics
    return None, [f"yug_unknown_availability_wording:{wording[:60]}"]


def _properties(zone: str) -> tuple[tuple[str, str], ...]:
    properties = []
    for key, value in _ROW_RE.findall(zone):
        name, item = _text(key), _text(value)
        if not name or not item or name.lower() in {"наименование", "параметры"}:
            continue
        properties.append((name, item))
        if len(properties) >= 120:
            break
    return tuple(properties)


def _category(source_url: str | None) -> tuple[str, str | None]:
    if not source_url:
        return "", None
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 3 and parts[0] == "catalog":
        return "/".join(parts[1:-1]), f"{parsed.scheme}://{parsed.hostname}/{'/'.join(parts[:-1])}"
    return "", None


def parse_ltt_yug_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse one saved LTT Yug detail page; related offers are out of scope."""
    from .ltt_yug_role import classify_yug_page, NonProductYugPage
    role = classify_yug_page(source, source_url)
    if role != 'PRODUCT_PAGE':
        raise NonProductYugPage(role)
    diagnostics: list[str] = ["ltt_yug_dnage_parser"]
    h1 = _H1_RE.search(source)
    name = _text(h1.group(1)) if h1 else ""
    if not name:
        diagnostics.append("yug_h1_missing")
    cut = _RELATED_CUT_RE.search(source)
    zone = source[:cut.start()] if cut else source

    site_id, identity_diagnostics = _identity(zone, name)
    diagnostics.extend(identity_diagnostics)
    model = _model_from_title(name)
    identity = model or name or site_id or "unknown"
    price = _price(zone, identity, source_url)
    availability, avail_diagnostics = _availability(zone)
    diagnostics.extend(avail_diagnostics)
    properties = _properties(zone)
    if not properties:
        diagnostics.append("yug_technical_properties_not_found")
    if model is None:
        diagnostics.append("yug_model_token_not_in_title")
    category, category_url = _category(source_url)
    availability_match = _COUNT_RE.search(zone)
    availability_raw = _text(availability_match.group(1)) if availability_match else ''

    return ReadOnlySupplierProduct(
        supplier="ltt_yug", source_url=source_url or BASE_URL, name=name,
        supplier_model=model, manufacturer_article=None, site_internal_id=site_id,
        availability=availability, price=price, diagnostics=tuple(diagnostics),
        description_text="", technical_properties=properties, source_images=(),
        source_category=category, source_category_url=category_url,
        raw_supplier_model=model, source_brand="",
        enrichment_evidence=source_evidence(name,category,properties,availability_raw),
    )
