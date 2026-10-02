"""Scoped listing evidence/identity bridge. No network, SQL or matcher mutation."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import re
from typing import Mapping, Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import CategoryNode, ProductCard, PropertyValue
from .commercial import PriceState, PriceExtraction, PriceSourceKind, ReadOnlySupplierProduct, parse_rub_money

IDENTITY_CONTRACT = 'intervesp-canonical-url-v1'


def product_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or parsed.hostname not in ('intervesp.ru', 'www.intervesp.ru')
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
            or parsed.query or parsed.fragment):
        raise ValueError('unsafe Intervesp product URL')
    path = re.sub('/+', '/', parsed.path).rstrip('/') + '/'
    parts = [part for part in path.split('/') if part]
    if len(parts) != 3 or parts[0] != 'catalog' or any(part in ('.', '..', 'filter') for part in parts):
        raise ValueError('not an Intervesp product URL')
    if re.search(r'%2f|%5c|%2e|\\', path, re.I):
        raise ValueError('encoded path ambiguity')
    return urlunsplit(('https', 'intervesp.ru', path, '', ''))


def url_identity(url: str) -> str:
    return 'url-sha256:' + hashlib.sha256(product_url(url).encode()).hexdigest()


def identity_index(records: Iterable[Mapping]) -> dict[str, dict]:
    """Keep old IDs/PKs. Reject duplicate/colliding URL identity, never merge rows."""
    urls, ids = {}, {}
    for record in records:
        if record.get('source', record.get('supplier_code', 'intervesp')) != 'intervesp':
            continue
        external = str(record['external_id'])
        aliases = {product_url(str(record[k])) for k in ('source_url', 'canonical_url') if record.get(k)}
        for url in aliases:
            if url in urls and urls[url]['external_id'] != external:
                raise ValueError('duplicate Intervesp URL requires REVIEW, not automatic remap')
            if external in ids and not aliases.intersection(ids[external]):
                raise ValueError('external identity refers to unrelated URLs')
            urls[url] = {**record, 'external_id': external}
        ids.setdefault(external, set()).update(aliases)
    return urls


def resolve_identity(url: str, previous: Mapping[str, Mapping]) -> str:
    canonical = product_url(url)
    old = previous.get(canonical)
    return str(old['external_id']) if old else url_identity(canonical)


def present(value) -> str:
    return '' if value in (None, '', 'UNKNOWN') else str(value)


def properties(row: Mapping) -> tuple[PropertyValue, ...]:
    result = []
    for line in present(row.get('short_specs')).split(' | '):
        if ': ' not in line:
            continue
        name, value = line.rsplit(': ', 1)
        if name and value:
            result.append(PropertyValue(name, value, position=len(result)))
    return tuple(result)


def listing_card(row: Mapping, previous: Mapping[str, Mapping] | None = None) -> ProductCard:
    url = product_url(row['product_url'])
    state = {'numeric': PriceState.NUMERIC_PUBLIC.value, 'price_on_request': PriceState.PRICE_ON_REQUEST.value,
             'missing': PriceState.MISSING.value, 'UNKNOWN': PriceState.AMBIGUOUS.value}.get(row['price_state'])
    if state is None:
        raise ValueError('unknown listing price state')
    price = parse_rub_money(row['price_text']) if state == PriceState.NUMERIC_PUBLIC.value else None
    if state == PriceState.NUMERIC_PUBLIC.value and (price is None or price <= 0):
        raise ValueError('numeric listing price is not a positive RUB amount')
    availability = {'InStock': 'in_stock', 'В наличии': 'in_stock', 'В пути': 'incoming',
                    'OutOfStock': 'out_of_stock', 'PreOrder': 'preorder', 'BackOrder': 'backorder',
                    'Нет в наличии': 'out_of_stock', 'Под заказ': 'preorder'}.get(row['availability'], 'unknown')
    sha = row['evidence_sha256'].split(' | ')
    if not sha or not all(re.fullmatch('[0-9a-f]{64}', digest) for digest in sha):
        raise ValueError('missing listing evidence hashes')
    return ProductCard(
        supplier_code='intervesp', external_id=resolve_identity(url, previous or {}), sku='', name=row['title'],
        requested_url=url, final_url=url, canonical_url=url, http_status=200, redirect_urls=(),
        price=price, old_price=None, currency='RUB' if price is not None else '',
        availability_raw=present(row['availability']), availability_normalized=availability, quantity=None,
        description_text='', description_html='', brand=present(row['brand']), manufacturer='',
        properties=properties(row), categories=tuple(CategoryNode(name, '', n) for n, name in
                    enumerate(present(row['category']).split(' | ')) if name),
        raw_data={'identity_contract': IDENTITY_CONTRACT, 'url_identity': url_identity(url),
                  'source_identity': {'site_internal_id': None, 'brand': present(row['brand']),
                                      'raw_model': present(row['model_candidate']), 'model_status': 'CANDIDATE',
                                      'manufacturer_article_explicit': False},
                  'listing_evidence': dict(row), 'capture_scope': 'listing',
                  'price_state': state, 'no_detail_get_performed': True},
        price_type='unknown', price_raw=present(row['price_text']), price_source='listing product card .price-block')


def replace_with_legacy_identity(card: ProductCard, records: Iterable[Mapping]) -> ProductCard:
    return replace(card, external_id=resolve_identity(card.canonical_url, identity_index(records)))


def persistence_identity(cursor, supplier_id: int, card: ProductCard) -> str:
    """Under the existing supplier row lock, reuse legacy PK/offer identity.

    Both listing and future detail writes take this path. No numeric ID migration,
    history rewrite, schema change or cross-supplier identity is permitted.
    """
    canonical = product_url(card.canonical_url)
    cursor.execute("SELECT external_id,source_url,canonical_url FROM source_products "
                   "WHERE supplier_id=%s AND (external_id=%s OR canonical_url=%s OR source_url=%s) FOR UPDATE",
                   (supplier_id, card.external_id, canonical, canonical))
    rows = cursor.fetchall()
    if len(rows) > 1:
        raise ValueError('Intervesp URL/external identity collision requires REVIEW')
    if not rows:
        return card.external_id
    external, source, stored_canonical = rows[0]
    aliases = {product_url(value) for value in (source, stored_canonical) if value}
    if canonical not in aliases:
        raise ValueError('Intervesp external identity points to another product URL')
    return str(external)


def bounded_rate_plan(robots_minimum: float, safe: float = 20, faster: float = 10) -> dict:
    """No rate below robots. A sample never grants unlimited faster crawling."""
    if robots_minimum < 0:
        raise ValueError('robots delay not verified')
    return {'safe_seconds': max(robots_minimum, safe), 'faster_seconds': max(robots_minimum, faster),
            'faster_distinct_permitted': max(robots_minimum, faster) < max(robots_minimum, safe),
            'parallel_requests': 1, 'stop_statuses': [403, 429], 'stop_on_challenge': True}


def listing_snapshot(row: Mapping) -> ReadOnlySupplierProduct:
    card = listing_card(row)
    extraction = PriceExtraction(PriceState(card.raw_data['price_state']), card.price, None,
        'RUB' if card.price is not None else None, card.price_raw, PriceSourceKind.PRODUCT_PRICE_DOM,
        card.price_source, ('intervesp_listing_evidence',), card.external_id, card.canonical_url,
        'listing product card ' + card.canonical_url)
    availability = {'in_stock': 'InStock', 'out_of_stock': 'OutOfStock', 'preorder': 'PreOrder',
                    'incoming': 'Incoming', 'backorder': 'BackOrder'}.get(card.availability_normalized)
    return ReadOnlySupplierProduct(supplier='intervesp', source_url=card.canonical_url, name=card.name,
        supplier_model=present(row['model_candidate']) or None, manufacturer_article=None,
        site_internal_id=None, availability=availability, price=extraction,
        technical_properties=tuple((p.name, p.value) for p in card.properties),
        source_category=present(row['category']), raw_supplier_model=present(row['model_candidate']),
        supplier_external_id=url_identity(card.canonical_url), source_brand=card.brand, listing_evidence=dict(row))


def retain_detail_content(listing_raw: dict, existing_raw: dict) -> dict:
    """A category capture must not erase previously fetched detail enrichment."""
    result = dict(existing_raw)
    result.update(listing_raw)
    old_content = existing_raw.get('source_content', {})
    new_content = listing_raw.get('source_content', {})
    content = dict(old_content)
    content.update({key: value for key, value in new_content.items() if value})
    if content: result['source_content'] = content
    old_site_id = existing_raw.get('source_identity', {}).get('site_internal_id')
    if old_site_id:
        result['source_identity'] = dict(result.get('source_identity', {}), site_internal_id=old_site_id)
    previous_capture = existing_raw.get('commercial_capture')
    if previous_capture and previous_capture != listing_raw.get('commercial_capture'):
        result['previous_detail_capture'] = existing_raw.get('previous_detail_capture', previous_capture)
    return result
