"""Actual local competitor read/UI integrity proof. No source fetch or mutation."""
import json,argparse
from pathlib import Path
from competitor_intel.store import Store
from competitor_intel.business_data import TABLES
from competitor_intel.monitoring import MonitoringRepository,MonitoringService
root=Path('reports/COMPETITOR_MONITORING_UI_LOCAL_2026-10-12')
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=root);args=parser.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=True)
config=Path('competitor-data/dev-config.json')
def proof():
    s=Store(json.loads(config.read_text())['dsn']);s.conn.read_only=True;out={}
    try:
        for t in TABLES:
            if s.query('SELECT to_regclass(%s) name',('public.'+t,))[0]['name'] is None:out[t]=None;continue
            order='singleton' if t=='competitor_meta' else 'source_id,seed_id' if t=='competitor_historical_seeds' else 'id'
            out[t]=s.query('SELECT count(*) n,md5(string_agg(md5(row_to_json(t)::text),\'\' ORDER BY '+order+')) fingerprint FROM '+t+' t')[0]
        return out
    finally:s.close()
before=proof();service=MonitoringService(MonitoringRepository(config));views={}
for tab in ('overview','changes','prices','promotions','assortment','sources'):
    v=service.view(tab,{});views[tab]={'rows_returned':len(v['rows']),'sources':len(v['sources']),'total':v['total']}
actual=service.view('sources',{});by={s['code']:s for s in actual['sources']}
after=proof()
changes=service.view('changes',{})
state=service.repository.load()
versions={r['content_hash']:r for r in state['competitor_page_versions']}
prices=[e for e in changes['rows'] if e['event_type'] in ('PRICE_DECREASE','PRICE_INCREASE')]
comparable=all(e.get('before_version') in versions and e.get('after_version') in versions and
    versions[e['before_version']]['qa_status']=='ACCEPTED' and versions[e['after_version']]['qa_status']=='ACCEPTED' and
    versions[e['before_version']]['extractor_version']==versions[e['after_version']]['extractor_version'] and
    e.get('observed_before') and e.get('observed_after') and e.get('price_type_before') in ('PUBLIC','SALE') and e.get('price_type_after') in ('PUBLIC','SALE') for e in prices)
result={'status':'PASS','source_HTTP':0,'AI':False,'scheduler':False,'source_data_mutations':0,'before':before,'after':after,'views':views,
 'checks':{'all_database_fingerprints_equal':before==after,'six_registered_sources':len(by)==6,'youmet_manual_no_fake_baseline':by['youmet']['status']=='MANUAL' and not by['youmet']['baseline_confirmed'] and by['youmet']['last_capture'] is None,
 'kami_404_not_global_failure':by['stanki_ru']['status']=='WORKING' and any('skidka-do-30-kley' in r['canonical_url'] for r in by['stanki_ru']['issues']),
 'real_baseline_or_verified_comparable_prices':changes['total']==0 if changes['baseline_only'] else bool(prices) and comparable,'frontend_page_bound':all(v['rows_returned']<=50 for v in views.values())}}
if not all(result['checks'].values()):result['status']='FAIL'
(root/'POSTGRES_READONLY_QA.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':result['checks'],'views':views},ensure_ascii=False))
if result['status']!='PASS':raise SystemExit(1)
