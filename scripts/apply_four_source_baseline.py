"""Project verified public captures only into isolated competitor live baseline."""
import json,hashlib,re
from collections import Counter
from pathlib import Path
from datetime import datetime,timezone,date
from urllib.robotparser import RobotFileParser
from psycopg.types.json import Jsonb
from bs4 import BeautifulSoup
from competitor_intel.store import Store,json_value
from competitor_intel.transport import Capture,USER_AGENT
from competitor_intel.source_transport import decode_public_html
from competitor_intel.four_source_extract import extract
from competitor_intel.business_data import read_state,fingerprints,normalized_dataset
from competitor_intel.business_report import build_facts,preview_from_store
from competitor_intel.model import money

ROOT=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12')
def load_captures(code,cfg):
    directory=ROOT/'audit'/code;a=json.loads((directory/'AUDIT.json').read_text(encoding='utf-8'))
    rows=[{**x,'kind':'HOMEPAGE_BLOCK'} for x in a['captures']]
    for x in a.get('surfaces',[]):
        path=x['url'].removeprefix(cfg['base_url'])
        kind='SALE_PAGE' if path in ('sale/','special_offers/','actions/') else 'NAVIGATION_INDEX'
        rows.append({**x,'kind':kind})
    for name in ('LIVE_EVIDENCE.json','EXTRA_EVIDENCE.json'):
        if (directory/name).exists():rows+=json.loads((directory/name).read_text(encoding='utf-8'))['captures']
    if code=='stanki_ru':rows.append(json.loads((ROOT/'STANKI_SPEC.json').read_text(encoding='utf-8')))
    return a,rows

def qa(page):
    errors=[]
    if '\ufffd' in page.title or '\ufffd' in page.normalized_text:errors.append('ENCODING_REPLACEMENT')
    for i in page.items:
        s=BeautifulSoup(i.evidence_html,'html.parser');visible=s.get_text(' ',strip=True)
        values={money(el.get_text(' ',strip=True)) for el in s.find_all(['span','del','s','td','div'])}
        for value in (i.new_price,i.old_price):
            if value is not None and value not in values:errors.append('PRICE_NOT_VISIBLE_IN_CARD')
        if any(w in i.warnings for w in ('INVALID_OLD_NEW_PRICE','UNPARSED_PRICE')):errors.append('PRICE_ROLE_CORRUPTION')
        if re.search(r'после авторизац|персональная цена|войдите.*цен',visible,re.I) and i.price_type in ('PUBLIC','SALE'):errors.append('PERSONAL_PUBLIC')
    return sorted(set(errors))

if __name__=='__main__':
    cfgs=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'));s=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
    result={'sources':{},'errors':[],'ai_network_calls':0,'scheduler_enabled':False,'universal_supplier_kami_writes':0}
    try:
        old=fingerprints(read_state(s))['sources'];run=s.run_start(date(2026,9,1))
        seeds=s.query("SELECT payload FROM competitor_historical_seeds WHERE is_live=false");lookup={r['payload']['product_url']:r['payload'] for r in seeds}
        refresh={};avoided=set();contexts=[]
        for code,cfg in cfgs.items():
            with s.conn.transaction(),s.conn.cursor() as c:
                c.execute('INSERT INTO competitor_sources(code,name,base_url,region) VALUES(%s,%s,%s,%s) ON CONFLICT(code) DO NOTHING',(code,cfg['name'],cfg['base_url'],'Россия'))
            audit,rows=load_captures(code,cfg);rp=None
            if audit.get('robots_capture'):rp=RobotFileParser();rp.parse(Path(audit['robots_capture']).read_text(encoding='utf-8').splitlines())
            stats={'pages':0,'versions':0,'items':0,'excluded':0,'qa_pages':0,'price_types':Counter(),'duplicates':0,'refresh_candidates':0};seen=set()
            for row in rows:
                url=row['url']
                if url in seen:continue
                seen.add(url);fetch=row.get('fetch_status','PUBLIC_FETCH_OK')
                ok=bool(row.get('raw_capture_ref')) and fetch in ('PUBLIC_FETCH_OK','BROWSER_FETCH_OK');errs=[];p=None
                if ok:
                    raw=Path(row['raw_capture_ref']).read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=row['raw_hash']:raise ValueError('RAW_CAPTURE_HASH_MISMATCH')
                    if row['kind'] not in ('SITEMAP_INDEX','PROMOTION_INDEX'):
                        cap=Capture(url,url,decode_public_html(raw),row['raw_capture_ref'],row['raw_hash'],datetime.fromisoformat(row['observed_at']),'HTTP')
                        p=extract(code,cap,row['kind']);errs=qa(p)
                        if code=='stanki_ru':
                            p.historical_context={i.product_url:{k:lookup[i.product_url].get(k) for k in ('seed_id','product_name','brand','model','category','original_capture_time','evidence_class','is_live')} for i in p.items if i.product_url in lookup}
                            contexts.extend({'live_url':u,**v} for u,v in p.historical_context.items())
                            for i in p.items:
                                if p.page_type in ('PROMOTION','SALE_PAGE','HOMEPAGE_BLOCK') or getattr(p,'campaign_evidence',{}).get('current_campaign_in_news'):
                                    refresh[i.product_url]={'url':i.product_url,'reason':'CURRENT_CAMPAIGN_MEMBER','historical_context':i.product_url in lookup,'live_listing_price_present':i.new_price is not None}
                                    if i.product_url in lookup and (not i.brand or not i.category or not i.model):avoided.add(i.product_url)
                        if errs:result['errors'].append({'source':code,'url':url,'qa_errors':errs})
                        elif p.inclusion_reason:
                            written=s.save(p,cap,run)
                            for k in ('pages','versions','items','excluded'):stats[k]+=written[k]
                            with s.conn.transaction(),s.conn.cursor() as c:
                                c.execute("UPDATE competitor_page_versions v SET qa_status='ACCEPTED' FROM competitor_pages p,competitor_sources src WHERE v.page_id=p.id AND p.source_id=src.id AND src.code=%s AND p.canonical_url=%s AND v.content_hash=%s",(code,p.canonical_url,p.content_hash()))
                            stats['qa_pages']+=1;stats['price_types'].update(i.price_type for i in p.items)
                        else:stats['excluded']+=1
                status='LIVE_CRAWLED' if ok and not errs else 'ERROR'
                if not ok:result['errors'].append({'source':code,'url':url,'fetch_status':fetch,'error':row.get('error','ACCESS_FAILURE')})
                reason='NORMAL_PUBLIC_CAPTURE_QA_PASS' if ok and not errs else 'ACTUAL_ACCESS_FAILURE' if not ok else 'EXTRACTOR_QA_FAILED'
                with s.conn.transaction(),s.conn.cursor() as c:
                    c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
                    SELECT id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM competitor_sources WHERE code=%s
                    ON CONFLICT(source_id,canonical_url) DO UPDATE SET status=excluded.status,fetch_status=excluded.fetch_status,robots_disallowed=excluded.robots_disallowed,evidence_class=excluded.evidence_class,reason=excluded.reason,observed_at=excluded.observed_at,raw_capture_ref=excluded.raw_capture_ref,details=excluded.details''',
                    (url,row['kind'],p.title if p else row.get('title'),status,not rp.can_fetch(USER_AGENT,url) if rp else None,fetch if not errs else 'ERROR','LIVE_CRAWLED' if ok and not errs else 'TRANSPORT_ERROR',reason,row.get('observed_at',datetime.now(timezone.utc)),row.get('raw_capture_ref'),Jsonb(json_value({**row,'qa_errors':errs,'inclusion_reason':p.inclusion_reason if p else None})),code))
            if code=='youmet':result['errors'].append({'source':code,'reason':'PUBLIC_ACCESS_NOT_RELIABLY_AVAILABLE','HTTP':'403','browser':'public DOM observed once; next ordinary visit inaccessible; no further retry/evasion'})
            result['sources'][code]=stats
        plan={'historical_seed_rows':len(lookup),'refresh_candidates':len(refresh),'selected':[],'deferred':list(refresh.values()),'refresh_deferred':len(refresh),'product_detail_GETs_avoided':len(avoided),'avoided_exact_urls':sorted(avoided),'targeted_product_detail_GETs':0,'historical_context_rows':len(contexts),'reasons_allowed':['CURRENT_CAMPAIGN_MEMBER','EXPLICIT_SOURCE_CHANGE','OPERATOR_REQUESTED_REFRESH'],'full_catalog':False}
        (ROOT/'REFRESH_PLAN.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT/'HISTORICAL_CONTEXT_USED.json').write_text(json.dumps(contexts,ensure_ascii=False,indent=2),encoding='utf-8')
        after=fingerprints(read_state(s));result['frozen_sources_unchanged']=all(old[k]==after['sources'][k] for k in ('kuvalda_nnov','metalmaster'))
        if not result['frozen_sources_unchanged']:raise ValueError('Frozen source mutation')
        result['efficiency']=plan
        result['status']='COMPETITOR_INTELLIGENCE_FOUR_SOURCE_ACCESS_INCOMPLETE'
        s.run_finish(run,result)
        (ROOT/'BASELINE_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='efficiency'},ensure_ascii=False,default=str))
    finally:s.close()
