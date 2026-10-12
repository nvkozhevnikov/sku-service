"""Read-only corpus → metadata seeds; pure bounded LIVE refresh planning.

No supplier imports, DB connections, HTTP, price transfer or catalogue discovery.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

def seed_url(value):
    p=urlsplit(value or '')
    if p.scheme!='https' or p.hostname not in ('stanki.ru','www.stanki.ru') or p.username or p.password or p.port or p.query:
        raise ValueError('UNSAFE_KAMI_SEED_URL')
    if not p.path.startswith('/catalog/'): raise ValueError('NOT_PRODUCT_CORPUS_URL')
    return urlunsplit(('https','www.stanki.ru',p.path.rstrip('/')+'/','',''))

def stamp(value):
    if not value: return None
    try: d=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,TypeError,AttributeError): return None
    return value if d.tzinfo is not None else None

def metadata(row,position):
    card=row['card']; capture=row.get('capture',{}); raw=card.get('raw_data') or {}
    url=seed_url(card.get('canonical_url') or card.get('final_url') or capture.get('url'))
    identity=card.get('external_id') or f'row:{position}'
    categories=[{'name':c.get('name'),'url':c.get('url'),'position':c.get('position')} for c in card.get('categories',[]) if isinstance(c,dict)]
    source_time=stamp(capture.get('observed_at'))
    return {'seed_id':hashlib.sha256(('kami|'+url+'|'+identity).encode()).hexdigest(),
            'source':'kami','evidence_class':'HISTORICAL_SEED','is_live':False,'source_record_id':identity,
            'product_url':url,'product_name':card.get('name'),'brand':card.get('brand'),
            'model':raw.get('full_model') or None,'model_evidence_status':raw.get('identity_status'),
            'card_kind':raw.get('card_kind'),'category':categories,
            'original_capture_time':source_time,'capture_time_status':'KNOWN' if source_time else 'UNKNOWN',
            'source_capture_sha256':capture.get('sha256'),'source_capture_ref':row.get('evidence_ref'),
            'price':None,'old_price':None,'availability':None,
            'live_price_eligible':False,'new_product_event_eligible':False}

def export_corpus(corpus,output,expected_sha=None):
    corpus=Path(corpus).resolve(); out=Path(output).resolve()
    if out==corpus or corpus.is_relative_to(out): raise ValueError('Output must not contain/overwrite source corpus')
    out.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(); seeds=[]; errors=[]; ids=set()
    # Read each original byte once; never mutate input or derive time from mtime.
    with corpus.open('rb') as f:
        for n,line in enumerate(f,1):
            digest.update(line)
            if not line.strip(): continue
            try:
                record=metadata(json.loads(line.decode('utf-8-sig')),n)
                if record['seed_id'] in ids: raise ValueError('DUPLICATE_SOURCE_RECORD')
                ids.add(record['seed_id']); seeds.append(record)
            except (ValueError,KeyError,TypeError) as e:
                errors.append({'line':n,'error':str(e) if isinstance(e,ValueError) else type(e).__name__})
    before=digest.hexdigest()
    if expected_sha and before!=expected_sha: raise ValueError('PINNED_CORPUS_HASH_MISMATCH')
    with corpus.open('rb') as f: after=hashlib.file_digest(f,'sha256').hexdigest()
    if before!=after: raise RuntimeError('Corpus changed during read; output not accepted')
    target=out/'HISTORICAL_SEEDS.jsonl'
    content=''.join(json.dumps(s,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for s in sorted(seeds,key=lambda s:s['seed_id']))
    target.write_bytes(content.encode('utf-8'))
    times=[s['original_capture_time'] for s in seeds if s['original_capture_time']]
    result={'provenance':'REPOSITORY_VERIFIED saved corpus only; not LIVE verification',
            'source_path':str(corpus),'source_sha256_before':before,'source_sha256_after':after,
            'source_unchanged':True,'seed_rows':len(seeds),'distinct_product_urls':len({s['product_url'] for s in seeds}),
            'unknown_capture_times':sum(s['original_capture_time'] is None for s in seeds),
            'original_capture_time_min':min(times) if times else None,'original_capture_time_max':max(times) if times else None,
            'brand_present':sum(bool(s['brand']) for s in seeds),'model_present':sum(bool(s['model']) for s in seeds),
            'category_present':sum(bool(s['category']) for s in seeds),'rejected_rows':errors,
            'output_sha256':hashlib.sha256(content.encode()).hexdigest(),'source_HTTP':0,'source_DB_mutations':0,
            'competitor_DB_applied':False,'current_price_rows_added':0,'new_product_events_added':0}
    (out/'SEED_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def historical_context(live_record,seeds):
    """Never replace live fields or borrow historical prices/observation time."""
    url=seed_url(live_record['product_url'])
    matches=[s for s in seeds if s['product_url']==url and s['evidence_class']=='HISTORICAL_SEED' and s['is_live'] is False]
    return {**live_record,'historical_context':[{k:s.get(k) for k in ('seed_id','product_name','brand','model','card_kind','category','original_capture_time','source_capture_sha256')} for s in matches]}

def plan_refresh(seeds,*,campaign_urls=(),changed_urls=(),explicit_urls=(),tracked_urls=(),stale_before=None,max_products=20):
    if not 0<=max_products<=100: raise ValueError('Explicit bounded product cap required (0..100)')
    index={}
    for s in seeds: index.setdefault(s['product_url'],[]).append(s)
    reasons={}
    for values,reason in [(campaign_urls,'CURRENT_CAMPAIGN_MEMBER'),(changed_urls,'EXPLICIT_SOURCE_CHANGE'),(explicit_urls,'OPERATOR_REQUESTED_REFRESH')]:
        for value in values:
            url=seed_url(value); reasons.setdefault(url,set()).add(reason)
    if stale_before:
        threshold=stamp(stale_before)
        if not threshold: raise ValueError('Timezone-aware stale threshold required')
        limit=datetime.fromisoformat(threshold.replace('Z','+00:00'))
        # Age never expands the entire HISTORICAL_SEED universe.
        for value in tracked_urls:
            url=seed_url(value); records=index.get(url,[])
            known=[datetime.fromisoformat(s['original_capture_time'].replace('Z','+00:00')) for s in records if s.get('original_capture_time')]
            if not known: reasons.setdefault(url,set()).add('TRACKED_CAPTURE_TIME_UNKNOWN')
            elif max(known)<limit: reasons.setdefault(url,set()).add('TRACKED_CAPTURE_STALE_EXPLICIT_POLICY')
    candidates=[{'url':url,'reasons':sorted(r),'seed_ids':[s['seed_id'] for s in index.get(url,[])],
                 'historical_lookup_only':True,'fetch_allowed_only_after_public_source_policy_check':True}
                for url,r in sorted(reasons.items(),key=lambda x:(0 if 'CURRENT_CAMPAIGN_MEMBER' in x[1] else 1,x[0]))]
    return {'selected':candidates[:max_products],'deferred':candidates[max_products:],'total_candidates':len(candidates),
            'full_catalog_crawl':False,'historical_prices_used':False,'source_HTTP':0}
