"""Offline operator HTML import. No HTTP, browser, HAR or credential handling."""
import hashlib,json,re
from datetime import datetime,timezone,date
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from bs4 import BeautifulSoup,Comment
from psycopg.types.json import Jsonb
from .transport import Capture,challenge
from .source_transport import decode_public_html
from .youmet_adapter import public_url,discover
from .four_source_extract import extract

SECRET_KEY=re.compile(r'cookie|authorization|password|passwd|session|sessid|csrf|token|localstorage|secret|jwt',re.I)
SECRET_TEXT=re.compile(r'(?:authorization\s*:\s*bearer\s+\S+|bearer\s+[A-Za-z0-9._~+/=-]+|(?:cookie|password|passwd|session[_-]?(?:id|token)?|sessid|csrf[_-]?token|access[_-]?token|refresh[_-]?token|localstorage|secret)\s*["\x27]?\s*[:=]\s*[^\s<,;]+|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)',re.I)
SAFE_ATTRS={'class','itemprop','itemtype','itemscope','datetime','content','title','alt','data-currency','data-value','colspan','rowspan'}
SAFE_META={'price','priceCurrency','availability','brand','model','category','name','datePublished','startDate','endDate'}

def checked_time(value):
    try:t=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (TypeError,ValueError,AttributeError):raise ValueError('Explicit ISO8601 capture time with timezone required') from None
    if t.tzinfo is None:raise ValueError('Capture time must include timezone; file mtime is not capture time')
    if t>datetime.now(timezone.utc):raise ValueError('Future capture time refused')
    return t

def checked_original_url(url):
    p=urlsplit(url)
    if p.fragment or any(SECRET_KEY.search(k) or k.lower() in ('login','logout','auth') for k,_ in parse_qsl(p.query,keep_blank_values=True)) or SECRET_TEXT.search(url):raise ValueError('Secret-bearing or authentication URL refused')
    public_url(url)
    return url

def decode_saved_html(data):
    # Copy outerHTML saved as UTF8 can retain an old windows-1251 meta tag.
    # Detect file encoding before consulting the original HTML declaration.
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):return data.decode('utf-16')
    try:return data.decode('utf-8-sig')
    except UnicodeDecodeError:return decode_public_html(data)

def sanitize_html(html,url):
    """Persist only inert business DOM; original source file stays read-only."""
    soup=BeautifulSoup(html,'html.parser')
    for n in soup.select('script,style,form,input,textarea,select,button,iframe,object,embed,link,base,noscript'):n.decompose()
    for n in soup.find_all(string=lambda s:isinstance(s,Comment)):n.extract()
    for n in list(soup.find_all(True)):
        if n.name is None or n.attrs is None:continue
        identity=' '.join(str(n.get(k,'')) for k in ('class','id','name'))
        if SECRET_KEY.search(identity):n.decompose();continue
        if n.name=='meta' and n.get('itemprop') not in SAFE_META:n.decompose();continue
        for key in list(n.attrs):
            val=n.attrs[key]
            if key=='href':
                try:
                    u=public_url(val,url);p=urlsplit(u)
                    query=parse_qsl(p.query,keep_blank_values=True)
                    if any(SECRET_KEY.search(k) or k.lower() in ('login','logout','auth') or SECRET_TEXT.search(v) for k,v in query):raise ValueError('Secret-bearing link refused')
                    n[key]=urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),''))
                except (TypeError,ValueError):del n.attrs[key]
            elif key=='id' and val in ('content','main','page'):continue
            elif key not in SAFE_ATTRS or SECRET_KEY.search(key) or SECRET_TEXT.search(str(val)):del n.attrs[key]
    for n in list(soup.find_all(string=True)):
        cleaned=SECRET_TEXT.sub('[REDACTED]',str(n))
        if cleaned!=str(n):n.replace_with(cleaned)
    clean=str(soup)
    if SECRET_TEXT.search(clean):raise ValueError('Capture privacy sanitization failed')
    return clean

def prepare_capture(file,url,capture_time,kind='AUTO',since=date(2026,9,1)):
    original=checked_original_url(url);at=checked_time(capture_time);path=Path(file)
    if path.stat().st_size>30*1024*1024:raise ValueError('Capture exceeds bounded 30MB HTML limit')
    raw=path.read_bytes();html=decode_saved_html(raw).lstrip('\ufeff')
    if not html.lstrip().startswith('<'):raise ValueError('Only HTML/outerHTML accepted; HAR/JSON profiles are not supported')
    if challenge(html):raise ValueError('Challenge/CAPTCHA DOM is not public business evidence')
    initial=BeautifulSoup(html,'html.parser')
    h1=initial.select_one('h1')
    if h1 and re.search(r'captcha|проверка браузера|подтвердите.{0,30}человек|доступ ограничен|access denied|доступ запрещ',h1.get_text(' ',strip=True),re.I):raise ValueError('Challenge/CAPTCHA DOM is not public business evidence')
    if h1 and re.search(r'^\s*(?:авторизация|вход в личный кабинет|login|sign in)\s*$',h1.get_text(' ',strip=True),re.I):raise ValueError('Authentication page refused')
    if any('logout' in a.get('href','').lower() for a in initial.select('a[href]')):raise ValueError('Authenticated capture refused; use an ordinary public page')
    clean=sanitize_html(html,original);digest=hashlib.sha256(clean.encode()).hexdigest()
    cap=Capture(original,public_url(original),clean,'',digest,at,'OPERATOR_BROWSER_CAPTURE')
    page=extract('youmet',cap,kind,since,at.date())
    page.extractor_version+='/operator-v1'
    metadata={'original_url':original,'canonical_url':page.canonical_url,'capture_time':at.isoformat(),'capture_method':'OPERATOR_BROWSER_CAPTURE','content_hash':digest,'source_domain':'youmet.ru','fetch_status':'OPERATOR_CAPTURE','evidence_class':'PUBLIC_BROWSER_EVIDENCE','sanitization':'INERT_ALLOWLIST_DOM_V1','input_sha256':hashlib.sha256(raw).hexdigest(),'is_crawler_verified':False}
    page.capture_provenance={k:metadata[k] for k in ('capture_method','content_hash','source_domain','fetch_status','evidence_class','is_crawler_verified')}
    metadata['source_region']=page.capture_region
    page.capture_provenance['source_region']=page.capture_region
    return cap,page,metadata,discover(clean,original)

def import_browser_capture(store,file,url,capture_time,output,kind='AUTO',since=date(2026,9,1)):
    cap,page,metadata,links=prepare_capture(file,url,capture_time,kind,since)
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    # Only sanitized bytes/metadata persist. Never copy the supplied original or
    # Save-page resource directory, profile, cookies, headers or localStorage.
    ref=out/(cap.raw_hash+'.sanitized.html')
    if ref.resolve()==Path(file).resolve():raise ValueError('Output must not overwrite supplied HTML; choose another output directory')
    ref.write_bytes(cap.html.encode('utf-8'));cap.raw_ref=str(ref.resolve())
    if page.publication_evidence:page.publication_evidence['raw_capture_ref']=cap.raw_ref
    manifest=out/(hashlib.sha256((page.canonical_url+metadata['capture_time']+cap.raw_hash).encode()).hexdigest()+'.json')
    result={'source':'youmet','status':'CAPTURE_SAVED_OUTSIDE_DATE_SCOPE','fetch_status':'OPERATOR_CAPTURE','provenance':metadata,'discovered':links,'source_HTTP':0,'AI_NETWORK_CALLS':0,'scheduler_enabled':False}
    with store.conn.transaction():
        with store.conn.cursor() as c:
            c.execute(Path(__file__).with_name('operator_capture_schema.sql').read_text(encoding='utf-8'))
            c.execute("SELECT id FROM competitor_sources WHERE code='youmet'");source=c.fetchone()
            if not source:raise ValueError('Register isolated YouMet source before importing capture')
            c.execute('SELECT last_seen_at FROM competitor_pages WHERE source_id=%s AND canonical_url=%s',(source['id'],page.canonical_url));prior=c.fetchone()
            if prior and cap.fetched_at<prior['last_seen_at']:raise ValueError('Older capture cannot replace current page; import same-page captures in capture-time order')
        run=store.run_start(since);saved=store.save(page,cap,run);result.update(saved)
        with store.conn.cursor() as c:
            c.execute('''SELECT v.id FROM competitor_page_versions v JOIN competitor_pages p ON p.id=v.page_id
              WHERE p.source_id=%s AND p.canonical_url=%s AND v.content_hash=%s''',(source['id'],page.canonical_url,page.content_hash()));version=c.fetchone()
            if version:
                c.execute("UPDATE competitor_page_versions SET qa_status='ACCEPTED' WHERE id=%s",(version['id'],))
            c.execute('''INSERT INTO competitor_operator_captures(source_id,page_version_id,original_url,canonical_url,capture_time,capture_method,source_domain,content_hash,fetch_status,evidence_class,sanitized_html_ref,metadata)
             VALUES(%s,%s,%s,%s,%s,'OPERATOR_BROWSER_CAPTURE','youmet.ru',%s,'OPERATOR_CAPTURE','PUBLIC_BROWSER_EVIDENCE',%s,%s)
             ON CONFLICT(source_id,canonical_url,content_hash,capture_time) DO NOTHING''',(source['id'],version['id'] if version else None,metadata['original_url'],page.canonical_url,cap.fetched_at,cap.raw_hash,cap.raw_ref,Jsonb(metadata)))
            # Keep automated failures as their own evidence, even for the same URL.
            c.execute('SELECT details,robots_disallowed FROM competitor_source_surfaces WHERE source_id=%s AND canonical_url=%s',(source['id'],page.canonical_url));old=c.fetchone()
            automated=(old['details'].get('automated_access_evidence') if 'operator_capture' in old['details'] else old['details']) if old else None
            details={'operator_capture':metadata,'automated_access_evidence':automated}
            c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
             VALUES(%s,%s,%s,%s,'OPERATOR_CAPTURED',%s,'OPERATOR_CAPTURE','PUBLIC_BROWSER_EVIDENCE','OPERATOR_SAVED_PUBLIC_DOM',%s,%s,%s)
             ON CONFLICT(source_id,canonical_url) DO UPDATE SET surface_kind=excluded.surface_kind,title=excluded.title,status=excluded.status,fetch_status=excluded.fetch_status,evidence_class=excluded.evidence_class,reason=excluded.reason,observed_at=excluded.observed_at,raw_capture_ref=excluded.raw_capture_ref,details=excluded.details''',(source['id'],page.canonical_url,page.page_type,page.title,old['robots_disallowed'] if old else None,cap.fetched_at,cap.raw_ref,Jsonb(details)))
            for link in links:
                if link['url']==page.canonical_url:continue
                c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
                  VALUES(%s,%s,%s,%s,'DISCOVERED_NOT_FETCHED',NULL,'NAVIGATION_EVIDENCE','OPERATOR_DOM_NAVIGATION_ONLY',%s,%s,%s)
                  ON CONFLICT(source_id,canonical_url) DO NOTHING''',(source['id'],link['url'],link['kind'],link['title'],cap.fetched_at,cap.raw_ref,Jsonb(link)))
        if version:result['status']='YOUMET_PUBLIC_BROWSER_BASELINE_VERIFIED'
        store.run_finish(run,{**result,'errors':[]})
    manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return result
