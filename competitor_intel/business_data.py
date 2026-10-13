"""Read-only repeatable snapshot of normalized competitor state, no transport/AI."""
import hashlib
import json
from pathlib import Path
from .store import json_value

TABLES=('competitor_sources','competitor_pages','competitor_page_versions','competitor_campaign_items',
        'competitor_observations','competitor_ai_analysis','competitor_runs','competitor_meta','competitor_source_surfaces','competitor_historical_seeds','competitor_operator_captures')

def read_state(store):
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        state={}
        for table in TABLES:
            c.execute('SELECT to_regclass(%s) AS name',('public.'+table,))
            if c.fetchone()['name'] is None: state[table]=[]; continue
            # Names are a closed local schema allowlist, never user/source input.
            c.execute('SELECT * FROM '+table+' ORDER BY '+('singleton' if table=='competitor_meta' else 'source_id,seed_id' if table=='competitor_historical_seeds' else 'id'))
            state[table]=json_value(c.fetchall())
    return state

def fingerprints(state):
    result={'tables':{},'sources':{}}
    for name,rows in state.items():
        blob=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        result['tables'][name]={'rows':len(rows),'sha256':hashlib.sha256(blob).hexdigest()}
    for source in state['competitor_sources']:
        pages=[p for p in state['competitor_pages'] if p['source_id']==source['id']]; pids={p['id'] for p in pages}
        versions=[v for v in state['competitor_page_versions'] if v['page_id'] in pids]; vids={v['id'] for v in versions}
        groups={'registry':[source],'pages':pages,'versions':versions,
                'items':[i for i in state['competitor_campaign_items'] if i['page_version_id'] in vids],
                'observations':[o for o in state['competitor_observations'] if o['page_id'] in pids],
                'analysis':[a for a in state['competitor_ai_analysis'] if a['page_version_id'] in vids],
                'surfaces':[s for s in state.get('competitor_source_surfaces',[]) if s['source_id']==source['id']]}
        result['sources'][source['code']]={k:{'rows':len(v),'sha256':hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()} for k,v in groups.items()}
    return result

def normalized_dataset(state):
    declared=json.loads(Path(__file__).with_name('source_capabilities.json').read_text(encoding='utf-8'))['sources']
    sources=[{**s,'capabilities':s.get('capabilities') or declared.get(s['code'],[])} for s in state['competitor_sources']]
    for source in sources:
        source['historical_seed_rows']=sum(r['source_id']==source['id'] for r in state.get('competitor_historical_seeds',[]))
        source['access_gaps']=[{'url':r['canonical_url'],'fetch_status':r['fetch_status'],'reason':r['reason']} for r in state.get('competitor_source_surfaces',[]) if r['source_id']==source['id'] and r['status']=='ERROR']
        captures=[r for r in state.get('competitor_operator_captures',[]) if r['source_id']==source['id'] and r.get('page_version_id')]
        source['capture_methods']=sorted({r['capture_method'] for r in captures})
        for r in state.get('competitor_source_surfaces',[]):
            automated=(r.get('details') or {}).get('automated_access_evidence') or {}
            if r['source_id']==source['id'] and automated.get('fetch_status') in ('HTTP_BLOCKED','CAPTCHA_BLOCKED','ERROR'):
                source['access_gaps'].append({'url':r['canonical_url'],'fetch_status':automated['fetch_status'],'reason':'AUTOMATED_ACCESS_REMAINS_INCOMPLETE'})
        if captures:source['automated_access_incomplete']=True
    source_by_id={s['id']:s for s in sources}; pages={p['id']:p for p in state['competitor_pages']}
    versions={v['id']:v for v in state['competitor_page_versions']}; items={}
    for item in state['competitor_campaign_items']: items.setdefault(item['page_version_id'],[]).append(item)
    current=[]; history=[]
    for page in pages.values():
        v=next((v for v in versions.values() if v['page_id']==page['id'] and v['content_hash']==page['current_content_hash']),None)
        if not v or v.get('qa_status')!='ACCEPTED': continue
        def snapshot(version,observed):
            p=version['payload']
            extra_keys={'sku','source_new_label','condition_label','condition_identity','displayed_saving_raw','ambiguous_commercial_values','record_kind','original_capture_time','capture_page_url','unit_price_evidence'}
            enriched=[]
            for item in items.get(version['id'],[]):
                metadata_key=json.dumps([item.get('product_url'),item.get('tab_title') or ''],ensure_ascii=False,separators=(',',':'))
                meta=(p.get('item_metadata') or {}).get(metadata_key,{})
                enriched.append({**item,**{k:v for k,v in meta.items() if k in extra_keys}})
            lineage=version['extractor_version'];operator=p.get('capture_provenance') or {}
            if operator.get('capture_method')=='OPERATOR_BROWSER_CAPTURE':
                # Regionless captures cannot establish comparable price history.
                lineage+='|REGION:'+operator['source_region'] if operator.get('source_region') else '|REGION_UNKNOWN:'+operator['content_hash']
            return {'source':source_by_id[page['source_id']]['code'],'entity':page['canonical_url'],
                    'version':version['content_hash'],'extractor':lineage,'observed_at':observed,
                    'page_type':p['page_type'],'title':p['title'],'published_at':p.get('published_at'),
                    'valid_from':p.get('valid_from'),'valid_to':p.get('valid_to'),'text':p.get('normalized_text',''),
                    'campaign_in_news':(p.get('campaign_evidence') or {}).get('current_campaign_in_news') is True,
                    'capture_provenance':p.get('capture_provenance'),
                    'items':enriched,'content_kind':p.get('content_kind') or p['page_type'],'campaign_coverage':p.get('campaign_coverage'),'item_scope_complete':p.get('item_scope_complete') is True,
                    'source_discovery_complete':p.get('source_discovery_complete') is True,
                    'mechanics':p.get('commercial_mechanics') or p.get('mechanics') or [],'scope_key':p.get('scope_key'), 'qa_status':version.get('qa_status')}
        current.append(snapshot(v,page['last_seen_at']))
        # No parser/coverage expansion or offline re-extraction is a live change.
        seen=set()
        for o in sorted(state['competitor_observations'],key=lambda o:(o['observed_at'],o['id'])):
            ov=versions.get(o['page_version_id'])
            if o['page_id']!=page['id'] or not ov or ov.get('qa_status')!='ACCEPTED' or ov['extractor_version']!=v['extractor_version']: continue
            if o['transport'].startswith(('OFFLINE','FIXTURE')): continue
            key=(ov['content_hash'],o['observed_at'])
            if key in seen: continue
            seen.add(key); history.append(snapshot(ov,o['observed_at']))
        if not seen: history.append(snapshot(v,v['fetched_at']))
    analysis=[]
    current_versions={p['version'] for p in current}
    for a in state['competitor_ai_analysis']:
        if a['analysis_status']=='DONE' and a['content_hash'] in current_versions and a['page_version_id'] in versions and versions[a['page_version_id']].get('qa_status')=='ACCEPTED':
            page=pages[versions[a['page_version_id']]['page_id']]
            analysis.append({**a,'source':source_by_id[page['source_id']]['code'],'entity':page['canonical_url']})
    coverage=[]; codes={s['code'] for s in sources}
    for run in state.get('competitor_runs',[]):
        for entry in (run.get('result') or {}).get('coverage',[]):
            if isinstance(entry,dict) and entry.get('source') in codes and isinstance(entry.get('entities'),list) and entry.get('observed_at'):
                coverage.append(entry)
    return {'sources':sources,'current':current,'history':history,'analysis':analysis,'coverage':coverage,
            'provenance':'SAVED_PUBLIC_OBSERVATIONS','ai_network_calls':0,
            'limitations':['Начальные снимки и переизвлечение данных не считаются новыми событиями.',
                          'Пропажа товара требует подтверждённого полного состава наблюдаемого блока; текущая история такого признака не содержит.',
                          'Отдельные даты, бренды, модели и категории не опубликованы источниками.']}
