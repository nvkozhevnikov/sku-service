"""Explicit 6 saved +13 detail ledger only; isolated RC, no discovery or activation."""
from dataclasses import replace,asdict
from datetime import datetime,timezone
from pathlib import Path
import json,os,subprocess,sys,time,re,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.resolve_reviews_offline import load,save,sha
from scripts.intervesp_full_preflight import read_source_state
from scripts.run_full_supplier_qa import _preflight_rc
from scripts.ingest_intervesp_listing_archive import write_report
from scripts.reconcile_cross_source_offline import url_numeric_model_contradiction
from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.bekamak_official import parse_bekamak_official_detail
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.full_supplier_collection import _SourceClient,PublicPacer
from universal_supplier.http_capture import UrllibPublicHttpClient,capture_public_html,CaptureStatus
from universal_supplier.postgres import PostgresConfig,PostgresRepository
from sterbrust_matching.normalization import normalize_model,extract_model

OUT=ROOT/'reports/RC_LOCAL/BEKA_GAP_VERIFIED_2026-10-01'
PRIOR=ROOT/'reports/RC_LOCAL/CROSS_SOURCE_FINAL_VERIFIED_2026-10-01'
COMMIT='9e3467ced966f8826749a6e315c075e6c37aca8e'

def parse_confirmed(source,url,body):
    parser=parse_bekamak_detail if source=='beka_mak' else parse_bekamak_official_detail
    p=parser(body,source_url=url)
    if not p.site_internal_id or not p.supplier_model or not p.name: raise ValueError('stable own source identity unavailable')
    title_view=re.sub(r'\bс\s+кабиной\b','ENCLOSED',p.name,flags=re.I)
    full=normalize_model(p.supplier_model); title_model=normalize_model(extract_model(title_view))
    enclosure_proven=(full.endswith('enclosed') and title_model==full.removesuffix('enclosed')
                       and bool(re.search(r'\bENCLOSED\b',title_view,re.I)))
    if full!=title_model and not enclosure_proven:
        raise ValueError('own title/article full execution contradiction')
    if url_numeric_model_contradiction(url,p.supplier_model): raise ValueError('URL/title numeric model contradiction')
    if source=='beka_mak_tr':
        slug=url.rstrip('/').split('/')[-2]
        # Opaque random-tail slugs are not model evidence; do not remove a
        # meaningful suffix from a normal model slug to force agreement.
        if '_' not in slug and normalize_model(slug)!=normalize_model(p.supplier_model):
            raise ValueError('official slug/H1 full execution contradiction')
    return p

def main():
    if OUT.exists(): raise ValueError('Preserve existing applied checkpoint; no implicit rerun')
    raw=subprocess.check_output(['git','show',COMMIT+':full_discovery/evidence/cross_source/BEKA_RC_GAP_INGEST.json'],cwd=ROOT)
    ledger=json.loads(raw)
    if ledger['counts']!={'OFFLINE_INGEST_READY':6,'ONE_DETAIL_GET_REQUIRED':13,'HUMAN_REVIEW_REQUIRED':23}: raise ValueError('Ledger count changed')
    offline=load(PRIOR/'BEKA_OFFLINE_INGEST_ROWS.json'); plan=load(PRIOR/'MINIMAL_GET_PLAN.json'); human=load(PRIOR/'HUMAN_GAP_REVIEW.json')
    differences=[]
    for status,previous in [('OFFLINE_INGEST_READY',offline),('ONE_DETAIL_GET_REQUIRED',plan),('HUMAN_REVIEW_REQUIRED',human)]:
        expected={(r['source'],r.get('source_url') or r.get('url')) for r in previous}
        actual={(r['source'],r['url']) for r in ledger['records'] if r['status']==status}
        if actual!=expected:
            if status=='ONE_DETAIL_GET_REQUIRED': raise ValueError('GET authorization plan changed')
            differences.append({'status':status,'new_ledger_only':sorted(actual-expected),'previous_only':sorted(expected-actual)})
    if {(r['source'],r['url']) for r in ledger['records']}!={(r['source'],r.get('source_url') or r.get('url')) for r in offline+plan+human}:
        raise ValueError('Ledger total identity scope changed')
    # Resolve the updated six READY records against pinned saved HTML, without
    # changing the prior proposal or trusting a summary without its bytes.
    ready=[r for r in ledger['records'] if r['status']=='OFFLINE_INGEST_READY']
    wanted={(r['evidence_ref'] or '').removeprefix('sha256:') for r in ready}; captures={}
    for folder in ledger['detail_directories']:
        directory=ROOT.parents[1]/folder
        for file in directory.rglob('*.html'):
            text=file.read_text(encoding='utf-8'); digest=hashlib.sha256(text.encode()).hexdigest()
            if digest in wanted: captures[digest]=file
    offline=[]
    for r in ready:
        digest=r['evidence_ref'].removeprefix('sha256:'); file=captures.get(digest)
        if not file: raise ValueError('Ready saved capture missing')
        p=parse_confirmed(r['source'],r['url'],file.read_text(encoding='utf-8'))
        if normalize_model(p.supplier_model)!=normalize_model(r['model']): raise ValueError('Ledger model disagrees with saved page')
        offline.append({'source':r['source'],'source_url':r['url'],'external_id':p.site_internal_id,
            'capture_path':str(file),'capture_sha256':sha(file)})
    before=read_source_state(); OUT.mkdir(); save(OUT/'LEDGER.json',ledger)
    report={'stage':'PREFLIGHT_PASS','before':before,'ledger_commit':COMMIT,'ledger_sha256':__import__('hashlib').sha256(raw).hexdigest(),
        'offline_rows':[],'http_rows':[],'replay_rows':[],'pause_seconds':{'beka_mak':20,'beka_mak_tr':20},
        'rate_basis':'existing sequential source collection policy, minimum20 seconds globally; no faster mode or parallelism',
        'http_gets':0,'human_rows_not_fetched':23,'ledger_reconciliation_differences':differences}
    path=OUT/'RUN.json'; write_report(path,report)
    passfile=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf'; os.environ['PGPASSFILE']=str(passfile)
    repository=PostgresRepository(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_runtime',None,'disable'))
    pairs=[]
    try:
        _preflight_rc(repository)
        with repository.connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended('beka-gap-ledger-explicit-job',0))")
            if not cur.fetchone()[0]: raise RuntimeError('Another gap ledger job running')
            cur.execute("SELECT s.code,sp.external_id,sp.id,o.id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr')")
            oldids={tuple(r[:2]):tuple(r[2:]) for r in cur.fetchall()}
        pgdump=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
        backup=OUT/'BEFORE.dump'
        subprocess.run([str(pgdump),'-h','127.0.0.1','-p','55449','-U','rc_admin','-d','universal_supplier_server','-Fc','--no-owner','-f',str(backup)],check=True,capture_output=True)
        report['backup']={'path':str(backup),'sha256':sha(backup),'bytes':backup.stat().st_size}; write_report(path,report)
        for r in offline:
            _preflight_rc(repository); saved=Path(r['capture_path'])
            if sha(saved)!=r['capture_sha256']: raise ValueError('Offline saved capture changed')
            p=parse_confirmed(r['source'],r['source_url'],saved.read_text(encoding='utf-8'))
            if str(p.site_internal_id)!=str(r['external_id']): raise ValueError('Offline source identity changed')
            capture=HttpCapture(requested_url=r['source_url'],final_url=r['source_url'],http_status=200,content_type='text/html',
                observed_at=datetime.fromtimestamp(saved.stat().st_mtime,timezone.utc),response_sha256=r['capture_sha256'],
                evidence_sha256=r['capture_sha256'],evidence_ref=str(saved),capture_scope='detail',
                response_hash_basis='sanitized_archive',diagnostics=('offline_saved_detail_replay','archive_mtime_not_verified_original_fetch_time'))
            result=repository.persist_commercial_observation(p,capture); pairs.append((p,capture))
            report['offline_rows'].append({'source':r['source'],'url':r['source_url'],'external_id':p.site_internal_id,
                'capture':asdict(capture),'product':asdict(p),**{k:v for k,v in result.items() if k!='ingest_counts'}})
            write_report(path,report)
        pacer=PublicPacer(); client=UrllibPublicHttpClient(); blocked=set()
        report['stage']='DETAIL_GET_RUNNING'; write_report(path,report)
        for r in plan:
            source=r['source']; url=r['url']
            if source in blocked: continue
            # Fresh read-only availability/identity immediately before each
            # source access, and runtime guard immediately before persistence.
            read_source_state(); _preflight_rc(repository); pacer.before_get()
            result=capture_public_html(url,evidence_dir=OUT/'evidence'/source,
                client=_SourceClient(source,client),timeout_seconds=20,max_attempts=1)
            report['http_gets']+=1
            row={'source':source,'url':url,'http_status':result.http_status,'status':result.status.value,
                'evidence_ref':result.evidence_ref,'diagnostics':list(result.diagnostics)}
            if result.capture: row['capture']=asdict(result.capture)
            if result.status in (CaptureStatus.BLOCKED,CaptureStatus.UNSAFE_URL): blocked.add(source)
            if result.status==CaptureStatus.SUCCESS and result.capture and result.evidence_body:
                try:
                    p=parse_confirmed(source,url,result.evidence_body.decode('utf-8','replace'))
                    _preflight_rc(repository); persisted=repository.persist_commercial_observation(p,result.capture)
                    pairs.append((p,result.capture)); row.update(status='PERSISTED',external_id=p.site_internal_id,
                        product=asdict(p),**{k:v for k,v in persisted.items() if k!='ingest_counts'})
                except ValueError as error: row.update(status='REVIEW',reason=str(error))
            report['http_rows'].append(row); write_report(path,report)
            print(json.dumps({'processed':report['http_gets'],'source':source,'status':row['status'],'http':row['http_status']}),flush=True)
            if result.status==CaptureStatus.NETWORK_ERROR:
                report['stage']='STOPPED_NETWORK_ERROR_NO_RETRY'; break
        _preflight_rc(repository)
        for p,capture in pairs:
            result=repository.persist_commercial_observation(p,capture)
            if not result['exact_noop']: raise RuntimeError('Replay not exact no-op')
            report['replay_rows'].append({'source':p.supplier,'external_id':p.site_internal_id,'exact_noop':True})
        after=read_source_state()
        with repository.connection.cursor() as cur:
            cur.execute("SELECT s.code,sp.external_id,sp.id,o.id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr')")
            current={tuple(r[:2]):tuple(r[2:]) for r in cur.fetchall()}
        if any(current.get(k)!=v for k,v in oldids.items()): raise RuntimeError('Old source/offer PK changed')
        for table in ('product_matches','catalog_products'):
            if after['table_counts'][table]!=before['table_counts'][table]: raise RuntimeError('Canonical/matching DB mutation detected')
        report.update(after=after,blocked_sources=sorted(blocked),new_source_products=after['table_counts']['source_products']-before['table_counts']['source_products'],
            finished_at=datetime.now(timezone.utc).isoformat(),stage=report['stage'] if report['stage'].startswith('STOPPED') else 'INGEST_AND_REPLAY_VERIFIED')
        write_report(path,report)
    except Exception as error:
        report.update(stage='STOPPED_ERROR_NO_RETRY',error=str(error)); write_report(path,report); raise
    finally: repository.close()

if __name__=='__main__': main()
