"""KAMI saved-card adapter. Supplier is not brand; group cards are not exact models.

No HTTP, PostgreSQL or canonical assignment occurs during parsing. Discovery
requires a separately verified full manifest rather than a three-path heuristic.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from universal_supplier.models import CategoryNode, FetchRecord, MediaItem, ProductCard, PropertyValue
from universal_supplier.characteristic_evidence import unit_from_label


def canonical_source_url(url: str) -> str:
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in {'stanki.ru', 'www.stanki.ru'}
            or p.username or p.password or p.port is not None or p.query):
        raise ValueError('unsafe KAMI product URL')
    path = re.sub('/+', '/', p.path)
    if not path.startswith('/catalog/'):
        raise ValueError('not a KAMI catalog URL')
    return urlunsplit(('https', 'www.stanki.ru', path.rstrip('/') + '/', '', ''))


def _text(node):
    return ' '.join(node.get_text(' ', strip=True).split()) if node else ''


def _rub(node):
    raw = _text(node)
    match = re.fullmatch(r'(?:от\s+)?([\d\s\u00a0]+(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?)', raw, re.I)
    if not match:
        return None
    value = Decimal(re.sub(r'\s', '', match.group(1)).replace(',', '.'))
    return value if value > 0 else None


def _availability(raw):
    text = raw.casefold()
    if 'нет в наличии' in text or 'отсутствует' in text:
        return 'out_of_stock'
    if 'в наличии' in text:
        return 'in_stock'
    if 'поступ' in text or 'в пути' in text:
        return 'incoming'
    if 'под заказ' in text:
        return 'backorder'
    return 'unknown'


def parse_listing(source: str, source_url: str) -> list[dict]:
    """Discovery evidence only; never silently promotes listing to full identity."""
    soup = BeautifulSoup(source, 'html.parser')
    result = []
    for node in soup.select('.productCard'):
        anchor = node.select_one('a.productCard__name[href]')
        if not anchor:
            continue
        url = canonical_source_url(urljoin(source_url, anchor['href']))
        result.append({'url': url, 'name': _text(anchor), 'site_internal_id': node.get('data-product-id', ''),
                       'has_alternative': anchor.get('data-has-alternative'),
                       'current_rub_raw': _text(node.select_one('.productCard__price:not(.productCard__price_old) [data-currency="rub"]')),
                       'availability_raw': _text(node.select_one('.productCard__status'))})
    return result


class KamiAdapter:
    code = 'kami'
    base_url = 'https://www.stanki.ru/'

    def discover(self, transport):
        # A sitemap URL's depth is insufficient to distinguish category, parent,
        # variant or stale card. Never allow generic runtime to call it complete.
        raise RuntimeError('KAMI_FULL_DISCOVERY_MANIFEST_REQUIRED')

    def parse_product(self, fetch: FetchRecord) -> ProductCard:
        if fetch.status_code != 200:
            raise ValueError('KAMI_NON_200_NOT_A_PRODUCT')
        soup = BeautifulSoup(fetch.text, 'html.parser')
        grids = soup.select('.productGrid[data-product-id]')
        if len(grids) != 1 or not grids[0].select_one('h1.productGrid__name'):
            raise ValueError('KAMI_EXACT_PRODUCT_SCOPE_MISSING')
        grid = grids[0]
        name = _text(grid.select_one('h1.productGrid__name'))
        canonical = soup.select_one('link[rel="canonical"]')
        url = canonical_source_url(urljoin(fetch.final_url, canonical['href']) if canonical else fetch.final_url)
        internal = grid.get('data-product-id', '').strip()
        if internal and not internal.isdigit():
            raise ValueError('KAMI_INVALID_INTERNAL_ID')
        # Visible codes can be multiple/group-scoped and their global uniqueness
        # has not yet been proven. The productGrid site ID agrees with listing ID.
        variant_ids = {n.get('data-sku', '').strip() for n in grid.select('.productTabs[data-sku]') if n.get('data-sku', '').strip()}
        if len(variant_ids) > 1 or any(not value.isdigit() for value in variant_ids):
            raise ValueError('KAMI_VARIANT_INTERNAL_ID_AMBIGUOUS')
        variant_id = next(iter(variant_ids), '')
        declared_canonical = url
        if variant_id:
            # Parent-canonical markup is an alias, not permission to collapse
            # two visible executions sharing a parent productGrid ID.
            url = canonical_source_url(fetch.final_url)
        external = ('variant:' + variant_id if variant_id else
                    'site:' + internal if internal else 'url:' + hashlib.sha256(url.encode()).hexdigest())
        brand_node = grid.select_one('.productGrid__brand')
        brand_img = brand_node.select_one('img[alt]') if brand_node else None
        brand = _text(brand_node) or (brand_img.get('alt', '').strip() if brand_img else '')
        supplier_code_raw = _text(grid.select_one('.productGrid__code'))
        codes = [v.strip() for v in supplier_code_raw.split(':', 1)[-1].split(',') if v.strip()] if supplier_code_raw else []
        table = grid.select_one('.compareTable')
        variants, matrix, properties, property_evidence = [], [], [], []
        headers = table.select('.thead .th:not(.th_large) .tcell') if table else []
        for header in headers:
            link = header.select_one('a[href]')
            # Selected variants render their own visible model as a span, not
            # an anchor. Do not read article/stock text as part of the model.
            visible_model = link or header.select_one(':scope > span')
            selected_url = url if variant_id and len(headers) == 1 else ''
            variants.append({'model': _text(visible_model), 'url': canonical_source_url(urljoin(url, link['href'])) if link else selected_url,
                             'supplier_article': _text(header.select_one('.tcell__article')), 'characteristics': []})
        for line in table.select('.tline') if table else []:
            group = line.find_parent(class_='tgroup')
            control = group.find_previous_sibling(class_='tcontrol') if group else None
            role = _text(control)
            label = _text(line.select_one(':scope > .td_large .tcell'))
            values = [_text(v) for v in line.select(':scope > .tw > .tr > .td > .tcell')]
            if not label or not values or label.upper() == 'ЦЕНА':
                continue
            unit = unit_from_label(label)  # Explicit label only; never infer from a number.
            matrix.append({'raw_label': label, 'values': values, 'role': role,
                           'unit': unit,
                           'position': len(matrix), 'provenance': '.compareTable .tline'})
            # Empty cells retained so no neighboring variant shifts into its place.
            if len(values) == len(variants):
                for variant, value in zip(variants, values):
                    variant['characteristics'].append({'raw_label': label, 'raw_value': value,
                                                       'role': role, 'unit': unit,
                                                       'position': len(variant['characteristics'])})
            if len(variants) == 1 and len(values) == 1:
                property_evidence.append({'raw_label': label, 'raw_value': values[0], 'role': role,
                                          'unit': unit,
                                          'position': len(properties), 'provenance': '.compareTable .tline'})
                properties.append(PropertyValue(label, values[0], unit, position=len(properties)))
        own_variant = [v for v in variants if v['url'] == url]
        if len(variants) > 1:
            card_kind = 'MULTI_VARIANT_WITH_EXPLICIT_IDENTITIES' if all(v['model'] and v['url'] for v in variants) else 'SERIES_OR_GROUP_CARD'
            model = ''
        elif len(variants) == 1 and variants[0]['model'] and variants[0]['model'].casefold() in name.casefold():
            card_kind, model = 'SINGLE_PRODUCT', variants[0]['model']
        else:
            card_kind, model = 'AMBIGUOUS', ''
        # A selected variant requires its own visible title/URL and exact table
        # column evidence. Parent series does not inherit a convenient column.
        if len(variants) > 1 and len(own_variant) == 1 and own_variant[0]['model'].casefold() in name.casefold():
            card_kind, model = 'SINGLE_PRODUCT', own_variant[0]['model']
            properties = [PropertyValue(v['raw_label'], v['raw_value'], v.get('unit', ''), position=i)
                          for i, v in enumerate(own_variant[0]['characteristics'])]
            property_evidence = [dict(v, provenance='.compareTable own execution column')
                                 for v in own_variant[0]['characteristics']]
        cost = grid.select_one('.productCost__price:not(.productCost__price_old)')
        current = cost.select_one('[data-currency="rub"]') if cost else None
        price_raw = _text(current) or _text(cost)
        price = _rub(current)
        request = bool(re.search(r'цен[ауы]\s+по\s+запросу|запросить\s+цену|узнать\s+цену', price_raw, re.I))
        price_state = 'price_on_request' if request else ('numeric_public' if price is not None else 'missing')
        price_basis = 'from_price' if re.match(r'^от\b', price_raw, re.I) else 'exact_price'
        old = grid.select_one('.productCost__price_old [data-currency="rub"]')
        availability_raw = _text(grid.select_one('.productCost__status'))
        category_node = grid.select_one('.productGrid__type a[href]')
        categories = (CategoryNode(_text(category_node), urljoin(url, category_node['href']), 0),) if category_node else ()
        media = []
        for link in grid.select('a.productPreview__picture[href]'):
            img = link.select_one('img')
            media.append(MediaItem('image', urljoin(url, link['href']), img.get('alt', '') if img else '', len(media), not media))
        document_urls = set()
        for link in grid.select('.productTabs__content a[href]'):
            target = urljoin(url, link['href'])
            parsed = urlsplit(target)
            if (parsed.scheme in {'http', 'https'} and not parsed.username and not parsed.password
                    and re.search(r'\.(?:pdf|docx?|xlsx?|dwg|dxf|step|stp|zip)$', parsed.path, re.I)
                    and target not in document_urls):
                document_urls.add(target)
                # Observe the card's own link only; no PDF/download GET or
                # assumption that the linked file is a verified manual.
                media.append(MediaItem('document', target, _text(link), len(media), False))
        detail = grid.select_one('[data-tab-item="detail"]')
        lazy = [n.get('data-tab-item') for n in grid.select('.productTabs__content.loading')]
        description = _text(detail) if detail and 'loading' not in detail.get('class', []) else ''
        raw = {'adapter': 'kami-v1', 'site_internal_id': internal, 'site_variant_id': variant_id,
               'declared_canonical_url': declared_canonical,
               'external_identity_basis': 'productTabs_variant_internal_id' if variant_id else ('productGrid_internal_id' if internal else 'canonical_url'),
               'visible_supplier_codes': codes, 'supplier_article': codes[0] if len(codes) == 1 else '',
               'manufacturer_article': '', 'manufacturer_state': 'NOT_FOUND', 'card_kind': card_kind,
               'full_model': model, 'variant_evidence': variants, 'comparison_matrix': matrix,
               'property_evidence': property_evidence,
               'identity_status': 'SOURCE_MODEL_OBSERVED' if model else 'REVIEW_VARIANT_OR_MODEL_IDENTITY',
               'price_state': price_state, 'price_basis': price_basis, 'old_price_raw': _text(old),
               'lazy_content_sections': lazy, 'description_state': 'UNKNOWN' if 'detail' in lazy else ('OBSERVED' if description else 'NOT_FOUND'),
               'documents_state': 'OBSERVED' if document_urls else ('UNKNOWN' if lazy else 'NOT_FOUND'),
               'documents_provenance': 'own_productTabs_anchor_urls_only_not_download_verified',
               'image_url_role': 'source_gallery_link_not_proven_original',
               'canonical_assignment': None, 'quantity_state': 'NOT_FOUND'}
        return ProductCard(self.code, external, codes[0] if len(codes) == 1 else '', name,
                           fetch.requested_url, fetch.final_url, url, 200, fetch.redirects,
                           None if request else price, _rub(old), 'RUB' if price is not None else '',
                           availability_raw, _availability(availability_raw), None, description,
                           str(detail) if description else '', brand=brand, properties=tuple(properties),
                           media=tuple(media), categories=categories, raw_data=raw,
                           price_type='unknown', price_raw=price_raw, price_source='.productCost__price [data-currency=rub]')
