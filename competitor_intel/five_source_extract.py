"""DOM adapters from the saved public source audit; no network or matching."""
from datetime import date
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
from bs4 import BeautifulSoup
import json,re
from .model import Page,Item,canonical,text,money,parse_date,campaign_dates,inclusion,price_type

EXTRACTOR='five-sources-1.2'
def public_url(href,base):
    raw=urljoin(base,href);p=urlsplit(raw);c=urlsplit(canonical(raw))
    # Keep actual source trailing-slash convention; RuStan exposes slashless URLs.
    return urlunsplit((c.scheme,c.netloc,p.path or '/',c.query,''))
def key(url,tab):return json.dumps([url,tab or ''],ensure_ascii=False,separators=(',',':'))
def nt(root,selector):
    n=root.select_one(selector);return text(n.get_text(' ',strip=True)) if n else None
def amount(label):
    value=re.sub(r'^\s*от\s+','',label or '',flags=re.I)
    value=re.sub(r'\s+[pр]\s*$',' ₽',value)
    return money(value)

def content_kind(code,url,hint):
    path=urlsplit(url).path.lower()
    if code=='rustan' and '/articles' in path:return 'BLOG'
    if code=='belmash':
        if '/products/top-offers/novinki/' in path:return 'SALE_PAGE'
        if path.rstrip('/') in ('/novosti/sales','/novosti/latest','/novosti/main','/novosti/expo','/novosti/upd','/novosti/polezno'):return 'NAVIGATION_INDEX'
        for segment,kind in [('/novosti/sales/','PROMOTION'),('/novosti/latest/','NEW_PRODUCT_ANNOUNCEMENT'),('/novosti/main/','NEWS'),('/novosti/expo/','EVENT'),('/novosti/upd/','MODERNIZATION'),('/novosti/polezno/','CONTENT')]:
            if segment in path and path.rstrip('/')!=segment.rstrip('/'):return kind
    if '/articles/' in path or '/blog/' in path:return 'BLOG'
    if '/news/' in path and not path.rstrip('/').endswith('/news'):return 'NEWS'
    if hint in ('BLOG','NEWS','EVENT','MODERNIZATION','CONTENT','NEW_PRODUCT_ANNOUNCEMENT','PRODUCT_DETAIL'):return hint
    if hint in ('NOVELTY_INDEX','OUTLET_INDEX','SALE_PAGE'):return 'SALE_PAGE'
    if hint=='PROMOTION' or code=='belmash' and '/top-offers/' in path and not path.rstrip('/').endswith('top-offers'):return 'PROMOTION'
    if hint.endswith('_INDEX'):return 'NAVIGATION_INDEX'
    return hint

def extract(code,cap,hint,since=date(2026,9,1),today=None,publication_proof=None):
    today=today or date.today();soup=BeautifulSoup(cap.html,'html.parser');base=cap.final_url
    canon=soup.select_one('link[rel=canonical][href]');url=public_url(canon['href'],base) if canon else public_url(base,base)
    pagination=[(k,v) for k,v in parse_qsl(urlsplit(base).query) if k.upper().startswith('PAGEN_') or k.lower() in ('page','p')]
    if pagination:
        parts=urlsplit(url);query=dict(parse_qsl(parts.query));query.update({k:v for k,v in pagination if v!='1'})
        url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(sorted(query.items())),''))
    main=soup.select_one('main,.main-box,.main-content,.page-content,.new-detail,.content') or soup
    title=nt(soup,'h1') or nt(soup,'title') or url;kind=content_kind(code,url,hint)
    page=Page(code,url,cap.url,title,kind,'','');page.extractor_version=EXTRACTOR;page.content_kind=kind
    page.item_scope_complete=False;page.source_discovery_complete=False;page.item_metadata={};page.commercial_mechanics=[]
    containers=[]
    if code=='jettools_ru':containers=soup.select('.component-item-product,.component-items-products-slides__item')
    elif code=='rustan':containers=soup.select('.similar-container')
    elif code=='belmash':containers=soup.select('.tf-card')
    elif code=='harvey_rus':containers=soup.select('.new-catalog-sect__list-item,.new-index__catalog-item')
    elif code=='amur_stan':containers=soup.select('.product-card,.index-offers-card')
    seen=set()
    for card in containers:
        if code=='jettools_ru':a=card.select_one('a.title[href],a.name[href]');price=card.select_one('.component-catalog-price > div:not(.old)');old=card.select_one('.component-catalog-price .old');sku=nt(card,'.component-product-vendor-code');available=nt(card,'[class*=availability],[class*=quantity]')
        elif code=='rustan':a=card.select_one('a.product-card[href]');price=card.select_one('.price.col');old=card.select_one('del,.old-price');sku=nt(card,'[class*=article],[class*=vendor]');available=nt(card,'.base-action:not(.lz-badge)')
        elif code=='belmash':a=card.select_one('a.d-flex[href],a.tf-card__title[href],.tf-card__title a[href],a[href]');price=card.select_one('.cip__new-price');old=card.select_one('.cip__old-price');sku=nt(card,'[class*=article],[class*=sku]');available=nt(card,'.cii__info-item-quantity')
        elif code=='harvey_rus':a=card.select_one('a.new-catalog-sect__list-name[href],a[href]');price=card.select_one('.new-catalog-sect__list-price,.new-index__catalog-item__cost');old=price.select_one('.base__price:not(.base__price_percent)') if price else None;sku=nt(card,'[class*=article],[class*=sku]');available=nt(card,'.sklad__nal,.bx_storege')
        else:a=card.select_one('a.product-card-title[href],a.index-offers-card__button[href]');price=card.select_one('.product-card-price__value,.index-offers-card__price-value');old=card.select_one('.product-card-price__old,.index-offers-card__price-old-value');sku=nt(card,'[class*=article],[class*=sku]');available=nt(card,'[class*=availability],[class*=stock]')
        if not a:continue
        name=text(a.get('title') or a.get_text(' ',strip=True))
        if not name or code=='harvey_rus':name=nt(card,'.new-catalog-sect__list-name,.new-index__catalog-item__name') or name
        if code=='amur_stan':name=nt(card,'.product-card-title,.index-offers-card__title') or name
        if code=='belmash':name=nt(card,'a.d-flex,.tf-card__title') or a.get('title') or name
        if not name:continue
        product_url=public_url(a['href'],base)
        if urlsplit(product_url).netloc!=urlsplit(base).netloc:continue
        badge=nt(card,'.tooltips-wrapper,.block_stikker,.tf-card__labels,.tf-cis,[class*=sticker]') or ''
        novelty='Новинка' if re.search(r'новинк',badge,re.I) else 'Новинки (раздел источника)' if hint=='NOVELTY_INDEX' else None
        if '/top-offers/novinki/' in base:novelty=novelty or 'Новинки (раздел источника)'
        novelty_block=card.find_parent(class_='block__novinka')
        if novelty_block is not None:novelty=novelty or 'Новинки (блок источника)'
        condition='Дисконт / уценённый экземпляр' if code=='harvey_rus' and ('/discount/' in product_url or re.search(r'дисконт|уцен',name,re.I)) else None
        tab='CONDITION:OUTLET' if condition else None
        if (product_url,tab) in seen:continue
        seen.add((product_url,tab))
        clean=BeautifulSoup(str(price),'html.parser') if price else None
        if clean:
            for n in clean.select('del,s,.old,.base__price,.cip__old-price'):n.decompose()
        current_label=text(clean.get_text(' ',strip=True)) if clean else ''
        old_label=text(old.get_text(' ',strip=True)) if old else None
        cur=amount(current_label);old_price=amount(old_label);ambiguous=[]
        if code=='amur_stan' and price:
            monetary=re.findall(r'\d[\d\s\u00a0\u202f]*(?:[,.]\d{1,2})?\s*(?:₽|руб\.?|\$|USD)',current_label,re.I)
            if len(monetary)>1 or '$' in current_label or 'USD' in current_label:
                ambiguous=monetary;cur=old_price=None
        commercial_offer=code=='amur_stan' and '/discount/' in product_url
        if commercial_offer:
            ambiguous=[{'role':'commercial_offer_current','value':current_label},{'role':'commercial_offer_old','value':old_label}]
            cur=old_price=None
        if kind=='HOMEPAGE_BLOCK' and old_price is None and not novelty and not re.search(r'акци|скид|подар|комплект',badge,re.I) and code!='amur_stan':continue
        personal=bool(re.search(r'персональная цена|после авторизации|войдите.*цен',card.get_text(' ',strip=True),re.I))
        ptype='AUTH_REQUIRED' if personal else 'UNKNOWN' if ambiguous or cur is None else price_type(current_label,old_price)
        if personal:cur=old_price=None
        category=nt(card,'[class*=category],.productCard__section') or title if hint=='NOVELTY_INDEX' else nt(card,'[class*=category]')
        brand_match=re.search(r'\b(JET|BELMASH|Harvey|JIB|CERTON|Powermatic|Triton|WARRIOR)\b',name,re.I)
        model_match=re.search(r'\b(?:JET|BELMASH|Harvey|JIB|CERTON|Powermatic|WARRIOR)\s+([A-ZА-Я0-9][\w.-]*(?:\s+\d[\w.-]*)?)',name,re.I)
        displayed=nt(card,'.base__price_percent,[class*=discount],.cip__discount')
        if not displayed:
            match=re.search(r'-?\d+(?:[.,]\d+)?\s*%',badge);displayed=match[0] if match else None
        item=Item(name,product_url,campaign_name=title,tab_title=tab,brand=brand_match[1] if brand_match else None,model=model_match[1] if model_match else None,category=category,old_price=old_price,new_price=cur,price_type=ptype,price_label_raw=current_label or None,displayed_discount_raw=displayed,availability=available,region='Россия',evidence_html=str(card)).finish()
        page.items.append(item)
        page.item_metadata[key(product_url,tab)]={'sku':sku,'source_new_label':novelty,'condition_label':condition,'condition_identity':'OUTLET_INSTANCE' if condition else 'STANDARD_CONTEXT','displayed_saving_raw':nt(card,'.cip__profit,[class*=saving]'),'ambiguous_commercial_values':ambiguous,'record_kind':'COMMERCIAL_OFFER' if commercial_offer else 'PRODUCT','original_capture_time':cap.fetched_at.isoformat(),'capture_page_url':cap.final_url,'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'unit_price_evidence':{'current_label':current_label,'old_label':old_label,'old_price_explicit':old is not None}}
    body=main
    for node in body.select('script,style,header,footer,nav,form,svg'):node.decompose()
    page.raw_text=body.get_text('\n',strip=True);page.normalized_text=text(page.raw_text)
    if kind in ('NEWS','BLOG','EVENT','MODERNIZATION','CONTENT','NEW_PRODUCT_ANNOUNCEMENT','PROMOTION'):
        date_node=main.select_one('time[datetime],.article-date,.articles-post__date,[class*=publish],.news-detail__date') or soup.select_one('time[datetime],.article-date,.articles-post__date')
        page.published_at=parse_date(date_node.get('datetime') or date_node.get_text(' ',strip=True)) if date_node else parse_date(publication_proof or '')
        if page.published_at:page.publication_evidence={'method':'VISIBLE_DATE_OR_LISTING_PROOF','value':str(page.published_at),'raw_capture_ref':cap.raw_ref}
    if kind=='PROMOTION':
        page.valid_from,page.valid_to=campaign_dates(page.normalized_text,page.published_at.year if page.published_at else None)
        from .business_report import mechanics
        page.commercial_mechanics=mechanics(page.normalized_text)
        page.campaign_coverage='PARTIAL'
    current=kind in ('HOMEPAGE_BLOCK','SALE_PAGE','PROMOTION','PRODUCT_DETAIL')
    page.inclusion_reason=inclusion(page.published_at,page.valid_from,page.valid_to,current,since,today)
    if kind=='NAVIGATION_INDEX':page.inclusion_reason='LIVE_SOURCE_AUDIT'
    if kind=='HOMEPAGE_BLOCK' and not page.items:page.page_type='NAVIGATION_INDEX';page.inclusion_reason='LIVE_SOURCE_AUDIT'
    return page

def discover(code,cap):
    soup=BeautifulSoup(cap.html,'html.parser');links=[];seen=set()
    # Dated content cards; stop archives at the listing rather than fetching old details.
    selectors='.article-card,.articles-grid__post,.news-card,.index-news-card,[class*=news-item]'
    for card in soup.select(selectors):
        a=card.select_one('a[href]')
        if not a:continue
        url=public_url(a['href'],cap.final_url);d=parse_date(card.get_text(' ',strip=True))
        if d and d<date(2026,9,1):continue
        if not d:continue
        links.append({'url':url,'kind':content_kind(code,url,'BLOG' if '/articles' in url else 'NEWS'),'published_at':str(d)})
    names={'акции':'PROMOTION_INDEX','новости':'NEWS_INDEX','статьи':'BLOG_INDEX','новинки':'NOVELTY_INDEX','дисконт':'OUTLET_INDEX','мероприятия':'EVENT_INDEX','выставки/мероприятия':'EVENT_INDEX','модернизация':'MODERNIZATION_INDEX','модернизация оборудования':'MODERNIZATION_INDEX','полезно':'CONTENT_INDEX'}
    for a in soup.select('a[href]'):
        label=re.sub(r'\s+\d+$','',text(a.get_text(' ',strip=True)).lower());kind=names.get(label)
        if kind:
            links.append({'url':public_url(a['href'],cap.final_url),'kind':kind})
        if '/actions' in cap.final_url and '/actions/' in a['href'] and label:
            links.append({'url':public_url(a['href'],cap.final_url),'kind':'PROMOTION'})
    for a in soup.select('.pagination a[href],.pager a[href],.tf-pagination a[href],[class*=pagination] a[href],[class*=pager] a[href]'):
        url=public_url(a['href'],cap.final_url)
        # A campaign pager may link back to a general offer catalogue. Stay in
        # the exact observed surface; parent catalogue pages are not campaign pages.
        if urlsplit(url).path!=urlsplit(cap.final_url).path:continue
        active_campaign='/top-offers/' in cap.final_url and not urlsplit(cap.final_url).path.rstrip('/').endswith('/top-offers') and '/top-offers/novinki/' not in cap.final_url
        if active_campaign or '/catalog/new/' in cap.final_url or '/top-offers/novinki/' in cap.final_url:links.append({'url':url,'kind':'PROMOTION' if active_campaign else 'NOVELTY_INDEX'})
    for link in links:
        if link['url'] in seen:continue
        seen.add(link['url']);yield link
