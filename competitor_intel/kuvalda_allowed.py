"""Kuvalda-only public surfaces. Robots informational per operator correction."""
import re
from urllib.parse import urlsplit, urljoin, quote, unquote
from bs4 import BeautifulSoup
from .model import canonical, text, priority, price_type, parse_date, campaign_dates, inclusion, MONTHS
from .transport import HttpTransport, USER_AGENT, SourceBlocked

BASE = 'https://nnov.kuvalda.ru/'
HOST = 'nnov.kuvalda.ru'
STATUSES = ('LIVE_CRAWLED','ROBOTS_BLOCKED','INDEX_ONLY','DISCOVERED_NOT_FETCHED','ERROR')
CATEGORY_RE = re.compile(r'станк|сварочн|компрессор|автосервис|промышленн',re.I)

class KuvaldaRobots:
    """Fixed ordinary UA uses '*' group. Wildcards/end anchors + longest match.

    Do not select search-engine groups or manipulate identity to obtain access.
    Robots decisions are recorded separately from public fetch status.
    """
    def __init__(self,raw):
        groups=[]; agents=[]; rules=[]; self.sitemaps=[]
        for line in raw.splitlines():
            line=line.split('#',1)[0].strip()
            if ':' not in line: continue
            key,val=line.split(':',1); key=key.lower().strip(); val=val.strip()
            if key=='user-agent':
                if rules: groups.append((agents,rules)); agents=[]; rules=[]
                agents.append(val.lower())
            elif key in ('allow','disallow') and agents:
                if val: rules.append((key,val))
            elif key=='sitemap': self.sitemaps.append(val)
        if agents: groups.append((agents,rules))
        self.rules=[r for agents,rules in groups if '*' in agents for r in rules]
        self.has_group=any('*' in agents for agents,_ in groups)

    def decision(self,url):
        p=urlsplit(url)
        if p.netloc!=HOST or p.scheme!='https': return False,'OUTSIDE_NNOV'
        if p.path=='/robots.txt': return True,'ROBOTS_IMPLICIT_ALLOWED'
        if not self.has_group: return False,'ROBOTS_STAR_GROUP_UNCONFIRMED'
        candidate=quote(p.path or '/',safe='/%:@!$&\'()*+,;=-._~')+('?' + p.query if p.query else '')
        # Decode only unreserved octets, preserving reserved separators.
        def norm(s):
            return re.sub(r'%([0-9a-fA-F]{2})',lambda m:chr(int(m[1],16)) if chr(int(m[1],16)) in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~' else '%'+m[1].upper(),s)
        candidate=norm(candidate)
        matches=[]
        for kind,pattern in self.rules:
            pattern=norm(pattern)
            end=pattern.endswith('$'); body=pattern[:-1] if end else pattern
            regex='^'+'.*'.join(re.escape(part) for part in body.split('*'))+('$' if end else '')
            if re.search(regex,candidate): matches.append((len(body.replace('*','').encode()),kind=='allow',pattern))
        if not matches: return True,'NO_MATCHING_DISALLOW'
        _,allowed,rule=max(matches)
        return allowed,('ALLOW: ' if allowed else 'DISALLOW: ')+rule

    def can_fetch(self,user_agent,url):
        if user_agent!=USER_AGENT: return False
        return self.decision(url)[0]

class KuvaldaHttp(HttpTransport):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.resource_gaps=[]; self.tab_gaps=[]
    def permitted(self,url):
        p=urlsplit(url)
        path=unquote(p.path).lower()
        private=re.match(r'^/(?:api|auth|admin|_backend|account|member|cart|order|payment|wishlist)(?:/|$)',path)
        return p.scheme=='https' and p.netloc==HOST and not private and not p.username and not p.password
    def prepare(self):
        cap=super().get(BASE+'robots.txt',False)
        self.robots[HOST]=KuvaldaRobots(cap.html)
        return cap
    def get(self,url,check_robots=True):
        if HOST not in self.robots and urlsplit(url).path!='/robots.txt': self.prepare()
        # Exact scoped operator change: robots informational, actual access controls
        # still enforced by HTTP transport and public-path allowlist.
        return super().get(url,False)

def navigation(url,html):
    """First-level industrial sale links + conservative product/category samples."""
    soup=BeautifulSoup(html,'html.parser'); result={}
    for a in soup.select('a[href]'):
        try: target=canonical(a['href'],url)
        except ValueError: continue
        if urlsplit(target).netloc!=HOST or urlsplit(target).query: continue
        label=text(a.get_text(' ',strip=True)); path=urlsplit(target).path
        if path.startswith('/sale/'):
            selected=bool(CATEGORY_RE.search(label))
            result[target]={'url':target,'label':label,'kind':'SALE_CATEGORY','selected':selected,'reason':'INDUSTRIAL_SALE_PRIORITY' if selected else 'UNRELATED_SALE_NOT_SELECTED'}
        elif path.startswith('/catalog/') and CATEGORY_RE.search(label):
            kind='PRODUCT' if '/product-' in path else 'CATEGORY'
            result[target]={'url':target,'label':label,'kind':kind,'selected':False,'reason':'BOUNDED_ALLOWED_NAVIGATION_SAMPLE'}
    return list(result.values())

def parse_allowed(url,html,since,today,listing_proof=None):
    from .parser import parse
    page=parse('kuvalda_nnov',url,html,since,today)
    page.extractor_version='kuvalda-allowed-1.1'
    p=urlsplit(url).path
    if p in ('/promo/','/news/'):
        page.page_type='PROMOTION_INDEX' if p=='/promo/' else 'NEWS_INDEX'
        page.inclusion_reason='CURRENT_PUBLIC_NAVIGATION'
        page.published_at=page.valid_from=page.valid_to=None
    elif p.startswith('/promo/item-'):
        soup=BeautifulSoup(html,'html.parser')
        section=soup.select_one('.section.container')
        status=soup.select_one('.page-header__status')
        details=text(section.get_text(' ',strip=True)) if section else page.normalized_text
        page.normalized_text=text(page.title+' '+(status.get_text(' ',strip=True) if status else '')+' '+details)
        page.valid_from,page.valid_to=campaign_dates(details)
        if listing_proof and listing_proof.get('valid_to'):
            proven_end=parse_date(listing_proof['valid_to'])
            if page.valid_to and page.valid_to!=proven_end: page.warnings.append('CAMPAIGN_DATE_CONFLICT')
            elif not page.valid_to: page.valid_to=proven_end
            page.campaign_evidence=listing_proof
        # The single year at the end of an explicit same-year range applies to
        # both dates. Never derive it from observation time or indexed snippets.
        mo='|'.join(MONTHS)
        m=re.search(r'с\s+(\d{1,2})\s+('+mo+r')\s+по\s+(\d{1,2})\s+('+mo+r')\s+(\d{4})',details.lower())
        if m and MONTHS[m[2]]<=MONTHS[m[4]]:
            page.valid_from=parse_date(f'{m[1]} {m[2]} {m[5]}')
            page.valid_to=parse_date(f'{m[3]} {m[4]} {m[5]}')
        page.inclusion_reason=inclusion(page.published_at,page.valid_from,page.valid_to,True,since,today)
        if not page.items and soup.select_one('.snippet_placeholder'): page.warnings.append('BROWSER_RENDER_REQUIRED')
    # Category/product pages are observed public listings, not undated campaigns.
    if p.startswith('/catalog/'):
        page.page_type='PRODUCT_OBSERVATION' if '/product-' in p else 'CATEGORY_OBSERVATION'
        page.inclusion_reason='CURRENT_ALLOWED_PRODUCT_OBSERVATION'
        page.published_at=page.valid_from=page.valid_to=None
    if page.page_type in ('SALE_PAGE','CATEGORY_OBSERVATION'):
        for item in page.items:
            item.category=page.title
            item.priority=priority(item.product_name,item.category)
    for item in page.items:
        if item.price_type=='UNKNOWN':
            role=price_type(item.price_label_raw,item.old_price)
            if role in ('AUTH_REQUIRED','PERSONAL','FROM_PRICE','INSTALLMENT_MONTHLY','CREDIT_PAYMENT','BONUS','DISCOUNT_PERCENT_ONLY'):
                item.price_type=role
    # Extraction of product-detail prices is intentionally fail-closed: a header
    # without a supported card is evidence of page reachability, not a price.
    return page

def blocks(html):
    s=BeautifulSoup(html,'html.parser'); result=[]
    for section in s.select('.section'):
        title=section.select_one('.section__title')
        if title and section.select_one('.promo-slider'):
            result.append({'block_title':text(title.get_text(' ',strip=True)),
                           'tabs':[{'title':text(b.get_text(' ',strip=True)),'endpoint':b.get('data-tabs-fetch')} for b in section.select('.alt-tabs__item')],
                           'initial_endpoint':section.select_one('.promo-slider').get('data-fetch')})
    return result
