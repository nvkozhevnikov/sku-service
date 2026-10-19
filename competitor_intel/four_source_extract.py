"""Adapters for DOM observed during the bounded October 2026 source audit."""
import json,re
from datetime import date
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .model import Page,Item,canonical,text,money,parse_date,campaign_dates,inclusion,price_type,MONTHS

EXTRACTOR='four-sources-1.3'
def node_text(root,selector):
    el=root.select_one(selector)
    return text(el.get_text(' ',strip=True)) if el else None

def extract(code,cap,kind,since=date(2026,9,1),today=date(2026,10,12)):
    if code=='youmet':
        from .youmet_adapter import extract_youmet
        return extract_youmet(cap,kind,since,today)
    soup=BeautifulSoup(cap.html,'html.parser');base=urljoin(cap.final_url,soup.base.get('href','')) if soup.base else cap.final_url
    main=soup.select_one('main') or soup.select_one('.page-content') or soup.select_one('.content') or soup
    title=node_text(soup,'h1') or node_text(soup,'title') or cap.final_url
    page=Page(code,canonical(cap.final_url),cap.url,title,kind,'','')
    page.extractor_version=EXTRACTOR
    card_root=main
    if kind=='BLOG':cards=[]
    else:cards=card_root.select('a.product[data-product]') if code=='jet_online' else card_root.select('.productCard') if code=='stanki_ru' else card_root.select('.shk-item.product') if code=='titaniumgroup' else []
    identities=set()
    for c in cards:
        if code=='jet_online':
            name=node_text(c,'.pn');href=c.get('href');cur=node_text(c,'.product-price .price');old=node_text(c,'.old-price')
            try:data=json.loads(c.get('data-product','{}'))
            except (ValueError,TypeError):data={} # Broken analytics attributes never supply price/identity facts.
            brand=data.get('brand');model=node_text(c,'.article');category=data.get('category');available=node_text(c,'.out');discount=None
        elif code=='stanki_ru':
            a=c.select_one('a.productCard__name[href]');name=node_text(c,'.productCard__name');href=a.get('href') if a else None
            cur=node_text(c,'.productCard__price:not(.productCard__price_old) [data-currency="rub"]')
            old=node_text(c,'.productCard__price_old [data-currency="rub"]')
            brand=node_text(c,'.productCard__brand');model=None;category=node_text(c,'.productCard__section');available=node_text(c,'.productCard__status');discount=node_text(c,'.productCard__priceDiscount')
        else:
            a=c.select_one('a.title[href]');name=node_text(c,'a.title');href=a.get('href') if a else None
            cur=node_text(c,'.info .price');old=node_text(c,'.oldprice');brand=node_text(c,'.brand strong');model=None;category=None;available=node_text(c,'.status');discount=node_text(c,'.badge')
        if not name or not href:continue
        # Ordinary homepage catalogue cards are not artificial promotional members.
        if kind=='HOMEPAGE_BLOCK' and not old:continue
        url=canonical(href,base)
        if url in identities:continue
        identities.add(url)
        label=cur or ''
        item=Item(name,url,campaign_name=title,brand=brand,model=model,category=category,old_price=money(old),new_price=money(cur),price_type=price_type(label,money(old)),price_label_raw=label,displayed_discount_raw=discount,availability=available,region='Россия',evidence_html=str(c)).finish()
        if re.search(r'звоните|запросить цену|по запросу',label,re.I):item.price_type='UNKNOWN';item.new_price=item.old_price=None
        if cur and item.new_price is None and item.price_type in ('PUBLIC','SALE'):item.warnings.append('UNPARSED_PRICE')
        page.items.append(item)
    # Keep only content DOM, excluding global navigation/forms and currency alternatives.
    if code=='stanki_ru' and kind=='NEWS':body=soup.select_one('.news-detail__section') or main
    elif code=='jet_online' and kind=='NEWS':body=soup.select_one('main') or main
    elif code=='titaniumgroup' and kind in ('NEWS','BLOG'):body=soup.select_one('.page-content') or main
    else:body=main
    for n in body.select('script,style,header,footer,nav,form,svg'):n.decompose()
    page.raw_text=body.get_text('\n',strip=True);page.normalized_text=text(page.raw_text)
    if kind in ('NEWS','BLOG'):
        date_el=main.select_one('.date-publication') if code=='titaniumgroup' else main.select_one('time[datetime],.news-detail__date,.news-detail-date,.date,.text-muted')
        published=parse_date((date_el.get('datetime') or date_el.get_text(' ',strip=True)) if date_el else '')
        if not published:
            # Date is accepted only from visible content, never URL year or file mtime.
            published=parse_date(page.normalized_text[:1400])
        page.published_at=published
        if published:page.publication_evidence={'method':'VISIBLE_CONTENT_DATE','value':str(published),'raw_capture_ref':cap.raw_ref}
    page.valid_from,page.valid_to=campaign_dates(page.normalized_text,page.published_at.year if page.published_at else None)
    if page.published_at and not page.valid_to:
        m=re.search(r'до\s+(\d{1,2}\s+(?:'+'|'.join(MONTHS)+r'))(?:\s+(\d{4}))?',page.normalized_text,re.I)
        if m:page.valid_to=parse_date(m[1]+' '+(m[2] or str(page.published_at.year)))
    if code=='stanki_ru' and kind in ('SALE_PAGE','PROMOTION'):
        for row in soup.select('.catalogTable__row_body'):
            a=row.select_one('.catalogTable__cell_model a[href]')
            if not a:continue
            url=canonical(a['href'],base)
            if url in identities:continue
            identities.add(url);old=node_text(row,'del[data-currency="rub"]');cur=node_text(row,'span[data-currency="rub"]');availability=row.select_one('i[data-title]')
            label=cur or node_text(row,'.catalogTable__cell_price') or ''
            page.items.append(Item(text(a.get_text(' ',strip=True)),url,campaign_name=title,model=text(a.get_text(' ',strip=True)),category=title,old_price=money(old),new_price=money(cur),price_type=price_type(label,money(old)) if cur else 'UNKNOWN',price_label_raw=label,availability=availability.get('data-title') if availability else None,region='Россия',evidence_html=str(row)).finish())
    if code=='titaniumgroup' and kind=='NEWS':
        for table in main.select('table'):
            header=table.get_text(' ',strip=True)[:180].lower()
            if not all(x in header for x in ('старая цена','новая цена','скидка')):continue
            category=None
            for row in table.select('tr'):
                cells=row.find_all(['td','th'],recursive=False)
                if len(cells)==1:category=text(cells[0].get_text(' ',strip=True));continue
                if len(cells)!=4:continue
                a=cells[0].select_one('a[href]');old=money(cells[1].get_text(' ',strip=True));cur=money(cells[3].get_text(' ',strip=True))
                if not a or not old or not cur:continue
                page.items.append(Item(text(a.get_text(' ',strip=True)),canonical(a['href'],base),campaign_name=title,category=category,old_price=old,new_price=cur,price_type='UNKNOWN',price_label_raw='Published campaign table; current listing price takes precedence',displayed_discount_raw=text(cells[2].get_text(' ',strip=True)),region='Россия',evidence_html=str(row),warnings=['PUBLICATION_PRICE_CONTEXT']).finish())
    if kind=='NEWS' and (page.valid_to or code=='titaniumgroup' and page.items) and re.search(r'скид|специальн.{0,20}цен|распродаж',page.normalized_text,re.I):
        page.campaign_evidence={'current_campaign_in_news':True,'date_year_basis':'PUBLICATION_YEAR' if page.valid_to else None}
        if code=='stanki_ru':
            for a in body.select('a[href]'):
                u=canonical(a['href'],base);name=text(a.get_text(' ',strip=True))
                if '/catalog/' not in u or not name or u in identities:continue
                identities.add(u);page.items.append(Item(name,u,campaign_name=title,price_type='UNKNOWN',region='Россия',evidence_html=str(a),warnings=['CAMPAIGN_MEMBER_LINK_NO_UNIT_PRICE']).finish())
    page.inclusion_reason=inclusion(page.published_at,page.valid_from,page.valid_to,kind in ('PROMOTION','SALE_PAGE','HOMEPAGE_BLOCK'),since,today)
    if kind=='NAVIGATION_INDEX':page.inclusion_reason='LIVE_SOURCE_AUDIT'
    if kind=='HOMEPAGE_BLOCK' and not page.items:page.page_type='NAVIGATION_INDEX';page.inclusion_reason='LIVE_SOURCE_AUDIT'
    page.item_scope_complete=False
    page.source_discovery_complete=False
    return page
