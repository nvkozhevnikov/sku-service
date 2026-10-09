"""Snapshot before/compare after the second LIVE crawl, no source HTTP."""
import argparse
import json
from pathlib import Path
from competitor_intel.store import Store

p=argparse.ArgumentParser(); p.add_argument('phase',choices=['before','after']); args=p.parse_args()
out=Path('reports/COMPETITOR_INTELLIGENCE_2026-10-09')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
state={'counts':store.counts(),'pages':store.query('SELECT s.code,p.canonical_url,p.current_content_hash FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id ORDER BY s.code,p.canonical_url'),
       'totals':store.query('SELECT (SELECT count(*) FROM competitor_pages) AS pages,(SELECT count(*) FROM competitor_page_versions) AS versions,(SELECT count(*) FROM competitor_campaign_items) AS items,(SELECT count(*) FROM competitor_observations) AS observations'),
       'latest_run':store.query('SELECT id,status FROM competitor_runs ORDER BY id DESC LIMIT 1')}
if args.phase=='before':
    (out/'IDEMPOTENCY_BEFORE.json').write_text(json.dumps(state,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(state['totals']))
else:
    if state['latest_run'][0]['status']=='RUNNING':
        store.close(); raise SystemExit('Second crawl still RUNNING: no final idempotency receipt')
    before=json.loads((out/'IDEMPOTENCY_BEFORE.json').read_text(encoding='utf-8'))
    delta={k:state['totals'][0][k]-before['totals'][0][k] for k in ['pages','versions','items','observations']}
    changes=[page for page in state['pages'] if page not in before['pages']]
    receipt={'provenance':'LIVE-VERIFIED dedicated dev / second public crawl', 'before':before,'after':state,'delta':delta,'changed_current_pages':changes,
             'status':'PASS' if not changes and delta['pages']==0 and delta['versions']==0 and delta['items']==0 else 'REVIEW_SEMANTIC_DELTAS',
             'note':'Observation append and last_seen timestamps are expected. New raw DOM hashes alone do not imply a semantic version.'}
    (out/'IDEMPOTENCY_RESULT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'status':receipt['status'],'delta':delta,'changes':changes},ensure_ascii=False))
store.close()
