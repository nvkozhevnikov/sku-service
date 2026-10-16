"""Shared MODX miniShop2 detail parser for the LTT regional sites.

ltt-msk.ru and lttsib.ru run the same MODX/miniShop2 template family, so one
parser with per-site configuration extracts both.  Identity is the miniShop2
``ms2_form`` hidden ``id``; prices come from the ``product_price`` /
``product_price_old_price`` / ``product_price_no_price`` zones of the product
card (related-goods ``catalog_item_*`` prices are out of scope); availability
is the coloured stock span.  A numeric price never coexists with the
price-on-request wording on the audited cards, and the parser refuses that
combination instead of choosing a winner.
"""
from __future__ import annotations

from html import unescape
import re
from urllib.parse import urljoin
from dataclasses import replace
from .ltt_uss_html import soup, text, price_value, data_rows, source_model, source_evidence

from universal_supplier.commercial import (
    PriceExtraction, PriceSourceKind, PriceState, ReadOnlySupplierProduct,
    extract_public_price,
)

LTT_MODX_SITES = {
    "ltt_msk": "https://ltt-msk.ru/",
    "ltt_sib": "https://lttsib.ru/",
}

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1\b[^>]*itemprop=[\"']name[\"'][^>]*>(.*?)</h1\s*>", re.I | re.S)
_H1_ANY_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
_MS2_ID_FORM_RE = re.compile(
    r"<form[^>]*class=[\"'][^\"']*ms2_form[^\"']*[\"'][^>]*>.*?"
    r"<input[^>]*name=[\"']id[\"'][^>]*value=[\"'](\d+)[\"']", re.I | re.S)
_MS2_ID_ANY_RE = re.compile(
    r"<input[^>]*name=[\"']id[\"'][^>]*value=[\"'](\d+)[\"']", re.I)
_PRICE_RE = re.compile(r"<div\s+class=[\"']product_price[\"'][^>]*>(.*?)</div>", re.I | re.S)
_OLD_PRICE_RE = re.compile(r"<div\s+class=[\"']product_old_price[\"'][^>]*>(.*?)</div>", re.I | re.S)
_POR_RE = re.compile(
    r"<div\s+class=[\"']product_price_no_price[\"'][^>]*>(.*?)</div>", re.I | re.S)
_AVAIL_SPAN_RE = re.compile(
    r"<span[^>]*style=[\"'][^\"']*color:\s*(green|red|orange)[^\"']*[\"'][^>]*>(.*?)</span>",
    re.I | re.S)
_BREADCRUMB_ZONE_RE = re.compile(
    r"itemtype=[\"'][^\"']*BreadcrumbList[\"'][^>]*>(.*?)(?:</nav>|<div[^>]*class=[\"'][^\"']*(?:product|content))",
    re.I | re.S)
_BREADCRUMB_NAME_RE = re.compile(r"itemprop=[\"']name[\"'][^>]*>(.*?)<", re.I | re.S)
_BREADCRUMB_LINK_RE = re.compile(r"itemprop=[\"']item[\"'][^>]*href=[\"']([^\"']+)[\"']", re.I)
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.I | re.S)
_RELATED_CUT_RE = re.compile(r"Другие\s+товары", re.I)
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9*+/.,\-]+$")

_AVAILABILITY_MAP = {
    "в наличии": "InStock",
    "нет в наличии": "OutOfStock",
    "заканчиваются": "InStock",
    "под заказ": "PreOrder",
}


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _card_zone(source: str) -> str:
    """Cut the page at the related-goods block; the card always precedes it."""
    dom = soup(source)
    own = dom.select_one('.product')
    if own is not None:
        for related in own.select('.catalog_item'):
            related.decompose()
        return str(own)
    cut = _RELATED_CUT_RE.search(source)
    return source[:cut.start()] if cut else source


def _model_from_title(name: str) -> str | None:
    return source_model(name)


def _availability(zone: str) -> tuple[str | None, tuple[str, ...]]:
    matches = _AVAIL_SPAN_RE.findall(zone)
    if not matches:
        return None, ("modx_availability_span_missing",)
    states = set()
    raws = []
    for color, raw in matches:
        wording = _text(raw).lower().rstrip(".")
        states.add(wording)
        raws.append(wording)
    if len(states) > 1:
        return None, ("modx_conflicting_availability_wordings", f"modx_saw:{'|'.join(sorted(states))}")
    wording = raws[0]
    state = _AVAILABILITY_MAP.get(wording)
    if state is None:
        return None, ("modx_unknown_availability_wording", f"modx_saw:{wording}")
    diagnostics = ()
    if wording == "заканчиваются":
        diagnostics = ("modx_limited_stock_wording",)
    return state, diagnostics


def _breadcrumbs(source: str) -> tuple[str, str | None]:
    zone = _BREADCRUMB_ZONE_RE.search(source)
    if not zone:
        return "", None
    names = [_text(name) for name in _BREADCRUMB_NAME_RE.findall(zone.group(1))]
    names = [name for name in names if name and name.lower() != "главная"]
    links = _BREADCRUMB_LINK_RE.findall(zone.group(1))
    if len(names) >= 2:
        return names[-2], (links[-2] if len(links) >= 2 else None)
    if names:
        return names[0], (links[0] if links else None)
    return "", None


def _properties(zone: str) -> tuple[tuple[str, str], ...]:
    properties = []
    for key, value in _ROW_RE.findall(zone):
        name, item = _text(key), _text(value)
        if not name or not item or name.lower() in {"наименование", "параметры"}:
            continue
        if any(ch.isdigit() for ch in name) and not any(ch.isdigit() for ch in item):
            # A row whose "name" carries digits and whose value has none is
            # usually a collapsed two-column model list, not a property.
            continue
        properties.append((name, item))
        if len(properties) >= 120:
            break
    return tuple(properties)



def parse_ltt_modx_detail(source: str, *, supplier: str,
                          source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse one saved LTT MODX (miniShop2) detail page; related block excluded."""
    if supplier not in LTT_MODX_SITES:
        raise ValueError(f"supplier {supplier} is not an LTT MODX site")
    diagnostics: list[str] = ["ltt_modx_shared_parser"]

    h1 = _H1_RE.search(source) or _H1_ANY_RE.search(source)
    name = _text(h1.group(1)) if h1 else ""
    zone = _card_zone(source)

    own_dom = soup(zone)
    form_ids = sorted({node.get('value') for node in own_dom.select('form.form-horizontal.ms2_form input[name="id"]')
                       if re.fullmatch(r'\d+', node.get('value', ''))})
    site_id: str | None = None
    if form_ids:
        if len(form_ids) == 1:
            site_id = form_ids[0]
        else:
            diagnostics.append(f"modx_ms2_form_ids_conflict:{'|'.join(form_ids[:4])}")
    else:
        bare_ids = sorted(set(_MS2_ID_ANY_RE.findall(zone)))
        if len(bare_ids) == 1:
            site_id = bare_ids[0]
            diagnostics.append("modx_ms2_id_outside_form_scope")
        else:
            diagnostics.append("modx_ms2_product_id_unresolved")

    price_match = _PRICE_RE.search(zone)
    por_match = _POR_RE.search(zone)
    old_match = _OLD_PRICE_RE.search(zone)
    model = _model_from_title(name)
    identity = model or name or (site_id or "unknown")
    if price_match and por_match:
        price = PriceExtraction(
            state=PriceState.PARSE_ERROR, current_price=None, old_price=None,
            currency=None, raw_text=f"{_text(price_match.group(1))} | {_text(por_match.group(1))}",
            source_kind=PriceSourceKind.PRODUCT_PRICE_DOM, source_path="div.product_price+div.product_price_no_price",
            diagnostics=("modx_numeric_and_por_simultaneously",), product_identity=identity,
            source_url=source_url, context_path=None)
    elif por_match:
        price = PriceExtraction(
            state=PriceState.PRICE_ON_REQUEST, current_price=None, old_price=None,
            currency=None, raw_text=_text(por_match.group(1)),
            source_kind=PriceSourceKind.PRODUCT_PRICE_DOM, source_path="div.product_price_no_price",
            diagnostics=(), product_identity=identity, source_url=source_url, context_path=None)
    elif price_match:
        price = price_value(_text(price_match.group(1)), identity=identity, url=source_url, path='div.product_price')
        if old_match:
            old = price_value(_text(old_match.group(1)), identity=identity, url=source_url, path='div.product_old_price')
            price = replace(price, old_price=old.current_price)
        if price.state is not PriceState.NUMERIC_PUBLIC:
            diagnostics.append(f"modx_price_not_numeric:{price.state.value}")
    else:
        price = PriceExtraction(
            state=PriceState.MISSING, current_price=None, old_price=None, currency=None,
            raw_text="", source_kind=PriceSourceKind.NONE, source_path=None,
            diagnostics=("modx_price_zone_not_found",), product_identity=identity,
            source_url=source_url, context_path=None)
        diagnostics.append("modx_price_zone_not_found")

    availability, avail_diagnostics = _availability(zone)
    diagnostics.extend(avail_diagnostics)
    category, category_url = _breadcrumbs(source)
    properties = tuple(row for row in data_rows(own_dom) if len(row) == 2 and all(row))
    model = source_model(name, properties)
    availability_raw = ' | '.join(_text(raw) for _,raw in _AVAIL_SPAN_RE.findall(zone))
    # Kind-only corroboration from this card's explicit purpose section. Do not
    # broaden commercial extraction or consume related product descriptions.
    own_description_node = own_dom.select_one('#product_descrption [itemprop="description"]')
    own_description = ' '.join(own_description_node.stripped_strings) if own_description_node else ''
    if not properties:
        diagnostics.append("modx_technical_properties_not_found")
    if model is None:
        diagnostics.append("modx_model_token_not_in_title")

    url = source_url or LTT_MODX_SITES[supplier]
    if category_url:
        category_url = urljoin(url, category_url)

    return ReadOnlySupplierProduct(
        supplier=supplier, source_url=url, name=name, supplier_model=model,
        manufacturer_article=None, site_internal_id=site_id,
        availability=availability, price=price, diagnostics=tuple(diagnostics),
        description_text="", technical_properties=properties,
        source_images=(), source_category=category, source_category_url=category_url,
        raw_supplier_model=model, source_brand="",
        enrichment_evidence=source_evidence(name,category,properties,availability_raw,
                                            own_description=own_description),
    )
