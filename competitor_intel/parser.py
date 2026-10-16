"""Evidence-led DOM adapters; deliberately no unscoped numeric scraping."""
import copy
import re
from datetime import timedelta
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .model import SOURCES, Page, Item, canonical, text, money, price_type, parse_date, campaign_dates, inclusion

def clean(node):
    clone = copy.copy(node)
    for el in clone.select('script,style,template,noscript,header,footer,nav,form,.breadcrumbs,.breadcrumb,.track__munu,.card-product__btns-container'):
        el.decompose()
    return clone

# lasercut.ru (MODX + miniShop2): machine catalog categories only; accessories
# (komplektuyushhie) and filter facets never produce machine-level facts.
LASERCUT_MACHINE_CATEGORIES = (
    'lazernyie-stanki', 'lazernyie-stanki-po-metallu', 'lazernye-truborezy', 'lazernyie-markeryi',
    'frezernyie-stanki', 'stanki-dlya-proizvodstva-mebeli', 'lazernyij-svarochnyij-apparat',
    'lazernye-ochistki', 'gibochnye-stanki-listogiby', 'vertikalnye-frezernye-stanki-po-metallu',
    'tokarnye-stanki', 'lentochnopilnye-stanki', 'mnogofunktsionalnye-raskroechnye-kompleksy',
)

def lasercut_path_kind(path):
    seg = [s for s in path.split('/') if s]
    if not seg: return 'HOMEPAGE_BLOCK'
    if seg[0] == 'vse-aktsii': return 'PROMOTION'
    if seg[0] == 'novosti': return 'NEWS'
    if seg[0] == 'blog':
        # Blog rubric categories are navigation; only articles are content.
        if len(seg) > 1 and (seg[1].startswith('stati-') or seg[1] in ('prochee', 'programmnoe-obespechenie')):
            return 'OTHER'
        return 'NEWS'
    if seg[0] == 'katalog' and len(seg) > 1 and seg[1] in LASERCUT_MACHINE_CATEGORIES:
        return 'PRODUCT_DETAIL' if len(seg) > 2 else 'SALE_PAGE'
    return 'OTHER'

def content_type(url):
    path = urlsplit(url).path
    if path == '/': return 'HOMEPAGE_BLOCK'
    if path.startswith('/news/') and path != '/news/': return 'NEWS'
    if path.startswith('/sale/') or path.startswith('/discount/'): return 'SALE_PAGE'
    if path == '/top_action/': return 'CAMPAIGN'
    if path.startswith(('/promo/', '/c_actions/')): return 'PROMOTION'
    if path.startswith(('/vse-aktsii/', '/novosti/', '/blog/', '/katalog/')): return lasercut_path_kind(path)
    return 'OTHER'

def card_item(card, source, base, campaign, tab=None):
    mm = source == 'metalmaster'
    lc = source == 'lasercut_ru'
    title = card.select_one('.card-item__title' if lc else '.card-product__title' if mm else '.snippet__title,.alt-snippet__title')
    if not title or not title.get_text(strip=True):
        return None
    name = text(title.get_text(' ', strip=True))
    link = card.select_one('a.card-item__link[href]' if lc else 'a.card-product__link[href]' if mm else 'a.snippet__title[href],a.alt-snippet__title[href]')
    if not link and title.name == 'a': link = title
    if not link: link = title.find_parent('a', href=True)
    if not link: link = card.select_one('a[href]')
    url = canonical(link['href'], base) if link and link['href'].startswith(('http', '/')) else None
    price = card.select_one('.card-item__price' if lc else '.card-product__price' if mm else '.snippet__price,.alt-snippet__price')
    old_el = price.select_one('del') if price else None
    if lc:
        old_el = card.select_one('.card-item__price-old')
    elif not mm:
        old_el = card.select_one('.snippet-price__old,.snippet__price-old, .snippet__old-price, .snippet__price del')
    old = money(text(old_el.get_text(' ', strip=True))) if old_el else None
    current = None
    raw_label = text(price.get_text(' ', strip=True)) if price else ''
    if price:
        p = copy.copy(price)
        for el in p.select('del,s,.snippet-price__old,.snippet__price-old,.snippet__old-price,.card-item__price-old,.card-item__price-usually'):
            el.decompose()
        # Kuvalda current price is explicitly separated from promotional labels.
        current_el = p.select_one('.snippet-price__value,.snippet__price-value, .snippet__price-current') if not mm else None
        current = money(text((current_el or p).get_text(' ', strip=True)))
    if lc and current is None:
        # Live run-50 evidence (lasercut promo cards): both prices are nested inside
        # .card-item__price as <s class="card-item__price-old">base</s> <span>от</span>
        # <span class="card-item__price-usually">X ₽</span>. FROM_PRICE convention
        # (same as amur_stan): base stays old_price, the "от X" value becomes
        # new_price, price_type stays FROM_PRICE; exact comparability remains
        # guarded because PUBLIC_TYPES excludes FROM_PRICE.
        usually = card.select_one('.card-item__price-usually')
        if usually is not None and price is not None and re.search(r'\bот\b', raw_label):
            current = money(text(usually.get_text(' ', strip=True)))
    labels = card.select('.card-item__price-usually,.card-item__presence' if lc else '.snippet__price-label,.snippet__price-note,.snippet__price-caption,.snippet__price-personal')
    label = text(raw_label+' '+' '.join(x.get_text(' ', strip=True) for x in labels))
    if not mm:
        markers = [x.get('data-label','')+' '+x.get('data-tooltip','') for x in card.select('[data-label],[data-tooltip]')]
        label = text(label+' '+' '.join(markers))
    # Only visible card text containing markers changes role, not banners elsewhere.
    card_text = text(card.get_text(' ', strip=True))
    if any(t in card_text.lower() for t in ['персональн', 'после авторизац', 'войдите', '₽/мес', '/мес']):
        label = card_text
    kind = price_type(label, old) if current is not None or old is not None else 'UNKNOWN'
    displayed = card.select_one('.card-product__sale,.snippet__discount,.snippet__sale,.snippet-price__discount')
    available = card.select_one('.card-item__presence' if lc else '.card-product__status .tag,.snippet__availability,.snippet__stock,.alt-snippet__pickup')
    brand_el = card.select_one('[itemprop="brand"]')
    model_el = card.select_one('[itemprop="model"]')
    return Item(name, product_url=url, campaign_name=campaign, tab_title=tab,
                brand=text(brand_el.get_text(' ', strip=True)) if brand_el else ('MetalMaster' if re.search(r'metal\s*master', name, re.I) else None),
                model=text(model_el.get_text(' ', strip=True)) if model_el else None,
                old_price=old, new_price=current, price_type=kind, price_label_raw=label or None,
                displayed_discount_raw=text(displayed.get_text(' ', strip=True)) if displayed else None,
                availability=text(available.get_text(' ', strip=True)) if available else None,
                region=SOURCES[source]['region'], category=tab, evidence_html=str(card)).finish()

def lasercut_offer_item(body, base, title):
    """lasercut.ru product detail: tabbed offer with under-order promo and in-stock prices."""
    offer = body.select_one('.offer__price')
    if not offer:
        return None
    panels = {p.get('id'): p for p in offer.select('[id]')}
    under, stock = panels.get('old'), panels.get('stock')
    old_el = offer.select_one('s.nope--stock,s.nope')
    old = money(text(old_el.get_text(' ', strip=True))) if old_el else None
    promo_el = under.select_one('.price') if under else offer.select_one('.price')
    current = money(text(promo_el.get_text(' ', strip=True))) if promo_el else None
    stock_el = stock.select_one('.price') if stock else None
    stock_price = money(text(stock_el.get_text(' ', strip=True))) if stock_el else None
    if current is None and old is None:
        return None
    # Both public price contexts stay visible evidence; nothing is averaged or invented.
    label = 'под заказ' + (': ' + text(promo_el.get_text(' ', strip=True)) if promo_el else '')
    if stock_el:
        label += '; из наличия: ' + text(stock_el.get_text(' ', strip=True))
    brand_el = body.select_one('[itemprop="brand"]')
    model_el = body.select_one('[itemprop="model"]')
    return Item(title, product_url=base,
                brand=text(brand_el.get_text(' ', strip=True)) if brand_el else None,
                model=text(model_el.get_text(' ', strip=True)) if model_el else None,
                old_price=old, new_price=current, price_type=price_type(label, old), price_label_raw=label,
                availability='из наличия' if stock_price else 'под заказ',
                region=SOURCES['lasercut_ru']['region'], evidence_html=str(offer)).finish()

def parse(source, url, html, since, today, observed_url=None, publication_evidence=None):
    soup = BeautifulSoup(html, 'html.parser')
    base = SOURCES[source]['base_url']
    identity = canonical(url)
    declared = soup.select_one('link[rel="canonical"][href]')
    if declared:
        candidate = canonical(declared['href'], url)
        # Never convert NN observations into www/Moscow identities.
        if urlsplit(candidate).netloc == urlsplit(base).netloc:
            identity = candidate
    kind = content_type(identity)
    h1 = soup.find('h1')
    title = text((h1 or soup.title).get_text(' ', strip=True)) if (h1 or soup.title) else identity
    main = soup.select_one('main,.page__main,.page-content,.page__content,.content') or soup.body or soup
    body = clean(main)
    raw_text = body.get_text('\n', strip=True)
    normalized = text(raw_text)
    publication = None
    if kind == 'NEWS' or (kind == 'PROMOTION' and urlsplit(identity).path not in ('/promo/', '/c_actions/', '/vse-aktsii/')):
        for el in body.select('time,[itemprop="datePublished"],.article__date,.news__date,.post__date,.date'):
            publication = parse_date(el.get('datetime') or el.get('content') or el.get_text(' ', strip=True))
            if publication: break
    if not publication and kind == 'NEWS' and publication_evidence:
        publication = parse_date(publication_evidence.get('published_at'))
    valid_from, valid_to = campaign_dates(normalized, publication.year if publication else None)
    # List dates refer to children and must not become the parent's campaign date.
    if kind == 'HOMEPAGE_BLOCK' or urlsplit(identity).path in ('/promo/', '/news/', '/c_actions/', '/vse-aktsii/', '/novosti/', '/blog/'):
        valid_from = valid_to = None
    current = kind in ('PROMOTION', 'SALE_PAGE', 'HOMEPAGE_BLOCK', 'CAMPAIGN', 'PRODUCT_DETAIL')
    reason = inclusion(publication, valid_from, valid_to, current, since, today)
    page = Page(source, identity, observed_url or url, title, kind, raw_text, normalized,
                publication, valid_from, valid_to, reason)
    page.publication_evidence = publication_evidence if publication_evidence and publication else None
    scopes = [(body, title, None)]
    if source == 'lasercut_ru' and kind == 'OTHER':
        # Accessories and non-machine catalog surfaces never produce machine facts.
        scopes = []
    if kind == 'HOMEPAGE_BLOCK':
        scopes = []
        if source == 'kuvalda_nnov':
            for section in body.select('.section'):
                heading = section.select_one('.section__title')
                if heading and section.select_one('.promo-slider'):
                    tab = section.select_one('.alt-tabs__item[disabled] .alt-tabs__label,.alt-tabs__item.is-active .alt-tabs__label')
                    scopes.append((section, text(heading.get_text(' ', strip=True)), text(tab.get_text(' ',strip=True)) if tab else None))
        else:
            # Only cards marked as participating in a campaign, not all homepage products.
            scopes = [(body, 'Homepage: current promotional cards', None)]
        # Do not hash navigation/catalog counts, unrelated widgets, session markup.
        page.normalized_text = ' | '.join(c+' '+(t or '') for _, c, t in scopes)
    if source == 'kuvalda_nnov' and kind == 'SALE_PAGE':
        # Floating chat/cookie/subscription controls differ by transport/session.
        # Preserve full raw text, hash only campaign content + structured cards.
        conditions = [text(n.parent.get_text(' ',strip=True)) for n in body.find_all(string=re.compile(r'могут иметь незначительные дефекты'))]
        navigation = [text(a.get_text(' ',strip=True)) for a in body.select('a[href]') if a['href'].startswith('/sale/')]
        page.normalized_text = ' | '.join([title]+sorted(set(conditions))+ (sorted(set(navigation)) if urlsplit(identity).path=='/sale/' else []))
    seen = set()
    cards_selector = ('.card-product' if source == 'metalmaster' else
                      '.card__item.card-item' if source == 'lasercut_ru' else
                      '.snippet:not(.snippet_placeholder),.alt-snippet:not(.alt-snippet_placeholder)')
    for scope, campaign, tab in scopes:
        for card in scope.select(cards_selector):
            if kind == 'HOMEPAGE_BLOCK' and source == 'metalmaster' and not card.select_one('.mm-badge_action'):
                continue
            # Only homepage cards evidencing an active promotion (shown old price) become items.
            if kind == 'HOMEPAGE_BLOCK' and source == 'lasercut_ru' and not card.select_one('.card-item__price-old'):
                continue
            item = card_item(card, source, identity, campaign, tab)
            if item:
                key = (item.product_url, item.product_name, item.campaign_name, item.tab_title)
                if key not in seen:
                    seen.add(key); page.items.append(item)
    if source == 'lasercut_ru' and kind == 'PRODUCT_DETAIL':
        item = lasercut_offer_item(body, identity, title)
        if item and not any(i.product_url == item.product_url for i in page.items):
            page.items.append(item)
    # Campaigns often name products without showing prices. Retain these links as
    # evidence, without fetching the catalog or fabricating monetary values.
    if source in ('metalmaster', 'lasercut_ru') and kind in ('NEWS', 'PROMOTION'):
        article = body.select_one('.contents__block-post,.text,.article,.news-text,.news__text,.news-detail,.page__text,.content__body')
        machine_words = (r'станок|лазерн|фрезерн|токарн|листогиб|труборез|маркер|сварочн|очистк|мебельн' if source == 'lasercut_ru'
                         else r'станок|станки|листогиб|пресс|metal\s*master|компрессор|вальц|гильотин')
        if article:
            for a in article.select('a[href]'):
                name = text(a.get_text(' ', strip=True))
                if len(name) < 5 or not re.search(machine_words, name, re.I): continue
                # Accessories never become machine facts, whatever the link text says.
                if source == 'lasercut_ru' and ('/katalog/komplektuyushhie/' in a['href'] or re.search(r'линз|сопло|расходн|запчаст|аксессуар|комплектующ|фильтр|чиллер|вентиляц|подставк', name, re.I)): continue
                try: product_url = canonical(a['href'], identity)
                except ValueError: continue
                if urlsplit(product_url).netloc != urlsplit(base).netloc: continue
                if any(i.product_url == product_url for i in page.items): continue
                page.items.append(Item(name, product_url=product_url, campaign_name=title, region=SOURCES[source]['region'], evidence_html=str(a)).finish())
    if not page.items and source == 'kuvalda_nnov' and (kind == 'HOMEPAGE_BLOCK' or (kind == 'SALE_PAGE' and urlsplit(identity).path != '/sale/')):
        page.warnings.append('BROWSER_RENDER_REQUIRED')
    return page

def discover(source, url, html, since, today):
    soup = BeautifulSoup(html, 'html.parser')
    base = SOURCES[source]['base_url']
    # MODX sources (lasercut.ru) emit section-relative hrefs and declare <base href>;
    # resolve against it, exactly as a browser would.
    base_tag = soup.select_one('base[href]')
    resolve = base_tag['href'].strip() if base_tag else url
    found = {}
    for a in soup.select('a[href]'):
        try: target = canonical(a['href'], resolve)
        except ValueError: continue
        if urlsplit(target).netloc != urlsplit(base).netloc: continue
        path = urlsplit(target).path
        if source == 'lasercut_ru':
            # Machine category roots only; accessories, blog rubrics, filter facets
            # and product detail pages are never selected in a bounded crawl.
            selected = lasercut_path_kind(path) in ('PROMOTION', 'NEWS', 'SALE_PAGE')
        else:
            selected = path.startswith(('/promo/', '/news/', '/c_actions/', '/top_action/', '/discount/')) or path == '/sale/'
            # Explicit industrial sale navigation only; never traverse whole catalog.
            if source == 'kuvalda_nnov' and path.startswith('/sale/') and re.search(r'^(?:деревообрабатывающие|металлообрабатывающие) станки$',text(a.get_text(' ',strip=True)),re.I): selected=True
        if not selected: continue
        container = a.find_parent(class_=re.compile(r'preview\b|card-news\b|rubricator-item|catalog-media')) or a
        label = text(container.get_text(' ', strip=True))
        pub = None
        t = container.find('time')
        if t: pub = parse_date(t.get('datetime') or t.get_text())
        elif path.startswith('/news/'): pub = parse_date(label)
        vf, vt = campaign_dates(label, pub.year if pub else None)
        reason = inclusion(pub, vf, vt, False, since, today)
        # Bound archive traversal: skip dated out-of-scope entries, but keep older
        # campaign news if excerpt explicitly overlaps the requested range.
        if pub and pub < since and not reason:
            # One bounded pre-period campaign candidate may overlap September;
            # publication date alone cannot establish expiration.
            if not (pub >= since-timedelta(days=45) and re.search(r'акци|скид|распрод|предложен',label,re.I)): continue
        if vt and vt < since: continue
        found[target] = {'url': target, 'label': label, 'published_at': str(pub) if pub else None,
                         'valid_to': str(vt) if vt else None, 'reason': reason or 'BOUNDED_NAVIGATION'}
    return list(found.values())
