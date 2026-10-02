"""Opt-in Beka supplier-card identity, not a canonical matching relaxation.

Only saved own-card DOM is primary. Slug/JSON-LD values are retrieval aliases.
The caller must supply its approved URL scope; existing adapters are unchanged.
"""
from dataclasses import replace
import json,re
from urllib.parse import urlparse
from lxml import html
from sterbrust_matching.normalization import normalize_model
from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.bekamak_official import _products
from universal_supplier.commercial import PriceExtraction,PriceState,PriceSourceKind,ReadOnlySupplierProduct

RESOLVED='SOURCE_IDENTITY_RESOLVED_VISIBLE_CARD'
CONTRADICTION='TRUE_VISIBLE_FIELD_CONTRADICTION'
INSUFFICIENT='INSUFFICIENT_VISIBLE_IDENTITY'
STALE='SOURCE_404_STALE_URL'
STALE_URL='https://www.bekamak.com/urun/2224-gantry/en'

def text(node): return ' '.join(node.text_content().split())

def visible(node):
    for n in [node,*node.iterancestors()]:
        style=re.sub(r'\s+','',n.get('style','').lower())
        if ('hidden' in n.attrib or n.get('aria-hidden')=='true' or
            'display:none' in style or 'visibility:hidden' in style or
            set(n.get('class','').split()) & {'hidden','d-none'} or n.tag in ('script','style','template')):
            return False
    return True

def model(value):
    # Cosmetic separator/case normalization, not suffix removal. One observed
    # Cyrillic O in Latin BMSО is normalized only inside the model token.
    value=re.sub(r'(?i)BMSО','BMSO',value)
    found=re.search(r'\b(?:BMSO|BMSY|BMDO|BMH|BMS|P[- ]SAW|GANTRY)[-\s]*\d[^,;:()\n]*',value,re.I)
    if not found: return ''
    tail=found.group(0).strip()
    tail=re.sub(r'(?i)\s+с\s+кабиной\b',' ENCLOSED',tail)
    if not re.fullmatch(r'[A-Za-z0-9+\s-]+',tail): return ''
    return ' '.join(tail.upper().split())

def assess(source,url,body,*,approved_urls,operator_stale=False):
    if url not in approved_urls or source not in ('beka_mak','beka_mak_tr'):
        raise ValueError('Outside approved Beka unresolved scope')
    host=urlparse(url).hostname
    if host not in ({'beka-mak.su'} if source=='beka_mak' else {'www.bekamak.com','bekamak.com'}):
        raise ValueError('Supplier namespace/host mismatch')
    doc=html.fromstring(body)
    titles=[text(n) for n in doc.xpath('//h1') if visible(n)]
    primary=model(titles[0]) if len(titles)==1 else ''
    articles=[text(n) for n in doc.xpath('//*[contains(@class,"item_block--article")]//*[@itemprop="value"]') if visible(n)]
    # Only explicit own technical-table identity fields, never menu/recommended
    # products or text found by searching the entire page.
    technical=doc.xpath('//*[@id="teknik-detaylar"]//tr | //table[contains(@class,"props") or contains(@class,"char")]//tr')
    props=[]; contradictions=[]
    for row in technical:
        cells=row.xpath('./td | ./th')
        if len(cells)!=2 or not all(visible(n) for n in cells): continue
        k,v=map(text,cells)
        if k and v: props.append((k,v))
    for row in doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," props ")]/*[contains(concat(" ",normalize-space(@class)," ")," prop ")]'):
        names=row.xpath('.//*[contains(@class,"char_name")]'); values=row.xpath('.//*[contains(@class,"char_value")]')
        if len(names)==len(values)==1 and visible(names[0]) and visible(values[0]):
            props.append((text(names[0]),text(values[0])))
    for k,v in props:
        if re.fullmatch(r'(?i)model|модель|execution|исполнение',k) and model(v) and normalize_model(model(v))!=normalize_model(primary):
            contradictions.append('VISIBLE_CHARACTERISTIC_MODEL_DISAGREES')
        if re.fullmatch(r'(?i)sku|article|артикул|product code|ürün kodu',k): articles.append(v)
    for article in articles:
        article_model=model(article)
        if not article_model or normalize_model(article_model)!=normalize_model(primary):
            contradictions.append('VISIBLE_H1_ARTICLE_FULL_EXECUTION_DISAGREES')
    if len(titles)>1: contradictions.append('MULTIPLE_VISIBLE_PRIMARY_TITLES')
    aliases=[{'kind':'url_alias','value':urlparse(url).path.split('/')[-2 if source=='beka_mak_tr' else -2],
              'provenance':url,'use':'CANDIDATE_RETRIEVAL_ONLY'}]
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        try: records=list(_products(json.loads(script.text or '')))
        except (ValueError,TypeError): continue
        for record in records:
            if record.get('url') and record['url'].rstrip('/')!=url.rstrip('/'): continue
            for key in ('sku','name','mpn'):
                if record.get(key): aliases.append({'kind':'metadata_alias','field':key,'value':str(record[key]),
                    'provenance':'script[type=application/ld+json].Product.'+key,'use':'CANDIDATE_RETRIEVAL_ONLY'})
    kind='saw_machine' if any(re.search(r'(?i)blade|band dimensions|cutting capacity',k) for k,v in props) or any('ленточнопильный станок' in t.lower() for t in titles) else 'unknown'
    if primary and re.search(r'(?i)jaws|губки|accessory|запасн',titles[0]):
        # A reference model embedded in an accessory name is not own machine identity.
        contradictions.append('ACCESSORY_MACHINE_IDENTITY_CONTEXT')
    if operator_stale:
        if url!=STALE_URL: raise ValueError('Operator404 authorization is URL-specific')
        verdict=STALE; reasons=['OPERATOR_CONFIRMED_404_STALE_URL_NOT_CANONICAL_ABSENCE']
    elif contradictions: verdict=CONTRADICTION; reasons=sorted(set(contradictions))
    elif not primary or kind=='unknown': verdict=INSUFFICIENT; reasons=['VISIBLE_OWN_FULL_MODEL_OR_PRODUCT_KIND_MISSING']
    else: verdict=RESOLVED; reasons=['VISIBLE_OWN_FULL_MODEL_AND_CONTEXT_CONSISTENT','SECONDARY_ALIASES_DO_NOT_OVERRIDE_VISIBLE_MODEL']
    return {'source':source,'url':url,'verdict':verdict,'visible_title':titles,'visible_primary_model':primary,
        'visible_article':articles,'visible_properties':props,'visible_product_kind':kind,'aliases':aliases,
        'reasons':reasons,'rule_provenance':'OPERATOR-CONFIRMED 2026-10-01',
        'canonical_identity_confirmed':False,'canonical_absence_proven':False}

def product_from_assessment(a,body):
    if a['verdict']!=RESOLVED: raise ValueError('Unresolved/stale card cannot be ingested')
    evidence={'supplier_page_identity':a,'alias_policy':'CANDIDATE_RETRIEVAL_ONLY_NOT_AUTO_CONFIRM'}
    if a['source']=='beka_mak':
        p=parse_bekamak_detail(body,source_url=a['url'])
        return replace(p,supplier_model=a['visible_primary_model'],raw_supplier_model=a['visible_primary_model'],
            manufacturer_article=a['visible_article'][0] if a['visible_article'] else None,
            technical_properties=tuple(a['visible_properties']),enrichment_evidence=evidence)
    price=PriceExtraction(PriceState.MISSING,None,None,None,'',PriceSourceKind.NONE,None,
        ('official_reference_no_verified_numeric_price',),a['visible_primary_model'],a['url'],'visible_card')
    return ReadOnlySupplierProduct(supplier='beka_mak_tr',source_url=a['url'],name=a['visible_title'][0],
        supplier_model=a['visible_primary_model'],raw_supplier_model=a['visible_primary_model'],
        manufacturer_article=a['visible_article'][0] if a['visible_article'] else None,
        site_internal_id=urlparse(a['url']).path.split('/')[-2],availability=None,price=price,
        technical_properties=tuple(a['visible_properties']),source_category=('Circular saw' if any('Blade OD' in k for k,v in a['visible_properties']) else 'Band saw'),
        diagnostics=('visible_card_identity_primary','hidden_metadata_alias_not_manufacturer_article',
                     'official_reference_not_verified_commercial_stock'),enrichment_evidence=evidence)
