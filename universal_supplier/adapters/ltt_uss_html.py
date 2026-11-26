"""Small DOM helpers for source-scoped frozen LTT/USS evidence."""
from decimal import Decimal
from dataclasses import asdict
import re
from bs4 import BeautifulSoup
from universal_supplier.commercial import PriceExtraction, PriceSourceKind, PriceState


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split()) if node is not None else ''


def soup(source):
    return BeautifulSoup(source, 'html.parser')


def price_value(raw, *, identity, url, path, currency='RUB'):
    """Parse one price cell, rejecting prose and multiple amounts."""
    normalized = ' '.join(raw.split())
    lower = normalized.lower()
    state, value, diagnostics = PriceState.MISSING, None, ()
    if any(word in lower for word in ('запрос', 'уточняйте')):
        state = PriceState.PRICE_ON_REQUEST
    elif normalized:
        # A unit is source evidence only. No package-size or unit arithmetic.
        amount = r'(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)([.,]\d{1,2})?'
        unit = re.fullmatch(amount + r'\s*(?:руб\.?|₽)\s*/\s*(бухта|шт\.?|комплект|упаковка|пара|м|метр|кг|литр|л)', normalized, re.I)
        match = unit or re.fullmatch(amount + r'\s*(?:руб\.?|₽)?', normalized, re.I)
        if match:
            value = Decimal(re.sub(r'\s', '', match.group(1)) + (match.group(2) or '').replace(',', '.'))
            state = PriceState.NUMERIC_PUBLIC if value > 0 else PriceState.ZERO_INVALID
            if unit:
                diagnostics = ('sales_unit_raw:' + unit.group(3),)
                currency = 'RUB'  # Explicit currency in the source string.
            if value <= 0:
                value = None
        else:
            usd = re.fullmatch(amount + r'\s+USD', normalized, re.I)
            if usd:
                value = Decimal(re.sub(r'\s', '', usd.group(1)) + (usd.group(2) or '').replace(',', '.'))
                state = PriceState.NUMERIC_PUBLIC if value > 0 else PriceState.ZERO_INVALID
                currency = 'USD'
                if value <= 0:
                    value = None
            else:
                state = PriceState.PARSE_ERROR
                diagnostics = ('unsupported_price_grammar',)
    return PriceExtraction(state, value, None, currency if value is not None else None,
                           raw, PriceSourceKind.PRODUCT_PRICE_DOM, path, diagnostics, identity, url, None)


def data_rows(root):
    for row in root.select('tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        if cells and not any(cell.find('table') for cell in cells):
            yield tuple(text(cell) for cell in cells)


def source_model(name, properties=()):
    """Explicit model row first; title candidates exclude dimensional units."""
    explicit = {value for key, value in properties if key.strip().lower() in {'модель', 'model'}}
    if len(explicit) == 1:
        return next(iter(explicit))
    if len(explicit) > 1:
        return None
    title = re.sub(r'\([^)]*\)', ' ', name)
    matches = re.findall(r'(?<!\w)[A-ZА-ЯЁ]{1,12}(?:\s+\d|[\d/-])[A-ZА-ЯЁa-zа-яё0-9*+/.\-]*(?:\s+(?:PRO|NEW|PLUS|V))?', title)
    if matches:
        return matches[-1].strip('., ')
    # Pure numeric models are valid only when a model row explicitly says so.
    return None


def source_evidence(name, category, properties, availability_raw, brand='', *, own_description=''):
    """Retain raw source facts beside a transparent, non-matching kind claim."""
    from sterbrust_matching.product_identity import classify_product_kind
    kind = asdict(classify_product_kind(name, category, own_description=own_description))
    head = name.lower().split(' для ')[0]
    spare = re.search(r'\b(?:ролик\w*|запчаст\w*|ремень\w*)', head)
    machine_head = re.search(r'\bстан(?:ок|ки|ка|ком|ке)\b', head)
    # Roller/carriage details after an owned complete-machine head do not
    # change the sold object. A roller before a later host still remains spare.
    owned_machine_before_spare = (kind['source'] == 'machine_name' and machine_head
                                  and spare and machine_head.start() < spare.start())
    if kind['source'] == 'owned_complete_processing_head' and spare:
        owned_head_start = head.find(kind['matched_alias'].lower())
        owned_machine_before_spare = 0 <= owned_head_start < spare.start()
    owned_feeder = (kind['product_kind'] == 'power_feeder'
                    and kind['source'] == 'sold_object_head')
    if spare and not owned_machine_before_spare and not owned_feeder:
        kind = dict(product_kind='spare', matched_alias=head, source='sold_title', confidence='HIGH')
    elif re.search(r'\b(?:фреза\w*|сверло\w*|резец\w*|патрон\w*)', head) and not re.search(r'\bстанок\b', head):
        kind = dict(product_kind='tooling', matched_alias=head, source='sold_title', confidence='HIGH')
    manufacturer = next((value for key,value in properties if key.lower() in {'производитель','manufacturer'}), None)
    return dict(source_title=name, source_category=category, availability_raw=availability_raw,
                source_brand=brand or None, manufacturer_raw=manufacturer,
                model_raw=source_model(name, properties), product_kind=kind,
                kind_evidence_class='INFERENCE_FROM_SOURCE_TEXT', identity_authority='source_internal_id_not_model_or_kind')
