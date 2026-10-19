"""USS-Stanko (uss-stanko.com) detail parser for the static cp1251 catalog.

The site is a hand-maintained static HTML catalog: no server-side product IDs,
``windows-1251`` encoding and per-section heading classes.  Identity is the
canonical URL path (explicitly diagnosed as a static-site path identity, never
presented as a manufacturer article).  Prices/stock live in a specification
table whose ``Цена`` row is either a numeric rouble amount or ``по запросу``;
MRCM family pages add a multi-article variant table preserved as evidence.
Discontinued wording overrides the stock row.
"""
from __future__ import annotations

from decimal import Decimal
from html import unescape
from functools import lru_cache
import re
from urllib.parse import urlparse, urljoin
from .ltt_uss_html import soup, text, price_value, data_rows, source_model, source_evidence

from universal_supplier.commercial import (
    PriceExtraction, PriceSourceKind, PriceState, ReadOnlySupplierProduct,
)

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1\b[^>]*class=[\"'][^\"']*zagolovok[^\"']*[\"'][^>]*>(.*?)</h1\s*>", re.I | re.S)
_H1_ANY_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
_CANONICAL_RE = re.compile(r"<link\s+rel=[\"']canonical[\"']\s+href=[\"']([^\"']+)[\"']", re.I)
_PRICE_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td>\s*(Цена[^<]{0,80}?)\s*</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.I | re.S)
_AVAIL_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td>\s*Наличие\s*</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.I | re.S)
_VARIANT_ANCHOR_RE = re.compile(r"<a\s+name=[\"']art([\w/.\-]+)[\"']", re.I)
_DISCONTINUED_RE = re.compile(r"снят[оа]?\s+с\s+производства", re.I)
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.I | re.S)
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9*+/.,\-]+$")

SECTION_BRANDS = {"dmtg": "DMTG", "mrcm": "MRCM", "unikraft": "Unicraft"}
BASE_URL = "https://uss-stanko.com/"


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


def _model_from_title(name: str) -> str | None:
    for token in reversed(re.split(r"[\s/]+", name.strip())):
        cleaned = token.strip(".,:;()")
        if cleaned and any(ch.isdigit() for ch in cleaned) and _MODEL_TOKEN_RE.match(cleaned):
            return cleaned
    return None


def _canonical(source: str, source_url: str | None) -> str:
    link = soup(source).select_one('link[rel="canonical"]')
    url = urljoin(source_url or BASE_URL, link.get('href', '') if link else source_url or BASE_URL)
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in {'uss-stanko.com', 'www.uss-stanko.com'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('unsafe USS canonical URL')
    return url


def _own_h2(source: str, source_url: str | None) -> str:
    """Accept only the verified static own-card layout, never a first-h2 guess."""
    dom = soup(source)
    headings = dom.select('h2.zagolovok_u')
    canonicals = dom.select('link[rel="canonical"]')
    tables = dom.select('table.data')
    if (dom.find('h1') or len(headings) != 1 or len(tables) != 1
            or len(canonicals) != 1 or not source_url
            or urljoin(source_url, canonicals[0].get('href', '')) != source_url):
        return ''
    heading, table = headings[0], tables[0]
    block = heading.parent
    if (block.name != 'div' or block.get('id') not in {'div_print', 'div_body'}
            or table.parent is not block or len(block.select('h1,h2')) != 1
            or any(a.name in {'nav', 'header', 'footer', 'aside'} or a.get('role') == 'navigation'
                   for a in heading.parents)
            or list(block.children).index(heading) >= list(block.children).index(table)):
        return ''
    name = text(heading)
    if re.search(r'\b(?:характеристик\w*|новост\w*|каталог\w*|категори\w*|серия|серии|модели|ассортимент|навигаци\w*)\b', name, re.I):
        return ''
    rows = tuple(data_rows(table))
    groups = [[row for row in rows if row and predicate(row[0].lower())]
              for predicate in (lambda k: k == 'модель',
                                lambda k: k in {'номер арт.', 'артикул'},
                                lambda k: k.startswith('цена'), lambda k: k == 'наличие')]
    if any(len(group) != 1 or len(group[0]) != 2 or not group[0][1] for group in groups):
        return ''
    model, article = groups[0][0][1], groups[1][0][1]
    # Spaces/punctuation vary (TR3 versus TR 3), but model prefixes must not match
    # a different product (ZX900 versus ZX9000).
    pattern = r'(?<!\w)' + r'[\W_]*'.join(re.escape(c) for c in model if c.isalnum()) + r'(?!\w)'
    if not re.search(pattern, name, re.I):
        return ''
    normalize = lambda value: ''.join(c for c in value.casefold() if c.isalnum())
    anchors = block.select('a[name^="art"]')
    controls = block.select('a.bnt_to_cart,a.bnt_zak')
    if (len(anchors) != 1 or anchors[0] not in table.descendants
            or normalize(anchors[0].get('name', '')) != 'art' + normalize(article)
            or len(controls) != 1 or controls[0] not in table.descendants
            or not re.match(r'\s*(?:add_to_cart|zak)\s*\(', controls[0].get('onclick', ''))):
        return ''
    # A second independently purchasable table, including classless tables,
    # makes this fallback uncertain even when the original parser ignored it.
    for other in block.find_all('table'):
        if other is table or other.find('table'):
            continue
        other_rows = tuple(data_rows(other))
        if any(row and (row[0].lower() == 'модель' or row[0].lower().startswith('цена')) for row in other_rows):
            return ''
    return name


def _commercial_rows(source):
    column = _own_column_rows(source)
    if column:
        return column
    dom = soup(source)
    legacy = dom.select('table.data')
    if legacy:
        return tuple(row for table in legacy for row in data_rows(table))
    # Some own specification tables have no CSS class. Never promote layout,
    # family/variant matrices or an arbitrary first table to a single product.
    candidates = []
    for table in dom.find_all('table'):
        if table.find('table'):
            continue
        rows = tuple(data_rows(table))
        groups = [[row for row in rows if row and predicate(row[0].lower())]
                  for predicate in (lambda k:k == 'модель', lambda k:k.startswith('цена'),
                                    lambda k:k in {'номер арт.', 'артикул'}, lambda k:k == 'наличие')]
        models, prices, articles, stocks = groups
        if prices and articles and stocks:
            candidates.append((table, rows, groups))
    if len(candidates) != 1 or len(dom.find_all('h1')) != 1:
        return ()
    table, rows, groups = candidates[0]
    if any(len(group) != 1 or len(group[0]) != 2 or not group[0][1] for group in groups):
        return ()
    model, _, article, _ = (group[0][1] for group in groups)
    normalized = lambda value: re.sub(r'[^\w]', '', value.casefold())
    if normalized(model) not in normalized(text(dom.h1)):
        return ()
    if not any(normalized(a.get('name', '')) == 'art' + normalized(article)
               for a in table.select('a[name]')):
        return ()
    return rows


def _norm(value):
    return ''.join(c for c in value.casefold() if c.isalnum())


def _purchase_controls(node):
    return [a for a in node.select('a.bnt_to_cart,a.bnt_zak')
            if re.match(r'\s*(?:add_to_cart|zak)\s*\(', a.get('onclick', ''))]


def _safe_content(node):
    return not any(a.name in {'nav','header','footer','aside'} or a.get('role') == 'navigation'
                   for a in [node, *node.parents] if getattr(a, 'attrs', None) is not None)


def _column_items(table):
    """One explicit header, rectangular cells, row-local article and purchase proof."""
    rows = [tr for tr in table.find_all('tr') if tr.find_parent('table') is table]
    if not rows or table.find('table'):
        return None
    cells = [tr.find_all(['td','th'], recursive=False) for tr in rows]
    values = [[text(c) for c in cs] for cs in cells]
    headers = []
    for ri, vals in enumerate(values):
        matches = [[i for i,v in enumerate(vals) if re.search(pattern,v,re.I)] for pattern in
                   (r'^(?:номер\s*арт\.?|артикул)$',r'^цена',r'^наличие$')]
        if all(len(m)==1 for m in matches) and len({m[0] for m in matches})==3:
            headers.append((ri,[m[0] for m in matches]))
    if len(headers)!=1 or headers[0][0]!=0:
        return None
    _, (ai,pi,si) = headers[0]
    if 0 in (ai,pi,si):
        return None
    items=[]
    for tr, cs, vals in zip(rows[1:],cells[1:],values[1:]):
        if len(vals)!=len(values[0]) or any(c.get('colspan','1')!='1' or c.get('rowspan','1')!='1' for c in cs):
            return None
        article=vals[ai];anchors=tr.select('a[name^="art"]');controls=_purchase_controls(tr)
        if (not vals[0] or not article or len(anchors)!=1 or len(controls)!=1
                or _norm(anchors[0].get('name',''))!='art'+_norm(article)):
            return None
        items.append(dict(name=vals[0],article=article,price=vals[pi],stock=vals[si],
                          price_label=values[0][pi],node=tr))
    return items or None


def _singular_heading(heading):
    name=text(heading)
    # A family URL cannot acquire the identity of its sole current child H2.
    return bool(name) and not re.search(
        r'\b(?:каталог\w*|категори\w*|серия|серии|модели|ассортимент|принадлежности|'
        r'пистолеты|дрели|винтоверты|машины|граверы|станки|светильники)\b',
        name.split(' для ')[0].split(' под ')[0],re.I)


@lru_cache(maxsize=8)
def _own_column_rows(source):
    """Fail-closed fallback for an own single item, never an arbitrary first row."""
    dom=soup(source);h1s=dom.find_all('h1')
    if len(h1s)!=1 or not _safe_content(h1s[0]) or not _singular_heading(h1s[0]):
        return ()
    canonicals=dom.select('link[rel="canonical"]')
    if len(canonicals)!=1:
        return ()
    tables=[t for t in dom.find_all('table') if not t.find('table')
            and (t.select('a[name^="art"]') or _purchase_controls(t))]
    if len(tables)!=1 or not _safe_content(tables[0]):
        return ()
    table=tables[0];items=_column_items(table)
    if not items or len(_purchase_controls(dom))!=len(items):
        return ()
    h1=h1s[0];headings=dom.find_all(['h1','h2'])
    selected=None
    if len(items)==1 and len(headings)==1:
        candidate=items[0]
        # Same sold-kind noun plus all explicit model tokens; dimensional suffixes
        # and descriptive adjectives need not be byte-identical.
        if (text(h1).split()[0].casefold()!=candidate['name'].split()[0].casefold()
                or (source_model(text(h1)) and _norm(source_model(text(h1))) not in _norm(candidate['name']))):
            return ()
        selected=candidate
    elif len(headings)==2 and headings[0] is h1 and headings[1].name=='h2':
        own=headings[1]
        matches=[item for item in items if _norm(item['name'])==_norm(text(own))]
        if len(matches)!=1 or table.find_previous(['h1','h2']) is not own:
            return ()
        selected=matches[0]
        # Shared-table exception requires explicit dependent accessory wording,
        # not a second model, arbitrary product or another main-row candidate.
        others=[item for item in items if item is not selected]
        if not others or any(not re.match(r'^(?:Уплотнение|Запасная часть|Прокладка)\s+для\s+',item['name'],re.I) for item in others):
            return ()
        main_words={w.casefold() for w in re.findall(r'\w+',text(h1))}
        if selected['name'].split()[0].casefold() not in main_words:
            return ()
    if selected is None:
        return ()
    # Prove locality in the content body, with headings preceding the table.
    block=table.find_parent('div',id=re.compile(r'^div_(?:body|print)$'))
    if block is None or h1 not in block.descendants or table.find_previous('h1') is not h1:
        return ()
    result=[('Наименование',selected['name']),('Номер арт.',selected['article']),
            (selected['price_label'],selected['price']),('Наличие',selected['stock'])]
    explicit_model=source_model(text(headings[-1]))
    if explicit_model:
        result.insert(0,('Модель',explicit_model))
    return tuple(result)


def _local_own_h2(source, source_url):
    """Retain original guards on a proven primary section before accessories."""
    dom=soup(source);headings=dom.select('h2.zagolovok_u')
    if dom.find('h1') or len(headings)!=1 or len(dom.find_all('h2'))!=1:
        return ''
    heading=headings[0];block=heading.parent
    if block.name!='div' or block.get('id') not in {'div_print','div_body'} or not _safe_content(block):
        return ''
    markers=[n for n in block.find_all('div',recursive=False)
             if any(c.startswith('zag_dop') for c in n.get('class',[]))
             and re.fullmatch(r'Рекомендуемые дополнительные принадлежности\s*:',text(n),re.I)]
    if len(markers)!=1:
        return ''
    marker=markers[0];siblings=list(block.children)
    if siblings.index(heading)>=siblings.index(marker):
        return ''
    tables=dom.select('table.data')
    primary=[t for t in tables if t.parent is block and siblings.index(t)<siblings.index(marker)]
    extras=[t for t in tables if t.parent is block and siblings.index(t)>siblings.index(marker)]
    if len(primary)!=1 or not extras or len(primary)+len(extras)!=len(tables):
        return ''
    # Every remaining purchase is inside an explicitly additional column table.
    extra_items=[_column_items(t) for t in extras]
    if any(not items for items in extra_items):
        return ''
    main_articles=primary[0].select('a[name^="art"]')
    if len(main_articles)!=1:
        return ''
    extra_articles=[a.get('name') for t in extras for a in t.select('a[name^="art"]')]
    if main_articles[0].get('name') in extra_articles or len(set(extra_articles))!=len(extra_articles):
        return ''
    if len(_purchase_controls(dom))!=len(_purchase_controls(primary[0]))+sum(len(_purchase_controls(t)) for t in extras):
        return ''
    for node in list(marker.next_siblings):
        node.extract()
    marker.extract()
    return _own_h2(str(dom),source_url)


def _multi_variant(source):
    rows = _commercial_rows(source)
    models = [row for row in rows if row and row[0].lower() == 'модель']
    prices = [row for row in rows if row and row[0].lower().startswith('цена')]
    return len(models) > 1 or len(prices) > 1 or any(len(row) > 2 for row in models + prices)


def _price(source: str, identity: str, source_url: str | None) -> PriceExtraction:
    rows = [row for row in _commercial_rows(source) if row and row[0].lower().startswith('цена')]
    if rows and len(rows) == 1 and len(rows[0]) == 2:
        path = 'table.data:Цена' if soup(source).select('table.data') else 'semantic-commercial-table:Цена'
        return price_value(rows[0][1], identity=identity, url=source_url, path=path,
                           currency='RUB' if 'руб' in rows[0][0].lower() else None)
    if rows:
        return PriceExtraction(PriceState.AMBIGUOUS, None, None, None, repr(rows),
                               PriceSourceKind.PRODUCT_PRICE_DOM, 'table.data:Цена',
                               ('MULTI_VARIANT',), identity, source_url, None)
    match = None
    if not match:
        return PriceExtraction(
            state=PriceState.MISSING, current_price=None, old_price=None, currency=None,
            raw_text="", source_kind=PriceSourceKind.NONE, source_path=None,
            diagnostics=("uss_price_row_not_found",), product_identity=identity,
            source_url=source_url, context_path=None)
    label, raw_value = _text(match.group(1)), _text(match.group(2))
    value_lower = raw_value.lower()
    if "запрос" in value_lower or "уточняйте" in value_lower:
        return PriceExtraction(
            state=PriceState.PRICE_ON_REQUEST, current_price=None, old_price=None,
            currency=None, raw_text=raw_value, source_kind=PriceSourceKind.PRODUCT_PRICE_DOM,
            source_path="spec-table:Цена", diagnostics=(), product_identity=identity,
            source_url=source_url, context_path=None)
    digits = re.sub(r"[^\d]", "", raw_value.split(",")[0] if "," in raw_value and not re.search(r"\d{3},\d", raw_value) else raw_value)
    if not digits:
        return PriceExtraction(
            state=PriceState.MISSING, current_price=None, old_price=None, currency=None,
            raw_text=raw_value, source_kind=PriceSourceKind.PRODUCT_PRICE_DOM,
            source_path="spec-table:Цена", diagnostics=("uss_price_value_empty",),
            product_identity=identity, source_url=source_url, context_path=None)
    try:
        value = Decimal(digits)
    except Exception:
        return PriceExtraction(
            state=PriceState.PARSE_ERROR, current_price=None, old_price=None, currency=None,
            raw_text=raw_value, source_kind=PriceSourceKind.PRODUCT_PRICE_DOM,
            source_path="spec-table:Цена", diagnostics=("uss_price_unparseable",),
            product_identity=identity, source_url=source_url, context_path=None)
    state = PriceState.NUMERIC_PUBLIC if value > 0 else PriceState.ZERO_INVALID
    currency = "RUB" if "руб" in label.lower() else None
    return PriceExtraction(
        state=state, current_price=value if value > 0 else None, old_price=None,
        currency=currency, raw_text=f"{label}: {raw_value}",
        source_kind=PriceSourceKind.PRODUCT_PRICE_DOM, source_path="spec-table:Цена",
        diagnostics=(), product_identity=identity, source_url=source_url, context_path=None)


def _availability(source: str) -> tuple[str | None, list[str]]:
    diagnostics: list[str] = []
    rows = [row for row in _commercial_rows(source) if row and row[0].lower() == 'наличие']
    if _DISCONTINUED_RE.search(source):
        diagnostics.append("uss_discontinued_wording_overrides_stock")
        return "OutOfStock", diagnostics
    if len(rows) != 1 or len(rows[0]) != 2:
        return None, diagnostics + ["uss_availability_row_not_found"]
    wording = rows[0][1].lower()
    if "нет" in wording or "not" in wording:
        return "OutOfStock", diagnostics
    if "под заказ" in wording or "заказ" in wording:
        return "PreOrder", diagnostics
    if "в наличии" in wording or "склад" in wording:
        return "InStock", diagnostics
    return None, diagnostics + [f"uss_unknown_availability_wording:{wording[:60]}"]



def _avail_row_for_anchor(source: str, anchor: str) -> str:
    position = source.find(f'art{anchor}')
    window = source[position:position + 1200] if position != -1 else ""
    stock = re.search(r"(?:в наличии|нет в наличии|под заказ|под заказом)", window, re.I)
    price = re.search(r"(\d[\d\s\u00a0]*)", window)
    parts = []
    if stock:
        parts.append(_text(stock.group(0)))
    if price:
        parts.append(f"{_text(price.group(1))} руб.")
    return "; ".join(parts)


def _properties(source: str) -> tuple[tuple[str, str], ...]:
    return tuple((row[0], row[1]) for row in _commercial_rows(source)
                 if len(row) == 2 and all(row) and not row[0].lower().startswith(('цена', 'наличие')))
    # Legacy regex extraction below is intentionally unreachable pending removal.
    properties = []
    for key, value in _ROW_RE.findall(source):
        name, item = _text(key), _text(value)
        if not name or not item:
            continue
        lowered = name.lower()
        if lowered.startswith("цена") or lowered.startswith("наличие"):
            continue
        properties.append((name, item))
        if len(properties) >= 120:
            break
    anchors = _VARIANT_ANCHOR_RE.findall(source)
    for anchor in anchors[:20]:
        properties.append((f"Вариант (артикул {anchor})", _avail_row_for_anchor(source, anchor) or "см. таблицу вариантов"))
    return tuple(properties)


def _brand_and_category(url: str) -> tuple[str, str, str | None]:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    brand = SECTION_BRANDS.get(parts[0], "") if parts else ""
    if len(parts) >= 3:
        category = f"{parts[0]}/{parts[-2]}"
        category_url = f"{parsed.scheme}://{parsed.hostname}/{'/'.join(parts[:-1])}/"
    elif parts:
        category = parts[0]
        category_url = f"{parsed.scheme}://{parsed.hostname}/{parts[0]}/"
    else:
        category, category_url = "", None
    return brand, category, category_url


def parse_uss_stanko_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse one saved USS-Stanko detail page (already charset-decoded HTML)."""
    diagnostics: list[str] = ["uss_stanko_static_parser"]
    url = _canonical(source, source_url)
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    if path.endswith("index.htm"):
        path = path[: -len("index.htm")]
    site_id = f"uss:{path.rstrip('/')}" if path else None
    diagnostics.append("identity_is_canonical_path_static_site")

    h1 = _H1_RE.search(source) or _H1_ANY_RE.search(source)
    name = _text(h1.group(1)) if h1 else ""
    own_h2 = (_own_h2(source, source_url) or _local_own_h2(source, source_url)) if not h1 else ''
    if own_h2:
        name = own_h2
        diagnostics.append('uss_own_h2_verified')
    if not name:
        diagnostics.append("uss_h1_missing")

    model = _model_from_title(name)
    identity = model or name or site_id or "unknown"
    price = _price(source, identity, url)
    availability, avail_diagnostics = _availability(source)
    if _multi_variant(source):
        diagnostics.append('MULTI_VARIANT')
        price = PriceExtraction(PriceState.AMBIGUOUS, None, None, None, price.raw_text,
                                PriceSourceKind.PRODUCT_PRICE_DOM, 'table.data',
                                ('MULTI_VARIANT',), identity, url, None)
        availability = None
        site_id = None
    diagnostics.extend(avail_diagnostics)
    properties = _properties(source)
    model = source_model(name,properties)
    if not properties:
        diagnostics.append("uss_spec_table_not_found")
    if model is None:
        diagnostics.append("uss_model_token_not_in_title")
    brand, category, category_url = _brand_and_category(url)
    availability_raw = ' | '.join(' | '.join(row[1:]) for row in _commercial_rows(source) if row[0].lower()=='наличие')
    # Preserve the established table.data payload contract. Newly covered own
    # tables expose their explicit article alongside their canonical-path ID.
    article = None
    if own_h2 or _own_column_rows(source) or not soup(source).select('table.data'):
        article = next((value for key, value in properties if key.lower() in {'номер арт.', 'артикул'}), None)

    return ReadOnlySupplierProduct(
        supplier="uss_stanko", source_url=url, name=name, supplier_model=model,
        manufacturer_article=article, site_internal_id=site_id,
        availability=availability, price=price, diagnostics=tuple(diagnostics),
        description_text="", technical_properties=properties, source_images=(),
        source_category=category, source_category_url=category_url,
        raw_supplier_model=model, source_brand=brand,
        enrichment_evidence=source_evidence(name,category,properties,availability_raw,brand),
    )
