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

def content_type(url):
    path = urlsplit(url).path
    if path == '/': return 'HOMEPAGE_BLOCK'
    if path.startswith('/news/') and path != '/news/': return 'NEWS'
    if path.startswith('/sale/') or path.startswith('/discount/'): return 'SALE_PAGE'
    if path == '/top_action/': return 'CAMPAIGN'
    if path.startswith(('/promo/', '/c_actions/')): return 'PROMOTION'
    return 'OTHER'

def card_item(card, source, base, campaign, tab=None):
    mm = source == 'metalmaster'
    title = card.select_one('.card-product__title' if mm else '.snippet__title,.alt-snippet__title')
    if not title or not title.get_text(strip=True):
        return None
    name = text(title.get_text(' ', strip=True))
    link = card.select_one('a.card-product__link[href]' if mm else 'a.snippet__title[href],a.alt-snippet__title[href]')
    if not link and title.name == 'a': link = title
    if not link: link = title.find_parent('a', href=True)
    if not link: link = card.select_one('a[href]')
    url = canonical(link['href'], base) if link and link['href'].startswith(('http', '/')) else None
    price = card.select_one('.card-product__price' if mm else '.snippet__price,.alt-snippet__price')
    old_el = price.select_one('del') if price else None
    if not mm:
        old_el = card.select_one('.snippet-price__old,.snippet__price-old, .snippet__old-price, .snippet__price del')
    old = money(text(old_el.get_text(' ', strip=True))) if old_el else None
    current = None
    raw_label = text(price.get_text(' ', strip=True)) if price else ''
    if price:
        p = copy.copy(price)
        for el in p.select('del,s,.snippet-price__old,.snippet__price-old,.snippet__old-price'):
            el.decompose()
        # Kuvalda current price is explicitly separated from promotional labels.
        current_el = p.select_one('.snippet-price__value,.snippet__price-value, .snippet__price-current') if not mm else None
        current = money(text((current_el or p).get_text(' ', strip=True)))
    labels = card.select('.snippet__price-label,.snippet__price-note,.snippet__price-caption,.snippet__price-personal')
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
    available = card.select_one('.card-product__status .tag,.snippet__availability,.snippet__stock,.alt-snippet__pickup')
    brand_el = card.select_one('[itemprop="brand"]')
    model_el = card.select_one('[itemprop="model"]')
    return Item(name, product_url=url, campaign_name=campaign, tab_title=tab,
                brand=text(brand_el.get_text(' ', strip=True)) if brand_el else ('MetalMaster' if re.search(r'metal\s*master', name, re.I) else None),
                model=text(model_el.get_text(' ', strip=True)) if model_el else None,
                old_price=old, new_price=current, price_type=kind, price_label_raw=label or None,
                displayed_discount_raw=text(displayed.get_text(' ', strip=True)) if displayed else None,
                availability=text(available.get_text(' ', strip=True)) if available else None,
                region=SOURCES[source]['region'], category=tab, evidence_html=str(card)).finish()

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
    if kind == 'NEWS' or (kind == 'PROMOTION' and urlsplit(identity).path not in ('/promo/', '/c_actions/')):
        for el in body.select('time,[itemprop="datePublished"],.article__date,.news__date,.post__date,.date'):
            publication = parse_date(el.get('datetime') or el.get('content') or el.get_text(' ', strip=True))
            if publication: break
    if not publication and kind == 'NEWS' and publication_evidence:
        publication = parse_date(publication_evidence.get('published_at'))
    valid_from, valid_to = campaign_dates(normalized, publication.year if publication else None)
    # List dates refer to children and must not become the parent's campaign date.
    if kind == 'HOMEPAGE_BLOCK' or urlsplit(identity).path in ('/promo/', '/news/', '/c_actions/'):
        valid_from = valid_to = None
    current = kind in ('PROMOTION', 'SALE_PAGE', 'HOMEPAGE_BLOCK', 'CAMPAIGN')
    reason = inclusion(publication, valid_from, valid_to, current, since, today)
    page = Page(source, identity, observed_url or url, title, kind, raw_text, normalized,
                publication, valid_from, valid_to, reason)
    page.publication_evidence = publication_evidence if publication_evidence and publication else None
    scopes = [(body, title, None)]
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
    for scope, campaign, tab in scopes:
        for card in scope.select('.card-product' if source == 'metalmaster' else '.snippet:not(.snippet_placeholder),.alt-snippet:not(.alt-snippet_placeholder)'):
            if kind == 'HOMEPAGE_BLOCK' and source == 'metalmaster' and not card.select_one('.mm-badge_action'):
                continue
            item = card_item(card, source, identity, campaign, tab)
            if item:
                key = (item.product_url, item.product_name, item.campaign_name, item.tab_title)
                if key not in seen:
                    seen.add(key); page.items.append(item)
    # Campaigns often name products without showing prices. Retain these links as
    # evidence, without fetching the catalog or fabricating monetary values.
    if source == 'metalmaster' and kind in ('NEWS', 'PROMOTION'):
        article = body.select_one('.contents__block-post,.text,.article,.news-text,.news__text,.news-detail,.page__text')
        if article:
            for a in article.select('a[href]'):
                name = text(a.get_text(' ', strip=True))
                if len(name) < 5 or not re.search(r'станок|станки|листогиб|пресс|metal\s*master|компрессор|вальц|гильотин', name, re.I): continue
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
    found = {}
    for a in soup.select('a[href]'):
        try: target = canonical(a['href'], url)
        except ValueError: continue
        if urlsplit(target).netloc != urlsplit(base).netloc: continue
        path = urlsplit(target).path
        selected = path.startswith(('/promo/', '/news/', '/c_actions/', '/top_action/', '/discount/')) or path == '/sale/'
        # Explicit industrial sale navigation only; never traverse whole catalog.
        if source == 'kuvalda_nnov' and path.startswith('/sale/') and re.search(r'^(?:деревообрабатывающие|металлообрабатывающие) станки$',text(a.get_text(' ',strip=True)),re.I): selected=True
        if not selected: continue
        container = a.find_parent(class_=re.compile(r'preview\b|card-news\b')) or a
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
