"""Exact recorded onboarding targets, shared CLI execution contract, no discovery."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,time
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb
from competitor_intel.collection_adapter import Repository,rebuild_report
from competitor_intel.execution import run_competitor_collection,settings
from competitor_intel.transport import Capture,SourceBlocked
from competitor_intel.source_transport import SourceTransport
from competitor_intel.harvey_decode import decode_harvey
from competitor_intel.five_source_extract import extract,key,amount
from competitor_intel.model import Item,text
from competitor_intel.store import json_value
from competitor_intel.business_data import read_state,fingerprints

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/FIVE_SOURCE_GAPS_2026-10-13'

def material_kind(url):
    return 'CONTENT' if url.rstrip('/').endswith(('/articles/gibka-metalla','/articles/gidroabrazivnaya-rezka')) else 'BLOG'

def harvey_page(cap):
    p=extract('harvey_rus',cap,'PRODUCT_DETAIL');soup=BeautifulSoup(cap.html,'html.parser')
    node=soup.select_one('.new-detail__title-price')
    if node is None:raise ValueError('DETAIL_PRICE_DOM_ABSENT')
    old=node.select_one('.base__price:not(.base__price_percent)');discount=node.select_one('.base__price_percent')
    current=BeautifulSoup(str(node),'html.parser')
    for n in current.select('.base__price'):n.decompose()
    label=text(current.get_text(' ',strip=True));old_label=text(old.get_text(' ',strip=True)) if old else None
    assert '/catalog/discount/' in cap.final_url and old is not None
    i=Item(p.title,cap.final_url,tab_title='CONDITION:OUTLET',old_price=amount(old_label),new_price=amount(label),price_type='SALE',price_label_raw=label,displayed_discount_raw=discount.get_text(' ',strip=True) if discount else None,region='Россия',evidence_html=str(node)).finish()
    assert i.new_price and i.old_price and i.old_price>i.new_price and not i.warnings
    p.items=[i];p.item_metadata={key(i.product_url,i.tab_title):{'condition_identity':'OUTLET_INSTANCE','condition_label':'Дисконт / уценённый экземпляр','record_kind':'PRODUCT','original_capture_time':cap.fetched_at.isoformat(),'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'capture_page_url':cap.final_url,'unit_price_evidence':{'current_label':label,'old_label':old_label,'old_price_explicit':True}}}
    return p

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    harvey=json.loads((OUT/'HARVEY_BYTE_AUDIT.json').read_text(encoding='utf-8'))
    pending=json.loads((ROOT/'competitor-data/collection/run-34/amur_stan/SOURCE_MAP.json').read_text(encoding='utf-8'))['pending']
    planned=json.loads((ROOT/'reports/FIVE_NEW_SOURCES_2026-10-13/AMUR_CONTENT_CONFIG.json').read_text(encoding='utf-8'))['targets']['amur_stan']
    targets={r['url']:r for r in planned if r['url'] in {u for u,k in pending}}
    assert len(harvey)==3 and len(targets)==7
    style=json.loads((ROOT/'competitor-data/collection/run-33/harvey_rus/PRICE_STYLE_EVIDENCE.json').read_text(encoding='utf-8'))
    assert style['base_price_strikethrough_verified']
    for a in style['assets']:assert hashlib.sha256(Path(a['raw_capture_ref']).read_bytes()).hexdigest()==a['raw_hash']
    cfg=settings();cfg.update(selected_sources=['harvey_rus','amur_stan'],max_pages_per_source=7,max_runtime=900)
    definitions=json.loads((ROOT/'competitor_intel/five_sources.json').read_text(encoding='utf-8'));accepted=[];network=[]
    repo=Repository(ROOT/'competitor-data/dev-config.json')
    before=read_state(repo.store)
    assert not [r for r in before['competitor_runs'] if r['status']=='RUNNING']
    def collector(repo,code,run,since,output,max_pages,deadline):
        rows=harvey if code=='harvey_rus' else list(targets.values());stats={'status':'COMPLETED','pages':0,'versions':0,'items':0,'errors':[],'HTTP_GETs':0,'baseline_completion':True}
        h=SourceTransport(definitions[code],output/code/'captures') if code=='amur_stan' else None
        if h:
            original=h.session.get
            def limited(url,**kw):
                if url not in targets or len(network)>=7:raise SourceBlocked('EXACT_SEVEN_CONTENT_GET_BUDGET')
                if time.monotonic()>=deadline:raise TimeoutError('MAX_RUNTIME')
                network.append({'url':url});r=original(url,**kw);network[-1].update(status=r.status_code,content_type=r.headers.get('Content-Type'));return r
            h.session.get=limited
        try:
            for row in rows:
                try:
                    if code=='harvey_rus':
                        raw=Path(row['raw_capture_ref']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['raw_hash']
                        html,decoding=decode_harvey(raw)
                        cap=Capture(row['url'],row['url'],html,row['raw_capture_ref'],row['raw_hash'],datetime.fromisoformat(row['observed_at']),'OFFLINE_BASELINE_COMPLETION')
                        p=harvey_page(cap)
                    else:
                        cap=h.get(row['url']);p=extract(code,cap,'BLOG',publication_proof=row['published_at'])
                        p.page_type=p.content_kind=material_kind(row['url'])
                        assert p.published_at and str(p.published_at)>='2026-09-01'
                        assert not p.items or all(i.price_type not in ('PERSONAL','AUTH_REQUIRED') for i in p.items)
                    assert p.inclusion_reason and p.canonical_url not in {r['canonical_url'] for r in before['competitor_pages'] if r['source_id'] in {s['id'] for s in before['competitor_sources'] if s['code']==code}}
                    delta=repo.store.save(p,cap,run)
                    for k in ('pages','versions','items'):stats[k]+=delta[k]
                    with repo.store.conn.transaction(),repo.store.conn.cursor() as c:
                        c.execute('''UPDATE competitor_page_versions v SET qa_status='ACCEPTED' FROM competitor_pages p,competitor_sources s WHERE v.page_id=p.id AND p.source_id=s.id AND s.code=%s AND p.canonical_url=%s AND v.content_hash=%s''',(code,p.canonical_url,p.content_hash()))
                        c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
                            SELECT id,%s,%s,%s,'LIVE_CRAWLED','PUBLIC_FETCH_OK','LIVE_CRAWLED','INITIAL_BASELINE_GAP_COMPLETION',%s,%s,%s FROM competitor_sources WHERE code=%s
                            ON CONFLICT(source_id,canonical_url) DO UPDATE SET status=excluded.status,fetch_status=excluded.fetch_status,reason=excluded.reason,details=excluded.details''',(p.canonical_url,p.page_type,p.title,cap.fetched_at,cap.raw_ref,Jsonb({'capture_method':cap.transport,'initial_baseline_completion':True,'headers': 'NOT_RETAINED' if code=='harvey_rus' else network[-1].get('content_type'),'decoding':row.get('decoding')}),code))
                    accepted.append((p,cap,run));(OUT/'ACCEPTED_TARGETS.json').write_text(json.dumps(json_value([{'source':a.source,'url':a.canonical_url,'title':a.title,'type':a.page_type,'published_at':a.published_at,'valid_from':a.valid_from,'valid_to':a.valid_to,'items':[i.__dict__ for i in a.items],'raw_capture_ref':b.raw_ref,'raw_hash':b.raw_hash,'observed_at':b.fetched_at,'run_id':n} for a,b,n in accepted]),ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception as e:
                    stats['errors'].append({'url':row['url'],'reason':type(e).__name__,'detail':str(e)[:180]});stats['status']='PARTIAL'
                    if isinstance(e,SourceBlocked):break
            stats['HTTP_GETs']=len(network) if h else 0
            return stats
        finally:
            if h:h.session.close()
            (OUT/'HTTP_REQUESTS.json').write_text(json.dumps(network,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        result=run_competitor_collection(repo,collector,rebuild_report,origin='CLI_MANUAL',config=cfg,runtime=ROOT/'competitor-data/collection')
        settled=fingerprints(read_state(repo.store));replay={'pages':0,'versions':0,'items':0}
        for p,cap,run in accepted:
            delta=repo.store.save(p,cap,run)
            for k in replay:replay[k]+=delta[k]
        assert replay=={'pages':0,'versions':0,'items':0} and fingerprints(read_state(repo.store))==settled
        after=read_state(repo.store);oldfp=fingerprints(before);newfp=fingerprints(after)
        assert all(oldfp['sources'][code]==newfp['sources'][code] for code in oldfp['sources'] if code not in ('harvey_rus','amur_stan'))
        events=json.loads((ROOT/'competitor-data/collection/business-report/EVENTS.json').read_text(encoding='utf-8'));urls={p.canonical_url for p,c,r in accepted}
        assert not [e for e in events if e['entity'] in urls and e['event_type']!='BASELINE_OBSERVATION']
        (OUT/'AFTER_STATE.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'RESULT.json').write_text(json.dumps(json_value({'run':result,'accepted':len(accepted),'HTTP_GETs':len(network),'Harvey_GETs':0,'Amur_GETs':len(network),'replay':replay,'protected_nine_sources_unchanged':True,'new_false_events':0}),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':result['status'],'run_id':result['run_id'],'accepted':len(accepted),'HTTP_GETs':len(network),'sources':result['sources'],'replay':replay},ensure_ascii=False))
    finally:repo.close()

if __name__=='__main__':main()
