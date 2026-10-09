"""Read-only repeatable snapshot of normalized competitor state, no transport/AI."""
import hashlib
import json
from pathlib import Path
from .store import json_value

TABLES=('competitor_sources','competitor_pages','competitor_page_versions','competitor_campaign_items',
        'competitor_observations','competitor_ai_analysis','competitor_runs','competitor_meta','competitor_source_surfaces')

def read_state(store):
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        state={}
        for table in TABLES:
            c.execute('SELECT to_regclass(%s) AS name',('public.'+table,))
            if c.fetchone()['name'] is None: state[table]=[]; continue
            # Names are a closed local schema allowlist, never user/source input.
            c.execute('SELECT * FROM '+table+' ORDER BY '+('singleton' if table=='competitor_meta' else 'id'))
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
    source_by_id={s['id']:s for s in sources}; pages={p['id']:p for p in state['competitor_pages']}
    versions={v['id']:v for v in state['competitor_page_versions']}; items={}
    for item in state['competitor_campaign_items']: items.setdefault(item['page_version_id'],[]).append(item)
    current=[]; history=[]
    for page in pages.values():
        v=next((v for v in versions.values() if v['page_id']==page['id'] and v['content_hash']==page['current_content_hash']),None)
        if not v or v.get('qa_status')!='ACCEPTED': continue
        def snapshot(version,observed):
            p=version['payload']
            return {'source':source_by_id[page['source_id']]['code'],'entity':page['canonical_url'],
                    'version':version['content_hash'],'extractor':version['extractor_version'],'observed_at':observed,
                    'page_type':p['page_type'],'title':p['title'],'published_at':p.get('published_at'),
                    'valid_from':p.get('valid_from'),'valid_to':p.get('valid_to'),'text':p.get('normalized_text',''),
                    'items':items.get(version['id'],[]),'item_scope_complete':p.get('item_scope_complete') is True,
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
