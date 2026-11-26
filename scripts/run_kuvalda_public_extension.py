"""Kuvalda only; MetalMaster stored data must remain byte/hash identical."""
import argparse
import json
from pathlib import Path
from competitor_intel.store import Store
from competitor_intel.kuvalda_public_runner import crawl_public

p=argparse.ArgumentParser(); p.add_argument('--max-pages',type=int,default=35); args=p.parse_args()
if not 1<=args.max_pages<=60: p.error('max-pages must be 1..60')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
try:
    result=crawl_public(store,'reports/KUVALDA_ALLOWED_SOURCE_EXTENSION_2026-10-09',max_pages=args.max_pages)
    print(json.dumps({'run_id':result['run_id'],'rows_delta':result['rows_delta'],'errors':result['errors'],'queue_remaining':len(result['queue_remaining'])},ensure_ascii=False))
finally: store.close()
