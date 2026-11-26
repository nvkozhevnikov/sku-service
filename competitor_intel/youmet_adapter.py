"""YouMet public DOM adapter shared by saved captures and normal capture pipeline.

Product/navigation selectors come from the saved ordinary-browser DOM. Detail
selectors accept explicit content/date fields; unseen layouts fail closed.
"""
import re
from datetime import date
from urllib.parse import urlsplit,unquote
from bs4 import BeautifulSoup
from .model import Page, Item, canonical, text, money, price_type, parse_date, inclusion, campaign_dates

KINDS=('AUTO','HOMEPAGE_BLOCK','NAVIGATION_INDEX','PROMOTION','NEWS','BLOG','SALE_PAGE','PRODUCT_DETAIL')

def public_url(value,base='https://youmet.ru/'):
    u=canonical(value,base);p=urlsplit(u)
    if p.scheme!='https' or p.netloc!='youmet.ru':raise ValueError('Only primary public youmet.ru URLs accepted')
    path=unquote(unquote(p.path))
    if path.startswith('//') or re.match(r'^/(?:auth|personal|account|admin|api|bitrix/admin|cart|basket|order)(?:/|$)',path,re.I):raise ValueError('Private/auth URL refused')
    return u

def value(root,selector):
    el=root.select_one(selector)
    return text(el.get('content') or el.get_text(' ',strip=True)) if el else None

def page_kind(url):
    path=urlsplit(url).path
    if path=='/':return 'HOMEPAGE_BLOCK'
    for prefix,kind in [('/akcia/','PROMOTION'),('/about/news/','NEWS'),('/blog/','BLOG')]:
        if path==prefix:return 'NAVIGATION_INDEX'
        if path.startswith(prefix):return kind
    if path.startswith('/shop/'):return 'PRODUCT_DETAIL'
    raise ValueError('Unrecognized page kind; specify --kind using observed page content')

def discover(html,url):
    soup=BeautifulSoup(html,'html.parser');found={}
    for a in soup.select('a[href]'):
        try:u=public_url(a['href'],url)
        except ValueError:continue
        label=text(a.get_text(' ',strip=True));path=urlsplit(u).path
        kind=next((k for prefix,k in [('/akcia/','PROMOTIONS'),('/about/news/','NEWS'),('/blog/','BLOG')] if path.startswith(prefix)),None)
        if not kind and re.search(r'спецпредлож|специальн.{0,8}предлож|распродаж',label,re.I):kind='SPECIAL_OFFERS'
        if kind:found[u]={'url':u,'title':label or a.get('title'),'kind':kind,'status':'DISCOVERED_NOT_FETCHED'}
    return list(found.values())

def extract_youmet(cap,kind='AUTO',since=date(2026,9,1),today=date(2026,10,12)):
    url=public_url(cap.final_url);kind=page_kind(url) if kind=='AUTO' else kind
    if kind not in KINDS:raise ValueError('Unsupported YouMet page kind')
    soup=BeautifulSoup(cap.html,'html.parser')
    main=soup.select_one('main, #content, .right_block, .page-content, .wraps') or soup
    title=value(main,'h1') or value(soup,'h1')
    if not title:raise ValueError('No public content h1; capture not accepted')
    body=main.select_one('.detail-text, .detail_text, article, .news-detail, .detail') or main
    if not text(body.get_text(' ',strip=True)):raise ValueError('Empty public content')
    published=value(main,'[itemprop="datePublished"], .date-publication, time[datetime], .period .date, .detail .date')
    time_node=main.select_one('time[datetime]')
    if time_node:published=time_node['datetime']
    page=Page('youmet',url,cap.url,title,kind,'','',published_at=parse_date(published))
    page.extractor_version='youmet-dom-1.0'
    page.capture_region=value(soup,'.region_wrapper .js_city_chooser > span, .region_wrapper .name, .region-name')
    if kind=='HOMEPAGE_BLOCK':
        page.homepage_blocks=[]
        for a in main.select('.item.inner_wrap a.name[href]'):
            try:u=public_url(a['href'],url)
            except ValueError:continue
            if not urlsplit(u).path.startswith('/akcia/'):continue
            container=a.find_parent(class_='info') or a.parent
            page.homepage_blocks.append({'title':text(a.get_text(' ',strip=True)),'url':u,'published_at':str(parse_date(value(container,'.date'))) if parse_date(value(container,'.date')) else None,'detail_status':'DISCOVERED_NOT_FETCHED'})
    if page.published_at:page.publication_evidence={'method':'OWN_CONTENT_DATE','value':str(page.published_at),'raw_capture_ref':cap.raw_ref}
    seen=set()
    for card in ([] if kind in ('BLOG','NEWS','NAVIGATION_INDEX') else main.select('.catalog_item, .product-card, [itemtype$="/Product"]')):
        a=card.select_one('.item-title a[href], a[itemprop="url"], .product-title[href]')
        name=value(card,'.item-title, [itemprop="name"], .product-title')
        if not a or not name:continue
        try:product_url=public_url(a['href'],url)
        except ValueError:continue
        if product_url in seen:continue
        old_node=card.select_one('.price.discount, .price.old, .old-price, .old_price')
        current_node=card.select_one('.price:not(.discount):not(.old), .current-price')
        current=value(current_node,'.price_value') if current_node else None
        old=value(old_node,'.price_value') if old_node else None
        current=current or (text(current_node.get_text(' ',strip=True)) if current_node else None)
        old=old or (text(old_node.get_text(' ',strip=True)) if old_node else None)
        # Homepage ordinary catalogue/new-product cards are not promotions.
        if kind=='HOMEPAGE_BLOCK' and not old_node:continue
        seen.add(product_url)
        label=text((current or '')+' '+(value(card,'.price-label, .price_type') or ''))
        role=price_type(label,money(old))
        if re.search(r'после авторизац|персональн.{0,12}цен|ежемесяч|рассроч.{0,20}/мес',card.get_text(' ',strip=True),re.I):role=price_type(card.get_text(' ',strip=True),money(old))
        currency=(current_node.get('data-currency') if current_node else None) or value(card,'[itemprop="priceCurrency"]') or ('RUB' if current_node and '₽' in current_node.get_text() else 'UNKNOWN')
        item=Item(name,product_url,campaign_name=title,brand=value(card,'[itemprop="brand"], .brand'),model=value(card,'[itemprop="model"], .model'),category=value(card,'[itemprop="category"], .category'),old_price=money(old),new_price=money(current),currency=currency,price_type=role,price_label_raw=label,displayed_discount_raw=value(card,'.sale_block, .discount_percent'),availability=value(card,'.item-stock .value, [itemprop="availability"], .availability'),region=page.capture_region,evidence_html=str(card)).finish()
        if current and item.new_price is None and role in ('PUBLIC','SALE'):
            if re.search(r'звоните|запрос|уточн',current,re.I):item.price_type='UNKNOWN'
            else:raise ValueError('Unparsed visible product price')
        if 'INVALID_OLD_NEW_PRICE' in item.warnings:raise ValueError('Invalid visible old/new price roles')
        page.items.append(item)
    for node in body.select('script,style,nav,header,footer,form,.related,.recommendations'):node.decompose()
    page.raw_text=body.get_text('\n',strip=True);page.normalized_text=text(page.raw_text)
    start=value(main,'[itemprop="startDate"], .valid-from');end=value(main,'[itemprop="endDate"], .valid-to')
    page.valid_from,page.valid_to=campaign_dates(page.normalized_text,page.published_at.year if page.published_at else None)
    page.valid_from=parse_date(start) or page.valid_from;page.valid_to=parse_date(end) or page.valid_to
    page.inclusion_reason=inclusion(page.published_at,page.valid_from,page.valid_to,kind in ('PROMOTION','SALE_PAGE','HOMEPAGE_BLOCK','PRODUCT_DETAIL'),since,today)
    if kind=='NAVIGATION_INDEX':page.inclusion_reason='OPERATOR_SOURCE_NAVIGATION'
    if kind=='HOMEPAGE_BLOCK' and not page.items:page.page_type='NAVIGATION_INDEX';page.inclusion_reason='OPERATOR_SOURCE_NAVIGATION'
    from .business_report import mechanics
    page.commercial_mechanics=mechanics(page.normalized_text) if kind in ('PROMOTION','SALE_PAGE','HOMEPAGE_BLOCK') else []
    page.item_scope_complete=False;page.source_discovery_complete=False
    return page
