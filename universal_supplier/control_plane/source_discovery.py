"""Source-specific discovery decoding; network belongs exclusively to host_fetch.

Sitemap candidates are provisional. Only the existing detail parser establishes
identity. KAMI retains ordinary listing/category traversal and pagination.
"""
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from .source_jobs import validate_url
from universal_supplier.full_supplier_collection import ROOT_SITEMAPS, _safe_source_url
from universal_supplier.ltt_uss_capture import SOURCE_HOSTS, safe_source_url, is_product_candidate

HOSTS={**{k:urlsplit(v).hostname for k,v in ROOT_SITEMAPS.items()},
       **{k:next(iter(v)) for k,v in SOURCE_HOSTS.items()}, 'kami':'www.stanki.ru'}

def root_urls(code):
    if code=='kami':return ['https://www.stanki.ru/catalog/','https://www.stanki.ru/bu/']
    return [ROOT_SITEMAPS.get(code,'https://'+HOSTS[code]+'/sitemap.xml')]

def validate_node(code,url):
    p=urlsplit(url)
    if p.port not in (None,443) or p.fragment:raise ValueError('UNSAFE_DISCOVERY_URL')
    if code=='kami':
        from scripts.discover_kami_catalog import traversal_url_is_safe
        if not traversal_url_is_safe(url):raise ValueError('UNSAFE_KAMI_TRAVERSAL')
    elif code in ROOT_SITEMAPS:_safe_source_url(code,url,sitemap=True)
    else:safe_source_url(code,url,sitemap=True)
    return url

def decode_node(code,url,body):
    """Return (child discovery nodes, provisional product entries)."""
    if code=='kami':
        from universal_supplier.adapters.kami import parse_listing
        text=body.decode('utf-8');soup=BeautifulSoup(text,'html.parser')
        products=[{'url':v['url'],'discovery_evidence':'productCard'} for v in parse_listing(text,url)]
        children=[]
        for a in soup.select('a[href]'):
            target=urljoin(url,a['href']);p=urlsplit(target)
            category=p.path.startswith(('/catalog/','/bu/')) and len(p.path.strip('/').split('/'))<=2
            page=a.has_attr('data-pagination-button') and bool(p.query)
            if not (category or page):continue
            try:validate_node(code,target)
            except ValueError:
                if page:raise ValueError('UNSAFE_REQUIRED_PAGINATION')
                continue
            children.append(target)
        return sorted(set(children)),list({x['url']:x for x in products}.values())
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():raise ValueError('XML_ENTITY_FORBIDDEN')
    root=ET.fromstring(body);kind=root.tag.rsplit('}',1)[-1]
    locs=[x.text.strip() for x in root.iter() if x.tag.rsplit('}',1)[-1]=='loc' and x.text]
    if kind=='sitemapindex':
        children=[]
        for target in locs:
            if target.endswith('sitemap-files.xml'):continue
            validate_node(code,target);children.append(target)
        return sorted(set(children)),[]
    if kind!='urlset':raise ValueError('UNEXPECTED_SITEMAP_ROOT')
    products=[]
    for target in locs:
        try:
            validate_url(code,target)
            if code in SOURCE_HOSTS and not is_product_candidate(code,target)[0]:continue
        except ValueError:continue
        products.append({'url':target,'discovery_evidence':'sitemap_candidate_not_identity'})
    return [],list({x['url']:x for x in products}.values())
